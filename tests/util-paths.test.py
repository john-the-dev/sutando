#!/usr/bin/env python3
"""Tests for src/util_paths.py — personal-asset path resolution.

Covers:
  a) _memory_dir_env()       — new name / legacy name / neither
  b) _private_machine_dir()  — with/without env, SUTANDO_HOST_LABEL
  c) personal_path()         — private-first, workspace fallback, avatar assets/
  d) shared_personal_path()  — top-level private, workspace fallback
  e) claude_home_path()      — CLAUDE_HOME override, subpath joining

Run: python3 tests/util-paths.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("util_paths", REPO / "src" / "util_paths.py")
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0

_ENV_KEYS = ["SUTANDO_MEMORY_DIR", "SUTANDO_PRIVATE_DIR", "SUTANDO_WORKSPACE",
             "SUTANDO_HOST_LABEL", "CLAUDE_HOME"]


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


@contextlib.contextmanager
def _env(**kwargs):
    """Temporarily set environment variables, removing keys not in kwargs."""
    saved = {k: os.environ.get(k) for k in _ENV_KEYS}
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in kwargs.items():
        if v is not None:
            os.environ[k] = v
    try:
        yield
    finally:
        for k in _ENV_KEYS:
            os.environ.pop(k, None)
            if saved[k] is not None:
                os.environ[k] = saved[k]


# ---------------------------------------------------------------------------
# (a) _memory_dir_env
# ---------------------------------------------------------------------------

def _test_memory_dir_env():
    # Neither env set → None
    with _env():
        _check("mde-neither-none", _mod._memory_dir_env() is None)

    # SUTANDO_MEMORY_DIR set → returns it
    with _env(SUTANDO_MEMORY_DIR="/tmp/mymem"):
        _check("mde-new-name", _mod._memory_dir_env() == "/tmp/mymem")

    # Only SUTANDO_PRIVATE_DIR set → returns it + emits deprecation to stderr
    with _env(SUTANDO_PRIVATE_DIR="/tmp/legacymem"):
        buf = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = buf
        try:
            val = _mod._memory_dir_env()
        finally:
            sys.stderr = old_stderr
        _check("mde-legacy-value", val == "/tmp/legacymem", f"got {val!r}")
        _check("mde-legacy-warning",
               "DEPRECATION" in buf.getvalue() or "deprecated" in buf.getvalue().lower(),
               f"stderr: {buf.getvalue()!r}")

    # Both set → SUTANDO_MEMORY_DIR takes priority
    with _env(SUTANDO_MEMORY_DIR="/tmp/new", SUTANDO_PRIVATE_DIR="/tmp/old"):
        _check("mde-new-wins", _mod._memory_dir_env() == "/tmp/new")


_test_memory_dir_env()


# ---------------------------------------------------------------------------
# (b) _private_machine_dir
# ---------------------------------------------------------------------------

def _test_private_machine_dir():
    # No env → None
    with _env():
        _check("pmd-no-env-none", _mod._private_machine_dir() is None)

    # With SUTANDO_MEMORY_DIR → machine-<hostname> subdir
    with tempfile.TemporaryDirectory() as tmp:
        with _env(SUTANDO_MEMORY_DIR=tmp):
            result = _mod._private_machine_dir()
        _check("pmd-returns-path", result is not None)
        _check("pmd-machine-prefix", result is not None and result.name.startswith("machine-"),
               f"got {result}")
        _check("pmd-under-memory-dir", result is not None and str(result).startswith(tmp))

    # SUTANDO_HOST_LABEL overrides gethostname()
    with tempfile.TemporaryDirectory() as tmp:
        with _env(SUTANDO_MEMORY_DIR=tmp, SUTANDO_HOST_LABEL="myhost"):
            result3 = _mod._private_machine_dir()
        _check("pmd-host-label", result3 is not None and result3.name == "machine-myhost",
               f"got {result3}")


_test_private_machine_dir()


# ---------------------------------------------------------------------------
# (c) personal_path
# ---------------------------------------------------------------------------

def _test_personal_path():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir()
        mem = Path(tmp) / "mem"
        mem.mkdir()
        machine_dir = mem / "machine-testhost"
        machine_dir.mkdir()

        # No SUTANDO_MEMORY_DIR, file doesn't exist → workspace/<filename>
        with _env():
            result = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-no-env-fallback-ws", result == ws / "stand-identity.json",
               f"got {result}")

        # SUTANDO_MEMORY_DIR set, machine file exists → returns machine file
        machine_file = machine_dir / "stand-identity.json"
        machine_file.write_text("{}")
        with _env(SUTANDO_MEMORY_DIR=str(mem), SUTANDO_HOST_LABEL="testhost"):
            result2 = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-machine-file-preferred", result2 == machine_file, f"got {result2}")

        # Machine file doesn't exist, workspace file does → workspace
        machine_file.unlink()
        ws_file = ws / "stand-identity.json"
        ws_file.write_text("{}")
        with _env(SUTANDO_MEMORY_DIR=str(mem), SUTANDO_HOST_LABEL="testhost"):
            result3 = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-ws-fallback", result3 == ws_file, f"got {result3}")

        # Nothing exists → returns preferred (machine dir path)
        ws_file.unlink()
        with _env(SUTANDO_MEMORY_DIR=str(mem), SUTANDO_HOST_LABEL="testhost"):
            result4 = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-nothing-returns-machine-preferred",
               result4 == machine_dir / "stand-identity.json",
               f"got {result4}")

        # No SUTANDO_MEMORY_DIR, nothing exists → workspace/<filename>
        with _env():
            result5 = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-no-env-nothing-returns-ws", result5 == ws / "stand-identity.json",
               f"got {result5}")

        # Avatar file: tries assets/ in workspace first
        assets_dir = ws / "assets"
        assets_dir.mkdir()
        avatar_assets = assets_dir / "stand-avatar.png"
        avatar_assets.write_bytes(b"\x89PNG")
        with _env():
            result6 = _mod.personal_path("stand-avatar.png", workspace=ws)
        _check("pp-avatar-assets-dir", result6 == avatar_assets, f"got {result6}")

        # Avatar: assets/ file doesn't exist, workspace root file does
        avatar_assets.unlink()
        avatar_ws = ws / "stand-avatar.png"
        avatar_ws.write_bytes(b"\x89PNG")
        with _env():
            result7 = _mod.personal_path("stand-avatar.png", workspace=ws)
        _check("pp-avatar-ws-root-fallback", result7 == avatar_ws, f"got {result7}")

        # Avatar: nothing exists → prefers assets/ path
        avatar_ws.unlink()
        with _env():
            result8 = _mod.personal_path("stand-avatar.png", workspace=ws)
        _check("pp-avatar-nothing-prefers-assets",
               result8 == ws / "assets" / "stand-avatar.png",
               f"got {result8}")

        # Machine file takes priority over everything (even when ws file exists)
        machine_file.write_text("{}")
        ws_file.write_text("{}")
        with _env(SUTANDO_MEMORY_DIR=str(mem), SUTANDO_HOST_LABEL="testhost"):
            result9 = _mod.personal_path("stand-identity.json", workspace=ws)
        _check("pp-machine-beats-ws", result9 == machine_file, f"got {result9}")


_test_personal_path()


# ---------------------------------------------------------------------------
# (d) shared_personal_path
# ---------------------------------------------------------------------------

def _test_shared_personal_path():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Path(tmp) / "workspace"
        ws.mkdir()
        mem = Path(tmp) / "mem"
        mem.mkdir()

        # No env → workspace/<filename>
        with _env():
            result = _mod.shared_personal_path("notes", workspace=ws)
        _check("spp-no-env-ws", result == ws / "notes", f"got {result}")

        # SUTANDO_MEMORY_DIR set, private file exists → private top-level (no machine-host/)
        priv_notes = mem / "notes"
        priv_notes.mkdir()
        with _env(SUTANDO_MEMORY_DIR=str(mem)):
            result2 = _mod.shared_personal_path("notes", workspace=ws)
        _check("spp-private-preferred", result2 == priv_notes, f"got {result2}")

        # Confirm it's NOT in machine-host/ subdir
        _check("spp-no-machine-subdir", "machine-" not in str(result2))

        # Private doesn't exist, workspace exists → workspace
        priv_notes.rmdir()
        ws_notes = ws / "notes"
        ws_notes.mkdir()
        with _env(SUTANDO_MEMORY_DIR=str(mem)):
            result3 = _mod.shared_personal_path("notes", workspace=ws)
        _check("spp-ws-fallback", result3 == ws_notes, f"got {result3}")

        # Nothing exists → returns preferred private path
        ws_notes.rmdir()
        with _env(SUTANDO_MEMORY_DIR=str(mem)):
            result4 = _mod.shared_personal_path("notes", workspace=ws)
        _check("spp-nothing-returns-private-preferred", result4 == mem / "notes",
               f"got {result4}")


_test_shared_personal_path()


# ---------------------------------------------------------------------------
# (e) claude_home_path
# ---------------------------------------------------------------------------

def _test_claude_home_path():
    # No CLAUDE_HOME, no subpath → ~/.claude
    with _env():
        result = _mod.claude_home_path()
    _check("chp-default-base", result == Path.home() / ".claude", f"got {result}")

    # No CLAUDE_HOME, with subpath → ~/.claude/<parts>
    with _env():
        result2 = _mod.claude_home_path("channels", "discord", "access.json")
    expected = Path.home() / ".claude" / "channels" / "discord" / "access.json"
    _check("chp-subpath", result2 == expected, f"got {result2}")

    # CLAUDE_HOME override
    with tempfile.TemporaryDirectory() as tmp:
        with _env(CLAUDE_HOME=tmp):
            result3 = _mod.claude_home_path()
        _check("chp-override-base", result3 == Path(tmp), f"got {result3}")

        with _env(CLAUDE_HOME=tmp):
            result4 = _mod.claude_home_path("skills", "my-skill")
        _check("chp-override-subpath", result4 == Path(tmp) / "skills" / "my-skill",
               f"got {result4}")

    # CLAUDE_HOME with ~ expansion
    with _env(CLAUDE_HOME="~/.test-claude-override"):
        result5 = _mod.claude_home_path()
    _check("chp-tilde-expansion",
           str(result5) == str(Path.home() / ".test-claude-override"),
           f"got {result5}")

    # Single subpath component
    with _env():
        result6 = _mod.claude_home_path("settings.json")
    _check("chp-single-component", result6 == Path.home() / ".claude" / "settings.json",
           f"got {result6}")


_test_claude_home_path()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"util-paths: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
