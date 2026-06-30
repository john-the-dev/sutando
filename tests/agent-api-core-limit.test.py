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
        pane = "You've hit your session limit \u00b7 resets 9:50am (America/Los_Angeles)\n"
        state = mod.detect_core_session_limit(pane)

        self.assertTrue(state["limited"])
        self.assertEqual(state["reset"], "9:50am (America/Los_Angeles)")
        self.assertIn("9:50am", state["message"])

    def test_no_limit_text_is_not_limited(self):
        mod = load_agent_api()
        self.assertEqual(mod.detect_core_session_limit("❯ "), {"limited": False})


if __name__ == "__main__":
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAgentApiCoreLimit)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
