#!/usr/bin/env python3
"""The delivery boundary stamps a task ID, so the PostToolUse hook is not a race.

The hook stamps after a tool call ends, but a bridge can read and post a visible
`results/task-*.txt` before that runs. Every delivery consumer funnels through
read_ready_result, so stamping there is what makes "no ordinary result is
delivered without an ID" structural rather than timing-dependent.
"""
import importlib.util
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src" / "result_ready.py"
_spec = importlib.util.spec_from_file_location("result_ready", _SRC)
rr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rr)

FAILED = []


def check(name, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f" — {extra}"))
    if not cond:
        FAILED.append(name)


tmp = Path(tempfile.mkdtemp())
res = tmp / "results"
res.mkdir()

# The race itself: the file is already visible when the boundary reads it.
f = res / "task-1786700000000.txt"
f.write_text("here is your answer")
body = rr.read_ready_result(f)
check("a visible unstamped result cannot be delivered without an ID",
      body.startswith("[task "), body[:40])
check("the stamp is persisted, so archive and audit see what was sent",
      f.read_text().startswith("[task "))

# Bridge control markers only fire as the first non-empty line.
for marker in ("[no-send]", "[deduped: task-9]", "[REPLIED]", "[channel: 123]", "[dm-only]"):
    g = res / f"task-m{abs(hash(marker))}.txt"
    g.write_text(marker + " trailing")
    check(f"marker stays on line 1: {marker}",
          rr.read_ready_result(g).startswith(marker))

h = res / "task-already.txt"
h.write_text("[task 20260101-007]\n\nbody")
check("an already-stamped body is not stamped twice",
      rr.read_ready_result(h).count("[task ") == 1)

pr = res / "proactive-123.txt"
pr.write_text("morning briefing")
check("a proactive body is never task-stamped",
      rr.read_ready_result(pr) == "morning briefing")

ids = []
for i in range(3):
    q = res / f"task-90{i}.txt"
    q.write_text(f"reply {i}")
    ids.append(rr.read_ready_result(q).split("]")[0])
check("each delivered result gets a distinct ID", len(set(ids)) == 3, str(ids))

empty = res / "task-empty.txt"
empty.write_text("   \n")
check("an empty file is still not ready (and mints no ID)",
      rr.read_ready_result(empty) is None)

print("\n" + ("PASS — delivery-boundary task stamping" if not FAILED
              else f"FAIL — {len(FAILED)} check(s): {FAILED}"))
sys.exit(1 if FAILED else 0)
