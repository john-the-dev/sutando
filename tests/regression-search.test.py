#!/usr/bin/env python3
"""Tests for skills/regression-search/scripts/ pure functions.

Covers:
  find-regression.py:
    a) parse_transcript()  — transcript text → (role, text) pairs
    b) classify_call()     — verdict + reason classification
    c) find_snippet()      — first keyword hit excerpt

  diagnose-call.py:
    d) _truncate()         — string truncation helper
    e) _ts_iso()           — unix timestamp → ISO-Z string
    f) analyze_turns()     — per-turn analysis dict

Run: python3 tests/regression-search.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "regression-search" / "scripts"

# ---------------------------------------------------------------------------
# Load both modules with a minimal workspace (resolve_workspace called at
# module level so SUTANDO_WORKSPACE must be set before import).
# ---------------------------------------------------------------------------

_tmp_ws = tempfile.mkdtemp(prefix="rs-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_fr = _load("find_regression", SCRIPTS / "find-regression.py")
_dc = _load("diagnose_call", SCRIPTS / "diagnose-call.py")

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
# (a) parse_transcript — find-regression.py
# ---------------------------------------------------------------------------

def _test_parse_transcript_fr():
    f = _fr.parse_transcript

    # Empty → empty
    _check("pt-fr-empty",       f("") == [])

    # Blank lines only → empty
    _check("pt-fr-blanks",      f("\n\n\n") == [])

    # Sutando role maps to "sutando"
    turns = f("Sutando: hello there")
    _check("pt-fr-sutando",     turns == [("sutando", "hello there")])

    # Recipient maps to "user"
    turns = f("Recipient: yes please")
    _check("pt-fr-recipient",   turns == [("user", "yes please")])

    # User maps to "user"
    turns = f("User: can you record")
    _check("pt-fr-user",        turns == [("user", "can you record")])

    # Caller maps to "user"
    turns = f("Caller: hey")
    _check("pt-fr-caller",      turns == [("user", "hey")])

    # Continuation line appended to previous turn
    turns = f("Sutando: first line\ncontinuation text")
    _check("pt-fr-continuation", turns == [("sutando", "first line continuation text")])

    # Continuation ignored if no prior turn
    turns = f("continuation without prefix")
    _check("pt-fr-no-prior",    turns == [])

    # Multi-turn round-trip
    script = "User: record this\nSutando: sure\nUser: thanks"
    turns = f(script)
    _check("pt-fr-multi-len",   len(turns) == 3)
    _check("pt-fr-multi-roles", [r for r, _ in turns] == ["user", "sutando", "user"])

    # Extra whitespace stripped
    turns = f("Sutando:   leading spaces  ")
    _check("pt-fr-strip",       turns[0][1] == "leading spaces")


_test_parse_transcript_fr()


# ---------------------------------------------------------------------------
# (b) classify_call — find-regression.py
# ---------------------------------------------------------------------------

def _test_classify_call():
    f = _fr.classify_call

    # Keyword absent → no-match
    verdict, reasons = f([], "record")
    _check("cc-empty-no-match",   verdict == "no-match")

    # Keyword in user turn only, no failures → working
    turns = [("user", "can you record"), ("sutando", "sure, recording now")]
    verdict, reasons = f(turns, "record")
    _check("cc-working-verdict",  verdict == "working")
    _check("cc-working-no-reasons", reasons == [])

    # Keyword only in Sutando turn → working (no user mention)
    turns = [("sutando", "I am now recording")]
    verdict, reasons = f(turns, "record")
    _check("cc-sutando-only-working", verdict == "working")

    # Refusal after user mentions keyword → broken
    turns = [("user", "can you record"), ("sutando", "I can't do that right now")]
    verdict, reasons = f(turns, "record")
    _check("cc-refusal-broken",   verdict == "broken")
    _check("cc-refusal-reason",   "refusal" in reasons)

    # Error after user mentions keyword → broken
    turns = [("user", "please record"), ("sutando", "something went wrong")]
    verdict, reasons = f(turns, "record")
    _check("cc-error-broken",     verdict == "broken")
    _check("cc-error-reason",     "error" in reasons)

    # Silence marker → broken
    turns = [("user", "record this"), ("sutando", "(silence)")]
    verdict, reasons = f(turns, "record")
    _check("cc-silence-broken",   verdict == "broken")
    _check("cc-silence-reason",   "silence" in reasons)

    # Keyword not in text at all → no-match
    turns = [("user", "play music"), ("sutando", "playing music")]
    verdict, reasons = f(turns, "record")
    _check("cc-no-match-verdict", verdict == "no-match")

    # User repeats verbatim (>=2x, len>6) with keyword → broken
    turns = [
        ("user", "please record this"),
        ("sutando", "hmm"),
        ("user", "please record this"),  # verbatim repeat
    ]
    verdict, reasons = f(turns, "record")
    _check("cc-repeat-broken",    verdict == "broken")
    _check("cc-repeat-reason",    any("repeated" in r for r in reasons))

    # Short repeat (<= 6 chars) NOT flagged
    turns = [
        ("user", "go"),
        ("sutando", "ok"),
        ("user", "go"),  # too short
    ]
    verdict, reasons = f(turns, "go")
    _check("cc-short-repeat-no-flag", "user repeated" not in " ".join(reasons))


_test_classify_call()


# ---------------------------------------------------------------------------
# (c) find_snippet — find-regression.py
# ---------------------------------------------------------------------------

def _test_find_snippet():
    f = _fr.find_snippet

    # No keyword → empty
    _check("fs-no-match",   f([], "record") == "")

    # Match in first turn
    turns = [("user", "please record this")]
    result = f(turns, "record")
    _check("fs-first-turn", "record" in result)
    _check("fs-role-prefix", result.startswith("user:"))

    # Match in second turn
    turns = [("user", "hello"), ("sutando", "sure I can record")]
    result = f(turns, "record")
    _check("fs-second-turn", "sutando:" in result)

    # Long snippet truncated at 120 chars (plus "...")
    long_text = "record " + "x" * 200
    turns = [("user", long_text)]
    result = f(turns, "record")
    _check("fs-truncated",  len(result) <= 123)  # 120 + "..."
    _check("fs-ellipsis",   result.endswith("..."))

    # Exactly 120 chars → no ellipsis
    exact = "record " + "y" * (120 - len("user: record "))
    turns = [("user", exact)]
    result = f(turns, "record")
    _check("fs-exact-no-ellipsis", not result.endswith("..."))


_test_find_snippet()


# ---------------------------------------------------------------------------
# (d) _truncate — diagnose-call.py
# ---------------------------------------------------------------------------

def _test_truncate():
    f = _dc._truncate

    # Short string unchanged
    _check("tr-short",       f("hello") == "hello")

    # Exactly 120 chars → unchanged
    s120 = "x" * 120
    _check("tr-exact",       f(s120) == s120)

    # 121 chars → truncated
    s121 = "x" * 121
    result = f(s121)
    _check("tr-over",        result == "x" * 120 + "...")

    # Leading/trailing whitespace stripped before length check
    _check("tr-strip",       f("  hello  ") == "hello")

    # Custom n
    _check("tr-custom-n",    f("abcdef", n=3) == "abc...")

    # Exactly n chars with custom n
    _check("tr-custom-exact", f("abc", n=3) == "abc")


_test_truncate()


# ---------------------------------------------------------------------------
# (e) _ts_iso — diagnose-call.py
# ---------------------------------------------------------------------------

def _test_ts_iso():
    f = _dc._ts_iso

    # Known Unix timestamp
    result = f(0)
    _check("ti-epoch",      result == "1970-01-01T00:00:00Z", f"got {result!r}")

    # None falls back to 0
    result = f(None)
    _check("ti-none",       result == "1970-01-01T00:00:00Z", f"got {result!r}")

    # Result always ends with Z (UTC marker)
    result = f(1000000000)
    _check("ti-ends-z",     result.endswith("Z"))

    # Result is a valid ISO string format
    result = f(1748000000)
    _check("ti-iso-format",  "T" in result and result.endswith("Z"))

    # Specific known value: 2001-09-09T01:46:40Z
    result = f(1000000000)
    _check("ti-known",      result == "2001-09-09T01:46:40Z", f"got {result!r}")


_test_ts_iso()


# ---------------------------------------------------------------------------
# (f) analyze_turns — diagnose-call.py
# ---------------------------------------------------------------------------

def _test_analyze_turns():
    f = _dc.analyze_turns

    # Empty turns
    result = f([])
    _check("at-empty-total",    result["total_turns"] == 0)
    _check("at-empty-ending",   result["ending"] == "normal")
    _check("at-empty-refusals", result["refusals"] == [])

    # Counts sutando vs user
    turns = [("sutando", "hi"), ("user", "hey"), ("sutando", "bye")]
    result = f(turns)
    _check("at-sutando-count",  result["sutando_turns"] == 2)
    _check("at-user-count",     result["user_turns"] == 1)
    _check("at-total-count",    result["total_turns"] == 3)

    # Refusal detected
    turns = [("sutando", "I can't do that"), ("user", "ok")]
    result = f(turns)
    _check("at-refusal-found",  len(result["refusals"]) >= 1)

    # Error detected
    turns = [("sutando", "something went wrong")]
    result = f(turns)
    _check("at-error-found",    len(result["errors"]) >= 1)

    # Silence detected
    turns = [("sutando", "(Silence)")]
    result = f(turns)
    _check("at-silence-count",  result["silences"] == 1)

    # Repeated user detected
    turns = [
        ("user", "play the recording"),
        ("sutando", "hmm"),
        ("user", "play the recording"),  # verbatim repeat
    ]
    result = f(turns)
    _check("at-repeat-found",   len(result["repeated_user"]) >= 1)

    # Abrupt ending: last turn is user with short text
    turns = [("sutando", "hello"), ("user", "bye")]
    result = f(turns)
    _check("at-abrupt-end",     "abrupt" in result["ending"])

    # Ended with sutando silence
    turns = [("user", "what?"), ("sutando", "(silence)")]
    result = f(turns)
    _check("at-sutando-silence-end", "silence" in result["ending"])

    # Normal ending: last turn is sutando without silence
    turns = [("user", "thanks"), ("sutando", "you're welcome")]
    result = f(turns)
    _check("at-normal-end",     result["ending"] == "normal")

    # Abrupt user ending only if text <= 12 chars
    turns = [("sutando", "hi"), ("user", "this is a very long goodbye message")]
    result = f(turns)
    _check("at-long-user-normal", result["ending"] == "normal")


_test_analyze_turns()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"regression-search: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
