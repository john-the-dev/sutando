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


def _alloc_locked(state: Path) -> str | None:
    """Allocate the next ID. THE CALLER MUST ALREADY HOLD the counter lock.

    Split out so the stamp can be one transaction: reading the file, deciding it
    needs an ID, allocating, and persisting all happen inside a single lock hold
    (see `stamp_result_file`). Allocating under its own short-lived lock and
    writing outside it is what let two writers each mint an ID for one result.
    """
    counter, history = state / "task-counter.json", state / "task-completions-daily.json"
    today = date.today().strftime("%Y%m%d")
    try:
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


def alloc_task_id(results_dir: Path) -> str | None:
    """Next YYYYMMDD-NNN, from the state/ beside `results_dir`. None on failure.

    Same file, lock and monotonic-floor rules as the stamping hook, which imports
    this rather than keeping a second copy. Standalone callers get the lock taken
    for them. Anything that goes on to WRITE the stamp must use
    `stamp_result_file` instead, so the allocation and the persist share one hold.
    """
    state = Path(results_dir).parent / "state"
    lockf = None
    try:
        state.mkdir(parents=True, exist_ok=True)
        lockf = open(state / ".task-counter.lock", "a+")
        fcntl.flock(lockf, fcntl.LOCK_EX)
        return _alloc_locked(state)
    except Exception:
        return None
    finally:
        if lockf is not None:
            try:
                fcntl.flock(lockf, fcntl.LOCK_UN)
                lockf.close()
            except Exception:
                pass


def stamp_result_file(p: Path) -> str | None:
    """Stamp `p` as ONE transaction. Returns the stamped body, or None to fail closed.

    Read-decide-allocate-persist happens inside a single hold of the counter
    lock, and the file is RE-READ under that lock. Without the re-read, two
    writers that both read the unstamped body outside the lock each allocate:
    two counts burned for one completion, and the ID delivered on the wire
    disagrees with the one left in the archive.

    Every writer of a `[task …]` stamp must go through here — the delivery path
    and the PostToolUse hook are two such writers on the same files.

    Every failure returns None, which the caller turns into "not ready" rather
    than an unstamped send. That is deliberate and it is the safer direction:
    a result left on disk is retried next pass and is visible to anyone looking,
    whereas a reply delivered without an ID — or carrying an ID that was never
    durably recorded — is silently wrong and unrecoverable once sent.
    """
    state = p.parent.parent / "state"
    lockf = None
    try:
        state.mkdir(parents=True, exist_ok=True)
        lockf = open(state / ".task-counter.lock", "a+")
        fcntl.flock(lockf, fcntl.LOCK_EX)
        # Re-read INSIDE the lock: a racing consumer may have stamped it already,
        # in which case that ID is authoritative and we must not mint a second.
        try:
            fresh = p.read_text().strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not fresh:
            return None
        if not needs_task_stamp(p.name, fresh):
            return fresh  # already stamped (or exempt) — adopt what is on disk
        tid = _alloc_locked(state)
        if not tid:
            return None
        stamped = f"[task {tid}]\n\n{fresh}"
        # Atomic replace, not truncate-in-place: this module's own contract is
        # that a partial write must never be observable as a body.
        tmp = p.with_name(p.name + ".stamp.tmp")
        tmp.write_text(stamped + "\n")
        tmp.replace(p)
        return stamped
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
        # Fail CLOSED: no ID, or an ID we could not durably persist, means this
        # result is not ready. It stays on disk and is retried on the next pass.
        return stamp_result_file(p)
    return body
