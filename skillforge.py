#!/usr/bin/env python3
"""
SkillForge v3 — Agentic Research Knowledge → LLM Skill File Pipeline

v3 changes:
  - AGENTIC: quality-gate retry loop (validate → fix gaps → re-validate, up to 3 rounds)
  - FIX: Gemini safety filter crash (finish_reason 2) handled gracefully
  - FIX: Rate limit retry with exponential backoff
  - QUALITY: GitHub prompt extracts math from code comments + demands benchmark results
  - QUALITY: Validation sends less content to avoid safety filter triggers

Usage:
  python skillforge.py --github https://github.com/owner/repo
  python skillforge.py --arxiv 2103.13630
  python skillforge.py --pdf paper.pdf
  python skillforge.py batch --list sources.txt
  python skillforge.py --arxiv 2103.13630 --provider gemini --model gemini-2.5-flash
  python skillforge.py --arxiv 2103.13630 --provider openrouter
  python skillforge.py --arxiv 2103.13630 --no-llm

Install:
  pip install anthropic PyMuPDF google-generativeai openai
"""

import warnings
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=DeprecationWarning)

import argparse, datetime, json, os, re, shutil, subprocess
import sys, tempfile, time, urllib.request
import threading
from pathlib import Path


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# TERMINAL SPINNER — keeps terminal alive during API calls
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class Spinner:
    """Animated spinner that runs during long API calls.
    Usage:
        with Spinner("Extracting chunk 1/3"):
            result = llm.call(...)
    """
    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message="Working", show_elapsed=True):
        self.message = message
        self.show_elapsed = show_elapsed
        self._stop = threading.Event()
        self._thread = None
        self._start_time = None

    def __enter__(self):
        self._start_time = time.time()
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        self._stop.set()
        self._thread.join()
        elapsed = time.time() - self._start_time
        # Clear spinner line and print completion
        sys.stdout.write(f"\r   ✓ {self.message} ({elapsed:.1f}s)\033[K\n")
        sys.stdout.flush()

    def _spin(self):
        i = 0
        while not self._stop.is_set():
            frame = self.FRAMES[i % len(self.FRAMES)]
            elapsed = time.time() - self._start_time
            if self.show_elapsed:
                line = f"\r   {frame} {self.message} [{elapsed:.0f}s]"
            else:
                line = f"\r   {frame} {self.message}"
            sys.stdout.write(f"{line}\033[K")
            sys.stdout.flush()
            i += 1
            self._stop.wait(0.1)

    def update(self, new_message):
        """Update message mid-spin."""
        self.message = new_message


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OPTIONAL IMPORTS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _require_anthropic():
    try: import anthropic; return anthropic
    except ImportError: print("❌ pip install anthropic"); sys.exit(1)

def _require_gemini():
    try: import google.generativeai as genai; return genai
    except ImportError: print("❌ pip install google-generativeai"); sys.exit(1)

def _require_openai():
    try: import openai; return openai
    except ImportError: print("❌ pip install openai"); sys.exit(1)

def _require_fitz():
    try: import fitz; return fitz
    except ImportError: print("❌ pip install PyMuPDF"); sys.exit(1)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SHARED UTILS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def sizeof_fmt(n):
    for u in ("B","KB","MB","GB"):
        if abs(n) < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"

def estimate_tokens(text): return int(len(text) / 3.5)

def clean_llm_output(text):
    text = text.strip()
    for pfx in ("```markdown", "```md", "```yaml", "```"):
        if text.startswith(pfx): text = text[len(pfx):].strip(); break
    if text.endswith("```"): text = text[:-3].strip()
    return text

def parse_json_from_llm(text):
    """Robustly extract JSON from LLM response."""
    text = text.strip()
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)
    text = text.strip()
    try: return json.loads(text)
    except json.JSONDecodeError: pass
    m = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if m:
        try: return json.loads(m.group())
        except json.JSONDecodeError: pass
    first, last = text.find('{'), text.rfind('}')
    if first != -1 and last > first:
        try: return json.loads(text[first:last+1])
        except json.JSONDecodeError: pass
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OPENROUTER — LIVE MODEL DISCOVERY + RANKING ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Hardcoded fallback — PROVEN WORKING models, used if live API discovery fails
OPENROUTER_FALLBACK_MODELS = [
    "google/gemini-2.5-flash-preview:free",
    "google/gemini-2.5-pro-exp-03-25:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-4-maverick:free",
    "meta-llama/llama-4-scout:free",
    "qwen/qwen3-235b-a22b:free",
    "google/gemma-3-27b-it:free",
    "microsoft/phi-4-reasoning-plus:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "deepseek/deepseek-r1-0528:free",
]

# Cache: avoid hitting /models every run
_MODEL_CACHE = {"models": None, "ts": 0, "ttl": 3600}  # 1 hour TTL


def _discover_free_models(api_key):
    """Hit OpenRouter /api/v1/models, filter free, rank by composite score."""
    import urllib.request, urllib.error

    # Check cache
    if _MODEL_CACHE["models"] and (time.time() - _MODEL_CACHE["ts"]) < _MODEL_CACHE["ttl"]:
        return _MODEL_CACHE["models"]

    print("   🔍 Discovering free models from OpenRouter…")
    url = "https://openrouter.ai/api/v1/models"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "SkillForge/4.0",
    })
    try:
        with Spinner("Fetching model registry"):
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"   ⚠ Discovery failed ({e}), using fallback pool")
        return None

    models = raw.get("data", [])
    if not models:
        print("   ⚠ No models returned, using fallback pool")
        return None

    # ── Filter: strict — only real text-gen chat models ──
    # Many "free" models are vision encoders, music generators, embedding
    # models, or overloaded behemoths that return blank. Filter aggressively.

    # Blacklist: models that technically list "text" output but aren't chat models
    BLACKLIST_PATTERNS = [
        "lyria",           # Google's music generation model
        "clip",            # Vision encoder, not text gen
        "imagen",          # Image generation
        "music",           # Music models
        "whisper",         # Audio transcription
        "embedding",       # Embedding models
        "rerank",          # Reranking models
        "tts",             # Text-to-speech
        "stable-diffusion",# Image gen
        "sdxl",            # Image gen
        "flux",            # Image gen
        "dall-e",          # Image gen
        "moderation",      # Content moderation
        "guard",           # Safety classifier
        "vision-only",     # Vision-only
        "ocr",             # OCR models
        "jina",            # Embedding/rerank
        "nomic",           # Embedding
        "voyage",          # Embedding
    ]

    # Require these params — proves it's a real chat model
    REQUIRED_PARAMS = {"temperature", "max_tokens"}

    free = []
    skipped_reasons = {"non_free": 0, "blacklisted": 0, "no_text_io": 0,
                       "short_context": 0, "low_completion": 0, "no_chat_params": 0}

    for m in models:
        mid = m.get("id", "")
        mid_lower = mid.lower()
        name_lower = m.get("name", "").lower()
        pricing = m.get("pricing", {})
        prompt_cost = pricing.get("prompt", "1")
        completion_cost = pricing.get("completion", "1")

        # Must be free
        if str(prompt_cost) not in ("0", "0.0", "0.00") or str(completion_cost) not in ("0", "0.0", "0.00"):
            skipped_reasons["non_free"] += 1
            continue

        # Blacklist check — name or ID contains non-chat model keywords
        if any(bl in mid_lower or bl in name_lower for bl in BLACKLIST_PATTERNS):
            skipped_reasons["blacklisted"] += 1
            continue

        # Must have text input AND text output
        arch = m.get("architecture", {})
        in_modalities = arch.get("input_modalities", [])
        out_modalities = arch.get("output_modalities", [])
        if out_modalities and "text" not in out_modalities:
            skipped_reasons["no_text_io"] += 1
            continue
        if in_modalities and "text" not in in_modalities:
            skipped_reasons["no_text_io"] += 1
            continue

        # Must be text→text or text+image→text (not image→image, audio→text, etc.)
        modality = arch.get("modality", "")
        if modality and "text" not in modality:
            skipped_reasons["no_text_io"] += 1
            continue

        # Must have sufficient context (≥32K — papers are big)
        ctx = m.get("context_length", 0)
        if ctx < 32000:
            skipped_reasons["short_context"] += 1
            continue

        # Must support basic chat params — proves it's a real LLM
        supported = set(m.get("supported_parameters", []))
        if not REQUIRED_PARAMS.issubset(supported):
            skipped_reasons["no_chat_params"] += 1
            continue

        # Must have reasonable completion cap (≥4K tokens for skill file output)
        top_p = m.get("top_provider", {})
        max_completion = top_p.get("max_completion_tokens") or 4096
        if max_completion < 4000:
            skipped_reasons["low_completion"] += 1
            continue

        free.append({
            "id": mid,
            "name": m.get("name", mid),
            "context_length": ctx,
            "max_completion": max_completion,
            "created": m.get("created", 0),
            "supported_params": m.get("supported_parameters", []),
            "description": m.get("description", ""),
        })

    # Log filtering stats
    total_skipped = sum(skipped_reasons.values())
    print(f"   ✓ {len(models)} total → {len(free)} passed filters ({total_skipped} rejected)")
    if not free:
        # Show why everything got filtered
        for reason, count in skipped_reasons.items():
            if count: print(f"      {reason}: {count}")
        print("   ⚠ No free models matched filters, using fallback pool")
        return None

    # ── Rank by composite SkillForge Quality Score (SQS) ──
    ranked = _rank_models(free)

    # Cache
    _MODEL_CACHE["models"] = ranked
    _MODEL_CACHE["ts"] = time.time()

    return ranked


