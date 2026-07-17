#!/usr/bin/env python3
"""Tests for skill-hints injection in slack/discord/telegram bridge task files.

Guards that the ===SKILL INSTRUCTIONS=== block is present in owner task files
and correctly references the notify + transcribe commands. Structural tests
only — no live bridge needed.

Run: python3 tests/bridge-skill-hints-injection.test.py
Exit code: 0 on pass, 1 on fail.
"""

from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parent.parent
SLACK_BRIDGE = REPO / "src" / "slack-bridge.py"
DISCORD_BRIDGE = REPO / "src" / "discord-bridge.py"
TELEGRAM_BRIDGE = REPO / "src" / "telegram-bridge.py"

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        print(f"PASS: {name}")
        PASS += 1
    else:
        print(f"FAIL: {name}" + (f" — {detail}" if detail else ""))
        FAIL += 1


# ---------------------------------------------------------------------------
# Slack bridge — behavioral (CR #1839: exercise _write_task and assert on the
# WRITTEN TASK FILE, not on slack-bridge.py source text). The discord/telegram
# sections below remain structural; converting them is tracked separately.
# ---------------------------------------------------------------------------

import importlib.util
import os
import tempfile
import types
from unittest.mock import patch

# Stub slack_bolt — mirrors tests/slack-bridge-write-task.test.py
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

_tmp_ws = tempfile.mkdtemp(prefix="sutando-hints-test-")
os.environ["SUTANDO_TEST_MODE"] = "1"
os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-not-real"
os.environ["SLACK_APP_TOKEN"] = "xapp-test-not-real"

# Two config dirs: one with the notify/transcribe skills "installed" (empty
# executable stand-ins — _write_task only checks existence), one without.
_ccd_with = Path(tempfile.mkdtemp(prefix="sutando-hints-ccd-with-"))
_ccd_without = Path(tempfile.mkdtemp(prefix="sutando-hints-ccd-without-"))
_notify_py = _ccd_with / "skills/task-progress/scripts/notify.py"
_transcribe_py = _ccd_with / "skills/audio-transcribe/scripts/transcribe.py"
for f in (_notify_py, _transcribe_py):
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("# test stand-in\n")

os.environ["CLAUDE_CONFIG_DIR"] = str(_ccd_with)

_spec = importlib.util.spec_from_file_location("slackbridge_hints", SLACK_BRIDGE)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
_TASKS_DIR = Path(_tmp_ws) / "tasks"
_TASKS_DIR.mkdir(parents=True, exist_ok=True)
_mod.TASKS_DIR = _TASKS_DIR


def _write_owner_task(text: str, files: list | None = None) -> str:
    """Run _write_task as an allowed owner; return the written task body."""
    event = {"user": "U_OWNER", "channel": "CFAKE", "channel_type": "im", "ts": "1000.001"}
    if files is not None:
        event["files"] = files
    with patch.object(_mod, "load_allowed", lambda: {"U_OWNER"}), \
         patch.object(_mod, "load_tier_map", dict), \
         patch.object(_mod, "write_owner_activity", lambda *a, **k: None), \
         patch.object(_mod, "_download_slack_file", lambda fd: "/tmp/fake-voice.m4a"), \
         patch.object(_mod, "_transcribe_via_skill", lambda p: None):
        task_id = _mod._write_task(event, "DM", text, "testowner")
    assert task_id, "_write_task returned None for an allowed owner"
    return (_TASKS_DIR / f"{task_id}.txt").read_text()


# Case A: skills installed, plain text task
_body = _write_owner_task("please check the Zacks")
check(
    "slack: owner task carries the SKILL INSTRUCTIONS block",
    "===SKILL INSTRUCTIONS" in _body,
)
check(
    "slack: notify hint present when task-progress skill is installed",
    f"python3 {_notify_py}" in _body and "--source slack --channel-id CFAKE" in _body,
)
check(
    "slack: no transcribe hint for a text-only task",
    "TRANSCRIBE:" not in _body,
)

