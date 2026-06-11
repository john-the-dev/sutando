#!/usr/bin/env python3
"""Regression guard: _grep_env_file() and resolve_workspace() in
skills/agent-registry/scripts/_workspace_resolve.py.

_grep_env_file(key):
  Walks up from __file__ up to _WALK_LEVELS dirs looking for a .env file.
  If found, returns the value of `key=VALUE` (unquoted), else None.
  Never raises.

resolve_workspace():
  1. $SUTANDO_WORKSPACE env var → expanded path.
  2. Best-effort _grep_env_file("SUTANDO_WORKSPACE") → .env fallback.
  3. ~/.sutando/workspace default.

Run: python3 tests/agent-registry-workspace-resolve.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "agent-registry" / "scripts"

# Load the module without executing side effects (none exist at module level
# beyond defining functions and constants).
spec = importlib.util.spec_from_file_location(
    "_workspace_resolve", SCRIPTS / "_workspace_resolve.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["_workspace_resolve"] = _mod
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
# _grep_env_file — monkey-patch __file__ on the module so the walk starts
# from a controlled temp directory.
# ---------------------------------------------------------------------------

def _test_grep_env_file():
    f = _mod._grep_env_file
    orig_file = _mod.__file__

    try:
        tmp = tempfile.mkdtemp(prefix="ws-resolve-test-")
        # Put a .env in tmp/ and pretend __file__ is tmp/sub/script.py
        sub = os.path.join(tmp, "sub")
        os.makedirs(sub, exist_ok=True)
        _mod.__file__ = os.path.join(sub, "script.py")

        # No .env file yet → None
        _check("gr-no-env",  f("SUTANDO_WORKSPACE") is None)

        # .env with bare value
        env = os.path.join(tmp, ".env")
        with open(env, "w") as fh:
            fh.write("SUTANDO_WORKSPACE=/my/workspace\n")
        _check("gr-bare",    f("SUTANDO_WORKSPACE") == "/my/workspace")

        # .env with double-quoted value
        with open(env, "w") as fh:
            fh.write('SUTANDO_WORKSPACE="/quoted/path"\n')
        _check("gr-dquote",  f("SUTANDO_WORKSPACE") == "/quoted/path")

        # .env with single-quoted value
        with open(env, "w") as fh:
            fh.write("SUTANDO_WORKSPACE='/single/quoted'\n")
        _check("gr-squote",  f("SUTANDO_WORKSPACE") == "/single/quoted")

        # Key not present → None
        with open(env, "w") as fh:
            fh.write("SOME_OTHER_KEY=value\n")
        _check("gr-missing-key", f("SUTANDO_WORKSPACE") is None)

        # First .env on the walk wins even if it lacks the key
        nested = os.path.join(tmp, "sub", "sub2")
        os.makedirs(nested, exist_ok=True)
        _mod.__file__ = os.path.join(nested, "deeper.py")
        with open(env, "w") as fh:
            fh.write("# no SUTANDO_WORKSPACE here\n")
        _check("gr-first-wins", f("SUTANDO_WORKSPACE") is None)

        # Tilde in value → expanded by _grep_env_file itself via os.path.expanduser
        with open(env, "w") as fh:
            fh.write("SUTANDO_WORKSPACE=~/workspace\n")
        expanded = f("SUTANDO_WORKSPACE")
        _check("gr-tilde-expanded", expanded == os.path.expanduser("~/workspace"))

    finally:
        _mod.__file__ = orig_file
        # cleanup
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


_test_grep_env_file()


# ---------------------------------------------------------------------------
# resolve_workspace — env var takes priority over .env and default
# ---------------------------------------------------------------------------

def _test_resolve_workspace():
    f = _mod.resolve_workspace
    orig_file = _mod.__file__

    tmp = tempfile.mkdtemp(prefix="ws-resolve2-test-")
    sub = os.path.join(tmp, "sub")
    os.makedirs(sub, exist_ok=True)
    _mod.__file__ = os.path.join(sub, "script.py")

    env_file = os.path.join(tmp, ".env")

    try:
        # 1. Env var set → returns expanded absolute path
        os.environ["SUTANDO_WORKSPACE"] = "/explicit/ws"
        result = f()
        _check("rw-env-var",    result == "/explicit/ws")
        del os.environ["SUTANDO_WORKSPACE"]

        # 2. No env var, .env has value → returns that path
        with open(env_file, "w") as fh:
            fh.write("SUTANDO_WORKSPACE=/from-env-file\n")
        result2 = f()
        _check("rw-env-file",   result2 == "/from-env-file")

        # 3. Env var takes priority over .env file
        os.environ["SUTANDO_WORKSPACE"] = "/priority"
        result3 = f()
        _check("rw-env-priority", result3 == "/priority")
        del os.environ["SUTANDO_WORKSPACE"]

        # 4. Neither env var nor .env → default ~/sutando/workspace expanded
        os.unlink(env_file)
        result4 = f()
        _check("rw-default",    result4 == os.path.expanduser("~/.sutando/workspace"))

        # 5. Env var with tilde → expanded
        os.environ["SUTANDO_WORKSPACE"] = "~/custom/ws"
        result5 = f()
        _check("rw-tilde",      result5 == os.path.expanduser("~/custom/ws"))
        del os.environ["SUTANDO_WORKSPACE"]

    finally:
        _mod.__file__ = orig_file
        os.environ.pop("SUTANDO_WORKSPACE", None)
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


_test_resolve_workspace()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"agent-registry-workspace-resolve: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
