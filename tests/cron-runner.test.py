#!/usr/bin/env python3
"""Tests for src/cron-runner.py — the OS-supervised reliable cron scheduler.

Covers the four things that must not regress:
  1. The 5-field cron matcher, including the crons.json expressions this host
     actually uses (`*/5`, `57 6 * * *`, `*/30`, `2 6 * * *`) and the standard
     DOM/DOW OR-semantics when both fields are restricted.
  2. `due_since` catch-up: a fire that landed while the machine was asleep is
     still caught on the next tick, bounded to one catch-up per entry.
  3. `emit_task`: task-file shape (prompt vs prompt_skill, source/tier/priority).
  4. `run()` tick: only `"launchd": true` entries fire; state is persisted so a
     fired entry does not re-fire on the next tick.

Run: python3 tests/cron-runner.test.py
"""
from __future__ import annotations

import calendar
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("cron_runner", REPO / "src" / "cron-runner.py")
assert _spec and _spec.loader
cr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cr)  # type: ignore

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ok: {msg}")
    else:
        print(f"  FAIL: {msg}")
        _failures.append(msg)


# --- 1. cron matcher --------------------------------------------------------
def _lt(y, mo, d, h, mi):
    """Local struct_time for the given wall-clock fields."""
    return time.localtime(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))


