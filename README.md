<p align="center">
  <h1 align="center">🔨 SkillForge</h1>
  <p align="center"><strong>Turn any research paper or GitHub repo into an AI-native skill file in 60 seconds.</strong></p>
  <p align="center">
    <a href="#quickstart">Quickstart</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#benchmarks">Benchmarks</a> •
    <a href="#examples">Examples</a> •
    <a href="https://colab.research.google.com/">Try on Colab</a>
  </p>
</p>

---

SkillForge is an **agentic CLI tool** that extracts every formula, algorithm, hyperparameter, and implementation detail from research papers and codebases, then packages them into structured **skill files** optimized for LLM consumption.

Unlike paper summaries (for humans) or datasets (for training), skill files are a **new artifact type**: runtime knowledge injections that make AI coding assistants dramatically more effective at implementation.

```
📄 33-page PDF  ──→  SkillForge  ──→  450-line SKILL.md with every equation,
                                       every table, every implementation detail
```

```
🐙 GitHub repo  ──→  SkillForge  ──→  400-line SKILL.md with architecture,
                                       annotated code, configs, math from comments
```

## Why?

Reading a quantization paper takes **4 hours**. Extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes **another 2 hours**. Multiply by 20 papers per project.

SkillForge does this in **one command**. The output is structured so Claude, GPT, Cursor, or Copilot can use it directly as context — no more pasting paper excerpts into chat.

I built this while competing in WorldQuant IQC and OpenAI Parameter Golf, where reading papers fast is a genuine competitive advantage.

---

## Quickstart

```bash
pip install anthropic PyMuPDF google-generativeai
```

```bash
# arXiv paper → skill file
python skillforge.py --arxiv 2103.13630 --provider gemini --model gemini-2.5-flash

# GitHub repo → skill file
python skillforge.py --github https://github.com/wolfecameron/nanoMoE --provider gemini

# Local PDF → skill file
python skillforge.py --pdf paper.pdf --provider anthropic

# Batch mode — mixed sources in one file
python skillforge.py batch --list sources.txt --provider gemini --model gemini-2.5-flash

# Zero-cost mode — extract text locally, no API calls
python skillforge.py --arxiv 2103.13630 --no-llm
```

Set your API key:
```bash
export GEMINI_API_KEY="..."       # Free tier available
# or
export ANTHROPIC_API_KEY="..."    # Needs credits at console.anthropic.com
```

---

## How It Works

### Paper Pipeline (arXiv / PDF)

```
┌─────────────┐     ┌──────────────────┐     ┌───────────────┐     ┌───────────┐
│ Download PDF │────▶│ Extract Text     │────▶│ Chunk (50K)   │────▶│ Phase 1   │
│ (arXiv API)  │     │ (PyMuPDF, 40pp)  │     │ 3 chunks avg  │     │ Deep      │
└─────────────┘     └──────────────────┘     └───────────────┘     │ Extraction│
                                                                    │ per chunk │
                                                                    └─────┬─────┘
                                                                          │
                    ┌──────────────────┐     ┌───────────────┐           │
                    │ Phase 3          │◀────│ Phase 2       │◀──────────┘
                    │ Enrichment       │     │ Merge into    │
                    │ (if <380 lines)  │     │ SKILL.md      │
                    └────────┬─────────┘     └───────────────┘
                             │
                    ┌────────▼─────────┐     ┌───────────────┐
                    │ Agentic Loop     │────▶│ Save          │
                    │ Validate → Fix   │     │ SKILL.md      │
                    │ → Re-validate    │     └───────────────┘
                    │ (up to 3 rounds) │
                    └──────────────────┘
```

### GitHub Pipeline

```
┌─────────────┐     ┌──────────────────┐     ┌───────────────┐     ┌───────────┐
│ git clone   │────▶│ Smart Filter     │────▶│ Priority Sort │────▶│ LLM       │
│ --depth 1   │     │ 60+ extensions   │     │ README first  │     │ Analysis  │
└─────────────┘     │ Skip node_modules│     │ configs next  │     │ (1 or 2   │
                    │ .ipynb flatten   │     │ tests last    │     │  passes)  │
                    │ Binary detection │     └───────────────┘     └─────┬─────┘
                    └──────────────────┘                                  │
                                                                          │
                                              ┌───────────────┐          │
                                              │ Agentic Loop  │◀─────────┘
                                              │ Validate → Fix│
                                              │ → Re-validate │
                                              └───────┬───────┘
                                                      │
                                              ┌───────▼───────┐
                                              │ Save SKILL.md │
                                              └───────────────┘
```

### What Makes It Agentic

The quality-gate retry loop: after generating a skill file, SkillForge **validates** the output, **identifies specific gaps** (missing equations, missing benchmark tables), and **fixes them** by sending the gaps + original source back to the LLM. This repeats until quality score ≥ 7/10 or max retries are reached.

```
Extract → Validate (4/10: "missing load balancing loss formula")
       → Fix (send gaps + source → LLM rewrites)
       → Re-validate (7/10: ✓)
       → Save
```

---

## Benchmarks

