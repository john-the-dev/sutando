#!/usr/bin/env python3
"""The unanswered-task check must FIRE on a missing result and STAY QUIET on
every shape a delivered result actually takes.

A checker that cannot fire is the failure this guards against, so the first
assertion is that the positive case is reachable at all.
Run: python3 tests/unanswered-tasks.test.py
"""
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

_s = importlib.util.spec_from_file_location(
    "uat", str(Path(__file__).resolve().parent.parent / "scripts" / "unanswered-tasks.py"))
uat = importlib.util.module_from_spec(_s)
_s.loader.exec_module(uat)

PASS = FAIL = 0


def check(cond, name):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name}")


def ws(tmp, task_id="task-abc123", result=None, age_sec=600):
    root = Path(tmp)
    (root / "tasks").mkdir(parents=True, exist_ok=True)
    (root / "results").mkdir(parents=True, exist_ok=True)
    t = root / "tasks" / f"{task_id}.txt"
    t.write_text("id: x\n")
    old = time.time() - age_sec
    import os
    os.utime(t, (old, old))
    if result:
        p = root / "results" / result
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("done\n")
    return root


with tempfile.TemporaryDirectory() as d:
    rows = uat.unanswered(ws(d), min_age_sec=120)
    check([r[0] for r in rows] == ["task-abc123"], "FIRES on a task with no result (the case this exists for)")

with tempfile.TemporaryDirectory() as d:
    check(uat.unanswered(ws(d, result="task-abc123.txt"), 120) == [], "quiet: plain results/<id>.txt")

with tempfile.TemporaryDirectory() as d:
    check(uat.unanswered(ws(d, result="phone-CA123.task-abc123.txt"), 120) == [],
          "quiet: per-channel pull namespace <key>.task-<id>.txt")

with tempfile.TemporaryDirectory() as d:
    check(uat.unanswered(ws(d, result="archive/2026-09/task-abc123-1788375000.txt"), 120) == [],
          "quiet: archived task-<id>-<epoch>.txt under archive/YYYY-MM/")

with tempfile.TemporaryDirectory() as d:
    check(uat.unanswered(ws(d, age_sec=5), 120) == [], "quiet: task younger than min-age is still in flight")

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    (root / "tasks").mkdir(parents=True)
    check(uat.unanswered(root, 120) == [], "no results/ dir is not a crash")

check(uat.unanswered(Path("/nonexistent-xyz"), 120) == [], "absent workspace is empty, not an error")

print(f"\nunanswered-tasks: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
