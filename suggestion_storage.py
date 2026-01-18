#!/usr/bin/env python3
"""
Mission Suggestions SQLite Storage Backend

Provides persistent, durable storage for mission suggestions with:
- ACID transactions for data integrity
- WAL mode for concurrent read performance
- Schema versioning for future migrations
- Full CRUD operations with filtering
- Migration utility from JSON

Usage:
    from suggestion_storage import SQLiteSuggestionStorage

    storage = SQLiteSuggestionStorage()

    # Get all suggestions
    suggestions = storage.get_all()

    # Add a new suggestion
    storage.add({"mission_title": "...", ...})

    # Query with filters
    hot_items = storage.get_filtered(health_status="hot")
"""

import json
import logging
import sqlite3
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Generator

logger = logging.getLogger(__name__)

# Database location
BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
DB_PATH = STATE_DIR / "mission_suggestions.db"

# Current schema version
SCHEMA_VERSION = 1


class SuggestionStorageBackend(ABC):
    """Abstract base class for suggestion storage backends."""

    @abstractmethod
    def get_all(self) -> List[Dict[str, Any]]:
        """Get all suggestions."""
        pass

    @abstractmethod
    def get_by_id(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific suggestion by ID."""
        pass

    @abstractmethod
    def add(self, suggestion: Dict[str, Any]) -> str:
        """Add a new suggestion. Returns the generated ID."""
        pass

    @abstractmethod
    def update(self, suggestion_id: str, updates: Dict[str, Any]) -> bool:
        """Update a suggestion. Returns True if found and updated."""
        pass

    @abstractmethod
    def delete(self, suggestion_id: str) -> bool:
        """Delete a suggestion. Returns True if found and deleted."""
        pass

    @abstractmethod
    def get_health_report(self) -> Dict[str, Any]:
        """Get health status summary."""
        pass


class SQLiteSuggestionStorage(SuggestionStorageBackend):
    """SQLite-based storage backend for mission suggestions."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self._ensure_schema()

    @contextmanager
    def _get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            # Enable WAL mode for concurrent reads
            conn.execute("PRAGMA journal_mode=WAL")
            # Enable foreign keys
            conn.execute("PRAGMA foreign_keys=ON")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        """Create database schema if it doesn't exist."""
        # Ensure state directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with self._get_connection() as conn:
            # Check current version
            version = conn.execute("PRAGMA user_version").fetchone()[0]

            if version < SCHEMA_VERSION:
                self._create_schema(conn)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                logger.info(f"Database schema updated to version {SCHEMA_VERSION}")

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        """Create the database schema."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mission_suggestions (
                id TEXT PRIMARY KEY,
                mission_title TEXT NOT NULL,
                mission_description TEXT,
                suggested_cycles INTEGER DEFAULT 3 CHECK(suggested_cycles >= 1 AND suggested_cycles <= 10),
                source_mission_id TEXT,
                source_mission_summary TEXT,
                rationale TEXT,
                created_at TEXT NOT NULL,
                source_type TEXT DEFAULT 'manual' CHECK(source_type IN ('drift_halt', 'successful_completion', 'merged', 'manual')),
                priority_score REAL DEFAULT 50.0,
                health_status TEXT DEFAULT 'healthy' CHECK(health_status IN ('healthy', 'stale', 'orphaned', 'needs_review', 'hot')),
                last_analyzed_at TEXT,
                last_edited_at TEXT,
                -- JSON columns for complex data
                auto_tags TEXT DEFAULT '[]',
                merged_from TEXT,
                merged_source_descriptions TEXT,
                drift_context TEXT,
                -- Preserved originals for edited items
                original_mission_title TEXT,
                original_mission_description TEXT,
                original_rationale TEXT,
                original_suggested_cycles INTEGER
            );

            -- Indexes for common queries
            CREATE INDEX IF NOT EXISTS idx_suggestions_source_type
                ON mission_suggestions(source_type);
            CREATE INDEX IF NOT EXISTS idx_suggestions_health_status
                ON mission_suggestions(health_status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_priority
                ON mission_suggestions(priority_score DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_created
                ON mission_suggestions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_source_mission
                ON mission_suggestions(source_mission_id);
        """)

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a database row to a suggestion dict."""
        if row is None:
            return None

        result = dict(row)

        # Parse JSON columns
        for json_col in ['auto_tags', 'merged_from', 'merged_source_descriptions', 'drift_context']:
            if result.get(json_col):
                try:
                    result[json_col] = json.loads(result[json_col])
                except json.JSONDecodeError:
                    result[json_col] = [] if json_col in ['auto_tags', 'merged_from'] else None
            elif json_col == 'auto_tags':
                result[json_col] = []

        # Remove None values for cleaner output
        return {k: v for k, v in result.items() if v is not None}

    def _dict_to_row(self, suggestion: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a suggestion dict to row values with JSON serialization."""
        row = dict(suggestion)

        # Serialize JSON columns
        for json_col in ['auto_tags', 'merged_from', 'merged_source_descriptions', 'drift_context']:
            if json_col in row and row[json_col] is not None:
                if isinstance(row[json_col], (list, dict)):
                    row[json_col] = json.dumps(row[json_col])

        return row

    # =========================================================================
    # CRUD Operations
    # =========================================================================

    def get_all(self) -> List[Dict[str, Any]]:
        """Get all suggestions sorted by priority."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM mission_suggestions ORDER BY priority_score DESC"
            )
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_by_id(self, suggestion_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific suggestion by ID."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM mission_suggestions WHERE id = ?",
                (suggestion_id,)
            )
            row = cursor.fetchone()
            return self._row_to_dict(row) if row else None

    def add(self, suggestion: Dict[str, Any]) -> str:
        """Add a new suggestion. Returns the generated ID.

        Args:
            suggestion: Dict containing suggestion data. Required fields:
                - mission_title (str): Title of the suggested mission

        Returns:
            str: The generated or provided suggestion ID

        Raises:
            ValueError: If mission_title is explicitly None or empty string
            sqlite3.IntegrityError: If duplicate ID is provided
        """
        # Defensive validation for required fields
        mission_title = suggestion.get('mission_title')
        if mission_title is None:
            raise ValueError(
                "mission_title is required and cannot be None. "
                "Provide a non-empty string for mission_title."
            )
        if isinstance(mission_title, str) and not mission_title.strip():
            raise ValueError(
                "mission_title cannot be an empty string. "
                "Provide a meaningful title for the mission suggestion."
            )

        # Validate source_type if provided
        valid_source_types = ('drift_halt', 'successful_completion', 'merged', 'manual')
        source_type = suggestion.get('source_type', 'manual')
        if source_type not in valid_source_types:
            raise ValueError(
                f"Invalid source_type '{source_type}'. "
                f"Must be one of: {', '.join(valid_source_types)}"
            )

        # Validate health_status if provided
        valid_health_statuses = ('healthy', 'stale', 'orphaned', 'needs_review', 'hot')
        health_status = suggestion.get('health_status', 'healthy')
        if health_status not in valid_health_statuses:
            raise ValueError(
                f"Invalid health_status '{health_status}'. "
                f"Must be one of: {', '.join(valid_health_statuses)}"
            )

        # Validate suggested_cycles range
        suggested_cycles = suggestion.get('suggested_cycles', 3)
        if not isinstance(suggested_cycles, int) or not (1 <= suggested_cycles <= 10):
            raise ValueError(
                f"suggested_cycles must be an integer between 1 and 10, got: {suggested_cycles}"
            )

        # Generate ID if not provided
        suggestion_id = suggestion.get('id') or f"rec_{uuid.uuid4().hex[:8]}"

        # Build normalized suggestion dict
        now = datetime.now().isoformat()
        suggestion = {
            'id': suggestion_id,
            'mission_title': suggestion.get('mission_title', 'Untitled Mission'),
            'mission_description': suggestion.get('mission_description', ''),
            'suggested_cycles': suggestion.get('suggested_cycles', 3),
            'source_mission_id': suggestion.get('source_mission_id'),
            'source_mission_summary': suggestion.get('source_mission_summary', ''),
            'rationale': suggestion.get('rationale', ''),
            'created_at': suggestion.get('created_at', now),
            'source_type': suggestion.get('source_type', 'manual'),
            'priority_score': suggestion.get('priority_score', 50.0),
            'health_status': suggestion.get('health_status', 'healthy'),
            'last_analyzed_at': suggestion.get('last_analyzed_at'),
            'last_edited_at': suggestion.get('last_edited_at'),
            'auto_tags': suggestion.get('auto_tags', []),
            'merged_from': suggestion.get('merged_from'),
            'merged_source_descriptions': suggestion.get('merged_source_descriptions'),
            'drift_context': suggestion.get('drift_context'),
            'original_mission_title': suggestion.get('original_mission_title'),
            'original_mission_description': suggestion.get('original_mission_description'),
            'original_rationale': suggestion.get('original_rationale'),
            'original_suggested_cycles': suggestion.get('original_suggested_cycles')
        }

        row = self._dict_to_row(suggestion)

        with self._get_connection() as conn:
            columns = ', '.join(row.keys())
            placeholders = ', '.join(['?' for _ in row])
            conn.execute(
                f"INSERT INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                list(row.values())
            )
            logger.debug(f"Added suggestion: {suggestion_id}")

        return suggestion_id

    def update(self, suggestion_id: str, updates: Dict[str, Any]) -> bool:
        """Update a suggestion. Returns True if found and updated."""
        # Don't allow updating the ID
        updates.pop('id', None)

        if not updates:
            return False

        row_updates = self._dict_to_row(updates)

        with self._get_connection() as conn:
            set_clause = ', '.join([f"{k} = ?" for k in row_updates.keys()])
            cursor = conn.execute(
                f"UPDATE mission_suggestions SET {set_clause} WHERE id = ?",
                list(row_updates.values()) + [suggestion_id]
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated suggestion: {suggestion_id}")
            return updated

    def delete(self, suggestion_id: str) -> bool:
        """Delete a suggestion. Returns True if found and deleted."""
        with self._get_connection() as conn:
            cursor = conn.execute(
                "DELETE FROM mission_suggestions WHERE id = ?",
                (suggestion_id,)
            )
            deleted = cursor.rowcount > 0
            if deleted:
                logger.debug(f"Deleted suggestion: {suggestion_id}")
            return deleted

    def delete_multiple(self, suggestion_ids: List[str]) -> int:
        """Delete multiple suggestions. Returns count of deleted items."""
        if not suggestion_ids:
            return 0

        with self._get_connection() as conn:
            placeholders = ', '.join(['?' for _ in suggestion_ids])
            cursor = conn.execute(
                f"DELETE FROM mission_suggestions WHERE id IN ({placeholders})",
                suggestion_ids
            )
            count = cursor.rowcount
            logger.debug(f"Deleted {count} suggestions")
            return count

    # =========================================================================
    # Filtered Queries
    # =========================================================================

    def get_filtered(
        self,
        source_type: str = None,
        health_status: str = None,
        min_priority: float = None,
        max_priority: float = None,
        limit: int = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get suggestions with optional filters."""
        conditions = []
        params = []

        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)

        if health_status:
            conditions.append("health_status = ?")
            params.append(health_status)

        if min_priority is not None:
            conditions.append("priority_score >= ?")
            params.append(min_priority)

        if max_priority is not None:
            conditions.append("priority_score <= ?")
            params.append(max_priority)

        query = "SELECT * FROM mission_suggestions"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority_score DESC"

        if limit:
            query += f" LIMIT {limit} OFFSET {offset}"

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def count(self, health_status: str = None, source_type: str = None) -> int:
        """Count suggestions with optional filters."""
        conditions = []
        params = []

        if health_status:
            conditions.append("health_status = ?")
            params.append(health_status)

        if source_type:
            conditions.append("source_type = ?")
            params.append(source_type)

        query = "SELECT COUNT(*) FROM mission_suggestions"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        with self._get_connection() as conn:
            return conn.execute(query, params).fetchone()[0]

    # =========================================================================
    # Health & Analytics
    # =========================================================================

    def get_health_report(self) -> Dict[str, Any]:
        """Get health status summary."""
        with self._get_connection() as conn:
            # Count by health status
            cursor = conn.execute("""
                SELECT health_status, COUNT(*) as count
                FROM mission_suggestions
                GROUP BY health_status
            """)
            counts = {row['health_status']: row['count'] for row in cursor.fetchall()}

            # Get total
            total = conn.execute(
                "SELECT COUNT(*) FROM mission_suggestions"
            ).fetchone()[0]

            # Get stale items (limited to 10)
            cursor = conn.execute("""
                SELECT id, mission_title, created_at
                FROM mission_suggestions
                WHERE health_status = 'stale'
                ORDER BY created_at ASC
                LIMIT 10
            """)
            stale_items = [dict(row) for row in cursor.fetchall()]

            # Get orphaned items (limited to 10)
            cursor = conn.execute("""
                SELECT id, mission_title
                FROM mission_suggestions
                WHERE health_status = 'orphaned'
                LIMIT 10
            """)
            orphaned_items = [dict(row) for row in cursor.fetchall()]

            return {
                'counts': {
                    'healthy': counts.get('healthy', 0),
                    'stale': counts.get('stale', 0),
                    'orphaned': counts.get('orphaned', 0),
                    'needs_review': counts.get('needs_review', 0),
                    'hot': counts.get('hot', 0)
                },
                'total': total,
                'stale_items': stale_items,
                'orphaned_items': orphaned_items,
                'needs_analysis': any(v == 0 for v in counts.values()) and total > 0
            }

    def get_stats(self) -> Dict[str, Any]:
        """Get general statistics about stored suggestions."""
        with self._get_connection() as conn:
            stats = {}

            # Total count
            stats['total'] = conn.execute(
                "SELECT COUNT(*) FROM mission_suggestions"
            ).fetchone()[0]

            # Count by source type
            cursor = conn.execute("""
                SELECT source_type, COUNT(*) as count
                FROM mission_suggestions
                GROUP BY source_type
            """)
            stats['by_source_type'] = {row['source_type']: row['count'] for row in cursor.fetchall()}

            # Count by health status
            cursor = conn.execute("""
                SELECT health_status, COUNT(*) as count
                FROM mission_suggestions
                GROUP BY health_status
            """)
            stats['by_health_status'] = {row['health_status']: row['count'] for row in cursor.fetchall()}

            # Average priority
            avg_priority = conn.execute(
                "SELECT AVG(priority_score) FROM mission_suggestions"
            ).fetchone()[0]
            stats['avg_priority'] = round(avg_priority, 2) if avg_priority else 0

            # Recent items (last 7 days)
            cursor = conn.execute("""
                SELECT COUNT(*) FROM mission_suggestions
                WHERE datetime(created_at) >= datetime('now', '-7 days')
            """)
            stats['recent_7d'] = cursor.fetchone()[0]

            # Database file size
            if self.db_path.exists():
                stats['db_size_kb'] = round(self.db_path.stat().st_size / 1024, 2)

            return stats

    # =========================================================================
    # Bulk Operations
    # =========================================================================

    def upsert(self, suggestion: Dict[str, Any]) -> str:
        """Insert or update a suggestion safely.

        Uses INSERT OR REPLACE to avoid race conditions.
        Returns the suggestion ID.
        """
        # Generate ID if not provided
        suggestion_id = suggestion.get('id') or f"rec_{uuid.uuid4().hex[:8]}"
        suggestion['id'] = suggestion_id

        # Ensure required fields have defaults
        now = datetime.now().isoformat()
        if 'created_at' not in suggestion:
            suggestion['created_at'] = now
        if 'mission_title' not in suggestion:
            suggestion['mission_title'] = 'Untitled'

        row = self._dict_to_row(suggestion)

        with self._get_connection() as conn:
            columns = ', '.join(row.keys())
            placeholders = ', '.join(['?' for _ in row])
            conn.execute(
                f"INSERT OR REPLACE INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                list(row.values())
            )

        return suggestion_id

    def upsert_batch(self, suggestions: List[Dict[str, Any]]) -> int:
        """Upsert multiple suggestions safely.

        Unlike update_all(), this does NOT delete existing records.
        Only updates/inserts the provided records.
        Returns count of upserted records.
        """
        count = 0
        now = datetime.now().isoformat()

        with self._get_connection() as conn:
            for suggestion in suggestions:
                if 'id' not in suggestion:
                    suggestion['id'] = f"rec_{uuid.uuid4().hex[:8]}"
                if 'created_at' not in suggestion:
                    suggestion['created_at'] = now
                if 'mission_title' not in suggestion:
                    suggestion['mission_title'] = 'Untitled'

                row = self._dict_to_row(suggestion)
                columns = ', '.join(row.keys())
                placeholders = ', '.join(['?' for _ in row])
                conn.execute(
                    f"INSERT OR REPLACE INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                    list(row.values())
                )
                count += 1

        logger.info(f"Upserted {count} suggestions (safe batch)")
        return count

    def update_all(self, suggestions: List[Dict[str, Any]]) -> int:
        """Replace all suggestions with the provided list.

        WARNING: This method is DEPRECATED due to race condition risks.
        Use upsert_batch() instead for safe updates that don't lose concurrent inserts.
        """
        import warnings
        warnings.warn(
            "update_all() is deprecated due to race condition risks. "
            "Use upsert_batch() instead.",
            DeprecationWarning,
            stacklevel=2
        )
        now = datetime.now().isoformat()
        with self._get_connection() as conn:
            # Clear existing
            conn.execute("DELETE FROM mission_suggestions")

            # Insert all
            for suggestion in suggestions:
                # Ensure required fields have defaults
                if 'created_at' not in suggestion:
                    suggestion['created_at'] = now
                if 'id' not in suggestion:
                    suggestion['id'] = f"rec_{uuid.uuid4().hex[:8]}"
                if 'mission_title' not in suggestion:
                    suggestion['mission_title'] = 'Untitled'

                row = self._dict_to_row(suggestion)
                columns = ', '.join(row.keys())
                placeholders = ', '.join(['?' for _ in row])
                conn.execute(
                    f"INSERT INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                    list(row.values())
                )

            logger.info(f"Bulk updated {len(suggestions)} suggestions")
            return len(suggestions)

    def update_batch(self, updates: List[Dict[str, Any]]) -> int:
        """Update multiple suggestions. Each dict must have 'id' field."""
        updated = 0
        with self._get_connection() as conn:
            for update in updates:
                suggestion_id = update.pop('id', None)
                if not suggestion_id or not update:
                    continue

                row_updates = self._dict_to_row(update)
                set_clause = ', '.join([f"{k} = ?" for k in row_updates.keys()])
                cursor = conn.execute(
                    f"UPDATE mission_suggestions SET {set_clause} WHERE id = ?",
                    list(row_updates.values()) + [suggestion_id]
                )
                updated += cursor.rowcount

        logger.info(f"Batch updated {updated} suggestions")
        return updated

    # =========================================================================
    # Migration
    # =========================================================================

    def migrate_from_json(self, json_path: Path) -> Dict[str, Any]:
        """Migrate existing JSON data to SQLite.

        Args:
            json_path: Path to the recommendations.json file

        Returns:
            Dict with migration results (imported, skipped, errors)
        """
        if not json_path.exists():
            return {
                'success': False,
                'error': f"JSON file not found: {json_path}",
                'imported': 0
            }

        try:
            with open(json_path, 'r') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            return {
                'success': False,
                'error': f"Invalid JSON: {e}",
                'imported': 0
            }

        items = data.get('items', [])
        if not items:
            return {
                'success': True,
                'imported': 0,
                'message': 'No items to migrate'
            }

        imported = 0
        skipped = 0
        errors = []

        with self._get_connection() as conn:
            for item in items:
                try:
                    # Check if already exists
                    existing = conn.execute(
                        "SELECT id FROM mission_suggestions WHERE id = ?",
                        (item.get('id'),)
                    ).fetchone()

                    if existing:
                        skipped += 1
                        continue

                    # Prepare item for insertion
                    suggestion = {
                        'id': item.get('id', f"rec_{uuid.uuid4().hex[:8]}"),
                        'mission_title': item.get('mission_title', 'Untitled'),
                        'mission_description': item.get('mission_description', ''),
                        'suggested_cycles': item.get('suggested_cycles', 3),
                        'source_mission_id': item.get('source_mission_id'),
                        'source_mission_summary': item.get('source_mission_summary', ''),
                        'rationale': item.get('rationale', ''),
                        'created_at': item.get('created_at', datetime.now().isoformat()),
                        'source_type': item.get('source_type', 'manual'),
                        'priority_score': item.get('priority_score', 50.0),
                        'health_status': item.get('health_status', 'healthy'),
                        'last_analyzed_at': item.get('last_analyzed_at'),
                        'last_edited_at': item.get('last_edited_at'),
                        'auto_tags': item.get('auto_tags', []),
                        'merged_from': item.get('merged_from'),
                        'merged_source_descriptions': item.get('merged_source_descriptions'),
                        'drift_context': item.get('drift_context'),
                        'original_mission_title': item.get('original_mission_title'),
                        'original_mission_description': item.get('original_mission_description'),
                        'original_rationale': item.get('original_rationale'),
                        'original_suggested_cycles': item.get('original_suggested_cycles')
                    }

                    row = self._dict_to_row(suggestion)
                    columns = ', '.join(row.keys())
                    placeholders = ', '.join(['?' for _ in row])
                    conn.execute(
                        f"INSERT INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                        list(row.values())
                    )
                    imported += 1

                except Exception as e:
                    errors.append({
                        'id': item.get('id', 'unknown'),
                        'error': str(e)
                    })

        # Verify count matches
        final_count = self.count()

        result = {
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'errors': errors[:10] if errors else [],
            'total_errors': len(errors),
            'json_count': len(items),
            'db_count': final_count,
            'counts_match': final_count >= imported
        }

        if imported > 0:
            logger.info(f"Migrated {imported} suggestions from JSON to SQLite")

        return result

    def export_to_json(self, json_path: Path = None) -> Dict[str, Any]:
        """Export all suggestions to JSON format (for backup).

        Args:
            json_path: Optional path to write JSON file

        Returns:
            Dict with items list and export metadata
        """
        suggestions = self.get_all()

        export_data = {
            'items': suggestions,
            'exported_at': datetime.now().isoformat(),
            'count': len(suggestions)
        }

        if json_path:
            with open(json_path, 'w') as f:
                json.dump(export_data, f, indent=2)
            logger.info(f"Exported {len(suggestions)} suggestions to {json_path}")

        return export_data


