#!/usr/bin/env python3
"""Behavioral tests for slack-bridge._write_task (PR #1839: CONTEXT-FIRST).

Loads slack-bridge.py with a minimal slack_bolt stub (same pattern as
tests/slack-bridge-chunking.test.py) and exercises _write_task directly.

Covers:
- CONTEXT-FIRST instruction injected for owner tasks regardless of skill presence
- Non-owner tasks: no skill hints block
- access_tier resolution: empty tier_map → "owner"

Run: python3 tests/slack-bridge-write-task.test.py   (exit 0 pass / 1 fail)
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent

# Stub slack_bolt — mirrors tests/slack-bridge-chunking.test.py
class _FakeApp:
    def __init__(self, token=None):
        self.client = types.SimpleNamespace(
            chat_postMessage=lambda **k: {"ok": True},
            conversations_replies=lambda **k: {"ok": True, "messages": []},
        )

    def _decorator(self, *a, **k):
        return lambda fn: fn

    event = message = command = action = shortcut = view = _decorator


_bolt = types.ModuleType("slack_bolt")
_bolt.App = _FakeApp
sys.modules["slack_bolt"] = _bolt
_adapter = types.ModuleType("slack_bolt.adapter")
_socket = types.ModuleType("slack_bolt.adapter.socket_mode")
_socket.SocketModeHandler = type("SocketModeHandler", (), {"__init__": lambda self, *a, **k: None})
sys.modules["slack_bolt.adapter"] = _adapter
sys.modules["slack_bolt.adapter.socket_mode"] = _socket

_tmp = tempfile.mkdtemp(prefix="sutando-sw-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp
os.environ["SUTANDO_TEST_MODE"] = "1"
os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-not-real"
os.environ["SLACK_APP_TOKEN"] = "xapp-test-not-real"

spec = importlib.util.spec_from_file_location("slackbridge_wt", REPO / "src" / "slack-bridge.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Point TASKS_DIR at our temp workspace (it resolves from SUTANDO_WORKSPACE already,
# but make it explicit so teardown is trivial).
TASKS_DIR = Path(_tmp) / "tasks"
TASKS_DIR.mkdir(parents=True, exist_ok=True)
mod.TASKS_DIR = TASKS_DIR

failures: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok  " if cond else "  FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def call_write_task(text: str, user_id: str = "U_OWNER", access_tier: str = "owner") -> Path | None:
    """Call _write_task with mocked access control; return written task file path."""
    event = {"user": user_id, "channel": "CFAKE", "channel_type": "im", "ts": "1000.001"}

    def _fake_load_allowed():
        return {user_id}

    def _fake_tier_map():
        # Empty dict → access_tier falls through to "owner" for any allowed user.
        return {}

    with patch.object(mod, "load_allowed", _fake_load_allowed), \
         patch.object(mod, "load_tier_map", _fake_tier_map), \
         patch.object(mod, "write_owner_activity", lambda *a, **k: None):
        task_id = mod._write_task(event, "DM", text, "testowner")

    if task_id is None:
        return None
    candidates = list(TASKS_DIR.glob(f"{task_id}.txt"))
    return candidates[0] if candidates else None


# ── Owner task — CONTEXT-FIRST injected even with no skills installed ──────────

task_path = call_write_task("please check the Zacks")
check("write_task returns a task_id (not None)", task_path is not None)

if task_path and task_path.exists():
    body = task_path.read_text()
    check("owner task: file written to TASKS_DIR", True)
    check("owner task: source: slack", "source: slack" in body)
    check("owner task: access_tier: owner", "access_tier: owner" in body)
    check("owner task: task body present", "please check the Zacks" in body)
    check("owner task: SKILL INSTRUCTIONS block present for owner",
          "===SKILL INSTRUCTIONS" in body)
    check("owner task: CONTEXT-FIRST step injected",
          "CONTEXT-FIRST" in body,
          "CONTEXT-FIRST instruction missing — PR #1839 regression")
else:
    check("owner task: file written to TASKS_DIR", False, "task_path is None or missing")
    for name in ("owner task: source: slack", "owner task: access_tier: owner",
                 "owner task: task body present", "owner task: SKILL INSTRUCTIONS block present",
                 "owner task: CONTEXT-FIRST step injected"):
        check(name, False, "task file not written")

# ── Other-tier task — no skill hints (fail-safe) ──────────────────────────────

def call_other_tier(text: str) -> Path | None:
    event = {"user": "U_OTHER", "channel": "CFAKE", "channel_type": "im", "ts": "1001.001"}

    def _fake_load_allowed():
        return {"U_OTHER"}

    def _fake_tier_map():
        return {"U_OTHER": "other"}

    with patch.object(mod, "load_allowed", _fake_load_allowed), \
         patch.object(mod, "load_tier_map", _fake_tier_map), \
         patch.object(mod, "write_owner_activity", lambda *a, **k: None):
        task_id = mod._write_task(event, "DM", text, "otherperson")

    if task_id is None:
        return None
    candidates = list(TASKS_DIR.glob(f"{task_id}.txt"))
    return candidates[0] if candidates else None


other_path = call_other_tier("what can sutando do?")
check("other-tier task: written (not silently dropped)", other_path is not None)
if other_path and other_path.exists():
    other_body = other_path.read_text()
    check("other-tier task: access_tier: other", "access_tier: other" in other_body)
    check("other-tier task: no CONTEXT-FIRST (hints block is owner-only)",
          "CONTEXT-FIRST" not in other_body)
    # The whole owner-gated hints block (===SKILL INSTRUCTIONS, incl. CONTEXT-FIRST)
    # must be absent for a non-owner tier — this is the behavioral equivalent of
    # the `if access_tier == "owner":` source guard (PR #1839 replaced that
    # source-grep with this assertion).
    check("other-tier task: no SKILL INSTRUCTIONS block (owner-gated)",
          "===SKILL INSTRUCTIONS" not in other_body)

# ── Empty event user_id → graceful None ───────────────────────────────────────

no_user = mod._write_task({"channel": "C_NOUSER"}, "DM", "hello", None)
check("empty user_id → _write_task returns None", no_user is None)


if failures:
    print(f"\nFAIL — {len(failures)} check(s) failed: {failures}")
    raise SystemExit(1)

print("\nPASS — slack-bridge _write_task behavioral tests")
