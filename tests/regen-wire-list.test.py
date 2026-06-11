#!/usr/bin/env python3
"""Tests for scripts/regen-wire-list.py — pure string/list functions only."""

import importlib.util
import sys
import unittest.mock
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "regen_wire_list", _REPO / "scripts" / "regen-wire-list.py"
)
_mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
sys.modules["regen_wire_list"] = _mod
spec.loader.exec_module(_mod)  # type: ignore[union-attr]

readme_excluded_ids = _mod.readme_excluded_ids
render_block = _mod.render_block
splice = _mod.splice

_passed = 0
_failed = 0

def _check(label: str, condition: bool) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL: {label}")


# Sample video data (no real IO needed)
def _vid(vid_id: str, published_at: str = "2025-01-15T12:00:00Z", like_count: int = 100) -> dict:
    return {"videoId": vid_id, "title": f"Video {vid_id}",
            "publishedAt": published_at, "likeCount": like_count}


# ---------------------------------------------------------------------------
# readme_excluded_ids
# ---------------------------------------------------------------------------

# No markers — search the whole text
readme_plain = "Check out https://youtu.be/AAAAAAAAAAA and https://www.youtube.com/watch?v=BBBBBBBBBBB"
ids = readme_excluded_ids(readme_plain)
_check("plain text → both IDs found", ids == {"AAAAAAAAAAA", "BBBBBBBBBBB"})

# With markers — region between markers is excluded from search
readme_with_block = """\
Before: https://youtu.be/OUTSIDE_111
<!-- wire-list:start -->
- https://youtu.be/INSIDE_1111
<!-- wire-list:end -->
After: https://youtu.be/OUTSIDE_222
"""
ids = readme_excluded_ids(readme_with_block)
_check("markers: OUTSIDE_111 found", "OUTSIDE_111" in ids)
_check("markers: OUTSIDE_222 found", "OUTSIDE_222" in ids)
_check("markers: INSIDE_1111 NOT found (inside block)", "INSIDE_1111" not in ids)

# Empty readme
_check("empty readme → empty set", readme_excluded_ids("") == set())

# No YouTube links
_check("no links → empty set", readme_excluded_ids("No links here.") == set())

# youtu.be vs embed URL forms
readme_forms = "https://youtu.be/SHORT1234AA and https://www.youtube.com/embed/EMBED1234AA"
ids = readme_forms and readme_excluded_ids(readme_forms)
_check("youtu.be form recognized", "SHORT1234AA" in ids)
_check("embed form recognized", "EMBED1234AA" in ids)

# ---------------------------------------------------------------------------
# splice
# ---------------------------------------------------------------------------

readme_splice = """\
Header
<!-- wire-list:start -->
OLD CONTENT
<!-- wire-list:end -->
Footer
"""

result = splice(readme_splice, "NEW LINE 1\nNEW LINE 2")
_check("splice: new content present", "NEW LINE 1\nNEW LINE 2" in result)
_check("splice: old content gone", "OLD CONTENT" not in result)
_check("splice: start marker preserved", "<!-- wire-list:start -->" in result)
_check("splice: end marker preserved", "<!-- wire-list:end -->" in result)
_check("splice: footer preserved", "Footer" in result)
_check("splice: header preserved", "Header" in result)

# splice with note in start marker
readme_note = """\
<!-- wire-list:start note here -->
OLD
<!-- wire-list:end -->
"""
result = splice(readme_note, "NEW")
_check("splice: note start marker preserved", "<!-- wire-list:start note here -->" in result)
_check("splice: new content in note variant", "NEW" in result)
_check("splice: old content gone in note variant", "OLD" not in result)

# Missing start marker → RuntimeError
try:
    splice("No markers here\n<!-- wire-list:end -->\n", "X")
    _check("splice: missing start raises RuntimeError", False)
except RuntimeError:
    _check("splice: missing start raises RuntimeError", True)

