#!/usr/bin/env python3
"""Graceful-shutdown sentinel helper (owner ask 2026-07-17).

The sentinel is the durable "shutting down on purpose, not crashing" signal the
core loop / bridges check to exit cleanly. Guards the mark/clear/check/info
lifecycle + the CLI used by restart.sh and startup.sh.

Run: python3 tests/shutdown-sentinel.test.py   (exit 0/1)
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("shutdown_mod", REPO / "src" / "shutdown.py")
sd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sd)

# Redirect the sentinel to a temp path (never touch the real state dir).
_tmp = Path(tempfile.mkdtemp(prefix="shutdown-")) / "shutdown.sentinel"
sd._sentinel_path = lambda: _tmp

failures: list[str] = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + ((" — " + detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# clean slate
check("not shutting down initially", sd.is_shutting_down() is False)
check("info is None when absent", sd.shutdown_info() is None)

# mark
sd.mark_shutdown("restart.sh --stop-only")
check("is_shutting_down after mark", sd.is_shutting_down() is True)
info = sd.shutdown_info()
check("info carries reason", info and info.get("reason") == "restart.sh --stop-only")
check("info carries a timestamp", info and isinstance(info.get("ts"), int) and info["ts"] > 0)

# mark is idempotent (overwrite, still exactly one sentinel)
sd.mark_shutdown("again")
check("mark overwrites reason", sd.shutdown_info().get("reason") == "again")

# clear
sd.clear_shutdown()
check("cleared → not shutting down", sd.is_shutting_down() is False)
check("clear is idempotent (no raise when absent)", (sd.clear_shutdown() or True))

# corrupt sentinel → info degrades, doesn't raise
_tmp.write_text("{not json")
check("corrupt sentinel still reads as shutting-down", sd.is_shutting_down() is True)
check("corrupt sentinel info degrades gracefully", sd.shutdown_info().get("reason") == "unknown")
_tmp.unlink()

# ── CLI (as restart.sh / startup.sh invoke it) ────────────────────────────────
# Point the CLI's workspace at our temp via a tiny wrapper env would be ideal,
# but the module resolves the real workspace; exercise the pure functions above
# for behavior and the CLI dispatch for exit codes with a monkeypatched path.
import os
env = dict(os.environ)
# check exits 1 when not shutting down (real state dir has no sentinel in CI)
r = subprocess.run([sys.executable, str(REPO / "src" / "shutdown.py"), "check"], env=env, capture_output=True)
check("CLI check exits nonzero when not shutting down", r.returncode == 1, f"rc={r.returncode}")

print()
if failures:
    print(f"FAIL — {len(failures)}: {failures}")
    sys.exit(1)
print("PASS — shutdown sentinel")
