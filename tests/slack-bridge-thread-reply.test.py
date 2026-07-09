#!/usr/bin/env python3
"""Tests: the Slack bridge threads the FIRST reply (progress ack), not just the result.

Bug this PR fixes: a task sent from inside a Slack thread had its "On it"
progress ack land in the parent channel. The result already threaded (via the
bridge's in-memory pending_replies[thread_ts]); the ack is sent by
task-progress/notify.py, and the bridge wrote that command with --channel-id but
no --thread-ts, and wrote no thread field into the task file.

Fix: write `thread_ts:` into the task file AND bake `--thread-ts <ts>` into the
generated NOTIFY FIRST command — both only for threaded messages (channel
@mentions), so DMs are unchanged (top-level reply).

Contract:
  (a) Channel @mention (thread_ts from event ts) → the task file's notify
      command includes `--thread-ts <ts>` AND a `thread_ts: <ts>` line.
  (b) DM (channel_type == "im", thread_ts None) → neither appears (top-level).

Run: python3 tests/slack-bridge-thread-reply.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load_slack_bridge():
    os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-placeholder")
    os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test-placeholder")
    os.environ.setdefault("SUTANDO_WORKSPACE", tempfile.mkdtemp(prefix="sutando-test-thread-"))

    class _StubApp:
        def __init__(self, *a, **kw):
            self.client = types.SimpleNamespace(chat_postMessage=lambda **kw: None)

        def event(self, _):
            return lambda fn: fn

    try:
        import slack_bolt as _real_bolt
        _real_bolt.App = _StubApp
    except ImportError:
        stub = types.ModuleType("slack_bolt")
        stub.App = _StubApp
        sys.modules["slack_bolt"] = stub

    if "slack_bolt.adapter" not in sys.modules:
        sys.modules["slack_bolt.adapter"] = types.ModuleType("slack_bolt.adapter")
    if "slack_bolt.adapter.socket_mode" not in sys.modules:
        sm = types.ModuleType("slack_bolt.adapter.socket_mode")
        sm.SocketModeHandler = object
        sys.modules["slack_bolt.adapter.socket_mode"] = sm

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "slack_bridge_thread", REPO / "src" / "slack-bridge.py")
    sys.path.insert(0, str(REPO / "src"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BRIDGE = _load_slack_bridge()


def _setup_state(td: str) -> Path:
    """access.json allowing the test user (no tierMap → owner-tier so the
    skill-hints block renders), temp TASKS_DIR + WORKSPACE, and a
    CLAUDE_CONFIG_DIR holding a fake notify.py so _notify_py.exists() is True."""
    access = Path(td) / "access.json"
    access.write_text(json.dumps({"allowFrom": ["U_OWNER"]}))
    BRIDGE.ACCESS_FILE = access
    tasks = Path(td) / "tasks"
    tasks.mkdir()
    BRIDGE.TASKS_DIR = tasks
    ws = Path(td) / "workspace"
    ws.mkdir()
    BRIDGE.WORKSPACE = str(ws)
    cfg = Path(td) / "cfg" / "skills" / "task-progress" / "scripts"
    cfg.mkdir(parents=True)
    (cfg / "notify.py").write_text("# fake notify for test\n")
    os.environ["CLAUDE_CONFIG_DIR"] = str(Path(td) / "cfg")
    return tasks


def _read_task_file(tasks_dir: Path) -> str:
    files = list(tasks_dir.glob("task-*.txt"))
    assert len(files) == 1, f"expected exactly one task file, got {files}"
    return files[0].read_text()


def test_mention_threads_notify_and_field():
    """(a) Channel @mention → --thread-ts in notify cmd + thread_ts field."""
    with tempfile.TemporaryDirectory() as td:
        tasks = _setup_state(td)
        # No channel_type key → app_mention (channel) event; thread_ts = ts.
        event = {"user": "U_OWNER", "channel": "C_PUB", "text": "hey",
                 "ts": "1700000000.000001"}
        tid = BRIDGE._write_task(event, "Slack mention", "hey", "owner")
        assert tid, "mention task should be written"
        body = _read_task_file(tasks)
        assert "--thread-ts 1700000000.000001" in body, (
            f"notify command must include --thread-ts for a mention; body:\n{body}"
        )
        assert "thread_ts: 1700000000.000001" in body, (
            f"task file must carry thread_ts for a mention; body:\n{body}"
        )


def test_dm_stays_top_level():
    """(b) DM (channel_type == im) → neither --thread-ts nor thread_ts field."""
    with tempfile.TemporaryDirectory() as td:
        tasks = _setup_state(td)
        event = {"user": "U_OWNER", "channel": "D_DM", "channel_type": "im",
                 "text": "hey", "ts": "1700000000.000002"}
        tid = BRIDGE._write_task(event, "Slack DM", "hey", "owner")
        assert tid, "DM task should be written"
        body = _read_task_file(tasks)
        assert "--thread-ts" not in body, (
            f"DM notify command must NOT include --thread-ts; body:\n{body}"
        )
        assert "thread_ts:" not in body, (
            f"DM task file must NOT carry thread_ts; body:\n{body}"
        )


def main() -> int:
    tests = [
        ("a-mention-threads", test_mention_threads_notify_and_field),
        ("b-dm-top-level", test_dm_stays_top_level),
    ]
    failures = 0
    for label, fn in tests:
        try:
            fn()
            print(f"PASS: {label}")
        except AssertionError as e:
            print(f"FAIL: {label} — {e}", file=sys.stderr)
            failures += 1
        except Exception as e:
            print(f"ERROR: {label} — {type(e).__name__}: {e}", file=sys.stderr)
            failures += 1
    if failures:
        print(f"\n{failures}/{len(tests)} tests failed.", file=sys.stderr)
        return 1
    print(f"\nAll {len(tests)} Slack thread-reply tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
