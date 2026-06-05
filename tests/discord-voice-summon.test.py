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
    ("<@100000000000000001> za warudo", True),
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


# --- #1427: multi-bot @-mention gating (summon_is_for_me) -------------------
# A bare "za warudo" reaches every bot's bridge (the phrase matcher strips
# leading mentions), so the bot must decide whether the summon is FOR IT:
# answer iff this bot is @-mentioned OR no other Sutando bot is named.
from join_trigger import summon_is_for_me as _sfm  # noqa: E402

BOT_A, BOT_B, HUMAN = 100000000000000001, 100000000000000002, 100000000000000003


class _U:
    def __init__(self, uid, bot):
        self.id, self.bot = uid, bot


class _Msg:
    def __init__(self, mentions):
        self.mentions = mentions


GATE_CASES = [
    # (description, mentions, self_id, expected)
    ("bare summon → BotA joins", [], BOT_A, True),
    ("bare summon → BotB joins", [], BOT_B, True),
    ("@BotB → BotA stays out", [_U(BOT_B, True)], BOT_A, False),
    ("@BotB → BotB joins", [_U(BOT_B, True)], BOT_B, True),
    ("@BotA → BotA joins", [_U(BOT_A, True)], BOT_A, True),
    ("@BotA → BotB stays out", [_U(BOT_A, True)], BOT_B, False),
    ("@human only → BotA still joins", [_U(HUMAN, False)], BOT_A, True),
    ("@BotA @BotB → BotA joins", [_U(BOT_A, True), _U(BOT_B, True)], BOT_A, True),
    ("@BotA @BotB → BotB joins", [_U(BOT_A, True), _U(BOT_B, True)], BOT_B, True),
    ("self_id None → backward-compat join", [_U(BOT_B, True)], None, True),
]

gate_fail = []
for desc, mentions, self_id, expected in GATE_CASES:
    got = _sfm(_Msg(mentions), self_id)
    status = "ok " if got == expected else "XX "
    print(f"  {status}{desc:38} -> {got} (want {expected})")
    if got != expected:
        gate_fail.append(desc)

if gate_fail:
    print(f"\nFAILED {len(gate_fail)}/{len(GATE_CASES)} gate cases: {gate_fail}")
    sys.exit(1)
print(f"OK — {len(GATE_CASES)}/{len(GATE_CASES)} summon-gate cases passed")
