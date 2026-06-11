#!/usr/bin/env python3
"""Regression guard: asset_cache.py in skills/make-viral-video/scripts/.

Tests the shared URL→file asset cache that lets viral-video runs share
previously fetched images instead of re-downloading on every run.

Functions under test:
  _basename_from_url(url)              — pure: strip query/fragment, sanitize
  cache_get(url)                       — returns cached Path or None
  cache_put(url, local_path)           — copy file into cache, update index
  preload_run_from_cache(run_dir, manifest_path) — copy cache hits into run
  promote_run_to_cache(run_dir)        — copy run fetched_assets into cache

All IO-touching tests redirect CACHE_DIR and INDEX_FILE to a temp directory.

Run: python3 tests/make-viral-video-asset-cache.test.py
Exit: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPTS = REPO / "skills" / "make-viral-video" / "scripts"

spec = importlib.util.spec_from_file_location(
    "asset_cache", SCRIPTS / "asset_cache.py"
)
_mod = importlib.util.module_from_spec(spec)
sys.modules["asset_cache"] = _mod
spec.loader.exec_module(_mod)

# Redirect cache to a temp dir for every test
_tmp_root = tempfile.mkdtemp(prefix="asset-cache-test-")
_CACHE_DIR = Path(_tmp_root) / "fetched_assets"
_INDEX_FILE = Path(_tmp_root) / "index.json"
_mod.CACHE_DIR = _CACHE_DIR
_mod.INDEX_FILE = _INDEX_FILE

_passed = 0
_failed = 0


def _check(label: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
    else:
        _failed += 1
        print(f"FAIL [{label}]{': ' + detail if detail else ''}", file=sys.stderr)


def _reset_cache():
    """Clear CACHE_DIR and INDEX_FILE between tests."""
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR)
    if _INDEX_FILE.exists():
        _INDEX_FILE.unlink()


def _write_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# ---------------------------------------------------------------------------
# _basename_from_url
# ---------------------------------------------------------------------------

def _test_basename():
    f = _mod._basename_from_url

    # Simple URL → last path component
    _check("bn-simple",     f("http://example.com/foo/bar.jpg") == "bar.jpg")

    # Query string stripped
    _check("bn-query",      f("http://cdn.example.com/img.png?v=42") == "img.png")

    # Fragment stripped
    _check("bn-fragment",   f("http://example.com/pic.jpg#section") == "pic.jpg")

    # Both query and fragment
    _check("bn-both",       f("http://example.com/file.mp4?s=1#t=30") == "file.mp4")

    # URL ending in slash → trailing slash stripped, basename of parent component used
    _check("bn-slash",      f("http://example.com/path/") == "path")

    # URL with no path → domain used as basename
    _check("bn-nopath",     f("http://example.com") == "example.com")

    # Bare slash → "asset" fallback (only empty-name case)
    _check("bn-bare-slash", f("/") == "asset")

    # Special chars sanitized to underscores
    result = f("http://example.com/file name (1).jpg")
    _check("bn-sanitize",   " " not in result and "(" not in result, f"got {result!r}")

    # Dots and dashes preserved
    _check("bn-dots-dashes",f("http://example.com/apollo-17.final.jpg") == "apollo-17.final.jpg")

    # Digits preserved
    _check("bn-digits",     f("http://cdn.nasa.gov/2024/img01.png") == "img01.png")


_test_basename()


# ---------------------------------------------------------------------------
# cache_get — miss / hit / file deleted
# ---------------------------------------------------------------------------

def _test_cache_get():
    _reset_cache()

    # Cache empty → None
    _check("cg-empty",  _mod.cache_get("http://example.com/a.jpg") is None)

    # Put a file and get it back
    src = Path(tempfile.mktemp(suffix=".jpg"))
    src.write_text("image-bytes")
    _mod.cache_put("http://example.com/a.jpg", src)
    result = _mod.cache_get("http://example.com/a.jpg")
    _check("cg-hit",        result is not None)
    _check("cg-hit-exists", result is not None and result.is_file())
    _check("cg-hit-name",   result is not None and result.name == "a.jpg")

    # Different URL → miss
    _check("cg-miss-other", _mod.cache_get("http://example.com/other.jpg") is None)

    # Index entry points to deleted file → None
    if _INDEX_FILE.exists():
        idx = json.loads(_INDEX_FILE.read_text())
        for url in idx:
            cached_path = _CACHE_DIR / idx[url]["local_file"]
            if cached_path.exists():
                cached_path.unlink()
        break_url = list(idx.keys())[0] if idx else None
        if break_url:
            _check("cg-file-deleted", _mod.cache_get(break_url) is None)

    src.unlink(missing_ok=True)


_test_cache_get()


# ---------------------------------------------------------------------------
# cache_put
# ---------------------------------------------------------------------------

def _test_cache_put():
    _reset_cache()

    src = Path(tempfile.mktemp(suffix=".png"))
    src.write_text("png-data")

    # Put a new file
    _mod.cache_put("http://example.com/photo.png", src)
    cached = _CACHE_DIR / "photo.png"
    _check("cp-file-exists",  cached.is_file())
    _check("cp-content",      cached.read_text() == "png-data")

    # Index is updated
    idx = json.loads(_INDEX_FILE.read_text())
    _check("cp-index-entry",  "http://example.com/photo.png" in idx)
    entry = idx["http://example.com/photo.png"]
    _check("cp-index-name",   entry["local_file"] == "photo.png")
    _check("cp-index-size",   entry["size_bytes"] == len("png-data"))

    # Put same URL again with new content — file is overwritten
    src.write_text("new-data")
    _mod.cache_put("http://example.com/photo.png", src)
    _check("cp-overwrite",    (CACHE_DIR := _CACHE_DIR / "photo.png").read_text() == "new-data")

    # Non-existent source file → no-op (doesn't raise)
    missing = Path("/nonexistent/missing.jpg")
    _mod.cache_put("http://example.com/missing.jpg", missing)
    _check("cp-missing-noop", "http://example.com/missing.jpg" not in
           json.loads(_INDEX_FILE.read_text()))

    src.unlink(missing_ok=True)


_test_cache_put()


# ---------------------------------------------------------------------------
# preload_run_from_cache
# ---------------------------------------------------------------------------

def _test_preload():
    _reset_cache()

    # Populate cache with two files
    tmp = Path(tempfile.mkdtemp(prefix="src-"))
    img_a = _write_file(tmp / "a.jpg", "img-a")
    img_b = _write_file(tmp / "b.jpg", "img-b")
    _mod.cache_put("http://example.com/a.jpg", img_a)
    _mod.cache_put("http://example.com/b.jpg", img_b)

    # Build a run dir with a manifest referencing both + one uncached URL
    run = Path(tempfile.mkdtemp(prefix="run-"))
    manifest = [
        {"url": "http://example.com/a.jpg", "local_file": "image_a.jpg"},
        {"url": "http://example.com/b.jpg", "local_file": "image_b.jpg"},
        {"url": "http://example.com/c.jpg", "local_file": "image_c.jpg"},  # miss
    ]
    (run / "artifacts").mkdir()
    (run / "artifacts" / "asset_manifest.json").write_text(json.dumps(manifest))

    hits = _mod.preload_run_from_cache(run)
    _check("pr-hits",       hits == 2, f"expected 2, got {hits}")
    _check("pr-file-a",     (run / "fetched_assets" / "image_a.jpg").is_file())
    _check("pr-file-b",     (run / "fetched_assets" / "image_b.jpg").is_file())
    _check("pr-no-c",       not (run / "fetched_assets" / "image_c.jpg").exists())

    # Missing manifest → 0 hits, no error
    run2 = Path(tempfile.mkdtemp(prefix="run2-"))
    _check("pr-no-manifest",_mod.preload_run_from_cache(run2) == 0)

    shutil.rmtree(tmp)
    shutil.rmtree(run)
    shutil.rmtree(run2)


_test_preload()


# ---------------------------------------------------------------------------
# promote_run_to_cache
# ---------------------------------------------------------------------------

def _test_promote():
    _reset_cache()

    run = Path(tempfile.mkdtemp(prefix="prom-"))
    fetched = run / "fetched_assets"
    fetched.mkdir(parents=True)

    # Two fetched assets, one with manifest URL, one without
    _write_file(fetched / "logo.png", "logo-bytes")
    _write_file(fetched / "hero.jpg", "hero-bytes")
    # data-card files are excluded
    _write_file(fetched / "data-card-01.png", "chart")

    manifest = [{"url": "http://nasa.gov/logo.png", "local_file": "logo.png"}]
    (run / "artifacts").mkdir()
    (run / "artifacts" / "asset_manifest.json").write_text(json.dumps(manifest))

    promoted = _mod.promote_run_to_cache(run)
    _check("prom-count",    promoted == 2, f"expected 2, got {promoted}")  # logo + hero
    _check("prom-logo",     _mod.cache_get("http://nasa.gov/logo.png") is not None)
    _check("prom-hero-key", _mod.cache_get("local://hero.jpg") is not None)
    _check("prom-no-chart", _mod.cache_get("local://data-card-01.png") is None)

    shutil.rmtree(run)


_test_promote()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(
    f"make-viral-video-asset-cache: {_passed}/{total} passed"
    f"{'' if _failed == 0 else f' — {_failed} FAILED'}"
)
sys.exit(0 if _failed == 0 else 1)
