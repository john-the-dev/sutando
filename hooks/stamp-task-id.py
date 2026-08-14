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

WS = Path(resolve_workspace())

COUNTER = WS / "state" / "task-counter.json"
HISTORY = WS / "state" / "task-completions-daily.json"
RESULTS = WS / "results"
# Match an already-present marker: [task 20260715-001] or ...-001-extend-...
_STAMPED = re.compile(r"^\s*\[task \d{8}-\d{3}")
# Bridge control markers that MUST be the first non-empty line to be honored
# (result_markers.parse_markers / discord-bridge skip+redirect). Prepending a
# `[task …]` line would push them off line 1 and silently break delivery
# routing, so leave these bodies unstamped entirely (PR #2125 review).
_BRIDGE_MARKER = re.compile(
    r"^\s*\[(?:no-send\]|deduped:|REPLIED\]|channel:|dm-only\])", re.IGNORECASE
)


def _history_count(day: str) -> int:
    """Today's recorded total, or 0. The counter's recovery floor when it is
    unreadable — history is the only other durable record of the day's count."""
    try:
        hist = json.load(open(HISTORY))
        return int(hist.get(day, 0)) if isinstance(hist, dict) else 0
    except Exception:
        return 0


def _record_history(day: str, count: int) -> None:
    """Upsert today's running total into the durable per-day history file.

    Only today's entry advances; past days are preserved verbatim. Fail-open —
    a history-write error must never break stamping or the tool."""
    try:
        try:
            hist = json.load(open(HISTORY))
            if not isinstance(hist, dict):
                hist = {}
        except Exception:
            hist = {}
        # Monotonic for today: a lower count means the counter was recovered from
        # a worse source, and lowering here would erase the surviving evidence.
        try:
            if int(hist.get(day, 0)) > count:
                return
        except Exception:
            pass
        hist[day] = count
        HISTORY.parent.mkdir(parents=True, exist_ok=True)
        tmp = HISTORY.with_suffix(".json.tmp")
        json.dump(hist, open(tmp, "w"), sort_keys=True)
        tmp.replace(HISTORY)  # atomic swap so a concurrent reader never sees a half-written file
    except Exception:
        pass


def _alloc() -> str:
    """Next YYYYMMDD-NNN from the persistent counter, resetting on a new day.

    Also records the day's new running total in the durable history file.

    The read-modify-write is serialized under an exclusive ``flock`` on the
    counter file so two concurrent PostToolUse invocations (or another writer of
    the same counter) can't both read the same count and mint a duplicate ID or
    lose a count (CR #2125). ``_record_history`` runs inside the held lock, so the
    history upsert is serialized too. Fail-open — any lock/IO error degrades to
    the unlocked path rather than breaking stamping or the tool."""
    today = datetime.date.today().strftime("%Y%m%d")
    COUNTER.parent.mkdir(parents=True, exist_ok=True)
    lockf = None
    try:
        # Lock a sidecar, NOT the counter: the counter is replaced atomically
        # below, and a lock held on its fd would guard an unlinked inode.
        lockf = open(COUNTER.with_suffix(".lock"), "a+")
        fcntl.flock(lockf, fcntl.LOCK_EX)
    except Exception:
        lockf = None
    try:
        try:
            s = json.loads(COUNTER.read_text() or "{}")
            if not isinstance(s, dict):
                s = {}
        except Exception:
            s = {}
        if s.get("date") != today:  # daily reset — NNN is the Nth task *today*
            s = {"date": today, "count": 0}
        try:
            base = int(s.get("count", 0))
        except Exception:
            base = 0
        # A truncated or corrupt counter reads as 0 and would remint 001 over a
        # day already in progress; today's history is the surviving floor.
        s["count"] = max(base, _history_count(today)) + 1
        try:
            tmp = COUNTER.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(s))
            tmp.replace(COUNTER)  # atomic: a crash leaves the old counter, never an empty one
        except Exception:
            pass
        _record_history(today, s["count"])
        return f"{today}-{s['count']:03d}"
    finally:
        if lockf is not None:
            try:
                fcntl.flock(lockf, fcntl.LOCK_UN)
                lockf.close()
            except Exception:
                pass


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
            if _STAMPED.match(body):
                continue  # already carries an ID (agent remembered) — don't double-count
            if _BRIDGE_MARKER.match(body):
                continue  # body-start bridge control marker — prepending would break its first-line parsing
            p.write_text(f"[task {_alloc()}]\n\n{body}")
    except Exception:
        pass
    sys.exit(0)  # fail-open: a stamping error must never block the tool


if __name__ == "__main__":
    main()
