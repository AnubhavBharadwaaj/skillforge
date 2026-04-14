<p align="center">
  <h1 align="center">🔨 SkillForge</h1>
  <p align="center"><strong>Turn any research paper or GitHub repo into an AI-native skill file in 60 seconds.</strong></p>
  <p align="center">
    <a href="#quickstart">Quickstart</a> •
    <a href="#how-it-works">How It Works</a> •
    <a href="#openrouter--free-tier">Free Tier</a> •
    <a href="#benchmarks">Benchmarks</a> •
    <a href="#examples">Examples</a> •
    <a href="#demo-video">Demo Video</a>
  </p>
</p>

---

SkillForge is an **agentic CLI tool** that extracts every formula, algorithm, hyperparameter, and implementation detail from research papers and codebases, then packages them into structured **skill files** optimized for LLM consumption.

Unlike paper summaries (for humans) or datasets (for training), skill files are a **new artifact type**: runtime knowledge injections that make AI coding assistants dramatically more effective at implementation.

```
📄 33-page PDF  ──→  SkillForge  ──→  550-line SKILL.md with every equation,
                                       every table, every implementation detail
```

```
🐙 GitHub repo  ──→  SkillForge  ──→  400-line SKILL.md with architecture,
                                       annotated code, configs, math from comments
```

### The Key Insight

**A 4B model + SKILL.md outperforms a 70B model + raw PDF.** SkillForge uses a frontier model once to do the hard reading comprehension. After that, any model — even one running on your phone — gives answers with precision approaching frontier quality. The weaker the model, the bigger the gain.

---

## Demo Video

