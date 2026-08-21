#!/usr/bin/env python3
"""Tests that src/agent/claude/cli/start-cli.sh writes the Claude runtime marker.

Gap this covers: only the Codex launcher (src/agent/codex/cli/start-cli.sh) wrote
`state/core-runtime.json` (hardcoded runtime:"codex"). The Claude launcher wrote
neither the marker nor a `runtime` field on its `state/session-starts.log` line, so
after a Codex->Claude core switch the marker stayed stale ("codex") or absent, even
though the live core was Claude. Readers (health-check's Codex-repair path, the
dashboard, rollback logic) then saw a runtime that no longer matched reality.

This drives the real launcher through its no-tmux fallback with a stub `claude`
(records argv, exits) and a stub `pgrep` (always "not running"), and redirects the
workspace to a per-test tmp dir via the sanctioned SUTANDO_TEST_MODE escape hatch
(src/sutando_config.py resolve_workspace). It asserts the launcher wrote, before it
exec'd claude:
  - state/core-runtime.json = {"runtime":"claude","session":"sutando-core","started_at":<int>}
  - state/session-starts.log last line carries source:"start-cli" AND runtime:"claude"

Run: python3 tests/start-cli-core-runtime-marker.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "src" / "agent" / "claude" / "cli" / "start-cli.sh"

# A curated bin dir (coreutils + stub claude/pgrep, no tmux) forces the no-tmux
# fallback, so the run never touches the real socket; /usr/bin would re-add tmux.
_TOOLS = [
    "bash", "sh", "env", "python3", "dirname", "hostname", "date", "sed",
    "mkdir", "mktemp", "rm", "cat", "sleep", "uname", "cut", "grep", "head",
    "tail", "chmod", "ls", "tr", "wc", "find", "stat", "touch", "cp", "mv",
    "printf", "expr", "id", "whoami", "mktemp",
]


def _run_launcher() -> Path:
    """Run start-cli.sh (no-tmux fallback, no real tmux); return the tmp workspace."""
    td = Path(tempfile.mkdtemp())
    bind = td / "bin"
    bind.mkdir()
    ws = td / "ws"

    # Symlink each real tool into bind. Skip tmux entirely so `command -v tmux`
    # fails and the launcher takes the no-tmux path.
    for tool in _TOOLS:
        real = shutil.which(tool)
        if real:
            link = bind / tool
            if not link.exists():
                link.symlink_to(real)
    # Stub claude: exit cleanly (the launcher exec's it AFTER the marker block).
    (bind / "claude").write_text("#!/bin/bash\nexit 0\n")
    (bind / "claude").chmod(0o755)
    # Stub pgrep: always "no match" so the already-running guard passes.
    (bind / "pgrep").write_text("#!/bin/bash\nexit 1\n")
    (bind / "pgrep").chmod(0o755)

    env = {
        "PATH": str(bind),  # ONLY the curated bin — no tmux anywhere
        "HOME": str(td),
        # Sanctioned test escape hatch (src/sutando_config.py resolve_workspace):
        # redirect workspace to our tmp dir so we never touch the real one.
        "SUTANDO_TEST_MODE": "1",
        "SUTANDO_WORKSPACE": str(ws),
    }
    subprocess.run(
        ["/bin/bash", str(SCRIPT)],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return ws


def case_core_runtime_marker() -> list[str]:
    fails = []
    ws = _run_launcher()
    marker = ws / "state" / "core-runtime.json"
    if not marker.exists():
        return ["core-runtime.json was not written by the Claude launcher"]
    try:
        d = json.loads(marker.read_text())
    except ValueError as e:
        return [f"core-runtime.json is not valid JSON: {e}"]
    if d.get("runtime") != "claude":
        fails.append(f'runtime should be "claude", got {d.get("runtime")!r}')
    if d.get("session") != "sutando-core":
        fails.append(f'session should be "sutando-core", got {d.get("session")!r}')
    if not isinstance(d.get("started_at"), int):
        fails.append(f"started_at should be an int epoch, got {d.get('started_at')!r}")
    return fails


def case_session_starts_runtime_field() -> list[str]:
    fails = []
    ws = _run_launcher()
    log = ws / "state" / "session-starts.log"
    if not log.exists():
        return ["session-starts.log was not written"]
    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    if not lines:
        return ["session-starts.log is empty"]
    try:
        last = json.loads(lines[-1])
    except ValueError as e:
        return [f"session-starts.log last line is not valid JSON: {e}"]
    if last.get("source") != "start-cli":
        fails.append(f'source should be "start-cli", got {last.get("source")!r}')
    if last.get("runtime") != "claude":
        fails.append(f'session-starts.log runtime should be "claude" (parity with Codex), got {last.get("runtime")!r}')
    return fails


def case_detached_publish_is_behind_the_liveness_gate() -> list[str]:
    """Ordering, not presence: a publish before the gate can replace a truthful
    marker with a runtime that never came up."""
    src = SCRIPT.read_text(encoding="utf-8")
    fails: list[str] = []
    try:
        gate = src.index("did not come up within")
        detached = src.index("new-session -d")
        pub = src.index("stamp_runtime_claude", gate)
    except ValueError as exc:
        return [f"could not locate the detached launch/gate/publish trio: {exc}"]
    if not (detached < gate < pub):
        fails.append(
            "the detached path must publish AFTER its liveness gate "
            f"(launch={detached}, gate={gate}, publish={pub})")
    return fails


def case_exec_paths_publish_adjacent_to_exec() -> list[str]:
    """`exec` replaces the process, so no post-launch point exists on those paths.

    Pre-exec publication there is a structural limit, not a chosen behaviour —
    pinned so it cannot silently spread to a path that CAN verify.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    fails: list[str] = []
    for anchor in ("exec claude --name", 'exec tmux -S "$TMUX_SOCKET" new-session -A'):
        i = src.find(anchor)
        if i < 0:
            fails.append(f"launch anchor vanished: {anchor!r}")
            continue
        if "stamp_runtime_claude" not in src[max(0, i - 260):i]:
            fails.append(f"no publish adjacent to {anchor!r}")
    return fails


def main() -> int:
    cases = [
        ("core-runtime-marker", case_core_runtime_marker),
        ("session-starts-runtime-field", case_session_starts_runtime_field),
        ("detached publish is behind the liveness gate", case_detached_publish_is_behind_the_liveness_gate),
        ("exec paths publish adjacent to exec", case_exec_paths_publish_adjacent_to_exec),
    ]
    all_failures = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as e:  # noqa: BLE001
            fails = [f"{label}) raised {type(e).__name__}: {e}"]
        if fails:
            all_failures.extend(fails)
            print(f"  ✗ case {label}")
            for f in fails:
                print(f"      {f}")
        else:
            print(f"  ✓ case {label}")
    if all_failures:
        print(f"\n{len(all_failures)} failure(s)")
        return 1
    print("\nstart-cli.sh writes the Claude runtime marker correctly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
