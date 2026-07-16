#!/usr/bin/env python3
"""Behavioral tests for the task-ID stamping hook + the per-day completions
report. Exercises the real code against a temp workspace (module globals
repointed), not mocks.

Covers:
  hook._alloc  — increments, formats NNN, resets on a new day, persists history
  hook.main    — stamps a fresh unstamped result, skips already-stamped / stale
                 (mtime) / empty files; does NOT double-count skips
  report       — load_history reads the file + folds today's live counter;
                 render lists per-day counts newest-first with a total

Run: python3 tests/stamp-task-id.test.py    (exit 0 pass, 1 fail)
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_failed = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _failed
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f" — {detail}"))
    if not cond:
        _failed += 1


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _point(mod, ws: Path):
    """Repoint a freshly-loaded module's globals at a temp workspace."""
    (ws / "state").mkdir(parents=True, exist_ok=True)
    (ws / "results").mkdir(parents=True, exist_ok=True)
    mod.WS = ws
    mod.COUNTER = ws / "state" / "task-counter.json"
    if hasattr(mod, "HISTORY"):
        mod.HISTORY = ws / "state" / "task-completions-daily.json"
    if hasattr(mod, "RESULTS"):
        mod.RESULTS = ws / "results"


TODAY = datetime.date.today().strftime("%Y%m%d")

# ---------------------------------------------------------------------------
# hook._alloc
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as t:
    ws = Path(t)
    hook = _load(REPO / "hooks" / "stamp-task-id.py", "stamp_task_id")
    _point(hook, ws)

    a1 = hook._alloc()
    a2 = hook._alloc()
    check("alloc formats YYYYMMDD-001", a1 == f"{TODAY}-001", a1)
    check("alloc increments to -002", a2 == f"{TODAY}-002", a2)
    counter = json.load(open(hook.COUNTER))
    check("counter persists date+count", counter == {"date": TODAY, "count": 2}, str(counter))
    hist = json.load(open(hook.HISTORY))
    check("history records today's running total", hist.get(TODAY) == 2, str(hist))

# daily reset preserves past days
with tempfile.TemporaryDirectory() as t:
    ws = Path(t)
    hook = _load(REPO / "hooks" / "stamp-task-id.py", "stamp_task_id2")
    _point(hook, ws)
    json.dump({"date": "20260101", "count": 7}, open(hook.COUNTER, "w"))
    json.dump({"20260101": 7}, open(hook.HISTORY, "w"))
    got = hook._alloc()
    check("alloc resets counter on a new day", got == f"{TODAY}-001", got)
    hist = json.load(open(hook.HISTORY))
    check("history keeps the prior day", hist.get("20260101") == 7, str(hist))
    check("history adds the new day", hist.get(TODAY) == 1, str(hist))

    # malformed counter/history files → alloc still succeeds (fail-open read)
    hook.COUNTER.write_text("corrupt")
    hook.HISTORY.write_text("corrupt")
    got = hook._alloc()
    check("alloc recovers from a corrupt counter", got == f"{TODAY}-001", got)
    check("history rebuilt after corruption", json.load(open(hook.HISTORY)).get(TODAY) == 1)

