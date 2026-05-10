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
import math
import re
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Generator

logger = logging.getLogger(__name__)

# Database location
BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
DB_PATH = STATE_DIR / "mission_suggestions.db"

# Current schema version
SCHEMA_VERSION = 8

# SQLite INTEGER max (2^63 - 1); values beyond this overflow
SQLITE_MAX_INT = (2 ** 63) - 1

ALLOWED_COLUMNS = frozenset({
    'id', 'mission_title', 'mission_description', 'suggested_cycles',
    'source_mission_id', 'source_mission_summary', 'rationale',
    'created_at', 'source_type', 'priority_score', 'health_status',
    'last_analyzed_at', 'last_edited_at', 'auto_tags', 'merged_from',
    'merged_source_descriptions', 'drift_context', 'original_mission_title',
    'original_mission_description', 'original_rationale', 'original_suggested_cycles',
    'classification', 'mission_type', 'bug_references', 'scope_context',
    'execution_profile', 'status', 'accepted_mission_id', 'queued_at',
    'completed_at', 'reopened_at', 'closed_reason', 'project_name',
    'project_slug', 'project_source',
})

# C2-2: defense-in-depth identifier safety. Column names must consist solely of
# alphanumerics and underscores — even after the ALLOWED_COLUMNS whitelist check.
# This prevents injection if ALLOWED_COLUMNS is ever expanded with an unsafe name,
# or if SQLite backtick-quoting semantics change in a future version.
_SAFE_COL_RE = re.compile(r'^[A-Za-z0-9_]+$')

_VALID_CLASSIFICATIONS = frozenset({'BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL'})
_VALID_STATUSES = frozenset({'open', 'queued', 'completed', 'deprecated'})
_VALID_EXEC_PROFILES = frozenset({
    'full_rd', 'plan_only', 'build_only', 'test_red_team',
    'bug_hunt', 'research_only', 'review_existing',
})

_CLASSIFICATION_TO_PROFILE = {
    'BUGFIX': 'bug_hunt',
    'TECH_DEBT': 'build_only',
    'EXPANSION': 'plan_only',
}


def _project_helpers():
    from Mission_Manager.project_registry import (
        canonicalize_project_name,
        infer_project_name,
        project_slug,
    )
    return canonicalize_project_name, infer_project_name, project_slug


