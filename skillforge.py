#!/usr/bin/env python3
"""
skillforge_complete.py — SkillForge v5: Complete-Source + Anti-Hallucination

Changes from v4 (all driven by harsh-critic review):

  Severity 1 (correctness):
    1. Verifier number matching uses digit-aware boundaries (no more "3"
       matching inside "13"; "1.5" not matching inside "21.5"). Other
       claim types use \\w word boundaries instead of raw substring.
    2. YAML `description:` is now verified (only `name:`/`id:`/`version:`
       are skipped — those are metadata, not source claims).
    3. `extract_skill_name` only reads inside the YAML frontmatter span.
    4. `git clone` has a hard timeout (default 300s, configurable).
    5. Submodules cloned by default (--recurse-submodules).

  Severity 2 (completeness):
    6. Web sources do same-origin BFS crawl (--crawl-pages, default 25).
    7. `llms.txt` is parsed as a URL manifest; each listed URL fetched.
    8. Auto-scroll iterates ALL scrollable containers (not just body).
    9. Main-content extraction via trafilatura strips sidebar/nav noise.

  Others:
   11. Agentic regeneration loop: verifier output feeds back into LLM
       with "delete or replace from source" up to --agentic-retries.
   12. Smart prioritized truncation: repos sort by README → manifest →
       config → core → other → tests; papers keep abstract + middle
       (methods) + tail.
   13. Repo-clone cache: blob/PR/repo URLs to same (owner, repo) share
       one clone.
   14. Skill-name collisions disambiguated with short hash of source URL.
   15. Verifier knows common ML/CS acronyms (Adam, BERT, ReLU, JSON, ...).
   16. Path traversal protection via abspath + prefix check.
   17. Startup banner warns when no GITHUB_TOKEN and GitHub API needed.
   18. Per-repo max size guard; above limit, fall back to priority-cap.
   19. GitHubFetcher.cleanup() via weakref finalizer + atexit.
   20. --json emits one machine-readable line per source to stdout.
   21. Verifier dedupes within-line by VALUE (not (value, type)).
   22. Per-section verification breakdown in VERIFICATION.md.
   23. Unicode normalization (NFKD + ASCII fold) on source and claims.

Install:
  pip install anthropic google-generativeai openai PyMuPDF \\
              requests beautifulsoup4 html2text playwright lxml trafilatura
  playwright install chromium     # for full dynamic-page fidelity
"""

from __future__ import annotations

import argparse
import atexit
import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
import weakref
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)


# ─────────────────────────────────────────────────────────────
# TERMINAL UI
# ─────────────────────────────────────────────────────────────

class Spinner:
    FRAMES = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

    def __init__(self, message="Working"):
        self.message = message
        self._stop = threading.Event()
        self._thread = None
        self._start_time = 0.0

    def __enter__(self):
        self._start_time = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *a):
        self._stop.set()
        if self._thread:
            self._thread.join()
        elapsed = time.time() - self._start_time
        sys.stdout.write(f"\r   ✓ {self.message} ({elapsed:.1f}s)\033[K\n")
        sys.stdout.flush()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            f = self.FRAMES[i % len(self.FRAMES)]
            el = time.time() - self._start_time
            sys.stdout.write(f"\r   {f} {self.message} [{el:.0f}s]\033[K")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.1)

    def update(self, msg):
        self.message = msg


def banner(text, char="═"):
    line = char * max(len(text) + 4, 60)
    print(f"\n{line}\n  {text}\n{line}")


def section(text):
    print(f"\n{'─'*60}\n  {text}\n{'─'*60}")


def sizeof_fmt(n):
    for u in ("B","KB","MB","GB"):
        if abs(n) < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def slugify(s):
    s = re.sub(r'https?://', '', s.strip())
    s = re.sub(r'[^a-zA-Z0-9._-]+', '_', s)
    return s.strip('_')[:80]


def short_hash(s, n=8):
    return hashlib.md5(s.encode("utf-8", errors="replace")).hexdigest()[:n]


def safe_join(base, rel):
    """Path-traversal-safe join. Returns absolute path or None if escapes base."""
    base_abs = os.path.abspath(base)
    rel = rel.replace("\\", "/")
    target = os.path.abspath(os.path.join(base_abs, rel))
    if target != base_abs and not target.startswith(base_abs + os.sep):
        return None
    return target


def norm_text(s):
    """Unicode-normalize for matching: NFKD, ASCII-fold, lowercase."""
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", s)
    folded = nfkd.encode("ascii", "ignore").decode("ascii")
    return folded.lower()


# ─────────────────────────────────────────────────────────────
# OPTIONAL IMPORTS
# ─────────────────────────────────────────────────────────────

def _try_import(module, install_hint):
    try:
        return __import__(module)
    except ImportError:
        return None


def _require(module, install_hint):
    try:
        return __import__(module)
    except ImportError:
        print(f"❌ Required: {install_hint}")
        sys.exit(1)


# ─────────────────────────────────────────────────────────────
# SOURCE TYPE DETECTION
# ─────────────────────────────────────────────────────────────

GITHUB_RE = re.compile(
    r'^(?:https?://)?(?:www\.)?github\.com/(?P<owner>[^/]+)(?:/(?P<repo>[^/]+))?'
    r'(?:/(?P<kind>blob|tree|pull|issues|releases|raw)/(?P<rest>.+))?/?$'
)
ARXIV_ID_RE = re.compile(r'^\d{4}\.\d{4,5}(v\d+)?$')


@dataclass
class SourceSpec:
    raw: str
    kind: str
    url: str
    extra: dict = field(default_factory=dict)


def detect_source(raw):
    s = raw.strip()
    if not s or s.startswith("#"):
        return SourceSpec(raw, "skip", "")

    if os.path.isfile(s) and s.lower().endswith(".pdf"):
        return SourceSpec(raw, "pdf", s)

    if ARXIV_ID_RE.match(s):
        return SourceSpec(raw, "arxiv", f"https://arxiv.org/abs/{s.split('v')[0]}",
                          extra={"arxiv_id": s.split('v')[0]})

    if "arxiv.org" in s:
        m = re.search(r'arxiv\.org/(?:abs|pdf|html)/([\d.]+)', s)
        if m:
            return SourceSpec(raw, "arxiv", f"https://arxiv.org/abs/{m.group(1)}",
                              extra={"arxiv_id": m.group(1)})

    norm = s if s.startswith("http") else f"https://{s}"
    gh = GITHUB_RE.match(norm)
    if gh:
        owner = gh.group("owner")
        repo = gh.group("repo")
        kind = gh.group("kind")
        rest = gh.group("rest") or ""

        if not repo:
            return SourceSpec(raw, "github_org", f"https://github.com/{owner}",
                              extra={"owner": owner})
        if kind in ("blob", "raw"):
            parts = rest.split("/", 1)
            ref = parts[0] if parts else "HEAD"
            path = parts[1] if len(parts) > 1 else ""
            return SourceSpec(raw, "github_blob",
                              f"https://github.com/{owner}/{repo}",
                              extra={"owner": owner, "repo": repo,
                                     "ref": ref, "path": path})
        if kind == "pull":
            pr = rest.split("/")[0] if rest else ""
            return SourceSpec(raw, "github_pr",
                              f"https://github.com/{owner}/{repo}/pull/{pr}",
                              extra={"owner": owner, "repo": repo, "pr": pr})
        if kind == "tree":
            return SourceSpec(raw, "github_repo",
                              f"https://github.com/{owner}/{repo}",
                              extra={"owner": owner, "repo": repo,
                                     "ref": rest.split("/")[0] if rest else None})
        return SourceSpec(raw, "github_repo",
                          f"https://github.com/{owner}/{repo}",
                          extra={"owner": owner, "repo": repo})

    if s.lower().endswith("llms.txt"):
        return SourceSpec(raw, "llms_manifest", norm)

    if s.endswith(".txt") or s.endswith(".md"):
        return SourceSpec(raw, "text", norm)

    if s.lower().endswith(".pdf") and s.startswith("http"):
        return SourceSpec(raw, "pdf", norm, extra={"is_url": True})

    if s.startswith("http") or (re.match(r'^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}', s) and " " not in s):
        return SourceSpec(raw, "web", norm)

    return SourceSpec(raw, "unknown", norm)


# ─────────────────────────────────────────────────────────────
# WEB DOWNLOADER — Playwright + container-scroll + crawler
# ─────────────────────────────────────────────────────────────

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 SkillForge/5.0"
)

_NOFOLLOW_EXT = (".png",".jpg",".jpeg",".gif",".svg",".webp",".ico",
                 ".pdf",".zip",".tar",".gz",".mp4",".mp3",".woff",
                 ".woff2",".ttf",".css",".js",".json",".xml")


