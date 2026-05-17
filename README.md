<p align="center">
  <h1 align="center">🔨 SkillForge</h1>
  <p align="center"><strong>Turn any paper, repo, or docs site into a verified skill file your AI assistant can read.</strong></p>
  <p align="center">
    <a href="#what-is-this">What is this?</a> •
    <a href="#quickstart">Quickstart</a> •
    <a href="#whats-new-in-v5">v5 Changes</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#benchmarks">Benchmarks</a> •
    <a href="#all-options">All Options</a> •
    <a href="#demo-video">Demo Video</a>
  </p>
</p>

---

## What is this?

**Plain-English version:** You give SkillForge a URL — a paper, a GitHub repo, a docs site, anything — and it produces a single markdown file (`SKILL.md`) that captures everything your AI assistant needs to know about that source. Drop the file into your project, and Claude, Cursor, or any AI tool can read it as context.

**Why it exists:** Reading a research paper and extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes hours. SkillForge does this in one command. It also verifies the output line-by-line against the source, so the file doesn't contain made-up details — a serious problem when LLMs summarize technical material.

```
📄 Paper  ──→  SkillForge  ──→  SKILL.md  +  VERIFICATION.md
🐙 Repo                          ↑              ↑
🌐 Docs                          │              └─ Every claim checked
                                 └─ Drop into your project
```

### The key insight

**A 4B model + SKILL.md outperforms a 70B model + raw PDF.** SkillForge uses a frontier model once to do the hard reading. After that, any model — even one running on your phone — gives answers with precision approaching frontier quality. The weaker the model, the bigger the gain.

---

## Demo Video

