#!/usr/bin/env bash
# augment_sparse_verbatim.sh — for docs-based skills where the code-block
# extractor produced minimal output, append the full page.md content so
# the verbatim file actually contains the page's source of truth.
#
# The triple-backtick extraction works great for repos but fails when docs
# pages render code without proper markdown fences. This script adds the
# entire crawled page content as a fallback "literal page content" section.

set -u

SKILLS_DIR="${1:-./skills}"
THRESHOLD_BYTES="${2:-2000}"   # below this, augment

# Map of skill_subdir → source_subdir for docs-type sources
DOCS_SOURCES=(
    "discovery-and-indexing|explorer.oobeprotocol.ai_docs_core_discovery"
    "explorer.oobeprotocol.ai_docs_sdk_escrow-api|explorer.oobeprotocol.ai_docs_sdk_escrow-api"
    "x402-payments|explorer.oobeprotocol.ai_docs_core_payments"
)

G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Augment sparse verbatim references"
echo "═══════════════════════════════════════════════════════════"
echo "  Below-threshold size: ${THRESHOLD_BYTES} bytes"
echo ""

AUGMENTED=0
SKIPPED=0
FAILED=0

for entry in "${DOCS_SOURCES[@]}"; do
    IFS='|' read -r skill_subdir source_subdir <<< "$entry"

    verbatim="$SKILLS_DIR/$skill_subdir/VERBATIM_REFERENCE.md"
    source_dir="$SKILLS_DIR/.sources/$source_subdir/files"

    echo "── $skill_subdir"

    if [[ ! -f "$verbatim" ]]; then
        echo "  ${R}✗${N} no VERBATIM_REFERENCE.md — run fix_critical_skills_v2.sh first"
        FAILED=$((FAILED + 1))
        continue
    fi

    current_size=$(wc -c < "$verbatim")
    if (( current_size >= THRESHOLD_BYTES )); then
        echo "  ${Y}⚠${N} already $current_size bytes (above threshold) — skipping"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    if [[ ! -d "$source_dir" ]]; then
        echo "  ${R}✗${N} no source bundle at $source_dir"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Check idempotency: don't append if "Full page content" header already there
    if grep -qF "## Full page content (verbatim fallback)" "$verbatim"; then
        echo "  ${Y}⚠${N} already augmented — skipping"
        SKIPPED=$((SKIPPED + 1))
        continue
    fi

    # Find all .md files in the source bundle and append them
    md_files=$(find "$source_dir" -type f -name "*.md" 2>/dev/null | sort)
    if [[ -z "$md_files" ]]; then
        echo "  ${R}✗${N} no .md files in $source_dir"
        FAILED=$((FAILED + 1))
        continue
    fi

    {
        echo ""
        echo "---"
        echo ""
        echo "## Full page content (verbatim fallback)"
        echo ""
        echo "> The structured code-block extractor found insufficient triple-backtick"
        echo "> fenced blocks. The full markdown content of the original docs page(s)"
        echo "> is appended below as the source of truth. Every line below is the"
        echo "> verbatim output of trafilatura's main-content extraction from the"
        echo "> live page at the time SkillForge ran."
        echo ""
        for f in $md_files; do
            rel="${f#$source_dir/}"
            echo "### From \`$rel\`"
            echo ""
            cat "$f"
            echo ""
        done
    } >> "$verbatim"

    new_size=$(wc -c < "$verbatim")
    echo "  ${G}✓${N} augmented: $current_size B → $new_size B (+$((new_size - current_size)) B)"
    AUGMENTED=$((AUGMENTED + 1))
done

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ${G}Augmented: $AUGMENTED${N}    Skipped: $SKIPPED    Failed: $FAILED"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "Final per-skill sizes (all 5 critical):"
echo ""
for entry in \
    "discovery-and-indexing" \
    "x402-client" \
    "explorer.oobeprotocol.ai_docs_sdk_escrow-api" \
    "x402-payments" \
    "acedatacloud-mcps"; do
    f="$SKILLS_DIR/$entry/VERBATIM_REFERENCE.md"
    if [[ -f "$f" ]]; then
        printf "  %-50s %10s B\n" "$entry" "$(wc -c < $f)"
    else
        printf "  %-50s %10s\n" "$entry" "(missing)"
    fi
done
echo ""
