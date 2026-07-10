#!/bin/bash
# Wrapper for the launchd-managed ag2.space gateway bridge
# (src/remote-gateway-bridge.py).
#
# Why a wrapper (not a bare ProgramArguments python3 call): the bridge needs
# REMOTE_TASK_TOKEN (legacy AG2_REMOTE_TOKEN), which lives in the ag2space
# channel .env — NOT in the environment launchd hands the job. launchd doesn't
# source shell profiles or .env files, so we resolve + load the channel .env
# here, map the legacy token names to the ones the bridge reads, then exec the
# bridge. Mirrors the credential-proxy-wrapper.sh pattern.
#
# Called by com.sutando.gateway-bridge.plist as the ProgramArguments entry so
# the launchd job tracks THIS pid and KeepAlive restarts the bridge on death —
# the fix for the 2026-07-10 incident where the bridge died and stayed dead for
# 3 days with nothing restarting it (mobile messages stranded in the cloud).

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

# Heal python3 onto PATH. launchd's plist PATH is a fixed guess and may miss the
# interpreter the bridge was validated with; resolve one explicitly, first match
# wins, and prepend its dir so `python3` in the exec below is found.
for _py_cand in \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /usr/bin/python3
do
    [ -x "$_py_cand" ] || continue
    _py_dir="$(dirname "$_py_cand")"
    case ":$PATH:" in *":$_py_dir:"*) ;; *) PATH="$_py_dir:$PATH"; export PATH ;; esac
    break
done

# Resolve + load the ag2space channel .env (holds REMOTE_TASK_TOKEN). Honor
# $CLAUDE_CONFIG_DIR if the plist exports it (claude-sutando installs); the
# config helper falls back to ~/.claude otherwise.
if _RELAY_ENV="$(bash "$REPO/scripts/sutando-config.sh" claude-home-path channels/ag2space/.env 2>/dev/null)"; then
    [ -f "$_RELAY_ENV" ] && { set -a; . "$_RELAY_ENV"; set +a; }
fi

# Map legacy AG2_REMOTE_* → REMOTE_TASK_* (the names the bridge reads).
REMOTE_TASK_TOKEN="${REMOTE_TASK_TOKEN:-${AG2_REMOTE_TOKEN:-}}"
REMOTE_TASK_TIER="${REMOTE_TASK_TIER:-${AG2_REMOTE_TIER:-team}}"
export REMOTE_TASK_TOKEN REMOTE_TASK_TIER

# If there's still no token, the bridge would FATAL-exit and KeepAlive would
# crash-loop. Exit 0 quietly instead — the install path only loads this job when
# a token is configured, so reaching here means the token was removed after
# install; don't hammer the system, just stop cleanly (launchd honors the clean
# exit under our KeepAlive.SuccessfulExit=false policy).
if [ -z "$REMOTE_TASK_TOKEN" ]; then
    echo "[gateway-bridge-wrapper] no REMOTE_TASK_TOKEN configured — nothing to run; exiting cleanly." >&2
    exit 0
fi

# Evict any already-running bridge before we exec. The bridge has NO
# single-instance lock, so a leftover bare (nohup) launch or a straggler from a
# prior supervised run would double-poll the gateway and double-process every
# message. Safe to run here: at this point we're still the bash wrapper (argv =
# gateway-bridge-wrapper.sh), so this pattern does not match — and cannot kill —
# our own process; only the python bridge instances match.
pkill -f 'remote-gateway-bridge\.py$' 2>/dev/null || true
sleep 0.3

exec python3 "$REPO/src/remote-gateway-bridge.py"
