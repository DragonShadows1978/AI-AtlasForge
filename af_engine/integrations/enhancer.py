"""
af_engine.integrations.enhancer - AtlasForge Enhancements

This integration provides AtlasForge-specific enhancements like
mission continuity tracking and baseline fingerprinting.
"""

import logging
import os
from typing import Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class EnhancerIntegration(BaseIntegrationHandler):
    """
    Provides AtlasForge-specific enhancements.

    Handles mission continuity tracking, baseline fingerprinting,
    and other RD-engine specific features.
    """

    name = "enhancer"
    priority = IntegrationPriority.BACKGROUND
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self):
        """Initialize enhancer integration."""
        super().__init__()
        self._enhancer = None

    def _check_availability(self) -> bool:
        """Check if enhancer module is available."""
        try:
            from atlasforge_enhancements.atlasforge_enhancer import AtlasForgeEnhancer
            return True
        except ImportError:
            logger.debug("AtlasForge Enhancer not available")
            return False

    def _get_enhancer(self, mission_id: str = None, storage_base = None, llm_provider: Optional[str] = None):
        """Lazy-load the enhancer instance."""
        if self._enhancer is None and mission_id:
            try:
                from atlasforge_enhancements.atlasforge_enhancer import AtlasForgeEnhancer
                from pathlib import Path
                storage = Path(storage_base) / 'af_data' if storage_base else None
                provider = (llm_provider or os.environ.get("ATLASFORGE_LLM_PROVIDER") or "claude")
                self._enhancer = AtlasForgeEnhancer(
                    mission_id=mission_id,
                    storage_base=storage,
                    llm_provider=provider
                )
            except Exception as e:
                logger.warning(f"Failed to initialize enhancer: {e}")
        return self._enhancer

    def on_mission_started(self, event: Event) -> None:
        """Set baseline fingerprint for mission continuity tracking."""
        mission_id = event.mission_id or event.data.get("mission_id")
        workspace = event.data.get("mission_workspace") or event.data.get("workspace")
        llm_provider = event.data.get("llm_provider")

        enhancer = self._get_enhancer(
            mission_id=mission_id,
            storage_base=workspace,
            llm_provider=llm_provider,
        )
        if not enhancer:
            return

        try:
            mission_statement = event.data.get("mission_statement", "")
            if mission_statement:
                enhancer.set_mission_baseline(mission_statement, source="initial_mission")
                logger.info("RDE baseline fingerprint set for mission continuity tracking")
        except Exception as e:
            logger.warning(f"Failed to set RDE baseline fingerprint: {e}")

    def on_stage_completed(self, event: Event) -> None:
        """Track stage completion for continuity."""
        enhancer = self._get_enhancer()
        if not enhancer:
            return

        try:
            if hasattr(enhancer, "set_llm_provider") and event.data.get("llm_provider"):
                enhancer.set_llm_provider(event.data.get("llm_provider"))
            enhancer.track_stage_completion(
                stage=event.stage,
                status=event.data.get("status", ""),
            )
        except Exception as e:
            logger.debug(f"Enhancer stage tracking failed: {e}")

    def on_mission_completed(self, event: Event) -> None:
        """Finalize mission tracking."""
        enhancer = self._get_enhancer()
        if not enhancer:
            return

        try:
            enhancer.finalize_mission(
                mission_id=event.mission_id,
                total_cycles=event.data.get("total_cycles", 0),
                final_report=event.data.get("final_report", {}),
            )
            logger.info("RDE mission tracking finalized")
        except Exception as e:
            logger.warning(f"Enhancer finalization failed: {e}")
