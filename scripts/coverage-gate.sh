#!/usr/bin/env bash
# Unit-test coverage gate — enforce >= 95% coverage on CHANGED Python lines.
#
# What it does:
#   1. Runs every standalone Python test (tests/**/*.test.py — same discovery
#      as ci.yml) under coverage.py instrumentation.
#   2. Prints the whole-tree coverage report (informational — the visibility
#      that lets us ratchet the global floor up over time).
#   3. Gates: `diff-cover` compares coverage.xml against the base branch and
#      FAILS if less than 95% of the added/changed executable Python lines
#      are exercised by the suite.
#
# Why diff coverage and not a global floor: the tree's historical global
# coverage is far below 95% (large untested legacy surface: bridges, GUI
# automation, installers). A global 95% floor would hard-red every PR on
# day one and block all merges. The diff gate applies the full 95% bar to
# every line of NEW code, which converges the tree toward the target
# without freezing development. Flip GLOBAL_FLOOR on once the tree gets
# there (see docs/testing-coverage.md).
#
# Usage:
#   bash scripts/coverage-gate.sh                 # gate vs origin/main
#   BASE_REF=<ref> bash scripts/coverage-gate.sh  # gate vs another base
#   COVERAGE_GATE_FAIL_UNDER=90 bash scripts/coverage-gate.sh  # override bar
#
# Deps (dev-only, mirrors detect-secrets handling in ci.yml):
#   python3 -m pip install coverage diff-cover
# Locally on PEP-668 (Homebrew) pythons, use a venv and put it on PATH.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

BASE="${BASE_REF:-origin/main}"
FAIL_UNDER="${COVERAGE_GATE_FAIL_UNDER:-95}"

if ! python3 -m coverage --version >/dev/null 2>&1; then
    echo "coverage-gate: coverage.py not importable by python3." >&2
    echo "  Install dev deps: python3 -m pip install coverage diff-cover" >&2
    exit 2
fi
if ! command -v diff-cover >/dev/null 2>&1; then
    echo "coverage-gate: diff-cover not on PATH." >&2
    echo "  Install dev deps: python3 -m pip install coverage diff-cover" >&2
    exit 2
fi

# Fast path: PRs that touch no Python have no diff to gate. (diff-cover
# would report 100% anyway; skipping saves the full instrumented suite run.)
if ! git diff --name-only "$BASE"...HEAD -- '*.py' | grep -q .; then
    echo "coverage-gate: no Python changes vs $BASE — nothing to gate. ✓"
    exit 0
fi

echo "coverage-gate: running Python suite under instrumentation..."
rm -f .coverage .coverage.* coverage.xml

failed=0
while IFS= read -r f; do
    if ! output=$(python3 -m coverage run --rcfile=.coveragerc "$f" 2>&1); then
        echo "✖ test failed under instrumentation: $f"
        echo "$output"
        failed=1
    fi
done < <(find tests -name '*.test.py' -not -path '*/node_modules/*' | sort)
if [ "$failed" -ne 0 ]; then
    echo "coverage-gate: suite must be green before coverage is meaningful." >&2
    exit 1
fi

python3 -m coverage combine --quiet
python3 -m coverage xml --quiet

echo
echo "── whole-tree coverage (informational) ─────────────────────────"
python3 -m coverage report --skip-covered --sort=cover | tail -15
total="$(python3 -m coverage report --format=total 2>/dev/null || echo '?')"
echo "TOTAL: ${total}%"
echo "─────────────────────────────────────────────────────────────────"
echo

echo "coverage-gate: enforcing ${FAIL_UNDER}% on lines changed vs $BASE"
diff-cover coverage.xml --compare-branch="$BASE" --fail-under="$FAIL_UNDER"
