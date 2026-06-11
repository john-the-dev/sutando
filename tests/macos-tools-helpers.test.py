#!/usr/bin/env python3
"""Regression guard: pure helpers in the macos-tools skill scripts.

email-sender.py:
  escape(s) -> str
    Escapes backslashes, double-quotes, and newlines for AppleScript.

calendar-reader.py:
  format_for_humans(data) -> str
    Renders event dict to plain-text briefing.
    data: {"events": [...], "days": int} or {"error": str}.

reminders.py:
  list_reminders(include_completed) -> list[dict]
    Parsing logic — "missing value" substitution, "|||" splitting, bool coerce.
    run_applescript() is monkey-patched for IO-free testing.

Run: python3 tests/macos-tools-helpers.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MACOS = REPO / "skills" / "macos-tools" / "scripts"

# ---------------------------------------------------------------------------
# Load modules
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


_email  = _load("email_sender",   MACOS / "email-sender.py")
_cal    = _load("calendar_reader", MACOS / "calendar-reader.py")
_rem    = _load("reminders",       MACOS / "reminders.py")

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
# email-sender.escape
# ---------------------------------------------------------------------------

def _test_escape():
    e = _email.escape

    # Plain string → unchanged
    _check("esc-plain",     e("hello world") == "hello world")

    # Backslash escaped
    _check("esc-backslash", e("a\\b") == "a\\\\b")

    # Double-quote escaped
    _check("esc-dquote",    e('say "hi"') == 'say \\"hi\\"')

    # Newline escaped
    _check("esc-newline",   e("line1\nline2") == "line1\\nline2")

    # All three together
    out = e('He said "hello\\world"\nhow\'s it?')
    _check("esc-combined-bs",  "\\\\" in out)
    _check("esc-combined-dq",  '\\"' in out)
    _check("esc-combined-nl",  "\\n" in out)

    # Empty string → empty string
    _check("esc-empty", e("") == "")


_test_escape()


# ---------------------------------------------------------------------------
# calendar-reader.format_for_humans
# ---------------------------------------------------------------------------

def _test_format_for_humans():
    f = _cal.format_for_humans

    # Error case
    out = f({"error": "permission denied"})
    _check("fh-error", "permission denied" in out)

    # No events
    out = f({"events": [], "days": 7})
    _check("fh-empty", "No events" in out and "7" in out)

    # Single regular (non-all-day) event with location
    data = {
        "days": 7,
        "events": [
            {
                "title": "Standup",
                "start": "Monday Jun 10 at 10:00 AM",
                "calendar": "Work",
                "all_day": False,
                "location": "Zoom",
            }
        ],
    }
    out = f(data)
    _check("fh-title",    "Standup" in out)
    _check("fh-calendar", "[Work]" in out)
    _check("fh-time",     "10:00 AM" in out)
    _check("fh-location", "@ Zoom" in out)
    _check("fh-count",    "1 event" in out)

    # All-day event — time stripped, "(all day)" added
    data2 = {
        "days": 3,
        "events": [
            {
                "title": "Holiday",
                "start": "Wednesday Jun 12 at 12:00 AM",
                "calendar": "Personal",
                "all_day": True,
                "location": "",
            }
        ],
    }
    out2 = f(data2)
    _check("fh-allday",     "(all day)" in out2)
    _check("fh-allday-time", "at 12:00 AM" not in out2)

    # No location → no "@" marker
    data3 = {
        "days": 7,
        "events": [{"title": "Call", "start": "Thu at 3pm", "calendar": "Work",
                    "all_day": False, "location": ""}],
    }
    out3 = f(data3)
    _check("fh-no-location", "@" not in out3)

    # Multiple events — header count correct
    events4 = [
        {"title": "A", "start": "Mon", "calendar": "X", "all_day": False, "location": ""},
        {"title": "B", "start": "Tue", "calendar": "X", "all_day": False, "location": ""},
        {"title": "C", "start": "Wed", "calendar": "X", "all_day": False, "location": ""},
    ]
    out4 = f({"days": 7, "events": events4})
    _check("fh-multi-count", "3 events" in out4)


_test_format_for_humans()


# ---------------------------------------------------------------------------
# reminders.list_reminders — monkey-patched run_applescript
# ---------------------------------------------------------------------------

def _test_list_reminders():
    lr = _rem.list_reminders

    # Normal output
    def _fake_normal(script):
        lines = (
            "Work|||Buy milk|||Monday Jun 10 2026 at 3:00:00 PM|||false|||Get whole milk\n"
            "Personal|||Pay bills|||missing value|||true|||missing value\n"
        )
        return lines, ""

    _rem.run_applescript = _fake_normal
    result = lr()
    _check("lr-count",     len(result) == 2)
    _check("lr-name",      result[0]["name"] == "Buy milk")
    _check("lr-list",      result[0]["list"] == "Work")
    _check("lr-due",       "Jun 10" in result[0]["due"])
    _check("lr-completed", result[0]["completed"] is False)
    _check("lr-body",      result[0]["body"] == "Get whole milk")

    # "missing value" replaced with ""
    _check("lr-due-missing",  result[1]["due"] == "")
    _check("lr-body-missing", result[1]["body"] == "")
    _check("lr-completed-true", result[1]["completed"] is True)

    # Error from AppleScript → [{error: ...}]
    def _fake_error(script):
        return "", "permission denied"

    _rem.run_applescript = _fake_error
    err_result = lr()
    _check("lr-error-list",  len(err_result) == 1)
    _check("lr-error-key",   "error" in err_result[0])
    _check("lr-error-value", "permission denied" in err_result[0]["error"])

    # Empty output → []
    def _fake_empty(script):
        return "", ""

    _rem.run_applescript = _fake_empty
    _check("lr-empty", lr() == [])

    # Line with too few fields skipped
    def _fake_partial(script):
        return "Work|||Only two fields\nWork|||A|||B|||false|||note\n", ""

    _rem.run_applescript = _fake_partial
    partial = lr()
    _check("lr-partial-skip", len(partial) == 1)
    _check("lr-partial-ok",   partial[0]["name"] == "A")

    # include_completed filter is passed to script (smoke-test: no crash)
    def _fake_any(script):
        return "", ""

    _rem.run_applescript = _fake_any
    _check("lr-include-completed", lr(include_completed=True) == [])


_test_list_reminders()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"macos-tools-helpers: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
