#!/usr/bin/env python3
"""Tests for src/slack-liveness.py — self-reported Slack liveness on the App Home tab.

CI-safe: no network (views.publish is monkeypatched), no daemon loop.
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

    def test_fresh(self):
        now = 1_000_000
        self.hb.write_text(str(now - 30))
        self.assertTrue(liveness.heartbeat_fresh(self.hb, 120, now))

    def test_stale(self):
        now = 1_000_000
        self.hb.write_text(str(now - 500))
        self.assertFalse(liveness.heartbeat_fresh(self.hb, 120, now))

    def test_missing(self):
        self.assertFalse(liveness.heartbeat_fresh(self.tmp / "nope", 120, 1_000_000))

    def test_garbage(self):
        self.hb.write_text("")  # empty-file race
        self.assertFalse(liveness.heartbeat_fresh(self.hb, 120, 1_000_000))


class HomeViewTests(unittest.TestCase):
    def test_alive_view(self):
        v = liveness.build_home_view(True, "11:23", 5)
        self.assertEqual(v["type"], "home")
        blob = str(v)
        self.assertIn("Online", blob)
        self.assertIn("11:23", blob)
        self.assertIn("large_green_circle", blob)

    def test_offline_view(self):
        v = liveness.build_home_view(False, "11:23", 5)
        blob = str(v)
        self.assertIn("offline", blob)
        self.assertIn("red_circle", blob)


class _FakeApi:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, token, method, payload):
        self.calls.append((method, payload))
        return self.responses.get(method, {"ok": True})


class PublishTests(unittest.TestCase):
    def setUp(self):
        self._orig = liveness._api

    def tearDown(self):
        liveness._api = self._orig

    def test_publish_home_targets_user(self):
        liveness._api = _FakeApi({"views.publish": {"ok": True}})
        r = liveness.publish_home("tok", "U9", {"type": "home", "blocks": []})
        self.assertTrue(r["ok"])
        method, payload = liveness._api.calls[0]
        self.assertEqual(method, "views.publish")
        self.assertEqual(payload["user_id"], "U9")


class TickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hb = self.tmp / "hb"
        self._orig = liveness._api
        liveness._api = _FakeApi({"views.publish": {"ok": True}})

    def tearDown(self):
        liveness._api = self._orig

    def test_alive_advances_clock_and_publishes_online(self):
        now = 1_000_000
        self.hb.write_text(str(now - 10))
        store = {}
        liveness.tick("tok", "U1", heartbeat=self.hb, stale_sec=120,
                      interval_min=5, now=now, last_alive_store=store)
        self.assertIn("hhmm", store)
        view = liveness._api.calls[-1][1]["view"]
        self.assertIn("Online", str(view))

    def test_stale_freezes_time_and_flips_offline(self):
        t0 = 1_000_000
        self.hb.write_text(str(t0 - 10))
        store = {}
        liveness.tick("tok", "U1", heartbeat=self.hb, stale_sec=120,
                      interval_min=5, now=t0, last_alive_store=store)
        frozen = store["hhmm"]
        self.hb.write_text(str(t0 - 999))
        liveness.tick("tok", "U1", heartbeat=self.hb, stale_sec=120,
                      interval_min=5, now=t0 + 3600, last_alive_store=store)
        self.assertEqual(store["hhmm"], frozen)
        self.assertIn("offline", str(liveness._api.calls[-1][1]["view"]))


class OwnerResolveTests(unittest.TestCase):
    def _write_access(self, tmp, payload):
        acc = tmp / ".claude-sutando" / "channels" / "slack"
        acc.mkdir(parents=True)
        (acc / "access.json").write_text(payload)

    def test_prefers_tofu_owner_over_list_order(self):
        # A collaborator (team tier) is listed FIRST; the real owner must win.
        tmp = Path(tempfile.mkdtemp())
        self._write_access(tmp, '{"allowFrom": ["Ucollab", "Uowner"], '
                                '"tierMap": {"Ucollab": "team", "Uowner": "owner"}, '
                                '"tofuOwner": "Uowner"}')
        orig = liveness._WS
        liveness._WS = tmp
        try:
            self.assertEqual(liveness._resolve_owner_id(), "Uowner")
        finally:
            liveness._WS = orig

    def test_missing_file_returns_none(self):
        tmp = Path(tempfile.mkdtemp())
        orig = liveness._WS
        liveness._WS = tmp
        try:
            self.assertIsNone(liveness._resolve_owner_id())
        finally:
            liveness._WS = orig


class MainOnceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hb = self.tmp / "hb"
        self.hb.write_text(str(int(time.time()) - 5))
        self._orig = liveness._api
        liveness._api = _FakeApi({"views.publish": {"ok": True}})
        os.environ["SLACK_BOT_TOKEN"] = "xoxb-test"

    def tearDown(self):
        liveness._api = self._orig
        os.environ.pop("SLACK_BOT_TOKEN", None)

    def test_main_once_explicit_user(self):
        rc = liveness.main(["--user", "U9", "--heartbeat", str(self.hb), "--once"])
        self.assertEqual(rc, 0)
        self.assertEqual(liveness._api.calls[-1][0], "views.publish")

    def test_main_missing_token(self):
        os.environ.pop("SLACK_BOT_TOKEN", None)
        orig = liveness._bot_token
        liveness._bot_token = lambda: None
        try:
            rc = liveness.main(["--user", "U9", "--heartbeat", str(self.hb), "--once"])
        finally:
            liveness._bot_token = orig
        self.assertEqual(rc, 1)

    def test_main_owner_unresolvable(self):
        orig = liveness._resolve_owner_id
        liveness._resolve_owner_id = lambda: None
        try:
            rc = liveness.main(["--user", "owner", "--heartbeat", str(self.hb), "--once"])
        finally:
            liveness._resolve_owner_id = orig
        self.assertEqual(rc, 1)

    def test_bot_token_from_env(self):
        self.assertEqual(liveness._bot_token(), "xoxb-test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
