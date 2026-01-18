#!/usr/bin/env python3
"""
Plan Backup Module - Automatic file backup before BUILDING stage.

This module provides automatic backup of files mentioned in implementation plans,
ensuring that any files to be modified during the BUILDING stage are preserved.

Features:
- Parse implementation_plan.md for file paths
- Create versioned backups (.v1.bak, .v2.bak, etc.)
- Automatic rotation (keep 10 versions per file per mission)
- Manifest tracking for backup history
- Restore functionality

Usage:
    from plan_backup import backup_planned_files

    # In atlasforge_engine.py when transitioning PLANNING -> BUILDING:
    result = backup_planned_files(mission)
    # Returns: {"files_backed_up": 3, "skipped": 1, "errors": [], ...}
"""

import os
import re
import shutil
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List, Set

logger = logging.getLogger(__name__)

# Configuration - use centralized configuration
from atlasforge_config import BACKUPS_DIR, BASE_DIR
BACKUP_DIR = BACKUPS_DIR / "plan_backups"
MAX_VERSIONS_PER_FILE = 10
MAX_FILE_SIZE_MB = 10  # Skip files larger than this

# Ensure backup directory exists
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def parse_plan_for_files(plan_path: Path) -> List[str]:
    """
    Parse an implementation plan markdown file to extract file paths.

    Looks for:
    - Explicit file paths in markdown (backticks, bold, or plain)
    - Paths in code blocks (e.g., File: /path/to/file.py)
    - Common patterns like absolute paths or `workspace/...`

    Args:
        plan_path: Path to the implementation_plan.md file

    Returns:
        List of unique file paths found in the plan
    """
    if not plan_path.exists():
        logger.warning(f"Plan file not found: {plan_path}")
        return []

    try:
        content = plan_path.read_text(encoding='utf-8')
    except Exception as e:
        logger.error(f"Failed to read plan file: {e}")
        return []

    found_paths: Set[str] = set()

    # Pattern 1: Explicit file paths with common extensions
    # Match absolute paths like /home/user/... or /opt/... with file extensions
    path_pattern = r'(?:^|\s|[`"\'\(])(/(?:home|opt|var|usr|tmp)/[^\s`"\'\)]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|toml|sh|sql|css|html))'
    for match in re.finditer(path_pattern, content, re.MULTILINE):
        found_paths.add(match.group(1))

    # Pattern 2: File: declarations (common in plans)
    file_decl_pattern = r'(?:File|Location|Path):\s*[`"\']?([^\s`"\'\n]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|toml|sh|sql|css|html))'
    for match in re.finditer(file_decl_pattern, content, re.IGNORECASE):
        path = match.group(1)
        if not path.startswith('/'):
            path = str(BASE_DIR / path)
        found_paths.add(path)

    # Pattern 3: Backtick-wrapped paths
    backtick_pattern = r'`([^`\s]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|toml|sh|sql|css|html))`'
    for match in re.finditer(backtick_pattern, content):
        path = match.group(1)
        if path.startswith('/'):
            found_paths.add(path)
        elif '/' in path:  # Relative path like workspace/file.py
            resolved = str(BASE_DIR / path)
            found_paths.add(resolved)

    # Pattern 4: Files to Modify table rows (markdown tables)
    # Match table rows like | `atlasforge_engine.py` | ... or | atlasforge_engine.py | ...
    table_pattern = r'\|\s*[`\*]?([^\|\`\*\s]+\.(?:py|ts|tsx|js|jsx|json|md|yaml|yml|toml|sh|sql|css|html))[`\*]?\s*\|'
    for match in re.finditer(table_pattern, content):
        path = match.group(1)
        if not path.startswith('/'):
            # Try to resolve relative paths
            # Check if it exists at BASE_DIR level
            candidate = BASE_DIR / path
            if candidate.exists():
                found_paths.add(str(candidate))

    # Filter out clearly non-path items
    filtered = []
    for path in found_paths:
        # Skip URLs
        if path.startswith('http://') or path.startswith('https://'):
            continue
        # Skip example/test patterns that are clearly not real files
        if '<' in path or '>' in path:  # e.g., <filename> or <N>
            continue
        if '${' in path or '{{' in path:  # Template variables
            continue
        # Skip the backup paths themselves
        if 'backups/plan_backups' in path:
            continue
        filtered.append(path)

    logger.info(f"Parsed {len(filtered)} file paths from plan")
    return sorted(set(filtered))


