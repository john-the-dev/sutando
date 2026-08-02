#!/usr/bin/env python3
"""Tests for src/core_heartbeat.py — per-host liveness signal.

Run: python3 tests/core-heartbeat.test.py
Exit: 0 on pass, 1 on fail.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def _short_host() -> str:
    return socket.gethostname().split(".")[0]


class TestHeartbeatWrite(unittest.TestCase):
    def setUp(self):
        self._saved_env = os.environ.get("SUTANDO_WORKSPACE")
        # Pin the host label to the short hostname so the .alive filename these
        # tests construct via _short_host() matches what _host_label() resolves
        # to. Without this, on macOS _host_label() prefers scutil LocalHostName
        # (e.g. `Qingyuns-MacBook-Pro-2200`) while _short_host() is the DHCP
        # short name (`QingyunsMBP2200`) — the #1745 drift — and the tests
        # look for the wrong file. CI (Linux, no scutil) already matched; this
        # makes the suite deterministic on drifting hosts too.
        self._saved_label = os.environ.get("SUTANDO_HOST_LABEL")
        os.environ["SUTANDO_HOST_LABEL"] = _short_host()
        self.tmp = Path(tempfile.mkdtemp(prefix="core-heartbeat-"))
        os.environ["SUTANDO_WORKSPACE"] = str(self.tmp)
        os.environ["SUTANDO_TEST_MODE"] = "1"  # v0.8: opt-in env-honor
        # Force re-import so module picks up the new env.
        sys.modules.pop("core_heartbeat", None)

    def tearDown(self):
        if self._saved_env is not None:
            os.environ["SUTANDO_WORKSPACE"] = self._saved_env
        elif "SUTANDO_WORKSPACE" in os.environ:
            del os.environ["SUTANDO_WORKSPACE"]
        if self._saved_label is not None:
            os.environ["SUTANDO_HOST_LABEL"] = self._saved_label
        else:
            os.environ.pop("SUTANDO_HOST_LABEL", None)
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        sys.modules.pop("core_heartbeat", None)

    def test_write_beat_creates_per_host_file(self):
        import core_heartbeat
        core_heartbeat.write_beat()
        alive_path = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        self.assertTrue(alive_path.is_file(), f"expected {alive_path} to exist")

    def test_write_beat_payload_schema(self):
        import core_heartbeat
        core_heartbeat.write_beat(status="custom-status")
        data = json.loads((self.tmp / "state" / "cores" / f"{_short_host()}.alive").read_text())
        # Required fields
        self.assertEqual(data["host"], _short_host())
        self.assertEqual(data["pid"], os.getpid())
        self.assertEqual(data["status"], "custom-status")
        self.assertEqual(data["schema_version"], 2)
        # locality (Track 10): {kind, host}, self-reported. Default kind=local.
        self.assertEqual(data["locality"], {"kind": "local", "host": _short_host()})
        # socket: the runtime-authored tmux socket the core runs on. Consumed by
        # `sutando-config.sh runtime` so the AgentRuntime descriptor reports the
        # real socket (incl. custom sockets) independent of a caller's env.
        self.assertEqual(
            data["socket"],
            os.environ.get("SUTANDO_TMUX_SOCKET", "/tmp/sutando-tmux.sock"),
        )
        self.assertIsInstance(data["started_at"], float)
        self.assertIsInstance(data["last_beat_at"], float)
        # last_beat_at advances after a sleep; just sanity-check it's recent.
        self.assertLess(abs(time.time() - data["last_beat_at"]), 5)

    def test_locality_kind_from_env(self):
        """Track 10: `kind` self-reports from $SUTANDO_CORE_LOCALITY — `cloud`
        for the spawn-user-core template, defaulting to `local` for a normal
        launch, and clamping any unrecognized value back to `local`."""
        import core_heartbeat
        path = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        saved = os.environ.get("SUTANDO_CORE_LOCALITY")
        try:
            # cloud: explicit template value
            os.environ["SUTANDO_CORE_LOCALITY"] = "cloud"
            core_heartbeat.write_beat()
            self.assertEqual(json.loads(path.read_text())["locality"]["kind"], "cloud")
            # case/whitespace tolerant
            os.environ["SUTANDO_CORE_LOCALITY"] = "  Cloud  "
            core_heartbeat.write_beat()
            self.assertEqual(json.loads(path.read_text())["locality"]["kind"], "cloud")
            # unrecognized value clamps to local (fail toward the safe case)
            os.environ["SUTANDO_CORE_LOCALITY"] = "bogus"
            core_heartbeat.write_beat()
            self.assertEqual(json.loads(path.read_text())["locality"]["kind"], "local")
            # unset → local
            del os.environ["SUTANDO_CORE_LOCALITY"]
            core_heartbeat.write_beat()
            self.assertEqual(json.loads(path.read_text())["locality"]["kind"], "local")
        finally:
            if saved is not None:
                os.environ["SUTANDO_CORE_LOCALITY"] = saved
            else:
                os.environ.pop("SUTANDO_CORE_LOCALITY", None)

    def test_write_beat_is_atomic_via_tmp(self):
        """The .alive write goes through .alive.tmp then renames into place —
        a concurrent reader at the destination path never sees a half-file."""
        import core_heartbeat
        core_heartbeat.write_beat()
        alive = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        tmp = self.tmp / "state" / "cores" / f"{_short_host()}.alive.tmp"
        self.assertTrue(alive.exists())
        self.assertFalse(tmp.exists(), "tmp file should have been renamed away")

    def test_write_beat_overwrites_on_second_call(self):
        import core_heartbeat
        core_heartbeat.write_beat(status="first")
        path = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        first_data = json.loads(path.read_text())
        time.sleep(0.01)
        core_heartbeat.write_beat(status="second")
        second_data = json.loads(path.read_text())
        self.assertEqual(second_data["status"], "second")
        # started_at should NOT change — it's set at module import.
        self.assertEqual(first_data["started_at"], second_data["started_at"])
        # last_beat_at should advance.
        self.assertGreater(second_data["last_beat_at"], first_data["last_beat_at"])

    def test_write_beat_creates_cores_dir(self):
        """The cores/ dir must be created if it doesn't yet exist — fresh
        install case."""
        import core_heartbeat
        cores_dir = self.tmp / "state" / "cores"
        self.assertFalse(cores_dir.exists())
        core_heartbeat.write_beat()
        self.assertTrue(cores_dir.is_dir())


class TestHeartbeatCli(unittest.TestCase):
    """End-to-end tests that exercise the script via subprocess so the CLI
    parsing, signal handling, and cleanup paths are covered."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="core-heartbeat-cli-"))
        # SUTANDO_TEST_MODE: post-v0.8 the resolver ignores $SUTANDO_WORKSPACE
        # unless this test-only escape hatch is set (mirrors line 30 in the
        # in-process fixture above — the subprocess env doesn't inherit it).
        # Pin SUTANDO_HOST_LABEL into the SUBPROCESS env for the same reason as
        # the in-process fixture (line 36): the child's _host_label() prefers
        # scutil LocalHostName on macOS, so without this the child writes
        # `<scutil-label>.alive` while these tests assert `<short-host>.alive`
        # — the #1745 drift, and both CLI cases fail locally. CI/Linux (no
        # scutil) matched already; this makes the subprocess path deterministic
        # on drifting hosts too.
        self.env = {**os.environ, "SUTANDO_WORKSPACE": str(self.tmp),
                    "SUTANDO_TEST_MODE": "1",
                    "SUTANDO_HOST_LABEL": _short_host()}

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_once_flag_writes_single_beat_and_exits(self):
        script = ROOT / "src" / "core_heartbeat.py"
        result = subprocess.run(
            [sys.executable, str(script), "--once", "--status", "smoke"],
            env=self.env, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        alive = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        self.assertTrue(alive.is_file())
        data = json.loads(alive.read_text())
        self.assertEqual(data["status"], "smoke")

    def test_sigterm_cleans_up_alive_file(self):
        """Graceful shutdown removes the .alive file so peers see the core
        leave immediately rather than wait for mtime staleness."""
        import signal as _signal
        script = ROOT / "src" / "core_heartbeat.py"
        proc = subprocess.Popen(
            [sys.executable, str(script), "--interval", "0.5"],
            env=self.env,
        )
        # Wait for first beat to land.
        alive = self.tmp / "state" / "cores" / f"{_short_host()}.alive"
        for _ in range(40):
            if alive.exists():
                break
            time.sleep(0.1)
        self.assertTrue(alive.exists(), "first beat should have landed within 4s")
        # Signal graceful shutdown.
        proc.send_signal(_signal.SIGTERM)
        proc.wait(timeout=5)
        self.assertFalse(alive.exists(), ".alive should have been unlinked on SIGTERM")


class TestOrphanWriterGuard(unittest.TestCase):
    """qingyun CR on #2527 (2nd P1): a dead core must not leave a fresh orphan
    heartbeat. core_heartbeat.py binds to the core's tmux session and stops
    beating once the session it saw has gone away."""

    def _mod(self):
        sys.modules.pop("core_heartbeat", None)
        import core_heartbeat  # noqa
        return core_heartbeat

    def test_should_stop_transitions(self):
        m = self._mod()
        f = m._should_stop_beating
        # alive True -> never stop; remember seen; reset misses.
        self.assertEqual(f(True, False, 5), (False, True, 0))
        # alive None (probe unavailable) -> keep beating, carry state unchanged.
        self.assertEqual(f(None, True, 1), (False, True, 1))
        self.assertEqual(f(None, False, 0), (False, False, 0))
        # alive False before ever seeing the core -> startup grace, keep beating.
        self.assertEqual(f(False, False, 0), (False, False, 0))
        # alive False after seen -> count misses; stop only at the limit.
        self.assertEqual(f(False, True, 0), (False, True, 1))   # 1st miss
        self.assertEqual(f(False, True, 1), (True, True, 2))    # 2nd miss -> stop
        # miss_limit override -> stop on first miss.
        self.assertEqual(f(False, True, 0, miss_limit=1), (True, True, 1))

    def test_core_session_alive_probe_states(self):
        m = self._mod()

        class _P:
            def __init__(self, rc):
                self.returncode = rc

        # ran, session present -> True
        m.subprocess.run = lambda *a, **k: _P(0)
        self.assertIs(m._core_session_alive("/tmp/sock"), True)
        # ran, session absent -> False
        m.subprocess.run = lambda *a, **k: _P(1)
        self.assertIs(m._core_session_alive("/tmp/sock"), False)
        # probe could not execute -> None (fail open; never stop on a glitch)
        def _boom(*a, **k):
            raise FileNotFoundError("tmux missing")
        m.subprocess.run = _boom
        self.assertIsNone(m._core_session_alive("/tmp/sock"))

    def test_run_forever_stops_when_seen_core_disappears(self):
        m = self._mod()
        # A .alive to be cleaned up on stop.
        tmp = Path(tempfile.mkdtemp(prefix="orphan-guard-"))
        cores = tmp / "state" / "cores"
        cores.mkdir(parents=True, exist_ok=True)
        alive = cores / "orphan-test.alive"
        alive.write_text("{}")
        m._alive_path = lambda: alive
        # Session seen once, then gone twice -> stop at miss_limit=2.
        seq = iter([True, False, False])
        m._core_session_alive = lambda *a, **k: next(seq, False)
        beats = {"n": 0}
        m.write_beat = lambda status="running": beats.__setitem__("n", beats["n"] + 1)
        m.time.sleep = lambda *_a, **_k: None
        m._SHUTDOWN_REQUESTED = False
        m.signal.signal = lambda *a, **k: None  # avoid touching real handlers

        rc = m.run_forever(interval=0.01)

        self.assertEqual(rc, 0)
        self.assertFalse(alive.exists(), "orphan .alive should be unlinked on stop")
        # Two beats: the 'seen' tick, then the first (debounced) miss tick still
        # beats; the SECOND consecutive miss reaches miss_limit and stops before
        # writing. A lone transient miss therefore never kills a live heartbeat.
        self.assertEqual(beats["n"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
