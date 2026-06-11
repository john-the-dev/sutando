#!/usr/bin/env python3
"""Regression guard: _env_file() and _token() in
skills/task-progress/scripts/notify.py.

_env_file(path):
  Parses key=value pairs, strips comments and blank lines, strips quotes.
  Returns {} on OSError (file missing).

_token(source, var):
  1. os.environ[var] wins if set and non-empty.
  2. Falls back to ~/.claude/channels/<source>/.env → _env_file().
  3. Returns "" if neither found.

send_slack / send_discord / send_telegram:
  Return False immediately (no HTTP call) when the required token is absent.

Run: python3 tests/task-progress-notify.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "notify", REPO / "skills" / "task-progress" / "scripts" / "notify.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["notify"] = _mod
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


def _write_tmp(content: str) -> Path:
    fd, p = tempfile.mkstemp(suffix=".env")
    os.close(fd)
    Path(p).write_text(content)
    return Path(p)


# ---------------------------------------------------------------------------
# _env_file
# ---------------------------------------------------------------------------

def _test_env_file():
    f = _mod._env_file

    # Basic key=value pairs
    p = _write_tmp("KEY=value\nFOO=bar\n")
    d = f(str(p))
    _check("ef-basic-key", d.get("KEY") == "value")
    _check("ef-basic-foo", d.get("FOO") == "bar")
    p.unlink()

    # Comments and blank lines skipped
    p2 = _write_tmp("# This is a comment\n\nKEY=val\n\n# Another comment\n")
    d2 = f(str(p2))
    _check("ef-comments", list(d2.keys()) == ["KEY"])
    p2.unlink()

    # Double-quoted values: quotes stripped
    p3 = _write_tmp('TOKEN="my-secret-token"\n')
    d3 = f(str(p3))
    _check("ef-dquote", d3.get("TOKEN") == "my-secret-token")
    p3.unlink()

    # Single-quoted values: quotes stripped
    p4 = _write_tmp("TOKEN='my-secret'\n")
    d4 = f(str(p4))
    _check("ef-squote", d4.get("TOKEN") == "my-secret")
    p4.unlink()

    # Value with = inside (partition splits on first =)
    p5 = _write_tmp("URL=https://api.example.com/v1?key=abc\n")
    d5 = f(str(p5))
    _check("ef-eq-in-val", d5.get("URL") == "https://api.example.com/v1?key=abc")
    p5.unlink()

    # Non-existent file → empty dict
    d6 = f("/nonexistent/path/to/.env")
    _check("ef-missing", d6 == {})

    # Empty file → empty dict
    p7 = _write_tmp("")
    _check("ef-empty", f(str(p7)) == {})
    p7.unlink()

    # Lines without = are skipped
    p8 = _write_tmp("NOT_A_PAIR\nKEY=val\n")
    d8 = f(str(p8))
    _check("ef-no-eq-skipped", "NOT_A_PAIR" not in d8)
    _check("ef-no-eq-key",     d8.get("KEY") == "val")
    p8.unlink()

    # Whitespace around key and value stripped
    p9 = _write_tmp("  KEY  =  spaced value  \n")
    d9 = f(str(p9))
    _check("ef-whitespace", d9.get("KEY") == "spaced value")
    p9.unlink()


_test_env_file()


# ---------------------------------------------------------------------------
# _token — env var wins over file
# ---------------------------------------------------------------------------

def _test_token():
    t = _mod._token

    # Env var set → returned directly (no file lookup needed)
    os.environ["__TEST_TOKEN_VAR__"] = "env-value"
    result = t("slack", "__TEST_TOKEN_VAR__")
    _check("tok-env-wins", result == "env-value")
    del os.environ["__TEST_TOKEN_VAR__"]

    # Env var empty → falls back to channel .env file
    os.environ["__TEST_TOKEN_VAR__"] = ""
    p = _write_tmp("__TEST_TOKEN_VAR__=file-value\n")
    # Override CLAUDE_CONFIG_DIR to point to a temp dir containing channels/testchannel/.env
    tmpdir = Path(tempfile.mkdtemp())
    channel_dir = tmpdir / "channels" / "testchannel"
    channel_dir.mkdir(parents=True)
    (channel_dir / ".env").write_text("__TEST_TOKEN_VAR__=channel-file-value\n")
    old_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmpdir)
    result2 = t("testchannel", "__TEST_TOKEN_VAR__")
    _check("tok-file-fallback", result2 == "channel-file-value",
           f"got {result2!r}")
    del os.environ["__TEST_TOKEN_VAR__"]
    os.environ["CLAUDE_CONFIG_DIR"] = old_dir
    p.unlink()

    # Neither env nor file → empty string
    os.environ.pop("__MISSING_TOKEN__", None)
    tmpdir2 = Path(tempfile.mkdtemp())
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmpdir2)
    result3 = t("slack", "__MISSING_TOKEN__")
    _check("tok-missing", result3 == "")
    if old_dir:
        os.environ["CLAUDE_CONFIG_DIR"] = old_dir
    else:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


_test_token()


# ---------------------------------------------------------------------------
# send_* — no-token path returns False without making HTTP call
# ---------------------------------------------------------------------------

def _test_send_no_token():
    # Clear any real tokens from env for this test
    saved = {}
    for var in ("SLACK_BOT_TOKEN", "DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
        saved[var] = os.environ.pop(var, None)

    # Point channel .env dir to a tmp dir with no .env files
    tmpdir = Path(tempfile.mkdtemp())
    old_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmpdir)

    # Capture stderr to verify the warning is emitted
    import io
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    r_slack   = _mod.send_slack("C123", "test message")
    r_discord = _mod.send_discord("123456", "test message")
    r_tg      = _mod.send_telegram("789", "test message")

    stderr_output = sys.stderr.getvalue()
    sys.stderr = old_stderr

    _check("send-slack-no-tok",   r_slack is False)
    _check("send-discord-no-tok", r_discord is False)
    _check("send-tg-no-tok",      r_tg is False)
    _check("send-warning",        "not found" in stderr_output.lower())

    # Restore env
    for var, val in saved.items():
        if val is not None:
            os.environ[var] = val
    if old_dir:
        os.environ["CLAUDE_CONFIG_DIR"] = old_dir
    else:
        os.environ.pop("CLAUDE_CONFIG_DIR", None)


_test_send_no_token()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"task-progress-notify: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
