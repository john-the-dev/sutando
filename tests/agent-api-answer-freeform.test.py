#!/usr/bin/env python3
"""Regression tests for free-form pending questions in agent-api.

GET /tasks/active must list free-form ## sections from pending-questions.md
and skip [RESOLVED] sections. POST /answer must accept that same set;
previously it skipped sections without Status:/Options: markers (404 on a
visible question). Both paths are tested against a live ThreadingHTTPServer.
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

    def get_tasks_active(self):
        with urllib.request.urlopen(self.base + "/tasks/active", timeout=5) as r:
            return json.loads(r.read().decode())

    def test_freeform_question_visible_in_tasks_active(self):
        """Free-form ## section without Status:/Options: must appear in /tasks/active."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## Should we migrate the DB now?\n\n"
            "Context: the migration has been tested on staging.\n"
        )
        body = self.get_tasks_active()
        self.assertEqual(len(body["questions"]), 1)
        self.assertIn("Should we migrate", body["questions"][0]["text"])

    def test_resolved_question_excluded_from_tasks_active(self):
        """[RESOLVED ...] sections must not appear in /tasks/active."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## [RESOLVED] Old decision\n\n"
            "This was already answered.\n\n"
            "## Open question\n\n"
            "Still pending.\n"
        )
        body = self.get_tasks_active()
        titles = [q["text"] for q in body["questions"]]
        self.assertNotIn("[RESOLVED] Old decision", titles)
        self.assertIn("Open question", titles)

    def test_preamble_not_counted_as_question(self):
        """Text before the first ## header must not generate a question entry."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\nSome preamble text.\n\n"
            "## Real question\n\nActual question body.\n"
        )
        body = self.get_tasks_active()
        self.assertEqual(len(body["questions"]), 1)
        self.assertEqual(body["questions"][0]["text"], "Real question")

    def test_concurrent_requests_do_not_block(self):
        """Two simultaneous GET /tasks/active requests must both complete (ThreadingHTTPServer)."""
        results = []
        errors = []

        def fetch():
            try:
                with urllib.request.urlopen(self.base + "/tasks/active", timeout=5) as r:
                    results.append(r.status)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=fetch) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=6)

        self.assertEqual(errors, [], f"Concurrent requests failed: {errors}")
        self.assertEqual(results, [200, 200])

    def test_answer_skips_resolved_section_in_loop(self):
        """[RESOLVED] section before the target question must be preserved — line 873."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## [RESOLVED] Q1 — old decision\n\n"
            "This was answered long ago.\n\n"
            "## Should we bump the version?\n\n"
            "The release is ready.\n"
        )
        # Q2 is the open question (Q1 is the [RESOLVED] section)
        status, body = self.post_answer("Q2", "yes, bump it")
        self.assertEqual(status, 200)
        updated = pq.read_text()
        self.assertIn("[RESOLVED] Q1 — old decision", updated)
        self.assertIn("Should we bump the version", updated)
        self.assertIn("**Status:** Answered — yes, bump it", updated)

    def test_answer_replaces_status_waiting_marker(self):
        """Section with **Status:** Waiting gets the marker replaced in-place — line 881."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## Deploy to prod?\n\n"
            "Staging tests passed.\n\n"
            "**Status:** Waiting for owner response\n"
        )
        status, body = self.post_answer("Q1", "yes, deploy now")
        self.assertEqual(status, 200)
        updated = pq.read_text()
        # The Waiting marker is replaced (not appended)
        self.assertNotIn("**Status:** Waiting", updated)
        self.assertIn("**Status:** Answered — yes, deploy now", updated)

    def test_answer_with_empty_section_preserved(self):
        """Empty ## section (blank after split) is passed through unchanged — line 864."""
        pq = self.workspace / "pending-questions.md"
        # An empty ## section arises when two consecutive ## headers appear.
        pq.write_text(
            "# Pending Questions\n\n"
            "## \n\n"            # empty section title/body
            "## Open question\n\n"
            "Still pending.\n"
        )
        # Q2 is "Open question" (Q1 is the empty section)
        status, body = self.post_answer("Q2", "acknowledged")
        self.assertEqual(status, 200)
        updated = pq.read_text()
        self.assertIn("Open question", updated)
        self.assertIn("**Status:** Answered — acknowledged", updated)

    def test_answer_skips_sub_heading_section(self):
        """Section whose title begins with '#' (sub-heading mis-parse) is skipped — lines 870-871."""
        pq = self.workspace / "pending-questions.md"
        pq.write_text(
            "# Pending Questions\n\n"
            "## # Stale header artefact\n\n"  # title = "# Stale header artefact"
            "Legacy content.\n\n"
            "## Real question\n\n"
            "Active pending item.\n"
        )
        # Q2 is "Real question" (Q1 is the # sub-heading section skipped)
        status, body = self.post_answer("Q2", "done")
        self.assertEqual(status, 200)
        updated = pq.read_text()
        self.assertIn("Real question", updated)
        self.assertIn("**Status:** Answered — done", updated)


if __name__  == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentApiAnswerFreeform)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
