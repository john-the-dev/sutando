#!/usr/bin/env python3
"""Sole writer of the marker and session-start log; launchers inject runtime/session/source.
core-runtime.json is replaced atomically (readers poll it); best-effort — a launch never fails on it."""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import time
from pathlib import Path

VALID_RUNTIMES = ("claude", "codex")


def write_marker(workspace, runtime: str, session: str, source: str = "start-cli") -> bool:
    """Declare `runtime` as the core in `workspace`. True if both records landed.
    Raises ValueError on an unknown runtime — it would publish a value no reader understands."""
    if runtime not in VALID_RUNTIMES:
        raise ValueError(f"unknown runtime {runtime!r}; expected one of {VALID_RUNTIMES}")
    if not workspace:
        return False
    state = Path(workspace) / "state"
    now = int(time.time())
    ok = True
    try:
        state.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    marker = {"runtime": runtime, "session": session, "started_at": now}
    try:
        # Atomic replace: a reader polling mid-write must never see a partial file.
        fd, tmp = tempfile.mkstemp(dir=str(state), prefix=".core-runtime.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(json.dumps(marker) + "\n")
            os.replace(tmp, str(state / "core-runtime.json"))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except (OSError, ValueError):
        ok = False

    entry = {
        "host": socket.gethostname().split(".")[0],
        "session_started_at": now,
        "iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "source": source,
        "runtime": runtime,
    }
    try:
        with open(state / "session-starts.log", "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        ok = False
    return ok


def main(argv: list[str]) -> int:
    if len(argv) < 4:
        print("usage: core_runtime_marker.py <workspace> <runtime> <session> [source]",
              file=sys.stderr)
        return 2
    try:
        ok = write_marker(argv[1], argv[2], argv[3], argv[4] if len(argv) > 4 else "start-cli")
    except ValueError as exc:
        print(f"core_runtime_marker: {exc}", file=sys.stderr)
        return 2
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
