"""
af_engine.integrations.queue_scheduler - Mission Queue Management

This integration manages the mission queue, starting next missions
when current ones complete.

NOTE: The actual queue processing is now handled directly by the
StageOrchestrator._process_mission_queue() method. This integration
only provides queue status information for monitoring/display purposes.
"""

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


class QueueSchedulerIntegration(BaseIntegrationHandler):
    """
    Monitors mission queue status.

    The actual queue processing (starting next mission) is handled
    by StageOrchestrator._process_mission_queue() which is called
    directly when a mission completes. This integration only provides
    status info for dashboard display.

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
        """Check if queue file exists."""
        try:
            from atlasforge_config import STATE_DIR
            queue_path = STATE_DIR / "mission_queue.json"
            return queue_path.exists()
        except ImportError:
            return False

    def on_mission_completed(self, event: Event) -> None:
        """
        Log queue status when mission completes.

        Note: Actual queue processing is handled by
        StageOrchestrator._process_mission_queue() which runs
        after the MISSION_COMPLETED event is emitted.
        """
        try:
            status = self.get_queue_status()
            pending_count = status.get("pending", 0)

            if pending_count > 0:
                logger.info(f"Queue: {pending_count} pending missions available")
                # Add to event data for other handlers
                event.data["pending_missions"] = pending_count
            else:
                logger.debug("Queue: No pending missions")

        except Exception as e:
            logger.warning(f"Queue status check failed: {e}")

    def get_queue_status(self) -> dict:
        """
        Get current queue status using direct file access.

        Avoids Flask context dependency by reading queue file directly.
        """
        try:
            # Try using mission_queue_scheduler if available
            try:
                from mission_queue_scheduler import get_scheduler
                scheduler = get_scheduler()
                state = scheduler.get_queue()
                return {
                    "pending": len([q for q in state.queue if q.get("status") != "completed"]),
                    "in_progress": 0,  # Determined by active mission state
                    "completed": len([q for q in state.queue if q.get("status") == "completed"]),
                    "enabled": state.enabled,
                    "paused": state.paused
                }
            except ImportError:
                pass

            # Fallback: read queue file directly
            try:
                import io_utils
                from atlasforge_config import STATE_DIR
                queue_path = STATE_DIR / "mission_queue.json"
                queue_data = io_utils.atomic_read_json(queue_path, {"queue": [], "enabled": True})
                queue = queue_data.get("queue", [])
                return {
                    "pending": len(queue),
                    "in_progress": 0,
                    "completed": 0,
                    "enabled": queue_data.get("enabled", True)
                }
            except Exception:
                pass

            return {"pending": 0, "in_progress": 0, "completed": 0}

        except Exception:
            return {"pending": 0, "in_progress": 0, "completed": 0}
