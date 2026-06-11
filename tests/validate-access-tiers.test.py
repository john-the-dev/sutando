#!/usr/bin/env python3
"""Tests for scripts/validate-access-tiers.py — violations() pure function."""

import importlib.util
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "validate_access_tiers", _REPO / "scripts" / "validate-access-tiers.py"
)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["validate_access_tiers"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

violations = _mod.violations

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


# No violation: bot_id not in allowFrom
data = {"allowFrom": ["111", "222", "333"]}
_check("no overlap → empty list", violations(data, {"999"}) == [])

# Single violation
data = {"allowFrom": ["111", "222", "BOT_ID_1"]}
v = violations(data, {"BOT_ID_1"})
_check("single violation found", v == ["BOT_ID_1"])

# Multiple violations sorted
data = {"allowFrom": ["BOT_B", "BOT_A", "333"]}
v = violations(data, {"BOT_A", "BOT_B"})
_check("multiple violations sorted", v == ["BOT_A", "BOT_B"])

# Partial overlap
data = {"allowFrom": ["OWNER_1", "BOT_X", "OTHER"]}
v = violations(data, {"BOT_X", "BOT_Y"})
_check("partial overlap → only intersecting", v == ["BOT_X"])

# allowFrom missing → no violation
data = {}
_check("missing allowFrom → no violation", violations(data, {"BOT_1"}) == [])

# allowFrom empty → no violation
data = {"allowFrom": []}
_check("empty allowFrom → no violation", violations(data, {"BOT_1"}) == [])

# Empty bot_ids set → no violation
data = {"allowFrom": ["BOT_1", "BOT_2"]}
_check("empty bot_ids → no violation", violations(data, set()) == [])

# int IDs in allowFrom — coerced to str
data = {"allowFrom": [111222333444555666]}
v = violations(data, {"111222333444555666"})
_check("int allowFrom coerced to str", v == ["111222333444555666"])

print(f"validate-access-tiers: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
