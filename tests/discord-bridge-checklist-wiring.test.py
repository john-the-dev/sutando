#!/usr/bin/env python3
"""Structural regression test: discord-bridge.py checklist wiring (issue #1104).

Checks that the bridge contains the necessary imports, event handler, and
poll_results intercept without requiring Discord API access.

Run: python3 tests/discord-bridge-checklist-wiring.test.py
Exit: 0 = pass, 1 = fail
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "src" / "discord-bridge.py").read_text()


class TestChecklistWiring(unittest.TestCase):

    def test_checklist_render_import_present(self):
        """discord-bridge.py must import from checklist_render (guarded by try/except)."""
        self.assertIn("from checklist_render import", SRC)

    def test_import_is_guarded_by_try_except(self):
        """Import must be wrapped in try/except so the bridge starts without the skill."""
        try_pos = SRC.find("try:")
        import_pos = SRC.find("from checklist_render import")
        except_pos = SRC.find("_CHECKLIST_SKILL_AVAILABLE = False")
        self.assertGreater(import_pos, try_pos, "checklist import must be inside try block")
        self.assertGreater(except_pos, import_pos, "_CHECKLIST_SKILL_AVAILABLE = False must follow import")

    def test_on_interaction_handler_present(self):
        """on_interaction event handler must exist for button click processing."""
        self.assertIn("async def on_interaction(", SRC)

    def test_on_interaction_decorated(self):
        """on_interaction must be registered with @client.event."""
        ci_pos = SRC.find("async def on_interaction(")
        # Search backward for @client.event
        snippet = SRC[max(0, ci_pos - 200):ci_pos]
        self.assertIn("@client.event", snippet)

    def test_on_interaction_calls_parse_custom_id(self):
        """on_interaction must use parse_custom_id to validate the button."""
        ci_start = SRC.find("async def on_interaction(")
        # Find the end of this function (next top-level @client.event or async def)
        ci_end = SRC.find("\n@client.event", ci_start + 1)
        if ci_end == -1:
            ci_end = SRC.find("\nasync def _handle_discord_message", ci_start + 1)
        handler_body = SRC[ci_start:ci_end]
        self.assertIn("parse_custom_id", handler_body)

    def test_on_interaction_access_control_present(self):
        """on_interaction must check load_allowed() — no unauthenticated clicks."""
        ci_start = SRC.find("async def on_interaction(")
        ci_end = SRC.find("\nasync def _handle_discord_message", ci_start + 1)
        handler_body = SRC[ci_start:ci_end]
        self.assertIn("load_allowed()", handler_body)

    def test_maybe_post_checklist_present(self):
        """_maybe_post_checklist helper must exist as the poll_results intercept."""
        self.assertIn("async def _maybe_post_checklist(", SRC)

    def test_poll_results_intercept_present(self):
        """poll_results must call _maybe_post_checklist before the normal send path."""
        intercept_pos = SRC.find("await _maybe_post_checklist(")
        self.assertGreater(intercept_pos, 0, "_maybe_post_checklist call not found in poll_results")
        # The intercept must come before the first chunk-send for that result
        send_pos = SRC.find("for chunk in _chunk_for_discord(clean_text)", intercept_pos)
        self.assertGreater(send_pos, intercept_pos,
            "_maybe_post_checklist must precede the chunk-send loop")

    def test_checklist_skill_availability_flag(self):
        """_CHECKLIST_SKILL_AVAILABLE flag must guard on_interaction and helper."""
        self.assertIn("_CHECKLIST_SKILL_AVAILABLE", SRC)
        # Both the import guard and the on_interaction guard must reference it
        occurrences = SRC.count("_CHECKLIST_SKILL_AVAILABLE")
        self.assertGreaterEqual(occurrences, 3,
            "Expected ≥3 references: import-true, import-false, on_interaction guard")

    def test_state_persisted_via_save_state(self):
        """on_interaction must persist click state via save_state."""
        ci_start = SRC.find("async def on_interaction(")
        ci_end = SRC.find("\nasync def _handle_discord_message", ci_start + 1)
        handler_body = SRC[ci_start:ci_end]
        self.assertIn("save_state(", handler_body)


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__("__main__"))
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        print(f"All {result.testsRun} discord-bridge-checklist-wiring tests passed.")
        sys.exit(0)
    else:
        sys.exit(1)
