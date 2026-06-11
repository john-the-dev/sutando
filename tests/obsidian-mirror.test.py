#!/usr/bin/env python3
"""Tests for src/obsidian-mirror.py — pure helpers + file-mirroring logic.

Covers:
  a) _parse_since()        — time-string parsing: s/m/h/d suffixes, plain int, empty
  b) _task_id_from_path()  — regex extraction from filenames
  c) _parse_task_file()    — header field parsing
  d) _write_task_mirror()  — create/update task .md in vault
  e) _write_result_mirror() — append Result block, update status
  f) _mirror_note()        — .md-only gate, idempotency
  g) sweep()               — counts per kind, since_seconds filter

Run: python3 tests/obsidian-mirror.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "obsidian_mirror", REPO / "src" / "obsidian-mirror.py"
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


# ---------------------------------------------------------------------------
# (a) _parse_since
# ---------------------------------------------------------------------------

def _test_parse_since():
    p = _mod._parse_since

    _check("ps-empty-zero",   p("") == 0)
    _check("ps-plain-int",    p("120") == 120)
    _check("ps-seconds",      p("30s") == 30)
    _check("ps-minutes",      p("30m") == 30 * 60)
    _check("ps-hours",        p("2h") == 2 * 3600)
    _check("ps-days",         p("1d") == 86400)
    _check("ps-large-hours",  p("6h") == 6 * 3600)
    _check("ps-uppercase-M",  p("5M") == 5 * 60)
    _check("ps-uppercase-H",  p("3H") == 3 * 3600)


_test_parse_since()


# ---------------------------------------------------------------------------
# (b) _task_id_from_path
# ---------------------------------------------------------------------------

def _test_task_id_from_path():
    t = _mod._task_id_from_path

    _check("tid-basic",   t(Path("task-123.txt")) == "task-123")
    _check("tid-ts",      t(Path("task-1718123456.txt")) == "task-1718123456")
    _check("tid-voice",   t(Path("task-voice-789.txt")) == "task-voice-789")
    _check("tid-no-match-result", t(Path("result-123.txt")) is None)
    _check("tid-no-match-bare",   t(Path("123.txt")) is None)
    _check("tid-no-match-empty",  t(Path("")) is None)
    # Path with directory component — only basename matters
    _check("tid-with-dir", t(Path("/some/dir/task-abc.txt")) == "task-abc")


_test_task_id_from_path()


# ---------------------------------------------------------------------------
# (c) _parse_task_file
# ---------------------------------------------------------------------------

def _test_parse_task_file():
    p = _mod._parse_task_file

    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "task-1.txt"

        # Non-existent file → info with empty raw
        result = p(Path(tmp) / "nonexistent.txt")
        _check("ptf-missing-raw-empty", result["raw"] == "")

        # Full header fields parsed
        f.write_text(
            "id: task-1718\n"
            "timestamp: 2026-06-10T12:00:00Z\n"
            "task: deploy the app\n"
            "source: voice\n"
            "access_tier: owner\n"
            "priority: urgent\n"
        )
        result = p(f)
        _check("ptf-id",        result.get("id") == "task-1718")
        _check("ptf-task",      result.get("task") == "deploy the app")
        _check("ptf-source",    result.get("source") == "voice")
        _check("ptf-tier",      result.get("access_tier") == "owner")
        _check("ptf-priority",  result.get("priority") == "urgent")
        _check("ptf-raw",       "deploy the app" in result["raw"])

        # Unknown fields ignored, known fields parsed
        f.write_text("id: t\ncustom-field: x\ntask: hello\n")
        result = p(f)
        _check("ptf-unknown-ignored", "custom-field" not in result)
        _check("ptf-known-kept",      result.get("task") == "hello")

        # Value with colon — partition stops at first colon
        f.write_text("task: deploy http://example.com\n")
        result = p(f)
        _check("ptf-colon-in-value",
               result.get("task") == "deploy http://example.com",
               f"got {result.get('task')!r}")


_test_parse_task_file()


# ---------------------------------------------------------------------------
# (d) _write_task_mirror
# ---------------------------------------------------------------------------

def _test_write_task_mirror():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        _mod._ensure_vault(vault)

        task_file = Path(tmp) / "task-abc.txt"
        task_file.write_text(
            "id: task-abc\ntimestamp: 2026-06-10T10:00:00Z\n"
            "source: discord\naccess_tier: owner\npriority: normal\n"
            "task: write the report\n"
        )

        # First write → True (changed)
        result = _mod._write_task_mirror(vault, task_file)
        _check("wtm-first-true", result)

        mirror = vault / "Sutando" / "Agent" / "Tasks" / "task-abc.md"
        _check("wtm-file-created", mirror.exists())

        content = mirror.read_text()
        _check("wtm-has-frontmatter", "---" in content)
        _check("wtm-id-in-fm",       "id: task-abc" in content)
        _check("wtm-status-pending",  "status: pending" in content)
        _check("wtm-source-in-fm",    "source: discord" in content)
        _check("wtm-task-in-body",    "write the report" in content)

        # Second write with same content → False (no change)
        result2 = _mod._write_task_mirror(vault, task_file)
        _check("wtm-idempotent-false", not result2)

        # Modify task → True again
        task_file.write_text(task_file.read_text() + "extra: line\n")
        result3 = _mod._write_task_mirror(vault, task_file)
        _check("wtm-modified-true", result3)

        # File with no matching pattern → False
        bad_path = Path(tmp) / "not-a-task.txt"
        bad_path.write_text("some content\n")
        result4 = _mod._write_task_mirror(vault, bad_path)
        _check("wtm-no-id-false", not result4)

        # Existing mirror with Result block → status becomes completed, result preserved
        result_text = "Done and deployed."
        mirror.write_text(
            mirror.read_text().rstrip() + "\n\n## Result\n\n" + result_text + "\n"
        )
        task_file.write_text(
            "id: task-abc\nsource: discord\ntask: write the report v2\n"
        )
        _mod._write_task_mirror(vault, task_file)
        updated = mirror.read_text()
        _check("wtm-preserves-result", result_text in updated, f"content: {updated[:200]!r}")
        _check("wtm-completed-when-result", "status: completed" in updated)


_test_write_task_mirror()


# ---------------------------------------------------------------------------
# (e) _write_result_mirror
# ---------------------------------------------------------------------------

def _test_write_result_mirror():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        _mod._ensure_vault(vault)

        result_file = Path(tmp) / "task-xyz.txt"
        result_file.write_text("The task is done. Here are the results.\n")

        # Mirror doesn't exist yet → creates stub + result
        changed = _mod._write_result_mirror(vault, result_file)
        _check("wrm-creates-true", changed)

        mirror = vault / "Sutando" / "Agent" / "Tasks" / "task-xyz.md"
        _check("wrm-file-created", mirror.exists())
        content = mirror.read_text()
        _check("wrm-status-completed", "status: completed" in content)
        _check("wrm-result-body",      "The task is done" in content)
        _check("wrm-result-section",   "## Result" in content)

        # Task mirror exists first, then result appended
        task_file = Path(tmp) / "task-def.txt"
        task_file.write_text("id: task-def\nsource: voice\ntask: analyze logs\n")
        _mod._write_task_mirror(vault, task_file)
        mirror_def = vault / "Sutando" / "Agent" / "Tasks" / "task-def.md"
        _check("wrm-pre-task-pending", "status: pending" in mirror_def.read_text())

        result_def = Path(tmp) / "task-def.txt"  # same name re-used for result
        result_def.write_text("Log analysis complete.\n")
        _mod._write_result_mirror(vault, result_def)
        updated_def = mirror_def.read_text()
        _check("wrm-appended-to-existing", "Log analysis complete." in updated_def)
        _check("wrm-status-updated",       "status: completed" in updated_def)

        # Non-matching filename → False
        bad = Path(tmp) / "not-a-result.txt"
        bad.write_text("ignored\n")
        _check("wrm-no-id-false", not _mod._write_result_mirror(vault, bad))

        # Missing result file → False
        _check("wrm-missing-false",
               not _mod._write_result_mirror(vault, Path(tmp) / "task-missing.txt"))


_test_write_result_mirror()


# ---------------------------------------------------------------------------
# (f) _mirror_note
# ---------------------------------------------------------------------------

def _test_mirror_note():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        _mod._ensure_vault(vault)

        # .md file → mirrored
        note = Path(tmp) / "research.md"
        note.write_text("# Research Notes\n\nSome findings.\n")
        result = _mod._mirror_note(vault, note)
        _check("mn-md-true", result)

        dest = vault / "Sutando" / "Agent" / "Notes" / "research.md"
        _check("mn-dest-exists", dest.exists())
        _check("mn-content-match", dest.read_text() == note.read_text())

        # Idempotent — same content → False
        result2 = _mod._mirror_note(vault, note)
        _check("mn-idempotent-false", not result2)

        # Non-.md file → False (gated by suffix check)
        txt = Path(tmp) / "notes.txt"
        txt.write_text("plain text\n")
        _check("mn-non-md-false", not _mod._mirror_note(vault, txt))

        # Updated content → True again
        note.write_text("# Research Notes\n\nUpdated findings.\n")
        result3 = _mod._mirror_note(vault, note)
        _check("mn-updated-true", result3)
        _check("mn-updated-content", "Updated findings." in dest.read_text())


_test_mirror_note()


# ---------------------------------------------------------------------------
# (g) sweep
# ---------------------------------------------------------------------------

def _test_sweep():
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        ws = Path(tmp) / "workspace"
        for d in [ws / "tasks", ws / "results", ws / "notes"]:
            d.mkdir(parents=True)

        # Empty workspace → all zeros
        counts = _mod.sweep(vault, ws)
        _check("sweep-empty-tasks",   counts["tasks"] == 0)
        _check("sweep-empty-results", counts["results"] == 0)
        _check("sweep-empty-notes",   counts["notes"] == 0)
        _check("sweep-empty-scanned", counts["scanned"] == 0)

        # Add a task file
        t1 = ws / "tasks" / "task-001.txt"
        t1.write_text("id: task-001\ntask: test\nsource: chat\n")
        counts2 = _mod.sweep(vault, ws)
        _check("sweep-task-counted", counts2["tasks"] == 1)
        _check("sweep-scanned-1",    counts2["scanned"] == 1)

        # Idempotent — same content → tasks = 0 (no change)
        counts3 = _mod.sweep(vault, ws)
        _check("sweep-task-idempotent", counts3["tasks"] == 0)

        # Add a result file
        r1 = ws / "results" / "task-001.txt"
        r1.write_text("Done.\n")
        counts4 = _mod.sweep(vault, ws)
        _check("sweep-result-counted", counts4["results"] == 1)

        # Add a note
        n1 = ws / "notes" / "ideas.md"
        n1.write_text("# Ideas\n\nFuture plans.\n")
        counts5 = _mod.sweep(vault, ws)
        _check("sweep-note-counted", counts5["notes"] == 1)

        # since_seconds: use a fresh vault/workspace to avoid pre-existing mirrors
        import os
        vault2 = Path(tmp) / "vault2"
        ws2 = Path(tmp) / "workspace2"
        for d in [ws2 / "tasks"]:
            d.mkdir(parents=True)

        old_task = ws2 / "tasks" / "task-002.txt"
        old_task.write_text("id: task-002\ntask: old one\n")
        old_mtime = time.time() - 7200  # 2 hours ago
        os.utime(old_task, (old_mtime, old_mtime))
        counts6 = _mod.sweep(vault2, ws2, since_seconds=3600)  # last 1h window
        _check("sweep-since-old-skipped", counts6["tasks"] == 0,
               f"got tasks={counts6['tasks']}")

        # since_seconds: recent file within window → included
        new_task = ws2 / "tasks" / "task-003.txt"
        new_task.write_text("id: task-003\ntask: recent one\n")
        counts7 = _mod.sweep(vault2, ws2, since_seconds=3600)
        _check("sweep-since-recent-included", counts7["tasks"] == 1,
               f"got tasks={counts7['tasks']}")

        # pending-questions.md → asks counted
        pq = ws / "pending-questions.md"
        pq.write_text("## What should I do?\n\n")
        counts8 = _mod.sweep(vault, ws)
        _check("sweep-asks-counted", counts8["asks"] == 1)


_test_sweep()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"obsidian-mirror: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
