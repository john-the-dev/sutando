#!/usr/bin/env python3
"""The gate must fire when the BASE moves under a PR's own files after approval.

Built against a labeled real case (#3823): two approvals predated #3818's merge
into the same two files, one re-approval followed it, and only that one covered
the tree that landed. Driven here on a synthetic repo so it needs no network.
"""
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("acb", ROOT / "scripts" / "approval-covers-base.py")
acb = importlib.util.module_from_spec(spec); spec.loader.exec_module(acb)

fails, ran = [], 0
def check(name, cond, detail=""):
    global ran; ran += 1
    print(f"  {'ok  ' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail and not cond else ""))
    if not cond: fails.append(name)

def git(d, *a, when=None):
    # `--date=` sets the AUTHOR date; `git log --since` filters on the COMMITTER
    # date, so a fixture that sets only the former is filtered by wall clock.
    import os
    env = dict(os.environ)
    if when:
        env["GIT_COMMITTER_DATE"] = when
        env["GIT_AUTHOR_DATE"] = when
    r = subprocess.run(["git", "-C", d, *a], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit(f"git {' '.join(a)}: {r.stderr[:200]}")
    return r.stdout.strip()

print("approval-covers-base")
with tempfile.TemporaryDirectory() as d:
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@t.t"); git(d, "config", "user.name", "t")
    (pathlib.Path(d) / "shared.sh").write_text("v1\n")
    (pathlib.Path(d) / "other.sh").write_text("v1\n")
    git(d, "add", "shared.sh", "other.sh")
    # Dates are the discriminator, so they are set explicitly, not by wall clock.
    git(d, "commit", "-q", "-m", "base", when="2026-09-03T16:00:00Z")
    T_EARLY = "2026-09-03T16:30:00Z"
    (pathlib.Path(d) / "shared.sh").write_text("v2\n")
    git(d, "add", "shared.sh")
    git(d, "commit", "-q", "-m", "base moves shared.sh", when="2026-09-03T17:00:00Z")
    T_LATE = "2026-09-03T17:30:00Z"

    PR_FILES = ["shared.sh"]
    early = acb.base_touched_since(d, "main", T_EARLY, PR_FILES)
    late = acb.base_touched_since(d, "main", T_LATE, PR_FILES)
    check("an approval BEFORE the base change is UNCOVERED", early == ["shared.sh"], f"got {early}")
    check("an approval AFTER the base change is COVERED", late == [], f"got {late}")

    # The property that makes it a gate and not a mood: a base change to a file
    # the PR does NOT touch must not invalidate anybody's approval.
    untouched = acb.base_touched_since(d, "main", T_EARLY, ["other.sh"])
    check("a base change to an UNRELATED file does not fire", untouched == [], f"got {untouched}")
    check("no files -> no claim", acb.base_touched_since(d, "main", T_EARLY, []) == [])

# Non-OPEN returns early ON PURPOSE: once merged, the PR's own merge commit is
# on the base and would match every approval, masking the covering one.
src = (ROOT / "scripts" / "approval-covers-base.py").read_text()
check("non-OPEN PRs are gated out before any base comparison",
      'if pr["state"] != "OPEN"' in src)
check("time-based, not commit_id: a re-stamped review keeps submitted_at",
      "commit_id" not in src.split('"""')[2])

print(f"\napproval-covers-base: {ran - len(fails)}/{ran} passed")
if fails:
    print("FAILED: " + ", ".join(fails)); raise SystemExit(1)
print("all passed")
