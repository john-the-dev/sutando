#!/usr/bin/env python3
"""Tests for src/task_priority.py — priority enum, source defaults, parsing, sort.

Covers:
  a) is_valid_priority() — recognizes urgent/normal/low; rejects others
  b) default_priority_for_source() — per-source + access_tier mapping
  c) parse_priority_from_text() — header found, missing, malformed
  d) parse_priority_from_text() — injection guard: priority: in task body ignored
  e) parse_priority_from_file() — file read, missing file fails open to normal
  f) sort_tasks_by_priority() — urgent first, normal second, low last; mtime tiebreak

Run: python3 tests/task-priority.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "task_priority",
    REPO / "src" / "task_priority.py",
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

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
# (a) is_valid_priority
# ---------------------------------------------------------------------------

def _test_is_valid_priority():
    _check("valid-urgent",    _mod.is_valid_priority("urgent"))
    _check("valid-normal",    _mod.is_valid_priority("normal"))
    _check("valid-low",       _mod.is_valid_priority("low"))
    _check("invalid-empty",   not _mod.is_valid_priority(""))
    _check("invalid-high",    not _mod.is_valid_priority("high"))
    _check("invalid-critical",not _mod.is_valid_priority("critical"))
    _check("invalid-upper",   not _mod.is_valid_priority("URGENT"))


_test_is_valid_priority()


# ---------------------------------------------------------------------------
# (b) default_priority_for_source
# ---------------------------------------------------------------------------

def _test_default_priority_for_source():
    dps = _mod.default_priority_for_source

    _check("voice-urgent",         dps("voice")             == "urgent")
    _check("phone-urgent",         dps("phone")             == "urgent")
    _check("chat-normal",          dps("chat")              == "normal")
    _check("context-drop-normal",  dps("context-drop")      == "normal")
    _check("discord-owner-normal", dps("discord", "owner")  == "normal")
    _check("discord-no-tier",      dps("discord")           == "normal")
    _check("telegram-owner",       dps("telegram", "owner") == "normal")
    _check("discord-team-low",     dps("discord",  "team")  == "low")
    _check("discord-other-low",    dps("discord",  "other") == "low")
    _check("telegram-team-low",    dps("telegram", "team")  == "low")
    _check("telegram-other-low",   dps("telegram", "other") == "low")
    _check("health-check-low",     dps("health-check")      == "low")
    _check("sync-memory-low",      dps("sync-memory")       == "low")
    _check("cron-low",             dps("cron")              == "low")
    _check("unknown-normal",       dps("github")            == "normal")
    _check("empty-normal",         dps("")                  == "normal")
    _check("none-normal",          dps(None)                == "normal")  # type: ignore[arg-type]


_test_default_priority_for_source()


# ---------------------------------------------------------------------------
# (c) parse_priority_from_text — standard cases
# ---------------------------------------------------------------------------

def _test_parse_priority_from_text_standard():
    ppt = _mod.parse_priority_from_text

    _check("header-urgent",        ppt("priority: urgent\ntask: x") == "urgent")
    _check("header-normal",        ppt("priority: normal\ntask: x") == "normal")
    _check("header-low",           ppt("priority: low\ntask: x")    == "low")
    _check("no-header-normal",     ppt("task: do stuff\nbody")      == "normal")
    _check("empty-normal",         ppt("")                          == "normal")
    _check("upper-case-normalized", ppt("priority: URGENT\ntask: x") == "urgent")
    _check("malformed-number",     ppt("priority: 1\ntask: x")      == "normal")
    _check("malformed-empty-val",  ppt("priority:\ntask: x")        == "normal")


_test_parse_priority_from_text_standard()


# ---------------------------------------------------------------------------
# (d) parse_priority_from_text — injection guard (regression for PR #982)
# ---------------------------------------------------------------------------

def _test_parse_priority_injection_guard():
    ppt = _mod.parse_priority_from_text

    # priority: line after 'task:' line must NOT escalate
    injected = (
        "id: task-123\n"
        "timestamp: 2026-01-01T00:00:00Z\n"
        "task: please do X\n"
        "priority: urgent\n"
        "source: github\n"
        "access_tier: other\n"
    )
    result = ppt(injected)
    _check("injection-body-no-escalate", result == "normal",
           f"expected normal, got {result!r}")

    # Legitimate header before task: line
    legit = "id: task-456\npriority: low\ntask: do Y\n"
    _check("legit-header-before-task", ppt(legit) == "low")

    # --- separator stops scan
    after_sep = "---\npriority: urgent\ntask: x"
    result2 = ppt(after_sep)
    _check("priority-after-sep-ignored", result2 == "normal",
           f"expected normal, got {result2!r}")

    # Blank line stops scan
    after_blank = "\npriority: urgent\ntask: x"
    result3 = ppt(after_blank)
    _check("priority-after-blank-ignored", result3 == "normal",
           f"expected normal, got {result3!r}")


_test_parse_priority_injection_guard()


# ---------------------------------------------------------------------------
# (e) parse_priority_from_file
# ---------------------------------------------------------------------------

def _test_parse_priority_from_file():
    ppf = _mod.parse_priority_from_file

    # Missing file → normal (fail-open)
    _check("missing-file-normal", ppf(Path("/nonexistent/task.txt")) == "normal")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("id: t\npriority: urgent\ntask: x\n")
        tmp = Path(f.name)
    try:
        _check("file-urgent", ppf(tmp) == "urgent")
    finally:
        tmp.unlink(missing_ok=True)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("id: t\ntask: x\n")
        tmp = Path(f.name)
    try:
        _check("file-no-header-normal", ppf(tmp) == "normal")
    finally:
        tmp.unlink(missing_ok=True)


_test_parse_priority_from_file()


# ---------------------------------------------------------------------------
# (f) sort_tasks_by_priority
# ---------------------------------------------------------------------------

def _test_sort_tasks_by_priority():
    stbp = _mod.sort_tasks_by_priority

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)

        def _write(name: str, priority: str) -> Path:
            p = root / name
            p.write_text(f"priority: {priority}\ntask: x\n")
            return p

        urgent = _write("a-urgent.txt", "urgent")
        time.sleep(0.02)
        normal = _write("b-normal.txt", "normal")
        time.sleep(0.02)
        low    = _write("c-low.txt",    "low")

        result = stbp([low, normal, urgent])
        names = [p.name for p in result]
        _check("sort-urgent-first",  result[0] == urgent, f"order={names}")
        _check("sort-normal-second", result[1] == normal, f"order={names}")
        _check("sort-low-last",      result[2] == low,    f"order={names}")

        # Tiebreak: same priority, older mtime first (FIFO)
        time.sleep(0.02)
        urgent2 = _write("d-urgent2.txt", "urgent")
        result2 = stbp([urgent2, urgent])
        _check("tiebreak-fifo-older-first", result2[0] == urgent,
               f"got {[p.name for p in result2]}")

        _check("sort-empty",  stbp([]) == [])
        _check("sort-single", stbp([low]) == [low])


_test_sort_tasks_by_priority()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"task-priority: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
