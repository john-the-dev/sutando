#!/usr/bin/env python3
"""Tests for the trusted-capabilities skill."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "trusted-capabilities"
    / "scripts"
    / "catalog.py"
)
SPEC = importlib.util.spec_from_file_location("trusted_capabilities", SCRIPT)
catalog = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = catalog
SPEC.loader.exec_module(catalog)


class TrustedCapabilitiesTest(unittest.TestCase):
    def setUp(self):
        self.source = catalog.Source("test", "owner/repo", "skills", "skill", True)
        self.entries = [
            {"path": "skills/demo/SKILL.md", "type": "blob", "size": 20},
            {"path": "skills/demo/scripts/run.py", "type": "blob", "size": 12},
        ]
        self.contents = {
            "skills/demo/SKILL.md": b"---\nname: demo\n---\n",
            "skills/demo/scripts/run.py": b"print('ok')\n",
        }

    def test_rejects_untrusted_source_and_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "not trusted"):
            catalog.resolve_source("not-allowlisted")
        with self.assertRaisesRegex(ValueError, "unsafe"):
            catalog.clean_repo_path("../escape")
        with self.assertRaisesRegex(ValueError, "outside trusted root"):
            catalog.source_path(self.source, "scripts/release")

    def test_requires_skill_marker_and_enforces_file_limit(self):
        with self.assertRaisesRegex(ValueError, "SKILL.md"):
            catalog.files_under(
                "skills/demo", [{"path": "skills/demo/README.md", "type": "blob"}]
            )
        oversized = [
            {"path": "skills/demo/SKILL.md", "type": "blob", "size": 1}
        ] + [
            {"path": f"skills/demo/{number}.txt", "type": "blob", "size": 1}
            for number in range(catalog.MAX_FILES)
        ]
        with self.assertRaisesRegex(ValueError, "safety limit"):
            catalog.files_under("skills/demo", oversized)
        self.assertEqual(
            catalog.files_under(
                "src/server",
                [{"path": "src/server/main.py", "type": "blob", "size": 1}],
                require_skill=False,
            )[0]["path"],
            "src/server/main.py",
        )

    def test_atomic_install_records_pinned_provenance_and_replaces(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = root / "demo"
            old.mkdir()
            (old / "old.txt").write_text("old")
            target = catalog.install_skill(
                self.source,
                "skills/demo",
                "abc123",
                self.entries,
                self.contents,
                root,
            )
            self.assertFalse((target / "old.txt").exists())
            self.assertEqual((target / "SKILL.md").read_bytes(), self.contents["skills/demo/SKILL.md"])
            metadata = json.loads((target / catalog.METADATA).read_text())
            self.assertEqual(metadata["commit"], "abc123")
            self.assertEqual(metadata["repo"], "owner/repo")
            self.assertFalse((root / ".demo.previous").exists())

    def test_static_assessment_surfaces_runtime_risks(self):
        findings = catalog.assess(
            self.entries,
            {
                "skills/demo/scripts/run.py": (
                    b"import subprocess\nsubprocess.run(['curl', 'https://example.com'])"
                )
            },
        )
        self.assertIn("contains executable scripts", findings)
        self.assertIn("references network access", findings)
        self.assertIn("can launch commands or evaluate code", findings)


if __name__ == "__main__":
    unittest.main()
