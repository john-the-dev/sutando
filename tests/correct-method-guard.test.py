#!/usr/bin/env python3
"""Behavioral test for hooks/correct-method-guard.py — feeds real PreToolUse
payloads through the hook and asserts deny/allow + the correct redirect.

Run: python3 tests/correct-method-guard.test.py   (exit 0 pass / 1 fail)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "hooks" / "correct-method-guard.py"

_failed = 0


def run(payload: dict, env_extra: dict | None = None):
    """Invoke the hook with a PreToolUse payload; return (decision, reason)."""
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    p = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=15,
    )
    assert p.returncode == 0, f"hook must always exit 0 (got {p.returncode}); stderr={p.stderr}"
    out = (p.stdout or "").strip()
    if not out:
        return (None, None)
    try:
        j = json.loads(out)
        hso = j.get("hookSpecificOutput", {})
        return (hso.get("permissionDecision"), hso.get("permissionDecisionReason", ""))
    except Exception:
        return (None, out)


def check(name: str, cond: bool, detail: str = ""):
    global _failed
    print(("  ok  " if cond else "  FAIL ") + name + (("" if cond else " — " + detail)))
    if not cond:
        _failed += 1


# 1) Google Drive MCP → denied, points at the service account.
d, r = run({"tool_name": "mcp__claude_ai_Google_Drive__search_files", "tool_input": {"query": "deck"}})
check("Google Drive MCP is denied", d == "deny", f"got {d}")
check("Drive deny mentions the service account", "service account" in (r or "").lower())

d, r = run({"tool_name": "mcp__claude_ai_Google_Drive__download_file_content", "tool_input": {"fileId": "x"}})
check("Google Drive MCP download is denied", d == "deny", f"got {d}")

# 2) Composio Gmail (toolkit=gmail) → denied, points at IMAP.
d, r = run({"tool_name": "mcp__sutando-station__composio_exec",
            "tool_input": {"toolkit": "gmail", "action": "GMAIL_FETCH_EMAILS", "arguments": {}}})
check("Composio Gmail is denied", d == "deny", f"got {d}")
check("Gmail deny mentions IMAP + X-GM-MSGID", "imap" in (r or "").lower() and "x-gm-msgid" in (r or "").lower())

d, r = run({"tool_name": "mcp__sutando-station__composio_find", "tool_input": {"toolkit": "gmail", "query": "attachment"}})
check("Composio Gmail find is denied", d == "deny", f"got {d}")

# 3) Name-based Gmail MCP tool → denied (defense in depth).
d, r = run({"tool_name": "mcp__some_server__gmail_get_message", "tool_input": {}})
check("Name-based Gmail MCP tool is denied", d == "deny", f"got {d}")

# 4) Composio NON-gmail toolkits pass through (calendar, slack).
d, _ = run({"tool_name": "mcp__sutando-station__composio_exec",
            "tool_input": {"toolkit": "googlecalendar", "action": "GOOGLECALENDAR_EVENTS_LIST"}})
check("Composio Calendar passes through", d is None, f"got {d}")
d, _ = run({"tool_name": "mcp__sutando-station__composio_exec", "tool_input": {"toolkit": "slack"}})
check("Composio Slack passes through", d is None, f"got {d}")

# 5) Unrelated tools pass through.
d, _ = run({"tool_name": "Bash", "tool_input": {"command": "ls"}})
check("Bash passes through", d is None, f"got {d}")
d, _ = run({"tool_name": "mcp__claude_ai_Slack__slack_send_message", "tool_input": {}})
check("Slack MCP passes through", d is None, f"got {d}")
# An unrelated 'drive' that is not Google Drive must not be caught.
d, _ = run({"tool_name": "mcp__some_server__usb_drive_list", "tool_input": {}})
check("Non-Google 'drive' tool passes through", d is None, f"got {d}")

# 6) Escape hatch disables the guard.
d, _ = run({"tool_name": "mcp__claude_ai_Google_Drive__search_files", "tool_input": {}},
           env_extra={"SUTANDO_ALLOW_GOOGLE_CONNECTORS": "1"})
check("Escape hatch lifts the Drive guard", d is None, f"got {d}")

# 7) Fail-open on malformed input (never wedge the core).
p = subprocess.run([sys.executable, str(HOOK)], input="not-json{", capture_output=True, text=True, timeout=15)
check("Malformed input fails open (exit 0)", p.returncode == 0, f"rc={p.returncode}")

print()
if _failed:
    print(f"FAIL — {_failed} check(s) failed")
    sys.exit(1)
print("PASS — correct-method-guard behavioral tests")
