#!/bin/bash
# Dead-man's-switch ping — the one outbound heartbeat that proves "this Mac is
# awake AND the Sutando core is alive" to an off-machine monitor
# (healthchecks.io or compatible). Missed pings there → the owner gets a
# phone-visible alert even when every local process is dead.
#
# Semantics (empty GETs, no payload — nothing about the system leaves the box):
#   core alive  → GET <url>        (healthy ping; resets the monitor's timer)
#   core down   → GET <url>/fail   (machine awake but core dead → instant alert,
#                                    no grace-period wait)
#   machine asleep/dead → no ping at all → monitor's grace period expires → alert
#
# "Core alive" = <workspace>/state/cores/<host>.alive mtime younger than 90s
# (the cross-host liveness contract from src/core_heartbeat.py).
#
# URL resolution: $HEALTHCHECKS_PING_URL env > vault key HEALTHCHECKS_PING_URL.
# Not configured → silent no-op (exit 0) so the launchd job is safe to install
# before the owner has signed up.
#
# Fail-open by design: THIS script must never be the thing that breaks. Any
# curl/vault/resolution failure logs one stderr line and exits 0.
#
# Test hook: $SUTANDO_DEADMAN_ALIVE_FILE overrides the alive-file path so the
# hermetic suite (tests/dead-mans-switch-ping.test.sh) runs without a real
# workspace.

set -u

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"

# Per-host label — lockstep with `_host_label()` in src/util_paths.py (the
# derivation the heartbeat writer uses for state/cores/<host>.alive). MUST match
# it or the switch pings /fail forever on a host with a label override even
# while the core is healthy. Precedence:
#   1. $SUTANDO_HOST_LABEL (or legacy $SUTANDO_HOST_OVERRIDE)
#   2. macOS `scutil --get LocalHostName` (stable Bonjour name; guards the
#      DHCP-hostname-drift split, 2026-06-22 incident)
#   3. short `hostname`
_host_label() {
    local env="${SUTANDO_HOST_LABEL:-${SUTANDO_HOST_OVERRIDE:-}}"
    if [ -n "$env" ]; then
        printf '%s\n' "$env"
        return
    fi
    local lhn=""
    if command -v scutil >/dev/null 2>&1; then
        lhn="$(scutil --get LocalHostName 2>/dev/null)"
    fi
    if [ -n "$lhn" ]; then
        printf '%s\n' "$lhn"
    else
        hostname | sed 's/\..*//'
    fi
}

URL="${HEALTHCHECKS_PING_URL:-}"
if [ -z "$URL" ]; then
    URL="$(command -v python3 >/dev/null 2>&1 && python3 "$REPO/skills/secret-vault/secret-vault.py" get HEALTHCHECKS_PING_URL 2>/dev/null || true)"
fi
if [ -z "$URL" ]; then
    # Not configured — installed-but-unarmed is a supported state.
    exit 0
fi

ALIVE="${SUTANDO_DEADMAN_ALIVE_FILE:-}"
if [ -z "$ALIVE" ]; then
    WORKSPACE="$(bash "$REPO/scripts/sutando-config.sh" workspace 2>/dev/null)" || WORKSPACE=""
    if [ -z "$WORKSPACE" ]; then
        echo "deadman-ping: workspace resolution failed — reporting core-down (/fail)" >&2
        WORKSPACE=""
    fi
    HOST="$(_host_label)"
    ALIVE="$WORKSPACE/state/cores/$HOST.alive"
fi

# Fresh heartbeat (<90s) = alive. Portable mtime via python3 (stat -f/-c differ
# across BSD/GNU). Missing file or unreadable mtime = down.
suffix="/fail"
if [ -f "$ALIVE" ]; then
    age="$(python3 -c 'import os,sys,time; print(int(time.time() - os.path.getmtime(sys.argv[1])))' "$ALIVE" 2>/dev/null || echo 99999)"
    if [ "$age" -le 90 ] 2>/dev/null; then
        suffix=""
    fi
fi

if ! curl -fsS -m 10 --retry 2 -o /dev/null "$URL$suffix" 2>/dev/null; then
    echo "deadman-ping: curl to monitor endpoint failed (fail-open, exit 0)" >&2
fi
exit 0
