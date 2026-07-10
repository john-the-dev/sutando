#!/bin/bash
# Install / uninstall / check the launchd-supervised ag2.space gateway-bridge.
#
# Role: keeps src/remote-gateway-bridge.py alive with automatic restart on
# crash/kill (KeepAlive) + RunAtLoad so it comes back after login/reboot. The
# bridge carries ag2.space MOBILE-app messages from the cloud gateway to the
# local core; before this job it was a single unmonitored process that, on
# 2026-07-10, was found dead for 3 days with mobile messages stranded in the
# cloud. Health-check reports it down (PR #2067); this job recovers it.
#
# Usage:
#   bash src/install-gateway-bridge-launchd.sh             # install (idempotent)
#   bash src/install-gateway-bridge-launchd.sh --uninstall # remove (idempotent)
#   bash src/install-gateway-bridge-launchd.sh --status    # print job state
#
# Idempotent: re-running install bootouts the existing job and reloads so a
# git pull that changes the template/wrapper is picked up. The wrapper evicts any
# bare (nohup) bridge instance before exec, so this composes with the legacy
# bare-& launch in startup.sh.

set -e

LABEL="com.sutando.gateway-bridge"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$REPO/src/launchd/$LABEL.plist"
WRAPPER="$REPO/src/launchd/gateway-bridge-wrapper.sh"
BRIDGE="$REPO/src/remote-gateway-bridge.py"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

# Resolve runtime workspace via the shared post-M0 helper (single source at
# src/workspace_resolve.sh). Prefer $REPO/src/, fall back to script-sibling.
__HELPER="$REPO/src/workspace_resolve.sh"
[ -f "$__HELPER" ] || __HELPER="$(cd "$(dirname "$0")" && pwd)/workspace_resolve.sh"
if [ -f "$__HELPER" ]; then
  # shellcheck source=workspace_resolve.sh
  source "$__HELPER"
  resolve_workspace_or_die
else
  echo "${0##*/}: cannot resolve workspace — workspace_resolve.sh not found." >&2
  exit 1
fi
unset __HELPER

resolve_brew_bin() {
    if [ -d /opt/homebrew/bin ]; then
        echo /opt/homebrew/bin
    elif [ -d /usr/local/bin ]; then
        echo /usr/local/bin
    else
        echo /usr/bin
    fi
}

bootout_if_loaded() {
    if launchctl print "$SERVICE" >/dev/null 2>&1; then
        echo "  Existing job found, removing first..."
        launchctl bootout "$SERVICE" 2>/dev/null || true
        for _ in $(seq 1 10); do
            launchctl print "$SERVICE" >/dev/null 2>&1 || break
            sleep 0.3
        done
    fi
}

cmd="${1:-install}"

case "$cmd" in
    install)
        if [ ! -f "$TEMPLATE" ]; then
            echo "ERROR: template not found: $TEMPLATE" >&2
            exit 1
        fi
        if [ ! -f "$WRAPPER" ]; then
            echo "ERROR: wrapper not found: $WRAPPER" >&2
            exit 1
        fi
        if [ ! -f "$BRIDGE" ]; then
            echo "ERROR: gateway bridge not found: $BRIDGE" >&2
            exit 1
        fi
        BREW_BIN="$(resolve_brew_bin)"
        echo "Installing $LABEL"
        echo "  repo:      $REPO"
        echo "  workspace: $WORKSPACE"
        echo "  brew bin:  $BREW_BIN"
        mkdir -p "$HOME/Library/LaunchAgents"
        mkdir -p "$WORKSPACE/logs"
        sed \
            -e "s|__REPO__|$REPO|g" \
            -e "s|__WORKSPACE__|$WORKSPACE|g" \
            -e "s|__BREW_BIN__|$BREW_BIN|g" \
            -e "s|__HOME__|$HOME|g" \
            "$TEMPLATE" > "$DEST"
        bootout_if_loaded
        launchctl bootstrap "$DOMAIN" "$DEST"
        echo "  Loaded via $SERVICE"
        echo
        echo "gateway-bridge is now launchd-supervised (RunAtLoad, KeepAlive, ThrottleInterval=10s)."
        echo "  • Crash/kill auto-restarts within ~10s"
        echo "  • Survives login/reboot via RunAtLoad"
        echo "  • View status:  bash $0 --status"
        echo "  • Uninstall:    bash $0 --uninstall"
        echo "  • Logs:         $WORKSPACE/logs/remote-gateway-bridge.log"
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
        echo "  Note: this also terminated the supervised bridge. startup.sh will relaunch it (bare or re-installed) on next run."
        ;;
    --status|status)
        echo "Service: $SERVICE"
        if launchctl print "$SERVICE" >/dev/null 2>&1; then
            launchctl print "$SERVICE" | grep -E '^\s+(state|pid|last exit code|runs|path)' || true
        else
            echo "  (not loaded)"
        fi
        ;;
    *)
        echo "Usage: $0 [install|--uninstall|--status]" >&2
        exit 2
        ;;
esac
