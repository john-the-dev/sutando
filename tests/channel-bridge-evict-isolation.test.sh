#!/bin/bash
# CR #2068: evict_own_bridge must kill only THIS checkout's bare bridge, never an
# identically-named bridge from another checkout on the same host.
#
# Two fake checkouts A and B each run `python3 src/<channel>-bridge.py` (relative,
# cwd = their own repo). Evicting for checkout A must kill A's bridge and leave
# B's alive. Also covers the absolute-path launch. Run: bash <this file> (exit 0/1)
set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER="$REPO_ROOT/src/launchd/evict-own-bridge.sh"
FAILED=0
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "SKIP: python3 not found"; exit 0; }

# shellcheck source=/dev/null
. "$HELPER"

TMP="$(mktemp -d 2>/dev/null || mktemp -d -t evict)"
cleanup() { pkill -P $$ 2>/dev/null || true; rm -rf "$TMP" 2>/dev/null || true; }
trap cleanup EXIT

assert_dead() {  # name  pid  — passes iff the process is gone (evicted)
  if kill -0 "$2" 2>/dev/null; then echo "  FAIL $1 (still alive)"; FAILED=1; else echo "  ok   $1"; fi
}
assert_alive() { # name  pid  — passes iff the process survived
  if kill -0 "$2" 2>/dev/null; then echo "  ok   $1"; else echo "  FAIL $1 (was killed)"; FAILED=1; fi
}

# Build two fake checkouts with an identical relative bridge path. Resolve to
# PHYSICAL paths (mktemp on macOS lives under a /tmp -> /private/tmp symlink).
mk_checkout() {
  mkdir -p "$1/src"
  printf 'import time,sys\ntime.sleep(120)\n' > "$1/src/slack-bridge.py"
}
mk_checkout "$TMP/checkoutA"; mk_checkout "$TMP/checkoutB"
A="$(cd "$TMP/checkoutA" && pwd -P)"; B="$(cd "$TMP/checkoutB" && pwd -P)"

# --- relative launch (as startup.sh does): cwd = the repo -------------------
( cd "$A" && exec "$PY" src/slack-bridge.py ) & PID_A=$!
( cd "$B" && exec "$PY" src/slack-bridge.py ) & PID_B=$!
sleep 0.6  # let them start + settle their cwd

assert_alive "checkout A's bridge started" "$PID_A"
assert_alive "checkout B's bridge started" "$PID_B"

evict_own_bridge slack "$A"
sleep 0.4
assert_dead  "checkout A's bridge (relative) was evicted" "$PID_A"
assert_alive "checkout B's bridge (other checkout) SURVIVED" "$PID_B"

kill "$PID_B" 2>/dev/null || true

# --- absolute launch: cmd path carries the repo ----------------------------
( exec "$PY" "$A/src/slack-bridge.py" ) & PID_A2=$!
( exec "$PY" "$B/src/slack-bridge.py" ) & PID_B2=$!
sleep 0.6
evict_own_bridge slack "$A"
sleep 0.4
assert_dead  "checkout A's bridge (absolute path) was evicted" "$PID_A2"
assert_alive "checkout B's bridge (absolute, other checkout) SURVIVED" "$PID_B2"
kill "$PID_A2" "$PID_B2" 2>/dev/null || true

echo
if [ "$FAILED" -eq 0 ]; then echo "PASS — channel-bridge evict isolation"; exit 0; fi
echo "FAIL — channel-bridge evict isolation"; exit 1
