#!/usr/bin/env python3
"""
Decision Graph - Tool Invocation Logger and Graph Visualization

This module provides:
1. Logging of all tool invocations with inputs/outputs
2. Building decision graphs for mission analysis
3. Failure point detection and pattern analysis
4. Export for dashboard visualization

Usage:
    logger = DecisionGraphLogger()

    # Log a tool invocation
    logger.log_invocation(
        mission_id="mission_abc",
        stage="BUILDING",
        tool_name="Edit",
        input_summary={"file": "main.py", "old_string": "...", "new_string": "..."},
        output_summary={"success": True},
        status="success",
        duration_ms=150,
        token_usage={"input": 100, "output": 50}
    )

    # Get graph for visualization
    graph = logger.get_mission_graph("mission_abc")
    failures = logger.get_failure_points("mission_abc")
"""

import json
import sqlite3
import logging
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

logger = logging.getLogger(__name__)

# Paths
BASE_DIR = Path("/home/vader/mini-mind-v2")
DECISION_GRAPH_DIR = BASE_DIR / "rde_data" / "decision_graphs"
DECISION_GRAPH_DIR.mkdir(parents=True, exist_ok=True)


class InvocationStatus(Enum):
    """Status of a tool invocation"""
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    BLOCKED = "blocked"
    PENDING = "pending"


@dataclass
class ToolInvocation:
    """A single tool invocation record"""
    invocation_id: str
    mission_id: str
    stage: str
    tool_name: str
    timestamp: str
    duration_ms: int = 0
    input_summary: Dict[str, Any] = field(default_factory=dict)
    output_summary: Dict[str, Any] = field(default_factory=dict)
    status: str = "success"
    error_message: str = ""
    parent_id: Optional[str] = None  # For hierarchical calls
    sequence_number: int = 0
    token_usage: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ToolInvocation':
        return cls(**data)


@dataclass
class GraphNode:
    """Node for graph visualization"""
    id: str
    label: str
    tool_name: str
    status: str
    timestamp: str
    x: float = 0.0
    y: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """Edge for graph visualization"""
    source: str
    target: str
    label: str = ""


