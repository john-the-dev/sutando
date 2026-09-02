#!/usr/bin/env python3
"""Tasks that were handled but never got a result file.

A task file stays in `tasks/` until a result is written and the bridge archives
it, so the queue is already the record of what is unanswered. Nothing reads it
at the END of a pass, though, and the miss is invisible from inside: the agent
answers in its own transcript, the terminal shows the reply, and only the queue
disagrees. Measured five times in one session, caught every time by re-listing
by hand and never by recall.

Exit 1 when a task older than --min-age-sec has no result, 0 otherwise.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


def _result_exists(results: Path, task_id: str) -> bool:
    """Every shape a delivered result can take (CLAUDE.md 'Result-body protocol').

    Missing one shape produces a FALSE ALARM, which is the safe direction here —
    it costs a re-check, where a false all-clear costs the reply itself.
    """
    if (results / f"{task_id}.txt").exists():
        return True
    # Per-channel pull namespace: `<channel-key>.task-{id}.txt`.
    if any(results.glob(f"*.{task_id}.txt")):
        return True
    # Claimed mid-delivery by a bridge rename.
    if any(results.glob(f"{task_id}.txt.sending")) or any(results.glob(f"*.{task_id}.txt.sending")):
        return True
    # Archived: `task-<id>-<epoch>.txt` under results/archive/YYYY-MM/.
    if any((results / "archive").glob(f"**/{task_id}*.txt")):
        return True
    return False


def unanswered(workspace: Path, min_age_sec: float, now: float | None = None) -> list[tuple[str, float]]:
    now = time.time() if now is None else now
    tasks, results = workspace / "tasks", workspace / "results"
    out: list[tuple[str, float]] = []
    if not tasks.is_dir():
        return out
    for f in sorted(tasks.glob("task-*.txt")):
        age = now - f.stat().st_mtime
        if age < min_age_sec:
            continue  # still plausibly in flight
        if not _result_exists(results, f.stem):
            out.append((f.stem, age))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--min-age-sec", type=float, default=120.0,
                    help="ignore tasks younger than this (default 120)")
    a = ap.parse_args()
    rows = unanswered(Path(a.workspace), a.min_age_sec)
    if not rows:
        print("unanswered-tasks: none")
        return 0
    for task_id, age in rows:
        print(f"UNANSWERED {task_id} ({age / 60:.1f}m old) — no result file; the room heard nothing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