def _rank_models(models):
    """
    SkillForge Quality Score (SQS) — industry-standard composite metric.

    Components (weighted):
      1. Context Length Score  (25%) — longer = can ingest bigger papers
      2. Completion Cap Score  (25%) — higher = can output full 400+ line skill files
      3. Capability Score      (20%) — json_mode, tool use, system prompt support
      4. Recency Score          (15%) — newer models extract better
      5. Parameter Scale Score  (15%) — larger models inferred from name

    Each sub-score is normalized to [0, 1]. Final SQS = weighted sum × 100.
    """
    if not models:
        return []

    now = time.time()

    # Pre-compute max values for normalization
    max_ctx = max(m["context_length"] for m in models)
    max_comp = max(m["max_completion"] for m in models)
    max_created = max(m["created"] for m in models) if any(m["created"] for m in models) else now
    min_created = min(m["created"] for m in models if m["created"] > 0) if any(m["created"] > 0 for m in models) else now - 86400 * 365

    scored = []
    for m in models:
        # 1. Context Length Score (25%)
        ctx_score = min(m["context_length"] / max(max_ctx, 1), 1.0) if max_ctx > 0 else 0.5

        # 2. Completion Cap Score (25%)
        comp_score = min(m["max_completion"] / max(max_comp, 1), 1.0) if max_comp > 0 else 0.5

        # 3. Capability Score (20%) — check for useful params
        valuable_params = {"json_object", "response_format", "tools", "tool_choice",
                           "temperature", "top_p", "max_tokens", "system"}
        supported = set(m.get("supported_params", []))
        cap_score = len(supported & valuable_params) / len(valuable_params)

        # 4. Recency Score (15%) — newer is better
        if m["created"] > 0 and max_created > min_created:
            rec_score = (m["created"] - min_created) / (max_created - min_created)
        else:
            rec_score = 0.5

        # 5. Parameter Scale Score (15%) — infer size from model name/ID
        scale_score = _infer_param_scale(m["id"], m["name"])

        # Weighted composite
        sqs = (
            0.25 * ctx_score +
            0.25 * comp_score +
            0.20 * cap_score +
            0.15 * rec_score +
            0.15 * scale_score
        ) * 100

        scored.append({**m, "sqs": round(sqs, 1),
                       "_scores": {
                           "ctx": round(ctx_score, 2), "comp": round(comp_score, 2),
                           "cap": round(cap_score, 2), "rec": round(rec_score, 2),
                           "scale": round(scale_score, 2),
                       }})

    # Sort descending by SQS
    scored.sort(key=lambda x: x["sqs"], reverse=True)
    return scored


def _infer_param_scale(model_id, name):
    """Infer relative model size from ID/name. Returns [0, 1]."""
    text = f"{model_id} {name}".lower()

    # Extract numbers that look like parameter counts
    # Patterns: 405b, 70b, 27b, 8b, 3b, etc.
    matches = re.findall(r'(\d+\.?\d*)\s*b(?:illion)?', text)
    if matches:
        largest = max(float(m) for m in matches)
        # Normalize: 1B=0.1, 7B=0.3, 70B=0.7, 405B=0.95, 1000B+=1.0
        return min(largest / 500, 1.0)

    # MoE models often list active/total: "235b-a22b"
    moe = re.findall(r'(\d+)b.*?a(\d+)b', text)
    if moe:
        total = float(moe[0][0])
        return min(total / 500, 1.0)

    # Known large models without explicit size
    if any(k in text for k in ["gpt-4", "gemini-2.5-pro", "claude-3.5"]):
        return 0.85
    if any(k in text for k in ["gemini-2.5-flash", "deepseek-v3", "deepseek-chat-v3"]):
        return 0.75
    if any(k in text for k in ["llama-4", "qwen3"]):
        return 0.70

    return 0.4  # unknown → assume mid-tier


def _print_model_rankings(models, top_n=8):
    """Print ranked model table to terminal."""
    show = models[:top_n]
    max_name = min(max(len(m["id"].split("/")[-1]) for m in show), 35)

    print(f"   ┌─{'─'*3}─┬─{'─'*max_name}─┬─{'─'*5}─┬─{'─'*8}─┬─{'─'*8}─┬─{'─'*4}─┐")
    print(f"   │ {'#':>3} │ {'Model':<{max_name}} │ {'SQS':>5} │ {'Context':>8} │ {'MaxComp':>8} │ {'Rec':>4} │")
    print(f"   ├─{'─'*3}─┼─{'─'*max_name}─┼─{'─'*5}─┼─{'─'*8}─┼─{'─'*8}─┼─{'─'*4}─┤")

    for i, m in enumerate(show):
        short = m["id"].split("/")[-1][:max_name]
        ctx_k = f"{m['context_length']//1000}K"
        comp_k = f"{m['max_completion']//1000}K"
        rec = m["_scores"]["rec"]
        marker = " ◄" if i == 0 else ""
        print(f"   │ {i+1:>3} │ {short:<{max_name}} │ {m['sqs']:>5} │ {ctx_k:>8} │ {comp_k:>8} │ {rec:>4} │{marker}")

    print(f"   └─{'─'*3}─┴─{'─'*max_name}─┴─{'─'*5}─┴─{'─'*8}─┴─{'─'*8}─┴─{'─'*4}─┘")

    if len(models) > top_n:
        print(f"   … +{len(models)-top_n} more models in fallback pool")


