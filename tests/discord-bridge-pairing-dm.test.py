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

# HARD-isolate ACCESS_FILE (#2158 CR). Setting CLAUDE_CONFIG_DIR is NOT enough:
# channel_access_path() prefers the canonical path but FALLS BACK to the real
# legacy ~/.claude/channels/discord/access.json when the (fresh tmp) canonical
# doesn't exist at import — so mod.ACCESS_FILE can resolve to the developer's
# REAL allowlist, and Case 4's pairing write would clobber it. Pin it to a
# throwaway temp file so no case can ever touch a real access.json.
mod.ACCESS_FILE = Path(tempfile.mkdtemp(prefix="pair-acl-")) / "channels" / "discord" / "access.json"

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

# ── Case 2: owner unreachable → fail-SAFE fallback, code NOT leaked ──────────
# #2158 CR: the fallback must NOT recreate the leak. When no owner DM is
# reachable, the channel gets a generic, code-free notice; the code stays in
# access.json `pending` + the owner-only bridge log for retrieval.
channel2 = FakeMessageable(name="pr-review")
with patch.object(mod, "client", FakeClient(None)):
    route2 = asyncio.run(
        mod._deliver_pairing_prompt(channel2, CODE, "newuser", "42", {"777"})
    )
check("route reported as channel fallback", route2 == "channel")
check("fallback still notifies the channel (one message)", len(channel2.sent) == 1)
check("fallback channel message does NOT contain the code (no leak in the fallback)",
      all(CODE not in m for m in channel2.sent), f"leaked: {channel2.sent}")

# ── Case 3: empty allowFrom (pre-TOFU) → fallback without fetch attempts ─────
channel3 = FakeMessageable(name="pr-review")
with patch.object(mod, "client", FakeClient(owner)):
    route3 = asyncio.run(
        mod._deliver_pairing_prompt(channel3, CODE, "newuser", "42", set())
    )
check("empty allowFrom routes to channel fallback", route3 == "channel")

# ── Case 4: on_message pairing branch drives _deliver_pairing_prompt ──────────
# The isolation cases above exercise _deliver_pairing_prompt directly; this one
# drives the on_message dispatch (_handle_discord_message) into the pairing
# branch so the changed CALL SITE — `route = await _deliver_pairing_prompt(...)`
# plus the "Pairing requested" log — is covered. A DM from an unpaired sender
# under dmPolicy=pairing reaches that branch; _deliver_pairing_prompt is stubbed
# so the test asserts the dispatch wiring, not the (separately-tested) delivery.
import json as _json
from unittest.mock import AsyncMock

class _FakeDM(discord.DMChannel):  # isinstance(_, discord.DMChannel) must be True
    def __init__(self, cid=999):
        self.id = cid
        self.sent: list[str] = []
    async def send(self, text):
        self.sent.append(text)

class _FakeAuthor:
    def __init__(self, uid=424242):
        self.id = uid
        self.bot = False
    def __str__(self):
        return "pairme#0001"

class _FakeMsg:
    def __init__(self, channel, author):
        self.channel = channel
        self.author = author
        self.content = "hello"
        self.mentions: list = []
        self.role_mentions: list = []
        self.embeds: list = []
        self.type = discord.MessageType.default
        self.reference = None
        self.id = 555
        self.message_snapshots: list = []

# Isolated ACCESS_FILE (CLAUDE_CONFIG_DIR was tmp'd before import): pairing on,
# empty allowFrom → the sender is unpaired, so the pairing branch fires. Create
# the nested channels/discord/ parent first — a fresh tmp CLAUDE_CONFIG_DIR
# (CI) has no such dir, and both this seed AND the code-under-test write it.
mod.ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
mod.ACCESS_FILE.write_text(_json.dumps({"dmPolicy": "pairing", "allowFrom": [], "pending": {}}))

_fake_client = type("_C", (), {"user": object()})()
_dm = _FakeDM()
_msg = _FakeMsg(_dm, _FakeAuthor())
_deliver = AsyncMock(return_value="dm")
with patch.object(mod, "client", _fake_client), \
     patch.object(mod, "_deliver_pairing_prompt", _deliver), \
     patch.object(mod, "_observe_for_mod", AsyncMock()), \
     patch.object(mod, "_update_dm_checkpoint", lambda *a, **k: None):
    asyncio.run(mod._handle_discord_message(_msg))
check("on_message pairing branch invoked _deliver_pairing_prompt once",
      _deliver.await_count == 1)
check("pairing branch passed the DM channel + generated code to delivery",
      _deliver.await_count == 1 and _deliver.await_args.args[0] is _dm
      and len(_deliver.await_args.args[1]) == 6)
check("pairing code persisted to access.pending",
      len(_json.loads(mod.ACCESS_FILE.read_text()).get("pending", {})) == 1)

print()
if failures:
    print(f"FAIL — {len(failures)} assertion(s): {failures}")
    sys.exit(1)
print("PASS — pairing-code DM routing behavioral tests")
