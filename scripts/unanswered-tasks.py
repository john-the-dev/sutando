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
import re
import time
from pathlib import Path

_DEDUP = re.compile(r"\A\s*\[deduped:\s*(task-[\w-]+)\s*\]", re.IGNORECASE)


def _result_path(results: Path, task_id: str) -> Path | None:
    """Every shape a delivered result can take (CLAUDE.md 'Result-body protocol').

    Missing one shape produces a FALSE ALARM, which is the safe direction here —
    it costs a re-check, where a false all-clear costs the reply itself.
    `sorted` because an unordered glob picks by filesystem order, which differs
    between APFS and the CI runner.
    """
    direct = results / f"{task_id}.txt"
    if direct.exists():
        return direct
    for pat in (f"*.{task_id}.txt",                      # per-channel pull namespace
                f"{task_id}.txt.sending",                # claimed mid-delivery
                f"*.{task_id}.txt.sending"):
        hits = sorted(results.glob(pat))
        if hits:
            return hits[0]
    # `{id}-*` requires the separator: a bare `{id}*` prefix also matches
    # `{id}.too-old.<epoch>`, i.e. QUARANTINED, which is the opposite of delivered.
    arch = results / "archive"
    hits = sorted(list(arch.glob(f"**/{task_id}.txt")) + list(arch.glob(f"**/{task_id}-*.txt")))
    return hits[0] if hits else None


def _task_exists(tasks: Path, task_id: str) -> bool:
    """Did this id ever exist HERE? Task ids are minted per recipient, so a
    peer's id is well-formed and still unresolvable — charset cannot tell."""
    if (tasks / f"{task_id}.txt").exists():
        return True
    arch = tasks / "archive"
    return bool(list(arch.glob(f"**/{task_id}.txt")) + list(arch.glob(f"**/{task_id}-*.txt")))


def _task_channel(tasks: Path, task_id: str) -> str | None:
    """The task's originating channel, from its header."""
    for cand in [tasks / f"{task_id}.txt", *sorted((tasks / "archive").glob(f"**/{task_id}*.txt"))]:
        try:
            text = cand.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if line.startswith("channel_id:"):
                return line.split(":", 1)[1].strip()
        return None
    return None


def _unanswered_reason(results: Path, task_id: str, tasks: Path | None = None,
                       origin_channel: str | None = None,
                       _seen: set[str] | None = None) -> str | None:
    """None when the room actually heard something; else why it did not.

    `[deduped: X]` promises the reply lives in X's result. If X never produced
    one the reply is nowhere, and every cheap check still sees a result file on
    disk for this task — which is exactly how two replies were lost silently.
    """
    seen = set() if _seen is None else _seen
    if task_id in seen:
        return f"dedup cycle at {task_id}"
    seen.add(task_id)
    path = _result_path(results, task_id)
    if path is None:
        return "no result file"
    try:
        head = path.read_text(errors="replace")[:200]
    except OSError:
        return None  # present but unreadable: someone wrote a result, don't invent an alarm
    match = _DEDUP.match(head)
    if not match:
        return None
    target = match.group(1)
    if tasks is not None:
        origin = origin_channel if origin_channel is not None else _task_channel(tasks, task_id)
        dest = _task_channel(tasks, target)
        # Resolving is not reaching: a target in ANOTHER channel answers someone
        # else, and this sender is silenced while every existence check passes.
        if origin and dest and origin != dest:
            return f"CROSS-SENDER: deduped into {target}, whose reply goes to {dest}, not {origin}"
    reason = _unanswered_reason(results, target, tasks, origin_channel, seen)
    if reason is None:
        return None
    # DANGLING vs ORPHANED: the fixes differ — "never name a peer's id" vs
    # "your own target never answered" — so never collapse them into one.
    if tasks is not None and reason == "no result file" and not _task_exists(tasks, target):
        return f"DANGLING: deduped into {target}, which does not exist in this workspace"
    return f"ORPHANED: deduped into {target}, which has {reason}"


def unanswered(workspace: Path, min_age_sec: float, now: float | None = None) -> list[tuple[str, float, str]]:
    now = time.time() if now is None else now
    tasks, results = workspace / "tasks", workspace / "results"
    out: list[tuple[str, float, str]] = []
    if not tasks.is_dir():
        return out
    for f in sorted(tasks.glob("task-*.txt")):
        age = now - f.stat().st_mtime
        if age < min_age_sec:
            continue  # still plausibly in flight
        reason = _unanswered_reason(results, f.stem, tasks)
        if reason is not None:
            out.append((f.stem, age, reason))
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
    for task_id, age, reason in rows:
        print(f"UNANSWERED {task_id} ({age / 60:.1f}m old) — {reason}; the room heard nothing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
