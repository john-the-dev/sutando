#!/usr/bin/env python3
"""Tests for the Slack online-presence helper in src/slack-bridge.py.

`_assert_presence_auto()` sets the bot's Slack presence to "auto" (Slack then
shows it active while connected). It must never raise — a keep-alive loop
depends on it returning a status dict on every outcome, including the
missing_scope case (no users:write scope) and arbitrary transport errors.

CI-safe: stubs slack_bolt, no network.
Run: python3 tests/slack-bridge-presence.test.py
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _load_bridge(workspace: Path):
    os.environ["SLACK_BOT_TOKEN"] = "xoxb-test-token"
    os.environ["SLACK_APP_TOKEN"] = "xapp-test-token"
    os.environ["SUTANDO_WORKSPACE"] = str(workspace)
    os.environ["SUTANDO_TEST_MODE"] = "1"
    sys.modules.pop("slack_bridge_under_test", None)

    class _StubApp:
        def __init__(self, *a, **kw):
            self.client = types.SimpleNamespace()

        def event(self, _name):
            return lambda fn: fn

    try:
        import slack_bolt as _bolt
        _bolt.App = _StubApp
    except ImportError:
        stub = types.ModuleType("slack_bolt")
        stub.App = _StubApp
        sys.modules["slack_bolt"] = stub
    for pkg in ("slack_bolt.adapter", "slack_bolt.adapter.socket_mode"):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            if pkg.endswith("socket_mode"):
                m.SocketModeHandler = object
            sys.modules[pkg] = m

    sys.path.insert(0, str(REPO / "src"))
    spec = importlib.util.spec_from_file_location(
        "slack_bridge_under_test", REPO / "src" / "slack-bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeResp:
    def __init__(self, data):
        self._d = data

    def get(self, k, default=None):
        return self._d.get(k, default)


class _ApiError(Exception):
    """Stand-in for slack_sdk.errors.SlackApiError (carries a .response)."""
    def __init__(self, error):
        super().__init__(error)
        self.response = _FakeResp({"error": error})


class _FakeClient:
    def __init__(self, raises=None):
        self._raises = raises
        self.calls = []

    def users_setPresence(self, presence=None):
        self.calls.append(presence)
        if self._raises is not None:
            raise self._raises
        return {"ok": True}


class PresenceHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="slack-presence-test-"))
        cls.mod = _load_bridge(cls.tmp)

    def test_success_sets_auto(self):
        c = _FakeClient()
        out = self.mod._assert_presence_auto(c)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(c.calls, ["auto"])

    def test_missing_scope_reported_not_raised(self):
        c = _FakeClient(raises=_ApiError("missing_scope"))
        out = self.mod._assert_presence_auto(c)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "missing_scope")

    def test_other_api_error_surfaced(self):
        c = _FakeClient(raises=_ApiError("ratelimited"))
        out = self.mod._assert_presence_auto(c)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "ratelimited")

    def test_transport_exception_without_response(self):
        c = _FakeClient(raises=ValueError("boom"))
        out = self.mod._assert_presence_auto(c)
        self.assertFalse(out["ok"])
        self.assertIn("boom", out["error"])

    def test_helpers_exist(self):
        # The keep-alive thread entrypoint must exist for main() to wire it.
        self.assertTrue(callable(self.mod._presence_keepalive))


if __name__ == "__main__":
    unittest.main(verbosity=2)