# =============================================================================
# Module-level singleton and accessor
# =============================================================================

_storage_instance: Optional[SQLiteSuggestionStorage] = None


def get_storage() -> SQLiteSuggestionStorage:
    """Get or create the global storage instance."""
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = SQLiteSuggestionStorage()
    return _storage_instance


def reset_storage() -> None:
    """Reset the global storage instance (useful for testing)."""
    global _storage_instance
    _storage_instance = None


# =============================================================================
# CLI for testing
# =============================================================================

if __name__ == "__main__":
    import sys

    print("=" * 60)
    print("Mission Suggestions SQLite Storage - Self Test")
    print("=" * 60)

    storage = SQLiteSuggestionStorage()

    print(f"\n[1] Database: {storage.db_path}")
    print(f"    Exists: {storage.db_path.exists()}")

    print("\n[2] Getting stats...")
    stats = storage.get_stats()
    print(f"    Total items: {stats['total']}")
    print(f"    By source type: {stats['by_source_type']}")
    print(f"    By health status: {stats['by_health_status']}")
    print(f"    Avg priority: {stats['avg_priority']}")
    if 'db_size_kb' in stats:
        print(f"    DB size: {stats['db_size_kb']} KB")

    print("\n[3] Health report...")
    health = storage.get_health_report()
    print(f"    Counts: {health['counts']}")
    print(f"    Total: {health['total']}")

    if len(sys.argv) > 1 and sys.argv[1] == "--migrate":
        json_path = STATE_DIR / "recommendations.json"
        print(f"\n[4] Migrating from {json_path}...")
        result = storage.migrate_from_json(json_path)
        print(f"    Success: {result['success']}")
        print(f"    Imported: {result['imported']}")
        print(f"    Skipped: {result['skipped']}")
        print(f"    Errors: {result.get('total_errors', 0)}")
        print(f"    Final DB count: {result['db_count']}")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
