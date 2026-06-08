#!/usr/bin/env python3
"""
Tests for the DM conversation-history injection added to discord-bridge.py.

When an owner sends a DM, the bridge fetches the last 15 messages before the
current one and appends them to the task body as a labelled history block.

Cases:
  Structural invariants (source-level checks):
    a) Guard condition: injection is gated on `is_dm and access_tier == "owner"`
    b) history() is called with `limit=15` and `before=message`
    c) Messages are reversed before appending (chronological order from newest-first)
    d) Delimiter markers present in source (`--- Recent conversation history ---`)

  Behavioral / formatting (inline replication of the formatting loop):
    e) Bot messages are labelled "Assistant"
    f) Owner's own messages are labelled "Owner"
    g) Other-user messages use their display name
    h) Messages with no text content use "[no text]" sentinel
    i) History block wraps with opening and closing delimiters
    j) Empty history (no prior messages) produces no history block

Run: python3 tests/discord-bridge-dm-history.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import datetime
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "discord-bridge.py"

# ---------------------------------------------------------------------------
# Structural tests — grep the source for key invariants
# ---------------------------------------------------------------------------

def case_a_guard_condition():
    """is_dm and access_tier == 'owner' must gate the history fetch."""
    fails = []
    source = SRC.read_text()
    if "is_dm and access_tier" not in source:
        fails.append("a) guard 'is_dm and access_tier' not found in source")
    # The guard must appear BEFORE the history() call
    guard_pos = source.find("is_dm and access_tier")
    history_pos = source.find("channel.history(limit=15")
    if guard_pos == -1 or history_pos == -1:
        fails.append("a) could not locate guard or history() call to check ordering")
    elif guard_pos > history_pos:
        fails.append("a) guard appears AFTER history() call — ownership check bypassed")
    return fails


def case_b_history_call_params():
    """history() must use limit=15 and before=message."""
    fails = []
    source = SRC.read_text()
    if "limit=15" not in source:
        fails.append("b) history(limit=15) not found — history depth may be wrong")
    if "before=message" not in source:
        fails.append("b) history(before=message) not found — may include current message")
    return fails


def case_c_chronological_order():
    """Messages are reversed (Discord history() returns newest-first)."""
    fails = []
    source = SRC.read_text()
    if "history_messages.reverse()" not in source:
        fails.append("c) history_messages.reverse() not found — messages may be newest-first")
    return fails


def case_d_delimiter_markers():
    """Opening and closing delimiter strings must be present."""
    fails = []
    source = SRC.read_text()
    opener = "--- Recent conversation history"
    closer = "--- End of history ---"
    if opener not in source:
        fails.append(f"d) opening delimiter {opener!r} not found in source")
    if closer not in source:
        fails.append(f"d) closing delimiter {closer!r} not found in source")
    return fails


# ---------------------------------------------------------------------------
# Behavioral tests — replicate the formatting loop with fake messages
# ---------------------------------------------------------------------------

def _fake_author(bot: bool = False, user_id: int = 999, name: str = "User") -> types.SimpleNamespace:
    return types.SimpleNamespace(bot=bot, id=user_id, name=name)


def _fake_message(
    author: types.SimpleNamespace,
    content: str,
    ts: datetime.datetime | None = None,
) -> types.SimpleNamespace:
    if ts is None:
        ts = datetime.datetime(2026, 6, 8, 12, 0, 0, tzinfo=datetime.timezone.utc)
    return types.SimpleNamespace(author=author, content=content, created_at=ts)


def _format_history(history_msgs: list, owner_id: int) -> str:
    """Replicate the inline history-formatting loop from discord-bridge.py."""
    formatted = []
    for hist_msg in history_msgs:
        ts = hist_msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        if hist_msg.author.bot:
            author = "Assistant"
        elif hist_msg.author.id == owner_id:
            author = "Owner"
        else:
            author = hist_msg.author.name
        content = hist_msg.content or "[no text]"
        formatted.append(f"[{ts}] {author}: {content}")

    if not formatted:
        return ""
    formatted.reverse()  # newest-first → chronological
    return (
        "\n\n--- Recent conversation history (last 15 messages) ---\n"
        + "\n".join(formatted)
        + "\n--- End of history ---\n"
    )


def case_e_bot_label():
    """Bot messages → label 'Assistant'."""
    fails = []
    msgs = [_fake_message(_fake_author(bot=True, name="Sutando"), "Hello!")]
    result = _format_history(msgs, owner_id=1)
    if "Assistant: Hello!" not in result:
        fails.append(f"e) bot message should be labelled 'Assistant', got: {result!r}")
    if "Sutando:" in result:
        fails.append(f"e) bot name should not appear; label should be 'Assistant', got: {result!r}")
    return fails


def case_f_owner_label():
    """Owner's own messages → label 'Owner'."""
    fails = []
    msgs = [_fake_message(_fake_author(user_id=42, name="Rui"), "What time is it?")]
    result = _format_history(msgs, owner_id=42)
    if "Owner: What time is it?" not in result:
        fails.append(f"f) owner message should be labelled 'Owner', got: {result!r}")
    if "Rui:" in result:
        fails.append(f"f) owner's name should not appear; label should be 'Owner', got: {result!r}")
    return fails


def case_g_other_user_display_name():
    """Messages from a third party use their display name."""
    fails = []
    msgs = [_fake_message(_fake_author(user_id=99, name="Alice"), "Hey there")]
    result = _format_history(msgs, owner_id=42)
    if "Alice: Hey there" not in result:
        fails.append(f"g) third-party message should use display name, got: {result!r}")
    return fails


def case_h_no_text_sentinel():
    """Empty message content → '[no text]'."""
    fails = []
    msgs = [_fake_message(_fake_author(user_id=42), "")]
    result = _format_history(msgs, owner_id=42)
    if "[no text]" not in result:
        fails.append(f"h) empty content should become '[no text]', got: {result!r}")
    return fails


def case_i_delimiter_wrapping():
    """History block has opening and closing delimiter lines."""
    fails = []
    msgs = [_fake_message(_fake_author(user_id=42), "ping")]
    result = _format_history(msgs, owner_id=42)
    if "--- Recent conversation history" not in result:
        fails.append(f"i) opening delimiter missing, got: {result!r}")
    if "--- End of history ---" not in result:
        fails.append(f"i) closing delimiter missing, got: {result!r}")
    return fails


def case_j_empty_history_no_block():
    """No prior messages → empty string (no history block appended to task)."""
    fails = []
    result = _format_history([], owner_id=42)
    if result:
        fails.append(f"j) empty history should produce empty string, got: {result!r}")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_guard_condition),
        ("b", case_b_history_call_params),
        ("c", case_c_chronological_order),
        ("d", case_d_delimiter_markers),
        ("e", case_e_bot_label),
        ("f", case_f_owner_label),
        ("g", case_g_other_user_display_name),
        ("h", case_h_no_text_sentinel),
        ("i", case_i_delimiter_wrapping),
        ("j", case_j_empty_history_no_block),
    ]
    all_failures = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as e:
            fails = [f"{label}) raised {type(e).__name__}: {e}"]
        if fails:
            all_failures.extend(fails)
            print(f"  FAIL case {label}")
            for f in fails:
                print(f"    {f}")
        else:
            print(f"  PASS case {label}")

    total = len(cases)
    failed = len(all_failures)
    print(f"\nResults: {total - failed}/{total} passed")
    return 1 if all_failures else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
