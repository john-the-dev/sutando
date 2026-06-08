#!/usr/bin/env python3
"""
Tests for in-band access_tier enforcement in src/worker-agent.ts (PR #1544 / issue #1501).

CodexWorker.execute() applies a three-tier sandbox-level resolution so callers
don't need to thread the access_tier through the call site:
  1. Explicit caller override via options.sandboxLevel (highest priority)
  2. access_tier header in task file: owner → workspace-write, team/other → read-only
  3. Fail-safe default: read-only when access_tier is absent (unknown = non-owner)

Cases:
  a) ExecutionOptions interface declares sandboxLevel field
  b) sandboxLevel type is 'read-only' | 'workspace-write' union
  c) CodexWorker parses access_tier from task content (reads task file header)
  d) 'read-only' is the fail-safe default when access_tier is absent
  e) owner access_tier maps to 'workspace-write'
  f) Caller-explicit sandboxLevel overrides task-file tier
  g) 'read-only' literal appears (used for non-owner tiers)

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
    has_type = (
        ("'read-only' | 'workspace-write'" in src or '"read-only" | "workspace-write"' in src)
        or
        ("'workspace-write' | 'read-only'" in src or '"workspace-write" | "read-only"' in src)
    )
    if not has_type:
        fails.append("b) sandboxLevel type union not found — expected 'read-only' | 'workspace-write'")
    return fails


def case_c_parses_access_tier_from_task():
    """CodexWorker must parse access_tier header from the task file content."""
    fails = []
    src = _source()
    codex_class_start = src.find("class CodexWorker")
    if codex_class_start == -1:
        fails.append("c) CodexWorker class not found")
        return fails
    codex_section = src[codex_class_start:]
    # Must match access_tier: from the task content
    has_tier_parse = (
        "access_tier" in codex_section
        and re.search(r"taskContent\.match\(.*access_tier", codex_section)
    )
    if not has_tier_parse:
        fails.append("c) CodexWorker does not parse access_tier from task file content — non-owner tasks would escalate to owner sandbox")
    return fails


def case_d_fail_safe_default_is_read_only():
    """'read-only' must be the fail-safe default when access_tier is absent."""
    fails = []
    src = _source()
    codex_class_start = src.find("class CodexWorker")
    if codex_class_start == -1:
        fails.append("d) CodexWorker class not found")
        return fails
    codex_section = src[codex_class_start:]
    # The else-branch (tier not 'owner') must map to 'read-only'
    # Accept patterns like: sandbox = tier === 'owner' ? 'workspace-write' : 'read-only'
    has_fail_safe = re.search(r"'owner'.*'workspace-write'.*'read-only'", codex_section) or \
                    re.search(r"'read-only'.*\).*fail.safe", codex_section, re.IGNORECASE)
    if not has_fail_safe:
        fails.append("d) fail-safe 'read-only' default not found — tasks with no access_tier header could get workspace-write")
    return fails


def case_e_owner_maps_to_workspace_write():
    """access_tier 'owner' must map to 'workspace-write'."""
    fails = []
    src = _source()
    codex_class_start = src.find("class CodexWorker")
    if codex_class_start == -1:
        fails.append("e) CodexWorker class not found")
        return fails
    codex_section = src[codex_class_start:]
    has_owner_map = re.search(r"'owner'.*'workspace-write'", codex_section) or \
                    re.search(r"tier\s*===\s*['\"]owner['\"].*workspace-write", codex_section)
    if not has_owner_map:
        fails.append("e) owner tier → 'workspace-write' mapping not found in CodexWorker")
    return fails


def case_f_caller_override_takes_precedence():
    """Explicit caller sandboxLevel must guard the task-file tier parse (if/else structure)."""
    fails = []
    src = _source()
    codex_class_start = src.find("class CodexWorker")
    if codex_class_start == -1:
        fails.append("f) CodexWorker class not found")
        return fails
    codex_section = src[codex_class_start:]
    # The structure must be: if options.sandboxLevel !== undefined → use it
    #                         else → parse access_tier from task file
    # Verify the if-guard and else-branch coexist in the right order.
    has_guard = re.search(
        r"options\??\.sandboxLevel\s*!==\s*undefined",
        codex_section,
    )
    if not has_guard:
        fails.append("f) 'options?.sandboxLevel !== undefined' guard not found — caller override path missing")
    # Also confirm that access_tier parse is inside an else block (not standalone)
    has_else_parse = re.search(
        r"else\s*\{[^}]*access_tier[^}]*\}",
        codex_section,
        re.DOTALL,
    )
    if not has_else_parse:
        fails.append("f) access_tier parse not found inside an else block — override and auto-detect are not mutually exclusive")
    return fails


def case_g_read_only_literal_present():
    """'read-only' must appear as a valid literal (used for non-owner tiers)."""
    fails = []
    src = _source()
    if "'read-only'" not in src and '"read-only"' not in src:
        fails.append("g) 'read-only' literal not found — non-owner tier can't produce this level")
    return fails


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main() -> int:
    cases = [
        ("a", case_a_interface_has_sandbox_level),
        ("b", case_b_sandbox_level_type),
        ("c", case_c_parses_access_tier_from_task),
        ("d", case_d_fail_safe_default_is_read_only),
        ("e", case_e_owner_maps_to_workspace_write),
        ("f", case_f_caller_override_takes_precedence),
        ("g", case_g_read_only_literal_present),
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
