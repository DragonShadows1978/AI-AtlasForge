#!/usr/bin/env python3
"""
Result Aggregator: Collects and merges outputs from parallel agents

When multiple agents work in parallel, their results need to be:
1. Collected from checkpoints
2. Validated for consistency
3. Merged into a coherent final result
4. Checked for conflicts

This module handles all result aggregation tasks.
"""

import json
import os
import difflib
from pathlib import Path
from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

from checkpoint_manager import CheckpointManager, Checkpoint, CheckpointStatus

logger = logging.getLogger("result_aggregator")

# Base paths - use centralized configuration
from atlasforge_config import BASE_DIR


class ConflictType(Enum):
    """Types of conflicts that can occur."""
    FILE_BOTH_MODIFIED = "file_both_modified"
    FILE_BOTH_CREATED = "file_both_created"
    CONTRADICTORY_RESULTS = "contradictory_results"
    PARTIAL_FAILURE = "partial_failure"


@dataclass
class Conflict:
    """Represents a conflict between agent results."""
    conflict_type: ConflictType
    agents_involved: List[str]
    description: str
    file_path: Optional[str] = None
    resolution: Optional[str] = None
    requires_human_review: bool = False

    def to_dict(self) -> dict:
        return {
            "type": self.conflict_type.value,
            "agents": self.agents_involved,
            "description": self.description,
            "file": self.file_path,
            "resolution": self.resolution,
            "requires_human_review": self.requires_human_review
        }


