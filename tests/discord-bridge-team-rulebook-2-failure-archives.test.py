#!/usr/bin/env python3
"""Block 2's FAILURE fallback must archive the task, exactly as 2b now does.

#3775 fixed block 2b — the owner-ping path left a task with no result, so it
sat in tasks/ forever and both health-check's task-queue probe and the
end-of-pass queue check reported it unanswered. The identical instruction
survived one branch up, in block 2's FAILURE fallback: `grep -c` for the
forbidding wording returned 2 on the pre-#3775 tree and 1 after.

That branch fires more often than 2b in one respect: 2b needs a human-judgement
case, while this one triggers on stall (125), cap (124), or any gh/codex error.

Run: python3 tests/discord-bridge-team-rulebook-2-failure-archives.test.py
"""
import unittest
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "src" / "discord-bridge.py").read_text()


ANCHOR = ("then write exactly `[no-send]` to results/task-{id}.txt "
          "so the task archives")
REVERTED = "and do NOT write results/task-{id}.txt"


def block_2(text: str) -> str:
    start = text.index("2. PR-REVIEW REQUEST")
    end = text.index("2b. MESSAGE OWNER", start)
    return text[start:end]


class Block2FailureArchives(unittest.TestCase):
    def test_the_failure_fallback_instructs_a_no_send_result(self):
        b = block_2(SRC)
        self.assertIn("On FAILURE", b)
        self.assertIn("[no-send]", b)
        self.assertIn("results/task-{id}.txt", b)

    def test_the_failure_fallback_no_longer_forbids_the_task_result(self):
        self.assertNotIn("do NOT write results/task-{id}.txt", block_2(SRC))

    def test_the_failure_fallback_still_pings_the_owner(self):
        self.assertIn("results/proactive-{ts}.txt", block_2(SRC))

    def test_NEITHER_branch_forbids_the_task_result_anywhere_in_the_rulebook(self):
        """The whole point: 2b alone left the sibling live. Assert on the file,
        so a third branch added later with the old wording fails here too."""
        self.assertNotIn("NOT write results/task-{id}", SRC)
        self.assertNotIn("Do NOT write to results/task-{id}", SRC)

    def test_the_replacement_ANCHOR_still_exists(self):
        """A fixture guard, NOT a detector control. If the anchor drifts out of
        SRC, `replace` no-ops and the revert test below silently checks the
        unmutated file while still passing."""
        self.assertIn(ANCHOR, SRC)

    def test_BOTH_detectors_flip_on_the_reverted_wording(self):
        """The real control: the pre-fix text must fail THIS suite's own
        predicates. The block-scoped one is easy — the revert inserts the very
        string it looks for. The file-scoped detector uses different wording and
        a different haystack, so it needs its own arm or nothing shows it is
        sensitive at all."""
        old = SRC.replace(ANCHOR, REVERTED)
        self.assertNotEqual(old, SRC, "anchor drifted: nothing was mutated")
        self.assertIn("do NOT write results/task-{id}.txt", block_2(old))
        self.assertIn("NOT write results/task-{id}", old)


if __name__ == "__main__":
    unittest.main()
