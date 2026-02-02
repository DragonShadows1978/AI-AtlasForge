"""
af_engine.integrations.transcript_archival - Transcript Archival Integration

This integration archives Claude conversation transcripts when a mission completes.
It calls the archive_mission_transcripts function from af_engine.py to:
1. Find all .jsonl transcripts in the mission time window
2. Copy them to artifacts/transcripts/{mission_id}/
3. Generate a manifest with token usage statistics

This is essential for GlassBox functionality - without archived transcripts,
completed missions don't appear in the GlassBox interface.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class TranscriptArchivalIntegration(BaseIntegrationHandler):
    """
    Archives Claude transcripts when a mission completes.

    This integration runs AFTER MissionReportIntegration (lower priority)
    to ensure the final report is saved before archiving transcripts.

    Subscribes to:
        - MISSION_COMPLETED: Archive transcripts for the completed mission

    Priority: LOW - runs after other high-priority integrations like
    MissionReportIntegration to ensure reports are saved first.
    """

    name = "transcript_archival"
    priority = IntegrationPriority.LOW
    subscriptions = [
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self):
        """Initialize the transcript archival integration."""
        super().__init__()
        self._archive_function = None

    def _get_archive_function(self):
        """
        Lazy-load the archive function to avoid circular imports.

        Returns:
            The archive_mission_transcripts function or None if not available
        """
        if self._archive_function is not None:
            return self._archive_function

        try:
            import sys
            root_dir = Path(__file__).resolve().parent.parent.parent
            if str(root_dir) not in sys.path:
                sys.path.insert(0, str(root_dir))

            from af_engine import archive_mission_transcripts
            self._archive_function = archive_mission_transcripts
            return self._archive_function
        except ImportError as e:
            logger.warning(f"[TranscriptArchival] Cannot import archive function: {e}")
            return None

    def on_mission_completed(self, event: Event) -> None:
        """
        Archive transcripts when a mission completes.

        Args:
            event: MISSION_COMPLETED event with mission data
        """
        mission_id = event.mission_id
        logger.info(f"[TranscriptArchival] Archiving transcripts for mission: {mission_id}")

        archive_fn = self._get_archive_function()
        if archive_fn is None:
            logger.error("[TranscriptArchival] Archive function not available")
            return

        mission = self._build_mission_dict(event)
        if not mission:
            logger.error(f"[TranscriptArchival] Could not build mission dict for {mission_id}")
            return

        try:
            result = archive_fn(mission)

            if result.get("success"):
                count = result.get("transcripts_archived", 0)
                path = result.get("archive_path", "unknown")
                logger.info(f"[TranscriptArchival] Archived {count} transcripts to {path}")
            else:
                errors = result.get("errors", [])
                logger.warning(f"[TranscriptArchival] Archival completed with errors: {errors}")
        except Exception as e:
            logger.error(f"[TranscriptArchival] Archival failed: {e}")

    def _build_mission_dict(self, event: Event) -> Optional[Dict[str, Any]]:
        """
        Build a mission dict suitable for archive_mission_transcripts.

        Args:
            event: The MISSION_COMPLETED event

        Returns:
            Mission dict with required fields, or None if unable to build
        """
        mission_id = event.mission_id
        event_data = event.data or {}

        mission = {"mission_id": mission_id}

        # Get created_at from event data or config file
        created_at = (
            event_data.get("started_at") or
            event_data.get("created_at") or
            event_data.get("mission_created_at") or
            self._get_created_at_from_config(mission_id)
        )

        if not created_at and event.timestamp:
            created_at = event.timestamp.isoformat()

        mission["created_at"] = created_at
        mission["last_updated"] = (
            event.timestamp.isoformat() if event.timestamp
            else datetime.now().isoformat()
        )

        # Get workspace and directory paths
        mission_workspace = event_data.get("mission_workspace")
        mission_dir = event_data.get("mission_dir")

        if not mission_workspace or not mission_dir:
            root_dir = Path(__file__).resolve().parent.parent.parent
            missions_dir = root_dir / "missions"
            workspace_dir = root_dir / "workspace"

            if not mission_dir:
                mission_dir = str(missions_dir / mission_id)

            if not mission_workspace:
                inferred_workspace = missions_dir / mission_id / "workspace"
                if inferred_workspace.exists():
                    mission_workspace = str(inferred_workspace)
                else:
                    project_name = event_data.get("project_name")
                    if project_name:
                        mission_workspace = str(workspace_dir / project_name)
                    else:
                        mission_workspace = str(inferred_workspace)

        mission["mission_workspace"] = mission_workspace
        mission["mission_dir"] = mission_dir

        logger.debug(f"[TranscriptArchival] Built mission dict: {mission}")
        return mission

    def _get_created_at_from_config(self, mission_id: str) -> Optional[str]:
        """
        Try to get created_at from mission_config.json.

        Args:
            mission_id: The mission ID

        Returns:
            ISO timestamp string or None
        """
        root_dir = Path(__file__).resolve().parent.parent.parent
        config_path = root_dir / "missions" / mission_id / "mission_config.json"

        if config_path.exists():
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
                return config.get("created_at")
            except (json.JSONDecodeError, IOError):
                pass

        return None

    def _check_availability(self) -> bool:
        """Check if required dependencies are available."""
        return self._get_archive_function() is not None
