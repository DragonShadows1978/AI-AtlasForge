"""
af_engine.integrations.websocket_events - Real-Time UI Updates

This integration sends real-time updates to connected WebSocket clients.
"""

import logging
from typing import Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class WebSocketIntegration(BaseIntegrationHandler):
    """
    Sends real-time updates to connected WebSocket clients.

    Runs at HIGH priority to ensure UI updates are sent promptly.
    """

    name = "websocket"
    priority = IntegrationPriority.HIGH
    subscriptions = [
        StageEvent.STAGE_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_STARTED,
        StageEvent.CYCLE_COMPLETED,
        StageEvent.MISSION_STARTED,
        StageEvent.MISSION_COMPLETED,
        StageEvent.RESPONSE_RECEIVED,
    ]

    def __init__(self):
        """Initialize WebSocket integration."""
        super().__init__()

    def _check_availability(self) -> bool:
        """Check if WebSocket module is available."""
        try:
            import websocket_events
            return True
        except ImportError:
            logger.debug("WebSocket events module not available")
            return False

    def on_stage_started(self, event: Event) -> None:
        """Emit stage started event."""
        self._emit("stage_started", {
            "mission_id": event.mission_id,
            "stage": event.stage,
            "timestamp": event.timestamp.isoformat(),
        })

    def on_stage_completed(self, event: Event) -> None:
        """Emit stage completed event."""
        self._emit("stage_completed", {
            "mission_id": event.mission_id,
            "stage": event.stage,
            "status": event.data.get("status", ""),
            "timestamp": event.timestamp.isoformat(),
        })

    def on_cycle_started(self, event: Event) -> None:
        """Emit cycle started event."""
        self._emit("cycle_started", {
            "mission_id": event.mission_id,
            "cycle_number": event.data.get("cycle_number", 0),
            "timestamp": event.timestamp.isoformat(),
        })

    def on_cycle_completed(self, event: Event) -> None:
        """Emit cycle completed event."""
        self._emit("cycle_completed", {
            "mission_id": event.mission_id,
            "cycle_number": event.data.get("cycle_number", 0),
            "cycles_remaining": event.data.get("cycles_remaining", 0),
            "timestamp": event.timestamp.isoformat(),
        })

    def on_mission_started(self, event: Event) -> None:
        """Emit mission started event."""
        self._emit("mission_started", {
            "mission_id": event.mission_id,
            "timestamp": event.timestamp.isoformat(),
        })

    def on_mission_completed(self, event: Event) -> None:
        """Emit mission completed event."""
        self._emit("mission_completed", {
            "mission_id": event.mission_id,
            "total_cycles": event.data.get("total_cycles", 0),
            "timestamp": event.timestamp.isoformat(),
        })

    def on_response_received(self, event: Event) -> None:
        """Emit response received event with token info."""
        self._emit("response_received", {
            "mission_id": event.mission_id,
            "stage": event.stage,
            "input_tokens": event.data.get("input_tokens", 0),
            "output_tokens": event.data.get("output_tokens", 0),
            "timestamp": event.timestamp.isoformat(),
        })

    def _emit(self, event_type: str, data: dict) -> None:
        """Emit event to WebSocket clients."""
        try:
            import websocket_events as ws
            ws.emit_event(event_type, data)
        except Exception as e:
            logger.debug(f"WebSocket emit failed: {e}")