def get_backup_dir(mission_id: str) -> Path:
    """Get the backup directory for a specific mission."""
    backup_dir = BACKUP_DIR / mission_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def get_backup_filename(original_path: str, version: int) -> str:
    """
    Generate backup filename with version number.

    Args:
        original_path: Full path to original file
        version: Version number (1-10)

    Returns:
        Backup filename like "atlasforge_engine.py.v3.bak"
    """
    filename = Path(original_path).name
    return f"{filename}.v{version}.bak"


def get_existing_versions(backup_dir: Path, filename: str) -> List[int]:
    """
    Get list of existing backup versions for a file.

    Args:
        backup_dir: Mission backup directory
        filename: Original filename (e.g., "atlasforge_engine.py")

    Returns:
        Sorted list of version numbers
    """
    versions = []
    pattern = re.compile(rf'^{re.escape(filename)}\.v(\d+)\.bak$')

    for f in backup_dir.iterdir():
        match = pattern.match(f.name)
        if match:
            versions.append(int(match.group(1)))

    return sorted(versions)


def rotate_backups(backup_dir: Path, filename: str, max_versions: int = MAX_VERSIONS_PER_FILE) -> None:
    """
    Rotate backups, keeping only the newest max_versions.

    Args:
        backup_dir: Mission backup directory
        filename: Original filename
        max_versions: Maximum versions to keep (default 10)
    """
    versions = get_existing_versions(backup_dir, filename)

    if len(versions) <= max_versions:
        return  # Nothing to rotate

    # Sort and delete oldest
    versions_to_delete = sorted(versions)[:-max_versions]

    for version in versions_to_delete:
        backup_path = backup_dir / get_backup_filename(filename, version)
        try:
            backup_path.unlink()
            logger.debug(f"Rotated out old backup: {backup_path}")
        except OSError as e:
            logger.warning(f"Failed to delete old backup {backup_path}: {e}")


def backup_file(file_path: str, mission_id: str) -> Optional[Path]:
    """
    Create a versioned backup of a file.

    Args:
        file_path: Full path to the file to backup
        mission_id: Mission identifier for organizing backups

    Returns:
        Path to the created backup, or None if backup failed
    """
    source = Path(file_path)

    # Validation
    if not source.exists():
        logger.debug(f"File does not exist (skipping): {file_path}")
        return None

    if not source.is_file():
        logger.debug(f"Not a regular file (skipping): {file_path}")
        return None

    # Size check
    try:
        size_mb = source.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            logger.warning(f"File too large ({size_mb:.1f}MB > {MAX_FILE_SIZE_MB}MB): {file_path}")
            return None
    except OSError as e:
        logger.warning(f"Could not stat file {file_path}: {e}")
        return None

    backup_dir = get_backup_dir(mission_id)
    filename = source.name

    # Find next version number
    existing = get_existing_versions(backup_dir, filename)
    if existing:
        next_version = max(existing) + 1
    else:
        next_version = 1

    # Create backup
    backup_name = get_backup_filename(file_path, next_version)
    backup_path = backup_dir / backup_name

    try:
        shutil.copy2(source, backup_path)
        logger.info(f"Created backup: {backup_path}")

        # Rotate old versions if needed
        rotate_backups(backup_dir, filename)

        return backup_path
    except Exception as e:
        logger.error(f"Failed to backup {file_path}: {e}")
        return None


