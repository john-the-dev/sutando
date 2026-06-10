#!/usr/bin/env python3
"""Functional tests for send_allowlist.is_path_sendable().

The architectural test (send-allowlist-shared.test.py) verifies the module
structure. This file tests the actual policy enforcement:

  a) Non-existent path → False (fail-closed)
  b) Directory (not a regular file) → False
  c) /tmp/sutando-* prefix → True (temp artifacts allowed)
  d) /tmp/echo-* prefix → True (echo skill artifacts)
  e) /private/tmp/sutando-* prefix → True (macOS realpath form)
  f) File outside any allowed root or prefix → False
  g) Path traversal via ../  (realpath collapses it) → False when dest is outside
  h) Symlink pointing outside the allowlist → False (realpath breaks out)
  i) Symlink pointing inside the allowlist → True (legitimate artifact link)
  j) Workspace results/ root → True (the primary result delivery path)

Run: python3 tests/send-allowlist-is-path-sendable.test.py
Exit: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "send_allowlist",
    REPO / "src" / "send_allowlist.py",
)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

ips = _mod.is_path_sendable

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
# (a) Non-existent path → False
# ---------------------------------------------------------------------------

_check("nonexistent-false",   not ips("/nonexistent/path/file.txt"))
_check("empty-string-false",  not ips(""))


# ---------------------------------------------------------------------------
# (b) Directory → False (os.path.isfile returns False for dirs)
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory(prefix="sutando-") as tmpdir:
    # The dir itself is under sutando- prefix, but isfile(dir) = False
    _check("directory-false", not ips(tmpdir),
           f"directory {tmpdir!r} should not be sendable")


# ---------------------------------------------------------------------------
# (c) /tmp/sutando-* prefix → True
# ---------------------------------------------------------------------------

with tempfile.NamedTemporaryFile(prefix="sutando-", dir="/tmp", delete=False, suffix=".txt") as f:
    f.write(b"test content")
    tmp_sutando = f.name

try:
    _check("tmp-sutando-prefix-true", ips(tmp_sutando),
           f"file {tmp_sutando!r} under /tmp/sutando- should be sendable")
finally:
    os.unlink(tmp_sutando)


# ---------------------------------------------------------------------------
# (d) /tmp/echo-* prefix → True
# ---------------------------------------------------------------------------

with tempfile.NamedTemporaryFile(prefix="echo-", dir="/tmp", delete=False, suffix=".png") as f:
    f.write(b"\x89PNG\r\n\x1a\n")  # PNG magic bytes
    tmp_echo = f.name

try:
    _check("tmp-echo-prefix-true", ips(tmp_echo),
           f"file {tmp_echo!r} under /tmp/echo- should be sendable")
finally:
    os.unlink(tmp_echo)


# ---------------------------------------------------------------------------
# (e) /private/tmp/sutando-* → True (macOS realpath resolves /tmp → /private/tmp)
# ---------------------------------------------------------------------------
# On macOS, /tmp is a symlink to /private/tmp. os.path.realpath("/tmp/sutando-x")
# returns "/private/tmp/sutando-x". The SEND_ALLOWED_PREFIXES list includes both
# "/tmp/sutando-" and "/private/tmp/sutando-" to cover this.

with tempfile.NamedTemporaryFile(prefix="sutando-", dir="/tmp", delete=False, suffix=".mov") as f:
    f.write(b"fake video")
    tmp_macos = f.name

try:
    real_path = os.path.realpath(tmp_macos)
    # Verify the realpath form (either form) is covered
    _check("macos-realpath-covered",
           real_path.startswith("/tmp/sutando-") or real_path.startswith("/private/tmp/sutando-"),
           f"realpath {real_path!r} not in expected prefix set")
    _check("macos-sutando-true", ips(tmp_macos),
           f"macOS realpath form {real_path!r} should be sendable")
finally:
    os.unlink(tmp_macos)


# ---------------------------------------------------------------------------
# (f) File outside any allowed root or prefix → False
# ---------------------------------------------------------------------------

with tempfile.NamedTemporaryFile(prefix="not-sutando-", dir="/tmp", delete=False) as f:
    f.write(b"not allowed")
    tmp_denied = f.name

try:
    _check("outside-allowlist-false", not ips(tmp_denied),
           f"file {tmp_denied!r} is outside allowlist, should be False")
finally:
    os.unlink(tmp_denied)


# ---------------------------------------------------------------------------
# (g) Path traversal via ../ → False when destination is outside allowlist
# ---------------------------------------------------------------------------

# Create a real file outside the allowlist, then reference it via a path that
# traverses through an allowed prefix directory using "../".
with tempfile.NamedTemporaryFile(prefix="secret-", dir="/tmp", delete=False) as f:
    f.write(b"sensitive data")
    secret_file = f.name
    secret_name = os.path.basename(secret_file)

try:
    # Construct a traversal path: /tmp/sutando-../secret-XXXX
    traversal = f"/tmp/sutando-fake/../{secret_name}"
    # realpath resolves this to /tmp/secret-XXXX (outside the prefix)
    resolved = os.path.realpath(traversal)
    if os.path.exists(resolved):
        # If the traversal resolves to an existing file, it should be denied
        _check("traversal-denied", not ips(traversal),
               f"traversal path {traversal!r} → {resolved!r} should be denied")
    else:
        # Traversal path doesn't resolve to existing file → isfile=False → False
        _check("traversal-nonexistent-false", not ips(traversal))
finally:
    os.unlink(secret_file)


# ---------------------------------------------------------------------------
# (h) Symlink pointing outside the allowlist → False
# ---------------------------------------------------------------------------

with tempfile.NamedTemporaryFile(prefix="real-secret-", dir="/tmp", delete=False) as f:
    f.write(b"secret")
    secret = f.name

symlink_path = f"/tmp/sutando-symlink-test-{os.getpid()}.txt"
try:
    # Symlink name looks like it's in the allowlist (sutando- prefix) but
    # realpath resolves to the target outside the allowlist.
    if os.path.exists(symlink_path):
        os.unlink(symlink_path)
    os.symlink(secret, symlink_path)
    real_of_link = os.path.realpath(symlink_path)
    # The symlink itself starts with /tmp/sutando- but realpath is /tmp/real-secret-*
    _check("symlink-outside-false", not ips(symlink_path),
           f"symlink {symlink_path!r} → {real_of_link!r} is outside allowlist")
finally:
    if os.path.islink(symlink_path):
        os.unlink(symlink_path)
    os.unlink(secret)


# ---------------------------------------------------------------------------
# (i) Symlink pointing inside the allowlist → True
# ---------------------------------------------------------------------------

with tempfile.NamedTemporaryFile(prefix="sutando-real-", dir="/tmp", delete=False) as f:
    f.write(b"allowed content")
    inner_file = f.name

inner_symlink = f"/tmp/sutando-inner-link-{os.getpid()}.txt"
try:
    if os.path.exists(inner_symlink):
        os.unlink(inner_symlink)
    os.symlink(inner_file, inner_symlink)
    # Both the link name and target are under /tmp/sutando- prefix
    _check("symlink-inside-true", ips(inner_symlink),
           f"symlink {inner_symlink!r} → {inner_file!r} both under allowlist")
finally:
    if os.path.islink(inner_symlink):
        os.unlink(inner_symlink)
    os.unlink(inner_file)


# ---------------------------------------------------------------------------
# (j) Workspace results/ root → True
# ---------------------------------------------------------------------------

from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(REPO / "src"))
from workspace_default import resolve_workspace  # noqa: E402

results_dir = resolve_workspace() / "results"
results_dir.mkdir(parents=True, exist_ok=True)

with tempfile.NamedTemporaryFile(dir=str(results_dir), prefix="test-result-",
                                  suffix=".txt", delete=False) as f:
    f.write(b"test result content")
    result_file = f.name

try:
    _check("results-dir-true", ips(result_file),
           f"file in results/ dir {result_file!r} should be sendable")
finally:
    os.unlink(result_file)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

total = _passed + _failed
print(f"send-allowlist-is-path-sendable: {_passed}/{total} passed"
      f"{'' if _failed == 0 else f' — {_failed} FAILED'}")
sys.exit(0 if _failed == 0 else 1)
