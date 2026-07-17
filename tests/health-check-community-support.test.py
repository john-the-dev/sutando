#!/usr/bin/env python3
"""Coverage + behavior for the issue-time community-support pointer (#2156).

health-check prints a link to the official Discord under its 'N issue(s)
found' summary so a stuck user has somewhere to go. The line is produced by
the pure helper community_support_line() (extracted so it's testable without
running the full main() health sweep).

Run: python3 tests/health-check-community-support.test.py  (exit 0/1)
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("healthcheck_cs", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(("  ok  " if cond else "  FAIL ") + name)
    if not cond:
        failures.append(name)


line = hc.community_support_line()
check("returns the official Discord invite", "discord.gg/uZHWXXmrCS" in line)
check("names real humans + community agents", "real humans" in line and "community agents" in line)
check("is indented to align under the issue list", line.startswith("  "))
check("is a single line", "\n" not in line)

print()
if failures:
    print(f"FAIL — {len(failures)}: {failures}")
    sys.exit(1)
print("PASS — community-support line")
