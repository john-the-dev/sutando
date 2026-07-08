#!/usr/bin/env bash
# Idempotently wire the /schedule-crons SessionStart hook into the project-level
# .claude/settings.json. Called by startup.sh and safe to run standalone.
#
# The hook injects additionalContext reminding the core agent to run
# /schedule-crons at the start of every session (including post-compaction
# restarts), so all 16 session-only crons are always registered.

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SETTINGS="$REPO/.claude/settings.json"
HINT_SCRIPT="$REPO/src/schedule-crons-session-hint.sh"
HOOK_CMD="bash \"$HINT_SCRIPT\""

# Ensure .claude dir exists
mkdir -p "$REPO/.claude"

# Create settings.json with empty hooks structure if missing
if [ ! -f "$SETTINGS" ]; then
  echo '{"hooks":{}}' > "$SETTINGS"
fi

# Use Python to do the idempotent merge (avoids jq dependency)
python3 /dev/stdin "$SETTINGS" "$HOOK_CMD" <<'PYEOF'
import json, sys

settings_path = sys.argv[1]
hook_cmd = sys.argv[2]

with open(settings_path) as f:
    settings = json.load(f)

hooks = settings.setdefault("hooks", {})
session_start = hooks.setdefault("SessionStart", [])

# Check if our hook command is already present in any entry
for entry in session_start:
    for h in entry.get("hooks", []):
        if h.get("command", "") == hook_cmd:
            print("  ✓ schedule-crons SessionStart hook (already installed)")
            sys.exit(0)

# Not found — prepend our entry so it fires first
session_start.insert(0, {
    "matcher": "",
    "hooks": [{"type": "command", "command": hook_cmd}]
})

with open(settings_path, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")

print("  ✓ schedule-crons SessionStart hook (installed)")
PYEOF