def test_parse_field():
    check(cr._parse_field("*", 0, 59) == set(range(0, 60)), "'*' expands full range")
    check(cr._parse_field("*/5", 0, 59) == {0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
          "'*/5' minute steps")
    check(cr._parse_field("1-5", 0, 6) == {1, 2, 3, 4, 5}, "'1-5' range")
    check(cr._parse_field("7", 0, 23) == {7}, "single value")
    check(cr._parse_field("1,15,30", 0, 59) == {1, 15, 30}, "comma list")
    check(cr._parse_field("*/3", 1, 31) == set(range(1, 32, 3)), "'*/3' day-of-month steps")
    check(cr._parse_field("0-10/2", 0, 59) == {0, 2, 4, 6, 8, 10}, "'A-B/N' stepped range")


def test_matches_realworld():
    # main-loop */5 — matches on multiples of 5, not otherwise.
    check(cr.cron_matches("*/5 * * * *", _lt(2026, 7, 2, 6, 5)), "*/5 matches :05")
    check(not cr.cron_matches("*/5 * * * *", _lt(2026, 7, 2, 6, 6)), "*/5 skips :06")
    # morning-briefing 57 6 * * * — only 06:57.
    check(cr.cron_matches("57 6 * * *", _lt(2026, 7, 2, 6, 57)), "57 6 matches 06:57")
    check(not cr.cron_matches("57 6 * * *", _lt(2026, 7, 2, 7, 57)), "57 6 skips 07:57")
    check(not cr.cron_matches("57 6 * * *", _lt(2026, 7, 2, 6, 58)), "57 6 skips 06:58")
    # loop-engineering-digest 2 6 * * * — only 06:02.
    check(cr.cron_matches("2 6 * * *", _lt(2026, 7, 2, 6, 2)), "2 6 matches 06:02")
    check(not cr.cron_matches("2 6 * * *", _lt(2026, 7, 2, 6, 3)), "2 6 skips 06:03")
    # pending-questions */30 — :00 and :30.
    check(cr.cron_matches("*/30 * * * *", _lt(2026, 7, 2, 6, 0)), "*/30 matches :00")
    check(cr.cron_matches("*/30 * * * *", _lt(2026, 7, 2, 6, 30)), "*/30 matches :30")
    check(not cr.cron_matches("*/30 * * * *", _lt(2026, 7, 2, 6, 15)), "*/30 skips :15")


def test_dom_dow_or_semantics():
    # Both DOM and DOW restricted → fire if EITHER matches (standard cron).
    # 2026-07-02 is a Thursday (dow=4). Expr: dom=1, dow=4 → matches via dow.
    check(cr.cron_matches("0 6 1 * 4", _lt(2026, 7, 2, 6, 0)),
          "DOM+DOW both restricted: matches on DOW alone (Thu)")
    # 2026-07-01 is a Wednesday (dow=3). Expr: dom=1, dow=4 → matches via dom.
    check(cr.cron_matches("0 6 1 * 4", _lt(2026, 7, 1, 6, 0)),
          "DOM+DOW both restricted: matches on DOM alone (the 1st)")
    # 2026-07-03 Friday, dom=3 → neither dom=1 nor dow=4 → no fire.
    check(not cr.cron_matches("0 6 1 * 4", _lt(2026, 7, 3, 6, 0)),
          "DOM+DOW both restricted: no match when neither hits")
    # Only DOM restricted (dow=*) → AND semantics degrade to DOM-only.
    check(cr.cron_matches("0 6 3 * *", _lt(2026, 7, 3, 6, 0)), "DOM-only matches the 3rd")
    check(not cr.cron_matches("0 6 3 * *", _lt(2026, 7, 2, 6, 0)), "DOM-only skips the 2nd")


def test_every_3_days_dom():
    # New agent-landscape schedule uses */3 day-of-month. Verify it fires on
    # the 1st, 4th, 7th... and not on the 2nd/3rd.
    expr = "4 6 */3 * *"
    check(cr.cron_matches(expr, _lt(2026, 7, 1, 6, 4)), "*/3 DOM matches the 1st")
    check(cr.cron_matches(expr, _lt(2026, 7, 4, 6, 4)), "*/3 DOM matches the 4th")
    check(not cr.cron_matches(expr, _lt(2026, 7, 2, 6, 4)), "*/3 DOM skips the 2nd")
    check(not cr.cron_matches(expr, _lt(2026, 7, 4, 6, 5)), "*/3 DOM respects minute")


def test_bad_expr_raises():
    try:
        cr.cron_matches("1 2 3", _lt(2026, 7, 2, 6, 0))
        check(False, "4-field expr should raise ValueError")
    except ValueError:
        check(True, "malformed expr raises ValueError")


# --- 2. due_since catch-up --------------------------------------------------
def _epoch(y, mo, d, h, mi):
    return int(time.mktime((y, mo, d, h, mi, 0, 0, 0, -1)))


def test_due_since_catchup():
    fire = _epoch(2026, 7, 2, 6, 2)  # 06:02 digest fire
    # Machine "woke" at 06:10; last recorded fire was yesterday 06:03.
    last = _epoch(2026, 7, 1, 6, 3)
    now = _epoch(2026, 7, 2, 6, 10)
    check(cr.due_since("2 6 * * *", last, now),
          "due_since catches a fire that landed before the current tick")
    # No fire in the window → not due.
    last2 = _epoch(2026, 7, 2, 6, 3)
    now2 = _epoch(2026, 7, 2, 6, 10)
    check(not cr.due_since("2 6 * * *", last2, now2),
          "due_since false when no fire-minute in window")
    # Catch-up is bounded — a fire older than MAX_CATCHUP_SECONDS is not
    # resurrected. last is 3 days ago, but window only looks back 24h.
    last3 = _epoch(2026, 6, 28, 0, 0)
    now3 = _epoch(2026, 7, 2, 6, 10)  # 06:02 fire today is within 24h → still due
    check(cr.due_since("2 6 * * *", last3, now3),
          "today's fire still due even with an ancient last-fire (bounded window)")


# --- 3. emit_task shape -----------------------------------------------------
def test_emit_task_prompt():
    with tempfile.TemporaryDirectory() as d:
        cr.TASKS_DIR = Path(d)
        path = cr.emit_task("digest", {"prompt": "do the thing"})
        body = path.read_text()
        check(path.name.startswith("task-cron-digest-"), "task filename carries name")
        check("task: do the thing" in body, "prompt body written")
        check("source: cron" in body, "source is cron")
        check("user_id: cron-runner" in body, "user_id is cron-runner")
        check("access_tier: owner" in body, "access_tier owner")
        check("priority: low" in body, "priority low")


def test_emit_task_prompt_skill():
    with tempfile.TemporaryDirectory() as d:
        cr.TASKS_DIR = Path(d)
        path = cr.emit_task("brief", {"prompt_skill": "morning-briefing"})
        body = path.read_text()
        check("task: /morning-briefing" in body, "prompt_skill rendered as slash command")


# --- 4. run() tick: launchd-flag filtering + state persistence --------------
def test_run_only_fires_launchd_entries():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        cr.TASKS_DIR = root / "tasks"
        cr.CRONS_FILE = root / "crons.json"
        cr.STATE_FILE = root / "state" / "cron-runner-state.json"
        # A launchd-owned entry that is due, and a session-owned one that is
        # also "due" by expression but must be skipped.
        now = _epoch(2026, 7, 2, 6, 2)
        import json
        cr.CRONS_FILE.write_text(json.dumps([
            {"name": "digest", "cron": "2 6 * * *", "prompt": "x", "launchd": True},
            {"name": "session-loop", "cron": "*/5 * * * *", "prompt_skill": "proactive-loop"},
        ]))
        # Seed state so "last" is just before today's fire (forces due).
        cr.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        cr.STATE_FILE.write_text(json.dumps({
            "digest": _epoch(2026, 7, 2, 6, 1),
            "session-loop": _epoch(2026, 7, 2, 6, 1),
        }))
        emitted = cr.run(now_epoch=now)
        check(emitted == ["digest"], "only the launchd entry fires, session entry skipped")
        files = list(cr.TASKS_DIR.glob("task-cron-*.txt"))
        check(len(files) == 1, "exactly one task file emitted")

        # Second tick at the same minute — state was persisted, so no re-fire.
        emitted2 = cr.run(now_epoch=now)
        check(emitted2 == [], "no double-fire: persisted state suppresses re-emit")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"{name}:")
            fn()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        sys.exit(1)
    print("ALL PASSED")


if __name__ == "__main__":
    _run_all()
