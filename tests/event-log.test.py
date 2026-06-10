#!/usr/bin/env python3
"""Tests for src/event_log.py — structured JSONL event logger.

Covers:
  a) get_log_path() uses date-based filename in the expected format
  b) log_event() writes a valid JSON line with required fields
  c) log_event() strips non-serializable values via repr() instead of raising
  d) log_event() never raises — the caller must never crash due to logging
  e) Multiple events in one session accumulate in JSONL order
  f) fields beyond ts/node/kind are written through as-is

Run: python3 tests/event-log.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("event_log", REPO / "src" / "event_log.py")
_mod = importlib.util.module_from_spec(spec)

# Override LOGS_DIR to a temp dir so no real files are written.
# We patch after loading the module by replacing the LOGS_DIR attribute.
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _with_tmp_logs(fn):
    """Run fn with LOGS_DIR patched to a fresh temp directory."""
    with tempfile.TemporaryDirectory() as td:
        original = _mod.LOGS_DIR
        _mod.LOGS_DIR = Path(td)
        try:
            fn(Path(td))
        finally:
            _mod.LOGS_DIR = original


# ---------------------------------------------------------------------------
# (a) get_log_path — date format
# ---------------------------------------------------------------------------

def _test_get_log_path():
    now_ts = time.time()
    path = _mod.get_log_path(now_ts)
    # Must be <something>/events-YYYY-MM-DD.jsonl
    name = path.name
    _check("log-path-prefix", name.startswith("events-"), f"got {name!r}")
    _check("log-path-suffix", name.endswith(".jsonl"), f"got {name!r}")
    # Date part must be 10 chars: YYYY-MM-DD
    date_part = name[len("events-"):-len(".jsonl")]
    _check("log-path-date-len", len(date_part) == 10, f"got {date_part!r}")
    _check("log-path-date-format", date_part[4] == "-" and date_part[7] == "-", f"got {date_part!r}")

    # Explicit past timestamp
    past = 0.0  # 1970-01-01 local
    past_path = _mod.get_log_path(past)
    _check("log-path-past-contains-1970", "1970" in past_path.name or "1969" in past_path.name,
           f"got {past_path.name!r}")


_test_get_log_path()


# ---------------------------------------------------------------------------
# (b) log_event writes a valid JSON line with required fields
# ---------------------------------------------------------------------------

def _test_basic_write():
    def run(td: Path):
        before = time.time()
        _mod.log_event("test.basic", foo="bar", count=42)
        after = time.time()

        log_path = _mod.get_log_path()
        _check("log-file-created", log_path.exists(), f"path={log_path}")
        lines = log_path.read_text().splitlines()
        _check("log-one-line", len(lines) == 1, f"lines={lines!r}")

        event = json.loads(lines[0])
        _check("log-has-ts", "ts" in event)
        _check("log-ts-in-range", before <= event["ts"] <= after, f"ts={event['ts']}")
        _check("log-has-node", "node" in event and event["node"])
        _check("log-has-kind", event.get("kind") == "test.basic")
        _check("log-extra-fields", event.get("foo") == "bar" and event.get("count") == 42)

    _with_tmp_logs(run)


_test_basic_write()


# ---------------------------------------------------------------------------
# (c) Non-serializable values are repr()'d, not raised
# ---------------------------------------------------------------------------

def _test_non_serializable():
    class _Unserializable:
        def __repr__(self):
            return "<custom-repr>"

    def run(td: Path):
        _mod.log_event("test.unserializable", bad=_Unserializable())
        log_path = _mod.get_log_path()
        event = json.loads(log_path.read_text().splitlines()[-1])
        _check("non-serial-repr", event.get("bad") == "<custom-repr>", f"got {event.get('bad')!r}")

    _with_tmp_logs(run)


_test_non_serializable()


# ---------------------------------------------------------------------------
# (d) log_event never raises — even with pathological inputs
# ---------------------------------------------------------------------------

def _test_never_raises():
    def run(td: Path):
        # These should all succeed without raising
        try:
            _mod.log_event("test.none_value", x=None)
            _mod.log_event("test.empty_kind")
            _mod.log_event("test.nested", data={"a": [1, 2, 3]})
            _mod.log_event("test.unicode", emoji="🤖", cjk="你好")
            _check("never-raises", True)
        except Exception as e:
            _check("never-raises", False, f"raised {e!r}")

    _with_tmp_logs(run)


_test_never_raises()


# ---------------------------------------------------------------------------
# (e) Multiple events accumulate in JSONL order
# ---------------------------------------------------------------------------

def _test_multi_event():
    def run(td: Path):
        _mod.log_event("test.first", seq=1)
        _mod.log_event("test.second", seq=2)
        _mod.log_event("test.third", seq=3)

        log_path = _mod.get_log_path()
        lines = log_path.read_text().splitlines()
        _check("multi-line-count", len(lines) == 3, f"got {len(lines)} lines")
        kinds = [json.loads(l)["kind"] for l in lines]
        _check("multi-order", kinds == ["test.first", "test.second", "test.third"], f"got {kinds!r}")

    _with_tmp_logs(run)


_test_multi_event()


# ---------------------------------------------------------------------------
# (f) JSONL output is valid JSON — each line parses independently
# ---------------------------------------------------------------------------

def _test_valid_jsonl():
    def run(td: Path):
        _mod.log_event("test.jsonl_a")
        _mod.log_event("test.jsonl_b", value=3.14)
        log_path = _mod.get_log_path()
        for i, line in enumerate(log_path.read_text().splitlines()):
            try:
                json.loads(line)
                _check(f"jsonl-line-{i}-valid", True)
            except json.JSONDecodeError as e:
                _check(f"jsonl-line-{i}-valid", False, f"parse error: {e}")

    _with_tmp_logs(run)


_test_valid_jsonl()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"event-log: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
