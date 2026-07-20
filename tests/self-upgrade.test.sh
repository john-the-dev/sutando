#!/usr/bin/env bash
# Behavioral tests for skills/self-upgrade/scripts/upgrade.sh — the mechanical
# half of the safe self-upgrade. Real git repos + a real bare remote + a stub
# restart.sh; no mocking of the code under test. Exercises the three behaviors
# that actually matter:
#   A. aborts (exit 2) on a dirty working tree — never clobbers uncommitted work
#   B. no-ops (exit 0) when already at latest — nothing to pull
#   C. runs the restart DETACHED — the load-bearing fix. Proven by TIMING: the
#      stub restart.sh blocks for 15s (simulating startup.sh's foreground hang);
#      if upgrade.sh detached it, upgrade.sh returns in a couple seconds; if it
#      ran it inline (the "stuck" bug) it would take ~15s+. We assert it returned
#      fast AND that restart.sh actually ran (marker file).
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
SRC_SCRIPT="$REPO/skills/self-upgrade/scripts/upgrade.sh"
[ -f "$SRC_SCRIPT" ] || { echo "FAIL: upgrade.sh not found at $SRC_SCRIPT" >&2; exit 1; }

TMPROOT="$(mktemp -d)"
trap 'rm -rf "$TMPROOT"; [ -n "${MARKER_SLEEP_PID:-}" ] && kill "$MARKER_SLEEP_PID" 2>/dev/null || true' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

# git identity for the ephemeral fixture repos (CI runners have none configured)
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t

# Build a fixture "checkout" at $1 whose layout matches the real repo enough for
# upgrade.sh: it embeds a copy of upgrade.sh at skills/self-upgrade/scripts/ (so
# the script's REPO=dirname/../../.. resolves to the fixture root) and a stub
# src/restart.sh. Wires it to a fresh bare remote and returns with $NEW_REPO set.
make_fixture() {
  local root="$1"
  local remote="$root.remote.git"
  mkdir -p "$root/skills/self-upgrade/scripts" "$root/src" "$root/scripts" "$root/workspace/state/cores"
  cp "$SRC_SCRIPT" "$root/skills/self-upgrade/scripts/upgrade.sh"
  cat > "$root/scripts/sutando-config.sh" <<EOF
#!/bin/bash
[ "\${1:-}" = workspace ] && printf '%s\n' "$root/workspace"
EOF
  chmod +x "$root/scripts/sutando-config.sh"
  # stub restart.sh: record that it ran, then BLOCK 15s (mimics startup.sh's
  # foreground hang). REPO is exported to it by upgrade.sh's cwd; write marker
  # into the fixture root via an absolute path passed through the env.
  cat > "$root/src/restart.sh" <<EOF
#!/bin/bash
echo "restart invoked" > "$root/restart-marker"
touch "$root/workspace/state/cores/test.alive"
sleep 15
EOF
  chmod +x "$root/src/restart.sh"

  git init -q -b main "$root"
  ( cd "$root" && git add -A && git commit -qm "init" )
  git init -q -b main --bare "$remote"
  ( cd "$root" && git remote add origin "$remote" && git push -q -u origin main )
}

# Advance the bare remote by one commit so the fixture is "behind" by 1.
advance_remote() {
  local root="$1" work="$1.pusher"
  git clone -q -b main "$root.remote.git" "$work"
  ( cd "$work" && echo "upstream change" > CHANGELOG && git add -A && git commit -qm "upstream" && git push -q origin main )
  rm -rf "$work"
}

run_upgrade() { # args: repo, extra args...  -> sets RC and OUT
  local repo="$1"; shift
  set +e
  OUT="$(cd "$repo" && SUTANDO_TEST_MODE=1 SUTANDO_UPGRADE_VERIFY_TRIES=1 bash "$repo/skills/self-upgrade/scripts/upgrade.sh" "$@" 2>&1)"
  RC=$?
  set -e
}

# --- A. dirty tree aborts (exit 2), never fetches/pulls -----------------------
A="$TMPROOT/a"; make_fixture "$A"
echo "uncommitted" > "$A/dirty.txt"
run_upgrade "$A"
[ "$RC" -eq 2 ] || fail "dirty tree: expected exit 2, got $RC (out: $OUT)"
case "$OUT" in *"dirty"*) : ;; *) fail "dirty tree: expected 'dirty' in output, got: $OUT" ;; esac
[ ! -f "$A/restart-marker" ] || fail "dirty tree: restart.sh must NOT run when aborting"
ok "A: aborts on dirty tree (exit 2), restart never invoked"

# --- B. already latest → no-op (exit 0), no restart --------------------------
B="$TMPROOT/b"; make_fixture "$B"
run_upgrade "$B"
[ "$RC" -eq 0 ] || fail "already-latest: expected exit 0, got $RC (out: $OUT)"
case "$OUT" in *"already at latest"*) : ;; *) fail "already-latest: expected 'already at latest', got: $OUT" ;; esac
[ ! -f "$B/restart-marker" ] || fail "already-latest: restart.sh must NOT run when nothing to pull"
ok "B: no-ops when already at latest (exit 0), no restart"

# --- C. real upgrade → pulls + restarts DETACHED (the core fix) ---------------
C="$TMPROOT/c"; make_fixture "$C"
advance_remote "$C"                       # remote now 1 ahead → fixture is behind
before="$(cd "$C" && git rev-parse --short HEAD)"
start=$(date +%s)
run_upgrade "$C"
elapsed=$(( $(date +%s) - start ))
after="$(cd "$C" && git rev-parse --short HEAD)"

[ "$RC" -eq 0 ] || fail "upgrade: expected exit 0, got $RC (out: $OUT)"
[ "$after" != "$before" ] || fail "upgrade: HEAD did not advance ($before -> $after); pull didn't happen"
[ -f "$C/restart-marker" ] || fail "upgrade: restart.sh was never invoked (marker missing)"
case "$OUT" in *"core heartbeat advancing"*) : ;; *) fail "upgrade: heartbeat verification did not pass: $OUT" ;; esac
# The detach proof: restart.sh blocks 15s. Detached => upgrade returns in a few
# seconds. Inline (the bug) => >= 15s. Generous threshold of 10s.
[ "$elapsed" -lt 10 ] || fail "upgrade: took ${elapsed}s — restart.sh was NOT detached (would hang the core)"
# the detached stub's `sleep 15` is still running; note its pgid for cleanup
MARKER_SLEEP_PID="$(pgrep -f "sleep 15" | head -1 || true)"
ok "C: pulls to latest + runs restart DETACHED (${elapsed}s < 10s, marker present)"

echo "PASS — self-upgrade behavioral suite (3/3)"
