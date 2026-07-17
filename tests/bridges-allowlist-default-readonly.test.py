#!/usr/bin/env python3
"""Allowlist default tier = read-only (owner request 2026-07-17).

Before: a user added to `allowFrom` with no `tierMap` entry was resolved as
OWNER (Slack: only when tierMap absent; Discord: always) — so "add to the
allowlist" silently granted full core capabilities. Fix: a one-time
grandfather-seed writes the CURRENT allowFrom as owner into tierMap, after
which any NEW allowFrom addition is missing from tierMap and resolves to a
read-only tier ("other" on Slack, "team" on Discord).

Guards (behavioral, against the real access.json writers — ACCESS_FILE is
redirected to a temp path so the real owner allowlist is never touched):
  Slack:
    1. seed grandfathers existing allowFrom -> owner in tierMap
    2. seed is idempotent (no-op when tierMap already present)
    3. a newly-added allowFrom uid (post-seed) is NOT owner
    4. TOFU onboarding writes tierMap with the enrollee as owner
  Discord:
    5. seed grandfathers existing allowFrom -> owner
    6. a newly-added allowFrom uid (post-seed) resolves to team, not owner

Run: python3 tests/bridges-allowlist-default-readonly.test.py  (exit 0/1)
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok  " if cond else "  FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ── Load slack-bridge with a stubbed slack_bolt + isolated ACCESS_FILE ────────
def _load_slack():
    class _FakeApp:
        def __init__(self, token=None):
            self.client = types.SimpleNamespace(
                chat_postMessage=lambda **k: {"ok": True},
                conversations_replies=lambda **k: {"ok": True, "messages": []},
            )

        def _d(self, *a, **k):
            return lambda fn: fn

        event = message = command = action = shortcut = view = _d

    _bolt = types.ModuleType("slack_bolt"); _bolt.App = _FakeApp
    sys.modules["slack_bolt"] = _bolt
    _ad = types.ModuleType("slack_bolt.adapter")
    _sm = types.ModuleType("slack_bolt.adapter.socket_mode")
    _sm.SocketModeHandler = type("SocketModeHandler", (), {"__init__": lambda self, *a, **k: None})
    sys.modules["slack_bolt.adapter"] = _ad
    sys.modules["slack_bolt.adapter.socket_mode"] = _sm
    os.environ["SUTANDO_TEST_MODE"] = "1"
    os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"
    os.environ["SLACK_APP_TOKEN"] = "xapp-test"
    spec = importlib.util.spec_from_file_location("slackbridge_acl", REPO / "src" / "slack-bridge.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


slack = _load_slack()
_sf = Path(tempfile.mkdtemp(prefix="sl-acl-")) / "access.json"
slack.ACCESS_FILE = _sf


def _write_slack(d):
    _sf.write_text(json.dumps(d))
    slack._update_access_cache(d) if hasattr(slack, "_update_access_cache") else None


# 1. grandfather existing allowFrom -> owner
_write_slack({"allowFrom": ["U_OWNER", "U_OLD"]})
slack._ensure_tier_map_seeded()
seeded = json.loads(_sf.read_text()).get("tierMap", {})
check("slack: seed grandfathers existing allowFrom as owner",
      seeded.get("U_OWNER") == "owner" and seeded.get("U_OLD") == "owner", str(seeded))

# 2. idempotent
before = _sf.read_text()
slack._ensure_tier_map_seeded()
check("slack: seed is idempotent", _sf.read_text() == before)

# 3. newly-added allowFrom uid is NOT owner (missing from tierMap -> "other")
data = json.loads(_sf.read_text())
data["allowFrom"].append("U_NEW")
_write_slack(data)
slack._ensure_tier_map_seeded()  # no-op now (tierMap present)
tm = slack.load_tier_map()
new_tier = tm.get("U_NEW", "other" if tm else "owner")
check("slack: newly-added allowlist user is not owner", new_tier != "owner", f"got {new_tier}")

# 4. TOFU writes tierMap with enrollee as owner
_sf.unlink(missing_ok=True)
if hasattr(slack, "_access_cache"):
    slack._access_cache = None
slack.tofu_onboard("U_TOFU", "tofu-user")
tofu = json.loads(_sf.read_text())
check("slack: TOFU writes tierMap with enrollee as owner",
      tofu.get("tierMap", {}).get("U_TOFU") == "owner", str(tofu.get("tierMap")))


# ── Discord ──────────────────────────────────────────────────────────────────
os.environ.setdefault("DISCORD_BOT_TOKEN", "faketoken")
os.environ["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="dc-acl-ccd-")
try:
    import discord  # noqa: F401
    _have_discord = True
except ImportError:
    _have_discord = False
    for _cand in ("/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        if os.path.exists(_cand) and os.path.realpath(_cand) != os.path.realpath(sys.executable):
            import subprocess
            if subprocess.run([_cand, "-c", "import discord"], capture_output=True).returncode == 0:
                os.execv(_cand, [_cand, os.path.abspath(__file__), *sys.argv[1:]])

if _have_discord:
    spec = importlib.util.spec_from_file_location("discordbridge_acl", REPO / "src" / "discord-bridge.py")
    dmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dmod)
    _df = Path(tempfile.mkdtemp(prefix="dc-acl-")) / "access.json"
    dmod.ACCESS_FILE = _df

    # 5. grandfather
    _df.write_text(json.dumps({"allowFrom": ["111", "222"]}))
    dmod.ensure_tier_map_seeded()
    dseed = json.loads(_df.read_text()).get("tierMap", {})
    check("discord: seed grandfathers existing allowFrom as owner",
          dseed.get("111") == "owner" and dseed.get("222") == "owner", str(dseed))

    # 6. newly-added -> team, resolution mirrors the handler
    data = json.loads(_df.read_text()); data["allowFrom"].append("333"); _df.write_text(json.dumps(data))
    dmod.ensure_tier_map_seeded()  # no-op
    tmap = dmod.load_tier_map()
    resolved = tmap.get("333", "owner" if not tmap else "team")
    check("discord: newly-added allowlist user resolves to team, not owner",
          resolved == "team", f"got {resolved}")
else:
    print("  SKIP discord — discord.py not importable")

print()
if failures:
    print(f"FAIL — {len(failures)}: {failures}")
    sys.exit(1)
print("PASS — allowlist default-readonly (grandfather migration) tests")
