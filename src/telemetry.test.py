#!/usr/bin/env python3
"""Behavioral tests for telemetry per-install id persistence (the 2026-07-16
DAU-inflation fix).

Proves the actual contract, not source structure: the id survives workspace
churn, migrates an existing legacy id instead of resetting it, and a fresh
install mints one stable id. Run: python3 src/telemetry.test.py
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _fresh_module(durable_file: Path, state_dir: Path):
    """Import telemetry with the id paths pointed at test locations."""
    os.environ["SUTANDO_TELEMETRY_ID_FILE"] = str(durable_file)
    os.environ["SUTANDO_STATE_DIR"] = str(state_dir)
    import telemetry
    importlib.reload(telemetry)
    return telemetry


def test_persists_across_workspace_churn():
    """The whole point: change the workspace/state dir between boots (as a
    desktop update/relaunch does) but keep the durable path — id is unchanged."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        durable = tmp / "appsupport" / "telemetry-id"
        t = _fresh_module(durable, tmp / "ws1" / "state")
        id1 = t._distinct_id()
        assert id1 and id1 != "anonymous", id1
        # Simulate a churned workspace (fresh, empty state dir) on next boot.
        t2 = _fresh_module(durable, tmp / "ws2-CHURNED" / "state")
        id2 = t2._distinct_id()
        assert id2 == id1, f"id changed across workspace churn: {id1} != {id2}"
        print("ok: persists across workspace churn")


def test_migrates_legacy_id_no_reset():
    """An install whose id already persisted under <workspace>/state must NOT be
    reset — the legacy id is adopted into the durable location."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        state = tmp / "state"
        state.mkdir(parents=True)
        (state / "telemetry-id").write_text("legacy-stable-id-abc123")
        durable = tmp / "appsupport" / "telemetry-id"  # does not exist yet
        t = _fresh_module(durable, state)
        got = t._distinct_id()
        assert got == "legacy-stable-id-abc123", got
        # And it was copied to the durable path (source of truth going forward).
        assert durable.read_text().strip() == "legacy-stable-id-abc123"
        print("ok: migrates legacy id without reset")


def test_fresh_install_mints_and_persists():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        durable = tmp / "appsupport" / "telemetry-id"
        t = _fresh_module(durable, tmp / "state")
        a = t._distinct_id()
        b = t._distinct_id()
        assert a == b and len(a) == 32, (a, b)  # uuid4().hex
        assert durable.read_text().strip() == a
        print("ok: fresh install mints one stable id")


def test_durable_path_platform():
    os.environ.pop("SUTANDO_TELEMETRY_ID_FILE", None)
    import telemetry
    importlib.reload(telemetry)
    p = telemetry._durable_id_path()
    assert p.name == "telemetry-id"
    assert "Sutando" in str(p) or "sutando" in str(p), p
    # Never under a workspace/state dir (that was the bug).
    assert "workspace" not in str(p), p
    print(f"ok: durable path is user-level ({p})")


if __name__ == "__main__":
    test_persists_across_workspace_churn()
    test_migrates_legacy_id_no_reset()
    test_fresh_install_mints_and_persists()
    test_durable_path_platform()
    print("\nALL PASS")
