#!/usr/bin/env python3
"""Tests for skills/catchup-after-startup/scripts/migrate-settings-hooks.py.

Covers:
  a) migrate() — no-op cases (missing file, invalid JSON, no hooks, no SessionStop)
  b) migrate() — basic SessionStop → SessionEnd migration
  c) migrate() — deduplication when command already in SessionEnd
  d) migrate() — SessionEnd created when absent
  e) migrate() — null/empty SessionStop cleaned up without output
  f) _atomic_write() — file written atomically, tmp cleaned up

Run: python3 tests/migrate-settings-hooks.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "catchup-after-startup" / "scripts" / "migrate-settings-hooks.py"

spec = importlib.util.spec_from_file_location("migrate_settings_hooks", SCRIPT)
_mod = importlib.util.module_from_spec(spec)
sys.modules["migrate_settings_hooks"] = _mod
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


def _write_settings(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2))


def _read_settings(path: Path) -> dict:
    return json.loads(path.read_text())


def _make_hook(cmd: str) -> dict:
    return {"hooks": [{"type": "command", "command": cmd}]}


# ---------------------------------------------------------------------------
# (a) No-op cases
# ---------------------------------------------------------------------------

def _test_noop():
    f = _mod.migrate

    # File doesn't exist → returns 0
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "settings.json"
        result = f(missing)
        _check("noop-missing",   result == 0)
        _check("noop-no-create", not missing.exists())

    # Invalid JSON → returns 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        p.write_text("{ not valid json")
        result = f(p)
        _check("noop-invalid-json", result == 0)

    # Not a dict → returns 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        p.write_text("[]")
        result = f(p)
        _check("noop-not-dict", result == 0)

    # No hooks key → returns 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        _write_settings(p, {"other": "stuff"})
        result = f(p)
        _check("noop-no-hooks", result == 0)

    # hooks is not a dict → returns 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        _write_settings(p, {"hooks": ["not", "a", "dict"]})
        result = f(p)
        _check("noop-hooks-not-dict", result == 0)

    # No SessionStop key → returns 0, file unchanged
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        orig = {"hooks": {"SessionEnd": [_make_hook("my-hook.sh")]}}
        _write_settings(p, orig)
        mtime_before = p.stat().st_mtime
        result = f(p)
        _check("noop-no-session-stop", result == 0)


_test_noop()


# ---------------------------------------------------------------------------
# (b) Basic migration
# ---------------------------------------------------------------------------

def _test_basic_migration():
    f = _mod.migrate

    # Single SessionStop hook → moved to SessionEnd
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [_make_hook("bash scripts/handoff.sh")],
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("basic-returns-1",    result == 1)
        data = _read_settings(p)
        _check("basic-stop-removed", "SessionStop" not in data["hooks"])
        _check("basic-end-exists",   "SessionEnd" in data["hooks"])
        # Migrated hook should appear in SessionEnd
        se_cmds = []
        for g in data["hooks"]["SessionEnd"]:
            for h in g.get("hooks", []):
                if h.get("type") == "command":
                    se_cmds.append(h["command"])
        _check("basic-cmd-present", "bash scripts/handoff.sh" in se_cmds, str(se_cmds))

    # Two hooks in SessionStop → both moved
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [
                    {"hooks": [
                        {"type": "command", "command": "hook-a.sh"},
                        {"type": "command", "command": "hook-b.sh"},
                    ]}
                ],
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("two-hooks-count", result == 2)
        data = _read_settings(p)
        _check("two-hooks-stop-gone", "SessionStop" not in data["hooks"])

    # Preserves existing non-hooks keys
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "allowedTools": ["bash"],
            "hooks": {
                "SessionStop": [_make_hook("handoff.sh")],
            }
        }
        _write_settings(p, settings)
        f(p)
        data = _read_settings(p)
        _check("preserve-other-keys", data.get("allowedTools") == ["bash"])


_test_basic_migration()


# ---------------------------------------------------------------------------
# (c) Deduplication
# ---------------------------------------------------------------------------

def _test_deduplication():
    f = _mod.migrate

    # Hook already in SessionEnd → deduplicated (returns 0 moved)
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [_make_hook("handoff.sh")],
                "SessionEnd": [_make_hook("handoff.sh")],
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("dedup-returns-0",    result == 0)
        data = _read_settings(p)
        _check("dedup-stop-removed", "SessionStop" not in data["hooks"])
        # Count occurrences of handoff.sh in SessionEnd — should be exactly 1
        se_cmds = []
        for g in data["hooks"]["SessionEnd"]:
            for h in g.get("hooks", []):
                if h.get("type") == "command":
                    se_cmds.append(h["command"])
        count = se_cmds.count("handoff.sh")
        _check("dedup-not-doubled", count == 1, f"handoff.sh appears {count}x")

    # Two hooks in SessionStop: one already in SessionEnd, one new
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [
                    {"hooks": [
                        {"type": "command", "command": "old.sh"},   # already in SE
                        {"type": "command", "command": "new.sh"},   # not yet in SE
                    ]}
                ],
                "SessionEnd": [_make_hook("old.sh")],
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("mixed-dedup-moved", result == 1)  # only new.sh moved


_test_deduplication()


# ---------------------------------------------------------------------------
# (d) SessionEnd created when absent
# ---------------------------------------------------------------------------

def _test_session_end_creation():
    f = _mod.migrate

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [_make_hook("handoff.sh")],
                # No SessionEnd key
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("create-se-moved",    result == 1)
        data = _read_settings(p)
        _check("create-se-exists",   "SessionEnd" in data["hooks"])

    # SessionEnd is explicitly null → reset to list
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        settings = {
            "hooks": {
                "SessionStop": [_make_hook("handoff.sh")],
                "SessionEnd": None,
            }
        }
        _write_settings(p, settings)
        result = f(p)
        _check("null-se-moved",   result == 1)
        data = _read_settings(p)
        _check("null-se-is-list", isinstance(data["hooks"]["SessionEnd"], list))


_test_session_end_creation()


# ---------------------------------------------------------------------------
# (e) Null/empty SessionStop cleaned up quietly
# ---------------------------------------------------------------------------

def _test_empty_session_stop():
    f = _mod.migrate

    # SessionStop: null → removed, returns 0, no output
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        _write_settings(p, {"hooks": {"SessionStop": None}})
        result = f(p)
        _check("null-stop-returns-0", result == 0)
        data = _read_settings(p)
        _check("null-stop-removed",   "SessionStop" not in data["hooks"])

    # SessionStop: [] → removed, returns 0
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "settings.json"
        _write_settings(p, {"hooks": {"SessionStop": []}})
        result = f(p)
        _check("empty-stop-returns-0", result == 0)
        data = _read_settings(p)
        _check("empty-stop-removed",   "SessionStop" not in data["hooks"])


_test_empty_session_stop()


# ---------------------------------------------------------------------------
# (f) _atomic_write
# ---------------------------------------------------------------------------

def _test_atomic_write():
    f = _mod._atomic_write

    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "out.json"
        f(target, '{"ok": true}')
        _check("aw-file-exists",   target.exists())
        _check("aw-content",       json.loads(target.read_text()) == {"ok": True})
        # Tmp file should not remain
        tmp = target.with_name(target.name + ".tmp")
        _check("aw-no-tmp-left",   not tmp.exists())

    # Overwrite existing
    with tempfile.TemporaryDirectory() as td:
        target = Path(td) / "out.json"
        f(target, '"first"')
        f(target, '"second"')
        _check("aw-overwrite", json.loads(target.read_text()) == "second")


_test_atomic_write()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"migrate-settings-hooks: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
