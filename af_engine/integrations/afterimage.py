"""
af_engine.integrations.afterimage - Episodic Code Memory

This integration provides code memory from past implementations,
injecting relevant code patterns into planning and building stages.
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


class AfterImageIntegration(BaseIntegrationHandler):
    """
    Provides episodic code memory from past implementations.

    Queries the AfterImage database for similar code patterns
    and injects them into stage prompts for context.
    """

    name = "afterimage"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.STAGE_STARTED,
        StageEvent.PROMPT_GENERATED,
    ]

    def __init__(self):
        """Initialize AfterImage integration."""
        super().__init__()
        self.current_context = ""

    def _check_availability(self) -> bool:
        """Check if AfterImage is available."""
        try:
            from afterimage import AfterImage
            return True
        except ImportError:
            logger.debug("AfterImage not available")
            return False

    def on_stage_started(self, event: Event) -> None:
        """Query AfterImage for relevant code memory."""
        if event.stage not in ("PLANNING", "BUILDING"):
            self.current_context = ""
            return

        try:
            from afterimage import AfterImage
            ai = AfterImage()

            # Get mission context for querying
            mission_statement = event.data.get("mission_statement", "")

            if mission_statement:
                # Query for similar past code
                results = ai.query_similar_code(mission_statement, limit=5)

                if results:
                    self.current_context = self._format_context(results)
                    logger.info(f"AfterImage: Found {len(results)} relevant code snippets")
                else:
                    self.current_context = ""

        except Exception as e:
            logger.warning(f"AfterImage query failed: {e}")
            self.current_context = ""

    def on_prompt_generated(self, event: Event) -> None:
        """Inject AfterImage context into prompt if available."""
        if self.current_context:
            event.data["afterimage_context"] = self.current_context

    def _format_context(self, results: List[dict]) -> str:
        """Format AfterImage results for prompt injection."""
        if not results:
            return ""

        lines = ["=== AFTERIMAGE CODE MEMORY ==="]
        lines.append("Previously written similar code:\n")

        for i, result in enumerate(results, 1):
            file_path = result.get("file_path", "unknown")
            block_name = result.get("block_name", "unknown")
            code = result.get("code", "")
            relevance = result.get("relevance", 0)

            lines.append(f"### {file_path}")
            lines.append(f"*block: {block_name}*")
            lines.append("```")
            lines.append(code[:500])  # Truncate long snippets
            lines.append("```")
            lines.append(f"*Relevance: {relevance:.0%}*\n")

        lines.append("=== END AFTERIMAGE ===")
        return "\n".join(lines)

    def get_context(self) -> str:
        """Get current AfterImage context."""
        return self.current_context