class WebDownloader:
    def __init__(self, headless=True, max_scroll_passes=15, scroll_wait_ms=600,
                 navigation_timeout_ms=60000, crawl_pages=25):
        self.headless = headless
        self.max_scroll_passes = max_scroll_passes
        self.scroll_wait_ms = scroll_wait_ms
        self.nav_timeout = navigation_timeout_ms
        self.crawl_pages = crawl_pages
        self._playwright_ok = self._check_playwright()
        if not self._playwright_ok:
            print("   ⚠ Playwright unavailable — falling back to requests for HTML")
            print("     (install: pip install playwright && playwright install chromium)")

    @staticmethod
    def _check_playwright():
        try:
            from playwright.sync_api import sync_playwright  # noqa
            return True
        except Exception:
            return False

    def fetch(self, url):
        if url.endswith(".txt") or url.endswith(".md"):
            return self._fetch_text_url(url)
        if self._playwright_ok:
            try:
                return self._fetch_playwright(url)
            except Exception as e:
                print(f"   ⚠ Playwright failed for {url}: {type(e).__name__}: {e}")
                print(f"   ↳ falling back to requests")
        return self._fetch_requests(url)

    def _fetch_text_url(self, url):
        req = _require("requests", "pip install requests")
        with Spinner(f"GET {url[:50]}"):
            r = req.get(url, headers={"User-Agent": DEFAULT_UA}, timeout=30)
            r.raise_for_status()
        text = r.text
        return {"url": url, "html": "", "text": text, "markdown": text,
                "final_url": r.url, "method": "requests-text"}

    def _fetch_playwright(self, url):
        from playwright.sync_api import sync_playwright
        with Spinner(f"Headless GET {url[:50]}") as sp:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=self.headless)
                ctx = browser.new_context(
                    user_agent=DEFAULT_UA,
                    viewport={"width": 1400, "height": 900},
                    java_script_enabled=True,
                )
                page = ctx.new_page()
                page.set_default_navigation_timeout(self.nav_timeout)
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception:
                    page.goto(url, wait_until="load")
                page.wait_for_timeout(800)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                sp.update(f"Scrolling {url[:45]}")
                # Scroll window + every overflow:auto container (fix #8)
                scroll_all_js = r"""
                () => {
                    let total = 0;
                    window.scrollTo(0, document.documentElement.scrollHeight);
                    total += document.documentElement.scrollHeight;
                    const all = document.querySelectorAll('*');
                    for (const el of all) {
                        try {
                            const cs = getComputedStyle(el);
                            if (el.scrollHeight > el.clientHeight + 10 &&
                                /auto|scroll/.test(cs.overflowY || cs.overflow)) {
                                el.scrollTop = el.scrollHeight;
                                total += el.scrollHeight;
                            }
                        } catch (e) {}
                    }
                    return total;
                }
                """
                last_total = -1
                stable = 0
                for _ in range(self.max_scroll_passes):
                    try:
                        total = page.evaluate(scroll_all_js)
                    except Exception:
                        total = 0
                    page.wait_for_timeout(self.scroll_wait_ms)
                    if total == last_total:
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0
                    last_total = total

                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass

                html = page.content()
                final_url = page.url
                inner = page.evaluate("() => document.body ? document.body.innerText : ''")
                browser.close()

        md = self._html_to_markdown(html, url=final_url) or inner
        return {"url": url, "html": html, "text": inner, "markdown": md,
                "final_url": final_url, "method": "playwright"}

    def _fetch_requests(self, url):
        req = _require("requests", "pip install requests")
        with Spinner(f"GET {url[:50]}"):
            r = req.get(url, headers={"User-Agent": DEFAULT_UA, "Accept": "*/*"},
                        timeout=30, allow_redirects=True)
            r.raise_for_status()
        html = r.text
        md = self._html_to_markdown(html, url=r.url)
        return {"url": url, "html": html, "text": "", "markdown": md,
                "final_url": r.url, "method": "requests"}

    @staticmethod
    def _html_to_markdown(html, url=""):
        """Prefer trafilatura main-content extraction; fall back to html2text."""
        if not html:
            return ""
        # Tier 1: trafilatura strips sidebar/footer/nav (fix #9)
        traf = _try_import("trafilatura", "pip install trafilatura")
        if traf:
            try:
                got = traf.extract(html, include_links=True, include_tables=True,
                                   include_comments=False, output_format="markdown",
                                   url=url, favor_recall=True)
                if got and len(got) > 200:
                    return got
            except Exception:
                pass

        # Tier 2: bs4 cleanup + html2text
        bs4 = _try_import("bs4", "pip install beautifulsoup4")
        h2t = _try_import("html2text", "pip install html2text")
        text = html
        if bs4:
            from bs4 import BeautifulSoup
            parser = "lxml" if _try_import("lxml", "pip install lxml") else "html.parser"
            soup = BeautifulSoup(html, parser)
            for sel in ["script", "style", "noscript", "iframe", "svg",
                        "nav", "footer", "aside"]:
                for el in soup.select(sel):
                    el.decompose()
            for cls in ["sidebar","side-bar","navbar","nav-bar","toc",
                        "table-of-contents","site-footer","breadcrumbs",
                        "cookie","consent"]:
                for el in soup.select(f"[class*='{cls}'], [id*='{cls}']"):
                    el.decompose()
            text = str(soup)
        if h2t:
            conv = h2t.HTML2Text()
            conv.body_width = 0
            conv.ignore_images = False
            conv.ignore_links = False
            conv.protect_links = True
            return conv.handle(text)
        if bs4:
            from bs4 import BeautifulSoup
            return BeautifulSoup(text, "html.parser").get_text("\n")
        return text

    def crawl(self, root_url, max_pages=None):
        """BFS same-origin crawl from root (fix #6)."""
        if max_pages is None:
            max_pages = self.crawl_pages
        if max_pages <= 1:
            return [self.fetch(root_url)]

        root = root_url if root_url.startswith("http") else f"https://{root_url}"
        base_host = urllib.parse.urlparse(root).netloc.lower()
        base_path = urllib.parse.urlparse(root).path.rstrip("/")
        path_prefix = ""
        for p in ("/docs","/documentation","/guide","/guides","/api",
                  "/manual","/reference","/help"):
            if p in base_path:
                idx = base_path.find(p)
                path_prefix = base_path[: idx + len(p)]
                break

        seen = {self._normalize_url(root)}
        queue = [root]
        results = []
        print(f"   🕸  Crawling {base_host}"
              f"{' (prefix='+path_prefix+')' if path_prefix else ''} "
              f"up to {max_pages} pages")
        while queue and len(results) < max_pages:
            url = queue.pop(0)
            try:
                r = self.fetch(url)
            except Exception as e:
                print(f"   ⚠ crawl: {url} -> {type(e).__name__}: {e}")
                continue
            results.append(r)
            html = r.get("html") or ""
            for link in self._extract_links(html, url):
                pu = urllib.parse.urlparse(link)
                if pu.netloc.lower() != base_host:
                    continue
                if any(link.lower().endswith(ext) for ext in _NOFOLLOW_EXT):
                    continue
                if path_prefix and not pu.path.startswith(path_prefix):
                    continue
                nl = self._normalize_url(link)
                if nl in seen:
                    continue
                if len(seen) >= max_pages * 3:
                    continue
                seen.add(nl)
                queue.append(link)
        print(f"   ✓ Crawled {len(results)} page(s) from {base_host}")
        return results

    @staticmethod
    def _normalize_url(u):
        pu = urllib.parse.urlparse(u)
        clean = pu._replace(fragment="").geturl()
        return clean.rstrip("/").lower()

    @staticmethod
    def _extract_links(html, base):
        bs4 = _try_import("bs4", "pip install beautifulsoup4")
        if not bs4 or not html:
            return []
        from bs4 import BeautifulSoup
        parser = "lxml" if _try_import("lxml", "pip install lxml") else "html.parser"
        soup = BeautifulSoup(html, parser)
        out = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#","javascript:","mailto:","tel:")):
                continue
            out.append(urllib.parse.urljoin(base, href))
        return out

    def fetch_or_crawl(self, url, force_single=False, max_pages=None):
        if force_single or (max_pages is not None and max_pages <= 1):
            return [self.fetch(url)]
        pu = urllib.parse.urlparse(url if url.startswith("http") else f"https://{url}")
        path = pu.path.lower()
        is_docs = any(t in path for t in ("/docs","/documentation","/guide",
                                          "/reference","/manual","/api/")) \
                  or any(t in pu.netloc.lower() for t in ("docs.","gitbook.io",
                                                          "readthedocs",
                                                          "developer.","developers."))
        if is_docs:
            return self.crawl(url, max_pages=max_pages)
        return [self.fetch(url)]
# ─────────────────────────────────────────────────────────────
# GITHUB FETCHER — full clone, cached, submodule-aware, size-bounded
# ─────────────────────────────────────────────────────────────

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "venv", ".venv", "env", "dist", "build", "target",
    ".idea", ".vscode", ".eggs", ".ipynb_checkpoints", "site-packages",
}

BINARY_EXTS = {
    ".png",".jpg",".jpeg",".gif",".webp",".bmp",".ico",".tif",".tiff",
    ".pdf",".zip",".gz",".tar",".tgz",".bz2",".xz",".7z",".rar",
    ".mp3",".mp4",".wav",".ogg",".webm",".mov",".avi",".mkv",
    ".ttf",".otf",".woff",".woff2",".eot",
    ".so",".dylib",".dll",".exe",".o",".a",".pyc",".pyo",
    ".whl",".egg",
    ".class",".jar",".onnx",".pt",".pth",".safetensors",".bin",
    ".npz",".npy",".h5",".hdf5",".parquet",".arrow",
}

# Priority order for file content inclusion (fix #12)
FILE_PRIORITY_PATTERNS = [
    (re.compile(r"(^|/)README", re.I), 0),
    (re.compile(r"\.md$|\.mdx$|\.rst$", re.I), 1),
    (re.compile(r"(^|/)(setup\.py|pyproject\.toml|package\.json|Cargo\.toml|go\.mod|build\.gradle|pom\.xml)$", re.I), 2),
    (re.compile(r"(^|/)requirements.*\.txt$|(^|/)Pipfile|(^|/)poetry\.lock$", re.I), 3),
    (re.compile(r"(^|/)config|\.toml$|\.yaml$|\.yml$", re.I), 4),
    (re.compile(r"model|architecture|network", re.I), 5),
    (re.compile(r"train|inference|infer|loss|optim", re.I), 6),
    (re.compile(r"(^|/)src/|(^|/)lib/|(^|/)core/|(^|/)main\.|(^|/)index\.", re.I), 7),
    (re.compile(r"\.py$|\.ts$|\.tsx$|\.js$|\.jsx$|\.rs$|\.go$|\.java$|\.kt$", re.I), 8),
    (re.compile(r"(^|/)examples?/|(^|/)demos?/", re.I), 10),
    (re.compile(r"(^|/)docs?/", re.I), 11),
    (re.compile(r"(^|/)tests?/|test_|_test\.|\.test\.|spec_|_spec\.", re.I), 90),
]


def _file_priority(path):
    for pat, score in FILE_PRIORITY_PATTERNS:
        if pat.search(path):
            return score
    return 50


@dataclass
class RepoDump:
    owner: str
    repo: str
    url: str
    clone_dir: str
    files: list
    binaries: list
    tree: str
    extra_text: str = ""


