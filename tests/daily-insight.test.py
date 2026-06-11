#!/usr/bin/env python3
"""Tests for src/daily-insight.py — pure call-analysis helpers.

Covers:
  a) analyze_call_timing()   — hour/day Counter from call list
  b) analyze_call_duration() — avg/max/long-call-pct stats; None on empty
  c) analyze_topics()        — keyword extraction, stopword filtering, top-N

Run: python3 tests/daily-insight.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "daily_insight",
    REPO / "src" / "daily-insight.py",
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
# (a) analyze_call_timing
# ---------------------------------------------------------------------------

def _test_analyze_call_timing():
    act = _mod.analyze_call_timing

    # Empty list → empty counters
    hours, days = act([])
    _check("timing-empty-hours", len(hours) == 0)
    _check("timing-empty-days",  len(days) == 0)

    calls = [
        {"start_time": "2026-01-05T10:30:00Z"},  # Mon 10h
        {"start_time": "2026-01-05T10:45:00Z"},  # Mon 10h
        {"start_time": "2026-01-06T14:00:00Z"},  # Tue 14h
        {"start_time": "2026-01-07T10:00:00Z"},  # Wed 10h
    ]
    hours, days = act(calls)

    _check("timing-peak-hour-10",   hours.most_common(1)[0][0] == 10,
           f"peak hour: {hours.most_common(1)}")
    _check("timing-hour-10-count-3", hours[10] == 3,
           f"hour 10: {hours[10]}")
    _check("timing-hour-14-count-1", hours[14] == 1)

    # Day names present (actual name depends on date in 2026)
    _check("timing-days-not-empty", len(days) > 0)

    # Call with no timestamp → skipped gracefully
    no_ts = [{"start_time": None}, {"start_time": ""}, {"caller": "unknown"}]
    h2, d2 = act(no_ts)
    _check("timing-no-ts-empty-hours", len(h2) == 0)

    # Malformed timestamp → skipped gracefully
    bad_ts = [{"start_time": "not-a-date"}]
    h3, _ = act(bad_ts)
    _check("timing-bad-ts-skip", len(h3) == 0)

    # "timestamp" fallback key honored (older call format)
    legacy = [{"timestamp": "2026-01-01T09:00:00Z"}]
    h4, _ = act(legacy)
    _check("timing-legacy-ts-key", h4[9] == 1)


_test_analyze_call_timing()


# ---------------------------------------------------------------------------
# (b) analyze_call_duration
# ---------------------------------------------------------------------------

def _test_analyze_call_duration():
    acd = _mod.analyze_call_duration

    # Empty list → None
    _check("dur-empty-none", acd([]) is None)

    # All calls missing duration → None
    _check("dur-no-field-none", acd([{"caller": "x"}, {"caller": "y"}]) is None)

    # Zero and negative durations excluded
    _check("dur-zero-neg-none",
           acd([{"duration_seconds": 0}, {"duration_seconds": -30}]) is None)

    # Single call
    result = acd([{"duration_seconds": 120}])
    _check("dur-single-count",       result is not None and result["count"] == 1)
    _check("dur-single-avg",         result is not None and result["avg_minutes"] == 2.0,
           f"got {result}")
    _check("dur-single-longest",     result is not None and result["longest_minutes"] == 2.0)
    _check("dur-single-long-pct",    result is not None and result["long_call_pct"] == 0.0)

    # Multiple calls — long-call threshold is avg * 2
    # avg = (60 + 120 + 600) / 3 = 260s; threshold = 520s → 600 is long
    calls = [
        {"duration_seconds": 60},
        {"duration_seconds": 120},
        {"duration_seconds": 600},
    ]
    r = acd(calls)
    _check("dur-multi-count",      r is not None and r["count"] == 3)
    _check("dur-multi-avg",        r is not None and r["avg_minutes"] == round((60+120+600)/(3*60), 1))
    _check("dur-multi-longest",    r is not None and r["longest_minutes"] == round(600/60, 1))
    _check("dur-multi-long-pct",   r is not None and r["long_call_pct"] == round(1/3*100, 1),
           f"got {r}")

    # "duration" key fallback honored
    legacy_dur = [{"duration": 90}]
    r2 = acd(legacy_dur)
    _check("dur-legacy-key", r2 is not None and r2["count"] == 1)

    # String duration ignored (not int/float)
    str_dur = [{"duration_seconds": "120"}]
    _check("dur-string-excluded", acd(str_dur) is None)


_test_analyze_call_duration()


# ---------------------------------------------------------------------------
# (c) analyze_topics
# ---------------------------------------------------------------------------

def _test_analyze_topics():
    at = _mod.analyze_topics

    # Empty → empty list
    _check("topics-empty", at([]) == [])

    # Calls with no summary/topic fields → empty
    no_summary = [{"caller": "x"}, {"duration": 120}]
    _check("topics-no-field", at(no_summary) == [])

    # Short words (≤ 4 chars) excluded
    short_words = [{"summary": "call from john about work item todo list done"}]
    topics = at(short_words)
    topic_words = [t[0] for t in topics]
    _check("topics-short-excluded", "call" not in topic_words and "from" not in topic_words,
           f"short words found: {topic_words}")
    _check("topics-long-included", "about" not in topic_words,
           "stopword 'about' should be filtered")
    # "work" (4 chars) excluded, "items" (5 chars) if present should be included
    _check("topics-work-excluded", "work" not in topic_words,
           f"'work' (4 chars) should be excluded — got {topic_words}")

    # Top-10 limit honored
    words = " ".join([f"keyword{i}" for i in range(20)])
    many = [{"summary": words}]
    _check("topics-top-10-limit", len(at(many)) <= 10)

    # Frequency-based ordering
    freq_calls = [
        {"summary": "billing question about account charges today"},
        {"summary": "billing issue with account statement"},
        {"summary": "technical support request"},
    ]
    ft = at(freq_calls)
    topic_words_ft = [t[0] for t in ft]
    # "billing" (7 chars, appears 2x) and "account" (7 chars, appears 2x)
    # both should rank above "technical" (9 chars, 1x)
    _check("topics-billing-present", "billing" in topic_words_ft,
           f"expected 'billing' in {topic_words_ft}")
    _check("topics-billing-count", any(w == "billing" and c == 2 for w, c in ft),
           f"expected billing:2 in {ft}")

    # Stopwords filtered (the hardcoded set)
    stopwords = [{"summary": "about their there would could should which where these those"}]
    st = at(stopwords)
    stop_words_present = [t[0] for t in st]
    _check("topics-stopwords-filtered", all(w not in stop_words_present
                                            for w in ["about", "their", "there", "would"]),
           f"stopwords found in {stop_words_present}")

    # Punctuation stripped
    punct_calls = [{"summary": "billing, (charges), billing!"}]
    pt = at(punct_calls)
    pt_words = [t[0] for t in pt]
    _check("topics-punctuation-stripped", "billing," not in pt_words and "billing" in pt_words,
           f"got {pt_words}")

    # "topic" field fallback honored
    topic_field = [{"topic": "scheduling meeting tomorrow morning"}]
    tf = at(topic_field)
    tf_words = [t[0] for t in tf]
    _check("topics-topic-field", "scheduling" in tf_words or "meeting" in tf_words,
           f"got {tf_words}")


_test_analyze_topics()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"daily-insight: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
