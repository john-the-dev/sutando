#!/bin/bash
# Tests for src/check-pending-tasks.sh — workspace-anchored Stop hook.
#
# Run: bash tests/check-pending-tasks.test.sh
# Exit: 0 on pass, 1 on fail.
#
# Regression target: the hook used to resolve `dirname($0)/../tasks` (repo
# path) — silently reading stale tasks/ from the legacy in-repo location.
# Post-fix it reads `$SUTANDO_WORKSPACE/tasks` (default `~/.sutando/workspace`)
# matching the rest of the post-#911 codebase. These tests pin that behavior.

set -u
SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/src/check-pending-tasks.sh"

if [ ! -x "$SCRIPT" ] && [ ! -f "$SCRIPT" ]; then
    echo "FAIL: $SCRIPT not found"
    exit 1
fi

PASS=0
FAIL=0

run_case() {
    local name="$1"; shift
    if ( "$@" ); then
        echo "PASS: $name"
        PASS=$((PASS + 1))
    else
        echo "FAIL: $name"
        FAIL=$((FAIL + 1))
    fi
}

# Run the hook with isolation: dedicated tmp workspace, no real env leakage.
run_hook() {
    local ws="$1"
    SUTANDO_WORKSPACE="$ws" bash "$SCRIPT" 2>&1
}

# Setup: scratch workspace with tasks/ + results/ dirs.
new_ws() {
    local d
    d="$(mktemp -d "${TMPDIR:-/tmp}/check-pending-tasks.test.XXXXXX")"
    mkdir -p "$d/tasks" "$d/results"
    echo "$d"
}

# Case 1: empty workspace → empty JSON output.
case_empty_workspace_returns_empty_json() {
    local ws
    ws="$(new_ws)"
    out="$(run_hook "$ws")"
    rm -rf "$ws"
    [ "$out" = "{}" ] || { echo "  expected '{}'; got: $out"; return 1; }
    return 0
}

# Case 2: one task with no matching result → block + task name in additionalContext.
case_unprocessed_task_returns_block() {
    local ws
    ws="$(new_ws)"
    echo "id: task-test-1\ntask: hello world" > "$ws/tasks/task-test-1.txt"
    out="$(run_hook "$ws")"
    rm -rf "$ws"
    echo "$out" | grep -q '"decision":"block"' || { echo "  expected block; got: $out"; return 1; }
    echo "$out" | grep -q "task-test-1.txt" || { echo "  expected task name in output; got: $out"; return 1; }
    return 0
}

# Case 3: task with matching result file → skipped (back to empty output).
case_task_with_result_is_skipped() {
    local ws
    ws="$(new_ws)"
    echo "id: task-test-2\ntask: done" > "$ws/tasks/task-test-2.txt"
    echo "result body" > "$ws/results/task-test-2.txt"
    out="$(run_hook "$ws")"
    rm -rf "$ws"
    [ "$out" = "{}" ] || { echo "  expected '{}' (task is processed); got: $out"; return 1; }
    return 0
}

# Case 4: SUTANDO_WORKSPACE unset → falls back to $HOME/.sutando/workspace.
# We isolate via HOME pointing at a tmpdir so we don't touch the real home.
case_env_unset_falls_back_to_home_default() {
    local home
    home="$(mktemp -d "${TMPDIR:-/tmp}/check-pending-tasks.fakehome.XXXXXX")"
    mkdir -p "$home/.sutando/workspace/tasks" "$home/.sutando/workspace/results"
    echo "id: task-test-3\ntask: home default" > "$home/.sutando/workspace/tasks/task-test-3.txt"

    # Unset SUTANDO_WORKSPACE for this call; pin HOME so the default resolves
    # inside the fake home.
    out="$(env -i HOME="$home" PATH="$PATH" bash "$SCRIPT" 2>&1)"
    rm -rf "$home"

    echo "$out" | grep -q "task-test-3" || { echo "  expected task-test-3 from fake-home default; got: $out"; return 1; }
    return 0
}

# Case 5: multiple unprocessed tasks → all included.
case_multiple_unprocessed_tasks() {
    local ws
    ws="$(new_ws)"
    echo "task one" > "$ws/tasks/task-a.txt"
    echo "task two" > "$ws/tasks/task-b.txt"
    echo "task three" > "$ws/tasks/task-c.txt"
    out="$(run_hook "$ws")"
    rm -rf "$ws"
    echo "$out" | grep -q "task-a.txt" || { echo "  missing task-a; got: $out"; return 1; }
    echo "$out" | grep -q "task-b.txt" || { echo "  missing task-b; got: $out"; return 1; }
    echo "$out" | grep -q "task-c.txt" || { echo "  missing task-c; got: $out"; return 1; }
    return 0
}

run_case "empty workspace returns {}" case_empty_workspace_returns_empty_json
run_case "unprocessed task returns block" case_unprocessed_task_returns_block
run_case "task with matching result is skipped" case_task_with_result_is_skipped
run_case "env unset falls back to home default" case_env_unset_falls_back_to_home_default
run_case "multiple unprocessed tasks all listed" case_multiple_unprocessed_tasks

echo ""
echo "${PASS} passed, ${FAIL} failed"
[ "$FAIL" = "0" ] || exit 1
