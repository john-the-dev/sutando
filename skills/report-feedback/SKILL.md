---
name: report-feedback
description: File a bug report, feature request, or feedback about Sutando to the team from any surface (chat, Discord, Telegram, or a voice-delegated task). Reuses the cloud /api/feedback API and auto-attaches diagnostic context. Use when the user says "report a bug", "something's broken, file it", "I have a feature request", etc.
---

# Report Feedback

When the user asks to **report a bug / issue / feature request / feedback about Sutando itself** — e.g. "report a bug", "something's broken, file it", "I have a feature request" — use this skill to file it.

It posts to the cloud `/api/feedback` route (the same one the desktop "Report an issue" form uses, which mirrors into GitHub issues) and auto-attaches diagnostic context (platform + a tail of recent workspace logs), so you don't need to gather logs yourself.

This is the **single reporting path for all surfaces** — chat, Discord, Telegram, and voice (which reaches it by delegating the task to the core agent). There is intentionally no separate voice tool, to avoid duplicating the same capability.

## Usage

```bash
python3 skills/report-feedback/report-feedback.py \
  --title "<short one-line summary>" \
  [--body "<what happened, steps to reproduce, what was expected>"] \
  [--kind bug|feature|other] \
  [--severity low|medium|high|critical] \
  [--no-logs]
```

- Ask the user for a short **title** and a **description** if they're not already clear from the conversation. Infer `kind` (default `bug`) and `severity` (default `medium`) from context.
- Pass `--no-logs` if the user prefers not to attach diagnostic logs.

## Behavior

- Requires the user to be **signed in to Sutando Cloud** (Settings → Sutando Cloud). If not, the script prints `NOT_SIGNED_IN` and exits 2 — relay that and ask them to sign in, then retry.
- On success it prints `OK: filed <kind> report`. On API error it prints `ERROR: …` — relay a brief apology and offer to retry.

## Access tier

**Owner-tier only** — it files under the owner's Sutando Cloud identity. Do not run it for non-owner (team/other) Discord or Slack tiers.
