#!/bin/bash
# launchd entry point shared by Slack, Discord, and Telegram bridges.

set -euo pipefail

CHANNEL="${1:-}"
case "$CHANNEL" in slack|discord|telegram) ;; *) exit 2 ;; esac

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

ENV_FILE="$(bash "$REPO/scripts/sutando-config.sh" claude-home-path "channels/$CHANNEL/.env" 2>/dev/null || true)"
[ -f "$ENV_FILE" ] && { set -a; . "$ENV_FILE"; set +a; }

case "$CHANNEL" in
  slack) TOKEN="${SLACK_BOT_TOKEN:-}"; MODULE=slack_bolt ;;
  discord) TOKEN="${DISCORD_BOT_TOKEN:-}"; MODULE=discord ;;
  telegram) TOKEN="${TELEGRAM_BOT_TOKEN:-}"; MODULE='' ;;
esac
if [ -z "$TOKEN" ]; then
  # KeepAlive=true is intentionally unconditional: the conditional
  # Crashed/SuccessfulExit dictionary can remain pended instead of respawning
  # after SIGKILL on current macOS. Stay resident without a child when the
  # channel is deconfigured, avoiding both a 10s crash loop and false bridge
  # activity. startup.sh sees no bridge PID and kickstarts this wrapper after
  # credentials return.
  echo "[$CHANNEL-bridge-wrapper] token removed; waiting idle" >&2
  while :; do sleep 300; done
fi

PYTHON="${SUTANDO_CHANNEL_BRIDGE_PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    if [ -n "$MODULE" ]; then
      "$candidate" -c "import $MODULE" >/dev/null 2>&1 || continue
    else
      "$candidate" -c 'import urllib.request as u; u.urlopen("https://api.telegram.org", timeout=8)' >/dev/null 2>&1 || continue
    fi
    PYTHON="$candidate"
    break
  done
fi
if [ -z "$PYTHON" ]; then
  echo "[$CHANNEL-bridge-wrapper] no usable Python interpreter" >&2
  exit 1
fi

WORKSPACE="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
STATE_DIR="$WORKSPACE/state/channel-bridge-supervisor"
mkdir -p "$STATE_DIR" "$WORKSPACE/results"
MARKER="$STATE_DIR/$CHANNEL.started"
emit_restart_alert() {
  NOW="$(date +%s)"
  RESULT="$WORKSPACE/results/proactive-$CHANNEL-bridge-restarted-$NOW.txt"
  echo "[$CHANNEL-bridge-wrapper] previous process exited; automatically restarting" >&2
  printf '%s\n' "⚠️ The $CHANNEL bridge exited and was automatically restarted." > "$RESULT"
  osascript -e "display notification \"The $CHANNEL bridge exited and was automatically restarted.\" with title \"Sutando\"" >/dev/null 2>&1 || true
}
if [ -f "$MARKER" ]; then emit_restart_alert; fi
date +%s > "$MARKER"

# Remove a pre-existing bare process for this channel before exec — but ONLY one
# belonging to THIS checkout. A plain `pkill -f src/<channel>-bridge.py$` matches
# the same bridge launched from ANY checkout on the host, so starting/upgrading
# one install could kill another install's live bridge (CR #2068). evict_own_bridge
# validates each candidate's identity (command path under $REPO, or cwd == $REPO).
# All three bridges also have single-instance protection, but eviction makes the
# launchd ownership transition immediate and deterministic. The helper is sourced
# only if present, so a partial deploy (or a test fixture that copies just this
# wrapper) degrades to no-eviction instead of `set -e`-aborting before the child
# is launched (CR #2068 round 2, qingyun-wu).
_EVICT_HELPER="$REPO/src/launchd/evict-own-bridge.sh"
if [ -f "$_EVICT_HELPER" ]; then
  # shellcheck source=evict-own-bridge.sh
  . "$_EVICT_HELPER"
  evict_own_bridge "$CHANNEL" "$REPO"
fi
sleep 0.3

# Keep this wrapper resident and supervise the bridge as its child. launchd's
# KeepAlive can deliberately defer a repeatedly-killed job as "inefficient";
# owning the bridge child here makes recovery deterministic and also lets us
# alert immediately after the actual channel process exits.
CHILD_PID=''
STOPPING=0
stop_wrapper() {
  STOPPING=1
  [ -z "$CHILD_PID" ] || kill "$CHILD_PID" 2>/dev/null || true
}
trap stop_wrapper TERM INT HUP
RESTART_DELAY="${SUTANDO_CHANNEL_BRIDGE_RESTART_DELAY:-10}"
while [ "$STOPPING" = 0 ]; do
  "$PYTHON" "$REPO/src/$CHANNEL-bridge.py" &
  CHILD_PID=$!
  set +e
  wait "$CHILD_PID"
  set -e
  CHILD_PID=''
  [ "$STOPPING" = 0 ] || break
  emit_restart_alert
  sleep "$RESTART_DELAY" &
  CHILD_PID=$!
  set +e
  wait "$CHILD_PID"
  set -e
  CHILD_PID=''
done
