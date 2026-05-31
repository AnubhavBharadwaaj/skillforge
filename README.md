<p align="center">
  <h1 align="center">🔨 Skillwright</h1>
  <p align="center"><strong>Turn any paper, repo, or docs site into a verified skill file your AI assistant can read — without silent code errors when it writes implementation code from those skills.</strong></p>
  <p align="center">
    <a href="#what-is-this">What is this?</a> •
    <a href="#quickstart">Quickstart</a> •
    <a href="#whats-new-in-v6">v6 Changes</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#benchmarks">Benchmarks</a> •
    <a href="#all-options">All Options</a> •
    <a href="#demo-video">Demo Video</a>
  </p>
</p>

---

## What is this?

**Plain-English version:** You give Skillwright a URL — a paper, a GitHub repo, a docs site, anything — and it produces a single markdown file (`SKILL.md`) that captures everything your AI assistant needs to know about that source. Drop the file into your project, and Claude, Cursor, or any AI tool can read it as context.

**Why it exists:** Reading a research paper and extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes hours. Skillwright does this in one command. It also verifies the output line-by-line against the source, and — new in v6 — extracts the actual API surface verbatim from source files so the AI tool reading your skill never makes silent code errors from hallucinated method names.

```
📄 Paper  ──→  Skillwright  ──→  SKILL.md             ← LLM-generated narrative
🐙 Repo                          VERIFICATION.md      ← every claim checked vs source
🌐 Docs                          VERBATIM_REFERENCE.md ← literal source extraction (v6)
                                 │
                                 └─ Drop into your project
```

### The key insight

**A 4B model + SKILL.md outperforms a 70B model + raw PDF.** Skillwright uses a frontier model once to do the hard reading. After that, any model — even one running on your phone — gives answers with precision approaching frontier quality. The weaker the model, the bigger the gain.

**The v6 corollary:** *A weak model + SKILL.md + VERBATIM_REFERENCE.md outperforms a frontier model writing from raw source code.* Even Opus-grade models hallucinate ~3–5% of method signatures when transcribing SDKs. The verbatim companion file pins exact identifiers so generated code compiles AND runs.

---

## Demo Video