@dataclass
class MergedResult:
    """The final merged result from all agents."""
    mission_id: str
    success: bool
    total_agents: int
    completed_agents: int
    failed_agents: int
    files_created: List[str]
    files_modified: List[str]
    conflicts: List[Conflict]
    agent_summaries: List[Dict[str, Any]]
    combined_summary: str
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "success": self.success,
            "total_agents": self.total_agents,
            "completed_agents": self.completed_agents,
            "failed_agents": self.failed_agents,
            "files_created": self.files_created,
            "files_modified": self.files_modified,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "agent_summaries": self.agent_summaries,
            "combined_summary": self.combined_summary,
            "timestamp": self.timestamp,
            "metadata": self.metadata
        }

    def save(self, filepath: Optional[Path] = None) -> Path:
        """Save merged result to file."""
        if filepath is None:
            results_dir = BASE_DIR / "experiments" / "merged_results"
            results_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filepath = results_dir / f"{self.mission_id}_{timestamp}.json"

        with open(filepath, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

        return filepath

    @property
    def has_conflicts(self) -> bool:
        return len(self.conflicts) > 0

    @property
    def requires_human_review(self) -> bool:
        return any(c.requires_human_review for c in self.conflicts)


class ResultAggregator:
    """
    Aggregates results from parallel agent execution.

    Usage:
        aggregator = ResultAggregator(mission_id="my_mission")

        # Collect from checkpoints
        agent_results = aggregator.collect()

        # Merge results
        merged = aggregator.merge(agent_results)

        # Check for conflicts
        if merged.has_conflicts:
            for conflict in merged.conflicts:
                print(f"Conflict: {conflict.description}")

        # Save
        merged.save()
    """

    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.checkpoint_mgr = CheckpointManager(mission_id)

    def collect(self, agent_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """
        Collect results from checkpoint files.

        Args:
            agent_ids: Optional list of specific agents. If None, collect all.

        Returns:
            List of result dictionaries
        """
        if agent_ids is None:
            agent_ids = self.checkpoint_mgr.list_agents()

        results = []
        for agent_id in agent_ids:
            checkpoint = self.checkpoint_mgr.get_checkpoint(agent_id)
            if checkpoint:
                results.append({
                    "agent_id": agent_id,
                    "status": checkpoint.status.value,
                    "result": checkpoint.result,
                    "error": checkpoint.error,
                    "created_at": checkpoint.created_at,
                    "updated_at": checkpoint.updated_at
                })

        return results

    def merge(
        self,
        agent_results: List[Dict[str, Any]],
        resolve_conflicts: bool = True
    ) -> MergedResult:
        """
        Merge results from multiple agents.

        Args:
            agent_results: List of agent result dicts
            resolve_conflicts: If True, attempt automatic conflict resolution

        Returns:
            MergedResult object
        """
        # Categorize agents
        completed = [r for r in agent_results if r.get("status") == CheckpointStatus.COMPLETED.value]
        failed = [r for r in agent_results if r.get("status") in [
            CheckpointStatus.FAILED.value,
            CheckpointStatus.TIMEOUT.value
        ]]

        # Collect all files
        all_files_created: Dict[str, List[str]] = {}  # file -> agents that created it
        all_files_modified: Dict[str, List[str]] = {}  # file -> agents that modified it

        for r in completed:
            agent_id = r.get("agent_id", "unknown")
            result = r.get("result", {}) or {}

            for f in result.get("files_created", []):
                if f not in all_files_created:
                    all_files_created[f] = []
                all_files_created[f].append(agent_id)

            for f in result.get("files_modified", []):
                if f not in all_files_modified:
                    all_files_modified[f] = []
                all_files_modified[f].append(agent_id)

        # Detect conflicts
        conflicts = self._detect_conflicts(
            all_files_created,
            all_files_modified,
            completed,
            failed
        )

        # Attempt resolution if requested
        if resolve_conflicts:
            conflicts = self._resolve_conflicts(conflicts)

        # Collect summaries
        agent_summaries = []
        for r in agent_results:
            result = r.get("result", {}) or {}
            agent_summaries.append({
                "agent_id": r.get("agent_id"),
                "status": r.get("status"),
                "summary": result.get("summary", "No summary provided"),
                "files_created": result.get("files_created", []),
                "files_modified": result.get("files_modified", [])
            })

        # Generate combined summary
        combined_summary = self._generate_combined_summary(
            completed,
            failed,
            conflicts
        )

        # Determine overall success
        success = (
            len(failed) == 0 and
            not any(c.requires_human_review for c in conflicts)
        )

        return MergedResult(
            mission_id=self.mission_id,
            success=success,
            total_agents=len(agent_results),
            completed_agents=len(completed),
            failed_agents=len(failed),
            files_created=list(all_files_created.keys()),
            files_modified=list(all_files_modified.keys()),
            conflicts=conflicts,
            agent_summaries=agent_summaries,
            combined_summary=combined_summary,
            timestamp=datetime.now().isoformat()
        )

    def _detect_conflicts(
        self,
        files_created: Dict[str, List[str]],
        files_modified: Dict[str, List[str]],
        completed: List[Dict],
        failed: List[Dict]
    ) -> List[Conflict]:
        """Detect conflicts between agent results."""
        conflicts = []

        # Check for files created by multiple agents
        for file_path, agents in files_created.items():
            if len(agents) > 1:
                conflicts.append(Conflict(
                    conflict_type=ConflictType.FILE_BOTH_CREATED,
                    agents_involved=agents,
                    description=f"File '{file_path}' created by multiple agents",
                    file_path=file_path,
                    requires_human_review=True
                ))

        # Check for files modified by multiple agents
        for file_path, agents in files_modified.items():
            if len(agents) > 1:
                conflicts.append(Conflict(
                    conflict_type=ConflictType.FILE_BOTH_MODIFIED,
                    agents_involved=agents,
                    description=f"File '{file_path}' modified by multiple agents",
                    file_path=file_path,
                    requires_human_review=True
                ))

        # Check for partial failures
        if failed and completed:
            conflicts.append(Conflict(
                conflict_type=ConflictType.PARTIAL_FAILURE,
                agents_involved=[r.get("agent_id", "unknown") for r in failed],
                description=f"{len(failed)} of {len(failed) + len(completed)} agents failed",
                requires_human_review=len(failed) > len(completed)  # Review if majority failed
            ))

        return conflicts

    def _resolve_conflicts(self, conflicts: List[Conflict]) -> List[Conflict]:
        """Attempt to automatically resolve conflicts."""
        resolved = []

        for conflict in conflicts:
            if conflict.conflict_type == ConflictType.FILE_BOTH_CREATED:
                # For now, mark as needing review
                # In future: could compare contents, take first, etc.
                conflict.resolution = "Requires manual review to choose version"
                resolved.append(conflict)

            elif conflict.conflict_type == ConflictType.FILE_BOTH_MODIFIED:
                # Could potentially merge changes if non-overlapping
                conflict.resolution = "Requires manual review to merge changes"
                resolved.append(conflict)

            elif conflict.conflict_type == ConflictType.PARTIAL_FAILURE:
                # Partial failure is informational, not blocking
                conflict.resolution = "Proceeding with successful agents"
                conflict.requires_human_review = False
                resolved.append(conflict)

            else:
                resolved.append(conflict)

        return resolved

    def _generate_combined_summary(
        self,
        completed: List[Dict],
        failed: List[Dict],
        conflicts: List[Conflict]
    ) -> str:
        """Generate a combined summary from all agent results."""
        lines = []

        lines.append(f"# Mission Summary: {self.mission_id}")
        lines.append("")

        # Status overview
        lines.append("## Status")
        lines.append(f"- Completed: {len(completed)} agents")
        lines.append(f"- Failed: {len(failed)} agents")
        lines.append(f"- Conflicts: {len(conflicts)}")
        lines.append("")

        # Completed agent summaries
        if completed:
            lines.append("## Completed Work")
            for r in completed:
                result = r.get("result", {}) or {}
                agent_id = r.get("agent_id", "unknown")
                summary = result.get("summary", "No summary")
                lines.append(f"### Agent: {agent_id}")
                lines.append(f"{summary}")
                lines.append("")

        # Failed agents
        if failed:
            lines.append("## Failures")
            for r in failed:
                agent_id = r.get("agent_id", "unknown")
                error = r.get("error", "Unknown error")
                lines.append(f"- **{agent_id}**: {error}")
            lines.append("")

        # Conflicts
        if conflicts:
            lines.append("## Conflicts")
            for c in conflicts:
                lines.append(f"- **{c.conflict_type.value}**: {c.description}")
                if c.resolution:
                    lines.append(f"  - Resolution: {c.resolution}")
            lines.append("")

        return "\n".join(lines)

    def merge_code_changes(
        self,
        agent_results: List[Dict[str, Any]],
        workspace_dir: Path
    ) -> Tuple[List[str], List[Conflict]]:
        """
        Attempt to merge code changes from multiple agents.

        Args:
            agent_results: List of agent results
            workspace_dir: Directory containing the code

        Returns:
            Tuple of (files_merged, unresolved_conflicts)
        """
        files_merged = []
        unresolved = []

        # Group changes by file
        changes_by_file: Dict[str, List[Dict]] = {}

        for r in agent_results:
            result = r.get("result", {}) or {}
            agent_id = r.get("agent_id", "unknown")

            for f in result.get("files_modified", []):
                if f not in changes_by_file:
                    changes_by_file[f] = []
                changes_by_file[f].append({
                    "agent": agent_id,
                    "changes": result.get("changes", [])
                })

        # Process each file
        for file_path, changes in changes_by_file.items():
            if len(changes) == 1:
                # Single agent modified - no conflict
                files_merged.append(file_path)
            else:
                # Multiple agents - conflict
                unresolved.append(Conflict(
                    conflict_type=ConflictType.FILE_BOTH_MODIFIED,
                    agents_involved=[c["agent"] for c in changes],
                    description=f"File '{file_path}' modified by {len(changes)} agents",
                    file_path=file_path,
                    requires_human_review=True
                ))

        return files_merged, unresolved


# Convenience function
def aggregate_mission(mission_id: str) -> MergedResult:
    """
    Quick convenience function to aggregate a mission's results.

    Args:
        mission_id: The mission ID to aggregate

    Returns:
        MergedResult
    """
    aggregator = ResultAggregator(mission_id)
    results = aggregator.collect()
    return aggregator.merge(results)


if __name__ == "__main__":
    # Self-test
    print("Result Aggregator - Self Test")
    print("=" * 50)

    # Create mock results for testing
    test_results = [
        {
            "agent_id": "agent_1",
            "status": "completed",
            "result": {
                "summary": "Implemented feature A",
                "files_created": ["new_file.py"],
                "files_modified": ["existing.py"]
            },
            "error": None
        },
        {
            "agent_id": "agent_2",
            "status": "completed",
            "result": {
                "summary": "Implemented feature B",
                "files_created": [],
                "files_modified": ["existing.py", "other.py"]  # Conflict!
            },
            "error": None
        },
        {
            "agent_id": "agent_3",
            "status": "failed",
            "result": None,
            "error": "Timeout exceeded"
        }
    ]

    # Test aggregation
    aggregator = ResultAggregator("test_mission")
    merged = aggregator.merge(test_results)

    print(f"Mission: {merged.mission_id}")
    print(f"Success: {merged.success}")
    print(f"Total agents: {merged.total_agents}")
    print(f"Completed: {merged.completed_agents}")
    print(f"Failed: {merged.failed_agents}")
    print(f"Conflicts: {len(merged.conflicts)}")

    if merged.conflicts:
        print("\nConflicts:")
        for c in merged.conflicts:
            print(f"  - {c.conflict_type.value}: {c.description}")

    print(f"\nCombined summary:\n{merged.combined_summary}")

    print("\nResult Aggregator self-test complete!")