[![SkillForge Demo](https://img.youtube.com/vi/O0J55eRcwZw/maxresdefault.jpg)](https://www.youtube.com/watch?v=O0J55eRcwZw)

**[Watch the full demo →](https://www.youtube.com/watch?v=O0J55eRcwZw)**

---

## Quickstart

### 1. Install

```bash
pip install -r requirements.txt
playwright install chromium    # only needed if you'll fetch JS-heavy docs sites
```

### 2. Set one API key

```bash
export GEMINI_API_KEY="..."        # free tier at aistudio.google.com — recommended for first try
# or
export ANTHROPIC_API_KEY="..."     # claude — higher quality, paid
# or
export OPENROUTER_API_KEY="..."    # access many models, has free tier
```

If you'll be hitting GitHub a lot (PR fetches, org cloning, blob URLs), also set:
```bash
export GITHUB_TOKEN="..."          # avoids the 60 req/hour anonymous limit
```

### 3. Run one of these

```bash
# An arXiv paper
python skillforge.py --arxiv 2103.13630

# A GitHub repository (gets cloned in full, including submodules)
python skillforge.py --github https://github.com/wolfecameron/nanoMoE

# A docs site (crawls every page in /docs)
python skillforge.py --url https://docs.acedata.cloud

# A local PDF
python skillforge.py --pdf paper.pdf

# A batch — process your whole reading list
python skillforge.py batch --list sources.txt
```

That's it. Output appears under `./skills/<source-name>/SKILL.md`, with a `VERIFICATION.md` next to it showing what was and wasn't verified against the source.

### 4. (Optional) Use the skill file

Copy the generated `SKILL.md` into your project's `.claude/skills/<name>/`, `.cursorrules`, or any tool that reads context files. Your AI assistant now has implementation-grade knowledge of the source.

---

## What's new in v5

> **For returning users:** v5 is a substantial rewrite. If you're upgrading from v3/v4, the CLI is mostly backwards-compatible but the source coverage and verification model are new. Skim this section.

v5 fixes a structural weakness in earlier versions: claimed "full source download" actually dropped 40-60% of large repos and docs sites, and verification was the LLM grading itself. This version adds:

### Source coverage

| What you can give it | v3/v4 | v5 |
| --- | :---: | :---: |
| arXiv paper (`--arxiv 2511.15712`) | ✓ | ✓ |
| Local PDF (`--pdf paper.pdf`) | ✓ | ✓ |
| GitHub repo (`--github user/repo`) | ✓ (partial) | ✓ (full, with submodules) |
| GitHub blob URL (`/blob/main/specs/...`) | ✗ | ✓ |
| GitHub Pull Request URL | ✗ | ✓ |
| GitHub org URL (clones all public repos) | ✗ | ✓ |
| Docs site (multi-page crawl) | ✗ | ✓ |
| `llms.txt` manifest (follows listed URLs) | ✗ | ✓ |
| HTML pages with lazy-loaded content | partial | ✓ (container-scroll) |

### Anti-hallucination verifier

Every generated `SKILL.md` is checked line-by-line against the downloaded source. A separate `VERIFICATION.md` shows the percentage verified, with a per-section breakdown. Numbers, identifiers, URLs, acronyms, and quoted strings are matched with **word boundaries** — a claim of "3 layers" will not falsely pass because the source happens to contain "13 layers".

If the verifier finds unverified claims, an **agentic repair loop** feeds those exact claims back to the LLM ("delete or replace from source") and regenerates. Up to `--agentic-retries` rounds, keeping the highest-scoring version.

### Other v5 changes

- **Smart truncation** — repos sort files by priority (README → manifest → config → core → tests) before sending to the LLM; papers keep abstract + methods + tail rather than head+tail.
- **Repo clone cache** — blob, PR, and repo URLs to the same `(owner, repo)` share one clone.
- **Tempdir cleanup** — `weakref.finalize` + `atexit`, no more leaks on long batch runs.
- **Path-traversal protection** — `safe_join` on every file write.
- **`--json` output** — one line per source for scripting.
- **Size guards** — `--max-repo-mb` falls back to a priority-filtered subset for huge repos; `--clone-timeout` bails on hung clones.

<details>
<summary><strong>Full v5 changelog (click to expand)</strong></summary>

**Severity 1 (correctness):**
1. Verifier number matching uses digit-aware boundaries — claim `3` no longer matches inside `13`; `1.5` no longer matches inside `21.5`; `1e-4` no longer matches inside `1e-40`.
2. YAML `description:` is now verified (only `name`/`id`/`version`/`category`/`author`/`date` are skipped).
3. `extract_skill_name` only reads inside the YAML frontmatter span — code-block `name:` lines can't impersonate the skill name.
4. `git clone` has a hard timeout (default 300s, configurable via `--clone-timeout`).
5. Submodules cloned by default (disable with `--no-submodules`).

**Severity 2 (completeness):**
6. Web sources do same-origin BFS crawl (`--crawl-pages`, default 25) with auto-detected `/docs` path prefix.
7. `llms.txt` parsed as a URL manifest; each listed URL fetched and concatenated.
8. Playwright auto-scroll iterates every `overflow:auto` container, not just `document.body` — handles Docusaurus, modern GitBook, React SPAs.
9. `trafilatura` main-content extraction strips sidebar/footer/nav before the LLM sees the page.

**Other fixes:**
11. Agentic regeneration loop: verifier output feeds back into the LLM with repair instructions.
12. Smart prioritized truncation for LLM input (repos by file priority; papers by section).
13. Repo-clone cache keyed on `(owner, repo, ref)`.
14. Skill-name collisions disambiguated with `short_hash(source_url, 6)`.
15. 191-entry `KNOWN_ACRONYMS` set catches Adam/BERT/ReLU/HTTP/REST/Ed25519/JSON/...
16. Path traversal protection via `os.path.abspath` + prefix check.
17. Startup banner warns when no `GITHUB_TOKEN` and GitHub API will be hit.
18. Per-repo size guard (`--max-repo-mb`, default 800).
19. Tempdir cleanup via `weakref.finalize` + `atexit`.
20. `--json` emits one JSON line per source: `{status, skill_path, verification_path, pct_verified, claims_total, claims_unverified, lines_flagged}`.
21. Verifier dedupes within-line by value (no more double-counted `code:50000` + `number:50000`).
22. Per-section verification breakdown in `VERIFICATION.md`.
23. Unicode normalization (NFKD + ASCII fold) on source and claims — `naïve` matches `naive`.

</details>

---

## How It Works

### Pipeline at a glance

```
┌────────────────────────────────────────────────────────────────┐
│  1. DETECT      What kind of source is this?                    │
│                 (github_repo / github_blob / github_pr /         │
│                  github_org / web / llms_manifest / arxiv /      │
│                  pdf / text)                                     │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  2. DOWNLOAD    Get the source COMPLETELY                       │
│                 • GitHub: full git clone with submodules         │
│                 • Web: BFS crawl with /docs prefix detection     │
│                 • Playwright: container-scroll for SPAs          │
│                 • llms.txt: follow every listed URL              │
│                 • arXiv: HTML version + PDF version              │
│                 → persisted on disk under .sources/              │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  3. PRIORITIZE  Smart truncation to fit LLM input budget        │
│                 • Repos: README → manifest → core → tests        │
│                 • Papers: abstract + methods + tail              │
│                 • Docs: full content (already main-extracted)    │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  4. GENERATE    LLM produces SKILL.md from source               │
│                 (Gemini / Claude / OpenRouter)                   │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  5. VERIFY      Line-by-line check vs downloaded source         │
│                 • Numbers: digit-aware boundaries                │
│                 • Identifiers: word boundaries                   │
│                 • 191-entry acronym set                          │
│                 • Unicode normalized                             │
│                 → if pct_verified < target, go to step 6         │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  6. REPAIR      Feed flagged claims back to LLM                 │
│                 "delete or replace from source"                  │
│                 → keep highest-pct_verified across rounds        │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                  SKILL.md + VERIFICATION.md
```

### What makes it agentic

The repair loop isn't just retrying. Each round, the verifier produces a list of specific flagged claims with their exact lines and types:

```
🔬 Round 0: 73.2% verified (38/142 unverified)
   🔧 Regenerating with 12 flagged lines as feedback…
      Line 47: The model uses Adam optimizer with lr=1e-3 ...
        Unverified: acronym:Adam, number:1e-3
      Line 89: Achieves 99.8% accuracy on ImageNet ...
        Unverified: number:99.8%
🔬 Round 1: 91.4% verified (12/140 unverified)
   ✓ Target met (90%)
```

The LLM gets the specific list ("you said Adam, but the source uses SGD" — implicitly, via the source excerpt) and rewrites those lines.

### Anti-hallucination verifier — what gets checked

The verifier extracts these claim types from every non-header, non-code-fence line of the generated SKILL.md:

| Claim type | Example | Match rule |
| --- | --- | --- |
| `number` | `12ms`, `1e-4`, `99.8%` | Digit-aware boundaries — won't match inside larger numbers |
| `code` | `` `verify_payment()` `` | Word boundaries — `verify` won't match inside `verification` |
| `quoted` | `"exact scheme"` | Word boundaries |
| `url` | `https://x402.org/api` | Substring (URLs are unique enough) |
| `path` | `src/main.py` | Word boundaries |
| `identifier` | `verify_payment`, `MyClass`, `Ed25519` | Word boundaries; only checked in body, not YAML |
| `acronym` | `Adam`, `BERT`, `JSON`, `HTTP`, `REST` | 191-entry curated list with `\b...\b` |

Every claim runs through unicode normalization first (`NFKD` + ASCII fold) so `naïve` matches `naive` and Unicode dashes match ASCII dashes.

The YAML `description:` field IS checked (this is where LLMs hallucinate most). Only metadata fields (`name`, `id`, `version`, `category`, `author`, `date`) are skipped.

---

## OpenRouter — Free Tier

SkillForge's OpenRouter provider gives you access to dozens of models through a single API key, including completely free models.

### How it works

1. **Live model discovery** — queries OpenRouter's API, filters for chat-capable models with ≥32K context
2. **SQS ranking** — ranks models by Speed (30%), Completion Cap (20%), Context Length (15%), Capability (15%), Recency (10%), Reputation (10%)
3. **Auto-rotation** — if a model rate-limits, SkillForge automatically rotates to the next one
4. **Free → paid upgrade** — when free models are exhausted, auto-upgrades to cheap paid models

### Cost per source

| Provider | Mode | Cost/Source | Notes |
| --- | --- | --- | --- |
| OpenRouter | Free models | **$0.00** | Auto-discovers, rotates on rate limits |
| OpenRouter | `--paid` | **$0.01–0.03** | Gemini Flash, fast and reliable |
| Gemini | Direct | **Free tier available** | Generous limits |
| Anthropic | Claude Sonnet | ~$0.15 | Higher quality, recommended for tricky sources |

---

## Benchmarks

Tested with Gemini 2.5 Flash (default). All runs automated, no manual editing.

### Per-source performance

| Source | Type | Lines | Time | % Verified (v5) |
| --- | --- | ---: | ---: | ---: |
| [Quantization Survey](https://arxiv.org/abs/2103.13630) (33 pages) | arXiv | 550 | 161s | 92% |
| [Deep Compression](https://arxiv.org/abs/1510.00149) (14 pages) | arXiv | 250 | ~90s | 94% |
| [Distilling Knowledge](https://arxiv.org/abs/1503.02531) (9 pages) | arXiv | 200 | ~70s | 95% |
| [nanoMoE](https://github.com/wolfecameron/nanoMoE) | GitHub | 349 | ~45s | 91% |
| 73-source batch (mixed) | Batch | 25,000+ | ~90min | avg 89% |

### vs manual reading

For the quantization survey (33 pages, 13 equations, 8 result tables):

| | Manual Reading | SkillForge v5 |
| --- | --- | --- |
| **Time** | 6–8 hours | 2.7 minutes |
| **Lines produced** | 455 | 550 |
| **Equations captured** | 13/13 | 11/13 |
| **Result tables** | Complete | Complete |
| **Verification** | None | 92% claims verified vs source |
| **Reusable format** | No | Yes (YAML triggers, git-versionable) |

### Weak model democratization

The real value isn't competing with frontier models on raw PDFs — it's making tiny models competitive by pre-digesting knowledge:

| Model | With Raw PDF | With SKILL.md | Gain |
| --- | --- | --- | --- |
| Llama 3.2 3B | Can't fit 33-page PDF | Precise answers from labeled sections | Massive |
| Gemma 3 4B | Hallucinates numbers | Finds exact values | +4× accuracy |
| Llama 3.3 70B | Misses buried details | Competitive with frontier | +2× accuracy |
| Claude Opus 4.6 | Excellent | Excellent (same quality, faster lookup) | Minimal |

---

## Why?

Reading a quantization paper takes **4 hours**. Extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes **another 2 hours**. Multiply by 20 papers per project.

SkillForge does this in one command. The output is structured so Claude, GPT, Cursor, or Copilot can use it directly as context — no more pasting paper excerpts into chat.

### Why not just upload the PDF to Claude?

Claude reads PDFs excellently. But:

- **Consistency** — Skill files follow the same schema every time. Three files from three different months have identical structure. Three PDFs have whatever format the authors chose.
- **Composability** — Skill files go in your git repo. They travel with your codebase. They work in Claude, Cursor, Windsurf — any tool that reads files. ChatPDF sessions expire.
- **Verifiable** — `VERIFICATION.md` tells you exactly which claims in the skill file appear in the source. ChatPDF gives you no audit trail.
- **Weak model support** — A 4B model can't fit a 33-page PDF in its 8K context window. A 500-line skill file fits and gives precise answers.
- **Batch pipeline** — Update `sources.txt` with your weekly reading list, run one command, your entire research library stays current.
- **Cost** — Process 30 papers for ~$0.30 total with `--paid`, or free with OpenRouter free models.

---

## All Options

```
python skillforge.py [OPTIONS]

Input (pick one):
  --url URL           Any URL (HTML page, llms.txt, PDF, ...)
  --github URL        GitHub repo / blob / PR / org URL
  --arxiv ID          arXiv ID or URL
  --pdf PATH          Local PDF file

Output:
  --output, -o DIR    Output directory (default: ./skills)
  --json              Emit one JSON line per result to stdout

Provider:
  --provider          anthropic, gemini, or openrouter (default: gemini)
  --model MODEL       Override default model
  --api-key KEY       API key (or set environment variable)

LLM controls:
  --max-input-chars N    Max source chars sent to LLM (default: 350000)
  --no-llm               Download only, skip generation
  --skip-verify          Skip verification (faster but no anti-hallucination)
  --annotate-skill       Add inline ⚠ UNVERIFIED markers to SKILL.md

Verification (new in v5):
  --agentic-retries N    Repair-loop rounds (default: 2, 0 to disable)
  --target-pct-verified F  Stop repair when reached (default: 90.0)

Web fetching (new in v5):
  --crawl-pages N        Max same-origin pages per web source (default: 25,
                         use 1 for no crawl)
  --no-headless          Show the browser window (for debugging)

GitHub (new in v5):
  --clone-timeout N      Git clone timeout in seconds (default: 300)
  --no-submodules        Don't recurse into submodules
  --max-repo-mb N        Above this, fall back to priority-filtered files
                         (default: 800)
  --max-org-repos N      Max repos to clone from an org URL (default: 25)

Batch:
  skillforge.py batch --list sources.txt [--delay 5] [other flags]
```

---

## Examples

### Example: A docs site

```bash
python skillforge.py --url https://docs.acedata.cloud --crawl-pages 50
```

What happens:
1. Detects this is a docs site (`docs.` subdomain).
2. Auto-detects `/docs` path prefix would apply if URL had one (this one is at root).
3. BFS crawls up to 50 same-origin pages, scrolling each fully.
4. Strips sidebar/nav/footer with `trafilatura`.
5. Generates a SKILL.md covering every page.
6. Verifies every claim against the concatenated downloaded content.

### Example: A GitHub PR

```bash
python skillforge.py --github https://github.com/ethereum/ERCs/pull/1170
```

What happens:
1. Detects PR URL.
2. Clones the PR's head ref (full clone with submodules).
3. Fetches PR metadata, diff, and review comments via GitHub API.
4. Combines repo files + PR context into one bundle.
5. Generates SKILL.md focused on what the PR changes.

### Example: Batch with sources.txt

```bash
# sources.txt:
# https://github.com/x402-foundation/x402
# https://x402.org/ecosystem
# https://github.com/AceDataCloud
# https://docs.acedata.cloud/llms.txt
# https://arxiv.org/abs/2511.15712

python skillforge.py batch --list sources.txt --json > results.jsonl
```

What happens:
1. Routing summary printed up-front: `Routing: github_repo=1, web=1, github_org=1, llms_manifest=1, arxiv=1`
2. GitHub auth warning if no `GITHUB_TOKEN` and the org URL would hit rate limits.
3. Each source processed sequentially; results emitted as JSON lines.
4. Repo clone cache means if any blob URLs to x402-foundation/x402 appear, they share one clone.
5. Final summary: `DONE: 5 ok, 0 raw-only, 0 errored`.

<details>
<summary><strong>Sample SKILL.md output: Deep Compression (Han et al.)</strong></summary>

```yaml
---
name: deep-compression
description: >
  Deep Compression is a three-stage pipeline for compressing deep neural
  networks without accuracy loss, combining pruning, trained quantization,
  and Huffman coding. Use this skill when deploying DNNs on memory-constrained
  devices. The method achieves 35-49x compression on AlexNet and VGG-16,
  reducing AlexNet from 240MB to 6.9MB and VGG-16 from 552MB to 11.3MB
  while maintaining original accuracy.
---

# Deep Compression: Compressing Deep Neural Networks
**Paper**: Song Han, Huizi Mao, William J. Dally
**Published**: ICLR, 2016
**Key result**: 35×-49× compression without accuracy loss

## Pipeline
1. Network Pruning: Remove connections with small weights (9×-13×)
2. Trained Quantization: k-means clustering of weights (→ 27×-31×)
3. Huffman Coding: Lossless compression (→ 35×-49×)
...
```

</details>

<details>
<summary><strong>Sample VERIFICATION.md output</strong></summary>

```markdown
# Verification report for `SKILL.md`

- Source: https://arxiv.org/abs/1510.00149
- Generated: 2026-05-17T04:45:12

## Summary
- Total claims checked: **142**
- Unverified claims: **8**
- Lines flagged: **6** of 250
- Overall verified: **94.4%**

## Per-section breakdown

| Section | Claims | Unverified | % verified | Lines flagged |
| --- | ---: | ---: | ---: | ---: |
| Pipeline | 18 | 0 | 100.0% | 0/12 |
| Pruning | 24 | 1 | 95.8% | 1/22 |
| Quantization | 31 | 2 | 93.5% | 2/28 |
| Huffman Coding | 22 | 1 | 95.5% | 1/19 |
| Results | 47 | 4 | 91.5% | 2/45 |

## Flagged lines
...
```

</details>

---

## Skill File Format

A skill file (`SKILL.md`) is a structured markdown document with a YAML trigger header:

```yaml
---
name: kebab-case-identifier
description: >
  Trigger-heavy description (100-180 words) that tells Claude or Cursor
  WHEN to load this file. You don't manually attach it — your AI assistant
  reads the trigger and pulls it in automatically when the topic comes up.
---
```

Followed by sections covering: source metadata, problem setup, core methods with every formula, architecture details, experimental results with full tables, implementation takeaways, and key references.

The format is designed to be:
- **Exhaustive** — every formula, every hyperparameter, every result table
- **LLM-native** — clear headers, code blocks, markdown tables
- **Composable** — multiple skill files stack together as context
- **Consistent** — same structure every time, across months, across sources
- **Verifiable** — every claim checked against source in `VERIFICATION.md`
- **Git-versionable** — plain text, diffs cleanly, travels with your codebase

See [docs/SKILL_FORMAT_SPEC.md](docs/SKILL_FORMAT_SPEC.md) for the full specification.

---

## Comparison

| | SkillForge v5 | Papers With Code | Elicit | ChatPDF | Manual Reading |
| --- | --- | --- | --- | --- | --- |
| **Output** | Verified skill file | Links to repos | Summaries | Chat session | Notes (maybe) |
| **Source coverage** | Papers, repos, docs, PRs, llms.txt | Papers + code | Papers | Single doc | Anything |
| **Captures formulas** | ✅ Every equation | ❌ | ❌ | Partial | If you take notes |
| **Captures code** | ✅ Annotated snippets | Links only | ❌ | ❌ | ❌ |
| **Captures configs** | ✅ Full hyperparameters | ❌ | ❌ | If you ask | Maybe |
| **YAML triggers** | ✅ Auto-loads in Claude/Cursor | ❌ | ❌ | ❌ | ❌ |
| **Hallucination check** | ✅ Line-by-line verifier | N/A | ❌ | ❌ | Manual |
| **Composable** | ✅ Same format, stackable | ❌ | ❌ | Sessions expire | ❌ |
| **Batch processing** | ✅ 30+ sources, one command | ❌ | Limited | One at a time | ❌ |
| **Git-versionable** | ✅ Plain markdown | ❌ | ❌ | ❌ | ❌ |
| **Works with weak models** | ✅ 4B models give precise answers | ❌ | ❌ | ❌ | ❌ |
| **Time per source** | 2–3 minutes | N/A | ~1 min (summary) | Manual | 4–8 hours |
| **Cost** | $0 (free) – $0.03 (paid) | Free | Subscription | Subscription | Free (but slow) |

---

## Use Cases

**ML Competition Teams** — Process your entire reading list overnight. Each paper becomes a composable knowledge module your AI assistant can use during implementation. Built for WorldQuant IQC, OpenAI Parameter Golf, and ImageCLEF 2026.

**Research Labs** — Build a shared, version-controlled skill library. When one person reads a paper, the entire team's AI tools immediately know how to implement it. Monday morning: update `sources.txt` with last week's papers, run one command, library stays current.

**Web3 / Protocol Teams** — Ingest protocol specs (x402, ERC drafts, EIPs), reference implementations (full GitHub orgs), and docs sites (`docs.*`, GitBook) into a single skill library. PR URLs let your AI track active proposals.

**Weak Model Users** — Can't afford frontier API costs? Extract skill files once with SkillForge's free tier, then use them with Ollama, llama.cpp, or any local model. A 4B model with skill files gives precise answers that a 70B model with raw PDFs can't match.

**Individual Researchers** — Stop re-reading papers. Extract once, reference forever. Skill files compose — load quantization + pruning + knowledge distillation together for a complete compression pipeline.

---

## Project Structure

```
skillforge-ai/
├── skillforge.py          # The complete tool (single file, ~2270 lines)
├── requirements.txt       # See dependencies below
├── README.md
├── LICENSE
├── examples/
│   ├── sources.txt        # Sample batch file
│   ├── sources_30.txt     # 30-paper batch file
│   └── sample-skills/     # Pre-generated examples
│       ├── quantization-for-efficient-neural-networks/
│       │   ├── SKILL.md
│       │   └── VERIFICATION.md
│       ├── deep-compression/
│       └── nanomoe/
├── notebooks/
│   └── SkillForge_Demo.ipynb
├── benchmark/
│   ├── benchmark.py       # Evaluation engine (FactScore, RAGAS, HHEM)
│   └── benchmark.jsx      # React visualization dashboard
└── docs/
    └── SKILL_FORMAT_SPEC.md
```

### Dependencies

```
anthropic               # If using --provider anthropic
google-generativeai     # If using --provider gemini
openai                  # If using --provider openrouter
PyMuPDF                 # PDF text extraction
requests                # HTTP fetching
beautifulsoup4          # HTML parsing
html2text               # HTML → Markdown
playwright              # Headless browser for JS-heavy pages
lxml                    # Fast HTML parser
trafilatura             # Main-content extraction (strips nav/footer)
```

---

## Roadmap

### Completed
- [x] arXiv paper → SKILL.md
- [x] GitHub repo → SKILL.md (full clone, submodules, blob/PR/org URLs)
- [x] Local PDF → SKILL.md
- [x] **Docs site → SKILL.md (multi-page BFS crawl)** ← v5
- [x] **llms.txt manifest support** ← v5
- [x] Gemini + Anthropic + OpenRouter provider support
- [x] Batch processing with `sources.txt`
- [x] **Line-by-line anti-hallucination verifier** ← v5
- [x] **Agentic repair loop (regenerate from flagged claims)** ← v5
- [x] **Per-section verification breakdown** ← v5
- [x] **Smart prioritized truncation (repos by priority, papers by section)** ← v5
- [x] OpenRouter free model auto-discovery with SQS ranking
- [x] Auto-rotation on rate limits
- [x] **`--json` output for scripting** ← v5
- [x] Zero-cost `--no-llm` mode

### Coming Soon
- [ ] **Semantic Scholar integration** — citation graph, intent (supporting/contrasting), live conflict detection
- [ ] **Execution-based verification** — clone paper repos, run training, verify claimed results match
- [ ] **Live monitoring daemon** — watch arXiv RSS for citations to your library, auto-flag contradictions
- [ ] **Ollama provider** — fully local, zero-cost
- [ ] **`--compact` mode** — shorter skill files for 8K context windows
- [ ] **Cross-reference index** — `CROSS_REF.md` showing conflicts and agreements across your skill library
- [ ] Claude Code MCP server integration
- [ ] Cursor plugin / VS Code extension
- [ ] Web UI (Streamlit)

---

## Troubleshooting

<details>
<summary><strong>Playwright fails or pages render incomplete</strong></summary>

Install the browser binary:
```bash
playwright install chromium
```

If Playwright still fails, SkillForge automatically falls back to `requests` for HTML fetching. You'll see fewer JS-rendered pages but the rest still works.

For debugging, run with `--no-headless` to watch the browser.

</details>

<details>
<summary><strong>GitHub anonymous rate limit (60 req/hour)</strong></summary>

Set a fine-grained PAT:
```bash
export GITHUB_TOKEN="ghp_..."
```

This raises the limit to 5000 req/hour. v5 prints a banner at startup if any source needs the API and no token is set.

</details>

<details>
<summary><strong>Verifier flags lots of claims that look correct</strong></summary>

Most common causes:
- **The LLM paraphrased instead of quoting verbatim.** Open the source on disk under `.sources/<basename>/` and check. If the source actually says the same thing in different words, the verifier is doing its job — it's strict by design.
- **A number with units the source writes differently.** Source says `latency: 50 ms`, skill says `50ms` — the verifier strips trailing letter suffixes but `50 ms` vs `50ms` is a known edge case. Open the source to check.
- **Identifiers from a code block the LLM rewrote.** v5 reads from priority-sorted file content; if the LLM invented method names not in the actual code, those are real flags.

Run with `--annotate-skill` to see ⚠ UNVERIFIED markers inline.

</details>

<details>
<summary><strong>"git clone timed out"</strong></summary>

Default timeout is 300s. For huge repos:
```bash
python skillforge.py --github user/big-repo --clone-timeout 1200
```

If the clone consistently times out, SkillForge automatically retries with `--depth 1 --single-branch`. If THAT also fails, the source is genuinely problematic — check connectivity or try a different repo.

</details>

<details>
<summary><strong>Skill file overwrites a previous one</strong></summary>

v5 disambiguates collisions automatically. If two sources produce skills named `payment-sdk`, the second saves to `payment-sdk__<6charhash>/SKILL.md`. The `.source_url` marker file inside each skill directory tracks which source produced it.

</details>

---

## Contributing

PRs welcome. The most impactful contributions right now:

1. **Better extraction prompts** — if you find a source type where extraction quality is low, submit the URL + expected output as a test case
2. **New source types** — add support for HuggingFace model pages, Notion exports, etc.
3. **Example skill files** — generate and submit high-quality skill files for popular papers
4. **New providers** — add support for Ollama, Together AI, or other OpenAI-compatible APIs
5. **Benchmark results** — run skill files vs raw PDFs with weak models and share the numbers

---

## License

MIT

---

## Author

**Anubhav Bharadwaj** — IIT (ISM) Dhanbad • IIT Kanpur MBA '28

Built while competing in WorldQuant IQC 2026, OpenAI Parameter Golf, and ImageCLEF 2026.

- GitHub: [@AnubhavBharadwaaj](https://github.com/AnubhavBharadwaaj)
