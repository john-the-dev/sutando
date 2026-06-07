#!/usr/bin/env python3
"""Unit tests for skills/checklist-respond/scripts/checklist_render.py.

Run: python3 tests/discord-checklist-render.test.py
Exit: 0 = all pass, 1 = failure
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "skills" / "checklist-respond" / "scripts"))
from checklist_render import (
    CUSTOM_ID_PREFIX,
    apply_click,
    build_view_spec,
    parse_checklist_marker,
    parse_custom_id,
    render_vote_summary,
    save_state,
    load_state,
)


class TestParseChecklistMarker(unittest.TestCase):

    def test_inline_pipe_form(self):
        text = "Pick one: [checklist: Yes | No | Maybe]"
        result = parse_checklist_marker(text)
        self.assertIsNotNone(result)
        items, body = result
        self.assertEqual(items, ["Yes", "No", "Maybe"])
        self.assertNotIn("[checklist", body)

    def test_bare_marker_with_list(self):
        text = "Options:\n[checklist]\n- Alpha\n- Beta\n- Gamma"
        result = parse_checklist_marker(text)
        self.assertIsNotNone(result)
        items, body = result
        self.assertEqual(items, ["Alpha", "Beta", "Gamma"])
        self.assertNotIn("[checklist]", body.lower())

    def test_no_marker_returns_none(self):
        self.assertIsNone(parse_checklist_marker("plain text"))
        self.assertIsNone(parse_checklist_marker(""))

    def test_bare_marker_no_list_returns_none(self):
        # [checklist] with no markdown list below is not a valid checklist
        self.assertIsNone(parse_checklist_marker("[checklist]\nNo list here."))

    def test_case_insensitive(self):
        text = "[CHECKLIST: A | B]"
        result = parse_checklist_marker(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], ["A", "B"])

    def test_asterisk_list_items(self):
        text = "[checklist]\n* One\n* Two"
        result = parse_checklist_marker(text)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], ["One", "Two"])


class TestBuildViewSpec(unittest.TestCase):

    def test_single_row(self):
        items = ["A", "B", "C"]
        rows = build_view_spec(items, "msg123")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]["buttons"]), 3)

    def test_custom_id_format(self):
        rows = build_view_spec(["X", "Y"], "msg42")
        cid0 = rows[0]["buttons"][0]["custom_id"]
        self.assertTrue(cid0.startswith(CUSTOM_ID_PREFIX))
        self.assertIn("msg42", cid0)
        self.assertIn(":0", cid0)

    def test_row_wraps_at_5(self):
        items = [str(i) for i in range(6)]
        rows = build_view_spec(items, "m")
        self.assertEqual(len(rows), 2)
        self.assertEqual(len(rows[0]["buttons"]), 5)
        self.assertEqual(len(rows[1]["buttons"]), 1)

    def test_max_rows_25_items(self):
        items = [str(i) for i in range(30)]
        rows = build_view_spec(items, "m")
        self.assertLessEqual(len(rows), 5)

    def test_label_truncated_to_80(self):
        long_item = "x" * 100
        rows = build_view_spec([long_item], "m")
        self.assertLessEqual(len(rows[0]["buttons"][0]["label"]), 80)

    def test_default_style_secondary(self):
        rows = build_view_spec(["A"], "m")
        self.assertEqual(rows[0]["buttons"][0]["style"], "secondary")


class TestApplyClick(unittest.TestCase):

    def test_first_click_adds_vote(self):
        state = apply_click({}, 0, "u1", "Alice", ["A", "B"])
        self.assertIn("u1", state["votes"]["0"])

    def test_second_click_toggles_off(self):
        state = {"items": ["A"], "votes": {"0": {"u1": "Alice"}}}
        state = apply_click(state, 0, "u1", "Alice", ["A"])
        self.assertNotIn("u1", state["votes"]["0"])

    def test_two_voters_independent(self):
        state = apply_click({}, 0, "u1", "Alice", ["A"])
        state = apply_click(state, 0, "u2", "Bob", ["A"])
        self.assertIn("u1", state["votes"]["0"])
        self.assertIn("u2", state["votes"]["0"])

    def test_different_items_independent(self):
        state = apply_click({}, 0, "u1", "Alice", ["A", "B"])
        state = apply_click(state, 1, "u1", "Alice", ["A", "B"])
        self.assertIn("u1", state["votes"]["0"])
        self.assertIn("u1", state["votes"]["1"])


class TestRenderVoteSummary(unittest.TestCase):

    def test_no_votes(self):
        state = {"items": ["A", "B"], "votes": {}}
        summary = render_vote_summary(state)
        self.assertIn("• A", summary)
        self.assertIn("• B", summary)
        self.assertNotIn("✅", summary)

    def test_with_vote(self):
        state = {"items": ["A", "B"], "votes": {"0": {"u1": "Alice"}}}
        summary = render_vote_summary(state)
        self.assertIn("✅ Alice", summary)
        self.assertIn("• B\n", summary + "\n")

    def test_empty_state(self):
        self.assertEqual(render_vote_summary({}), "")


class TestParseCustomId(unittest.TestCase):

    def test_valid(self):
        cid = f"{CUSTOM_ID_PREFIX}msg999:3"
        result = parse_custom_id(cid)
        self.assertEqual(result, ("msg999", 3))

    def test_invalid_prefix(self):
        self.assertIsNone(parse_custom_id("other:msg:0"))

    def test_non_int_index(self):
        self.assertIsNone(parse_custom_id(f"{CUSTOM_ID_PREFIX}msg:notint"))

    def test_msg_id_with_colons(self):
        # msg_id itself may contain colons (Discord snowflake won't, but defensive)
        cid = f"{CUSTOM_ID_PREFIX}abc:def:2"
        result = parse_custom_id(cid)
        self.assertEqual(result, ("abc:def", 2))


class TestStatePersistence(unittest.TestCase):

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir)
            state = {"items": ["X"], "votes": {"0": {"u1": "Alice"}}}
            save_state(state_dir, "msg1", state)
            loaded = load_state(state_dir, "msg1")
            self.assertEqual(loaded, state)

    def test_load_missing_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            loaded = load_state(Path(tmpdir), "nonexistent")
            self.assertEqual(loaded, {})


if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__("__main__"))
    runner = unittest.TextTestRunner(verbosity=0)
    result = runner.run(suite)
    if result.wasSuccessful():
        print(f"All {result.testsRun} discord-checklist-render tests passed.")
        sys.exit(0)
    else:
        sys.exit(1)
