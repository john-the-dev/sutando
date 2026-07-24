#!/bin/bash
# self-upgrade — the mechanical half of a safe Sutando self-upgrade.
#
# Pulls the repo to latest and restarts background services WITHOUT bricking
# the running core session. Encodes the hard-won 2026-07-20 lesson: never run
# `src/restart.sh` inline from the core session — it ends with
# `exec bash src/startup.sh`, and startup.sh runs FOREGROUND work (a Swift
# rebuild of ax-read/Sutando.app, and it foreground-holds the credential-proxy)
# so an inline call never returns and the upgrade "sticks". Handing it to a
# durable tmux service session keeps the caller responsive and survives
# executors that reap ordinary nohup children when a command finishes.
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
SERVICE_SESSION="sutando-services"
DONE_MARKER="/tmp/sutando-self-upgrade-restart.done"
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

# A restart is only safe if its owner outlives this executor. Preflight the
# durable handoff before pulling so a host without tmux is left untouched.
TMUX_BIN=""
TMUX_SOCKET=""
if [ "$DO_RESTART" = "1" ]; then
  TMUX_BIN="$(command -v tmux 2>/dev/null || true)"
  if [ -z "$TMUX_BIN" ]; then
    echo "self-upgrade: ABORT — tmux is required for a restart that survives the task executor. Install tmux or use --no-restart." >&2
    exit 2
  fi
  TMUX_SOCKET="$(bash "$REPO/scripts/sutando-config.sh" tmux-socket 2>/dev/null || true)"
  if [ -z "$TMUX_SOCKET" ]; then
    echo "self-upgrade: ABORT — could not resolve the Sutando tmux socket." >&2
    exit 2
  fi
  if "$TMUX_BIN" -S "$TMUX_SOCKET" has-session -t "=$SERVICE_SESSION" 2>/dev/null; then
    EXISTING_COMMAND="$("$TMUX_BIN" -S "$TMUX_SOCKET" list-panes -t "=$SERVICE_SESSION" -F '#{pane_current_command}' 2>/dev/null | head -1 || true)"
    EXISTING_PID="$("$TMUX_BIN" -S "$TMUX_SOCKET" list-panes -t "=$SERVICE_SESSION" -F '#{pane_pid}' 2>/dev/null | head -1 || true)"
    COMPLETED_PID="$(cat "$DONE_MARKER" 2>/dev/null || true)"
    if [ -z "$EXISTING_PID" ] || [ "$COMPLETED_PID" != "$EXISTING_PID" ]; then
      echo "self-upgrade: ABORT — $SERVICE_SESSION is still active ($EXISTING_COMMAND). Wait for that restart to finish." >&2
      exit 2
    fi
  fi
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

# Capture a timestamp before launching the restart. The always-on core heartbeat
# should advance past it even on hosts where no optional channel bridge is
# configured.
WORKSPACE="$(bash "$REPO/scripts/sutando-config.sh" workspace 2>/dev/null || true)"
VERIFY_STAMP="$(mktemp -t sutando-upgrade-verify.XXXXXX 2>/dev/null || true)"

# 5. THE LOAD-BEARING STEP — hand the restart to the core's durable tmux
#    server. Plain `nohup ... &` is not enough: Codex executors may reap that
#    child when the command returns. restart.sh explicitly does NOT touch the
#    core agent; look for "sutando-core already running".
LOG="/tmp/sutando-self-upgrade-restart.log"
if "$TMUX_BIN" -S "$TMUX_SOCKET" has-session -t "=$SERVICE_SESSION" 2>/dev/null; then
  "$TMUX_BIN" -S "$TMUX_SOCKET" kill-session -t "=$SERVICE_SESSION" ||
    { echo "self-upgrade: could not clear completed $SERVICE_SESSION session" >&2; exit 2; }
fi
: > "$LOG" || { echo "self-upgrade: cannot write restart log: $LOG" >&2; exit 2; }
: > "$DONE_MARKER" || { echo "self-upgrade: cannot write completion marker: $DONE_MARKER" >&2; exit 2; }
printf -v RESTART_COMMAND \
  'cd %q && bash %q >> %q 2>&1; rc=$?; printf "%%s\n" "$$" > %q; printf "self-upgrade: restart exit=%%s\n" "$rc" >> %q; exec sleep 2147483647' \
  "$REPO" "$REPO/src/restart.sh" "$LOG" "$DONE_MARKER" "$LOG"
"$TMUX_BIN" -S "$TMUX_SOCKET" new-session -d -s "$SERVICE_SESSION" "$RESTART_COMMAND" ||
  { echo "self-upgrade: durable restart handoff failed" >&2; exit 2; }
"$TMUX_BIN" -S "$TMUX_SOCKET" has-session -t "=$SERVICE_SESSION" 2>/dev/null ||
  { echo "self-upgrade: durable restart session disappeared immediately; check $LOG" >&2; exit 2; }
echo "self-upgrade: restart.sh handed to durable tmux session $SERVICE_SESSION (log: $LOG)"

# 6. Verify the core heartbeat advances while services restart (best-effort,
#    bounded). Do not key this on a specific channel bridge: every bridge is
#    optional and may be intentionally unconfigured on this host.
#    SUTANDO_UPGRADE_VERIFY_TRIES caps the wait (each try = ~2s); default 45.
CORE_ALIVE_DIR="$WORKSPACE/state/cores"
heartbeat_advanced() {
  [ -n "$VERIFY_STAMP" ] && [ -d "$CORE_ALIVE_DIR" ] &&
    find "$CORE_ALIVE_DIR" -type f -name '*.alive' -newer "$VERIFY_STAMP" -print -quit 2>/dev/null | grep -q .
}
for _ in $(seq 1 "${SUTANDO_UPGRADE_VERIFY_TRIES:-45}"); do
  if heartbeat_advanced; then break; fi
  sleep 2
done
if heartbeat_advanced; then
  echo "self-upgrade: ✓ core heartbeat advancing while services restart"
else
  echo "self-upgrade: ⚠ core heartbeat has not advanced yet — startup.sh may still be building; check $LOG"
fi
[ -z "$VERIFY_STAMP" ] || rm -f "$VERIFY_STAMP"

cat <<'NEXT'
self-upgrade: mechanical steps done. AGENT MUST NOW:
  1. Re-arm the task watcher via the Monitor tool (restart.sh killed `watch-tasks`).
  2. Run `python3 src/health-check.py` and confirm all-green.
  3. Do NOT hand-kill the lingering startup.sh — it foreground-holds the
     credential-proxy; killing it drops :7846 (see feedback: untidy != broken).
NEXT
exit 0