def _normalize_insert_defaults(suggestion: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize full-row defaults before validation for every insert path."""
    normalized = dict(suggestion)
    legacy_mission_type = normalized.get('mission_type')
    explicit_invalid_mission_type = False
    explicit_invalid_execution_profile = False
    classification = normalized.get('classification')
    if classification is None and isinstance(legacy_mission_type, str):
        upper_legacy = legacy_mission_type.upper().strip()
        if upper_legacy in _VALID_CLASSIFICATIONS:
            classification = upper_legacy
        elif legacy_mission_type not in _VALID_EXEC_PROFILES:
            explicit_invalid_mission_type = True
    elif legacy_mission_type is not None and not isinstance(legacy_mission_type, str):
        explicit_invalid_mission_type = True
    if classification is None:
        classification = 'EXPANSION'
    normalized['classification'] = classification

    profile = None
    if isinstance(legacy_mission_type, str) and legacy_mission_type in _VALID_EXEC_PROFILES:
        profile = legacy_mission_type
    if profile is None and isinstance(normalized.get('execution_profile'), str) and normalized.get('execution_profile') in _VALID_EXEC_PROFILES:
        profile = normalized.get('execution_profile')
    elif (
        normalized.get('execution_profile') not in (None, "")
        and not (
            isinstance(normalized.get('execution_profile'), str)
            and normalized.get('execution_profile') in _VALID_EXEC_PROFILES
        )
    ):
        explicit_invalid_execution_profile = True
    if profile is None:
        profile = _CLASSIFICATION_TO_PROFILE.get(classification, 'full_rd')
    if not explicit_invalid_mission_type:
        normalized['mission_type'] = profile
    if not explicit_invalid_execution_profile:
        normalized['execution_profile'] = profile

    if normalized.get('status') is None:
        normalized['status'] = 'open'

    try:
        canonicalize_project_name, infer_project_name, project_slug = _project_helpers()
        project_name = canonicalize_project_name(normalized.get('project_name'))
        project_source = normalized.get('project_source')
        if project_name:
            normalized['project_name'] = project_name
            normalized['project_slug'] = project_slug(project_name)
            normalized['project_source'] = project_source or 'explicit'
        else:
            inferred = infer_project_name(normalized)
            normalized['project_name'] = inferred
            normalized['project_slug'] = project_slug(inferred)
            normalized['project_source'] = project_source or 'inferred'
    except Exception:
        if normalized.get('project_name') is None:
            normalized['project_name'] = 'AI-AtlasForge'
            normalized['project_slug'] = 'ai-atlasforge'
            normalized['project_source'] = 'fallback'
    return normalized


def _normalize_partial_update(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize aliases for partial writes without inventing absent defaults."""
    normalized = dict(updates)
    raw_mission_type = normalized.get('mission_type')
    if isinstance(raw_mission_type, str):
        upper = raw_mission_type.upper().strip()
        if upper in _VALID_CLASSIFICATIONS:
            normalized['classification'] = upper
            normalized.pop('mission_type', None)
        elif raw_mission_type in _VALID_EXEC_PROFILES:
            normalized['mission_type'] = raw_mission_type
            normalized['execution_profile'] = raw_mission_type

    raw_execution_profile = normalized.get('execution_profile')
    if isinstance(raw_execution_profile, str) and raw_execution_profile in _VALID_EXEC_PROFILES:
        normalized['mission_type'] = raw_execution_profile

    raw_classification = normalized.get('classification')
    if isinstance(raw_classification, str):
        normalized['classification'] = raw_classification.upper().strip()
    if 'project_name' in normalized:
        try:
            canonicalize_project_name, _, project_slug = _project_helpers()
            project_name = canonicalize_project_name(normalized.get('project_name'))
            normalized['project_name'] = project_name
            normalized['project_slug'] = project_slug(project_name) if project_name else ''
            normalized.setdefault('project_source', 'explicit')
        except Exception:
            pass
    return normalized


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

    _COMPACT_EVERY_N_WRITES = 100

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DB_PATH
        self._write_count = 0
        self._wc_lock = threading.Lock()  # C3-A fix: protect _write_count from concurrent increments
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
        except Exception as e:
            try:
                conn.rollback()
            except Exception as rollback_err:
                raise RuntimeError(f"Rollback also failed: {rollback_err}") from e
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

            if version < 1:
                self._create_schema(conn)
                conn.execute("PRAGMA user_version = 1")
                version = 1
                logger.info("Database schema created at version 1")

            if version < 2:
                self._migrate_v1_to_v2(conn)
                conn.execute("PRAGMA user_version = 2")
                version = 2
                logger.info("Database schema migrated to version 2 (mission_type, bug_references, scope_context)")

            if version < 3:
                self._migrate_v2_to_v3(conn)
                conn.execute("PRAGMA user_version = 3")
                version = 3
                logger.info("Database schema migrated to version 3 (execution_profile)")

            if version < 4:
                self._migrate_v3_to_v4(conn)
                conn.execute("PRAGMA user_version = 4")
                version = 4
                logger.info("Database schema migrated to version 4 (suggestion status)")

            if version < 5:
                self._migrate_v4_to_v5(conn)
                conn.execute("PRAGMA user_version = 5")
                version = 5
                logger.info("Database schema migrated to version 5 (suggestion lifecycle metadata)")

            if version < 6:
                self._migrate_v5_to_v6(conn)
                conn.execute("PRAGMA user_version = 6")
                version = 6
                logger.info("Database schema migrated to version 6 (classification separated from mission_type)")

            if version < 7:
                self._migrate_v6_to_v7(conn)
                conn.execute("PRAGMA user_version = 7")
                version = 7
                logger.info("Database schema migrated to version 7 (deprecated suggestion status)")

            if version < 8:
                self._migrate_v7_to_v8(conn)
                conn.execute("PRAGMA user_version = 8")
                version = 8
                logger.info("Database schema migrated to version 8 (suggestion project identity)")

            # Idempotent backfill: even on DBs already at v3, sweep any rows
            # whose execution_profile is NULL or empty. Cheap (single UPDATE
            # filtered to NULL/'') and protects the UI from blank badges.
            try:
                conn.execute(
                    "UPDATE mission_suggestions SET execution_profile = 'full_rd' "
                    "WHERE execution_profile IS NULL OR execution_profile = ''"
                )
            except sqlite3.OperationalError:
                # Column missing on a pre-v3 DB that failed migration — let
                # the migration error above surface; this is purely defensive.
                pass
            try:
                conn.execute(
                    "UPDATE mission_suggestions SET status = 'open' "
                    "WHERE status IS NULL OR status = ''"
                )
            except sqlite3.OperationalError:
                pass
            try:
                conn.execute(
                    "UPDATE mission_suggestions SET classification = 'EXPANSION' "
                    "WHERE classification IS NULL OR classification = ''"
                )
            except sqlite3.OperationalError:
                pass
            try:
                self._backfill_project_fields(conn)
            except sqlite3.OperationalError:
                pass

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
                classification TEXT DEFAULT 'EXPANSION' CHECK(classification IN ('BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL')),
                status TEXT DEFAULT 'open' CHECK(status IN ('open', 'queued', 'completed', 'deprecated')),
                project_name TEXT DEFAULT 'AI-AtlasForge',
                project_slug TEXT DEFAULT 'ai-atlasforge',
                project_source TEXT DEFAULT 'inferred',
                accepted_mission_id TEXT,
                queued_at TEXT,
                completed_at TEXT,
                reopened_at TEXT,
                closed_reason TEXT,
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
            CREATE INDEX IF NOT EXISTS idx_suggestions_classification
                ON mission_suggestions(classification);
            CREATE INDEX IF NOT EXISTS idx_suggestions_status
                ON mission_suggestions(status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_project_slug
                ON mission_suggestions(project_slug);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestions_accepted_mission
                ON mission_suggestions(accepted_mission_id)
                WHERE accepted_mission_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_suggestions_completed_at
                ON mission_suggestions(completed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_priority
                ON mission_suggestions(priority_score DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_created
                ON mission_suggestions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_source_mission
                ON mission_suggestions(source_mission_id);
        """)

    def _migrate_v1_to_v2(self, conn: sqlite3.Connection) -> None:
        """
        Add mission_type, bug_references, scope_context columns (v1 → v2).

        All three are nullable/defaulted so existing rows are fully backward-compatible.
        Migration sets mission_type='MANUAL' for user-created rows and 'EXPANSION' for
        auto-generated ones, leaving the two new columns as NULL for legacy data.
        """
        for stmt in [
            "ALTER TABLE mission_suggestions ADD COLUMN mission_type TEXT DEFAULT 'EXPANSION'",
            "ALTER TABLE mission_suggestions ADD COLUMN bug_references TEXT DEFAULT '[]'",
            "ALTER TABLE mission_suggestions ADD COLUMN scope_context TEXT DEFAULT NULL",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e):
                    logger.warning("_migrate_v1_to_v2: unexpected error for stmt %r: %s", stmt, e)
                    raise
                # Column already exists — idempotent, safe to continue

        # Fix-up: user-created rows get MANUAL; auto-generated keep EXPANSION
        conn.execute(
            "UPDATE mission_suggestions SET mission_type = 'MANUAL' WHERE source_type = 'manual'"
        )
        conn.execute(
            "UPDATE mission_suggestions SET mission_type = 'EXPANSION' WHERE source_type = 'successful_completion' AND mission_type IS NULL"
        )

    def _migrate_v2_to_v3(self, conn: sqlite3.Connection) -> None:
        """Add execution_profile column (v2 → v3). Existing rows default to 'full_rd'."""
        try:
            conn.execute(
                "ALTER TABLE mission_suggestions ADD COLUMN execution_profile TEXT DEFAULT 'full_rd'"
            )
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e):
                logger.warning("_migrate_v2_to_v3: unexpected error: %s", e)
                raise
        # SQLite's ALTER TABLE ADD COLUMN ... DEFAULT only applies to inserts after
        # the migration; rows that pre-date the column may end up NULL on some
        # SQLite versions. Idempotent backfill so the UI never sees a blank profile.
        conn.execute(
            "UPDATE mission_suggestions SET execution_profile = 'full_rd' "
            "WHERE execution_profile IS NULL OR execution_profile = ''"
        )

    def _migrate_v3_to_v4(self, conn: sqlite3.Connection) -> None:
        """Add suggestion lifecycle status (v3 → v4). Existing rows are open."""
        try:
            conn.execute(
                "ALTER TABLE mission_suggestions ADD COLUMN status TEXT DEFAULT 'open' "
                "CHECK(status IN ('open', 'queued', 'completed'))"
            )
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e):
                logger.warning("_migrate_v3_to_v4: unexpected error: %s", e)
                raise
        conn.execute(
            "UPDATE mission_suggestions SET status = 'open' "
            "WHERE status IS NULL OR status = ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_status "
            "ON mission_suggestions(status)"
        )

    def _migrate_v4_to_v5(self, conn: sqlite3.Connection) -> None:
        """Add lifecycle metadata for accepted/completed suggestions (v4 → v5)."""
        for stmt in [
            "ALTER TABLE mission_suggestions ADD COLUMN accepted_mission_id TEXT",
            "ALTER TABLE mission_suggestions ADD COLUMN queued_at TEXT",
            "ALTER TABLE mission_suggestions ADD COLUMN completed_at TEXT",
            "ALTER TABLE mission_suggestions ADD COLUMN reopened_at TEXT",
            "ALTER TABLE mission_suggestions ADD COLUMN closed_reason TEXT",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e):
                    logger.warning("_migrate_v4_to_v5: unexpected error for stmt %r: %s", stmt, e)
                    raise
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestions_accepted_mission "
            "ON mission_suggestions(accepted_mission_id) "
            "WHERE accepted_mission_id IS NOT NULL"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_completed_at "
            "ON mission_suggestions(completed_at DESC)"
        )

    def _migrate_v5_to_v6(self, conn: sqlite3.Connection) -> None:
        """Separate suggestion classification from execution mission_type (v5 → v6)."""
        try:
            conn.execute(
                "ALTER TABLE mission_suggestions ADD COLUMN classification TEXT DEFAULT 'EXPANSION' "
                "CHECK(classification IN ('BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL'))"
            )
        except sqlite3.OperationalError as e:
            if 'duplicate column name' not in str(e):
                logger.warning("_migrate_v5_to_v6: unexpected classification error: %s", e)
                raise

        conn.execute("""
            UPDATE mission_suggestions
            SET classification = CASE
                WHEN UPPER(COALESCE(mission_type, '')) IN ('BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL')
                    THEN UPPER(mission_type)
                WHEN classification IS NULL OR classification = ''
                    THEN 'EXPANSION'
                ELSE classification
            END
        """)
        conn.execute("""
            UPDATE mission_suggestions
            SET mission_type = CASE
                WHEN classification = 'BUGFIX' AND (execution_profile IS NULL OR execution_profile = '' OR execution_profile = 'full_rd')
                    THEN 'bug_hunt'
                WHEN classification = 'TECH_DEBT' AND (execution_profile IS NULL OR execution_profile = '' OR execution_profile = 'full_rd')
                    THEN 'build_only'
                WHEN classification = 'EXPANSION' AND (execution_profile IS NULL OR execution_profile = '' OR execution_profile = 'full_rd')
                    THEN 'plan_only'
                WHEN execution_profile IN ('full_rd', 'plan_only', 'build_only', 'test_red_team', 'bug_hunt', 'research_only', 'review_existing')
                    THEN execution_profile
                ELSE 'full_rd'
            END
            WHERE mission_type IS NULL
               OR mission_type = ''
               OR UPPER(mission_type) IN ('BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL')
        """)
        conn.execute("""
            UPDATE mission_suggestions
            SET execution_profile = mission_type
            WHERE mission_type IN ('full_rd', 'plan_only', 'build_only', 'test_red_team', 'bug_hunt', 'research_only', 'review_existing')
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_classification "
            "ON mission_suggestions(classification)"
        )

    def _migrate_v6_to_v7(self, conn: sqlite3.Connection) -> None:
        """Expand lifecycle status enum to include deprecated (v6 → v7)."""
        conn.executescript("""
            CREATE TABLE mission_suggestions_v7 (
                id TEXT PRIMARY KEY,
                mission_title TEXT NOT NULL,
                mission_description TEXT,
                suggested_cycles INTEGER DEFAULT 3 CHECK(suggested_cycles >= 1 AND suggested_cycles <= 10),
                source_mission_id TEXT,
                source_mission_summary TEXT,
                rationale TEXT,
                created_at TEXT NOT NULL,
                source_type TEXT DEFAULT 'manual' CHECK(source_type IN ('drift_halt', 'successful_completion', 'merged', 'manual')),
                classification TEXT DEFAULT 'EXPANSION' CHECK(classification IN ('BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL')),
                status TEXT DEFAULT 'open' CHECK(status IN ('open', 'queued', 'completed', 'deprecated')),
                accepted_mission_id TEXT,
                queued_at TEXT,
                completed_at TEXT,
                reopened_at TEXT,
                closed_reason TEXT,
                priority_score REAL DEFAULT 50.0,
                health_status TEXT DEFAULT 'healthy' CHECK(health_status IN ('healthy', 'stale', 'orphaned', 'needs_review', 'hot')),
                last_analyzed_at TEXT,
                last_edited_at TEXT,
                auto_tags TEXT DEFAULT '[]',
                merged_from TEXT,
                merged_source_descriptions TEXT,
                drift_context TEXT,
                original_mission_title TEXT,
                original_mission_description TEXT,
                original_rationale TEXT,
                original_suggested_cycles INTEGER,
                mission_type TEXT,
                bug_references TEXT DEFAULT '[]',
                scope_context TEXT,
                execution_profile TEXT DEFAULT 'full_rd'
            );

            INSERT INTO mission_suggestions_v7 (
                id, mission_title, mission_description, suggested_cycles,
                source_mission_id, source_mission_summary, rationale,
                created_at, source_type, classification, status,
                accepted_mission_id, queued_at, completed_at, reopened_at, closed_reason,
                priority_score, health_status, last_analyzed_at, last_edited_at,
                auto_tags, merged_from, merged_source_descriptions, drift_context,
                original_mission_title, original_mission_description,
                original_rationale, original_suggested_cycles,
                mission_type, bug_references, scope_context, execution_profile
            )
            SELECT
                id, mission_title, mission_description, suggested_cycles,
                source_mission_id, source_mission_summary, rationale,
                created_at, source_type, classification, status,
                accepted_mission_id, queued_at, completed_at, reopened_at, closed_reason,
                priority_score, health_status, last_analyzed_at, last_edited_at,
                auto_tags, merged_from, merged_source_descriptions, drift_context,
                original_mission_title, original_mission_description,
                original_rationale, original_suggested_cycles,
                mission_type, bug_references, scope_context, execution_profile
            FROM mission_suggestions;

            DROP TABLE mission_suggestions;
            ALTER TABLE mission_suggestions_v7 RENAME TO mission_suggestions;

            CREATE INDEX IF NOT EXISTS idx_suggestions_source_type
                ON mission_suggestions(source_type);
            CREATE INDEX IF NOT EXISTS idx_suggestions_health_status
                ON mission_suggestions(health_status);
            CREATE INDEX IF NOT EXISTS idx_suggestions_classification
                ON mission_suggestions(classification);
            CREATE INDEX IF NOT EXISTS idx_suggestions_status
                ON mission_suggestions(status);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_suggestions_accepted_mission
                ON mission_suggestions(accepted_mission_id)
                WHERE accepted_mission_id IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_suggestions_completed_at
                ON mission_suggestions(completed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_priority
                ON mission_suggestions(priority_score DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_created
                ON mission_suggestions(created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_suggestions_source_mission
                ON mission_suggestions(source_mission_id);
        """)

    def _migrate_v7_to_v8(self, conn: sqlite3.Connection) -> None:
        """Add project identity fields (v7 → v8)."""
        for stmt in [
            "ALTER TABLE mission_suggestions ADD COLUMN project_name TEXT DEFAULT 'AI-AtlasForge'",
            "ALTER TABLE mission_suggestions ADD COLUMN project_slug TEXT DEFAULT 'ai-atlasforge'",
            "ALTER TABLE mission_suggestions ADD COLUMN project_source TEXT DEFAULT 'inferred'",
        ]:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if 'duplicate column name' not in str(e):
                    logger.warning("_migrate_v7_to_v8: unexpected error for stmt %r: %s", stmt, e)
                    raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_suggestions_project_slug "
            "ON mission_suggestions(project_slug)"
        )
        self._backfill_project_fields(conn)

    def _backfill_project_fields(self, conn: sqlite3.Connection) -> None:
        """Best-effort project backfill for legacy suggestions."""
        try:
            canonicalize_project_name, infer_project_name, project_slug = _project_helpers()
        except Exception:
            return

        rows = conn.execute(
            "SELECT * FROM mission_suggestions "
            "WHERE project_name IS NULL OR project_name = '' "
            "OR project_slug IS NULL OR project_slug = '' "
            "OR project_source IS NULL OR project_source IN ('inferred', 'backfill', 'fallback')"
        ).fetchall()
        if not rows:
            return

        known_rows = conn.execute("""
            SELECT project_name
            FROM mission_suggestions
            WHERE project_name IS NOT NULL AND project_name != ''
              AND project_source IN ('explicit', 'merged')
        """).fetchall()
        known_projects = [canonicalize_project_name(row["project_name"]) for row in known_rows if row["project_name"]]

        for row in rows:
            suggestion = dict(row)
            source = suggestion.get("project_source") or "inferred"
            if source in {"explicit", "merged"}:
                name = canonicalize_project_name(suggestion.get("project_name"), known_projects)
            else:
                name = ""
            if not name:
                name = infer_project_name(suggestion, known_projects)
                source = "backfill"
            slug = project_slug(name)
            conn.execute(
                "UPDATE mission_suggestions SET project_name = ?, project_slug = ?, project_source = ? WHERE id = ?",
                (name, slug, source, suggestion["id"]),
            )
            known_projects.append(name)

    def _row_to_dict(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        """Convert a database row to a suggestion dict."""
        if row is None:
            return None

        result = dict(row)

        # Parse JSON columns
        for json_col in ['auto_tags', 'merged_from', 'merged_source_descriptions', 'drift_context', 'bug_references']:
            if result.get(json_col):
                try:
                    result[json_col] = json.loads(result[json_col])
                except json.JSONDecodeError:
                    result[json_col] = [] if json_col in ['auto_tags', 'merged_from', 'bug_references'] else None
            elif json_col in ('auto_tags', 'bug_references'):
                result[json_col] = []

        # Preserve all keys including None values for faithful round-tripping
        return result

    def _dict_to_row(self, suggestion: Dict[str, Any]) -> Dict[str, Any]:
        """Convert a suggestion dict to row values with JSON serialization."""
        row = dict(suggestion)

        # Serialize JSON columns
        for json_col in ['auto_tags', 'merged_from', 'merged_source_descriptions', 'drift_context', 'bug_references']:
            if json_col in row and row[json_col] is not None:
                if isinstance(row[json_col], tuple):
                    row[json_col] = list(row[json_col])
                if isinstance(row[json_col], (list, dict)):
                    row[json_col] = json.dumps(row[json_col])

        # Allowlist: reject any column not in the schema to prevent SQL injection.
        # Also apply _SAFE_COL_RE as defense-in-depth (guards if ALLOWED_COLUMNS is
        # ever expanded with a malformed entry).
        unknown = set(row.keys()) - ALLOWED_COLUMNS
        if unknown:
            raise ValueError(f"Unknown column(s) not allowed: {unknown}")
        for col in row.keys():
            if col in ALLOWED_COLUMNS and not _SAFE_COL_RE.match(col):
                raise ValueError(f"Column name contains unsafe characters: {col!r}")
        return {k: v for k, v in row.items() if k in ALLOWED_COLUMNS}

    # =========================================================================
    # Validation Helpers
    # =========================================================================

    def _validate_row(self, suggestion: Dict[str, Any], partial: bool = False) -> None:
        """Validate suggestion fields. Raises ValueError on bad input.

        Called by add()/upsert() for full rows (partial=False, default) and by
        update()/update_batch() for partial patches (partial=True).
        When partial=True, required-field checks are skipped for keys absent
        from the dict -- absence means 'do not change this field'.
        """
        # mission_title: required for full rows; only validated when present in partials
        if 'mission_title' in suggestion or not partial:
            mission_title = suggestion.get('mission_title')
            if mission_title is None:
                raise ValueError(
                    "mission_title is required and cannot be None. "
                    "Provide a non-empty string for mission_title."
                )
            if not isinstance(mission_title, str):
                raise ValueError(
                    f"mission_title must be a string, got: {type(mission_title).__name__}"
                )
            if not mission_title.strip():
                raise ValueError(
                    "mission_title cannot be an empty string. "
                    "Provide a meaningful title for the mission suggestion."
                )

        # source_type enum
        if 'source_type' in suggestion or not partial:
            valid_source_types = ('drift_halt', 'successful_completion', 'merged', 'manual')
            source_type = suggestion.get('source_type', 'manual')
            if source_type not in valid_source_types:
                raise ValueError(
                    f"Invalid source_type '{source_type}'. "
                    f"Must be one of: {', '.join(valid_source_types)}"
                )

        # health_status enum
        if 'health_status' in suggestion or not partial:
            valid_health_statuses = ('healthy', 'stale', 'orphaned', 'needs_review', 'hot')
            health_status = suggestion.get('health_status', 'healthy')
            if health_status not in valid_health_statuses:
                raise ValueError(
                    f"Invalid health_status '{health_status}'. "
                    f"Must be one of: {', '.join(valid_health_statuses)}"
                )

        # lifecycle status enum
        if 'status' in suggestion or not partial:
            status = suggestion.get('status', 'open')
            if status not in _VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{status}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_STATUSES))}"
                )

        # suggested_cycles: int in [1, 10], not bool
        if 'suggested_cycles' in suggestion or not partial:
            suggested_cycles = suggestion.get('suggested_cycles', 3)
            if isinstance(suggested_cycles, bool) or not isinstance(suggested_cycles, int) or not (1 <= suggested_cycles <= 10):
                raise ValueError(
                    f"suggested_cycles must be an integer between 1 and 10, got: {suggested_cycles!r}"
                )

        # JSON-backed fields: accept native structured values only. Storing
        # scalars here makes _row_to_dict return shapes the UI does not expect.
        list_json_fields = (
            'auto_tags',
            'merged_from',
            'merged_source_descriptions',
            'bug_references',
        )
        for field in list_json_fields:
            if field in suggestion:
                value = suggestion.get(field)
                if value is not None and not isinstance(value, (list, tuple)):
                    raise ValueError(f"{field} must be a list")
        if 'drift_context' in suggestion:
            drift_context = suggestion.get('drift_context')
            if drift_context is not None and not isinstance(drift_context, dict):
                raise ValueError("drift_context must be an object")

        # classification enum
        if 'classification' in suggestion or not partial:
            classification = suggestion.get('classification', 'EXPANSION')
            if classification not in _VALID_CLASSIFICATIONS:
                raise ValueError(
                    f"Invalid classification '{classification}'. "
                    f"Must be one of: {', '.join(sorted(_VALID_CLASSIFICATIONS))}"
                )

        # mission_type/execution_profile enum. In this schema, mission_type is
        # the AtlasForge stage profile. execution_profile is kept as a mirror
        # for older callers.
        if 'mission_type' in suggestion or not partial:
            mt = suggestion.get('mission_type', 'full_rd')
            if mt not in _VALID_EXEC_PROFILES:
                raise ValueError(
                    f"Invalid mission_type {mt!r}. "
                    f"Must be one of: {', '.join(sorted(_VALID_EXEC_PROFILES))}"
                )
        if 'execution_profile' in suggestion:
            ep = suggestion.get('execution_profile')
            # Both partial updates AND full-row inserts must reject explicit
            # falsy values. Absence of the key is the only "use default" signal.
            if ep is None or ep == "":
                raise ValueError(
                    "execution_profile cannot be None/empty; "
                    "pass a valid profile or omit the key to use the default"
                )
            try:
                valid = ep in _VALID_EXEC_PROFILES
            except TypeError:
                valid = False
            if not valid:
                raise ValueError(
                    f"Invalid execution_profile {ep!r}. "
                    f"Must be one of: {', '.join(sorted(_VALID_EXEC_PROFILES))}"
                )

        # project identity
        if 'project_name' in suggestion or not partial:
            project_name = suggestion.get('project_name', 'AI-AtlasForge')
            if not isinstance(project_name, str) or not project_name.strip():
                raise ValueError("project_name must be a non-empty string")
            if len(project_name) > 100:
                raise ValueError("project_name must be 100 characters or fewer")
        if 'project_slug' in suggestion:
            project_slug = suggestion.get('project_slug')
            if not isinstance(project_slug, str) or not re.match(r'^[a-z0-9][a-z0-9-]{0,79}$', project_slug):
                raise ValueError("project_slug must be a normalized slug")
        if 'project_source' in suggestion:
            project_source = suggestion.get('project_source')
            if project_source is not None and (
                not isinstance(project_source, str)
                or project_source not in {'explicit', 'inferred', 'backfill', 'fallback', 'merged'}
            ):
                raise ValueError("project_source must be explicit, inferred, backfill, fallback, merged, or null")

        # priority_score: numeric, no NaN/inf, bounded range
        if 'priority_score' in suggestion or not partial:
            priority_score = suggestion.get('priority_score', 50.0)
            if priority_score is None and not partial:
                raise ValueError("priority_score cannot be None for full rows; provide a numeric value")
            if priority_score is not None:
                if isinstance(priority_score, bool):
                    raise ValueError(f"priority_score must be numeric, got: {priority_score!r}")
                try:
                    ps = float(priority_score)
                except (TypeError, ValueError):
                    raise ValueError(f"priority_score must be numeric, got: {priority_score!r}")
                if math.isnan(ps) or math.isinf(ps):
                    raise ValueError(f"priority_score must be a finite number, got: {priority_score!r}")
                if ps < -1000 or ps > 10000:
                    raise ValueError(
                        f"priority_score must be between -1000 and 10000, got: {ps}"
                    )

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
        # Normalize defaults (missing/None → defaults) before validation.
        # classification: missing or None → EXPANSION.
        # mission_type/execution_profile: missing/None/"" → auto-map from classification.
        # Other non-string types (False, 0, [], {}) fall through to _validate_row,
        # which raises a typed ValueError.
        suggestion = _normalize_insert_defaults(suggestion)

        # Validate all fields via shared helper (also used by upsert)
        self._validate_row(suggestion)

        # Reject unknown keys before rebuilding (otherwise silently dropped)
        unknown = set(suggestion.keys()) - ALLOWED_COLUMNS
        if unknown:
            raise ValueError(f"Unknown column(s) not allowed: {unknown}")

        # Generate ID if not provided
        suggestion_id = suggestion.get('id') or f"rec_{uuid.uuid4().hex[:8]}"
        classification = suggestion.get('classification', 'EXPANSION')
        mission_type = suggestion.get('mission_type', 'full_rd')
        project_name = suggestion.get('project_name', 'AI-AtlasForge')
        project_slug_value = suggestion.get('project_slug', 'ai-atlasforge')

        # Build normalized suggestion dict
        now = datetime.now(timezone.utc).isoformat()
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
            'classification': classification,
            'status': suggestion.get('status', 'open'),
            'project_name': project_name,
            'project_slug': project_slug_value,
            'project_source': suggestion.get('project_source', 'inferred'),
            'accepted_mission_id': suggestion.get('accepted_mission_id'),
            'queued_at': suggestion.get('queued_at'),
            'completed_at': suggestion.get('completed_at'),
            'reopened_at': suggestion.get('reopened_at'),
            'closed_reason': suggestion.get('closed_reason'),
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
            'original_suggested_cycles': suggestion.get('original_suggested_cycles'),
            # v2 columns
            'mission_type': mission_type,
            'bug_references': suggestion.get('bug_references', []),
            'scope_context': suggestion.get('scope_context'),
            # v3 column kept as a compatibility mirror for older callers
            'execution_profile': suggestion.get('execution_profile') or mission_type,
        }

        row = self._dict_to_row(suggestion)

        # S1: belt-and-suspenders allowlist check before SQL interpolation
        for col in row.keys():
            if col not in ALLOWED_COLUMNS:
                raise ValueError(f"Column '{col}' not in allowlist")
            # C2-2a: identifier format safety — reject any name with chars outside [A-Za-z0-9_]
            if not _SAFE_COL_RE.match(col):
                raise ValueError(f"Column '{col}' contains unsafe characters")

        with self._get_connection() as conn:
            columns = ', '.join(f'`{col}`' for col in row.keys())
            placeholders = ', '.join(['?' for _ in row])
            conn.execute(
                f"INSERT INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                list(row.values())
            )
            logger.debug(f"Added suggestion: {suggestion_id}")

        with self._wc_lock:
            self._write_count += 1
            should_compact = (self._write_count % self._COMPACT_EVERY_N_WRITES == 0)
        if should_compact:
            try:
                self.compact()
            except Exception as _compact_err:
                logger.warning("compact() failed (WAL may grow): %s", _compact_err)
        return suggestion_id

    def update(self, suggestion_id: str, updates: Dict[str, Any]) -> bool:
        """Update a suggestion. Returns True if found and updated."""
        # Work on a copy so we don't mutate the caller's dict
        updates = dict(updates)
        updates.pop('id', None)
        updates = _normalize_partial_update(updates)

        if not updates:
            return False

        self._validate_row(updates, partial=True)
        row_updates = self._dict_to_row(updates)

        # S2: belt-and-suspenders allowlist check before SQL interpolation
        for col in row_updates.keys():
            if col not in ALLOWED_COLUMNS:
                raise ValueError(f"Column '{col}' not in allowlist")
            # C2-2b: identifier format safety — same defense as INSERT path
            if not _SAFE_COL_RE.match(col):
                raise ValueError(f"Column '{col}' contains unsafe characters")

        with self._get_connection() as conn:
            set_clause = ', '.join([f'`{k}` = ?' for k in row_updates.keys()])
            cursor = conn.execute(
                f"UPDATE mission_suggestions SET {set_clause} WHERE id = ?",
                list(row_updates.values()) + [suggestion_id]
            )
            updated = cursor.rowcount > 0
            if updated:
                logger.debug(f"Updated suggestion: {suggestion_id}")
                with self._wc_lock:
                    self._write_count += 1
                    should_compact = (self._write_count % self._COMPACT_EVERY_N_WRITES == 0)
                if should_compact:
                    try:
                        self.compact()
                    except Exception as _compact_err:
                        logger.warning("compact() failed after update (WAL may grow): %s", _compact_err)
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
                with self._wc_lock:
                    self._write_count += 1
                    should_compact = (self._write_count % self._COMPACT_EVERY_N_WRITES == 0)
                if should_compact:
                    try:
                        self.compact()
                    except Exception as _compact_err:
                        logger.warning("compact() failed after delete (WAL may grow): %s", _compact_err)
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
        # Red team fix: delete_multiple also triggers compaction.
        # C3-iter2 Fix: increment by actual count (not 1) to match upsert_batch() behavior.
        if count > 0:
            with self._wc_lock:
                old = self._write_count
                self._write_count += count
                new = self._write_count
                n = self._COMPACT_EVERY_N_WRITES
                should_compact = (new // n) > (old // n)
            if should_compact:
                try:
                    self.compact()
                except Exception as _compact_err:
                    logger.warning("compact() failed after delete_multiple (WAL may grow): %s", _compact_err)
        return count

    def compact(self) -> None:
        """Truncate WAL file to prevent unbounded growth (best-effort)."""
        with self._get_connection() as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # =========================================================================
    # Filtered Queries
    # =========================================================================

    def get_filtered(
        self,
        source_type: str = None,
        health_status: str = None,
        status: str = None,
        project_slug: str = None,
        project_name: str = None,
        min_priority: float = None,
        max_priority: float = None,
        limit: int = None,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get suggestions with optional filters.

        limit: maximum rows to return. 0 returns empty list; None returns all rows.
            Negative values raise ValueError.
        offset: number of rows to skip (default 0).
        """
        conditions = []
        params = []

        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)

        if health_status is not None:
            conditions.append("health_status = ?")
            params.append(health_status)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

        if project_slug is not None:
            conditions.append("project_slug = ?")
            params.append(project_slug)
        elif project_name is not None:
            try:
                _, _, _project_slug = _project_helpers()
                conditions.append("project_slug = ?")
                params.append(_project_slug(project_name))
            except Exception:
                conditions.append("project_name = ?")
                params.append(project_name)

        if min_priority is not None:
            conditions.append("priority_score >= ?")
            params.append(min_priority)

        if max_priority is not None:
            conditions.append("priority_score <= ?")
            params.append(max_priority)

        # Bug 20: reject non-int limit/offset (float, string, etc.)
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
            raise ValueError(f"limit must be an integer or None, got: {type(limit).__name__}")
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise ValueError(f"offset must be an integer, got: {type(offset).__name__}")
        # HIGH-1: reject negative limit/offset
        if limit is not None and limit < 0:
            raise ValueError(f"limit must be non-negative, got: {limit}")
        if offset < 0:
            raise ValueError(f"offset must be non-negative, got: {offset}")
        # HIGH-2: reject values that overflow SQLite INTEGER
        if limit is not None and limit > SQLITE_MAX_INT:
            raise ValueError(f"limit exceeds SQLite max integer ({SQLITE_MAX_INT}): {limit}")
        if offset > SQLITE_MAX_INT:
            raise ValueError(f"offset exceeds SQLite max integer ({SQLITE_MAX_INT}): {offset}")

        query = "SELECT * FROM mission_suggestions"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY priority_score DESC"

        if limit == 0:
            return []

        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([int(limit), int(offset)])
        elif offset:
            query += " LIMIT -1 OFFSET ?"
            params.append(int(offset))

        with self._get_connection() as conn:
            cursor = conn.execute(query, params)
            return [self._row_to_dict(row) for row in cursor.fetchall()]

    def get_projects(self, status: str = None) -> List[Dict[str, Any]]:
        """Return canonical projects represented in the suggestion DB."""
        conditions = [
            "project_name IS NOT NULL",
            "project_name != ''",
            "project_slug IS NOT NULL",
            "project_slug != ''",
        ]
        params = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        query = f"""
            SELECT project_name, project_slug, COUNT(*) AS count
            FROM mission_suggestions
            WHERE {' AND '.join(conditions)}
            GROUP BY project_slug
            ORDER BY LOWER(project_name)
        """
        with self._get_connection() as conn:
            rows = conn.execute(query, params).fetchall()
            return [
                {
                    "project_name": row["project_name"],
                    "project_slug": row["project_slug"],
                    "count": row["count"],
                }
                for row in rows
            ]

    def count(self, health_status: str = None, source_type: str = None, status: str = None) -> int:
        """Count suggestions with optional filters."""
        conditions = []
        params = []

        if health_status is not None:
            conditions.append("health_status = ?")
            params.append(health_status)

        if source_type is not None:
            conditions.append("source_type = ?")
            params.append(source_type)

        if status is not None:
            conditions.append("status = ?")
            params.append(status)

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
                'needs_analysis': total > 0 and conn.execute(
                    "SELECT COUNT(*) FROM mission_suggestions WHERE last_analyzed_at IS NULL"
                ).fetchone()[0] > 0
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
            stats['avg_priority'] = round(avg_priority, 2) if avg_priority is not None else 0

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

        WARNING: INSERT OR REPLACE deletes the old row and inserts a new one,
        so any columns not included in `suggestion` will revert to their
        DEFAULT values (not the previously stored values). Callers must pass
        all columns they want preserved. For partial updates, use update()
        instead.
        """
        suggestion = _normalize_insert_defaults(suggestion)

        # Generate ID if not provided
        suggestion_id = suggestion.get('id') or f"rec_{uuid.uuid4().hex[:8]}"
        suggestion['id'] = suggestion_id

        # Ensure required fields have defaults (only when key is absent — not when explicitly None)
        now = datetime.now(timezone.utc).isoformat()
        if 'created_at' not in suggestion:
            suggestion['created_at'] = now
        if 'mission_title' not in suggestion:
            suggestion['mission_title'] = 'Untitled'

        # HIGH-3: validate via shared helper (same rules as add()) AFTER injecting absent-key defaults
        # but BEFORE SQL construction — catches mission_title=None (explicit null) which was previously
        # silently overwritten to 'Untitled' by the old default-injection block.
        self._validate_row(suggestion)

        row = self._dict_to_row(suggestion)

        # S-upsert: belt-and-suspenders allowlist check before SQL interpolation
        for col in row.keys():
            if col not in ALLOWED_COLUMNS:
                raise ValueError(f"Column '{col}' not in allowlist")
            if not _SAFE_COL_RE.match(col):
                raise ValueError(f"Column '{col}' contains unsafe characters")

        with self._get_connection() as conn:
            columns = ', '.join(f'`{col}`' for col in row.keys())
            placeholders = ', '.join(['?' for _ in row])
            conn.execute(
                f"INSERT OR REPLACE INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                list(row.values())
            )

        # Red team fix: upsert also triggers compaction
        with self._wc_lock:
            self._write_count += 1
            should_compact = (self._write_count % self._COMPACT_EVERY_N_WRITES == 0)
        if should_compact:
            try:
                self.compact()
            except Exception as _compact_err:
                logger.warning("compact() failed (WAL may grow): %s", _compact_err)
        return suggestion_id

    def upsert_batch(self, suggestions: List[Dict[str, Any]]) -> int:
        """Upsert multiple suggestions safely.

        Unlike update_all(), this does NOT delete existing records.
        Only updates/inserts the provided records.
        Returns count of upserted records.
        """
        count = 0
        now = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            for i, orig in enumerate(suggestions):
                try:
                    suggestion = _normalize_insert_defaults(orig)

                    if 'id' not in suggestion:
                        suggestion['id'] = f"rec_{uuid.uuid4().hex[:8]}"
                    if 'created_at' not in suggestion:
                        suggestion['created_at'] = now
                    if 'mission_title' not in suggestion:
                        suggestion['mission_title'] = 'Untitled'

                    # Validate AFTER absent-key defaults but BEFORE SQL construction — catches
                    # mission_title=None (explicit null) which was previously silently overwritten.
                    self._validate_row(suggestion)

                    row = self._dict_to_row(suggestion)

                    # S-upsert-batch: belt-and-suspenders allowlist check before SQL interpolation
                    for col in row.keys():
                        if col not in ALLOWED_COLUMNS:
                            raise ValueError(f"Column '{col}' not in allowlist")
                        if not _SAFE_COL_RE.match(col):
                            raise ValueError(f"Column '{col}' contains unsafe characters")

                    columns = ', '.join(f'`{col}`' for col in row.keys())
                    placeholders = ', '.join(['?' for _ in row])
                    conn.execute(
                        f"INSERT OR REPLACE INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                        list(row.values())
                    )
                    count += 1
                except (ValueError, TypeError) as _batch_err:
                    raise ValueError(f"Item {i}: {_batch_err}") from _batch_err

        logger.info(f"Upserted {count} suggestions (safe batch)")
        # Red team fix: upsert_batch triggers compaction proportional to batch size
        if count > 0:
            with self._wc_lock:
                old = self._write_count
                self._write_count += count
                new = self._write_count
                n = self._COMPACT_EVERY_N_WRITES
                should_compact = (new // n) > (old // n)
            if should_compact:
                try:
                    self.compact()
                except Exception as _compact_err:
                    logger.warning("compact() failed after upsert_batch (WAL may grow): %s", _compact_err)
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
        now = datetime.now(timezone.utc).isoformat()
        with self._get_connection() as conn:
            # Disable sqlite3 implicit transaction management so we can issue
            # BEGIN IMMEDIATE explicitly, making the delete+insert atomic.
            conn.isolation_level = None
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Clear existing
                conn.execute("DELETE FROM mission_suggestions")

                # Insert all
                for orig in suggestions:
                    suggestion = _normalize_insert_defaults(orig)
                    # Ensure required fields have defaults
                    if 'created_at' not in suggestion:
                        suggestion['created_at'] = now
                    if 'id' not in suggestion:
                        suggestion['id'] = f"rec_{uuid.uuid4().hex[:8]}"
                    if 'mission_title' not in suggestion:
                        suggestion['mission_title'] = 'Untitled'

                    self._validate_row(suggestion)  # enforce validation (Bug 3 fix)

                    row = self._dict_to_row(suggestion)
                    for col in row.keys():
                        if not _SAFE_COL_RE.match(col):
                            raise ValueError(f"update_all: invalid column name '{col}'")
                    columns = ', '.join(f'`{col}`' for col in row.keys())
                    placeholders = ', '.join(['?' for _ in row])
                    conn.execute(
                        f"INSERT INTO mission_suggestions ({columns}) VALUES ({placeholders})",
                        list(row.values())
                    )
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise

            logger.info(f"Bulk updated {len(suggestions)} suggestions")
            if suggestions:
                with self._wc_lock:
                    old = self._write_count
                    self._write_count += len(suggestions)
                    new = self._write_count
                    n = self._COMPACT_EVERY_N_WRITES
                    should_compact = (new // n) > (old // n)
                if should_compact:
                    try:
                        self.compact()
                    except Exception as _compact_err:
                        logger.warning("compact() failed after update_all (WAL may grow): %s", _compact_err)
            return len(suggestions)

    def update_batch(self, updates: List[Dict[str, Any]]) -> int:
        """Update multiple suggestions. Each dict must have 'id' field."""
        updated = 0
        with self._get_connection() as conn:
            for update in updates:
                update = dict(update)
                suggestion_id = update.pop('id', None)
                if not suggestion_id or not update:
                    continue

                update = _normalize_partial_update(update)
                self._validate_row(update, partial=True)
                row_updates = self._dict_to_row(update)
                for col in row_updates.keys():
                    if not _SAFE_COL_RE.match(col):
                        raise ValueError(f"update_batch: invalid column name '{col}'")
                set_clause = ', '.join([f'`{k}` = ?' for k in row_updates.keys()])
                cursor = conn.execute(
                    f"UPDATE mission_suggestions SET {set_clause} WHERE id = ?",
                    list(row_updates.values()) + [suggestion_id]
                )
                updated += cursor.rowcount

        logger.info(f"Batch updated {updated} suggestions")

        # C3-iter2 Fix: update_batch was the only write method that never triggered
        # WAL compaction. Add proportional _write_count increment to match all other
        # write methods (add, update, delete, delete_multiple, upsert, upsert_batch).
        if updated > 0:
            with self._wc_lock:
                old = self._write_count
                self._write_count += updated
                new = self._write_count
                n = self._COMPACT_EVERY_N_WRITES
                should_compact = (new // n) > (old // n)
            if should_compact:
                try:
                    self.compact()
                except Exception as _compact_err:
                    logger.warning("compact() failed after update_batch (WAL may grow): %s", _compact_err)

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

        if not isinstance(data, dict):
            return {
                'success': False,
                'error': f"Expected JSON object at root, got {type(data).__name__}",
                'imported': 0
            }

        items = data.get('items', [])
        if items is None:
            return {
                'success': False,
                'error': 'items field is None',
                'imported': 0
            }
        if not isinstance(items, list):
            return {
                'success': False,
                'error': f'items must be a list, got {type(items).__name__}',
                'imported': 0
            }
        if not items:
            return {
                'success': True,
                'imported': 0,
                'message': 'No items to migrate'
            }

        imported = 0
        skipped = 0
        errors = []
        before_count = self.count()

        with self._get_connection() as conn:
            for item in items:
                try:
                    if not isinstance(item, dict):
                        errors.append({
                            'id': repr(item)[:50],
                            'error': f"Expected dict, got {type(item).__name__}"
                        })
                        continue

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
                        'created_at': item.get('created_at', datetime.now(timezone.utc).isoformat()),
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
                        'original_suggested_cycles': item.get('original_suggested_cycles'),
                        'execution_profile': item.get('execution_profile'),
                        'classification': item.get('classification'),
                        'mission_type': item.get('mission_type', 'EXPANSION'),
                    }

                    suggestion = _normalize_insert_defaults(suggestion)

                    # S3: validate field values before insertion
                    self._validate_row(suggestion)

                    row = self._dict_to_row(suggestion)
                    columns = ', '.join(f'`{col}`' for col in row.keys())
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
        expected_count = before_count + imported

        result = {
            'success': True,
            'imported': imported,
            'skipped': skipped,
            'errors': errors[:10] if errors else [],
            'total_errors': len(errors),
            'json_count': len(items),
            'db_count_before': before_count,
            'db_count': final_count,
            'expected_db_count': expected_count,
            'counts_match': final_count == expected_count
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
            'exported_at': datetime.now(timezone.utc).isoformat(),
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
_storage_lock = threading.Lock()


def get_storage() -> SQLiteSuggestionStorage:
    """Get or create the global storage instance (thread-safe)."""
    global _storage_instance
    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                _storage_instance = SQLiteSuggestionStorage()
    return _storage_instance


def reset_storage() -> None:
    """Reset the global storage instance (useful for testing)."""
    global _storage_instance
    with _storage_lock:
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
