#!/usr/bin/env python3
"""
Tests for result-body protocol marker support in src/task-bridge.ts.

Issue #1381 item 2: TS voice surfaces (task-bridge, phone, discord-voice)
didn't honor [no-send] / [REPLIED] markers from result_markers.py spec.
The python bridges (discord, telegram, slack) already honored them; this
gap meant the markers leaked as literal text to voice narration when the
agent used them on voice-path tasks.

Cases:
  a) [no-send] marker regex present in task-bridge.ts
  b) [REPLIED] marker regex present in task-bridge.ts
  c) both markers archive silently (no-send path verified structurally)
  d) [deduped:] marker still present (regression guard)
  e) marker check comes BEFORE voice-offline forwarding (order matters)
  f) case-insensitive matching for [no-send] and [REPLIED]

Run: python3 tests/task-bridge-result-markers.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "task-bridge.ts"


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def case_a_no_send_marker_present():
    """[no-send] regex must be present in task-bridge.ts."""
    src = _source()
    if "no-send" not in src:
        return ["a) '[no-send]' not found in task-bridge.ts — marker not handled"]
    if not re.search(r"no-send", src):
        return ["a) no-send marker pattern not found"]
    return []


def case_b_replied_marker_present():
    """[REPLIED] regex must be present in task-bridge.ts."""
    src = _source()
    if "REPLIED" not in src:
        return ["b) '[REPLIED]' not found in task-bridge.ts — marker not handled"]
    return []


def case_c_silent_archive_on_no_send():
    """The [no-send] branch must archive silently (no onResult call before archiveFile)."""
    src = _source()
    # Find the [no-send] handler block
    no_send_idx = src.find("no-send")
    if no_send_idx == -1:
        return ["c) no-send block not found"]

    # Extract the handler block (from the regex check to the 'continue')
    block_start = src.rfind("\n", 0, no_send_idx)
    block_end = src.find("continue;", no_send_idx)
    if block_end == -1:
        return ["c) 'continue' not found after no-send check — may not skip delivery"]
    block = src[block_start:block_end + len("continue;")]

    # Must NOT call onResult() (which would narrate to voice)
    if re.search(r"\bonResult\s*\(", block):
        return ["c) [no-send] block calls onResult() — result would be narrated to voice (wrong)"]

    # Must call archiveFile (or schedule it via setTimeout)
    if "archiveFile" not in block:
        return ["c) [no-send] block does not call archiveFile — result not cleaned up"]

    return []


def case_d_deduped_marker_regression():
    """[deduped:] handler must still be present (regression guard)."""
    src = _source()
    if "deduped" not in src.lower():
        return ["d) [deduped:] marker handler not found — regression: was previously present"]
    return []


def case_e_marker_check_before_voice_offline():
    """[no-send]/[REPLIED] check must appear BEFORE the voice-offline forwarding block.

    If order is reversed, a [no-send] result from a voice task would be forwarded
    to Discord DM before the marker check gets a chance to suppress it.
    """
    src = _source()
    no_send_idx = src.find("no-send")
    # Find the voice-offline forwarding block (keyed by its canonical comment)
    offline_idx = src.find("Voice client offline")
    if no_send_idx == -1:
        return ["e) no-send block not found"]
    if offline_idx == -1:
        return ["e) voice-offline block not found — can't verify order"]
    if no_send_idx > offline_idx:
        return [
            "e) [no-send] check comes AFTER the voice-offline forwarding block — "
            "no-send results would be forwarded to Discord before being suppressed"
        ]
    return []


def case_f_case_insensitive_matching():
    """Marker regexes must use case-insensitive flag (/i) or uppercase REPLIED."""
    src = _source()
    # [REPLIED] is uppercase-canonical per result_markers.py spec; /i allows variants
    # [no-send] uses /i in the spec. Check that both use /i or equivalent.
    no_send_pattern = re.search(r"no-send.*?/i", src)
    replied_pattern = re.search(r"REPLIED.*?/i", src)
    fails = []
    if not no_send_pattern:
        # Accept bare /i anywhere near the no-send check
        if not re.search(r"no-send[^;]*\}/i", src):
            fails.append("f) [no-send] regex does not use /i flag — case-sensitive match only")
    if not replied_pattern:
        if not re.search(r"REPLIED[^;]*\}/i", src):
            fails.append("f) [REPLIED] regex does not use /i flag — case-sensitive match only")
    return fails


def main() -> int:
    cases = [
        ("a", case_a_no_send_marker_present),
        ("b", case_b_replied_marker_present),
        ("c", case_c_silent_archive_on_no_send),
        ("d", case_d_deduped_marker_regression),
        ("e", case_e_marker_check_before_voice_offline),
        ("f", case_f_case_insensitive_matching),
    ]
    all_failures = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as exc:
            fails = [f"{label}) raised {type(exc).__name__}: {exc}"]
        if fails:
            all_failures.extend(fails)
            print(f"  FAIL case {label}")
            for f in fails:
                print(f"    {f}")
        else:
            print(f"  PASS case {label}")

    total = len(cases)
    failed = len(all_failures)
    print(f"\nResults: {total - failed}/{total} passed")
    return 1 if all_failures else 0


if __name__ == "__main__":
    sys.exit(main())
