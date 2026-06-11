#!/usr/bin/env python3
"""Security regression guard: _safe_id() and _safe_path() in src/agent-api.py.

These two sanitizers are the first-line defence against path traversal and
identifier injection on the /task and /result HTTP endpoints.

_safe_id(raw):
  Strips everything outside [a-zA-Z0-9_-.] to prevent shell/path injection
  in task identifiers.

_safe_path(base_dir, filename):
  Uses the two-stage CodeQL-recognised path-injection defence:
    1. Whitelist basename to [a-zA-Z0-9_.-] (reject empty).
    2. os.path.realpath normalization.
    3. .startswith(base + os.sep) prefix check.
  Returns None on any traversal attempt.

Run: python3 tests/agent-api-safe-id-path.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# agent-api.py calls resolve_workspace() at module level; set SUTANDO_WORKSPACE
# to a temp dir so it doesn't touch the real workspace on import.
_tmp_ws = tempfile.mkdtemp(prefix="api-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))

spec = importlib.util.spec_from_file_location("agent_api_safe", REPO / "src" / "agent-api.py")
_mod = importlib.util.module_from_spec(spec)
sys.modules["agent_api_safe"] = _mod
spec.loader.exec_module(_mod)

del os.environ["SUTANDO_WORKSPACE"]

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
# _safe_id
# ---------------------------------------------------------------------------

def _test_safe_id():
    f = _mod._safe_id

    # Allowed chars preserved
    _check("si-alnum",         f("task-001_abc.txt") == "task-001_abc.txt")
    _check("si-upper",         f("TASKID") == "TASKID")
    _check("si-digits",        f("12345") == "12345")

    # Path traversal stripped
    _check("si-dotdot",        f("../../etc/passwd") == "....etcpasswd")
    _check("si-slash",         f("path/to/task") == "pathtotask")
    _check("si-backslash",     f("path\\to\\task") == "pathtotask")

    # Shell injection stripped
    _check("si-semicolon",     f("task;rm -rf") == "taskrm-rf")
    _check("si-newline",       f("task\nmalicious") == "taskmalicious")
    _check("si-dollar",        f("task$HOME") == "taskHOME")
    _check("si-pipe",          f("task|cat") == "taskcat")

    # Unicode stripped
    _check("si-unicode",       f("tâche-001") == "tche-001")

    # Empty → empty
    _check("si-empty",         f("") == "")

    # All special → empty
    _check("si-all-special",   f("@#$%^&*") == "")

    # Dash and underscore and dot preserved
    _check("si-dash-dot",      f("task-001.v2") == "task-001.v2")


_test_safe_id()


# ---------------------------------------------------------------------------
# _safe_path
# ---------------------------------------------------------------------------

def _test_safe_path():
    f = _mod._safe_path

    with tempfile.TemporaryDirectory() as base:
        base_path = Path(base)
        # Use realpath for comparisons — macOS /var/folders → /private/var/folders symlink
        base_real = os.path.realpath(base)

        # Normal valid filename → path under base
        result = f(base_path, "task-001")
        _check("sp-valid",         result is not None, str(result))
        _check("sp-under-base",    result is not None and str(result).startswith(base_real))
        _check("sp-txt-suffix",    result is not None and str(result).endswith(".txt"))

        # Empty filename → None (empty after sanitize)
        _check("sp-empty",         f(base_path, "") is None)

        # All-special filename → None (empty after sanitize)
        _check("sp-all-special",   f(base_path, "@#$%") is None)

        # Path traversal attempt: "../secret" — sanitized to "..secret.txt"
        # after stripping "/", but "..secret" realpaths to base/../..secret.txt
        # which is OUTSIDE base, so _safe_path returns None.
        result_traversal = f(base_path, "../secret")
        if result_traversal is not None:
            # If not None, must be safely under base (realpath-expanded)
            _check("sp-traversal-safe",
                   str(result_traversal).startswith(base_real + os.sep),
                   str(result_traversal))
        else:
            _check("sp-traversal-none", True)

        # Double-dot path traversal: "../../etc/passwd" → "....etcpasswd" →
        # resolves outside base (e.g. base/../../.. goes way up) → None
        dbl = f(base_path, "../../etc/passwd")
        _check("sp-dotdot",        dbl is None or str(dbl).startswith(base_real))

        # Absolute path attempt → slash stripped → safe name under base
        result_abs = f(base_path, "/etc/passwd")
        _check("sp-abs-stripped",  result_abs is not None)
        if result_abs is not None:
            _check("sp-abs-under-base", str(result_abs).startswith(base_real))

        # Newline in filename → stripped, result safely under base
        result_nl = f(base_path, "task\n001")
        _check("sp-newline",       result_nl is None or str(result_nl).startswith(base_real))

        # Valid with dots in name
        result_dot = f(base_path, "task.v2.2026")
        _check("sp-dots-valid",    result_dot is not None)
        _check("sp-dots-under",    result_dot is not None and str(result_dot).startswith(base_real))

        # Alphanumeric only
        result_alnum = f(base_path, "taskABC")
        _check("sp-alnum",         result_alnum is not None)

        # Symlink pointing outside base must be blocked
        inner = base_path / "inner"
        inner.mkdir()
        outside = tempfile.mkdtemp(prefix="outside-")
        symlink = inner / "escape"
        symlink.symlink_to(outside)
        # If we ask for the symlink target itself:
        # safe_path builds inner/<safe_name>.txt, not the symlink
        # — this is just ensuring safe_path doesn't traverse via inner/
        result_sym = f(inner, "escape")
        if result_sym is not None:
            # The resolved path (via realpath) may or may not point outside inner
            resolved = os.path.realpath(str(result_sym))
            _check("sp-symlink-safe",
                   resolved.startswith(os.path.realpath(str(inner)) + os.sep),
                   f"resolved={resolved}, inner={inner}")
        else:
            _check("sp-symlink-none", True)


_test_safe_path()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"agent-api-safe-id-path: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
