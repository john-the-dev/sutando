#!/usr/bin/env python3
"""Tests for src/call-stats.py — pure helper functions.

Covers:
  a) mask_phone() — US 11-digit, short/unknown/None, non-US long number
  b) parse_ts()   — valid ISO-Z, ISO offset, missing-Z, invalid, None, empty
  c) filter_by_window() — days=None pass-through, window includes/excludes
  d) compute_stats() — totals, duration aggregates, counters, empty list
  e) format_text() — section presence, empty counters suppressed

Run: python3 tests/call-stats.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "call_stats",
    REPO / "src" / "call-stats.py",
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


# ---------------------------------------------------------------------------
# (a) mask_phone
# ---------------------------------------------------------------------------

def _test_mask_phone():
    mp = _mod.mask_phone

    # Standard US +1 number (11 digits)
    _check("us-11digit",      mp("+14256716122") == "+1-425-XXX-XXXX",
           f"got {mp('+14256716122')!r}")

    # "unknown" sentinel
    _check("unknown-str",     mp("unknown") == "unknown")

    # None → "unknown"
    _check("none-unknown",    mp(None) == "unknown")

    # Empty string → "unknown"
    _check("empty-unknown",   mp("") == "unknown")

    # Short number (< 10 digits) → returned unchanged
    _check("short-unchanged", mp("123") == "123")

    # Non-US long number (14 digits) uses else branch
    result_long = mp("+44201234567890")
    _check("long-startswith-plus", result_long.startswith("+"),
           f"got {result_long!r}")
    _check("long-xxx-xxxx",        "XXX-XXXX" in result_long,
           f"got {result_long!r}")

    # Digits-only 10-digit number
    result_10 = mp("4256716122")
    _check("ten-digit-xxx-xxxx", "XXX-XXXX" in result_10,
           f"got {result_10!r}")

    # Formatting chars stripped before length check
    result_formatted = mp("+1 (425) 671-6122")
    _check("formatted-us", result_formatted == "+1-425-XXX-XXXX",
           f"got {result_formatted!r}")


_test_mask_phone()


# ---------------------------------------------------------------------------
# (b) parse_ts
# ---------------------------------------------------------------------------

def _test_parse_ts():
    pt = _mod.parse_ts

    # Valid ISO-Z
    ts = pt("2026-01-15T10:30:00Z")
    _check("iso-z-returns-dt",     isinstance(ts, datetime))
    _check("iso-z-utc",            ts is not None and ts.tzinfo is not None)
    _check("iso-z-hour",           ts is not None and ts.hour == 10)

    # ISO with offset (no Z)
    ts2 = pt("2026-01-15T10:30:00+00:00")
    _check("iso-offset-ok",        isinstance(ts2, datetime))

    # None → None
    _check("none-returns-none",    pt(None) is None)

    # Empty string → None
    _check("empty-returns-none",   pt("") is None)

    # Invalid string → None
    _check("invalid-returns-none", pt("not-a-date") is None)

    # Partial date (no time) — fromisoformat accepts "2026-01-15" but result should be a date-like dt
    ts3 = pt("2026-01-15")
    _check("date-only-ok",         ts3 is not None)


_test_parse_ts()


# ---------------------------------------------------------------------------
# (c) filter_by_window
# ---------------------------------------------------------------------------

def _test_filter_by_window():
    fbw = _mod.filter_by_window

    now = datetime.now(timezone.utc)

    def _call(days_ago: float, **kw) -> dict:
        ts = now - timedelta(days=days_ago)
        return {"start_time": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), **kw}

    recent = _call(5)   # 5 days ago
    old    = _call(60)  # 60 days ago

    # days=None → pass-through (all calls returned)
    all_calls = [recent, old]
    _check("none-passthrough", fbw(all_calls, None) == all_calls)

    # days=30 → old excluded, recent included
    filtered = fbw([recent, old], 30)
    _check("window-includes-recent", recent in filtered,
           f"recent not in {filtered}")
    _check("window-excludes-old",    old not in filtered,
           f"old not excluded from {filtered}")

    # Empty list → empty list
    _check("empty-list", fbw([], 30) == [])

    # Call with no timestamp field → excluded (parse_ts returns None, ts < cutoff is False)
    no_ts = {"caller": "unknown"}
    result = fbw([no_ts], 30)
    _check("no-ts-excluded", no_ts not in result)


_test_filter_by_window()


# ---------------------------------------------------------------------------
# (d) compute_stats
# ---------------------------------------------------------------------------

def _test_compute_stats():
    cs = _mod.compute_stats

    # Empty list → zeros
    empty = cs([])
    _check("empty-total-0",     empty["total"] == 0)
    _check("empty-avg-0",       empty["avg_duration_seconds"] == 0)
    _check("empty-peak-none",   empty["peak_hour"] is None)
    _check("empty-busiest-none",empty["busiest_day"] is None)

    calls = [
        {
            "duration_seconds": 120,
            "start_time": "2026-01-05T10:30:00Z",
            "caller": "+14255551234",
            "purpose": "inquiry",
            "is_meeting": False,
            "is_owner": True,
        },
        {
            "duration_seconds": 300,
            "start_time": "2026-01-06T14:00:00Z",
            "caller": "+14255559999",
            "purpose": "support",
            "is_meeting": True,
            "is_owner": False,
        },
        {
            "duration_seconds": 60,
            "start_time": "2026-01-07T10:15:00Z",
            "caller": "+14255551234",
            "purpose": "inquiry",
            "is_meeting": False,
            "is_owner": False,
        },
    ]

    stats = cs(calls)
    _check("total-3",             stats["total"] == 3)
    _check("with-duration-3",     stats["with_duration"] == 3)
    _check("meetings-1",          stats["meetings"] == 1)
    _check("owner-calls-1",       stats["owner_calls"] == 1)
    _check("longest-300",         stats["longest_seconds"] == 300)
    _check("shortest-60",         stats["shortest_seconds"] == 60)
    _check("avg-160",             stats["avg_duration_seconds"] == 160.0,
           f"got {stats['avg_duration_seconds']}")
    _check("total-minutes",       stats["total_minutes"] == round((120+300+60)/60, 1))
    _check("top-purpose-inquiry", stats["top_purposes"][0][0] == "inquiry",
           f"got {stats['top_purposes']}")
    _check("top-caller-repeated", stats["top_callers"][0][1] == 2,
           f"top caller count: {stats['top_callers']}")

    # Call with no duration data (missing field)
    no_dur = [{"start_time": "2026-01-01T09:00:00Z", "purpose": "x"}]
    nd_stats = cs(no_dur)
    _check("no-duration-zero-avg", nd_stats["avg_duration_seconds"] == 0)
    _check("no-duration-with-0",   nd_stats["with_duration"] == 0)

    # Negative duration excluded from aggregates
    neg_dur = [{"duration_seconds": -5, "start_time": "2026-01-01T09:00:00Z"}]
    ng_stats = cs(neg_dur)
    _check("negative-dur-excluded", ng_stats["with_duration"] == 0)


_test_compute_stats()


# ---------------------------------------------------------------------------
# (e) format_text
# ---------------------------------------------------------------------------

def _test_format_text():
    ft = _mod.format_text

    full_stats = {
        "total": 5,
        "with_duration": 4,
        "avg_duration_seconds": 180.0,
        "longest_seconds": 300,
        "shortest_seconds": 60,
        "total_minutes": 12.0,
        "meetings": 2,
        "owner_calls": 3,
        "peak_hour": (14, 3),
        "quiet_hours": [8, 9, 21],
        "busiest_day": ("Mon", 3),
        "top_purposes": [("inquiry", 3), ("support", 2)],
        "top_callers": [("+1-425-XXX-XXXX", 3)],
    }

    text = ft(full_stats, "last 7 days")
    _check("contains-label",      "last 7 days" in text)
    _check("contains-total",      "5 calls" in text)
    _check("contains-duration",   "180.0" in text or "180" in text)
    _check("contains-meetings",   "Meetings: 2" in text)
    _check("contains-peak",       "14:00" in text)
    _check("contains-busiest",    "Mon" in text)
    _check("contains-purpose",    "inquiry" in text)
    _check("contains-caller",     "+1-425-XXX-XXXX" in text)

    # No duration data → no duration section, no crash
    no_dur_stats = {**full_stats, "with_duration": 0}
    text_no_dur = ft(no_dur_stats, "all time")
    _check("no-dur-no-crash",     "all time" in text_no_dur)
    _check("no-dur-section-note", "no duration" in text_no_dur.lower())

    # Unknown callers suppressed
    unknown_stats = {**full_stats, "top_callers": [("unknown", 2)]}
    text_unknown = ft(unknown_stats, "last 30 days")
    _check("unknown-caller-suppressed", "Top callers" not in text_unknown,
           "unknown caller section should be suppressed")

    # Unknown purposes suppressed
    unknown_purp = {**full_stats, "top_purposes": [("unknown", 5)]}
    text_purp = ft(unknown_purp, "last 30 days")
    _check("unknown-purpose-suppressed", "Purposes" not in text_purp,
           "unknown purpose section should be suppressed")


_test_format_text()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"call-stats: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
