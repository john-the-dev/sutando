#!/usr/bin/env python3
"""Tests for skills/call-diagnostics/scripts/diagnose.py — pure helpers.

Covers:
  a) merge_timeline()    — event + toolCall merge + sort
  b) parse_ts()          — ISO string → datetime (or None)
  c) _ts_short()         — timestamp abbreviation
  d) categorize_issue()  — issue dict → category string
  e) diagnose()          — full call dict → issue list (no DB)

Run: python3 tests/call-diagnostics.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "call-diagnostics" / "scripts" / "diagnose.py"

_tmp_ws = tempfile.mkdtemp(prefix="cd-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))

spec = importlib.util.spec_from_file_location("call_diagnostics", SCRIPT)
_mod = importlib.util.module_from_spec(spec)
sys.modules["call_diagnostics"] = _mod
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


# Helper: build a call dict with typed lists
def _call(events=None, tool_calls=None):
    return {"events": events or [], "toolCalls": tool_calls or []}


def _event(ts, detail):
    return {"timestamp": ts, "event": detail}


def _tool_call(ts, name, duration_ms):
    return {"timestamp": ts, "name": name, "durationMs": duration_ms}


# ---------------------------------------------------------------------------
# (a) merge_timeline
# ---------------------------------------------------------------------------

def _test_merge_timeline():
    f = _mod.merge_timeline

    # Empty call → empty list
    _check("mt-empty",     f(_call()) == [])

    # Events only
    result = f(_call(events=[_event("2026-01-01T10:00:00Z", "start")]))
    _check("mt-event-only", len(result) == 1)
    _check("mt-event-type", result[0]["type"] == "event")
    _check("mt-event-detail", result[0]["detail"] == "start")

    # ToolCalls only
    result = f(_call(tool_calls=[_tool_call("2026-01-01T10:00:01Z", "record", 500)]))
    _check("mt-tool-only", len(result) == 1)
    _check("mt-tool-type", result[0]["type"] == "toolCall")
    _check("mt-tool-name", result[0]["name"] == "record")
    _check("mt-tool-dur",  result[0]["durationMs"] == 500)
    _check("mt-tool-detail", "record (500ms)" in result[0]["detail"])

    # Sorted by timestamp
    call = _call(
        events=[_event("2026-01-01T10:00:02Z", "e2"), _event("2026-01-01T10:00:00Z", "e1")],
        tool_calls=[_tool_call("2026-01-01T10:00:01Z", "t1", 100)],
    )
    result = f(call)
    _check("mt-sort-len",   len(result) == 3)
    _check("mt-sort-order", [r["ts"][17:19] for r in result] == ["00", "01", "02"])


_test_merge_timeline()


# ---------------------------------------------------------------------------
# (b) parse_ts
# ---------------------------------------------------------------------------

def _test_parse_ts():
    f = _mod.parse_ts

    # Valid ISO-Z string
    dt = f("2026-01-01T12:00:00Z")
    _check("pts-valid",    dt is not None)
    _check("pts-hour",     dt.hour == 12)
    _check("pts-minute",   dt.minute == 0)

    # ISO with +00:00 offset
    dt2 = f("2026-06-10T15:30:00+00:00")
    _check("pts-offset",   dt2 is not None)

    # Invalid string → None
    _check("pts-invalid",  f("not-a-date") is None)

    # Empty string → None
    _check("pts-empty",    f("") is None)

    # None-like (TypeError caught) → None
    _check("pts-none",     f(None) is None)


_test_parse_ts()


# ---------------------------------------------------------------------------
# (c) _ts_short
# ---------------------------------------------------------------------------

def _test_ts_short():
    f = _mod._ts_short

    # Full ISO → HH:MM:SS slice
    _check("tss-full",   f("2026-01-01T12:34:56Z") == "12:34:56")

    # Exactly 19 chars → returned as-is (not sliced — condition is > 19)
    s19 = "2026-01-01T12:34:56"
    _check("tss-exact",  f(s19) == s19)

    # Shorter than 19 → returned as-is
    short = "12:34"
    _check("tss-short",  f(short) == short)

    # Empty → empty
    _check("tss-empty",  f("") == "")


_test_ts_short()


# ---------------------------------------------------------------------------
# (d) categorize_issue
# ---------------------------------------------------------------------------

def _test_categorize_issue():
    f = _mod.categorize_issue

    # Fast return → "... returned too fast (failed)"
    issue = {"issue": "record_audio returned in 5ms — likely failed silently", "detail": ""}
    result = f(issue)
    _check("ci-fast-return", "returned too fast (failed)" in result, result)

    # Wrong tool
    issue = {"issue": "wrong tool: describe_screen instead of work",
             "detail": "code/repo questions should use work."}
    result = f(issue)
    _check("ci-wrong-tool", result.startswith("Wrong tool:"), result)

    # Hallucination — playing
    issue = {"issue": "possible hallucination: \"video is playing\"", "detail": "playing"}
    result = f(issue)
    _check("ci-halluc-playing", "Hallucinated: 'video is playing'" == result, result)

    # Hallucination — recording complete
    issue = {"issue": "possible hallucination: \"recording is complete\"", "detail": ""}
    result = f(issue)
    _check("ci-halluc-recording", "Hallucinated: 'recording is complete'" == result, result)

    # Auto-invoked
    issue = {"issue": "auto-invoked scroll_and_describe — no matching user request", "detail": ""}
    result = f(issue)
    _check("ci-auto-invoked", result == "Auto-played video without user asking", result)

    # Auto-play
    issue = {"issue": "auto-play after recording — user didn't ask", "detail": ""}
    result = f(issue)
    _check("ci-auto-play", result == "Auto-played video without user asking", result)

    # Inline delegated via work — recording
    issue = {"issue": "inline task delegated via work: \"record this\"",
             "detail": "use work: \"record this\""}
    result = f(issue)
    _check("ci-inline-record", "Recording delegated via work" in result, result)

    # User correction
    issue = {"issue": "user correction: \"you're not asking\"", "detail": ""}
    result = f(issue)
    _check("ci-user-correction", "User correction" in result or "Gemini" in result, result)

    # Unmet expectation
    issue = {"issue": "unmet expectation — user repeated request", "detail": ""}
    result = f(issue)
    _check("ci-unmet", result == "User repeated request (not understood)", result)

    # STT timestamp lag
    issue = {"issue": "caller speech logged 8s after record tool call", "detail": ""}
    result = f(issue)
    _check("ci-stt-lag", result == "STT timestamp lag", result)

    # Repeated failures
    issue = {"issue": "record_audio failed 3 times in this call", "detail": ""}
    result = f(issue)
    _check("ci-failed-repeated", "failed repeatedly" in result, result)

    # Fallthrough → "Other: ..."
    issue = {"issue": "something unusual happened", "detail": ""}
    result = f(issue)
    _check("ci-other", result.startswith("Other:"), result)


_test_categorize_issue()


# ---------------------------------------------------------------------------
# (e) diagnose — constructed call dicts
# ---------------------------------------------------------------------------

def _test_diagnose():
    f = _mod.diagnose

    # Empty call → no issues
    issues = f(_call())
    _check("dg-empty",       issues == [])

    # Fast tool call (<10ms) → error issue
    call = _call(tool_calls=[_tool_call("2026-01-01T10:00:00Z", "record_audio", 5)])
    issues = f(call)
    _check("dg-fast-tool",   len(issues) >= 1)
    _check("dg-fast-sev",    issues[0]["severity"] == "error")
    _check("dg-fast-msg",    "5ms" in issues[0]["issue"])

    # Normal-speed tool call (≥10ms) → no fast-return issue
    call = _call(tool_calls=[_tool_call("2026-01-01T10:00:00Z", "record_audio", 100)])
    issues = f(call)
    fast_issues = [i for i in issues if "returned in" in i["issue"]]
    _check("dg-normal-speed", fast_issues == [])

    # work tool fast is NOT flagged (explicitly excluded)
    call = _call(tool_calls=[_tool_call("2026-01-01T10:00:00Z", "work", 0)])
    issues = f(call)
    fast_issues = [i for i in issues if "returned in" in i["issue"]]
    _check("dg-work-excluded", fast_issues == [])

    # Hallucination: sutando speaks without prior tool result
    call = _call(events=[_event("2026-01-01T10:00:00Z", "sutando: the video is currently playing")])
    issues = f(call)
    halluc = [i for i in issues if "hallucination" in i["issue"].lower()]
    _check("dg-halluc-found",  len(halluc) >= 1)
    _check("dg-halluc-warn",   halluc[0]["severity"] == "warn")

    # Inline task delegated for recording keyword
    call = _call(events=[_event("2026-01-01T10:00:00Z", "task_delegated: record the screen")])
    issues = f(call)
    inline = [i for i in issues if "inline task" in i["issue"].lower()]
    _check("dg-inline-record",  len(inline) >= 1)
    _check("dg-inline-sev",     inline[0]["severity"] == "error")

    # Auto-play: play_recording called right after auto-stop, no caller speech between
    events = [
        _event("2026-01-01T10:00:00Z", "auto-stop: recording stopped"),
        _event("2026-01-01T10:00:01Z", "tool_call:play_recording"),
    ]
    call = _call(events=events)
    issues = f(call)
    auto_play = [i for i in issues if "auto-play" in i["issue"].lower()]
    _check("dg-autoplay-found", len(auto_play) >= 1)

    # No auto-play if caller speech is between auto-stop and play_recording
    # Use whole-second timestamps to avoid string-sort issues with fractional seconds
    events = [
        _event("2026-01-01T10:00:00Z", "auto-stop: recording stopped"),
        _event("2026-01-01T10:00:01Z", "caller: play it please"),
        _event("2026-01-01T10:00:02Z", "tool_call:play_recording"),
    ]
    call = _call(events=events)
    issues = f(call)
    auto_play = [i for i in issues if "auto-play" in i["issue"].lower()]
    _check("dg-autoplay-caller-ok", auto_play == [])


_test_diagnose()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"call-diagnostics: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
