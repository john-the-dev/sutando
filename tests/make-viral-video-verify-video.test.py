#!/usr/bin/env python3
"""Regression guard: verify() in skills/make-viral-video/scripts/verify_video.py.

verify(video_path, expected_duration_s):
  Returns a dict {valid, reason?, path, duration_s, size_bytes, streams}.
  Guards checked in order:
    1. file_not_found
    2. zero_byte_file
    3. ffprobe_failed (CalledProcessError)
    4. no_video_stream
    5. video_codec_not_h264
    6. wrong_dimensions
    7. no_audio_stream
    8. audio_codec_not_aac
    9. duration_outside_tolerance (only when expected_duration_s supplied)
   10. av_duration_mismatch
   11. valid (all checks pass)

ffprobe() is monkey-patched for IO-free testing of all logic gates beyond
the filesystem checks (file-not-found, zero-byte).

Run: python3 tests/make-viral-video-verify-video.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "make-viral-video" / "scripts"

spec = importlib.util.spec_from_file_location(
    "verify_video", SCRIPTS / "verify_video.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["verify_video"] = _mod
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


def _write_tmp(content: bytes = b"fake video data") -> Path:
    fd, p = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    Path(p).write_bytes(content)
    return Path(p)


# ---------------------------------------------------------------------------
# Helpers for building mock ffprobe payloads
# ---------------------------------------------------------------------------

def _make_probe(
    video_codec: str = "h264",
    width: int = 1280,
    height: int = 720,
    audio_codec: str = "aac",
    video_duration: str = "45.0",
    audio_duration: str = "45.0",
    fmt_duration: str = "45.0",
    fmt_size: str = "1048576",
) -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": video_codec,
                "width": width,
                "height": height,
                "duration": video_duration,
            },
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
                "duration": audio_duration,
            },
        ],
        "format": {
            "duration": fmt_duration,
            "size": fmt_size,
        },
    }


def _no_video_probe() -> dict:
    return {
        "streams": [
            {"codec_type": "audio", "codec_name": "aac", "duration": "30.0"}
        ],
        "format": {"duration": "30.0", "size": "1000"},
    }


def _no_audio_probe() -> dict:
    return {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "duration": "30.0",
            }
        ],
        "format": {"duration": "30.0", "size": "1000"},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _test_verify():
    f = _mod.verify

    # 1. File does not exist
    r = f(Path("/nonexistent/ghost.mp4"))
    _check("vv-missing",   r.get("valid") is False)
    _check("vv-missing-r", r.get("reason") == "file_not_found")

    # 2. Zero-byte file
    tmp = _write_tmp(b"")
    r = f(tmp)
    _check("vv-zero",   r.get("valid") is False)
    _check("vv-zero-r", r.get("reason") == "zero_byte_file")
    tmp.unlink()

    # 3. ffprobe CalledProcessError → ffprobe_failed
    tmp3 = _write_tmp(b"fake data")

    def _ffprobe_fail(path):
        raise subprocess.CalledProcessError(1, "ffprobe", stderr="bad file")

    _mod.ffprobe = _ffprobe_fail
    r = f(tmp3)
    _check("vv-ffprobe-fail",   r.get("valid") is False)
    _check("vv-ffprobe-fail-r", "ffprobe_failed" in r.get("reason", ""))
    tmp3.unlink()

    # 4. No video stream
    tmp4 = _write_tmp()
    _mod.ffprobe = lambda path: _no_video_probe()
    r = f(tmp4)
    _check("vv-no-video",   r.get("valid") is False)
    _check("vv-no-video-r", r.get("reason") == "no_video_stream")
    tmp4.unlink()

    # 5. Video codec not h264
    tmp5 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(video_codec="hevc")
    r = f(tmp5)
    _check("vv-codec",   r.get("valid") is False)
    _check("vv-codec-r", "video_codec_not_h264" in r.get("reason", ""))
    tmp5.unlink()

    # 6. Wrong dimensions — portrait (1080×1920)
    tmp6 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(width=1080, height=1920)
    r = f(tmp6)
    _check("vv-dims",   r.get("valid") is False)
    _check("vv-dims-r", "wrong_dimensions" in r.get("reason", ""))
    tmp6.unlink()

    # 6b. Wrong dimensions — wrong width only
    tmp6b = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(width=1920, height=720)
    r = f(tmp6b)
    _check("vv-dims-w",   r.get("valid") is False)
    _check("vv-dims-w-r", "wrong_dimensions" in r.get("reason", ""))
    tmp6b.unlink()

    # 7. No audio stream
    tmp7 = _write_tmp()
    _mod.ffprobe = lambda path: _no_audio_probe()
    r = f(tmp7)
    _check("vv-no-audio",   r.get("valid") is False)
    _check("vv-no-audio-r", r.get("reason") == "no_audio_stream")
    tmp7.unlink()

    # 8. Audio codec not aac
    tmp8 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(audio_codec="mp3")
    r = f(tmp8)
    _check("vv-acodec",   r.get("valid") is False)
    _check("vv-acodec-r", "audio_codec_not_aac" in r.get("reason", ""))
    tmp8.unlink()

    # 9. Duration outside tolerance (too long)
    tmp9 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(fmt_duration="60.0", video_duration="60.0",
                                            audio_duration="60.0")
    r = f(tmp9, expected_duration_s=45.0)
    _check("vv-dur-long",   r.get("valid") is False)
    _check("vv-dur-long-r", "duration_outside_tolerance" in r.get("reason", ""))
    tmp9.unlink()

    # 9b. Duration outside tolerance (too short)
    tmp9b = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(fmt_duration="30.0", video_duration="30.0",
                                            audio_duration="30.0")
    r = f(tmp9b, expected_duration_s=45.0)
    _check("vv-dur-short",   r.get("valid") is False)
    _check("vv-dur-short-r", "duration_outside_tolerance" in r.get("reason", ""))
    tmp9b.unlink()

    # 9c. Duration exactly at boundary passes (within 5s tolerance)
    tmp9c = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(fmt_duration="50.0", video_duration="50.0",
                                            audio_duration="50.0")
    r = f(tmp9c, expected_duration_s=45.0)
    _check("vv-dur-boundary", r.get("valid") is True,
           f"got valid={r.get('valid')} reason={r.get('reason')!r}")
    tmp9c.unlink()

    # 10. AV duration mismatch (audio is too short — silent tail)
    tmp10 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(video_duration="45.0", audio_duration="44.0")
    r = f(tmp10)
    _check("vv-av-sync",   r.get("valid") is False)
    _check("vv-av-sync-r", "av_duration_mismatch" in r.get("reason", ""))
    tmp10.unlink()

    # 10b. AV mismatch exactly at boundary passes (within 0.5s)
    tmp10b = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(video_duration="45.0", audio_duration="44.6")
    r = f(tmp10b)
    _check("vv-av-boundary", r.get("valid") is True,
           f"got valid={r.get('valid')} reason={r.get('reason')!r}")
    tmp10b.unlink()

    # 11. All checks pass — no expected_duration
    tmp11 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe()
    r = f(tmp11)
    _check("vv-valid",   r.get("valid") is True)
    _check("vv-valid-dur", r.get("duration_s") == 45.0)
    _check("vv-valid-streams", len(r.get("streams", [])) == 2)
    tmp11.unlink()

    # 11b. All checks pass — with expected_duration
    tmp11b = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(fmt_duration="47.0", video_duration="47.0",
                                            audio_duration="47.0")
    r = f(tmp11b, expected_duration_s=45.0)
    _check("vv-valid-dur-ok", r.get("valid") is True,
           f"got valid={r.get('valid')} reason={r.get('reason')!r}")
    tmp11b.unlink()

    # 12. ffprobe generic exception → ffprobe_exception
    tmp12 = _write_tmp()

    def _ffprobe_exc(path):
        raise RuntimeError("unexpected codec error")

    _mod.ffprobe = _ffprobe_exc
    r = f(tmp12)
    _check("vv-ffprobe-exc",   r.get("valid") is False)
    _check("vv-ffprobe-exc-r", "ffprobe_exception" in r.get("reason", ""))
    tmp12.unlink()

    # 13. Report always includes path, duration_s, size_bytes, streams on valid
    tmp13 = _write_tmp()
    _mod.ffprobe = lambda path: _make_probe(fmt_size="2097152")
    r = f(tmp13)
    _check("vv-report-path",    "path" in r)
    _check("vv-report-size",    r.get("size_bytes") == 2097152)
    _check("vv-report-streams", isinstance(r.get("streams"), list))
    tmp13.unlink()


_test_verify()

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"make-viral-video-verify-video: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
