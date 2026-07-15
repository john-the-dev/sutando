#!/usr/bin/env python3
"""correct-method-guard — PreToolUse hook that denies the wrong Google
integrations and redirects to the documented, working paths.

Why (recurring field report 64340119 / 14015ea1, michael@actoneventures.com,
team-wide, latest 2026-07-15): the core keeps reaching for the **Google Drive
MCP** and the **Gmail MCP connector** even though those paths are broken for the
real jobs, and re-learns the lesson only after being told. The two correct paths
(established repeatedly by Michael) are:

  1. Google Drive  -> the **service account** (docs/built-in-tools.md), NOT the
     Google Drive MCP (search is hook-blocked / unreliable on these installs).
  2. Email + attachments -> **IMAP** via ``X-GM-MSGID`` + the vaulted
     ``GMAIL_APP_PASSWORD``, NOT the Gmail MCP connector (which exposes no
     attachment-download method, so attachment jobs dead-end and the core
     reports itself stuck).

Written rules (capability memories, feedback memories, CLAUDE.md guidance) have
failed many times because they depend on the model *recalling* them at
tool-selection time — and on one occasion the very memory that carried the IMAP
technique was stranded in a backup tree and never loaded into the live index.
The durable fix is the same one that already works for Gmail writes
(gmail-write-guard.py): remove the wrong tool from the menu. This hook denies the
wrong calls **before they run** and hands back the correct path in the reason.

Scope — deliberately narrow, so it never blocks a correct call:
  * **Google Drive MCP**: any ``mcp__…`` tool whose server segment names Google
    Drive (``google_drive`` / ``gdrive``). Non-Google "drive" tools pass through.
  * **Gmail connector**: the Composio bridge (``mcp__sutando-station__composio_exec``
    / ``…composio_find``) invoked with ``toolkit == "gmail"`` — the Gmail-MCP
    surface on these installs. Other toolkits (googlecalendar, slack, …) pass
    through untouched. (Name-based claude.ai Gmail *write* tools are already
    handled by the sibling gmail-write-guard.py.)
  * Everything else: no-op (exit 0), safe under a broad matcher.

Escape hatch: set ``SUTANDO_ALLOW_GOOGLE_CONNECTORS=1`` to disable the guard
(e.g. on an install where the connector paths actually work, or the service
account / IMAP creds are not provisioned).

Fail-OPEN on any error — a crashing hook must never wedge the core (same
contract as gmail-write-guard.py / skip-ask-user-question.py).

Registration: manual per-node deploy like context-source-guard.py — see
hooks/README.md.
"""
import json
import os
import sys

DRIVE_REASON = (
    "Google Drive MCP is blocked on this install: the Drive connector is "
    "unreliable / search-hook-blocked here and repeatedly dead-ends real jobs. "
    "Use the Drive **service account** instead (docs/built-in-tools.md -> Google "
    "Drive: the gdrive service-account script with the vaulted key). If a task "
    "needs an email ATTACHMENT, that is not a Drive job either — fetch it over "
    "**IMAP** (see below). Set SUTANDO_ALLOW_GOOGLE_CONNECTORS=1 to lift this "
    "guard. [correct-method-guard]"
)

GMAIL_REASON = (
    "The Gmail MCP connector is blocked on this install: it exposes no "
    "attachment-download method, so email/attachment jobs dead-end here. Use "
    "**IMAP** instead: connect with imaplib + the vaulted GMAIL_APP_PASSWORD, "
    "locate the message by its X-GM-MSGID, and fetch the body/attachment "
    "(docs/built-in-tools.md -> Email). This is the path that has worked every "
    "time. Set SUTANDO_ALLOW_GOOGLE_CONNECTORS=1 to lift this guard. "
    "[correct-method-guard]"
)


def _is_google_drive_mcp(tool_name: str) -> bool:
    """True for an MCP tool whose server segment is the Google Drive connector."""
    if not tool_name.startswith("mcp__"):
        return False
    lowered = tool_name.lower()
    # Match the Google Drive connector specifically (e.g.
    # mcp__claude_ai_Google_Drive__search_files) — not an unrelated "drive".
    return "google_drive" in lowered or "gdrive" in lowered or "googledrive" in lowered


def _is_gmail_connector(tool_name: str, tool_input: dict) -> bool:
    """True for a Gmail call routed through the Composio bridge (toolkit=gmail),
    or any other MCP tool whose name carries 'gmail'."""
    lowered = tool_name.lower()
    if lowered.startswith("mcp__") and "gmail" in lowered:
        return True
    # Composio bridge: the tool name is composio_exec/composio_find; the Gmail-ness
    # lives in the arguments (toolkit=gmail). Other toolkits pass through.
    if lowered.startswith("mcp__") and "composio" in lowered:
        toolkit = tool_input.get("toolkit")
        if isinstance(toolkit, str) and toolkit.strip().lower() == "gmail":
            return True
    return False


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}))


def main() -> None:
    if os.environ.get("SUTANDO_ALLOW_GOOGLE_CONNECTORS", "").strip() == "1":
        sys.exit(0)
    data = json.loads(sys.stdin.read())
    tool_name = str(data.get("tool_name") or "")
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        tool_input = {}
    if _is_google_drive_mcp(tool_name):
        _deny(DRIVE_REASON)
    elif _is_gmail_connector(tool_name, tool_input):
        _deny(GMAIL_REASON)
    # Everything else (and the deny above) exits 0; PreToolUse only blocks when a
    # deny decision is present in the emitted JSON payload.
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # fail-open: never wedge the core on a hook error
        print(f"[correct-method-guard] non-fatal error, allowing: {e}", file=sys.stderr)
        sys.exit(0)
