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
            from dashboard_modules.queue_scheduler import start_next_mission
            return True
        except ImportError:
            logger.debug("Queue scheduler not available")
            return False

    def on_mission_completed(self, event: Event) -> None:
        """Check for and start next queued mission."""
        try:
            from dashboard_modules.queue_scheduler import queue_status

            # Get queue status to check for pending missions
            status = queue_status()
            if hasattr(status, 'get_json'):
                status_data = status.get_json()
            else:
                status_data = status

            pending_count = status_data.get("queue_length", 0)

            if pending_count > 0:
                logger.info(f"Queue: {pending_count} pending missions available")
                # Signal that there are pending missions
                # The dashboard/orchestrator handles actual mission start
                event.data["pending_missions"] = pending_count
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