def _build_model_pool(api_key, user_model=None):
    """Discover, rank, and return ordered model ID list."""
    ranked = _discover_free_models(api_key)

    if ranked:
        print(f"   ✓ Found {len(ranked)} free models, ranked by SQS:")
        _print_model_rankings(ranked)
        pool = [m["id"] for m in ranked]
    else:
        print(f"   ⚠ Using hardcoded fallback pool ({len(OPENROUTER_FALLBACK_MODELS)} models)")
        pool = list(OPENROUTER_FALLBACK_MODELS)

    # If user specified a model, put it first
    if user_model:
        pool = [user_model] + [m for m in pool if m != user_model]

    return pool


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LLM PROVIDER — v4: openrouter + agentic model rotation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class LLMProvider:
    def __init__(self, provider="anthropic", model=None, api_key=None):
        self.provider = provider
        if provider == "anthropic":
            mod = _require_anthropic()
            self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not self.api_key: print("❌ Set ANTHROPIC_API_KEY"); sys.exit(1)
            self.client = mod.Anthropic(api_key=self.api_key)
            self.model = model or "claude-sonnet-4-20250514"
        elif provider == "gemini":
            genai = _require_gemini()
            self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
            if not self.api_key: print("❌ Set GEMINI_API_KEY"); sys.exit(1)
            genai.configure(api_key=self.api_key)
            self.model = model or "gemini-2.5-pro"
            self._genai = genai
        elif provider == "openrouter":
            openai = _require_openai()
            self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
            if not self.api_key: print("❌ Set OPENROUTER_API_KEY"); sys.exit(1)
            self.client = openai.OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=self.api_key,
            )
            # Live discovery + ranking
            self._model_pool = _build_model_pool(self.api_key, model)
            self._exhausted = set()
            self._current_idx = 0
            self.model = self._model_pool[0]
            self._call_count = 0
            self._rotations = 0
            _or_banner(self.model, len(self._model_pool))
        else:
            print(f"❌ Unknown provider: {provider}"); sys.exit(1)

    # ── OpenRouter model rotation ──

    def _rotate_model(self, failed_model, reason="rate_limit"):
        """Rotate to next available free model. Returns True if rotation succeeded."""
        self._exhausted.add(failed_model)
        available = [m for m in self._model_pool if m not in self._exhausted]
        if not available:
            print(f"   {'─'*50}")
            print(f"   ❌ All {len(self._model_pool)} models exhausted. No fallbacks left.")
            print(f"   💡 Wait 60s for rate limits to reset, or use --provider anthropic")
            print(f"   {'─'*50}")
            return False
        old_short = failed_model.split("/")[-1][:30]
        self.model = available[0]
        self._current_idx = self._model_pool.index(self.model)
        self._rotations += 1
        new_short = self.model.split("/")[-1][:30]
        remaining = len(available) - 1
        # Look up SQS if available from cache
        sqs_str = ""
        if _MODEL_CACHE["models"]:
            for cm in _MODEL_CACHE["models"]:
                if cm["id"] == self.model:
                    sqs_str = f" (SQS: {cm['sqs']})"
                    break
        # Elegant terminal output
        print(f"   {'─'*50}")
        print(f"   🔄 Model rotation #{self._rotations}")
        print(f"   ├─ ✗ {old_short} → {reason}")
        print(f"   ├─ ✓ {new_short}{sqs_str}")
        print(f"   └─ {remaining} fallback{'s' if remaining != 1 else ''} remaining")
        print(f"   {'─'*50}")
        return True

    def _is_rate_limit(self, error):
        """Detect rate limit errors across providers."""
        s = str(error).lower()
        return any(k in s for k in [
            "429", "rate_limit", "rate limit", "resource_exhausted",
            "quota", "too many requests", "requests per minute",
            "capacity", "overloaded", "try again",
        ])

    def _is_permission_error(self, error):
        s = str(error).lower()
        return any(k in s for k in ["403", "permission_denied", "denied access", "forbidden"])

    def _is_context_length_error(self, error):
        s = str(error).lower()
        return any(k in s for k in ["context_length", "too long", "maximum context", "token limit"])

    # ── Main generate with rotation ──

    def generate(self, system_prompt, user_message, max_tokens=16000, json_mode=False):
        """Generate with retry, model rotation on rate limits, and safety filter handling."""
        max_attempts = len(self._model_pool) + 2 if self.provider == "openrouter" else 3

        for attempt in range(max_attempts):
            try:
                result = self._generate_inner(system_prompt, user_message, max_tokens, json_mode)
                if self.provider == "openrouter":
                    self._call_count += 1
                return result
            except Exception as e:
                err_str = str(e).lower()

                # ── OpenRouter: rotate on rate limit ──
                if self.provider == "openrouter" and self._is_rate_limit(e):
                    if self._rotate_model(self.model, "rate_limit"):
                        continue
                    else:
                        # All exhausted — wait and reset
                        print(f"   ⏳ All models exhausted. Waiting 60s for reset…")
                        time.sleep(60)
                        self._exhausted.clear()
                        self.model = self._model_pool[0]
                        print(f"   🔄 Pool reset. Retrying with {self.model.split('/')[-1][:30]}")
                        continue

                # ── OpenRouter: rotate on permission denied ──
                if self.provider == "openrouter" and self._is_permission_error(e):
                    if self._rotate_model(self.model, "permission_denied"):
                        continue
                    return ""

                # ── OpenRouter: rotate on context length ──
                if self.provider == "openrouter" and self._is_context_length_error(e):
                    if self._rotate_model(self.model, "context_too_long"):
                        continue
                    return ""

                # ── OpenRouter: rotate on empty/blank response ──
                if self.provider == "openrouter" and ("empty response" in err_str or "blank content" in err_str):
                    if self._rotate_model(self.model, "empty_response"):
                        continue
                    return ""

                # ── Non-OpenRouter rate limit ──
                if self._is_rate_limit(e):
                    wait = (attempt + 1) * 30
                    print(f"   ⏳ Rate limited, waiting {wait}s (attempt {attempt+1}/3)…")
                    time.sleep(wait)
                    continue

                # ── Safety filter ──
                if "finish_reason" in err_str or "valid `part`" in err_str:
                    print(f"   ⚠ Safety filter triggered, returning empty")
                    return ""

                # ── Unknown error ──
                raise

        print("   ❌ Max retries exceeded")
        return ""

    def _generate_inner(self, system_prompt, user_message, max_tokens, json_mode=False):
        if self.provider == "anthropic":
            with Spinner(f"Generating via {self.model.split('/')[-1][:25]}"):
                msg = self.client.messages.create(
                    model=self.model, max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                )
            print(f"   [{self.provider}] {msg.usage.input_tokens:,} in / {msg.usage.output_tokens:,} out")
            return msg.content[0].text

        elif self.provider == "gemini":
            gm = self._genai.GenerativeModel(self.model, system_instruction=system_prompt)
            gen_config = {"max_output_tokens": max_tokens, "temperature": 0.2}
            if json_mode:
                gen_config["response_mime_type"] = "application/json"
            with Spinner(f"Generating via {self.model}"):
                try:
                    resp = gm.generate_content(
                        user_message,
                        generation_config=self._genai.types.GenerationConfig(**gen_config),
                    )
                except TypeError:
                    gen_config.pop("response_mime_type", None)
                    resp = gm.generate_content(
                        user_message,
                        generation_config=self._genai.types.GenerationConfig(**gen_config),
                    )
            # Check for safety block before accessing .text
            if not resp.candidates or not resp.candidates[0].content.parts:
                fr = resp.candidates[0].finish_reason if resp.candidates else "unknown"
                print(f"   ⚠ Gemini returned no content (finish_reason={fr})")
                return ""
            result = resp.text
            try:
                print(f"   [{self.provider}] {resp.usage_metadata.prompt_token_count:,} in / {resp.usage_metadata.candidates_token_count:,} out")
            except: print(f"   [{self.provider}] {len(result)} chars")
            return result

        elif self.provider == "openrouter":
            model_short = self.model.split("/")[-1][:25]
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]
            kwargs = {
                "model": self.model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": 0.2,
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            with Spinner(f"Generating via {model_short}"):
                resp = self.client.chat.completions.create(**kwargs)

            # Guard against None responses
            if not resp or not resp.choices or not resp.choices[0].message:
                print(f"   ⚠ {model_short} returned empty response")
                raise RuntimeError(f"Empty response from {self.model} — rotating")
            text = resp.choices[0].message.content or ""
            if not text.strip():
                print(f"   ⚠ {model_short} returned blank content")
                raise RuntimeError(f"Blank content from {self.model} — rotating")
            # Token logging
            if resp.usage:
                print(f"   [openrouter/{model_short}] {resp.usage.prompt_tokens:,} in / {resp.usage.completion_tokens:,} out")
            else:
                print(f"   [openrouter/{model_short}] {len(text)} chars")
            return text

    def generate_multi_turn(self, system_prompt, messages, max_tokens=16000):
        if self.provider == "anthropic":
            msg = self.client.messages.create(
                model=self.model, max_tokens=max_tokens,
                system=system_prompt, messages=messages,
            )
            print(f"   [{self.provider}] {msg.usage.input_tokens:,} in / {msg.usage.output_tokens:,} out")
            return msg.content[0].text
        elif self.provider == "gemini":
            gm = self._genai.GenerativeModel(self.model, system_instruction=system_prompt)
            chat = gm.start_chat()
            result = None
            for m in messages:
                if m["role"] == "assistant":
                    chat.history.append({"role": "model", "parts": [m["content"]]})
                else:
                    try:
                        resp = chat.send_message(
                            m["content"],
                            generation_config=self._genai.types.GenerationConfig(
                                max_output_tokens=max_tokens, temperature=0.2,
                            ),
                        )
                        result = resp.text
                    except Exception as e:
                        if "finish_reason" in str(e).lower() or "valid `part`" in str(e).lower():
                            print(f"   ⚠ Safety filter in multi-turn, returning partial")
                            return result or ""
                        raise
            return result or ""
        elif self.provider == "openrouter":
            # Build full conversation for OpenRouter
            api_messages = [{"role": "system", "content": system_prompt}]
            for m in messages:
                api_messages.append({
                    "role": m["role"] if m["role"] != "assistant" else "assistant",
                    "content": m["content"],
                })
            model_short = self.model.split("/")[-1][:25]
            # Use same rotation-aware generate path
            for attempt in range(len(self._model_pool) + 2):
                try:
                    with Spinner(f"Multi-turn via {model_short}"):
                        resp = self.client.chat.completions.create(
                            model=self.model,
                            messages=api_messages,
                            max_tokens=max_tokens,
                            temperature=0.2,
                        )
                    if not resp or not resp.choices or not resp.choices[0].message:
                        raise RuntimeError(f"Empty response from {self.model}")
                    text = resp.choices[0].message.content or ""
                    if not text.strip():
                        raise RuntimeError(f"Blank content from {self.model}")
                    if resp.usage:
                        print(f"   [openrouter/{model_short}] {resp.usage.prompt_tokens:,} in / {resp.usage.completion_tokens:,} out")
                    else:
                        print(f"   [openrouter/{model_short}] {len(text)} chars")
                    self._call_count += 1
                    return text
                except Exception as e:
                    if self._is_rate_limit(e) or "empty response" in str(e).lower() or "blank content" in str(e).lower():
                        if self._rotate_model(self.model, "rate_limit"):
                            model_short = self.model.split("/")[-1][:25]
                            continue
                    raise
            return ""


def _or_banner(model, pool_size):
    """Print OpenRouter startup banner with SQS."""
    short = model.split("/")[-1][:35]
    sqs = "—"
    if _MODEL_CACHE["models"]:
        for cm in _MODEL_CACHE["models"]:
            if cm["id"] == model:
                sqs = f"{cm['sqs']}/100"
                break
    source = "live API" if _MODEL_CACHE["models"] else "fallback"
    print(f"   ┌{'─'*52}┐")
    print(f"   │ {'🌐 OpenRouter — Agentic Model Routing':^50} │")
    print(f"   ├{'─'*52}┤")
    print(f"   │ {'Primary:':<12} {short:<38} │")
    print(f"   │ {'SQS:':<12} {sqs:<38} │")
    print(f"   │ {'Pool:':<12} {f'{pool_size} models ({source})':<38} │")
    print(f"   │ {'Rotation:':<12} {'automatic on rate limit':<38} │")
    print(f"   │ {'Cost:':<12} {'$0 (free tier models)':<38} │")
    print(f"   └{'─'*52}┘")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SYSTEM PROMPTS — v3
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CHUNK_EXTRACTION_PROMPT = r"""You are SkillForge, extracting implementation-critical content from a SECTION of a research paper.

Extract EVERYTHING from this section. Your output will be merged with other sections — anything you skip is LOST FOREVER.

MANDATORY CHECKLIST — extract ALL that appear:

□ Every equation/formula with ALL variable definitions
□ Every table — reproduce completely in markdown with ALL rows and columns
□ Every algorithm/pseudocode — reproduce verbatim
□ Every architecture detail — layer counts, dimensions, channel sizes, activations
□ Every hyperparameter — learning rates, batch sizes, optimizers, schedules, warmup
□ Every experimental result — accuracy, perplexity, loss values, speedups with numbers
□ Every hardware/efficiency measurement — energy (pJ), area (μm²), latency, throughput
□ Every comparison table — model vs model with actual numbers
□ Every initialization scheme — distributions, scale factors
□ Every loss function — full formula with all terms and weights
□ Every known failure mode, bug, edge case, caveat
□ Every dependency/library/framework mentioned

Write equations as:
```
Equation N: [description]
Q(r) = Int(r/S) - Z
where: r = real-valued input, S = scaling factor, Z = zero point
```

DO NOT summarize. Extract SPECIFIC methods with SPECIFIC details.

SECTION TEXT:
"""

PAPER_SYSTEM_PROMPT = r"""You are SkillForge, producing the FINAL merged SKILL.md from partial extractions of a research paper.

QUALITY TARGET: 400-600 lines. Under 350 = dropping critical content.

MANDATORY CONTENT:
1. YAML frontmatter with name + trigger-heavy description (100-150 words)
2. Paper metadata (all authors, affiliation, venue, year)
3. EVERY equation — numbered, all variable definitions
4. EVERY results/comparison table — ALL rows and numbers
5. EVERY algorithm/pseudocode — complete
6. ALL hyperparameters in dedicated section/table
7. ALL architecture details with exact dimensions
8. ALL hardware efficiency data in tables
9. ALL known failure modes, caveats
10. 8-12 implementation takeaways
11. Key references section

FORMAT:
---
name: [kebab-case]
description: [100-150 words, trigger-heavy, include "Use this skill when..." with 4+ scenarios]
---
# [Title]
**Paper**: [Authors] ([Affiliations])
**Published**: [Venue, Year]
**Key result**: [One-line with specific numbers]
---
## 1. Problem Setup and Notation
## 2-N. [Core sections]
## N+1. Experimental Results [COMPLETE tables]
## N+2. Hardware/Efficiency Data [tables]
## N+3. Key Takeaways (8-12 items)
## N+4. References

RULES:
1. NEVER skip an equation
2. NEVER summarize a table — REPRODUCE with numbers
3. NEVER say "various methods" — NAME each with details
4. Deduplicate keeping MORE detailed version
5. DO NOT wrap in code fences

{domain_context}"""


GITHUB_SYSTEM_PROMPT = r"""You are SkillForge, creating a SKILL.md for a GitHub repository.

QUALITY TARGET: 400-700 lines.

## TASK
Analyze the source code dump EXHAUSTIVELY. Extract ALL implementation details.

## CRITICAL ADDITIONS FOR v3
When code references formulas from papers (in comments, docstrings, or variable names):
- WRITE OUT the full mathematical formula, not just "implements equation (4) from [paper]"
- If a loss function is computed in code, write both the CODE and the MATH formula it implements
- If a paper is referenced (arXiv ID, citation), note the paper and what concept is borrowed

When README or configs mention benchmark results:
- REPRODUCE them in a table (training loss, validation perplexity, comparison vs baselines)
- Include hardware specs, training time, GPU requirements

## OUTPUT FORMAT

### YAML Frontmatter
---
name: <id>
description: <100-150 words with triggers>
---

### Sections (ALL required):

**1. What It Does** — 2-4 paragraphs, key insight/novelty

**2. Architecture Overview**
- 2.1 Component Summary TABLE: Component | Class/Module | Purpose
- 2.2 Pipeline Flow — ASCII art with data shapes

**3-N. Detailed Code Analysis** — per major module:
- Class __init__ with annotated params (types, defaults, what each does)
- forward() with exact input/output tensor shapes
- Config values as tables (dimensions, channels, hidden sizes)
- Important patterns with annotated code snippets
- Loss functions: BOTH the code AND the math formula they implement

**N+1. Mathematical Foundations**
- Every formula referenced or implemented in the code
- Written as equations with variable definitions
- Cross-referenced to the code that implements them

**N+2. Training Configuration**
- ALL hyperparameters from config files in a table
- Optimizer, LR schedule, batch size, warmup, total steps/tokens

**N+3. Benchmark Results**
- Training/validation loss or perplexity from README or logs
- Comparison vs baselines (e.g., GPT-2 val loss vs this model's)
- Hardware requirements and training time

**N+4. Pretrained Models** — Table: Model | Source | Download | Performance

**N+5. Dependencies** — exact versions, known conflicts

**N+6. Adaptation & Reuse Patterns** — what's reusable, how to adapt

## RULES
1. Don't guess — say "unclear from source" if unsure
2. Extract EXACT numbers from code/configs
3. Show annotated code snippets (strip boilerplate)
4. When code computes a loss/metric, write the MATH formula it implements
5. Tables for ALL structured info
6. DO NOT wrap output in code fences

{domain_context}"""


GAP_FIX_PROMPT = r"""You are SkillForge's agentic repair pass. A skill file was generated but scored poorly on validation.

VALIDATION RESULT:
Score: {score}/10
Gaps identified:
{gaps}

YOUR TASK: Fix ALL identified gaps. You have the current skill file and the original source content.

RULES:
1. Output the COMPLETE corrected skill file (not a diff, not patches)
2. Keep everything that was already correct
3. ADD the missing content identified in the gaps
4. If a gap says "missing mathematics" — find and write out the specific equations
5. If a gap says "missing numbers/results" — find and add the specific tables/benchmarks
6. Maintain the same YAML frontmatter and overall structure
7. DO NOT wrap output in code fences

CURRENT SKILL FILE:
{skill_content}

ORIGINAL SOURCE:
{source_excerpt}

Output the COMPLETE corrected SKILL.md:"""


VALIDATION_PROMPT = """Review this skill file for completeness (1-10 scale).

Check:
1. MISSING MATH: Equations referenced but not written?
2. MISSING RESULTS: Benchmarks mentioned but not tabulated?
3. VAGUE CONTENT: "various methods" without specifics?
4. IMPLEMENTATION GAPS: Could an LLM implement using ONLY this?
5. MISSING CONFIG: Hyperparameters not listed?

Skill file (excerpt):
{skill_content}

Respond with ONLY valid JSON. No fences. No explanation:
{{"score": <1-10>, "gaps": ["gap1", "gap2"], "suggested_additions": ["add1"], "is_production_ready": true}}"""


ENRICHMENT_PROMPT = r"""Find MISSING content that should be in the skill file but isn't.

Focus on:
1. EQUATIONS missing from the skill file
2. TABLES with numerical results not reproduced
3. SPECIFIC NUMBERS not captured
4. ALGORITHM pseudocode not included

Output ONLY the missing content as appendable markdown sections.
If nothing significant is missing, respond: "NO_GAPS_FOUND"
Do NOT repeat existing content."""


DOMAIN_CONTEXTS = {
    "imageclef": "\n## DOMAIN CONTEXT: ImageCLEF Deepfake 2026\n**Image**: 256x256 faces, MediaPipe 478 landmarks\n**Audio**: zero-shot voice cloning, 16kHz mono WAV\n**Adaptation notes** and **reusable patterns** required.\n",
    "parametergolf": "\n## DOMAIN CONTEXT: Parameter Golf\n- Parameter count vs perplexity tradeoffs?\n- Quantization, pruning, weight sharing applicable?\n- Compression ratios from experiments?\n",
    "kaggle": "\n## DOMAIN CONTEXT: Kaggle\n- Competitive edge components?\n- Inference speed for time limits?\n- Ensemble compatibility?\n",
}

def get_domain_context(d):
    if not d: return ""
    return DOMAIN_CONTEXTS.get(d, f"\n## DOMAIN CONTEXT: {d}\nAssess relevance.\n")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AGENTIC QUALITY LOOP — v3 core addition
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def agentic_quality_loop(skill_content, source_text, llm, max_retries=2):
    """
    Validate → if score < 7, fix gaps → re-validate → repeat.
    This is what makes SkillForge agentic: observe, reason, act, loop.
    """
    best_content = skill_content
    best_score = 0

    for attempt in range(max_retries + 1):
        # ── Observe: validate current output ──
        validation = validate_skill(best_content, llm)
        score = validation.get("score", 0)
        gaps = validation.get("gaps", [])

        if attempt == 0:
            print(f"🔍 Initial quality: {score}/10")
        else:
            print(f"🔍 Retry {attempt} quality: {score}/10")

        if score >= 7:
            print(f"   ✓ Quality target met ({score}/10)")
            return best_content, validation

        if score > best_score:
            best_score = score
            best_content_at_best = best_content

        if attempt >= max_retries:
            print(f"   ⚠ Max retries reached, using best ({best_score}/10)")
            break

        if not gaps or gaps == ["Validation parse failed"]:
            print(f"   ⚠ No actionable gaps, skipping retry")
            break

        # ── Reason + Act: fix identified gaps ──
        gaps_str = "\n".join(f"- {g}" for g in gaps)
        print(f"   🔧 Fixing {len(gaps)} gaps…")

        # Send truncated source to stay within context limits
        source_excerpt = source_text[:35000] if source_text else ""

        fixed = llm.generate(
            "You are SkillForge's repair agent. Fix all gaps in this skill file.",
            GAP_FIX_PROMPT.format(
                score=score,
                gaps=gaps_str,
                skill_content=best_content,
                source_excerpt=source_excerpt,
            ),
            max_tokens=16000,
        )

        if fixed and len(fixed) > len(best_content) * 0.5:
            best_content = clean_llm_output(fixed)
            new_lines = best_content.count('\n')
            print(f"   📝 Fixed version: {new_lines} lines")
        else:
            print(f"   ⚠ Fix pass returned insufficient content, keeping previous")
            break

        time.sleep(2)  # Rate limit courtesy

    # Return best we got
    final_validation = validate_skill(best_content, llm) if best_score < 7 else validation
    return best_content, final_validation


def validate_skill(skill_content, llm):
    """v3.1: Use json_mode for Gemini, debug output, regex fallback."""
    print("   🔍 Validating…")
    # Send only first 8K chars to avoid safety filter
    excerpt = skill_content[:8000]
    try:
        text = llm.generate(
            "Respond with ONLY a JSON object. No fences. No explanation.",
            VALIDATION_PROMPT.format(skill_content=excerpt),
            max_tokens=1000,
            json_mode=True,  # Forces JSON output on Gemini, skips thinking tokens
        )
        if not text:
            return {"score": 0, "gaps": ["Validation returned empty"], "is_production_ready": False}

        # Try standard JSON parsing first
        parsed = parse_json_from_llm(text)
        if parsed and "score" in parsed:
            return parsed

        # Fallback: regex extraction if JSON parsing failed
        print(f"   ⚠ JSON parse failed, trying regex. Raw ({len(text)} chars): {text[:150]}")
        score_match = re.search(r'"score"\s*:\s*(\d+)', text)
        if score_match:
            score = int(score_match.group(1))
            gaps = re.findall(r'"([^"]{10,})"', text)  # Extract strings >10 chars as potential gaps
            return {"score": score, "gaps": gaps[:5], "is_production_ready": score >= 7}

    except Exception as e:
        print(f"   Validation error: {e}")
    return {"score": 0, "gaps": ["Validation parse failed"], "is_production_ready": False}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GITHUB REPO HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TEXT_EXTENSIONS = {
    ".py",".pyx",".pxd",".js",".jsx",".ts",".tsx",".mjs",".cjs",
    ".java",".kt",".kts",".scala",".groovy",
    ".c",".h",".cpp",".cxx",".cc",".hpp",".hxx",
    ".cs",".fs",".fsx",".go",".rs",".rb",".php",".pl",".pm",
    ".swift",".m",".mm",".lua",".r",".R",".jl",".ex",".exs",
    ".zig",".nim",".v",".d",
    ".sh",".bash",".zsh",".fish",".bat",".cmd",".ps1",
    ".sql",".graphql",".gql",".proto",".thrift",".avsc",
    ".md",".mdx",".rst",".txt",".text",".adoc",
    ".html",".htm",".xml",".xhtml",".svg",
    ".css",".scss",".sass",".less",".styl",
    ".json",".jsonc",".json5",".yaml",".yml",
    ".toml",".ini",".cfg",".conf",".env",".envrc",
    ".properties",".gradle",".dockerfile",".tf",".hcl",
    ".nix",".dhall",".ipynb",".csv",".tsv",
    ".cmake",".makefile",".mk",".lock",
}
ALWAYS_INCLUDE_NAMES = {
    "Dockerfile","Makefile","CMakeLists.txt","Gemfile","Rakefile",
    "Procfile","LICENSE","LICENCE","NOTICE","Pipfile","Brewfile",
    ".gitignore",".gitattributes",".gitmodules",".dockerignore",
    ".editorconfig",".flake8",".pylintrc",".clang-format",
    "requirements.txt","setup.py","setup.cfg","pyproject.toml",
    "package.json","package-lock.json","yarn.lock","pnpm-lock.yaml",
    "Cargo.toml","Cargo.lock","go.mod","go.sum",
    "build.gradle","settings.gradle","pom.xml",
    "Justfile","Taskfile.yml","CODEOWNERS",
    "CHANGELOG","CHANGELOG.md","CONTRIBUTING.md",
}
SKIP_DIRS = {
    ".git","node_modules","__pycache__",".tox",".nox",
    ".mypy_cache",".pytest_cache",".ruff_cache",
    "venv",".venv","env",".env",
    "dist","build","target","out","bin","obj",
    ".idea",".vscode",".vs","vendor","third_party","3rdparty",
    ".eggs","site-packages",".ipynb_checkpoints",
    "wandb","mlruns","lightning_logs","logs","log","__MACOSX",
}
PRIORITY_PATTERNS = [
    re.compile(r"^README", re.I), re.compile(r"^INSTALL", re.I),
    re.compile(r"^setup\.(py|cfg)$", re.I), re.compile(r"^pyproject\.toml$", re.I),
    re.compile(r"^requirements.*\.txt$", re.I), re.compile(r"^package\.json$", re.I),
    re.compile(r"^Cargo\.toml$", re.I), re.compile(r"^go\.mod$", re.I),
    re.compile(r"^Dockerfile", re.I), re.compile(r"^Makefile$", re.I),
]
KEEP_PRIORITY = [
    re.compile(r"README", re.I), re.compile(r"setup\.(py|cfg)", re.I),
    re.compile(r"pyproject\.toml", re.I), re.compile(r"requirements", re.I),
    re.compile(r"config", re.I), re.compile(r"model", re.I),
    re.compile(r"network", re.I), re.compile(r"train", re.I),
    re.compile(r"infer", re.I), re.compile(r"loss", re.I),
    re.compile(r"__init__\.py", re.I), re.compile(r"manager", re.I),
]
DROP_PRIORITY = [
    re.compile(r"\.lock$", re.I), re.compile(r"package-lock", re.I),
    re.compile(r"\.csv$|\.tsv$", re.I), re.compile(r"test_|_test\.|tests/", re.I),
    re.compile(r"LICENSE|LICENCE|NOTICE", re.I),
    re.compile(r"CONTRIBUTING|CODE_OF_CONDUCT", re.I),
    re.compile(r"\.github/", re.I), re.compile(r"docs/", re.I),
]
FENCE_LANG = {
    ".py":"python",".js":"javascript",".ts":"typescript",".java":"java",
    ".c":"c",".h":"c",".cpp":"cpp",".go":"go",".rs":"rust",".rb":"ruby",
    ".sh":"bash",".sql":"sql",".html":"html",".css":"css",
    ".json":"json",".yaml":"yaml",".yml":"yaml",".toml":"toml",
    ".md":"markdown",".dockerfile":"dockerfile",".ipynb":"python",
}

def is_text_file(p):
    if p.name in ALWAYS_INCLUDE_NAMES: return True
    if p.suffix.lower() in TEXT_EXTENSIONS: return True
    if not p.suffix and p.name.startswith("."): return True
    return False
def is_binary(d): return b"\x00" in d[:8192]
def skip_dir(d): return d in SKIP_DIRS or d.endswith(".egg-info")

def clone_repo(url, dest):
    url = url.rstrip("/")
    if not url.endswith(".git"): url += ".git"
    with Spinner(f"Cloning {url.split('/')[-1]}"):
        r = subprocess.run(["git","clone","--depth","1","--single-branch",url,dest],
                           capture_output=True, text=True)
    if r.returncode != 0: print(f"❌ git clone failed:\n{r.stderr}",file=sys.stderr); sys.exit(1)

def extract_repo_meta(url):
    url = url.rstrip("/")
    pts = url.replace("https://","").replace("http://","").split("/")
    return {"owner": pts[1] if len(pts)>1 else "?",
            "repo": pts[2].replace(".git","") if len(pts)>2 else "?", "url": url}

def build_tree(root, max_d=5):
    lines = []
    def _r(cur, pfx, d):
        if d > max_d: lines.append(f"{pfx}…"); return
        try: entries = sorted(cur.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        except PermissionError: return
        dirs = [e for e in entries if e.is_dir() and not skip_dir(e.name)]
        files = [e for e in entries if e.is_file()]
        items = dirs + files
        for i, e in enumerate(items):
            last = i == len(items)-1
            c = "└── " if last else "├── "
            if e.is_dir():
                lines.append(f"{pfx}{c}{e.name}/")
                _r(e, pfx+("    " if last else "│   "), d+1)
            else:
                lines.append(f"{pfx}{c}{e.name}  ({sizeof_fmt(e.stat().st_size)})")
    _r(root, "", 0)
    return "\n".join(lines)

def flatten_nb(content):
    try: nb = json.loads(content)
    except: return content
    parts = []
    for i, c in enumerate(nb.get("cells",[])):
        s = "".join(c.get("source",[]))
        if s.strip(): parts.append(f"# Cell {i+1} [{c.get('cell_type','?')}]\n{s}")
    return "\n\n".join(parts)

def collect_files(root, max_kb=200):
    inc, skip = [], []
    mx = max_kb * 1024
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if not skip_dir(d)]
        for fn in sorted(fns):
            fp = Path(dp)/fn; rel = str(fp.relative_to(root))
            if not is_text_file(fp): skip.append((rel,"non-text")); continue
            try: sz = fp.stat().st_size
            except: skip.append((rel,"unreadable")); continue
            if sz > mx: skip.append((rel,f">{max_kb}KB")); continue
            if sz == 0: skip.append((rel,"empty")); continue
            try: raw = fp.read_bytes()
            except: skip.append((rel,"read error")); continue
            if is_binary(raw): skip.append((rel,"binary")); continue
            try: txt = raw.decode("utf-8", errors="replace")
            except: skip.append((rel,"decode error")); continue
            inc.append((rel, txt))
    return inc, skip

def p_sort(rp):
    fn = Path(rp).name
    for i,p in enumerate(PRIORITY_PATTERNS):
        if p.search(fn): return (0,i,rp)
    return (1, rp.count("/"), rp)

def p_score(path):
    for i,p in enumerate(DROP_PRIORITY):
        if p.search(path): return (2,i,path)
    for i,p in enumerate(KEEP_PRIORITY):
        if p.search(path): return (0,i,path)
    return (1, path.count("/"), path)

def build_raw_dump(meta, tree, inc, skip):
    inc.sort(key=lambda p: p_sort(p[0]))
    ts = datetime.datetime.now().strftime("%d%b%Y_%I%M%p").lower()
    tc = sum(len(c) for _,c in inc)
    parts = [f"# RAW DUMP — {meta['owner']}/{meta['repo']}\n",
             f"- **Source:** {meta['url']}", f"- **Captured:** {ts}",
             f"- **Files:** {len(inc)}", f"- **Chars:** {tc:,}\n",
             "---\n\n## Directory Structure\n\n```", tree, "```\n"]
    if skip:
        parts.append("---\n\n## Skipped\n")
        for r,rs in sorted(skip)[:30]: parts.append(f"- `{r}` — {rs}")
    parts.append("\n---\n\n## File Contents\n")
    for rel, content in inc:
        suf = Path(rel).suffix.lower()
        if suf == ".ipynb": content = flatten_nb(content)
        lang = FENCE_LANG.get(suf, "")
        parts.append(f"### `{rel}`\n\n```{lang}\n{content}\n```\n")
    return "\n".join(parts)

def split_sections(dump):
    mk = "## File Contents"
    idx = dump.find(mk)
    if idx == -1: return dump, []
    header = dump[:idx+len(mk)]
    body = dump[idx+len(mk):]
    pat = re.compile(r'^### `(.+?)`\s*$', re.MULTILINE)
    ms = list(pat.finditer(body))
    secs = []
    for i,m in enumerate(ms):
        end = ms[i+1].start() if i+1<len(ms) else len(body)
        secs.append((m.group(1), body[m.start():end]))
    return header, secs

def trim_budget(dump, budget):
    cur = estimate_tokens(dump)
    if cur <= budget: return dump
    print(f"⚠️  ~{cur:,} tok > {budget:,}. Trimming…")
    header, secs = split_sections(dump)
    secs.sort(key=lambda p: p_score(p[0]))
    kept, dropped = [], []
    rem = budget - estimate_tokens(header) - 5000
    for path, content in secs:
        t = estimate_tokens(content)
        if rem >= t: kept.append((path,content)); rem -= t
        else: dropped.append(path)
    if dropped: print(f"   ✂️  Kept {len(kept)}, dropped {len(dropped)}")
    result = header + "\n\n"
    if dropped:
        result += f"> {len(dropped)} files trimmed\n\n"
    for _,c in kept: result += c
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GITHUB PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def github_pipeline(url, llm, output_dir, max_file_kb=200,
                    token_budget=150000, domain=None, skip_val=False):
    meta = extract_repo_meta(url)
    tmpdir = tempfile.mkdtemp(prefix="sf_")
    clone_dest = os.path.join(tmpdir, meta["repo"])
    try:
        clone_repo(url, clone_dest)
        root = Path(clone_dest)
        print("📂 Building tree…")
        tree = build_tree(root)
        print("📄 Reading files…")
        inc, skip = collect_files(root, max_file_kb)
        print(f"   ✓ {len(inc)} included, {len(skip)} skipped")
        raw_dump = build_raw_dump(meta, tree, inc, skip)
        print(f"📦 Raw dump: {len(raw_dump):,} chars (~{estimate_tokens(raw_dump):,} tok)")

        if llm is None:
            out = Path(output_dir) / f"raw_dump_{meta['repo']}.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(raw_dump)
            print(f"✅ Raw dump saved: {out}"); return str(out)

        trimmed = trim_budget(raw_dump, token_budget)
        dc = get_domain_context(domain)
        sp = GITHUB_SYSTEM_PROMPT.format(domain_context=dc)

        tot = estimate_tokens(trimmed) + estimate_tokens(sp) + 500
        if tot > token_budget * 1.5:
            print("📋 Two-pass (large repo)")
            skill_content = _gh_two_pass(llm, sp, trimmed, token_budget)
        else:
            print(f"📡 Single-pass via {llm.provider}…")
            skill_content = llm.generate(
                sp,
                f"Complete source dump. Produce SKILL.md. Target 400+ lines.\n\n"
                f"---BEGIN---\n\n{trimmed}\n\n---END---",
                max_tokens=16000,
            )

        skill_content = clean_llm_output(skill_content)

        # ── AGENTIC LOOP ──
        if not skip_val:
            skill_content, validation = agentic_quality_loop(
                skill_content, trimmed[:35000], llm, max_retries=2
            )
        else:
            validation = None

        name = extract_skill_name(skill_content) or meta["repo"]
        return _save_skill(skill_content, name, output_dir, validation)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def _gh_two_pass(llm, sp, dump, budget):
    header, secs = split_sections(dump)
    secs.sort(key=lambda p: p_score(p[0]))
    half = (budget - estimate_tokens(header)) // 2
    p1, p2 = [], []
    left = half
    for path, content in secs:
        t = estimate_tokens(content)
        if left >= t: p1.append((path,content)); left -= t
        else: p2.append((path,content))
    p1d = header + "\n\n" + "".join(c for _,c in p1)
    print(f"📡 Pass 1: {len(p1)} files…")
    outline = llm.generate(sp,
        f"PART 1 (priority files). Mark gaps [NEEDS_PART2].\n\n---BEGIN---\n\n{p1d}\n\n---END---",
        max_tokens=12000)
    p2b = budget - estimate_tokens(outline) - 5000
    p2p, used = [], 0
    for _,c in p2:
        t = estimate_tokens(c)
        if used+t <= p2b: p2p.append(c); used += t
    p2d = "".join(p2p)
    print(f"📡 Pass 2: {len(p2p)} files…")
    return llm.generate_multi_turn(sp, [
        {"role":"user","content":f"PART 1:\n\n---BEGIN---\n\n{p1d}\n\n---END---"},
        {"role":"assistant","content":outline},
        {"role":"user","content":f"PART 2. COMPLETE FINAL SKILL.md.\n\n---BEGIN---\n\n{p2d}\n\n---END---"},
    ], max_tokens=16000)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PDF / ARXIV PIPELINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def download_arxiv(aid, od="/tmp"):
    aid = aid.strip()
    for pfx in ["https://arxiv.org/abs/","https://arxiv.org/pdf/",
                 "http://arxiv.org/abs/","http://arxiv.org/pdf/",
                 "arxiv.org/abs/","arxiv.org/pdf/"]:
        if aid.startswith(pfx): aid = aid[len(pfx):]; break
    aid = aid.replace(".pdf","").split("v")[0]
    url = f"https://arxiv.org/pdf/{aid}.pdf"
    out = os.path.join(od, f"{aid.replace('/','_')}.pdf")
    with Spinner(f"Downloading {url}"):
        req = urllib.request.Request(url, headers={"User-Agent":"SkillForge/4.0"})
        with urllib.request.urlopen(req) as r, open(out,"wb") as f: f.write(r.read())
    print(f"   📥 Saved to {out}")
    return out

def extract_pdf(path, max_p=40):
    fitz = _require_fitz()
    doc = fitz.open(path)
    pages = min(len(doc), max_p)
    parts = [f"--- PAGE {i+1}/{pages} ---\n{doc[i].get_text('text')}" for i in range(pages)]
    doc.close()
    combined = "\n".join(parts)
    print(f"📄 Extracted {len(combined):,} chars from {pages} pages")
    return combined

def chunk_text(text, max_c=50000):
    if len(text) <= max_c: return [text]
    chunks, cur = [], ""
    for page in text.split("--- PAGE "):
        if not page.strip(): continue
        block = f"--- PAGE {page}"
        if len(cur) + len(block) > max_c:
            if cur: chunks.append(cur)
            cur = block
        else: cur += block
    if cur: chunks.append(cur)
    return chunks


def pdf_pipeline(pdf_path, llm, output_dir, domain=None, max_pages=40, skip_val=False):
    paper_text = extract_pdf(pdf_path, max_pages)

    if llm is None:
        out = Path(output_dir) / f"raw_{Path(pdf_path).stem}.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(paper_text)
        print(f"✅ Raw text saved: {out}"); return str(out)

    dc = get_domain_context(domain)
    merge_prompt = PAPER_SYSTEM_PROMPT.format(domain_context=dc)
    chunks = chunk_text(paper_text)
    t0 = time.time()

    if len(chunks) == 1:
        print(f"📡 Single-pass via {llm.provider}…")
        skill_content = llm.generate(
            merge_prompt,
            f"Complete paper:\n\n{chunks[0]}\n\nProduce SKILL.md. Target 400+ lines. EXHAUSTIVE.",
            max_tokens=16000)
    else:
        print(f"📡 Phase 1: Deep extraction ({len(chunks)} chunks) via {llm.provider}…")
        partials = []
        for i, chunk in enumerate(chunks):
            print(f"   Chunk {i+1}/{len(chunks)} ({len(chunk):,} chars)…")
            p = llm.generate(
                CHUNK_EXTRACTION_PROMPT,
                f"{chunk}\n\nChunk {i+1}/{len(chunks)}. Extract EVERYTHING.",
                max_tokens=16000)
            partials.append(p)
            time.sleep(2)

        print("   Phase 2: Merging…")
        sep = "\n\n" + "═"*40 + " NEXT SECTION " + "═"*40 + "\n\n"
        merged = sep.join(partials)
        skill_content = llm.generate(
            merge_prompt,
            f"{len(chunks)} extractions from same paper. Merge into SKILL.md.\n"
            f"MUST be 400+ lines. Include EVERY equation, table, result.\n\n{merged}",
            max_tokens=16000)

        # Phase 3: Enrichment if thin
        skill_content = clean_llm_output(skill_content)
        lines = skill_content.count('\n')
        print(f"   Phase 2: {lines} lines")
        if lines < 380:
            print("   Phase 3: Enrichment…")
            adds = llm.generate(
                ENRICHMENT_PROMPT,
                f"SKILL FILE ({lines} lines):\n\n{skill_content}\n\n{'═'*40}\n\n"
                f"ORIGINAL:\n\n{paper_text[:40000]}",
                max_tokens=8000)
            adds = clean_llm_output(adds)
            if adds.strip() and "NO_GAPS_FOUND" not in adds:
                skill_content = skill_content.rstrip() + "\n\n---\n\n## Additional Details\n\n" + adds
                print(f"   Enrichment: {skill_content.count(chr(10))} lines")

    elapsed = time.time() - t0
    skill_content = clean_llm_output(skill_content)

    # ── AGENTIC LOOP ──
    if not skip_val:
        skill_content, validation = agentic_quality_loop(
            skill_content, paper_text[:35000], llm, max_retries=2
        )
    else:
        validation = None

    name = extract_skill_name(skill_content) or Path(pdf_path).stem
    result = _save_skill(skill_content, name, output_dir, validation)
    print(f"   ⏱ Total: {time.time()-t0:.1f}s")
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SAVE + HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def extract_skill_name(c):
    m = re.search(r'^name:\s*(.+)$', c, re.MULTILINE)
    return m.group(1).strip() if m else None

def _save_skill(content, name, output_dir, validation):
    """Dedup: skip if exists and new is smaller, else backup + overwrite."""
    skill_dir = Path(output_dir) / name
    out = skill_dir / "SKILL.md"
    if out.exists():
        old_sz = out.stat().st_size
        new_sz = len(content)
        if new_sz <= old_sz:
            print(f"\n⏭️  Skipped: {out} exists ({old_sz:,}B ≥ {new_sz:,}B)")
            return str(out)
        ts = datetime.datetime.now().strftime("%H%M%S")
        backup = skill_dir / f"SKILL_prev_{ts}.md"
        shutil.copy2(out, backup)
        print(f"   📦 Backed up → {backup.name}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    lines = content.count("\n")
    print(f"\n✅ Saved: {out}")
    print(f"   {lines} lines, {len(content):,} chars")
    if validation:
        s = validation.get("score", "?")
        print(f"   Quality: {s}/10")
        for g in validation.get("gaps", [])[:5]:
            print(f"   ⚠ {g}")
        if validation.get("is_production_ready"):
            print("   ✓ Production ready")
    return str(out)

def detect_type(src):
    s = src.strip()
    if "github.com" in s: return "github"
    if re.match(r'^\d{4}\.\d{4,5}$', s): return "arxiv"
    if "arxiv.org" in s: return "arxiv"
    if os.path.isfile(s): return "pdf"
    if re.match(r'^[a-z-]+/\d+$', s): return "arxiv"
    return "unknown"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def process_one(src, llm, args):
    t = detect_type(src)
    if t == "github":
        return github_pipeline(src, llm, args.output, args.max_file_kb,
                               args.token_budget, args.domain, args.skip_validation)
    elif t == "arxiv":
        pdf = download_arxiv(src)
        return pdf_pipeline(pdf, llm, args.output, args.domain,
                            args.max_pages, args.skip_validation)
    elif t == "pdf":
        return pdf_pipeline(src, llm, args.output, args.domain,
                            args.max_pages, args.skip_validation)
    else:
        print(f"❌ Unknown source: {src}"); return None

def main():
    p = argparse.ArgumentParser(
        description="SkillForge v3 — Agentic research → skill file pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  %(prog)s --github https://github.com/owner/repo
  %(prog)s --arxiv 2103.13630
  %(prog)s --pdf paper.pdf
  %(prog)s --arxiv 2103.13630 --provider gemini --model gemini-2.5-flash
  %(prog)s --arxiv 2103.13630 --provider openrouter
  %(prog)s batch --list sources.txt
""")
    sub = p.add_subparsers(dest="command")
    bp = sub.add_parser("batch", help="Batch process from file")
    bp.add_argument("--list", required=True)
    bp.add_argument("--delay", type=int, default=5)

    for pr in [p, bp]:
        pr.add_argument("--output","-o", default="./skills")
        pr.add_argument("--provider", choices=["anthropic","gemini","openrouter"], default="anthropic")
        pr.add_argument("--model", default=None)
        pr.add_argument("--api-key", default=None)
        pr.add_argument("--domain", default=None)
        pr.add_argument("--max-file-kb", type=int, default=200)
        pr.add_argument("--max-pages", type=int, default=40)
        pr.add_argument("--token-budget", type=int, default=150000)
        pr.add_argument("--skip-validation", action="store_true")
        pr.add_argument("--no-llm", action="store_true")

    p.add_argument("--github", help="GitHub URL")
    p.add_argument("--arxiv", help="arXiv ID or URL")
    p.add_argument("--pdf", help="Local PDF path")

    args = p.parse_args()
    llm = None if args.no_llm else LLMProvider(args.provider, args.model, args.api_key)

    if args.command == "batch":
        with open(args.list) as f:
            srcs = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        print(f"\n{'═'*60}\n  SkillForge v3 Batch — {len(srcs)} sources\n{'═'*60}")
        results = []
        for i, src in enumerate(srcs):
            print(f"\n{'━'*50}\n  [{i+1}/{len(srcs)}] {src}\n{'━'*50}")
            try:
                out = process_one(src, llm, args)
                results.append({"source":src,"output":out,"status":"ok"})
            except Exception as e:
                print(f"❌ {e}")
                results.append({"source":src,"status":"fail","error":str(e)})
            if i < len(srcs)-1 and llm: time.sleep(args.delay)
        ok = sum(1 for r in results if r["status"]=="ok")
        print(f"\n{'═'*60}\n  DONE: {ok}/{len(results)}\n{'═'*60}")
        for r in results:
            print(f"  {'✓' if r['status']=='ok' else '✗'} {r['source']}")

    elif args.github:
        github_pipeline(args.github, llm, args.output, args.max_file_kb,
                        args.token_budget, args.domain, args.skip_validation)
    elif args.arxiv:
        pdf = download_arxiv(args.arxiv)
        pdf_pipeline(pdf, llm, args.output, args.domain, args.max_pages, args.skip_validation)
    elif args.pdf:
        if not os.path.exists(args.pdf): print(f"❌ Not found: {args.pdf}"); sys.exit(1)
        pdf_pipeline(args.pdf, llm, args.output, args.domain, args.max_pages, args.skip_validation)
    else:
        p.print_help(); sys.exit(1)

if __name__ == "__main__":
    main()