# ---------------------------------------------------------------------------
# hook.main — stamping behavior
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as t:
    ws = Path(t)
    hook = _load(REPO / "hooks" / "stamp-task-id.py", "stamp_task_id3")
    _point(hook, ws)
    R = ws / "results"

    fresh = R / "task-1.txt"; fresh.write_text("Done with the thing.\n")
    stamped = R / "task-2.txt"; stamped.write_text(f"[task {TODAY}-005]\n\nAlready has an id.\n")
    empty = R / "task-3.txt"; empty.write_text("   \n")
    stale = R / "task-4.txt"; stale.write_text("Old undelivered result.\n")
    old = time.time() - 3600
    os.utime(stale, (old, old))  # backlog file, older than the freshness window

    try:
        hook.main()  # real hook fail-open-exits 0; swallow that for in-process testing
    except SystemExit:
        pass

    check("fresh unstamped result gets an id", hook._STAMPED.match(fresh.read_text()) is not None,
          fresh.read_text()[:40])
    check("already-stamped file unchanged",
          stamped.read_text() == f"[task {TODAY}-005]\n\nAlready has an id.\n")
    check("empty file left alone", empty.read_text() == "   \n")
    check("stale/backlog file NOT stamped (mtime guard)",
          hook._STAMPED.match(stale.read_text()) is None, stale.read_text()[:40])
    # only the one fresh file consumed a counter id
    counter = json.load(open(hook.COUNTER))
    check("only fresh file advanced the counter (=1)", counter.get("count") == 1, str(counter))

# ---------------------------------------------------------------------------
# report — load_history + render
# ---------------------------------------------------------------------------
with tempfile.TemporaryDirectory() as t:
    ws = Path(t)
    rep = _load(REPO / "scripts" / "task-completions.py", "task_completions")
    _point(rep, ws)
    # Use fixed PAST dates (not today) so the seed never collides with the run day.
    json.dump({"20260101": 37, "20260102": 49}, open(rep.HISTORY, "w"))
    json.dump({"date": TODAY, "count": 3}, open(rep.COUNTER, "w"))

    hist = rep.load_history()
    check("load_history reads recorded days", hist.get("20260102") == 49, str(hist))
    check("load_history folds today's live counter", hist.get(TODAY) == 3, str(hist))

    out = rep.render(hist, days=14)
    check("render lists a recorded day", "2026-01-02" in out and ": 49" in out, out)
    check("render marks today", "today: 3" in out, out)
    check("render shows a total line", "total" in out, out)

    # counter ahead of a stale history entry wins (mid-day report reflects live count)
    json.dump({TODAY: 1}, open(rep.HISTORY, "w"))
    json.dump({"date": TODAY, "count": 9}, open(rep.COUNTER, "w"))
    check("live counter overrides a lagging history entry", rep.load_history().get(TODAY) == 9)

    # empty / malformed inputs → graceful
    check("render on empty history", rep.render({}, days=14) == "No task completions recorded yet.")
    check("_fmt_day passes through a non-date", rep._fmt_day("notadate") == "notadate")
    rep.HISTORY.write_text("this is not json")       # malformed history
    rep.COUNTER.write_text("also not json")          # malformed counter
    check("load_history tolerates malformed files", rep.load_history() == {})
    # a history file that is a JSON list, plus a bad-value entry → ignored, not crash
    json.dump(["nope"], open(rep.HISTORY, "w"))
    check("load_history ignores non-dict json", rep.load_history() == {})
    json.dump({"20260101": "NaN", "20260103": 4}, open(rep.HISTORY, "w"))
    json.dump({"date": TODAY, "count": 2}, open(rep.COUNTER, "w"))
    h = rep.load_history()
    check("load_history skips a non-int value", "20260101" not in h and h.get("20260103") == 4, str(h))

    # main() entrypoint — default, --all, --json, --days
    import contextlib
    import io

    def run_main(argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = rep.main(argv)
        return rc, buf.getvalue()

    rc, out = run_main([])
    check("main() default exits 0 + prints a report", rc == 0 and "completions by day" in out, out[:60])
    rc, out = run_main(["--all"])
    check("main() --all lists recorded day", rc == 0 and "2026-01-03" in out, out[:80])
    rc, out = run_main(["--json"])
    check("main() --json emits parseable {day:count}", json.loads(out).get("20260103") == 4, out[:80])
    rc, out = run_main(["--days", "1"])
    check("main() --days 1 shows a single day", rc == 0 and out.count(":") >= 1, out[:80])

print()
if _failed:
    print(f"FAIL — {_failed} check(s) failed")
    raise SystemExit(1)
print("PASS — stamp-task-id + task-completions")