def backup_planned_files(mission: Dict) -> Dict[str, Any]:
    """
    Backup all files mentioned in the mission's implementation plan.

    This is the main entry point for atlasforge_engine.py integration.

    Args:
        mission: Mission dict with mission_id, mission_workspace, etc.

    Returns:
        Result dict with:
        - files_backed_up: Number of files successfully backed up
        - files_skipped: Number of files skipped (non-existent, etc.)
        - errors: List of error messages
        - manifest: List of backup details
    """
    result = {
        "files_backed_up": 0,
        "files_skipped": 0,
        "errors": [],
        "manifest": [],
        "backed_up_at": datetime.now().isoformat()
    }

    mission_id = mission.get("mission_id", "unknown")

    # Find implementation plan
    mission_workspace = mission.get("mission_workspace")
    if mission_workspace:
        plan_path = Path(mission_workspace) / "artifacts" / "implementation_plan.md"
    else:
        # Fallback to global workspace
        plan_path = BASE_DIR / "workspace" / "artifacts" / "implementation_plan.md"

    if not plan_path.exists():
        result["errors"].append(f"Implementation plan not found: {plan_path}")
        logger.warning(f"No implementation plan found at {plan_path}")
        return result

    # Parse plan for files
    file_paths = parse_plan_for_files(plan_path)

    if not file_paths:
        logger.info("No files found in implementation plan to backup")
        return result

    logger.info(f"Found {len(file_paths)} files in implementation plan")

    # Backup each file
    for file_path in file_paths:
        try:
            backup_path = backup_file(file_path, mission_id)
            if backup_path:
                result["files_backed_up"] += 1
                result["manifest"].append({
                    "original": file_path,
                    "backup": str(backup_path),
                    "backed_up_at": datetime.now().isoformat()
                })
            else:
                result["files_skipped"] += 1
        except Exception as e:
            error_msg = f"Error backing up {file_path}: {e}"
            result["errors"].append(error_msg)
            logger.error(error_msg)

    # Save manifest to backup directory
    backup_dir = get_backup_dir(mission_id)
    manifest_path = backup_dir / "manifest.json"
    try:
        existing_manifest = []
        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    existing_manifest = json.load(f)
            except (json.JSONDecodeError, IOError):
                existing_manifest = []

        # Append new backup session
        manifest_entry = {
            "backup_session": datetime.now().isoformat(),
            "mission_id": mission_id,
            "plan_path": str(plan_path),
            "files": result["manifest"]
        }
        existing_manifest.append(manifest_entry)

        with open(manifest_path, 'w') as f:
            json.dump(existing_manifest, f, indent=2)
    except Exception as e:
        logger.warning(f"Failed to save manifest: {e}")

    logger.info(f"Backup complete: {result['files_backed_up']} backed up, {result['files_skipped']} skipped")
    return result


def get_backup_manifest(mission_id: str) -> List[Dict]:
    """
    Get the backup manifest for a mission.

    Args:
        mission_id: Mission identifier

    Returns:
        List of backup session dicts
    """
    manifest_path = get_backup_dir(mission_id) / "manifest.json"

    if not manifest_path.exists():
        return []

    try:
        with open(manifest_path, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error(f"Failed to read manifest: {e}")
        return []


def list_backups(mission_id: str) -> List[Dict]:
    """
    List all backup files for a mission.

    Args:
        mission_id: Mission identifier

    Returns:
        List of backup file info dicts
    """
    backup_dir = get_backup_dir(mission_id)
    backups = []

    for f in backup_dir.iterdir():
        if f.name.endswith('.bak'):
            try:
                stat = f.stat()
                backups.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat()
                })
            except OSError:
                continue

    return sorted(backups, key=lambda x: x["filename"])


