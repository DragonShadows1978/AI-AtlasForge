"""
af_engine.integrations.drift_validation - Mission Drift Detection Integration

This integration provides dashboard visibility for drift validation results.

Design: The actual LLM-as-judge validation and halt logic lives in
orchestrator._validate_continuation_drift(), which calls MissionDriftValidator
directly at the CYCLE_END → PLANNING decision point.

This integration's role is to:
1. Capture the original mission statement on MISSION_STARTED
2. On CYCLE_COMPLETED, read the drift tracking state saved by the orchestrator
   and expose real drift metrics to the dashboard / event bus
3. Avoid duplicate LLM calls — it never calls MissionDriftValidator itself
"""

import json
import logging
from pathlib import Path
from typing import Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class DriftValidationIntegration(BaseIntegrationHandler):
    """
    Exposes real drift validation metrics from orchestrator to the dashboard.

    The orchestrator runs LLM-as-judge validation at CYCLE_END and persists
    results to <mission_dir>/drift_validation/. This handler reads those
    persisted results and makes them available via get_drift_score() /
    is_drifting() for any component that queries the integration layer.
    """

    name = "drift_validation"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self, drift_threshold: float = 0.3):
        """Initialize drift validation integration."""
        super().__init__()
        self.drift_threshold = drift_threshold
        self.original_mission: Optional[str] = None
        self.mission_dir: Optional[Path] = None

        # Live metrics — updated from persisted orchestrator results
        self.drift_score: float = 0.0          # 1 - average_similarity
        self.failure_count: int = 0
        self.average_similarity: float = 1.0
        self.last_decision: str = "allow"
        self.last_severity: str = "none"
        self.warning_issued: bool = False
        self.halted_due_to_drift: bool = False

    def on_mission_started(self, event: Event) -> None:
        """Capture original mission and mission directory."""
        self.original_mission = event.data.get("mission_statement", "")
        mission_dir = event.data.get("mission_dir")
        if mission_dir:
            self.mission_dir = Path(mission_dir)

        # Reset metrics for new mission
        self.drift_score = 0.0
        self.failure_count = 0
        self.average_similarity = 1.0
        self.last_decision = "allow"
        self.last_severity = "none"
        self.warning_issued = False
        self.halted_due_to_drift = False
        logger.debug("Drift validation integration initialized for mission")

    def on_stage_completed(self, event: Event) -> None:
        """On ANALYZING completion, refresh metrics from persisted state."""
        if event.stage == "ANALYZING":
            self._refresh_from_disk(event)

    def on_cycle_completed(self, event: Event) -> None:
        """On cycle completion, refresh metrics from persisted state."""
        self._refresh_from_disk(event)

    def _refresh_from_disk(self, event: Event) -> None:
        """
        Read drift tracking state saved by orchestrator and update local metrics.

        The orchestrator writes <mission_dir>/drift_validation/drift_tracking_state.json
        after every validation. We read it here for dashboard exposure.
        Falls back to state data on the event if file is not present yet.
        """
        # First, try to read from disk (most authoritative source)
        if self.mission_dir:
            drift_dir = self.mission_dir / "drift_validation"
            tracking_path = drift_dir / "drift_tracking_state.json"
            if tracking_path.exists():
                try:
                    data = json.loads(tracking_path.read_text(encoding="utf-8"))
                    self.failure_count = data.get("failure_count", 0)
                    self.average_similarity = data.get("average_similarity", 1.0)
                    self.warning_issued = data.get("warning_issued", False)
                    self.drift_score = min(1.0, max(0.0, 1.0 - self.average_similarity))

                    # Read last validation result for decision/severity.
                    # save_validation_result() writes per-cycle files under drift_validations/.
                    results_dir = drift_dir / "drift_validations"
                    if results_dir.exists():
                        cycle_files = []
                        try:
                            cycle_files = sorted(results_dir.glob("validation_cycle_*.json"))
                            if cycle_files:
                                result_data = json.loads(cycle_files[-1].read_text(encoding="utf-8"))
                                self.last_decision = result_data.get("decision", "allow")
                                self.last_severity = result_data.get("drift_severity", "none")
                        except Exception as e:
                            last_file = cycle_files[-1] if cycle_files else results_dir
                            logger.warning("Failed to read drift validation file %s: %s", last_file, e, exc_info=True)

                    if self.drift_score > self.drift_threshold:
                        logger.warning(
                            f"Drift integration: score={self.drift_score:.1%}, "
                            f"failures={self.failure_count}, severity={self.last_severity}"
                        )
                    return
                except Exception as e:
                    logger.debug(f"Could not read drift tracking state from disk: {e}")

        # Fallback: read from mission state field set by orchestrator
        drift_info = event.data.get("drift_validation")
        if drift_info:
            self.failure_count = drift_info.get("failure_count", self.failure_count)
            self.average_similarity = drift_info.get("average_similarity", self.average_similarity)
            self.warning_issued = drift_info.get("warning_issued", self.warning_issued)
            self.last_decision = drift_info.get("last_decision", self.last_decision)
            self.last_severity = drift_info.get("last_severity", self.last_severity)
            self.drift_score = min(1.0, max(0.0, 1.0 - self.average_similarity))

    def get_drift_score(self) -> float:
        """Get current drift score (0 = aligned, 1 = fully drifted)."""
        return self.drift_score

    def is_drifting(self) -> bool:
        """Check if mission is drifting beyond threshold."""
        return self.drift_score > self.drift_threshold

    def get_status(self) -> dict:
        """Return full drift status dict for dashboard consumption."""
        return {
            "drift_score": self.drift_score,
            "average_similarity": self.average_similarity,
            "failure_count": self.failure_count,
            "warning_issued": self.warning_issued,
            "last_decision": self.last_decision,
            "last_severity": self.last_severity,
            "halted_due_to_drift": self.halted_due_to_drift,
            "is_drifting": self.is_drifting(),
        }
