#!/usr/bin/env python3
"""Behavioral tests for Codex durable-schedule reconciliation."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

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

    def test_rejects_invalid_config_and_state_shapes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            crons.write_text("{}")
            with self.assertRaisesRegex(ValueError, "JSON list"):
                reconcile_launchd.reconcile(crons, state)

            crons.write_text("[]")
            state.write_text("[]")
            with self.assertRaisesRegex(ValueError, "JSON object"):
                reconcile_launchd.reconcile(crons, state)

    def test_skips_entries_without_valid_names(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            crons.write_text(json.dumps([
                "not-an-object",
                {"cron": "* * * * *", "prompt": "missing name"},
                {"name": "", "cron": "* * * * *", "prompt": "empty name"},
                {"name": "proactive", "cron": "* * * * *", "prompt": "/proactive-loop"},
            ]))

            result = reconcile_launchd.reconcile(crons, state, now=10)

            self.assertEqual(result, {"migrated": [], "runner_needed": False})
            self.assertFalse(state.exists())

    def test_default_paths_use_workspace_and_host_helpers(self):
        crons, state = reconcile_launchd._default_paths()
        self.assertEqual(crons.name, "crons.json")
        self.assertEqual(crons.parent.parent.name, "hosts")
        self.assertEqual(state.name, "cron-runner-state.json")
        self.assertEqual(state.parent.name, "state")
        self.assertEqual(crons.parents[2], state.parents[1])

    def test_main_handles_missing_and_present_config(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            crons = root / "crons.json"
            state = root / "state.json"
            output = StringIO()
            with mock.patch.object(reconcile_launchd, "_default_paths", return_value=(crons, state)):
                with redirect_stdout(output):
                    self.assertEqual(reconcile_launchd.main(), 0)
            self.assertEqual(output.getvalue().strip(), "runner_needed=0 migrated=0")

            crons.write_text(json.dumps([
                {"name": "digest", "cron": "2 6 * * *", "prompt": "run"},
            ]))
            output = StringIO()
            with mock.patch.object(reconcile_launchd, "_default_paths", return_value=(crons, state)):
                with redirect_stdout(output):
                    self.assertEqual(reconcile_launchd.main(), 0)
            self.assertEqual(
                output.getvalue().strip(),
                "runner_needed=1 migrated=1 names=digest",
            )


if __name__ == "__main__":
    unittest.main()
