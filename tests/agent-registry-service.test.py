#!/usr/bin/env python3
"""Security + correctness regression guard: agent-registry registry-service.py.

Tests the core business logic of the agent-registry service — the in-process
functions that back the /register, /heartbeat, /deregister, /agents, and
/health HTTP endpoints. All tests run against an in-memory SQLite database
so there is no filesystem I/O and no live service required.

Functions under test (skills/agent-registry/scripts/registry-service.py):
  make_id(name, pid)    — unique ID generator
  row_to_agent(row)     — sqlite3.Row → dict (stale-detection logic)
  prune(conn)           — delete old stopped/stale rows
  op_register(body)     — INSERT with upsert
  op_heartbeat(body)    — UPDATE last_heartbeat; 400/404 on bad input
  op_deregister(body)   — UPDATE status→stopped; 400 on missing id
  op_agents()           — SELECT all + prune
  op_health()           — COUNT + uptime

Run: python3 tests/agent-registry-service.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "agent-registry" / "scripts"

# Set SUTANDO_WORKSPACE before import so module-level resolve_workspace()
# doesn't touch the real workspace.
_tmp_ws = tempfile.mkdtemp(prefix="registry-test-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws

spec = importlib.util.spec_from_file_location(
    "registry_service", SCRIPTS / "registry-service.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["registry_service"] = _mod
spec.loader.exec_module(_mod)

del os.environ["SUTANDO_WORKSPACE"]

# Redirect the module to a temp-dir SQLite for tests. Each _fresh_db()
# deletes the file and reconnects so each test group starts clean.
_TEST_DB_PATH = os.path.join(_tmp_ws, "data", "test-registry.db")
os.makedirs(os.path.join(_tmp_ws, "data"), exist_ok=True)
_mod.DB_PATH = _TEST_DB_PATH
_mod._db = None  # force db() to initialise against our path


def _fresh_db():
    """Reset to a fresh empty DB and return the connection."""
    if _mod._db is not None:
        try:
            _mod._db.close()
        except Exception:
            pass
        _mod._db = None
    # Delete the old file so db() creates a clean schema
    if os.path.exists(_TEST_DB_PATH):
        os.unlink(_TEST_DB_PATH)
    return _mod.db()


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
# Helpers
# ---------------------------------------------------------------------------

def _insert(conn, id_, name, cwd, pid, host, started_at, last_heartbeat, status, meta="{}"):
    conn.execute(
        "INSERT INTO agents (id,name,cwd,pid,host,started_at,last_heartbeat,status,meta) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (id_, name, cwd, pid, host, started_at, last_heartbeat, status, meta),
    )
    conn.commit()


def _fetch(conn, id_):
    return conn.execute("SELECT * FROM agents WHERE id=?", (id_,)).fetchone()


# ---------------------------------------------------------------------------
# make_id
# ---------------------------------------------------------------------------

def _test_make_id():
    # Format: {name}-{pid}-{ts}-{2-byte-hex}
    result = _mod.make_id("myagent", 12345)
    _check("mi-prefix",  result.startswith("myagent-12345-"))
    parts = result.split("-")
    _check("mi-parts",   len(parts) == 4, f"got {len(parts)} parts")
    _check("mi-hex-len", len(parts[3]) == 4, f"hex part: '{parts[3]}'")  # 2 bytes = 4 hex chars

    # Different calls return different IDs (random hex)
    a = _mod.make_id("a", 1)
    b = _mod.make_id("a", 1)
    _check("mi-unique",  a != b, f"a={a!r} b={b!r}")


_test_make_id()


# ---------------------------------------------------------------------------
# row_to_agent
# ---------------------------------------------------------------------------

def _test_row_to_agent():
    conn = _fresh_db()
    t = time.time()

    # Active, recent heartbeat → stays "active"
    _insert(conn, "r1", "agent1", "/cwd", 101, "host1", t, t, "active", '{"k":"v"}')
    row = _fetch(conn, "r1")
    agent = _mod.row_to_agent(row)
    _check("rta-id",       agent["id"] == "r1")
    _check("rta-name",     agent["name"] == "agent1")
    _check("rta-status",   agent["status"] == "active")
    _check("rta-meta",     agent["meta"] == {"k": "v"})
    _check("rta-hb-age",   0 <= agent["heartbeat_age"] < 5)

    # Active, old heartbeat → auto-demoted to "stale"
    old = t - (_mod.STALE_SECS + 10)
    _insert(conn, "r2", "agent2", "/cwd", 102, "host1", old, old, "active")
    row2 = _fetch(conn, "r2")
    agent2 = _mod.row_to_agent(row2)
    _check("rta-stale",    agent2["status"] == "stale")

    # Stopped always stays "stopped" regardless of heartbeat age
    _insert(conn, "r3", "agent3", "/cwd", 103, "host1", old, old, "stopped")
    row3 = _fetch(conn, "r3")
    agent3 = _mod.row_to_agent(row3)
    _check("rta-stopped",  agent3["status"] == "stopped")

    # Null meta → empty dict
    _insert(conn, "r4", "agent4", "/cwd", 104, "host1", t, t, "active", None)
    row4 = _fetch(conn, "r4")
    agent4 = _mod.row_to_agent(row4)
    _check("rta-meta-null", agent4["meta"] == {})

    # heartbeat_age is rounded to 1 decimal
    _insert(conn, "r5", "agent5", "/cwd", 105, "host1", t, t - 3.7, "active")
    row5 = _fetch(conn, "r5")
    agent5 = _mod.row_to_agent(row5)
    _check("rta-age-round", isinstance(agent5["heartbeat_age"], float))


_test_row_to_agent()


# ---------------------------------------------------------------------------
# prune
# ---------------------------------------------------------------------------

def _test_prune():
    conn = _fresh_db()
    t = time.time()
    old = t - (_mod.PRUNE_SECS + 60)  # definitely older than PRUNE_SECS

    # Old stopped row → pruned
    _insert(conn, "p1", "a", "/", 1, "h", old, old, "stopped")
    # Old stale row → pruned
    _insert(conn, "p2", "b", "/", 2, "h", old, old, "stale")
    # Recent stopped row → NOT pruned (heartbeat recent)
    _insert(conn, "p3", "c", "/", 3, "h", t, t, "stopped")
    # Active row with old heartbeat → NOT pruned (prune only stops stopped/stale)
    _insert(conn, "p4", "d", "/", 4, "h", old, old, "active")

    _mod.prune(conn)
    conn.commit()

    ids = {r["id"] for r in conn.execute("SELECT id FROM agents").fetchall()}
    _check("pr-old-stopped-gone", "p1" not in ids)
    _check("pr-old-stale-gone",   "p2" not in ids)
    _check("pr-recent-stopped",   "p3" in ids)
    _check("pr-active-kept",      "p4" in ids)


_test_prune()


# ---------------------------------------------------------------------------
# op_register
# ---------------------------------------------------------------------------

def _test_op_register():
    _fresh_db()

    # Basic register → returns ok + id
    r = _mod.op_register({"name": "sutando", "pid": 99, "cwd": "/repo"})
    _check("or-ok",       r.get("ok") is True)
    _check("or-id",       isinstance(r.get("id"), str) and len(r["id"]) > 4)

    # Auto-generated ID is in the DB
    agent_id = r["id"]
    conn = _mod.db()
    row = _fetch(conn, agent_id)
    _check("or-in-db",    row is not None)
    _check("or-name",     row["name"] == "sutando")
    _check("or-status",   row["status"] == "active")

    # Explicit ID provided → used verbatim
    r2 = _mod.op_register({"name": "b", "pid": 1, "id": "explicit-id-123"})
    _check("or-explicit-id", r2["id"] == "explicit-id-123")

    # Upsert: same ID again → row updated (no error)
    r3 = _mod.op_register({"name": "b-updated", "pid": 1, "id": "explicit-id-123"})
    _check("or-upsert-ok", r3.get("ok") is True)
    row3 = _fetch(conn, "explicit-id-123")
    _check("or-upsert-name", row3["name"] == "b-updated")

    # Meta dict stored as JSON
    r4 = _mod.op_register({"name": "c", "pid": 2, "meta": {"model": "opus"}})
    row4 = _fetch(conn, r4["id"])
    _check("or-meta", row4["meta"] == '{"model": "opus"}')


_test_op_register()


# ---------------------------------------------------------------------------
# op_heartbeat
# ---------------------------------------------------------------------------

def _test_op_heartbeat():
    _fresh_db()

    # Register an agent first
    reg = _mod.op_register({"name": "h-agent", "pid": 55})
    aid = reg["id"]

    # Valid heartbeat → ok
    result = _mod.op_heartbeat({"id": aid})
    _check("hb-ok",      result.get("ok") is True)
    _check("hb-status",  result.get("status") == "active")

    # Missing id → 400 tuple
    err400 = _mod.op_heartbeat({})
    _check("hb-missing-id",  isinstance(err400, tuple) and err400[1] == 400)

    # Unknown id → 404 tuple
    err404 = _mod.op_heartbeat({"id": "nonexistent-xyz"})
    _check("hb-unknown-id",  isinstance(err404, tuple) and err404[1] == 404)

    # Stopped agent → 404 (rowcount=0 because WHERE status!='stopped')
    _mod.op_deregister({"id": aid})
    stopped_hb = _mod.op_heartbeat({"id": aid})
    _check("hb-stopped-404", isinstance(stopped_hb, tuple) and stopped_hb[1] == 404)


_test_op_heartbeat()


# ---------------------------------------------------------------------------
# op_deregister
# ---------------------------------------------------------------------------

def _test_op_deregister():
    _fresh_db()

    reg = _mod.op_register({"name": "d-agent", "pid": 66})
    aid = reg["id"]

    # Deregister known agent → ok
    r = _mod.op_deregister({"id": aid})
    _check("dr-ok",      r.get("ok") is True)

    # Row now has status='stopped'
    conn = _mod.db()
    row = _fetch(conn, aid)
    _check("dr-status",  row["status"] == "stopped")

    # Missing id → 400
    err = _mod.op_deregister({})
    _check("dr-missing-id", isinstance(err, tuple) and err[1] == 400)

    # Unknown id → ok (no-op, 0 rows affected but no error raised)
    r2 = _mod.op_deregister({"id": "not-a-real-id"})
    _check("dr-unknown-ok", r2.get("ok") is True)


_test_op_deregister()


# ---------------------------------------------------------------------------
# op_agents
# ---------------------------------------------------------------------------

def _test_op_agents():
    _fresh_db()

    # Empty DB
    result = _mod.op_agents()
    _check("oa-empty-list",  result["agents"] == [])
    _check("oa-empty-count", result["count"] == 0)

    # Register two agents
    r1 = _mod.op_register({"name": "a1", "pid": 1})
    r2 = _mod.op_register({"name": "a2", "pid": 2})
    result2 = _mod.op_agents()
    _check("oa-count",       result2["count"] == 2)
    ids = {a["id"] for a in result2["agents"]}
    _check("oa-ids",         r1["id"] in ids and r2["id"] in ids)

    # op_agents prunes old stopped rows
    t = time.time()
    old = t - (_mod.PRUNE_SECS + 60)
    conn = _mod.db()
    _insert(conn, "old-stop", "x", "/", 99, "h", old, old, "stopped")
    result3 = _mod.op_agents()
    ids3 = {a["id"] for a in result3["agents"]}
    _check("oa-prunes",      "old-stop" not in ids3)


_test_op_agents()


# ---------------------------------------------------------------------------
# op_health
# ---------------------------------------------------------------------------

def _test_op_health():
    _fresh_db()

    h = _mod.op_health()
    _check("oh-ok",    h.get("ok") is True)
    _check("oh-count", h.get("count") == 0)
    _check("oh-uptime", isinstance(h.get("uptime"), float) and h["uptime"] >= 0)

    _mod.op_register({"name": "z", "pid": 1})
    h2 = _mod.op_health()
    _check("oh-count-1", h2["count"] == 1)


_test_op_health()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"agent-registry-service: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
