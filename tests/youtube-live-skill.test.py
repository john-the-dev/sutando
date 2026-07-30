#!/usr/bin/env python3
"""Unit tests for the youtube-live skill. CI-safe: no network, no ffmpeg spawn."""

import importlib.util
import os
import sys
import unittest
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parent.parent
    / "skills" / "youtube-live" / "scripts" / "go_live.py"
)
_spec = importlib.util.spec_from_file_location("go_live", _SCRIPT)
go_live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(go_live)

CFG = {"resolution": "1280x720", "fps": 30, "video_bitrate": "4500k",
       "audio_bitrate": "128k", "buffer_size": "9000k", "avf_screen_spec": "1:0"}


class BuildCmdTests(unittest.TestCase):
    def test_test_source_has_lavfi_video_and_audio(self):
        cmd = go_live.build_ffmpeg_cmd("test", "KEY123", CFG)
        joined = " ".join(cmd)
        self.assertIn("testsrc2=size=1280x720:rate=30", joined)
        self.assertIn("sine=frequency=1000", joined)
        # YouTube-compatible codecs
        self.assertIn("libx264", cmd)
        self.assertIn("aac", cmd)
        self.assertIn("yuv420p", cmd)
        # gop = 2 * fps
        gi = cmd.index("-g")
        self.assertEqual(cmd[gi + 1], "60")

    def test_ends_with_flv_to_ingest_and_key(self):
        cmd = go_live.build_ffmpeg_cmd("test", "SECRET", CFG,
                                       ingest_base="rtmp://x/live2")
        self.assertEqual(cmd[-3:], ["-f", "flv", "rtmp://x/live2/SECRET"])

    def test_file_source_loop(self):
        cmd = go_live.build_ffmpeg_cmd("file:/tmp/a.mp4", "K", CFG, loop=True)
        self.assertIn("-stream_loop", cmd)
        self.assertIn("/tmp/a.mp4", cmd)

    def test_file_source_no_loop(self):
        cmd = go_live.build_ffmpeg_cmd("file:/tmp/a.mp4", "K", CFG, loop=False)
        self.assertNotIn("-stream_loop", cmd)

    def test_image_source_has_still_and_silent_audio(self):
        cmd = go_live.build_ffmpeg_cmd("image:/tmp/s.png", "K", CFG)
        joined = " ".join(cmd)
        self.assertIn("-loop", cmd)
        self.assertIn("/tmp/s.png", cmd)
        self.assertIn("anullsrc", joined)

    def test_screen_source_uses_avfoundation(self):
        cmd = go_live.build_ffmpeg_cmd("screen", "K", CFG)
        self.assertIn("avfoundation", cmd)
        self.assertIn("1:0", cmd)

    def test_unknown_source_raises(self):
        with self.assertRaises(ValueError):
            go_live.build_ffmpeg_cmd("bogus", "K", CFG)


class RedactionTests(unittest.TestCase):
    def test_key_is_redacted_in_printed_command(self):
        cmd = go_live.build_ffmpeg_cmd("test", "SUPERSECRETKEY", CFG)
        printed = go_live._redacted_str(cmd, "SUPERSECRETKEY")
        self.assertNotIn("SUPERSECRETKEY", printed)
        self.assertIn("<STREAM_KEY>", printed)


class KeyResolutionTests(unittest.TestCase):
    def test_cli_beats_env(self):
        os.environ["YOUTUBE_STREAM_KEY"] = "envkey"
        try:
            self.assertEqual(go_live._resolve_stream_key("clikey"), "clikey")
        finally:
            del os.environ["YOUTUBE_STREAM_KEY"]

    def test_env_used_when_no_cli(self):
        os.environ["YOUTUBE_STREAM_KEY"] = "envkey"
        try:
            self.assertEqual(go_live._resolve_stream_key(None), "envkey")
        finally:
            del os.environ["YOUTUBE_STREAM_KEY"]


class DryRunTests(unittest.TestCase):
    def test_dry_run_does_not_stream_and_redacts(self):
        import io
        import json
        from contextlib import redirect_stdout

        class Args:
            source = "test"
            loop = False
            stream_key = "TOPSECRET"
            dry_run = True

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = go_live.cmd_start(Args())
        self.assertEqual(rc, 0)
        out = json.loads(buf.getvalue())
        self.assertTrue(out["dry_run"])
        self.assertNotIn("TOPSECRET", out["command"])
        self.assertIn("<STREAM_KEY>", out["command"])


class StatusTests(unittest.TestCase):
    def test_status_not_running(self):
        import io
        import json
        from contextlib import redirect_stdout

        # Ensure no stale pid file interferes.
        if os.path.exists(go_live.PID_FILE):
            self.skipTest("a real stream pid file exists; skip to avoid touching it")
        buf = io.StringIO()
        with redirect_stdout(buf):
            go_live.cmd_status(None)
        out = json.loads(buf.getvalue())
        self.assertFalse(out["running"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
