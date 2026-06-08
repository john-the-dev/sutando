#!/usr/bin/env bash
# Generate AGENTS.md from CLAUDE.md via systematic substitutions.
#
# Codex CLI and OpenAI-style agents read AGENTS.md as their per-project
# instructions file, the way Claude Code reads CLAUDE.md. The two files are
# identical modulo four substitutions:
#
#   1. "Claude Code default" → "Codex default"   (longer match before shorter)
#   2. "Claude Code"         → "Codex"
#   3. "pgrep -f claude"     → "pgrep -f Codex"
#   4. "CLAUDE.md"           → "AGENTS.md"        (self-references only)
#
# Re-runnable; output is fully reproducible from CLAUDE.md alone.
#
# Usage:
#   bash scripts/agents-md-sync.sh          # regenerate AGENTS.md
#   bash scripts/agents-md-sync.sh --check  # exit 1 if AGENTS.md is stale (CI)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$REPO_ROOT/CLAUDE.md"
dst="$REPO_ROOT/AGENTS.md"

[ -f "$src" ] || { echo "agents-md-sync: CLAUDE.md missing at $src" >&2; exit 1; }

generated="$(sed \
  -e 's/Claude Code default/Codex default/g' \
  -e 's/Claude Code/Codex/g' \
  -e 's/pgrep -f claude/pgrep -f Codex/g' \
  -e 's/CLAUDE\.md/AGENTS.md/g' \
  "$src")"

# Sanity: verify expected markers fired
for marker in 'Codex' 'AGENTS.md'; do
  echo "$generated" | grep -qF "$marker" \
    || { echo "agents-md-sync: expected marker '$marker' missing from output — substitution may have broken" >&2; exit 1; }
done

if [ "${1:-}" = "--check" ]; then
  if [ ! -f "$dst" ]; then
    echo "agents-md-sync: AGENTS.md missing — run 'bash scripts/agents-md-sync.sh' and commit" >&2
    exit 1
  fi
  if ! diff -q <(echo "$generated") "$dst" > /dev/null 2>&1; then
    echo "agents-md-sync: AGENTS.md is stale — run 'bash scripts/agents-md-sync.sh' and commit" >&2
    diff <(echo "$generated") "$dst" | head -20 >&2
    exit 1
  fi
  echo "agents-md-sync: AGENTS.md is up to date"
  exit 0
fi

echo "$generated" > "$dst"
echo "agents-md-sync: AGENTS.md regenerated from CLAUDE.md ($(wc -l < "$dst") lines)"
