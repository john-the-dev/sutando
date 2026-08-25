#!/usr/bin/env python3
"""Contract for the shared core-runtime writer, plus each launcher's delegation.
The delegation half fails if either launcher grows its own writer back."""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MOD = REPO / "src" / "core_runtime_marker.py"
CLAUDE_CLI = REPO / "src" / "agent" / "claude" / "cli" / "start-cli.sh"
CODEX_CLI = REPO / "src" / "agent" / "codex" / "cli" / "start-cli.sh"

spec = importlib.util.spec_from_file_location("core_runtime_marker", MOD)
crm = importlib.util.module_from_spec(spec)
sys.modules["core_runtime_marker"] = crm
spec.loader.exec_module(crm)


class TestContract(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp())

    def _marker(self):
        return json.loads((self.ws / "state" / "core-runtime.json").read_text())

    def test_writes_both_records(self):
        self.assertTrue(crm.write_marker(self.ws, "claude", "sutando-core"))
        m = self._marker()
        self.assertEqual(m["runtime"], "claude")
        self.assertEqual(m["session"], "sutando-core")
        self.assertIsInstance(m["started_at"], int)
        line = json.loads((self.ws / "state" / "session-starts.log").read_text().splitlines()[-1])
        self.assertEqual(line["runtime"], "claude")
        self.assertEqual(line["source"], "start-cli")

    def test_both_runtimes_share_one_schema(self):
        crm.write_marker(self.ws, "claude", "s")
        claude_keys = set(self._marker())
        ws2 = Path(tempfile.mkdtemp())
        crm.write_marker(ws2, "codex", "s")
        codex_keys = set(json.loads((ws2 / "state" / "core-runtime.json").read_text()))
        self.assertEqual(claude_keys, codex_keys,
                         "the two runtimes must not drift apart in shape again")

    def test_unknown_runtime_is_a_caller_bug(self):
        with self.assertRaises(ValueError):
            crm.write_marker(self.ws, "gpt", "s")
        self.assertFalse((self.ws / "state" / "core-runtime.json").exists(),
                         "a rejected runtime must not publish a value no reader understands")

    def test_marker_is_replaced_atomically(self):
        crm.write_marker(self.ws, "codex", "s")
        crm.write_marker(self.ws, "claude", "s")
        self.assertEqual(self._marker()["runtime"], "claude")
        leftovers = [p.name for p in (self.ws / "state").iterdir()
                     if p.name.startswith(".core-runtime.")]
        self.assertEqual(leftovers, [], f"temp files left behind: {leftovers}")

    def test_log_is_append_only(self):
        crm.write_marker(self.ws, "codex", "s")
        crm.write_marker(self.ws, "claude", "s", "start-cli-heal")
        lines = (self.ws / "state" / "session-starts.log").read_text().splitlines()
        self.assertEqual(len(lines), 2, "each launch must leave its own line")
        self.assertEqual(json.loads(lines[-1])["source"], "start-cli-heal")

    def test_unwritable_workspace_never_raises(self):
        # A launch must not fail because bookkeeping did.
        blocked = Path(tempfile.mkdtemp()) / "ro"
        blocked.mkdir()
        os.chmod(blocked, 0o500)
        try:
            self.assertFalse(crm.write_marker(blocked, "claude", "s"))
        finally:
            os.chmod(blocked, 0o700)

    def test_empty_workspace_is_false_not_a_crash(self):
        self.assertFalse(crm.write_marker("", "claude", "s"))

    def test_failed_replace_leaves_no_temp_file(self):
        # The cleanup exists so a failed publish does not litter state/ with
        # .core-runtime.*.tmp files that no reader understands.
        real = crm.os.replace

        def boom(src, dst):
            raise OSError("simulated replace failure")

        crm.os.replace = boom
        try:
            self.assertFalse(crm.write_marker(self.ws, "claude", "s"))
        finally:
            crm.os.replace = real
        leftovers = [p.name for p in (self.ws / "state").iterdir()
                     if p.name.startswith(".core-runtime.")]
        self.assertEqual(leftovers, [], f"temp file survived a failed replace: {leftovers}")

    def test_cleanup_failure_does_not_mask_the_original_error(self):
        # Both the publish AND its cleanup fail. The original OSError must still
        # surface as a False return rather than being replaced by the unlink's.
        real_replace, real_unlink = crm.os.replace, crm.os.unlink

        def boom(*a, **k):
            raise OSError("simulated failure")

        crm.os.replace = boom
        crm.os.unlink = boom
        try:
            self.assertFalse(crm.write_marker(self.ws, "claude", "s"))
        finally:
            crm.os.replace, crm.os.unlink = real_replace, real_unlink

    def test_marker_lands_even_when_the_log_cannot_be_written(self):
        # Partial failure must be reported, not swallowed: the marker is what
        # readers poll, so it still lands while the return value says not-all-ok.
        (self.ws / "state").mkdir(parents=True)
        (self.ws / "state" / "session-starts.log").mkdir()  # a dir blocks append
        self.assertFalse(crm.write_marker(self.ws, "claude", "s"))
        self.assertEqual(self._marker()["runtime"], "claude")


