#!/usr/bin/env python3
"""Regression test for Conversation task rows preserving original task text.

When /tasks/active rebuilds history from a result file after the task file has
already been archived, the row title must remain the original `task:` body, not
the first line of the result.
"""

from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


api = _load("agent_api", REPO / "src" / "agent-api.py")


def test_result_only_history_uses_archived_task_text():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        api.TASK_DIR = root / "tasks"
        api.RESULT_DIR = root / "results"
        api.task_history.clear()

        archive = api.TASK_DIR / "archive"
        archive.mkdir(parents=True)
        api.RESULT_DIR.mkdir(parents=True)

        (archive / "task-123.txt").write_text(
            "id: task-123\n"
            "timestamp: 2026-07-08T00:00:00Z\n"
            "source: api\n"
            "from: web\n"
            "task: original user request\n"
        )
        result_file = api.RESULT_DIR / "task-123.txt"
        result_file.write_text("Done - result summary\n\nDetails follow.\n")

        api._remember_done_result_file(result_file)

        row = api.task_history["task-123"]
        assert row["status"] == "done"
        assert row["text"] == "original user request"
        assert row["result"].startswith("Done - result summary")
        assert row["source"] == "api"


def test_result_only_history_falls_back_without_task_file():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        api.TASK_DIR = root / "tasks"
        api.RESULT_DIR = root / "results"
        api.task_history.clear()
        api.TASK_DIR.mkdir(parents=True)
        api.RESULT_DIR.mkdir(parents=True)

        result_file = api.RESULT_DIR / "task-456.txt"
        result_file.write_text("Done - result summary\n\nDetails follow.\n")

        api._remember_done_result_file(result_file)

        assert api.task_history["task-456"]["text"] == "Done - result summary"


if __name__ == "__main__":
    test_result_only_history_uses_archived_task_text()
    test_result_only_history_falls_back_without_task_file()
    print("agent-api task display text tests passed.")
