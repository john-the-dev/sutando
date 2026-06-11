#!/usr/bin/env python3
"""Tests for skills/voice-agent-test-harness — pure helper functions.

Covers report.py:
  a) _percentile()  — linear interpolation, edge cases
  b) summarize()    — pass rate, latency/clarity aggregates, no-response count
  c) _fmt_ms()      — sub-1000ms vs ≥1000ms formatting, None
  d) render()       — output sections, regression list vs none, no-response flag

Covers baseline.py:
  e) diff()         — accuracy regressions, latency p95, clarity drop, no-baseline
  f) is_green()     — hard fail blocks, no-response blocks, soft wrong OK

Run: python3 tests/voice-agent-test-harness.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "skills" / "voice-agent-test-harness" / "scripts"

def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, HARNESS / filename)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

_report   = _load("report",   "report.py")
_baseline = _load("baseline", "baseline.py")

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
# (a) _percentile
# ---------------------------------------------------------------------------

def _test_percentile():
    p = _report._percentile

    # Empty → None
    _check("pct-empty-none", p([], 0.5) is None)

    # Single value: any percentile returns that value
    _check("pct-single-p0",   p([42.0], 0.0) == 42.0)
    _check("pct-single-p50",  p([42.0], 0.5) == 42.0)
    _check("pct-single-p100", p([42.0], 1.0) == 42.0)

    # Two values
    _check("pct-two-p0",   p([10.0, 20.0], 0.0) == 10.0)
    _check("pct-two-p100", p([10.0, 20.0], 1.0) == 20.0)
    _check("pct-two-p50",  p([10.0, 20.0], 0.5) == 15.0)

    # Sorted internally — input order shouldn't matter
    _check("pct-unsorted-p50", p([20.0, 10.0], 0.5) == 15.0)

    # p95 of [100, 200, 300, 400, 500] → 480.0 (linear interp)
    vals = [100.0, 200.0, 300.0, 400.0, 500.0]
    _check("pct-p95-interp", p(vals, 0.95) == 480.0,
           f"got {p(vals, 0.95)}")

    # Returns rounded to 1 decimal
    _check("pct-rounded", p([1.0, 2.0, 4.0], 0.5) == 2.0)


_test_percentile()


# ---------------------------------------------------------------------------
# (b) summarize
# ---------------------------------------------------------------------------

def _make_tests() -> list[dict]:
    return [
        {"id": "wake-word",    "accuracy": "pass",    "latency_ms": 200.0, "clarity": 4.5, "soft": False},
        {"id": "tool-call",    "accuracy": "pass",    "latency_ms": 800.0, "clarity": 4.0, "soft": False},
        {"id": "soft-wrong",   "accuracy": "fail",    "latency_ms": 300.0, "clarity": 3.5, "soft": True},
        {"id": "no-resp",      "accuracy": "fail",    "latency_ms": None,  "clarity": None, "soft": False,
         "no_response": True},
        {"id": "partial-hard", "accuracy": "partial", "latency_ms": 500.0, "clarity": 4.0, "soft": False},
    ]


def _test_summarize():
    s = _report.summarize

    # Empty list
    empty = s([])
    _check("sum-empty-total-0",    empty["total"] == 0)
    _check("sum-empty-pass-0",     empty["pass"] == 0)
    _check("sum-empty-p50-none",   empty["p50_latency_ms"] is None)
    _check("sum-empty-clarity-none", empty["clarity_mean"] is None)

    tests = _make_tests()
    r = s(tests)

    # hard tests: wake-word(pass), tool-call(pass), no-resp(fail), partial-hard(partial) = 4
    # soft-wrong is soft → excluded from hard_total
    _check("sum-hard-total",  r["hard_total"] == 4, f"got {r['hard_total']}")
    _check("sum-pass",        r["pass"] == 2,        f"got {r['pass']}")
    _check("sum-total",       r["total"] == 5)
    _check("sum-no-response", r["no_response"] == 1)

    # latencies: 200, 800, 300, 500 (None excluded) = 4 values
    _check("sum-p50-set", r["p50_latency_ms"] is not None)
    _check("sum-p95-set", r["p95_latency_ms"] is not None)

    # clarity: 4.5, 4.0, 3.5, 4.0 (None excluded) = mean 4.0
    _check("sum-clarity-mean", r["clarity_mean"] == 4.0,
           f"got {r['clarity_mean']}")


_test_summarize()


# ---------------------------------------------------------------------------
# (c) _fmt_ms
# ---------------------------------------------------------------------------

def _test_fmt_ms():
    f = _report._fmt_ms

    _check("fmt-none",      f(None) == "n/a")
    _check("fmt-sub-1000",  f(500.0) == "500ms")
    _check("fmt-exact-999", f(999.0) == "999ms")
    _check("fmt-1000",      f(1000.0) == "1.0s")
    _check("fmt-1500",      f(1500.0) == "1.5s")
    _check("fmt-zero",      f(0.0) == "0ms")


_test_fmt_ms()


# ---------------------------------------------------------------------------
# (d) render
# ---------------------------------------------------------------------------

def _test_render():
    r = _report.render

    summary = {
        "pass": 2, "hard_total": 4, "total": 5,
        "p50_latency_ms": 350.0, "p95_latency_ms": 750.0,
        "clarity_mean": 4.0, "no_response": 0,
    }
    run = {"summary": summary}

    # No regressions
    text_clean = r(run, [], "2026-06-10")
    _check("render-date",          "2026-06-10" in text_clean)
    _check("render-pass-ratio",    "2/4" in text_clean)
    _check("render-no-regressions", "No regressions" in text_clean)
    _check("render-full-link",     "results/voice-test/2026-06-10.json" in text_clean)
    _check("render-p50",           "350ms" in text_clean)

    # With regressions
    regr = ["wake-word: pass→fail", "p95 latency 500→900ms"]
    text_regr = r(run, regr, "2026-06-10")
    _check("render-regression-section", "Regressions" in text_regr)
    _check("render-regression-count",   "2)" in text_regr)
    _check("render-regression-item",    "wake-word" in text_regr)

    # no-baseline advisory (starts with "(") → NOT counted as real regression
    advisory = ["(no baseline yet — this run will seed it)"]
    text_advisory = r(run, advisory, "2026-06-10")
    _check("render-advisory-not-regression", "No regressions" in text_advisory)

    # No-response flag in summary
    summary_nr = {**summary, "no_response": 3}
    text_nr = r({"summary": summary_nr}, [], "2026-06-10")
    _check("render-no-response-flag", "no-response" in text_nr or "3" in text_nr)


_test_render()


# ---------------------------------------------------------------------------
# (e) diff (baseline.py)
# ---------------------------------------------------------------------------

def _test_diff():
    d = _baseline.diff

    # No baseline → advisory message (not a real regression)
    result = d({"tests": [], "summary": {}}, None)
    _check("diff-no-baseline-advisory", len(result) == 1)
    _check("diff-advisory-starts-paren", result[0].startswith("("))

    # Accuracy regression: pass → fail
    current = {
        "tests": [{"id": "t1", "accuracy": "fail", "rationale": "no tool call"}],
        "summary": {},
    }
    baseline_ = {
        "tests": [{"id": "t1", "accuracy": "pass"}],
        "summary": {},
    }
    r = d(current, baseline_)
    _check("diff-accuracy-regression", len(r) == 1, f"got {r}")
    _check("diff-regression-id",       "t1" in r[0])
    _check("diff-regression-direction","pass→fail" in r[0])
    _check("diff-rationale-included",  "no tool call" in r[0])

    # partial → fail is a regression; pass → partial is a regression
    for cur_acc, base_acc in [("fail", "partial"), ("partial", "pass")]:
        r2 = d(
            {"tests": [{"id": "t2", "accuracy": cur_acc}], "summary": {}},
            {"tests": [{"id": "t2", "accuracy": base_acc}], "summary": {}},
        )
        _check(f"diff-{base_acc}-to-{cur_acc}", len(r2) >= 1, f"got {r2}")

    # pass → pass is NOT a regression
    r3 = d(
        {"tests": [{"id": "t3", "accuracy": "pass"}], "summary": {}},
        {"tests": [{"id": "t3", "accuracy": "pass"}], "summary": {}},
    )
    _check("diff-pass-to-pass-ok", r3 == [])

    # New test in current (not in baseline) → skipped, not a regression
    r4 = d(
        {"tests": [{"id": "new-t", "accuracy": "fail"}], "summary": {}},
        {"tests": [], "summary": {}},
    )
    _check("diff-new-test-not-regression", r4 == [])

    # Latency p95 regression: both >25% rel AND >300ms abs required
    r5 = d(
        {"tests": [], "summary": {"p95_latency_ms": 900.0, "clarity_mean": 4.0}},
        {"tests": [], "summary": {"p95_latency_ms": 500.0, "clarity_mean": 4.0}},
    )
    _check("diff-latency-regression", any("p95 latency" in x for x in r5),
           f"got {r5}")  # 900-500=400>300 AND 400/500=0.8>0.25 → flagged

    # Latency: big absolute but NOT >25% rel → NOT flagged
    r6 = d(
        {"tests": [], "summary": {"p95_latency_ms": 5000.0, "clarity_mean": 4.0}},
        {"tests": [], "summary": {"p95_latency_ms": 4300.0, "clarity_mean": 4.0}},
    )
    # 5000-4300=700>300abs but 700/4300=0.16<0.25rel → NOT flagged
    _check("diff-latency-abs-only-no-flag", not any("p95" in x for x in r6),
           f"got {r6}")

    # Clarity drop > 0.5 → flagged
    r7 = d(
        {"tests": [], "summary": {"clarity_mean": 3.0}},
        {"tests": [], "summary": {"clarity_mean": 4.0}},
    )
    _check("diff-clarity-regression", any("clarity" in x for x in r7),
           f"got {r7}")

    # Clarity drop ≤ 0.5 → NOT flagged
    r8 = d(
        {"tests": [], "summary": {"clarity_mean": 3.6}},
        {"tests": [], "summary": {"clarity_mean": 4.0}},
    )
    _check("diff-clarity-ok", not any("clarity" in x for x in r8))


_test_diff()


# ---------------------------------------------------------------------------
# (f) is_green (baseline.py)
# ---------------------------------------------------------------------------

def _test_is_green():
    ig = _baseline.is_green

    # Empty run → green
    _check("green-empty",         ig({"tests": []}))

    # All hard pass → green
    all_pass = {"tests": [
        {"id": "t1", "accuracy": "pass", "soft": False},
        {"id": "t2", "accuracy": "pass", "soft": False},
    ]}
    _check("green-all-pass",      ig(all_pass))

    # Hard fail → NOT green
    hard_fail = {"tests": [{"id": "t1", "accuracy": "fail", "soft": False}]}
    _check("green-hard-fail",     not ig(hard_fail))

    # Soft fail (wrong answer) → green (soft tests don't block baseline)
    soft_fail = {"tests": [{"id": "t1", "accuracy": "fail", "soft": True}]}
    _check("green-soft-fail-ok",  ig(soft_fail))

    # no_response → NOT green (even on soft tests)
    no_resp = {"tests": [{"id": "t1", "accuracy": "pass", "no_response": True}]}
    _check("green-no-response",   not ig(no_resp))

    # partial hard → green (is_green only blocks on "fail", not "partial")
    partial = {"tests": [{"id": "t1", "accuracy": "partial", "soft": False}]}
    _check("green-partial-hard-ok", ig(partial))

    # partial soft → green (soft partial doesn't block)
    soft_partial = {"tests": [{"id": "t1", "accuracy": "partial", "soft": True}]}
    _check("green-soft-partial-ok", ig(soft_partial))

    # Mixed: one soft fail + one hard pass → green
    mixed_ok = {"tests": [
        {"id": "t1", "accuracy": "fail", "soft": True},
        {"id": "t2", "accuracy": "pass", "soft": False},
    ]}
    _check("green-mixed-ok",      ig(mixed_ok))

    # Mixed: one hard fail + one hard pass → NOT green
    mixed_fail = {"tests": [
        {"id": "t1", "accuracy": "pass", "soft": False},
        {"id": "t2", "accuracy": "fail", "soft": False},
    ]}
    _check("green-mixed-fail",    not ig(mixed_fail))


_test_is_green()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"voice-agent-test-harness: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
