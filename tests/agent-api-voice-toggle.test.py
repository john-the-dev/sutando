#!/usr/bin/env python3
"""Coverage test for the _voice_state_lock-guarded /voice/toggle endpoint.

Exercises the `with _voice_state_lock:` block added in #1975 so the
diff-coverage gate passes.  Follows the main-thread-server pattern
from agent-api-delegation.test.py: coverage tracer only records
execution that happens on the traced (main) thread, so the handler
runs on main while requests come from a worker thread.

Run: python3 tests/agent-api-voice-toggle.test.py
"""
import http.server
import importlib.util
import json
import sys
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


api = _load("agent_api", REPO / "src" / "agent-api.py")

# No API_TOKEN → check_auth() returns True unconditionally (local-use path).
api.API_TOKEN = ""
# Reset voice state to known starting point.
api.voice_desired_state = "disconnected"

server = http.server.HTTPServer(("127.0.0.1", 0), api.Handler)
server.timeout = 0.5
port = server.server_address[1]
BASE = f"http://127.0.0.1:{port}"

failures = []


def check(name, cond, detail=""):
    print(("  ok  " if cond else "  FAIL ") + name + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


def _raw_req(method, path, body=None):
    r = urllib.request.Request(f"{BASE}{path}", method=method,
                               data=None if body is None else json.dumps(body).encode())
    r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload
    except Exception as e:
        return -1, {"error": repr(e)}


def req(method, path, body=None):
    """Issue request from worker thread; serve on main thread (coverage-traced)."""
    out = {}
    t = threading.Thread(target=lambda: out.update(
        zip(("code", "data"), _raw_req(method, path, body))), daemon=True)
    t.start()
    while t.is_alive():
        server.handle_request()
    t.join()
    return out["code"], out["data"]


# 1. GET /voice/state returns initial "disconnected".
code, data = req("GET", "/voice/state")
check("initial /voice/state is disconnected",
      code == 200 and data.get("state") == "disconnected", str(data))

# 2. POST /voice/toggle — exercises the with _voice_state_lock: block.
code, data = req("POST", "/voice/toggle")
check("first toggle: 200", code == 200, str(data))
check("first toggle: state is connected", data.get("state") == "connected", str(data))

# 3. Verify via /voice/state.
code, data = req("GET", "/voice/state")
check("/voice/state agrees: connected", code == 200 and data.get("state") == "connected", str(data))

# 4. Toggle back — exercises the else branch of the conditional.
code, data = req("POST", "/voice/toggle")
check("second toggle: 200", code == 200, str(data))
check("second toggle: state is disconnected", data.get("state") == "disconnected", str(data))

# 5. Verify lock is still functional (module-level object persists).
check("_voice_state_lock is a Lock",
      hasattr(api, "_voice_state_lock") and hasattr(api._voice_state_lock, "acquire"))

server.server_close()

if failures:
    print(f"\n{len(failures)} test(s) FAILED: {failures}")
    sys.exit(1)
print("\nAll tests passed.")
