# v3 workspace-paths fix verification — snapshot

Captured by `bash scripts/verify-v3-workspace-fixes.sh` against branches
`fix/sutando-migrate-cli-and-tier-a-sentinel`, `fix/check-pending-tasks-workspace-paths`,
`fix/widen-state-paths-adoption-test`, `fix/env-lookup-workspace-vs-repo`,
`fix/notify-and-session-handoff-workspace-paths`, `fix/scripts-workspace-paths-batch`,
`fix/sync-memory-workspace-paths`, `fix/deal-finder-workspace-paths`,
`fix/self-diagnose-workspace-paths`, `fix/marketing-skills-workspace-paths`,
`fix/voice-skills-workspace-paths`.

Re-run on any host:

```bash
bash scripts/verify-v3-workspace-fixes.sh           # all 11
bash scripts/verify-v3-workspace-fixes.sh 1278 1281 # subset by PR#
```

Exit 0 = all checks pass; exit 1 = at least one fails.

---

## Run output (2026-05-28 UTC)

```
## v3 workspace-paths fix verification — 2026-05-28T05:16:51Z

Repo:      /Users/xueqingliu/Documents/sutando/sutando
Workspace: /Users/xueqingliu/.sutando/workspace
PRs:       1271 1272 1273 1274 1275 1276 1277 1278 1279 1280 1281

### #1271 sutando-migrate.sh
  ✅ 1271 dry-run banner observed (read-only by default)
  ✅ 1271 --help documents --commit

### #1272 check-pending-tasks.sh
  ✅ 1272 WORKSPACE default fallback installed
  ✅ 1272 TASKS_DIR rebased on workspace
  ✅ 1272 RESULTS_DIR rebased on workspace

### #1273 state-paths-adoption test scope
  ✅ 1273 SCRIPTS dir scope added
  ✅ 1273 shell-regex check added
  ✅ 1273 git-tracked filter present

### #1274 env-lookup (discord-bridge / telegram-bridge / dm-result)
  ✅ 1274 src/discord-bridge.py has _SRC_TREE
  ✅ 1274 src/discord-bridge.py loads .env from _SRC_TREE
  ✅ 1274 src/telegram-bridge.py has _SRC_TREE
  ✅ 1274 src/telegram-bridge.py loads .env from _SRC_TREE
  ✅ 1274 src/dm-result.py has _SRC_TREE
  ✅ 1274 src/dm-result.py loads .env from _SRC_TREE
  ✅ 1274 .env exists at src-tree root (2670 bytes)

### #1275 notify.sh + session-handoff.sh
  ✅ 1275 src/notify.sh resolves SUTANDO_WORKSPACE with default
  ✅ 1275 src/session-handoff.sh resolves SUTANDO_WORKSPACE with default
  ✅ 1275 session-handoff lists workspace tasks

### #1276 5-script workspace batch
  ✅ 1276 scripts/presenter-mode.sh WORKSPACE default present
  ✅ 1276 scripts/results-health.sh WORKSPACE default present
  ✅ 1276 scripts/stage-readiness.sh WORKSPACE default present
  ✅ 1276 scripts/tail-events.sh WORKSPACE default present
  ✅ 1276 scripts/query-conversation.sh WORKSPACE default present

### #1277 sync-memory.sh
  ✅ 1277 site: NOTES_DIR="$WORKSPACE/notes"
  ✅ 1277 site: CONFLICT_DIR="$WORKSPACE/notes
  ✅ 1277 site: PRESENTER_SENTINEL="$WORKSPACE/state
  ✅ 1277 site: $WORKSPACE/data
  ✅ 1277 site: mkdir -p "$WORKSPACE/state"

### #1278 deal-finder/scan.py
  ✅ 1278 _WORKSPACE = resolve_workspace()
  ✅ 1278 RESULTS_DIR rebased on workspace

### #1279 self-diagnose gather scripts
  ✅ 1279 WS default present
  ✅ 1279 site uses $WS/build_log.md
  ✅ 1279 site uses $WS/pending-questions.md
  ✅ 1279 site uses $WS/logs/voice-agent.log
  ✅ 1279 site uses $WS/logs/discord-bridge.log
  ✅ 1279 site uses $WS/results

### #1280 marketing skills (TTS + viral)
  ✅ 1280 skills/gemini-tts/scripts/synthesize.sh WORKSPACE default
  ✅ 1280 skills/openai-tts/scripts/synthesize.sh WORKSPACE default
  ✅ 1280 skills/make-viral-video/scripts/build.sh WORKSPACE default
  ✅ 1280 asset_cache.py _WORKSPACE resolved
  ✅ 1280 asset_cache.py CACHE_DIR rebased

### #1281 voice paths (callsFile / gws cache / phone scan-script INVERSE)
  ✅ 1281 voice-context callsFile uses WORKSPACE_DIR
  ✅ 1281 gws-gmail CACHE_PATH uses resolveWorkspace()
  ✅ 1281 phone scanScript INVERSE uses REPO_DIR (code path)
  ✅ 1281 scan-call-logs.py exists at repo path (467 lines)

─────────────────────────────────────────────────────────────
  Summary: 45 passed, 0 failed
─────────────────────────────────────────────────────────────
```
