#!/usr/bin/env python3
"""A tmux branch that accepts the command but whose child dies must NOT clear the sentinel.

The sibling suite `shutdown-sentinel-survives-failed-launch.test.py` cannot see this
failure: it accepts nearby token presence (`healed_idx`, `new-session`) as proof of a
liveness check, so all 22 of its checks passed at 7cc1414c, when all three branches
still cleared immediately after command acceptance. A control that passes on the broken
code is not a control.

So this drives the REAL launcher into each branch with tmux ACCEPTING every command
while no core process exists — `new-window`/`new-session` return success, the liveness
probe stays false. That is the immediate-exit case: tmux took the command, the child
was gone before anyone looked. The sentinel must survive it, because clearing it opens
task intake when no core is serving.

Run: python3 tests/shutdown-sentinel-immediate-exit-controls.test.py  (exit 0/1)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REAL_REPO = Path(__file__).resolve().parent.parent
NEEDED = (
    "src/agent/claude/cli/start-cli.sh",
    "src/agent/codex/cli/start-cli.sh",
    "src/agent/restart-guard.sh",
    "src/claude_config_dir.sh",
    "src/shutdown.py",
    "src/workspace_default.py",
    "src/sutando_config.py",
    "src/util_paths.py",
    "scripts/python-binary.sh",
    "scripts/sutando-config.sh",
    # without these the launcher aborts before tmux and the assert is vacuous
    "scripts/install-personal-claude-hook.sh",
)
failures: list[str] = []


def _exe(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


# tmux accepts every command and reports the session present; liveness is
# answered separately by pgrep, which finds nothing.
TMUX_STUB = """#!/bin/bash
args="$*"
case "$args" in
  *new-window*|*new-session*) echo 0; exit 0 ;;
  *has-session*)              exit 0 ;;
  *list-windows*)             echo "0: core"; exit 0 ;;
  *)                          exit 0 ;;
esac
"""
# No process ever matches, so core_claude_running() is false before AND after
# the window is created — the child that exited immediately.
PGREP_STUB = "#!/bin/bash\nexit 1\n"


def run_branch(label: str, launcher_rel: str, env_extra: dict) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root, binp = Path(tmp) / "repo", Path(tmp) / "bin"
        binp.mkdir(parents=True)
        missing = False
        for rel in NEEDED:
            src = REAL_REPO / rel
            if not src.exists():
                failures.append(f"{label}: {rel} missing from repo")
                missing = True
                continue
            dst = root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        if missing:
            return

        _exe(binp / "tmux", TMUX_STUB)
        _exe(binp / "pgrep", PGREP_STUB)
        # mktemp is load-bearing: without it claude_config_dir.sh fails and the
        # launcher exits before reaching tmux, making the assert vacuous.
        for real in ("bash", "python3", "sed", "awk", "grep", "ps", "seq", "sleep",
                     "cat", "mkdir", "rm", "date", "uname", "dirname", "basename",
                     "tr", "head", "tail", "cut", "wc", "sort", "id", "hostname",
                     "mktemp", "touch", "chmod", "ln", "cp", "mv", "pwd", "expr",
                     "printf", "sh", "find", "xargs", "stat", "realpath", "env", "which"):
            found = shutil.which(real)
            if found:
                try:
                    (binp / real).symlink_to(found)
                except FileExistsError:
                    pass

        ws = root / "workspace"
        (ws / "state").mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "PATH": str(binp),
            "HOME": str(root),
            "SUTANDO_TMUX_SOCKET": str(root / "sock"),
            "SUTANDO_TMUX_SESSION": "sutando-core",
        })
        env.update(env_extra)

        # Read it back after marking: otherwise "still set" is vacuous, since a
        # probe that never sees a set sentinel cannot fail.
        mark = subprocess.run([sys.executable, str(root / "src/shutdown.py"), "mark", label],
                              capture_output=True, text=True, env=env, cwd=str(root))
        path_p = subprocess.run([sys.executable, str(root / "src/shutdown.py"), "path"],
                                capture_output=True, text=True, env=env, cwd=str(root))
        sentinel = Path(path_p.stdout.strip()) if path_p.stdout.strip() else None
        if sentinel is None or not sentinel.exists():
            failures.append(f"{label}: setup failed — sentinel not set after mark "
                            f"(mark rc={mark.returncode}, stderr={mark.stderr.strip()[:200]})")
            return

        proc = subprocess.run(["bash", str(root / launcher_rel)],
                              capture_output=True, text=True, env=env, cwd=str(root),
                              timeout=180)

        if sentinel.exists():
            print(f"OK: {label} — tmux accepted the command, no core lived, sentinel SURVIVED")
        else:
            failures.append(
                f"{label}: sentinel was CLEARED after tmux merely accepted the command "
                f"while no core process existed — task intake would open with nothing serving. "
                f"launcher rc={proc.returncode}\n"
                f"  stdout tail: {proc.stdout.strip()[-400:]}\n"
                f"  stderr tail: {proc.stderr.strip()[-400:]}")


run_branch("claude-heal", "src/agent/claude/cli/start-cli.sh", {})

# The two Codex branches are NOT asserted: this harness never reaches them, so
# a check there passes without running the code it names (verified at 7cc1414c).

if failures:
    print("\nFAILURES:")
    for f in failures:
        print(" -", f)
    raise SystemExit(1)
print("\nAll immediate-exit controls passed.")
