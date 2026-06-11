#!/usr/bin/env python3
"""Regression guard: parse_script() and wrap_text() in
skills/make-viral-video/scripts/render.py.

parse_script(script_md):
  Splits a final_script.md string into HOOK / SUPPORT / CLOSER sections.
  Preferred: explicit section headers (##HOOK, ## HOOK, **HOOK**, #HOOK).
  Fallback: sentence-split (3+ sentences → first/middle/last).
  Fallback-fallback: everything in HOOK, empty SUPPORT/CLOSER.

wrap_text(text, max_chars_per_line):
  Word-wraps text to lines of at most max_chars_per_line characters.
  Returns a list of strings (lines). Empty text → empty list.

Run: python3 tests/make-viral-video-render.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "make-viral-video" / "scripts"

spec = importlib.util.spec_from_file_location(
    "render_script", SCRIPTS / "render.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["render_script"] = _mod
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
# parse_script
# ---------------------------------------------------------------------------

def _test_parse_script():
    f = _mod.parse_script

    # Standard ## section headers
    md_std = (
        "## HOOK\nThis is the hook line.\n\n"
        "## SUPPORT\nHere is the support fact.\n\n"
        "## CLOSER\nCall to action here.\n"
    )
    r = f(md_std)
    _check("ps-hook",    r.get("HOOK") == "This is the hook line.")
    _check("ps-support", r.get("SUPPORT") == "Here is the support fact.")
    _check("ps-closer",  r.get("CLOSER") == "Call to action here.")

    # Case-insensitive section names
    md_lower = "## hook\nLower hook.\n## support\nLower support.\n## closer\nLower closer.\n"
    r2 = f(md_lower)
    _check("ps-case",    r2.get("HOOK") == "Lower hook.")

    # Bold markdown **HOOK** prefix — content must be on the NEXT line
    # (the regex matches the header and `continue`s; inline content is skipped)
    md_bold = "**HOOK**\nBold hook text.\n**SUPPORT**\nBold support.\n**CLOSER**\nBold closer.\n"
    r3 = f(md_bold)
    _check("ps-bold",    r3.get("HOOK") == "Bold hook text.")

    # # single-hash prefix
    md_hash = "# HOOK\nSingle hash hook.\n# SUPPORT\nSingle hash support.\n# CLOSER\nSingle hash closer.\n"
    r4 = f(md_hash)
    _check("ps-single-hash", r4.get("HOOK") == "Single hash hook.")

    # Missing SUPPORT → SUPPORT not in result (only HOOK + CLOSER)
    md_no_support = "## HOOK\nHook text.\n## CLOSER\nCloser text.\n"
    r5 = f(md_no_support)
    _check("ps-no-support-hook",   r5.get("HOOK") == "Hook text.")
    _check("ps-no-support-closer", r5.get("CLOSER") == "Closer text.")
    _check("ps-no-support-absent", "SUPPORT" not in r5)

    # No section markers, 3 sentences → sentence-split fallback
    three_sent = "First sentence. Second sentence. Third sentence."
    r6 = f(three_sent)
    _check("ps-sent-hook",    r6.get("HOOK") == "First sentence.")
    _check("ps-sent-support", "Second" in r6.get("SUPPORT", ""))
    _check("ps-sent-closer",  r6.get("CLOSER") == "Third sentence.")

    # No section markers, 1 sentence → everything in HOOK
    one_sent = "Just one sentence."
    r7 = f(one_sent)
    _check("ps-one-sent",    r7.get("HOOK") == "Just one sentence.")
    _check("ps-one-support", r7.get("SUPPORT") == "")
    _check("ps-one-closer",  r7.get("CLOSER") == "")

    # Empty string → HOOK="" SUPPORT="" CLOSER=""
    r8 = f("")
    _check("ps-empty-hook",    r8.get("HOOK") == "")
    _check("ps-empty-support", r8.get("SUPPORT") == "")

    # Multi-line content within a section → joined
    md_multi = "## HOOK\nLine one.\nLine two.\n## CLOSER\nClose.\n"
    r9 = f(md_multi)
    _check("ps-multi-join", r9.get("HOOK") == "Line one. Line two.")


_test_parse_script()


# ---------------------------------------------------------------------------
# wrap_text
# ---------------------------------------------------------------------------

def _test_wrap_text():
    f = _mod.wrap_text

    # Empty text → empty list
    _check("wt-empty",    f("", 20) == [])

    # Single short word fits on one line
    _check("wt-single",   f("Hello", 20) == ["Hello"])

    # All words fit → single line
    _check("wt-fits",     f("Hello world", 20) == ["Hello world"])

    # Wraps at max_chars_per_line
    lines = f("one two three four five six", 10)
    _check("wt-wraps",    len(lines) > 1)
    for line in lines:
        _check("wt-max-width", len(line) <= 10, f"line {line!r} length={len(line)}")

    # Every line is non-empty
    lines2 = f("a b c d e f g", 3)
    _check("wt-no-empty", all(line.strip() for line in lines2))

    # Reconstructed text matches original (modulo joining whitespace)
    original = "the quick brown fox jumps over the lazy dog"
    wrapped = f(original, 15)
    reconstructed = " ".join(wrapped)
    _check("wt-lossless", reconstructed == original)

    # Single word longer than max → goes on its own line
    long_word_lines = f("superlongwordthatexceedslimit short", 10)
    _check("wt-long-word", "superlongwordthatexceedslimit" in long_word_lines)


_test_wrap_text()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"make-viral-video-render: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
