#!/usr/bin/env python3
"""Regression guard: all pure functions in src/result_channel_key.py.

sanitize_key(raw) -> str
  Collapses to [A-Za-z0-9_-]; empty/None/falsy → "unknown".
  Leading/trailing whitespace stripped first.

result_filename(channel_key, task_id) -> str
  Scoped filename: "<sanitized_key>.<task_id>.txt".

parse_result_filename(filename) -> (key | None, task_id)
  Scoped form "<key>.task-{id}[.txt]" → (key, "task-{id}").
  Anything else (legacy flat, voice-, proactive-) → (None, basename).

result_belongs_to(filename, channel_key) -> bool
  True iff filename is the scoped form claimed by channel_key.
  Must end with ".txt"; .tmp/.partial etc. return False.
  task_id must start with "task-".

discord_voice_key(vc_id) -> str  — "dvoice-<sanitize_key(vc_id)>"
phone_call_key(call_sid) -> str  — "phone-<sanitize_key(call_sid)>"

Run: python3 tests/result-channel-key.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "result_channel_key", REPO / "src" / "result_channel_key.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["result_channel_key"] = _mod
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
# sanitize_key
# ---------------------------------------------------------------------------

def _test_sanitize_key():
    s = _mod.sanitize_key

    # Alphanumeric + allowed chars pass through unchanged
    _check("sk-alpha",       s("dvoice-123_abc") == "dvoice-123_abc")

    # Spaces collapsed to "-"
    _check("sk-space",       s("my channel") == "my-channel")

    # Dot collapsed to "-"
    _check("sk-dot",         s("chan.nel") == "chan-nel")

    # Slash collapsed to "-"
    _check("sk-slash",       "." not in s("a/b") and "/" not in s("a/b"))

    # None → "unknown"
    _check("sk-none",        s(None) == "unknown")

    # Empty string → "unknown"
    _check("sk-empty",       s("") == "unknown")

    # All-unsafe chars → all collapsed (non-empty result)
    _check("sk-all-unsafe",  s("...") == "---")

    # Leading/trailing whitespace stripped (not collapsed to dashes)
    _check("sk-strip",       s("  hello  ") == "hello")

    # Numeric-only string preserved
    _check("sk-numeric",     s("1234567890") == "1234567890")


_test_sanitize_key()


# ---------------------------------------------------------------------------
# result_filename
# ---------------------------------------------------------------------------

def _test_result_filename():
    rf = _mod.result_filename

    # Standard usage
    _check("rf-basic", rf("dvoice-123456789", "task-1718000000") ==
           "dvoice-123456789.task-1718000000.txt")

    # Unsafe key sanitized
    out2 = rf("my channel!", "task-42")
    _check("rf-sanitize", "!" not in out2 and " " not in out2)
    _check("rf-suffix",   out2.endswith(".txt"))

    # Underscore in key preserved
    _check("rf-underscore", rf("phone_call", "task-99") == "phone_call.task-99.txt")


_test_result_filename()


# ---------------------------------------------------------------------------
# parse_result_filename
# ---------------------------------------------------------------------------

def _test_parse_result_filename():
    p = _mod.parse_result_filename

    # Scoped form with .txt suffix
    key, task_id = p("dvoice-123.task-1718000000.txt")
    _check("prf-key",     key == "dvoice-123")
    _check("prf-task-id", task_id == "task-1718000000")

    # Scoped form without .txt suffix
    key2, tid2 = p("dvoice-123.task-1718000000")
    _check("prf-no-ext-key",  key2 == "dvoice-123")
    _check("prf-no-ext-task", tid2 == "task-1718000000")

    # Legacy flat form → (None, basename)
    key3, tid3 = p("task-1718000000.txt")
    _check("prf-legacy-key",  key3 is None)
    _check("prf-legacy-task", tid3 == "task-1718000000")

    # voice- prefix → (None, basename)
    key4, tid4 = p("voice-1234567890.txt")
    _check("prf-voice-key",  key4 is None)
    _check("prf-voice-name", tid4 == "voice-1234567890")

    # proactive- prefix → (None, basename)
    key5, _ = p("proactive-1718000000.txt")
    _check("prf-proactive", key5 is None)

    # Phone-scoped form
    key7, tid7 = p("phone-CA1234567890abcdef.task-1718000001.txt")
    _check("prf-phone-key",  key7 == "phone-CA1234567890abcdef")
    _check("prf-phone-task", tid7 == "task-1718000001")


_test_parse_result_filename()


# ---------------------------------------------------------------------------
# result_belongs_to
# ---------------------------------------------------------------------------

def _test_result_belongs_to():
    rb = _mod.result_belongs_to

    # Exact match
    _check("rbt-match",    rb("dvoice-123.task-1.txt", "dvoice-123") is True)

    # Different key → False
    _check("rbt-diff-key", rb("dvoice-123.task-1.txt", "dvoice-456") is False)

    # Legacy flat → False (key is None)
    _check("rbt-legacy",   rb("task-1.txt", "dvoice-123") is False)

    # Missing .txt suffix → False
    _check("rbt-no-ext",   rb("dvoice-123.task-1", "dvoice-123") is False)

    # Temp file (.tmp suffix) → False
    _check("rbt-tmp",      rb("dvoice-123.task-1.txt.tmp", "dvoice-123") is False)

    # task_id must start with "task-" (proactive is not a task)
    _check("rbt-non-task", rb("dvoice-123.proactive-1.txt", "dvoice-123") is False)

    # Phone channel match
    _check("rbt-phone",    rb("phone-CA123.task-2.txt", "phone-CA123") is True)

    # Channel key comparison uses sanitize_key (unsafe chars normalized)
    _check("rbt-sanitize-match",
           rb("dvoice-123.task-1.txt", "dvoice-123") is True)


_test_result_belongs_to()


# ---------------------------------------------------------------------------
# discord_voice_key / phone_call_key
# ---------------------------------------------------------------------------

def _test_typed_constructors():
    dv = _mod.discord_voice_key
    pk = _mod.phone_call_key

    # Normal discord voice channel snowflake
    _check("dvk-basic",   dv("1234567890123456") == "dvoice-1234567890123456")

    # None → "dvoice-unknown"
    _check("dvk-none",    dv(None) == "dvoice-unknown")

    # Unsafe chars in vc_id sanitized
    out = dv("vc:12:34")
    _check("dvk-unsafe",  out.startswith("dvoice-") and ":" not in out)

    # Phone call sid
    sid = "CA1234567890abcdef1234567890abcdef"
    _check("pk-basic",    pk(sid) == f"phone-{sid}")

    # None → "phone-unknown"
    _check("pk-none",     pk(None) == "phone-unknown")


_test_typed_constructors()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"result-channel-key: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
