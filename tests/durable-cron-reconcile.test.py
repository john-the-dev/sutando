#!/usr/bin/env python3
"""Behavioral tests for Codex durable-schedule reconciliation."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "schedule-crons" / "scripts" / "reconcile_launchd.py"
SPEC = importlib.util.spec_from_file_location("reconcile_launchd", SCRIPT)
assert SPEC and SPEC.loader
reconcile_launchd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reconcile_launchd)


class DurableCronReconcileTest(unittest.TestCase):
    def test_migrates_codex_schedules_and_initializes_boundary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            crons.write_text(json.dumps([
                {"name": "main-loop", "cron": "*/5 * * * *", "prompt_skill": "proactive-loop"},
                {"name": "digest", "cron": "2 6 * * *", "prompt": "run digest"},
            ]))

            result = reconcile_launchd.reconcile(crons, state, now=12345)

            self.assertEqual(result["migrated"], ["digest"])
            entries = json.loads(crons.read_text())
            self.assertNotIn("launchd", entries[0])
            self.assertIs(entries[1]["launchd"], True)
            self.assertEqual(json.loads(state.read_text()), {"digest": 12345})

    def test_preserves_other_owners_and_existing_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            crons.write_text(json.dumps([
                {"name": "existing", "cron": "0 8 * * *", "prompt": "x", "launchd": True},
                {"name": "codex", "cron": "0 9 * * *", "prompt": "x", "execution": "codex-task"},
                {"name": "dynamic", "loop": "dynamic", "prompt": "x"},
                {"name": "new", "cron": "0 10 * * *", "prompt": "x"},
            ]))
            state.write_text(json.dumps({"existing": 100, "unrelated": 200}))

            result = reconcile_launchd.reconcile(crons, state, now=300)

            self.assertEqual(result["migrated"], ["new"])
            self.assertEqual(
                json.loads(state.read_text()),
                {"existing": 100, "unrelated": 200, "new": 300},
            )

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            crons.write_text(json.dumps([
                {"name": "digest", "cron": "2 6 * * *", "prompt": "x"},
            ]))
            first = reconcile_launchd.reconcile(crons, state, now=10)
            second = reconcile_launchd.reconcile(crons, state, now=20)

            self.assertEqual(first["migrated"], ["digest"])
            self.assertEqual(second["migrated"], [])
            self.assertEqual(json.loads(state.read_text()), {"digest": 10})


if __name__ == "__main__":
    unittest.main()
