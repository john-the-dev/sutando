#!/usr/bin/env python3
"""Regression guard: pure string helpers in skills/obsidian-vault/scripts/dream.py.

Functions under test (all IO-free):

  _strip_frontmatter(body) -> str
    Removes YAML front-matter delimited by "---\n...\n---\n" at the start.

  _strip_dream_block(body) -> str
    Removes <!-- sutando-dream:start --> … <!-- sutando-dream:end --> blocks.

  _strip_codefence(s) -> str
    Strips opening ```[lang] and closing ``` from JSON-fence strings.

  apply_inline_ref(body, quote, target_stem) -> (str, bool)
    Appends "(cf. [[target_stem]])" after the paragraph containing quote.
    Returns (body, False) when quote is absent or citation already present.

  build_footer(tiered) -> str
    Renders a markdown footer block between the dream sentinels.

  upsert_footer(body, footer_block) -> str
    Replaces existing dream block (if any) or appends; ensures single blank line.

`anthropic` is mocked so the module loads without the package installed.

Run: python3 tests/obsidian-dream-helpers.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Mock anthropic before loading dream
_anthro = types.ModuleType("anthropic")
_anthro.Anthropic = None
sys.modules["anthropic"] = _anthro

spec = importlib.util.spec_from_file_location(
    "dream", REPO / "skills" / "obsidian-vault" / "scripts" / "dream.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["dream"] = _mod
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
# _strip_frontmatter
# ---------------------------------------------------------------------------

def _test_strip_frontmatter():
    f = _mod._strip_frontmatter

    # Standard YAML frontmatter stripped
    body = "---\ntitle: Test\ndate: 2026-06-10\n---\n# Heading\nContent here."
    out = f(body)
    _check("fm-basic",        "title" not in out)
    _check("fm-body-kept",    "# Heading" in out)

    # No frontmatter → unchanged
    body2 = "# Note without frontmatter\nSome content."
    _check("fm-none", f(body2) == body2)

    # Minimal frontmatter block (one blank key)
    body3 = "---\nkey:\n---\n# After"
    out3 = f(body3)
    _check("fm-empty",      "key:" not in out3)
    _check("fm-empty-body", "# After" in out3)

    # Frontmatter only (no body after)
    body4 = "---\ntitle: X\n---\n"
    out4 = f(body4)
    _check("fm-only", out4.strip() == "")

    # Only first block stripped (count=1)
    body5 = "---\nfirst: 1\n---\n---\nsecond: 2\n---\nContent"
    out5 = f(body5)
    _check("fm-count1", "first" not in out5)
    _check("fm-count1-second", "second: 2" in out5)


_test_strip_frontmatter()


# ---------------------------------------------------------------------------
# _strip_dream_block
# ---------------------------------------------------------------------------

def _test_strip_dream_block():
    f = _mod._strip_dream_block
    START = _mod.SENTINEL_START
    END = _mod.SENTINEL_END

    # Body with dream block in middle
    body = f"Paragraph one.\n\n{START}\n## Related\n\n- [[some-note]]\n{END}\n\nParagraph two."
    out = f(body)
    _check("db-start-gone",    START not in out)
    _check("db-end-gone",      END not in out)
    _check("db-para1-kept",    "Paragraph one." in out)
    _check("db-para2-kept",    "Paragraph two." in out)
    _check("db-link-gone",     "[[some-note]]" not in out)

    # No dream block → unchanged
    body2 = "Clean note without any dream block."
    _check("db-none", f(body2) == body2)

    # Multiple dream blocks stripped
    block = f"{START}\n## Related\n{END}"
    body3 = f"Intro.\n\n{block}\n\nMiddle.\n\n{block}\n\nEnd."
    out3 = f(body3)
    _check("db-multi", START not in out3)

    # Dream block at end of file
    body4 = f"Content.\n{START}\n{END}\n"
    out4 = f(body4)
    _check("db-end-file", "Content." in out4 and START not in out4)


_test_strip_dream_block()


# ---------------------------------------------------------------------------
# _strip_codefence
# ---------------------------------------------------------------------------

def _test_strip_codefence():
    f = _mod._strip_codefence

    # Standard JSON code fence
    fenced = "```json\n{\"key\": \"value\"}\n```"
    out = f(fenced)
    _check("cf-json",     '{"key": "value"}' in out)
    _check("cf-tick-gone", "```" not in out)

    # Plain backtick fence (no language specifier)
    fenced2 = "```\nplain content\n```"
    out2 = f(fenced2)
    _check("cf-plain",      "plain content" in out2)
    _check("cf-plain-tick", "```" not in out2)

    # No fence → returned as-is
    raw = '{"key": "value"}'
    _check("cf-no-fence", f(raw) == raw)

    # Trailing whitespace stripped after fence removal
    fenced3 = "```json\n  {\"x\": 1}  \n```"
    out3 = f(fenced3)
    _check("cf-stripped", out3 == '{"x": 1}')

    # Only opening fence present (no closing ```) → still strips header line
    fenced4 = "```json\n{\"partial\": true}"
    out4 = f(fenced4)
    _check("cf-no-close", '{"partial": true}' in out4)


_test_strip_codefence()


# ---------------------------------------------------------------------------
# apply_inline_ref
# ---------------------------------------------------------------------------

def _test_apply_inline_ref():
    f = _mod.apply_inline_ref

    # Basic insertion: quote found, citation appended at end of paragraph
    body = "This is a sentence about Python programming.\n\nSecond paragraph."
    new_body, ok = f(body, "Python programming", "python-notes")
    _check("air-ok",     ok is True)
    _check("air-cite",   "(cf. [[python-notes]])" in new_body)
    _check("air-para2",  "Second paragraph." in new_body)

    # Citation appended BEFORE the paragraph break, not after
    idx_cite = new_body.index("(cf. [[python-notes]])")
    idx_para2 = new_body.index("Second paragraph.")
    _check("air-before-break", idx_cite < idx_para2)

    # Quote not in body → (body, False)
    body2 = "Completely different content."
    new2, ok2 = f(body2, "nonexistent quote here", "target")
    _check("air-missing-ok",   ok2 is False)
    _check("air-missing-body", new2 == body2)

    # Citation already present → idempotent (False, unchanged)
    body3 = "Some content (cf. [[target-stem]]).\n\nNext para."
    new3, ok3 = f(body3, "Some content", "target-stem")
    _check("air-idem-ok",   ok3 is False)
    _check("air-idem-body", new3 == body3)

    # Quote at EOF (no trailing blank line) → appended at end
    body4 = "Single paragraph with a relevant phrase."
    new4, ok4 = f(body4, "relevant phrase", "ref-note")
    _check("air-eof-ok",   ok4 is True)
    _check("air-eof-cite", "(cf. [[ref-note]])" in new4)

    # Citation inserted only once even if quote appears multiple times
    body5 = "alpha beta alpha beta.\n\nSeparate."
    new5, ok5 = f(body5, "alpha beta", "repeated-ref")
    _check("air-dedup-ok",    ok5 is True)
    _check("air-dedup-count", new5.count("(cf. [[repeated-ref]])") == 1)


_test_apply_inline_ref()


# ---------------------------------------------------------------------------
# build_footer
# ---------------------------------------------------------------------------

def _test_build_footer():
    f = _mod.build_footer
    START = _mod.SENTINEL_START
    END = _mod.SENTINEL_END

    # Empty tiered → sentinels + placeholder comment
    out = f({})
    _check("bf-empty-start",   START in out)
    _check("bf-empty-end",     END in out)
    _check("bf-empty-comment", "no related notes" in out)

    # Strongly related tier
    out2 = f({"strongly_related": [("note-a", "shares methodology")]})
    _check("bf-sr-heading",    "## Strongly Related" in out2)
    _check("bf-sr-link",       "[[note-a]]" in out2)
    _check("bf-sr-rationale",  "shares methodology" in out2)

    # Related tier only
    out3 = f({"related": [("note-b", ""), ("note-c", "some reason")]})
    _check("bf-rel-heading", "## Related" in out3)
    _check("bf-rel-both",    "[[note-b]]" in out3 and "[[note-c]]" in out3)

    # Tier order: strongly_related before related before see_also
    out4 = f({
        "strongly_related": [("sr-note", "SR reason")],
        "related":          [("r-note", "R reason")],
        "see_also":         [("sa-note", "SA reason")],
    })
    idx_sr = out4.index("Strongly Related")
    idx_r  = out4.index("## Related")
    idx_sa = out4.index("See also")
    _check("bf-order-sr-r",  idx_sr < idx_r)
    _check("bf-order-r-sa",  idx_r < idx_sa)

    # Items within a tier sorted alphabetically
    out5 = f({"related": [("z-note", ""), ("a-note", "")]})
    idx_a = out5.index("[[a-note]]")
    idx_z = out5.index("[[z-note]]")
    _check("bf-sorted", idx_a < idx_z)

    # Ends with newline
    _check("bf-newline", out4.endswith("\n"))


_test_build_footer()


# ---------------------------------------------------------------------------
# upsert_footer
# ---------------------------------------------------------------------------

def _test_upsert_footer():
    f = _mod.upsert_footer
    START = _mod.SENTINEL_START
    END = _mod.SENTINEL_END
    NEW_BLOCK = f"{START}\n## Related\n\n- [[ref]]\n{END}\n"

    # No existing block → appended with blank line before
    body = "Existing content.\n"
    out = f(body, NEW_BLOCK)
    _check("uf-append",     "Existing content." in out)
    _check("uf-block",      NEW_BLOCK in out)
    _check("uf-blank-line", "\n\n" + NEW_BLOCK in out)

    # Existing dream block replaced
    OLD_BLOCK = f"{START}\n## Old\n{END}\n"
    body2 = f"Content.\n\n{OLD_BLOCK}"
    out2 = f(body2, NEW_BLOCK)
    _check("uf-replace-new",   NEW_BLOCK in out2)
    _check("uf-replace-old",   "## Old" not in out2)
    _check("uf-content-kept",  "Content." in out2)

    # Single dream block (no double insertion)
    _check("uf-single-start", out2.count(START) == 1)

    # Body with trailing whitespace before new block
    body3 = "Text   "
    out3 = f(body3, NEW_BLOCK)
    _check("uf-rstrip",  not out3.startswith("Text   \n\n"))
    _check("uf-rstrip2", "Text\n\n" in out3)


_test_upsert_footer()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"obsidian-dream-helpers: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