Tested on Google Colab with Gemini 2.5 Pro. All runs automated, no manual editing.

| Source | Type | Lines | Chars | Quality | Time |
|--------|------|-------|-------|---------|------|
| [Quantization Survey](https://arxiv.org/abs/2103.13630) (Gholami et al.) | arXiv | 395 | 28,229 | — | 139s |
| [Deep Compression](https://arxiv.org/abs/1510.00149) (Han et al.) | arXiv | 250 | 16,004 | — | ~120s |
| [nanoMoE](https://github.com/wolfecameron/nanoMoE) (Wolfe) | GitHub | 349 | 22,346 | 7/10 | ~45s |
| [IndexTTS](https://github.com/indexteam/IndexTTS) | GitHub | 225 | 14,822 | — | ~50s |

### vs Manual Reading

For the quantization survey (33 pages, 12 equations, 8 result tables):

| | Manual Reading | SkillForge v2 |
|---|---|---|
| **Time** | 6-8 hours | 2.3 minutes |
| **Lines** | 455 | 395 |
| **Equations captured** | 12/12 | 9-10/12 |
| **Result tables** | Complete | Partial |
| **Implementation takeaways** | 10 items | 7 items |
| **Quality** | 100% | ~85% |

SkillForge gets **85% of manual quality at 0.3% of the time**.

### Cost Per Paper

| Provider | Model | Cost/Paper | Notes |
|----------|-------|-----------|-------|
| Gemini | 2.5 Flash | **Free** | Generous free tier |
| Gemini | 2.5 Pro | **Free** | Lower free quota |
| Anthropic | Claude Sonnet | ~$0.30 | Higher quality |

---

## Examples

### Example Output: Deep Compression (Han et al., ICLR 2016)

<details>
<summary>Click to expand — 250 lines</summary>

```yaml
---
name: deep-compression
description: >
  Implements the "Deep Compression" paper by Han et al. (2016), a three-stage
  pipeline of pruning, trained quantization, and Huffman coding to compress
  neural networks by 35x-49x without accuracy loss. Use this skill when you
  need to compress a trained neural network for deployment on mobile or
  embedded systems. Triggers: model compression, network pruning, weight
  quantization, weight sharing, Huffman coding, reducing model size.
---

# Deep Compression: Compressing Deep Neural Networks with Pruning, Trained Quantization and Huffman Coding

**Paper**: Song Han, Huizi Mao, William J. Dally
**Published**: ICLR, 2016
**Key result**: 35×-49× compression without accuracy loss

---

## 1. Overview of the Deep Compression Pipeline

Three stages applied sequentially:
1. **Network Pruning**: Remove connections with small weights (9×-13× reduction)
2. **Trained Quantization**: k-means clustering of weights (further to 27×-31×)
3. **Huffman Coding**: Lossless compression of non-uniform distributions (final 35×-49×)

## 2. Stage 1: Network Pruning

Equation: Pruning threshold per layer
threshold_l = λ × std(W_l)
where: W_l = weights of layer l, λ = sensitivity parameter

...
```

</details>

### Example Output: nanoMoE (GitHub repo)

<details>
<summary>Click to expand — key sections</summary>

```yaml
---
name: nanomoe
description: >
  nanoMoE is a from-scratch Mixture of Experts tutorial forking nanoGPT.
  Implements top-k expert routing, load balancing loss, expert capacity
  factors, and MoE transformer blocks. Use this skill when building MoE
  language models, implementing expert routing, or studying sparse
  activation patterns.
---

# nanoMoE — Mixture of Experts from Scratch

**GitHub**: https://github.com/wolfecameron/nanoMoE

## 2. Architecture Overview

| Component | Class/Module | Purpose |
|-----------|-------------|---------|
| Router | `TopKRouter` | Selects top-k experts per token |
| Expert | `Expert` | Standard MLP (FFN) block |
| MoE Layer | `SparseMoEBlock` | Replaces FFN in transformer |
| Load Balance | `load_balancing_loss()` | Prevents expert collapse |

## 3. Mathematical Foundations

Load Balancing Loss (from Switch Transformer, Eq. 4):
L_balance = α × N × Σᵢ (fᵢ × pᵢ)
where:
  N = number of experts
  fᵢ = fraction of tokens routed to expert i
  pᵢ = average router probability for expert i
  α = loss coefficient (default: 0.01)

...
```

</details>

---

## All Options

```
python skillforge.py [OPTIONS]

Input (pick one):
  --arxiv ID          arXiv paper ID or URL (e.g., 2103.13630)
  --pdf PATH          Local PDF file
  --github URL        GitHub repository URL

Provider:
  --provider          anthropic or gemini (default: anthropic)
  --model MODEL       Model override (e.g., gemini-2.5-flash)
  --api-key KEY       API key (or set via environment variable)

Quality:
  --domain DOMAIN     Inject domain context: imageclef, parametergolf, kaggle
  --skip-validation   Skip quality validation (saves 1-3 API calls)
  --no-llm            Extract text only — zero API calls

Tuning:
  --output DIR        Output directory (default: ./skills)
  --max-pages N       Max PDF pages (default: 40)
  --max-file-kb N     Max file size for GitHub repos (default: 200KB)
  --token-budget N    Max tokens for GitHub analysis (default: 150000)

Batch:
  skillforge.py batch --list sources.txt [--delay 5]
```

---

## Domain Context Injection

SkillForge can inject competition-specific or domain-specific assessment into skill files:

```bash
# For ML competition work
python skillforge.py --arxiv 2103.13630 --domain parametergolf

# For ImageCLEF deepfake challenge
python skillforge.py --github https://github.com/some/repo --domain imageclef

# For Kaggle
python skillforge.py --pdf paper.pdf --domain kaggle
```

This adds a dedicated section to the skill file assessing the paper's relevance to your specific use case.

---

## Use Cases

**ML Competition Teams** — Process your entire reading list overnight. Each paper becomes a composable knowledge module your AI assistant can use during implementation. I use this for WorldQuant IQC and OpenAI Parameter Golf.

**Research Labs** — Build a shared, version-controlled skill library from your team's paper reading. When one person reads a paper, the entire team's AI tools immediately know how to implement it.

**AI Course Creators** — Pre-extract skill packs per lecture topic. Students get structured implementation references alongside the course material.

**Individual Researchers** — Stop re-reading papers. Extract once, reference forever. Skill files compose — load quantization + pruning + knowledge distillation together for a complete compression pipeline.

---

## Comparison

| | SkillForge | Papers With Code | Elicit | Manual Reading |
|---|---|---|---|---|
| **Output** | Implementation-ready skill file | Links to code repos | Research summaries | Notes (if you take them) |
| **Captures formulas** | ✅ Every equation | ❌ | ❌ | Depends on you |
| **Captures code** | ✅ Annotated snippets | Links only | ❌ | ❌ |
| **Captures configs** | ✅ Full hyperparameters | ❌ | ❌ | Maybe |
| **LLM-consumable** | ✅ Designed for it | ❌ | ❌ | ❌ |
| **Composable** | ✅ Stack multiple skills | ❌ | ❌ | ❌ |
| **Time per paper** | 2 minutes | N/A | ~1 minute (summary only) | 4-8 hours |
| **Cost** | $0 (Gemini) - $0.30 | Free | Subscription | Free (but slow) |

---

## Skill File Format

A skill file (SKILL.md) is a structured markdown document with:

```yaml
---
name: kebab-case-identifier
description: >
  Trigger-heavy description (100-150 words) that ensures
  the skill file is activated when relevant. Includes paper
  title, authors, year, and all relevant keywords.
---
```

Followed by sections covering: problem setup, core methods with every formula, architecture details, experimental results with full tables, implementation takeaways, and key references.

The format is designed to be:
- **Exhaustive** — every formula, every hyperparameter
- **LLM-native** — clear headers, code blocks, markdown tables
- **Composable** — multiple skill files stack together as context
- **Implementation-oriented** — not "what" but "how to build it"

See [docs/SKILL_FORMAT_SPEC.md](docs/SKILL_FORMAT_SPEC.md) for the full specification.

---

## Project Structure

```
skillforge-ai/
├── skillforge.py          # The complete tool (single file, ~1050 lines)
├── requirements.txt       # anthropic, PyMuPDF, google-generativeai
├── README.md
├── LICENSE
├── examples/
│   ├── sources.txt        # Sample batch file
│   └── sample-skills/     # Pre-generated examples
│       ├── nanomoe/SKILL.md
│       ├── deep-compression/SKILL.md
│       └── neural-network-quantization-survey/SKILL.md
├── notebooks/
│   └── SkillForge_Demo.ipynb
└── docs/
    └── SKILL_FORMAT_SPEC.md
```

---

## Roadmap

- [x] arXiv paper → SKILL.md
- [x] GitHub repo → SKILL.md
- [x] Local PDF → SKILL.md
- [x] Gemini + Anthropic provider support
- [x] Batch processing
- [x] Domain context injection
- [x] Agentic quality-gate retry loop
- [x] Zero-cost `--no-llm` mode
- [ ] Web UI (Streamlit — upload PDF, get skill file)
- [ ] Claude Code MCP server integration
- [ ] Skill file marketplace with curated packs
- [ ] VSCode extension
- [ ] Auto-update when papers get new arXiv versions
- [ ] Community contribution pipeline

---

## Contributing

PRs welcome. The most impactful contributions right now:

1. **Better extraction prompts** — if you find a paper type where extraction quality is low, submit the paper + expected output as a test case
2. **New domain contexts** — add your competition/field to `DOMAIN_CONTEXTS`
3. **Example skill files** — generate and submit high-quality skill files for popular papers

---

## License

MIT

---

## Author

**Anubhav Bharadwaj** — IIT (ISM) Dhanbad • IIT Kanpur MBA '28

Built while competing in WorldQuant IQC 2026, OpenAI Parameter Golf, and ImageCLEF 2026.

- GitHub: [@AnubhavBharadwaaj](https://github.com/AnubhavBharadwaaj)
