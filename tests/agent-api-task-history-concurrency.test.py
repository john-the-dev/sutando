#!/usr/bin/env python3
"""Guards that agent-api serializes task_history access under ThreadingHTTPServer.

agent-api runs on a ThreadingHTTPServer (one thread per request). POST
/task-done mutates the module-global task_history, and GET /tasks/active
rebuilds + sorts it. Before #1855's `_task_history_lock`, those read-modify-write
paths ran unsynchronized — a /tasks/active sort iterating the dict while a
/task-done inserted could raise "dictionary changed size during iteration", and
interleaved writes could clobber each other. qingyun-wu's review noted the
earlier test only proved two parallel GETs complete, not the shared mutable path.

The race itself is timing-dependent and doesn't reproduce deterministically under
the GIL, so rather than a flaky hammer this test is deterministic: it instruments
_task_history_lock and asserts BOTH mutating endpoints actually enter the critical
section, then does a concurrent smoke that asserts no request 5xx's and no write
is lost.

Run: python3 tests/agent-api-task-history-concurrency.test.py
"""
from __future__ import annotations

import http.server
import importlib.util
import json
import threading
import tempfile
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class _CountingLock:
    """Wraps a real lock, counting how many times the guard is entered."""
    def __init__(self):
        self._lock = threading.Lock()
        self.enter_count = 0

    def __enter__(self):
        self._lock.acquire()
        self.enter_count += 1
        return self

    def __exit__(self, *exc):
        self._lock.release()
        return False


def load_agent_api():
    spec = importlib.util.spec_from_file_location("agent_api", REPO / "src" / "agent-api.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class TestTaskHistoryLocking(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.tmp.name)
        (self.workspace / "tasks").mkdir()
        (self.workspace / "results").mkdir()
        self.mod = load_agent_api()
        self.mod.WORKSPACE_DIR = self.workspace
        self.mod.TASK_DIR = self.workspace / "tasks"
        self.mod.RESULT_DIR = self.workspace / "results"
        self.mod.API_TOKEN = ""
        self.mod.task_history.clear()
        self.lock = _CountingLock()
        self.mod._task_history_lock = self.lock
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self.mod.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tmp.cleanup()

    def _post_done(self, i: int) -> int:
        data = json.dumps({"taskId": f"task-{i:03d}", "result": f"done {i}"}).encode()
        req = urllib.request.Request(
            self.base + "/task-done", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status

    def _get_active(self) -> int:
        with urllib.request.urlopen(self.base + "/tasks/active", timeout=5) as r:
            r.read()
            return r.status

    def test_task_done_enters_the_lock(self):
        before = self.lock.enter_count
        self.assertEqual(self._post_done(1), 200)
        self.assertGreater(self.lock.enter_count, before,
                           "POST /task-done mutated task_history without holding the lock")

    def test_tasks_active_enters_the_lock(self):
        before = self.lock.enter_count
        self.assertEqual(self._get_active(), 200)
        self.assertGreater(self.lock.enter_count, before,
                           "GET /tasks/active rebuilt task_history without holding the lock")

    def test_concurrent_writes_and_reads_stay_consistent(self):
        """N concurrent /task-done writes interleaved with /tasks/active reads:
        every request returns 200 and no write is lost."""
        N = 40
        results = []
        with ThreadPoolExecutor(max_workers=16) as ex:
            futs = []
            for i in range(N):
                futs.append(ex.submit(self._post_done, i))
                if i % 3 == 0:
                    futs.append(ex.submit(self._get_active))
            for f in futs:
                results.append(f.result())
        self.assertTrue(all(code == 200 for code in results),
                        f"a request failed under concurrency: {sorted(set(results))}")
        self.assertEqual(
            {f"task-{i:03d}" for i in range(N)} - set(self.mod.task_history),
            set(), "some /task-done writes were lost",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
