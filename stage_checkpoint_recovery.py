#!/usr/bin/env python3
"""
Stage Checkpoint Recovery System

This module provides crash recovery for the RDE by:
1. Checkpointing progress at key points within each stage
2. Detecting incomplete missions on startup
3. Generating recovery prompts to resume from last checkpoint
4. Tracking files created for rollback if needed

Usage:
    # During mission execution
    checkpoint = StageCheckpoint(mission_id, stage)
    checkpoint.save_progress({"step": "building_feature_x"}, files_created=["src/x.py"])

    # On startup
    recovery = detect_incomplete_mission()
    if recovery:
        context = recovery.generate_recovery_context()
        # Inject into prompt
"""

import json
import os
import shutil
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field, asdict

import io_utils

logger = logging.getLogger(__name__)

# Paths - use centralized configuration
from atlasforge_config import BASE_DIR, STATE_DIR, MISSION_PATH
CHECKPOINTS_DIR = BASE_DIR / "checkpoints"

# Ensure directories exist
CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class CheckpointData:
    """Data stored in a stage checkpoint"""
    checkpoint_id: str
    mission_id: str
    stage: str
    timestamp: str
    progress: Dict[str, Any]  # Stage-specific progress data
    files_created: List[str]
    files_modified: List[str]
    recovery_hint: str  # Human-readable hint for recovery
    iteration: int = 0
    cycle: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'CheckpointData':
        return cls(**data)


class StageCheckpoint:
    """
    Manages checkpoints within a single stage for crash recovery.

    Creates checkpoints at key points during stage execution,
    enabling recovery if Claude crashes mid-stage.
    """

    # Maximum length for mission_id to prevent filesystem errors
    MAX_MISSION_ID_LENGTH = 100
    # Valid stages
    VALID_STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]

    def __init__(self, mission_id: str, stage: str):
        """
        Initialize checkpoint manager for a stage.

        Args:
            mission_id: Current mission identifier
            stage: Current stage name (PLANNING, BUILDING, etc.)

        Raises:
            ValueError: If mission_id or stage is invalid
        """
        # Input validation - prevent path traversal and filesystem issues
        self._validate_mission_id(mission_id)
        self._validate_stage(stage)

        self.mission_id = mission_id
        self.stage = stage
        self.checkpoint_dir = CHECKPOINTS_DIR / mission_id / stage
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_file = self.checkpoint_dir / "checkpoint.json"
        self.files_backup_dir = self.checkpoint_dir / "file_backups"

    @classmethod
    def _validate_mission_id(cls, mission_id: str) -> None:
        """
        Validate mission_id to prevent security issues and filesystem errors.

        Args:
            mission_id: Mission identifier to validate

        Raises:
            ValueError: If mission_id is invalid
        """
        if not mission_id:
            raise ValueError("mission_id cannot be empty")

        if len(mission_id) > cls.MAX_MISSION_ID_LENGTH:
            raise ValueError(
                f"mission_id too long (max {cls.MAX_MISSION_ID_LENGTH} chars, got {len(mission_id)})"
            )

        # Check for path traversal characters
        dangerous_chars = ['/', '\\', '..', '\x00', '\n', '\r']
        for char in dangerous_chars:
            if char in mission_id:
                raise ValueError(f"mission_id contains invalid character: {repr(char)}")

        # Only allow safe characters (alphanumeric, underscore, hyphen)
        import re
        if not re.match(r'^[a-zA-Z0-9_-]+$', mission_id):
            raise ValueError(
                "mission_id must contain only alphanumeric characters, underscores, or hyphens"
            )

    @classmethod
    def _validate_stage(cls, stage: str) -> None:
        """
        Validate stage name.

        Args:
            stage: Stage name to validate

        Raises:
            ValueError: If stage is invalid
        """
        if not stage:
            raise ValueError("stage cannot be empty")

        if stage not in cls.VALID_STAGES:
            raise ValueError(
                f"Invalid stage '{stage}'. Must be one of: {', '.join(cls.VALID_STAGES)}"
            )

    def save_progress(
        self,
        progress: Dict[str, Any],
        files_created: Optional[List[str]] = None,
        files_modified: Optional[List[str]] = None,
        recovery_hint: str = ""
    ) -> str:
        """
        Save a checkpoint with current progress.

        Args:
            progress: Stage-specific progress data (e.g., {"step": "parsing_complete"})
            files_created: List of files created so far in this stage
            files_modified: List of files modified (backed up)
            recovery_hint: Human-readable hint for resuming

        Returns:
            Checkpoint ID
        """
        # Get current mission state
        mission = io_utils.atomic_read_json(MISSION_PATH, {})

        checkpoint_id = f"{self.mission_id}_{self.stage}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        checkpoint = CheckpointData(
            checkpoint_id=checkpoint_id,
            mission_id=self.mission_id,
            stage=self.stage,
            timestamp=datetime.now().isoformat(),
            progress=progress,
            files_created=files_created or [],
            files_modified=files_modified or [],
            recovery_hint=recovery_hint,
            iteration=mission.get("iteration", 0),
            cycle=mission.get("current_cycle", 1)
        )

        # Backup modified files
        if files_modified:
            self._backup_files(files_modified)

        # Save checkpoint atomically
        io_utils.atomic_write_json(self.checkpoint_file, checkpoint.to_dict())

        logger.info(f"Checkpoint saved: {checkpoint_id}")
        return checkpoint_id

    def _backup_files(self, files: List[str]):
        """Backup files before modification for potential rollback"""
        self.files_backup_dir.mkdir(parents=True, exist_ok=True)

        for file_path in files:
            try:
                src = Path(file_path)
                if src.exists():
                    # Create backup with timestamp
                    backup_name = f"{src.name}.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    backup_path = self.files_backup_dir / backup_name
                    shutil.copy2(src, backup_path)
                    logger.debug(f"Backed up: {file_path} -> {backup_path}")
            except Exception as e:
                logger.warning(f"Failed to backup {file_path}: {e}")

    def get_latest_checkpoint(self) -> Optional[CheckpointData]:
        """
        Get the most recent checkpoint for this stage.

        Returns:
            CheckpointData if checkpoint exists, None otherwise
        """
        if not self.checkpoint_file.exists():
            return None

        try:
            data = io_utils.atomic_read_json(self.checkpoint_file, None)
            if data:
                return CheckpointData.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to read checkpoint: {e}")

        return None

    def clear_checkpoint(self):
        """Clear checkpoint after successful stage completion"""
        try:
            if self.checkpoint_file.exists():
                self.checkpoint_file.unlink()
            # Keep backups for a while in case needed
            logger.info(f"Checkpoint cleared for {self.mission_id}/{self.stage}")
        except Exception as e:
            logger.warning(f"Failed to clear checkpoint: {e}")

    def generate_recovery_context(self) -> str:
        """
        Generate context for resuming from this checkpoint.

        Returns:
            Formatted context string to inject into prompt
        """
        checkpoint = self.get_latest_checkpoint()
        if not checkpoint:
            return ""

        # Calculate time since crash
        try:
            checkpoint_time = datetime.fromisoformat(checkpoint.timestamp)
            time_since = datetime.now() - checkpoint_time
            time_str = f"{time_since.total_seconds() / 60:.1f} minutes ago"
        except Exception:
            time_str = "unknown time ago"

        # Build file list
        files_info = ""
        if checkpoint.files_created:
            files_info += f"\n  Files created: {', '.join(checkpoint.files_created[:5])}"
            if len(checkpoint.files_created) > 5:
                files_info += f" (+{len(checkpoint.files_created) - 5} more)"
        if checkpoint.files_modified:
            files_info += f"\n  Files modified: {', '.join(checkpoint.files_modified[:5])}"

        # Build progress summary
        progress_str = json.dumps(checkpoint.progress, indent=2) if checkpoint.progress else "No progress data"

        return f"""
=== CRASH RECOVERY ===
Your previous session crashed during the **{checkpoint.stage}** stage ({time_str}).

**Mission:** {checkpoint.mission_id}
**Iteration:** {checkpoint.iteration}
**Cycle:** {checkpoint.cycle}

**Progress at crash:**
{progress_str}
{files_info}

**Recovery hint:** {checkpoint.recovery_hint or 'No specific hint'}

IMPORTANT: Resume from where you left off. Do NOT restart from scratch.
Check which files already exist before recreating them.
=== END CRASH RECOVERY ===
"""


