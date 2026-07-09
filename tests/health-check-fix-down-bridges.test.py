#!/usr/bin/env python3
"""
Regression tests for fix_down_bridges(): `--fix` restarting bridges that are
"configured but not running".

Incident (2026-07-02): discord-bridge died at boot with nothing logged. Its
check status is "warn" (optional channels don't page), which excludes it from
`issues` — so main()'s fix loop never reached the bridge-restart branch and
`--fix` left it down while owner DMs queued channel-side. fix_down_bridges()
dispatches off the full `checks` list instead, mirroring the screen-capture
warn-fix pattern.

Guards:

  a) "configured but not running" warn → bridge restarted (all 3 bridges)
  b) other bridge warns (multiple PIDs, token invalid, stale log) → untouched
  c) non-bridge checks with the same detail → untouched
  d) ok/fail bridge statuses → untouched (fail belongs to the main fix loop)

Run: python3 tests/health-check-fix-down-bridges.test.py
Exit code: 0 on pass, 1 on fail.
"""

from __future__ import annotations
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent

# Load src/health-check.py as `health_check` (filename has a hyphen, can't
# import directly).
spec = importlib.util.spec_from_file_location("health_check", REPO / "src" / "health-check.py")
hc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hc)


def check(name: str, status: str, detail: str) -> dict:
    return {"name": name, "status": status, "detail": detail}


def run_with_popen_stub(checks: list) -> tuple[list, list]:
    """Call fix_down_bridges with Popen stubbed; return (restarted, spawn argvs).

    Also stubs the interpreter probe and slack-env load so the test is
    hermetic: without these, fix_down_bridges would probe the host for
    discord.py / slack_bolt (flaky across machines) and skip the restart when
    absent. Here every bridge gets a known-good interpreter and slack gets a
    token, so the restart path is exercised deterministically.
    """
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        return mock.MagicMock()

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(hc, "WORKSPACE_DIR", Path(td)), \
             mock.patch.object(hc, "_bridge_interpreter", return_value="python3"), \
             mock.patch.object(hc, "_load_channel_env", return_value={"SLACK_BOT_TOKEN": "xoxb-test"}), \
             mock.patch.object(hc.subprocess, "Popen", side_effect=fake_popen):
            restarted = hc.fix_down_bridges(checks)
    return restarted, spawned


def case_a_down_bridges_restarted() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "warn", "configured but not running"),
        check("telegram-bridge", "warn", "configured but not running"),
        check("slack-bridge", "warn", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted != ["discord-bridge", "telegram-bridge", "slack-bridge"]:
        fails.append(f"a) expected all 3 bridges restarted, got {restarted}")
    if len(spawned) != 3:
        fails.append(f"a) expected 3 spawns, got {len(spawned)}")
    for argv in spawned:
        if not str(argv[1]).endswith("-bridge.py"):
            fails.append(f"a) spawn argv doesn't target a bridge script: {argv}")
    return fails


def case_b_other_bridge_warns_untouched() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "warn", "multiple processes (2 PIDs: 1,2)"),
        check("discord-bridge", "warn", "token invalid (LoginFailure) — regenerate at discord.com/developers/applications"),
        check("telegram-bridge", "warn", "log stale (36.0h) — process may be wedged"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"b) non-down bridge warns triggered restart: {restarted}")
    return fails


def case_c_non_bridge_checks_untouched() -> list[str]:
    fails = []
    checks = [
        check("conversation-server", "warn", "configured but not running"),
        check("credential-proxy", "warn", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"c) non-covered checks triggered restart: {restarted}")
    return fails


def case_d_other_statuses_untouched() -> list[str]:
    fails = []
    checks = [
        check("discord-bridge", "ok", "running"),
        check("telegram-bridge", "down", "configured but not running"),
    ]
    restarted, spawned = run_with_popen_stub(checks)
    if restarted or spawned:
        fails.append(f"d) ok/down statuses triggered restart: {restarted}")
    return fails


def case_e_main_fix_prints_bridge_names() -> list[str]:
    """main() --fix exercises lines 2349-2354: prints '{name}: restart attempted'."""
    fails = []
    fake_checks = [
        check("discord-bridge", "warn", "configured but not running"),
        check("slack-bridge",   "warn", "configured but not running"),
    ]
    captured = io.StringIO()
    with mock.patch.object(sys, "argv", ["health-check.py", "--fix"]), \
         mock.patch.object(hc, "run_all_checks", return_value=fake_checks), \
         mock.patch.object(hc, "fix_down_bridges", return_value=["discord-bridge", "slack-bridge"]):
        try:
            with redirect_stdout(captured):
                hc.main()
        except SystemExit:
            pass
    out = captured.getvalue()
    for name in ("discord-bridge", "slack-bridge"):
        expected = f"  {name}: restart attempted (was not running)"
        if expected not in out:
            fails.append(f"e) missing expected line '{expected}' in main() --fix output")
    return fails


def case_f_run_all_checks_emits_slack_configured_not_running() -> list[str]:
    """Reachability guard (PR #1898): run_all_checks() must emit the
    'configured but not running' warn for slack-bridge — otherwise
    fix_down_bridges()'s slack branch is dead code and case_a is a false
    positive. Slack is made to look configured (access.json present) and not
    running (pgrep for slack-bridge.py returns nothing); every OTHER pgrep/
    subprocess call is delegated to the real implementation so the rest of the
    health check runs normally.
    """
    fails = []
    real_run = hc.subprocess.run

    def fake_run(argv, *args, **kwargs):
        # Intercept ONLY the slack-bridge pgrep so it reports "not running".
        if (isinstance(argv, list) and len(argv) >= 3
                and argv[0] == "/usr/bin/pgrep" and "slack-bridge" in argv[2]):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="")
        return real_run(argv, *args, **kwargs)

    with tempfile.TemporaryDirectory() as home_td:
        home = Path(home_td)
        # Make slack look configured: create channels/slack/access.json under a
        # fake claude-home. Point claude_home_path() at it for ALL lookups
        # (real one just joins subpaths onto the home root, which is what we
        # emulate here).
        (home / "channels" / "slack").mkdir(parents=True, exist_ok=True)
        (home / "channels" / "slack" / "access.json").write_text('{"allowFrom": []}')

        def fake_home(*subpath):
            return home.joinpath(*subpath)

        with mock.patch.object(hc.subprocess, "run", side_effect=fake_run), \
             mock.patch.object(hc, "claude_home_path", side_effect=fake_home):
            try:
                checks = hc.run_all_checks()
            except Exception as e:  # pragma: no cover - defensive
                return [f"f) run_all_checks raised: {e!r}"]

    slack = [c for c in checks if c.get("name") == "slack-bridge"]
    if not slack:
        fails.append("f) run_all_checks emitted NO slack-bridge check (branch unreachable)")
    elif not any(c.get("detail") == "configured but not running" for c in slack):
        fails.append(f"f) slack-bridge check(s) present but none 'configured but not running': {slack}")
    return fails


