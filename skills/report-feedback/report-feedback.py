#!/usr/bin/env python3
"""Report a bug / feature request / feedback about Sutando to the team.

Files to the cloud /api/feedback route (the same API the desktop "Report an
issue" form uses, which mirrors into GitHub issues), attaching diagnostic
context (platform + a tail of recent workspace logs). This is the single
reporting path for every surface — chat, Discord, Telegram, and voice (via
task delegation) — so there's no per-surface duplication.

Usage:
  python3 skills/report-feedback/report-feedback.py \
      --title "..." [--body "..."] [--kind bug|feature|other] \
      [--severity low|medium|high|critical] [--no-logs]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import urllib.error
import urllib.request
from pathlib import Path


def resolve_workspace() -> Path:
    """Canonical workspace, mirroring the TS/py resolver; fall back to <repo>/workspace."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
        from workspace_default import resolve_workspace as rw  # type: ignore

        return Path(rw())
    except Exception:
        return Path(__file__).resolve().parents[2] / "workspace"


def read_cloud_auth(ws: Path):
    """Return (apiBase, token) if signed in to Sutando Cloud, else (None, None).

    Matches the desktop's readCloudAuth (electron/ipc.cjs): the record lives at
    ``<workspace>/cloud-auth.json`` and "signed in" simply means a ``token`` is
    present (there is no ``signedIn`` field). We also probe the packaged-app
    canonical workspace (~/.sutando/repo/workspace) so the skill finds the token
    even when it runs from a different checkout than the desktop uses. Falls
    back to the metering env the supervisor injects for signed-in runs.
    """
    seen: set[str] = set()
    for p in (
        ws / "cloud-auth.json",  # <workspace>/cloud-auth.json — where the desktop writes it
        Path.home() / ".sutando" / "repo" / "workspace" / "cloud-auth.json",  # packaged-app default (per workspace contract)
        Path.home() / "Library" / "Application Support" / "@stando" / "ui" / "cloud-auth.json",  # legacy
    ):
        rp = str(p)
        if rp in seen:
            continue
        seen.add(rp)
        try:
            if p.exists():
                d = json.loads(p.read_text())
                if d.get("token"):  # signed in == has token (matches desktop)
                    return (d.get("apiBase") or "https://sutando.ag2.ai"), d["token"]
        except Exception:
            continue

    hdrs = os.environ.get("SUTANDO_METERING_HEADERS")
    if hdrs:
        try:
            auth = json.loads(hdrs).get("Authorization", "")
            tok = auth.split(" ", 1)[1] if auth.lower().startswith("bearer ") else auth
            base = os.environ.get("SUTANDO_METERING_ENDPOINT", "").replace("/api/usage/v2", "").rstrip("/")
            if tok:
                return (base or "https://sutando.ag2.ai"), tok
        except Exception:
            pass
    return None, None


def logs_excerpt(ws: Path):
    """Last 40 lines of the 4 most-recent <workspace>/logs/*.log (capped ~8KB)."""
    try:
        logs = ws / "logs"
        if not logs.is_dir():
            return None, []
        files = sorted(
            (f for f in logs.iterdir() if f.suffix == ".log"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )[:4]
        parts = []
        for f in files:
            tail = "\n".join(f.read_text(errors="replace").splitlines()[-40:])
            parts.append(f"===== {f.name} (last 40 lines) =====\n{tail}")
        return "\n\n".join(parts)[-8000:], [f.name for f in files]
    except Exception:
        return None, []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True)
    ap.add_argument("--body", default="")
    ap.add_argument("--kind", choices=["bug", "feature", "other"], default="bug")
    ap.add_argument("--severity", choices=["low", "medium", "high", "critical"], default="medium")
    ap.add_argument("--no-logs", action="store_true", help="Omit the diagnostic log excerpt.")
    a = ap.parse_args()

    if not a.title.strip():
        print("ERROR: --title is required (a short one-line summary).")
        sys.exit(1)

    ws = resolve_workspace()
    base, token = read_cloud_auth(ws)
    if not token:
        print("NOT_SIGNED_IN: not signed in to Sutando Cloud — ask the user to sign in (Settings → Sutando Cloud), then retry.")
        sys.exit(2)

    ctx: dict = {"source": "core-agent", "platform": platform.platform(), "python": platform.python_version()}
    if not a.no_logs:
        excerpt, names = logs_excerpt(ws)
        if excerpt:
            ctx["last_logs_excerpt"] = excerpt
            ctx["log_files"] = names

    payload = {
        "kind": a.kind,
        "severity": a.severity,
        "title": a.title.strip(),
        "body": a.body.strip() or None,
        "context": ctx,
    }
    req = urllib.request.Request(
        f"{base.rstrip('/')}/api/feedback",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"OK: filed {a.kind} report ({r.status}).")
    except urllib.error.HTTPError as e:
        print(f"ERROR: feedback API {e.code}: {e.read().decode(errors='replace')[:300]}")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
