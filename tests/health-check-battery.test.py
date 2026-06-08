#!/usr/bin/env python3
"""
Tests for check_battery() added to health-check.py (closes #1486).

Covers:
  a) Non-macOS → None (caller skips the check)
  b) AC power → ok with level detail
  c) Battery power above threshold → warn (on battery, consider plugging in)
  d) Battery at/below threshold → warn with threshold message
  e) pmset failure → warn with error detail

Run: python3 tests/health-check-battery.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

_MISSING = object()

# ---------------------------------------------------------------------------
# Fake pmset output strings
# ---------------------------------------------------------------------------

_PMSET_AC = """\
Now drawing from 'AC Power'
 -InternalBattery-0 (id=123456)\t82%; AC attached; not charging present: yes"""

_PMSET_BATT_HIGH = """\
Now drawing from 'Battery Power'
 -InternalBattery-0 (id=123456)\t75%; discharging; 3:22 remaining present: yes"""

_PMSET_BATT_LOW = """\
Now drawing from 'Battery Power'
 -InternalBattery-0 (id=123456)\t15%; discharging; 0:45 remaining present: yes"""

_PMSET_BATT_AT_THRESHOLD = """\
Now drawing from 'Battery Power'
 -InternalBattery-0 (id=123456)\t20%; discharging; 1:00 remaining present: yes"""

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def case_a_non_macos_returns_none():
    """check_battery() must return None on non-macOS (no pmset available)."""
    fails = []
    with mock.patch("sys.platform", "linux"):
        r = hc.check_battery()
    if r is not None:
        fails.append(f"a) non-macOS should return None, got {r}")
    return fails


def case_b_ac_power_ok():
    """On AC power → status ok with percentage detail."""
    fails = []
    with mock.patch("sys.platform", "darwin"):
        with mock.patch.object(hc.subprocess, "check_output", return_value=_PMSET_AC):
            r = hc.check_battery()
    if r is None:
        fails.append("b) AC power check returned None instead of a result dict")
        return fails
    if r["status"] != "ok":
        fails.append(f"b) AC power should be ok, got {r['status']} ({r['detail']})")
    if "AC" not in r["detail"] and "ac" not in r["detail"].lower():
        fails.append(f"b) detail should mention AC power, got: {r['detail']}")
    return fails


def case_c_battery_above_threshold_warn():
    """On battery above threshold → warn (operator should know)."""
    fails = []
    with mock.patch("sys.platform", "darwin"):
        with mock.patch.object(hc.subprocess, "check_output", return_value=_PMSET_BATT_HIGH):
            r = hc.check_battery()
    if r is None:
        fails.append("c) battery above threshold returned None")
        return fails
    if r["status"] != "warn":
        fails.append(f"c) on-battery above threshold should be warn, got {r['status']}")
    if "75%" not in r["detail"]:
        fails.append(f"c) detail should include battery level, got: {r['detail']}")
    return fails


def case_d_battery_at_or_below_threshold_warn():
    """At or below SUTANDO_BATTERY_WARN_PCT (default 20) → warn with threshold message."""
    fails = []
    with mock.patch("sys.platform", "darwin"):
        with mock.patch.object(hc.subprocess, "check_output", return_value=_PMSET_BATT_LOW):
            r = hc.check_battery()
    if r is None:
        fails.append("d) battery below threshold returned None")
        return fails
    if r["status"] != "warn":
        fails.append(f"d) battery at/below threshold should be warn, got {r['status']}")
    if "15%" not in r["detail"]:
        fails.append(f"d) detail should include battery level, got: {r['detail']}")
    # The threshold-specific message uses "at or below" or the env var name
    if "threshold" not in r["detail"] and "SUTANDO_BATTERY_WARN_PCT" not in r["detail"]:
        fails.append(f"d) detail should mention threshold, got: {r['detail']}")
    return fails


def case_d2_battery_exactly_at_threshold():
    """Exactly at threshold (20%) → same threshold warn path."""
    fails = []
    with mock.patch("sys.platform", "darwin"):
        with mock.patch.object(hc.subprocess, "check_output", return_value=_PMSET_BATT_AT_THRESHOLD):
            r = hc.check_battery()
    if r is None:
        fails.append("d2) battery at threshold returned None")
        return fails
    if r["status"] != "warn":
        fails.append(f"d2) battery exactly at threshold (20%) should be warn, got {r['status']}")
    return fails


def case_e_pmset_failure_warn():
    """pmset failing (e.g. not found) → warn with error detail."""
    fails = []
    with mock.patch("sys.platform", "darwin"):
        with mock.patch.object(
            hc.subprocess, "check_output",
            side_effect=Exception("pmset: command not found"),
        ):
            r = hc.check_battery()
    if r is None:
        fails.append("e) pmset failure returned None instead of a warn dict")
        return fails
    if r["status"] != "warn":
        fails.append(f"e) pmset failure should be warn, got {r['status']}")
    if "pmset" not in r["detail"].lower():
        fails.append(f"e) detail should mention pmset failure, got: {r['detail']}")
    return fails


def case_f_custom_threshold():
    """SUTANDO_BATTERY_WARN_PCT overrides default threshold of 20."""
    fails = []
    # 75% battery, custom threshold of 80 → should trigger the threshold warn
    saved_env = os.environ.get("SUTANDO_BATTERY_WARN_PCT", _MISSING)
    os.environ["SUTANDO_BATTERY_WARN_PCT"] = "80"
    try:
        with mock.patch("sys.platform", "darwin"):
            with mock.patch.object(hc.subprocess, "check_output", return_value=_PMSET_BATT_HIGH):
                r = hc.check_battery()
    finally:
        if saved_env is _MISSING:
            os.environ.pop("SUTANDO_BATTERY_WARN_PCT", None)
        else:
            os.environ["SUTANDO_BATTERY_WARN_PCT"] = saved_env
    if r is None:
        fails.append("f) custom threshold test returned None")
        return fails
    # 75% is at or below custom threshold of 80, so should get threshold message
    if r["status"] != "warn":
        fails.append(f"f) 75% with custom threshold=80 should be warn, got {r['status']}")
    if "SUTANDO_BATTERY_WARN_PCT" not in r["detail"] and "threshold" not in r["detail"]:
        fails.append(f"f) detail should mention threshold, got: {r['detail']}")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_non_macos_returns_none),
        ("b", case_b_ac_power_ok),
        ("c", case_c_battery_above_threshold_warn),
        ("d", case_d_battery_at_or_below_threshold_warn),
        ("d2", case_d2_battery_exactly_at_threshold),
        ("e", case_e_pmset_failure_warn),
        ("f", case_f_custom_threshold),
    ]
    all_failures = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as e:
            fails = [f"{label}) raised {type(e).__name__}: {e}"]
        if fails:
            all_failures.extend(fails)
            print(f"  FAIL case {label}")
            for f in fails:
                print(f"    {f}")
        else:
            print(f"  PASS case {label}")

    total = len(cases)
    failed = len(all_failures)
    print(f"\nResults: {total - failed}/{total} passed")
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
