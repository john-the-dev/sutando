#!/usr/bin/env python3
"""Tests for scripts/check-utc-z-strftime.py — AST-based local-time mislabeling check."""

import ast
import importlib.util
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "check_utc_z_strftime", _REPO / "scripts" / "check-utc-z-strftime.py"
)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["check_utc_z_strftime"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_is_time_strftime = _mod._is_time_strftime
_single_arg_iso_strftime = _mod._single_arg_iso_strftime
scan_file = _mod.scan_file

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


def _parse_call(src: str) -> ast.Call:
    """Parse a single expression and return the Call node."""
    tree = ast.parse(src, mode="eval")
    return tree.body  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# _is_time_strftime
# ---------------------------------------------------------------------------

call_yes = _parse_call("time.strftime('%Y-%m-%dT%H:%M:%S')")
_check("time.strftime() → True", _is_time_strftime(call_yes))

call_other = _parse_call("datetime.strftime(dt, '%Y-%m-%d')")
_check("datetime.strftime → False (not time.strftime)", not _is_time_strftime(call_other))

call_plain = _parse_call("strftime('%Y-%m-%dT%H:%M:%S')")
_check("bare strftime() → False (no .)", not _is_time_strftime(call_plain))

call_other_attr = _parse_call("time.sleep(1)")
_check("time.sleep → False (wrong attr)", not _is_time_strftime(call_other_attr))

# Not a Call node — pass an ast.Name
name_node = ast.parse("time", mode="eval").body
try:
    result = _is_time_strftime(name_node)  # type: ignore[arg-type]
    _check("Name node → False (no .func)", not result)
except Exception:
    _check("Name node → raises (acceptable)", True)

# ---------------------------------------------------------------------------
# _single_arg_iso_strftime
# ---------------------------------------------------------------------------

# ISO format + single arg → returns format string
call_iso = _parse_call("time.strftime('%Y-%m-%dT%H:%M:%S')")
fmt = _single_arg_iso_strftime(call_iso)
_check("single-arg ISO → returns format string", fmt == "%Y-%m-%dT%H:%M:%S")

# Two args (explicit time tuple) → None even though it's time.strftime + ISO
call_two_args = _parse_call("time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime())")
_check("two-arg call → None (genuine UTC is ok)", _single_arg_iso_strftime(call_two_args) is None)

# Single arg but NOT ISO datetime format
call_no_iso = _parse_call("time.strftime('%H:%M:%S')")
_check("non-ISO format → None", _single_arg_iso_strftime(call_no_iso) is None)

# Not time.strftime at all
call_not_time = _parse_call("datetime.strftime(dt, '%Y-%m-%dT%H:%M:%S')")
_check("non-time.strftime → None", _single_arg_iso_strftime(call_not_time) is None)

# Not a Call node
name_node2 = ast.parse("x", mode="eval").body
_check("non-Call node → None", _single_arg_iso_strftime(name_node2) is None)  # type: ignore[arg-type]

# ---------------------------------------------------------------------------
# scan_file via temp files
# ---------------------------------------------------------------------------

def _tf(content: str) -> Path:
    f = tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False, encoding="utf-8")
    f.write(content)
    f.close()
    return Path(f.name)

# Case A: f-string with ISO single-arg strftime + literal Z → flagged
src_fstring_z = 'import time\nts = f"{time.strftime(\'%Y-%m-%dT%H:%M:%S\')}Z"\n'
hits = scan_file(_tf(src_fstring_z))
_check("f-string ISO+Z → flagged", len(hits) == 1)
_check("f-string ISO+Z → correct line number", hits[0][0] == 2)

# Case B: standalone strftime with Z embedded in format → flagged
src_standalone_z = "import time\nts = time.strftime('%Y-%m-%dT%H:%M:%SZ')\n"
hits = scan_file(_tf(src_standalone_z))
_check("standalone Z in format → flagged", len(hits) == 1)

# Safe: two-arg (explicit gmtime) → not flagged
src_safe_gmtime = "import time\nts = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())\n"
hits = scan_file(_tf(src_safe_gmtime))
_check("two-arg gmtime → not flagged", len(hits) == 0)

# Safe: single-arg but no Z anywhere → not flagged
src_no_z = "import time\nts = time.strftime('%Y-%m-%dT%H:%M:%S')\n"
hits = scan_file(_tf(src_no_z))
_check("single-arg no Z → not flagged", len(hits) == 0)

# Safe: f-string ISO but no literal Z piece → not flagged
src_fstring_no_z = 'import time\nts = f"{time.strftime(\'%Y-%m-%dT%H:%M:%S\')} UTC"\n'
hits = scan_file(_tf(src_fstring_no_z))
_check("f-string ISO no Z literal → not flagged", len(hits) == 0)

# Syntax error → returns empty list (no crash)
src_bad = "def broken(\n"
hits = scan_file(_tf(src_bad))
_check("syntax error → empty list", hits == [])

# Empty file → no hits
src_empty = ""
hits = scan_file(_tf(src_empty))
_check("empty file → no hits", hits == [])

print(f"check-utc-z-strftime: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