[![SkillForge Demo](https://img.youtube.com/vi/O0J55eRcwZw/maxresdefault.jpg)](https://www.youtube.com/watch?v=O0J55eRcwZw)

**[Watch the full demo →](https://www.youtube.com/watch?v=O0J55eRcwZw)**

---

## Why?

Reading a quantization paper takes **4 hours**. Extracting the 12 formulas, 8 hyperparameters, and 3 known failure modes you actually need takes **another 2 hours**. Multiply by 20 papers per project.

SkillForge does this in **one command**. The output is structured so Claude, GPT, Cursor, or Copilot can use it directly as context — no more pasting paper excerpts into chat.

### Why not just upload the PDF to Claude?

Claude reads PDFs excellently. But:

- **Consistency**: Skill files follow the same schema every time. Three files from three different months have identical structure — same YAML triggers, same equation notation, same table layout. Three PDFs have whatever format the authors chose.
- **Composability**: Skill files go in your git repo. They travel with your codebase. They work in Claude, Cursor, Windsurf — any tool that reads files. ChatPDF sessions expire.
- **Weak model support**: A 4B model can't fit a 33-page PDF in its 8K context window. A 500-line skill file fits easily and gives precise answers.
- **Batch pipeline**: Update `sources.txt` with your weekly reading list, run one command, your entire research library stays current.
- **Cost**: Process 30 papers for ~$0.30 total with `--paid`, or free with OpenRouter free models.

---

## Quickstart

```bash
pip install anthropic PyMuPDF google-generativeai openai
```

```bash
# arXiv paper → skill file (free, uses OpenRouter free models)
python skillforge.py --arxiv 2103.13630 --provider openrouter

# Paid mode — fast, reliable, ~$0.03/paper
python skillforge.py --arxiv 2103.13630 --provider openrouter --paid

# GitHub repo → skill file
python skillforge.py --github https://github.com/wolfecameron/nanoMoE --provider openrouter

# Local PDF → skill file
python skillforge.py --pdf paper.pdf --provider anthropic

# Batch mode — process your entire reading list
python skillforge.py batch --list sources.txt --provider openrouter --paid

# High quality target — escalates to stronger models automatically
python skillforge.py --arxiv 2103.13630 --provider openrouter --paid --quality 9
```

Set your API key:
```bash
export OPENROUTER_API_KEY="..."   # Free key at openrouter.ai
# or
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
                    │ → Escalate model │
                    │ (up to 7 models) │
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
                                              ┌───────────────┐          │
                                              │ Agentic Loop  │◀─────────┘
                                              │ Validate → Fix│
                                              │ → Escalate    │
                                              └───────┬───────┘
                                                      │
                                              ┌───────▼───────┐
                                              │ Save SKILL.md │
                                              └───────────────┘
```

### What Makes It Agentic

**Quality-gate retry loop with model escalation**: After generating a skill file, SkillForge validates the output, identifies specific gaps (missing equations, missing benchmark tables), and fixes them. If the current model can't hit the quality target, it automatically escalates to a stronger model.

```
Extract → Validate (5/10: "missing load balancing loss formula")
       → Fix (send gaps + source → LLM rewrites)
       → Re-validate (6/10: still below target)
       → Escalate (gemini-flash → deepseek-v3 → gemini-pro → claude-sonnet → claude-opus)
       → Fix with stronger model
       → Re-validate (8/10: ✓)
       → Save
```

---

## OpenRouter — Free Tier

SkillForge's OpenRouter provider gives you access to dozens of models through a single API key, including completely free models.

### How It Works

1. **Live model discovery** — queries OpenRouter's API, filters for chat-capable models with ≥32K context
2. **SQS ranking** — ranks models by Speed (30%), Completion Cap (20%), Context Length (15%), Capability (15%), Recency (10%), Reputation (10%)
3. **Auto-rotation** — if a model rate-limits, SkillForge automatically rotates to the next one
4. **Free → paid upgrade** — when free models are exhausted, auto-upgrades to cheap paid models
5. **Quality escalation chain** — if quality target isn't met, escalates through stronger models

### Quality Escalation Chain (--paid mode)

When the current model can't hit your quality target:

```
gemini-2.5-flash     → $0.01/paper   (default, tries first)
deepseek-chat-v3     → $0.02/paper   (escalation 1)
gemini-2.5-pro       → $0.05/paper   (escalation 2)
gpt-4.1-mini         → $0.03/paper   (escalation 3)
claude-sonnet-4      → $0.15/paper   (escalation 4)
gpt-4.1              → $0.30/paper   (escalation 5)
claude-opus-4        → $0.75/paper   (final boss)
```

Most papers hit 7/10 on gemini-flash without escalation. The average cost stays low because escalation is rare.

### What You See on Screen

```
🔍 Initial quality: 5/10 (target: 7/10)
   🔧 Fixing 4 gaps…
   📝 Fixed version: 380 lines
🔍 Retry 1 quality: 6/10
   ──────────────────────────────────────────────────
   ⬆ Quality escalation (6/7 not met)
   ├─ ✗ gemini-2.5-flash → scored 6/10
   ├─ ✓ deepseek-chat-v3-0324 (stronger model)
   └─ 5 escalations remaining
   ──────────────────────────────────────────────────
   🔧 Re-running repair with stronger model…
🔍 Post-escalation quality: 8/10
   ✓ Quality target met after escalation (8/7)
```

---

## Benchmarks

Tested with OpenRouter `--paid` mode (gemini-2.5-flash primary). All runs automated, no manual editing.

| Source | Type | Lines | Chars | Quality | Time | Cost |
|--------|------|-------|-------|---------|------|------|
| [Quantization Survey](https://arxiv.org/abs/2103.13630) (33 pages) | arXiv | 550 | 58,503 | 7/10 | 161s | ~$0.03 |
| [Deep Compression](https://arxiv.org/abs/1510.00149) (14 pages) | arXiv | 250 | 16,004 | 7/10 | ~90s | ~$0.01 |
| [Distilling Knowledge](https://arxiv.org/abs/1503.02531) (9 pages) | arXiv | 200 | 12,800 | 7/10 | ~70s | ~$0.01 |
| [nanoMoE](https://github.com/wolfecameron/nanoMoE) | GitHub | 349 | 22,346 | 7/10 | ~45s | ~$0.01 |
| 30-paper batch (mixed) | Batch | 9,000+ | — | avg 7/10 | ~60min | ~$0.30 |

### vs Manual Reading

For the quantization survey (33 pages, 13 equations, 8 result tables):

| | Manual Reading | SkillForge |
|---|---|---|
| **Time** | 6-8 hours | 2.7 minutes |
| **Lines** | 455 | 550 |
| **Equations captured** | 13/13 | 10-11/13 |
| **Result tables** | Complete | Complete |
| **Implementation takeaways** | 10 items | 12 items |
| **Quality** | 100% | ~87% |
| **Reusable format** | No | Yes — YAML triggers, git-versionable |

### Weak Model Democratization

The real value isn't competing with frontier models on raw PDFs — it's making tiny models competitive by pre-digesting knowledge:

| Model | With Raw PDF | With SKILL.md | Gain |
|-------|-------------|---------------|------|
| Llama 3.2 3B | Can't fit 33-page PDF in context | Precise answers from structured tables | Massive |
| Gemma 3 4B | Hallucinates numbers from dense prose | Finds exact values in labeled sections | +4x accuracy |
| Llama 3.3 70B | Good but misses buried details | Competitive with frontier | +2x accuracy |
| Claude Opus 4.6 | Excellent | Excellent (same quality, faster lookup) | Minimal |

**A 4B model + SKILL.md outperforms a 70B model + raw PDF.** SkillForge extracts once with a frontier model, then any model gives precise answers.

### Cost Per Paper

| Provider | Mode | Cost/Paper | Notes |
|----------|------|-----------|-------|
| OpenRouter | Free models | **$0.00** | Auto-discovers free models, rotates on rate limits |
| OpenRouter | `--paid` | **$0.01-0.03** | Gemini Flash, fast and reliable |
| OpenRouter | `--paid --quality 9` | **$0.05-0.50** | Auto-escalates to stronger models |
| Gemini | Direct | **Free** | Generous free tier |
| Anthropic | Claude Sonnet | ~$0.15 | Higher quality |

---

## Examples

### Example Output: Quantization Survey (Gholami et al.)

<details>
<summary>Click to expand — YAML trigger + equations + tables</summary>

```yaml
---
name: quantization-for-efficient-neural-networks
description: Use this skill when you need to understand, implement, or optimize
  neural network quantization for efficient inference. This includes scenarios like
  deploying models on resource-constrained edge devices, reducing model memory
  footprint, accelerating inference speed, or improving energy efficiency.
  Specifically, leverage this skill for choosing between uniform/non-uniform,
  symmetric/asymmetric quantization, deciding on static/dynamic range calibration,
  selecting quantization granularity (layerwise, channelwise), and applying
  fine-tuning methods like QAT or PTQ.
---

# Quantization for Efficient Neural Networks: A Survey
**Paper**: Gholami et al. (UC Berkeley, Intel, Google Brain)
**Published**: arXiv, 2021
**Key result**: INT8 inference showing up to 5.02x speedup for InceptionV3
  and mixed-precision INT4/INT8 yielding 23% speedup for ResNet50 vs INT8.

## Uniform Quantization
Q(r) = Int(r/S) - Z
where:
  r = real-valued input (activation or weight)
  S = real-valued scaling factor
  Z = integer zero point
  Int() = rounding operation

## Scaling Factor
S = (β - α) / (2^b - 1)
where:
  [α, β] = clipping range
  b = quantization bit width

## Inference Speedup Data
| Model       | Quant Type | Hardware        | Speedup |
|-------------|-----------|-----------------|---------|
| ResNet50    | INT8      | NVIDIA GTX 1080 | 3.89x   |
| InceptionV3 | INT8      | NVIDIA GTX 1080 | 5.02x   |
| BERT        | INT8      | (unspecified)   | 4.0x    |

## Key Takeaways
1. Use symmetric quantization for weights, asymmetric for activations
2. Channelwise quantization for kernels — dedicated scaling factor per channel
3. QAT yields higher accuracy but requires retraining; PTQ is faster
4. STE is the workhorse for QAT gradients
...
```

</details>

### Example Output: Deep Compression (Han et al.)

<details>
<summary>Click to expand — 250 lines</summary>

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

---

## All Options

```
python skillforge.py [OPTIONS]

Input (pick one):
  --arxiv ID          arXiv paper ID or URL (e.g., 2103.13630)
  --pdf PATH          Local PDF file
  --github URL        GitHub repository URL

Provider:
  --provider          anthropic, gemini, or openrouter (default: anthropic)
  --model MODEL       Model override (e.g., gemini-2.5-flash)
  --api-key KEY       API key (or set via environment variable)

Quality:
  --paid              Use cheap paid models (~$0.01-0.10/paper)
  --quality N         Quality target 1-10 (default: 7)
  --verify            Extra verification pass checking equations/tables
  --domain DOMAIN     Inject domain context: imageclef, parametergolf, kaggle
  --skip-validation   Skip quality validation (saves 1-3 API calls)
  --no-llm            Extract text only — zero API calls

Tuning:
  --output DIR        Output directory (default: ./skills)
  --max-pages N       Max PDF pages (default: 40)
  --max-file-kb N     Max file size for GitHub repos (default: 200KB)
  --token-budget N    Max tokens for GitHub analysis (default: 150000)

Batch:
  skillforge.py batch --list sources.txt [--delay 5] [--paid] [--quality N]
```

---

## Skill File Format

A skill file (SKILL.md) is a structured markdown document with a YAML trigger header:

```yaml
---
name: kebab-case-identifier
description: >
  Trigger-heavy description (100-150 words) that tells Claude or Cursor
  WHEN to load this file. You don't manually attach it — your AI assistant
  reads the trigger and pulls it in automatically when the topic comes up.
---
```

Followed by sections covering: paper metadata, problem setup, core methods with every formula, architecture details, experimental results with full tables, implementation takeaways, and key references.

The format is designed to be:
- **Exhaustive** — every formula, every hyperparameter, every result table
- **LLM-native** — clear headers, code blocks, markdown tables
- **Composable** — multiple skill files stack together as context
- **Consistent** — same structure every time, across months, across papers
- **Git-versionable** — plain text, diffs cleanly, travels with your codebase

See [docs/SKILL_FORMAT_SPEC.md](docs/SKILL_FORMAT_SPEC.md) for the full specification.

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

**ML Competition Teams** — Process your entire reading list overnight. Each paper becomes a composable knowledge module your AI assistant can use during implementation. Built for WorldQuant IQC, OpenAI Parameter Golf, and ImageCLEF 2026.

**Research Labs** — Build a shared, version-controlled skill library. When one person reads a paper, the entire team's AI tools immediately know how to implement it. Monday morning: update `sources.txt` with last week's papers, run one command, library stays current.

**Weak Model Users** — Can't afford frontier API costs? Extract skill files once with SkillForge's free tier, then use them with Ollama, llama.cpp, or any local model. A 4B model with skill files gives precise answers that a 70B model with raw PDFs can't match.

**Individual Researchers** — Stop re-reading papers. Extract once, reference forever. Skill files compose — load quantization + pruning + knowledge distillation together for a complete compression pipeline.

---

## Comparison

| | SkillForge | Papers With Code | Elicit | ChatPDF | Manual Reading |
|---|---|---|---|---|---|
| **Output** | Implementation-ready skill file | Links to code repos | Research summaries | Chat session | Notes (if you take them) |
| **Captures formulas** | ✅ Every equation with variables defined | ❌ | ❌ | Partial | Depends on you |
| **Captures code** | ✅ Annotated snippets | Links only | ❌ | ❌ | ❌ |
| **Captures configs** | ✅ Full hyperparameters | ❌ | ❌ | If you ask | Maybe |
| **YAML triggers** | ✅ Auto-activates in Claude/Cursor | ❌ | ❌ | ❌ | ❌ |
| **Composable** | ✅ Same format, stackable | ❌ | ❌ | Sessions expire | ❌ |
| **Batch processing** | ✅ 30 papers, one command | ❌ | Limited | One at a time | ❌ |
| **Git-versionable** | ✅ Plain markdown | ❌ | ❌ | ❌ | ❌ |
| **Works with weak models** | ✅ 4B models give precise answers | ❌ | ❌ | ❌ | ❌ |
| **Time per paper** | 2-3 minutes | N/A | ~1 minute (summary only) | Manual effort | 4-8 hours |
| **Cost** | $0 (free) - $0.03 (paid) | Free | Subscription | Subscription | Free (but slow) |

---

## Project Structure

```
skillforge-ai/
├── skillforge.py          # The complete tool (single file, ~1990 lines)
├── requirements.txt       # anthropic, PyMuPDF, google-generativeai, openai
├── README.md
├── LICENSE
├── examples/
│   ├── sources.txt        # Sample batch file (4 sources)
│   ├── sources_30.txt     # 30-paper batch file for benchmarking
│   └── sample-skills/     # Pre-generated examples
│       ├── quantization-for-efficient-neural-networks/SKILL.md
│       ├── deep-compression/SKILL.md
│       └── nanomoe/SKILL.md
├── notebooks/
│   └── SkillForge_Demo.ipynb
├── benchmark/
│   ├── benchmark.py       # Evaluation engine (FactScore, RAGAS, HHEM)
│   └── benchmark.jsx      # React visualization dashboard
└── docs/
    └── SKILL_FORMAT_SPEC.md
```

---

## Roadmap

### Completed
- [x] arXiv paper → SKILL.md
- [x] GitHub repo → SKILL.md
- [x] Local PDF → SKILL.md
- [x] Gemini + Anthropic + OpenRouter provider support
- [x] Batch processing with `sources.txt`
- [x] Domain context injection (parametergolf, imageclef, kaggle)
- [x] Agentic quality-gate retry loop
- [x] OpenRouter free model auto-discovery with SQS ranking
- [x] Agentic model rotation on rate limits / errors
- [x] Quality escalation chain (7 models, cheapest → strongest)
- [x] `--paid` mode with cheap models (~$0.01-0.03/paper)
- [x] `--quality N` configurable quality target
- [x] `--verify` extra verification pass
- [x] Auto-upgrade from free → paid when free pool exhausts
- [x] Terminal spinner (braille animation) on all long operations
- [x] NoneType / 402 / empty response crash fixes
- [x] Zero-cost `--no-llm` mode

### Coming Soon
- [ ] **Semantic Scholar integration** — citation graph queries, citation intent (supporting/contrasting), live conflict detection across your skill library
- [ ] **Execution-based verification** — clone paper repos, run training scripts, verify claimed results match (inspired by [FactReview](https://arxiv.org/abs/2604.04074))
- [ ] **Live monitoring daemon** — watch arXiv RSS for papers that cite papers in your library, auto-flag contradictions
- [ ] **Ollama provider** — `--provider ollama` for fully local, zero-cost processing
- [ ] **`--compact` mode** — shorter skill files optimized for 8K context windows
- [ ] **Cross-reference index** — auto-generated `CROSS_REF.md` showing conflicts and agreements across your skill library
- [ ] Claude Code MCP server integration
- [ ] Cursor plugin / VS Code extension
- [ ] Web UI (Streamlit)

---

## Contributing

PRs welcome. The most impactful contributions right now:

1. **Better extraction prompts** — if you find a paper type where extraction quality is low, submit the paper + expected output as a test case
2. **New domain contexts** — add your competition/field to `DOMAIN_CONTEXTS`
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
