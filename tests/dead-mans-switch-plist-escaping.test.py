#!/usr/bin/env python3
"""Regression: the installed plist survives special characters in host paths.

The installer templated the launchd plist with `sed -e "s|__REPO__|$REPO|g"`.
Three things go wrong there, and all of them exit 0 with a plist on disk:

  * `&` in a sed REPLACEMENT means "the text that matched", so a repo path
    containing `&` produced `.../test__REPO__path/...` in ProgramArguments;
  * `|` is the delimiter, so a path containing it breaks the expression;
  * `&`, `<` and `>` are XML metacharacters and must be escaped in a plist
    value regardless of sed.

A launchd job whose ProgramArguments path is silently wrong never runs, which
for this skill means the dead-man's switch is installed and dead.

The substitution block is extracted from `install.sh` and executed, so this
covers the shipped code rather than a copy of it. Assertions parse the result
with `plistlib`: that proves the output is well-formed XML *and* that the value
round-trips exactly, which a string comparison alone would not.
"""

import plistlib
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO / "skills" / "dead-mans-switch" / "install.sh"
TEMPLATE = REPO / "skills" / "dead-mans-switch" / "launchd" / "com.sutando.deadman-ping.plist"

failures = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + (f"  [{detail}]" if not cond and detail else ""))
    if not cond:
        failures.append(label)


def substitution_block() -> str:
    """The python heredoc install.sh actually runs."""
    src = INSTALL_SH.read_text(encoding="utf-8")
    m = re.search(r"python3 - .*?<<'PY'\n(.*?)\nPY\n", src, re.DOTALL)
    assert m, "substitution block not found in install.sh — did the installer change shape?"
    return m.group(1)


def render(repo: str, workspace: str, brew: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "out.plist"
        script = Path(tmp) / "subst.py"
        script.write_text(substitution_block(), encoding="utf-8")
        subprocess.run(
            [sys.executable, str(script), str(TEMPLATE), str(dest), repo, workspace, brew],
            check=True,
        )
        with open(dest, "rb") as fh:
            return plistlib.load(fh)


def main():
    check("install.sh no longer templates the plist with sed",
          "s|__REPO__|" not in INSTALL_SH.read_text(encoding="utf-8"))

    # Control: an ordinary path must still work, or the fix broke the feature.
    plist = render("/Users/x/repo", "/Users/x/ws", "/opt/homebrew/bin")
    check("CONTROL benign repo path lands in ProgramArguments",
          any("/Users/x/repo/skills/dead-mans-switch/scripts/ping.sh" == a
              for a in plist["ProgramArguments"]), str(plist["ProgramArguments"]))
    check("CONTROL benign workspace path lands in log paths",
          plist["StandardOutPath"] == "/Users/x/ws/logs/deadman-ping.log",
          plist.get("StandardOutPath"))

    # The exact character the reviewer reproduced with.
    amp_repo = "/tmp/test&path"
    plist = render(amp_repo, "/Users/x/ws", "/opt/homebrew/bin")
    check("'&' in repo path round-trips exactly",
          f"{amp_repo}/skills/dead-mans-switch/scripts/ping.sh" in plist["ProgramArguments"],
          str(plist["ProgramArguments"]))
    check("'&' path is not corrupted into the token",
          not any("__REPO__" in a for a in plist["ProgramArguments"]),
          str(plist["ProgramArguments"]))

    # XML metacharacters: these must survive as data, not become markup.
    xml_ws = "/Users/x/<ws>&more"
    plist = render("/Users/x/repo", xml_ws, "/opt/homebrew/bin")
    check("'<', '>' and '&' in workspace path round-trip exactly",
          plist["StandardOutPath"] == f"{xml_ws}/logs/deadman-ping.log",
          plist.get("StandardOutPath"))

    # sed's own delimiter — would have broken the expression, not just the value.
    pipe_repo = "/tmp/a|b"
    plist = render(pipe_repo, "/Users/x/ws", "/opt/homebrew/bin")
    check("'|' (the old sed delimiter) in repo path round-trips exactly",
          f"{pipe_repo}/skills/dead-mans-switch/scripts/ping.sh" in plist["ProgramArguments"],
          str(plist["ProgramArguments"]))

    # Homebrew path shares the same substitution path.
    plist = render("/Users/x/repo", "/Users/x/ws", "/opt/brew&bin")
    check("'&' in the Homebrew bin path round-trips exactly",
          plist["EnvironmentVariables"]["PATH"].startswith("/opt/brew&bin:"),
          plist["EnvironmentVariables"]["PATH"])

    print(f"\n{'FAILED: ' + ', '.join(failures) if failures else 'all passed'}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
