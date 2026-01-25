"""
af_engine.integrations.analytics - Token Usage and Cost Tracking

This integration tracks token usage and costs across mission execution.
"""

import logging
from typing import List

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class AnalyticsIntegration(BaseIntegrationHandler):
    """
    Tracks token usage and API costs during mission execution.

    Runs at CRITICAL priority to ensure accurate tracking before
    any other integrations process the response.
    """

    name = "analytics"
    priority = IntegrationPriority.CRITICAL
    subscriptions = [
        StageEvent.RESPONSE_RECEIVED,
        StageEvent.MISSION_STARTED,
        StageEvent.MISSION_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self):
        """Initialize analytics tracking."""
        super().__init__()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.cycle_costs = []

    def on_response_received(self, event: Event) -> None:
        """Track token usage from Claude's response."""
        data = event.data
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)

        self.total_input_tokens += input_tokens
        self.total_output_tokens += output_tokens

        # Calculate cost (Claude 3 Opus pricing as baseline)
        cost = (input_tokens * 0.015 / 1000) + (output_tokens * 0.075 / 1000)
        self.total_cost += cost

        logger.debug(f"Tracked {input_tokens}+{output_tokens} tokens (${cost:.4f})")

    def on_mission_started(self, event: Event) -> None:
        """Reset tracking for new mission."""
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_cost = 0.0
        self.cycle_costs = []

    def on_cycle_completed(self, event: Event) -> None:
        """Record cycle-level costs."""
        self.cycle_costs.append({
            "cycle": event.data.get("cycle_number", 0),
            "cost": self.total_cost,
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
        })

    def on_mission_completed(self, event: Event) -> None:
        """Log final mission costs."""
        logger.info(
            f"Mission {event.mission_id} complete: "
            f"{self.total_input_tokens}+{self.total_output_tokens} tokens, "
            f"${self.total_cost:.4f}"
        )

    def get_current_stats(self) -> dict:
        """Get current usage statistics."""
        return {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cost": self.total_cost,
            "cycle_costs": self.cycle_costs,
        }
