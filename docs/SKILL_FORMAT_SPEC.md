# Skill File Format Specification v1.0

## Overview

A skill file (SKILL.md) is a structured knowledge artifact optimized for LLM consumption during coding tasks. Unlike paper summaries (optimized for human reading) or datasets (optimized for training), skill files are **runtime knowledge injections** that make an LLM dramatically more effective at implementing specific techniques.

## Design Principles

1. **Exhaustive over concise** — every formula, every hyperparameter, every known failure mode
2. **LLM-native formatting** — code blocks for math, markdown tables for structured data, clear headers for navigation
3. **Composable** — skill files stack together. Load quantization + pruning + distillation for a complete compression pipeline
4. **Implementation-oriented** — not "what the paper found" but "how to build it"
5. **Bug-aware** — known failure modes, edge cases, pitfalls that would waste hours of debugging

## Required Structure

### 1. YAML Frontmatter

```yaml
---
name: kebab-case-identifier
description: >
  100-150 word trigger description. Must include: paper title, all author
  last names, publication year and venue, key technique names, alternative
  names or acronyms. Must include "Use this skill when..." with 3-4
  specific trigger scenarios. Should be slightly aggressive about
  triggering — false positives are better than false negatives.
---
```

### 2. Paper/Project Metadata

```markdown
# Full Paper Title

**Paper**: Author1, Author2, Author3 (Affiliation)
**Published**: Venue, Year
**arXiv**: XXXX.XXXXX (if applicable)
**GitHub**: https://github.com/... (if applicable)
**Key result**: One-line summary with specific numbers
```

### 3. Content Sections (Paper-sourced)

For papers, sections should include:

- **Problem Setup and Notation** — every variable, every convention
- **Core Methods** (1 section per major contribution) — every formula with variable definitions, every algorithm
- **Experimental Results** — complete tables with ALL rows and numbers, not summaries
- **Hardware/Efficiency Data** — energy tables, speedup tables, throughput comparisons
- **Key Takeaways for Implementation** — 8-12 numbered, specific, actionable items
- **References** — key cited papers useful for implementation

### 4. Content Sections (Code-sourced)

For GitHub repos, sections should include:

- **What It Does** — 2-4 paragraphs, plain language
- **Architecture Overview** — component table + pipeline ASCII art with data shapes
- **Detailed Code Analysis** — per module: class definitions, forward() signatures, config values, annotated snippets
- **Mathematical Foundations** — formulas implemented in code, written as equations
- **Training Configuration** — all hyperparameters in a table
- **Benchmark Results** — from README/logs
- **Dependencies** — packages with versions
- **Adaptation Patterns** — what's reusable and how

## Equation Format

```
Equation N: [Description]
L_balance = α × N × Σᵢ (fᵢ × pᵢ)
where:
  α = loss coefficient (default: 0.01)
  N = number of experts
  fᵢ = fraction of tokens routed to expert i
  pᵢ = average router probability for expert i
```

## Quality Targets

| Metric | Minimum | Good | Excellent |
|--------|---------|------|-----------|
| Lines | 250 | 400 | 500+ |
| Equations captured | 70% | 85% | 100% |
| Result tables | Key ones | Most | All |
| Implementation takeaways | 5 | 8 | 12+ |
| Validation score | 5/10 | 7/10 | 9/10 |
