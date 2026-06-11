#!/usr/bin/env python3
"""Tests for skills/submit-use-case/scripts/submit_use_case.py — pure helpers.

Covers:
  a) slugify()                — URL-slug generation
  b) validate_title()         — outcome-framing gate
  c) suggest_reframes()       — cheap deterministic suggestions
  d) render_long_description() — summary + bullets stitch
  e) _yaml_escape()           — YAML scalar escaping
  f) render_pr_file()         — frontmatter file rendering
  g) render_issue_body()      — markdown issue body

Run: python3 tests/submit-use-case.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "skills" / "submit-use-case" / "scripts" / "submit_use_case.py"

spec = importlib.util.spec_from_file_location("submit_use_case", SCRIPT)
_mod = importlib.util.module_from_spec(spec)
sys.modules["submit_use_case"] = _mod
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


# ---------------------------------------------------------------------------
# (a) slugify
# ---------------------------------------------------------------------------

def _test_slugify():
    f = _mod.slugify

    # Basic lowercase
    _check("sl-basic",       f("Hello World") == "hello-world")

    # Special chars become dashes
    _check("sl-special",     f("Foo & Bar!") == "foo-bar")

    # Consecutive non-alnum → single dash
    _check("sl-multi-sep",   f("foo  --  bar") == "foo-bar")

    # Leading/trailing dashes stripped
    _check("sl-strip",       f("---foo---") == "foo")

    # Long title truncated at 60 chars
    long = "a" * 100
    result = f(long)
    _check("sl-truncate",    len(result) <= 60)
    _check("sl-truncate-alnum", result == "a" * 60)

    # Numbers preserved
    _check("sl-numbers",     f("PR 1234") == "pr-1234")

    # Empty → empty
    _check("sl-empty",       f("") == "")


_test_slugify()


# ---------------------------------------------------------------------------
# (b) validate_title
# ---------------------------------------------------------------------------

def _test_validate_title():
    f = _mod.validate_title

    # Too short (< 6 chars)
    ok, reason = f("Hi")
    _check("vt-too-short",    not ok)
    _check("vt-short-reason", "length" in reason)

    # Too long (> 80 chars)
    ok, reason = f("A" * 81)
    _check("vt-too-long",     not ok)
    _check("vt-long-reason",  "length" in reason)

    # Trailing period rejected
    ok, reason = f("Run your business hands-free.")
    _check("vt-trailing-dot", not ok)
    _check("vt-dot-reason",   "period" in reason or "trailing" in reason.lower())

    # REJECT_PATTERN: "Ask your AI..."
    ok, reason = f("Ask your AI to handle emails")
    _check("vt-reject-ask",   not ok)
    _check("vt-reject-cap",   "capability" in reason or "reframe" in reason.lower() or "framed" in reason)

    # REJECT_PATTERN: "Sutando can..."
    ok, reason = f("Sutando can send emails for you")
    _check("vt-reject-sutando-can", not ok)

    # REJECT_PATTERN: "Send a..."
    ok, reason = f("Send a message to your team")
    _check("vt-reject-send",  not ok)

    # REJECT_PATTERN: "AI that can..."
    ok, reason = f("An AI that can do everything")
    _check("vt-reject-ai-can", not ok)

    # Valid outcome-framed title
    ok, reason = f("Run your business hands-free")
    _check("vt-valid-ok",     ok)
    _check("vt-valid-reason", "outcome" in reason.lower())

    # Exactly 6 chars → valid
    ok, reason = f("Fix it")
    _check("vt-exact-6",      ok)

    # Exactly 80 chars → valid
    ok, reason = f("A" * 80)
    _check("vt-exact-80",     ok)


_test_validate_title()


# ---------------------------------------------------------------------------
# (c) suggest_reframes
# ---------------------------------------------------------------------------

def _test_suggest_reframes():
    f = _mod.suggest_reframes

    result = f("Ask your AI to manage your inbox")
    _check("sr-returns-list",   isinstance(result, list))
    _check("sr-has-items",      len(result) >= 2)
    # One entry should reference the original title text
    combined = " ".join(result).lower()
    _check("sr-contains-inbox", "inbox" in combined or "manage" in combined)
    # Trailing period stripped in the "have your AI" variant
    result2 = f("send emails.")
    _check("sr-no-double-dot",  not any(s.endswith("..") for s in result2))


_test_suggest_reframes()


# ---------------------------------------------------------------------------
# (d) render_long_description
# ---------------------------------------------------------------------------

def _test_render_long_description():
    f = _mod.render_long_description

    # No bullets → just the summary
    result = f("Great tool", [])
    _check("rld-no-bullets",    result == "Great tool")

    # One bullet → summary + bullet
    result = f("Great tool", ["Does the work"])
    _check("rld-one-bullet",    result.startswith("Great tool"))
    _check("rld-bullet-joined", "Does the work." in result)

    # Multiple bullets joined with spaces
    result = f("Summary", ["Step one", "Step two", "Step three"])
    _check("rld-multi-bullets", result.startswith("Summary"))
    _check("rld-multi-joined",  "Step one." in result and "Step two." in result)

    # Bullet already ending in period → just one period
    result = f("Summary", ["Already done."])
    _check("rld-no-double-dot", ".." not in result)


_test_render_long_description()


# ---------------------------------------------------------------------------
# (e) _yaml_escape
# ---------------------------------------------------------------------------

def _test_yaml_escape():
    f = _mod._yaml_escape

    # No special chars → unchanged
    _check("ye-plain",       f("hello world") == "hello world")

    # Double quote escaped
    _check("ye-dquote",      f('say "hi"') == r'say \"hi\"')

    # Backslash escaped
    _check("ye-backslash",   f("a\\b") == "a\\\\b")

    # Both
    result = f('path\\to\\"file"')
    _check("ye-both",        "\\\\" in result and '\\"' in result)

    # Empty → empty
    _check("ye-empty",       f("") == "")


_test_yaml_escape()


# ---------------------------------------------------------------------------
# (f) render_pr_file
# ---------------------------------------------------------------------------

def _test_render_pr_file():
    f = _mod.render_pr_file

    # Minimal required fields
    result = f(
        slug="run-biz-free",
        title="Run your business hands-free",
        summary="Automate the boring parts",
        long_desc="Full description here.",
        video_url=None,
        youtube_id=None,
        x_url=None,
        linkedin_url=None,
        contact=None,
        submitted_at="2026-06-10",
    )
    _check("rpf-starts-frontmatter", result.startswith("---"))
    _check("rpf-has-slug",   'slug: "run-biz-free"' in result)
    _check("rpf-has-title",  'title: "Run your business hands-free"' in result)
    _check("rpf-has-summary",'summary: "Automate the boring parts"' in result)
    _check("rpf-has-thumbnail", 'thumbnail: "/use-cases/run-biz-free.jpg"' in result)
    _check("rpf-ends-content", "Full description here." in result)
    # Optional fields absent → not in output
    _check("rpf-no-video",   "videoUrl" not in result)
    _check("rpf-no-youtube", "youtubeId" not in result)

    # Optional fields present
    result2 = f(
        slug="test",
        title="Test title",
        summary="Summary",
        long_desc="Desc.",
        video_url="https://example.com/v",
        youtube_id="abc123",
        x_url="https://x.com/post",
        linkedin_url="https://linkedin.com/post",
        contact="user@example.com",
        submitted_at="2026-06-10",
    )
    _check("rpf-video-present",    'videoUrl: "https://example.com/v"' in result2)
    _check("rpf-youtube-present",  'youtubeId: "abc123"' in result2)
    _check("rpf-x-present",        'xUrl: "https://x.com/post"' in result2)
    _check("rpf-contact-present",  'contact: "user@example.com"' in result2)

    # Special chars in title escaped
    result3 = f(
        slug="test",
        title='Title with "quotes"',
        summary="Summary",
        long_desc="Desc.",
        video_url=None, youtube_id=None, x_url=None,
        linkedin_url=None, contact=None,
        submitted_at="2026-06-10",
    )
    _check("rpf-title-escaped", '\\"quotes\\"' in result3 or r'\"quotes\"' in result3)


_test_render_pr_file()


# ---------------------------------------------------------------------------
# (g) render_issue_body
# ---------------------------------------------------------------------------

def _test_render_issue_body():
    f = _mod.render_issue_body

    # Minimal
    result = f(
        slug="run-biz",
        title="Run your biz",
        summary="Great outcome",
        bullets=[],
        video_path=None,
        media_url=None,
        youtube_id=None,
        x_url=None,
        linkedin_url=None,
        contact=None,
        pr_branch=None,
    )
    _check("rib-has-title",   "## Use case: Run your biz" in result)
    _check("rib-has-summary", "Great outcome" in result)
    _check("rib-no-bullets",  "### What happens" not in result)
    _check("rib-has-meta",    "slug: `run-biz`" in result)
    _check("rib-framing-check", "framing" in result.lower())

    # With bullets
    result2 = f(
        slug="test",
        title="Test",
        summary="Sum",
        bullets=["Step A", "Step B"],
        video_path=None, media_url=None, youtube_id=None,
        x_url=None, linkedin_url=None, contact=None, pr_branch=None,
    )
    _check("rib-bullets-section", "### What happens" in result2)
    _check("rib-bullet-a",        "- Step A" in result2)

    # With links
    result3 = f(
        slug="test",
        title="Test",
        summary="Sum",
        bullets=[],
        video_path="/tmp/vid.mp4",
        media_url=None,
        youtube_id="xyz",
        x_url="https://x.com",
        linkedin_url=None,
        contact="me@example.com",
        pr_branch="community-use-case/test",
    )
    _check("rib-video-path",   "/tmp/vid.mp4" in result3)
    _check("rib-youtube",      "youtu.be/xyz" in result3)
    _check("rib-x-url",        "https://x.com" in result3)
    _check("rib-contact",      "me@example.com" in result3)
    _check("rib-pr-branch",    "community-use-case/test" in result3)


_test_render_issue_body()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"submit-use-case: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
