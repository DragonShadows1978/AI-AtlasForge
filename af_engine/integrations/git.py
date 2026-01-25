"""
af_engine.integrations.git - Checkpoint-Based Git Commits

This integration creates git commits at checkpoints during mission execution.
"""

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class GitIntegration(BaseIntegrationHandler):
    """
    Creates git commits at checkpoints during mission execution.

    Commits are created after BUILDING stage completion and
    at mission end to preserve work.
    """

    name = "git"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self, workspace_dir: Optional[Path] = None):
        """Initialize git integration."""
        super().__init__()
        self.workspace_dir = workspace_dir or Path.cwd()

    def _check_availability(self) -> bool:
        """Check if git is available."""
        try:
            result = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def on_stage_completed(self, event: Event) -> None:
        """Create commit after BUILDING stage."""
        if event.stage == "BUILDING":
            self._create_checkpoint_commit(
                f"[AF] Build checkpoint - {event.mission_id}",
                event.data.get("files_created", []) + event.data.get("files_modified", [])
            )

    def on_cycle_completed(self, event: Event) -> None:
        """Create commit at end of each cycle."""
        cycle_number = event.data.get("cycle_number", 0)
        self._create_checkpoint_commit(
            f"[AF] Cycle {cycle_number} complete - {event.mission_id}",
            []  # Commit all staged changes
        )

    def on_mission_completed(self, event: Event) -> None:
        """Create final commit when mission completes."""
        self._create_checkpoint_commit(
            f"[AF] Mission complete - {event.mission_id}",
            []
        )

    def _create_checkpoint_commit(
        self,
        message: str,
        files: List[str],
    ) -> bool:
        """Create a git commit with specified files."""
        try:
            # Stage files (or all if no specific files)
            if files:
                for f in files:
                    subprocess.run(
                        ["git", "add", f],
                        cwd=self.workspace_dir,
                        capture_output=True,
                        timeout=30,
                    )
            else:
                subprocess.run(
                    ["git", "add", "-A"],
                    cwd=self.workspace_dir,
                    capture_output=True,
                    timeout=30,
                )

            # Check if there are changes to commit
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_dir,
                capture_output=True,
                timeout=10,
            )

            if not status.stdout.strip():
                logger.debug("No changes to commit")
                return False

            # Create commit
            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.workspace_dir,
                capture_output=True,
                timeout=30,
            )

            if result.returncode == 0:
                logger.info(f"Git commit created: {message}")
                return True
            else:
                logger.warning(f"Git commit failed: {result.stderr.decode()}")
                return False

        except Exception as e:
            logger.warning(f"Git operation failed: {e}")
            return False