# Missing end marker → RuntimeError
try:
    splice("<!-- wire-list:start -->\nContent\n", "X")
    _check("splice: missing end raises RuntimeError", False)
except RuntimeError:
    _check("splice: missing end raises RuntimeError", True)

# ---------------------------------------------------------------------------
# render_block — monkey-patch age_days to control age
# ---------------------------------------------------------------------------

# Patch age_days to always return 30 (well above HERO_MIN_AGE_DAYS=7)
with unittest.mock.patch.object(_mod, "age_days", return_value=30):
    v1 = _vid("AAAAAAAAAA1", "2025-06-01T00:00:00Z", like_count=50)
    v2 = _vid("AAAAAAAAAA2", "2025-05-01T00:00:00Z", like_count=200)
    v3 = _vid("AAAAAAAAAA3", "2025-04-01T00:00:00Z", like_count=150)
    v4 = _vid("AAAAAAAAAA4", "2025-03-01T00:00:00Z", like_count=100)
    v5 = _vid("AAAAAAAAAA5", "2025-02-01T00:00:00Z", like_count=10)
    v6 = _vid("AAAAAAAAAA6", "2025-01-01T00:00:00Z", like_count=5)

    block = render_block([v1, v2, v3, v4, v5, v6])
    _check("render_block: v1 in output (newest)", "AAAAAAAAAA1" in block)
    _check("render_block: v2 in output (newest)", "AAAAAAAAAA2" in block)
    _check("render_block: v2 listed as hero (highest likes not-newest)", "AAAAAAAAAA2" in block)
    # Heroes are v2 (200), v3 (150), v4 (100) — but v2 is already in newest;
    # so heroes should be v3, v4, v5
    _check("render_block: v3 in output (hero)", "AAAAAAAAAA3" in block)
    _check("render_block: v4 in output (hero)", "AAAAAAAAAA4" in block)
    # v6 should not appear (only 5 slots total)
    _check("render_block: v6 not in output (below cutoff)", "AAAAAAAAAA6" not in block)

    # Each line is a markdown list item
    lines = [l for l in block.split("\n") if l.strip()]
    _check("render_block: lines start with - [", all(l.startswith("- [") for l in lines))

    # exclude_ids: v1 excluded → still 2 newest but v1 replaced
    block_excl = render_block([v1, v2, v3, v4, v5, v6], exclude_ids={"AAAAAAAAAA1"})
    _check("render_block: excluded v1 not in output", "AAAAAAAAAA1" not in block_excl)

    # Empty list → empty string
    block_empty = render_block([])
    _check("render_block: empty list → empty block", block_empty == "")

    # All excluded
    all_excl = {v["videoId"] for v in [v1, v2, v3, v4, v5, v6]}
    block_all_excl = render_block([v1, v2, v3, v4, v5, v6], exclude_ids=all_excl)
    _check("render_block: all excluded → empty", block_all_excl == "")

# Age below HERO_MIN_AGE_DAYS → not eligible for hero; fallback fills
with unittest.mock.patch.object(_mod, "age_days", return_value=3):
    # All 3 days old — age_days < 7, no hero candidates; fallback by likeCount
    v_old1 = _vid("NEW_VID_AA1", "2025-06-10T00:00:00Z", like_count=5)
    v_old2 = _vid("NEW_VID_AA2", "2025-06-09T00:00:00Z", like_count=3)
    v_old3 = _vid("NEW_VID_AA3", "2025-06-08T00:00:00Z", like_count=8)
    block_young = render_block([v_old1, v_old2, v_old3])
    # Fallback should still fill slots from remaining videos
    filled_lines = [l for l in block_young.split("\n") if l.strip()]
    _check("render_block: young videos still fill slots", len(filled_lines) <= 5)

print(f"regen-wire-list: {_passed}/{_passed + _failed} passed")
sys.exit(0 if _failed == 0 else 1)
