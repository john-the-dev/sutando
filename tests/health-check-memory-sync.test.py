#!/usr/bin/env python3
"""
Tests for health-check.py's check_memory_sync() opt-out behavior.

Context (owner ask 2026-07-10): cross-machine memory sync is OPT-IN, but the
health check warned "SUTANDO_MEMORY_REPO not set — cross-machine sync disabled"
on every tick — a permanent nag on any single-machine install, and it ignored a
deliberate config opt-out (vault.enabled=false). This surfaced as "noise" after
the 0.61 migration. Fix: report the disabled / not-configured cases as
informational (ok), not warn; a configured-but-stale sync still warns.

Covers: _vault_sync_disabled() (true/false/error) and the two early-return
branches of check_memory_sync (opt-out → ok, not-configured → ok).

Run: python3 tests/health-check-memory-sync.test.py
Exit code: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

FAILURES = []


def check(name, cond, detail=""):
    print(f"{'  ok  ' if cond else '  FAIL '}{name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(f"{name}: {detail}")


def _run_mock(stdout):
    r = unittest.mock.MagicMock()
    r.stdout = stdout
    return r


def main() -> int:
    # _vault_sync_disabled(): "false" → True, "true" → False, error → False.
    with unittest.mock.patch.object(hc.subprocess, "run", return_value=_run_mock("false")):
        check("_vault_sync_disabled: 'false' → True", hc._vault_sync_disabled() is True)
    with unittest.mock.patch.object(hc.subprocess, "run", return_value=_run_mock("true")):
        check("_vault_sync_disabled: 'true' → False", hc._vault_sync_disabled() is False)
    with unittest.mock.patch.object(hc.subprocess, "run", side_effect=OSError("boom")):
        check("_vault_sync_disabled: error → False (fail-open)", hc._vault_sync_disabled() is False)

    # check_memory_sync: deliberate opt-out → ok (no nag).
    with unittest.mock.patch.object(hc, "_vault_sync_disabled", return_value=True):
        r = hc.check_memory_sync()
    check("opt-out → ok", r["status"] == "ok", f"got {r!r}")
    check("opt-out detail mentions opt-out", "opt-out" in r["detail"], f"got {r!r}")

    # check_memory_sync: not opted out, no SUTANDO_MEMORY_REPO configured → ok
    # (single-machine), NOT warn.
    empty_repo = Path(tempfile.mkdtemp(prefix="sutando-hc-nosync-"))  # no .env inside
    with unittest.mock.patch.object(hc, "_vault_sync_disabled", return_value=False), \
         unittest.mock.patch.object(hc, "REPO_DIR", empty_repo):
        r = hc.check_memory_sync()
    check("not configured → ok (not warn)", r["status"] == "ok", f"got {r!r}")
    check("not-configured detail is non-scary", "not configured" in r["detail"], f"got {r!r}")

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nall check_memory_sync opt-out cases passed")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
