"""
af_engine.integrations.queue_scheduler - Mission Queue Management

This integration manages the mission queue, starting next missions
when current ones complete.
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


class QueueSchedulerIntegration(BaseIntegrationHandler):
    """
    Manages mission queue and auto-starts next queued mission
    when current mission completes.

    Operates at LOW priority to ensure all other completion
    handlers run first.
    """

    name = "queue_scheduler"
    priority = IntegrationPriority.LOW
    subscriptions = [
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, queue_path: Optional[str] = None):
        """Initialize queue scheduler."""
        super().__init__()
        self.queue_path = queue_path

    def _check_availability(self) -> bool:
        """Check if queue scheduler module is available."""
        try:
            from dashboard_modules.queue_scheduler import get_next_queued_mission
            return True
        except ImportError:
            logger.debug("Queue scheduler not available")
            return False

    def on_mission_completed(self, event: Event) -> None:
        """Check for and start next queued mission."""
        try:
            from dashboard_modules.queue_scheduler import (
                get_next_queued_mission,
                mark_mission_started,
            )

            # Get next queued mission
            next_mission = get_next_queued_mission()

            if next_mission:
                mission_id = next_mission.get("id")
                mission_title = next_mission.get("mission_title", "Untitled")

                logger.info(f"Queue: Starting next mission - {mission_title}")

                # Mark as started in queue
                mark_mission_started(mission_id)

                # Signal to orchestrator to start new mission
                # The actual mission creation happens in the orchestrator
                # based on this event data
                event.data["next_queued_mission"] = next_mission

            else:
                logger.debug("Queue: No pending missions")

        except Exception as e:
            logger.warning(f"Queue scheduler failed: {e}")

    def get_queue_status(self) -> dict:
        """Get current queue status."""
        try:
            from dashboard_modules.queue_scheduler import get_queue_status
            return get_queue_status()
        except Exception:
            return {"pending": 0, "in_progress": 0, "completed": 0}
