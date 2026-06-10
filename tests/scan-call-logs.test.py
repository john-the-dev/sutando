#!/usr/bin/env python3
"""Tests for src/scan-call-logs.py — pure detector functions.

Covers:
  a) detect_duplicate_responses()    — consecutive identical Sutando turns
  b) detect_access_issues()          — access-denial pattern variants
  c) detect_task_timeout()           — timeout/retry phrasing (one issue per transcript)
  d) detect_confusion()              — 2+ caller confusion signals threshold
  e) detect_fabrication()            — Sutando-context vs non-Sutando context
  f) detect_reconnect_leak()         — "I'm back" in Sutando lines
  g) detect_repeated_command()       — 3+ summon / 3+ tab-switch
  h) detect_identity_confusion()     — owner/human identity claims in Sutando lines
  i) detect_recording_confusion()    — recording complaint lines
  j) detect_scroll_frustration()     — 3+ scroll requests
  k) detect_stt_retry()              — word-overlap retry detection
  l) detect_metadata_issues()        — short call < 10s
  m) scan_entry()                    — orchestrates all detectors

Run: python3 tests/scan-call-logs.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "scan_call_logs",
    REPO / "src" / "scan-call-logs.py",
)
_mod = importlib.util.module_from_spec(spec)
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


def _patterns(issues: list[dict]) -> list[str]:
    return [i["pattern"] for i in issues]


# ---------------------------------------------------------------------------
# (a) detect_duplicate_responses
# ---------------------------------------------------------------------------

def _test_detect_duplicate_responses():
    ddr = _mod.detect_duplicate_responses

    # Clean — no repeats
    clean = "Sutando: Hello, how can I help you today?\nRecipient: I need help with my order.\nSutando: Sure, let me look into that for you."
    _check("dup-clean-empty", ddr(clean) == [])

    # Consecutive duplicate → triggers
    dup = (
        "Sutando: I'll check your account right now and get back to you shortly.\n"
        "Sutando: I'll check your account right now and get back to you shortly.\n"
        "Recipient: Hello?"
    )
    issues = ddr(dup)
    _check("dup-consecutive-triggers",  len(issues) == 1, f"issues={issues}")
    _check("dup-pattern-name",          issues[0]["pattern"] == "duplicate_response" if issues else False)
    _check("dup-severity-medium",       issues[0]["severity"] == "medium" if issues else False)

    # Three identical consecutive → still one issue (same key)
    triple = (
        "Sutando: I'll check your account right now and get back to you shortly.\n"
        "Sutando: I'll check your account right now and get back to you shortly.\n"
        "Sutando: I'll check your account right now and get back to you shortly.\n"
    )
    _check("dup-triple-one-issue", len(ddr(triple)) == 1)

    # Separated duplicates (not consecutive) → NOT triggered
    separated = (
        "Sutando: I'll check your account right now and get back to you shortly.\n"
        "Recipient: Thank you.\n"
        "Sutando: Of course, let me look that up.\n"
        "Sutando: I'll check your account right now and get back to you shortly.\n"
    )
    _check("dup-separated-no-trigger", ddr(separated) == [])

    # Short text (< 15 chars) ignored
    short = "Sutando: Yes.\nSutando: Yes.\n"
    _check("dup-short-ignored", ddr(short) == [])


_test_detect_duplicate_responses()


# ---------------------------------------------------------------------------
# (b) detect_access_issues
# ---------------------------------------------------------------------------

def _test_detect_access_issues():
    dai = _mod.detect_access_issues

    _check("access-clean", dai("Sutando: Sure, I can do that!") == [])

    triggers = [
        ("I can't access that calendar", "access_denied"),
        ("You are not authorized to do this", "not_authorized"),
        ("I don't have permission to send emails", "no_permission"),
        ("That requires owner-level access", "owner_only"),
        ("That feature isn't available to you", "feature_unavailable"),
    ]
    for text, expected_tag in triggers:
        issues = dai(f"Sutando: {text}")
        _check(f"access-{expected_tag}", any(expected_tag in i["pattern"] for i in issues),
               f"no {expected_tag} in {_patterns(issues)!r}")

    # Multiple patterns in same transcript → multiple issues
    multi = "I cannot access that. You are not authorized."
    _check("access-multi-issues", len(dai(multi)) >= 2)


_test_detect_access_issues()


# ---------------------------------------------------------------------------
# (c) detect_task_timeout
# ---------------------------------------------------------------------------

def _test_detect_task_timeout():
    dtt = _mod.detect_task_timeout

    _check("timeout-clean", dtt("Sutando: Done! I've sent the email.") == [])

    timeout_triggers = [
        "still working on that for you",
        "still processing your request",
        "let me try again",
        "sorry for taking a while",
        "request timed out",
    ]
    for phrase in timeout_triggers:
        issues = dtt(f"Sutando: {phrase}")
        _check(f"timeout-{phrase[:20]}", len(issues) == 1,
               f"expected 1 issue for {phrase!r}, got {len(issues)}")

    # Only one issue even if multiple timeout patterns present (break after first)
    multi_timeout = "still working... let me try again, sorry for taking so long"
    _check("timeout-one-issue-max", len(dtt(multi_timeout)) == 1)


_test_detect_task_timeout()


# ---------------------------------------------------------------------------
# (d) detect_confusion
# ---------------------------------------------------------------------------

def _test_detect_confusion():
    dc = _mod.detect_confusion

    _check("confusion-clean", dc("Sutando: Done.\nRecipient: Thanks.") == [])

    # Single confusion signal → NOT triggered (threshold is 2)
    single = "Recipient: Hello? Are you there?\nSutando: Yes, I'm here."
    _check("confusion-single-no-trigger", dc(single) == [])

    # Two confusion signals → triggered
    two = (
        "Recipient: Hello? Are you there?\n"
        "Sutando: Still here.\n"
        "Recipient: What? I don't understand.\n"
    )
    issues = dc(two)
    _check("confusion-two-triggers", len(issues) == 1, f"issues={issues}")
    _check("confusion-pattern", issues[0]["pattern"] == "caller_confusion" if issues else False)

    # Correction signal counts
    correction = (
        "Recipient: Hello? Are you there?\n"
        "Recipient: No, that's wrong, I said something else.\n"
    )
    _check("confusion-correction-counts", len(dc(correction)) == 1)

    # Confusion only counted in Recipient/Caller lines, not Sutando lines
    sutando_confusion = (
        "Sutando: Hello? Are you there?\n"
        "Sutando: What? I don't understand.\n"
    )
    _check("confusion-sutando-not-counted", dc(sutando_confusion) == [])


_test_detect_confusion()


# ---------------------------------------------------------------------------
# (e) detect_fabrication
# ---------------------------------------------------------------------------

def _test_detect_fabrication():
    df = _mod.detect_fabrication

    _check("fab-clean", df("Sutando: I'll look that up for you.") == [])

    # Fabrication in Sutando context → triggers
    fab_transcript = (
        "Recipient: What's the address?\n"
        "Sutando: The address is 123 Main Street.\n"
    )
    issues = df(fab_transcript)
    _check("fab-sutando-triggers", len(issues) >= 1,
           f"expected fabrication issue, got {issues}")
    _check("fab-severity-high", issues[0]["severity"] == "high" if issues else False)

    # Dollar amount in Sutando context
    balance_transcript = (
        "Recipient: What's my balance?\n"
        "Sutando: Your balance is $1,234.\n"
    )
    _check("fab-balance", len(df(balance_transcript)) >= 1)

    # Pattern in Recipient context → NOT flagged
    recipient_fab = (
        "Recipient: The address is 456 Oak Ave.\n"
        "Sutando: Okay, I see.\n"
    )
    _check("fab-recipient-no-flag", df(recipient_fab) == [])


_test_detect_fabrication()


# ---------------------------------------------------------------------------
# (f) detect_reconnect_leak
# ---------------------------------------------------------------------------

def _test_detect_reconnect_leak():
    drl = _mod.detect_reconnect_leak

    _check("reconnect-clean", drl("Sutando: How can I help you?") == [])

    variants = [
        "Sutando: I'm back! What were we talking about?",
        "Sutando: I am back, sorry for the interruption.",
        "Sutando: Welcome back! Let me pick up where we left off.",
    ]
    for v in variants:
        issues = drl(v)
        _check(f"reconnect-{v[:25]}", len(issues) == 1,
               f"expected issue for {v!r}")

    # "I'm back" in Recipient line → not flagged
    recipient_back = "Recipient: I'm back, sorry about that.\nSutando: No problem."
    _check("reconnect-recipient-no-flag", drl(recipient_back) == [])

    # Only one issue even if multiple occurrences (break after first)
    multi = "Sutando: I'm back.\nSutando: I'm back again.\n"
    _check("reconnect-one-issue-max", len(drl(multi)) == 1)


_test_detect_reconnect_leak()


# ---------------------------------------------------------------------------
# (g) detect_repeated_command
# ---------------------------------------------------------------------------

def _test_detect_repeated_command():
    drc = _mod.detect_repeated_command

    _check("repeated-clean", drc("Recipient: Can you help me?\nSutando: Sure.") == [])

    # 3 summon attempts → triggers
    summon3 = "\n".join([
        "Recipient: Can you summon it?",
        "Sutando: Trying...",
        "Recipient: Summon, please.",
        "Sutando: Working on it.",
        "Recipient: Could you summon the assistant?",
    ])
    issues = drc(summon3)
    patterns = _patterns(issues)
    _check("repeated-summon-triggers", "repeated_summon" in patterns,
           f"patterns={patterns}")

    # 2 summon attempts → NOT triggered
    summon2 = "Recipient: Summon.\nRecipient: Summon again.\n"
    _check("repeated-summon-2-no-trigger",
           "repeated_summon" not in _patterns(drc(summon2)))

    # 3 tab-switch attempts → triggers
    tab3 = "\n".join([
        "Recipient: Switch to the other tab.",
        "Recipient: Switch tabs please.",
        "Recipient: Open the next tab.",
    ])
    tab_issues = drc(tab3)
    _check("repeated-tab-triggers", "repeated_tab_switch" in _patterns(tab_issues),
           f"patterns={_patterns(tab_issues)}")


_test_detect_repeated_command()


# ---------------------------------------------------------------------------
# (h) detect_identity_confusion
# ---------------------------------------------------------------------------

def _test_detect_identity_confusion():
    dic = _mod.detect_identity_confusion

    _check("identity-clean", dic("Sutando: I'm Sutando, your AI assistant.") == [])

    # Sutando claims owner identity
    owner_claim = "Sutando: I'm Chi, the owner of this account."
    issues = dic(owner_claim)
    _check("identity-owner-triggers", len(issues) >= 1,
           f"issues={issues}")
    _check("identity-owner-severity", issues[0]["severity"] == "high" if issues else False)
    _check("identity-owner-tag",
           any("claimed_owner_identity" in i["pattern"] for i in issues))

    # Sutando denies being AI
    ai_denial = "Sutando: I am a human, not an AI system."
    ai_issues = dic(ai_denial)
    _check("identity-human-triggers", len(ai_issues) >= 1)
    _check("identity-human-tag",
           any("denied_ai_identity" in i["pattern"] for i in ai_issues))

    # Same claims in Recipient lines → not flagged
    recipient_claim = "Recipient: I'm Chi.\nSutando: Hi there."
    _check("identity-recipient-no-flag", dic(recipient_claim) == [])


_test_detect_identity_confusion()


# ---------------------------------------------------------------------------
# (i) detect_recording_confusion
# ---------------------------------------------------------------------------

def _test_detect_recording_confusion():
    drc2 = _mod.detect_recording_confusion

    _check("rec-clean", drc2("Recipient: Thanks.\nSutando: No problem.") == [])

    complaints = [
        "Caller: You haven't started recording yet!",
        "Recipient: You're still recording, please stop.",
        "Caller: Please stop the recording now.",
    ]
    for complaint in complaints:
        issues = drc2(complaint)
        _check(f"rec-{complaint[:30]}", len(issues) >= 1,
               f"expected issue for {complaint!r}")

    # Recording-like text but it's about menu/IVR → excluded
    ivr_text = "Caller: I haven't entered any numbers yet, press 1 for menu option."
    _check("rec-ivr-excluded", drc2(ivr_text) == [])


_test_detect_recording_confusion()


# ---------------------------------------------------------------------------
# (j) detect_scroll_frustration
# ---------------------------------------------------------------------------

def _test_detect_scroll_frustration():
    dsf = _mod.detect_scroll_frustration

    _check("scroll-clean", dsf("Recipient: Can you help me?\nSutando: Sure.") == [])

    # 2 scroll requests → NOT triggered
    two_scroll = "Recipient: Scroll down.\nRecipient: Scroll more.\n"
    _check("scroll-2-no-trigger", dsf(two_scroll) == [])

    # 3 scroll requests → triggers
    three_scroll = "\n".join([
        "Recipient: Can you scroll down?",
        "Sutando: Scrolling now.",
        "Recipient: Scroll more please.",
        "Sutando: Done.",
        "Recipient: Scroll to the bottom.",
    ])
    issues = dsf(three_scroll)
    _check("scroll-3-triggers", len(issues) == 1,
           f"issues={issues}")
    _check("scroll-pattern", issues[0]["pattern"] == "scroll_frustration" if issues else False)

    # 4 scroll requests → still one issue
    four_scroll = "\n".join([f"Recipient: Scroll {i}." for i in range(4)])
    _check("scroll-4-one-issue", len(dsf(four_scroll)) == 1)


_test_detect_scroll_frustration()


# ---------------------------------------------------------------------------
# (k) detect_stt_retry
# ---------------------------------------------------------------------------

def _test_detect_stt_retry():
    dsr = _mod.detect_stt_retry

    _check("stt-clean", dsr("Recipient: Hello.\nRecipient: Goodbye.\n") == [])

    # Identical lines → overlap = 1.0, NOT in (0.3, 0.9), no retry counted
    identical = (
        "Recipient: Please scroll to the top of the page.\n"
        "Recipient: Please scroll to the top of the page.\n"
    )
    _check("stt-identical-no-retry", dsr(identical) == [])

    # Three lines with moderate overlap (30%-90%) between each consecutive pair → triggers
    retry_lines = "\n".join([
        "Recipient: Can you please scroll to the bottom of this document right now?",
        "Recipient: Please scroll down to the end of the document on screen.",
        "Recipient: Scroll down to the bottom of the document please.",
        "Recipient: Could you scroll to the bottom of this page for me?",
    ])
    issues = dsr(retry_lines)
    _check("stt-retry-triggers", len(issues) == 1,
           f"expected issue, got {issues}")
    _check("stt-pattern", issues[0]["pattern"] == "stt_retry" if issues else False)
    _check("stt-severity-low", issues[0]["severity"] == "low" if issues else False)

    # Short lines (< 3 words) not counted (len check: `len(prev_words) >= 3`)
    short_lines = "Recipient: Yes.\nRecipient: No.\nRecipient: OK.\n"
    _check("stt-short-no-trigger", dsr(short_lines) == [])


_test_detect_stt_retry()


# ---------------------------------------------------------------------------
# (l) detect_metadata_issues
# ---------------------------------------------------------------------------

def _test_detect_metadata_issues():
    dmi = _mod.detect_metadata_issues

    # No duration → no issue
    _check("meta-no-duration", dmi({}) == [])
    _check("meta-zero-duration", dmi({"duration_seconds": 0}) == [])

    # Short call (< 10s), not a meeting → triggers
    issues = dmi({"duration_seconds": 5})
    _check("meta-short-triggers", len(issues) == 1,
           f"expected short_call, got {issues}")
    _check("meta-short-pattern", issues[0]["pattern"] == "short_call" if issues else False)
    _check("meta-short-severity", issues[0]["severity"] == "medium" if issues else False)

    # Short meeting → NOT flagged (meetings can be brief)
    meeting_issues = dmi({"duration_seconds": 3, "is_meeting": True})
    _check("meta-short-meeting-ok", meeting_issues == [])

    # Long call → no issue
    _check("meta-long-ok", dmi({"duration_seconds": 300}) == [])

    # Exactly 10s → no issue (boundary: < 10)
    _check("meta-10s-boundary", dmi({"duration_seconds": 10}) == [])

    # Negative duration → no issue (guard: > 0)
    _check("meta-negative-ok", dmi({"duration_seconds": -1}) == [])


_test_detect_metadata_issues()


# ---------------------------------------------------------------------------
# (m) scan_entry
# ---------------------------------------------------------------------------

def _test_scan_entry():
    se = _mod.scan_entry

    # Clean entry → None
    clean = {"transcript": "Sutando: Hello.\nRecipient: Hi.\nSutando: How can I help?"}
    _check("scan-clean-none", se(clean) is None)

    # Short transcript (< 20 chars) → detectors skipped, metadata checked
    short = {"transcript": "Hi.", "duration_seconds": 5}
    result = se(short)
    _check("scan-short-transcript-meta", result is not None)
    if result:
        _check("scan-short-has-issues", len(result["issues"]) >= 1)

    # Entry with timeout phrase → has issues
    timeout_entry = {"transcript": "Sutando: I'm still working on processing your request right now."}
    result2 = se(timeout_entry)
    _check("scan-timeout-not-none", result2 is not None)
    if result2:
        patterns = _patterns(result2["issues"])
        _check("scan-timeout-in-issues", "task_timeout" in patterns,
               f"patterns={patterns}")


_test_scan_entry()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"scan-call-logs: {_passed}/{total} passed{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