class MissionRecoveryManager:
    """
    Manages recovery detection across all missions.

    Checks for incomplete missions on startup and provides
    recovery options.
    """

    def __init__(self):
        self.checkpoints_dir = CHECKPOINTS_DIR

    def detect_incomplete_missions(self) -> List[Tuple[str, str, CheckpointData]]:
        """
        Detect all missions with incomplete stages.

        Returns:
            List of (mission_id, stage, checkpoint) tuples
        """
        incomplete = []

        if not self.checkpoints_dir.exists():
            return incomplete

        for mission_dir in self.checkpoints_dir.iterdir():
            if not mission_dir.is_dir():
                continue

            mission_id = mission_dir.name

            for stage_dir in mission_dir.iterdir():
                if not stage_dir.is_dir():
                    continue

                stage = stage_dir.name
                checkpoint_file = stage_dir / "checkpoint.json"

                if checkpoint_file.exists():
                    try:
                        data = io_utils.atomic_read_json(checkpoint_file, None)
                        if data:
                            checkpoint = CheckpointData.from_dict(data)
                            incomplete.append((mission_id, stage, checkpoint))
                    except Exception as e:
                        logger.warning(f"Error reading checkpoint for {mission_id}/{stage}: {e}")

        return incomplete

    def get_current_mission_recovery(self) -> Optional[StageCheckpoint]:
        """
        Get recovery checkpoint for the current mission.

        Returns:
            StageCheckpoint if recovery available, None otherwise
        """
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get("mission_id")
        current_stage = mission.get("current_stage")

        if not mission_id or not current_stage:
            return None

        # No recovery needed if mission is complete
        if current_stage == "COMPLETE":
            return None

        # Check for checkpoint in current stage
        checkpoint = StageCheckpoint(mission_id, current_stage)
        if checkpoint.get_latest_checkpoint():
            return checkpoint

        return None

    def clean_old_checkpoints(self, max_age_days: int = 7):
        """
        Remove checkpoints older than max_age_days.

        Args:
            max_age_days: Maximum age in days before removal
        """
        if not self.checkpoints_dir.exists():
            return

        cutoff = datetime.now().timestamp() - (max_age_days * 24 * 60 * 60)
        removed = 0

        for mission_dir in self.checkpoints_dir.iterdir():
            if not mission_dir.is_dir():
                continue

            for stage_dir in mission_dir.iterdir():
                if not stage_dir.is_dir():
                    continue

                checkpoint_file = stage_dir / "checkpoint.json"
                if checkpoint_file.exists():
                    try:
                        mtime = checkpoint_file.stat().st_mtime
                        if mtime < cutoff:
                            shutil.rmtree(stage_dir)
                            removed += 1
                    except Exception as e:
                        logger.warning(f"Error cleaning checkpoint: {e}")

            # Remove empty mission dirs
            if mission_dir.exists() and not any(mission_dir.iterdir()):
                try:
                    mission_dir.rmdir()
                except Exception:
                    pass

        logger.info(f"Cleaned {removed} old checkpoints")


