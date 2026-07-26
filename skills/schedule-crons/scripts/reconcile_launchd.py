#!/usr/bin/env python3
"""Give Codex-core schedules a durable launchd owner.

Codex does not have Claude Code's session CronCreate surface.  When a host
switches to the Codex core, ordinary crons.json entries would otherwise remain
defined but stop firing after the old Claude session exits.  Reconciliation is
idempotent and initializes runner state before changing ownership so enabling
the durable runner cannot replay a backlog of old actions.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional


def launchd_eligible(entry: dict[str, Any]) -> bool:
    """Return whether Codex should move this entry to the launchd runner."""
    if entry.get("launchd") is True or entry.get("execution") == "codex-task":
        return False
    if entry.get("loop") == "dynamic" or not entry.get("cron"):
        return False
    if entry.get("name") == "main-loop" or entry.get("prompt_skill") == "proactive-loop":
        return False
    prompt = str(entry.get("prompt") or "").strip()
    return prompt != "/proactive-loop"


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(value, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def reconcile(crons_file: Path, state_file: Path, now: Optional[int] = None) -> dict[str, Any]:
    """Move eligible entries to launchd ownership without replaying old slots."""
    crons = json.loads(crons_file.read_text())
    if not isinstance(crons, list):
        raise ValueError(f"{crons_file} must contain a JSON list")

    try:
        state = json.loads(state_file.read_text())
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError(f"{state_file} must contain a JSON object")

    boundary = int(time.time() if now is None else now)
    migrated: list[str] = []
    for entry in crons:
        if not isinstance(entry, dict) or not launchd_eligible(entry):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            continue
        state.setdefault(name, boundary)
        migrated.append(name)

    if migrated:
        # Ordering is the safety property: a launchd tick can see either the
        # old session-owned config or the new config plus an initialized
        # boundary, never launchd ownership with an absent 24h catch-up state.
        _atomic_json(state_file, state)
        for entry in crons:
            if isinstance(entry, dict) and entry.get("name") in migrated:
                entry["launchd"] = True
        _atomic_json(crons_file, crons)

    runner_needed = any(
        isinstance(entry, dict) and entry.get("launchd") is True for entry in crons
    )
    return {"migrated": migrated, "runner_needed": runner_needed}


def _default_paths() -> tuple[Path, Path]:
    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo / "src"))
    from util_paths import _host_label  # type: ignore  # noqa: PLC0415
    from workspace_default import resolve_workspace  # type: ignore  # noqa: PLC0415

    workspace = Path(resolve_workspace())
    return (
        workspace / "hosts" / _host_label() / "crons.json",
        workspace / "state" / "cron-runner-state.json",
    )


def main() -> int:
    crons_file, state_file = _default_paths()
    if not crons_file.exists():
        print("runner_needed=0 migrated=0")
        return 0
    result = reconcile(crons_file, state_file)
    names = ",".join(result["migrated"])
    print(
        f"runner_needed={int(result['runner_needed'])} "
        f"migrated={len(result['migrated'])} names={names}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