# Case B: skills NOT installed — CONTEXT-FIRST must survive, hints must not
os.environ["CLAUDE_CONFIG_DIR"] = str(_ccd_without)
_body = _write_owner_task("terse follow-up")
check(
    "slack: CONTEXT-FIRST injected even with no skills installed",
    "CONTEXT-FIRST" in _body,
)
check(
    "slack: notify hint absent when task-progress skill is missing (exists() guard)",
    "NOTIFY FIRST" not in _body,
)
os.environ["CLAUDE_CONFIG_DIR"] = str(_ccd_with)

# Case C: attachment present + audio-transcribe installed
_body = _write_owner_task("", files=[{"id": "F1", "name": "note.m4a"}])
check(
    "slack: transcribe hint present for an attachment task",
    f"TRANSCRIBE: python3 {_transcribe_py} '/tmp/fake-voice.m4a'" in _body,
)
check(
    "slack: attachment task swaps notify message to the voice variant",
    "Got your voice message" in _body,
)
check(
    "slack: task body carries the attached-file line",
    "[File attached: /tmp/fake-voice.m4a]" in _body,
)
# NOTE: CONTEXT-FIRST wording + the owner-only gate (non-owner gets no hints)
# are covered behaviorally in tests/slack-bridge-write-task.test.py.

# ---------------------------------------------------------------------------
# Discord bridge
# ---------------------------------------------------------------------------

discord_src = DISCORD_BRIDGE.read_text()

check(
    "discord: skill hints block defined for owner tasks",
    'discord_skill_hints = ""' in discord_src and 'access_tier == "owner"' in discord_src,
)
check(
    "discord: skill injection guarded by skill file existence check",
    "_notify_py.exists()" in discord_src and "_transcribe_py.exists()" in discord_src,
)
check(
    "discord: notify command uses task-progress skill",
    "task-progress/scripts/notify.py" in discord_src,
)
check(
    "discord: audio transcription command uses audio-transcribe skill",
    "audio-transcribe/scripts/transcribe.py" in discord_src,
)
check(
    "discord: skill hints appended to task_file.write_text",
    re.search(r'task_file\.write_text\(.*discord_skill_hints', discord_src, re.DOTALL) is not None,
    "discord_skill_hints not found inside write_text call",
)
check(
    "discord: SKILL INSTRUCTIONS sentinel present",
    "===SKILL INSTRUCTIONS" in discord_src,
)
check(
    "discord: audio detection checks common voice extensions",
    all(ext in discord_src for ext in (".m4a", ".ogg", ".opus")),
)

# ---------------------------------------------------------------------------
# Telegram bridge
# ---------------------------------------------------------------------------

telegram_src = TELEGRAM_BRIDGE.read_text()

check(
    "telegram: skill hints block defined",
    "tg_skill_hints" in telegram_src,
)
check(
    "telegram: skill injection guarded by skill file existence check",
    "_notify_py.exists()" in telegram_src and "_transcribe_py.exists()" in telegram_src,
)
check(
    "telegram: notify command uses task-progress skill",
    "task-progress/scripts/notify.py" in telegram_src,
)
check(
    "telegram: audio transcription command uses audio-transcribe skill",
    "audio-transcribe/scripts/transcribe.py" in telegram_src,
)
check(
    "telegram: skill hints appended to task_file.write_text",
    re.search(r'task_file\.write_text\(.*tg_skill_hints', telegram_src, re.DOTALL) is not None,
    "tg_skill_hints not found inside write_text call",
)
check(
    "telegram: SKILL INSTRUCTIONS sentinel present",
    "===SKILL INSTRUCTIONS" in telegram_src,
)
check(
    "telegram: audio detection checks ogg/oga for Telegram voice notes",
    ".oga" in telegram_src and ".ogg" in telegram_src,
)
check(
    "telegram: uses --chat-id (not --channel-id) for Telegram notify",
    "--chat-id" in telegram_src,
)

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(0 if FAIL == 0 else 1)
