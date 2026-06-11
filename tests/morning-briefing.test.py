#!/usr/bin/env python3
"""Tests for src/morning-briefing.py — pure helper functions.

Covers:
  a) synthesize()            — greeting by hour, weather/events/reminders/
                               discord/pending-qs/health/insight sections,
                               insight raw-data filter, clean-day closing
  b) get_pending_questions() — section parsing, # Resolved divider,
                               date-prefix stripping, title truncation

Run: python3 tests/morning-briefing.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent

_tmp_ws = tempfile.mkdtemp(prefix="mb-boot-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))
spec = importlib.util.spec_from_file_location(
    "morning_briefing", REPO / "src" / "morning-briefing.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["morning_briefing"] = _mod  # needed for patch() to resolve the module
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


def _synth(weather=None, events=None, reminders=None, discord_msgs=None,
           pending_qs=None, health_issues=None, insight=None) -> str:
    return _mod.synthesize(
        weather,
        events or [],
        reminders or [],
        discord_msgs or [],
        pending_qs or [],
        health_issues or [],
        insight,
    )


# ---------------------------------------------------------------------------
# (a) synthesize
# ---------------------------------------------------------------------------

def _test_synthesize():
    # Greeting by hour: morning (0–11), afternoon (12–16), evening (17+)
    for hour, expected in [(6, "Good morning"), (11, "Good morning"),
                           (12, "Good afternoon"), (16, "Good afternoon"),
                           (17, "Good evening"), (23, "Good evening")]:
        with patch("morning_briefing.datetime") as mock_dt:
            import datetime as real_dt
            mock_dt.now.return_value = real_dt.datetime(2026, 6, 10, hour, 0, 0)
            mock_dt.side_effect = lambda *a, **kw: real_dt.datetime(*a, **kw)
            result = _mod.synthesize(None, [], [], [], [], [])
        _check(f"synth-greeting-{hour}", expected in result, f"got {result[:40]!r}")

    # Weather included when present
    result = _synth(weather="72°F and clear, high of 78, low of 58")
    _check("synth-weather-present", "72°F" in result)

    # Weather absent → not in output
    result = _synth(weather=None)
    _check("synth-weather-absent", "°F" not in result)

    # Calendar clear → "clear today"
    result = _synth(events=[])
    _check("synth-cal-clear", "clear today" in result)

    # One event → "One meeting today"
    result = _synth(events=[{"raw": "10:00am Standup", "calendar": "Work"}])
    _check("synth-one-event", "One meeting today" in result)
    _check("synth-one-event-detail", "10:00am Standup" in result)

    # Multiple events → count + first up
    events = [
        {"raw": "9:00am Sync", "calendar": "Work"},
        {"raw": "2:00pm Review", "calendar": "Work"},
        {"raw": "4:00pm Retro", "calendar": "Work"},
    ]
    result = _synth(events=events)
    _check("synth-multi-events-count", "3 meetings" in result)
    _check("synth-multi-events-first", "9:00am Sync" in result)

    # Reminders included
    result = _synth(reminders=["Buy groceries", "Call dentist"])
    _check("synth-reminders-present", "Reminders due" in result)
    _check("synth-reminders-item", "Buy groceries" in result)

    # No reminders → no mention
    result = _synth(reminders=[])
    _check("synth-no-reminders", "Reminders" not in result)

    # One pending question → "One pending question"
    result = _synth(pending_qs=["Should I merge the PR?"])
    _check("synth-one-pq", "One pending question" in result)
    _check("synth-one-pq-text", "Should I merge" in result)

    # Multiple pending questions → count + top item
    result = _synth(pending_qs=["First question", "Second question", "Third question"])
    _check("synth-multi-pq-count", "3 pending questions" in result)
    _check("synth-multi-pq-top", "First question" in result)

    # Discord messages → count
    result = _synth(discord_msgs=["user1: hey", "user2: ping"])
    _check("synth-discord-count", "2 Discord messages" in result)

    # Single Discord message → singular
    result = _synth(discord_msgs=["user1: hey"])
    _check("synth-discord-singular", "1 Discord message" in result and "messages" not in result)

    # Health issues → "System note"
    result = _synth(health_issues=["voice-agent: port unreachable"])
    _check("synth-health-present", "System note" in result)
    _check("synth-health-detail", "voice-agent" in result)

    # Insight included when clean (no JSON braces, not too many colons)
    result = _synth(insight="You tend to schedule meetings in the morning. Adjust energy accordingly.")
    _check("synth-insight-present", "Insight:" in result)
    _check("synth-insight-first-sentence", "You tend to schedule meetings" in result)

    # Insight skipped when it looks like raw data (has {})
    result = _synth(insight='{"count": 5, "avg": 3.2}. Analysis here.')
    _check("synth-insight-raw-json-skip", "Insight:" not in result)

    # Insight skipped when first sentence has too many colons (>2)
    result = _synth(insight="a: b: c: d: e: something. Rest of insight.")
    _check("synth-insight-many-colons-skip", "Insight:" not in result)

    # Insight too short (<= 20 chars) → skipped
    result = _synth(insight="Short insight.")
    _check("synth-insight-too-short-skip", "Insight:" not in result)

    # Clean day → "Everything looks clean"
    result = _synth()
    _check("synth-clean-day", "Everything looks clean" in result)

    # Not clean day when any non-empty section exists
    result = _synth(health_issues=["issue"])
    _check("synth-not-clean-with-issues", "Everything looks clean" not in result)


_test_synthesize()


# ---------------------------------------------------------------------------
# (b) get_pending_questions
# ---------------------------------------------------------------------------

def _test_get_pending_questions():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp)
        _mod.WORKSPACE = ws

        # No file → empty
        _check("gpq-no-file", _mod.get_pending_questions() == [])

        pq = ws / "pending-questions.md"

        # Empty content → empty
        pq.write_text("")
        _check("gpq-empty", _mod.get_pending_questions() == [])

        # Single section → one question
        pq.write_text("## Should I deploy now?\n\nSome context.\n")
        result = _mod.get_pending_questions()
        _check("gpq-single", len(result) == 1, f"got {result}")
        _check("gpq-single-title", "Should I deploy now?" in result[0])

        # Multiple sections
        pq.write_text("## Question A\n\n## Question B\n\n## Question C\n")
        result = _mod.get_pending_questions()
        _check("gpq-multi-count", len(result) == 3, f"got {result}")

        # # Resolved divider — sections below are excluded
        pq.write_text(
            "## Active Q\n\nSome context.\n\n"
            "# Resolved\n\n"
            "## Old resolved Q\n\nDone.\n"
        )
        result = _mod.get_pending_questions()
        _check("gpq-resolved-divider-active", len(result) == 1, f"got {result}")
        _check("gpq-resolved-divider-excludes", "Old resolved Q" not in str(result))

        # Date prefix stripped: "[2026-05-27] Question here"
        pq.write_text("## [2026-05-27] What should we ship?\n\n")
        result = _mod.get_pending_questions()
        _check("gpq-date-prefix-stripped", result and "What should we ship?" in result[0],
               f"got {result}")
        _check("gpq-date-prefix-removed", result and "[2026-05-27]" not in result[0])

        # Title truncated to 60 chars
        long_title = "A" * 80
        pq.write_text(f"## {long_title}\n\n")
        result = _mod.get_pending_questions()
        _check("gpq-title-max-60", result and len(result[0]) <= 60,
               f"got len={len(result[0]) if result else 'empty'}")


_test_get_pending_questions()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"morning-briefing: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