def detect_incomplete_mission() -> Optional[StageCheckpoint]:
    """
    Quick check for incomplete current mission.

    Returns:
        StageCheckpoint if recovery available, None otherwise
    """
    manager = MissionRecoveryManager()
    return manager.get_current_mission_recovery()


def save_stage_progress(
    progress: Dict[str, Any],
    files_created: Optional[List[str]] = None,
    recovery_hint: str = ""
) -> Optional[str]:
    """
    Convenience function to save progress for current mission/stage.

    Args:
        progress: Progress data dict
        files_created: Files created so far
        recovery_hint: Hint for recovery

    Returns:
        Checkpoint ID if saved, None if no active mission
    """
    mission = io_utils.atomic_read_json(MISSION_PATH, {})
    mission_id = mission.get("mission_id")
    current_stage = mission.get("current_stage")

    if not mission_id or not current_stage:
        return None

    checkpoint = StageCheckpoint(mission_id, current_stage)
    return checkpoint.save_progress(
        progress=progress,
        files_created=files_created,
        recovery_hint=recovery_hint
    )


def clear_current_checkpoint():
    """Clear checkpoint for current mission/stage after successful completion"""
    mission = io_utils.atomic_read_json(MISSION_PATH, {})
    mission_id = mission.get("mission_id")
    current_stage = mission.get("current_stage")

    if mission_id and current_stage:
        checkpoint = StageCheckpoint(mission_id, current_stage)
        checkpoint.clear_checkpoint()


def get_recovery_context() -> str:
    """
    Get recovery context if available for current mission.

    Returns:
        Recovery context string, or empty string if no recovery needed
    """
    checkpoint = detect_incomplete_mission()
    if checkpoint:
        return checkpoint.generate_recovery_context()
    return ""


# Auto-checkpoint decorator for stage functions
def with_checkpoint(stage_name: str):
    """
    Decorator to automatically checkpoint stage function execution.

    Usage:
        @with_checkpoint("BUILDING")
        def build_feature(args):
            # This progress will be checkpointed automatically
            return result
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            mission = io_utils.atomic_read_json(MISSION_PATH, {})
            mission_id = mission.get("mission_id", "unknown")

            checkpoint = StageCheckpoint(mission_id, stage_name)

            # Save initial checkpoint
            checkpoint.save_progress(
                progress={"status": "started", "function": func.__name__},
                recovery_hint=f"Function {func.__name__} was executing"
            )

            try:
                result = func(*args, **kwargs)

                # Clear checkpoint on success
                checkpoint.clear_checkpoint()
                return result

            except Exception as e:
                # Save error checkpoint
                checkpoint.save_progress(
                    progress={"status": "error", "error": str(e)},
                    recovery_hint=f"Function {func.__name__} failed with: {str(e)[:100]}"
                )
                raise

        return wrapper
    return decorator


if __name__ == "__main__":
    # Test/demo usage
    logging.basicConfig(level=logging.INFO)

    # Check for incomplete missions
    manager = MissionRecoveryManager()
    incomplete = manager.detect_incomplete_missions()

    print(f"Found {len(incomplete)} incomplete missions:")
    for mission_id, stage, checkpoint in incomplete:
        print(f"  - {mission_id}: {stage} (checkpointed at {checkpoint.timestamp})")

    # Demo saving a checkpoint
    print("\n--- Demo: Saving checkpoint ---")
    save_stage_progress(
        progress={"step": "feature_implementation", "feature": "test_feature"},
        files_created=["test_file.py"],
        recovery_hint="Was implementing test_feature"
    )

    # Get recovery context
    context = get_recovery_context()
    if context:
        print("\nRecovery context:")
        print(context)
    else:
        print("\nNo recovery needed")

    # Clean old checkpoints
    manager.clean_old_checkpoints(max_age_days=7)
