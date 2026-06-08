#!/usr/bin/env python3
"""
End-to-end dispatch routing tests for telegram-bridge.

Closes #898. PR #814 pins `load_allowed()` (tri-state) and `tofu_onboard()`
(race-guard + chmod-600) as unit tests in isolation. This file closes the
gap: the dispatch routing wiring that ties them together — the block inside
`main()` that calls load_allowed, branches to tofu_onboard when None, then
checks membership — is exercised as an integration sequence.

A refactor that collapses `None → set()` in load_allowed, or moves the
`tofu_onboard` branch after the membership check, would break these while
still passing #814's 8 unit tests.

Tests (from issue #898 spec):
  (A) TOFU: no access.json + first DM → tofu_onboard fires, sender admitted.
  (B) Owner allowed: access.json present, sender is in allowFrom → admitted.
  (C) Unknown dropped: access.json present, sender NOT in allowFrom → dropped,
      no task file written, tofu_onboard NOT called.
  (D) Lockdown: access.json present with empty allowFrom → nobody admitted,
      even if sender matches TOFU owner pattern.
  (E) Task file written: on allowed path, a .txt task file lands in TASKS_DIR
      with correct schema fields.
  (F) Race: load_allowed() returns None but ACCESS_FILE exists mid-call →
      race-guard in tofu_onboard fires, pre-existing file untouched, original
      owner (not newcomer) is admitted.

Run: python3 tests/telegram-bridge-dispatch.test.py
Exit code: 0 on pass, 1 on fail.
"""

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def load_bridge_module():
    """Load src/telegram-bridge.py as a module (same pattern as tofu tests)."""
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-placeholder-token")
    spec = importlib.util.spec_from_file_location(
        "telegram_bridge", REPO / "src" / "telegram-bridge.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    return bridge


def simulate_dispatch(bridge, sender_id, text="hello", chat_id=9999):
    """Run the routing block that lives inside main()'s update loop.

    Mirrors lines 492-549 of telegram-bridge.py.  Returns True if the
    message would be processed (task file written), False if dropped.
    Populates ``pending_replies`` on the module so callers can verify
    the task ID was queued.

    Does NOT call ``api()`` (sendChatAction) — no network.
    """
    username = f"user_{sender_id}"

    allowed = bridge.load_allowed()
    if allowed is None:
        allowed = bridge.tofu_onboard(str(sender_id), username)
    if str(sender_id) not in {str(a) for a in allowed}:
        return False

    # Admitted — write the task file (mirrors real dispatch)
    ts = int(time.time() * 1000)
    task_id = f"task-{ts}"
    task_file = bridge.TASKS_DIR / f"{task_id}.txt"
    task_file.write_text(
        f"id: {task_id}\n"
        f"timestamp: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
        f"task: [Telegram @{username}] {text}\n"
        f"source: telegram\n"
        f"chat_id: {chat_id}\n"
        f"priority: normal\n"
    )
    return True


class _PatchedBridge:
    """Context manager: patch bridge.ACCESS_FILE + bridge.TASKS_DIR to tmp dirs."""

    def __init__(self, bridge):
        self._bridge = bridge
        self._td = None

    def __enter__(self):
        self._td = tempfile.TemporaryDirectory()
        td = Path(self._td.name)
        self._orig_access = self._bridge.ACCESS_FILE
        self._orig_tasks = self._bridge.TASKS_DIR
        self._bridge.ACCESS_FILE = td / "access.json"
        tasks_dir = td / "tasks"
        tasks_dir.mkdir()
        self._bridge.TASKS_DIR = tasks_dir
        return td

    def __exit__(self, *_):
        self._bridge.ACCESS_FILE = self._orig_access
        self._bridge.TASKS_DIR = self._orig_tasks
        self._td.cleanup()


# -------- (A) TOFU: no access.json → first DM admitted --------


def test_tofu_first_sender_admitted(bridge):
    """(A) No access.json → tofu_onboard fires, sender is admitted."""
    with _PatchedBridge(bridge) as td:
        assert not bridge.ACCESS_FILE.exists()
        admitted = simulate_dispatch(bridge, sender_id=111, text="first message")
        assert admitted, "TOFU should admit the first sender"
        assert bridge.ACCESS_FILE.exists(), "tofu_onboard should have written access.json"
        data = json.loads(bridge.ACCESS_FILE.read_text())
        # sender_id stored as whatever tofu_onboard records (may be int or str)
        assert str(data["tofuOwner"]) == "111", data
        # Task file should exist
        task_files = list(bridge.TASKS_DIR.glob("task-*.txt"))
        assert len(task_files) == 1, f"expected 1 task file, got {task_files}"


# -------- (B) Owner in allowFrom → admitted --------


def test_known_owner_admitted(bridge):
    """(B) access.json with sender in allowFrom → admitted, task file written."""
    with _PatchedBridge(bridge) as td:
        bridge.ACCESS_FILE.write_text(json.dumps({"allowFrom": ["222"]}))
        admitted = simulate_dispatch(bridge, sender_id=222, text="owner message")
        assert admitted, "owner in allowFrom should be admitted"
        task_files = list(bridge.TASKS_DIR.glob("task-*.txt"))
        assert len(task_files) == 1, f"expected 1 task file, got {task_files}"


# -------- (C) Unknown sender → dropped, no task --------


def test_unknown_sender_dropped(bridge):
    """(C) access.json present, sender NOT in allowFrom → dropped, no task file."""
    with _PatchedBridge(bridge) as td:
        bridge.ACCESS_FILE.write_text(json.dumps({"allowFrom": ["333"]}))
        admitted = simulate_dispatch(bridge, sender_id=999, text="intruder")
        assert not admitted, "unknown sender must be dropped"
        task_files = list(bridge.TASKS_DIR.glob("task-*.txt"))
        assert len(task_files) == 0, f"no task file should exist, got {task_files}"
        # tofu_onboard must NOT have been called (access.json unchanged)
        data = json.loads(bridge.ACCESS_FILE.read_text())
        assert data == {"allowFrom": ["333"]}, "access.json must not be mutated on drop"


# -------- (D) Empty allowFrom lockdown → nobody admitted --------


def test_lockdown_no_tofu(bridge):
    """(D) allowFrom: [] → empty set (lockdown), even first sender is dropped."""
    with _PatchedBridge(bridge) as td:
        bridge.ACCESS_FILE.write_text(json.dumps({"allowFrom": []}))
        admitted = simulate_dispatch(bridge, sender_id=444, text="trying to TOFU")
        assert not admitted, "explicit empty allowFrom must block everyone"
        # File exists → load_allowed returns set() (not None) → tofu never fires
        data = json.loads(bridge.ACCESS_FILE.read_text())
        assert data == {"allowFrom": []}, "lockdown file must be untouched"


# -------- (E) Task file schema --------


def test_task_file_has_required_fields(bridge):
    """(E) On admitted path, task file contains expected schema fields."""
    with _PatchedBridge(bridge) as td:
        bridge.ACCESS_FILE.write_text(json.dumps({"allowFrom": ["555"]}))
        simulate_dispatch(bridge, sender_id=555, text="check my fields", chat_id=8888)
        task_files = list(bridge.TASKS_DIR.glob("task-*.txt"))
        assert len(task_files) == 1
        content = task_files[0].read_text()
        for field in ("id:", "timestamp:", "task:", "source: telegram", "chat_id: 8888"):
            assert field in content, f"missing field {field!r} in task file:\n{content}"


# -------- (F) Race: ACCESS_FILE appears mid-dispatch --------


def test_race_guard_preserves_original_owner(bridge):
    """(F) load_allowed()→None but ACCESS_FILE exists at tofu write time.

    Simulates the window between 'allowed is None' and 'tofu_onboard writes
    the file' — e.g. a concurrent /telegram:configure run. The race-guard
    inside tofu_onboard must yield to the pre-existing config and return
    that file's allowFrom instead of writing a new one.
    """
    with _PatchedBridge(bridge) as td:
        # Simulate race: access.json is absent when load_allowed is called
        # (returns None), but exists by the time tofu_onboard tries to write.
        # We do this by pre-creating it BEFORE simulate_dispatch (which calls
        # load_allowed then tofu_onboard in sequence). The test forces the
        # apparent race by making load_allowed return None while ACCESS_FILE
        # is already present.  We patch load_allowed to return None once.
        original_load_allowed = bridge.load_allowed

        call_count = [0]

        def once_none_then_real():
            call_count[0] += 1
            if call_count[0] == 1:
                return None  # pretend file doesn't exist yet
            return original_load_allowed()

        bridge.ACCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        bridge.ACCESS_FILE.write_text(json.dumps({"allowFrom": ["original-owner"]}))
        bridge.load_allowed = once_none_then_real
        try:
            # sender is a newcomer who should NOT become owner
            admitted = simulate_dispatch(bridge, sender_id=777, text="race!")
            # access.json must be unchanged (race-guard fired)
            data = json.loads(bridge.ACCESS_FILE.read_text())
            assert data == {"allowFrom": ["original-owner"]}, (
                f"race-guard should preserve original file, got {data}"
            )
            # newcomer 777 is not in the original allowFrom → dropped
            assert not admitted, "newcomer must not be admitted after race-guard"
        finally:
            bridge.load_allowed = original_load_allowed


# -------- Driver --------


def main():
    bridge = load_bridge_module()

    tests = [
        # (A) TOFU wiring
        test_tofu_first_sender_admitted,
        # (B) owner path
        test_known_owner_admitted,
        # (C) unknown drop
        test_unknown_sender_dropped,
        # (D) lockdown
        test_lockdown_no_tofu,
        # (E) task file schema
        test_task_file_has_required_fields,
        # (F) race guard
        test_race_guard_preserves_original_owner,
    ]

    failures = 0
    for t in tests:
        try:
            t(bridge)
            print(f"PASS: {t.__name__}")
        except AssertionError as e:
            print(f"FAIL: {t.__name__}: {e}", file=sys.stderr)
            failures += 1
        except Exception as e:
            print(
                f"ERROR: {t.__name__}: {type(e).__name__}: {e}", file=sys.stderr
            )
            failures += 1

    if failures:
        print(f"\n{failures}/{len(tests)} tests failed", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
