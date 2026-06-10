"""
Regression guard for #1381 priority-1: Telegram access-tier support.

Verifies that telegram-bridge.py:
1. Has a load_tier_map() function
2. Resolves tiers correctly from tierMap (with the same logic as slack-bridge)
3. Writes access_tier to task files
4. Injects in-band SYSTEM INSTRUCTIONS for non-owner tiers
5. Guards write_owner_activity to owner-tier only
6. Remains backward-compatible when tierMap is absent
"""

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Source-assertion helpers — avoids importing the whole bridge (Telegram deps)
# ---------------------------------------------------------------------------

SRC_PATH = Path(__file__).resolve().parent.parent / "src" / "telegram-bridge.py"
SRC = SRC_PATH.read_text()


class TestTelegramTierSourceAssertions(unittest.TestCase):
    """Structural checks that the key tier-gate patterns are present."""

    def test_load_tier_map_function_defined(self):
        self.assertIn("def load_tier_map()", SRC,
                      "load_tier_map() function must be defined in telegram-bridge.py")

    def test_load_tier_map_reads_tierMap_key(self):
        self.assertIn('data.get("tierMap")', SRC,
                      "load_tier_map() must read 'tierMap' key from access.json")

    def test_access_tier_field_in_task_file(self):
        self.assertIn('access_tier: {access_tier}', SRC,
                      "task file must include access_tier field")

    def test_in_band_system_instructions_for_non_owner(self):
        self.assertIn("SUTANDO SYSTEM INSTRUCTIONS", SRC,
                      "non-owner task bodies must include in-band SYSTEM INSTRUCTIONS block")
        self.assertIn("codex exec --sandbox read-only", SRC,
                      "SYSTEM INSTRUCTIONS must specify sandboxed codex execution")

    def test_owner_activity_guarded_to_owner_tier(self):
        # The guard must appear: access_tier == "owner" before write_owner_activity
        guard_idx = SRC.index('access_tier == "owner"')
        activity_idx = SRC.index('write_owner_activity("telegram"', guard_idx)
        self.assertGreater(activity_idx, guard_idx,
                           "write_owner_activity must be guarded inside access_tier == 'owner' block")

    def test_priority_passes_access_tier(self):
        self.assertIn('default_priority_for_source("telegram", access_tier)', SRC,
                      "task priority must be resolved with the actual access_tier, not hardcoded 'owner'")

    def test_tier_resolution_fail_safe_for_missing_tiermap_entry(self):
        # When tierMap exists but uid is missing, we must degrade to "other"
        self.assertIn('"other"', SRC,
                      "tier resolution must have an 'other' fallback")
        self.assertIn("tierMap present but uid missing", SRC,
                      "Expected comment documenting fail-safe behavior for missing tierMap entry")

    def test_backward_compat_no_tiermap(self):
        # When no tierMap at all, all allowFrom users are "owner" — backward compat
        self.assertIn('"owner"', SRC,
                      "tier resolution must default to 'owner' when tierMap is absent")


# ---------------------------------------------------------------------------
# Unit tests for load_tier_map() logic via a thin test double
# ---------------------------------------------------------------------------

class TestLoadTierMap(unittest.TestCase):
    """Test load_tier_map() in isolation using a temp access.json."""

    def _make_access_json(self, tmp_dir: str, data: dict) -> Path:
        p = Path(tmp_dir) / "access.json"
        p.write_text(json.dumps(data))
        return p

    def _run_load_tier_map(self, access_file: Path) -> dict:
        """Execute load_tier_map() with ACCESS_FILE pointing at our fixture."""
        # Extract and exec just the function body from the source
        src_lines = SRC.splitlines()
        # Find the function start
        start = next(i for i, l in enumerate(src_lines) if l.strip() == "def load_tier_map() -> dict:")
        # Collect body until next def/class at same indent
        body_lines = []
        for line in src_lines[start:]:
            if line.startswith("def ") or line.startswith("class "):
                if body_lines:
                    break
            body_lines.append(line)
        fn_src = "\n".join(body_lines)
        namespace = {"ACCESS_FILE": access_file, "json": json}
        exec(fn_src, namespace)  # noqa: S102
        return namespace["load_tier_map"]()

    def test_returns_tier_map_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._make_access_json(tmp, {
                "allowFrom": ["111", "222"],
                "tierMap": {"111": "owner", "222": "team"},
            })
            result = self._run_load_tier_map(f)
            self.assertEqual(result, {"111": "owner", "222": "team"})

    def test_returns_empty_dict_when_tiermap_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = self._make_access_json(tmp, {"allowFrom": ["111"]})
            result = self._run_load_tier_map(f)
            self.assertEqual(result, {})

    def test_returns_empty_dict_when_file_missing(self):
        f = Path("/nonexistent/path/access.json")
        result = self._run_load_tier_map(f)
        self.assertEqual(result, {})

    def test_returns_empty_dict_on_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "access.json"
            f.write_text("not json")
            result = self._run_load_tier_map(f)
            self.assertEqual(result, {})


if __name__ == "__main__":
    unittest.main()
