#!/bin/bash
# Evict a pre-existing BARE channel bridge that belongs to THIS checkout, before
# the launchd wrapper starts its own supervised child.
#
# CR #2068: the wrapper used `pkill -f "src/<channel>-bridge.py$"`, which matches
# the same bridge launched from ANY Sutando checkout on the host — so starting or
# upgrading one installation could kill another installation's live bridge.
# startup.sh launches slack/discord/telegram with a RELATIVE path
# (`python3 src/<channel>-bridge.py`, cwd = repo), so the command line alone is
# identical across checkouts; the only reliable discriminator is the process's
# resolved identity. This validates PID ownership: kill a candidate only if its
# command path is under THIS repo, OR its working directory IS this repo.
#
# Usage (sourced): `. evict-own-bridge.sh; evict_own_bridge <channel> <repo>`
# Usage (script, for tests): `evict-own-bridge.sh <channel> <repo>`

# Resolve a pid's working directory, cross-platform: /proc on Linux (CI), lsof on
# macOS (production). Empty string if it can't be determined.
_pid_cwd() {
  pid="$1"
  if [ -r "/proc/$pid/cwd" ]; then
    readlink "/proc/$pid/cwd" 2>/dev/null || true
  else
    lsof -a -d cwd -p "$pid" -Fn 2>/dev/null | sed -n 's/^n//p' | head -1
  fi
}

evict_own_bridge() {
  channel="$1"
  repo="$2"
  rel="src/$channel-bridge.py"
  # Candidates: any process whose command line ends with `src/<channel>-bridge.py`
  # (matches both relative and absolute launches).
  for pid in $(pgrep -f "src/$channel-bridge\.py\$" 2>/dev/null); do
    [ "$pid" = "$$" ] && continue
    cmd="$(ps -o command= -p "$pid" 2>/dev/null || true)"
    case "$cmd" in
      *"$repo/$rel"*)
        # Absolute path under this checkout — unambiguously ours.
        kill "$pid" 2>/dev/null || true
        continue
        ;;
    esac
    # Relative launch: ours only if the process's cwd is this checkout. Compare
    # PHYSICAL paths so a symlinked checkout or macOS's /tmp -> /private/tmp
    # (lsof reports the resolved path) doesn't cause a false mismatch.
    cwd="$(_pid_cwd "$pid")"
    [ -n "$cwd" ] || continue
    cwd_p="$(cd "$cwd" 2>/dev/null && pwd -P || echo "$cwd")"
    repo_p="$(cd "$repo" 2>/dev/null && pwd -P || echo "$repo")"
    if [ "$cwd_p" = "$repo_p" ]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
}

# Run directly when invoked as a script (tests / manual), not when sourced.
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  evict_own_bridge "${1:-}" "${2:-}"
fi