class GitHubFetcher:
    """Full clone, submodule-aware, cached, size-bounded, timed."""

    def __init__(self, tmpdir=None, github_token=None, clone_timeout=300,
                 recurse_submodules=True, max_repo_mb=800):
        self.tmpdir = tmpdir or tempfile.mkdtemp(prefix="sf_gh_")
        self.token = github_token or os.environ.get("GITHUB_TOKEN")
        self.clone_timeout = clone_timeout
        self.recurse_submodules = recurse_submodules
        self.max_repo_mb = max_repo_mb
        os.makedirs(self.tmpdir, exist_ok=True)
        self._repo_cache = {}  # (owner, repo, ref) → RepoDump (fix #13)
        # Auto-cleanup via finalizer (fix #19)
        self._finalizer = weakref.finalize(self, self._cleanup_paths, [self.tmpdir])

    @staticmethod
    def _cleanup_paths(paths):
        for p in paths:
            shutil.rmtree(p, ignore_errors=True)

    def cleanup(self):
        self._finalizer()

    def fetch(self, spec):
        if spec.kind == "github_repo":
            return self._fetch_repo(spec.extra["owner"], spec.extra["repo"],
                                    spec.extra.get("ref"))
        if spec.kind == "github_blob":
            return self._fetch_blob(spec)
        if spec.kind == "github_pr":
            return self._fetch_pr(spec)
        if spec.kind == "github_org":
            return self._fetch_org(spec)
        raise ValueError(f"Not a github kind: {spec.kind}")

    def _clone(self, owner, repo, ref=None, shallow=False):
        url = f"https://github.com/{owner}/{repo}.git"
        if self.token:
            url = url.replace("https://", f"https://{self.token}@")
        dest = os.path.join(self.tmpdir, f"{owner}__{repo}__{short_hash(ref or '')}")
        if os.path.exists(dest):
            shutil.rmtree(dest, ignore_errors=True)

        cmd = ["git", "clone"]
        if shallow:
            cmd += ["--depth", "1", "--single-branch"]
        if self.recurse_submodules:
            cmd += ["--recurse-submodules", "--shallow-submodules"]
        cmd += [url, dest]

        with Spinner(f"git clone {owner}/{repo}{' (shallow)' if shallow else ''}"):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   timeout=self.clone_timeout)
            except subprocess.TimeoutExpired:
                shutil.rmtree(dest, ignore_errors=True)
                if not shallow:
                    print(f"   ↳ full clone timed out, retrying shallow")
                    return self._clone(owner, repo, ref, shallow=True)
                raise RuntimeError(
                    f"git clone timed out after {self.clone_timeout}s for {owner}/{repo}"
                )
        if r.returncode != 0:
            if not shallow:
                print(f"   ↳ full clone failed, retrying shallow")
                return self._clone(owner, repo, ref, shallow=True)
            raise RuntimeError(f"git clone failed: {r.stderr.strip()[:300]}")

        if ref:
            with Spinner(f"checkout {ref[:30]}"):
                subprocess.run(["git", "-C", dest, "fetch", "origin", ref],
                               capture_output=True, text=True, timeout=120)
                subprocess.run(["git", "-C", dest, "checkout", ref],
                               capture_output=True, text=True, timeout=60)
        return dest

    def _repo_size_bytes(self, root):
        total = 0
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if d not in SKIP_DIRS]
            for fn in fns:
                try:
                    total += os.path.getsize(os.path.join(dp, fn))
                except Exception:
                    pass
        return total

    def _walk(self, root, priority_cap=None):
        text_files = []
        binary_files = []
        tree_lines = []
        root_path = Path(root)

        def emit_tree(d, depth=0):
            if depth > 8:
                return
            try:
                entries = sorted(d.iterdir(),
                                 key=lambda e: (not e.is_dir(), e.name.lower()))
            except Exception:
                return
            for e in entries:
                if e.is_dir() and e.name in SKIP_DIRS:
                    continue
                indent = "  " * depth
                if e.is_dir():
                    tree_lines.append(f"{indent}{e.name}/")
                    emit_tree(e, depth + 1)
                else:
                    try:
                        sz = e.stat().st_size
                        tree_lines.append(f"{indent}{e.name}  ({sizeof_fmt(sz)})")
                    except Exception:
                        tree_lines.append(f"{indent}{e.name}")
        emit_tree(root_path)

        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in sorted(filenames):
                fp = Path(dirpath) / fn
                try:
                    rel = str(fp.relative_to(root_path))
                except ValueError:
                    continue
                try:
                    sz = fp.stat().st_size
                except Exception:
                    continue
                ext = fp.suffix.lower()
                if ext in BINARY_EXTS:
                    binary_files.append((rel, sz))
                    continue
                if priority_cap is not None and _file_priority(rel) > priority_cap:
                    binary_files.append((rel, sz))
                    continue
                try:
                    with open(fp, "rb") as f:
                        head = f.read(8192)
                except Exception:
                    binary_files.append((rel, sz))
                    continue
                if b"\x00" in head:
                    binary_files.append((rel, sz))
                    continue
                try:
                    raw = fp.read_bytes()
                    text = raw.decode("utf-8", errors="replace")
                except Exception:
                    binary_files.append((rel, sz))
                    continue
                if ext == ".ipynb":
                    text = self._flatten_ipynb(text)
                text_files.append((rel, text))

        # Priority-sort so downstream truncation keeps the important stuff
        text_files.sort(key=lambda t: (_file_priority(t[0]), t[0]))
        return text_files, binary_files, "\n".join(tree_lines)

    @staticmethod
    def _flatten_ipynb(content):
        try:
            nb = json.loads(content)
        except Exception:
            return content
        out = []
        for i, c in enumerate(nb.get("cells", [])):
            src = "".join(c.get("source", []))
            if src.strip():
                out.append(f"# Cell {i+1} [{c.get('cell_type', '?')}]\n{src}")
            for output in c.get("outputs", []):
                txt = output.get("text") or output.get("data", {}).get("text/plain")
                if isinstance(txt, list):
                    txt = "".join(txt)
                if isinstance(txt, str) and txt.strip():
                    out.append(f"# Output of cell {i+1}\n{txt[:2000]}")
        return "\n\n".join(out)

    def _fetch_repo(self, owner, repo, ref=None):
        key = (owner.lower(), repo.lower(), ref or "")
        if key in self._repo_cache:
            print(f"   ♻️  Reusing cached clone of {owner}/{repo}")
            return self._repo_cache[key]

        dest = self._clone(owner, repo, ref, shallow=False)
        size = self._repo_size_bytes(dest)
        priority_cap = None
        if size > self.max_repo_mb * 1024 * 1024:
            print(f"   ⚠ Repo size {sizeof_fmt(size)} > {self.max_repo_mb}MB limit")
            print(f"   ↳ falling back to priority-filtered subset (priority<=8)")
            priority_cap = 8

        text_files, bins, tree = self._walk(dest, priority_cap=priority_cap)
        print(f"   ✓ {len(text_files)} text files, {len(bins)} binary/excluded")
        dump = RepoDump(owner=owner, repo=repo,
                        url=f"https://github.com/{owner}/{repo}",
                        clone_dir=dest, files=text_files,
                        binaries=bins, tree=tree)
        self._repo_cache[key] = dump
        return dump

    def _fetch_blob(self, spec):
        owner = spec.extra["owner"]
        repo = spec.extra["repo"]
        ref = spec.extra.get("ref")
        path = spec.extra.get("path", "")
        dump = self._fetch_repo(owner, repo, ref=ref)
        if path:
            found = False
            for rel, content in dump.files:
                if rel == path:
                    dump.extra_text = (
                        f"### Primary file (from blob URL): `{path}`\n\n{content}\n"
                    )
                    found = True
                    break
            if not found:
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{ref}/{path}"
                req = _try_import("requests", "pip install requests")
                if req:
                    try:
                        r = req.get(raw_url, headers={"User-Agent": DEFAULT_UA},
                                    timeout=30)
                        if r.ok:
                            dump.extra_text = (
                                f"### Primary file (raw): `{path}`\n\n{r.text}\n"
                            )
                            dump.files.append((path, r.text))
                    except Exception:
                        pass
        return dump

    def _fetch_pr(self, spec):
        owner = spec.extra["owner"]; repo = spec.extra["repo"]; pr = spec.extra["pr"]
        req = _try_import("requests", "pip install requests")
        if not req:
            print(f"   ⚠ requests missing — falling back to repo clone only")
            return self._fetch_repo(owner, repo)

        headers = {"User-Agent": DEFAULT_UA, "Accept": "application/vnd.github+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        api = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr}"
        pr_body = ""
        head_ref = None
        try:
            with Spinner(f"PR #{pr} metadata"):
                r = req.get(api, headers=headers, timeout=30)
                if r.ok:
                    j = r.json()
                    pr_body = f"# PR #{pr}: {j.get('title','')}\n\n{j.get('body','') or ''}\n"
                    head_ref = (j.get("head") or {}).get("ref")
        except Exception as e:
            print(f"   ⚠ PR metadata fetch failed: {e}")

        diff = ""
        try:
            with Spinner(f"PR #{pr} diff"):
                r = req.get(api,
                            headers={**headers, "Accept": "application/vnd.github.v3.diff"},
                            timeout=60)
                if r.ok:
                    diff = r.text
        except Exception as e:
            print(f"   ⚠ PR diff fetch failed: {e}")

        comments_parts = []
        try:
            with Spinner(f"PR #{pr} comments"):
                r = req.get(f"{api}/comments", headers=headers, timeout=30)
                if r.ok:
                    for c in r.json()[:200]:
                        comments_parts.append(
                            f"### {c.get('user',{}).get('login','?')} on {c.get('path','')}:\n{c.get('body','')}"
                        )
                r2 = req.get(f"https://api.github.com/repos/{owner}/{repo}/issues/{pr}/comments",
                             headers=headers, timeout=30)
                if r2.ok:
                    for c in r2.json()[:200]:
                        comments_parts.append(
                            f"### {c.get('user',{}).get('login','?')}:\n{c.get('body','')}"
                        )
        except Exception as e:
            print(f"   ⚠ PR comments fetch failed: {e}")
        comments = "\n\n".join(comments_parts)

        dump = self._fetch_repo(owner, repo, ref=head_ref)
        extra = []
        if pr_body:
            extra.append(pr_body)
        if diff:
            extra.append(f"## Diff\n\n```diff\n{diff}\n```")
        if comments:
            extra.append(f"## Review comments\n\n{comments}")
        dump.extra_text = ((dump.extra_text + "\n\n") if dump.extra_text else "") + \
                          "\n\n".join(extra)
        return dump

    def _fetch_org(self, spec, max_repos=25):
        owner = spec.extra["owner"]
        req = _try_import("requests", "pip install requests")
        repos = []
        if req:
            headers = {"User-Agent": DEFAULT_UA, "Accept": "application/vnd.github+json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            for api_pattern in (f"https://api.github.com/orgs/{owner}/repos",
                                f"https://api.github.com/users/{owner}/repos"):
                try:
                    with Spinner(f"list {owner}/* via {api_pattern.split('/')[-2]}"):
                        page = 1
                        while page < 10 and len(repos) < max_repos:
                            r = req.get(f"{api_pattern}?per_page=100&page={page}&type=public",
                                        headers=headers, timeout=30)
                            if not r.ok:
                                break
                            j = r.json()
                            if not j:
                                break
                            for it in j:
                                if isinstance(it, dict) and not it.get("fork") and not it.get("archived"):
                                    repos.append(it["name"])
                                if len(repos) >= max_repos:
                                    break
                            page += 1
                    if repos:
                        break
                except Exception as e:
                    print(f"   ⚠ org list failed: {e}")
                    continue

        if not repos:
            print(f"   ⚠ No repos discovered for org {owner}")
            return []

        print(f"   ✓ Found {len(repos)} repos under {owner}, cloning all (cap={max_repos})")
        dumps = []
        for name in repos[:max_repos]:
            try:
                dumps.append(self._fetch_repo(owner, name))
            except Exception as e:
                print(f"   ⚠ {owner}/{name}: {e}")
        return dumps


# ─────────────────────────────────────────────────────────────
# ARXIV FETCHER
# ─────────────────────────────────────────────────────────────

class ArxivFetcher:
    def __init__(self, web, tmpdir=None):
        self.web = web
        self.tmpdir = tmpdir or tempfile.mkdtemp(prefix="sf_arxiv_")
        os.makedirs(self.tmpdir, exist_ok=True)
        self._finalizer = weakref.finalize(self, GitHubFetcher._cleanup_paths,
                                           [self.tmpdir])

    def cleanup(self):
        self._finalizer()

    def fetch(self, arxiv_id):
        aid = arxiv_id.strip().split("v")[0]
        html_text = ""
        for src in (f"https://ar5iv.labs.arxiv.org/html/{aid}",
                    f"https://arxiv.org/html/{aid}"):
            try:
                r = self.web.fetch(src)
                if r.get("markdown") and len(r["markdown"]) > 1000:
                    html_text = r["markdown"]
                    print(f"   ✓ HTML version: {len(html_text):,} chars from {src}")
                    break
            except Exception as e:
                print(f"   ⚠ {src}: {e}")

        pdf_text = ""
        pdf_path = os.path.join(self.tmpdir, f"{aid.replace('/','_')}.pdf")
        try:
            url = f"https://arxiv.org/pdf/{aid}.pdf"
            with Spinner(f"PDF {url}"):
                req = urllib.request.Request(url, headers={"User-Agent": DEFAULT_UA})
                with urllib.request.urlopen(req, timeout=60) as r, open(pdf_path,"wb") as f:
                    f.write(r.read())
            pdf_text = self._extract_pdf(pdf_path, max_pages=999)
            print(f"   ✓ PDF: {len(pdf_text):,} chars")
        except Exception as e:
            print(f"   ⚠ PDF fetch failed: {e}")

        parts = []
        if html_text:
            parts.append(f"## ArXiv HTML version\n\n{html_text}")
        if pdf_text:
            parts.append(f"## ArXiv PDF version\n\n{pdf_text}")
        return {"arxiv_id": aid, "html": html_text, "pdf_text": pdf_text,
                "pdf_path": pdf_path if pdf_text else None,
                "combined": "\n\n".join(parts)}

    @staticmethod
    def _extract_pdf(path, max_pages=999):
        fitz = _try_import("fitz", "pip install PyMuPDF")
        if not fitz:
            return ""
        doc = fitz.open(path)
        pages = min(len(doc), max_pages)
        parts = []
        for i in range(pages):
            parts.append(f"--- PAGE {i+1}/{pages} ---\n{doc[i].get_text('text')}")
        doc.close()
        return "\n".join(parts)


# ─────────────────────────────────────────────────────────────
# UNIFIED BUNDLE + DISK PERSISTENCE
# ─────────────────────────────────────────────────────────────

@dataclass
class SourceBundle:
    spec: SourceSpec
    text: str
    files: dict = field(default_factory=dict)
    on_disk_path: str = ""
    skill_basename: str = ""
    metadata: dict = field(default_factory=dict)


def save_bundle(bundle, output_dir):
    """Persist full downloaded source, path-traversal-safe (fix #16)."""
    sdir = Path(output_dir) / ".sources" / bundle.skill_basename
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "source.txt").write_text(bundle.text, encoding="utf-8")
    (sdir / "metadata.json").write_text(
        json.dumps({"kind": bundle.spec.kind, "url": bundle.spec.url,
                    "raw": bundle.spec.raw, **bundle.metadata}, indent=2),
        encoding="utf-8",
    )
    if bundle.files:
        files_dir = sdir / "files"
        files_dir.mkdir(exist_ok=True)
        files_base_abs = os.path.abspath(str(files_dir))
        for relpath, content in bundle.files.items():
            try:
                target = safe_join(files_base_abs, relpath)
                if not target:
                    print(f"   ⚠ path-traversal blocked: {relpath}")
                    continue
                Path(os.path.dirname(target)).mkdir(parents=True, exist_ok=True)
                Path(target).write_text(content, encoding="utf-8")
            except Exception:
                continue
    bundle.on_disk_path = str(sdir)
    print(f"   📦 Source preserved at {sdir} "
          f"({sizeof_fmt(len(bundle.text.encode('utf-8')))})")
