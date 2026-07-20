#!/bin/bash
# self-upgrade — the mechanical half of a safe Sutando self-upgrade.
#
# Pulls the repo to latest and restarts background services WITHOUT bricking
# the running core session. Encodes the hard-won 2026-07-20 lesson: never run
# `src/restart.sh` inline from the core session — it ends with
# `exec bash src/startup.sh`, and startup.sh runs FOREGROUND work (a Swift
# rebuild of ax-read/Sutando.app, and it foreground-holds the credential-proxy)
# so an inline call never returns and the upgrade "sticks". Running it fully
# detached keeps the caller responsive.
#
# What this script does NOT do (agent-side, handled by SKILL.md):
#   - re-arm the task watcher (restart.sh kills `watch-tasks`; the Monitor-tool
#     watcher is owned by the agent session, not by a shell script)
#   - run the post-upgrade health check / report to the owner
#
# Usage:
#   bash skills/self-upgrade/scripts/upgrade.sh [--remote <name>] [--branch <name>] [--no-restart]
# Exit codes: 0 = upgraded (or already latest); 2 = aborted (dirty tree / not FF-able)

set -uo pipefail

REMOTE="origin"
BRANCH="main"
DO_RESTART=1
while [ $# -gt 0 ]; do
  case "$1" in
    --remote) REMOTE="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --no-restart) DO_RESTART=0; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO" || { echo "self-upgrade: cannot cd to repo root" >&2; exit 2; }

echo "self-upgrade: repo=$REPO  remote=$REMOTE  branch=$BRANCH"

# 1. Clean working tree required — never clobber uncommitted work.
if [ -n "$(git status --porcelain)" ]; then
  echo "self-upgrade: ABORT — working tree is dirty ($(git status --porcelain | wc -l | tr -d ' ') files). Commit or stash first." >&2
  exit 2
fi

# 2. Fetch + measure the gap.
git fetch "$REMOTE" --quiet || { echo "self-upgrade: git fetch failed" >&2; exit 2; }
LOCAL="$(git rev-parse --short HEAD)"
BEHIND="$(git rev-list --count "HEAD..$REMOTE/$BRANCH" 2>/dev/null || echo 0)"
AHEAD="$(git rev-list --count "$REMOTE/$BRANCH..HEAD" 2>/dev/null || echo 0)"
echo "self-upgrade: local=$LOCAL  behind=$BEHIND  ahead=$AHEAD"

if [ "$BEHIND" = "0" ]; then
  echo "self-upgrade: already at latest ($LOCAL). Nothing to pull."
  exit 0
fi
if [ "$AHEAD" != "0" ]; then
  echo "self-upgrade: ABORT — local is $AHEAD commit(s) ahead of $REMOTE/$BRANCH; not a fast-forward. Resolve manually." >&2
  exit 2
fi

# 3. Heads-up if a rebuild is likely needed (dependency/build files changed).
REBUILD="$(git diff --name-only "HEAD..$REMOTE/$BRANCH" | grep -iE 'package.*\.json|package-lock|tsconfig|\.swift$|requirements' || true)"
if [ -n "$REBUILD" ]; then
  echo "self-upgrade: NOTE — dependency/build files changed; a rebuild (npm ci / tsc) may be needed after restart:"
  echo "$REBUILD" | sed 's/^/    /'
fi

# 4. Fast-forward pull — the actual code upgrade.
git pull --ff-only "$REMOTE" "$BRANCH" || { echo "self-upgrade: git pull --ff-only failed" >&2; exit 2; }
NOW="$(git rev-parse --short HEAD)"
echo "self-upgrade: pulled $LOCAL -> $NOW (0 behind)"

if [ "$DO_RESTART" = "0" ]; then
  echo "self-upgrade: --no-restart set; skipping service restart. New code applies on next restart."
  exit 0
fi

# 5. THE LOAD-BEARING STEP — restart services DETACHED so startup.sh's
#    foreground work can't block us. restart.sh explicitly does NOT touch the
#    Claude Code CLI (core agent); look for "sutando-core already running".
LOG="/tmp/sutando-self-upgrade-restart.log"
nohup bash "$REPO/src/restart.sh" > "$LOG" 2>&1 &
disown
echo "self-upgrade: restart.sh launched DETACHED (log: $LOG)"

# 6. Verify a bridge comes back (best-effort, bounded).
#    SUTANDO_UPGRADE_VERIFY_TRIES caps the wait (each try = ~2s); default 45.
for _ in $(seq 1 "${SUTANDO_UPGRADE_VERIFY_TRIES:-45}"); do
  if pgrep -f "$REPO/src/discord-bridge.py" >/dev/null 2>&1; then break; fi
  sleep 2
done
if pgrep -f "$REPO/src/discord-bridge.py" >/dev/null 2>&1; then
  echo "self-upgrade: ✓ services restarting on new code (discord-bridge back up)"
else
  echo "self-upgrade: ⚠ discord-bridge not yet visible — startup.sh may still be building; check $LOG"
fi

cat <<'NEXT'
self-upgrade: mechanical steps done. AGENT MUST NOW:
  1. Re-arm the task watcher via the Monitor tool (restart.sh killed `watch-tasks`).
  2. Run `python3 src/health-check.py` and confirm all-green.
  3. Do NOT hand-kill the lingering startup.sh — it foreground-holds the
     credential-proxy; killing it drops :7846 (see feedback: untidy != broken).
NEXT
exit 0
