#!/usr/bin/env python3
"""
Checkpoint Manager for Hierarchical Agent Coordination

Provides file-based synchronization for parallel Claude agents.
Each agent writes a checkpoint file with its status and results.
Parent agents can poll or wait for completion of child agents.

Design:
- Atomic file writes using temporary files + rename
- JSON-based checkpoint format for flexibility
- Polling-based wait (no external dependencies like inotify)
- Timeout-aware waiting with configurable intervals
"""

import json
import time
import os
import fcntl
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass, asdict
from enum import Enum
import tempfile
import logging

logger = logging.getLogger("checkpoint_manager")

# Base directory for checkpoints
BASE_DIR = Path("/home/vader/mini-mind-v2")
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"

# Ensure checkpoint directory exists
CHECKPOINTS_DIR.mkdir(exist_ok=True)


class CheckpointStatus(Enum):
    """Status states for agent checkpoints."""
    PENDING = "pending"          # Agent started but not complete
    IN_PROGRESS = "in_progress"  # Agent actively working
    COMPLETED = "completed"      # Agent finished successfully
    FAILED = "failed"            # Agent encountered error
    TIMEOUT = "timeout"          # Agent timed out


@dataclass
class Checkpoint:
    """Represents a single agent checkpoint."""
    agent_id: str
    mission_id: str
    status: CheckpointStatus
    created_at: str
    updated_at: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    progress: float = 0.0  # 0.0 to 1.0
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['status'] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'Checkpoint':
        data['status'] = CheckpointStatus(data['status'])
        return cls(**data)


