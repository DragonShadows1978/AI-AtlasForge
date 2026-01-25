"""
af_engine.integrations.recovery - Checkpoint and Crash Recovery

This integration creates checkpoints and enables crash recovery.
"""

import logging
from pathlib import Path
from typing import List, Optional
import json

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class RecoveryIntegration(BaseIntegrationHandler):
    """
    Creates checkpoints and enables crash recovery.

    Runs at HIGH priority to ensure checkpoints are created
    before other integrations that might depend on them.
    """

    name = "recovery"
    priority = IntegrationPriority.HIGH
    subscriptions = [
        StageEvent.STAGE_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_STARTED,
        StageEvent.RESPONSE_RECEIVED,
    ]

    def __init__(self, checkpoint_dir: Optional[Path] = None):
        """Initialize recovery with checkpoint directory."""
        super().__init__()
        self.checkpoint_dir = checkpoint_dir
        self.last_checkpoint = None

    def on_mission_started(self, event: Event) -> None:
        """Initialize checkpoint directory for mission."""
        if self.checkpoint_dir is None:
            # Use mission-specific directory
            mission_id = event.mission_id
            self.checkpoint_dir = Path(f".af_checkpoints/{mission_id}")

        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(f"Recovery initialized for mission {event.mission_id}")

    def on_stage_started(self, event: Event) -> None:
        """Create checkpoint at stage start."""
        self._create_checkpoint(event, "stage_start")

    def on_stage_completed(self, event: Event) -> None:
        """Create checkpoint at stage completion."""
        self._create_checkpoint(event, "stage_complete")

    def on_response_received(self, event: Event) -> None:
        """Create checkpoint after receiving response."""
        self._create_checkpoint(event, "response")

    def _create_checkpoint(self, event: Event, checkpoint_type: str) -> None:
        """Create a checkpoint file."""
        if self.checkpoint_dir is None:
            return

        checkpoint_data = {
            "type": checkpoint_type,
            "stage": event.stage,
            "mission_id": event.mission_id,
            "timestamp": event.timestamp.isoformat(),
            "data": event.data,
        }

        checkpoint_file = self.checkpoint_dir / f"checkpoint_{event.stage}_{checkpoint_type}.json"

        try:
            with open(checkpoint_file, "w") as f:
                json.dump(checkpoint_data, f, indent=2, default=str)
            self.last_checkpoint = checkpoint_file
            logger.debug(f"Checkpoint created: {checkpoint_file}")
        except Exception as e:
            logger.warning(f"Failed to create checkpoint: {e}")

    def get_last_checkpoint(self) -> Optional[Path]:
        """Get path to most recent checkpoint."""
        return self.last_checkpoint

    def recover_from_checkpoint(self, checkpoint_file: Path) -> Optional[dict]:
        """Load state from checkpoint file."""
        try:
            with open(checkpoint_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load checkpoint {checkpoint_file}: {e}")
            return None
