#!/usr/bin/env python3
"""
One-time Migration Script: recommendations.json -> SQLite

This script migrates existing mission suggestions from the JSON file
to the new SQLite database backend.

Usage:
    python migrate_suggestions_to_sqlite.py [--dry-run] [--backup]

Options:
    --dry-run   Show what would be migrated without actually doing it
    --backup    Create a backup of the JSON file before migration
    --force     Force migration even if database already has data
"""

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from suggestion_storage import SQLiteSuggestionStorage, DB_PATH
from atlasforge_config import BASE_DIR, STATE_DIR, BACKUPS_DIR

# Paths
JSON_PATH = STATE_DIR / "recommendations.json"
BACKUP_DIR = BACKUPS_DIR / "migration_backups"


def create_backup(json_path: Path) -> Path:
    """Create a timestamped backup of the JSON file."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = BACKUP_DIR / f"recommendations_{timestamp}.json"
    shutil.copy2(json_path, backup_path)
    return backup_path


def main():
    parser = argparse.ArgumentParser(
        description="Migrate mission suggestions from JSON to SQLite"
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Show what would be migrated without doing it"
    )
    parser.add_argument(
        '--backup',
        action='store_true',
        help="Create a backup of the JSON file before migration"
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help="Force migration even if database already has data"
    )
    args = parser.parse_args()

    print("=" * 60)
    print("Mission Suggestions Migration: JSON -> SQLite")
    print("=" * 60)

    # Check source file
    if not JSON_PATH.exists():
        print(f"\n[ERROR] Source file not found: {JSON_PATH}")
        sys.exit(1)

    # Load and analyze JSON file
    try:
        with open(JSON_PATH, 'r') as f:
            data = json.load(f)
        items = data.get('items', [])
    except json.JSONDecodeError as e:
        print(f"\n[ERROR] Invalid JSON file: {e}")
        sys.exit(1)

    print(f"\n[1] Source Analysis:")
    print(f"    JSON file: {JSON_PATH}")
    print(f"    File size: {JSON_PATH.stat().st_size / 1024:.2f} KB")
    print(f"    Total items: {len(items)}")

    if not items:
        print("\n[INFO] No items to migrate. Exiting.")
        sys.exit(0)

    # Analyze items
    source_types = {}
    health_statuses = {}
    for item in items:
        st = item.get('source_type', 'unknown')
        hs = item.get('health_status', 'unknown')
        source_types[st] = source_types.get(st, 0) + 1
        health_statuses[hs] = health_statuses.get(hs, 0) + 1

    print(f"    By source_type: {source_types}")
    print(f"    By health_status: {health_statuses}")

    # Check database status
    print(f"\n[2] Target Database:")
    print(f"    Database: {DB_PATH}")
    print(f"    Exists: {DB_PATH.exists()}")

    storage = SQLiteSuggestionStorage()
    existing_count = storage.count()
    print(f"    Existing items: {existing_count}")

    if existing_count > 0 and not args.force:
        print("\n[WARNING] Database already contains data!")
        print("    Use --force to proceed anyway (items with same ID will be skipped)")
        if not args.dry_run:
            response = input("    Continue? [y/N]: ")
            if response.lower() != 'y':
                print("\nMigration cancelled.")
                sys.exit(0)

    # Dry run - just show what would happen
    if args.dry_run:
        print("\n[3] Dry Run Results:")
        print(f"    Would migrate {len(items)} items")
        print(f"    From: {JSON_PATH}")
        print(f"    To: {DB_PATH}")
        print("\n    No changes made (dry run).")
        sys.exit(0)

    # Create backup if requested
    if args.backup:
        print("\n[3] Creating Backup:")
        backup_path = create_backup(JSON_PATH)
        print(f"    Backup created: {backup_path}")

    # Run migration
    print(f"\n[{'4' if args.backup else '3'}] Running Migration:")
    result = storage.migrate_from_json(JSON_PATH)

    print(f"    Success: {result['success']}")
    print(f"    Imported: {result['imported']}")
    print(f"    Skipped: {result['skipped']} (already existed)")
    print(f"    Errors: {result.get('total_errors', 0)}")

    if result.get('errors'):
        print("\n    First 5 errors:")
        for err in result['errors'][:5]:
            print(f"      - {err['id']}: {err['error']}")

    # Verify migration
    print(f"\n[{'5' if args.backup else '4'}] Verification:")
    final_count = storage.count()
    print(f"    JSON items: {result['json_count']}")
    print(f"    DB items: {final_count}")
    print(f"    Match: {'YES' if final_count >= result['imported'] else 'NO'}")

    # Show final stats
    stats = storage.get_stats()
    print(f"\n[{'6' if args.backup else '5'}] Database Stats:")
    print(f"    Total items: {stats['total']}")
    print(f"    By source type: {stats['by_source_type']}")
    print(f"    By health status: {stats['by_health_status']}")
    if 'db_size_kb' in stats:
        print(f"    DB size: {stats['db_size_kb']} KB")

    print("\n" + "=" * 60)
    if result['success'] and result['imported'] > 0:
        print("Migration completed successfully!")
        print(f"\nThe JSON file has been preserved at: {JSON_PATH}")
        print("You can delete it after verifying the dashboard works correctly.")
    elif result['imported'] == 0 and result['skipped'] > 0:
        print("All items already exist in database. Nothing to migrate.")
    else:
        print("Migration completed with issues. Check errors above.")
    print("=" * 60)


if __name__ == "__main__":
    main()
