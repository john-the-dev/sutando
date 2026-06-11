#!/usr/bin/env python3
"""Tests for _filter_pids_this_checkout (PR #1650) in src/health-check.py.

Covers:
  a) argv contains repo path → kept
  b) argv contains sibling-clone path (same suffix) → dropped
  c) argv is empty (ps fails) + cwd matches repo exactly → kept
  d) argv is empty + cwd is subdirectory of repo → kept
  e) argv is empty + cwd is foreign → dropped
  f) ps raises TimeoutExpired, lsof raises TimeoutExpired → fail-open (kept)
  g) ps raises OSError, lsof returns no "n" line + argv empty → fail-open (kept)
  h) argv is non-empty foreign (ps returned), lsof not reached → dropped
  i) empty PID list → empty result
  j) mixed PID list → only own-checkout PIDs returned

Run: python3 tests/health-check-filter-pids.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FAKE_REPO = Path("/home/runner/sutando")

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


def _run_filter(pids: list[str], ps_stdouts: list[str], lsof_stdouts: list[str]) -> list[str]:
    """Call _filter_pids_this_checkout with mocked subprocess.run.

    ps_stdouts: one entry per PID (ps output). Pass "" to simulate empty/failed.
    lsof_stdouts: one entry per PID (lsof output). Pass "" for no "n" line.
    If an entry is None, subprocess.run raises subprocess.TimeoutExpired.
    If an entry is "OSError", subprocess.run raises OSError.
    """
    ps_iter = iter(ps_stdouts)
    lsof_iter = iter(lsof_stdouts)

    def _side_effect(cmd, **kwargs):
        if cmd[0] in ("/bin/ps", "/usr/bin/ps"):
            val = next(ps_iter, "")
            if val is None:
                raise subprocess.TimeoutExpired(cmd, 5)
            if val == "OSError":
                raise OSError("test")
            mock_result = unittest.mock.MagicMock()
            mock_result.stdout = val + "\n" if val else ""
            return mock_result
        if "lsof" in str(cmd[0]):
            val = next(lsof_iter, "")
            if val is None:
                raise subprocess.TimeoutExpired(cmd, 5)
            if val == "FileNotFoundError":
                raise FileNotFoundError("no lsof")
            mock_result = unittest.mock.MagicMock()
            mock_result.stdout = val
            return mock_result
        mock_result = unittest.mock.MagicMock()
        mock_result.stdout = ""
        return mock_result

    orig_repo = hc.REPO_DIR
    try:
        hc.REPO_DIR = FAKE_REPO
        with unittest.mock.patch("subprocess.run", side_effect=_side_effect):
            return hc._filter_pids_this_checkout(pids)
    finally:
        hc.REPO_DIR = orig_repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

# (a) argv contains repo path → kept
result = _run_filter(
    pids=["42"],
    ps_stdouts=[f"node {FAKE_REPO}/src/voice-agent.ts"],
    lsof_stdouts=[""],
)
_check("a: argv contains repo path → kept", result == ["42"])

# (b) argv contains sibling clone (same name suffix, different parent) → dropped
result = _run_filter(
    pids=["43"],
    ps_stdouts=["/home/runner/staging-sutando/src/voice-agent.ts"],
    lsof_stdouts=[""],
)
_check("b: argv from sibling clone → dropped", result == [])

# (b2) argv contains path that IS a substring match but not boundary match → dropped
# e.g., repo=/home/sutando, argv=/home/sutando-staging/src/agent.ts
result = _run_filter(
    pids=["44"],
    ps_stdouts=[f"/home/sutando-staging/src/agent.ts"],
    lsof_stdouts=[""],
)
_check("b2: argv with same prefix but no trailing slash → dropped", result == [])

# (c) ps fails with OSError, lsof shows repo cwd exactly → kept
result = _run_filter(
    pids=["45"],
    ps_stdouts=["OSError"],
    lsof_stdouts=[f"p45\nn{FAKE_REPO}\n"],
)
_check("c: ps OSError + lsof shows repo cwd → kept", result == ["45"])

# (d) ps returns empty, lsof shows subdirectory of repo → kept
result = _run_filter(
    pids=["46"],
    ps_stdouts=[""],
    lsof_stdouts=[f"p46\nn{FAKE_REPO}/src\n"],
)
_check("d: ps empty + lsof shows repo subdir → kept", result == ["46"])

# (e) ps returns empty, lsof shows foreign cwd → dropped
result = _run_filter(
    pids=["47"],
    ps_stdouts=[""],
    lsof_stdouts=["p47\nn/home/runner/other-project\n"],
)
_check("e: ps empty + lsof foreign cwd → dropped", result == [])

# (f) ps raises TimeoutExpired, lsof raises TimeoutExpired → fail-open (kept)
result = _run_filter(
    pids=["48"],
    ps_stdouts=[None],
    lsof_stdouts=[None],
)
_check("f: ps timeout + lsof timeout → fail-open (kept)", result == ["48"])

# (g) ps raises OSError, lsof has no "n" line → argv="" cwd="" → fail-open (kept)
result = _run_filter(
    pids=["49"],
    ps_stdouts=["OSError"],
    lsof_stdouts=["p49\nc4u-\n"],  # only cwd-type "c" lines, no "n" line
)
_check("g: ps error + lsof no n-line → fail-open (kept)", result == ["49"])

# (h) argv is non-empty foreign process, lsof not reached → dropped
result = _run_filter(
    pids=["50"],
    ps_stdouts=["/usr/bin/python3 /home/runner/other-app/worker.py"],
    lsof_stdouts=[""],
)
_check("h: non-empty foreign argv → dropped", result == [])

# (i) empty PID list → empty result
result = _run_filter(pids=[], ps_stdouts=[], lsof_stdouts=[])
_check("i: empty PID list → empty", result == [])

# (j) mixed PID list: own=[42,46], foreign=[43,47]
def _mixed_run():
    """Two own-checkout PIDs (argv + cwd), two foreign PIDs."""
    call_count = {"ps": 0, "lsof": 0}
    # PID 42: own via argv
    # PID 43: foreign argv
    # PID 46: own via cwd
    # PID 47: foreign cwd
    responses = {
        "ps": {
            "42": f"{FAKE_REPO}/src/voice-agent.ts",
            "43": "/home/runner/staging/src/voice-agent.ts",
            "46": "",
            "47": "",
        },
        "lsof": {
            # PID 42 won't reach lsof (already kept)
            "43": "p43\nn/home/runner/staging/src\n",
            "46": f"p46\nn{FAKE_REPO}/src\n",
            "47": "p47\nn/tmp/foreign\n",
        },
    }
    ps_calls = ["42", "43", "46", "47"]
    lsof_calls = ["43", "46", "47"]
    ps_iter = iter(ps_calls)
    lsof_iter = iter(lsof_calls)

    def _side_effect(cmd, **kwargs):
        if cmd[0] in ("/bin/ps", "/usr/bin/ps"):
            pid = next(ps_iter)
            mock_result = unittest.mock.MagicMock()
            mock_result.stdout = responses["ps"][pid]
            return mock_result
        if "lsof" in str(cmd[0]):
            pid = next(lsof_iter)
            mock_result = unittest.mock.MagicMock()
            mock_result.stdout = responses["lsof"][pid]
            return mock_result
        mock_result = unittest.mock.MagicMock()
        mock_result.stdout = ""
        return mock_result

    orig_repo = hc.REPO_DIR
    try:
        hc.REPO_DIR = FAKE_REPO
        with unittest.mock.patch("subprocess.run", side_effect=_side_effect):
            return hc._filter_pids_this_checkout(["42", "43", "46", "47"])
    finally:
        hc.REPO_DIR = orig_repo

result = _mixed_run()
_check("j: mixed PIDs — own [42,46] kept, foreign [43,47] dropped", result == ["42", "46"])

# (k) lsof raises FileNotFoundError (lsof not installed) + ps empty → fail-open
result = _run_filter(
    pids=["51"],
    ps_stdouts=[""],
    lsof_stdouts=["FileNotFoundError"],
)
_check("k: lsof FileNotFoundError + ps empty → fail-open (kept)", result == ["51"])

# (l) argv matches via resolved path (REPO_DIR.resolve() may differ on macOS /private/tmp)
# Simulate /private/tmp vs /tmp symlink scenario
def _resolved_path_test():
    orig_repo = hc.REPO_DIR
    try:
        # Fake: REPO_DIR = /tmp/sutando, REPO_DIR.resolve() = /private/tmp/sutando
        fake_resolved = Path("/private/tmp/sutando")
        hc.REPO_DIR = Path("/tmp/sutando")
        # Patch REPO_DIR.resolve() — must patch the resolve method on the Path object
        # Instead, rely on {str(REPO_DIR), str(REPO_DIR.resolve())} both being tested:
        # Use the _resolved_ form in the argv
        argv_val = f"/private/tmp/sutando/src/voice-agent.ts"
        mock_result = unittest.mock.MagicMock()
        mock_result.stdout = argv_val

        with unittest.mock.patch("subprocess.run", return_value=mock_result):
            # This will only keep if /private/tmp/sutando/ is in repo_forms
            # repo_forms = {"/tmp/sutando", "/tmp/sutando"} if resolve() == REPO_DIR
            # On a real macOS with symlinks it'd have /private/tmp/sutando too
            # Test the fallback: if /tmp/sutando/ not in argv but /private/tmp/sutando/ is
            # The test is: the function uses both str(REPO_DIR) and str(REPO_DIR.resolve())
            # We can't easily force resolve() to return a different value, so just verify
            # the basic argv match works for the non-resolved form
            mock_result.stdout = f"/tmp/sutando/src/voice-agent.ts"
            return hc._filter_pids_this_checkout(["52"])
    finally:
        hc.REPO_DIR = orig_repo

result = _resolved_path_test()
_check("l: argv matches str(REPO_DIR) form → kept", result == ["52"])

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n{'OK' if _failed == 0 else 'FAIL'} — {_passed} passed, {_failed} failed")
sys.exit(0 if _failed == 0 else 1)
