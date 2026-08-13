#!/usr/bin/env python3
"""A handler worker that dies without emitting HANDLER_DONE must not hold its slot.

`drain_dispatch_queue()` counted FILES in `running/` rather than live workers, so
a worker killed (or reaped) before it emitted `HANDLER_DONE` left its marker
forever. With TASK_HANDLER_WORKERS=2, two such deaths stall the required-handler
lane for the life of the watcher: every later Team-tier task queues in `pending/`
and is never processed, while health-check still reports the watcher healthy.

Observed live 2026-08-13 — 2 running markers, both worker pids dead, one task
pending 10+ minutes, zero dispatches.

WHY A REAL-PROCESS TEST: the defect lives in the interaction between the drain
loop, the worker receipts and the FIFO, and `watch-tasks-stream.sh` has no
BASH_SOURCE guard, so its functions cannot be sourced in isolation.

CLEANUP DISCIPLINE (from tests/watch-tasks-stream-sentinel-ownership.test.py):
`cleanup()` ends in `kill 0`, which signals the whole process group — every
watcher here is started with start_new_session=True or the code under test kills
this test. Never `pkill -f watch-tasks-stream`: that pattern matches the
operator's own live watcher. Only pids this test recorded are killed.

Run: python3 tests/watch-tasks-stream-dead-worker-reap.test.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def wait_for(pred, timeout: float = 12.0, step: float = 0.25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def write_stub_fswatch(bin_dir: Path, feed: Path) -> None:
    """Emitting stub: holds stdout open AND replays paths the test appends.

    The blocking `exec sleep` stub used by the sentinel test is not enough here —
    the stall only becomes observable when a NEW task arrives and finds no free
    slot, and arrival is what triggers a drain. `tail -f` gives both properties
    in one process whose death closes the fd.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "fswatch"
    stub.write_text("#!/bin/sh\nexec tail -n +1 -f " + str(feed) + "\n")
    stub.chmod(0o755)


def write_stub_handler(path: Path) -> None:
    """--probe exits 4 (required handler); a real run blocks so it can be killed."""
    path.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do [ "$a" = "--probe" ] && exit 4; done\n'
        "exec sleep 100000\n"
    )
    path.chmod(0o755)


def dispatch_dir(tmp: Path) -> Path | None:
    cands = sorted(tmp.glob("sutando-task-dispatch.*"), key=lambda p: p.stat().st_mtime)
    return cands[-1] if cands else None


def names(d: Path | None) -> set[str]:
    if d is None or not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_file()}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="reap-test-"))
    ws = tmp / "ws"
    (ws / "tasks").mkdir(parents=True)
    (ws / "results").mkdir()
    (ws / "state").mkdir()
    feed = tmp / "feed"
    feed.write_text("")
    handler = tmp / "handler.sh"
    write_stub_handler(handler)
    write_stub_fswatch(tmp / "bin", feed)

    def task(name: str) -> Path:
        p = ws / "tasks" / name
        p.write_text(f"id: {name[:-4]}\naccess_tier: team\ntask: probe\n")
        return p

    # A and B fill both worker slots via the startup sweep; C arrives later.
    a, b = task("task-aaa.txt"), task("task-bbb.txt")

    env = dict(os.environ)
    env["PATH"] = f"{tmp/'bin'}:{env['PATH']}"
    env["TMPDIR"] = str(tmp)                      # makes the watcher's mktemp dirs findable
    env["SUTANDO_WORKSPACE_DIR"] = str(ws)
    env["SUTANDO_RESULTS_DIR"] = str(ws / "results")
    env["SUTANDO_TASK_EVENT_HANDLER"] = str(handler)

    # The watched dir is $1, NOT an env var — passing it as an env var would
    # silently fall through to the resolver and watch the REAL workspace.
    proc = subprocess.Popen(
        ["bash", "src/watch-tasks-stream.sh", str(ws / "tasks")], cwd=str(REPO), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        got = wait_for(lambda: len(names(dispatch_dir(tmp) and dispatch_dir(tmp) / "running")) >= 2)
        d = dispatch_dir(tmp)
        check("both worker slots filled by the startup sweep", got,
              f"running={names(d and d/'running')} dispatch_dir={d}")
        if not got:
            return 1

        # Record the worker pids, then kill them WITHOUT letting them emit
        # HANDLER_DONE — exactly what a crashed/reaped worker leaves behind.
        pids = []
        for r in (d / "workers").iterdir():
            try:
                pids.append(int(r.read_text().strip()))
            except (ValueError, OSError):
                pass
        check("worker receipts recorded pids", len(pids) >= 2, str(pids))
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        check("both workers are dead",
              wait_for(lambda: all(not _alive(p) for p in pids), 8.0), str(pids))

        # C arrives. Its arrival triggers a drain; with both markers orphaned the
        # buggy drain sees running_count == TASK_HANDLER_WORKERS and never spawns.
        c = task("task-ccc.txt")
        with feed.open("a") as fh:
            fh.write(str(c.resolve()) + "\n")

        dispatched = wait_for(lambda: "task-ccc.txt" in names(dispatch_dir(tmp) / "running"), 12.0)
        d = dispatch_dir(tmp)
        check("a task arriving after both workers died still gets dispatched",
              dispatched,
              f"pending={names(d/'pending')} running={names(d/'running')} "
              "— dead workers still hold both slots (the defect)")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.wait(timeout=5)

    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nPASS — dead handler workers do not hold dispatch slots")
    return 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())
