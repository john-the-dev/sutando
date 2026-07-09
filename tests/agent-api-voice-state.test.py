#!/usr/bin/env python3
"""Coverage + regression tests for the voice-state POST handlers in agent-api.

POST /voice/toggle flips voice_desired_state between "connected" and
"disconnected"; POST /voice/set writes an explicit state. Both mutate the
module-global voice_desired_state under _voice_state_lock (ThreadingHTTPServer
serves each request on its own thread, so the read-modify-write must be
guarded). These tests drive both endpoints against a live server so the
`with _voice_state_lock:` critical sections execute.
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_agent_api():
    spec = importlib.util.spec_from_file_location("agent_api", REPO / "src" / "agent-api.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestAgentApiVoiceState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "tasks").mkdir()
        (self.workspace / "results").mkdir()
        self.mod = load_agent_api()
        self.mod.WORKSPACE_DIR = self.workspace
        self.mod.TASK_DIR = self.workspace / "tasks"
        self.mod.RESULT_DIR = self.workspace / "results"
        self.mod.API_TOKEN = ""  # no-token install → check_auth passes
        # Known starting point so the toggle assertion is deterministic.
        self.mod.voice_desired_state = "disconnected"
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.mod.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def post(self, path: str, payload=None):
        data = json.dumps(payload).encode() if payload is not None else b""
        req = urllib.request.Request(
            self.base + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_toggle_flips_state_under_lock(self):
        """POST /voice/toggle flips disconnected -> connected -> disconnected."""
        status, body = self.post("/voice/toggle")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "connected")
        self.assertEqual(self.mod.voice_desired_state, "connected")

        status, body = self.post("/voice/toggle")
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "disconnected")
        self.assertEqual(self.mod.voice_desired_state, "disconnected")

    def test_set_writes_explicit_state_under_lock(self):
        """POST /voice/set stores the requested state verbatim."""
        status, body = self.post("/voice/set", {"state": "connected"})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "connected")
        self.assertEqual(self.mod.voice_desired_state, "connected")

    def test_set_defaults_to_disconnected_when_state_absent(self):
        """POST /voice/set with no 'state' key defaults to disconnected."""
        self.mod.voice_desired_state = "connected"
        status, body = self.post("/voice/set", {})
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "disconnected")
        self.assertEqual(self.mod.voice_desired_state, "disconnected")

    def test_set_rejects_invalid_body(self):
        """POST /voice/set with a non-JSON body returns 400 and does not mutate."""
        self.mod.voice_desired_state = "connected"
        req = urllib.request.Request(
            self.base + "/voice/set",
            data=b"not json{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 400)
        # State unchanged after the rejected write.
        self.assertEqual(self.mod.voice_desired_state, "connected")

    def test_concurrent_toggles_do_not_corrupt_state(self):
        """Parallel toggles must both complete against the ThreadingHTTPServer."""
        results = []
        errors = []

        def hit():
            try:
                s, b = self.post("/voice/toggle")
                results.append(b["state"])
            except Exception as e:  # pragma: no cover - only on failure
                errors.append(e)

        threads = [threading.Thread(target=hit) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=6)

        self.assertEqual(errors, [], f"Concurrent toggles failed: {errors}")
        self.assertEqual(len(results), 4)
        # 4 flips from "disconnected" return to "disconnected".
        self.assertEqual(self.mod.voice_desired_state, "disconnected")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentApiVoiceState)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
