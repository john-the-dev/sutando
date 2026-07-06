#!/usr/bin/env python3
"""Behavioral regression tests for discord-bridge task-write instrumentation (#1763).

PR #1763 wrapped the task_file.write_text() call in a try/except to make
silent message drops diagnosable. `_write_task_file` is the helper that
encapsulates this logic; tested here by simulating real write failures.

Run: python3 tests/discord-bridge-task-write-instrument.test.py
Exit 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent


def _load_bridge():
    """Load discord-bridge with a minimal discord stub (no live connection)."""
    if "discord" not in sys.modules:
        stub = types.ModuleType("discord")
        stub.Intents = type("Intents", (), {
            "default": staticmethod(lambda: type("I", (), {"message_content": False})()),
        })
        stub.Client = type("Client", (), {
            "__init__": lambda self, **kw: None,
            "event": staticmethod(lambda fn: fn),
        })
        stub.File = type("File", (), {})
        stub.DMChannel = type("DMChannel", (), {})
        stub.Object = lambda id: type("Object", (), {"id": id})()
        stub.MessageType = type("MessageType", (), {"default": 0, "reply": 19})()
        sys.modules["discord"] = stub

    tmp = tempfile.mkdtemp(prefix="sutando-tw-test-")
    os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-not-real")
    os.environ["SUTANDO_WORKSPACE"] = tmp
    os.environ["SUTANDO_TEST_MODE"] = "1"
    (Path(tmp) / "state").mkdir(parents=True, exist_ok=True)
    (Path(tmp) / "tasks").mkdir(parents=True, exist_ok=True)

    spec = importlib.util.spec_from_file_location(
        "discord_bridge", REPO / "src" / "discord-bridge.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, tmp


bridge, _tmp = _load_bridge()


class TestWriteTaskFile(unittest.TestCase):
    """Behavioral tests for _write_task_file — the task-write instrumentation helper."""

    def _capture(self, fn, *args, **kwargs):
        """Call fn, capture stdout, return (return_value, printed_text)."""
        buf = StringIO()
        with patch("sys.stdout", buf):
            result = fn(*args, **kwargs)
        return result, buf.getvalue()

    def test_success_returns_true(self):
        task_file = Path(_tmp) / "tasks" / "task-test-success.txt"
        ok, _ = self._capture(
            bridge._write_task_file, task_file, "content", "user1", "general", "owner", 111
        )
        self.assertTrue(ok)

    def test_success_prints_wrote_tag(self):
        task_file = Path(_tmp) / "tasks" / "task-test-wrote.txt"
        _, out = self._capture(
            bridge._write_task_file, task_file, "content", "user1", "general", "owner", 112
        )
        self.assertIn("[task-write] wrote", out)

    def test_success_log_includes_filename(self):
        task_file = Path(_tmp) / "tasks" / "task-test-filename.txt"
        _, out = self._capture(
            bridge._write_task_file, task_file, "content", "alice", "mychan", "owner", 113
        )
        self.assertIn(task_file.name, out)

    def test_success_log_includes_tier(self):
        task_file = Path(_tmp) / "tasks" / "task-test-tier.txt"
        _, out = self._capture(
            bridge._write_task_file, task_file, "content", "alice", "mychan", "team", 114
        )
        self.assertIn("tier=team", out)

    def test_failure_returns_false(self):
        readonly = Path(_tmp) / "tasks" / "task-test-ro.txt"
        with patch.object(Path, "write_text", side_effect=PermissionError("read-only")):
            ok, _ = self._capture(
                bridge._write_task_file, readonly, "content", "user1", "general", "owner", 200
            )
        self.assertFalse(ok)

    def test_failure_prints_failed_tag(self):
        p = Path(_tmp) / "tasks" / "task-test-fail-tag.txt"
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _, out = self._capture(
                bridge._write_task_file, p, "content", "bob", "chan2", "owner", 201
            )
        self.assertIn("[task-write] FAILED", out)

    def test_failure_log_includes_username(self):
        p = Path(_tmp) / "tasks" / "task-test-fail-user.txt"
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            _, out = self._capture(
                bridge._write_task_file, p, "content", "charlie", "chan3", "owner", 202
            )
        self.assertIn("@charlie", out)

    def test_failure_log_includes_exception_type(self):
        p = Path(_tmp) / "tasks" / "task-test-fail-exc.txt"
        with patch.object(Path, "write_text", side_effect=PermissionError("no write")):
            _, out = self._capture(
                bridge._write_task_file, p, "content", "dave", "chan4", "owner", 203
            )
        self.assertIn("PermissionError", out)

    def test_failure_does_not_raise(self):
        """A write exception must be caught — bridge must continue processing."""
        p = Path(_tmp) / "tasks" / "task-test-no-raise.txt"
        with patch.object(Path, "write_text", side_effect=RuntimeError("unexpected")):
            try:
                bridge._write_task_file(p, "content", "eve", "chan5", "other", 204)
            except Exception as exc:
                self.fail(f"_write_task_file propagated an exception: {exc}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
