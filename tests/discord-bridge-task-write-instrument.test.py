#!/usr/bin/env python3
"""Structural regression tests: discord-bridge task-write instrumentation (#1763).

PR #1763 wrapped the task_file.write_text() call in a try/except to make
silent message drops diagnosable. Before the fix, an exception during the
f-string build or write would silently lose the message with no log entry;
every other early-return path already logs its reason.

Post-fix behavior:
  - SUCCESS: prints "[task-write] wrote <file> (@user, #chan, tier=…)"
  - FAILURE: prints "[task-write] FAILED for @user in #chan (…): ExcType: msg"
            then returns (bridge continues; the drop is now observable)

Absence of BOTH lines in logs pinpoints a new/unmapped path; presence of
FAILED pinpoints the write as the culprit. Purely diagnostic — no happy-path
behavior change.

Run: python3 tests/discord-bridge-task-write-instrument.test.py
Exit 0 on pass, 1 on fail.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "src" / "discord-bridge.py").read_text()


def _task_write_block() -> str:
    """Return the source window covering the task-write try/except block."""
    # Anchor on the instrumentation comment, which is stable and unique.
    anchor = "early-return log pinpoints a new path"
    start = SRC.find(anchor)
    if start < 0:
        # Fall back to the try: line just before task_file.write_text
        start = SRC.rfind("try:\n        task_file.write_text(")
    if start < 0:
        return ""
    return SRC[start : start + 1200]


class TestDiscordTaskWriteInstrumentation(unittest.TestCase):

    def setUp(self):
        self._block = _task_write_block()
        self.assertGreater(len(self._block), 0, "task-write try/except block not found in discord-bridge.py")

    # ------------------------------------------------------------------
    # try/except wraps the write
    # ------------------------------------------------------------------

    def test_write_wrapped_in_try(self):
        """task_file.write_text() must be inside a try block."""
        self.assertIn(
            "try:",
            self._block,
            "task_file.write_text must be wrapped in a try: block",
        )
        self.assertIn(
            "task_file.write_text(",
            self._block,
            "task_file.write_text must be present in the instrumented block",
        )

    def test_except_catches_exception(self):
        """The except clause must catch all exceptions (Exception) to log the failure."""
        self.assertIn(
            "except Exception as _tw_exc:",
            self._block,
            "except clause must catch Exception as _tw_exc for the FAILED log",
        )

    # ------------------------------------------------------------------
    # FAILED log line
    # ------------------------------------------------------------------

    def test_failed_log_uses_task_write_tag(self):
        """The failure log line must use the [task-write] FAILED tag for greppability."""
        self.assertIn(
            '"  [task-write] FAILED',
            self._block,
            "failure log must start with '[task-write] FAILED' for structured log search",
        )

    def test_failed_log_includes_username(self):
        """The failure log must include the @username so the drop is attributable."""
        self.assertIn(
            "FAILED for @{username}",
            self._block,
            "failure log must include '@{username}' to identify the sender",
        )

    def test_failed_log_includes_exception_type(self):
        """The failure log must include the exception type and message for debugging."""
        self.assertIn(
            "{type(_tw_exc).__name__}: {_tw_exc}",
            self._block,
            "failure log must include exception type and message for root-cause diagnosis",
        )

    def test_failed_path_returns_after_logging(self):
        """After logging FAILED, the handler must return (bridge continues processing)."""
        failed_pos = self._block.find('"  [task-write] FAILED')
        return_pos = self._block.find("\n        return\n", failed_pos)
        self.assertGreater(failed_pos, 0, "[task-write] FAILED not found")
        self.assertGreater(
            return_pos,
            failed_pos,
            "return must appear after the FAILED log so bridge continues on drop",
        )

    # ------------------------------------------------------------------
    # SUCCESS log line
    # ------------------------------------------------------------------

    def test_success_log_uses_task_write_tag(self):
        """The success log must use the [task-write] wrote tag."""
        self.assertIn(
            '"  [task-write] wrote',
            self._block,
            "success log must use '[task-write] wrote' tag for structured log search",
        )

    def test_success_log_includes_filename(self):
        """The success log must include the task filename for cross-referencing."""
        self.assertIn(
            "{task_file.name}",
            self._block,
            "success log must include {task_file.name} to identify the written file",
        )

    def test_success_log_includes_tier(self):
        """The success log must include the access tier for per-tier accountability."""
        success_start = self._block.find('"  [task-write] wrote')
        self.assertGreater(success_start, 0)
        success_line = self._block[success_start : success_start + 200]
        self.assertIn(
            "tier={access_tier}",
            success_line,
            "success log must include 'tier={access_tier}' for owner-vs-team auditing",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromTestCase(TestDiscordTaskWriteInstrumentation)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
