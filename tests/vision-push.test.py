#!/usr/bin/env python3
"""Tests for src/vision_push.py — push_image() pre-network guards.

Covers the logic that runs BEFORE any HTTP call:
  a) push_image() path guards — file not found, file too small, MIME detection
  b) push_image() with monkeypatched is_voice_ready / _post — voice-down fallback,
     end-to-end accept/reject paths
  c) MIN_FRAME_BYTES constant sanity

Run: python3 tests/vision-push.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location("vision_push", REPO / "src" / "vision_push.py")
_mod = importlib.util.module_from_spec(spec)
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


# Helpers: monkeypatch is_voice_ready and _post without network

def _with_voice(ready: bool, post_status: int = 200):
    """Context that patches is_voice_ready and _post."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        orig_ready = _mod.is_voice_ready
        orig_post = _mod._post
        _mod.is_voice_ready = lambda *a, **kw: ready
        _mod._post = lambda *a, **kw: (post_status, b"ok")
        try:
            yield
        finally:
            _mod.is_voice_ready = orig_ready
            _mod._post = orig_post

    return _ctx()


# ---------------------------------------------------------------------------
# (a) path guards and MIME detection
# ---------------------------------------------------------------------------

def _test_path_guards():
    # Non-existent path → False, no network
    _check("pg-not-found", not _mod.push_image("/tmp/nonexistent-vision-frame.jpg"))

    with tempfile.TemporaryDirectory() as tmp:
        # File too small (< MIN_FRAME_BYTES) → False
        small = Path(tmp) / "small.jpg"
        small.write_bytes(b"\xff\xd8" + b"\x00" * 100)  # 102 bytes
        _check("pg-too-small", not _mod.push_image(str(small)))

        # Minimum size boundary: exactly MIN_FRAME_BYTES → not rejected by size check
        # (will fail at is_voice_ready, not size)
        exact = Path(tmp) / "exact.jpg"
        exact.write_bytes(b"\xff\xd8" + b"\x00" * (_mod.MIN_FRAME_BYTES - 2))
        with _with_voice(ready=False):
            _check("pg-exact-min-rejected-by-voice-not-size",
                   not _mod.push_image(str(exact)))

        # One byte below MIN_FRAME_BYTES → False regardless of voice state
        below = Path(tmp) / "below.jpg"
        below.write_bytes(b"\x00" * (_mod.MIN_FRAME_BYTES - 1))
        with _with_voice(ready=True, post_status=200):
            _check("pg-below-min-always-false", not _mod.push_image(str(below)))

        # JPEG extension → image/jpeg MIME
        jpeg_file = Path(tmp) / "frame.jpg"
        jpeg_file.write_bytes(b"\xff\xd8" + b"\x00" * _mod.MIN_FRAME_BYTES)
        detected_mime = []
        orig_post = _mod._post
        def _capture_mime(path, body, ct, **kw):
            detected_mime.append(ct)
            return (200, b"ok")
        _mod._post = _capture_mime
        orig_ready = _mod.is_voice_ready
        _mod.is_voice_ready = lambda *a, **kw: True
        try:
            _mod.push_image(str(jpeg_file))
        finally:
            _mod._post = orig_post
            _mod.is_voice_ready = orig_ready
        # _post is called twice (start + frame); the frame call gets the image MIME
        _check("pg-jpeg-mime", any("image/jpeg" in m for m in detected_mime),
               f"got {detected_mime}")

        # PNG extension → image/png
        png_file = Path(tmp) / "frame.png"
        png_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * _mod.MIN_FRAME_BYTES)
        detected_mime.clear()
        _mod._post = _capture_mime
        _mod.is_voice_ready = lambda *a, **kw: True
        try:
            _mod.push_image(str(png_file))
        finally:
            _mod._post = orig_post
            _mod.is_voice_ready = orig_ready
        _check("pg-png-mime", any("image/png" in m for m in detected_mime),
               f"got {detected_mime}")

        # Unknown extension → defaults to image/jpeg
        unknown = Path(tmp) / "frame.bin"
        unknown.write_bytes(b"\x00" * _mod.MIN_FRAME_BYTES * 2)
        detected_mime.clear()
        _mod._post = _capture_mime
        _mod.is_voice_ready = lambda *a, **kw: True
        try:
            _mod.push_image(str(unknown))
        finally:
            _mod._post = orig_post
            _mod.is_voice_ready = orig_ready
        _check("pg-unknown-defaults-jpeg", any("image/jpeg" in m for m in detected_mime),
               f"got {detected_mime}")


_test_path_guards()


# ---------------------------------------------------------------------------
# (b) voice-ready gate and end-to-end accept/reject
# ---------------------------------------------------------------------------

def _test_voice_gate():
    with tempfile.TemporaryDirectory() as tmp:
        ok_file = Path(tmp) / "ok.jpg"
        ok_file.write_bytes(b"\xff\xd8" + b"\x00" * _mod.MIN_FRAME_BYTES)

        # Voice not ready → False
        with _with_voice(ready=False):
            _check("vg-not-ready-false", not _mod.push_image(str(ok_file)))

        # Voice ready, /vision/frame returns 200 → True
        with _with_voice(ready=True, post_status=200):
            _check("vg-ready-200-true", _mod.push_image(str(ok_file)))

        # Voice ready, /vision/frame returns 201 → True (any 2xx)
        with _with_voice(ready=True, post_status=201):
            _check("vg-ready-201-true", _mod.push_image(str(ok_file)))

        # Voice ready, /vision/frame returns 500 → False
        with _with_voice(ready=True, post_status=500):
            _check("vg-ready-500-false", not _mod.push_image(str(ok_file)))

        # Voice ready, /vision/frame returns 404 → False
        with _with_voice(ready=True, post_status=404):
            _check("vg-ready-404-false", not _mod.push_image(str(ok_file)))

        # source parameter flows through the /vision/start body
        start_bodies = []
        orig_post = _mod._post
        orig_ready = _mod.is_voice_ready
        _mod.is_voice_ready = lambda *a, **kw: True
        def _capture_start(path, body, ct, **kw):
            if "start" in path:
                start_bodies.append(body)
            return (200, b"ok")
        _mod._post = _capture_start
        try:
            _mod.push_image(str(ok_file), source="telegram")
        finally:
            _mod._post = orig_post
            _mod.is_voice_ready = orig_ready
        _check("vg-source-in-start-body",
               any(b"telegram" in b for b in start_bodies),
               f"got {start_bodies}")


_test_voice_gate()


# ---------------------------------------------------------------------------
# (c) MIN_FRAME_BYTES constant
# ---------------------------------------------------------------------------

def _test_constants():
    # MIN_FRAME_BYTES should be > 0 and ≤ 10 KB (guards against tiny/corrupted frames)
    _check("const-min-positive", _mod.MIN_FRAME_BYTES > 0)
    _check("const-min-reasonable", _mod.MIN_FRAME_BYTES <= 10240,
           f"got {_mod.MIN_FRAME_BYTES}")
    # Documented as 2048
    _check("const-min-is-2048", _mod.MIN_FRAME_BYTES == 2048,
           f"got {_mod.MIN_FRAME_BYTES}")


_test_constants()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"vision-push: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
