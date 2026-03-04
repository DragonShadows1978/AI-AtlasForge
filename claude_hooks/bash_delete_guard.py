#!/usr/bin/env python3
"""
Bash Delete Guard Hook v1.0

Intercepts rm/rmdir/unlink commands that target consequential files and
applies a deny-then-allow pattern:

1. First attempt: DENY with warning, back up the file(s) to
   ~/.afterimage/deleted_backups/<timestamp>/, tell the model what was saved.
2. Second attempt (same command hash): ALLOW — deletion is conscious.

Skipped (always allowed):
  - /tmp/, ~/.cache/, __pycache__, *.pyc, node_modules, dist/, .pytest_cache
  - Non-code/config file extensions
  - rm without a recognizable target file/dir

Backed up and warned:
  - Source/config files: .py .js .ts .json .yaml .yml .html .css .md .sql .sh .env
  - rm -rf on project directories
  - Any file inside a known project workspace
"""

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path


# --- Configuration -----------------------------------------------------------

SEEN_DELETES_FILE = Path.home() / ".afterimage" / ".seen_deletes"
BACKUP_DIR = Path.home() / ".afterimage" / "deleted_backups"

# Extensions that warrant a pause + backup
PROTECTED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".json", ".yaml", ".yml",
    ".html", ".css", ".scss", ".md", ".sql", ".sh", ".bash", ".env",
    ".toml", ".cfg", ".ini", ".conf", ".txt", ".rs", ".go", ".java",
    ".cpp", ".c", ".h", ".rb", ".php",
}

# Path fragments that are always safe to delete — skip guard
WHITELIST_FRAGMENTS = {
    "/tmp/", "/__pycache__/", "/.pytest_cache/", "/node_modules/",
    "/.mypy_cache/", "/.ruff_cache/", "/dist/", "/build/", "/.git/objects/",
    "/.cache/", "/site-packages/", ".pyc", ".pyo", ".egg-info",
}


# --- Helpers -----------------------------------------------------------------

def _is_whitelisted(path: str) -> bool:
    for fragment in WHITELIST_FRAGMENTS:
        if fragment in path or path.endswith(fragment.rstrip("/")):
            return True
    return False


def _is_protected(path: str) -> bool:
    p = Path(path)
    if _is_whitelisted(path):
        return False
    if p.suffix.lower() in PROTECTED_EXTENSIONS:
        return True
    # Directories being rm -rf'd are always consequential
    if p.exists() and p.is_dir():
        return True
    return False


# Patterns to extract file/dir targets from rm commands
_RM_PATTERN = re.compile(
    r'\brm\s+'
    r'(?:-[rfRFidvI\s]*\s+)*'
    r'(["\']?[\w./~\-][^\s;|&>]*)',
    re.MULTILINE
)

_UNLINK_PATTERN = re.compile(
    r'\bunlink\s+(["\']?[\w./~\-][^\s;|&>]*)'
)


def _extract_targets(command: str) -> list:
    targets = []
    for m in _RM_PATTERN.finditer(command):
        t = m.group(1).strip("'\"")
        if t and not t.startswith("-"):
            targets.append(os.path.expanduser(t))
    for m in _UNLINK_PATTERN.finditer(command):
        t = m.group(1).strip("'\"")
        if t:
            targets.append(os.path.expanduser(t))
    return targets


def _command_hash(command: str) -> str:
    return hashlib.md5(command.strip().encode()).hexdigest()[:16]


def _was_already_shown(cmd_hash: str) -> bool:
    if not SEEN_DELETES_FILE.exists():
        return False
    try:
        seen = SEEN_DELETES_FILE.read_text().strip().split("\n")
        return cmd_hash in seen[-200:]
    except Exception:
        return False


def _mark_shown(cmd_hash: str):
    SEEN_DELETES_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = []
        if SEEN_DELETES_FILE.exists():
            existing = SEEN_DELETES_FILE.read_text().strip().split("\n")[-199:]
        existing.append(cmd_hash)
        SEEN_DELETES_FILE.write_text("\n".join(existing))
    except Exception:
        pass


def _backup_targets(targets: list) -> list:
    """Back up existing targets to BACKUP_DIR. Returns list of backup paths."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backed_up = []
    for t in targets:
        p = Path(t)
        if not p.exists():
            continue
        dest_dir = BACKUP_DIR / ts
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / p.name
        try:
            if p.is_dir():
                shutil.copytree(str(p), str(dest), dirs_exist_ok=True)
            else:
                shutil.copy2(str(p), str(dest))
            backed_up.append(str(dest))
        except Exception as e:
            backed_up.append(f"(backup failed for {p.name}: {e})")
    return backed_up


# --- Main --------------------------------------------------------------------

def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    if input_data.get("hook_event_name") != "PreToolUse":
        sys.exit(0)
    if input_data.get("tool_name") != "Bash":
        sys.exit(0)

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    if not re.search(r'\brm\b|\bunlink\b', command):
        sys.exit(0)

    targets = _extract_targets(command)
    protected = [t for t in targets if _is_protected(t)]

    if not protected:
        sys.exit(0)

    cmd_hash = _command_hash(command)

    if _was_already_shown(cmd_hash):
        sys.exit(0)

    _mark_shown(cmd_hash)
    backed_up = _backup_targets(protected)

    lines = [
        "============================================================",
        "DELETE GUARD: Consequential deletion detected",
        "============================================================",
        "",
        "The following path(s) are about to be permanently deleted:",
        "",
    ]
    for t in protected:
        lines.append(f"  {t}")

    if backed_up:
        lines.extend([
            "",
            "Backup created before deletion:",
            "",
        ])
        for b in backed_up:
            lines.append(f"  {b}")
        lines.extend([
            "",
            "Recovery available at: ~/.afterimage/deleted_backups/",
        ])
    else:
        lines.extend([
            "",
            "No backup created (files may not exist yet or backup failed).",
        ])

    lines.extend([
        "",
        "If deletion is intentional, retry the same command to proceed.",
        "============================================================",
        "",
    ])

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "\n".join(lines),
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    main()
