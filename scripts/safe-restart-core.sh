#!/bin/bash
# scripts/safe-restart-core.sh — restart the sutando-core CLI from INSIDE the
# running core session, to apply config changes (e.g. switch model).
#
# Why this exists
# ---------------
# scripts/start-cli.sh --restart does `tmux kill-session` then relaunch. Its own
# header warns: --restart MUST NOT be invoked from inside sutando-core, because
# kill-session terminates the running agent mid-task — so the relaunch never
# runs and the core just dies. That makes "agent, restart yourself with a new
# model" impossible through start-cli.sh alone.
#
# This script closes that gap with a DETACHED hand-off: it (1) persists the
# requested config into .env so the change survives, then (2) spawns a
# background helper that is immune to the SIGHUP tmux sends on kill-session
# (via nohup + disown) and reparents to launchd. The helper waits a short delay
# — long enough for the calling agent to finish writing its reply so the bridge
# delivers it — then calls `start-cli.sh --restart` with the new config in its
# environment. Because the helper lives outside the tmux pane's process tree,
# killing the current agent does NOT kill the helper; it relaunches the core
# cleanly on the new model.
#
# Usage:
#   bash scripts/safe-restart-core.sh --model <model>       # switch model + restart
#   bash scripts/safe-restart-core.sh --set KEY=VALUE ...   # set arbitrary .env config + restart
#   bash scripts/safe-restart-core.sh --model opus --set SUTANDO_OBS_ENDPOINT=...
#   bash scripts/safe-restart-core.sh --delay 8 --model sonnet
#   bash scripts/safe-restart-core.sh --dry-run --model opus   # print plan, don't restart
#
# Flags:
#   --model <m>     sugar for --set SUTANDO_CORE_MODEL=<m> (the value start-cli.sh
#                   passes to `claude --model`). Empty/"default"/"inherit" CLEARS
#                   the pin so the core inherits the global model.
#   --set KEY=VAL   upsert an env var into .env (repeatable). KEY must match
#                   [A-Z_][A-Z0-9_]*; VAL must be single-line.
#   --delay N       seconds the detached helper waits before restarting (default 5).
#   --dry-run       persist nothing, spawn nothing; just print what would happen.
#   --stop-only     don't relaunch after killing (passes through to start-cli.sh).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO/.env"
DELAY=5
DRY_RUN=0
STOP_ONLY=0
declare -a SETS=()

die() { echo "safe-restart-core: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --model)
      [ $# -ge 2 ] || die "--model needs a value"
      SETS+=("SUTANDO_CORE_MODEL=$2"); shift 2 ;;
    --set)
      [ $# -ge 2 ] || die "--set needs KEY=VALUE"
      SETS+=("$2"); shift 2 ;;
    --delay)
      [ $# -ge 2 ] || die "--delay needs a value"
      case "$2" in ''|*[!0-9]*) die "--delay must be a non-negative integer" ;; esac
      DELAY="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --stop-only) STOP_ONLY=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

[ "${#SETS[@]}" -gt 0 ] || die "nothing to do — pass --model or --set (or --stop-only with a config)"

# Validate every KEY=VALUE up front so we never partially write .env.
for kv in "${SETS[@]}"; do
  key="${kv%%=*}"
  val="${kv#*=}"
  [ "$key" != "$kv" ] || die "malformed --set (need KEY=VALUE): $kv"
  [[ "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]] || die "invalid env key '$key' (must match [A-Z_][A-Z0-9_]*)"
  case "$val" in
    *$'\n'*) die "value for $key must be single-line" ;;
  esac
done

# Model normalization: treat empty/default/inherit as "clear the pin".
model_for_env=""
model_pin_present=0
for kv in "${SETS[@]}"; do
  if [ "${kv%%=*}" = "SUTANDO_CORE_MODEL" ]; then
    model_pin_present=1
    v="${kv#*=}"
    case "$v" in default|inherit|"") model_for_env="" ;; *) model_for_env="$v" ;; esac
  fi
