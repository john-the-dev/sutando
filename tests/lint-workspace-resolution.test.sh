#!/usr/bin/env bash
# Self-test for scripts/lint-workspace-resolution.sh — one offender per
# PATTERN_ENV access form (the #1824 review found the JS dot form and the
# Python subscript form passed clean), plus hardcoded-home and a clean file.
# Builds a throwaway git repo so `git ls-files` / `git diff` see exactly the
# fixture files. Standalone shell test; exits non-zero on first failure.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
LINT="$REPO/scripts/lint-workspace-resolution.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

# --- fixture repo -------------------------------------------------------------
cd "$TMPDIR"
git init -q
git config user.email "lint-test@example.invalid"
git config user.name "lint-test"
mkdir -p scripts src
cp "$LINT" scripts/lint-workspace-resolution.sh
printf 'clean base\n' > src/clean-base.js
git add -A
git commit -qm "base"
base_sha="$(git rev-parse HEAD)"

# One offender per access form. Filenames double as assertion labels.
printf 'const w = process.env.SUTANDO_WORKSPACE;\n'        > src/js-dot.js
printf "const w = process.env['SUTANDO_WORKSPACE'];\n"     > src/js-bracket.js
printf 'w = os.environ["SUTANDO_WORKSPACE"]\n'             > src/py-subscript.py
printf 'w = os.environ.get("SUTANDO_WORKSPACE")\n'         > src/py-get.py
printf 'w = os.getenv("SUTANDO_WORKSPACE")\n'              > src/py-getenv.py
printf 'const p = home + "/.sutando/workspace/state";\n'   > src/hardcoded-home.mjs
printf 'const w = resolveWorkspace();\n'                   > src/clean-loader.js
git add -A
git commit -qm "offenders"

offenders="src/js-dot.js src/js-bracket.js src/py-subscript.py src/py-get.py src/py-getenv.py src/hardcoded-home.mjs"

# --- whole-tree mode: informational (exit 0) but must list every offender ----
out="$(bash scripts/lint-workspace-resolution.sh)" || fail "whole-tree mode should exit 0 (informational), got $?"
for f in $offenders; do
  case "$out" in
    *"$f"*) : ;;
    *) fail "whole-tree: expected offender '$f' in output" ;;
  esac
done
case "$out" in
  *"src/clean-loader.js"*) fail "whole-tree: clean file was flagged" ;;
  *) : ;;
esac
ok "whole-tree mode flags all $(echo $offenders | wc -w | tr -d ' ') offender forms, not the clean file"

# --- --diff mode: CI-enforcing (exit 1) and must list every offender ----------
set +e
diff_out="$(BASE_REF="$base_sha" bash scripts/lint-workspace-resolution.sh --diff 2>&1)"
rc=$?
set -e
[ "$rc" -eq 1 ] || fail "--diff: expected exit 1 with offenders, got $rc"
for f in $offenders; do
  case "$diff_out" in
    *"$f"*) : ;;
    *) fail "--diff: expected offender '$f' in output" ;;
  esac
done
case "$diff_out" in
  *"src/clean-loader.js"*) fail "--diff: clean file was flagged" ;;
  *) : ;;
esac
ok "--diff mode exits 1 and flags all offender forms, not the clean file"

# --- --diff mode with no offenders → exit 0 ------------------------------------
git rm -q $offenders
git commit -qm "remove offenders"
set +e
BASE_REF="$base_sha" bash scripts/lint-workspace-resolution.sh --diff >/dev/null 2>&1
rc=$?
set -e
[ "$rc" -eq 0 ] || fail "--diff clean: expected exit 0, got $rc"
ok "--diff mode exits 0 when the diff is clean"

echo
echo "OK — 3/3 lint-workspace-resolution self-tests passed"
