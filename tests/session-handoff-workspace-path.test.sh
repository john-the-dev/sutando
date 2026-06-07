#!/usr/bin/env bash
# Structural regression test for session-handoff.sh workspace-path fix.
#
# Before this fix, `ls "$REPO/tasks/"*.txt` used the repo directory for tasks.
# Tasks live in $WORKSPACE/tasks/, not $REPO/tasks/ — so the session snapshot
# always reported "None pending" even when tasks were queued.
#
# Run: bash tests/session-handoff-workspace-path.test.sh
# Exit: 0 = pass, 1 = fail

SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/src/session-handoff.sh"
FAILURES=0

fail() { echo "FAIL: $1" >&2; FAILURES=$((FAILURES + 1)); }
pass() { echo "ok: $1"; }

# Test 1: Must NOT reference $REPO/tasks in the tasks listing
if grep 'ls.*\$REPO/tasks' "$SCRIPT" | grep -qv '#'; then
    fail "session-handoff.sh still uses \$REPO/tasks — tasks are in workspace, not repo"
else
    pass "no \$REPO/tasks in tasks listing"
fi

# Test 2: tasks listing must use a workspace variable (WORKSPACE_DIR, SUTANDO_WORKSPACE, or _WORKSPACE)
if grep 'ls.*tasks.*\.txt' "$SCRIPT" | grep -qv '#'; then
    tasks_line=$(grep 'ls.*tasks.*\.txt' "$SCRIPT" | grep -v '#')
    if echo "$tasks_line" | grep -qE 'WORKSPACE_DIR|SUTANDO_WORKSPACE|_WORKSPACE'; then
        pass "tasks listing uses workspace variable"
    else
        fail "tasks listing does not use a workspace variable: $tasks_line"
    fi
else
    fail "no tasks listing found in session-handoff.sh"
fi

# Test 3: quota path correctly uses a workspace variable
if grep 'QUOTA_FILE' "$SCRIPT" | grep -qE 'WORKSPACE_DIR|SUTANDO_WORKSPACE'; then
    pass "quota path uses workspace variable"
else
    fail "quota path should use WORKSPACE_DIR or SUTANDO_WORKSPACE"
fi

# Test 4: sentinel path correctly uses a workspace variable
if grep 'SENTINEL' "$SCRIPT" | grep -qE 'WORKSPACE_DIR|SUTANDO_WORKSPACE'; then
    pass "sentinel path uses workspace variable"
else
    fail "sentinel path should use WORKSPACE_DIR or SUTANDO_WORKSPACE"
fi

echo ""
if [ "$FAILURES" -eq 0 ]; then
    echo "All tests passed."
    exit 0
else
    echo "$FAILURES test(s) failed."
    exit 1
fi
