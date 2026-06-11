#!/usr/bin/env python3
"""Tests for src/dm-result.py — pure helper functions.

Covers:
  a) _split_file_markers()  — extracts [file:|send:|attach:] markers from text
  b) _is_fence_open_line()  — detects Markdown fence opener lines
  c) _chunk_for_discord()   — Discord-safe chunking with code-fence preservation

Run: python3 tests/dm-result.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Set SUTANDO_WORKSPACE before import so resolve_workspace() doesn't warn
_tmp_ws = tempfile.mkdtemp(prefix="dm-result-boot-")
os.environ["SUTANDO_WORKSPACE"] = _tmp_ws
sys.path.insert(0, str(REPO / "src"))
spec = importlib.util.spec_from_file_location("dm_result", REPO / "src" / "dm-result.py")
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)
del os.environ["SUTANDO_WORKSPACE"]

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
# (a) _split_file_markers
# ---------------------------------------------------------------------------

def _test_split_file_markers():
    s = _mod._split_file_markers

    # No markers → text unchanged, empty files list
    text, files = s("Hello world")
    _check("sfm-no-marker-text", text == "Hello world")
    _check("sfm-no-marker-files", files == [])

    # Single file: marker
    text, files = s("Here is the result [file: /tmp/sutando-test.png]")
    _check("sfm-file-marker-extracted", "/tmp/sutando-test.png" in files,
           f"got files={files}")
    _check("sfm-file-marker-removed", "[file:" not in text)
    _check("sfm-file-text-trimmed", text == "Here is the result", f"got {text!r}")

    # send: variant
    text, files = s("See [send: /tmp/sutando-report.pdf]")
    _check("sfm-send-variant", "/tmp/sutando-report.pdf" in files)
    _check("sfm-send-removed", "[send:" not in text)

    # attach: variant
    text, files = s("Attaching [attach: /tmp/sutando-image.jpg]")
    _check("sfm-attach-variant", "/tmp/sutando-image.jpg" in files)

    # Multiple markers in order
    text, files = s("[file: /tmp/a.png] some text [file: /tmp/b.png]")
    _check("sfm-multi-count", len(files) == 2, f"got {files}")
    _check("sfm-multi-order", files[0] == "/tmp/a.png" and files[1] == "/tmp/b.png",
           f"got {files}")
    _check("sfm-multi-text", "some text" in text)

    # Tilde path (~/ prefix)
    text, files = s("Result [file: ~/Documents/report.pdf]")
    _check("sfm-tilde-path", "~/Documents/report.pdf" in files)

    # Marker-only body → empty clean text
    text, files = s("[file: /tmp/sutando-x.png]")
    _check("sfm-marker-only-empty-text", text == "", f"got {text!r}")
    _check("sfm-marker-only-file", len(files) == 1)

    # Mixed file: send: attach: in same body
    text, files = s("[file: /tmp/a.png] [send: /tmp/b.pdf] [attach: /tmp/c.jpg]")
    _check("sfm-all-variants-count", len(files) == 3, f"got {files}")


_test_split_file_markers()


# ---------------------------------------------------------------------------
# (b) _is_fence_open_line
# ---------------------------------------------------------------------------

def _test_is_fence_open_line():
    f = _mod._is_fence_open_line

    # Plain text → None
    _check("fence-plain-none",   f("hello world") is None)
    _check("fence-empty-none",   f("") is None)

    # Three backticks → opener
    _check("fence-triple-tick",  f("```") is not None)

    # Three tildes → opener
    _check("fence-triple-tilde", f("~~~") is not None)

    # With language tag
    result = f("```python")
    _check("fence-lang-tag-not-none", result is not None)
    _check("fence-lang-tag-content",  result is not None and "python" in result)

    # Four backticks → opener (still valid)
    _check("fence-four-ticks",   f("````") is not None)

    # Up to 3 spaces of indentation → valid
    _check("fence-1-indent",  f(" ```") is not None)
    _check("fence-2-indent",  f("  ```") is not None)
    _check("fence-3-indent",  f("   ```") is not None)

    # 4 spaces indentation → code block in Markdown, NOT a fence
    _check("fence-4-indent-none", f("    ```") is None)

    # Inline backticks in prose → None
    _check("fence-inline-code-none", f("use `code` here") is None)
    _check("fence-partial-fence-none", f("text ``` more") is None)

    # Tilde with language tag
    result_tilde = f("~~~bash")
    _check("fence-tilde-lang", result_tilde is not None)
    _check("fence-tilde-lang-content", result_tilde is not None and "bash" in result_tilde)


_test_is_fence_open_line()


# ---------------------------------------------------------------------------
# (c) _chunk_for_discord
# ---------------------------------------------------------------------------

def _chunk(text, max_len=1900):
    return list(_mod._chunk_for_discord(text, max_len))


def _test_chunk_for_discord():
    # Empty → no chunks
    _check("chunk-empty", _chunk("") == [])

    # Short text → single chunk, unchanged
    _check("chunk-short", _chunk("hello") == ["hello"])

    # Text fits when max_len > len(line)+1 (line_overhead includes newline separator)
    exact = "x" * 20
    _check("chunk-fits-in-max", _chunk(exact, max_len=21) == [exact])

    # Multi-line within max_len → single chunk
    two_lines = "line1\nline2"
    result = _chunk(two_lines, max_len=50)
    _check("chunk-multiline-fits", len(result) == 1)
    _check("chunk-multiline-content", result[0] == two_lines)

    # Multi-line that splits at newline boundary
    big = "A" * 10 + "\n" + "B" * 10
    result = _chunk(big, max_len=12)
    _check("chunk-split-at-newline", len(result) >= 2, f"got {result}")
    combined = "\n".join(result)
    # All content accounted for
    _check("chunk-split-content-a", "A" * 10 in combined)
    _check("chunk-split-content-b", "B" * 10 in combined)

    # Code fence: no split needed → fence stays intact
    fenced = "```python\ncode here\n```"
    result = _chunk(fenced, max_len=1900)
    _check("chunk-fence-intact", len(result) == 1)
    _check("chunk-fence-single", result[0] == fenced)

    # Code fence: split forces chunk boundary INSIDE fence → reopened in next chunk
    long_code = "```python\n" + "x = 1\n" * 50 + "```"
    result = _chunk(long_code, max_len=100)
    _check("chunk-fence-split-multi", len(result) > 1, f"got {len(result)} chunks")
    # Every chunk that's cut mid-fence must close with the fence token
    # and the next chunk that continues must reopen with the opener
    all_joined = "\n".join(result)
    _check("chunk-fence-split-complete", "x = 1" in all_joined)
    # Each chunk should have balanced fence delimiters
    for i, chunk in enumerate(result):
        count = chunk.count("```")
        _check(f"chunk-fence-balanced-{i}", count % 2 == 0,
               f"chunk {i} has odd backtick-fence count {count}: {chunk[:60]!r}")

    # Tilde fence preserved
    tilde_code = "~~~bash\necho hi\n~~~"
    result = _chunk(tilde_code, max_len=1900)
    _check("chunk-tilde-intact", len(result) == 1)
    _check("chunk-tilde-content", "~~~bash" in result[0])

    # Code fence with language tag preserved across split
    tagged = "```javascript\n" + "const x = 1;\n" * 20 + "```"
    result = _chunk(tagged, max_len=80)
    _check("chunk-lang-tag-split", len(result) > 1)
    # Subsequent chunks should reopen with the same language tag
    if len(result) > 1:
        _check("chunk-lang-tag-reopen", "```javascript" in result[1] or
               any("```javascript" in c for c in result[1:]),
               f"got chunks: {[c[:40] for c in result]}")

    # Very long single line forced to split mid-line
    long_line = "Z" * 200
    result = _chunk(long_line, max_len=50)
    _check("chunk-long-line-split", len(result) >= 4, f"got {len(result)}")
    _check("chunk-long-line-complete", "".join(result) == long_line,
           f"joined={len(''.join(result))} vs orig={len(long_line)}")

    # Unclosed fence — last flush still emits content (closes it)
    unclosed = "```\nsome code"
    result = _chunk(unclosed, max_len=1900)
    _check("chunk-unclosed-fence-not-empty", len(result) > 0)
    _check("chunk-unclosed-fence-has-content", "some code" in result[-1])

    # Text with no newlines, just over max_len
    no_newline = "A" * 100
    result = _chunk(no_newline, max_len=30)
    _check("chunk-no-newline-split", len(result) >= 3, f"got {len(result)}")
    _check("chunk-no-newline-complete", "".join(result) == no_newline,
           f"joined={len(''.join(result))} vs orig={len(no_newline)}")


_test_chunk_for_discord()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"dm-result: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
