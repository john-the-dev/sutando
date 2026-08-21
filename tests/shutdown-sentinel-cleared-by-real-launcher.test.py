#!/usr/bin/env python3
"""Every production launcher must clear the shutdown sentinel, loudly (#2165).

`restart.sh --stop-only` leaves the sentinel set on purpose — it IS the clean-exit
signal — and `watch-tasks-stream.sh` gates on its bare existence. If the next real
core boot does not clear it, every task that session is silently skipped: a
healthy-looking core that stops answering. `shutdown-sentinel.test.py` cannot see
this; it drives the helper directly and never asks which launchers call it.

This test deliberately does NOT execute `shutdown.py clear`: that helper resolves
the REAL workspace (`resolve_workspace()`), so running it here would clear an
owner's intentional stop. Behaviour of clear itself is covered by the helper suite.

Run: python3 tests/shutdown-sentinel-cleared-by-real-launcher.test.py  (exit 0/1)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHERS = {
    "src/agent/claude/cli/start-cli.sh": "the launcher the desktop app actually uses",
    "src/startup.sh": "the headless/service launcher",
}
CLEAR_RE = re.compile(r'^[^\n]*shutdown\.py"?\s+clear[^\n]*$', re.M)
failures: list[str] = []

for rel, why in LAUNCHERS.items():
    path = REPO / rel
    if not path.exists():
        failures.append(f"{rel}: not found ({why})")
        continue
    lines = CLEAR_RE.findall(path.read_text())
    if not lines:
        failures.append(
            f"{rel}: never clears shutdown.sentinel ({why}) — after "
            f"`restart.sh --stop-only` the intake gate holds every task all session")
        continue
    for ln in lines:
        if "2>/dev/null" in ln and re.search(r"\|\|\s*true", ln):
            failures.append(
                f"{rel}: clear discards its own failure (`2>/dev/null || true`) — "
                f"a failed transition then reports success")
        if re.search(r"(^|\s)python3\s", ln):
            failures.append(
                f"{rel}: clear runs a bare `python3`, which can resolve to the "
                f"Xcode-CLT stub; route it through the resolved interpreter")

if failures:
    for f in failures:
        print(f"FAIL: {f}")
    print(f"\nResults: {len(failures)} failure(s)")
    sys.exit(1)
print("OK: both production launchers clear the sentinel, via a resolved interpreter, "
      "without discarding failure")
print("Results: all assertions passed")
