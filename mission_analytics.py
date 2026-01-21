#!/usr/bin/env python3
"""
Mission Analytics & Cost Tracking System

This module provides comprehensive analytics for AtlasForge missions by:
1. Tracking token usage (input, output, cache read, cache write)
2. Estimating API costs based on current model pricing
3. Recording stage-by-stage timing metrics
4. Providing per-mission and aggregate statistics
5. Exporting data in JSON format for dashboard integration

Usage:
    analytics = MissionAnalytics()

    # Track stage timing
    analytics.start_stage("mission_123", "PLANNING")
    # ... stage execution ...
    analytics.end_stage("mission_123", "PLANNING")

    # Record token usage (from transcript parsing)
    analytics.record_token_usage("mission_123", "PLANNING", {
        "input_tokens": 5000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 1000
    })

    # Get mission summary
    summary = analytics.get_mission_summary("mission_123")

    # Get aggregate stats
    stats = analytics.get_aggregate_stats()
"""

import json
import sqlite3
import logging
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# Paths - use centralized configuration
from atlasforge_config import ANALYTICS_DIR, MISSIONS_DIR, ARTIFACTS_DIR
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"
TRANSCRIPTS_DIR = ARTIFACTS_DIR / "transcripts"

# Claude transcript directories (live, not archived)
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Ensure directories exist
ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

# Model pricing (per 1M tokens) as of Dec 2025
# Source: https://www.anthropic.com/pricing
MODEL_PRICING = {
    "claude-opus-4-5-20251101": {
        "input": 15.00,
        "output": 75.00,
        "cache_read": 1.50,   # 90% discount
        "cache_write": 18.75  # 25% premium
    },
    "claude-sonnet-4-20250514": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75
    },
    "claude-sonnet-4-5-20250514": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75
    },
    "claude-haiku-4-5-20251001": {
        "input": 0.80,
        "output": 4.00,
        "cache_read": 0.08,
        "cache_write": 1.00
    },
    # Default fallback for unknown models
    "default": {
        "input": 3.00,
        "output": 15.00,
        "cache_read": 0.30,
        "cache_write": 3.75
    }
}


@dataclass
class StageMetrics:
    """Metrics for a single stage execution"""
    mission_id: str
    stage: str
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    duration_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    model: str = "unknown"
    iteration: int = 0
    cycle: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'StageMetrics':
        return cls(**data)


@dataclass
class MissionMetrics:
    """Aggregate metrics for an entire mission"""
    mission_id: str
    problem_statement: str = ""
    started_at: Optional[str] = None
    ended_at: Optional[str] = None
    total_duration_seconds: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    total_tokens: int = 0
    total_estimated_cost_usd: float = 0.0
    stages_completed: int = 0
    cycles_completed: int = 0
    final_status: str = "unknown"
    stages: Dict[str, StageMetrics] = field(default_factory=dict)

    def to_dict(self) -> dict:
        result = asdict(self)
        # Convert nested StageMetrics to dicts
        result["stages"] = {k: v.to_dict() if hasattr(v, 'to_dict') else v
                           for k, v in self.stages.items()}
        return result


