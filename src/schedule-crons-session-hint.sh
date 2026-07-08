#!/usr/bin/env bash
# SessionStart hook — reminds the core agent to run /schedule-crons at the
# start of every session (including post-compaction restarts).
#
# Claude Code crons are session-only: they expire when the session ends.
# Without an explicit /schedule-crons invocation, recurring jobs (proactive
# loop, morning briefing, digests, etc.) never fire for that session.
#
# The hook outputs additionalContext that Claude Code injects into the fresh
# context window. Best-effort: any failure exits 0 so the hook never blocks
# a session start.

set -euo pipefail

printf '%s' '{
  "hookSpecificOutput": {
    "hookEventName": "SessionStart",
    "additionalContext": "SUTANDO STARTUP: Run /schedule-crons immediately — crons are session-only and all 16 scheduled jobs (proactive loop, morning briefing, digests, etc.) are unregistered in this fresh session. Also start the streaming task watcher if not running. Do this before any other work."
  }
}'
