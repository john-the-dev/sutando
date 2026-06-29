"""Tests for scripts/lint-class-rules.py — layer 3 of #1543.

Structural assertions that the lint script:
1. Exists and is executable as a standalone script.
2. Exits 0 when sutando-migrate.sh is absent (no-op guard).
3. Dynamically scans Python/TS source for personal_path callers.
4. Fails correctly when a personal_path file is classified rehome-state.
5. Passes when personal_path files have root-keeping classifications.

Uses synthetic CLASS_RULES strings so the test doesn't depend on
sutando-migrate.sh being present on this branch.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LINT_SCRIPT = REPO / "scripts" / "lint-class-rules.py"
SRC = LINT_SCRIPT.read_text()


# ---------------------------------------------------------------------------
# Structural checks — script exists and has the key functions
# ---------------------------------------------------------------------------

class TestLintClassRulesStructure(unittest.TestCase):

    def test_script_exists(self):
        self.assertTrue(LINT_SCRIPT.exists(), "scripts/lint-class-rules.py must exist")

    def test_parse_class_rules_function_defined(self):
        self.assertIn("def parse_class_rules(", SRC,
                      "parse_class_rules() function must be defined")

    def test_extract_personal_path_args_py_defined(self):
        self.assertIn("def extract_personal_path_args_py(", SRC,
                      "extract_personal_path_args_py() must be defined")

    def test_extract_personal_path_args_ts_defined(self):
        self.assertIn("def extract_personal_path_args_ts(", SRC,
                      "extract_personal_path_args_ts() must be defined")

    def test_run_lint_function_defined(self):
        self.assertIn("def run_lint(", SRC, "run_lint() must be defined")

    def test_rehome_to_state_classes_constant_defined(self):
        self.assertIn("REHOME_TO_STATE_CLASSES", SRC,
                      "REHOME_TO_STATE_CLASSES constant must be defined")
        self.assertIn('"rehome-state"', SRC,
                      "rehome-state must be in REHOME_TO_STATE_CLASSES")

    def test_no_op_when_migrate_sh_absent(self):
        self.assertIn("not found", SRC,
                      "must emit a 'not found' message when migrate.sh is absent")
        self.assertIn("return 0", SRC,
                      "must return 0 (pass) when migrate.sh is absent")

    def test_ci_step_added_to_workflow(self):
        ci_yml = (REPO / ".github" / "workflows" / "ci.yml").read_text()
        self.assertIn("lint-class-rules.py", ci_yml,
                      "CI workflow must include lint-class-rules.py step")


# ---------------------------------------------------------------------------
# Functional tests using the script's internal functions directly
# ---------------------------------------------------------------------------

# Import the lint script's functions by exec-ing it in a namespace
# Provide __file__ so the REPO Path(...) at module level works correctly.
_ns: dict = {"__file__": str(LINT_SCRIPT)}
exec(compile(SRC, str(LINT_SCRIPT), "exec"), _ns)  # noqa: S102
_parse_class_rules = _ns["parse_class_rules"]
_classify_file = _ns["classify_file"]
_extract_personal_path_args_py = _ns["extract_personal_path_args_py"]
_extract_personal_path_args_ts = _ns["extract_personal_path_args_ts"]


class TestParseClassRules(unittest.TestCase):

    def _make_migrate_sh(self, rules: list[str]) -> Path:
        body = "\n".join(f'    "{r}"' for r in rules)
        content = f"""#!/bin/bash
# test script
CLASS_RULES=(
{body}
)
"""
        p = Path(tempfile.mktemp(suffix=".sh"))
        p.write_text(content)
        return p

    def test_parses_simple_rules(self):
        f = self._make_migrate_sh([
            "stand-identity.json|newest-mtime",
            "state/*.json|structural",
            "*|quarantine-unknown",
        ])
        try:
            rules = _parse_class_rules(f)
            self.assertEqual(rules, [
                ("stand-identity.json", "newest-mtime"),
                ("state/*.json", "structural"),
                ("*", "quarantine-unknown"),
            ])
        finally:
            f.unlink(missing_ok=True)

    def test_ignores_comment_lines(self):
        f = self._make_migrate_sh([
            "# this is a comment",
            "stand-identity.json|newest-mtime",
        ])
        try:
            rules = _parse_class_rules(f)
            self.assertEqual(len(rules), 1)
            self.assertEqual(rules[0], ("stand-identity.json", "newest-mtime"))
        finally:
            f.unlink(missing_ok=True)

    def test_classify_first_match_wins(self):
        rules = [
            ("stand-identity.json", "newest-mtime"),
            ("stand-identity.json", "rehome-state"),
            ("*", "quarantine-unknown"),
        ]
        cls = _classify_file("stand-identity.json", rules)
        self.assertEqual(cls, "newest-mtime")

    def test_classify_rehome_state_detected(self):
        rules = [
            ("stand-identity.json", "rehome-state"),
            ("*", "quarantine-unknown"),
        ]
        cls = _classify_file("stand-identity.json", rules)
        self.assertEqual(cls, "rehome-state",
                         "should detect rehome-state classification for personal_path file")


class TestExtractCallers(unittest.TestCase):

    def test_py_extracts_string_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.py"
            f.write_text(
                "from util_paths import personal_path\n"
                'si = personal_path("stand-identity.json")\n'
                'pq = personal_path("pending-questions.md")\n'
            )
            result = _extract_personal_path_args_py(Path(tmp))
            self.assertIn("stand-identity.json", result)
            self.assertIn("pending-questions.md", result)

    def test_py_ignores_variable_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.py"
            f.write_text(
                'personal_path(some_variable)\n'
                'personal_path("literal.json")\n'
            )
            result = _extract_personal_path_args_py(Path(tmp))
            self.assertIn("literal.json", result)
            self.assertNotIn("some_variable", result)

    def test_ts_extracts_string_literal(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "test.ts"
            f.write_text(
                "import { personalPath } from './util_paths.js';\n"
                "const si = personalPath('stand-identity.json');\n"
            )
            result = _extract_personal_path_args_ts(Path(tmp))
            self.assertIn("stand-identity.json", result)


class TestRunLintNoOp(unittest.TestCase):

    def test_exits_zero_on_clean_checkout(self):
        """The script must exit 0 on a clean checkout (PASS or SKIP — never 1)."""
        result = subprocess.run(
            [sys.executable, str(LINT_SCRIPT)],
            capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0,
                         f"lint script must exit 0 on main:\n{result.stdout}\n{result.stderr}")
        passed_or_skipped = "PASS" in result.stdout or "SKIP" in result.stdout
        self.assertTrue(passed_or_skipped,
                        f"output must contain PASS or SKIP on main:\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
