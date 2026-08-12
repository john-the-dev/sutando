#!/bin/bash
# Install / uninstall the dead-man's-switch ping launchd job.
#
# Mirrors src/install-health-check-launchd.sh conventions (bootstrap via
# `launchctl bootstrap gui/$UID`, idempotent re-install, --status verb) and
# carries the #1897 lesson forward: a post-install self-test kickstarts one
# tick and verifies the job actually produced a log line — catching the
# silent-EPERM TCC failure mode (launchd bash blocked from the workspace
# under ~/Documents) at install time instead of days later.
#
# Strictly opt-in: not called by startup.sh.
#
# Usage:
#   bash skills/dead-mans-switch/install.sh             # install (idempotent)
#   bash skills/dead-mans-switch/install.sh --uninstall # remove (idempotent)
#   bash skills/dead-mans-switch/install.sh --status    # print job state

set -e

LABEL="com.sutando.deadman-ping"
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$SKILL_DIR/../.." && pwd)"
TEMPLATE="$SKILL_DIR/launchd/$LABEL.plist"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

WORKSPACE="$(bash "$REPO/scripts/sutando-config.sh" workspace)"
LOG="$WORKSPACE/logs/deadman-ping.log"

bootout_if_loaded() {
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
        echo "  Existing job found, removing first..."
        launchctl bootout "$SERVICE" 2>/dev/null || true
        # bootout is async — wait for the service to actually disappear so
        # the subsequent bootstrap doesn't race.
        for _ in $(seq 1 10); do
            launchctl print "$SERVICE" >/dev/null 2>&1 || break
            sleep 0.3
        done
    fi
}

resolve_homebrew_bin() {
    if [ -d /opt/homebrew/bin ]; then
        echo /opt/homebrew/bin
    elif [ -d /usr/local/bin ]; then
        echo /usr/local/bin
    else
        echo /usr/bin
    fi
}

cmd="${1:-install}"

case "$cmd" in
    install)
        if [ ! -f "$TEMPLATE" ]; then
            echo "ERROR: template not found: $TEMPLATE" >&2
            exit 1
        fi
        BREW_BIN="$(resolve_homebrew_bin)"
        echo "Installing $LABEL"
        echo "  repo:      $REPO"
        echo "  workspace: $WORKSPACE"
        mkdir -p "$HOME/Library/LaunchAgents"
        mkdir -p "$WORKSPACE/logs"
        # Not sed: `&` in a replacement means "the matched text", `|` is the
        # delimiter, and plist values need XML escaping. A path with any of
        # those silently produced a corrupt ProgramArguments and exit 0.
        python3 - "$TEMPLATE" "$DEST" "$REPO" "$WORKSPACE" "$BREW_BIN" <<'PY'
import sys
from xml.sax.saxutils import escape

template, dest, repo, workspace, brew_bin = sys.argv[1:6]
text = open(template, encoding="utf-8").read()
for token, value in (("__REPO__", repo),
                     ("__WORKSPACE__", workspace),
                     ("__HOMEBREW_BIN__", brew_bin)):
    text = text.replace(token, escape(value))
open(dest, "w", encoding="utf-8").write(text)
PY
        bootout_if_loaded
        launchctl bootstrap "$DOMAIN" "$DEST"
        echo "  Loaded via $SERVICE"

        # Post-install self-test (#1897 pattern): force one tick, verify the
        # job ran. RunAtLoad already fired once at bootstrap; kickstart makes
        # the check deterministic. The TCC failure mode is a HUNG or silent
        # run — no log growth — so log mtime is the probe.
        before="$(date +%s)"
        launchctl kickstart "$SERVICE" 2>/dev/null || true
        ok=""
        for _ in $(seq 1 10); do
            sleep 1
            if [ -f "$LOG" ]; then
                mt="$(python3 -c 'import os,sys; print(int(os.path.getmtime(sys.argv[1])))' "$LOG" 2>/dev/null || echo 0)"
                if [ "$mt" -ge "$before" ]; then ok=1; break; fi
            fi
            # A configured-and-healthy run writes nothing to the log (quiet
            # success) — treat a clean exit as pass: launchd records the last
            # exit status once the run finishes.
            state="$(launchctl print "$SERVICE" 2>/dev/null | grep -E 'last exit code' | head -1)"
            case "$state" in *"= 0"*) ok=1; break ;; esac
        done
        if [ -n "$ok" ]; then
            echo "  Self-test: job ran cleanly."
        else
            echo "  ⚠ Self-test: no run evidence within 10s. If this persists, grant"
            echo "    /bin/bash Full Disk Access (System Settings → Privacy & Security)"
            echo "    or check $LOG — same TCC gate as the health-check fallback (#1897)."
        fi
        echo
        if [ -n "${HEALTHCHECKS_PING_URL:-}" ] || python3 "$REPO/skills/secret-vault/secret-vault.py" get HEALTHCHECKS_PING_URL >/dev/null 2>&1; then
            echo "Dead-man's switch is ARMED — pinging every 5 min."
        else
            echo "Job installed but UNARMED — no ping URL configured yet."
            echo "  Arm it: send \`vault set HEALTHCHECKS_PING_URL <url>\` via Slack/Discord."
        fi
        echo "  • Status:     bash $0 --status"
        echo "  • Uninstall:  bash $0 --uninstall"
        ;;
    --uninstall|uninstall)
        echo "Uninstalling $LABEL"
        bootout_if_loaded
        if [ -f "$DEST" ]; then
            rm "$DEST"
            echo "  Removed $DEST"
        else
            echo "  (no plist on disk; nothing to remove)"
        fi
        echo "Done."
        ;;
    --status|status)
        if launchctl print "$SERVICE" 2>/dev/null | grep -E 'state|last exit code|runs'; then
            echo "  log: $LOG"
        else
            echo "$LABEL is not loaded."
        fi
        ;;
    *)
        echo "Usage: $0 [install|--uninstall|--status]" >&2
        exit 2
        ;;
esac
