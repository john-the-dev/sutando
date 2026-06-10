#!/usr/bin/env python3
"""Tests for src/github-webhook.py — signature verification and event formatting.

Covers:
  a) verify_github_signature() — correct HMAC, wrong HMAC, missing/empty secret, bad prefix
  b) format_event() — issues/opened, pull_request/opened, pull_request/merged,
     pull_request/closed-not-merged, star/created, issue_comment/created (human + bot),
     unknown event type, missing action

Run: python3 tests/github-webhook.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib.util
import os
import sys
from pathlib import Path

# Must set before loading the module — WEBHOOK_SECRET is read at module level.
os.environ.setdefault("GITHUB_WEBHOOK_SECRET", "test-secret-xyz")

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "github_webhook",
    REPO / "src" / "github-webhook.py",
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

# Ensure the test secret is active regardless of what .env loaded.
_mod.WEBHOOK_SECRET = "test-secret-xyz"

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _make_sig(body: bytes, secret: str = "test-secret-xyz") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# (a) verify_github_signature
# ---------------------------------------------------------------------------

def _test_verify_signature():
    vgs = _mod.verify_github_signature
    body = b'{"action":"opened","repository":{"full_name":"owner/repo"}}'
    good_sig = _make_sig(body)

    _check("valid-sig-true",        vgs(body, good_sig))
    _check("wrong-sig-false",       not vgs(body, _make_sig(b"other body")))
    _check("tampered-body-false",   not vgs(b"tampered", good_sig))
    _check("empty-sig-false",       not vgs(body, ""))
    _check("no-sha256-prefix-false",not vgs(body, good_sig.replace("sha256=", "md5=")))
    _check("wrong-secret-false",    not vgs(body, _make_sig(body, "wrong-secret")))

    # No secret configured → always False
    orig = _mod.WEBHOOK_SECRET
    _mod.WEBHOOK_SECRET = ""
    _check("no-secret-always-false", not vgs(body, good_sig))
    _mod.WEBHOOK_SECRET = orig

    # Timing-safe: uses hmac.compare_digest (structural check)
    import inspect
    src = inspect.getsource(vgs)
    _check("uses-compare-digest", "compare_digest" in src,
           "signature comparison must be timing-safe")


_test_verify_signature()


# ---------------------------------------------------------------------------
# (b) format_event
# ---------------------------------------------------------------------------

def _make_repo(name: str = "owner/repo") -> dict:
    return {"full_name": name, "stargazers_count": 42}


def _make_sender(login: str = "user123") -> dict:
    return {"login": login}


def _test_format_event():
    fe = _mod.format_event

    # issues/opened
    result = fe("issues", {
        "action": "opened",
        "repository": _make_repo(),
        "sender": _make_sender(),
        "issue": {"number": 7, "title": "Something broke", "body": "Details here"},
    })
    _check("issues-opened-not-none", result is not None)
    _check("issues-opened-contains-number", result is not None and "#7" in result)
    _check("issues-opened-contains-title", result is not None and "Something broke" in result)
    _check("issues-opened-contains-sender", result is not None and "@user123" in result)

    # issues/closed → None (not handled)
    result_closed = fe("issues", {"action": "closed", "repository": _make_repo(), "sender": _make_sender(), "issue": {"number": 1, "title": "x"}})
    _check("issues-closed-none", result_closed is None)

    # pull_request/opened
    result_pr = fe("pull_request", {
        "action": "opened",
        "repository": _make_repo(),
        "sender": _make_sender("devbot"),
        "pull_request": {"number": 42, "title": "Add feature", "body": "Implements X"},
    })
    _check("pr-opened-not-none",      result_pr is not None)
    _check("pr-opened-contains-42",   result_pr is not None and "#42" in result_pr)
    _check("pr-opened-contains-title",result_pr is not None and "Add feature" in result_pr)

    # pull_request/closed + merged
    result_merged = fe("pull_request", {
        "action": "closed",
        "repository": _make_repo(),
        "sender": _make_sender(),
        "pull_request": {"number": 99, "title": "Merge me", "merged": True},
    })
    _check("pr-merged-not-none",    result_merged is not None)
    _check("pr-merged-contains-99", result_merged is not None and "#99" in result_merged)

    # pull_request/closed without merge → None
    result_not_merged = fe("pull_request", {
        "action": "closed",
        "repository": _make_repo(),
        "sender": _make_sender(),
        "pull_request": {"number": 5, "title": "Closed without merge", "merged": False},
    })
    _check("pr-closed-no-merge-none", result_not_merged is None)

    # star/created
    result_star = fe("star", {
        "action": "created",
        "repository": _make_repo(),
        "sender": _make_sender("stargazer"),
    })
    _check("star-created-not-none",    result_star is not None)
    _check("star-created-sender",      result_star is not None and "@stargazer" in result_star)
    _check("star-created-count",       result_star is not None and "42" in result_star)

    # issue_comment/created by human
    result_comment = fe("issue_comment", {
        "action": "created",
        "repository": _make_repo(),
        "sender": _make_sender("alice"),
        "issue": {"number": 3, "title": "Open issue"},
        "comment": {"body": "Looks good to me", "user": {"type": "User"}},
    })
    _check("comment-human-not-none",  result_comment is not None)
    _check("comment-human-contains",  result_comment is not None and "Looks good to me" in result_comment)
    _check("comment-human-sender",    result_comment is not None and "@alice" in result_comment)

    # issue_comment/created by Bot → None
    result_bot = fe("issue_comment", {
        "action": "created",
        "repository": _make_repo(),
        "sender": _make_sender("ci-bot"),
        "issue": {"number": 3, "title": "Open issue"},
        "comment": {"body": "CI passed", "user": {"type": "Bot"}},
    })
    _check("comment-bot-none", result_bot is None)

    # Unknown event type → None
    result_unknown = fe("deployment", {"action": "created", "repository": _make_repo(), "sender": _make_sender()})
    _check("unknown-event-none", result_unknown is None)

    # Missing action field → None (no matching branch)
    result_no_action = fe("issues", {"repository": _make_repo(), "sender": _make_sender(), "issue": {"number": 1, "title": "x"}})
    _check("missing-action-none", result_no_action is None)

    # Body truncated at 500 chars for issues
    long_body = "x" * 500 + "UNIQUE_TAIL_MARKER"
    result_long = fe("issues", {
        "action": "opened",
        "repository": _make_repo(),
        "sender": _make_sender(),
        "issue": {"number": 1, "title": "Long body issue", "body": long_body},
    })
    _check("body-truncated-500", result_long is not None and "UNIQUE_TAIL_MARKER" not in result_long,
           "body beyond 500 chars should be truncated")

    # No body field in issue → doesn't crash
    result_no_body = fe("issues", {
        "action": "opened",
        "repository": _make_repo(),
        "sender": _make_sender(),
        "issue": {"number": 2, "title": "No body"},
    })
    _check("missing-body-no-crash", result_no_body is not None)


_test_format_event()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"github-webhook: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
