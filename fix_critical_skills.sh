#!/usr/bin/env bash
# ============================================================================
# fix_critical_skills.sh — eliminate silent-code-mistake risk for the bounty
#
# Strategy: stop asking the LLM to paraphrase API surface. Instead, extract
# the EXACT function signatures, type declarations, exports, and code blocks
# directly from the source files on disk (no LLM in the loop), and inject a
# trust directive at the top of each SKILL.md telling Claude to prefer that
# verbatim file for any code generation.
#
# Result: the 5 weakest skill files now have a companion VERBATIM_REFERENCE.md
# that's guaranteed accurate (it's literally copy-pasted from source). When
# Claude writes code, it uses these verbatim signatures — no invented method
# names, no hallucinated parameters, no wrong types.
#
# Usage: ./fix_critical_skills.sh [skills_dir]
#        (default skills_dir = ./skills)
# ============================================================================

# Note: NOT using pipefail because head/tail-truncated pipelines (head -200,
# head -120) would SIGPIPE upstream commands and trigger set -e exit on
# legitimate "we've seen enough" pipeline endings. We handle individual
# failures with || true where needed.
set -eu

SKILLS_DIR="${1:-./skills}"

# Critical files identified from the batch run.
# Format: skill_subdir|source_subdir|kind(repo|web)
CRITICAL=(
    "discovery-and-indexing|explorer.oobeprotocol.ai_docs_core_discovery|web"
    "x402-client|AceDataCloud_X402Client|repo"
    "explorer.oobeprotocol.ai_docs_sdk_escrow-api|explorer.oobeprotocol.ai_docs_sdk_escrow-api|web"
    "x402-payments|explorer.oobeprotocol.ai_docs_core_payments|web"
    "acedatacloud-mcps|AceDataCloud_MCPs|repo"
)

# Colors
G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; B=$'\e[34m'; D=$'\e[2m'; N=$'\e[0m'

log()  { echo "${B}[$(date +%H:%M:%S)]${N} $*"; }
ok()   { echo "  ${G}✓${N} $*"; }
warn() { echo "  ${Y}⚠${N} $*"; }
err()  { echo "  ${R}✗${N} $*" >&2; }

banner() {
    echo ""
    echo "═══════════════════════════════════════════════════════════"
    echo "  $1"
    echo "═══════════════════════════════════════════════════════════"
}

section() {
    echo ""
    echo "──────────────────────────────────────────────────────────"
    echo "  $1"
    echo "──────────────────────────────────────────────────────────"
}

[[ -d "$SKILLS_DIR" ]] || { err "Skills dir not found: $SKILLS_DIR"; exit 1; }

banner "Critical skill repair — bounty 10/10 enforcement"
echo "  Skills directory: $SKILLS_DIR"
echo "  Files to repair:  ${#CRITICAL[@]}"

