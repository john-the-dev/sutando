#!/usr/bin/env bash
# verify-v3-workspace-fixes.sh
#
# Replays empirical verification for each PR in the v3 workspace-paths cleanup
# batch (#1271-#1281). Branch-agnostic — reads each PR's branch via `git show`
# rather than checking out, so it can run from any branch without disturbing
# the working tree.
#
# Output: per-PR PASS/FAIL line + the salient evidence under each.
#
# Usage:
#   bash scripts/verify-v3-workspace-fixes.sh           # all 11
#   bash scripts/verify-v3-workspace-fixes.sh 1278 1281 # subset
#
# Exit code: 0 if all checks pass, 1 if any fail.

set -u
# Intentionally NOT using `pipefail`: many checks do `echo "$src" | grep -qF
# "pat"`, and `grep -q` exits early on first match, closing the pipe and
# sending SIGPIPE upstream. With pipefail on, the pipeline reports failure
# even though grep succeeded — false-negative across most checks. The
# downstream consequences (PASS/FAIL counters) are based on the grep exit
# alone, not the pipeline, so leaving pipefail off is safe here.

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"

PASS=0
FAIL=0
declare -a FAILED

pass() { echo "  ✅ $*"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $*"; FAIL=$((FAIL+1)); FAILED+=("$1"); }
section() { echo ""; echo "### $*"; }

# show-from-branch: print a file from a branch without checkout.
sb() { git show "$1:$2" 2>/dev/null; }

# Each verify_<n> writes its findings; counts ratchet PASS/FAIL.

verify_1271() {
	section "#1271 sutando-migrate.sh"
	local br="fix/sutando-migrate-cli-and-tier-a-sentinel"
	if ! sb "$br" "scripts/sutando-migrate.sh" > /tmp/v3-migrate.sh; then
		fail "1271 source missing on $br"; return; fi
	chmod +x /tmp/v3-migrate.sh
	# Dry-run is default; expect MODE: DRY-RUN banner somewhere in early output.
	if bash /tmp/v3-migrate.sh 2>&1 | head -30 | grep -q "MODE:.*DRY-RUN"; then
		pass "1271 dry-run banner observed (read-only by default)"
	else
		fail "1271 dry-run banner missing"; fi
	# --help text mentions --commit
	if bash /tmp/v3-migrate.sh --help 2>&1 | grep -q -- "--commit"; then
		pass "1271 --help documents --commit"
	else
		fail "1271 --help broken"; fi
	rm -f /tmp/v3-migrate.sh
}

verify_1272() {
	section "#1272 check-pending-tasks.sh"
	local br="fix/check-pending-tasks-workspace-paths"
	local src; src=$(sb "$br" "src/check-pending-tasks.sh")
	if echo "$src" | grep -qE "WORKSPACE=.*SUTANDO_WORKSPACE.*HOME/.sutando/workspace"; then
		pass "1272 WORKSPACE default fallback installed"
	else fail "1272 missing WORKSPACE default"; fi
	if echo "$src" | grep -qE 'TASKS_DIR="\$WORKSPACE/tasks"'; then
		pass "1272 TASKS_DIR rebased on workspace"
	else fail "1272 TASKS_DIR not under WORKSPACE"; fi
	if echo "$src" | grep -qE 'RESULTS_DIR="\$WORKSPACE/results"'; then
		pass "1272 RESULTS_DIR rebased on workspace"
	else fail "1272 RESULTS_DIR not under WORKSPACE"; fi
}

verify_1273() {
	section "#1273 state-paths-adoption test scope"
	local br="fix/widen-state-paths-adoption-test"
	local src; src=$(sb "$br" "tests/state-paths-adoption.test.py")
	if echo "$src" | grep -qE "^SCRIPTS\s*="; then
		pass "1273 SCRIPTS dir scope added"
	else fail "1273 SCRIPTS scope missing"; fi
	if echo "$src" | grep -qE "SH_CANONICAL"; then
		pass "1273 shell-regex check added"
	else fail "1273 shell-regex check missing"; fi
	if echo "$src" | grep -qE "_git_tracked"; then
		pass "1273 git-tracked filter present"
	else fail "1273 git-tracked filter missing"; fi
}

verify_1274() {
	section "#1274 env-lookup (discord-bridge / telegram-bridge / dm-result)"
	local br="fix/env-lookup-workspace-vs-repo"
	for f in src/discord-bridge.py src/telegram-bridge.py src/dm-result.py; do
		local src; src=$(sb "$br" "$f")
		# Looser pattern (avoids brittle regex on parens / escapes)
		if echo "$src" | grep -qF "_SRC_TREE = Path(__file__)"; then
			pass "1274 $f has _SRC_TREE"
		else fail "1274 $f missing _SRC_TREE"; continue; fi
		# Either env_file = _SRC_TREE / ".env"  OR  load_dotenv(_SRC_TREE / ".env")
		if echo "$src" | grep -qE '_SRC_TREE\s*/\s*"\.env"'; then
			pass "1274 $f loads .env from _SRC_TREE"
		else fail "1274 $f .env not from _SRC_TREE"; fi
	done
	# Empirical: confirm .env actually resolvable from src-tree
	if [ -f "$REPO_DIR/.env" ]; then
		pass "1274 .env exists at src-tree root ($(wc -c < "$REPO_DIR/.env" | tr -d ' ') bytes)"
	else fail "1274 .env not at src-tree root"; fi
}

verify_1275() {
	section "#1275 notify.sh + session-handoff.sh"
	local br="fix/notify-and-session-handoff-workspace-paths"
	for f in src/notify.sh src/session-handoff.sh; do
		local src; src=$(sb "$br" "$f")
		# Accept WORKSPACE / WORKSPACE_DIR / __WS — each script picked its own var name
		if echo "$src" | grep -qE '(WORKSPACE(_DIR)?|__WS)="?\$\{SUTANDO_WORKSPACE'; then
			pass "1275 $f resolves SUTANDO_WORKSPACE with default"
		else fail "1275 $f missing SUTANDO_WORKSPACE resolution"; fi
	done
	# End-to-end: run fixed session-handoff against a synthetic transcript and
	# assert the Tasks section lists workspace paths, not repo paths.
	if ! sb "$br" "src/session-handoff.sh" > /tmp/v3-handoff.sh; then
		fail "1275 cannot extract session-handoff.sh"; return; fi
	chmod +x /tmp/v3-handoff.sh
	echo "# fake" > /tmp/v3-fake-transcript.md
	bash /tmp/v3-handoff.sh /tmp/v3-fake-transcript.md > /dev/null 2>&1 || true
	# session-handoff.sh writes to $REPO/session-state.md per its existing design;
	# only the *tasks listing* was changed to workspace.
	if grep -qE "/\.sutando/workspace/tasks/" "$REPO_DIR/session-state.md" 2>/dev/null; then
		pass "1275 session-handoff lists workspace tasks"
	else fail "1275 session-handoff still using repo tasks (or none present)"; fi
	rm -f /tmp/v3-handoff.sh /tmp/v3-fake-transcript.md
}

verify_1276() {
	section "#1276 5-script workspace batch"
	local br="fix/scripts-workspace-paths-batch"
	local files=(
		"scripts/presenter-mode.sh"
		"scripts/results-health.sh"
		"scripts/stage-readiness.sh"
		"scripts/tail-events.sh"
		"scripts/query-conversation.sh"
	)
	for f in "${files[@]}"; do
		local src; src=$(sb "$br" "$f")
		if echo "$src" | grep -qE 'WORKSPACE=.*SUTANDO_WORKSPACE.*HOME/.sutando/workspace'; then
			pass "1276 $f WORKSPACE default present"
		else fail "1276 $f missing WORKSPACE default"; fi
	done
}

verify_1277() {
	section "#1277 sync-memory.sh"
	local br="fix/sync-memory-workspace-paths"
	local src; src=$(sb "$br" "scripts/sync-memory.sh")
	# Use fgrep to avoid regex/argument confusion with strings containing `-d`
	for pat in \
		'NOTES_DIR="$WORKSPACE/notes"' \
		'CONFLICT_DIR="$WORKSPACE/notes' \
		'PRESENTER_SENTINEL="$WORKSPACE/state' \
		'$WORKSPACE/data' \
		'mkdir -p "$WORKSPACE/state"' \
	; do
		if echo "$src" | grep -qF -- "$pat"; then
			pass "1277 site: $pat"
		else fail "1277 missing: $pat"; fi
	done
}

verify_1278() {
	section "#1278 deal-finder/scan.py"
	local br="fix/deal-finder-workspace-paths"
	local src; src=$(sb "$br" "skills/deal-finder/scripts/scan.py")
	if echo "$src" | grep -qE "^_WORKSPACE\s*=\s*resolve_workspace\(\)"; then
		pass "1278 _WORKSPACE = resolve_workspace()"
	else fail "1278 _WORKSPACE not set via resolve_workspace"; fi
	if echo "$src" | grep -qE "^RESULTS_DIR\s*=\s*_WORKSPACE\s*/\s*\"results\""; then
		pass "1278 RESULTS_DIR rebased on workspace"
	else fail "1278 RESULTS_DIR not on workspace"; fi
}

verify_1279() {
	section "#1279 self-diagnose gather scripts"
	local br="fix/self-diagnose-workspace-paths"
	local src; src=$(sb "$br" "skills/self-diagnose/scripts/gather.sh")
	if echo "$src" | grep -qE 'WS=.*SUTANDO_WORKSPACE.*HOME/.sutando/workspace'; then
		pass "1279 WS default present"
	else fail "1279 WS default missing"; fi
	for site in "WS/build_log.md" "WS/pending-questions.md" "WS/logs/voice-agent.log" "WS/logs/discord-bridge.log" "WS/results"; do
		if echo "$src" | grep -qF "\$$site"; then
			pass "1279 site uses \$$site"
		else fail "1279 site missing: $site"; fi
	done
}

verify_1280() {
	section "#1280 marketing skills (TTS + viral)"
	local br="fix/marketing-skills-workspace-paths"
	for f in skills/gemini-tts/scripts/synthesize.sh skills/openai-tts/scripts/synthesize.sh skills/make-viral-video/scripts/build.sh; do
		local src; src=$(sb "$br" "$f")
		if echo "$src" | grep -qE 'WORKSPACE=.*SUTANDO_WORKSPACE'; then
			pass "1280 $f WORKSPACE default"
		else fail "1280 $f missing"; fi
	done
	local py; py=$(sb "$br" "skills/make-viral-video/scripts/asset_cache.py")
	if echo "$py" | grep -qE '_WORKSPACE\s*=\s*Path\(.*SUTANDO_WORKSPACE'; then
		pass "1280 asset_cache.py _WORKSPACE resolved"
	else fail "1280 asset_cache.py _WORKSPACE missing"; fi
	if echo "$py" | grep -qE 'CACHE_DIR\s*=\s*_WORKSPACE\s*/\s*"state"\s*/\s*"viral-cache"'; then
		pass "1280 asset_cache.py CACHE_DIR rebased"
	else fail "1280 asset_cache.py CACHE_DIR not on workspace"; fi
}

verify_1281() {
	section "#1281 voice paths (callsFile / gws cache / phone scan-script INVERSE)"
	local br="fix/voice-skills-workspace-paths"
	local v; v=$(sb "$br" "src/voice-context.ts")
	if echo "$v" | grep -qE "callsFile\s*=\s*join\(WORKSPACE_DIR,\s*'results',\s*'calls'"; then
		pass "1281 voice-context callsFile uses WORKSPACE_DIR"
	else fail "1281 voice-context callsFile wrong"; fi
	local g; g=$(sb "$br" "skills/gws-gmail-voice/tools.ts")
	if echo "$g" | grep -qE "CACHE_PATH\s*=\s*join\(resolveWorkspace\(\)"; then
		pass "1281 gws-gmail CACHE_PATH uses resolveWorkspace()"
	else fail "1281 gws-gmail CACHE_PATH wrong"; fi
	local c; c=$(sb "$br" "skills/phone-conversation/scripts/conversation-server.ts")
	if echo "$c" | grep -qF "scanScript = join(REPO_DIR, 'src', 'scan-call-logs.py')"; then
		pass "1281 phone scanScript INVERSE uses REPO_DIR (code path)"
	else fail "1281 phone scanScript not using REPO_DIR"; fi
	# Empirical: confirm scan-call-logs.py actually exists at repo path (was
	# the silent-fail bug).
	if [ -f "$REPO_DIR/src/scan-call-logs.py" ]; then
		pass "1281 scan-call-logs.py exists at repo path ($(wc -l < "$REPO_DIR/src/scan-call-logs.py" | tr -d ' ') lines)"
	else fail "1281 scan-call-logs.py missing from repo"; fi
}

# Pick which PRs to verify
WANT=("$@")
if [ ${#WANT[@]} -eq 0 ]; then
	WANT=(1271 1272 1273 1274 1275 1276 1277 1278 1279 1280 1281)
fi

echo "## v3 workspace-paths fix verification — $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo ""
echo "Repo:      $REPO_DIR"
echo "Workspace: $WORKSPACE"
echo "PRs:       ${WANT[*]}"

for pr in "${WANT[@]}"; do
	if declare -F "verify_$pr" > /dev/null; then
		"verify_$pr"
	else
		fail "verify_$pr undefined"
	fi
done

echo ""
echo "─────────────────────────────────────────────────────────────"
echo "  Summary: $PASS passed, $FAIL failed"
echo "─────────────────────────────────────────────────────────────"
if [ "$FAIL" -gt 0 ]; then
	echo "  Failed PRs: ${FAILED[*]}"
	exit 1
fi
exit 0
