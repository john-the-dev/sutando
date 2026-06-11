#!/usr/bin/env python3
"""Tests for scripts/import-session-metrics.py — iso_to_unix() pure function."""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "import_session_metrics", _REPO / "scripts" / "import-session-metrics.py"
)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["import_session_metrics"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

iso_to_unix = _mod.iso_to_unix

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


# Basic UTC (Z suffix)
ts = iso_to_unix("2025-01-15T12:00:00Z")
_check("Z suffix → numeric", isinstance(ts, float))
_check("Z suffix → correct epoch (>0)", ts is not None and ts > 0)

# Explicit timezone offset
ts2 = iso_to_unix("2025-01-15T12:00:00+00:00")
_check("explicit +00:00 → same as Z", abs((ts or 0) - (ts2 or 0)) < 0.001)

# Non-UTC offset
ts_offset = iso_to_unix("2025-01-15T14:00:00+02:00")
_check("+02:00 offset → correct (14:00+02:00 == 12:00Z)", ts_offset is not None and abs(ts_offset - (ts or 0)) < 0.001)

# None input → None
_check("None → None", iso_to_unix(None) is None)

# Non-string input → None
_check("int → None", iso_to_unix(123) is None)  # type: ignore[arg-type]
_check("list → None", iso_to_unix([]) is None)   # type: ignore[arg-type]

# Invalid string → None
_check("empty string → None", iso_to_unix("") is None)
_check("random string → None", iso_to_unix("not-a-date") is None)
_check("partial date → None", iso_to_unix("2025-01") is None)

# Epoch for a known timestamp
ts_known = iso_to_unix("1970-01-01T00:00:00Z")
_check("epoch 1970-01-01 → 0.0", ts_known == 0.0)

print(f"import-session-metrics: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
