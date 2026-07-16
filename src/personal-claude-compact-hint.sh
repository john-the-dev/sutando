#!/usr/bin/env bash
# SessionStart(compact) hook — re-inject PERSONAL_CLAUDE.md after context
# compaction.
#
# CLAUDE.md and the memory index are part of the system prompt, so they
# survive a /compact or a long-session summary. PERSONAL_CLAUDE.md is NOT:
# it only enters context via an explicit Read at session start, and that
# Read lives in the conversation — which is exactly what compaction
# summarizes away. On long sessions the agent silently loses its per-user
# rules and re-makes mistakes the file explicitly documents.
#
# This hook closes the gap: registered under the SessionStart "compact"
# matcher (see scripts/install-personal-claude-hook.sh), it emits the
# resolved PERSONAL_CLAUDE.md as additionalContext so the rules re-enter
# the fresh post-compaction window. Startup/resume are NOT matched — the
# session-start Read (CLAUDE.md "Personal overrides") already covers them.
#
# Resolution delegates to src/util_paths.py:personal_path() — the same
# per-host-first order every other reader uses (hosts/<host>/ → legacy
# memory-dir → workspace root). No inline `hostname | sed` here (#1745).
#
# Token control (opt-in): if the file contains the marker line
#   <!-- COMPACT-CORE-END -->
# only the content ABOVE the marker is injected, plus a one-line pointer to
# the full file. This lets a large PERSONAL_CLAUDE.md (the reporting install
# is ~12k tokens) keep a small always-on core without splitting into two
# files. No marker → the whole file is injected.
#
# Best-effort: any failure exits 0 so the hook never blocks a session start.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"

python3 - "$REPO" <<'PYEOF' || exit 0
import json
import sys

repo = sys.argv[1]
sys.path.insert(0, repo)

try:
    from src.util_paths import personal_path
except ImportError:
    sys.exit(0)

MARKER = "<!-- COMPACT-CORE-END -->"

p = personal_path("PERSONAL_CLAUDE.md")
if not p.exists():
    sys.exit(0)  # no personal overrides on this install — stay silent

try:
    content = p.read_text()
except OSError:
    sys.exit(0)

if MARKER in content:
    core = content.split(MARKER, 1)[0].rstrip()
    body = (
        f"{core}\n\n"
        f"(Injected: always-on core of {p}. The reference tail below the "
        f"{MARKER} marker was omitted to save tokens — Read the file when "
        f"you need it.)"
    )
else:
    body = content

context = (
    "PERSONAL_CLAUDE.md (re-injected after context compaction — these "
    "per-user rules override/extend CLAUDE.md and were lost from the "
    f"compacted context; source: {p}):\n\n{body}"
)

print(json.dumps({
    "hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": context,
    }
}))
PYEOF
