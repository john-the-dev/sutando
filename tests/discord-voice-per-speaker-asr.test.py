#!/usr/bin/env python3
"""Structural tests for per-speaker ASR in discord-voice-server.ts (#1456).

Verifies:
1. GoogleGenAI is imported from @google/genai
2. _perSpeakerGenAI is declared at module level
3. transcribeUtterance function exists
4. WAV header construction is present (44-byte header)
5. subscribeUser buffers PCM chunks (_asrBuffer)
6. subscribeUser calls transcribeUtterance on utterance end (resampler.on('end'))
7. conversation-store: discord_voice table has speaker_id column in CREATE TABLE
8. conversation-store: recordConversation accepts speakerId parameter
9. speaker_id is included in turnStmt INSERT for discord-voice

Run: python3 tests/discord-voice-per-speaker-asr.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DISCORD_VOICE_SERVER = REPO / "skills" / "discord-voice" / "scripts" / "discord-voice-server.ts"
CONVERSATION_STORE = REPO / "src" / "conversation-store.ts"

_FAILURES: list[str] = []


def fail(msg: str, ctx: str = "") -> None:
    _FAILURES.append(msg)
    print(f"FAIL: {msg}", file=sys.stderr)
    if ctx:
        print("--- context ---", file=sys.stderr)
        print(ctx[:400], file=sys.stderr)


def expect(cond: bool, label: str, ctx: str = "") -> None:
    if not cond:
        fail(label, ctx)


def main() -> None:
    for path in [DISCORD_VOICE_SERVER, CONVERSATION_STORE]:
        if not path.exists():
            fail(f"file not found: {path}")
            sys.exit(1)

    src = DISCORD_VOICE_SERVER.read_text()
    store = CONVERSATION_STORE.read_text()

    # 1. GoogleGenAI imported
    expect(
        "GoogleGenAI" in src and "@google/genai" in src,
        "discord-voice-server.ts: must import GoogleGenAI from @google/genai",
    )

    # 2. _perSpeakerGenAI declared at module level
    expect(
        "_perSpeakerGenAI" in src,
        "discord-voice-server.ts: must declare _perSpeakerGenAI at module level",
    )

    # 3. transcribeUtterance function exists
    expect(
        "async function transcribeUtterance(" in src,
        "discord-voice-server.ts: must define async function transcribeUtterance",
    )

    # 4. WAV header (44-byte) construction in transcribeUtterance
    fn_match = re.search(
        r"async function transcribeUtterance\(.*?\n\}",
        src,
        re.DOTALL,
    )
    fn_body = fn_match.group(0) if fn_match else ""
    expect(fn_body != "", "discord-voice-server.ts: transcribeUtterance must exist", fn_body[:100])
    expect(
        "RIFF" in fn_body and "WAVE" in fn_body and "audio/wav" in fn_body,
        "discord-voice-server.ts: transcribeUtterance must build WAV header and use audio/wav MIME",
        ctx=fn_body[:400],
    )

    # 5. _asrBuffer declared inside subscribeUser
    sub_match = re.search(
        r"function subscribeUser\(.*?\n\}",
        src,
        re.DOTALL,
    )
    sub_body = sub_match.group(0) if sub_match else ""
    expect(sub_body != "", "discord-voice-server.ts: subscribeUser must exist")
    expect(
        "_asrBuffer" in sub_body,
        "discord-voice-server.ts: subscribeUser must declare _asrBuffer for per-user PCM buffering",
        ctx=sub_body[:400],
    )

    # 6. transcribeUtterance called in resampler.on('end') inside subscribeUser
    expect(
        "transcribeUtterance" in sub_body,
        "discord-voice-server.ts: subscribeUser must call transcribeUtterance on utterance end",
        ctx=sub_body[:600],
    )

    # 7. discord_voice table has speaker_id column
    expect(
        "speaker_id  TEXT" in store or "speaker_id TEXT" in store,
        "conversation-store.ts: discord_voice CREATE TABLE must include speaker_id TEXT column",
    )

    # 8. recordConversation accepts speakerId param
    rc_match = re.search(r"export function recordConversation\([^)]+\)", store)
    rc_sig = rc_match.group(0) if rc_match else ""
    expect(
        "speakerId" in rc_sig,
        "conversation-store.ts: recordConversation must accept speakerId parameter",
        ctx=rc_sig,
    )

    # 9. turnStmt INSERT for discord-voice includes speaker_id
    expect(
        "INSERT INTO discord_voice" in store and "speaker_id" in store,
        "conversation-store.ts: discord-voice INSERT statement must include speaker_id column",
    )

    # Summary
    if _FAILURES:
        print(f"\n{len(_FAILURES)} test(s) FAILED:", file=sys.stderr)
        for f in _FAILURES:
            print(f"  - {f}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"All 9 structural tests passed.")


if __name__ == "__main__":
    main()
