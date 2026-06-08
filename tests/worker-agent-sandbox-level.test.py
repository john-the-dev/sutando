#!/usr/bin/env python3
"""
Tests for sandboxLevel support in src/worker-agent.ts (PR #1544 / issue #1501).

The ExecutionOptions interface must expose a sandboxLevel field so callers
(e.g. task-bridge) can pass 'read-only' for non-owner access tiers instead of
the default 'workspace-write'. Without this, all Codex tasks run with
workspace-write access regardless of access_tier — violating the CLAUDE.md
access-control model that requires 'codex exec --sandbox read-only' for
team / other tiers.

Cases:
  a) ExecutionOptions interface declares sandboxLevel field
  b) sandboxLevel type is 'read-only' | 'workspace-write' union
  c) CodexWorker passes sandboxLevel to '-s' arg (not hardcoded 'workspace-write')
  d) 'workspace-write' is the default when sandboxLevel is omitted
  e) 'read-only' literal appears as a valid level (for non-owner tier use)

Run: python3 tests/worker-agent-sandbox-level.test.py
Exit code: 0 on pass, 1 on fail.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "worker-agent.ts"


def _source() -> str:
    return SRC.read_text(encoding="utf-8")


def case_a_interface_has_sandbox_level():
    """ExecutionOptions interface declares sandboxLevel."""
    fails = []
    src = _source()
    if "sandboxLevel" not in src:
        fails.append("a) 'sandboxLevel' not found in worker-agent.ts — field not declared")
    if "ExecutionOptions" not in src:
        fails.append("a) ExecutionOptions interface not found in worker-agent.ts")
    return fails


def case_b_sandbox_level_type():
    """sandboxLevel type must be the union 'read-only' | 'workspace-write'."""
    fails = []
    src = _source()
    # Accept either order of the union
    has_type = (
        ("'read-only' | 'workspace-write'" in src or '"read-only" | "workspace-write"' in src)
        or
        ("'workspace-write' | 'read-only'" in src or '"workspace-write" | "read-only"' in src)
    )
    if not has_type:
        fails.append("b) sandboxLevel type union not found — expected 'read-only' | 'workspace-write'")
    return fails


def case_c_codex_worker_uses_sandbox_level():
    """CodexWorker must use the sandboxLevel option, not hardcode 'workspace-write'."""
    fails = []
    src = _source()
    # The '-s' arg must reference the options variable, not a literal string
    codex_class_start = src.find("class CodexWorker")
    if codex_class_start == -1:
        fails.append("c) CodexWorker class not found")
        return fails
    codex_section = src[codex_class_start:]
    # Accept either 'sandboxLevel' or 'sandbox' variable name in the args array
    uses_var = re.search(r"'-s',\s*(sandbox\w*|options\??\.\w*sandbox\w*)", codex_section, re.IGNORECASE)
    has_hardcode = re.search(r"'-s',\s*'workspace-write'", codex_section)
    if has_hardcode:
        fails.append("c) CodexWorker still hardcodes '-s', 'workspace-write' — sandboxLevel option not used")
    if not uses_var and not has_hardcode:
        # Also accept the form where sandbox is computed separately then passed
        if "sandbox" not in codex_section.lower():
            fails.append("c) no sandbox variable found in CodexWorker — options.sandboxLevel not threaded through")
    return fails


def case_d_workspace_write_is_default():
    """'workspace-write' must be the default sandbox level (used when option is absent)."""
    fails = []
    src = _source()
    # The null-coalescing default must be 'workspace-write'
    has_default = (
        "?? 'workspace-write'" in src
        or '?? "workspace-write"' in src
        or "sandboxLevel ?? 'workspace-write'" in src.replace('\n', ' ')
    )
    if not has_default:
        fails.append("d) 'workspace-write' default not found — non-owner tasks could escalate to owner-tier access")
    return fails


def case_e_read_only_literal_present():
    """'read-only' must appear as a valid literal (used by non-owner tiers)."""
    fails = []
    src = _source()
    if "'read-only'" not in src and '"read-only"' not in src:
        fails.append("e) 'read-only' literal not found — non-owner tier can't pass this level to execute()")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_interface_has_sandbox_level),
        ("b", case_b_sandbox_level_type),
        ("c", case_c_codex_worker_uses_sandbox_level),
        ("d", case_d_workspace_write_is_default),
        ("e", case_e_read_only_literal_present),
    ]
    all_failures = []
    for label, fn in cases:
        try:
            fails = fn()
        except Exception as e:
            fails = [f"{label}) raised {type(e).__name__}: {e}"]
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
