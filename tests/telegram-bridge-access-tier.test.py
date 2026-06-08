#!/usr/bin/env python3
"""
Tests for Telegram access-tier support (issue #1381 item 1).

Verifies that _resolve_access_tier() correctly reads tierMap from access.json
and that the task write format includes access_tier + user_id fields.

Run: python3 tests/telegram-bridge-access-tier.test.py
Exit 0 on pass, 1 on fail.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Stub external dependencies before loading the bridge
# ---------------------------------------------------------------------------

_tp = types.ModuleType("task_priority")
_tp.default_priority_for_source = lambda source, access_tier=None: (
    "normal" if (access_tier or "owner") == "owner" else "low"
)
sys.modules["task_priority"] = _tp

_wd = types.ModuleType("workspace_default")
_wd.resolve_workspace = lambda: REPO
sys.modules["workspace_default"] = _wd

_vp = types.ModuleType("vision_push")
_vp.push_image = lambda path, source="telegram": False
sys.modules["vision_push"] = _vp

_dotenv = types.ModuleType("dotenv")
_dotenv.load_dotenv = lambda *a, **kw: None
sys.modules["dotenv"] = _dotenv

# result_markers and task_archive stubs (imported by bridge at module level)
_rm = types.ModuleType("result_markers")
_rm.parse_markers = lambda body: {}
sys.modules["result_markers"] = _rm

_ta = types.ModuleType("task_archive")
_ta.find_task_file = lambda task_id, tasks_dir, archive_dir: None
sys.modules["task_archive"] = _ta

_si = types.ModuleType("single_instance")
_si.acquire = lambda *a, **kw: True
sys.modules["single_instance"] = _si

os.environ["TELEGRAM_BOT_TOKEN"] = "test-stub-token"


def _load_bridge():
    src = (REPO / "src" / "telegram-bridge.py").read_text()
    spec_loader = None
    import importlib.util
    spec = importlib.util.spec_from_loader("telegram_bridge", loader=spec_loader)
    mod = importlib.util.module_from_spec(spec)
    mod.__file__ = str(REPO / "src" / "telegram-bridge.py")
    exec(src, mod.__dict__)
    return mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def run():
    bridge = _load_bridge()
    fails: list[str] = []
    passed = 0

    with tempfile.TemporaryDirectory() as td:
        fake_access = Path(td) / "access.json"
        orig_access = bridge.ACCESS_FILE

        def _set(data: dict | None):
            if data is None:
                fake_access.unlink(missing_ok=True)
            else:
                fake_access.write_text(json.dumps(data))

        try:
            bridge.ACCESS_FILE = fake_access

            # 1. No tierMap at all → default "owner"
            _set({"allowFrom": ["alice"]})
            result = bridge._resolve_access_tier("alice")
            if result != "owner":
                fails.append("1. no tierMap: expected 'owner', got {!r}".format(result))
            else:
                passed += 1

            # 2. tierMap explicit "owner"
            _set({"allowFrom": ["alice"], "tierMap": {"alice": "owner"}})
            result = bridge._resolve_access_tier("alice")
            if result != "owner":
                fails.append("2. explicit owner: expected 'owner', got {!r}".format(result))
            else:
                passed += 1

            # 3. tierMap "team"
            _set({"allowFrom": ["alice", "bob"], "tierMap": {"bob": "team"}})
            result = bridge._resolve_access_tier("bob")
            if result != "team":
                fails.append("3. team tier: expected 'team', got {!r}".format(result))
            else:
                passed += 1

            # 4. tierMap "other"
            _set({"allowFrom": ["alice", "guest"], "tierMap": {"guest": "other"}})
            result = bridge._resolve_access_tier("guest")
            if result != "other":
                fails.append("4. other tier: expected 'other', got {!r}".format(result))
            else:
                passed += 1

            # 5. tierMap has unknown value → degrade to "other" (split-default)
            # A misspelled tier value (e.g. "superadmin") is unrecognised; the
            # safe fallback when tierMap is present is "other", not "owner" —
            # same as the "mapped but value invalid" case (issue #937).
            _set({"allowFrom": ["alice"], "tierMap": {"alice": "superadmin"}})
            result = bridge._resolve_access_tier("alice")
            if result != "other":
                fails.append("5. unknown tier value: expected 'other' (safe fallback), got {!r}".format(result))
            else:
                passed += 1

            # 6. access.json missing → fail-open to "owner"
            _set(None)
            result = bridge._resolve_access_tier("alice")
            if result != "owner":
                fails.append("6. missing file: expected 'owner' fallback, got {!r}".format(result))
            else:
                passed += 1

            # 7. Malformed JSON → fail-open to "owner"
            fake_access.write_text("NOT JSON {{{")
            result = bridge._resolve_access_tier("alice")
            if result != "owner":
                fails.append("7. malformed JSON: expected 'owner' fallback, got {!r}".format(result))
            else:
                passed += 1

            # 8. Split-default: tierMap present, sender not in it → "other" (#937)
            # The admin consciously created tierMap for some users. An unlisted
            # sender must NOT silently get owner-tier — that would be silent
            # privilege escalation. Degrade to "other" (fail-safe).
            _set({"allowFrom": ["alice", "bob"], "tierMap": {"bob": "team"}})
            result = bridge._resolve_access_tier("alice")
            if result != "other":
                fails.append("8. split-default: tierMap present, sender missing → expected 'other', got {!r}".format(result))
            else:
                passed += 1

            # 8b. Only when tierMap is absent entirely → "owner" (backward compat)
            _set({"allowFrom": ["alice", "bob"]})  # no tierMap key at all
            result = bridge._resolve_access_tier("alice")
            if result != "owner":
                fails.append("8b. no-tierMap: expected 'owner' (backward compat), got {!r}".format(result))
            else:
                passed += 1

        finally:
            bridge.ACCESS_FILE = orig_access

    # Structural checks: verify the bridge source contains required task-file fields
    bridge_src = (REPO / "src" / "telegram-bridge.py").read_text()

    # 9. access_tier written to task file
    if "access_tier: {access_tier}" not in bridge_src:
        fails.append("9. source: task file must write 'access_tier: {access_tier}'")
    else:
        passed += 1

    # 10. user_id written to task file
    if "user_id: {sender_id}" not in bridge_src:
        fails.append("10. source: task file must write 'user_id: {sender_id}'")
    else:
        passed += 1

    # 11. SUTANDO SYSTEM INSTRUCTIONS injected for non-owner tasks
    if "SUTANDO SYSTEM INSTRUCTIONS" not in bridge_src:
        fails.append("11. source: must inject SUTANDO SYSTEM INSTRUCTIONS for non-owner tiers")
    else:
        passed += 1

    # 12. _resolve_access_tier function exists in the bridge
    if "_resolve_access_tier" not in bridge_src:
        fails.append("12. source: _resolve_access_tier function not found in bridge")
    else:
        passed += 1

    # 13. write_owner_activity gated by access_tier == "owner"
    if 'access_tier == "owner"' not in bridge_src or "write_owner_activity" not in bridge_src:
        fails.append("13. source: write_owner_activity must be gated by access_tier == 'owner'")
    else:
        passed += 1

    # 14. priority passes access_tier (not hardcoded "owner")
    # Ensure the call uses the resolved tier, not "owner" literal
    if 'default_priority_for_source("telegram", "owner")' in bridge_src:
        fails.append(
            "14. source: default_priority_for_source must use resolved access_tier, "
            "not hardcoded 'owner'"
        )
    else:
        passed += 1

    # Summary
    print()
    if fails:
        print("━━━ telegram-bridge access-tier tests: {} FAILED ━━━".format(len(fails)))
        for f in fails:
            print("  ✗ {}".format(f))
        print("\n  {}/{} passed".format(passed, passed + len(fails)))
        return 1
    else:
        print("━━━ telegram-bridge access-tier tests: {} passed / 0 failed ━━━".format(passed))
        return 0


if __name__ == "__main__":
    sys.exit(run())
