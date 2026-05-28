#!/bin/bash
# Stop hook: blocks Claude from finishing when unprocessed tasks exist.
# Skips tasks that already have a corresponding result file.
#
# Per docs/workspace-contract.md, tasks/ and results/ live under
# $SUTANDO_WORKSPACE, NOT under the repo. Reading from REPO/tasks/ leaves
# the hook seeing stale legacy files (pre-#911 migration leftovers) and
# missing the actual workspace queue — the bug class fixed in PR #1263.

WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
TASKS_DIR="$WORKSPACE/tasks"
RESULTS_DIR="$WORKSPACE/results"

UNPROCESSED=""
shopt -s nullglob 2>/dev/null
for f in "$TASKS_DIR"/*.txt; do
  BASENAME=$(basename "$f")
  # Skip if result already exists
  [ -f "$RESULTS_DIR/$BASENAME" ] && continue
  UNPROCESSED+="--- $BASENAME ---\n$(cat "$f")\n\n"
done

if [ -n "$UNPROCESSED" ]; then
  printf '{"decision":"block","reason":"Unprocessed tasks in tasks/","additionalContext":"UNPROCESSED TASKS — process these NOW:\n%s"}' "$(echo -e "$UNPROCESSED" | sed 's/"/\\"/g' | tr '\n' ' ')"
else
  echo '{}'
fi
