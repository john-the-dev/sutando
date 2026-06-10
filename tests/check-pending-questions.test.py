#!/usr/bin/env python3
"""Tests for src/check-pending-questions.py — pending question detection.

Covers:
  a) get_waiting_questions() — empty / missing file returns []
  b) get_waiting_questions() — free-form section (no Status:) is unanswered
  c) get_waiting_questions() — explicit unanswered/waiting status included
  d) get_waiting_questions() — explicit resolved/done/answered status skipped
  e) get_waiting_questions() — sections below # Resolved divider excluded
  f) get_waiting_questions() — mixed resolved/unresolved sections, correct count
  g) presenter_mode_active() — absent sentinel → False
  h) presenter_mode_active() — future ISO timestamp → True
  i) presenter_mode_active() — past ISO timestamp → False
  j) presenter_mode_active() — malformed sentinel (non-digit start) → False (fail-closed)
  k) should_notify() — no prior notify file → True
  l) should_notify() — recent notify (< 1h ago) → False
  m) should_notify() — old notify (> 1h ago) → True

Run: python3 tests/check-pending-questions.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "check_pending_questions",
    REPO / "src" / "check-pending-questions.py",
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _with_tmp(fn):
    """Run fn(tmp_dir: Path) with module paths patched to a fresh temp dir."""
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        orig_pq = _mod.PQ_FILE
        orig_last = _mod.LAST_NOTIFY_FILE
        orig_sentinel = _mod.PRESENTER_SENTINEL
        _mod.PQ_FILE = td_path / "pending-questions.md"
        _mod.LAST_NOTIFY_FILE = td_path / ".last-pq-notify"
        _mod.PRESENTER_SENTINEL = td_path / "state" / "presenter-mode.sentinel"
        (td_path / "state").mkdir()
        try:
            fn(td_path)
        finally:
            _mod.PQ_FILE = orig_pq
            _mod.LAST_NOTIFY_FILE = orig_last
            _mod.PRESENTER_SENTINEL = orig_sentinel


# ---------------------------------------------------------------------------
# (a) Missing file → empty list
# ---------------------------------------------------------------------------

def _test_missing_file():
    def run(td: Path):
        # PQ_FILE does not exist
        result = _mod.get_waiting_questions()
        _check("missing-file-empty", result == [], f"got {result!r}")

    _with_tmp(run)


_test_missing_file()


# ---------------------------------------------------------------------------
# (b) Free-form section (no Status marker) → unanswered
# ---------------------------------------------------------------------------

def _test_freeform_section():
    def run(td: Path):
        _mod.PQ_FILE.write_text(
            "# Pending Questions\n\n"
            "## Do we need a timeout here?\n\n"
            "The bridge currently waits indefinitely...\n"
        )
        qs = _mod.get_waiting_questions()
        _check("freeform-count", len(qs) == 1, f"got {len(qs)} questions")
        _check("freeform-title", qs[0]["title"] == "Do we need a timeout here?", f"got {qs[0]['title']!r}")

    _with_tmp(run)


_test_freeform_section()


# ---------------------------------------------------------------------------
# (c) Explicit unanswered / waiting status → included
# ---------------------------------------------------------------------------

def _test_explicit_unanswered():
    def run(td: Path):
        _mod.PQ_FILE.write_text(
            "## Q1 — First\n\n**Status:** unanswered\n\nSome body.\n\n"
            "## Q2 — Second\n\n**Status:** Waiting for input\n\nOther body.\n"
        )
        qs = _mod.get_waiting_questions()
        _check("explicit-unanswered-count", len(qs) == 2, f"got {len(qs)}")
        titles = {q["title"] for q in qs}
        _check("explicit-unanswered-titles", "Q1 — First" in titles and "Q2 — Second" in titles,
               f"got {titles!r}")

    _with_tmp(run)


_test_explicit_unanswered()


# ---------------------------------------------------------------------------
# (d) Explicit resolved / done / answered → skipped
# ---------------------------------------------------------------------------

def _test_explicit_resolved_statuses():
    def run(td: Path):
        _mod.PQ_FILE.write_text(
            "## Already done\n\n**Status:** resolved\n\nbody\n\n"
            "## Also done\n\n**Status:** done\n\nbody\n\n"
            "## Answered already\n\n**Status:** answered — 2026-06-01\n\nbody\n\n"
            "## Still open\n\n**Status:** unanswered\n\nbody\n"
        )
        qs = _mod.get_waiting_questions()
        _check("explicit-skip-count", len(qs) == 1, f"got {len(qs)}")
        _check("explicit-skip-title", qs[0]["title"] == "Still open", f"got {qs[0]['title']!r}")

    _with_tmp(run)


_test_explicit_resolved_statuses()


# ---------------------------------------------------------------------------
# (e) Sections below # Resolved divider excluded
# ---------------------------------------------------------------------------

def _test_resolved_divider():
    def run(td: Path):
        _mod.PQ_FILE.write_text(
            "## Open question\n\nNo status, should be pending.\n\n"
            "# Resolved\n\n"
            "## Already answered\n\nThis should NOT show up.\n"
        )
        qs = _mod.get_waiting_questions()
        _check("divider-count", len(qs) == 1, f"got {len(qs)}")
        _check("divider-title", qs[0]["title"] == "Open question", f"got {qs[0]['title']!r}")

    _with_tmp(run)


_test_resolved_divider()


# ---------------------------------------------------------------------------
# (f) Mixed: resolved divider + some open, some explicit-resolved above it
# ---------------------------------------------------------------------------

def _test_mixed_sections():
    def run(td: Path):
        _mod.PQ_FILE.write_text(
            "## Open one\n\nNo status.\n\n"
            "## Closed explicit\n\n**Status:** answered\n\nbody\n\n"
            "## Open two\n\nAlso no status.\n\n"
            "# Resolved\n\n"
            "## Historic\n\nShould be excluded.\n"
        )
        qs = _mod.get_waiting_questions()
        _check("mixed-count", len(qs) == 2, f"got {len(qs)}")
        titles = {q["title"] for q in qs}
        _check("mixed-titles", titles == {"Open one", "Open two"}, f"got {titles!r}")

    _with_tmp(run)


_test_mixed_sections()


# ---------------------------------------------------------------------------
# (g) presenter_mode_active() — sentinel absent → False
# ---------------------------------------------------------------------------

def _test_presenter_absent():
    def run(td: Path):
        # PRESENTER_SENTINEL was patched; it doesn't exist in tmp dir
        result = _mod.presenter_mode_active()
        _check("presenter-absent", result is False, f"got {result!r}")

    _with_tmp(run)


_test_presenter_absent()


# ---------------------------------------------------------------------------
# (h) presenter_mode_active() — future ISO timestamp → True
# ---------------------------------------------------------------------------

def _test_presenter_future():
    def run(td: Path):
        future = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() + 7200))
        _mod.PRESENTER_SENTINEL.write_text(future)
        result = _mod.presenter_mode_active()
        _check("presenter-future", result is True, f"future={future!r}, got {result!r}")

    _with_tmp(run)


_test_presenter_future()


# ---------------------------------------------------------------------------
# (i) presenter_mode_active() — past ISO timestamp → False
# ---------------------------------------------------------------------------

def _test_presenter_past():
    def run(td: Path):
        past = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(time.time() - 7200))
        _mod.PRESENTER_SENTINEL.write_text(past)
        result = _mod.presenter_mode_active()
        _check("presenter-past", result is False, f"past={past!r}, got {result!r}")

    _with_tmp(run)


_test_presenter_past()


# ---------------------------------------------------------------------------
# (j) presenter_mode_active() — malformed sentinel (non-digit start) → False
#     Guards against the fail-open bug: "garbage" < "2..." in ASCII sort,
#     so a naive comparison would report presenter mode as always active.
# ---------------------------------------------------------------------------

def _test_presenter_malformed():
    def run(td: Path):
        for bad_content in ("garbage", "not-a-date", "", "  "):
            _mod.PRESENTER_SENTINEL.write_text(bad_content)
            result = _mod.presenter_mode_active()
            _check(f"presenter-malformed-{bad_content!r}", result is False,
                   f"malformed sentinel {bad_content!r} must NOT enable presenter mode, got {result!r}")

    _with_tmp(run)


_test_presenter_malformed()


# ---------------------------------------------------------------------------
# (k) should_notify() — no prior file → True
# ---------------------------------------------------------------------------

def _test_notify_no_prior():
    def run(td: Path):
        # LAST_NOTIFY_FILE doesn't exist
        result = _mod.should_notify()
        _check("notify-no-prior", result is True, f"got {result!r}")

    _with_tmp(run)


_test_notify_no_prior()


# ---------------------------------------------------------------------------
# (l) should_notify() — recent notify (30s ago) → False (within 1h cooldown)
# ---------------------------------------------------------------------------

def _test_notify_recent():
    def run(td: Path):
        _mod.LAST_NOTIFY_FILE.write_text(str(int(time.time())))
        # Touch mtime to 30s ago
        import os
        recent = time.time() - 30
        os.utime(_mod.LAST_NOTIFY_FILE, (recent, recent))
        result = _mod.should_notify()
        _check("notify-recent", result is False, f"got {result!r}")

    _with_tmp(run)


_test_notify_recent()


# ---------------------------------------------------------------------------
# (m) should_notify() — old notify (2h ago) → True (past cooldown)
# ---------------------------------------------------------------------------

def _test_notify_old():
    def run(td: Path):
        _mod.LAST_NOTIFY_FILE.write_text(str(int(time.time())))
        import os
        old = time.time() - 7200  # 2 hours ago
        os.utime(_mod.LAST_NOTIFY_FILE, (old, old))
        result = _mod.should_notify()
        _check("notify-old", result is True, f"got {result!r}")

    _with_tmp(run)


_test_notify_old()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"check-pending-questions: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
