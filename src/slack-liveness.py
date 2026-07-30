#!/usr/bin/env python3
"""Self-reported Slack "last alive" indicator.

Slack does not render presence for bots (see reference: setPresence returns ok
but paints no dot for a Socket Mode bot; profile-status is blocked for bot
tokens). So instead of a green dot, we self-report: keep ONE message in the
owner's DM and refresh it in place (chat.update) every few minutes with the last
time the bridge's Socket Mode socket was confirmed live. If the timestamp stops
advancing (message goes stale), the agent is offline — the exact signal the
owner asked for.

Liveness source: the slack-bridge heartbeat file, which is written only while the
socket is actually up. A fresh heartbeat = live socket; a stale one = down/wedged,
so we flip the message to "may be offline" and freeze the last-alive time.

Run (daemon):
    python3 src/slack-liveness.py --channel <owner-dm-id>
Options: --interval SEC (default 300) · --stale-sec SEC (heartbeat freshness,
default 120) · --heartbeat PATH · --state PATH · --once (single update, for tests
/ smoke). Token from $SLACK_BOT_TOKEN or channels/slack/.env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Make repo src/ importable for the workspace resolver.
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))
try:
    from workspace_default import resolve_workspace  # noqa: E402
    _WS = Path(resolve_workspace())
except Exception:  # pragma: no cover
    _WS = _REPO / "workspace"  # pragma: no cover

DEFAULT_HEARTBEAT = _WS / "state" / "slack-bridge.heartbeat"
DEFAULT_STATE = _WS / "state" / "slack-liveness.json"
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


def heartbeat_fresh(path, stale_sec: int, now: float) -> bool:
    """True if the bridge heartbeat exists and is younger than stale_sec.

    The heartbeat records the last epoch the Socket Mode socket was confirmed
    live, so freshness here means the bridge is actually connected.
    """
    try:
        beat = int(Path(path).read_text().strip())
    except (OSError, ValueError):
        return False
    return (now - beat) <= stale_sec


def compose_message(alive: bool, last_alive_hhmm: str, interval_min: int) -> str:
    """The single-line status text. `last_alive_hhmm` is the last time the socket
    was confirmed live (frozen once the bridge goes quiet)."""
    if alive:
        return (f":large_green_circle: *Sutando — online*  ·  last alive *{last_alive_hhmm}*  ·  "
                f"updates every ~{interval_min} min "
                f"(if this time is more than ~{interval_min} min old, I may be offline)")
    return (f":red_circle: *Sutando — may be offline*  ·  last alive *{last_alive_hhmm}*  ·  "
            f"the heartbeat went stale, so I'm likely down or my Slack socket wedged")


def _api(token: str, method: str, payload: dict) -> dict:  # pragma: no cover — network boundary; tests monkeypatch this
    req = urllib.request.Request(  # pragma: no cover
        f"{_SLACK}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    return json.load(urllib.request.urlopen(req))  # pragma: no cover


def _owner_ids_from_access() -> list:
    """Owner Slack user IDs from the bridge's access.json allowFrom (may be empty)."""
    access = _WS / ".claude-sutando" / "channels" / "slack" / "access.json"
    try:
        data = json.loads(access.read_text())
    except (OSError, ValueError):
        return []
    allow = data.get("allowFrom", [])
    return [str(u) for u in allow] if isinstance(allow, list) else []


def resolve_channel(token: str, channel: str) -> str | None:
    """Pass a literal channel id through; resolve the sentinel "owner" to the
    owner's DM channel via conversations.open (needs im:write, which the bridge
    already has). Returns None if it can't be resolved."""
    if channel != "owner":
        return channel
    ids = _owner_ids_from_access()
    if not ids:
        return None
    resp = _api(token, "conversations.open", {"users": ids[0]})  # pragma: no cover — network
    return (resp.get("channel") or {}).get("id") if resp.get("ok") else None  # pragma: no cover


def _load_state(path) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _save_state(path, data: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".json.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    os.replace(tmp, p)


def post_or_update(token: str, channel: str, text: str, state_path) -> dict:
    """Post the status message once, then chat.update the same message thereafter.
    Reposts fresh if the stored message was deleted. Returns the API response."""
    state = _load_state(state_path)
    ts = state.get("ts") if state.get("channel") == channel else None
    if ts:
        resp = _api(token, "chat.update", {"channel": channel, "ts": ts, "text": text})
        if resp.get("ok"):
            return resp
        # Message gone (deleted) — fall through to a fresh post.
    resp = _api(token, "chat.postMessage", {"channel": channel, "text": text})
    if resp.get("ok"):
        _save_state(state_path, {"channel": channel, "ts": resp.get("ts")})
    return resp


def tick(token: str, channel: str, *, heartbeat, state_path, stale_sec: int,
         interval_min: int, now: float, last_alive_store: dict) -> dict:
    """One update cycle. Advances the last-alive clock only while the heartbeat is
    fresh; freezes it (and flips to offline) once the bridge goes quiet."""
    alive = heartbeat_fresh(heartbeat, stale_sec, now)
    if alive:
        last_alive_store["hhmm"] = datetime.fromtimestamp(now).strftime("%H:%M")
    hhmm = last_alive_store.get("hhmm") or datetime.fromtimestamp(now).strftime("%H:%M")
    text = compose_message(alive, hhmm, interval_min)
    return post_or_update(token, channel, text, state_path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Self-reported Slack liveness indicator.")
    ap.add_argument("--channel", required=True,
                    help="Channel id to post into, or the sentinel 'owner' to auto-resolve the owner DM.")
    ap.add_argument("--interval", type=int, default=300, help="Seconds between updates (default 300).")
    ap.add_argument("--stale-sec", type=int, default=120, help="Heartbeat freshness threshold (default 120).")
    ap.add_argument("--heartbeat", default=str(DEFAULT_HEARTBEAT))
    ap.add_argument("--state", default=str(DEFAULT_STATE))
    ap.add_argument("--once", action="store_true", help="Single update then exit (smoke/test).")
    args = ap.parse_args(argv)

    token = _bot_token()
    if not token:
        print("slack-liveness: no SLACK_BOT_TOKEN (env or channels/slack/.env)", file=sys.stderr)
        return 1

    channel = resolve_channel(token, args.channel)
    if not channel:
        print("slack-liveness: could not resolve channel "
              f"'{args.channel}' (no owner in access.json?)", file=sys.stderr)
        return 1

    interval_min = max(1, round(args.interval / 60))
    store: dict = {}
    while True:  # pragma: no cover
        resp = tick(token, channel, heartbeat=args.heartbeat, state_path=args.state,
                    stale_sec=args.stale_sec, interval_min=interval_min,
                    now=time.time(), last_alive_store=store)
        if not resp.get("ok"):
            print(f"slack-liveness: update failed: {resp.get('error')}", file=sys.stderr)
        if args.once:
            return 0 if resp.get("ok") else 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
