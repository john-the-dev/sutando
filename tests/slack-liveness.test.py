#!/usr/bin/env python3
"""Tests for src/slack-liveness.py — the self-reported Slack "last alive" indicator.

CI-safe: no network (Slack calls are monkeypatched), no daemon loop.
Run: python3 tests/slack-liveness.test.py
"""

from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("slack_liveness", REPO / "src" / "slack-liveness.py")
liveness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(liveness)


class HeartbeatFreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hb = self.tmp / "hb"

    def test_fresh_heartbeat(self):
        now = 1_000_000
        self.hb.write_text(str(now - 30))
        self.assertTrue(liveness.heartbeat_fresh(self.hb, 120, now))

    def test_stale_heartbeat(self):
        now = 1_000_000
        self.hb.write_text(str(now - 500))
        self.assertFalse(liveness.heartbeat_fresh(self.hb, 120, now))

    def test_missing_heartbeat(self):
        self.assertFalse(liveness.heartbeat_fresh(self.tmp / "nope", 120, 1_000_000))

    def test_garbage_heartbeat(self):
        self.hb.write_text("")  # the empty-file race that flaked codex-core-launcher
        self.assertFalse(liveness.heartbeat_fresh(self.hb, 120, 1_000_000))


class ComposeTests(unittest.TestCase):
    def test_alive_message(self):
        m = liveness.compose_message(True, "11:23", 5)
        self.assertIn("online", m)
        self.assertIn("11:23", m)
        self.assertIn("5 min", m)
        self.assertIn("large_green_circle", m)

    def test_offline_message(self):
        m = liveness.compose_message(False, "11:23", 5)
        self.assertIn("offline", m)
        self.assertIn("11:23", m)
        self.assertIn("red_circle", m)


class _FakeApi:
    """Records calls; returns scripted responses keyed by method."""
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        r = self.responses.get(method, {"ok": True, "ts": "1.1"})
        return r


class PostOrUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state.json"
        self._orig = liveness._api

    def tearDown(self):
        liveness._api = self._orig

    def test_first_post_saves_ts(self):
        liveness._api = _FakeApi({"chat.postMessage": {"ok": True, "ts": "111.222"}})
        r = liveness.post_or_update("tok", "C1", "hi", self.state)
        self.assertTrue(r["ok"])
        saved = __import__("json").loads(self.state.read_text())
        self.assertEqual(saved, {"channel": "C1", "ts": "111.222"})
        self.assertEqual(liveness._api.calls[0][0], "chat.postMessage")

    def test_subsequent_updates_same_message(self):
        self.state.write_text('{"channel": "C1", "ts": "111.222"}')
        liveness._api = _FakeApi({"chat.update": {"ok": True, "ts": "111.222"}})
        liveness.post_or_update("tok", "C1", "hi again", self.state)
        self.assertEqual(liveness._api.calls[0][0], "chat.update")
        self.assertEqual(liveness._api.calls[0][1]["ts"], "111.222")

    def test_reposts_when_message_gone(self):
        self.state.write_text('{"channel": "C1", "ts": "111.222"}')
        liveness._api = _FakeApi({
            "chat.update": {"ok": False, "error": "message_not_found"},
            "chat.postMessage": {"ok": True, "ts": "333.444"},
        })
        r = liveness.post_or_update("tok", "C1", "revive", self.state)
        self.assertTrue(r["ok"])
        methods = [c[0] for c in liveness._api.calls]
        self.assertEqual(methods, ["chat.update", "chat.postMessage"])
        saved = __import__("json").loads(self.state.read_text())
        self.assertEqual(saved["ts"], "333.444")


class TickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state.json"
        self.hb = self.tmp / "hb"
        self._orig = liveness._api
        liveness._api = _FakeApi({"chat.postMessage": {"ok": True, "ts": "1.1"},
                                  "chat.update": {"ok": True, "ts": "1.1"}})

    def tearDown(self):
        liveness._api = self._orig

    def test_tick_alive_advances_clock(self):
        now = 1_000_000
        self.hb.write_text(str(now - 10))  # fresh
        store = {}
        liveness.tick("tok", "C1", heartbeat=self.hb, state_path=self.state,
                      stale_sec=120, interval_min=5, now=now, last_alive_store=store)
        self.assertIn("hhmm", store)  # clock advanced because alive
        sent = liveness._api.calls[-1][1]["text"]
        self.assertIn("online", sent)

    def test_tick_stale_freezes_and_flips_offline(self):
        # First a fresh tick to set a last-alive time...
        t0 = 1_000_000
        self.hb.write_text(str(t0 - 10))
        store = {}
        liveness.tick("tok", "C1", heartbeat=self.hb, state_path=self.state,
                      stale_sec=120, interval_min=5, now=t0, last_alive_store=store)
        frozen = store["hhmm"]
        # ...then a stale tick: clock must NOT advance, message flips offline.
        self.hb.write_text(str(t0 - 999))
        liveness.tick("tok", "C1", heartbeat=self.hb, state_path=self.state,
                      stale_sec=120, interval_min=5, now=t0 + 3600, last_alive_store=store)
        self.assertEqual(store["hhmm"], frozen)  # frozen at last-alive
        sent = liveness._api.calls[-1][1]["text"]
        self.assertIn("offline", sent)


class ResolveChannelTests(unittest.TestCase):
    def test_literal_channel_passthrough(self):
        # A real id passes through untouched — no network.
        self.assertEqual(liveness.resolve_channel("tok", "C12345"), "C12345")

    def test_owner_ids_from_access(self):
        tmp = Path(tempfile.mkdtemp())
        acc = tmp / ".claude-sutando" / "channels" / "slack"
        acc.mkdir(parents=True)
        (acc / "access.json").write_text('{"allowFrom": ["U1", "U2"]}')
        orig = liveness._WS
        liveness._WS = tmp
        try:
            self.assertEqual(liveness._owner_ids_from_access(), ["U1", "U2"])
        finally:
            liveness._WS = orig

    def test_owner_ids_missing_file(self):
        tmp = Path(tempfile.mkdtemp())
        orig = liveness._WS
        liveness._WS = tmp
        try:
            self.assertEqual(liveness._owner_ids_from_access(), [])
        finally:
            liveness._WS = orig


class MainOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.state = self.tmp / "state.json"
        self.hb = self.tmp / "hb"
        self.hb.write_text(str(int(time.time()) - 5))  # fresh
        self._orig = liveness._api
        liveness._api = _FakeApi({"chat.postMessage": {"ok": True, "ts": "1.1"}})
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"

    def tearDown(self):
        liveness._api = self._orig
        os.environ.pop("SLACK_BOT_TOKEN", None)

    def test_main_once_posts_and_returns_zero(self):
        rc = liveness.main(["--channel", "C9", "--heartbeat", str(self.hb),
                            "--state", str(self.state), "--once"])
        self.assertEqual(rc, 0)
        self.assertEqual(liveness._api.calls[-1][0], "chat.postMessage")

    def test_main_missing_token_returns_1(self):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        orig = liveness._bot_token
        liveness._bot_token = lambda: None
        try:
            rc = liveness.main(["--channel", "C9", "--heartbeat", str(self.hb),
                                "--state", str(self.state), "--once"])
        finally:
            liveness._bot_token = orig
        self.assertEqual(rc, 1)

    def test_bot_token_from_env(self):
        self.assertEqual(liveness._bot_token(), "xoxb-test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
