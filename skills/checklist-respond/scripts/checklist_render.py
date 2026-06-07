"""Render a checklist as Discord Components V2 buttons and manage click state.

Used by discord-bridge.py's on_interaction handler and the [checklist] marker
post-processor. Pure helpers — no I/O, fully unit-testable.

Design from issue #1104 (Lucy + Chi 2026-05-25):
- Phase 1: explicit [checklist] opt-in marker (this module)
- Phase 2: auto-detect heuristic (future)
- Owner pref: preserve original text below buttons, voter-per-row style
- MVP rollout: [checklist] marker → list items become buttons
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# Maximum buttons per Discord row (Discord API limit).
_ROW_LIMIT = 5
# Maximum rows per message (Discord API limit).
_MAX_ROWS = 5
# Prefix stored in button custom_id to identify our checklist interactions.
CUSTOM_ID_PREFIX = "ck:"


def parse_checklist_marker(text: str) -> Optional[tuple[list[str], str]]:
    """Parse a [checklist] marker from bot reply text.

    Returns (items, body_without_marker) or None if no marker.

    Supported forms:
      [checklist: item1 | item2 | item3]  — inline pipe-separated
      [checklist]                          — uses the markdown list below the marker
    """
    # Inline form: [checklist: a | b | c]
    m = re.search(r'\[checklist:\s*([^\]]+)\]', text, re.IGNORECASE)
    if m:
        raw = m.group(1)
        items = [i.strip() for i in raw.split("|") if i.strip()]
        body = text[:m.start()].rstrip() + "\n" + text[m.end():].lstrip()
        return items, body.strip()

    # Bare marker [checklist] followed by a markdown list
    m = re.search(r'\[checklist\]', text, re.IGNORECASE)
    if m:
        after = text[m.end():]
        items = re.findall(r'^\s*[-*]\s+(.+)$', after, re.MULTILINE)
        if items:
            body = text[:m.start()].rstrip()
            return items, body.strip()

    return None


def build_view_spec(items: list[str], msg_id: str) -> list[dict]:
    """Return a serialisable list of row-specs for the Discord view.

    Each row-spec: {"buttons": [{"label": str, "custom_id": str, "style": str}]}
    The caller (discord-bridge.py) converts this into discord.ui.View buttons.

    custom_id format: "ck:<msg_id>:<item_index>"
    """
    rows: list[dict] = []
    row_buttons: list[dict] = []
    for idx, item in enumerate(items):
        if len(row_buttons) >= _ROW_LIMIT:
            rows.append({"buttons": row_buttons})
            row_buttons = []
        if len(rows) >= _MAX_ROWS:
            break  # Discord hard cap
        row_buttons.append({
            "label": item[:80],  # Discord button label limit
            "custom_id": f"{CUSTOM_ID_PREFIX}{msg_id}:{idx}",
            "style": "secondary",  # grey = unchecked default
        })
    if row_buttons:
        rows.append({"buttons": row_buttons})
    return rows


def load_state(state_dir: Path, msg_id: str) -> dict:
    """Load persisted checklist state for a message. Returns {} if missing."""
    p = state_dir / "checklists" / f"{msg_id}.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def save_state(state_dir: Path, msg_id: str, state: dict) -> None:
    """Persist checklist click state for a message."""
    d = state_dir / "checklists"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{msg_id}.json").write_text(json.dumps(state))


def apply_click(
    state: dict,
    item_idx: int,
    clicker_id: str,
    clicker_name: str,
    items: list[str],
) -> dict:
    """Record a button click and return the updated state.

    State schema:
      {"items": [str, ...], "votes": {"<item_idx>": {"<user_id>": "<name>", ...}}}

    Per owner pref (#1104 comment): per-voter side-by-side, not aggregate tally.
    Clicking again toggles off (deselects).
    """
    state.setdefault("items", items)
    votes: dict[str, dict] = state.setdefault("votes", {})
    key = str(item_idx)
    item_votes: dict = votes.setdefault(key, {})
    if clicker_id in item_votes:
        del item_votes[clicker_id]  # toggle off
    else:
        item_votes[clicker_id] = clicker_name
    return state


def render_vote_summary(state: dict) -> str:
    """Render the current vote state as plain text for the message body.

    Format (per owner pref — per-voter side-by-side):
      • Item text — ✅ Alice, ❌ Bob
    Items with no votes are rendered without a vote suffix.
    """
    items: list[str] = state.get("items", [])
    votes: dict = state.get("votes", {})
    lines = []
    for idx, item in enumerate(items):
        item_votes = votes.get(str(idx), {})
        if item_votes:
            voter_tags = ", ".join(
                f"✅ {name}" for name in item_votes.values()
            )
            lines.append(f"• {item} — {voter_tags}")
        else:
            lines.append(f"• {item}")
    return "\n".join(lines)


def parse_custom_id(custom_id: str) -> Optional[tuple[str, int]]:
    """Parse custom_id → (msg_id, item_idx) or None if not ours."""
    if not custom_id.startswith(CUSTOM_ID_PREFIX):
        return None
    rest = custom_id[len(CUSTOM_ID_PREFIX):]
    parts = rest.rsplit(":", 1)
    if len(parts) != 2:
        return None
    try:
        return parts[0], int(parts[1])
    except ValueError:
        return None
