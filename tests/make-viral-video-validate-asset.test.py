#!/usr/bin/env python3
"""Regression guard: md5_of_file() and validate() in
skills/make-viral-video/scripts/validate_asset.py.

md5_of_file(path):
  Returns the hex MD5 digest of a file read in 8 KiB chunks.

validate(path, allowed_domains, known_404_hashes, source_url):
  Returns a dict {valid, reason?, hash, mime, dims?}.
  Guards checked in order:
    1. file_not_found
    2. non_image_content_type (from `file --mime-type`)
    3. matches_known_404_hash (prefix match against known_404_hashes)
    4. sub_minimum_resolution (w < 600 or h < 400)
    5. off_whitelist_domain (source_url not under allowed_domains)

detect_image_dims and ocr_text are monkey-patched for IO-free testing
of validate()'s logic gates beyond the filesystem check.

Run: python3 tests/make-viral-video-validate-asset.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "make-viral-video" / "scripts"

spec = importlib.util.spec_from_file_location(
    "validate_asset", SCRIPTS / "validate_asset.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["validate_asset"] = _mod
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


def _write_tmp(content: bytes = b"test data") -> Path:
    fd, p = tempfile.mkstemp(suffix=".bin")
    import os
    os.close(fd)
    Path(p).write_bytes(content)
    return Path(p)


# ---------------------------------------------------------------------------
# md5_of_file
# ---------------------------------------------------------------------------

def _test_md5_of_file():
    f = _mod.md5_of_file

    # Known content → known MD5
    content = b"hello world\n"
    expected = hashlib.md5(content).hexdigest()
    p = _write_tmp(content)
    _check("m5-known",     f(p) == expected, f"got {f(p)!r}, expected {expected!r}")
    p.unlink()

    # Empty file → known empty MD5
    empty_md5 = hashlib.md5(b"").hexdigest()
    p2 = _write_tmp(b"")
    _check("m5-empty",     f(p2) == empty_md5)
    p2.unlink()

    # Different content → different MD5
    p3 = _write_tmp(b"content A")
    p4 = _write_tmp(b"content B")
    _check("m5-different", f(p3) != f(p4))
    p3.unlink()
    p4.unlink()

    # Large file (forces multiple 8 KiB chunks)
    large = b"x" * 100_000
    expected_large = hashlib.md5(large).hexdigest()
    p5 = _write_tmp(large)
    _check("m5-large",     f(p5) == expected_large)
    p5.unlink()


_test_md5_of_file()


# ---------------------------------------------------------------------------
# validate — monkey-patch detect_image_dims + ocr_text for logic-gate tests
# ---------------------------------------------------------------------------

def _fake_dims_image(path):
    """Pretend file is a 1280×720 JPEG — passes content-type + resolution checks."""
    return (1280, 720), "image/jpeg"


def _fake_dims_text(path):
    """Pretend file is text/plain — triggers non_image_content_type."""
    return None, "text/plain"


def _fake_dims_small(path):
    """Pretend file is a tiny 100×100 PNG — triggers sub_minimum_resolution."""
    return (100, 100), "image/png"


def _fake_ocr_empty(path):
    return ""


def _test_validate():
    f = _mod.validate

    # 1. File does not exist
    r_missing = f(Path("/nonexistent/ghost.jpg"), [], [])
    _check("va-missing",   r_missing.get("valid") is False)
    _check("va-missing-r", r_missing.get("reason") == "file_not_found")

    # 2. Non-image content type (mocked)
    tmp = _write_tmp(b"not an image")
    _mod.detect_image_dims = _fake_dims_text
    _mod.ocr_text = _fake_ocr_empty
    r_text = f(tmp, [], [])
    _check("va-nonimage",   r_text.get("valid") is False)
    _check("va-nonimage-r", r_text.get("reason") == "non_image_content_type")
    tmp.unlink()

    # 3. Known 404 hash match
    content = b"fake 404 page screenshot bytes"
    md5 = hashlib.md5(content).hexdigest()
    tmp3 = _write_tmp(content)
    _mod.detect_image_dims = _fake_dims_image
    r_hash = f(tmp3, [], [md5[:6]])
    _check("va-404hash",   r_hash.get("valid") is False)
    _check("va-404hash-r", "matches_known_404_hash" in r_hash.get("reason", ""))
    _check("va-hash-val",  r_hash.get("hash") == md5)
    tmp3.unlink()

    # 4. Sub-minimum resolution
    tmp4 = _write_tmp(b"small image bytes")
    _mod.detect_image_dims = _fake_dims_small
    r_small = f(tmp4, [], [])
    _check("va-small",   r_small.get("valid") is False)
    _check("va-small-r", "sub_minimum_resolution" in r_small.get("reason", ""))
    tmp4.unlink()

    # 5. Off-whitelist domain
    tmp5 = _write_tmp(b"image bytes")
    _mod.detect_image_dims = _fake_dims_image
    r_domain = f(tmp5, ["nasa.gov"], [], source_url="https://evil.com/image.jpg")
    _check("va-domain",   r_domain.get("valid") is False)
    _check("va-domain-r", "off_whitelist_domain" in r_domain.get("reason", ""))
    tmp5.unlink()

    # 6. Valid image, no hash match, good domain → valid
    tmp6 = _write_tmp(b"real image bytes that pass all checks")
    _mod.detect_image_dims = _fake_dims_image
    r_valid = f(tmp6, ["nasa.gov"], [], source_url="https://nasa.gov/img.jpg")
    _check("va-valid",   r_valid.get("valid") is True)
    _check("va-valid-hash", len(r_valid.get("hash", "")) == 32)
    _check("va-valid-dims", r_valid.get("dims") == [1280, 720])
    tmp6.unlink()

    # 7. Subdomain matches whitelist entry
    tmp7 = _write_tmp(b"subdomain image")
    _mod.detect_image_dims = _fake_dims_image
    r_sub = f(tmp7, ["nasa.gov"], [], source_url="https://images.nasa.gov/img.jpg")
    _check("va-subdomain", r_sub.get("valid") is True)
    tmp7.unlink()

    # 8. Empty allowed_domains with source_url → domain check skipped → valid
    tmp8 = _write_tmp(b"any image")
    _mod.detect_image_dims = _fake_dims_image
    r_nodom = f(tmp8, [], [], source_url="https://anything.com/img.jpg")
    _check("va-nodom-skip", r_nodom.get("valid") is True)
    tmp8.unlink()


_test_validate()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"make-viral-video-validate-asset: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
