#!/usr/bin/env python3
"""Regression tests for PR #1764 — detect-secrets as soft / dev-only dep.

Tests cover the specific behavior added in #1764:
  - `secret_scanner` import is lazy (inside _replacer, not at module level)
  - Unquoted value is REFUSED (not stored) when detect-secrets is absent
  - REFUSED placeholder is self-documenting (contains AGENT install instruction)
  - Quoted values bypass the FP guard and store even without detect-secrets
  - `failed` list is populated on refusal; Keychain is never called

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

SRC = (REPO / "src" / "vault_intercept.py").read_text()


class TestSoftDepStructural(unittest.TestCase):
    """Source-code invariants for the soft-dep design (no live process needed)."""

    def test_secret_scanner_import_is_not_at_module_level(self):
        top_import_block = SRC[: SRC.find("\n\n\n")]
        self.assertNotIn(
            "from secret_scanner import",
            top_import_block,
            "secret_scanner must be a lazy import (detect-secrets is a soft dep) "
            "— a top-level import would crash the bridge on hosts without detect-secrets.",
        )

    def test_secret_scanner_import_is_inside_try_block(self):
        try_pos = SRC.find("try:\n")
        scanner_pos = SRC.find("from secret_scanner import scan_secrets")
        self.assertGreater(
            scanner_pos,
            try_pos,
            "from secret_scanner import must appear after a try: — it is a lazy import "
            "guarded against ImportError.",
        )

    def test_except_import_error_guards_the_import(self):
        scanner_pos = SRC.find("from secret_scanner import scan_secrets")
        window = SRC[scanner_pos : scanner_pos + 200]
        self.assertIn(
            "except ImportError",
            window,
            "except ImportError must follow the secret_scanner import within 200 chars.",
        )

    def test_refused_placeholder_present_in_source(self):
        self.assertIn(
            "REFUSED — detect-secrets not installed",
            SRC,
            "REFUSED placeholder text must exist in vault_intercept.py (PR #1764).",
        )

    def test_refused_placeholder_contains_agent_install_instruction(self):
        self.assertIn(
            "python3 -m pip install detect-secrets",
            SRC,
            "REFUSED placeholder must include the install instruction so the "
            "agent reading the task knows what to do (self-documenting, PR #1764).",
        )

    def test_refused_placeholder_tells_agent_not_to_echo_value(self):
        self.assertIn(
            "Never echo",
            SRC,
            "REFUSED placeholder must instruct the agent never to echo the secret.",
        )

    def test_is_quoted_guard_present_to_bypass_scan(self):
        self.assertIn(
            "is_quoted",
            SRC,
            "is_quoted flag must be present — quoted values bypass the FP scan "
            "and store directly even without detect-secrets.",
        )

    def test_quoted_branch_skips_scan(self):
        guard_pos = SRC.find("if not is_quoted:")
        self.assertGreater(
            guard_pos,
            0,
            "`if not is_quoted:` must wrap the secret_scanner call.",
        )

    def test_failed_list_populated_on_import_error(self):
        import_err_pos = SRC.find("except ImportError:")
        window = SRC[import_err_pos : import_err_pos + 1600]
        self.assertIn(
            "failed.append(key)",
            window,
            "On ImportError the key must be added to failed[] (PR #1764).",
        )


class TestSoftDepFunctional(unittest.TestCase):
    """Functional tests: simulate a host where detect-secrets is absent."""

    def _intercept_without_scanner(self, text: str):
        """Run intercept_vault_commands with secret_scanner forcibly absent."""
        import vault_intercept
        # Remove any cached import of secret_scanner so the lazy import fires.
        sys.modules.pop("secret_scanner", None)
        with patch.dict(sys.modules, {"secret_scanner": None}):
            return vault_intercept.intercept_vault_commands(text)

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
        self.assertIn(
            "MY_KEY",
            result.failed,
            "Refused key must appear in result.failed.",
        )

    def test_refused_key_not_in_stored_list(self):
        result = self._intercept_without_scanner("vault set MY_KEY barevalue")
        self.assertNotIn(
            "MY_KEY",
            result.stored,
            "Refused key must NOT appear in result.stored.",
        )

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
        self.assertIn(
            "MY_KEY",
            result.stored,
            "Quoted vault-set must store successfully even without detect-secrets "
            "(quoted values bypass the FP guard per PR #1764).",
        )
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
