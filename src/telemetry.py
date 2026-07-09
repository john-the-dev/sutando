#!/usr/bin/env python3
"""Anonymous, opt-out product telemetry for Sutando (PostHog).

Sutando is open source; the desktop app runs the same core. Instrumenting the
core (not just the app) lets maintainers see how many people run Sutando and
which features they use — WITHOUT ever collecting who they are or what they do.

What is sent
------------
Only bucketed / categorical PRODUCT events (e.g. ``core_started``,
``feature_used {feature: ...}``). Never task content, message text, prompts,
logs, file paths, or any PII. See ``TELEMETRY.md`` for the exact list.

How it is sent
--------------
A best-effort JSON POST to PostHog's ``/capture`` endpoint over the standard
library (no third-party dependency), fired in a daemon thread so it can never
block or crash the app. Any error is swallowed.

Opting out (checked live on every call — never cached)
------------------------------------------------------
Set ANY of the following and all telemetry becomes a silent no-op:

* ``DO_NOT_TRACK=1``     — the cross-project standard (Astro, Bun, Prisma, …)
* ``SUTANDO_TELEMETRY=0``
* a file at ``<workspace>/state/telemetry-disabled``

Identity
--------
A random per-install UUID persisted at ``<workspace>/state/telemetry-id``. It
is not a device fingerprint and is not tied to any account, email, or IP.

Config
------
* ``POSTHOG_API_KEY`` — the PostHog *project* key. ``phc_...`` keys are public
  and write-only, so one may be embedded in ``_EMBEDDED_KEY`` below for
  distribution. Absent a key, telemetry is a no-op.
* ``POSTHOG_HOST``    — defaults to the EU cloud (``https://eu.i.posthog.com``).
* ``SUTANDO_DEBUG_TELEMETRY=1`` — print every event to stderr before sending.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request
import uuid
from pathlib import Path

# ``phc_...`` PostHog project keys are PUBLIC and write-only — safe to embed in
# open-source source/binaries. Paste the project key here to enable telemetry
# for distributed builds; forks/self-hosters override via POSTHOG_API_KEY.
_EMBEDDED_KEY = ""

_KEY = (os.environ.get("POSTHOG_API_KEY") or _EMBEDDED_KEY).strip()
_HOST = (os.environ.get("POSTHOG_HOST") or "https://eu.i.posthog.com").rstrip("/")

_TRUTHY = {"1", "true", "yes", "on"}


def _state_dir() -> Path:
    """`<workspace>/state`, resolved via the M0 helper when importable."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from workspace_default import resolve_workspace  # noqa: E402

        return Path(resolve_workspace()) / "state"
    except Exception:
        base = os.environ.get("SUTANDO_STATE_DIR")
        if base:
            return Path(base)
        return Path.home() / ".sutando" / "repo" / "workspace" / "state"


def opted_out() -> bool:
    """True if the user has opted out via env var or the disable file.

    Checked on every ``capture`` call (never cached) so toggling it takes
    effect immediately — the bug class that has bitten other OSS projects
    whose opt-out flag was read once at import.
    """
    if os.environ.get("DO_NOT_TRACK", "").strip().lower() in _TRUTHY:
        return True
    if os.environ.get("SUTANDO_TELEMETRY", "").strip().lower() in {"0", "false", "no", "off"}:
        return True
    try:
        if (_state_dir() / "telemetry-disabled").exists():
            return True
    except Exception:
        pass
    return False


def _distinct_id() -> str:
    """Stable random per-install id (not a fingerprint, not PII)."""
    try:
        d = _state_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / "telemetry-id"
        if f.exists():
            got = f.read_text().strip()
            if got:
                return got
        new = uuid.uuid4().hex
        f.write_text(new)
        return new
    except Exception:
        return "anonymous"


def enabled() -> bool:
    """Telemetry fires only when a key is configured AND not opted out."""
    return bool(_KEY) and not opted_out()


def _post(payload: dict) -> None:
    try:
        req = urllib.request.Request(
            f"{_HOST}/capture/",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass  # best-effort: telemetry must never affect the app


def capture(event: str, properties: dict | None = None) -> None:
    """Record one anonymous product event. No-op if opted out or no key.

    ``properties`` must be bucketed/categorical only — never task content,
    message text, paths, or PII. The caller owns that discipline; this module
    does not inspect payloads beyond attaching the anonymous distinct id.
    """
    if os.environ.get("SUTANDO_DEBUG_TELEMETRY", "").strip().lower() in _TRUTHY:
        sys.stderr.write(
            f"[telemetry] {event} {properties or {}} "
            f"(enabled={enabled()})\n"
        )
    if not enabled():
        return
    payload = {
        "api_key": _KEY,
        "event": event,
        "distinct_id": _distinct_id(),
        # $process_person_profile=False keeps these anonymous (no person
        # profiles) — cheaper and privacy-preserving for OSS install counts.
        "properties": {"$process_person_profile": False, **(properties or {})},
    }
    threading.Thread(target=_post, args=(payload,), daemon=True).start()