def restore_from_backup(file_path: str, mission_id: str, version: int = None) -> bool:
    """
    Restore a file from backup.

    Args:
        file_path: Original file path to restore
        mission_id: Mission identifier
        version: Specific version to restore (default: latest)

    Returns:
        True if restore succeeded, False otherwise
    """
    backup_dir = get_backup_dir(mission_id)
    filename = Path(file_path).name

    # Find available versions
    versions = get_existing_versions(backup_dir, filename)

    if not versions:
        logger.error(f"No backups found for {filename}")
        return False

    # Select version
    if version is None:
        version = max(versions)  # Latest
    elif version not in versions:
        logger.error(f"Version {version} not found. Available: {versions}")
        return False

    backup_path = backup_dir / get_backup_filename(file_path, version)

    if not backup_path.exists():
        logger.error(f"Backup file not found: {backup_path}")
        return False

    try:
        # Create backup of current file before restore
        target = Path(file_path)
        if target.exists():
            pre_restore_backup = target.with_suffix(target.suffix + '.pre_restore')
            shutil.copy2(target, pre_restore_backup)
            logger.info(f"Created pre-restore backup: {pre_restore_backup}")

        # Restore
        shutil.copy2(backup_path, file_path)
        logger.info(f"Restored {file_path} from {backup_path}")
        return True
    except Exception as e:
        logger.error(f"Restore failed: {e}")
        return False


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("Plan Backup Module - Self Test")
    print("=" * 60)

    # Test 1: Parse plan for files
    print("\n[TEST 1] Parsing sample implementation plan...")

    with tempfile.NamedTemporaryFile(mode='w', suffix='.md', delete=False) as f:
        f.write("""
# Implementation Plan

## Files to Modify

| File | Purpose |
|------|---------|
| `atlasforge_engine.py` | Add backup trigger |
| `dashboard_v2.py` | Add API endpoint |

## Implementation

**File:** `plan_backup.py`

This is the main module.

Also modify `mission_analytics.py` for analytics.

```python
# Example code
from plan_backup import backup_planned_files
```
""")
        temp_plan = f.name

    files = parse_plan_for_files(Path(temp_plan))
    print(f"  Found {len(files)} files:")
    for f in files:
        print(f"    - {f}")
    os.unlink(temp_plan)

    # Test 2: Backup a file
    print("\n[TEST 2] Creating test backup...")
    test_mission = "test_backup_mission"

    # Create a test file
    test_file = BASE_DIR / "workspace" / "test_backup_file.txt"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("Test content v1")

    backup_path = backup_file(str(test_file), test_mission)
    if backup_path:
        print(f"  Created backup: {backup_path}")
    else:
        print("  ERROR: Backup failed!")

    # Test 3: Create multiple versions
    print("\n[TEST 3] Creating multiple versions...")
    for i in range(2, 6):
        test_file.write_text(f"Test content v{i}")
        backup_file(str(test_file), test_mission)

    versions = get_existing_versions(get_backup_dir(test_mission), test_file.name)
    print(f"  Versions created: {versions}")

    # Test 4: Restore
    print("\n[TEST 4] Testing restore...")
    test_file.write_text("Modified content")
    success = restore_from_backup(str(test_file), test_mission, version=1)
    content = test_file.read_text()
    print(f"  Restore success: {success}")
    print(f"  Content after restore: {content}")

    # Test 5: Rotation
    print("\n[TEST 5] Testing rotation...")
    for i in range(6, 15):
        test_file.write_text(f"Test content v{i}")
        backup_file(str(test_file), test_mission)

    versions = get_existing_versions(get_backup_dir(test_mission), test_file.name)
    print(f"  Versions after rotation: {versions}")
    print(f"  Expected: 10 versions, got: {len(versions)}")

    # Cleanup
    print("\n[CLEANUP]")
    test_file.unlink()
    backup_dir = get_backup_dir(test_mission)
    shutil.rmtree(backup_dir)
    print("  Cleaned up test files")

    print("\n" + "=" * 60)
    print("All tests completed!")
    print("=" * 60)
