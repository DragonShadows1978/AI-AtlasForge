#!/usr/bin/env python3
"""
Claude Code Pre-Edit Hook: Automatic Backup System
Backs up core files before they are edited or written.

This hook triggers on Edit and Write operations and automatically
creates timestamped backups of protected "core" files.
"""

import json
import sys
import os
import shutil
from datetime import datetime
from pathlib import Path

# =============================================================================
# CONFIGURATION: Define your core files to protect
# =============================================================================

CORE_FILES = [
    # Mini-Mind v2 Core
    "/home/vader/mini-mind-v2/dashboard_v2.py",
    "/home/vader/mini-mind-v2/rd_engine.py",
    "/home/vader/mini-mind-v2/claude_autonomous.py",
    "/home/vader/mini-mind-v2/exploration_hooks.py",
    "/home/vader/mini-mind-v2/io_utils.py",
    "/home/vader/mini-mind-v2/GROUND_RULES.md",

    # RDE Enhancements
    "/home/vader/mini-mind-v2/rde_enhancements/rde_enhancer.py",
    "/home/vader/mini-mind-v2/rde_enhancements/exploration_graph.py",
    "/home/vader/mini-mind-v2/rde_enhancements/fingerprint_extractor.py",
    "/home/vader/mini-mind-v2/rde_enhancements/mission_continuity_tracker.py",

    # GlassBox
    "/home/vader/mini-mind-v2/workspace/glassbox/dashboard_routes.py",
    "/home/vader/mini-mind-v2/workspace/glassbox/mission_archiver.py",
    "/home/vader/mini-mind-v2/workspace/glassbox/transcript_parser.py",
]

# Backup directory
BACKUP_DIR = "/home/vader/mini-mind-v2/backups/auto_backups"

# Maximum backups to keep per file (oldest get deleted)
MAX_BACKUPS_PER_FILE = 10

# =============================================================================
# BACKUP FUNCTIONS
# =============================================================================

def get_backups_for_file(file_path):
    """Get list of existing backups for a file, sorted by date (oldest first)."""
    if not os.path.exists(BACKUP_DIR):
        return []

    base_name = os.path.basename(file_path)
    backups = []

    for f in os.listdir(BACKUP_DIR):
        if f.startswith(base_name + ".") and f.endswith(".bak"):
            backups.append(os.path.join(BACKUP_DIR, f))

    # Sort by modification time (oldest first)
    backups.sort(key=lambda x: os.path.getmtime(x))
    return backups

def cleanup_old_backups(file_path):
    """Remove old backups if we exceed MAX_BACKUPS_PER_FILE."""
    backups = get_backups_for_file(file_path)

    # Remove oldest backups if we have too many
    while len(backups) >= MAX_BACKUPS_PER_FILE:
        oldest = backups.pop(0)
        try:
            os.remove(oldest)
        except:
            pass

def create_backup(file_path):
    """Create a timestamped backup of the file."""
    try:
        if not os.path.exists(file_path):
            return True, "File doesn't exist yet, no backup needed"

        # Create backup directory if it doesn't exist
        os.makedirs(BACKUP_DIR, exist_ok=True)

        # Cleanup old backups first
        cleanup_old_backups(file_path)

        # Generate backup filename with timestamp
        file_name = os.path.basename(file_path)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{file_name}.{timestamp}.bak"
        backup_path = os.path.join(BACKUP_DIR, backup_name)

        # Create the backup
        shutil.copy2(file_path, backup_path)

        return True, f"Backup created: {backup_name}"
    except Exception as e:
        return False, f"Backup error: {e}"

def is_core_file(file_path):
    """Check if a file is in the protected core files list."""
    if not file_path:
        return False

    # Normalize path for comparison
    normalized_path = os.path.abspath(file_path)

    for core_file in CORE_FILES:
        if os.path.abspath(core_file) == normalized_path:
            return True

    return False

# =============================================================================
# MAIN HOOK HANDLER
# =============================================================================

def main():
    """Process PreToolUse hook for Write and Edit operations."""
    try:
        input_data = json.load(sys.stdin)
    except json.JSONDecodeError:
        # Invalid input, just exit
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})
    file_path = tool_input.get("file_path", "")

    # Only process Write and Edit tools
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    if not file_path:
        sys.exit(0)

    # Check if this file is a protected core file
    if is_core_file(file_path):
        success, message = create_backup(file_path)

        if success:
            # Backup successful, allow the operation
            # Print message to stderr so it shows in Claude's output
            print(f"[AUTO-BACKUP] {message}", file=sys.stderr)

            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": message
                }
            }
            print(json.dumps(output))
        else:
            # Backup failed - still allow but warn
            print(f"[AUTO-BACKUP WARNING] {message}", file=sys.stderr)

            output = {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": f"Backup failed but allowing edit: {message}"
                }
            }
            print(json.dumps(output))

    # Exit cleanly
    sys.exit(0)

if __name__ == "__main__":
    main()