# ─────────────────────────────────────────────────────────────
# DOWNLOAD ORCHESTRATOR
# ─────────────────────────────────────────────────────────────

class Downloader:
    def __init__(self, web=None, gh=None, arxiv=None, max_org_repos=25, crawl_pages=25):
        self.web = web or WebDownloader(crawl_pages=crawl_pages)
        self.gh = gh or GitHubFetcher()
        self.arxiv = arxiv or ArxivFetcher(self.web)
        self.max_org_repos = max_org_repos
        self.crawl_pages = crawl_pages

    def download(self, spec):
        if spec.kind == "github_repo":
            return self._bundle_repo(self.gh._fetch_repo(spec.extra["owner"],
                                                        spec.extra["repo"],
                                                        spec.extra.get("ref")),
                                     spec)
        if spec.kind == "github_blob":
            return self._bundle_repo(self.gh._fetch_blob(spec), spec)
        if spec.kind == "github_pr":
            return self._bundle_repo(self.gh._fetch_pr(spec), spec)
        if spec.kind == "github_org":
            dumps = self.gh._fetch_org(spec, max_repos=self.max_org_repos)
            return [self._bundle_repo(d, SourceSpec(
                raw=f"{spec.raw}#{d.repo}",
                kind="github_repo",
                url=d.url,
                extra={"owner": d.owner, "repo": d.repo},
            )) for d in dumps]
        if spec.kind == "arxiv":
            r = self.arxiv.fetch(spec.extra["arxiv_id"])
            return SourceBundle(
                spec=spec, text=r["combined"],
                files={"arxiv_html.md": r["html"], "arxiv_pdf.txt": r["pdf_text"]},
                skill_basename=f"arxiv_{r['arxiv_id'].replace('/','_')}",
                metadata={"arxiv_id": r["arxiv_id"]},
            )
        if spec.kind == "pdf":
            return self._bundle_pdf(spec)
        if spec.kind == "web":
            return self._bundle_web(spec)
        if spec.kind == "llms_manifest":
            return self._bundle_llms_manifest(spec)
        if spec.kind == "text":
            r = self.web.fetch(spec.url)
            return SourceBundle(
                spec=spec, text=r["markdown"] or r["text"] or r["html"],
                files={Path(spec.url).name or "page.txt": r["markdown"] or r["text"]},
                skill_basename=slugify(spec.url),
                metadata={"final_url": r.get("final_url")},
            )
        raise ValueError(f"Cannot download kind={spec.kind}")

    def _bundle_repo(self, dump, spec):
        parts = [
            f"# Repository: {dump.owner}/{dump.repo}",
            f"URL: {dump.url}",
            f"Files: {len(dump.files)} text, {len(dump.binaries)} binary/excluded",
            "",
            "## Directory tree",
            "```",
            dump.tree,
            "```",
        ]
        if dump.binaries:
            parts.append("\n## Binary / excluded inventory")
            for rel, sz in dump.binaries[:200]:
                parts.append(f"- `{rel}` ({sizeof_fmt(sz)})")
            if len(dump.binaries) > 200:
                parts.append(f"- … +{len(dump.binaries) - 200} more")
        if dump.extra_text:
            parts.append("\n## Extra context\n")
            parts.append(dump.extra_text)
        parts.append("\n## File contents (priority-ordered)\n")
        files_dict = {}
        for rel, content in dump.files:
            ext = Path(rel).suffix.lower().lstrip(".")
            parts.append(f"### `{rel}`\n\n```{ext}\n{content}\n```\n")
            files_dict[rel] = content
        text = "\n".join(parts)
        return SourceBundle(
            spec=spec, text=text, files=files_dict,
            skill_basename=f"{dump.owner}_{dump.repo}",
            metadata={"owner": dump.owner, "repo": dump.repo,
                      "n_files": len(dump.files), "n_binaries": len(dump.binaries)},
        )

    def _bundle_web(self, spec):
        pages = self.web.fetch_or_crawl(spec.url, max_pages=self.crawl_pages)
        if not pages:
            return SourceBundle(spec=spec, text="", files={},
                                skill_basename=slugify(spec.url), metadata={})
        if len(pages) == 1:
            r = pages[0]
            md = r["markdown"] or r["text"] or r["html"]
            files_dict = {"page.md": md}
            if r.get("html"):
                files_dict["page.html"] = r["html"]
            return SourceBundle(
                spec=spec, text=md, files=files_dict,
                skill_basename=slugify(spec.url),
                metadata={"final_url": r.get("final_url"),
                          "method": r.get("method"), "pages_crawled": 1},
            )
        text_parts = []
        files_dict = {}
        for i, r in enumerate(pages, 1):
            md = r.get("markdown") or r.get("text") or ""
            url = r.get("final_url") or r.get("url") or ""
            text_parts.append(f"\n\n===== PAGE {i}/{len(pages)} — {url} =====\n\n{md}")
            files_dict[f"page_{i:03d}_{slugify(url)[:60]}.md"] = md
        return SourceBundle(
            spec=spec, text="".join(text_parts), files=files_dict,
            skill_basename=slugify(spec.url),
            metadata={"pages_crawled": len(pages),
                      "page_urls": [r.get("final_url") for r in pages]},
        )

    def _bundle_pdf(self, spec):
        path = spec.url
        if spec.extra.get("is_url"):
            tmp = os.path.join(self.arxiv.tmpdir, slugify(path) + ".pdf")
            req = urllib.request.Request(path, headers={"User-Agent": DEFAULT_UA})
            with Spinner(f"GET {path[:50]}"):
                with urllib.request.urlopen(req, timeout=60) as r, open(tmp,"wb") as f:
                    f.write(r.read())
            path = tmp
        text = ArxivFetcher._extract_pdf(path, max_pages=999)
        return SourceBundle(
            spec=spec, text=text, files={"document.txt": text},
            skill_basename=slugify(Path(path).stem),
            metadata={"pdf_path": path},
        )

    def _bundle_llms_manifest(self, spec):
        """llms.txt is a URL manifest — fetch each listed URL (fix #7)."""
        req = _require("requests", "pip install requests")
        with Spinner(f"GET {spec.url}"):
            r = req.get(spec.url, headers={"User-Agent": DEFAULT_UA}, timeout=30)
            r.raise_for_status()
        manifest_text = r.text
        urls = re.findall(r'https?://[^\s\]\)>"\'<`]+', manifest_text)
        seen = set(); unique_urls = []
        for u in urls:
            u = u.rstrip(".,;)")
            if u in seen:
                continue
            seen.add(u); unique_urls.append(u)
        print(f"   ✓ llms.txt: {len(unique_urls)} URLs found, fetching each")

        files_dict = {"_manifest.txt": manifest_text}
        text_parts = [f"# llms.txt manifest: {spec.url}\n\n{manifest_text}\n\n"]
        cap = min(len(unique_urls), self.crawl_pages * 2)
        for i, u in enumerate(unique_urls[:cap], 1):
            try:
                page = self.web.fetch(u)
                md = page.get("markdown") or page.get("text") or ""
                text_parts.append(f"\n\n===== MANIFEST ENTRY {i}/{cap} — {u} =====\n\n{md}")
                files_dict[f"entry_{i:03d}_{slugify(u)[:60]}.md"] = md
            except Exception as e:
                print(f"   ⚠ {u}: {e}")
        return SourceBundle(
            spec=spec, text="".join(text_parts), files=files_dict,
            skill_basename=slugify(spec.url),
            metadata={"manifest_urls": unique_urls[:cap]},
        )


