#!/usr/bin/env python3
"""Readiness of a `results/<task-id>.txt` file, for every delivery consumer.

The single owner of "is this result file ready to send?". Adapters bind their
own resolved results directory and keep only provider-specific delivery; they
must not re-implement the check.

A result path can exist before it holds an answer. The core writes
temp-file-then-rename, but it is an LLM driving a shell and will create the
destination for unrelated reasons, and a partial write can be observed
mid-content. File existence is therefore not readiness: a consumer that treats
it as readiness delivers an empty message and archives the task as done, which
strands the real answer written moments later.

A deliberately empty reply is expressed with the `[no-send]` marker, parsed by
`result_markers`, not by writing an empty file.

This is also the task-ID stamping boundary. A PostToolUse hook stamps results
after a tool call ends, but a bridge can read and post a `results/task-*.txt`
the moment it appears — before that hook runs — and deliver a reply with no ID.
Stamping HERE closes the race structurally: every delivery consumer funnels
through this function, so an unstamped ordinary result cannot be read for
delivery without acquiring an ID first.

Stdlib-only and self-contained BY CONTRACT: this file is vendored verbatim into
packages/ag2-sparrow (tools/sync_from_src.py), where a sibling import would not
resolve. The counter is derived from the caller's own results dir rather than
resolved from a workspace, which keeps that package's no-workspace-resolution
rule intact.
"""
from __future__ import annotations

import fcntl
import json
import re
import time
from datetime import date
from pathlib import Path

__all__ = ["read_ready_result", "is_ready_body", "needs_task_stamp", "alloc_task_id"]

# Already carries an ID: [task 20260715-001] or ...-001-extend-...
_STAMPED = re.compile(r"^\s*\[task \d{8}-\d{3}")
# Bridge control markers only fire as the FIRST non-empty line. Prepending an
# ID would push them off line 1 and silently break skip/redirect routing.
_BRIDGE_MARKER = re.compile(
    r"^\s*\[(?:no-send\]|deduped:|REPLIED\]|channel:|dm-only\])", re.IGNORECASE
)


def needs_task_stamp(name: str, body: str) -> bool:
    """True when `body` of result file `name` must acquire a task ID."""
    return (
        name.startswith("task-")
        and name.endswith(".txt")
        and bool(body.strip())
        and not _STAMPED.match(body)
        and not _BRIDGE_MARKER.match(body)
    )


def alloc_task_id(results_dir: Path) -> str | None:
    """Next YYYYMMDD-NNN, from the state/ beside `results_dir`. None on failure.

    Same file, lock and monotonic-floor rules as the stamping hook, which imports
    this rather than keeping a second copy.
    """
    state = Path(results_dir).parent / "state"
    counter, history = state / "task-counter.json", state / "task-completions-daily.json"
    today = date.today().strftime("%Y%m%d")
    lockf = None
    try:
        state.mkdir(parents=True, exist_ok=True)
        lockf = open(state / ".task-counter.lock", "a+")
        fcntl.flock(lockf, fcntl.LOCK_EX)
        try:
            s = json.loads(counter.read_text())
        except Exception:
            s = {}
        if s.get("date") != today:
            s = {"date": today, "count": 0}
        try:
            base = int(s.get("count", 0))
        except Exception:
            base = 0
        try:
            hist = json.loads(history.read_text())
            floor = int(hist.get(today, 0))
        except Exception:
            hist, floor = {}, 0
        # A truncated counter reads 0 and would remint 001 over a day in
        # progress; today's history is the surviving floor.
        s["count"] = max(base, floor) + 1
        tmp = counter.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s))
        tmp.replace(counter)
        if s["count"] > floor:
            hist[today] = s["count"]
            htmp = history.with_suffix(".json.tmp")
            htmp.write_text(json.dumps(hist))
            htmp.replace(history)
        return f"{today}-{s['count']:03d}"
    except Exception:
        return None
    finally:
        if lockf is not None:
            try:
                fcntl.flock(lockf, fcntl.LOCK_UN)
                lockf.close()
            except Exception:
                pass


def is_ready_body(text: str | None) -> bool:
    """True when `text` is a deliverable body (non-empty after stripping)."""
    return bool(text and text.strip())


def read_ready_result(path: str | Path) -> str | None:
    """Return the stripped body of `path`, or None when it is not ready.

    None covers missing, unreadable and empty-or-whitespace-only files. Callers
    skip on None and retry on a later pass — the file is not consumed, so a
    result that lands between passes is still delivered.
    """
    p = Path(path)
    try:
        body = p.read_text()
    except (OSError, UnicodeDecodeError):
        # Missing, unreadable, or a partial write mid-character. Never
        # deliverable, and readable again on a later pass.
        return None
    body = body.strip()
    if not body:
        return None
    if needs_task_stamp(p.name, body):
        tid = alloc_task_id(p.parent)
        if tid:
            body = f"[task {tid}]\n\n{body}"
            try:
                p.write_text(body + "\n")  # persist so archive/audit see the sent text
            except OSError:
                pass  # deliver the stamped body regardless; the ID is what matters
    return body
