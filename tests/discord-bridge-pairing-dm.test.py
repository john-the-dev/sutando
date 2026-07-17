#!/usr/bin/env python3
"""Behavioral tests for _deliver_pairing_prompt (pairing-code security fix).

The pairing code is the approval credential; posting it in a shared channel
leaks it to every member (owner catch 2026-07-17). Guards:

  1. Owner reachable  -> code goes to the owner DM; the channel message is
     generic and does NOT contain the code.
  2. No owner reachable (fetch_user raises / empty allowFrom) -> falls back
     to the legacy in-channel prompt (pairing must remain completable).

Run: python3 tests/discord-bridge-pairing-dm.test.py   (exit 0 pass / 1 fail)
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent

# The bridge needs discord.py. Importing it under an interpreter WITHOUT it
# triggers the bridge's rescue re-exec, which execs the BRIDGE (not this test)
# as __main__ — colliding with a live bridge's singleton lock. So resolve the
# interpreter HERE: re-exec this test under one that has discord.py, or skip.
try:
    import discord  # noqa: F401
except ImportError:
    for _cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if os.path.exists(_cand) and os.path.realpath(_cand) != os.path.realpath(sys.executable):
            import subprocess
            if subprocess.run([_cand, "-c", "import discord"], capture_output=True).returncode == 0:
                os.execv(_cand, [_cand, os.path.abspath(__file__), *sys.argv[1:]])
    print("SKIP — discord.py not importable under any known interpreter")
    sys.exit(0)

# Isolate module-global paths BEFORE import — the bridge derives ACCESS_FILE
# from CLAUDE_CONFIG_DIR at import time (never touch the real access.json).
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sutando-pair-test-")
# The bridge exit(1)s at import when no token resolves; a fake one is fine —
# nothing connects until client.run(), which tests never call.
os.environ.setdefault("DISCORD_BOT_TOKEN", "faketoken-for-tests")

spec = importlib.util.spec_from_file_location("discordbridge_pair", REPO / "src" / "discord-bridge.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok  " if cond else "  FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeMessageable:
    def __init__(self, name=None):
        if name is not None:
            self.name = name
        self.sent: list[str] = []

    async def send(self, text):
        self.sent.append(text)


class FakeClient:
    def __init__(self, owner):
        self.owner = owner

    async def fetch_user(self, uid):
        if self.owner is None:
            raise RuntimeError("user not found")
        return self.owner


CODE = "zzz999"

# ── Case 1: owner reachable → code in DM only, channel stays generic ─────────
owner = FakeMessageable()
channel = FakeMessageable(name="pr-review")
with patch.object(mod, "client", FakeClient(owner)):
    route = asyncio.run(
        mod._deliver_pairing_prompt(channel, CODE, "newuser", "42", {"777"})
    )
check("route reported as dm", route == "dm")
check("owner DM contains the pairing code", any(CODE in m for m in owner.sent))
check("owner DM names the requester and channel",
      any("newuser" in m and "pr-review" in m for m in owner.sent))
check("channel got exactly one message", len(channel.sent) == 1)
check("channel message does NOT contain the code",
      all(CODE not in m for m in channel.sent), f"leaked: {channel.sent}")

# ── Case 2: owner unreachable → legacy in-channel fallback ───────────────────
channel2 = FakeMessageable(name="pr-review")
with patch.object(mod, "client", FakeClient(None)):
    route2 = asyncio.run(
        mod._deliver_pairing_prompt(channel2, CODE, "newuser", "42", {"777"})
    )
check("route reported as channel fallback", route2 == "channel")
check("fallback channel message contains the code (pairing still completable)",
      any(CODE in m for m in channel2.sent))

# ── Case 3: empty allowFrom (pre-TOFU) → fallback without fetch attempts ─────
channel3 = FakeMessageable(name="pr-review")
with patch.object(mod, "client", FakeClient(owner)):
    route3 = asyncio.run(
        mod._deliver_pairing_prompt(channel3, CODE, "newuser", "42", set())
    )
check("empty allowFrom routes to channel fallback", route3 == "channel")

print()
if failures:
    print(f"FAIL — {len(failures)} assertion(s): {failures}")
    sys.exit(1)
print("PASS — pairing-code DM routing behavioral tests")
