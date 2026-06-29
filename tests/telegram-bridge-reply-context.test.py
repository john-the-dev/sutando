#!/usr/bin/env python3
"""Structural regression tests: Telegram reply-context and CONTEXT-FIRST (#1782).

PR #1782 added three features to the telegram task-write block:
  1. source_message_id field — always written when message_id is present.
  2. parent_message_id field — written when the message is a reply (reply_to_message).
  3. [Replying to @user: text] embed — appended to the task body for terse replies.
  4. CONTEXT-FIRST step in SKILL INSTRUCTIONS — always emitted (unlike NOTIFY which
     is gated on skill presence), because Telegram's Bot API has no history-fetch.

These tests guard against accidental removal of the reply-context infrastructure
that reconstructable-reply behavior depends on.

Run: python3 tests/telegram-bridge-reply-context.test.py
Exit 0 on pass, 1 on fail.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = (REPO / "src" / "telegram-bridge.py").read_text()


def _task_write_block() -> str:
    """Return the source window covering the reply-context + task-write block."""
    start = SRC.find("reply_note = ")
    if start < 0:
        return ""
    # Generous window: reply_note init → past the task_file.write_text() call
    return SRC[start : start + 2500]


class TestTelegramReplyContext(unittest.TestCase):

    def setUp(self):
        self._block = _task_write_block()
        self.assertGreater(len(self._block), 0, "reply_note init block not found in telegram-bridge.py")

    # ------------------------------------------------------------------
    # source_message_id
    # ------------------------------------------------------------------

    def test_source_message_id_field_formed(self):
        """The task file must include a source_message_id: header from msg.message_id."""
        self.assertIn(
            'src_line = f"source_message_id: {_src_mid}\\n"',
            self._block,
            "source_message_id line must be formed from _src_mid",
        )

    def test_source_message_id_written_to_task_file(self):
        """src_line must be interpolated into the task file f-string."""
        # The write_text call uses f"{src_line}" as one line of the multi-line f-string.
        self.assertIn(
            'f"{src_line}"',
            SRC,
            "task_file.write_text must include f\"{src_line}\"",
        )

    # ------------------------------------------------------------------
    # parent_message_id
    # ------------------------------------------------------------------

    def test_parent_message_id_field_formed(self):
        """When reply_to_message is present, parent_message_id: header must be formed."""
        self.assertIn(
            'parent_line = f"parent_message_id: {_rep_mid}\\n"',
            self._block,
            "parent_message_id line must be formed from _rep_mid",
        )

    def test_parent_message_id_written_to_task_file(self):
        """parent_line must be interpolated into the task file f-string."""
        # The write_text call uses f"{parent_line}" as one line of the multi-line f-string.
        self.assertIn(
            'f"{parent_line}"',
            SRC,
            "task_file.write_text must include f\"{parent_line}\"",
        )

    # ------------------------------------------------------------------
    # [Replying to @user: text] embed
    # ------------------------------------------------------------------

    def test_reply_note_embed_formed(self):
        """reply_note must embed '@username' and the quoted reply text."""
        self.assertIn(
            'reply_note = f"\\n\\n[Replying to @{_rep_user}: {_rep_text}]"',
            self._block,
            "reply_note must include [Replying to @user: text] format",
        )

    def test_reply_text_truncated_at_300(self):
        """Embedded reply text must be capped at 300 chars to bound task-file size."""
        # Source line: ).replace("\n", " ")[:300]
        self.assertIn(
            ")[:300]",
            self._block,
            "reply_text extraction must be truncated at [:300]",
        )

    def test_reply_note_appended_to_task_body(self):
        """reply_note must be appended inside the task: line's confine_user_content call."""
        # The task: f-string includes {reply_note} as part of the quoted text block.
        self.assertIn(
            "{reply_note}",
            SRC,
            "task body must include {reply_note} so the quote reaches the agent",
        )

    def test_reply_note_newlines_stripped_in_quoted_text(self):
        """Newlines in the quoted reply text must be collapsed (replace('\\n', ' ')).

        A multi-line reply-to message would otherwise break the single-line task: header.
        """
        self.assertIn(
            '.replace("\\n", " ")',
            self._block,
            "reply-to message text must have newlines replaced before embedding",
        )

    # ------------------------------------------------------------------
    # CONTEXT-FIRST step
    # ------------------------------------------------------------------

    def test_context_first_step_present_in_skill_instructions(self):
        """CONTEXT-FIRST step must be in the SKILL INSTRUCTIONS block (always emitted)."""
        self.assertIn(
            "CONTEXT-FIRST",
            SRC,
            "CONTEXT-FIRST step must appear in telegram-bridge.py SKILL INSTRUCTIONS",
        )

    def test_context_first_mentions_session_transcript_fallback(self):
        """CONTEXT-FIRST must name the session transcript as the fallback reconstruct source.

        Unlike Discord (which can pull message history), Telegram relies on the
        embedded [Replying to ...] quote + session transcript. The instruction must
        explicitly name both so the agent doesn't hallucinate a history-fetch.
        """
        # Find the CONTEXT-FIRST lines window
        cf_start = SRC.find("CONTEXT-FIRST")
        self.assertGreater(cf_start, 0)
        cf_block = SRC[cf_start : cf_start + 600]
        self.assertIn(
            "session transcript",
            cf_block,
            "CONTEXT-FIRST step must mention 'session transcript' as the fallback source",
        )

    def test_context_first_emitted_before_notify_step(self):
        """CONTEXT-FIRST (step 1) must appear before the NOTIFY FIRST step in source order.

        Reconstruction must happen before the agent sends a notification, otherwise
        a terse reply like 'no' triggers an alert before the agent knows what it's about.
        """
        cf_pos = SRC.find("CONTEXT-FIRST")
        notify_pos = SRC.find("NOTIFY FIRST")
        self.assertGreater(cf_pos, 0, "CONTEXT-FIRST not found in telegram-bridge.py")
        self.assertGreater(notify_pos, 0, "NOTIFY FIRST not found in telegram-bridge.py")
        self.assertLess(
            cf_pos,
            notify_pos,
            "CONTEXT-FIRST must appear before NOTIFY FIRST in source order",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromTestCase(TestTelegramReplyContext)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
