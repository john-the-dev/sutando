#!/usr/bin/env python3
"""Regression tests for the report-feedback skill."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "report-feedback" / "report-feedback.py"

spec = importlib.util.spec_from_file_location("report_feedback", SCRIPT)
report_feedback = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(report_feedback)


class TestReportFeedbackRedaction(unittest.TestCase):
    def test_redacts_aws_access_key(self):
        aws_key = "AKIA" + "IOSFODNN7EXAMPLE"
        redacted = report_feedback._redact(f"aws={aws_key}")

        self.assertNotIn(aws_key, redacted)
        self.assertIn("<redacted-token>", redacted)

    def test_redacts_slack_app_token(self):
        token = "xapp-" + "1-" + "A" * 12 + "-" + "B" * 12 + "-" + "C" * 20
        redacted = report_feedback._redact(f"slack app token {token}")

        self.assertNotIn(token, redacted)
        self.assertIn("<redacted-token>", redacted)


class TestReportFeedbackCloudAuth(unittest.TestCase):
    def test_reads_migrated_workspace_auth_before_legacy_root(self):
        with tempfile.TemporaryDirectory() as td:
            ws = Path(td)
            migrated = ws / "state" / "auth" / "cloud-auth.json"
            migrated.parent.mkdir(parents=True)
            migrated.write_text(
                json.dumps({"apiBase": "https://canonical.example", "token": "canonical-token"})
            )
            (ws / "cloud-auth.json").write_text(
                json.dumps({"apiBase": "https://legacy.example", "token": "legacy-token"})
            )

            self.assertEqual(
                report_feedback.read_cloud_auth(ws),
                ("https://canonical.example", "canonical-token"),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