class MissionAnalytics:
    """
    Comprehensive analytics tracking for AtlasForge missions.

    Uses SQLite for persistent storage and provides methods for:
    - Tracking stage timing and token usage
    - Estimating API costs
    - Querying historical data
    - Generating dashboard-ready JSON exports
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize the analytics system.

        Args:
            storage_path: Path to store the database (default: ANALYTICS_DIR)
        """
        self.storage_path = storage_path or ANALYTICS_DIR
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_path / "mission_analytics.db"
        self._init_db()

    def _init_db(self):
        """Initialize SQLite database with required tables"""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Mission summary table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mission_metrics (
                    mission_id TEXT PRIMARY KEY,
                    problem_statement TEXT,
                    started_at TEXT,
                    ended_at TEXT,
                    total_duration_seconds REAL DEFAULT 0,
                    total_input_tokens INTEGER DEFAULT 0,
                    total_output_tokens INTEGER DEFAULT 0,
                    total_cache_read_tokens INTEGER DEFAULT 0,
                    total_cache_write_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    total_estimated_cost_usd REAL DEFAULT 0,
                    stages_completed INTEGER DEFAULT 0,
                    cycles_completed INTEGER DEFAULT 0,
                    final_status TEXT DEFAULT 'unknown'
                )
            """)

            # Stage-level metrics table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS stage_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    started_at TEXT,
                    ended_at TEXT,
                    duration_seconds REAL DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    total_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd REAL DEFAULT 0,
                    model TEXT DEFAULT 'unknown',
                    iteration INTEGER DEFAULT 0,
                    cycle INTEGER DEFAULT 1,
                    FOREIGN KEY (mission_id) REFERENCES mission_metrics(mission_id)
                )
            """)

            # Token usage events (granular tracking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS token_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    model TEXT,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    cache_read_tokens INTEGER DEFAULT 0,
                    cache_write_tokens INTEGER DEFAULT 0,
                    request_id TEXT,
                    FOREIGN KEY (mission_id) REFERENCES mission_metrics(mission_id)
                )
            """)

            # Create indexes for common queries
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stage_mission ON stage_metrics(mission_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_stage_stage ON stage_metrics(stage)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_mission ON token_events(mission_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON token_events(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_mission_started ON mission_metrics(started_at)")

            # Add unique index on (mission_id, request_id) to prevent duplicates
            # This handles watcher restarts where in-memory deduplication resets
            self._add_deduplication_index(cursor)

            conn.commit()
        finally:
            conn.close()

    def _add_deduplication_index(self, cursor):
        """
        Add unique index on (mission_id, request_id) for deduplication.

        Handles existing duplicates by keeping only the first occurrence.
        Uses a partial index that excludes NULL/empty request_ids.
        """
        # Check if index already exists
        cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='index' AND name='idx_events_unique_request'
        """)
        if cursor.fetchone():
            return  # Already exists

        # First, clean up existing duplicates by keeping only the first occurrence
        # For each (mission_id, request_id), keep the row with the lowest id
        cursor.execute("""
            DELETE FROM token_events
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM token_events
                WHERE request_id IS NOT NULL AND request_id != ''
                GROUP BY mission_id, request_id
            )
            AND request_id IS NOT NULL
            AND request_id != ''
        """)
        deleted_count = cursor.rowcount
        if deleted_count > 0:
            logger.info(f"Cleaned up {deleted_count} duplicate token_events")

        # Create unique index on (mission_id, request_id) for non-null request_ids
        # SQLite doesn't support partial indexes with WHERE in the same way as PostgreSQL,
        # but we can create a unique index that treats NULLs as distinct
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_events_unique_request
            ON token_events(mission_id, request_id)
            WHERE request_id IS NOT NULL AND request_id != ''
        """)
        logger.info("Created unique index idx_events_unique_request on token_events")

    # =========================================================================
    # MISSION LIFECYCLE
    # =========================================================================

    def start_mission(self, mission_id: str, problem_statement: str = "") -> None:
        """
        Record the start of a new mission.

        Args:
            mission_id: Unique mission identifier
            problem_statement: The mission's problem statement
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO mission_metrics
                (mission_id, problem_statement, started_at, final_status)
                VALUES (?, ?, ?, 'in_progress')
            """, (mission_id, problem_statement, datetime.now().isoformat()))
            conn.commit()
            logger.info(f"Analytics: Started tracking mission {mission_id}")
        finally:
            conn.close()

    def end_mission(self, mission_id: str, status: str = "complete") -> None:
        """
        Record the end of a mission and calculate totals.

        Args:
            mission_id: Unique mission identifier
            status: Final status (complete, failed, abandoned)
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Get current mission data
            cursor.execute("SELECT started_at FROM mission_metrics WHERE mission_id = ?",
                          (mission_id,))
            row = cursor.fetchone()

            ended_at = datetime.now()
            duration = 0.0
            if row and row[0]:
                try:
                    started_at = datetime.fromisoformat(row[0])
                    duration = (ended_at - started_at).total_seconds()
                except (ValueError, TypeError):
                    pass

            # Calculate totals from stage metrics
            cursor.execute("""
                SELECT
                    SUM(input_tokens), SUM(output_tokens),
                    SUM(cache_read_tokens), SUM(cache_write_tokens),
                    SUM(total_tokens), SUM(estimated_cost_usd),
                    COUNT(DISTINCT stage), MAX(cycle)
                FROM stage_metrics WHERE mission_id = ?
            """, (mission_id,))
            totals = cursor.fetchone()

            # If stage_metrics has no token data, fall back to token_events
            if not totals[4] or totals[4] == 0:
                cursor.execute("""
                    SELECT
                        SUM(input_tokens), SUM(output_tokens),
                        SUM(cache_read_tokens), SUM(cache_write_tokens)
                    FROM token_events WHERE mission_id = ?
                """, (mission_id,))
                event_totals = cursor.fetchone()
                if event_totals and event_totals[0]:
                    # Calculate cost from token_events
                    in_tok = event_totals[0] or 0
                    out_tok = event_totals[1] or 0
                    cache_read = event_totals[2] or 0
                    cache_write = event_totals[3] or 0
                    total_tok = in_tok + out_tok + cache_read + cache_write
                    cost = self.estimate_cost(in_tok, out_tok, cache_read, cache_write)
                    # Override totals with token_events data
                    totals = (in_tok, out_tok, cache_read, cache_write, total_tok, cost, totals[6] or 0, totals[7] or 0)

            cursor.execute("""
                UPDATE mission_metrics SET
                    ended_at = ?,
                    total_duration_seconds = ?,
                    total_input_tokens = ?,
                    total_output_tokens = ?,
                    total_cache_read_tokens = ?,
                    total_cache_write_tokens = ?,
                    total_tokens = ?,
                    total_estimated_cost_usd = ?,
                    stages_completed = ?,
                    cycles_completed = ?,
                    final_status = ?
                WHERE mission_id = ?
            """, (
                ended_at.isoformat(),
                duration,
                totals[0] or 0,
                totals[1] or 0,
                totals[2] or 0,
                totals[3] or 0,
                totals[4] or 0,
                totals[5] or 0.0,
                totals[6] or 0,
                totals[7] or 0,
                status,
                mission_id
            ))
            conn.commit()
            logger.info(f"Analytics: Mission {mission_id} ended with status {status}")
        finally:
            conn.close()

    # =========================================================================
    # STAGE TRACKING
    # =========================================================================

    def start_stage(self, mission_id: str, stage: str,
                    iteration: int = 0, cycle: int = 1) -> int:
        """
        Record the start of a stage.

        Args:
            mission_id: Mission identifier
            stage: Stage name (PLANNING, BUILDING, etc.)
            iteration: Current iteration number
            cycle: Current cycle number

        Returns:
            The stage_id for this record
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stage_metrics
                (mission_id, stage, started_at, iteration, cycle)
                VALUES (?, ?, ?, ?, ?)
            """, (mission_id, stage, datetime.now().isoformat(), iteration, cycle))
            conn.commit()
            stage_id = cursor.lastrowid
            logger.debug(f"Analytics: Started stage {stage} for mission {mission_id}")
            return stage_id
        finally:
            conn.close()

    def end_stage(self, mission_id: str, stage: str,
                  iteration: int = 0, cycle: int = 1) -> None:
        """
        Record the end of a stage and calculate duration.

        Args:
            mission_id: Mission identifier
            stage: Stage name
            iteration: Current iteration
            cycle: Current cycle
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Find the matching stage record
            cursor.execute("""
                SELECT id, started_at FROM stage_metrics
                WHERE mission_id = ? AND stage = ? AND iteration = ? AND cycle = ?
                ORDER BY id DESC LIMIT 1
            """, (mission_id, stage, iteration, cycle))
            row = cursor.fetchone()

            if row:
                stage_id, started_at = row
                ended_at = datetime.now()
                duration = 0.0
                if started_at:
                    try:
                        started_dt = datetime.fromisoformat(started_at)
                        duration = (ended_at - started_dt).total_seconds()
                    except (ValueError, TypeError):
                        pass

                cursor.execute("""
                    UPDATE stage_metrics SET ended_at = ?, duration_seconds = ?
                    WHERE id = ?
                """, (ended_at.isoformat(), duration, stage_id))
                conn.commit()
                logger.debug(f"Analytics: Ended stage {stage} (duration: {duration:.1f}s)")
        finally:
            conn.close()

    # =========================================================================
    # TOKEN TRACKING
    # =========================================================================

    def record_token_usage(self, mission_id: str, stage: str,
                           usage: Dict[str, Any], model: str = "unknown",
                           request_id: str = None) -> bool:
        """
        Record token usage from an API call.

        Uses INSERT OR IGNORE to silently skip duplicates when the same
        (mission_id, request_id) combination is already recorded. This
        handles watcher restarts gracefully.

        Args:
            mission_id: Mission identifier
            stage: Current stage
            usage: Dict with token counts (input_tokens, output_tokens, etc.)
            model: Model name for cost calculation
            request_id: Optional request ID for correlation

        Returns:
            True if the event was recorded, False if it was a duplicate
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", 0)
            cache_write = usage.get("cache_creation_input_tokens", 0)

            # Record the event using INSERT OR IGNORE to skip duplicates
            # The unique index on (mission_id, request_id) prevents duplicates
            cursor.execute("""
                INSERT OR IGNORE INTO token_events
                (mission_id, stage, timestamp, model, input_tokens, output_tokens,
                 cache_read_tokens, cache_write_tokens, request_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                mission_id, stage, datetime.now().isoformat(), model,
                input_tokens, output_tokens, cache_read, cache_write, request_id
            ))

            # Check if the insert actually happened (rowcount > 0)
            was_inserted = cursor.rowcount > 0

            if was_inserted:
                # Calculate cost
                cost = self.estimate_cost(input_tokens, output_tokens,
                                         cache_read, cache_write, model)

                # Update stage totals (for most recent matching stage record)
                cursor.execute("""
                    UPDATE stage_metrics SET
                        input_tokens = input_tokens + ?,
                        output_tokens = output_tokens + ?,
                        cache_read_tokens = cache_read_tokens + ?,
                        cache_write_tokens = cache_write_tokens + ?,
                        total_tokens = total_tokens + ?,
                        estimated_cost_usd = estimated_cost_usd + ?,
                        model = ?
                    WHERE id = (
                        SELECT id FROM stage_metrics
                        WHERE mission_id = ? AND stage = ?
                        ORDER BY id DESC LIMIT 1
                    )
                """, (
                    input_tokens, output_tokens, cache_read, cache_write,
                    input_tokens + output_tokens + cache_read + cache_write,
                    cost, model, mission_id, stage
                ))
            else:
                logger.debug(f"Skipped duplicate token event: mission={mission_id}, request_id={request_id}")

            conn.commit()
            return was_inserted
        finally:
            conn.close()

    def estimate_cost(self, input_tokens: int, output_tokens: int,
                     cache_read: int = 0, cache_write: int = 0,
                     model: str = "default") -> float:
        """
        Estimate the cost of API usage.

        Args:
            input_tokens: Input token count
            output_tokens: Output token count
            cache_read: Cache read token count
            cache_write: Cache write token count
            model: Model name

        Returns:
            Estimated cost in USD
        """
        # Try to get pricing from provider_config first (multi-provider support)
        pricing = None
        try:
            import provider_config
            # Check all providers for this model
            for provider, models in provider_config.PRICING_MAP.items():
                if model in models:
                    pricing = models[model]
                    break
        except Exception as e:
            logger.debug(f"Could not load provider_config pricing: {e}")

        # Fall back to legacy MODEL_PRICING
        if not pricing:
            pricing = MODEL_PRICING.get(model, MODEL_PRICING["default"])

        # Calculate cost (pricing is per 1M tokens)
        # Handle both new format (input/output only) and legacy format (with cache fields)
        cost = (
            (input_tokens / 1_000_000) * pricing.get("input", 0) +
            (output_tokens / 1_000_000) * pricing.get("output", 0) +
            (cache_read / 1_000_000) * pricing.get("cache_read", 0) +
            (cache_write / 1_000_000) * pricing.get("cache_write", 0)
        )

        return round(cost, 6)

    # =========================================================================
    # TRANSCRIPT INGESTION
    # =========================================================================

    def ingest_transcript(self, transcript_path: Path, mission_id: str,
                          stage: str = "unknown") -> Dict[str, Any]:
        """
        Parse a transcript file and record all token usage.

        Args:
            transcript_path: Path to .jsonl transcript file
            mission_id: Mission to attribute usage to
            stage: Stage name

        Returns:
            Dict with total usage stats
        """
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "records_processed": 0,
            "cost_usd": 0.0
        }

        try:
            with open(transcript_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        if record.get("type") == "assistant":
                            msg = record.get("message", {})
                            usage = msg.get("usage", {})
                            model = msg.get("model", "unknown")
                            request_id = record.get("requestId")

                            if usage:
                                self.record_token_usage(
                                    mission_id, stage, usage,
                                    model=model, request_id=request_id
                                )
                                totals["input_tokens"] += usage.get("input_tokens", 0)
                                totals["output_tokens"] += usage.get("output_tokens", 0)
                                totals["cache_read_tokens"] += usage.get("cache_read_input_tokens", 0)
                                totals["cache_write_tokens"] += usage.get("cache_creation_input_tokens", 0)
                                totals["records_processed"] += 1
                    except json.JSONDecodeError:
                        continue

            totals["cost_usd"] = self.estimate_cost(
                totals["input_tokens"], totals["output_tokens"],
                totals["cache_read_tokens"], totals["cache_write_tokens"]
            )

        except Exception as e:
            logger.error(f"Error ingesting transcript {transcript_path}: {e}")

        return totals

    def ingest_mission_transcripts(self, mission_id: str) -> Dict[str, Any]:
        """
        Ingest all transcripts for a mission from the archive.

        Args:
            mission_id: Mission identifier

        Returns:
            Dict with total stats from all transcripts
        """
        archive_path = TRANSCRIPTS_DIR / mission_id
        totals = {
            "transcripts_processed": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_write_tokens": 0,
            "total_cost_usd": 0.0
        }

        if not archive_path.exists():
            logger.warning(f"No transcript archive found for {mission_id}")
            return totals

        for jsonl_file in archive_path.glob("*.jsonl"):
            result = self.ingest_transcript(jsonl_file, mission_id)
            totals["transcripts_processed"] += 1
            totals["total_input_tokens"] += result["input_tokens"]
            totals["total_output_tokens"] += result["output_tokens"]
            totals["total_cache_read_tokens"] += result["cache_read_tokens"]
            totals["total_cache_write_tokens"] += result["cache_write_tokens"]
            totals["total_cost_usd"] += result["cost_usd"]

        return totals

    def _find_live_transcript_dir(self, mission_id: str) -> Optional[Path]:
        """
        Find the Claude transcript directory for a mission's workspace.

        Claude stores transcripts in ~/.claude/projects/-{path-with-dashes}
        where underscores and slashes are converted to dashes.

        Args:
            mission_id: Mission identifier (e.g., "mission_c05663da")

        Returns:
            Path to transcript directory, or None if not found
        """
        # Construct the expected directory name
        # Pattern: <ATLASFORGE_ROOT>/missions/mission_xxx/workspace
        # -> -path-to-atlasforge-missions-mission-xxx-workspace
        workspace_path = str(MISSIONS_DIR / mission_id / "workspace")
        escaped = workspace_path.replace('/', '-').replace('_', '-')

        transcript_dir = CLAUDE_PROJECTS_DIR / escaped

        if transcript_dir.exists():
            return transcript_dir

        # Fallback: search for partial match
        search_pattern = f"*-{mission_id.replace('_', '-')}-workspace"
        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if d.is_dir() and d.name.endswith(search_pattern.replace('*', '')):
                return d

        return None

    def ingest_live_transcripts(self, mission_id: str) -> Dict[str, Any]:
        """
        Ingest transcripts from Claude's live project directory.

        This method finds transcripts in ~/.claude/projects/ and ingests them
        directly, without waiting for mission archival.

        Args:
            mission_id: Mission identifier

        Returns:
            Dict with total stats from all transcripts
        """
        totals = {
            "transcripts_processed": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_write_tokens": 0,
            "total_cost_usd": 0.0,
            "source": "live"
        }

        transcript_dir = self._find_live_transcript_dir(mission_id)

        if not transcript_dir:
            logger.debug(f"No live transcript directory found for {mission_id}")
            # Fall back to archived transcripts
            archived = self.ingest_mission_transcripts(mission_id)
            archived["source"] = "archive"
            return archived

        logger.info(f"Found live transcripts at {transcript_dir}")

        for jsonl_file in transcript_dir.glob("*.jsonl"):
            result = self.ingest_transcript(jsonl_file, mission_id)
            totals["transcripts_processed"] += 1
            totals["total_input_tokens"] += result["input_tokens"]
            totals["total_output_tokens"] += result["output_tokens"]
            totals["total_cache_read_tokens"] += result["cache_read_tokens"]
            totals["total_cache_write_tokens"] += result["cache_write_tokens"]
            totals["total_cost_usd"] += result["cost_usd"]

        logger.info(f"Ingested {totals['transcripts_processed']} live transcripts for {mission_id}")
        return totals

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    def get_mission_summary(self, mission_id: str) -> Optional[MissionMetrics]:
        """
        Get complete metrics for a mission.

        Args:
            mission_id: Mission identifier

        Returns:
            MissionMetrics object or None if not found
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Get mission data
            cursor.execute("""
                SELECT mission_id, problem_statement, started_at, ended_at,
                       total_duration_seconds, total_input_tokens, total_output_tokens,
                       total_cache_read_tokens, total_cache_write_tokens, total_tokens,
                       total_estimated_cost_usd, stages_completed, cycles_completed,
                       final_status
                FROM mission_metrics WHERE mission_id = ?
            """, (mission_id,))
            row = cursor.fetchone()

            if not row:
                return None

            metrics = MissionMetrics(
                mission_id=row[0],
                problem_statement=row[1] or "",
                started_at=row[2],
                ended_at=row[3],
                total_duration_seconds=row[4] or 0.0,
                total_input_tokens=row[5] or 0,
                total_output_tokens=row[6] or 0,
                total_cache_read_tokens=row[7] or 0,
                total_cache_write_tokens=row[8] or 0,
                total_tokens=row[9] or 0,
                total_estimated_cost_usd=row[10] or 0.0,
                stages_completed=row[11] or 0,
                cycles_completed=row[12] or 0,
                final_status=row[13] or "unknown"
            )

            # If mission_metrics shows 0 tokens, fall back to token_events
            # This handles cases where stage tracking wasn't working properly
            if metrics.total_tokens == 0:
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(input_tokens), 0),
                        COALESCE(SUM(output_tokens), 0),
                        COALESCE(SUM(cache_read_tokens), 0),
                        COALESCE(SUM(cache_write_tokens), 0)
                    FROM token_events WHERE mission_id = ?
                """, (mission_id,))
                event_row = cursor.fetchone()
                if event_row and (event_row[0] or event_row[1]):
                    metrics.total_input_tokens = event_row[0]
                    metrics.total_output_tokens = event_row[1]
                    metrics.total_cache_read_tokens = event_row[2]
                    metrics.total_cache_write_tokens = event_row[3]
                    metrics.total_tokens = (
                        metrics.total_input_tokens +
                        metrics.total_output_tokens +
                        metrics.total_cache_read_tokens +
                        metrics.total_cache_write_tokens
                    )
                    metrics.total_estimated_cost_usd = self.estimate_cost(
                        metrics.total_input_tokens,
                        metrics.total_output_tokens,
                        metrics.total_cache_read_tokens,
                        metrics.total_cache_write_tokens
                    )

            # Get stage metrics
            cursor.execute("""
                SELECT stage, started_at, ended_at, duration_seconds,
                       input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                       total_tokens, estimated_cost_usd, model, iteration, cycle
                FROM stage_metrics WHERE mission_id = ?
                ORDER BY id ASC
            """, (mission_id,))

            for row in cursor.fetchall():
                stage_key = f"{row[0]}_iter{row[11]}_cycle{row[12]}"
                metrics.stages[stage_key] = StageMetrics(
                    mission_id=mission_id,
                    stage=row[0],
                    started_at=row[1],
                    ended_at=row[2],
                    duration_seconds=row[3] or 0.0,
                    input_tokens=row[4] or 0,
                    output_tokens=row[5] or 0,
                    cache_read_tokens=row[6] or 0,
                    cache_write_tokens=row[7] or 0,
                    total_tokens=row[8] or 0,
                    estimated_cost_usd=row[9] or 0.0,
                    model=row[10] or "unknown",
                    iteration=row[11] or 0,
                    cycle=row[12] or 1
                )

            return metrics
        finally:
            conn.close()

    def get_aggregate_stats(self, days: int = 30) -> Dict[str, Any]:
        """
        Get aggregate statistics across all missions.

        Args:
            days: Number of days to include (0 for all time)

        Returns:
            Dict with aggregate statistics
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Build date filter
            date_filter = ""
            params = []
            if days > 0:
                cutoff = datetime.now().isoformat()[:10]  # YYYY-MM-DD
                date_filter = "WHERE started_at >= date(?, '-' || ? || ' days')"
                params = [cutoff, days]

            # Get totals
            cursor.execute(f"""
                SELECT
                    COUNT(*) as mission_count,
                    SUM(total_input_tokens) as total_input,
                    SUM(total_output_tokens) as total_output,
                    SUM(total_cache_read_tokens) as total_cache_read,
                    SUM(total_cache_write_tokens) as total_cache_write,
                    SUM(total_tokens) as total_tokens,
                    SUM(total_estimated_cost_usd) as total_cost,
                    AVG(total_duration_seconds) as avg_duration,
                    SUM(stages_completed) as total_stages,
                    SUM(cycles_completed) as total_cycles
                FROM mission_metrics {date_filter}
            """, params)

            row = cursor.fetchone()

            # Get status breakdown
            cursor.execute(f"""
                SELECT final_status, COUNT(*)
                FROM mission_metrics {date_filter}
                GROUP BY final_status
            """, params)
            status_breakdown = dict(cursor.fetchall())

            # Get stage breakdown
            # The subquery needs the same params as the outer query (just date filter params)
            cursor.execute(f"""
                SELECT stage,
                       COUNT(*) as count,
                       AVG(duration_seconds) as avg_duration,
                       SUM(total_tokens) as total_tokens,
                       SUM(estimated_cost_usd) as total_cost
                FROM stage_metrics
                WHERE mission_id IN (
                    SELECT mission_id FROM mission_metrics {date_filter}
                )
                GROUP BY stage
            """, params)

            stage_breakdown = {}
            for stage_row in cursor.fetchall():
                stage_breakdown[stage_row[0]] = {
                    "count": stage_row[1],
                    "avg_duration_seconds": round(stage_row[2] or 0, 2),
                    "total_tokens": stage_row[3] or 0,
                    "total_cost_usd": round(stage_row[4] or 0, 4)
                }

            # Build totals from mission_metrics
            totals = {
                "missions": row[0] or 0,
                "input_tokens": row[1] or 0,
                "output_tokens": row[2] or 0,
                "cache_read_tokens": row[3] or 0,
                "cache_write_tokens": row[4] or 0,
                "total_tokens": row[5] or 0,
                "total_cost_usd": round(row[6] or 0, 4),
                "avg_mission_duration_seconds": round(row[7] or 0, 2),
                "total_stages": row[8] or 0,
                "total_cycles": row[9] or 0
            }

            # If mission_metrics shows 0 tokens, fall back to token_events
            # This provides accurate totals when stage tracking wasn't working
            if totals["total_tokens"] == 0:
                cursor.execute("""
                    SELECT
                        COALESCE(SUM(input_tokens), 0),
                        COALESCE(SUM(output_tokens), 0),
                        COALESCE(SUM(cache_read_tokens), 0),
                        COALESCE(SUM(cache_write_tokens), 0),
                        COUNT(DISTINCT mission_id)
                    FROM token_events
                """)
                event_row = cursor.fetchone()
                if event_row and (event_row[0] or event_row[1]):
                    totals["input_tokens"] = event_row[0]
                    totals["output_tokens"] = event_row[1]
                    totals["cache_read_tokens"] = event_row[2]
                    totals["cache_write_tokens"] = event_row[3]
                    totals["total_tokens"] = (
                        totals["input_tokens"] +
                        totals["output_tokens"] +
                        totals["cache_read_tokens"] +
                        totals["cache_write_tokens"]
                    )
                    totals["total_cost_usd"] = round(self.estimate_cost(
                        totals["input_tokens"],
                        totals["output_tokens"],
                        totals["cache_read_tokens"],
                        totals["cache_write_tokens"]
                    ), 4)
                    # Update mission count from token_events if needed
                    if totals["missions"] == 0:
                        totals["missions"] = event_row[4]

            return {
                "period_days": days if days > 0 else "all_time",
                "generated_at": datetime.now().isoformat(),
                "totals": totals,
                "status_breakdown": status_breakdown,
                "stage_breakdown": stage_breakdown
            }
        finally:
            conn.close()

    def get_recent_missions(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recently completed missions with their metrics.

        Aggregates from token_events when mission_metrics has 0 values,
        ensuring accurate cost/token display even when stage tracking
        wasn't working properly.

        Args:
            limit: Maximum number of missions to return

        Returns:
            List of mission summary dicts
        """
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # First, get all unique mission_ids from token_events with their aggregates
            # This is more reliable than mission_metrics which may have 0 values
            cursor.execute("""
                SELECT
                    mission_id,
                    MIN(timestamp) as first_event,
                    MAX(timestamp) as last_event,
                    SUM(input_tokens) as input_tokens,
                    SUM(output_tokens) as output_tokens,
                    SUM(cache_read_tokens) as cache_read,
                    SUM(cache_write_tokens) as cache_write,
                    COUNT(*) as event_count
                FROM token_events
                GROUP BY mission_id
                ORDER BY MAX(timestamp) DESC
                LIMIT ?
            """, (limit,))

            token_data = {}
            for row in cursor.fetchall():
                mid = row[0]
                in_tok = row[3] or 0
                out_tok = row[4] or 0
                cache_read = row[5] or 0
                cache_write = row[6] or 0
                total = in_tok + out_tok + cache_read + cache_write
                cost = self.estimate_cost(in_tok, out_tok, cache_read, cache_write)
                token_data[mid] = {
                    "first_event": row[1],
                    "last_event": row[2],
                    "total_tokens": total,
                    "cost_usd": cost,
                    "event_count": row[7]
                }

            # Now get mission_metrics data
            cursor.execute("""
                SELECT mission_id, problem_statement, started_at, ended_at,
                       total_duration_seconds, total_tokens, total_estimated_cost_usd,
                       final_status, cycles_completed
                FROM mission_metrics
                ORDER BY started_at DESC
                LIMIT ?
            """, (limit,))

            missions = []
            seen_ids = set()

            for row in cursor.fetchall():
                mid = row[0]
                seen_ids.add(mid)

                # Use token_events data if mission_metrics shows 0
                tokens_from_metrics = row[5] or 0
                cost_from_metrics = row[6] or 0

                if tokens_from_metrics == 0 and mid in token_data:
                    # Use aggregated token_events data
                    total_tokens = token_data[mid]["total_tokens"]
                    cost_usd = token_data[mid]["cost_usd"]
                else:
                    total_tokens = tokens_from_metrics
                    cost_usd = cost_from_metrics

                missions.append({
                    "mission_id": mid,
                    "problem_statement": (row[1] or "")[:100],
                    "started_at": row[2],
                    "ended_at": row[3],
                    "duration_seconds": row[4] or 0,
                    "total_tokens": total_tokens,
                    "cost_usd": round(cost_usd, 4),
                    "status": row[7],
                    "cycles": row[8] or 0
                })

            # Add any missions from token_events not in mission_metrics
            for mid, td in token_data.items():
                if mid not in seen_ids:
                    missions.append({
                        "mission_id": mid,
                        "problem_statement": "",
                        "started_at": td["first_event"],
                        "ended_at": td["last_event"],
                        "duration_seconds": 0,
                        "total_tokens": td["total_tokens"],
                        "cost_usd": round(td["cost_usd"], 4),
                        "status": "unknown",
                        "cycles": 0
                    })

            # Sort by started_at descending
            missions.sort(key=lambda m: m.get("started_at") or "", reverse=True)

            return missions[:limit]
        finally:
            conn.close()

    # =========================================================================
    # EXPORT METHODS
    # =========================================================================

    def export_dashboard_data(self) -> Dict[str, Any]:
        """
        Export all analytics data in dashboard-friendly format.

        Returns:
            Dict suitable for JSON serialization to dashboard
        """
        return {
            "aggregate": self.get_aggregate_stats(days=30),
            "all_time": self.get_aggregate_stats(days=0),
            "recent_missions": self.get_recent_missions(limit=20),
            "model_pricing": MODEL_PRICING
        }

    def export_to_json(self, output_path: Path = None) -> Path:
        """
        Export analytics to JSON file.

        Args:
            output_path: Output file path (default: analytics/export.json)

        Returns:
            Path to the created file
        """
        output_path = output_path or (self.storage_path / "analytics_export.json")

        data = self.export_dashboard_data()

        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)

        logger.info(f"Analytics exported to {output_path}")
        return output_path


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_analytics_instance = None
from atlasforge_config import MISSION_PATH


def get_current_mission_analytics() -> Dict[str, Any]:
    """
    Get analytics for the currently active mission.

    Queries token_events directly to get real-time accurate data,
    bypassing the stage_metrics aggregation which may have issues
    with stage matching.

    Returns:
        Dict with mission analytics including tokens, cost breakdown
    """
    import io_utils as io_utils_module

    # Read current mission state
    mission = io_utils_module.atomic_read_json(MISSION_PATH, {})
    mission_id = mission.get("mission_id")

    if not mission_id:
        return {"error": "No active mission", "tokens": 0, "cost": 0}

    analytics = get_analytics()
    conn = sqlite3.connect(analytics.db_path)
    try:
        cursor = conn.cursor()

        # Query token_events directly - this is always accurate
        cursor.execute("""
            SELECT
                COALESCE(SUM(input_tokens), 0),
                COALESCE(SUM(output_tokens), 0),
                COALESCE(SUM(cache_read_tokens), 0),
                COALESCE(SUM(cache_write_tokens), 0),
                COUNT(*) as event_count
            FROM token_events
            WHERE mission_id = ?
        """, (mission_id,))
        row = cursor.fetchone()

        if row and row[4] > 0:  # Has events
            input_tokens = row[0]
            output_tokens = row[1]
            cache_read = row[2]
            cache_write = row[3]
            total = input_tokens + output_tokens + cache_read + cache_write
            cost = analytics.estimate_cost(input_tokens, output_tokens, cache_read, cache_write)

            return {
                "mission_id": mission_id,
                "tokens": total,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_tokens": cache_read,
                "cache_write_tokens": cache_write,
                "cost": cost,
                "event_count": row[4]
            }

        # No events yet for this mission
        return {
            "mission_id": mission_id,
            "tokens": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost": 0.0,
            "event_count": 0
        }
    finally:
        conn.close()


def get_analytics() -> MissionAnalytics:
    """Get or create the global analytics instance"""
    global _analytics_instance
    if _analytics_instance is None:
        _analytics_instance = MissionAnalytics()
    return _analytics_instance


def track_stage_start(mission_id: str, stage: str,
                      iteration: int = 0, cycle: int = 1) -> None:
    """Convenience function to track stage start"""
    get_analytics().start_stage(mission_id, stage, iteration, cycle)


def track_stage_end(mission_id: str, stage: str,
                    iteration: int = 0, cycle: int = 1) -> None:
    """Convenience function to track stage end"""
    get_analytics().end_stage(mission_id, stage, iteration, cycle)


def track_tokens(mission_id: str, stage: str, usage: Dict[str, Any],
                 model: str = "unknown") -> None:
    """Convenience function to track token usage"""
    get_analytics().record_token_usage(mission_id, stage, usage, model)


def get_daily_aggregates(days: int = 30) -> Dict[str, Any]:
    """
    Get daily cost and token aggregates for the specified period.

    Args:
        days: Number of days to include (0 for all time)

    Returns:
        Dict with daily data points for trend charts
    """
    analytics = get_analytics()
    conn = sqlite3.connect(analytics.db_path)
    try:
        cursor = conn.cursor()

        # Build date filter
        date_filter = ""
        params = []
        if days > 0:
            cutoff = datetime.now().isoformat()[:10]
            date_filter = "WHERE DATE(timestamp) >= date(?, '-' || ? || ' days')"
            params = [cutoff, days]

        # Group by date
        cursor.execute(f"""
            SELECT
                DATE(timestamp) as day,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(cache_read_tokens) as cache_read,
                SUM(cache_write_tokens) as cache_write,
                COUNT(DISTINCT mission_id) as mission_count
            FROM token_events
            {date_filter}
            GROUP BY DATE(timestamp)
            ORDER BY day DESC
            LIMIT 60
        """, params)

        daily = []
        for row in cursor.fetchall():
            day_input = row[1] or 0
            day_output = row[2] or 0
            day_cache_read = row[3] or 0
            day_cache_write = row[4] or 0
            day_total = day_input + day_output + day_cache_read + day_cache_write
            day_cost = analytics.estimate_cost(day_input, day_output, day_cache_read, day_cache_write)

            daily.append({
                "date": row[0],
                "input_tokens": day_input,
                "output_tokens": day_output,
                "cache_read_tokens": day_cache_read,
                "cache_write_tokens": day_cache_write,
                "total_tokens": day_total,
                "cost": round(day_cost, 4),
                "missions": row[5] or 0
            })

        # Reverse to get chronological order
        daily.reverse()

        # Calculate summary stats
        if daily:
            costs = [d["cost"] for d in daily]
            avg_daily = sum(costs) / len(costs)
            peak_idx = costs.index(max(costs))
            peak_day = daily[peak_idx]["date"]
            peak_cost = daily[peak_idx]["cost"]
        else:
            avg_daily = 0
            peak_day = "-"
            peak_cost = 0

        return {
            "daily": daily,
            "summary": {
                "avg_daily_cost": round(avg_daily, 4),
                "peak_day": peak_day,
                "peak_cost": round(peak_cost, 4),
                "days_with_data": len(daily)
            }
        }
    finally:
        conn.close()


def get_stage_aggregates(days: int = 30) -> Dict[str, Any]:
    """
    Get stage-level aggregates.

    Args:
        days: Number of days to include

    Returns:
        Dict with per-stage statistics
    """
    analytics = get_analytics()
    conn = sqlite3.connect(analytics.db_path)
    try:
        cursor = conn.cursor()

        # Build date filter
        date_filter = ""
        params = []
        if days > 0:
            cutoff = datetime.now().isoformat()[:10]
            date_filter = "WHERE DATE(timestamp) >= date(?, '-' || ? || ' days')"
            params = [cutoff, days]

        # Group by stage
        cursor.execute(f"""
            SELECT
                stage,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(cache_read_tokens) as cache_read,
                SUM(cache_write_tokens) as cache_write,
                COUNT(*) as event_count,
                COUNT(DISTINCT mission_id) as mission_count
            FROM token_events
            {date_filter}
            GROUP BY stage
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, params)

        stages = {}
        total_tokens = 0
        total_cost = 0

        for row in cursor.fetchall():
            stage_name = row[0] or "UNKNOWN"
            stage_input = row[1] or 0
            stage_output = row[2] or 0
            stage_cache_read = row[3] or 0
            stage_cache_write = row[4] or 0
            stage_total = stage_input + stage_output + stage_cache_read + stage_cache_write
            stage_cost = analytics.estimate_cost(stage_input, stage_output, stage_cache_read, stage_cache_write)

            stages[stage_name] = {
                "input_tokens": stage_input,
                "output_tokens": stage_output,
                "cache_read_tokens": stage_cache_read,
                "cache_write_tokens": stage_cache_write,
                "total_tokens": stage_total,
                "cost": round(stage_cost, 4),
                "event_count": row[5] or 0,
                "mission_count": row[6] or 0
            }

            total_tokens += stage_total
            total_cost += stage_cost

        return {
            "stages": stages,
            "summary": {
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "stage_count": len(stages)
            }
        }
    finally:
        conn.close()


