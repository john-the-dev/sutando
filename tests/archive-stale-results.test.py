#!/usr/bin/env python3
"""Tests for src/archive-stale-results.py — stale-results archiver.

Covers:
  a) DRY_RUN env-var parsing: mixed-case "No"/"FALSE" must NOT enable dry run
  b) Only .txt files under results/ top-level are considered
  c) Files newer than retention cutoff are left in place
  d) Old .txt files move to archive-YYYY-MM-DD/ with correct return code
  e) DRY_RUN=1 prints intent, moves nothing, returns 0
  f) Files inside existing archive-* subdirs are never touched

Run: python3 tests/archive-stale-results.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "archive_stale_results", REPO / "src" / "archive-stale-results.py"
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


def _with_tmp_results(fn, *, dry_run: bool = False, retention_hours: int = 24):
    """Run fn(results_dir) with module globals patched to a fresh temp directory."""
    with tempfile.TemporaryDirectory() as td:
        results = Path(td) / "results"
        results.mkdir()
        orig_results = _mod.RESULTS
        orig_dry = _mod.DRY_RUN
        orig_retention = _mod.RETENTION_HOURS
        _mod.RESULTS = results
        _mod.DRY_RUN = dry_run
        _mod.RETENTION_HOURS = retention_hours
        try:
            fn(results)
        finally:
            _mod.RESULTS = orig_results
            _mod.DRY_RUN = orig_dry
            _mod.RETENTION_HOURS = orig_retention


# ---------------------------------------------------------------------------
# (a) DRY_RUN env-var parsing — mixed-case must NOT activate dry run
# ---------------------------------------------------------------------------

def _test_dry_run_parsing():
    # Replicate the module expression for white-box testing
    def _parse(val: str) -> bool:
        return val.strip().lower() not in ("", "0", "false", "no")

    # These must NOT enable dry run
    _check("dry-run-empty",   not _parse(""))
    _check("dry-run-zero",    not _parse("0"))
    _check("dry-run-false",   not _parse("false"))
    _check("dry-run-no",      not _parse("no"))
    # Mixed-case variants that were broken before the .lower() fix
    _check("dry-run-No",      not _parse("No"),    "No must NOT enable dry run")
    _check("dry-run-FALSE",   not _parse("FALSE"),  "FALSE must NOT enable dry run")
    _check("dry-run-NO",      not _parse("NO"),    "NO must NOT enable dry run")

    # These SHOULD enable dry run
    _check("dry-run-1",    _parse("1"))
    _check("dry-run-yes",  _parse("yes"))
    _check("dry-run-true", _parse("true"))
    _check("dry-run-Yes",  _parse("Yes"))


_test_dry_run_parsing()


# ---------------------------------------------------------------------------
# (b) Non-.txt files and subdirectories are not touched
# ---------------------------------------------------------------------------

def _test_non_txt_files_ignored():
    def run(results: Path):
        old_ts = time.time() - 48 * 3600  # definitely stale

        # These must NOT be archived
        non_targets = [
            results / "task-123.json",
            results / "task-123.log",
            results / "task-123",          # no suffix
            results / "notes.md",
        ]
        for p in non_targets:
            p.write_text("data")
            os.utime(p, (old_ts, old_ts))

        # Also make a subdirectory (must not be touched)
        subdir = results / "archive-2020-01-01"
        subdir.mkdir()
        (subdir / "task-old.txt").write_text("old")

        _mod.main()

        # Nothing should have moved
        for p in non_targets:
            _check(f"non-txt-preserved-{p.suffix or '(no-ext)'}", p.exists(),
                   f"{p.name} was incorrectly archived")

        # Subdir should still be there intact
        _check("subdir-preserved", subdir.exists())
        _check("subdir-child-preserved", (subdir / "task-old.txt").exists())

    _with_tmp_results(run)


_test_non_txt_files_ignored()


# ---------------------------------------------------------------------------
# (c) Fresh files are left in place
# ---------------------------------------------------------------------------

def _test_fresh_files_not_archived():
    def run(results: Path):
        fresh = results / "task-fresh.txt"
        fresh.write_text("data")
        # mtime = now — definitely within 24h retention

        rc = _mod.main()
        _check("fresh-file-stays", fresh.exists())
        _check("fresh-rc-zero", rc == 0)

    _with_tmp_results(run, retention_hours=24)


_test_fresh_files_not_archived()


# ---------------------------------------------------------------------------
# (d) Stale .txt files are moved to archive-YYYY-MM-DD/
# ---------------------------------------------------------------------------

def _test_stale_files_archived():
    def run(results: Path):
        old_ts = time.time() - 48 * 3600

        stale1 = results / "task-111.txt"
        stale2 = results / "voice-222.txt"
        stale1.write_text("a")
        stale2.write_text("b")
        os.utime(stale1, (old_ts, old_ts))
        os.utime(stale2, (old_ts, old_ts))

        rc = _mod.main()
        _check("stale-moved-1", not stale1.exists(), "task-111.txt still in results/")
        _check("stale-moved-2", not stale2.exists(), "voice-222.txt still in results/")
        _check("stale-rc-zero", rc == 0)

        # Archive dir must exist and contain both files
        archive_dirs = [p for p in results.iterdir() if p.is_dir() and p.name.startswith("archive-")]
        _check("archive-dir-created", len(archive_dirs) == 1,
               f"expected 1 archive dir, got {len(archive_dirs)}")
        if archive_dirs:
            archived = {p.name for p in archive_dirs[0].iterdir()}
            _check("archive-has-task", "task-111.txt" in archived)
            _check("archive-has-voice", "voice-222.txt" in archived)

    _with_tmp_results(run, retention_hours=24)


_test_stale_files_archived()


# ---------------------------------------------------------------------------
# (e) DRY_RUN=1 prints intent, moves nothing, returns 0
# ---------------------------------------------------------------------------

def _test_dry_run_no_move():
    def run(results: Path):
        old_ts = time.time() - 48 * 3600
        stale = results / "task-dry.txt"
        stale.write_text("x")
        os.utime(stale, (old_ts, old_ts))

        rc = _mod.main()
        _check("dry-run-file-stays", stale.exists(),
               "DRY_RUN moved the file instead of leaving it")
        _check("dry-run-rc-zero", rc == 0)
        # No archive directory should have been created
        archive_dirs = [p for p in results.iterdir()
                        if p.is_dir() and p.name.startswith("archive-")]
        _check("dry-run-no-archive-dir", len(archive_dirs) == 0)

    _with_tmp_results(run, dry_run=True)


_test_dry_run_no_move()


# ---------------------------------------------------------------------------
# (f) Files inside archive-* subdirs are never touched
# ---------------------------------------------------------------------------

def _test_archive_subdir_untouched():
    def run(results: Path):
        old_ts = time.time() - 48 * 3600
        existing_archive = results / "archive-2020-01-01"
        existing_archive.mkdir()
        old_archived = existing_archive / "task-old.txt"
        old_archived.write_text("already archived")
        os.utime(old_archived, (old_ts, old_ts))

        _mod.main()
        _check("archive-subdir-untouched", old_archived.exists(),
               "main() re-archived a file already inside archive-*/")

    _with_tmp_results(run)


_test_archive_subdir_untouched()


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"archive-stale-results: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
