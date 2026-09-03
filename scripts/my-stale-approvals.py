#!/usr/bin/env python3
"""Find approvals of yours that a later push left describing code nobody read.

A stale CHANGES_REQUESTED advertises itself: the PR is blocked and the author
says so. A stale APPROVAL is silent by construction, and on a repo with
`dismiss_stale_reviews_on_push: false` it keeps COUNTING toward the merge bar —
so it does not delay a PR, it authorises a head you never saw.

Two measurements this makes on your behalf, because both were got wrong by hand:

1. SCOPE. "PRs with a review requested of me" is not "PRs I approved". Measured
   on one repo: 19 stale approvals, of which 3 were in the review-request list.
   A scan filtered by review-requests reported 16% of the population as the total.

2. STALENESS. Compare against the newest AUTHORED commit, split by parent count.
   A base merge moves the head without anyone writing code, so comparing against
   the head alone marks approvals stale that are not.

DECISIVE means your vote can carry the PR now: open, not draft, nobody else
holding CHANGES_REQUESTED, and qualifying approvals already at or one short of
the bar. Those are the ones to re-review first; the rest are blocked by someone
else regardless of what you do.

Only COLLABORATOR/MEMBER/OWNER approvals count at the gate, so only those are
counted toward the bar.

Usage:
  python3 scripts/my-stale-approvals.py [--repo OWNER/NAME] [--login LOGIN]
                                        [--bar N] [--decisive-only] [--json]
Exit: 0 always (a report, not a gate) unless --fail-on-decisive is passed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

GATE_ASSOCIATIONS = ("COLLABORATOR", "MEMBER", "OWNER")


def gh_json(*args, default=None):
    p = subprocess.run(["gh", *args], capture_output=True, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        return default
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return default


def current_login() -> str | None:
    d = gh_json("api", "user", "--jq", '{login: .login}')
    return (d or {}).get("login")


def newest_authored(repo: str, number: int) -> str:
    """Newest commit someone actually wrote. Merge commits have 2+ parents and
    move the head without any review-worthy change, so they must not count."""
    commits = gh_json("api", f"repos/{repo}/pulls/{number}/commits", "--paginate", default=[]) or []
    dates = [c["commit"]["committer"]["date"] for c in commits if len(c.get("parents", [])) == 1]
    return max(dates, default="")


def latest_per_author(reviews):
    out = {}
    for r in reviews:
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED"):
            out[r["user"]["login"]] = r
    return out


def scan(repo: str, login: str, bar: int):
    prs = gh_json("pr", "list", "--repo", repo, "--state", "open", "--limit", "200",
                  "--json", "number,title,author,isDraft,baseRefName", default=[]) or []
    rows = []
    for p in prs:
        if p["isDraft"] or p["author"]["login"] == login:
            continue
        reviews = gh_json("api", f"repos/{repo}/pulls/{p['number']}/reviews", "--paginate", default=[]) or []
        latest = latest_per_author(reviews)
        mine = latest.get(login)
        if not mine or mine["state"] != "APPROVED":
            continue
        cutoff = newest_authored(repo, p["number"])
        if not cutoff or mine["submitted_at"] > cutoff:
            continue
        commits = gh_json("api", f"repos/{repo}/pulls/{p['number']}/commits", "--paginate", default=[]) or []
        after = [c for c in commits
                 if len(c.get("parents", [])) == 1
                 and c["commit"]["committer"]["date"] > mine["submitted_at"]]
        blockers = [u for u, r in latest.items()
                    if r["state"] == "CHANGES_REQUESTED" and u != login]
        qualifying = sum(1 for r in latest.values()
                         if r["state"] == "APPROVED" and r.get("author_association") in GATE_ASSOCIATIONS)
        rows.append({
            "number": p["number"], "title": p["title"], "author": p["author"]["login"],
            "base": p["baseRefName"], "approved_at": mine["submitted_at"],
            "newest_authored": cutoff, "commits_after": len(after),
            "blocked_by_others": blockers, "qualifying_approvals": qualifying,
            "decisive": not blockers and qualifying >= bar - 1,
        })
    rows.sort(key=lambda r: (not r["decisive"], -r["commits_after"]))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="sonichi/sutando")
    ap.add_argument("--login", default=None, help="defaults to the authenticated gh user")
    ap.add_argument("--bar", type=int, default=2, help="required approving reviews")
    ap.add_argument("--decisive-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-decisive", action="store_true",
                    help="exit 1 when a decisive stale approval exists")
    a = ap.parse_args()

    login = a.login or current_login()
    if not login:
        print("my-stale-approvals: could not resolve a login (gh api user failed); "
              "pass --login", file=sys.stderr)
        return 2

    rows = scan(a.repo, login, a.bar)
    if a.decisive_only:
        rows = [r for r in rows if r["decisive"]]
    if a.json:
        print(json.dumps({"repo": a.repo, "login": login, "bar": a.bar, "rows": rows}, indent=2))
    else:
        decisive = sum(1 for r in rows if r["decisive"])
        print(f"{login} on {a.repo}: {len(rows)} stale approval(s), {decisive} decisive (bar={a.bar})")
        for r in rows:
            mark = "DECISIVE" if r["decisive"] else "blocked "
            why = f"blocked by {','.join(r['blocked_by_others'])}" if r["blocked_by_others"] \
                else f"{r['qualifying_approvals']}/{a.bar} qualifying"
            print(f"  {mark} #{r['number']:<5} {r['author']:<15} "
                  f"{r['commits_after']} commit(s) after your {r['approved_at'][:10]}  "
                  f"[{why}] base={r['base'][:12]}  {r['title'][:44]}")
        if not rows:
            print("  none — every approval of yours is at its PR's newest authored commit")
    if a.fail_on_decisive and any(r["decisive"] for r in rows):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
