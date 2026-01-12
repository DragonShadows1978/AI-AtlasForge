#!/usr/bin/env python3
"""
Backup Utilities: Standardized backup and restore for AI-AtlasForge.

Directory structure:
    <ATLASFORGE_ROOT>/backups/<module>/<file>.backup.<YYYY-MM-DD>.<ext>

Examples:
    backups/rd_engine/rd_engine.backup.2024-12-08.py
    backups/init_guard/init_guard.backup.2024-12-08.py

Usage:
    from backup_utils import create_backup, restore_backup, list_backups

    # Create a backup
    backup_path = create_backup("rd_engine.py")

    # Restore from backup
    restore_backup(backup_path)

    # List all backups
    backups = list_backups()
"""

import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict

# Base paths - use centralized configuration
from atlasforge_config import BACKUPS_DIR


def get_backup_dir() -> Path:
    """Get the backup directory path, creating it if needed."""
    BACKUPS_DIR.mkdir(exist_ok=True)
    return BACKUPS_DIR


def create_backup(source_path: str | Path, module_name: Optional[str] = None) -> Optional[Path]:
    """
    Create a backup of a file.

    Args:
        source_path: Path to the file to back up
        module_name: Optional module name for organizing backups.
                     If not provided, uses the filename without extension.

    Returns:
        Path to the backup file, or None if backup failed

    Example:
        backup_path = create_backup("rd_engine.py")
        # Creates: backups/rd_engine/rd_engine.backup.2024-12-08.py
    """
    source = Path(source_path)

    if not source.exists():
        print(f"ERROR: Source file does not exist: {source}")
        return None

    # Determine module name
    if module_name is None:
        module_name = source.stem  # filename without extension

    # Create module backup directory
    module_dir = get_backup_dir() / module_name
    module_dir.mkdir(exist_ok=True)

    # Generate backup filename
    date_str = datetime.now().strftime("%Y-%m-%d")
    backup_name = f"{source.stem}.backup.{date_str}{source.suffix}"
    backup_path = module_dir / backup_name

    # Handle multiple backups on same day
    counter = 1
    while backup_path.exists():
        backup_name = f"{source.stem}.backup.{date_str}.{counter}{source.suffix}"
        backup_path = module_dir / backup_name
        counter += 1

    try:
        shutil.copy2(source, backup_path)
        print(f"Backup created: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"ERROR: Failed to create backup: {e}")
        return None


def restore_backup(backup_path: str | Path, target_path: Optional[str | Path] = None) -> bool:
    """
    Restore a file from backup.

    Args:
        backup_path: Path to the backup file
        target_path: Where to restore to. If None, infers from backup path.

    Returns:
        True if restore succeeded, False otherwise

    Example:
        restore_backup("backups/rd_engine/rd_engine.backup.2024-12-08.py")
        # Restores to: <ATLASFORGE_ROOT>/rd_engine.py
    """
    backup = Path(backup_path)

    if not backup.exists():
        print(f"ERROR: Backup file does not exist: {backup}")
        return False

    # Infer target path if not provided
    if target_path is None:
        # Parse backup filename: <name>.backup.<date>[.<counter>].<ext>
        name_parts = backup.stem.split(".backup.")
        if len(name_parts) >= 1:
            original_name = name_parts[0]
            target_path = BASE_DIR / f"{original_name}{backup.suffix}"
        else:
            print(f"ERROR: Could not infer target path from backup name: {backup}")
            return False

    target = Path(target_path)

    try:
        shutil.copy2(backup, target)
        print(f"Restored: {backup} -> {target}")
        return True
    except Exception as e:
        print(f"ERROR: Failed to restore backup: {e}")
        return False


def list_backups(module_name: Optional[str] = None) -> List[Dict]:
    """
    List all backups, optionally filtered by module.

    Args:
        module_name: Optional module name to filter by

    Returns:
        List of dicts with backup info: path, module, date, size
    """
    backup_dir = get_backup_dir()
    backups = []

    if module_name:
        # List backups for specific module
        module_dir = backup_dir / module_name
        if module_dir.exists():
            for backup_file in module_dir.iterdir():
                if backup_file.is_file() and ".backup." in backup_file.name:
                    backups.append(_backup_info(backup_file, module_name))
    else:
        # List all backups
        for module_dir in backup_dir.iterdir():
            if module_dir.is_dir():
                for backup_file in module_dir.iterdir():
                    if backup_file.is_file() and ".backup." in backup_file.name:
                        backups.append(_backup_info(backup_file, module_dir.name))

    # Sort by date descending
    backups.sort(key=lambda x: x.get("modified", ""), reverse=True)
    return backups


def _backup_info(backup_path: Path, module_name: str) -> Dict:
    """Get info about a backup file."""
    stat = backup_path.stat()
    return {
        "path": str(backup_path),
        "module": module_name,
        "filename": backup_path.name,
        "size_bytes": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
    }


def get_latest_backup(module_name: str) -> Optional[Path]:
    """
    Get the most recent backup for a module.

    Args:
        module_name: Name of the module

    Returns:
        Path to the latest backup, or None if no backups exist
    """
    backups = list_backups(module_name)
    if backups:
        return Path(backups[0]["path"])
    return None


def backup_multiple(file_paths: List[str | Path]) -> Dict[str, Optional[Path]]:
    """
    Backup multiple files at once.

    Args:
        file_paths: List of file paths to back up

    Returns:
        Dict mapping original path to backup path (or None if failed)
    """
    results = {}
    for path in file_paths:
        results[str(path)] = create_backup(path)
    return results


if __name__ == "__main__":
    # Self-test
    print("Backup Utils - Self Test")
    print("=" * 50)

    print(f"\nBackup directory: {get_backup_dir()}")

    # List existing backups
    backups = list_backups()
    print(f"\nExisting backups: {len(backups)}")
    for b in backups[:5]:
        print(f"  - {b['module']}/{b['filename']} ({b['size_bytes']} bytes)")

    print("\nBackup Utils ready!")
