"""
af_engine.integrations.artifact_manager - Automated Artifact Management

This integration manages artifacts created during mission execution,
including organization, cleanup, and archival.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class ArtifactManagerIntegration(BaseIntegrationHandler):
    """
    Manages artifacts created during mission execution.

    Handles organization, cleanup, and archival of artifacts
    like plans, reports, and research documents.
    """

    name = "artifact_manager"
    priority = IntegrationPriority.LOW
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, archive_dir: Optional[Path] = None):
        """Initialize artifact manager."""
        super().__init__()
        self.archive_dir = archive_dir
        self.artifacts_created = []

    def on_mission_started(self, event: Event) -> None:
        """Initialize artifact tracking for new mission."""
        self.artifacts_created = []

        # Ensure artifact directories exist
        workspace = event.data.get("workspace_dir")
        if workspace:
            workspace_path = Path(workspace)
            (workspace_path / "artifacts").mkdir(parents=True, exist_ok=True)
            (workspace_path / "research").mkdir(parents=True, exist_ok=True)

    def on_stage_completed(self, event: Event) -> None:
        """Track artifacts created during stage."""
        files_created = event.data.get("files_created", [])
        files_modified = event.data.get("files_modified", [])

        # Track artifact files
        for f in files_created + files_modified:
            if "artifacts/" in f or "research/" in f:
                self.artifacts_created.append({
                    "file": f,
                    "stage": event.stage,
                    "timestamp": event.timestamp.isoformat(),
                })

    def on_mission_completed(self, event: Event) -> None:
        """Archive artifacts on mission completion."""
        if not self.artifacts_created:
            return

        try:
            self._archive_artifacts(event.mission_id)
            logger.info(f"Archived {len(self.artifacts_created)} artifacts")
        except Exception as e:
            logger.warning(f"Artifact archival failed: {e}")

    def _archive_artifacts(self, mission_id: str) -> Optional[Path]:
        """Archive mission artifacts."""
        if self.archive_dir is None:
            self.archive_dir = Path(".af_archives")

        self.archive_dir.mkdir(parents=True, exist_ok=True)

        # Create mission archive directory
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_path = self.archive_dir / f"{mission_id}_{timestamp}"
        archive_path.mkdir(parents=True, exist_ok=True)

        # Copy artifacts to archive
        for artifact in self.artifacts_created:
            try:
                src = Path(artifact["file"])
                if src.exists():
                    dst = archive_path / src.name
                    shutil.copy2(src, dst)
            except Exception as e:
                logger.debug(f"Failed to archive {artifact['file']}: {e}")

        return archive_path

    def get_artifacts(self) -> List[dict]:
        """Get list of tracked artifacts."""
        return self.artifacts_created
