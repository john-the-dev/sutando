#!/usr/bin/env bash
#
# sutando-migrate.sh — Move legacy per-user state from <repo>/ to $SUTANDO_WORKSPACE/.
#
# Closes the #1149-class bug for existing users: code post-#1170 expects all state
# under WORKSPACE, but accumulated repo state from before that change still lives in
# <repo>/{notes,state,results,tasks,logs,data}/, plus a few loose files. This CLI
# is what `src/workspace_default.py:467` has been telling users to run.
#
# Safety (#1149 lesson):
#  - Dry-run by default. Use --commit to actually move.
#  - Per-file atomic: copy → SHA-256 verify → delete. Interrupt-safe.
#  - rsync --update preserves newer side; older replaced version backed up as
#    `.pre-migrate-bak` so nothing is silently lost.
#  - No shutil.move into symlinks (the original #1149 footgun).
#  - Idempotent: re-running --commit after partial progress just resumes.
#
# Usage:
#   bash scripts/sutando-migrate.sh                    # dry-run
#   bash scripts/sutando-migrate.sh --commit           # actually do it
#   bash scripts/sutando-migrate.sh --commit --resume  # skip already-migrated
#   bash scripts/sutando-migrate.sh --help
#
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Resolution
# ──────────────────────────────────────────────────────────────────────────────

# Repo dir: env override, else walk-up from this script.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${SUTANDO_REPO_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Workspace dir: env override, else canonical default.
WORKSPACE_DIR="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"

# Sanity: refuse if same dir (no-op + dangerous).
if [ "$(cd "$REPO_DIR" 2>/dev/null && pwd)" = "$(cd "$WORKSPACE_DIR" 2>/dev/null && pwd)" ]; then
    echo "ERROR: REPO_DIR and WORKSPACE_DIR are the same path ($REPO_DIR)." >&2
    echo "This means SUTANDO_WORKSPACE is unset and you're in a legacy in-repo install." >&2
    echo "Set SUTANDO_WORKSPACE to a path outside the repo before running migrate." >&2
    exit 2
fi

# ──────────────────────────────────────────────────────────────────────────────
# Args
# ──────────────────────────────────────────────────────────────────────────────

COMMIT=0
RESUME=0
QUIET=0
for arg in "$@"; do
    case "$arg" in
        --commit) COMMIT=1 ;;
        --resume) RESUME=1 ;;
        --quiet)  QUIET=1 ;;
        --help|-h)
            sed -n '2,30p' "${BASH_SOURCE[0]}"
            exit 0
            ;;
        *)
            echo "Unknown flag: $arg" >&2
            echo "Try --help" >&2
            exit 2
            ;;
    esac
done

# ──────────────────────────────────────────────────────────────────────────────
# State surface — what to migrate
# ──────────────────────────────────────────────────────────────────────────────
#
# Per docs/workspace-contract.md, all per-user mutable state lives under
# WORKSPACE_DIR. The following are the canonical roots + loose files that
# legacy installs had under REPO_DIR.

# Top-level dirs (whole-tree migration with conflict-resolution)
STATE_DIRS=(
    "notes"
    "state"
    "results"
    "tasks"
    "logs"
    "data"
)

# Loose files (per-file migration)
STATE_FILES=(
    "build_log.md"
    "pending-questions.md"
    "context-drop.txt"
    "session-state.md"
)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

say() {
    [ "$QUIET" = "1" ] || echo "$@"
}

action() {
    # Print intended action. In dry-run, prepend "[dry] ". In commit mode, prepend "[do] ".
    local prefix="[dry] "
    [ "$COMMIT" = "1" ] && prefix="[do]  "
    say "${prefix}$*"
}