class DecisionGraphLogger:
    """
    Logs tool invocations and builds decision graphs for analysis.

    Uses SQLite for persistent storage and provides methods for:
    - Recording tool invocations
    - Querying invocation history
    - Building graph structures for visualization
    - Detecting failure patterns
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize the decision graph logger.

        Args:
            storage_path: Path to store database (default: DECISION_GRAPH_DIR)
        """
        self.storage_path = storage_path or DECISION_GRAPH_DIR
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_path / "decision_graph.db"
        self._init_db()
        self._sequence_counters = {}  # mission_id -> counter

    def _init_db(self):
        """Initialize SQLite database schema"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Tool invocations table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tool_invocations (
                    invocation_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    duration_ms INTEGER DEFAULT 0,
                    input_summary TEXT,
                    output_summary TEXT,
                    status TEXT DEFAULT 'success',
                    error_message TEXT,
                    parent_id TEXT,
                    sequence_number INTEGER DEFAULT 0,
                    token_usage TEXT
                )
            """)

            # Indexes for efficient querying
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_mission ON tool_invocations(mission_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_stage ON tool_invocations(stage)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_tool ON tool_invocations(tool_name)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_status ON tool_invocations(status)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_timestamp ON tool_invocations(timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_invocations_sequence ON tool_invocations(mission_id, sequence_number)")

            # Patterns table for storing detected patterns
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS detected_patterns (
                    pattern_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    pattern_type TEXT NOT NULL,
                    description TEXT,
                    severity TEXT DEFAULT 'info',
                    invocation_ids TEXT,
                    timestamp TEXT NOT NULL
                )
            """)

            conn.commit()

    def _get_next_sequence(self, mission_id: str) -> int:
        """Get next sequence number for a mission"""
        if mission_id not in self._sequence_counters:
            # Load from database
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT MAX(sequence_number) FROM tool_invocations WHERE mission_id = ?",
                    (mission_id,)
                )
                result = cursor.fetchone()[0]
                self._sequence_counters[mission_id] = (result or 0) + 1

        seq = self._sequence_counters[mission_id]
        self._sequence_counters[mission_id] += 1
        return seq

    def log_invocation(
        self,
        mission_id: str,
        stage: str,
        tool_name: str,
        input_summary: Dict[str, Any],
        output_summary: Dict[str, Any] = None,
        status: str = "success",
        error_message: str = "",
        duration_ms: int = 0,
        parent_id: str = None,
        token_usage: Dict[str, int] = None
    ) -> str:
        """
        Log a tool invocation.

        Args:
            mission_id: Mission identifier
            stage: Current stage (PLANNING, BUILDING, etc.)
            tool_name: Name of the tool (Read, Write, Edit, Bash, etc.)
            input_summary: Summary of input parameters (truncated for storage)
            output_summary: Summary of output (truncated for storage)
            status: Invocation status (success, error, timeout, blocked)
            error_message: Error message if status is error
            duration_ms: Execution duration in milliseconds
            parent_id: Parent invocation ID for hierarchical calls
            token_usage: Token usage dict {input, output}

        Returns:
            Invocation ID
        """
        timestamp = datetime.now().isoformat()
        sequence_number = self._get_next_sequence(mission_id)

        # Generate deterministic ID
        id_string = f"{mission_id}_{stage}_{tool_name}_{timestamp}_{sequence_number}"
        invocation_id = hashlib.sha256(id_string.encode()).hexdigest()[:16]

        # Truncate input/output summaries for storage
        input_str = json.dumps(self._truncate_dict(input_summary or {}))
        output_str = json.dumps(self._truncate_dict(output_summary or {}))
        token_str = json.dumps(token_usage or {})

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tool_invocations
                (invocation_id, mission_id, stage, tool_name, timestamp, duration_ms,
                 input_summary, output_summary, status, error_message, parent_id,
                 sequence_number, token_usage)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                invocation_id, mission_id, stage, tool_name, timestamp, duration_ms,
                input_str, output_str, status, error_message, parent_id,
                sequence_number, token_str
            ))
            conn.commit()

        logger.debug(f"Logged invocation: {tool_name} ({status}) for {mission_id}")
        return invocation_id

    def _truncate_dict(self, d: dict, max_str_len: int = 500) -> dict:
        """Truncate string values in dict for storage"""
        result = {}
        for k, v in d.items():
            if isinstance(v, str) and len(v) > max_str_len:
                result[k] = v[:max_str_len] + "..."
            elif isinstance(v, dict):
                result[k] = self._truncate_dict(v, max_str_len)
            elif isinstance(v, list):
                result[k] = v[:10] if len(v) > 10 else v
            else:
                result[k] = v
        return result

    def get_invocations(
        self,
        mission_id: str,
        stage: str = None,
        tool_name: str = None,
        status: str = None,
        limit: int = 100
    ) -> List[ToolInvocation]:
        """
        Get tool invocations with optional filtering.

        Args:
            mission_id: Mission identifier
            stage: Filter by stage (optional)
            tool_name: Filter by tool name (optional)
            status: Filter by status (optional)
            limit: Maximum number of results

        Returns:
            List of ToolInvocation objects
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM tool_invocations WHERE mission_id = ?"
            params = [mission_id]

            if stage:
                query += " AND stage = ?"
                params.append(stage)
            if tool_name:
                query += " AND tool_name = ?"
                params.append(tool_name)
            if status:
                query += " AND status = ?"
                params.append(status)

            query += " ORDER BY sequence_number ASC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

        columns = [
            "invocation_id", "mission_id", "stage", "tool_name", "timestamp",
            "duration_ms", "input_summary", "output_summary", "status",
            "error_message", "parent_id", "sequence_number", "token_usage"
        ]

        invocations = []
        for row in rows:
            data = dict(zip(columns, row))
            data["input_summary"] = json.loads(data["input_summary"] or "{}")
            data["output_summary"] = json.loads(data["output_summary"] or "{}")
            data["token_usage"] = json.loads(data["token_usage"] or "{}")
            invocations.append(ToolInvocation.from_dict(data))

        return invocations

    def get_mission_graph(self, mission_id: str) -> Dict[str, Any]:
        """
        Get graph data for a mission suitable for visualization.

        Args:
            mission_id: Mission identifier

        Returns:
            Dict with nodes and edges for graph rendering
        """
        invocations = self.get_invocations(mission_id, limit=500)

        if not invocations:
            return {"nodes": [], "edges": [], "stats": {"total": 0, "errors": 0}}

        nodes = []
        edges = []
        prev_id = None

        for i, inv in enumerate(invocations):
            # Create node
            node = {
                "id": inv.invocation_id,
                "label": inv.tool_name,
                "tool_name": inv.tool_name,
                "status": inv.status,
                "timestamp": inv.timestamp,
                "stage": inv.stage,
                "sequence": inv.sequence_number,
                "duration_ms": inv.duration_ms,
                "has_error": inv.status == "error",
                "error_message": inv.error_message if inv.status == "error" else ""
            }
            nodes.append(node)

            # Create edge from previous node (sequential flow)
            if prev_id:
                edges.append({
                    "source": prev_id,
                    "target": inv.invocation_id,
                    "label": ""
                })

            # Also add edge from parent if different
            if inv.parent_id and inv.parent_id != prev_id:
                edges.append({
                    "source": inv.parent_id,
                    "target": inv.invocation_id,
                    "label": "child"
                })

            prev_id = inv.invocation_id

        # Calculate statistics
        error_count = sum(1 for n in nodes if n.get("has_error"))
        stage_counts = {}
        tool_counts = {}

        for inv in invocations:
            stage_counts[inv.stage] = stage_counts.get(inv.stage, 0) + 1
            tool_counts[inv.tool_name] = tool_counts.get(inv.tool_name, 0) + 1

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "total": len(nodes),
                "errors": error_count,
                "by_stage": stage_counts,
                "by_tool": tool_counts
            }
        }

    def get_failure_points(self, mission_id: str) -> List[Dict[str, Any]]:
        """
        Get all invocations that failed for a mission.

        Args:
            mission_id: Mission identifier

        Returns:
            List of failure details with context
        """
        invocations = self.get_invocations(mission_id, status="error", limit=100)

        failures = []
        for inv in invocations:
            failure = {
                "invocation_id": inv.invocation_id,
                "tool_name": inv.tool_name,
                "stage": inv.stage,
                "timestamp": inv.timestamp,
                "error_message": inv.error_message,
                "input_summary": inv.input_summary,
                "sequence_number": inv.sequence_number
            }
            failures.append(failure)

        return failures

    def get_unusual_patterns(self, mission_id: str) -> List[Dict[str, Any]]:
        """
        Detect unusual patterns in tool invocations.

        Patterns detected:
        - Repeated failures on same tool
        - Same file edited many times
        - Long sequences without progress
        - Rapid tool switching

        Args:
            mission_id: Mission identifier

        Returns:
            List of detected patterns
        """
        invocations = self.get_invocations(mission_id, limit=500)

        if len(invocations) < 5:
            return []

        patterns = []

        # Pattern 1: Repeated failures on same tool
        error_streaks = {}
        current_streak = {"tool": None, "count": 0, "ids": []}

        for inv in invocations:
            if inv.status == "error":
                if inv.tool_name == current_streak["tool"]:
                    current_streak["count"] += 1
                    current_streak["ids"].append(inv.invocation_id)
                else:
                    if current_streak["count"] >= 3:
                        patterns.append({
                            "type": "repeated_failure",
                            "description": f"{current_streak['count']} consecutive failures on {current_streak['tool']}",
                            "severity": "warning" if current_streak["count"] < 5 else "error",
                            "invocation_ids": current_streak["ids"]
                        })
                    current_streak = {"tool": inv.tool_name, "count": 1, "ids": [inv.invocation_id]}
            else:
                if current_streak["count"] >= 3:
                    patterns.append({
                        "type": "repeated_failure",
                        "description": f"{current_streak['count']} consecutive failures on {current_streak['tool']}",
                        "severity": "warning" if current_streak["count"] < 5 else "error",
                        "invocation_ids": current_streak["ids"]
                    })
                current_streak = {"tool": None, "count": 0, "ids": []}

        # Pattern 2: Same file edited many times
        file_edits = {}
        for inv in invocations:
            if inv.tool_name in ("Edit", "Write"):
                file_path = inv.input_summary.get("file_path", "")
                if file_path:
                    if file_path not in file_edits:
                        file_edits[file_path] = []
                    file_edits[file_path].append(inv.invocation_id)

        for file_path, ids in file_edits.items():
            if len(ids) >= 5:
                patterns.append({
                    "type": "excessive_edits",
                    "description": f"File edited {len(ids)} times: {file_path}",
                    "severity": "info" if len(ids) < 10 else "warning",
                    "invocation_ids": ids[:10]
                })

        # Pattern 3: Tool switching (high entropy in tool sequence)
        recent_window = min(20, len(invocations))
        recent_tools = [inv.tool_name for inv in invocations[-recent_window:]]
        unique_tools = len(set(recent_tools))

        if recent_window >= 10 and unique_tools >= recent_window * 0.8:
            patterns.append({
                "type": "tool_switching",
                "description": f"High tool variety in recent {recent_window} invocations ({unique_tools} unique tools)",
                "severity": "info",
                "invocation_ids": [inv.invocation_id for inv in invocations[-recent_window:]]
            })

        return patterns

    def get_invocation_details(self, invocation_id: str) -> Optional[ToolInvocation]:
        """Get full details for a specific invocation"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM tool_invocations WHERE invocation_id = ?",
                (invocation_id,)
            )
            row = cursor.fetchone()

        if not row:
            return None

        columns = [
            "invocation_id", "mission_id", "stage", "tool_name", "timestamp",
            "duration_ms", "input_summary", "output_summary", "status",
            "error_message", "parent_id", "sequence_number", "token_usage"
        ]

        data = dict(zip(columns, row))
        data["input_summary"] = json.loads(data["input_summary"] or "{}")
        data["output_summary"] = json.loads(data["output_summary"] or "{}")
        data["token_usage"] = json.loads(data["token_usage"] or "{}")

        return ToolInvocation.from_dict(data)

    def get_missions_with_graphs(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get list of missions that have decision graph data"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mission_id,
                       MIN(timestamp) as first_invocation,
                       MAX(timestamp) as last_invocation,
                       COUNT(*) as total_invocations,
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as error_count
                FROM tool_invocations
                GROUP BY mission_id
                ORDER BY last_invocation DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

        missions = []
        for row in rows:
            missions.append({
                "mission_id": row[0],
                "first_invocation": row[1],
                "last_invocation": row[2],
                "total_invocations": row[3],
                "error_count": row[4]
            })

        return missions

    def get_mission_summary(self, mission_id: str) -> Dict[str, Any]:
        """Get summary statistics for a mission's decision graph"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Total stats
            cursor.execute("""
                SELECT COUNT(*),
                       SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END),
                       SUM(duration_ms),
                       MIN(timestamp),
                       MAX(timestamp)
                FROM tool_invocations
                WHERE mission_id = ?
            """, (mission_id,))
            row = cursor.fetchone()

            # By stage
            cursor.execute("""
                SELECT stage, COUNT(*) FROM tool_invocations
                WHERE mission_id = ?
                GROUP BY stage
            """, (mission_id,))
            by_stage = dict(cursor.fetchall())

            # By tool
            cursor.execute("""
                SELECT tool_name, COUNT(*) FROM tool_invocations
                WHERE mission_id = ?
                GROUP BY tool_name
                ORDER BY COUNT(*) DESC
                LIMIT 10
            """, (mission_id,))
            by_tool = dict(cursor.fetchall())

        return {
            "mission_id": mission_id,
            "total_invocations": row[0] or 0,
            "error_count": row[1] or 0,
            "total_duration_ms": row[2] or 0,
            "first_invocation": row[3],
            "last_invocation": row[4],
            "by_stage": by_stage,
            "by_tool": by_tool
        }


