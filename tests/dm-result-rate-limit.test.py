#!/usr/bin/env python3
"""Regression tests for retained Discord fallback results.

An oversized result used to be sent as many independent messages. If a later
message hit HTTP 429, the bridge retained the result file and the next poll
started again at message one, duplicating every earlier chunk.
"""

import importlib.util
import io
import json
import os
import tempfile
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-not-real")


def _load():
    source = Path(os.environ.get("DM_RESULT_SOURCE", REPO / "src" / "dm-result.py"))
    spec = importlib.util.spec_from_file_location("dm_result_rate_limit", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


dm = _load()


class FakeResponse:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def with_owner(fn):
    original_access = dm.ACCESS_JSON
    original_load = dm.discord_config.load_config
    access = Path(tempfile.mkdtemp(prefix="sutando-dm429-access-")) / "access.json"
    access.write_text(json.dumps({"allowFrom": ["owner"], "tierMap": {"owner": "owner"}}))
    dm.ACCESS_JSON = access
    dm.discord_config.load_config = lambda: {}
    try:
        fn()
    finally:
        dm.ACCESS_JSON = original_access
        dm.discord_config.load_config = original_load
        access.unlink()
        access.parent.rmdir()


def test_oversized_file_is_one_atomic_message():
    # The production path is workspace/results; /tmp/sutando-* is the shared
    # allowlist's explicit fixture-safe temporary prefix.
    tmpdir = Path(tempfile.mkdtemp(prefix="sutando-dm429-", dir="/tmp"))
    result = tmpdir / "result.txt"
    outbox = tmpdir / "outbox.log"
    text = "long report line\n" * 400
    result.write_text(text)
    calls = []
    original_urlopen = dm.urllib.request.urlopen
    original_outbox = None

    def urlopen(request, timeout=None):
        calls.append(request)
        if request.full_url.endswith("/users/@me/channels"):
            return FakeResponse({"id": "owner-dm"})
        if request.full_url.endswith("/channels/owner-dm/messages"):
            return FakeResponse({"id": "message"})
        raise AssertionError(f"unexpected request: {request.full_url}")

    def run():
        nonlocal original_outbox
        import outbox_log

        original_outbox = outbox_log._outbox_path
        outbox_log._outbox_path = lambda: outbox
        dm.urllib.request.urlopen = urlopen
        try:
            assert dm.send_dm(text, source_file=str(result)) is True
        finally:
            dm.urllib.request.urlopen = original_urlopen
            outbox_log._outbox_path = original_outbox

    try:
        with_owner(run)
        messages = [request for request in calls if request.full_url.endswith("/messages")]
        assert len(messages) == 1, f"oversized retained result used {len(messages)} message requests"
        content_type = messages[0].headers.get("Content-type", "")
        assert "multipart/form-data" in content_type
        assert text.encode() in messages[0].data
        assert b"full text attached" in messages[0].data
    finally:
        result.unlink(missing_ok=True)
        outbox.unlink(missing_ok=True)
        tmpdir.rmdir()


def test_json_post_honors_retry_after_on_429():
    calls = 0
    sleeps = []
    original_urlopen = dm.urllib.request.urlopen
    original_sleep = dm.time.sleep

    def urlopen(request, timeout=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                429,
                "rate limited",
                {"Retry-After": "0.25"},
                io.BytesIO(b'{"retry_after": 0.25}'),
            )
        return FakeResponse({"id": "sent"})

    dm.urllib.request.urlopen = urlopen
    dm.time.sleep = sleeps.append
    try:
        response = dm._discord_api("POST", "/channels/test/messages", "token", {"content": "hello"})
    finally:
        dm.urllib.request.urlopen = original_urlopen
        dm.time.sleep = original_sleep

    assert response == {"id": "sent"}
    assert calls == 2
    assert sleeps == [0.75], sleeps


def main():
    test_oversized_file_is_one_atomic_message()
    print("  OK: oversized file-backed result uses one atomic multipart message")
    test_json_post_honors_retry_after_on_429()
    print("  OK: HTTP 429 honors Retry-After and retries the same request")
    print("All dm-result rate-limit regression tests passed.")


if __name__ == "__main__":
    main()
