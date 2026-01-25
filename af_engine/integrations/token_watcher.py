"""
af_engine.integrations.token_watcher - Real-Time Token Monitoring

This integration monitors token usage in real-time and can trigger
warnings or actions when approaching limits.
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


class TokenWatcherIntegration(BaseIntegrationHandler):
    """
    Monitors token usage in real-time and alerts on thresholds.

    Runs at CRITICAL priority to catch token limit issues early.
    """

    name = "token_watcher"
    priority = IntegrationPriority.CRITICAL
    subscriptions = [
        StageEvent.RESPONSE_RECEIVED,
        StageEvent.STAGE_STARTED,
    ]

    # Default token limits
    DEFAULT_CONTEXT_LIMIT = 200000  # Claude's context window
    DEFAULT_WARNING_THRESHOLD = 0.8  # Warn at 80% usage

    def __init__(
        self,
        context_limit: int = DEFAULT_CONTEXT_LIMIT,
        warning_threshold: float = DEFAULT_WARNING_THRESHOLD,
    ):
        """Initialize token watcher with limits."""
        super().__init__()
        self.context_limit = context_limit
        self.warning_threshold = warning_threshold
        self.current_context_usage = 0
        self._warning_emitted = False

    def on_response_received(self, event: Event) -> None:
        """Track context window usage."""
        data = event.data

        # Update context usage estimate
        input_tokens = data.get("input_tokens", 0)
        output_tokens = data.get("output_tokens", 0)
        self.current_context_usage = input_tokens + output_tokens

        # Check threshold
        usage_ratio = self.current_context_usage / self.context_limit
        if usage_ratio >= self.warning_threshold and not self._warning_emitted:
            logger.warning(
                f"Token usage at {usage_ratio:.1%} of context limit "
                f"({self.current_context_usage}/{self.context_limit})"
            )
            self._warning_emitted = True

        if usage_ratio >= 0.95:
            logger.error(
                f"CRITICAL: Token usage at {usage_ratio:.1%} - "
                "approaching context limit!"
            )

    def on_stage_started(self, event: Event) -> None:
        """Reset warning state on new stage."""
        # Reset warning for new stage but keep tracking
        self._warning_emitted = False

    def get_usage_percentage(self) -> float:
        """Get current context usage as percentage."""
        return self.current_context_usage / self.context_limit * 100

    def is_near_limit(self) -> bool:
        """Check if approaching context limit."""
        return self.current_context_usage >= (self.context_limit * self.warning_threshold)
