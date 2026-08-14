#!/usr/bin/env python3
"""PostToolUse hook — structurally enforce the owner's task-ID requirement AND
keep a per-day completion history so "how many tasks did I finish each day?" is
answerable at any time (see scripts/task-completions.py).

The owner requires every task reply to carry a `[task YYYYMMDD-NNN]` ID (daily
counter, resets each day). Relying on the agent to remember to prepend it failed
(memory had the rule; the agent still lapsed across a busy session). This hook
removes the reliance on memory: after any tool runs, it scans the live `results/`
dir and, for any `task-*.txt` whose body does NOT already start with a
`[task YYYYMMDD-NNN]` marker, it allocates the next counter ID and prepends it.
So a reply the agent wrote without an ID still gets one before the bridge
delivers it.

The daily counter (`state/task-counter.json`) resets every day, so on its own it
only knows *today's* count — yesterday's total is overwritten. To make the
per-day history durable, every allocation also upserts today's running total
into `state/task-completions-daily.json` ({"YYYYMMDD": count}). Past days are
never touched; only today's entry advances. That file is the source of truth for
the completions report.

Idempotent (skips already-stamped files) and fail-open (never blocks a tool).
"""
import datetime
import fcntl
import glob
import json
import re
import sys
import time
from pathlib import Path

# Only stamp a result file the CURRENT turn just wrote — mtime within this many
# seconds of now. Without this, a backlog of old undelivered results in results/
# would each get stamped, inflating the counter (NNN must mean "tasks done today",
# not "files sitting in results/"). PostToolUse fires within ~1s of the write, so
# a small window is ample.
_FRESH_S = 45

# Resolve the workspace the same way the rest of the stack does — the sanctioned
# resolver (workspace_default.resolve_workspace) owns all fallback/override logic;
# never reconstruct a workspace path inline here.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from workspace_default import resolve_workspace  # noqa: E402
from result_ready import alloc_task_id, needs_task_stamp  # noqa: E402

WS = Path(resolve_workspace())

COUNTER = WS / "state" / "task-counter.json"
HISTORY = WS / "state" / "task-completions-daily.json"
RESULTS = WS / "results"





def main() -> None:
    try:
        now = time.time()
        for f in glob.glob(str(RESULTS / "task-*.txt")):
            p = Path(f)
            try:
                if now - p.stat().st_mtime > _FRESH_S:
                    continue  # stale/backlog file — not something this turn wrote
                body = p.read_text()
            except Exception:
                continue
            if not body.strip():
                continue  # empty/placeholder — leave it
            if not needs_task_stamp(p.name, body):
                continue  # already stamped, or a body-start bridge marker
            tid = alloc_task_id(RESULTS)
            if tid:
                p.write_text(f"[task {tid}]\n\n{body}")
    except Exception:
        pass
    sys.exit(0)  # fail-open: a stamping error must never block the tool


if __name__ == "__main__":
    main()