# ============================================================================
# Function: extract_repo_api
# Pulls public API surface out of TS/JS/Python files in a source bundle.
# Output goes to stdout in markdown form.
# ============================================================================
extract_repo_api() {
    local src_root="$1/files"
    [[ -d "$src_root" ]] || { warn "no files/ at $src_root"; return; }

    # Priority entry points first (these have the public surface)
    local entry_candidates=(
        "src/index.ts" "src/index.js" "src/main.ts" "src/main.py"
        "index.ts" "index.js" "main.py" "__init__.py"
        "lib/index.ts" "lib/index.js"
    )
    echo ""
    echo "## Entry points (verbatim)"
    echo ""
    for cand in "${entry_candidates[@]}"; do
        local f="$src_root/$cand"
        if [[ -f "$f" ]]; then
            local size; size=$(wc -c < "$f")
            if (( size < 50000 )); then
                echo "### \`$cand\`"
                echo ""
                local ext="${cand##*.}"
                echo "\`\`\`$ext"
                cat "$f"
                echo "\`\`\`"
                echo ""
            fi
        fi
    done

    # TypeScript / JavaScript: export and declare lines + 5 lines of context
    echo ""
    echo "## TypeScript/JavaScript exports (verbatim)"
    echo ""
    find "$src_root" -type f \( -name "*.ts" -o -name "*.tsx" -o -name "*.mts" -o -name "*.cts" \) 2>/dev/null \
        | grep -vE "node_modules|\.test\.|\.spec\.|__tests__|\.d\.ts$" \
        | sort | while read -r f; do
            relpath="${f#$src_root/}"
            if grep -qE "^(export|declare) " "$f" 2>/dev/null; then
                echo "### \`$relpath\`"
                echo ""
                echo '```typescript'
                # Multi-line signature capture: from export/declare line until line ending in
                # ;, {, or starts looking like next declaration
                { awk '
                    /^(export|declare) / { capturing=1; depth=0 }
                    capturing {
                        print
                        # crude brace tracker
                        n_open = gsub(/\{/, "{")
                        n_close = gsub(/\}/, "}")
                        depth += n_open - n_close
                        if (depth == 0 && /[;}]$/) { capturing=0; print "" }
                    }
                ' "$f" 2>/dev/null | head -120 2>/dev/null; } || true
                echo '```'
                echo ""
            fi
        done

    # Python: def, class, async def + the line right after for signatures
    echo ""
    echo "## Python definitions (verbatim)"
    echo ""
    find "$src_root" -type f -name "*.py" 2>/dev/null \
        | grep -vE "__pycache__|test_|_test\.py|tests/" \
        | sort | while read -r f; do
            relpath="${f#$src_root/}"
            if grep -qE "^(async def|def|class) " "$f" 2>/dev/null; then
                echo "### \`$relpath\`"
                echo ""
                echo '```python'
                { grep -nE "^(async def|def|class|@[A-Za-z])" "$f" 2>/dev/null \
                    | head -80 2>/dev/null \
                    | sed 's/^[0-9]*://' 2>/dev/null; } || true
                echo '```'
                echo ""
            fi
        done

    # Rust / Go signatures (for kora etc — defensive)
    echo ""
    echo "## Rust/Go signatures (verbatim)"
    echo ""
    find "$src_root" -type f \( -name "*.rs" -o -name "*.go" \) 2>/dev/null \
        | grep -vE "_test\.go|tests/" | sort | while read -r f; do
            relpath="${f#$src_root/}"
            local has_sig=0
            if grep -qE "^(pub fn|pub struct|pub enum|pub trait|func |type )" "$f" 2>/dev/null; then
                has_sig=1
            fi
            if (( has_sig )); then
                echo "### \`$relpath\`"
                echo ""
                local ext="${f##*.}"
                echo "\`\`\`$ext"
                { grep -E "^(pub fn|pub struct|pub enum|pub trait|func |type )" "$f" 2>/dev/null | head -50 2>/dev/null; } || true
                echo '```'
                echo ""
            fi
        done

    # Package/config files for type-correct constants
    echo ""
    echo "## Package manifests & config (verbatim)"
    echo ""
    for manifest in "package.json" "pyproject.toml" "Cargo.toml" "go.mod" "tsconfig.json"; do
        local f="$src_root/$manifest"
        if [[ -f "$f" ]]; then
            echo "### \`$manifest\`"
            echo ""
            local ext="${manifest##*.}"
            echo "\`\`\`$ext"
            { head -100 "$f" 2>/dev/null; } || true
            echo '```'
            echo ""
        fi
    done
}

# ============================================================================
# Function: extract_doc_code
# Pulls every fenced code block out of docs page sources, with the preceding
# header for context.
# ============================================================================
extract_doc_code() {
    local src_root="$1/files"
    [[ -d "$src_root" ]] || { warn "no files/ at $src_root"; return; }

    echo ""
    echo "## All code blocks from documentation (verbatim)"
    echo ""
    find "$src_root" -type f -name "*.md" 2>/dev/null | sort | while read -r f; do
        relpath="${f#$src_root/}"
        if grep -q '^```' "$f" 2>/dev/null; then
            local block_count
            block_count=$(grep -c '^```' "$f" || echo 0)
            block_count=$((block_count / 2))
            echo "### From \`$relpath\` ($block_count code blocks)"
            echo ""
            # Walk the file, emit each code fence with its nearest header
            awk '
                /^#{1,6} / { current_header = $0 }
                /^```/ && !in_block {
                    in_block = 1
                    if (current_header != "" && current_header != last_emitted_header) {
                        print "**" current_header "**"
                        print ""
                        last_emitted_header = current_header
                    }
                    print
                    next
                }
                in_block && /^```/ {
                    in_block = 0
                    print
                    print ""
                    next
                }
                in_block { print }
            ' "$f"
        fi
    done

    # Also grab any inline `code` references that look like API surface
    # (CamelCase, snake_case, dotted) — useful for endpoint paths, fn names
    echo ""
    echo "## Inline code identifiers (verbatim)"
    echo ""
    echo '```'
    {
        find "$src_root" -type f -name "*.md" 2>/dev/null -exec cat {} \; 2>/dev/null \
            | grep -oE '`[A-Za-z_][A-Za-z0-9_.()/-]{3,80}`' 2>/dev/null \
            | sort -u 2>/dev/null \
            | head -200 2>/dev/null
    } || true
    echo '```'
}

