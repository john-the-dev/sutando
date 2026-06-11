#!/usr/bin/env python3
"""Regression guard: read_discovery() and pid_alive() in registry-client.py.

read_discovery():
  Reads <workspace>/state/agent-registry.json and returns the parsed dict,
  or None on FileNotFoundError / JSONDecodeError.

pid_alive(pid):
  Returns True if the given pid is running (os.kill(pid, 0) succeeds or
  raises PermissionError).  Returns True for pid <= 0 ("nothing to watch").
  Returns False for ProcessLookupError or OSError.

Run: python3 tests/agent-registry-client.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "agent-registry" / "scripts"

# Set SUTANDO_WORKSPACE before import so DISCOVERY_PATH resolves inside tmp.
_tmp_ws = tempfile.mkdtemp(prefix="reg-client-test-")
os.makedirs(os.path.join(_tmp_ws, "state"), exist_ok=True)
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws

spec = importlib.util.spec_from_file_location(
    "registry_client", SCRIPTS / "registry-client.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["registry_client"] = _mod
spec.loader.exec_module(_mod)

del os.environ["SUTANDO_WORKSPACE"]

# Use the resolved discovery path from the module.
_DISC = _mod.DISCOVERY_PATH

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


# ---------------------------------------------------------------------------
# read_discovery
# ---------------------------------------------------------------------------

def _write_disc(data: dict) -> None:
    with open(_DISC, "w") as fh:
        json.dump(data, fh)


def _remove_disc() -> None:
    try:
        os.unlink(_DISC)
    except FileNotFoundError:
        pass


def _test_read_discovery():
    # File missing → None
    _remove_disc()
    _check("rd-missing",     _mod.read_discovery() is None)

    # Valid JSON → returns parsed dict
    _write_disc({"host": "127.0.0.1", "port": 7847, "url": "http://127.0.0.1:7847"})
    disc = _mod.read_discovery()
    _check("rd-valid",       disc is not None)
    _check("rd-host",        disc.get("host") == "127.0.0.1")
    _check("rd-port",        disc.get("port") == 7847)
    _check("rd-url",         disc.get("url") == "http://127.0.0.1:7847")

    # Extra fields preserved
    _write_disc({"host": "127.0.0.1", "port": 9000, "pid": 12345, "started_at": 1000.0})
    disc2 = _mod.read_discovery()
    _check("rd-extra-pid",   disc2.get("pid") == 12345)
    _check("rd-extra-ts",    disc2.get("started_at") == 1000.0)

    # Malformed JSON → None
    with open(_DISC, "w") as fh:
        fh.write("{not valid json")
    _check("rd-malformed",   _mod.read_discovery() is None)

    # Empty file → None (empty string is invalid JSON)
    with open(_DISC, "w") as fh:
        fh.write("")
    _check("rd-empty-file",  _mod.read_discovery() is None)

    # Cleanup
    _remove_disc()


_test_read_discovery()


# ---------------------------------------------------------------------------
# pid_alive
# ---------------------------------------------------------------------------

def _test_pid_alive():
    f = _mod.pid_alive

    # pid=0 → True ("nothing to watch")
    _check("pa-zero",    f(0) is True)

    # pid=None → True
    _check("pa-none",    f(None) is True)

    # pid=-1 → True (≤ 0 sentinel)
    _check("pa-neg",     f(-1) is True)

    # pid=-100 → True (≤ 0 sentinel)
    _check("pa-neg100",  f(-100) is True)

    # Current process is alive
    _check("pa-self",    f(os.getpid()) is True)

    # PID 1 (init/launchd on macOS) exists; os.kill(1, 0) raises PermissionError
    # → function returns True (process exists, owned by another user)
    _check("pa-pid1",    f(1) is True)

    # Dead PID: spawn a short-lived process, wait for it, then check its pid
    dead = subprocess.Popen(["true"])
    dead.wait()
    # Give the OS a moment (realistically unnecessary but safe)
    import time; time.sleep(0.05)
    _check("pa-dead",    f(dead.pid) is False,
           f"pid {dead.pid} reported alive after process exited")


_test_pid_alive()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"agent-registry-client: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