# ─────────────────────────────────────────────────────────────
# LLM PROVIDER
# ─────────────────────────────────────────────────────────────

def parse_json_from_llm(text):
    text = (text or "").strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    first, last = text.find('{'), text.rfind('}')
    if first != -1 and last > first:
        try:
            return json.loads(text[first:last+1])
        except json.JSONDecodeError:
            pass
    return None


def clean_llm_output(text):
    text = (text or "").strip()
    for pfx in ("```markdown", "```md", "```yaml", "```"):
        if text.startswith(pfx):
            text = text[len(pfx):].strip()
            break
    if text.endswith("```"):
        text = text[:-3].strip()
    return text


class LLMProvider:
    def __init__(self, provider="gemini", model=None, api_key=None):
        self.provider = provider
        if provider == "anthropic":
            anthropic = _require("anthropic", "pip install anthropic")
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not self.api_key:
                print("❌ Set ANTHROPIC_API_KEY"); sys.exit(1)
            self.client = anthropic.Anthropic(api_key=self.api_key)
            self.model = model or "claude-sonnet-4-20250514"
        elif provider == "gemini":
            genai = _require("google.generativeai", "pip install google-generativeai")
            self.api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
            if not self.api_key:
                print("❌ Set GEMINI_API_KEY"); sys.exit(1)
            genai.configure(api_key=self.api_key)
            self._genai = genai
            self.model = model or "gemini-2.5-flash"
        elif provider == "openrouter":
            openai = _require("openai", "pip install openai")
            self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
            if not self.api_key:
                print("❌ Set OPENROUTER_API_KEY"); sys.exit(1)
            self.client = openai.OpenAI(base_url="https://openrouter.ai/api/v1",
                                        api_key=self.api_key)
            self.model = model or "google/gemini-2.5-flash"
        else:
            print(f"❌ Unknown provider: {provider}"); sys.exit(1)

    def generate(self, system_prompt, user_message, max_tokens=16000, json_mode=False):
        for attempt in range(3):
            try:
                return self._generate_inner(system_prompt, user_message,
                                            max_tokens, json_mode)
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("429","rate","quota","resource_exhausted")):
                    wait = (attempt + 1) * 20
                    print(f"   ⏳ Rate limited, waiting {wait}s")
                    time.sleep(wait)
                    continue
                if "finish_reason" in msg or "valid `part`" in msg:
                    print("   ⚠ Safety filter, returning empty")
                    return ""
                if attempt < 2:
                    print(f"   ⚠ LLM error: {e}; retrying")
                    time.sleep(5)
                    continue
                raise
        return ""

    def _generate_inner(self, system_prompt, user_message, max_tokens, json_mode):
        if self.provider == "anthropic":
            with Spinner(f"Anthropic {self.model[:25]}"):
                m = self.client.messages.create(
                    model=self.model, max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role":"user","content":user_message}],
                )
            print(f"   [{self.provider}] {m.usage.input_tokens:,} in / {m.usage.output_tokens:,} out")
            return m.content[0].text

        if self.provider == "gemini":
            gm = self._genai.GenerativeModel(self.model, system_instruction=system_prompt)
            cfg = {"max_output_tokens": max_tokens, "temperature": 0.2}
            if json_mode:
                cfg["response_mime_type"] = "application/json"
            with Spinner(f"Gemini {self.model}"):
                try:
                    resp = gm.generate_content(
                        user_message,
                        generation_config=self._genai.types.GenerationConfig(**cfg),
                    )
                except TypeError:
                    cfg.pop("response_mime_type", None)
                    resp = gm.generate_content(
                        user_message,
                        generation_config=self._genai.types.GenerationConfig(**cfg),
                    )
            if not resp.candidates or not resp.candidates[0].content.parts:
                fr = resp.candidates[0].finish_reason if resp.candidates else "?"
                print(f"   ⚠ Gemini empty (finish_reason={fr})")
                return ""
            return resp.text

        if self.provider == "openrouter":
            kwargs = dict(model=self.model,
                          messages=[{"role":"system","content":system_prompt},
                                    {"role":"user","content":user_message}],
                          max_tokens=max_tokens, temperature=0.2)
            if json_mode:
                kwargs["response_format"] = {"type":"json_object"}
            with Spinner(f"OpenRouter {self.model.split('/')[-1][:25]}"):
                resp = self.client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        return ""


# ─────────────────────────────────────────────────────────────
# PROMPTS + SMART TRUNCATION
# ─────────────────────────────────────────────────────────────

REPO_SYSTEM_PROMPT = r"""You are SkillForge, generating a SKILL.md from a GitHub repository.

CRITICAL RULES (DO NOT VIOLATE):
1. Use ONLY information present in the source dump below. NEVER guess values.
2. If a value (a number, hyperparameter, version, benchmark, framework, API
   shape) is not explicitly stated in the source, say "not specified in
   source" — do NOT invent it.
3. Reproduce file paths, class names, function names, and constants VERBATIM.
4. When you quote code, use exact lines from the dump.
5. Tables must contain ONLY numbers that appear in the source.

OUTPUT FORMAT:
---
name: <kebab-case-id>
description: <120-180 words with explicit trigger phrases for when to use it. Use ONLY facts from the source.>
---

# <Project name from README>

## 1. What it does
2-4 paragraphs describing what is in the repo, using its own README/docs.

## 2. Architecture
Component table: Component | File | Purpose
Use ONLY components present in the source.

## 3. Key files
For each important file, summary based on actual contents.

## 4. APIs / Functions / Classes
List public surface verbatim from the code.

## 5. Configuration
Hyperparameters / env vars / config keys with actual values from source.

## 6. Dependencies
Versions from requirements / package manifest.

## 7. Usage
Reproduce usage from README; do not invent.

## 8. References
Links found in the repo.

DO NOT wrap in code fences. DO NOT invent metrics."""


WEB_SYSTEM_PROMPT = r"""You are SkillForge, generating a SKILL.md from documentation pages.

CRITICAL RULES:
1. Use ONLY information present in the page content below. NEVER guess.
2. If something is not stated, say "not specified on the source pages".
3. Reproduce identifiers, URLs, and code blocks verbatim.
4. If multiple pages are provided, integrate them — do not just summarize one.

OUTPUT FORMAT:
---
name: <kebab-case>
description: <120-180 words, trigger-phrase heavy. Use only facts present on the pages.>
---

# <page or product title>

Organize the SKILL by the concepts that ACTUALLY appear on the pages. Include
tables, code blocks, and examples verbatim.

DO NOT wrap in code fences. DO NOT invent."""


PAPER_SYSTEM_PROMPT = r"""You are SkillForge, generating a SKILL.md from a research paper.

CRITICAL RULES:
1. Use ONLY information stated in the paper.
2. Reproduce every equation, table, and benchmark number EXACTLY as printed.
3. Do not invent author names, citations, or results.

Sections: paper metadata, problem setup, method, equations, algorithm,
experiments (full tables), results (verbatim numbers), key takeaways,
references.

DO NOT wrap in code fences. DO NOT invent."""


REPAIR_SYSTEM_PROMPT = r"""You are SkillForge's repair agent.

A skill file you previously produced contains UNVERIFIED CLAIMS that do not
appear in the downloaded source. Your job is to fix this — and ONLY this.

REQUIRED ACTIONS:
1. For each line listed below as "Unverified", you must EITHER:
   a) Delete the line / the offending claim entirely, OR
   b) Replace the unverified value with the correct value FROM THE SOURCE.
2. Do NOT invent replacements. If no source-grounded replacement exists,
   delete.
3. Preserve all VERIFIED content. Keep the same YAML frontmatter, headings,
   and overall structure.
4. Output the COMPLETE corrected skill file. Not a diff. Not patches.
5. Do NOT wrap output in code fences."""


def _system_prompt_for(kind):
    if kind in ("arxiv","pdf"):
        return PAPER_SYSTEM_PROMPT
    if kind in ("github_repo","github_blob","github_pr"):
        return REPO_SYSTEM_PROMPT
    return WEB_SYSTEM_PROMPT


