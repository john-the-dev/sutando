#!/usr/bin/env python3
"""Tests for health-check durable schedule ownership and heartbeat."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "health-check.py"
SPEC = importlib.util.spec_from_file_location("health_check", SCRIPT)
assert SPEC and SPEC.loader
health = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(health)


class CronRunnerHealthTest(unittest.TestCase):
    def _workspace(self, root: Path, entries: list[dict]) -> Path:
        workspace = root / "workspace"
        config = workspace / "hosts" / "test-host" / "crons.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps(entries))
        return workspace

    def test_codex_session_schedule_is_reported_orphaned(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = self._workspace(Path(td), [
                {"name": "main-loop", "cron": "*/5 * * * *", "prompt_skill": "proactive-loop"},
                {"name": "digest", "cron": "2 6 * * *", "prompt": "run"},
            ])
            check = health.check_cron_runner(
                workspace, host_label="test-host", runtime="codex"
            )
            self.assertEqual(check["status"], "down")
            self.assertIn("1 configured schedule(s)", check["detail"])

    def test_missing_launchd_service_is_down(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = self._workspace(Path(td), [
                {"name": "digest", "cron": "2 6 * * *", "prompt": "run", "launchd": True},
            ])
            check = health.check_cron_runner(
                workspace,
                host_label="test-host",
                runtime="codex",
                launchd_check=lambda _: {"status": "not_loaded"},
            )
            self.assertEqual(check["status"], "down")
            self.assertIn("not_loaded", check["detail"])

    def test_loaded_runner_requires_fresh_state(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            workspace = self._workspace(root, [
                {"name": "digest", "cron": "2 6 * * *", "prompt": "run", "launchd": True},
            ])
            state = workspace / "state" / "cron-runner-state.json"
            state.parent.mkdir(parents=True)
            state.write_text("{}")
            os.utime(state, (100, 100))
            launchd_ok = lambda _: {"status": "ok"}

            stale = health.check_cron_runner(
                workspace, "test-host", "codex", launchd_ok, now=400
            )
            fresh = health.check_cron_runner(
                workspace, "test-host", "codex", launchd_ok, now=200
            )
            self.assertEqual(stale["status"], "down")
            self.assertIn("stale", stale["detail"])
            self.assertEqual(fresh["status"], "ok")
            self.assertIn("1 durable schedule(s)", fresh["detail"])


if __name__ == "__main__":
    unittest.main()
