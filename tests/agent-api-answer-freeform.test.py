#!/usr/bin/env python3
"""Regression test for POST /answer accepting free-form pending questions.

The web UI lists free-form `##` sections from pending-questions.md as Q1/Q2...
POST /answer must accept that same set. Previously it skipped sections without
**Status:**/**Options:** markers, so answering a visible Q1 returned 404.
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


class TestAgentApiAnswerFreeform(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "tasks").mkdir()
        (self.workspace / "results").mkdir()
        self.mod = load_agent_api()
        self.mod.WORKSPACE_DIR = self.workspace
        self.mod.TASK_DIR = self.workspace / "tasks"
        self.mod.RESULT_DIR = self.workspace / "results"
        self.mod.API_TOKEN = ""
        self.mod.task_history.clear()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.mod.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def post_answer(self, qid: str, answer: str):
        data = json.dumps({"id": qid, "answer": answer}).encode()
        req = urllib.request.Request(
            self.base + "/answer",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())

    def test_freeform_question_can_be_answered(self):
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## Web-chat no response diagnosis\n\n"
            "Marker-less prose question body.\n"
        )

        status, body = self.post_answer("Q1", "yes, open the fix")

        self.assertEqual(status, 200)
        self.assertEqual(body["id"], "Q1")
        updated = pq.read_text()
        self.assertIn("Marker-less prose question body.", updated)
        self.assertIn("**Status:** Answered — yes, open the fix", updated)
        self.assertEqual(len(list((self.workspace / "tasks").glob("answer-Q1-*.txt"))), 1)

    def test_already_answered_question_still_404s(self):
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## Done already\n\n"
            "**Status:** Answered — previous\n"
        )
        data = json.dumps({"id": "Q1", "answer": "again"}).encode()
        req = urllib.request.Request(
            self.base + "/answer",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(ctx.exception.code, 404)


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentApiAnswerFreeform)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