# Atomic per-file migration: copy via rsync (newer-wins + backup), verify SHA-256,
# then remove from source. Idempotent: if WS already has matching content, just rm src.
migrate_one_file() {
    local src="$1"
    local dst="$2"
    local label="${3:-$src}"

    if [ ! -e "$src" ]; then
        return 0
    fi

    if [ -d "$src" ]; then
        echo "ERROR: migrate_one_file called on a directory: $src" >&2
        return 1
    fi

    # Refuse symlinks at src (the #1149 footgun: shutil.move into a symlink target).
    if [ -L "$src" ]; then
        say "  ⚠ skip symlink: $label (#1149 safety — symlink at REPO side)"
        return 0
    fi

    local dst_dir
    dst_dir="$(dirname "$dst")"

    # If destination exists, decide newer-wins; old version backed up.
    if [ -e "$dst" ]; then
        if [ "$src" -nt "$dst" ]; then
            action "REPO newer → overwrite WS (backup old as .pre-migrate-bak): $label"
            if [ "$COMMIT" = "1" ]; then
                mkdir -p "$dst_dir"
                rsync -a --update --backup --suffix=".pre-migrate-bak" "$src" "$dst"
            fi
        elif [ "$src" -ot "$dst" ]; then
            # WS already newer — REPO copy is stale. Just verify & delete REPO side.
            if [ "$RESUME" = "1" ]; then
                say "  resume: WS newer → drop REPO stale: $label"
            else
                action "WS newer → drop REPO stale: $label"
            fi
        else
            # Same mtime — content might still differ. Hash compare.
            local src_hash dst_hash
            src_hash="$(shasum -a 256 "$src" | awk '{print $1}')"
            dst_hash="$(shasum -a 256 "$dst" | awk '{print $1}')"
            if [ "$src_hash" = "$dst_hash" ]; then
                action "identical hash → drop REPO dup: $label"
            else
                action "same mtime, diff hash → overwrite WS (backup): $label"
                if [ "$COMMIT" = "1" ]; then
                    mkdir -p "$dst_dir"
                    rsync -a --backup --suffix=".pre-migrate-bak" "$src" "$dst"
                fi
            fi
        fi
    else
        # Destination missing — straight copy.
        action "REPO-only → copy to WS: $label"
        if [ "$COMMIT" = "1" ]; then
            mkdir -p "$dst_dir"
            rsync -a "$src" "$dst"
        fi
    fi

    # Verify + delete source (only in commit mode).
    if [ "$COMMIT" = "1" ]; then
        if [ -e "$dst" ]; then
            local sh1 sh2
            sh1="$(shasum -a 256 "$src" | awk '{print $1}')"
            sh2="$(shasum -a 256 "$dst" | awk '{print $1}')"
            # Equal hash → safe to delete src. Not equal → WS has its own newer version (backup exists), safe to delete src too.
            if [ "$sh1" = "$sh2" ] || [ -e "${dst}.pre-migrate-bak" ]; then
                rm "$src"
            else
                say "  ⚠ verify mismatch + no backup, leaving REPO copy: $label"
                return 1
            fi
        fi
    fi
}

# Walk a directory tree, calling migrate_one_file for each regular file.
migrate_dir_tree() {
    local src_root="$1"
    local dst_root="$2"
    local label="${3:-$src_root}"

    if [ ! -d "$src_root" ]; then
        return 0
    fi

    # Note: -print0 / -d '' to handle filenames with spaces.
    local count=0
    while IFS= read -r -d '' f; do
        local rel="${f#$src_root/}"
        migrate_one_file "$f" "$dst_root/$rel" "$label/$rel" || true
        count=$((count + 1))
    done < <(find "$src_root" -type f -print0)

    say "  $count files processed under $label/"

    # If commit + dir is now empty, leave it as empty dir + sentinel
    if [ "$COMMIT" = "1" ]; then
        # Count remaining (skip .pre-migrate-bak)
        local remaining
        remaining="$(find "$src_root" -type f ! -name '*.pre-migrate-bak' 2>/dev/null | wc -l | tr -d ' ')"
        if [ "$remaining" = "0" ]; then
            # Clean up empty subdirs but keep the top-level dir + write sentinel
            find "$src_root" -mindepth 1 -type d -empty -delete 2>/dev/null || true
            # If a top-level sentinel is already there, leave it. Else write one.
            local sentinel="$src_root/DO-NOT-WRITE-HERE.md"
            if [ ! -e "$sentinel" ]; then
                write_sentinel "$sentinel" "$label" "$dst_root"
            fi
        fi
    fi
}

