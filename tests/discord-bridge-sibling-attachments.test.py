#!/usr/bin/env python3
"""Unit tests for `_select_sibling_attachments` in `src/discord-bridge.py`.

Regression guard for the cross-instance media gap (2026-07-16): when someone
pings the bot with text but no media of their own and references ANOTHER user
("@bot make the video @Alice sent"), the media lives on that user's own earlier,
un-mentioned messages. Those messages neither invoke the bot nor are replied-to,
so the primary attachment loop and the reply-context loop both miss them.

`_select_sibling_attachments(history, referenced_ids, cutoff, cap)` is the pure
selection logic that decides which sibling attachments to pull. It is kept out of
the async download I/O so it can be tested without a live discord channel. These
tests pin: referenced-user filtering, the time-window cutoff (ordered early-stop),
the cap, oldest-first ordering, and defensive handling of empty/attachmentless
messages.
"""

import importlib.util
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Same module-load harness as the sibling attachment-filename test: stub the
# `discord` module + a fake token so discord-bridge.py imports cleanly in CI.
_WORKSPACE_TMP = tempfile.mkdtemp(prefix="sutando-discord-sibling-test-")
os.environ["SUTANDO_WORKSPACE"] = _WORKSPACE_TMP
os.environ["SUTANDO_TEST_MODE"] = "1"
_HOME_TMP = tempfile.mkdtemp(prefix="sutando-discord-sibling-test-home-")
os.environ["HOME"] = _HOME_TMP
_token_dir = Path(_HOME_TMP) / ".claude" / "channels" / "discord"
_token_dir.mkdir(parents=True, exist_ok=True)
(_token_dir / ".env").write_text("DISCORD_BOT_TOKEN=test-token-not-real\n")
os.environ.setdefault("DISCORD_BOT_TOKEN", "test-token-not-real")


def _load(name: str, path: Path):
    if "discord" not in sys.modules:
        stub = types.ModuleType("discord")
        stub.Intents = type("Intents", (), {"default": staticmethod(lambda: type("I", (), {"message_content": False})())})
        stub.Client = type("Client", (), {"__init__": lambda self, **kw: None, "event": staticmethod(lambda fn: fn)})
        stub.File = type("File", (), {})
        sys.modules["discord"] = stub
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


bridge = _load("discord_bridge", REPO / "src" / "discord-bridge.py")
select = bridge._select_sibling_attachments


# --- fakes -----------------------------------------------------------------
class _Author:
    def __init__(self, uid, name):
        self.id = uid
        self._name = name

    def __str__(self):
        return self._name


class _Att:
    def __init__(self, filename):
        self.filename = filename


class _Msg:
    def __init__(self, uid, name, created_at, filenames):
        self.author = _Author(uid, name)
        self.created_at = created_at
        self.attachments = [_Att(f) for f in filenames]


_NOW = datetime(2026, 7, 16, 2, 15, 0)
_CUTOFF = _NOW - timedelta(minutes=15)


def _hist(*msgs):
    """History is newest-first (as discord channel.history(before=...) yields)."""
    return list(msgs)


# --- tests -----------------------------------------------------------------
def test_picks_referenced_users_media():
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=1), ["garden.mp4"]),
    )
    picked = select(history, {999}, _CUTOFF)
    assert len(picked) == 1, picked
    author, att = picked[0]
    assert author == "Alice"
    assert att.filename == "garden.mp4"


def test_skips_non_referenced_users():
    history = _hist(
        _Msg(111, "Bob", _NOW - timedelta(minutes=1), ["random.png"]),
        _Msg(999, "Alice", _NOW - timedelta(minutes=2), ["wanted.mp4"]),
    )
    picked = select(history, {999}, _CUTOFF)
    assert len(picked) == 1
    assert picked[0][1].filename == "wanted.mp4"


def test_respects_cutoff_early_stop():
    # Ordered newest-first: once we hit an older-than-cutoff message the scan
    # stops, so an in-window attachment BEHIND an out-of-window message from the
    # same user is (intentionally) not reached.
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=20), ["too-old.mp4"]),
        _Msg(999, "Alice", _NOW - timedelta(minutes=25), ["also-old.mp4"]),
    )
    picked = select(history, {999}, _CUTOFF)
    assert picked == [], picked


def test_cap_limits_total():
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=1), [f"f{i}.png" for i in range(10)]),
    )
    picked = select(history, {999}, _CUTOFF, cap=3)
    assert len(picked) == 3, picked


def test_oldest_first_ordering():
    # newest-first input; each message has one attachment
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=1), ["newest.mp4"]),
        _Msg(999, "Alice", _NOW - timedelta(minutes=3), ["middle.mp4"]),
        _Msg(999, "Alice", _NOW - timedelta(minutes=5), ["oldest.mp4"]),
    )
    picked = select(history, {999}, _CUTOFF)
    names = [a.filename for _, a in picked]
    assert names == ["oldest.mp4", "middle.mp4", "newest.mp4"], names


def test_messages_without_attachments_ignored():
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=1), []),
        _Msg(999, "Alice", _NOW - timedelta(minutes=2), ["real.mp4"]),
    )
    picked = select(history, {999}, _CUTOFF)
    assert len(picked) == 1
    assert picked[0][1].filename == "real.mp4"


def test_empty_history_returns_empty():
    assert select([], {999}, _CUTOFF) == []


def test_empty_referenced_ids_returns_empty():
    history = _hist(_Msg(999, "Alice", _NOW - timedelta(minutes=1), ["x.mp4"]))
    assert select(history, set(), _CUTOFF) == []


def test_multiple_referenced_users():
    history = _hist(
        _Msg(999, "Alice", _NOW - timedelta(minutes=1), ["a.mp4"]),
        _Msg(888, "Cara", _NOW - timedelta(minutes=2), ["c.mp4"]),
        _Msg(111, "Bob", _NOW - timedelta(minutes=3), ["ignored.png"]),
    )
    picked = select(history, {999, 888}, _CUTOFF)
    names = sorted(a.filename for _, a in picked)
    assert names == ["a.mp4", "c.mp4"], names


def main():
    failures = []
    for fn in (
        test_picks_referenced_users_media,
        test_skips_non_referenced_users,
        test_respects_cutoff_early_stop,
        test_cap_limits_total,
        test_oldest_first_ordering,
        test_messages_without_attachments_ignored,
        test_empty_history_returns_empty,
        test_empty_referenced_ids_returns_empty,
        test_multiple_referenced_users,
    ):
        try:
            fn()
            print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            failures.append(f"{fn.__name__}: {e}")
            print(f"  ✗ {fn.__name__}")
    if failures:
        print("\nFailures:")
        for f in failures:
            print(f"  {f}")
        sys.exit(1)
    print("All 9 sibling-attachment selection tests passed.")


if __name__ == "__main__":
    main()