def smart_truncate_for_llm(bundle, budget_chars):
    """Priority-ordered truncation (fix #12)."""
    if len(bundle.text) <= budget_chars:
        return bundle.text

    kind = bundle.spec.kind

    if kind in ("github_repo","github_blob","github_pr","github_org"):
        # Files were priority-sorted in _walk. Keep the header + as many
        # priority-ordered sections as fit, drop the rest.
        m = re.search(r'\n## File contents \(priority-ordered\)\n', bundle.text)
        if not m:
            return _head_tail_truncate(bundle.text, budget_chars)
        header = bundle.text[: m.end()]
        body = bundle.text[m.end():]
        sections = re.split(r'(?=^### `)', body, flags=re.MULTILINE)
        kept = []
        used = len(header)
        dropped = 0
        for sec in sections:
            slen = len(sec)
            if used + slen <= budget_chars - 500:
                kept.append(sec)
                used += slen
            else:
                dropped += 1
        elided = (f"\n\n> {dropped} lower-priority file section(s) elided "
                  f"to fit LLM input budget; full source on disk under .sources/.\n\n")
        return header + "".join(kept) + elided

    if kind in ("arxiv","pdf"):
        # Keep abstract+intro (~40%), middle methods chunk, tail (~30%)
        n = len(bundle.text)
        head_chars = int(budget_chars * 0.40)
        tail_chars = int(budget_chars * 0.30)
        mid_chars = budget_chars - head_chars - tail_chars - 300
        head = bundle.text[:head_chars]
        tail = bundle.text[-tail_chars:]
        kw_pos = -1
        for kw in ("Method","Methods","Algorithm","Architecture",
                   "Equation","We propose","Implementation"):
            kp = bundle.text.find(kw, head_chars)
            if kp != -1 and kp < n - tail_chars:
                kw_pos = kp; break
        if kw_pos == -1:
            kw_pos = n // 2
        start = max(head_chars, kw_pos - mid_chars // 2)
        end = min(n - tail_chars, start + mid_chars)
        middle = bundle.text[start:end]
        return (head
                + f"\n\n[... elided {start - head_chars:,} chars; full source on disk ...]\n\n"
                + middle
                + f"\n\n[... elided {n - tail_chars - end:,} chars ...]\n\n"
                + tail)

    return _head_tail_truncate(bundle.text, budget_chars)


def _head_tail_truncate(text, budget):
    if len(text) <= budget:
        return text
    h = int(budget * 0.55)
    t = int(budget * 0.40)
    return (text[:h] + f"\n\n[... {len(text)-h-t:,} chars elided; "
            f"full source on disk ...]\n\n" + text[-t:])


# ─────────────────────────────────────────────────────────────
# SKILL GENERATION + AGENTIC LOOP (fix #11)
# ─────────────────────────────────────────────────────────────

def generate_skill_once(bundle, llm, max_input_chars=350_000, max_output_tokens=16000):
    sp = _system_prompt_for(bundle.spec.kind)
    src_for_llm = smart_truncate_for_llm(bundle, max_input_chars)
    user = (f"Source ({bundle.spec.kind} — {bundle.spec.url}):\n\n"
            f"---BEGIN SOURCE---\n{src_for_llm}\n---END SOURCE---\n\n"
            f"Produce the SKILL.md now. Use ONLY content from the source above.")
    out = llm.generate(sp, user, max_tokens=max_output_tokens)
    return clean_llm_output(out)


def agentic_skill_loop(bundle, llm, max_input_chars=350_000,
                       target_pct_verified=90.0, max_retries=2):
    """Generate, verify, regenerate-on-flag (fix #11)."""
    best_skill = ""
    best_results = []
    best_summary = {"pct_verified": 0.0, "claims_total": 0,
                    "claims_unverified": 0, "lines_flagged": 0,
                    "total_lines_checked": 0, "per_section": []}

    print(f"   📝 Initial generation…")
    skill = generate_skill_once(bundle, llm, max_input_chars=max_input_chars)
    if not skill.strip():
        return skill, [], best_summary

    for attempt in range(max_retries + 1):
        verifier = SourceVerifier(bundle.text)
        results, summary = verifier.verify(skill)
        print(f"   🔬 Round {attempt}: {summary['pct_verified']:.1f}% verified "
              f"({summary['claims_unverified']}/{summary['claims_total']} unverified)")

        if summary["pct_verified"] > best_summary["pct_verified"]:
            best_skill = skill
            best_results = results
            best_summary = summary

        if summary["pct_verified"] >= target_pct_verified:
            print(f"   ✓ Target met ({target_pct_verified}%)")
            return best_skill, best_results, best_summary

        if attempt >= max_retries or not results:
            break

        flagged_block = []
        for r in results[:40]:
            short_claims = ", ".join(c for c in r.claims_unverified[:6])
            if len(r.claims_unverified) > 6:
                short_claims += f", +{len(r.claims_unverified)-6} more"
            flagged_block.append(
                f"- Line {r.line_no}: {r.line[:160]}\n  Unverified claims: {short_claims}"
            )
        flagged = "\n".join(flagged_block)
        src_excerpt = smart_truncate_for_llm(bundle, max_input_chars // 2)
        user = (f"## Current skill file\n\n{skill}\n\n"
                f"## Verifier flagged these lines (claims not found in source):\n\n"
                f"{flagged}\n\n"
                f"## Source (excerpt for reference):\n\n{src_excerpt}\n\n"
                f"Produce the corrected, complete SKILL.md. Remove or replace every "
                f"flagged claim. Do not introduce new unverified claims.")
        print(f"   🔧 Regenerating with {len(results)} flagged lines as feedback…")
        repaired = llm.generate(REPAIR_SYSTEM_PROMPT, user, max_tokens=16000)
        repaired = clean_llm_output(repaired)
        if repaired and len(repaired) > len(skill) * 0.4:
            skill = repaired
        else:
            print(f"   ⚠ Repair returned insufficient content, keeping previous round")
            break

    return best_skill or skill, best_results, best_summary
# ─────────────────────────────────────────────────────────────
# VERIFIER — word-boundary, unicode-normalized, per-section
# ─────────────────────────────────────────────────────────────

NUMBER_RE = re.compile(r'(?<![\w.])(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?(?:%|[a-zA-Z]{1,4})?)(?![\w.])')
INLINE_CODE_RE = re.compile(r'`([^`\n]{2,80})`')
QUOTED_RE = re.compile(r'"([^"\n]{3,80})"')
SQUOTED_RE = re.compile(r"'([^'\n]{3,80})'")
URL_RE = re.compile(r'\bhttps?://[^\s)\]\}"]+', re.IGNORECASE)
PATH_RE = re.compile(r'\b[A-Za-z0-9_.-]+/[A-Za-z0-9_./\-]{2,}')

# camelCase, snake_case, MultiPascalCase, SCREAMING_SNAKE, dotted, alnum+digits
IDENT_RE = re.compile(
    r'\b('
    r'[a-z][a-zA-Z0-9]*[A-Z][a-zA-Z0-9_]+'
    r'|[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+'
    r'|[A-Z][a-z]+(?:[A-Z][a-z]+){1,}'
    r'|[A-Z]{2,}[A-Z0-9_]+'
    r'|[A-Za-z]+(?:\.[A-Za-z][A-Za-z0-9_]+){1,}'
    r'|[A-Za-z]{2,}\d+[A-Za-z0-9]*'
    r')\b'
)

# Common technical acronyms missed by IDENT_RE (fix #15)
KNOWN_ACRONYMS = {
    # ML / DL
    "Adam","AdamW","SGD","RMSprop","Adagrad","LARS","LAMB",
    "ReLU","GELU","SiLU","ELU","SELU","Tanh","Sigmoid","Softmax",
    "BERT","GPT","T5","BART","XLNet","RoBERTa","ViT","CLIP","LoRA","QLoRA",
    "RNN","LSTM","GRU","CNN","MLP","GAN","VAE","NeRF","DDPM","DDIM",
    "PPO","DPO","RLHF","TRPO","SAC","DQN","A2C","A3C","KL","MSE","CE","BCE",
    # Hardware / runtime
    "CUDA","ROCm","NVCC","cuDNN","NCCL","HBM","SRAM","DRAM","TPU","GPU","CPU","FPGA","ASIC",
    "AVX","SIMD","SSE","SSE2","ARM","x86","RISCV",
    # Web / protocols / formats
    "HTTP","HTTPS","REST","gRPC","GraphQL","WebSocket","WebRTC",
    "JSON","YAML","TOML","XML","HTML","CSS","SVG","CSV","TSV","Parquet","Avro",
    "JWT","OAuth","OIDC","SAML","SSO","TLS","SSL","mTLS","DNS","BGP","NAT","CDN",
    "TCP","UDP","ICMP","FTP","SFTP","SMTP","IMAP","POP3","SSH",
    # Crypto / blockchain
    "SHA","MD5","HMAC","AES","RSA","ECDSA","Ed25519","Curve25519","X25519","Schnorr",
    "EVM","SVM","ABI","BIP","ERC","EIP","TPS","UTXO","DeFi","NFT","DAO","ZKP","SNARK","STARK",
    "USDC","USDT","ETH","BTC","SOL","MATIC",
    # Software / cloud
    "CLI","SDK","API","UI","UX","IaC","CI","CD","SLA","SLO","SRE","P99","P95","P50",
    "AWS","GCP","Azure","S3","EC2","RDS","ECS","EKS","GKE","AKS","K8s","KMS","IAM",
    "WASM","WASI","JVM","CLR","FFI","IPC","RPC","MCP",
    "SQL","NoSQL","ORM","CRUD","ACID","CAP","CRDT","WAL","MVCC",
    # Stats / math
    "FLOPs","FLOPS","BPE","WPM","BLEU","ROUGE","METEOR","CER","WER","mAP","IoU",
}

STOPWORDS = {
    "true","false","none","null","this","that","with","from","into","over",
    "the","and","or","not","but","for","you","your","our","its","their",
    "use","using","used","when","where","while","after","before","then",
    "yes","no","unknown","unclear","specified","source","page","section",
    "see","also","note","example","examples","other","various","more","most",
    "many","some","few","like","such","etc","via","along","through","across",
    "include","includes","including","required","optional","default","custom",
}

TRIVIAL_NUMBERS = {"0","1","2","3","4","5","6","7","8","9","10","100","1000"}


@dataclass
class ClaimCheck:
    line_no: int
    line: str
    section: str
    claims_total: int
    claims_unverified: list


def _extract_claims_from_line(line, is_header, is_yaml):
    """Returns list of (value, type), deduplicated by VALUE (fix #21)."""
    found = []
    seen = set()

    def add(value, ctype):
        v = value.strip()
        if not v:
            return
        k = norm_text(v)
        if not k or k in seen or k in STOPWORDS:
            return
        seen.add(k)
        found.append((v, ctype))

    for m in INLINE_CODE_RE.finditer(line):
        v = m.group(1).strip()
        if len(v) >= 2:
            add(v, "code")
    for m in URL_RE.finditer(line):
        add(m.group(0).rstrip(".,;)]}>"), "url")
    for m in PATH_RE.finditer(line):
        v = m.group(0)
        if not v.startswith("http") and "/" in v:
            add(v, "path")
    for m in QUOTED_RE.finditer(line):
        v = m.group(1).strip()
        if len(v) >= 3:
            add(v, "quoted")
    for m in SQUOTED_RE.finditer(line):
        v = m.group(1).strip()
        if len(v) >= 3:
            add(v, "quoted")
    for m in NUMBER_RE.finditer(line):
        v = m.group(1)
        if v in TRIVIAL_NUMBERS:
            continue
        add(v, "number")

    # IDENT is the noisier regex — restrict to body content (not headers/YAML).
    if not is_header and not is_yaml:
        for m in IDENT_RE.finditer(line):
            v = m.group(1)
            if len(v) < 4:
                continue
            add(v, "identifier")
    # Acronyms are well-curated and are the highest-signal hallucination
    # surface inside descriptions (Adam vs SGD, REST vs GraphQL, Ed25519 vs
    # secp256k1) — so we check them in YAML too. Skip only on raw headers.
    if not is_header:
        for acro in KNOWN_ACRONYMS:
            if re.search(r'\b' + re.escape(acro) + r'\b', line):
                add(acro, "acronym")

    return found


class SourceVerifier:
    """Line-by-line check of SKILL.md against the downloaded source.

    Correctness vs v4:
      - Unicode-normalized comparisons (NFKD + ASCII fold).
      - Word-boundary matching for code/identifiers/quoted/acronyms.
      - Digit-aware boundaries for numbers (no "3" inside "13").
      - YAML `description:` IS verified (only name/id/version skipped).
      - Per-section breakdown.
      - In-line claim dedup by value."""

    def __init__(self, source_text):
        self.source = source_text
        self._norm_src = norm_text(source_text)

    def _match_number(self, v):
        try:
            pat = r'(?<![\d.eE+\-])' + re.escape(v) + r'(?![\d.eE])'
            if re.search(pat, self._norm_src):
                return True
        except re.error:
            pass
        m = re.match(r'(\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)', v)
        if m:
            bare = m.group(1)
            try:
                pat = r'(?<![\d.eE+\-])' + re.escape(bare) + r'(?![\d.eE])'
                if re.search(pat, self._norm_src):
                    return True
            except re.error:
                pass
        return False

    def _match_wordbound(self, v):
        try:
            pat = r'(?<!\w)' + re.escape(v) + r'(?!\w)'
            if re.search(pat, self._norm_src):
                return True
        except re.error:
            pass
        if any(c.isspace() for c in v):
            parts = v.split()
            try:
                flex = r'(?<!\w)' + r'\s+'.join(re.escape(p) for p in parts) + r'(?!\w)'
                if re.search(flex, self._norm_src):
                    return True
            except re.error:
                pass
        return False

    def _is_in_source(self, value, claim_type):
        v = norm_text(value).strip()
        if not v:
            return True
        if claim_type == "number":
            return self._match_number(v)
        if claim_type == "url":
            stripped = v.rstrip(".,;)]}>")
            return stripped in self._norm_src
        return self._match_wordbound(v)

    def verify(self, skill_text):
        in_yaml = False
        in_fence = False
        current_section = "(top)"
        section_stats = defaultdict(lambda: {"total":0,"unverified":0,
                                              "lines":0,"flagged_lines":0})
        results = []
        total_claims = 0
        total_unverified = 0
        total_lines = 0

        for i, raw_line in enumerate(skill_text.splitlines(), start=1):
            line = raw_line.rstrip()
            stripped = line.strip()
            total_lines += 1

            # YAML frontmatter (fix #2: only skip metadata keys, not description)
            if i == 1 and stripped == "---":
                in_yaml = True
                continue
            if in_yaml and stripped == "---":
                in_yaml = False
                continue
            if in_yaml:
                if re.match(r'^(name|id|version|category|author|date):\s', stripped, re.I):
                    continue

            if stripped.startswith("```"):
                in_fence = not in_fence
                continue

            if stripped.startswith("## "):
                current_section = stripped.lstrip("# ").strip()[:60] or "(unnamed)"

            is_header = stripped.startswith("#")
            claims = _extract_claims_from_line(line, is_header=is_header, is_yaml=in_yaml)
            section_stats[current_section]["lines"] += 1
            if not claims:
                continue

            unverified = []
            for value, ctype in claims:
                total_claims += 1
                section_stats[current_section]["total"] += 1
                if not self._is_in_source(value, ctype):
                    unverified.append(f"{ctype}:{value}")
                    total_unverified += 1
                    section_stats[current_section]["unverified"] += 1

            if unverified:
                section_stats[current_section]["flagged_lines"] += 1
                results.append(ClaimCheck(
                    line_no=i, line=line, section=current_section,
                    claims_total=len(claims),
                    claims_unverified=unverified,
                ))

        per_section = []
        for name, st in section_stats.items():
            pct = (1.0 - st["unverified"]/st["total"]) * 100 if st["total"] else 100.0
            per_section.append({
                "section": name,
                "claims_total": st["total"],
                "claims_unverified": st["unverified"],
                "pct_verified": pct,
                "lines": st["lines"],
                "flagged_lines": st["flagged_lines"],
            })
        per_section.sort(key=lambda s: s["pct_verified"])

        summary = {
            "total_lines_checked": total_lines,
            "lines_flagged": len(results),
            "claims_total": total_claims,
            "claims_unverified": total_unverified,
            "pct_verified": (1.0 - total_unverified/total_claims) * 100 if total_claims else 100.0,
            "per_section": per_section,
        }
        return results, summary


def write_verification_report(skill_path, results, summary, source_url):
    """VERIFICATION.md with per-section breakdown (fix #22)."""
    ver_path = str(Path(skill_path).with_name("VERIFICATION.md"))
    lines = [
        f"# Verification report for `{Path(skill_path).name}`",
        f"",
        f"- Source: {source_url}",
        f"- Generated: {_dt.datetime.now().isoformat(timespec='seconds')}",
        f"",
        f"## Summary",
        f"- Total claims checked: **{summary['claims_total']}**",
        f"- Unverified claims: **{summary['claims_unverified']}**",
        f"- Lines flagged: **{summary['lines_flagged']}** of {summary['total_lines_checked']}",
        f"- Overall verified: **{summary['pct_verified']:.1f}%**",
        f"",
        f"## Per-section breakdown",
        f"",
        f"| Section | Claims | Unverified | % verified | Lines flagged |",
        f"| --- | ---: | ---: | ---: | ---: |",
    ]
    for s in summary["per_section"]:
        if s["claims_total"] == 0:
            continue
        lines.append(
            f"| {s['section']} | {s['claims_total']} | {s['claims_unverified']} "
            f"| {s['pct_verified']:.1f}% | {s['flagged_lines']}/{s['lines']} |"
        )
    lines.append("")
    if not results:
        lines.append("✅ No unverified claims found.")
    else:
        lines.append("## Flagged lines\n")
        lines.append("Each entry lists the skill-file line and the specific claims")
        lines.append("that could not be located in the downloaded source.\n")
        for r in results:
            lines.append(f"### Line {r.line_no} — section: _{r.section}_")
            lines.append(f"")
            lines.append(f"> {r.line[:300]}")
            lines.append(f"")
            lines.append(f"Unverified ({len(r.claims_unverified)} of {r.claims_total}):")
            for c in r.claims_unverified:
                lines.append(f"- `{c}`")
            lines.append("")
    Path(ver_path).write_text("\n".join(lines), encoding="utf-8")
    return ver_path


def annotate_skill_inline(skill_path, results):
    skill = Path(skill_path).read_text(encoding="utf-8")
    flagged_by_line = {r.line_no: r for r in results}
    out = []
    for i, line in enumerate(skill.splitlines(), start=1):
        if i in flagged_by_line:
            r = flagged_by_line[i]
            short = ", ".join(c.split(":",1)[-1] for c in r.claims_unverified[:4])
            if len(r.claims_unverified) > 4:
                short += f", +{len(r.claims_unverified)-4} more"
            out.append(f"{line}  <!-- ⚠ UNVERIFIED: {short} -->")
        else:
            out.append(line)
    Path(skill_path).write_text("\n".join(out) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────
# SAVE — frontmatter-only name + collision disambiguation
# ─────────────────────────────────────────────────────────────

def extract_skill_name(content):
    """Read `name:` ONLY from the YAML frontmatter span (fix #3)."""
    m = re.match(r'^---\s*\r?\n(.*?)\r?\n---\s*\r?\n', content, re.DOTALL)
    if not m:
        return None
    frontmatter = m.group(1)
    nm = re.search(r'^name:\s*(.+)$', frontmatter, re.MULTILINE)
    return nm.group(1).strip() if nm else None


def save_skill(content, name, output_dir, source_url=""):
    """Save SKILL.md. Disambiguate name on collision (fix #14)."""
    out_root = Path(output_dir)
    skill_dir = out_root / name
    if skill_dir.exists():
        marker = skill_dir / ".source_url"
        if marker.exists():
            existing = marker.read_text(encoding="utf-8").strip()
            if existing and source_url and existing != source_url:
                slug = short_hash(source_url, 6)
                disambig = f"{name}__{slug}"
                print(f"   ⚠ Name '{name}' already taken by {existing}; "
                      f"using '{disambig}' instead")
                name = disambig
                skill_dir = out_root / name
        out = skill_dir / "SKILL.md"
        if out.exists():
            ts = _dt.datetime.now().strftime("%H%M%S")
            backup = skill_dir / f"SKILL_prev_{ts}.md"
            shutil.copy2(out, backup)
            print(f"   📦 Backed up → {backup.name}")
    skill_dir.mkdir(parents=True, exist_ok=True)
    if source_url:
        (skill_dir / ".source_url").write_text(source_url, encoding="utf-8")
    out_path = skill_dir / "SKILL.md"
    out_path.write_text(content, encoding="utf-8")
    print(f"   ✅ {out_path}  ({content.count(chr(10))} lines, {len(content):,} chars)")
    return str(out_path)
# ─────────────────────────────────────────────────────────────
# DRIVER
# ─────────────────────────────────────────────────────────────

@dataclass
class RunOptions:
    output: str = "./skills"
    provider: str = "gemini"
    model: str = None
    api_key: str = None
    max_input_chars: int = 350_000
    max_org_repos: int = 25
    crawl_pages: int = 25
    clone_timeout: int = 300
    recurse_submodules: bool = True
    max_repo_mb: int = 800
    agentic_retries: int = 2
    target_pct_verified: float = 90.0
    no_llm: bool = False
    annotate_skill: bool = False
    skip_verify: bool = False
    headless: bool = True
    json_output: bool = False


def emit_json_result(opts, record):
    if opts.json_output:
        sys.stdout.write(json.dumps(record) + "\n")
        sys.stdout.flush()


def process_spec(spec, opts, downloader, llm):
    records = []
    if spec.kind == "skip" or spec.kind == "unknown":
        print(f"   ⏭️  Skipping ({spec.kind}): {spec.raw}")
        rec = {"raw": spec.raw, "kind": spec.kind, "status": "skipped"}
        emit_json_result(opts, rec); records.append(rec); return records

    bundles = downloader.download(spec)
    if not isinstance(bundles, list):
        bundles = [bundles]

    for bundle in bundles:
        save_bundle(bundle, opts.output)

        if opts.no_llm or llm is None:
            raw_path = Path(opts.output) / bundle.skill_basename / "RAW.md"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(bundle.text, encoding="utf-8")
            print(f"   ✅ Raw dump only: {raw_path}")
            rec = {"raw": spec.raw, "kind": bundle.spec.kind,
                   "url": bundle.spec.url, "status": "raw_only",
                   "raw_path": str(raw_path),
                   "source_dir": bundle.on_disk_path}
            emit_json_result(opts, rec); records.append(rec); continue

        print(f"   📡 Generating SKILL.md ({bundle.spec.kind}) → {bundle.skill_basename}")
        if opts.skip_verify:
            skill = generate_skill_once(bundle, llm, max_input_chars=opts.max_input_chars)
            results, summary = [], {"pct_verified": -1.0,
                                    "claims_total": 0,
                                    "claims_unverified": 0,
                                    "lines_flagged": 0,
                                    "total_lines_checked": skill.count("\n") + 1,
                                    "per_section": []}
        else:
            skill, results, summary = agentic_skill_loop(
                bundle, llm,
                max_input_chars=opts.max_input_chars,
                target_pct_verified=opts.target_pct_verified,
                max_retries=opts.agentic_retries,
            )

        if not skill.strip():
            print(f"   ⚠ LLM returned empty for {bundle.skill_basename}")
            rec = {"raw": spec.raw, "kind": bundle.spec.kind,
                   "url": bundle.spec.url, "status": "empty_llm"}
            emit_json_result(opts, rec); records.append(rec); continue

        name = extract_skill_name(skill) or bundle.skill_basename
        skill_path = save_skill(skill, name, opts.output,
                                source_url=bundle.spec.url)

        ver_path = ""
        if not opts.skip_verify:
            ver_path = write_verification_report(skill_path, results, summary,
                                                 bundle.spec.url)
            print(f"   📋 Verification: {summary['pct_verified']:.1f}% claims verified "
                  f"({summary['claims_unverified']} of {summary['claims_total']} flagged)")
            print(f"   📋 Report: {ver_path}")
            worst = next((s for s in summary["per_section"]
                          if s["claims_total"] >= 3 and s["pct_verified"] < summary["pct_verified"]),
                         None)
            if worst:
                print(f"   ⚠ Worst section: '{worst['section']}' at "
                      f"{worst['pct_verified']:.1f}%")
            if opts.annotate_skill and results:
                annotate_skill_inline(skill_path, results)
                print(f"   📝 Inline ⚠ UNVERIFIED markers added")

        rec = {"raw": spec.raw, "kind": bundle.spec.kind,
               "url": bundle.spec.url, "status": "ok",
               "skill_path": skill_path,
               "verification_path": ver_path,
               "source_dir": bundle.on_disk_path,
               "pct_verified": round(summary["pct_verified"], 1),
               "claims_total": summary["claims_total"],
               "claims_unverified": summary["claims_unverified"],
               "lines_flagged": summary["lines_flagged"]}
        emit_json_result(opts, rec); records.append(rec)

    return records


def maybe_warn_github_auth(specs):
    """Fix #17 — surface GitHub rate-limit warning at startup."""
    if os.environ.get("GITHUB_TOKEN"):
        return
    needs_api = sum(1 for s in specs if s.kind in ("github_pr","github_org","github_blob"))
    if needs_api == 0:
        return
    print()
    print("─" * 60)
    print(f"  ⚠ {needs_api} source(s) need the GitHub API "
          f"(PR / org / blob fetches)")
    print(f"  ⚠ No GITHUB_TOKEN set — anonymous limit is 60 requests/hour")
    print(f"     Set GITHUB_TOKEN to a fine-grained PAT for 5000/hour")
    print("─" * 60)


def main():
    p = argparse.ArgumentParser(
        description="SkillForge v5 — Complete-Source + Anti-Hallucination",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              skillforge_complete.py --url https://x402.org/ecosystem
              skillforge_complete.py --github https://github.com/peacprotocol/peac
              skillforge_complete.py --github https://github.com/ethereum/ERCs/pull/1170
              skillforge_complete.py --arxiv 2511.15712
              skillforge_complete.py batch --list sources.txt --annotate-skill --json
        """),
    )
    sub = p.add_subparsers(dest="command")
    bp = sub.add_parser("batch", help="Batch from file")
    bp.add_argument("--list", required=True)
    bp.add_argument("--delay", type=int, default=3)

    for pr in [p, bp]:
        pr.add_argument("--output", "-o", default="./skills")
        pr.add_argument("--provider", choices=["anthropic","gemini","openrouter"],
                        default="gemini")
        pr.add_argument("--model", default=None)
        pr.add_argument("--api-key", default=None)
        pr.add_argument("--max-input-chars", type=int, default=350_000)
        pr.add_argument("--max-org-repos", type=int, default=25)
        pr.add_argument("--crawl-pages", type=int, default=25,
                        help="Max same-origin pages per web source (1 = no crawl)")
        pr.add_argument("--clone-timeout", type=int, default=300,
                        help="git clone timeout in seconds")
        pr.add_argument("--no-submodules", action="store_true",
                        help="Disable --recurse-submodules in git clone")
        pr.add_argument("--max-repo-mb", type=int, default=800,
                        help="Above this size, fall back to priority-filtered files")
        pr.add_argument("--agentic-retries", type=int, default=2,
                        help="Verifier-driven regeneration rounds (0 to disable)")
        pr.add_argument("--target-pct-verified", type=float, default=90.0)
        pr.add_argument("--no-llm", action="store_true",
                        help="Download + persist only; skip generation")
        pr.add_argument("--annotate-skill", action="store_true",
                        help="Add inline ⚠ UNVERIFIED markers to SKILL.md")
        pr.add_argument("--skip-verify", action="store_true")
        pr.add_argument("--no-headless", action="store_true")
        pr.add_argument("--json", dest="json_output", action="store_true",
                        help="Emit one JSON line per result to stdout")

    p.add_argument("--url", help="Any URL (HTML page, llms.txt, PDF, ...)")
    p.add_argument("--github", help="GitHub URL: repo / blob / pull / org")
    p.add_argument("--arxiv", help="arXiv ID or URL")
    p.add_argument("--pdf", help="Local PDF path")

    args = p.parse_args()

    opts = RunOptions(
        output=args.output,
        provider=args.provider,
        model=args.model,
        api_key=args.api_key,
        max_input_chars=args.max_input_chars,
        max_org_repos=args.max_org_repos,
        crawl_pages=args.crawl_pages,
        clone_timeout=args.clone_timeout,
        recurse_submodules=not args.no_submodules,
        max_repo_mb=args.max_repo_mb,
        agentic_retries=args.agentic_retries,
        target_pct_verified=args.target_pct_verified,
        no_llm=args.no_llm,
        annotate_skill=args.annotate_skill,
        skip_verify=args.skip_verify,
        headless=not args.no_headless,
        json_output=args.json_output,
    )

    web = WebDownloader(headless=opts.headless, crawl_pages=opts.crawl_pages)
    gh = GitHubFetcher(clone_timeout=opts.clone_timeout,
                       recurse_submodules=opts.recurse_submodules,
                       max_repo_mb=opts.max_repo_mb)
    arxiv = ArxivFetcher(web)
    downloader = Downloader(web=web, gh=gh, arxiv=arxiv,
                            max_org_repos=opts.max_org_repos,
                            crawl_pages=opts.crawl_pages)
    llm = None if opts.no_llm else LLMProvider(opts.provider, opts.model, opts.api_key)

    Path(opts.output).mkdir(parents=True, exist_ok=True)

    def _cleanup_on_exit():
        try:
            gh.cleanup()
            arxiv.cleanup()
        except Exception:
            pass
    atexit.register(_cleanup_on_exit)

    try:
        if args.command == "batch":
            with open(args.list) as f:
                lines = [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]
            specs = [detect_source(l) for l in lines]
            banner(f"SkillForge v5 batch — {len(specs)} sources")
            from collections import Counter
            kc = Counter(s.kind for s in specs)
            print("  Routing: " + ", ".join(f"{k}={v}" for k, v in kc.most_common()))
            maybe_warn_github_auth(specs)

            all_records = []
            for i, spec in enumerate(specs, 1):
                section(f"[{i}/{len(specs)}] {spec.kind}: {spec.raw}")
                try:
                    recs = process_spec(spec, opts, downloader, llm)
                    all_records.extend(recs)
                except KeyboardInterrupt:
                    print("\n⛔ Interrupted by user"); break
                except Exception as e:
                    print(f"❌ {type(e).__name__}: {e}")
                    rec = {"raw": spec.raw, "kind": spec.kind,
                           "status": "error", "error": str(e)}
                    emit_json_result(opts, rec)
                    all_records.append(rec)
                if i < len(specs) and llm:
                    time.sleep(args.delay)
            ok = sum(1 for r in all_records if r.get("status") == "ok")
            raw = sum(1 for r in all_records if r.get("status") == "raw_only")
            err = sum(1 for r in all_records if r.get("status") == "error")
            banner(f"DONE: {ok} ok, {raw} raw-only, {err} errored")
            for r in all_records:
                m = {"ok":"✓","raw_only":"○","error":"✗",
                     "skipped":"⏭","empty_llm":"✗"}.get(r.get("status"), "?")
                pv = ""
                if "pct_verified" in r and r["pct_verified"] >= 0:
                    pv = f"  ({r['pct_verified']}% verified)"
                err_s = ""
                if r.get("status") == "error":
                    err_s = f"  — {r.get('error', '')[:60]}"
                print(f"  {m} [{r.get('kind','?'):<14}] {r.get('raw','?')}{pv}{err_s}")
            return

        spec = None
        if args.github:
            spec = detect_source(args.github)
        elif args.arxiv:
            spec = detect_source(args.arxiv)
        elif args.url:
            spec = detect_source(args.url)
        elif args.pdf:
            if not os.path.exists(args.pdf):
                print(f"❌ Not found: {args.pdf}"); sys.exit(1)
            spec = SourceSpec(args.pdf, "pdf", args.pdf)
        else:
            p.print_help(); sys.exit(1)

        if not spec or spec.kind in ("skip","unknown"):
            print(f"❌ Unrecognized source: {spec.raw if spec else '?'}")
            sys.exit(1)
        maybe_warn_github_auth([spec])
        process_spec(spec, opts, downloader, llm)

    finally:
        _cleanup_on_exit()


if __name__ == "__main__":
    main()
