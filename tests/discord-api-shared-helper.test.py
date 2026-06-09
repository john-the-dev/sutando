#!/usr/bin/env python3
"""Contract tests for the shared Discord API helper (src/discord_api.py).

Guards three things:

1. discord_api.py exists and exposes `discord_api(method, path, token, body)`.
2. src/dm-result.py imports from discord_api rather than defining its own copy
   — prevents the local-definition anti-pattern from silently drifting back.
3. src/discord-bridge.py's _send_via_rest uses the shared helper — eliminates
   the inline urllib boilerplate that previously lived there.

The integration behaviour of discord_api() itself is exercised by patching
urllib.request.urlopen (which django_api uses at call time) and verifying the
request shape sent to Discord.
"""
import importlib.util
import json
import os
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# ── load discord_api module ──────────────────────────────────────────────────

def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m  # register so cross-module imports resolve
    spec.loader.exec_module(m)
    return m


da = _load("discord_api", REPO / "src" / "discord_api.py")


# ── helpers ──────────────────────────────────────────────────────────────────

class _FakeResponse:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen(response_body: dict):
    """Replace urllib.request.urlopen for one call; return the request seen."""
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["method"] = req.get_method()
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        if req.data:
            captured["body"] = json.loads(req.data)
        return _FakeResponse(response_body)

    original = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    return captured, original


# ── test: discord_api sends correct request shape ────────────────────────────

def test_get_request_shape():
    captured, orig = _patch_urlopen({"id": "user-1"})
    try:
        result = da.discord_api("GET", "/users/@me", "tok-abc")
    finally:
        urllib.request.urlopen = orig
    assert captured["method"] == "GET"
    assert captured["url"] == "https://discord.com/api/v10/users/@me"
    assert captured["headers"].get("Authorization") == "Bot tok-abc"
    assert captured["headers"].get("Content-type") == "application/json"
    assert "body" not in captured
    assert result == {"id": "user-1"}
    print("PASS: GET sends correct request and returns parsed JSON")


def test_post_request_shape():
    captured, orig = _patch_urlopen({"id": "msg-1"})
    try:
        result = da.discord_api("POST", "/channels/123/messages", "tok-xyz", {"content": "hello"})
    finally:
        urllib.request.urlopen = orig
    assert captured["method"] == "POST"
    assert captured["url"] == "https://discord.com/api/v10/channels/123/messages"
    assert captured["body"] == {"content": "hello"}
    assert result == {"id": "msg-1"}
    print("PASS: POST sends body and returns parsed JSON")


def test_empty_response():
    orig = urllib.request.urlopen

    class _EmptyResponse:
        def read(self): return b""
        def __enter__(self): return self
        def __exit__(self, *a): return False

    urllib.request.urlopen = lambda req, timeout=None: _EmptyResponse()
    try:
        result = da.discord_api("POST", "/channels/1/messages", "tok", {"content": "x"})
    finally:
        urllib.request.urlopen = orig
    assert result is None
    print("PASS: empty response body returns None")


# ── test: dm-result.py uses shared helper, no local definition ───────────────

def test_dm_result_no_local_discord_api():
    src = (REPO / "src" / "dm-result.py").read_text()
    assert "def _discord_api(" not in src, (
        "dm-result.py defines its own _discord_api() — should import from discord_api instead"
    )
    print("PASS: dm-result.py has no local _discord_api() definition")


def test_dm_result_imports_shared_helper():
    src = (REPO / "src" / "dm-result.py").read_text()
    assert "from discord_api import" in src, (
        "dm-result.py does not import from discord_api — add: "
        "from discord_api import discord_api as _discord_api"
    )
    print("PASS: dm-result.py imports from shared discord_api module")


# ── test: discord-bridge.py _send_via_rest uses shared helper ───────────────

def _extract_fn(src: str, fn_name: str) -> str:
    """Extract function body from source by finding the next top-level def."""
    fn_start = src.find(f"def {fn_name}(")
    assert fn_start != -1, f"{fn_name} not found"
    # find the next top-level def (starts at column 0)
    import re
    m = re.search(r"\ndef \w", src[fn_start + 1:])
    fn_end = (fn_start + 1 + m.start()) if m else len(src)
    return src[fn_start:fn_end]


def test_discord_bridge_send_via_rest_uses_shared_helper():
    src = (REPO / "src" / "discord-bridge.py").read_text()
    fn_body = _extract_fn(src, "_send_via_rest")
    assert "urllib.request.urlopen" not in fn_body, (
        "_send_via_rest still contains inline urllib.request.urlopen — "
        "should use the shared discord_api helper instead"
    )
    assert "_discord_api_post(" in fn_body, (
        "_send_via_rest does not call _discord_api_post"
    )
    print("PASS: discord-bridge.py _send_via_rest uses shared helper")


def test_discord_bridge_no_inline_urllib_in_send_via_rest():
    src = (REPO / "src" / "discord-bridge.py").read_text()
    fn_body = _extract_fn(src, "_send_via_rest")
    assert "urllib.request.Request(" not in fn_body, (
        "_send_via_rest still builds urllib.request.Request inline"
    )
    print("PASS: _send_via_rest has no inline Request construction")


if __name__ == "__main__":
    os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token")
    test_get_request_shape()
    test_post_request_shape()
    test_empty_response()
    test_dm_result_no_local_discord_api()
    test_dm_result_imports_shared_helper()
    test_discord_bridge_send_via_rest_uses_shared_helper()
    test_discord_bridge_no_inline_urllib_in_send_via_rest()
    print("\nAll 7 tests passed.")
