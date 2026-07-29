#!/usr/bin/env python3
"""slack-bridge wedge-detection: the heartbeat must be gated on the LIVE Socket
Mode connection so it goes stale during a wedge (alive-but-deaf), which is what
lets health-check's existing heartbeat-staleness check (Check 3) see it.

Does NOT import the bridge (slack_bolt is a CI-absent dep + the module has
import-time side effects) — mirrors the other slack-bridge tests: source-
structure assertions, plus a behavioral test that exec's the `_socket_connected`
function in isolation against fake handlers. Run: python3 tests/slack-bridge-heartbeat-wedge.test.py
"""
import re
import types
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "src" / "slack-bridge.py").read_text()
passed = []


def check(name, cond, detail=""):
    assert cond, f"FAIL {name}: {detail}"
    passed.append(name)


# --- structure: the three load-bearing properties of the fix ---

# 1. The heartbeat write is GATED on _socket_connected() — an unconditional
#    write would stay fresh through a wedge and hide it (the original bug).
check("heartbeat-gated-on-connection",
      re.search(r"if\s+now\s*-\s*last_heartbeat\s*>=\s*60\s+and\s+_socket_connected\(\)\s*:", SRC) is not None,
      "heartbeat write must be guarded by `and _socket_connected()`")

# 2. _socket_connected() consults the real socket client's is_connected().
check("socket-connected-checks-is_connected",
      re.search(r"def _socket_connected\(\)", SRC) is not None and "is_connected()" in SRC,
      "_socket_connected() must call the client's is_connected()")

# 3. The handler is wired to the module ref BEFORE handler.start() so the
#    heartbeat thread (started earlier) can read live state.
wire = SRC.find("_socket_handler = handler")
# rfind: the real `handler.start()` call is the LAST occurrence — an earlier
# mention lives in a code comment.
start = SRC.rfind("handler.start()")
check("handler-wired-before-start",
      wire != -1 and start != -1 and wire < start,
      "_socket_handler must be set before handler.start()")

# --- behavioral: exec the real _socket_connected source against fakes ---
# Pull the function's source out of the module and exec it standalone, so we
# test the actual code without importing slack_bolt.
m = re.search(r"\ndef _socket_connected\(\)[\s\S]+?\n(?=\S)", SRC)
assert m, "could not locate _socket_connected source"
fn_src = m.group(0)

def run_socket_connected(handler):
    ns = {"_socket_handler": handler}
    exec(fn_src, ns)
    return ns["_socket_connected"]()

class _Client:
    def __init__(self, connected):
        self._c = connected
    def is_connected(self):
        return self._c

# connected socket -> heartbeat allowed
check("connected-true", run_socket_connected(types.SimpleNamespace(client=_Client(True))) is True)
# wedged/disconnected socket -> heartbeat suppressed (goes stale -> detectable)
check("disconnected-false", run_socket_connected(types.SimpleNamespace(client=_Client(False))) is False)
# handler not wired yet (early boot) -> False, no crash
check("no-handler-false", run_socket_connected(None) is False)
# handler present but client missing -> False, no crash
check("no-client-false", run_socket_connected(types.SimpleNamespace(client=None)) is False)
# is_connected raising -> caught, False (never crash the heartbeat thread)
class _Boom:
    def is_connected(self):
        raise RuntimeError("socket state unavailable")
check("is_connected-raises-false", run_socket_connected(types.SimpleNamespace(client=_Boom())) is False)

# --- health-check already has the consuming half (Check 3) — assert it exists,
#     so this fix and that detector stay coupled. ---
HC = (Path(__file__).resolve().parent.parent / "src" / "health-check.py").read_text()
check("health-check-has-heartbeat-staleness-check",
      "heartbeat stale" in HC and re.search(r"\.heartbeat\"?\s*\n?", HC) is not None,
      "health-check must retain its heartbeat-staleness detection (Check 3)")

print(f"OK — {len(passed)} checks passed: {', '.join(passed)}")
