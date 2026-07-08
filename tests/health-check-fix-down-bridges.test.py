#!/usr/bin/env python3
"""
Regression tests for fix_down_bridges(): `--fix` restarting bridges that are
"configured but not running".

Incident (2026-07-02): discord-bridge died at boot with nothing logged. Its
check status is "warn" (optional channels don't page), which excludes it from
`issues` — so main()'s fix loop never reached the bridge-restart branch and
`--fix` left it down while owner DMs queued channel-side. fix_down_bridges()
dispatches off the full `checks` list instead, mirroring the screen-capture
warn-fix pattern.

Guards:

  a) "configured but not running" warn → bridge restarted (all 3 bridges)
  b) other bridge warns (multiple PIDs, token invalid, stale log) → untouched
  c) non-bridge checks with the same detail → untouched
  d) ok/fail bridge statuses → untouched (fail belongs to the main fix loop)

Run: python3 tests/health-check-fix-down-bridges.test.py
Exit code: 0 on pass, 1 on fail.
"""

from __future__ import annotations
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

# Load src/health-check.py as `health_check` (filename has a hyphen, can't
# import directly).
spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def run_with_popen_stub(checks: list) -> tuple[list, list]:
    """Call fix_down_bridges with Popen stubbed; return (restarted, spawn argvs)."""
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        return mock.MagicMock()

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(hc, "WORKSPACE_DIR", Path(td)), \
             mock.patch.object(hc.subprocess, "Popen", side_effect=fake_popen):
            restarted = hc.fix_down_bridges(checks)
    return restarted, spawned


def case_a_down_bridges_restarted() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "warn", "configured but not running"),
        check("telegram-bridge", "warn", "configured but not running"),
        check("slack-bridge", "warn", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted != ["discord-bridge", "telegram-bridge", "slack-bridge"]:
        fails.append(f"a) expected all 3 bridges restarted, got {restarted}")
    if len(spawned) != 3:
        fails.append(f"a) expected 3 spawns, got {len(spawned)}")
    for argv in spawned:
        if not str(argv[1]).endswith("-bridge.py"):
            fails.append(f"a) spawn argv doesn't target a bridge script: {argv}")
    return fails


def case_b_other_bridge_warns_untouched() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "warn", "multiple processes (2 PIDs: 1,2)"),
        check("discord-bridge", "warn", "token invalid (LoginFailure) — regenerate at discord.com/developers/applications"),
        check("telegram-bridge", "warn", "log stale (36.0h) — process may be wedged"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"b) non-down bridge warns triggered restart: {restarted}")
    return fails


def case_c_non_bridge_checks_untouched() -> list[str]:
    fails = []
    checks = [
        check("conversation-server", "warn", "configured but not running"),
        check("credential-proxy", "warn", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"c) non-covered checks triggered restart: {restarted}")
    return fails


def case_d_other_statuses_untouched() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "ok", "running"),
        check("telegram-bridge", "down", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"d) ok/down statuses triggered restart: {restarted}")
    return fails


def main() -> int:
    all_fails = []
    for case in (case_a_down_bridges_restarted, case_b_other_bridge_warns_untouched,
                 case_c_non_bridge_checks_untouched, case_d_other_statuses_untouched):
        fails = case()
        status = "PASS" if not fails else "FAIL"
        print(f"  {status} {case.__name__}")
        all_fails.extend(fails)
    if all_fails:
        print()
        for f in all_fails:
            print(f"  ✗ {f}")
        return 1
    print("All fix_down_bridges tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