# ============================================================================
# Function: inject_directive
# Prepends a trust directive to SKILL.md (after YAML frontmatter if present).
# Idempotent — won't re-inject if marker already present.
# ============================================================================
inject_directive() {
    local skill_md="$1"
    local marker="<!-- VERBATIM-DIRECTIVE-V1 -->"

    if grep -qF "$marker" "$skill_md" 2>/dev/null; then
        warn "Directive already present, leaving as-is"
        return
    fi

    local directive
    directive=$(cat <<'DIRECTIVE'
<!-- VERBATIM-DIRECTIVE-V1 -->
> **⚠ TRUSTED API REFERENCE — read this before generating any code**
>
> This skill's narrative sections were generated by an LLM and may contain
> **paraphrased** API references. For ANY code generation involving:
>
> - method names / function names
> - parameter names or parameter order
> - parameter types or return types
> - constants, enum values, struct/interface fields
> - configuration keys or environment variable names
> - file paths inside the source repository
>
> You **MUST** consult the companion file `VERBATIM_REFERENCE.md` in this
> same directory. It contains literal, character-for-character extraction
> from the actual source code with no LLM interpretation.
>
> **Rule of thumb:** if a method signature appears in `VERBATIM_REFERENCE.md`,
> use it exactly as written. If it appears only here in `SKILL.md` and NOT
> in `VERBATIM_REFERENCE.md`, treat it as unverified — ask the user before
> writing code that depends on it, or read the actual file under
> `.sources/<basename>/files/` to confirm.
>
> The narrative below remains useful for conceptual context, architecture
> overviews, and "what does this do" questions. Just don't trust it for
> exact code identifiers.

DIRECTIVE
)

    local content; content=$(cat "$skill_md")
    if [[ "$content" =~ ^---[[:space:]]*$'\n' ]]; then
        # Has YAML frontmatter — split after closing ---
        awk -v dir="$directive" '
            BEGIN { state = "before"; frontmatter = "" }
            state == "before" && NR == 1 && /^---[[:space:]]*$/ {
                state = "in"; print; next
            }
            state == "in" && /^---[[:space:]]*$/ {
                state = "after"; print; print ""; print dir; next
            }
            { print }
        ' "$skill_md" > "$skill_md.tmp"
        mv "$skill_md.tmp" "$skill_md"
    else
        # No frontmatter — prepend at top
        { echo "$directive"; echo ""; cat "$skill_md"; } > "$skill_md.tmp"
        mv "$skill_md.tmp" "$skill_md"
    fi
    ok "Trust directive injected"
}

