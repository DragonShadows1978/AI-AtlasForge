"""
af_engine.integrations.snapshots - Mission State Snapshots

This integration creates snapshots of mission state at key points.
"""

import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class SnapshotIntegration(BaseIntegrationHandler):
    """
    Creates snapshots of mission state at key points.

    Snapshots capture the full mission state for debugging
    and analysis purposes.
    """

    name = "snapshots"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, snapshots_dir: Optional[Path] = None):
        """Initialize snapshot integration."""
        super().__init__()
        self.snapshots_dir = snapshots_dir
        self.snapshot_count = 0

    def on_stage_completed(self, event: Event) -> None:
        """Create snapshot after stage completion."""
        self._create_snapshot(event, f"stage_{event.stage.lower()}")

    def on_cycle_completed(self, event: Event) -> None:
        """Create snapshot at end of each cycle."""
        cycle = event.data.get("cycle_number", 0)
        self._create_snapshot(event, f"cycle_{cycle}")

    def on_mission_completed(self, event: Event) -> None:
        """Create final mission snapshot."""
        self._create_snapshot(event, "mission_complete")

    def _create_snapshot(self, event: Event, snapshot_type: str) -> Optional[Path]:
        """Create a snapshot file."""
        # Initialize snapshots directory
        if self.snapshots_dir is None:
            self.snapshots_dir = Path(f".af_snapshots/{event.mission_id}")

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

        # Build snapshot data
        snapshot_data = {
            "snapshot_type": snapshot_type,
            "mission_id": event.mission_id,
            "stage": event.stage,
            "timestamp": event.timestamp.isoformat(),
            "snapshot_number": self.snapshot_count,
            "event_data": event.data,
        }

        # Create snapshot file
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"snapshot_{self.snapshot_count:03d}_{snapshot_type}_{timestamp}.json"
        snapshot_path = self.snapshots_dir / filename

        try:
            with open(snapshot_path, "w") as f:
                json.dump(snapshot_data, f, indent=2, default=str)

            self.snapshot_count += 1
            logger.debug(f"Snapshot created: {snapshot_path}")
            return snapshot_path

        except Exception as e:
            logger.warning(f"Snapshot creation failed: {e}")
            return None

    def get_latest_snapshot(self) -> Optional[Dict[str, Any]]:
        """Get the most recent snapshot."""
        if not self.snapshots_dir or not self.snapshots_dir.exists():
            return None

        snapshots = sorted(self.snapshots_dir.glob("snapshot_*.json"))
        if not snapshots:
            return None

        try:
            with open(snapshots[-1]) as f:
                return json.load(f)
        except Exception:
            return None

    def get_snapshot_count(self) -> int:
        """Get number of snapshots created."""
        return self.snapshot_count