def get_model_aggregates(days: int = 30) -> Dict[str, Any]:
    """
    Get model-level aggregates.

    Args:
        days: Number of days to include

    Returns:
        Dict with per-model statistics
    """
    analytics = get_analytics()
    conn = sqlite3.connect(analytics.db_path)
    try:
        cursor = conn.cursor()

        # Build date filter
        date_filter = ""
        params = []
        if days > 0:
            cutoff = datetime.now().isoformat()[:10]
            date_filter = "WHERE DATE(timestamp) >= date(?, '-' || ? || ' days')"
            params = [cutoff, days]

        # Group by model
        cursor.execute(f"""
            SELECT
                model,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(cache_read_tokens) as cache_read,
                SUM(cache_write_tokens) as cache_write,
                COUNT(*) as event_count,
                COUNT(DISTINCT mission_id) as mission_count
            FROM token_events
            {date_filter}
            GROUP BY model
            ORDER BY SUM(input_tokens) + SUM(output_tokens) DESC
        """, params)

        models = {}
        total_tokens = 0
        total_cost = 0

        for row in cursor.fetchall():
            model_name = row[0] or "unknown"
            model_input = row[1] or 0
            model_output = row[2] or 0
            model_cache_read = row[3] or 0
            model_cache_write = row[4] or 0
            model_total = model_input + model_output + model_cache_read + model_cache_write
            model_cost = analytics.estimate_cost(model_input, model_output, model_cache_read, model_cache_write, model_name)

            # Clean up model name for display
            display_name = model_name.replace("claude-", "").replace("-20251101", "").replace("-20250514", "").replace("-20251001", "")

            models[model_name] = {
                "display_name": display_name.title(),
                "input_tokens": model_input,
                "output_tokens": model_output,
                "cache_read_tokens": model_cache_read,
                "cache_write_tokens": model_cache_write,
                "total_tokens": model_total,
                "cost": round(model_cost, 4),
                "event_count": row[5] or 0,
                "mission_count": row[6] or 0
            }

            total_tokens += model_total
            total_cost += model_cost

        return {
            "models": models,
            "summary": {
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "model_count": len(models)
            }
        }
    finally:
        conn.close()


