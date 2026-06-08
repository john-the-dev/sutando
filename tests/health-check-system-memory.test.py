#!/usr/bin/env python3
"""
Tests for check_system_memory() added to health-check.py (closes #1485).

Covers:
  a) Normal memory (below threshold) → ok status
  b) High usage (≥ threshold) → warn status
  c) macOS compressor > 50% of total RAM → warn with 'severe pressure' in detail
  d) psutil missing → warn with install hint
  e) Custom threshold via SUTANDO_MEMORY_WARN_THRESHOLD_PCT env var

Run: python3 tests/health-check-system-memory.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

_MISSING = object()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_psutil(percent: float, total_gb: float = 16.0, avail_gb: float = 4.0):
    m = types.ModuleType("psutil")

    class _VM:
        pass

    vm = _VM()
    vm.total = int(total_gb * 1024 ** 3)
    vm.available = int(avail_gb * 1024 ** 3)
    vm.percent = percent
    m.virtual_memory = lambda: vm
    return m


_VM_STAT_OK = "\n".join([
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)",
    "Pages free:                         200000.",
    "Pages stored in compressor:          50000.",   # ~0.8 GB  — well below 50% of 16 GB
])

_VM_STAT_SEVERE = "\n".join([
    "Mach Virtual Memory Statistics: (page size of 16384 bytes)",
    "Pages free:                          30000.",
    "Pages stored in compressor:         600000.",   # ~9.2 GB — >50% of 16 GB
])


def _set_psutil(module_or_none):
    """Install a psutil mock (or None to trigger ImportError). Returns (old, was_missing)."""
    old = sys.modules.get("psutil", _MISSING)
    sys.modules["psutil"] = module_or_none
    return old


def _restore_psutil(old):
    if old is _MISSING:
        sys.modules.pop("psutil", None)
    else:
        sys.modules["psutil"] = old


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def case_a_normal_ok():
    """Below default threshold (80%) → ok."""
    fails = []
    old = _set_psutil(_make_psutil(percent=50.0, total_gb=16.0, avail_gb=8.0))
    try:
        with mock.patch.object(hc.subprocess, "check_output", return_value=_VM_STAT_OK):
            r = hc.check_system_memory()
    finally:
        _restore_psutil(old)
    if r["status"] != "ok":
        fails.append(f"a) 50% used (threshold=80) should be ok, got {r['status']} ({r['detail']})")
    if "50%" not in r["detail"]:
        fails.append(f"a) detail should include used%, got: {r['detail']}")
    return fails


def case_b_high_usage_warn():
    """At or above default threshold (80%) → warn."""
    fails = []
    old = _set_psutil(_make_psutil(percent=85.0, total_gb=16.0, avail_gb=2.0))
    try:
        with mock.patch.object(hc.subprocess, "check_output", return_value=_VM_STAT_OK):
            r = hc.check_system_memory()
    finally:
        _restore_psutil(old)
    if r["status"] != "warn":
        fails.append(f"b) 85% used (threshold=80) should be warn, got {r['status']}")
    return fails


def case_c_compressor_severe():
    """Compressor > 50% of total RAM → warn with 'severe pressure' detail."""
    fails = []
    # Percent below threshold so the only trigger is the compressor size.
    old = _set_psutil(_make_psutil(percent=70.0, total_gb=16.0, avail_gb=5.0))
    try:
        with mock.patch("sys.platform", "darwin"):
            with mock.patch.object(hc.subprocess, "check_output", return_value=_VM_STAT_SEVERE):
                r = hc.check_system_memory()
    finally:
        _restore_psutil(old)
    if r["status"] != "warn":
        fails.append(f"c) severe compressor should be warn, got {r['status']}")
    if "severe pressure" not in r["detail"]:
        fails.append(f"c) detail should mention 'severe pressure', got: {r['detail']}")
    if "compressor" not in r["detail"]:
        fails.append(f"c) detail should mention compressor size, got: {r['detail']}")
    return fails


def case_d_psutil_missing():
    """psutil not importable → warn with install hint."""
    fails = []
    # Setting sys.modules['psutil'] = None causes `import psutil` to raise ImportError.
    old = _set_psutil(None)
    try:
        r = hc.check_system_memory()
    finally:
        _restore_psutil(old)
    if r["status"] != "warn":
        fails.append(f"d) psutil missing should be warn, got {r['status']}")
    if "psutil" not in r["detail"].lower():
        fails.append(f"d) detail should mention psutil, got: {r['detail']}")
    return fails


def case_e_custom_threshold():
    """SUTANDO_MEMORY_WARN_THRESHOLD_PCT overrides the default 80."""
    fails = []
    old = _set_psutil(_make_psutil(percent=70.0, total_gb=16.0, avail_gb=5.0))
    saved_env = os.environ.get("SUTANDO_MEMORY_WARN_THRESHOLD_PCT", _MISSING)
    os.environ["SUTANDO_MEMORY_WARN_THRESHOLD_PCT"] = "60"
    try:
        with mock.patch.object(hc.subprocess, "check_output", return_value=_VM_STAT_OK):
            r = hc.check_system_memory()
    finally:
        _restore_psutil(old)
        if saved_env is _MISSING:
            os.environ.pop("SUTANDO_MEMORY_WARN_THRESHOLD_PCT", None)
        else:
            os.environ["SUTANDO_MEMORY_WARN_THRESHOLD_PCT"] = saved_env
    if r["status"] != "warn":
        fails.append(f"e) 70% used at custom threshold=60 should be warn, got {r['status']}")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_normal_ok),
        ("b", case_b_high_usage_warn),
        ("c", case_c_compressor_severe),
        ("d", case_d_psutil_missing),
        ("e", case_e_custom_threshold),
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
