#!/usr/bin/env python3
"""Self-reported Slack liveness on the bot's **App Home** tab.

Slack shows no presence for bots (setPresence returns ok but paints no dot for a
Socket Mode bot; profile-status is blocked for bot tokens). And editing a DM
message every few minutes is easy to miss and clutters history. So we publish a
dedicated **App Home** view for the owner and republish it every few minutes with
the last time the bridge's Socket Mode socket was confirmed live.

While the socket is up → "🟢 online — last alive HH:MM". Once the bridge heartbeat
goes stale → "🔴 may be offline" and the timestamp freezes, so a stale time really
means the agent is down/wedged.

Liveness source: the slack-bridge heartbeat file. NOTE: this is only a true
socket-liveness signal once #2387 ("gate heartbeat on live socket") lands — on
main the heartbeat is refreshed unconditionally by the result-watcher loop, so a
wedged socket could still read green. This indicator therefore DEPENDS on #2387;
until it merges, "online" means "bridge process alive", not "socket confirmed up".

views.publish needs the app's **Home Tab** enabled (Slack app → App Home → Home
Tab); no extra OAuth scope. The owner is resolved via the bridge's
`resolve_proactive_owner_id` (tofuOwner / owner-tier), never raw allowFrom order.

Run (daemon):  python3 src/slack-liveness.py [--user owner]
Options: --interval SEC (default 300) · --stale-sec SEC (default 120) ·
--heartbeat PATH · --once (single publish, for tests/smoke).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
try:
    from workspace_default import resolve_workspace  # noqa: E402
    _WS = Path(resolve_workspace())
except Exception:  # pragma: no cover
    _WS = _REPO / "workspace"  # pragma: no cover

DEFAULT_HEARTBEAT = _WS / "state" / "slack-bridge.heartbeat"
_SLACK = "https://slack.com/api"


def _bot_token() -> str | None:
    tok = os.environ.get("SLACK_BOT_TOKEN")
    if tok:
        return tok
    env = _WS / ".claude-sutando" / "channels" / "slack" / ".env"
    try:  # pragma: no cover — filesystem fallback; tests use the env var
        for line in env.read_text().splitlines():
            if line.strip().startswith("SLACK_BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:  # pragma: no cover
        pass
    return None  # pragma: no cover


def _resolve_owner_id() -> str | None:
    """The bridge's configured owner Slack id — NOT just allowFrom[0].

    Reuses the bridge's `resolve_proactive_owner_id`, which prefers `tofuOwner`
    and filters `tierMap` to owner-tier, so a collaborator listed before the
    owner in `allowFrom` never gets the liveness Home tab (qingyun-wu CR on #2427).
    """
    access = _WS / ".claude-sutando" / "channels" / "slack" / "access.json"
    try:
        data = json.loads(access.read_text())
    except (OSError, ValueError):
        return None
    try:
        from slack_owner import resolve_proactive_owner_id  # type: ignore
    except ImportError:  # pragma: no cover — src/ not importable
        allow = data.get("allowFrom") or []
        return str(allow[0]) if allow else None
    return resolve_proactive_owner_id(data)


def heartbeat_fresh(path, stale_sec: int, now: float) -> bool:
    """True if the bridge heartbeat exists and is younger than stale_sec — i.e.
    the Socket Mode socket was confirmed live within that window."""
    try:
        beat = int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return False
    return (now - beat) <= stale_sec


def build_home_view(alive: bool, last_alive_hhmm: str, interval_min: int) -> dict:
    """The App Home view (Block Kit) showing the liveness status."""
    if alive:
        header = ":large_green_circle:  Sutando — Online"
        detail = f"*Last alive:* {last_alive_hhmm}   ·   updates every ~{interval_min} min"
        note = f"If this time is more than ~{interval_min} min old, I may be offline."
    else:
        header = ":red_circle:  Sutando — may be offline"
        detail = f"*Last alive:* {last_alive_hhmm}"
        note = "The heartbeat went stale, so I'm likely down or my Slack socket wedged."
    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": header, "emoji": True}},
            {"type": "section", "text": {"type": "mrkdwn", "text": detail}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": note}]},
        ],
    }


def _api(token: str, method: str, payload: dict) -> dict:  # pragma: no cover — network boundary; tests monkeypatch this
    req = urllib.request.Request(  # pragma: no cover
        f"{_SLACK}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    return json.load(urllib.request.urlopen(req))  # pragma: no cover


def publish_home(token: str, user_id: str, view: dict) -> dict:
    """Publish (idempotent replace) the owner's App Home view. Stateless — no
    message ts to track, unlike the DM approach."""
    return _api(token, "views.publish", {"user_id": user_id, "view": view})


def tick(token: str, user_id: str, *, heartbeat, stale_sec: int, interval_min: int,
         now: float, last_alive_store: dict) -> dict:
    """One publish cycle. Advances the last-alive clock only while the heartbeat
    is fresh; freezes it (and flips to offline) once the bridge goes quiet."""
    alive = heartbeat_fresh(heartbeat, stale_sec, now)
    if alive:
        last_alive_store["hhmm"] = datetime.fromtimestamp(now).strftime("%H:%M")
    hhmm = last_alive_store.get("hhmm") or datetime.fromtimestamp(now).strftime("%H:%M")
    return publish_home(token, user_id, build_home_view(alive, hhmm, interval_min))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Self-reported Slack liveness on the App Home tab.")
    ap.add_argument("--user", default="owner",
                    help="Slack user id whose Home tab to publish, or 'owner' to resolve from access.json.")
    ap.add_argument("--interval", type=int, default=300, help="Seconds between updates (default 300).")
    ap.add_argument("--stale-sec", type=int, default=120, help="Heartbeat freshness threshold (default 120).")
    ap.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT))
    ap.add_argument("--once", action="store_true", help="Single publish then exit (smoke/test).")
    args = ap.parse_args(argv)

    token = _bot_token()
    if not token:
        print("slack-liveness: no SLACK_BOT_TOKEN (env or channels/slack/.env)", file=sys.stderr)
        return 1

    user = args.user
    if user == "owner":
        user = _resolve_owner_id()
        if not user:
            print("slack-liveness: no owner in access.json to publish a Home tab for", file=sys.stderr)
            return 1

    interval_min = max(1, round(args.interval / 60))
    store: dict = {}
    while True:  # pragma: no cover
        resp = tick(token, user, heartbeat=args.heartbeat, stale_sec=args.stale_sec,
                    interval_min=interval_min, now=time.time(), last_alive_store=store)
        if not resp.get("ok"):
            err = resp.get("error")
            hint = " (enable the app's Home Tab: Slack app → App Home → Home Tab)" if err == "not_enabled" else ""
            print(f"slack-liveness: views.publish failed: {err}{hint}", file=sys.stderr)
        if args.once:
            return 0 if resp.get("ok") else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
