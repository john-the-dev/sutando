---
name: checklist-respond
description: "Render Discord button checklists from [checklist] markers in bot replies. Handles button clicks with per-voter state persistence."
user-invocable: false
---

# Checklist Respond

Adds interactive button checklists to Discord bot replies. When a result contains a `[checklist]` marker, discord-bridge renders the items as clickable Discord buttons instead of plain text. Clicks are tracked per voter.

## Usage (in bot replies)

Inline form:
```
[checklist: Yes | No | Maybe]
```

Block form (with markdown list below):
```
Pick a direction:
[checklist]
- Option A
- Option B
- Option C
```

Original reply text is preserved above the buttons (mis-fires are visible and non-blocking).

## Click behavior

- Any allowlisted Discord user can click
- Clicking again toggles the selection off
- Vote summary shows per-voter side-by-side: `• Item — ✅ Alice, ✅ Bob`
- State persisted to `$SUTANDO_WORKSPACE/state/checklists/<msg_id>.json`

## Architecture

- `scripts/checklist_render.py` — pure helpers (parse, build, state, render). No I/O.
- `src/discord-bridge.py` — `on_interaction` handler + `_maybe_post_checklist` intercept in `poll_results`.
- Gracefully absent: `_CHECKLIST_SKILL_AVAILABLE` flag; bridge starts normally if skill not installed.

## Phases

- **Phase 1 (this PR)**: explicit `[checklist]` opt-in marker
- **Phase 2 (future)**: auto-detect heuristic for lettered-option / yes-no / ≥3-item list replies

Design from issue #1104 (Lucy + Chi, 2026-05-25).
