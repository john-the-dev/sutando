#!/usr/bin/env python3
"""Tests for src/health-check.py — pure helper functions.

Covers:
  a) _extract_close_code()  — regex extraction from log lines
  b) _extract_close_reason() — regex extraction from log lines
  c) _find_all()             — substring index generator
  d) _extract_body()         — brace-matched body extraction
  e) _slack_failures()       — filter: on-demand warns excluded, real fails kept
  f) check_task_queue()      — queue-size + age threshold logic

Run: python3 tests/health-check.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_tmp_ws = tempfile.mkdtemp(prefix="hc-boot-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))
spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
_mod = importlib.util.module_from_spec(spec)
sys.modules["health_check"] = _mod
spec.loader.exec_module(_mod)
del os.environ["SUTANDO_WORKSPACE"]

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


# ---------------------------------------------------------------------------
# (a) _extract_close_code
# ---------------------------------------------------------------------------

def _test_extract_close_code():
    f = _mod._extract_close_code

    _check("ecc-found",     f("ws close code=1006 reason=abnormal") == "1006")
    _check("ecc-1000",      f("code=1000") == "1000")
    _check("ecc-not-found", f("no code here") is None)
    _check("ecc-empty",     f("") is None)
    # Only the first match
    _check("ecc-first",     f("code=1001 code=1002") == "1001")


_test_extract_close_code()


# ---------------------------------------------------------------------------
# (b) _extract_close_reason
# ---------------------------------------------------------------------------

def _test_extract_close_reason():
    f = _mod._extract_close_reason

    _check("ecr-found",       f('reason="going away"') == "going away")
    _check("ecr-empty-str",   f('reason=""') == "")
    _check("ecr-not-found",   f("no reason here") is None)
    _check("ecr-no-quotes",   f("reason=plain") is None)
    _check("ecr-multi-word",  f('reason="connection closed"') == "connection closed")


_test_extract_close_reason()


# ---------------------------------------------------------------------------
# (c) _find_all
# ---------------------------------------------------------------------------

def _test_find_all():
    f = _mod._find_all

    # No occurrences → empty
    _check("fa-empty",    list(f("hello", "z")) == [])

    # Single occurrence
    _check("fa-single",   list(f("hello", "ell")) == [1])

    # Multiple non-overlapping
    _check("fa-multi",    list(f("ababab", "ab")) == [0, 2, 4])

    # Non-overlapping: advances by len(needle), so "aaa"/"aa" → [0] only
    _check("fa-non-overlap", list(f("aaa", "aa")) == [0])

    # Full string
    _check("fa-full",     list(f("abc", "abc")) == [0])

    # Needle longer than haystack
    _check("fa-longer",   list(f("ab", "abcdef")) == [])


_test_find_all()


# ---------------------------------------------------------------------------
# (d) _extract_body
# ---------------------------------------------------------------------------

def _test_extract_body():
    f = _mod._extract_body

    # Simple function body
    code = "function foo() { return 1; }"
    result = f(code, 0)
    _check("eb-simple",     result == "{ return 1; }", f"got {result!r}")

    # Nested braces
    code2 = "function bar() { if (x) { y(); } return z; }"
    result2 = f(code2, 0)
    _check("eb-nested",     result2 == "{ if (x) { y(); } return z; }", f"got {result2!r}")

    # start after first brace
    code3 = "prelude { inner { } }"
    result3 = f(code3, 8)
    _check("eb-offset",     result3 == "{ inner { } }", f"got {result3!r}")

    # No brace → empty string
    result4 = f("no brace here", 0)
    _check("eb-no-brace",   result4 == "")

    # Brace not found at start position
    result5 = f("abc", 10)
    _check("eb-past-end",   result5 == "")

    # Unmatched open brace — returns up to 2000 chars
    code5 = "{" + "x" * 10
    result5b = f(code5, 0)
    _check("eb-unmatched",  len(result5b) <= 2000)
    _check("eb-unmatched-starts", result5b.startswith("{"))


_test_extract_body()


# ---------------------------------------------------------------------------
# (e) _slack_failures
# ---------------------------------------------------------------------------

def _test_slack_failures():
    f = _mod._slack_failures

    # Empty → empty
    _check("sf-empty",  f([]) == [])

    # Hard statuses all pass through
    for st in ("down", "missing", "not_loaded", "fail", "stale"):
        result = f([{"name": "x", "status": st, "detail": ""}])
        _check(f"sf-hard-{st}", len(result) == 1, f"status={st} got {result}")

    # warn without "on-demand" → included
    result = f([{"name": "x", "status": "warn", "detail": "bridge crashed"}])
    _check("sf-warn-real", len(result) == 1)

    # warn with "on-demand" → excluded
    result = f([{"name": "discord-voice", "status": "warn",
                 "detail": "not running (on-demand)"}])
    _check("sf-warn-on-demand-excluded", len(result) == 0, f"got {result}")

    # ok → excluded
    result = f([{"name": "x", "status": "ok", "detail": "all good"}])
    _check("sf-ok-excluded", len(result) == 0)

    # Mixed: one real fail + one on-demand warn + one ok
    checks = [
        {"name": "core-loop", "status": "stale", "detail": "stale for 10min"},
        {"name": "disc-voice", "status": "warn", "detail": "not running (on-demand)"},
        {"name": "voice-agent", "status": "ok", "detail": ""},
    ]
    result = f(checks)
    _check("sf-mixed-count", len(result) == 1)
    _check("sf-mixed-stale-kept", result[0]["name"] == "core-loop")

    # warn with "on-demand" in detail but detail=None → included (not a string)
    result2 = f([{"name": "x", "status": "warn", "detail": None}])
    _check("sf-warn-none-detail", len(result2) == 1)


_test_slack_failures()


# ---------------------------------------------------------------------------
# (f) check_task_queue
# ---------------------------------------------------------------------------

def _test_check_task_queue():
    with tempfile.TemporaryDirectory() as tmp:
        tasks_dir = Path(tmp) / "tasks"
        _mod.WORKSPACE_DIR = Path(tmp)

        # tasks/ doesn't exist → ok
        result = _mod.check_task_queue()
        _check("ctq-no-dir-ok",     result["status"] == "ok")

        # tasks/ exists but empty → ok
        tasks_dir.mkdir()
        result = _mod.check_task_queue()
        _check("ctq-empty-ok",      result["status"] == "ok")
        _check("ctq-empty-detail",  "empty" in result["detail"])

        # Under threshold: 2 files (below count=3), old age → ok
        for i in range(2):
            p = tasks_dir / f"task-{i:03d}.txt"
            p.write_text(f"id: task-{i}\n")
            old = time.time() - 3600
            os.utime(p, (old, old))
        result = _mod.check_task_queue(threshold_count=3, threshold_age_sec=300)
        _check("ctq-under-count-ok", result["status"] == "ok")

        # Over both thresholds: 4 files (>3) AND oldest >300s → warn
        for i in range(2, 4):
            p = tasks_dir / f"task-{i:03d}.txt"
            p.write_text(f"id: task-{i}\n")
            old = time.time() - 3600
            os.utime(p, (old, old))
        result = _mod.check_task_queue(threshold_count=3, threshold_age_sec=300)
        _check("ctq-over-both-warn", result["status"] == "warn")
        _check("ctq-warn-count",     "4 tasks" in result["detail"])

        # Fresh files: count > threshold but age ≤ threshold → ok (spike of new tasks)
        tasks_dir2 = Path(tmp) / "tasks2"
        tasks_dir2.mkdir()
        _mod.WORKSPACE_DIR = Path(tmp) / "sub"
        (Path(tmp) / "sub").mkdir()
        (Path(tmp) / "sub" / "tasks").symlink_to(tasks_dir2)
        for i in range(4):
            p = tasks_dir2 / f"task-{i:03d}.txt"
            p.write_text(f"id: task-{i}\n")
            # Very recent mtime (< threshold_age_sec)
        result2 = _mod.check_task_queue(threshold_count=3, threshold_age_sec=300)
        _check("ctq-fresh-burst-ok", result2["status"] == "ok",
               f"got {result2}")

        # Archive-subdirs don't count — only *.txt at tasks/ root
        tasks_dir3 = Path(tmp) / "tasks3"
        tasks_dir3.mkdir()
        archive = tasks_dir3 / "archive" / "2026-05"
        archive.mkdir(parents=True)
        # Put files in archive (shouldn't count)
        for i in range(5):
            (archive / f"task-{i:03d}.txt").write_text(f"id: task-{i}\n")
        _mod.WORKSPACE_DIR = Path(tmp) / "sub3"
        (Path(tmp) / "sub3").mkdir()
        (Path(tmp) / "sub3" / "tasks").symlink_to(tasks_dir3)
        result3 = _mod.check_task_queue(threshold_count=3, threshold_age_sec=300)
        _check("ctq-archive-not-counted", result3["status"] == "ok",
               f"got {result3}")


_test_check_task_queue()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"health-check: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
