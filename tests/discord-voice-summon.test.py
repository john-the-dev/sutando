#!/usr/bin/env python3
"""E2E-of-the-logic test for the discord-voice 'za warudo' summon matcher
(join_trigger.message_is_join_phrase). Locks the exact-match-only behaviour:
only a bare "za warudo" (modulo trailing punctuation) spawns; "za warudo <word>"
is reserved for in-session sub-commands (e.g. screen-push) and must NOT summon —
the bug that re-spawned the bot mid-session and killed the live screen-push.

Standalone (run by `npm run test:py`): exits non-zero on first failure.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "discord-voice" / "scripts"))
from join_trigger import message_is_join_phrase as m  # noqa: E402

PHRASE = "za warudo"
CASES = [
    # bare phrase (+ trailing punctuation / mention prefix) → summons
    ("za warudo", True),
    ("za warudo!", True),
    ("za warudo.", True),
    ("za warudo ", True),
    ("ZA WARUDO", True),
    ("<@1494435872949665953> za warudo", True),
    # "za warudo <word>" → reserved for sub-commands, must NOT summon
    ("za warudo screen", False),
    ("za warudo stop screen", False),
    ("za warudo please", False),
    ("<@123> za warudo screen", False),
    # word-boundary + non-matches
    ("za warudonow", False),
    ("hello za warudo", False),   # must START with the phrase
    ("", False),
]

failures = []
for text, expected in CASES:
    got = m(text, PHRASE)
    status = "ok " if got == expected else "XX "
    print(f"  {status}{text!r:34} -> {got} (want {expected})")
    if got != expected:
        failures.append((text, expected, got))

if failures:
    print(f"\nFAILED {len(failures)}/{len(CASES)}:")
    for text, exp, got in failures:
        print(f"  {text!r}: expected {exp}, got {got}")
    sys.exit(1)
print(f"\nOK — {len(CASES)}/{len(CASES)} summon-match cases passed")