def get_mission_stage_breakdown(mission_id: str) -> Dict[str, Any]:
    """
    Get detailed stage breakdown for a specific mission.

    Args:
        mission_id: Mission identifier

    Returns:
        Dict with stage-by-stage breakdown for the mission
    """
    analytics = get_analytics()
    conn = sqlite3.connect(analytics.db_path)
    try:
        cursor = conn.cursor()

        # Get mission info first
        cursor.execute("""
            SELECT problem_statement, started_at, ended_at, final_status
            FROM mission_metrics
            WHERE mission_id = ?
        """, (mission_id,))
        mission_row = cursor.fetchone()

        # Get stage-by-stage from token_events
        cursor.execute("""
            SELECT
                stage,
                MIN(timestamp) as started_at,
                MAX(timestamp) as ended_at,
                SUM(input_tokens) as input_tokens,
                SUM(output_tokens) as output_tokens,
                SUM(cache_read_tokens) as cache_read,
                SUM(cache_write_tokens) as cache_write,
                COUNT(*) as event_count,
                GROUP_CONCAT(DISTINCT model) as models
            FROM token_events
            WHERE mission_id = ?
            GROUP BY stage
            ORDER BY MIN(timestamp) ASC
        """, (mission_id,))

        stages = []
        total_tokens = 0
        total_cost = 0

        for row in cursor.fetchall():
            stage_input = row[3] or 0
            stage_output = row[4] or 0
            stage_cache_read = row[5] or 0
            stage_cache_write = row[6] or 0
            stage_total = stage_input + stage_output + stage_cache_read + stage_cache_write
            stage_cost = analytics.estimate_cost(stage_input, stage_output, stage_cache_read, stage_cache_write)

            stages.append({
                "stage": row[0],
                "started_at": row[1],
                "ended_at": row[2],
                "input_tokens": stage_input,
                "output_tokens": stage_output,
                "cache_read_tokens": stage_cache_read,
                "cache_write_tokens": stage_cache_write,
                "total_tokens": stage_total,
                "cost": round(stage_cost, 4),
                "event_count": row[7] or 0,
                "models": row[8] or ""
            })

            total_tokens += stage_total
            total_cost += stage_cost

        return {
            "mission_id": mission_id,
            "problem_statement": mission_row[0] if mission_row else "",
            "started_at": mission_row[1] if mission_row else None,
            "ended_at": mission_row[2] if mission_row else None,
            "status": mission_row[3] if mission_row else "unknown",
            "stages": stages,
            "summary": {
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "stage_count": len(stages)
            }
        }
    finally:
        conn.close()


