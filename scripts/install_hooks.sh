#!/usr/bin/env bash
#
# install_hooks.sh — Install AtlasForge Claude Code hooks
#
# Copies hooks from claude_hooks/ into ~/.claude/hooks/ and merges the
# hook registrations into ~/.claude/settings.json.
#
# Safe to run multiple times — existing hooks are overwritten with the
# latest version from the repo; settings.json entries are not duplicated.
#
# Usage:
#   ./scripts/install_hooks.sh
#   ./scripts/install_hooks.sh --dry-run    # show what would happen, no changes
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ATLASFORGE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOKS_SRC="$ATLASFORGE_ROOT/claude_hooks"
HOOKS_DEST="$HOME/.claude/hooks"
SETTINGS_FILE="$HOME/.claude/settings.json"

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
fi

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info()    { echo -e "${BLUE}[hooks]${NC} $1"; }
log_success() { echo -e "${GREEN}[hooks]${NC} $1"; }
log_dry()     { echo -e "${YELLOW}[dry-run]${NC} $1"; }

# ── 1. Copy hook files ────────────────────────────────────────────────────────

HOOKS=(
    pre_tool_use.py
    stage_gate_hook.py
    backup-core-files.py
    bash_write_guard.py
    bash_delete_guard.py
)

log_info "Installing hooks to $HOOKS_DEST"

if [ "$DRY_RUN" = false ]; then
    mkdir -p "$HOOKS_DEST"
fi

for hook in "${HOOKS[@]}"; do
    src="$HOOKS_SRC/$hook"
    dest="$HOOKS_DEST/$hook"

    if [ ! -f "$src" ]; then
        echo "  WARNING: $hook not found in claude_hooks/, skipping"
        continue
    fi

    if [ "$DRY_RUN" = true ]; then
        log_dry "Would copy: $src → $dest"
    else
        cp "$src" "$dest"
        chmod +x "$dest"
        log_success "Installed: $hook"
    fi
done

# ── 2. Merge settings.json ────────────────────────────────────────────────────
#
# Python handles the JSON merge safely. The merge is additive: existing hook
# entries are preserved; AtlasForge entries are added only if not already present.

log_info "Merging hook registrations into $SETTINGS_FILE"

python3 - "$SETTINGS_FILE" "$HOOKS_DEST" "$DRY_RUN" <<'PYEOF'
import json
import os
import sys

settings_file = sys.argv[1]
hooks_dest = sys.argv[2]
dry_run = sys.argv[3] == "true"

AF_HOOKS_WRITE_EDIT = [
    {"type": "command", "command": f"{hooks_dest}/pre_tool_use.py"},
    {"type": "command", "command": f"{hooks_dest}/stage_gate_hook.py"},
    {"type": "command", "command": f"{hooks_dest}/backup-core-files.py"},
]
AF_HOOKS_BASH = [
    {"type": "command", "command": f"{hooks_dest}/pre_tool_use.py"},
    {"type": "command", "command": f"{hooks_dest}/stage_gate_hook.py"},
    {"type": "command", "command": f"{hooks_dest}/bash_write_guard.py"},
    {"type": "command", "command": f"{hooks_dest}/bash_delete_guard.py"},
]

if os.path.exists(settings_file):
    with open(settings_file) as f:
        settings = json.load(f)
else:
    settings = {}

settings.setdefault("hooks", {})
settings["hooks"].setdefault("PreToolUse", [])

def find_matcher(hooks_list, matcher):
    for entry in hooks_list:
        if entry.get("matcher") == matcher:
            return entry
    return None

def merge_hooks(existing_list, matcher, new_hooks):
    entry = find_matcher(existing_list, matcher)
    if entry is None:
        existing_list.append({"matcher": matcher, "hooks": list(new_hooks)})
        return len(new_hooks)
    existing_cmds = {h["command"] for h in entry.get("hooks", [])}
    added = 0
    for h in new_hooks:
        if h["command"] not in existing_cmds:
            entry["hooks"].append(h)
            added += 1
    return added

pre = settings["hooks"]["PreToolUse"]
added_we = merge_hooks(pre, "Write|Edit", AF_HOOKS_WRITE_EDIT)
added_bash = merge_hooks(pre, "Bash", AF_HOOKS_BASH)

if dry_run:
    print(f"  Would add {added_we} Write|Edit hook(s) and {added_bash} Bash hook(s)")
else:
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    with open(settings_file, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")
    print(f"  Added {added_we} Write|Edit hook(s) and {added_bash} Bash hook(s)")
PYEOF

# ── 3. Summary ────────────────────────────────────────────────────────────────

echo ""
if [ "$DRY_RUN" = true ]; then
    log_dry "Dry run complete — no changes made"
else
    log_success "Hook installation complete"
    echo ""
    echo "  Hooks installed to: $HOOKS_DEST"
    echo "  Settings updated:   $SETTINGS_FILE"
    echo ""
    echo "  NOTE: afterimage_hook.py is NOT installed here."
    echo "  It belongs to AI-AfterImage — install that project separately."
    echo "  See: https://github.com/DragonShadows1978/AI-AfterImage"
fi
