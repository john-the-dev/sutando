#!/usr/bin/env python3
"""Tests for scripts/dedup-conversation-store.py — _table_exists/_count_dupes/_delete_dupes."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent

# Patch subprocess before loading so _live_processes doesn't call pgrep
import unittest.mock
with unittest.mock.patch("subprocess.check_output", return_value=""), \
     unittest.mock.patch("subprocess.run", return_value=unittest.mock.MagicMock(returncode=1, stdout="")):
    spec = importlib.util.spec_from_file_location(
        "dedup_conversation_store", _REPO / "scripts" / "dedup-conversation-store.py"
    )
    _mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["dedup_conversation_store"] = _mod
    spec.loader.exec_module(_mod)  # type: ignore[union-attr]

_table_exists = _mod._table_exists
_count_dupes = _mod._count_dupes
_delete_dupes = _mod._delete_dupes

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


def _make_db() -> sqlite3.Connection:
    return sqlite3.connect(":memory:")


# ---------------------------------------------------------------------------
# _table_exists
# ---------------------------------------------------------------------------

db = _make_db()
_check("missing table → False", not _table_exists(db, "no_such_table"))

db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
_check("existing table → True", _table_exists(db, "messages"))
_check("other table still missing → False", not _table_exists(db, "sessions"))

db.execute("CREATE TABLE sessions (id TEXT, ts REAL)")
_check("second table → True", _table_exists(db, "sessions"))
db.close()

# ---------------------------------------------------------------------------
# _count_dupes
# ---------------------------------------------------------------------------

db = _make_db()
db.execute("CREATE TABLE turns (session_id TEXT, role TEXT, content TEXT)")
db.executemany("INSERT INTO turns VALUES (?,?,?)", [
    ("s1", "user", "hello"),
    ("s1", "user", "hello"),   # dup of row above (same session_id+role+content)
    ("s1", "asst", "hi"),
    ("s2", "user", "world"),
])

n = _count_dupes(db, "turns", ["session_id", "role", "content"])
_check("2 identical rows → 1 dup", n == 1)

# No dupes
db2 = _make_db()
db2.execute("CREATE TABLE t (a TEXT, b TEXT)")
db2.executemany("INSERT INTO t VALUES (?,?)", [("x","1"), ("x","2"), ("y","1")])
_check("no dupes → 0", _count_dupes(db2, "t", ["a", "b"]) == 0)

# All rows are dupes of the first
db3 = _make_db()
db3.execute("CREATE TABLE t (a TEXT)")
db3.executemany("INSERT INTO t VALUES (?)", [("x",), ("x",), ("x",)])
_check("3 identical rows → 2 dupes", _count_dupes(db3, "t", ["a"]) == 2)

db.close(); db2.close(); db3.close()

# ---------------------------------------------------------------------------
# _delete_dupes
# ---------------------------------------------------------------------------

# One dup in a two-col key
db = _make_db()
db.execute("CREATE TABLE turns (session_id TEXT, role TEXT, content TEXT)")
db.executemany("INSERT INTO turns VALUES (?,?,?)", [
    ("s1", "user", "hello"),
    ("s1", "user", "hello"),   # dup
    ("s1", "asst", "hi"),
])
deleted = _delete_dupes(db, "turns", ["session_id", "role", "content"])
_check("_delete_dupes returns 1 for one dup", deleted == 1)
_check("remaining rows = 2 after delete", db.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2)

# Single-col key, multiple dupes
db = _make_db()
db.execute("CREATE TABLE t (key TEXT)")
db.executemany("INSERT INTO t VALUES (?)", [("a",), ("a",), ("a",), ("b",)])
deleted = _delete_dupes(db, "t", ["key"])
_check("3×a → deletes 2", deleted == 2)
_check("2 rows remain (one a + one b)", db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2)

# No dupes → deletes nothing
db = _make_db()
db.execute("CREATE TABLE t (a TEXT, b TEXT)")
db.executemany("INSERT INTO t VALUES (?,?)", [("x","1"), ("y","2")])
_check("no dupes → 0 deleted", _delete_dupes(db, "t", ["a", "b"]) == 0)
_check("rows unchanged when no dupes", db.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2)

db.close()

# Round-trip: count then delete clears all dupes
db = _make_db()
db.execute("CREATE TABLE msgs (id TEXT, body TEXT)")
db.executemany("INSERT INTO msgs VALUES (?,?)", [
    ("m1","hello"), ("m1","hello"), ("m2","world"), ("m2","world"), ("m2","world"),
])
before = _count_dupes(db, "msgs", ["id", "body"])
_check("round-trip before → 3 dupes", before == 3)
_delete_dupes(db, "msgs", ["id", "body"])
after = _count_dupes(db, "msgs", ["id", "body"])
_check("round-trip after → 0 dupes", after == 0)
_check("round-trip: 2 rows remain", db.execute("SELECT COUNT(*) FROM msgs").fetchone()[0] == 2)
db.close()

print(f"dedup-conversation-store: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
