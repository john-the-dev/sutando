# Self-upgrade

Safely upgrade this Sutando checkout to the latest upstream code **without
bricking the running core session** — the "success path" distilled from a real
2026-07-20 upgrade that would otherwise hang (and did, the first time).

**Usage**: `/self-upgrade`

## Why this skill exists

A naive "pull + restart" self-upgrade gets **stuck**, because:

1. `src/restart.sh` ends with `exec bash src/startup.sh`.
2. `src/startup.sh` runs **foreground** work — it rebuilds the Swift helpers
   (`ax-read`, `Sutando.app`) and it **foreground-parents the credential-proxy**
   (a `tsx` process that never exits).
3. So running `restart.sh` **inline** from the core session never returns —
   the Bash call hangs forever, the task never gets a result, and from the
   owner's side you've "gone stuck."

The fix is simple once you know it: **hand the restart to a durable tmux
service session** so the core stays responsive and the restart outlives the
task executor, then re-establish the pieces the restart tore down. Ordinary
`nohup … &` is not sufficient under executors that reap children on return.
This skill encodes that exact sequence.

## On activation

### Step 1 — Pull + durable restart handoff (mechanical)

Run the helper. It aborts safely on a dirty tree or a non-fast-forward, pulls
`--ff-only`, and launches `src/restart.sh` in the `sutando-services` tmux
session on the core's configured socket:

```bash
bash skills/self-upgrade/scripts/upgrade.sh          # origin/main
# bash skills/self-upgrade/scripts/upgrade.sh --no-restart   # pull only
```

Exit `0` = upgraded (or already latest); exit `2` = aborted (dirty tree /
not a fast-forward) — surface the reason and stop.

If the diff touched `package*.json` / `tsconfig` / `*.swift` / `requirements`
(the script prints this), a rebuild may be needed — startup.sh handles the
Swift rebuild itself; for npm deps run `npm ci` before relying on the TS
services.

### Step 2 — Re-arm the task watcher (agent-side — the script can't)

`restart.sh` runs `pkill -f "watch-tasks"`, which kills the streaming task
watcher — including the one your session streams via the **Monitor** tool
(it dies with exit ~144; that's expected, not an error). A shell script
cannot re-arm a Monitor-tool watcher, so **you** must:

> Monitor tool → `command: 'bash src/watch-tasks-stream.sh'`, `persistent: true`,
> `description: 'Streaming task watcher'`

Without this, new task files stop reaching your session.

### Step 3 — Verify + report

```bash
python3 src/health-check.py
```

Expect **"All systems operational."** Confirm the core survived (the restart
log contains `sutando-core already running` — `restart.sh` never touches the
core CLI) and that bridges came back on **new PIDs**. `telegram-bridge` /
`slack-bridge` warnings are fine if they were already optional/unconfigured.

Report to the owner: old → new commit, how many commits, whether a rebuild
was needed, and that the core stayed up.

## Guardrails (learned the hard way)

- **Never run `restart.sh` / `startup.sh` inline** from the core session, and
  do not rely on plain `nohup … &`. Inline = stuck; an executor may reap the
  nohup child. Use the helper's durable tmux handoff.
- **Do NOT hand-kill an active `sutando-services` session** to "tidy up."
  It deliberately parks after startup completes so background bridges keep
  their durable parent. The helper marks the session `done` and only replaces
  that completed session when a later upgrade actually needs another restart.
- **Verify a process is actually yours before killing anything.** `pgrep -f
  watch-tasks-stream` also matches *other* installs (e.g. a `/tmp/…` checkout);
  match the full repo path, not a bare pattern.
- **Clean tree first.** The helper aborts on uncommitted changes rather than
  clobber them; commit or stash before upgrading.

## Iteration log

- v0.2.0 — 2026-07-23 — replace plain `nohup` with a durable
  `sutando-services` tmux handoff that outlives task executors.
- v0.1.0 — 2026-07-20 — initial. Distilled from a live self-upgrade (8 commits
  behind → 0) where the naive inline restart hung on startup.sh's foreground
  Swift build + credential-proxy hold. Encodes: `--ff-only` pull, **detached**
  restart, agent re-arms the watcher, don't-hand-kill-startup.sh.
