#!/usr/bin/env python3
"""Regression guard: generate_readme() in skills/publish.py.

generate_readme(skill_dir):
  Reads SKILL.md from the skill directory and generates a publishable
  README string. Returns "" if SKILL.md doesn't exist. Includes:
    - Skill name (from skill_dir.name)
    - Script count and names from skills/<name>/scripts/
    - Usage section extracted from SKILL.md ("## When to Use" block)

Run: python3 tests/publish-generate-readme.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "publish", REPO / "skills" / "publish.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["publish"] = _mod
spec.loader.exec_module(_mod)

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _make_skill(tmp: Path, name: str, skill_md: str | None = None, scripts: list[str] | None = None) -> Path:
    skill_dir = tmp / name
    skill_dir.mkdir(parents=True)
    if skill_md is not None:
        (skill_dir / "SKILL.md").write_text(skill_md)
    if scripts:
        (skill_dir / "scripts").mkdir()
        for s in scripts:
            (skill_dir / "scripts" / s).write_text("")
    return skill_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def _test_generate_readme():
    f = _mod.generate_readme

    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)

        # No SKILL.md → empty string
        no_skill = _make_skill(tmp, "no-skill-md")
        _check("gr-no-skill-md",    f(no_skill) == "")

        # Minimal SKILL.md, no scripts dir
        minimal = _make_skill(tmp, "my-skill", skill_md="# My Skill\n\nDoes things.\n")
        result = f(minimal)
        _check("gr-has-content",    len(result) > 0)
        _check("gr-skill-name",     "my-skill" in result)
        _check("gr-zero-scripts",   "0 scripts:" in result)
        _check("gr-install-block",  "git clone" in result)
        _check("gr-ln-block",       "ln -s" in result and "my-skill" in result)
        _check("gr-license",        "MIT" in result)

        # With scripts → count reflected
        with_scripts = _make_skill(tmp, "scripted",
                                   skill_md="# Scripted\n\n",
                                   scripts=["run.py", "helper.sh", "utils.py"])
        result2 = f(with_scripts)
        _check("gr-script-count",   "3 scripts:" in result2)
        _check("gr-script-name",    "run.py" in result2)
        _check("gr-script-sh",      "helper.sh" in result2)

        # "## When to Use" section extracted
        skill_with_usage = (
            "# My Tool\n\n"
            "Some intro.\n\n"
            "## When to Use\n\n"
            "Use this when you need to do XYZ with your agent.\n\n"
            "## Requirements\n\nNeeds Python 3.10.\n"
        )
        with_usage = _make_skill(tmp, "tool-with-usage", skill_md=skill_with_usage)
        result3 = f(with_usage)
        _check("gr-usage-extracted", "Use this when you need to do XYZ" in result3)
        _check("gr-no-req-leaked",   "## Requirements" not in result3.split("## Usage")[1].split("## Requirements")[0]
                                     if "## Requirements" in result3 else True)

        # No "## When to Use" in SKILL.md → fallback text
        no_when = _make_skill(tmp, "no-when", skill_md="# No When\n\nJust a skill.\n")
        result4 = f(no_when)
        _check("gr-fallback-usage",  "See SKILL.md for details." in result4)

        # Skill name appears in install ln -s line
        install_skill = _make_skill(tmp, "my-cool-skill", skill_md="# Cool\n\n")
        result5 = f(install_skill)
        _check("gr-ln-name",         "my-cool-skill" in result5)
        _check("gr-github-url",      "sonichi/sutando" in result5)


_test_generate_readme()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"publish-generate-readme: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
