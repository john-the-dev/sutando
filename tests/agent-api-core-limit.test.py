#!/usr/bin/env python3
"""Regression tests for surfacing Claude Code session-limit state."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load_agent_api():
    spec = importlib.util.spec_from_file_location("agent_api", REPO / "src" / "agent-api.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestAgentApiCoreLimit(unittest.TestCase):
    def test_detects_session_limit_reset_text(self):
        mod = load_agent_api()
        pane = "You've hit your session limit · resets 9:50am (America/Los_Angeles)\n"
        state = mod.detect_core_session_limit(pane)

        self.assertTrue(state["limited"])
        self.assertEqual(state["reset"], "9:50am (America/Los_Angeles)")
        self.assertIn("9:50am", state["message"])

    def test_no_limit_text_is_not_limited(self):
        mod = load_agent_api()
        self.assertEqual(mod.detect_core_session_limit("❯ "), {"limited": False})

    def test_stale_scrollback_not_reported_as_limited(self):
        """Limit text in scrollback followed by a fresh REPL prompt = session reset; not limited."""
        mod = load_agent_api()
        pane = (
            "You’ve hit your session limit · resets 9:50am (America/Los_Angeles)\n"
            "some output\n"
            "❯ continuing work after reset\n"
        )
        self.assertEqual(mod.detect_core_session_limit(pane), {"limited": False})

    def test_limit_with_no_subsequent_prompt_is_still_limited(self):
        """Limit text with no REPL prompt after it = genuinely limited."""
        mod = load_agent_api()
        pane = (
            "❯ previous session activity\n"
            "You’ve hit your session limit · resets 10:00am (America/Los_Angeles)\n"
        )
        state = mod.detect_core_session_limit(pane)
        self.assertTrue(state["limited"])
        self.assertEqual(state["reset"], "10:00am (America/Los_Angeles)")


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentApiCoreLimit)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
