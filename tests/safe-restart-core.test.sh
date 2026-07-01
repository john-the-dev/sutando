#!/bin/bash
# tests/safe-restart-core.test.sh — behavioral tests for scripts/safe-restart-core.sh
#
# Verifies the two things the script is responsible for WITHOUT ever restarting
# a real core:
#   1. .env config upsert is atomic + idempotent (append new key, replace in
#      place, no duplicate lines, clear-a-pin).
#   2. the detached restart hand-off fires start-cli.sh with the right arg —
#      exercised against a harmless stub start-cli.sh in a temp REPO.
#
# The script derives REPO from its own location ($(dirname $0)/..), so we run it
# from a temp REPO layout (tmp/scripts/{safe-restart-core.sh,start-cli.sh}) whose
# start-cli.sh is a stub that records its args instead of touching tmux.

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REAL_SCRIPT="$HERE/../scripts/safe-restart-core.sh"
[ -f "$REAL_SCRIPT" ] || { echo "FAIL: cannot find $REAL_SCRIPT"; exit 1; }

TMP="$(mktemp -d -t safe-restart-test.XXXXXX)"
cleanup() { pkill -f "$TMP" 2>/dev/null; rm -rf "$TMP"; }
trap cleanup EXIT

mkdir -p "$TMP/scripts" "$TMP/logs"
cp "$REAL_SCRIPT" "$TMP/scripts/safe-restart-core.sh"
chmod +x "$TMP/scripts/safe-restart-core.sh"
# Stub start-cli.sh: record args + the model env it was launched with, then exit.
cat > "$TMP/scripts/start-cli.sh" <<'STUB'
#!/bin/bash
echo "args=$*" >> "$(dirname "$0")/../restart-invoked"
echo "model=${SUTANDO_CORE_MODEL:-<unset>}" >> "$(dirname "$0")/../restart-invoked"
STUB
chmod +x "$TMP/scripts/start-cli.sh"

SR="$TMP/scripts/safe-restart-core.sh"
ENV="$TMP/.env"
MARK="$TMP/restart-invoked"

pass=0; fail=0
ok() { echo "  ok: $1"; pass=$((pass+1)); }
no() { echo "  FAIL: $1"; fail=$((fail+1)); }

reset() { rm -f "$ENV" "$MARK"; pkill -f "$TMP" 2>/dev/null; sleep 0.05; }

# --- Case A: --dry-run persists nothing, spawns nothing ----------------------
reset
printf 'FOO=bar\n' > "$ENV"
out="$(bash "$SR" --dry-run --model opus 2>&1)"; rc=$?
[ $rc -eq 0 ] && grep -q "dry-run" <<<"$out" && ok "dry-run exits 0 and says dry-run" || no "dry-run exit/msg (rc=$rc)"
grep -q "^FOO=bar$" "$ENV" && ! grep -q "SUTANDO_CORE_MODEL" "$ENV" && ok "dry-run left .env untouched" || no "dry-run mutated .env"
[ ! -f "$MARK" ] && ok "dry-run spawned no restart" || no "dry-run spawned a restart"

# --- Case B: append a new key, preserve existing --------------------------------
reset
printf 'FOO=bar\n' > "$ENV"
bash "$SR" --model opus --delay 30 >/dev/null 2>&1
grep -q "^FOO=bar$" "$ENV" && ok "existing key preserved" || no "existing key lost"
grep -q "^SUTANDO_CORE_MODEL=opus$" "$ENV" && ok "new model key appended" || no "model key not appended"

# --- Case C: replace in place, no duplicate line -----------------------------
reset
printf 'SUTANDO_CORE_MODEL=old\nOTHER=1\n' > "$ENV"
bash "$SR" --model new --delay 30 >/dev/null 2>&1
n="$(grep -c '^SUTANDO_CORE_MODEL=' "$ENV")"
[ "$n" -eq 1 ] && ok "exactly one model line after update" || no "duplicate model lines ($n)"
grep -q "^SUTANDO_CORE_MODEL=new$" "$ENV" && ok "model updated in place" || no "model not updated in place"
grep -q "^OTHER=1$" "$ENV" && ok "unrelated key preserved on in-place update" || no "unrelated key lost"

# --- Case D: restart actually fires with --restart + model env ---------------
reset
: > "$ENV"
bash "$SR" --model opus --delay 1 >/dev/null 2>&1
sleep 2
if [ -f "$MARK" ] && grep -q -- "--restart" "$MARK"; then ok "detached helper invoked start-cli.sh --restart"; else no "restart did not fire"; fi
grep -q "model=opus" "$MARK" 2>/dev/null && ok "restart carried SUTANDO_CORE_MODEL=opus in env" || no "restart missing model env"

# --- Case E: clear the pin (default) writes empty value ----------------------
reset
printf 'SUTANDO_CORE_MODEL=opus\n' > "$ENV"
bash "$SR" --model default --delay 30 >/dev/null 2>&1
grep -q "^SUTANDO_CORE_MODEL=$" "$ENV" && ok "model pin cleared to empty" || no "pin not cleared ($(grep SUTANDO_CORE_MODEL "$ENV"))"

# --- Case F: reject invalid env key ------------------------------------------
reset
bash "$SR" --set "bad key=1" --delay 30 >/dev/null 2>&1; rc=$?
[ $rc -ne 0 ] && ok "invalid key rejected (rc=$rc)" || no "invalid key accepted"
[ ! -f "$MARK" ] && ok "invalid key spawned no restart" || no "invalid key spawned restart"

# --- Case G: --stop-only passes through ---------------------------------------
reset
: > "$ENV"
bash "$SR" --stop-only --set "X=1" --delay 1 >/dev/null 2>&1
sleep 2
grep -q -- "--stop-only" "$MARK" 2>/dev/null && ok "stop-only passed through to start-cli.sh" || no "stop-only not passed"

# --- Case H: no-op args error out --------------------------------------------
reset
bash "$SR" --delay 5 >/dev/null 2>&1; rc=$?
[ $rc -ne 0 ] && ok "no config args rejected" || no "no-op accepted"

echo
echo "safe-restart-core: $pass passed, $fail failed"
[ $fail -eq 0 ]
