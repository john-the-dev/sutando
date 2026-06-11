#!/usr/bin/env python3
"""Tests for src/friction-detector.py — pure text-parsing helpers.

Covers:
  a) check_pending_questions() — parses pending-questions.md:
       no-file, empty, (No pending questions) sentinel, resolved divider,
       **Status:** resolved skip, <24h not stale, ≥24h stale + age,
       no Asked date included, title truncation, free-form sections
  b) check_notes_without_follow_up() — parses notes/*.md for action markers:
       - [ ] checkbox, todo: prefix, follow-up:/followup: prefix, tags: todo,
       age threshold (≤7d skipped, >7d reported)

Run: python3 tests/friction-detector.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Load with SUTANDO_WORKSPACE pointing to a real temp dir so resolve_workspace()
# at import time picks something valid. We'll patch _mod.WORKSPACE per-test.
_tmp_bootstrap = tempfile.mkdtemp(prefix="friction-boot-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_bootstrap
sys.path.insert(0, str(REPO / "src"))
spec = importlib.util.spec_from_file_location(
    "friction_detector", REPO / "src" / "friction-detector.py"
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
del os.environ["SUTANDO_WORKSPACE"]

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).date().isoformat()


# ---------------------------------------------------------------------------
# (a) check_pending_questions
# ---------------------------------------------------------------------------

def _test_pending_questions():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _mod.WORKSPACE = ws

        # No file → empty
        result = _mod.check_pending_questions()
        _check("pq-no-file-empty", result == [], f"got {result}")

        pq = ws / "pending-questions.md"

        # Empty content → empty
        pq.write_text("")
        _check("pq-empty-content", _mod.check_pending_questions() == [])

        # Sentinel string → empty
        pq.write_text("(No pending questions)\n")
        _check("pq-sentinel-empty", _mod.check_pending_questions() == [])

        # Single section no Asked date — always included
        pq.write_text("## Should I buy ETH?\n\nSome context.\n")
        result = _mod.check_pending_questions()
        _check("pq-no-asked-included", len(result) == 1,
               f"got {result}")
        _check("pq-no-asked-text", "Should I buy ETH?" in result[0])

        # Asked <24h → not stale (skipped)
        today = _date(0)
        pq.write_text(f"## Is the server up?\n\n**Asked:** {today}\n")
        result = _mod.check_pending_questions()
        _check("pq-fresh-skipped", result == [], f"got {result}")

        # Asked ≥24h → stale with age
        old = _date(3)
        pq.write_text(f"## Deploy timing?\n\n**Asked:** {old}\n")
        result = _mod.check_pending_questions()
        _check("pq-stale-present", len(result) == 1, f"got {result}")
        _check("pq-stale-age", "3d old" in result[0], f"got {result}")
        _check("pq-stale-title", "Deploy timing?" in result[0])

        # Section with **Status:** resolved → skipped
        pq.write_text(
            f"## Fix prod DB\n\n**Asked:** {old}\n**Status:** resolved\n"
        )
        result = _mod.check_pending_questions()
        _check("pq-resolved-status-skipped", result == [], f"got {result}")

        # Status: answered → skipped
        pq.write_text(
            f"## Check logs\n\n**Asked:** {old}\n**Status:** answered\n"
        )
        _check("pq-answered-skipped", _mod.check_pending_questions() == [])

        # Status: done → skipped
        pq.write_text(
            f"## Merge PR?\n\n**Asked:** {old}\n**Status:** Done\n"
        )
        _check("pq-done-skipped", _mod.check_pending_questions() == [])

        # Sections below # Resolved divider are ignored
        pq.write_text(
            f"## Active question\n\n**Asked:** {old}\n\n"
            "# Resolved\n\n"
            f"## Old resolved thing\n\n**Asked:** {old}\n"
        )
        result = _mod.check_pending_questions()
        _check("pq-resolved-divider-active-kept", len(result) == 1, f"got {result}")
        _check("pq-resolved-divider-old-dropped", "Old resolved thing" not in str(result))

        # Multiple sections — mix of stale/fresh
        pq.write_text(
            f"## Stale A\n\n**Asked:** {old}\n\n"
            f"## Fresh B\n\n**Asked:** {today}\n\n"
            f"## Stale C\n\n**Asked:** {_date(7)}\n"
        )
        result = _mod.check_pending_questions()
        _check("pq-multi-two-stale", len(result) == 2, f"got {result}")
        _check("pq-multi-stale-a", any("Stale A" in r for r in result))
        _check("pq-multi-stale-c", any("Stale C" in r for r in result))

        # Title truncation — titles > 80 chars are sliced
        long_title = "A" * 100
        pq.write_text(f"## {long_title}\n\n**Asked:** {old}\n")
        result = _mod.check_pending_questions()
        _check("pq-title-truncated", len(result) == 1)
        # The slice is [:80] applied to title after "## "
        _check("pq-title-max-80", "A" * 80 in result[0] and "A" * 81 not in result[0],
               f"got {result[0]!r}")


_test_pending_questions()


# ---------------------------------------------------------------------------
# (b) check_notes_without_follow_up
# ---------------------------------------------------------------------------

def _test_notes_without_follow_up():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        notes = ws / "notes"
        notes.mkdir()
        _mod.WORKSPACE = ws

        def _write_note(name: str, content: str, days_old: int = 10) -> Path:
            p = notes / name
            p.write_text(content)
            old_mtime = time.time() - days_old * 86400
            os.utime(p, (old_mtime, old_mtime))
            return p

        # No notes dir (doesn't exist yet relative to fresh ws) → empty
        empty_ws = Path(tmp) / "nonoteshere"
        _mod.WORKSPACE = empty_ws
        _check("notes-no-dir", _mod.check_notes_without_follow_up() == [])
        _mod.WORKSPACE = ws

        # Note with - [ ] checkbox → reported when >7d
        _write_note("task-a.md", "# Task A\n\n- [ ] do something\n- [x] done\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-checkbox-reported", any("Task A" in r for r in result),
               f"got {result}")

        # Note with todo: prefix → reported when >7d
        _write_note("task-b.md", "# Task B\n\ntodo: write tests\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-todo-prefix-reported", any("Task B" in r for r in result),
               f"got {result}")

        # Note with follow-up: prefix → reported when >7d
        _write_note("task-c.md", "# Task C\n\nfollow-up: check metrics\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-follow-up-reported", any("Task C" in r for r in result),
               f"got {result}")

        # Note with followup: (no hyphen) → reported
        _write_note("task-d.md", "# Task D\n\nfollowup: ping them\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-followup-reported", any("Task D" in r for r in result),
               f"got {result}")

        # Note with tags: todo → reported
        _write_note("task-e.md", "# Task E\n\ntags: research, todo, voice\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-tags-todo-reported", any("Task E" in r for r in result),
               f"got {result}")

        # Note ≤7 days old → NOT reported
        _write_note("fresh.md", "# Fresh Note\n\n- [ ] fresh task\n", days_old=3)
        result = _mod.check_notes_without_follow_up()
        _check("notes-fresh-not-reported", not any("Fresh Note" in r for r in result),
               f"got {result}")

        # Note at 6 days old (≤7 → skipped; condition is strictly > 7)
        _write_note("boundary.md", "# Boundary\n\n- [ ] boundary task\n", days_old=6)
        result = _mod.check_notes_without_follow_up()
        _check("notes-6day-boundary-skipped", not any("Boundary" in r for r in result),
               f"got {result}")

        # Note with no action markers → NOT reported
        _write_note("clean.md", "# Clean Note\n\nJust some text.\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-no-marker-not-reported", not any("Clean Note" in r for r in result),
               f"got {result}")

        # Note with 'action: ...' prose (not a directive) → NOT reported
        # (action: was dropped per code comment — too noisy)
        _write_note("action-prose.md",
                    "# Shortcuts Research\n\nAction: Get Contents of URL\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-action-prose-not-reported",
               not any("Shortcuts Research" in r for r in result),
               f"got {result}")

        # Reported result contains filename stem as title
        _write_note("my-research-note.md", "# My Research\n\n- [ ] follow up\n", days_old=10)
        result = _mod.check_notes_without_follow_up()
        _check("notes-title-in-result",
               any("My Research Note" in r for r in result),
               f"got {result}")


_test_notes_without_follow_up()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"friction-detector: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
