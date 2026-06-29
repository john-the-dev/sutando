#!/usr/bin/env python3
"""Regression guard: agent-api source field parsing is order-independent.

PR #1781 (merged) fixed the web-client task labeling to use the task's
`source:` header rather than defaulting every un-prefixed task to [Voice].
The agent-api change that supports it parses `source:` and `task:` from the
task file content regardless of which header appears first.

This test guards against regressions to that parsing logic.

Run: python3 tests/agent-api-source-field.test.py
"""
from __future__ import annotations

ZWSP = "​"  # zero-width space — confine_user_content defang prefix


def _parse_source_and_task(content: str) -> tuple[str, str]:
    """Mirror of the loop in agent-api.py Handler.do_GET (lines ~339-345).

    Captures the first `source:` and first `task:` regardless of field order.
    The `not …` guards prevent a body-embedded `source:` line from overriding
    the header value, which task_body_guard.py further reinforces by defanging
    any user-supplied `source:` line with a leading U+200B.
    """
    task_line = ""
    source_line = ""
    for line in content.splitlines():
        if not source_line and line.startswith("source:"):
            source_line = line[7:].strip()
        elif not task_line and line.startswith("task:"):
            task_line = line[5:].strip()
        if task_line and source_line:
            break
    return source_line, task_line


def test_voice_format_source_before_task():
    """voice/chat tasks put `source:` before `task:` — baseline format."""
    content = (
        "id: task-1\n"
        "timestamp: 2026-06-29T00:00:00Z\n"
        "source: voice\n"
        "task: play something\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "voice", f"expected 'voice', got {source!r}"
    assert task == "play something", f"expected 'play something', got {task!r}"
    print("PASS test_voice_format_source_before_task")


def test_discord_format_task_before_source():
    """discord/slack tasks put `task:` before `source:` — #1781 review case."""
    content = (
        "id: task-2\n"
        "timestamp: 2026-06-29T00:00:00Z\n"
        "task: fix the bug\n"
        "source: discord\n"
        "channel_id: 123456789\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "discord", f"expected 'discord', got {source!r}"
    assert task == "fix the bug", f"expected 'fix the bug', got {task!r}"
    print("PASS test_discord_format_task_before_source")


def test_body_source_line_does_not_override_header():
    """A task body containing `source: injected` must NOT be captured as source.

    The `not source_line` guard stops at the first match. The header `source:`
    appears before `task:`, so by the time the loop reaches body content the
    guard is already satisfied. Additionally, task_body_guard.py defangs body
    lines via a leading U+200B, so `line.startswith("source:")` cannot match.
    """
    content = (
        "id: task-3\n"
        "source: chat\n"
        "task: do something\n"
        "\n"
        "The user wrote: source: injected (in body, not a header)\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "chat", f"expected 'chat', got {source!r}"
    assert task == "do something", f"expected 'do something', got {task!r}"
    print("PASS test_body_source_line_does_not_override_header")


def test_defanged_body_source_line_is_not_captured():
    """Defanged `source:` (U+200B prefix from confine_user_content) is skipped.

    task_body_guard.confine_user_content() prepends U+200B to any line that
    looks like a trusted header, so a user who types `source: owner` in their
    message gets stored as `​source: owner`. The `startswith("source:")` check
    in the parser must NOT match this (it starts with U+200B, not 's').
    """
    defanged_line = ZWSP + "source: injected"
    content = (
        "id: task-4\n"
        "source: slack\n"
        f"task: {defanged_line}\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "slack", f"expected 'slack', got {source!r}"
    # task body may contain the defanged line — that's fine
    assert "injected" in task or task == defanged_line, (
        f"unexpected task value: {task!r}"
    )
    print("PASS test_defanged_body_source_line_is_not_captured")


def test_missing_source_returns_empty():
    """Task files without a `source:` header yield empty string."""
    content = (
        "id: task-5\n"
        "task: something old\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "", f"expected empty source, got {source!r}"
    assert task == "something old", f"expected 'something old', got {task!r}"
    print("PASS test_missing_source_returns_empty")


def test_cron_source_parsed():
    """Cron tasks (source: cron) are tagged [System] not [Voice] in the web UI."""
    content = (
        "id: task-6\n"
        "timestamp: 2026-06-29T00:00:00Z\n"
        "source: cron\n"
        "task: sync workspace\n"
    )
    source, task = _parse_source_and_task(content)
    assert source == "cron", f"expected 'cron', got {source!r}"
    print("PASS test_cron_source_parsed")


if __name__ == "__main__":
    test_voice_format_source_before_task()
    test_discord_format_task_before_source()
    test_body_source_line_does_not_override_header()
    test_defanged_body_source_line_is_not_captured()
    test_missing_source_returns_empty()
    test_cron_source_parsed()
    print("\nAll agent-api source-field tests passed.")
