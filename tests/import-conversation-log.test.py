#!/usr/bin/env python3
"""Tests for scripts/import-conversation-log.py — parse_iso_to_unix() pure function."""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "import_conversation_log", _REPO / "scripts" / "import-conversation-log.py"
)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["import_conversation_log"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

parse_iso_to_unix = _mod.parse_iso_to_unix

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


# Z suffix → correct UTC epoch
ts = parse_iso_to_unix("2025-06-10T15:30:00Z")
_check("Z suffix → numeric", isinstance(ts, float))
_check("Z suffix → positive", ts is not None and ts > 0)

# Explicit +00:00 matches Z
ts2 = parse_iso_to_unix("2025-06-10T15:30:00+00:00")
_check("+00:00 matches Z", ts is not None and ts2 is not None and abs(ts - ts2) < 0.001)

# Non-UTC offset is handled
ts_plus = parse_iso_to_unix("2025-06-10T17:30:00+02:00")
_check("+02:00 offset normalised", ts_plus is not None and abs(ts_plus - (ts or 0)) < 0.001)

# Epoch boundary
ts_epoch = parse_iso_to_unix("1970-01-01T00:00:00Z")
_check("epoch → 0.0", ts_epoch == 0.0)

# Invalid/empty strings → None
_check("empty string → None", parse_iso_to_unix("") is None)
_check("invalid string → None", parse_iso_to_unix("not-a-timestamp") is None)

print(f"import-conversation-log: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
