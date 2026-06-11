#!/usr/bin/env python3
"""Tests for skills/discord-voice/scripts/join_trigger.py — pure helpers.

Covers:
  a) message_is_join_phrase() — the "magic word" summon matcher
     (pure function when join_phrase is supplied explicitly)

Run: python3 tests/discord-voice-join-trigger.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "discord-voice" / "scripts" / "join_trigger.py"

# join_trigger.py resolves the workspace at import time via _resolve_workspace()
# which calls resolve_workspace() from workspace_default.py. Set SUTANDO_WORKSPACE
# to a temp dir so no real workspace is read.
_tmp_ws = tempfile.mkdtemp(prefix="jt-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))

spec = importlib.util.spec_from_file_location("join_trigger", SCRIPT)
_mod = importlib.util.module_from_spec(spec)
sys.modules["join_trigger"] = _mod
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


PHRASE = "za warudo"


def _test_message_is_join_phrase():
    f = _mod.message_is_join_phrase

    # Empty text → False
    _check("mj-empty",            not f("", PHRASE))

    # Whitespace-only → False
    _check("mj-whitespace",       not f("   ", PHRASE))

    # Empty phrase → False
    _check("mj-empty-phrase",     not f("za warudo", ""))

    # Exact match → True
    _check("mj-exact",            f("za warudo", PHRASE))

    # Case-insensitive (phrase lower, text upper)
    _check("mj-case-upper",       f("ZA WARUDO", PHRASE))
    _check("mj-case-mixed",       f("Za Warudo", PHRASE))

    # Trailing exclamation → True (non-alnum boundary)
    _check("mj-trailing-bang",    f("za warudo!", PHRASE))

    # Trailing comma + space → True
    _check("mj-trailing-comma",   f("za warudo, please", PHRASE))

    # Trailing newline → True
    _check("mj-trailing-newline", f("za warudo\nsome context", PHRASE))

    # Leading/trailing whitespace on text → True
    _check("mj-leading-space",    f("  za warudo  ", PHRASE))

    # Prefix match WITHOUT boundary → False ("za warudonow" should not match)
    _check("mj-prefix-no-bound",  not f("za warudonow", PHRASE))

    # Totally different text → False
    _check("mj-different",        not f("hello world", PHRASE))

    # Short phrase does NOT match longer word ("go" vs "google")
    _check("mj-short-no-word",    not f("google this", "go"))

    # Short phrase DOES match with boundary ("go!" matches "go")
    _check("mj-short-boundary",   f("go!", "go"))

    # Leading Discord @mention stripped — "@<id> za warudo" → True
    _check("mj-at-mention",       f("<@1234567890> za warudo", PHRASE))

    # Leading @! mention stripped
    _check("mj-at-bang",          f("<@!987654321> za warudo", PHRASE))

    # Leading role mention stripped
    _check("mj-at-role",          f("<@&111222333> za warudo", PHRASE))

    # Multiple mentions stripped
    _check("mj-multi-mention",    f("<@1111> <@2222> za warudo", PHRASE))

    # Mention-only, no phrase after → False
    _check("mj-mention-only",     not f("<@12345>", PHRASE))

    # Phrase contains underscore boundary — "_" is NOT a word boundary
    _check("mj-underscore",       not f("za warudo_extra", PHRASE))

    # Custom phrase different from default
    _check("mj-custom-phrase",    f("ACTIVATE", "activate"))
    _check("mj-custom-no-match",  not f("za warudo", "activate"))


_test_message_is_join_phrase()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"discord-voice-join-trigger: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