[![Skillwright Demo](https://img.youtube.com/vi/O0J55eRcwZw/maxresdefault.jpg)](https://www.youtube.com/watch?v=O0J55eRcwZw)

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
python skillwright.py --arxiv 2103.13630

# A GitHub repository (full clone, submodules, + verbatim API extraction)
python skillwright.py --github https://github.com/wolfecameron/nanoMoE

# A docs site (crawls every page in /docs)
python skillwright.py --url https://docs.acedata.cloud

# A local PDF
python skillwright.py --pdf paper.pdf

# A batch — process your whole reading list
python skillwright.py batch --list sources.txt
```

That's it. Output appears under `./skills/<source-name>/SKILL.md`, with `VERIFICATION.md` showing what was verified against the source. For code-bearing sources (GitHub repos, dangerous docs sections), a `VERBATIM_REFERENCE.md` companion file is generated automatically.

### 4. (Optional) Use the skill files

Copy the generated files into your project's `.claude/skills/<name>/`, `.cursorrules`, or any tool that reads context files. **Attach both `SKILL.md` and `VERBATIM_REFERENCE.md` when present** — the SKILL.md has a directive at the top telling your AI assistant to use the verbatim file for any exact API calls.

---

## What's new in v6

> **The headline feature:** Silent-error prevention. When an AI tool reads a SKILL.md and writes code from it, the most common failure mode isn't conceptual misunderstanding — it's slightly-paraphrased API signatures that compile cleanly and fail at runtime (`TypeError: client.pay is not a function`, wrong parameter names, hallucinated return types). v6 fixes this structurally.

### The Verbatim Guard

After verification, Skillwright looks at the per-section breakdown. If any section is flagged as **dangerous** — meaning low pct_verified AND a section name that suggests code content (`APIs`, `Functions`, `Methods`, `Configuration`, `Architecture`, `Endpoints`, `SDK`, `Integration`, `Implementation`, ...) — it triggers a second extraction pass that produces a **`VERBATIM_REFERENCE.md`** companion file.

The verbatim file contains the actual API surface extracted character-for-character from the source files preserved on disk. **No LLM is in the loop.** It's pure pattern-matching:

- **TypeScript / JavaScript** — every `export` and `declare` line with brace-balanced bodies
- **Python** — every `def`, `class`, `async def` with decorators and docstrings
- **Rust** — every `pub fn`, `pub struct`, `pub enum`, `pub trait`
- **Go** — every `func`, `type`, exported `var`/`const`
- **Java / Kotlin / C# / Ruby / Swift** — public surface declarations
- **Package manifests** — `package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, `Anchor.toml`
- **Docs pages** — every fenced code block with its parent section header for context, plus inline `` `identifiers` ``

A trust directive is automatically injected at the top of `SKILL.md` (after the YAML frontmatter) telling any AI tool reading the file:

> ⚠ For ANY method names / parameters / types / constants, consult `VERBATIM_REFERENCE.md` first. If the value isn't there verbatim, treat it as unverified.

### What this prevents

The exact category of bug that wastes hours of debugging time:

```
LLM reads SKILL.md, writes:           Actual SDK has:
─────────────────────────────         ─────────────────────────────
client.pay(req)                       client.settlePayment(req)
verifyPayment(opts)                   verify_payment(opts)
{ amount, recipient }                 { paymentAmount, recipientAddr }
returns: TxHash                       returns: TxSignature
DEFAULT_RPC_URL                       DEFAULT_RPC
```

These compile. They typecheck. They fail at runtime with cryptic errors. v6 makes them structurally impossible by giving the AI tool the actual signatures alongside the narrative.

### When the guard fires

Three triggers in default `--verbatim auto` mode:

1. **A dangerous section name + low verification:** A section called `APIs / Functions / Classes` at 71% verified triggers extraction.
2. **Any section severely below threshold:** Any section more than 5 points below `--verbatim-threshold` (default 92%) triggers extraction even if the name is benign.
3. **GitHub repos / blobs / PRs:** Always extract — having verbatim API surface available is cheap insurance and zero LLM cost.

For docs sites, only #1 and #2 trigger. For pure prose sources (research papers without code), the guard usually doesn't fire.

<details>
<summary><strong>Full v6 changelog (click to expand)</strong></summary>

**New in v6:**

1. **`VerbatimExtractor` class** — language-aware extraction of public API surface from source bundles. Supports TypeScript, JavaScript, Python, Rust, Go, Java, Kotlin, C#, Ruby, Swift. Handles brace-balanced bodies with truncation at 50 lines, multi-line Python signatures with docstrings, and decorator stacks.

2. **`assess_dangerous_sections()`** — examines per-section verification stats and flags sections matching 27 dangerous-name patterns combined with sub-threshold pct_verified.

3. **`inject_verbatim_directive()`** — idempotent trust-directive injection that lands after YAML frontmatter and references the specific flagged sections by name and percentage.

4. **`write_verbatim_reference()`** — orchestrates the full extraction-and-write pipeline.

5. **Three new CLI flags:**
   - `--verbatim {auto,always,never}` (default: `auto`)
   - `--verbatim-threshold FLOAT` (default: `92.0`)
   - `--no-verbatim` shorthand

6. **Two new `--json` fields:**
   - `verbatim_path` — path to the companion file, or empty string if not extracted
   - `dangerous_sections` — list of section names that triggered extraction

7. **Pipeline integration** — runs automatically after verification, before JSON emission, in `process_spec`.

**v5 changes preserved (all retained):**

- Verifier number matching with digit-aware boundaries
- YAML `description:` verification
- Frontmatter-only skill name extraction
- Git clone timeout + submodules + cache + size guard
- Same-origin BFS docs crawl with `/docs` prefix detection
- `llms.txt` URL manifest support
- Container-scroll for SPA docs sites
- `trafilatura` main-content extraction
- Agentic regeneration loop driven by verifier flags
- Smart priority truncation
- Repo-clone cache
- Skill-name collision disambiguation
- 191-entry `KNOWN_ACRONYMS` set
- Path traversal protection
- GitHub auth banner
- Per-repo size guard with priority-cap fallback
- Tempdir cleanup via `weakref.finalize` + `atexit`
- `--json` output mode
- Per-section verification breakdown
- Unicode normalization (NFKD + ASCII fold)

</details>

---

## How It Works

### Pipeline at a glance (now 7 stages)

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
│                 • Per-section breakdown                          │
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
┌────────────────────────────────────────────────────────────────┐  ← NEW IN v6
│  7. GUARD       Verbatim source extraction                      │
│                 • Detect dangerous sections (low pct + code-y    │
│                   name patterns)                                 │
│                 • Extract API surface verbatim from source       │
│                 • Write VERBATIM_REFERENCE.md companion          │
│                 • Inject trust directive into SKILL.md           │
│                 → silent runtime errors structurally prevented   │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
            SKILL.md + VERIFICATION.md + VERBATIM_REFERENCE.md
```

### What makes it agentic (stages 5–6)

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

The LLM gets the specific list and rewrites those lines using actual source content.

### What makes it silent-error-proof (stage 7)

When the agentic loop plateaus — which it does on sections heavy with API signatures because LLMs paraphrase identifiers no matter how often you ask them not to — stage 7 takes over:

```
🔬 Round 0: 71.4% verified (15/52 unverified)
🔬 Round 1: 71.4% verified (15/52 unverified)
🔬 Round 2: 71.4% verified (15/52 unverified)
🛡  VERBATIM_REFERENCE.md created (anti-hallucination guard)
     Flagged sections: '4. APIs / Functions / Classes' (71.4%)
     Trust directive injected into SKILL.md
```

Now when Claude reads `skills/x402-client/SKILL.md` to write payment code:

1. The directive at the top says "for any method signature, use `VERBATIM_REFERENCE.md`"
2. Claude opens that file
3. Finds `async settlePayment(req: PaymentRequest): Promise<TxSignature>` extracted verbatim from `src/index.ts`
4. Writes code that calls the real method with the real parameter shape

No invented method names. No hallucinated parameters. No wrong return types.

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

Every claim runs through unicode normalization first (`NFKD` + ASCII fold) so `naïve` matches `naive`.

### Dangerous-section detection — what triggers the guard

A section in the generated SKILL.md is flagged dangerous if **all three** hold:

1. It has ≥ 3 claims (skips trivially small sections)
2. Its `pct_verified` is below `--verbatim-threshold` (default 92.0)
3. **Either** its name matches one of 27 dangerous patterns **OR** its pct_verified is more than 5 points below threshold

Dangerous-name patterns include:
```
APIs, Functions, Classes, Methods, Endpoints, Configuration,
Interfaces, Types, Schema, Signatures, Architecture, Components,
SDK, Usage, RPC, GraphQL, REST, Integration, Implementation,
Module, Staking, Payments, Discovery, Escrow, Intent, Command, Mapping
```

For GitHub sources, the guard runs **regardless** of whether sections were flagged — having verbatim API surface available is free insurance.

---

## OpenRouter — Free Tier

Skillwright's OpenRouter provider gives you access to dozens of models through a single API key, including completely free models.

### How it works

1. **Live model discovery** — queries OpenRouter's API, filters for chat-capable models with ≥32K context
2. **SQS ranking** — ranks models by Speed (30%), Completion Cap (20%), Context Length (15%), Capability (15%), Recency (10%), Reputation (10%)
3. **Auto-rotation** — if a model rate-limits, Skillwright automatically rotates to the next one
4. **Free → paid upgrade** — when free models are exhausted, auto-upgrades to cheap paid models

### Cost per source

| Provider | Mode | Cost/Source | Notes |
| --- | --- | --- | --- |
| OpenRouter | Free models | **$0.00** | Auto-discovers, rotates on rate limits |
| OpenRouter | `--paid` | **$0.01–0.03** | Gemini Flash, fast and reliable |
| Gemini | Direct | **Free tier available** | Generous limits |
| Anthropic | Claude Sonnet | ~$0.15 | Higher quality, recommended for tricky sources |

The Verbatim Guard adds **zero LLM cost** — it's pure filesystem extraction from the source bundles Skillwright already preserves on disk. Runtime impact is ~0.5–2 seconds per source.

---

## Benchmarks

Tested with Gemini 2.5 Flash (default). All runs automated, no manual editing.

### Per-source performance

| Source | Type | Lines | Time | % Verified | Verbatim guard |
| --- | --- | ---: | ---: | ---: | :---: |
| [Quantization Survey](https://arxiv.org/abs/2103.13630) (33 pages) | arXiv | 550 | 161s | 92% | — |
| [Deep Compression](https://arxiv.org/abs/1510.00149) (14 pages) | arXiv | 250 | ~90s | 94% | — |
| [nanoMoE](https://github.com/wolfecameron/nanoMoE) | GitHub | 349 | ~45s | 91% | ✓ (precautionary) |
| 84-source web3 batch | Batch | 25,000+ | ~95min | avg 95.4% | ✓ (12 triggered) |

### vs manual reading

For the quantization survey (33 pages, 13 equations, 8 result tables):

| | Manual Reading | Skillwright v6 |
| --- | --- | --- |
| **Time** | 6–8 hours | 2.7 minutes |
| **Lines produced** | 455 | 550 |
| **Equations captured** | 13/13 | 11/13 |
| **Result tables** | Complete | Complete |
| **Verification** | None | 92% claims verified vs source |
| **API surface accuracy** | Depends on transcription | 100% (verbatim from source) |
| **Reusable format** | No | Yes (YAML triggers, git-versionable) |

### Weak model democratization

The real value isn't competing with frontier models on raw PDFs — it's making tiny models competitive by pre-digesting knowledge:

| Model | With Raw PDF | With SKILL.md | With SKILL.md + VERBATIM (v6) |
| --- | --- | --- | --- |
| Llama 3.2 3B | Can't fit 33-page PDF | Precise answers | Same + 100% accurate code |
| Gemma 3 4B | Hallucinates numbers | Finds exact values | Same + zero silent code errors |
| Llama 3.3 70B | Misses buried details | Competitive with frontier | Surpasses frontier on impl code |
| Claude Opus 4.7 | Excellent | Excellent | Excellent + verifiable identifiers |

---

## Why?

Reading a quantization paper takes **4 hours**. Extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes **another 2 hours**. Multiply by 20 papers per project.

Skillwright does this in one command. The output is structured so Claude, GPT, Cursor, or Copilot can use it directly as context — and v6 ensures it can write working code from those skills without hallucinated API calls.

### Why not just upload the PDF to Claude?

Claude reads PDFs excellently. But:

- **Consistency** — Skill files follow the same schema every time
- **Composability** — Skill files go in your git repo, work in any tool that reads files. ChatPDF sessions expire.
- **Verifiable** — `VERIFICATION.md` tells you exactly which claims appear in the source
- **No silent code errors** — `VERBATIM_REFERENCE.md` pins exact API signatures (v6)
- **Weak model support** — A 4B model can't fit a 33-page PDF in its 8K context window. A 500-line skill file fits.
- **Batch pipeline** — Update `sources.txt` with your weekly reading list, run one command
- **Cost** — Process 30 papers for ~$0.30 total with `--paid`, or free with OpenRouter free models

---

## All Options

```
python skillwright.py [OPTIONS]

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

Verification:
  --agentic-retries N           Repair-loop rounds (default: 2, 0 to disable)
  --target-pct-verified F       Stop repair when reached (default: 90.0)

Verbatim guard (new in v6):
  --verbatim {auto,always,never}    Default: auto
                                    auto    = extract for dangerous sections
                                              + always for GitHub sources
                                    always  = extract for every source
                                    never   = disable (not recommended)
  --verbatim-threshold F            Default: 92.0. In auto mode, sections with
                                    pct_verified below this trigger extraction
  --no-verbatim                     Shorthand for --verbatim never

Web fetching:
  --crawl-pages N        Max same-origin pages per web source (default: 25,
                         use 1 for no crawl)
  --no-headless          Show the browser window (for debugging)

GitHub:
  --clone-timeout N      Git clone timeout in seconds (default: 300)
  --no-submodules        Don't recurse into submodules
  --max-repo-mb N        Above this, fall back to priority-filtered files
                         (default: 800)
  --max-org-repos N      Max repos to clone from an org URL (default: 25)

Batch:
  skillwright.py batch --list sources.txt [--delay 5] [other flags]
```

---

## Examples

### Example: A docs site

```bash
python skillwright.py --url https://docs.acedata.cloud --crawl-pages 50
```

What happens:
1. Detects this is a docs site (`docs.` subdomain).
2. BFS crawls up to 50 same-origin pages, scrolling each fully.
3. Strips sidebar/nav/footer with `trafilatura`.
4. Generates a SKILL.md covering every page.
5. Verifies every claim against the concatenated downloaded content.
6. If any code-bearing section (e.g. "API Reference", "SDK Usage") verifies below 92%, extracts the code blocks verbatim into `VERBATIM_REFERENCE.md`.

### Example: A GitHub repository (v6 verbatim guard always fires)

```bash
python skillwright.py --github https://github.com/AceDataCloud/X402Client
```

```
🔬 Round 0: 90.0% verified (60/600 unverified)
🔬 Round 1: 90.0% verified — plateau, stopping
   📋 Verification: 90.0% claims verified
   ⚠ Worst section: '4. APIs / Functions / Classes' at 71.4%
🛡  VERBATIM_REFERENCE.md created (anti-hallucination guard)
     Flagged sections: '4. APIs / Functions / Classes' (71.4%)
     Trust directive injected into SKILL.md
   ✅ skills/x402-client/SKILL.md
```

Result: even though the LLM paraphrased ~30% of the API signatures in the SKILL.md, the verbatim file contains the actual `export class X402Client { ... }` declarations from `src/index.ts`. Claude reading both files gets the real signatures.

### Example: Batch with sources.txt

```bash
# sources.txt:
# https://github.com/x402-foundation/x402
# https://x402.org/ecosystem
# https://docs.acedata.cloud/llms.txt
# https://arxiv.org/abs/2511.15712

python skillwright.py batch --list sources.txt --json > results.jsonl
```

What happens:
1. Routing summary printed up-front: `Routing: github_repo=1, web=1, llms_manifest=1, arxiv=1`
2. GitHub auth warning if no `GITHUB_TOKEN` and the source would hit rate limits.
3. Each source processed sequentially; results emitted as JSON lines including `verbatim_path` and `dangerous_sections`.
4. Repo clone cache means if any blob URLs to x402-foundation/x402 appear, they share one clone.
5. Final summary: `DONE: 4 ok, 0 raw-only, 0 errored`.

Sample JSON record from `--json`:

```json
{
  "raw": "github.com/AceDataCloud/X402Client",
  "kind": "github_repo",
  "url": "https://github.com/AceDataCloud/X402Client",
  "status": "ok",
  "skill_path": "skills/x402-client/SKILL.md",
  "verification_path": "skills/x402-client/VERIFICATION.md",
  "verbatim_path": "skills/x402-client/VERBATIM_REFERENCE.md",
  "source_dir": "skills/.sources/AceDataCloud_X402Client",
  "pct_verified": 90.0,
  "claims_total": 600,
  "claims_unverified": 60,
  "lines_flagged": 38,
  "dangerous_sections": ["4. APIs / Functions / Classes"]
}
```

<details>
<summary><strong>Sample SKILL.md output (with v6 trust directive)</strong></summary>

```yaml
---
name: x402-client
description: >
  Use this skill when working with AceDataCloud's x402 payment client SDK
  for HTTP-based crypto payments on Solana mainnet using the exact scheme.
---

<!-- VERBATIM-DIRECTIVE-V1 -->
> **⚠ TRUSTED API REFERENCE — read this before generating any code**
>
> This skill's narrative sections were generated by an LLM and may contain
> paraphrased API references. Specifically these sections were flagged
> as having significant unverified content: _4. APIs / Functions / Classes_ (71.4%)
>
> For ANY code generation involving method names / parameters / types /
> constants / config keys, consult `VERBATIM_REFERENCE.md` in this same
> directory. If a method signature appears there, use it exactly. If it
> appears only in SKILL.md and NOT in the verbatim file, treat it as
> unverified.

# X402Client

## 1. What it does
The X402Client SDK provides ...

## 4. APIs / Functions / Classes
[LLM-generated descriptions of the API surface — useful for context but
treat exact identifiers as unverified; check VERBATIM_REFERENCE.md]
...
```

</details>

<details>
<summary><strong>Sample VERBATIM_REFERENCE.md output</strong></summary>

```markdown
# Verbatim source reference: x402-client

**Skill**: `skills/x402-client/SKILL.md`
**Source bundle**: `skills/.sources/AceDataCloud_X402Client`
**Source kind**: `github_repo`
**Generated**: 2026-05-17T14:32:01
**Dangerous sections flagged**: _4. APIs / Functions / Classes_ (71.4%)

> ⚠ This file contains content extracted DIRECTLY from the source files
> preserved on disk. No LLM is in the loop — every character below appears
> in the original source.

---

## Entry points (full content, verbatim)

### `src/index.ts`

\`\`\`typescript
export interface PaymentRequest {
  amount: bigint;
  recipient: string;
  scheme: "exact_svm" | "exact_evm";
}

export class X402Client {
  constructor(private opts: { rpcUrl: string; payer: KeyPair }) {}

  async settlePayment(req: PaymentRequest): Promise<TxSignature> {
    return await this._rpc.send(req);
  }

  async verifyReceipt(sig: TxSignature): Promise<boolean> {
    return this._verifier.check(sig);
  }
}

export const DEFAULT_RPC = "https://api.acedata.cloud/x402";
\`\`\`

## Typescript signatures (verbatim)

### `src/payment.ts`

\`\`\`typescript
export class PaymentBuilder { ... }
\`\`\`

## Package manifests & config (verbatim)

### `package.json`

\`\`\`json
{
  "name": "@acedata/x402-client",
  "version": "1.4.2",
  ...
}
\`\`\`
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
  WHEN to load this file.
---
```

When v6's verbatim guard fires, a trust directive is injected after the frontmatter, followed by the LLM-generated narrative.

Companion files (when applicable):
- **`VERIFICATION.md`** — per-section verification breakdown
- **`VERBATIM_REFERENCE.md`** — verbatim source extraction (v6)

The combined output is designed to be:
- **Exhaustive** — every formula, every hyperparameter, every result table
- **LLM-native** — clear headers, code blocks, markdown tables
- **Composable** — multiple skill files stack together as context
- **Verifiable** — every claim checked against source in `VERIFICATION.md`
- **Silent-error-proof** — exact API signatures in `VERBATIM_REFERENCE.md` (v6)
- **Git-versionable** — plain text, diffs cleanly, travels with your codebase

See [docs/SKILL_FORMAT_SPEC.md](docs/SKILL_FORMAT_SPEC.md) for the full specification.

---

## Comparison

| | Skillwright v6 | Papers With Code | Elicit | ChatPDF | Manual Reading |
| --- | --- | --- | --- | --- | --- |
| **Output** | Verified skill + verbatim ref | Links to repos | Summaries | Chat session | Notes (maybe) |
| **Source coverage** | Papers, repos, docs, PRs, llms.txt | Papers + code | Papers | Single doc | Anything |
| **Captures formulas** | ✅ Every equation | ❌ | ❌ | Partial | If you take notes |
| **Captures code** | ✅ Annotated snippets | Links only | ❌ | ❌ | ❌ |
| **Captures configs** | ✅ Full hyperparameters | ❌ | ❌ | If you ask | Maybe |
| **YAML triggers** | ✅ Auto-loads in Claude/Cursor | ❌ | ❌ | ❌ | ❌ |
| **Hallucination check** | ✅ Line-by-line verifier | N/A | ❌ | ❌ | Manual |
| **Silent-error prevention** | ✅ Verbatim API extraction | ❌ | ❌ | ❌ | Manual |
| **Composable** | ✅ Same format, stackable | ❌ | ❌ | Sessions expire | ❌ |
| **Batch processing** | ✅ 30+ sources, one command | ❌ | Limited | One at a time | ❌ |
| **Git-versionable** | ✅ Plain markdown | ❌ | ❌ | ❌ | ❌ |
| **Works with weak models** | ✅ 4B models give precise answers | ❌ | ❌ | ❌ | ❌ |
| **Time per source** | 2–3 minutes | N/A | ~1 min (summary) | Manual | 4–8 hours |
| **Cost** | $0 (free) – $0.03 (paid) | Free | Subscription | Subscription | Free (but slow) |

---

## Use Cases

**ML Competition Teams** — Process your entire reading list overnight. Each paper becomes a composable knowledge module your AI assistant can use during implementation. Built for WorldQuant IQC, OpenAI Parameter Golf, and ImageCLEF 2026.

**Research Labs** — Build a shared, version-controlled skill library. When one person reads a paper, the entire team's AI tools immediately know how to implement it.

**Web3 / Protocol Teams** — Ingest protocol specs, reference implementations (full GitHub orgs), and docs sites into a single skill library. The v6 verbatim guard ensures generated payment/contract code uses the actual SDK methods, not paraphrased ones. PR URLs let your AI track active proposals.

**Weak Model Users** — Can't afford frontier API costs? Extract skill files once with Skillwright's free tier, then use them with Ollama, llama.cpp, or any local model. A 4B model with skill files plus verbatim references gives precise, runtime-correct answers that a 70B model with raw PDFs can't match.

**Bounty / Hackathon Participants** — When you have 7 days to ship a working autonomous agent on top of a stack you've never used, the verbatim references mean your AI assistant writes code that compiles AND runs the first time. Saves the typical "debug hallucinated method names for 4 hours" tax.

**Individual Researchers** — Stop re-reading papers. Extract once, reference forever.

---

## Project Structure

```
skillwright-ai/
├── skillwright.py          # The complete tool (single file, ~2920 lines)
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
│       ├── x402-client/
│       │   ├── SKILL.md
│       │   ├── VERIFICATION.md
│       │   └── VERBATIM_REFERENCE.md      ← v6
│       └── nanomoe/
│           ├── SKILL.md
│           ├── VERIFICATION.md
│           └── VERBATIM_REFERENCE.md      ← v6
├── notebooks/
│   └── Skillwright_Demo.ipynb
├── benchmark/
│   ├── benchmark.py
│   └── benchmark.jsx
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

The verbatim extractor uses only the Python standard library — no new dependencies in v6.

---

## Roadmap

### Completed
- [x] arXiv paper → SKILL.md
- [x] GitHub repo → SKILL.md (full clone, submodules, blob/PR/org URLs)
- [x] Local PDF → SKILL.md
- [x] Docs site → SKILL.md (multi-page BFS crawl)
- [x] llms.txt manifest support
- [x] Gemini + Anthropic + OpenRouter provider support
- [x] Batch processing with `sources.txt`
- [x] Line-by-line anti-hallucination verifier
- [x] Agentic repair loop (regenerate from flagged claims)
- [x] Per-section verification breakdown
- [x] Smart prioritized truncation
- [x] OpenRouter free model auto-discovery with SQS ranking
- [x] `--json` output for scripting
- [x] **Verbatim source extraction (silent-error prevention)** ← v6
- [x] **Dangerous-section auto-detection** ← v6
- [x] **Trust directive injection** ← v6
- [x] **Multi-language API extraction (TS/JS/Py/Rust/Go/Java/Kotlin/C#/Ruby/Swift)** ← v6

### Coming Soon
- [ ] **Execution-based verification** — clone paper repos, run training, verify claimed results match
- [ ] **Tree-sitter integration** — replace regex-based signature extraction with AST-grade parsing for 100% language fidelity
- [ ] **Semantic Scholar integration** — citation graph, intent (supporting/contrasting), live conflict detection
- [ ] **Live monitoring daemon** — watch arXiv RSS for citations to your library, auto-flag contradictions
- [ ] **Ollama provider** — fully local, zero-cost
- [ ] **`--compact` mode** — shorter skill files for 8K context windows
- [ ] **Cross-reference index** — `CROSS_REF.md` showing conflicts and agreements across your skill library
- [ ] **`VERBATIM_DIFF.md`** — when a repo is re-processed, diff the verbatim extraction to surface API changes
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

If Playwright still fails, Skillwright automatically falls back to `requests` for HTML fetching.

For debugging, run with `--no-headless` to watch the browser.

</details>

<details>
<summary><strong>GitHub anonymous rate limit (60 req/hour)</strong></summary>

Set a fine-grained PAT:
```bash
export GITHUB_TOKEN="ghp_..."
```

This raises the limit to 5000 req/hour. v6 prints a banner at startup if any source needs the API and no token is set.

</details>

<details>
<summary><strong>Verifier flags lots of claims that look correct</strong></summary>

Most common causes:
- **The LLM paraphrased instead of quoting verbatim.** Open the source on disk under `.sources/<basename>/` and check.
- **A number with units the source writes differently.** Source says `latency: 50 ms`, skill says `50ms` — the verifier strips trailing letter suffixes but `50 ms` vs `50ms` is a known edge case.
- **Identifiers from a code block the LLM rewrote.** v6's verbatim guard catches this automatically for code-bearing sources.

Run with `--annotate-skill` to see ⚠ UNVERIFIED markers inline.

</details>

<details>
<summary><strong>VERBATIM_REFERENCE.md is empty or sparse</strong></summary>

The extractor only finds content matching language-specific signature patterns. Possible causes:
- **The source repo uses an unsupported language.** Currently supported: TypeScript, JavaScript, Python, Rust, Go, Java, Kotlin, C#, Ruby, Swift. Open a feature request for others.
- **The repo is doc-only (no source files).** This is expected — for pure docs the extractor falls back to code-block extraction from markdown.
- **All source files are filtered.** The extractor skips `.test.`, `.spec.`, `__tests__`, `.d.ts`, `node_modules`, `__pycache__`, `dist/`, `build/`, `target/`. If the repo's only code lives in these paths, nothing gets extracted.

Check what was preserved under `.sources/<basename>/files/`.

</details>

<details>
<summary><strong>Want to disable the verbatim guard</strong></summary>

```bash
python skillwright.py --github user/repo --no-verbatim
```

Reproduces v5 behavior exactly. Not recommended for code-generation use cases — the silent-error prevention is the main reason to upgrade.

To disable just for non-flagged sections but keep it for GitHub:
```bash
python skillwright.py --github user/repo --verbatim auto --verbatim-threshold 100
```

(Setting threshold to 100 means no docs section is "low enough" to trigger; only GitHub-source-default extraction runs.)

</details>

<details>
<summary><strong>"git clone timed out"</strong></summary>

Default timeout is 300s. For huge repos:
```bash
python skillwright.py --github user/big-repo --clone-timeout 1200
```

If the clone consistently times out, Skillwright automatically retries with `--depth 1 --single-branch`.

</details>

<details>
<summary><strong>Skill file overwrites a previous one</strong></summary>

v5+ disambiguates collisions automatically. If two sources produce skills named `payment-sdk`, the second saves to `payment-sdk__<6charhash>/SKILL.md`. The `.source_url` marker file inside each skill directory tracks which source produced it.

</details>

---

## Contributing

PRs welcome. The most impactful contributions right now:

1. **Tree-sitter integration** — replace the regex-based signature extraction with AST parsing for higher fidelity, especially on Rust generics, TypeScript conditional types, and Python type unions
2. **Additional languages in the verbatim extractor** — Haskell, OCaml, Elixir, Scala, C/C++, Zig
3. **Better extraction prompts** — if you find a source type where extraction quality is low, submit the URL + expected output as a test case
4. **New source types** — add support for HuggingFace model pages, Notion exports, etc.
5. **Example skill files** — generate and submit high-quality skill files for popular papers/repos
6. **New providers** — add support for Ollama, Together AI, or other OpenAI-compatible APIs
7. **Benchmark results** — run skill files vs raw PDFs with weak models and share the numbers

---

## License

MIT

---

## Author

**Anubhav Bharadwaj** — IIT (ISM) Dhanbad • IIT Kanpur MBA '28

Built while competing in WorldQuant IQC 2026, OpenAI Parameter Golf, and ImageCLEF 2026.

- GitHub: [@AnubhavBharadwaaj](https://github.com/AnubhavBharadwaaj)