done

echo "safe-restart-core: repo=$REPO"
echo "  config changes to persist in .env:"
for kv in "${SETS[@]}"; do
  key="${kv%%=*}"; val="${kv#*=}"
  if [ "$key" = "SUTANDO_CORE_MODEL" ] && [ -z "$model_for_env" ]; then
    echo "    - $key: (cleared → inherit global model)"
  else
    echo "    - $key=$val"
  fi
done
echo "  restart: start-cli.sh --restart$([ $STOP_ONLY -eq 1 ] && echo ' (stop-only)') after ${DELAY}s (detached)"

# ---- persist config into .env (atomic, idempotent upsert) -------------------
upsert_env() {
  ENV_FILE="$ENV_FILE" python3 - "$@" <<'PY'
import os, sys, tempfile
env = os.environ["ENV_FILE"]
pairs = []
for a in sys.argv[1:]:
    k, _, v = a.partition("=")
    pairs.append((k, v))
lines = []
if os.path.exists(env):
    with open(env) as f:
        lines = f.read().splitlines()
seen = set()
out = []
keys = {k for k, _ in pairs}
def render(k, v):
    return f"{k}={v}"
for ln in lines:
    stripped = ln.lstrip()
    key = None
    if stripped and not stripped.startswith("#") and "=" in stripped:
        key = stripped.split("=", 1)[0].strip()
    if key in keys:
        if key not in seen:
            v = dict(pairs)[key]
            out.append(render(key, v)); seen.add(key)
        # drop later duplicate assignments of the same key
        continue
    out.append(ln)
for k, v in pairs:
    if k not in seen:
        out.append(render(k, v)); seen.add(k)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(env) or ".", prefix=".env.")
with os.fdopen(fd, "w") as f:
    f.write("\n".join(out) + "\n")
os.replace(tmp, env)
PY
}

if [ $DRY_RUN -eq 1 ]; then
  echo "  [dry-run] no .env write, no restart spawned."
  exit 0
fi

# Persist. For a cleared model pin, write the empty value (start-cli.sh treats
# empty SUTANDO_CORE_MODEL as "no --model flag").
declare -a persist=()
for kv in "${SETS[@]}"; do
  key="${kv%%=*}"
  if [ "$key" = "SUTANDO_CORE_MODEL" ]; then
    persist+=("SUTANDO_CORE_MODEL=$model_for_env")
  else
    persist+=("$kv")
  fi
done
upsert_env "${persist[@]}"
echo "  ✓ .env updated"

# ---- detached restart hand-off ----------------------------------------------
# nohup + disown → immune to the SIGHUP tmux delivers on kill-session, and
# reparented to launchd once this shell exits. The model is passed through the
# helper's ENV directly (not relying on .env being sourced by start-cli.sh),
# so the immediate relaunch honors it even though start-cli.sh reads only the
# environment. The .env write above makes it durable for future full startups.
LOG="$REPO/logs/safe-restart-core.log"
mkdir -p "$REPO/logs"
restart_arg="--restart"
[ $STOP_ONLY -eq 1 ] && restart_arg="--stop-only"

# Build the env-prefix for the helper: only SUTANDO_CORE_MODEL matters to
# start-cli.sh's launch flags; other --set keys are already in .env for the
# next startup.sh. Pass model explicitly when pinned.
if [ $model_pin_present -eq 1 ] && [ -n "$model_for_env" ]; then
  nohup bash -c "sleep $DELAY; exec env SUTANDO_CORE_MODEL='$model_for_env' bash '$REPO/scripts/start-cli.sh' $restart_arg" \
    >>"$LOG" 2>&1 &
else
  nohup bash -c "sleep $DELAY; exec bash '$REPO/scripts/start-cli.sh' $restart_arg" \
    >>"$LOG" 2>&1 &
fi
disown || true

echo "  ✓ restart scheduled (detached, pid $!) — core will bounce in ~${DELAY}s"
echo "  log: $LOG"
