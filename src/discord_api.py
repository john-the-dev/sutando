"""Canonical Discord REST API helper shared by src/ modules.

All JSON-body Discord API calls in src/ go through this one implementation.
Call sites that need multipart/form-data (file uploads) build their requests
manually — this helper is JSON-only.
"""
from __future__ import annotations

import json
import urllib.request

API_BASE = "https://discord.com/api/v10"
USER_AGENT = "Sutando/1.0"


def discord_api(method: str, path: str, token: str, body=None):
    """Make a Discord REST API call. Returns parsed JSON on 2xx, raises on error.

    path   — API path, e.g. "/channels/123/messages"
    token  — Bot token (without "Bot " prefix; this function prepends it)
    body   — Optional dict JSON-encoded as application/json; omit for GET
    """
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bot {token}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None
