#!/usr/bin/env python3
"""Structural regression test for the discord-voice bot-speaker attribution fix (#1465).

Guards two invariants in skills/discord-voice/scripts/discord-voice-server.ts:

1. turnSpeakers.add(userId) must appear AFTER the isBot check in the speaking.start
   handler. Before this fix, bots added to turnSpeakers (even when correctly excluded
   from audio subscription) caused effectiveTier() to resolve to 'other', blocking
   the owner's tools during any turn overlapping with bot speech.

2. subscribeUser's PCM data handler must contain a self-audio guard
   (userId === s.client.user?.id) as defense-in-depth against the case where the
   isBot check races (catch path sets isBot=false, subscribing the bot temporarily).

Run: python3 tests/discord-voice-bot-speaker-attribution.test.py
Exit: 0 = all pass, 1 = failure
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "skills" / "discord-voice" / "scripts" / "discord-voice-server.ts").read_text()

_FAILURES: list[str] = []


def fail(msg: str) -> None:
    _FAILURES.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)


def check_turnspeakers_after_bot_filter() -> None:
    """turnSpeakers.add must appear after the isBot guard, not before it.

    The speaking.start handler must:
      1. Fetch isBot (with botFlagCache)
      2. Return early if (isBot && !ALLOWED_BOT_USER_IDS.has(userId))
      3. THEN call turnSpeakers.add(userId)

    If turnSpeakers.add appears before the bot check, filtered bots contaminate
    the tier gate and can drop the owner's effective tier to 'other'.
    """
    # Find the speaking.start handler block
    handler_match = re.search(
        r"connection\.receiver\.speaking\.on\('start'.*?subscribeUser\(s, userId\);?\s*\}\);",
        SRC,
        re.DOTALL,
    )
    if not handler_match:
        fail("Could not locate speaking.start handler in discord-voice-server.ts")
        return

    handler = handler_match.group(0)

    # Find positions of the key constructs within the handler
    bot_filter_pos = handler.find("!ALLOWED_BOT_USER_IDS.has(userId)")
    turnspeakers_pos = handler.find("turnSpeakers.add(userId)")

    if bot_filter_pos == -1:
        fail("Could not find bot filter (ALLOWED_BOT_USER_IDS check) in speaking.start handler")
        return

    if turnspeakers_pos == -1:
        fail("Could not find turnSpeakers.add(userId) in speaking.start handler")
        return

    if turnspeakers_pos <= bot_filter_pos:
        fail(
            f"turnSpeakers.add(userId) appears at position {turnspeakers_pos} BEFORE the "
            f"bot filter at {bot_filter_pos} — bots will contaminate the tier gate. "
            "turnSpeakers.add must come after the ALLOWED_BOT_USER_IDS guard."
        )
    else:
        print(f"OK: turnSpeakers.add is after bot filter ({bot_filter_pos} < {turnspeakers_pos})")


def check_self_audio_guard() -> None:
    """subscribeUser's data handler must drop chunks from the bot's own userId.

    The guard `if (userId === s.client.user?.id) return;` prevents our own TTS
    output from being fed back to Gemini as user input when the bot's speaking
    event is incorrectly subscribed (e.g., on isBot fetch error).
    """
    # Find subscribeUser function body
    func_match = re.search(
        r"function subscribeUser\(.*?\n\}",
        SRC,
        re.DOTALL,
    )
    if not func_match:
        fail("Could not locate subscribeUser function in discord-voice-server.ts")
        return

    func_body = func_match.group(0)

    # Check for the self-audio guard
    if "userId === s.client.user?.id" not in func_body:
        fail(
            "subscribeUser data handler is missing the self-audio guard "
            "(userId === s.client.user?.id). Without this, the bot's own TTS "
            "output can be fed back to Gemini as user input on isBot fetch errors."
        )
    else:
        print("OK: self-audio guard present in subscribeUser data handler")

    # The guard must appear before the handleAudioFromClient *call* (not comment).
    # Strip single-line comments before searching to avoid matching the guard's
    # own explanatory comment "// ... reaches handleAudioFromClient" at an earlier pos.
    func_body_no_comments = re.sub(r"//[^\n]*", "", func_body)
    guard_pos = func_body_no_comments.find("userId === s.client.user?.id")
    handle_pos = func_body_no_comments.find("handleAudioFromClient")
    if guard_pos != -1 and handle_pos != -1 and guard_pos >= handle_pos:
        fail(
            f"Self-audio guard at position {guard_pos} appears AFTER "
            f"handleAudioFromClient call at {handle_pos} — the guard must precede the call."
        )
    elif guard_pos != -1 and handle_pos != -1:
        print(f"OK: self-audio guard precedes handleAudioFromClient call ({guard_pos} < {handle_pos})")


def check_bot_filter_present() -> None:
    """The ALLOWED_BOT_USER_IDS bot filter must still be present (not accidentally removed)."""
    if "ALLOWED_BOT_USER_IDS.has(userId)" not in SRC:
        fail(
            "Bot filter (ALLOWED_BOT_USER_IDS.has(userId)) is missing from "
            "discord-voice-server.ts — the speaking.start handler must still "
            "exclude non-allowlisted bots from audio subscription."
        )
    else:
        print("OK: ALLOWED_BOT_USER_IDS bot filter present")


if __name__ == "__main__":
    check_bot_filter_present()
    check_turnspeakers_after_bot_filter()
    check_self_audio_guard()

    if _FAILURES:
        print(f"\n{len(_FAILURES)} test(s) failed.", file=sys.stderr)
        sys.exit(1)
    else:
        print("\nAll checks passed.")
        sys.exit(0)
