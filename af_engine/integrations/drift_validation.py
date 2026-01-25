"""
af_engine.integrations.drift_validation - Mission Drift Detection

This integration detects when the mission drifts from original objectives.
"""

import logging
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class DriftValidationIntegration(BaseIntegrationHandler):
    """
    Detects when mission execution drifts from original objectives.

    Compares current work against original mission statement to
    identify scope creep or misaligned execution.
    """

    name = "drift_validation"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self, drift_threshold: float = 0.3):
        """Initialize drift validation."""
        super().__init__()
        self.drift_threshold = drift_threshold
        self.original_mission = None
        self.key_objectives = []
        self.drift_score = 0.0

    def on_mission_started(self, event: Event) -> None:
        """Capture original mission for comparison."""
        self.original_mission = event.data.get("mission_statement", "")
        self.key_objectives = event.data.get("objectives", [])
        self.drift_score = 0.0
        logger.debug("Drift validation initialized for mission")

    def on_stage_completed(self, event: Event) -> None:
        """Check for drift after each stage."""
        if event.stage == "ANALYZING":
            self._check_drift(event)

    def on_cycle_completed(self, event: Event) -> None:
        """Check drift at end of each cycle."""
        self._check_drift(event)

    def _check_drift(self, event: Event) -> None:
        """Analyze current state for mission drift."""
        # Get current work summary
        current_summary = event.data.get("summary", "")
        accomplishments = event.data.get("achievements", [])

        # Simple drift detection: check if objectives are being addressed
        addressed_objectives = 0
        for obj in self.key_objectives:
            obj_lower = obj.lower()
            if any(obj_lower in acc.lower() for acc in accomplishments):
                addressed_objectives += 1

        if self.key_objectives:
            alignment_ratio = addressed_objectives / len(self.key_objectives)
            self.drift_score = 1.0 - alignment_ratio

            if self.drift_score > self.drift_threshold:
                logger.warning(
                    f"Mission drift detected: {self.drift_score:.1%} deviation. "
                    f"Only {addressed_objectives}/{len(self.key_objectives)} "
                    "objectives addressed."
                )

    def get_drift_score(self) -> float:
        """Get current drift score (0 = aligned, 1 = drifted)."""
        return self.drift_score

    def is_drifting(self) -> bool:
        """Check if mission is drifting beyond threshold."""
        return self.drift_score > self.drift_threshold
