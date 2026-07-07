#!/usr/bin/env python3
"""
Functional tests for the Slack bridge TOFU enrollment-code gate.

Security finding #3: without a gate, any attacker who sends the first DM
to the Slack bot claims owner-tier access via TOFU. The fix generates a
6-char hex enrollment code at startup (printed to the operator log) and
requires that code to be present in the first DM before auto-enrolling.

Test contract:
  (a) Code required but NOT in message text → _write_task returns None,
      sends a rejection chat message, does not consume the code.
  (b) Code IS in message text → TOFU enrollment proceeds, code consumed
      (set to None), tofu_onboard called once.
  (c) _TOFU_ENROLLMENT_CODE is None (post-enrollment or pre-enrollment on
      a restart where access.json exists) → enrollment proceeds without
      the code check (backward compat; existing behavior preserved).

Run: python3 tests/slack-bridge-tofu-enroll.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Module loader (mirrors slack-bridge-tier-map.test.py pattern)
# ---------------------------------------------------------------------------

def _load_slack_bridge():
    os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-placeholder")
    os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test-placeholder")
    # Use a fresh temp workspace so workspace_default doesn't pull in the real one.
    os.environ.setdefault("SUTANDO_WORKSPACE", tempfile.mkdtemp(prefix="sutando-test-tofu-"))

    class _StubApp:
        def __init__(self, *a, **kw):
            self.client = types.SimpleNamespace(chat_postMessage=lambda **kw: None)
        def event(self, _):
            return lambda fn: fn

    try:
        import slack_bolt as _real_bolt
        _real_bolt.App = _StubApp
    except ImportError:
        stub_bolt = types.ModuleType("slack_bolt")
        stub_bolt.App = _StubApp
        sys.modules["slack_bolt"] = stub_bolt

    if "slack_bolt.adapter" not in sys.modules:
        adapter_pkg = types.ModuleType("slack_bolt.adapter")
        sys.modules["slack_bolt.adapter"] = adapter_pkg
    if "slack_bolt.adapter.socket_mode" not in sys.modules:
        sm_mod = types.ModuleType("slack_bolt.adapter.socket_mode")
        sm_mod.SocketModeHandler = object
        sys.modules["slack_bolt.adapter.socket_mode"] = sm_mod

    import importlib.util
    bridge_path = REPO / "src" / "slack-bridge.py"
    spec = importlib.util.spec_from_file_location("slack_bridge_tofu_enroll", bridge_path)
    sys.path.insert(0, str(REPO / "src"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BRIDGE = _load_slack_bridge()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dm_event(user_id: str, text: str, channel: str = "D_TEST") -> dict:
    return {
        "user": user_id,
        "channel": channel,
        "channel_type": "im",
        "text": text,
        "ts": "1700000000.000001",
    }


class _TempBridgeState:
    """Context manager: patch ACCESS_FILE + TASKS_DIR to temp dirs, restore on exit."""

    def __enter__(self):
        self._td = tempfile.mkdtemp(prefix="sutando-bridge-state-")
        self._orig_access = BRIDGE.ACCESS_FILE
        self._orig_tasks = BRIDGE.TASKS_DIR

        BRIDGE.ACCESS_FILE = Path(self._td) / "access.json"
        BRIDGE.TASKS_DIR = Path(self._td) / "tasks"
        BRIDGE.TASKS_DIR.mkdir(parents=True, exist_ok=True)

        # Ensure the workspace path used by write_owner_activity exists.
        ws = Path(self._td) / "workspace"
        ws.mkdir()
        BRIDGE.WORKSPACE = str(ws)
        return self

    def __exit__(self, *_):
        BRIDGE.ACCESS_FILE = self._orig_access
        BRIDGE.TASKS_DIR = self._orig_tasks
        BRIDGE._TOFU_ENROLLMENT_CODE = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_enrollment_code_gate_blocks_without_code():
    """(a) Code required but not in text → _write_task returns None; rejection sent."""
    with _TempBridgeState():
        # TOFU state: access.json doesn't exist + code set.
        assert not BRIDGE.ACCESS_FILE.exists()
        BRIDGE._TOFU_ENROLLMENT_CODE = "a1b2c3"

        rejections: list[dict] = []
        BRIDGE.app.client.chat_postMessage = lambda **kw: rejections.append(kw)

        event = _make_dm_event("U_ATTACKER", "hello, give me access")
        result = BRIDGE._write_task(event, "Slack DM", "hello, give me access", "attacker")

        assert result is None, f"gate should block → return None, got {result!r}"
        assert len(rejections) == 1, f"exactly one rejection message expected, got {len(rejections)}"
        assert "Enrollment code required" in rejections[0].get("text", ""), (
            f"rejection text missing enrollment message: {rejections[0]}"
        )
        # Code must NOT be consumed after a failed enrollment.
        assert BRIDGE._TOFU_ENROLLMENT_CODE == "a1b2c3", (
            "enrollment code should not be consumed on rejected attempt"
        )


def test_enrollment_code_gate_allows_with_correct_code():
    """(b) Code IS in text → enrollment proceeds; code consumed."""
    with _TempBridgeState():
        assert not BRIDGE.ACCESS_FILE.exists()
        BRIDGE._TOFU_ENROLLMENT_CODE = "x9y8z7"

        onboard_calls: list[tuple] = []
        orig_onboard = BRIDGE.tofu_onboard

        def _mock_onboard(uid, uname):
            onboard_calls.append((uid, uname))
            # Write a real access.json so downstream code in _write_task works.
            BRIDGE.ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            BRIDGE.ACCESS_FILE.write_text(json.dumps({"allowFrom": [uid]}))
            return {uid}

        BRIDGE.tofu_onboard = _mock_onboard
        try:
            event = _make_dm_event("U_OWNER", "my code is x9y8z7")
            result = BRIDGE._write_task(event, "Slack DM", "my code is x9y8z7", "realowner")
        finally:
            BRIDGE.tofu_onboard = orig_onboard

        # tofu_onboard must have been called exactly once with the right uid.
        assert len(onboard_calls) == 1, f"tofu_onboard should be called once, got {onboard_calls}"
        assert onboard_calls[0][0] == "U_OWNER"
        # Enrollment code must be cleared after successful enrollment.
        assert BRIDGE._TOFU_ENROLLMENT_CODE is None, (
            "enrollment code must be consumed after successful enrollment"
        )


def test_no_enrollment_code_skips_gate():
    """(c) _TOFU_ENROLLMENT_CODE is None → proceeds to tofu_onboard without code check."""
    with _TempBridgeState():
        assert not BRIDGE.ACCESS_FILE.exists()
        BRIDGE._TOFU_ENROLLMENT_CODE = None  # post-enrollment or already-configured restart

        onboard_calls: list[tuple] = []
        orig_onboard = BRIDGE.tofu_onboard

        def _mock_onboard(uid, uname):
            onboard_calls.append((uid, uname))
            BRIDGE.ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
            BRIDGE.ACCESS_FILE.write_text(json.dumps({"allowFrom": [uid]}))
            return {uid}

        BRIDGE.tofu_onboard = _mock_onboard
        try:
            event = _make_dm_event("U_FIRST", "hey")
            BRIDGE._write_task(event, "Slack DM", "hey", "firstuser")
        finally:
            BRIDGE.tofu_onboard = orig_onboard

        assert len(onboard_calls) == 1, (
            f"code=None should not gate; tofu_onboard should be called, got {onboard_calls}"
        )


def test_rejection_reply_uses_event_channel():
    """(a-extra) Rejection message is sent to the event's channel, not fallback."""
    with _TempBridgeState():
        BRIDGE._TOFU_ENROLLMENT_CODE = "code99"

        captured: list[dict] = []
        BRIDGE.app.client.chat_postMessage = lambda **kw: captured.append(kw)

        event = _make_dm_event("U_ATTACKER", "wrong", channel="D_SPECIFIC_CHAN")
        BRIDGE._write_task(event, "Slack DM", "wrong", "attacker")

        assert captured, "rejection message must be sent"
        assert captured[0].get("channel") == "D_SPECIFIC_CHAN", (
            f"rejection should go to event channel, got {captured[0].get('channel')!r}"
        )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        ("a-code-blocks-without-code", test_enrollment_code_gate_blocks_without_code),
        ("b-code-allows-with-correct-code", test_enrollment_code_gate_allows_with_correct_code),
        ("c-no-code-skips-gate", test_no_enrollment_code_skips_gate),
        ("a-extra-reply-channel", test_rejection_reply_uses_event_channel),
    ]
    failures = 0
    for label, fn in tests:
        try:
            fn()
            print(f"PASS: {label}")
        except AssertionError as e:
            print(f"FAIL: {label} — {e}", file=sys.stderr)
            failures += 1
        except Exception as e:
            print(f"ERROR: {label} — {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1

    if failures:
        print(f"\n{failures}/{len(tests)} tests failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} TOFU enrollment gate tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