class TestLauncherDelegation(unittest.TestCase):
    """Neither launcher may write these records itself."""

    def _assert_delegates(self, path: Path, runtime: str):
        src = path.read_text()
        self.assertIn("core_runtime_marker.py", src,
                      f"{path.name} must delegate to the shared writer")
        for record in ("core-runtime.json", "session-starts.log"):
            inline = [ln for ln in src.splitlines()
                      if record in ln and re.search(r"printf|echo|>\s*\"?\$", ln)
                      and "core_runtime_marker" not in ln]
            self.assertEqual(inline, [],
                             f"{path.name} writes {record} inline again: {inline}")

    def test_claude_launcher_delegates(self):
        self._assert_delegates(CLAUDE_CLI, "claude")

    def test_codex_launcher_delegates(self):
        self._assert_delegates(CODEX_CLI, "codex")

    def test_main_returns_in_process(self):
        """Call main() directly, not only through a subprocess.
        A subprocess is invisible to coverage and hides which branch ran."""
        ws = Path(tempfile.mkdtemp())
        self.assertEqual(crm.main(["prog", str(ws), "codex", "sess"]), 0)
        self.assertEqual(crm.main(["prog", "only-a-workspace"]), 2)      # usage
        self.assertEqual(crm.main(["prog", str(ws), "gpt", "sess"]), 2)  # bad runtime
        partial = Path(tempfile.mkdtemp())
        (partial / "state").mkdir()
        (partial / "state" / "session-starts.log").mkdir()
        self.assertEqual(crm.main(["prog", str(partial), "claude", "sess"]), 1)

    def test_cli_entrypoint_works(self):
        ws = Path(tempfile.mkdtemp())
        p = subprocess.run([sys.executable, str(MOD), str(ws), "codex", "sess", "start-cli"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads((ws / "state" / "core-runtime.json").read_text())["runtime"],
                         "codex")

    def test_cli_rejects_unknown_runtime_nonzero(self):
        ws = Path(tempfile.mkdtemp())
        p = subprocess.run([sys.executable, str(MOD), str(ws), "gpt", "sess"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)

    def test_cli_without_enough_args_is_a_usage_error(self):
        p = subprocess.run([sys.executable, str(MOD), "only-a-workspace"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 2)
        self.assertIn("usage:", p.stderr)

    def test_cli_reports_partial_failure_nonzero(self):
        # A launcher ignores this exit code by design, but it must still be honest.
        ws = Path(tempfile.mkdtemp())
        (ws / "state").mkdir()
        (ws / "state" / "session-starts.log").mkdir()
        p = subprocess.run([sys.executable, str(MOD), str(ws), "claude", "sess"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