# Write a warning sentinel into an emptied state dir.
write_sentinel() {
    local path="$1"
    local label="$2"
    local ws_path="$3"
    cat > "$path" << EOF
# ⚠️ DO NOT WRITE TO THIS DIRECTORY ⚠️

This \`<repo>/$label/\` directory is **LEGACY**. Per the Sutando workspace contract
(see \`docs/workspace-contract.md\`), all per-user runtime state lives under
\`\$SUTANDO_WORKSPACE/$label/\` (default: \`~/.sutando/workspace/$label/\`).

If you're an LLM about to write here — **STOP**. Use:

\`\`\`python
from workspace_default import resolve_workspace
target = resolve_workspace() / "$label"
\`\`\`

or in bash:

\`\`\`bash
TARGET="\${SUTANDO_WORKSPACE:-\$HOME/.sutando/workspace}/$label"
\`\`\`

This sentinel was placed by \`scripts/sutando-migrate.sh\` after migrating
the contents to \`$ws_path\`.
EOF
}

# ──────────────────────────────────────────────────────────────────────────────
# Banner
# ──────────────────────────────────────────────────────────────────────────────

say "─────────────────────────────────────────────────────────────"
say "  sutando-migrate"
say "─────────────────────────────────────────────────────────────"
say "  REPO_DIR:      $REPO_DIR"
say "  WORKSPACE_DIR: $WORKSPACE_DIR"
say "  MODE:          $([ $COMMIT = 1 ] && echo "COMMIT (will modify)" || echo "DRY-RUN (read-only preview)")"
say "  RESUME:        $([ $RESUME = 1 ] && echo "yes" || echo "no")"
say "─────────────────────────────────────────────────────────────"

# ──────────────────────────────────────────────────────────────────────────────
# Pre-flight
# ──────────────────────────────────────────────────────────────────────────────

if [ "$COMMIT" = "1" ]; then
    mkdir -p "$WORKSPACE_DIR"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Migrate top-level state dirs
# ──────────────────────────────────────────────────────────────────────────────

for dir in "${STATE_DIRS[@]}"; do
    src="$REPO_DIR/$dir"
    dst="$WORKSPACE_DIR/$dir"

    if [ ! -d "$src" ]; then
        say "  [skip] $dir/ — not present in REPO"
        continue
    fi

    say ""
    say "▶ $dir/"
    migrate_dir_tree "$src" "$dst" "$dir"
done

# ──────────────────────────────────────────────────────────────────────────────
# Migrate loose files
# ──────────────────────────────────────────────────────────────────────────────

say ""
say "▶ loose files"
for f in "${STATE_FILES[@]}"; do
    src="$REPO_DIR/$f"
    dst="$WORKSPACE_DIR/$f"

    if [ ! -e "$src" ]; then
        continue
    fi

    migrate_one_file "$src" "$dst" "$f" || true
done

# ──────────────────────────────────────────────────────────────────────────────
# Done
# ──────────────────────────────────────────────────────────────────────────────

say ""
say "─────────────────────────────────────────────────────────────"
if [ "$COMMIT" = "1" ]; then
    say "  ✓ Migration commit complete."
    say ""
    say "  Re-run with --commit --resume to verify idempotency (should be no-op)."
    say "  To verify health: bash scripts/sutando-migrate.sh   # dry-run shows 0 actions"
else
    say "  Dry-run complete. No files modified."
    say ""
    say "  To execute: bash scripts/sutando-migrate.sh --commit"
    say "  To resume after interrupt: bash scripts/sutando-migrate.sh --commit --resume"
fi
say "─────────────────────────────────────────────────────────────"