# =============================================================================
# MAIN (Demo/Test)
# =============================================================================

if __name__ == "__main__":
    # Self-test
    print("=" * 60)
    print("Mission Analytics - Self Test")
    print("=" * 60)

    # Create test instance
    test_dir = Path("/tmp/analytics_test")
    test_dir.mkdir(exist_ok=True)

    analytics = MissionAnalytics(storage_path=test_dir)

    # Test 1: Mission lifecycle
    print("\n[TEST 1] Mission lifecycle...")
    test_mission = "test_mission_001"
    analytics.start_mission(test_mission, "Test mission for analytics validation")

    # Test 2: Stage tracking
    print("[TEST 2] Stage tracking...")
    analytics.start_stage(test_mission, "PLANNING", iteration=0, cycle=1)

    # Test 3: Token recording
    print("[TEST 3] Token recording...")
    analytics.record_token_usage(test_mission, "PLANNING", {
        "input_tokens": 5000,
        "output_tokens": 2000,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 500
    }, model="claude-sonnet-4-5-20250514")

    analytics.end_stage(test_mission, "PLANNING", iteration=0, cycle=1)

    # Test 4: Second stage
    print("[TEST 4] Second stage...")
    analytics.start_stage(test_mission, "BUILDING", iteration=0, cycle=1)
    analytics.record_token_usage(test_mission, "BUILDING", {
        "input_tokens": 10000,
        "output_tokens": 8000,
        "cache_read_input_tokens": 5000
    }, model="claude-sonnet-4-5-20250514")
    analytics.end_stage(test_mission, "BUILDING", iteration=0, cycle=1)

    # End mission
    analytics.end_mission(test_mission, "complete")

    # Test 5: Query summary
    print("[TEST 5] Mission summary...")
    summary = analytics.get_mission_summary(test_mission)
    if summary:
        print(f"  Mission: {summary.mission_id}")
        print(f"  Total tokens: {summary.total_tokens}")
        print(f"  Estimated cost: ${summary.total_estimated_cost_usd:.4f}")
        print(f"  Stages: {list(summary.stages.keys())}")
    else:
        print("  ERROR: No summary returned!")

    # Test 6: Aggregate stats
    print("[TEST 6] Aggregate stats...")
    stats = analytics.get_aggregate_stats(days=0)
    print(f"  Total missions: {stats['totals']['missions']}")
    print(f"  Total tokens: {stats['totals']['total_tokens']}")
    print(f"  Total cost: ${stats['totals']['total_cost_usd']:.4f}")

    # Test 7: Cost estimation
    print("[TEST 7] Cost estimation...")
    cost_opus = analytics.estimate_cost(100000, 50000, model="claude-opus-4-5-20251101")
    cost_sonnet = analytics.estimate_cost(100000, 50000, model="claude-sonnet-4-5-20250514")
    cost_haiku = analytics.estimate_cost(100000, 50000, model="claude-haiku-4-5-20251001")
    print(f"  100k in + 50k out (Opus): ${cost_opus:.4f}")
    print(f"  100k in + 50k out (Sonnet): ${cost_sonnet:.4f}")
    print(f"  100k in + 50k out (Haiku): ${cost_haiku:.4f}")

    # Test 8: Export
    print("[TEST 8] JSON export...")
    export_path = analytics.export_to_json()
    print(f"  Exported to: {export_path}")

    # Validate export
    with open(export_path, 'r') as f:
        export_data = json.load(f)

    if "aggregate" in export_data and "recent_missions" in export_data:
        print("  Export structure: VALID")
    else:
        print("  Export structure: INVALID!")

    # Cleanup
    import shutil
    shutil.rmtree(test_dir)

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
