#!/usr/bin/env python3
"""
Tests for check_stand_identity() added to health-check.py (#1543 layer 2).

personal_path() searches:
  1. $SUTANDO_MEMORY_DIR/machine-<host>/stand-identity.json
  2. <workspace>/stand-identity.json  (fallback)

If neither exists the Stand name silently falls back to "Sutando" — this check
surfaces that so the operator knows to run the v0.8 migration fix (#1540/#1542).

Covers:
  a) File not found at any reader path → warn with migration hint
  b) File found under SUTANDO_MEMORY_DIR/machine-<host>/ → ok
  c) File found under workspace fallback → ok

Run: python3 tests/health-check-stand-identity.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest.mock as mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

def case_a_file_missing_warn():
    """stand-identity.json not found at any reader path → warn with migration hint."""
    fails = []
    # personal_path() returns a path that does not exist.
    with mock.patch.object(hc, "personal_path", return_value=Path("/nonexistent/path/stand-identity.json")):
        r = hc.check_stand_identity()
    if r["status"] != "warn":
        fails.append(f"a) missing stand-identity.json should be warn, got {r['status']}")
    if "stand-identity.json" not in r["detail"]:
        fails.append(f"a) detail should mention stand-identity.json, got: {r['detail']}")
    # Migration hint must be present — the whole point of this check is actionability.
    if "migrate" not in r["detail"].lower() and "move" not in r["detail"].lower() and "#1540" not in r["detail"]:
        fails.append(f"a) detail should include migration hint, got: {r['detail']}")
    return fails


def case_b_file_found_memory_dir_ok():
    """File exists under SUTANDO_MEMORY_DIR/machine-<host>/ → ok."""
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        mem_machine = td / "machine-testhost"
        mem_machine.mkdir(parents=True)
        si_path = mem_machine / "stand-identity.json"
        si_path.write_text('{"stand_name": "My Stand"}')
        with mock.patch.object(hc, "personal_path", return_value=si_path):
            r = hc.check_stand_identity()
    if r["status"] != "ok":
        fails.append(f"b) found stand-identity.json should be ok, got {r['status']} ({r['detail']})")
    if str(si_path) not in r["detail"]:
        fails.append(f"b) detail should include the path, got: {r['detail']}")
    return fails


def case_c_file_found_workspace_fallback_ok():
    """File exists under <workspace>/ (fallback path) → ok."""
    fails = []
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        si_path = td / "stand-identity.json"
        si_path.write_text('{"stand_name": "My Stand"}')
        with mock.patch.object(hc, "personal_path", return_value=si_path):
            r = hc.check_stand_identity()
    if r["status"] != "ok":
        fails.append(f"c) workspace-fallback stand-identity.json should be ok, got {r['status']} ({r['detail']})")
    if str(si_path) not in r["detail"]:
        fails.append(f"c) detail should include the resolved path, got: {r['detail']}")
    return fails


def case_d_check_name_field():
    """Result dict must always have name='stand-identity' regardless of outcome."""
    fails = []
    # Missing case
    with mock.patch.object(hc, "personal_path", return_value=Path("/nonexistent")):
        r_miss = hc.check_stand_identity()
    if r_miss.get("name") != "stand-identity":
        fails.append(f"d) missing path: name should be 'stand-identity', got {r_miss.get('name')}")
    # Found case
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "stand-identity.json"
        p.write_text("{}")
        with mock.patch.object(hc, "personal_path", return_value=p):
            r_found = hc.check_stand_identity()
    if r_found.get("name") != "stand-identity":
        fails.append(f"d) found path: name should be 'stand-identity', got {r_found.get('name')}")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_file_missing_warn),
        ("b", case_b_file_found_memory_dir_ok),
        ("c", case_c_file_found_workspace_fallback_ok),
        ("d", case_d_check_name_field),
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
