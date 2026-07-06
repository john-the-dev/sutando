#!/usr/bin/env python3
"""Regression tests for PR #1764 — detect-secrets as soft / dev-only dep.

Tests simulate a host where detect-secrets is absent and assert the real
runtime behavior: unquoted values refused, quoted values stored, failed list
populated, Keychain never called.

Run: python3 tests/vault-intercept-soft-dep.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


class TestSoftDepFunctional(unittest.TestCase):
    """Functional tests: simulate a host where detect-secrets is absent."""

    def _intercept_without_scanner(self, text: str):
        """Run intercept_vault_commands with secret_scanner forcibly absent."""
        import vault_intercept
        sys.modules.pop("secret_scanner", None)
        with patch.dict(sys.modules, {"secret_scanner": None}):
            return vault_intercept.intercept_vault_commands(text)

    def test_module_loads_without_detect_secrets(self):
        """vault_intercept must import successfully even when detect-secrets is absent."""
        import importlib
        import vault_intercept
        sys.modules.pop("secret_scanner", None)
        with patch.dict(sys.modules, {"secret_scanner": None}):
            # Reload to exercise the lazy-import path, not a cached top-level import.
            importlib.reload(vault_intercept)

    def test_unquoted_value_refused_when_scanner_missing(self):
        result = self._intercept_without_scanner("vault set MY_KEY barevalue")
        self.assertIn(
            "REFUSED",
            result.text,
            "Unquoted vault-set must be REFUSED when detect-secrets is missing.",
        )

    def test_refused_text_contains_agent_instruction(self):
        result = self._intercept_without_scanner("vault set MY_KEY barevalue")
        self.assertIn(
            "detect-secrets not installed",
            result.text,
            "REFUSED placeholder must explain WHY the store was refused.",
        )

    def test_refused_key_in_failed_list(self):
        result = self._intercept_without_scanner("vault set MY_KEY barevalue")
        self.assertIn("MY_KEY", result.failed)

    def test_refused_key_not_in_stored_list(self):
        result = self._intercept_without_scanner("vault set MY_KEY barevalue")
        self.assertNotIn("MY_KEY", result.stored)

    def test_keychain_not_called_on_refusal(self):
        import vault_intercept
        sys.modules.pop("secret_scanner", None)
        with patch.dict(sys.modules, {"secret_scanner": None}), \
             patch("vault_intercept.subprocess.run") as mock_run:
            vault_intercept.intercept_vault_commands("vault set MY_KEY barevalue")
        mock_run.assert_not_called()

    def test_quoted_value_stored_without_scanner(self):
        import vault_intercept
        sys.modules.pop("secret_scanner", None)
        with patch.dict(sys.modules, {"secret_scanner": None}), \
             patch("vault_intercept.subprocess.run", return_value=MagicMock(returncode=0)), \
             patch.object(vault_intercept, "_register_key"):
            result = vault_intercept.intercept_vault_commands(
                'vault set MY_KEY "quoted secret value"'
            )
        self.assertIn("MY_KEY", result.stored)
        self.assertIn("[STORED-IN-KEYCHAIN]", result.text)

    def test_refused_placeholder_not_raw_value(self):
        result = self._intercept_without_scanner("vault set MY_KEY supersecret")
        self.assertNotIn(
            "supersecret",
            result.text,
            "The raw value must never appear in the sanitized text, even on refusal.",
        )

    def test_multiple_unquoted_refused(self):
        result = self._intercept_without_scanner(
            "vault set A first\nvault set B second"
        )
        self.assertIn("A", result.failed)
        self.assertIn("B", result.failed)
        self.assertNotIn("first", result.text)
        self.assertNotIn("second", result.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