def case_g_launch_parity_interpreter_and_env() -> list[str]:
    """Launch parity (PR #1898): fix_down_bridges must (1) launch discord/slack
    with an interpreter probed for the bridge's import — NOT bare
    sys.executable — and (2) inject the slack channel .env into the child.
    """
    fails = []
    spawned = []  # (argv, env)

    def fake_popen(argv, **kwargs):
        spawned.append((argv, kwargs.get("env")))
        return mock.MagicMock()

    checks = [
        check("discord-bridge", "warn", "configured but not running"),
        check("slack-bridge", "warn", "configured but not running"),
    ]
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(hc, "WORKSPACE_DIR", Path(td)), \
             mock.patch.object(hc, "_bridge_interpreter", return_value="/opt/homebrew/bin/python3"), \
             mock.patch.object(hc, "_load_channel_env", return_value={"SLACK_BOT_TOKEN": "xoxb-abc", "SLACK_APP_TOKEN": "xapp-xyz"}), \
             mock.patch.object(hc.subprocess, "Popen", side_effect=fake_popen):
            restarted = hc.fix_down_bridges(checks)

    if restarted != ["discord-bridge", "slack-bridge"]:
        fails.append(f"g) expected both restarted, got {restarted}")
    for argv, env in spawned:
        if argv[0] != "/opt/homebrew/bin/python3":
            fails.append(f"g) bridge not launched with probed interpreter: {argv[0]}")
        if str(argv[1]).endswith("slack-bridge.py"):
            if not env or env.get("SLACK_BOT_TOKEN") != "xoxb-abc":
                fails.append("g) slack child env missing SLACK_BOT_TOKEN from channel .env")
    return fails


def case_h_launch_parity_failsafe_skips() -> list[str]:
    """Fail-safe (PR #1898): if no interpreter can import the bridge dep, OR the
    slack tokens are unavailable, fix_down_bridges must SKIP that bridge (no
    crash-loop spawn) rather than launch it.
    """
    fails = []
    spawned = []

    def fake_popen(argv, **kwargs):
        spawned.append(argv)
        return mock.MagicMock()

    checks = [
        check("discord-bridge", "warn", "configured but not running"),
        check("slack-bridge", "warn", "configured but not running"),
    ]
    # discord: no capable interpreter (None). slack: interpreter fine but env
    # has no token — and ensure the ambient env doesn't carry one either.
    clean_env = {k: v for k, v in os.environ.items() if k != "SLACK_BOT_TOKEN"}
    with tempfile.TemporaryDirectory() as td:
        with mock.patch.object(hc, "WORKSPACE_DIR", Path(td)), \
             mock.patch.object(hc, "_bridge_interpreter", side_effect=lambda n: None if n == "discord-bridge" else "python3"), \
             mock.patch.object(hc, "_load_channel_env", return_value={}), \
             mock.patch.dict(hc.os.environ, clean_env, clear=True), \
             mock.patch.object(hc.subprocess, "Popen", side_effect=fake_popen):
            restarted = hc.fix_down_bridges(checks)

    if restarted:
        fails.append(f"h) expected no restarts (fail-safe), got {restarted}")
    if spawned:
        fails.append(f"h) fail-safe still spawned a process: {spawned}")
    return fails


def main() -> int:
    all_fails = []
    for case in (case_a_down_bridges_restarted, case_b_other_bridge_warns_untouched,
                 case_c_non_bridge_checks_untouched, case_d_other_statuses_untouched,
                 case_e_main_fix_prints_bridge_names,
                 case_f_run_all_checks_emits_slack_configured_not_running,
                 case_g_launch_parity_interpreter_and_env,
                 case_h_launch_parity_failsafe_skips):
        fails = case()
        status = "PASS" if not fails else "FAIL"
        print(f"  {status} {case.__name__}")
        all_fails.extend(fails)
    if all_fails:
        print()
        for f in all_fails:
            print(f"  ✗ {f}")
        return 1
    print("All fix_down_bridges tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
