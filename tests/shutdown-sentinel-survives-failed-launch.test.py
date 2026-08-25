#!/usr/bin/env python3
"""No launch path may clear the shutdown sentinel before a core is verified (#2165).

The sentinel is the intentional-stop gate: `watch-tasks-stream.sh` holds every
task while it exists. Clearing it before a launch that can still fail opens
intake with nothing serving — the half-open state the sentinel exists to prevent.

WHAT THIS DOES NOT DO, deliberately: it never runs a launcher end-to-end. Those
launchers call `shutdown.py clear`, which resolves the REAL workspace, so a live
run would cancel an owner's intentional stop (the same reason
`shutdown-sentinel-cleared-by-real-launcher.test.py` refuses to execute clear).
A first draft of this file did drive the launchers in a sandbox and PASSED
against the unfixed sources — the sandbox never reached a clear, so the assertion
proved nothing. Ordering is a property of the source; the one runtime claim the
fix rests on is checked for real below.

Run: python3 tests/shutdown-sentinel-survives-failed-launch.test.py  (exit 0/1)
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAUNCHERS = {
    "claude": REPO / "src" / "agent" / "claude" / "cli" / "start-cli.sh",
    "codex": REPO / "src" / "agent" / "codex" / "cli" / "start-cli.sh",
}
failures: list[str] = []
checks = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if cond:
        print(f"  ok   {name}")
    else:
        failures.append(name)
        print(f"  FAIL {name}{(' — ' + detail) if detail else ''}")


print("the stop gate outlives a launch that never produced a core:\n")

# 1. RUNTIME claim the whole restore path rests on. Without execfail, bash exits
#    127 at a failed exec and every line after it is unreachable.
without = subprocess.run(["bash", "-c", "exec no_such_binary_xyz; echo REACHED"],
                         capture_output=True, text=True)
with_ = subprocess.run(["bash", "-c", "shopt -s execfail; exec no_such_binary_xyz; echo REACHED"],
                       capture_output=True, text=True)
check("bash without execfail does NOT reach code after a failed exec",
      "REACHED" not in without.stdout and without.returncode == 127,
      f"got rc={without.returncode} out={without.stdout!r}")
check("bash WITH execfail does reach it",
      "REACHED" in with_.stdout,
      f"got rc={with_.returncode} out={with_.stdout!r}")

# 2. Each launcher arms that behavior and has something to run afterwards.
for label, path in LAUNCHERS.items():
    src = path.read_text(encoding="utf-8")
    check(f"{label}: arms `shopt -s execfail`", "shopt -s execfail" in src,
          "otherwise the restore below is dead code")
    check(f"{label}: restores the sentinel after a failed exec",
          "restore_shutdown_sentinel" in src)
    check(f"{label}: refuses to clear when the core binary is absent",
          re.search(r"command -v (claude|codex).*\n.*not clearing the shutdown sentinel",
                    src, re.M) is not None)

# 3. ORDERING: no clear may precede the verification that a core is live.
claude = LAUNCHERS["claude"].read_text(encoding="utf-8")
calls = [m.start() for m in re.finditer(r"^\s*clear_shutdown_sentinel\s*(?:#.*)?$", claude, re.M)]
check("claude: has clear call sites at all", bool(calls))
def _window(src: str, pos: int, lines: int = 40) -> str:
    """The `lines` source lines immediately preceding `pos`."""
    return "\n".join(src[:pos].split("\n")[-lines:])

for pos in calls:
    w = _window(claude, pos)
    verified = ("tmux_core_session_running" in w or "healed_idx" in w
                or "command -v claude" in w)
    check(f"claude: clear at line {claude[:pos].count(chr(10))+1} follows a liveness proof",
          verified, "a clear with no preceding verification can open intake with no core")

codex = LAUNCHERS["codex"].read_text(encoding="utf-8")
ccalls = [m.start() for m in re.finditer(r"^\s*clear_shutdown_sentinel\s*(?:#.*)?$", codex, re.M)]
check("codex: has clear call sites at all", bool(ccalls))
for pos in ccalls:
    w = _window(codex, pos)
    check(f"codex: clear at line {codex[:pos].count(chr(10))+1} follows session creation or a binary check",
          "new-session" in w or "command -v codex" in w)

# 4. startup.sh cannot verify anything — it must delegate, not clear.
startup = (REPO / "src" / "startup.sh").read_text(encoding="utf-8")
check("startup.sh does not clear the sentinel itself", "shutdown.py" not in startup,
      "it runs ~850 lines before `exec start-cli.sh`, so it cannot know a core started")

if failures:
    print(f"\n{len(failures)} failure(s) of {checks}")
    sys.exit(1)
print(f"\nAll {checks} checks passed.")