class CheckpointManager:
    """
    Manages checkpoints for a specific mission.

    Usage:
        mgr = CheckpointManager("my_mission")

        # Child agent creates checkpoint
        mgr.create_checkpoint("agent_1", CheckpointStatus.IN_PROGRESS)
        # ... do work ...
        mgr.update_checkpoint("agent_1", CheckpointStatus.COMPLETED, result={"files": [...])

        # Parent waits for children
        if mgr.wait_for_all(["agent_1", "agent_2"], timeout=600):
            results = mgr.get_all_results()
    """

    def __init__(self, mission_id: str):
        self.mission_id = mission_id
        self.checkpoint_dir = CHECKPOINTS_DIR / mission_id
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.checkpoint_dir / ".lock"

    def _get_checkpoint_path(self, agent_id: str) -> Path:
        """Get the path for an agent's checkpoint file."""
        return self.checkpoint_dir / f"{agent_id}.json"

    def _atomic_write(self, path: Path, data: dict):
        """Write data atomically using temp file + rename."""
        # Create temp file in same directory (same filesystem for atomic rename)
        fd, temp_path = tempfile.mkstemp(
            suffix='.tmp',
            dir=str(self.checkpoint_dir)
        )
        try:
            with os.fdopen(fd, 'w') as f:
                json.dump(data, f, indent=2)
            # Atomic rename
            os.rename(temp_path, path)
        except Exception:
            # Clean up temp file on error
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise

    def _read_checkpoint(self, agent_id: str) -> Optional[Checkpoint]:
        """Read a checkpoint file safely."""
        path = self._get_checkpoint_path(agent_id)
        if not path.exists():
            return None
        try:
            with open(path, 'r') as f:
                data = json.load(f)
            return Checkpoint.from_dict(data)
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning(f"Corrupt checkpoint for {agent_id}: {e}")
            return None

    def create_checkpoint(
        self,
        agent_id: str,
        status: CheckpointStatus = CheckpointStatus.PENDING,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Checkpoint:
        """
        Create a new checkpoint for an agent.

        Args:
            agent_id: Unique identifier for this agent
            status: Initial status
            metadata: Optional metadata to attach

        Returns:
            The created Checkpoint
        """
        now = datetime.now().isoformat()
        checkpoint = Checkpoint(
            agent_id=agent_id,
            mission_id=self.mission_id,
            status=status,
            created_at=now,
            updated_at=now,
            metadata=metadata or {}
        )

        path = self._get_checkpoint_path(agent_id)
        self._atomic_write(path, checkpoint.to_dict())
        logger.info(f"Created checkpoint: {agent_id} ({status.value})")

        return checkpoint

    def update_checkpoint(
        self,
        agent_id: str,
        status: Optional[CheckpointStatus] = None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        progress: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[Checkpoint]:
        """
        Update an existing checkpoint.

        Args:
            agent_id: The agent whose checkpoint to update
            status: New status (if changing)
            result: Result data (usually set on completion)
            error: Error message (if failed)
            progress: Progress 0.0-1.0
            metadata: Additional metadata to merge

        Returns:
            Updated Checkpoint, or None if not found
        """
        checkpoint = self._read_checkpoint(agent_id)
        if checkpoint is None:
            logger.warning(f"Cannot update non-existent checkpoint: {agent_id}")
            return None

        if status is not None:
            checkpoint.status = status
        if result is not None:
            checkpoint.result = result
        if error is not None:
            checkpoint.error = error
        if progress is not None:
            checkpoint.progress = progress
        if metadata is not None:
            if checkpoint.metadata:
                checkpoint.metadata.update(metadata)
            else:
                checkpoint.metadata = metadata

        checkpoint.updated_at = datetime.now().isoformat()

        path = self._get_checkpoint_path(agent_id)
        self._atomic_write(path, checkpoint.to_dict())
        logger.info(f"Updated checkpoint: {agent_id} ({checkpoint.status.value})")

        return checkpoint

    def mark_completed(
        self,
        agent_id: str,
        result: Dict[str, Any]
    ) -> Optional[Checkpoint]:
        """Convenience method to mark an agent as completed with results."""
        return self.update_checkpoint(
            agent_id,
            status=CheckpointStatus.COMPLETED,
            result=result,
            progress=1.0
        )

    def mark_failed(
        self,
        agent_id: str,
        error: str
    ) -> Optional[Checkpoint]:
        """Convenience method to mark an agent as failed."""
        return self.update_checkpoint(
            agent_id,
            status=CheckpointStatus.FAILED,
            error=error
        )

    def is_complete(self, agent_id: str) -> bool:
        """Check if an agent's checkpoint indicates completion."""
        checkpoint = self._read_checkpoint(agent_id)
        if checkpoint is None:
            return False
        return checkpoint.status in (
            CheckpointStatus.COMPLETED,
            CheckpointStatus.FAILED,
            CheckpointStatus.TIMEOUT
        )

    def are_all_complete(self, agent_ids: List[str]) -> bool:
        """Check if all specified agents are complete."""
        return all(self.is_complete(aid) for aid in agent_ids)

    def get_completion_status(self, agent_ids: List[str]) -> Dict[str, str]:
        """Get completion status for all agents."""
        statuses = {}
        for agent_id in agent_ids:
            checkpoint = self._read_checkpoint(agent_id)
            if checkpoint:
                statuses[agent_id] = checkpoint.status.value
            else:
                statuses[agent_id] = "not_found"
        return statuses

    def wait_for_all(
        self,
        agent_ids: List[str],
        timeout: int = 3600,
        poll_interval: float = 2.0,
        progress_callback: Optional[Callable[[Dict[str, str]], None]] = None
    ) -> bool:
        """
        Wait for all specified agents to complete.

        Args:
            agent_ids: List of agent IDs to wait for
            timeout: Maximum seconds to wait
            poll_interval: Seconds between status checks
            progress_callback: Optional function called with status updates

        Returns:
            True if all completed successfully, False if timeout or any failed
        """
        start_time = time.time()

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout:
                logger.warning(f"Timeout waiting for agents: {agent_ids}")
                # Mark remaining as timed out
                for agent_id in agent_ids:
                    if not self.is_complete(agent_id):
                        self.update_checkpoint(
                            agent_id,
                            status=CheckpointStatus.TIMEOUT
                        )
                return False

            # Check status
            statuses = self.get_completion_status(agent_ids)

            if progress_callback:
                progress_callback(statuses)

            # Check if all done
            if self.are_all_complete(agent_ids):
                # Check if any failed
                all_success = all(
                    statuses.get(aid) == CheckpointStatus.COMPLETED.value
                    for aid in agent_ids
                )
                return all_success

            # Wait before next check
            time.sleep(poll_interval)

    def get_all_results(
        self,
        agent_ids: Optional[List[str]] = None
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """
        Get results from all (or specified) agents.

        Args:
            agent_ids: Optional list of specific agents. If None, get all.

        Returns:
            Dict mapping agent_id -> result (or None if no result)
        """
        if agent_ids is None:
            # Get all checkpoints in directory
            agent_ids = [
                p.stem for p in self.checkpoint_dir.glob("*.json")
                if not p.name.startswith('.')
            ]

        results = {}
        for agent_id in agent_ids:
            checkpoint = self._read_checkpoint(agent_id)
            if checkpoint and checkpoint.result:
                results[agent_id] = checkpoint.result
            else:
                results[agent_id] = None

        return results

    def get_checkpoint(self, agent_id: str) -> Optional[Checkpoint]:
        """Get full checkpoint for an agent."""
        return self._read_checkpoint(agent_id)

    def list_agents(self) -> List[str]:
        """List all agents with checkpoints in this mission."""
        return [
            p.stem for p in self.checkpoint_dir.glob("*.json")
            if not p.name.startswith('.')
        ]

    def cleanup(self, keep_completed: bool = False):
        """
        Clean up checkpoint files.

        Args:
            keep_completed: If True, only remove pending/in-progress checkpoints
        """
        for agent_id in self.list_agents():
            checkpoint = self._read_checkpoint(agent_id)
            if checkpoint:
                if keep_completed and checkpoint.status == CheckpointStatus.COMPLETED:
                    continue
            path = self._get_checkpoint_path(agent_id)
            path.unlink()
            logger.info(f"Removed checkpoint: {agent_id}")

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all checkpoints for this mission."""
        agents = self.list_agents()
        summary = {
            "mission_id": self.mission_id,
            "total_agents": len(agents),
            "by_status": {},
            "agents": []
        }

        for agent_id in agents:
            checkpoint = self._read_checkpoint(agent_id)
            if checkpoint:
                status = checkpoint.status.value
                summary["by_status"][status] = summary["by_status"].get(status, 0) + 1
                summary["agents"].append({
                    "id": agent_id,
                    "status": status,
                    "progress": checkpoint.progress,
                    "has_result": checkpoint.result is not None
                })

        return summary


# Convenience function for quick checkpoint operations
def quick_checkpoint(mission_id: str, agent_id: str, status: str, result: dict = None):
    """
    Quick one-liner for creating/updating checkpoints from subprocess agents.

    Usage in agent code:
        from checkpoint_manager import quick_checkpoint
        quick_checkpoint("mission_123", "agent_1", "completed", {"files": ["a.py"]})
    """
    mgr = CheckpointManager(mission_id)
    status_enum = CheckpointStatus(status)

    if not mgr._read_checkpoint(agent_id):
        mgr.create_checkpoint(agent_id, status_enum)

    if result or status_enum in (CheckpointStatus.COMPLETED, CheckpointStatus.FAILED):
        mgr.update_checkpoint(agent_id, status=status_enum, result=result)


if __name__ == "__main__":
    # Self-test
    print("Checkpoint Manager - Self Test")
    print("=" * 50)

    # Create a test mission
    test_mission = f"test_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    mgr = CheckpointManager(test_mission)

    # Test creating checkpoints
    cp1 = mgr.create_checkpoint("agent_1", CheckpointStatus.IN_PROGRESS)
    cp2 = mgr.create_checkpoint("agent_2", CheckpointStatus.IN_PROGRESS)
    print(f"Created checkpoints: agent_1, agent_2")

    # Test updating
    mgr.update_checkpoint("agent_1", progress=0.5)
    mgr.mark_completed("agent_1", {"files": ["test.py"]})
    mgr.mark_failed("agent_2", "Test error")
    print("Updated checkpoints")

    # Test reading
    summary = mgr.get_summary()
    print(f"Summary: {json.dumps(summary, indent=2)}")

    # Test results
    results = mgr.get_all_results()
    print(f"Results: {results}")

    # Cleanup
    mgr.cleanup()
    print(f"Cleaned up test mission: {test_mission}")

    print("\nCheckpoint Manager self-test complete!")