# Convenience function for global access
_decision_logger = None

def get_decision_logger() -> DecisionGraphLogger:
    """Get or create the global decision graph logger"""
    global _decision_logger
    if _decision_logger is None:
        _decision_logger = DecisionGraphLogger()
    return _decision_logger


def log_tool_invocation(
    mission_id: str,
    stage: str,
    tool_name: str,
    input_summary: Dict[str, Any],
    output_summary: Dict[str, Any] = None,
    status: str = "success",
    error_message: str = "",
    duration_ms: int = 0
) -> str:
    """Convenience function to log a tool invocation"""
    return get_decision_logger().log_invocation(
        mission_id=mission_id,
        stage=stage,
        tool_name=tool_name,
        input_summary=input_summary,
        output_summary=output_summary,
        status=status,
        error_message=error_message,
        duration_ms=duration_ms
    )


if __name__ == "__main__":
    # Self-test
    print("=" * 60)
    print("Decision Graph Logger - Self Test")
    print("=" * 60)

    import tempfile
    import shutil

    # Create test instance
    test_dir = Path(tempfile.mkdtemp())
    logger_instance = DecisionGraphLogger(storage_path=test_dir)

    test_mission = "test_mission_001"

    # Test 1: Log invocations
    print("\n[TEST 1] Logging invocations...")

    inv1 = logger_instance.log_invocation(
        mission_id=test_mission,
        stage="PLANNING",
        tool_name="Read",
        input_summary={"file_path": "/home/vader/project/main.py"},
        output_summary={"lines": 100},
        status="success",
        duration_ms=50
    )
    print(f"  Logged: {inv1}")

    inv2 = logger_instance.log_invocation(
        mission_id=test_mission,
        stage="BUILDING",
        tool_name="Edit",
        input_summary={"file_path": "/home/vader/project/main.py", "old_string": "foo", "new_string": "bar"},
        output_summary={"success": True},
        status="success",
        duration_ms=100
    )
    print(f"  Logged: {inv2}")

    inv3 = logger_instance.log_invocation(
        mission_id=test_mission,
        stage="BUILDING",
        tool_name="Bash",
        input_summary={"command": "python test.py"},
        output_summary={"exit_code": 1, "error": "ImportError"},
        status="error",
        error_message="ImportError: No module named 'foo'",
        duration_ms=200
    )
    print(f"  Logged: {inv3}")

    # Test 2: Get invocations
    print("\n[TEST 2] Retrieving invocations...")
    invocations = logger_instance.get_invocations(test_mission)
    print(f"  Found {len(invocations)} invocations")

    # Test 3: Get graph
    print("\n[TEST 3] Building graph...")
    graph = logger_instance.get_mission_graph(test_mission)
    print(f"  Nodes: {len(graph['nodes'])}")
    print(f"  Edges: {len(graph['edges'])}")
    print(f"  Stats: {graph['stats']}")

    # Test 4: Get failures
    print("\n[TEST 4] Getting failure points...")
    failures = logger_instance.get_failure_points(test_mission)
    print(f"  Found {len(failures)} failures")
    for f in failures:
        print(f"    - {f['tool_name']}: {f['error_message'][:50]}")

    # Test 5: Get mission summary
    print("\n[TEST 5] Getting mission summary...")
    summary = logger_instance.get_mission_summary(test_mission)
    print(f"  Total invocations: {summary['total_invocations']}")
    print(f"  Errors: {summary['error_count']}")
    print(f"  By tool: {summary['by_tool']}")

    # Cleanup
    shutil.rmtree(test_dir)

    print("\n" + "=" * 60)
    print("All tests completed successfully!")
    print("=" * 60)
