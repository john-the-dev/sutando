#!/usr/bin/env python3
"""A dead handler worker must not hold its dispatch slot, and the reap must publish
a terminal failure unless an EXACT, non-empty result for that task already exists."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []
FAILURE_TEXT = "could not safely process"


def check(name: str, cond: bool, detail: str = "") -> None:
    print(("  ok   " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def wait_for(pred, timeout: float = 30.0, step: float = 0.25) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def names(d: Path | None) -> set[str]:
    if d is None or not d.is_dir():
        return set()
    return {p.name for p in d.iterdir() if p.is_file()}


class Harness:
    """One isolated watcher: own workspace, TMPDIR and session (cleanup() ends in
    `kill 0`). Only pids recorded here are killed — never a pattern match."""

    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="reap-test-"))
        self.ws = self.tmp / "ws"
        (self.ws / "tasks").mkdir(parents=True)
        (self.ws / "results" / "archive").mkdir(parents=True)
        (self.ws / "state").mkdir()
        self.feed = self.tmp / "feed"
        self.feed.write_text("")
        # `tail -f` holds stdout open AND emits on demand: the stall is only
        # observable when a NEW task arrives, and arrival is what drains.
        stub_dir = self.tmp / "bin"
        stub_dir.mkdir()
        (stub_dir / "fswatch").write_text(f"#!/bin/sh\nexec tail -n +1 -f {self.feed}\n")
        (stub_dir / "fswatch").chmod(0o755)
        self.handler = self.tmp / "handler.sh"
        self.handler.write_text(
            '#!/bin/sh\nfor a in "$@"; do [ "$a" = "--probe" ] && exit 4; done\nexec sleep 100000\n')
        self.handler.chmod(0o755)
        self.proc: subprocess.Popen | None = None

    def task(self, name: str) -> Path:
        p = self.ws / "tasks" / name
        p.write_text(f"id: {name[:-4]}\naccess_tier: team\ntask: probe\n")
        return p

    def start(self) -> None:
        env = dict(os.environ)
        env["PATH"] = f"{self.tmp/'bin'}:{env['PATH']}"
        env["TMPDIR"] = str(self.tmp)
        env["SUTANDO_RESULTS_DIR"] = str(self.ws / "results")
        env["SUTANDO_TASK_EVENT_HANDLER"] = str(self.handler)
        # The watched dir is $1, NOT an env var — passing it as one would fall
        # through to the resolver and watch the REAL workspace.
        self.proc = subprocess.Popen(
            ["bash", "src/watch-tasks-stream.sh", str(self.ws / "tasks")],
            cwd=str(REPO), env=env, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)

    def dispatch(self) -> Path | None:
        c = sorted(self.tmp.glob("sutando-task-dispatch.*"), key=lambda p: p.stat().st_mtime)
        return c[-1] if c else None

    def deliver(self, name: str) -> None:
        p = self.task(name)
        with self.feed.open("a") as fh:
            fh.write(str(p.resolve()) + "\n")

    def kill_workers(self) -> list[int]:
        pids = []
        d = self.dispatch()
        for r in (d / "workers").iterdir():
            try:
                pids.append(int(r.read_text().strip()))
            except (ValueError, OSError):
                pass
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        wait_for(lambda: all(not alive(p) for p in pids), 8.0)
        return pids

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        self.proc.wait(timeout=5)


def scenario_slot_recovery() -> None:
    """Dead workers held both slots, so nothing dispatched again."""
    h = Harness()
    h.task("task-aaa.txt")
    h.task("task-bbb.txt")
    h.start()
    try:
        if not wait_for(lambda: len(names(h.dispatch() and h.dispatch() / "running")) >= 2):
            check("both worker slots filled by the startup sweep", False, str(h.dispatch()))
            return
        check("both worker slots filled by the startup sweep", True)
        check("worker pids recorded and killed", len(h.kill_workers()) >= 2)
        h.deliver("task-ccc.txt")
        got = wait_for(lambda: "task-ccc.txt" in names(h.dispatch() / "running"))
        d = h.dispatch()
        check("a task arriving after both workers died still gets dispatched", got,
              f"pending={names(d/'pending')} running={names(d/'running')}")
    finally:
        h.stop()


def scenario_reap_publishes_only_without_an_exact_result() -> None:
    """A prefix-colliding archive must not satisfy the id; a genuine archived
    result must still suppress the failure (so the fix is not "always publish")."""
    h = Harness()
    arch = h.ws / "results" / "archive"
    # `task-1234` must not satisfy `task-123` (prefix collision).
    (arch / "task-1234-999.txt").write_text("a different task's answer\n")
    # A genuine archived result for task-abc — the reap must stay silent here.
    (arch / "task-abc-999.txt").write_text("the real answer\n")
    h.task("task-123.txt")
    h.task("task-abc.txt")
    h.start()
    try:
        if not wait_for(lambda: len(names(h.dispatch() and h.dispatch() / "running")) >= 2):
            check("collision scenario: both slots filled", False)
            return
        h.kill_workers()
        h.deliver("task-zzz.txt")

        res = h.ws / "results"
        published = wait_for(lambda: (res / "task-123.txt").is_file()
                             and (res / "task-123.txt").stat().st_size > 0)
        check("prefix-colliding archive does NOT count as this task's result",
              published and FAILURE_TEXT in (res / "task-123.txt").read_text(),
              "no terminal failure published for task-123")
        # Negative control: the fix must not become "always publish".
        time.sleep(1.0)
        check("a genuine archived result still suppresses the terminal failure",
              not (res / "task-abc.txt").exists(),
              f"spurious failure written: {(res/'task-abc.txt').read_text()[:60] if (res/'task-abc.txt').exists() else ''}")
    finally:
        h.stop()


def scenario_empty_live_result_is_not_delivered() -> None:
    """Empty AND whitespace-only bodies are undeliverable placeholders, per the
    shared result_ready contract — neither may suppress the terminal failure."""
    h = Harness()
    h.task("task-456.txt")
    h.task("task-space.txt")
    h.start()
    try:
        if not wait_for(lambda: len(names(h.dispatch() and h.dispatch() / "running")) >= 2):
            check("placeholder scenario: both slots filled", False)
            return
        (h.ws / "results" / "task-456.txt").write_text("")
        (h.ws / "results" / "task-space.txt").write_text("   \n\t\n")
        h.kill_workers()
        h.deliver("task-yyy.txt")
        for tid, label in (("task-456.txt", "a zero-byte"), ("task-space.txt", "a whitespace-only")):
            res = h.ws / "results" / tid
            check(f"{label} live result does NOT suppress the terminal failure",
                  wait_for(lambda r=res: r.is_file() and FAILURE_TEXT in r.read_text()),
                  f"body={res.read_text()[:40]!r}" if res.exists() else "missing")
    finally:
        h.stop()


def scenario_whitespace_archived_result_is_not_delivered() -> None:
    """An archived body that is whitespace-only never delivered an answer, so the
    reap must still publish rather than release the claim as success."""
    h = Harness()
    (h.ws / "results" / "archive" / "task-wsa-999.txt").write_text("  \n \n")
    h.task("task-wsa.txt")
    h.start()
    try:
        if not wait_for(lambda: len(names(h.dispatch() and h.dispatch() / "running")) >= 1):
            check("archived-whitespace scenario: slot filled", False)
            return
        h.kill_workers()
        h.deliver("task-www.txt")
        res = h.ws / "results" / "task-wsa.txt"
        check("a whitespace-only ARCHIVED result does NOT suppress the failure",
              wait_for(lambda: res.is_file() and FAILURE_TEXT in res.read_text()),
              f"exists={res.exists()}")
    finally:
        h.stop()


def main() -> int:
    scenario_slot_recovery()
    scenario_reap_publishes_only_without_an_exact_result()
    scenario_empty_live_result_is_not_delivered()
    scenario_whitespace_archived_result_is_not_delivered()
    if FAILURES:
        print(f"\n{len(FAILURES)} failure(s)")
        return 1
    print("\nPASS — dead workers free their slot; the reap publishes unless an exact result exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
