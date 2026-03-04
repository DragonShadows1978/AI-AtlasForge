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

# Get the AI-AtlasForge root directory (two levels up from hooks dir)
ATLASFORGE_ROOT = Path(__file__).resolve().parent.parent.parent

CORE_FILES = [
    # AI-AtlasForge Core
    str(ATLASFORGE_ROOT / "dashboard_v2.py"),
    str(ATLASFORGE_ROOT / "atlasforge_engine.py"),
    str(ATLASFORGE_ROOT / "atlasforge_conductor.py"),
    str(ATLASFORGE_ROOT / "exploration_hooks.py"),
    str(ATLASFORGE_ROOT / "io_utils.py"),
    str(ATLASFORGE_ROOT / "GROUND_RULES.md"),

    # AtlasForge Enhancements
    str(ATLASFORGE_ROOT / "atlasforge_enhancements" / "atlasforge_enhancer.py"),
    str(ATLASFORGE_ROOT / "atlasforge_enhancements" / "exploration_graph.py"),
    str(ATLASFORGE_ROOT / "atlasforge_enhancements" / "fingerprint_extractor.py"),
    str(ATLASFORGE_ROOT / "atlasforge_enhancements" / "mission_continuity_tracker.py"),

    # GlassBox
    str(ATLASFORGE_ROOT / "workspace" / "glassbox" / "dashboard_routes.py"),
    str(ATLASFORGE_ROOT / "workspace" / "glassbox" / "mission_archiver.py"),
    str(ATLASFORGE_ROOT / "workspace" / "glassbox" / "transcript_parser.py"),
]

# Backup directory
BACKUP_DIR = str(ATLASFORGE_ROOT / "backups" / "auto_backups")

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