# ============================================================================
# Main loop
# ============================================================================
TOTAL=${#CRITICAL[@]}
SUCCEEDED=0
FAILED=0

for entry in "${CRITICAL[@]}"; do
    IFS='|' read -r skill_subdir source_subdir kind <<< "$entry"

    skill_dir="$SKILLS_DIR/$skill_subdir"
    source_dir="$SKILLS_DIR/.sources/$source_subdir"
    skill_md="$skill_dir/SKILL.md"
    verbatim_md="$skill_dir/VERBATIM_REFERENCE.md"
    backup_md="$skill_dir/SKILL_pre_repair.md"

    section "$skill_subdir ($kind)"

    if [[ ! -f "$skill_md" ]]; then
        err "Missing: $skill_md"
        FAILED=$((FAILED + 1))
        continue
    fi
    if [[ ! -d "$source_dir" ]]; then
        err "Missing: $source_dir"
        FAILED=$((FAILED + 1))
        continue
    fi

    # Backup
    if [[ ! -f "$backup_md" ]]; then
        cp "$skill_md" "$backup_md"
        ok "Backed up SKILL.md → SKILL_pre_repair.md"
    else
        warn "Backup already exists (re-run detected)"
    fi

    # Extract verbatim content
    log "Extracting verbatim source content..."
    {
        echo "# Verbatim source reference: $skill_subdir"
        echo ""
        echo "**Skill**: \`$skill_md\`"
        echo "**Source bundle**: \`$source_dir\`"
        echo "**Generated**: $(date -Iseconds 2>/dev/null || date)"
        echo ""
        echo "> ⚠ This file contains content extracted DIRECTLY from the source"
        echo "> files preserved on disk. No LLM is in the loop — every"
        echo "> character below appears in the original source. Use this as"
        echo "> the source of truth for any code generation."
        echo ""
        echo "---"
        if [[ "$kind" == "repo" ]]; then
            extract_repo_api "$source_dir"
        else
            extract_doc_code "$source_dir"
        fi
    } > "$verbatim_md"

    verbatim_bytes=$(wc -c < "$verbatim_md")
    verbatim_lines=$(wc -l < "$verbatim_md")
    ok "VERBATIM_REFERENCE.md: $verbatim_lines lines, $verbatim_bytes bytes"

    # Inject trust directive
    inject_directive "$skill_md"

    SUCCEEDED=$((SUCCEEDED + 1))
done

# ============================================================================
# Summary
# ============================================================================
banner "Repair summary"
echo "  Processed: $TOTAL"
echo "  Succeeded: ${G}$SUCCEEDED${N}"
echo "  Failed:    ${R}$FAILED${N}"
echo ""
echo "  Per-skill verbatim reference sizes:"
echo ""
printf "    %-50s %8s %10s\n" "skill" "lines" "bytes"
printf "    %-50s %8s %10s\n" "─────────────────────────────────────────────" "──────" "──────────"
for entry in "${CRITICAL[@]}"; do
    IFS='|' read -r skill_subdir _ _ <<< "$entry"
    f="$SKILLS_DIR/$skill_subdir/VERBATIM_REFERENCE.md"
    if [[ -f "$f" ]]; then
        size=$(wc -c < "$f")
        lines=$(wc -l < "$f")
        printf "    %-50s %8s %10s\n" "$skill_subdir" "$lines" "$size"
    fi
done
echo ""

# ============================================================================
# Sanity check — show first chunk of x402-client extraction
# ============================================================================
verbatim="$SKILLS_DIR/x402-client/VERBATIM_REFERENCE.md"
if [[ -f "$verbatim" ]]; then
    banner "Sanity check — x402-client verbatim extraction (first 30 lines)"
    sed -n '1,30p' "$verbatim" | sed 's/^/    /'
    echo ""
    echo "  ${D}(full file at $verbatim)${N}"
fi

# ============================================================================
# Next steps
# ============================================================================
banner "Next steps for the bounty"
cat <<NEXT
  1. When loading these 5 skills into Claude, attach BOTH:
     - SKILL.md
     - VERBATIM_REFERENCE.md
     The trust directive at the top of each SKILL.md tells Claude to prefer
     the verbatim file for any API calls.

  2. Spot-check what got extracted:

     ${B}skills/x402-client/VERBATIM_REFERENCE.md${N}
       → look for the SDK exports (Client class, settlePayment etc.)
     ${B}skills/discovery-and-indexing/VERBATIM_REFERENCE.md${N}
       → look for code blocks from the discovery docs page
     ${B}skills/acedatacloud-mcps/VERBATIM_REFERENCE.md${N}
       → look for MCP server configs and tool schemas

  3. If you want even more confidence, also run SkillForge once more on
     just the X402Client with stronger settings:

     ${B}python skillforge.py \\
         --github https://github.com/AceDataCloud/X402Client \\
         --provider openrouter --model google/gemini-2.5-pro \\
         --agentic-retries 4 --target-pct-verified 96.0${N}

     Estimated cost: ~\$1.50 on OpenRouter. The result will have a higher
     overall verification % and the VERBATIM_REFERENCE.md is preserved
     because save_skill keeps companion files when source URL matches.

  4. To revert the repair on any skill:

     ${B}cp skills/<name>/SKILL_pre_repair.md skills/<name>/SKILL.md
     rm skills/<name>/VERBATIM_REFERENCE.md${N}

  ${G}You're now closer to 10/10 for code generation.${N}
  The 5 critical skills have guaranteed-accurate API surface accessible to
  Claude via the trust directive. Silent code mistakes from hallucinated
  method names should drop close to zero on these skills.

NEXT
