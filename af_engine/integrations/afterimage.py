"""
af_engine.integrations.afterimage - Episodic Code Memory

This integration provides code memory from past implementations,
injecting relevant code patterns into planning and building stages.
"""

import logging
import sys
from pathlib import Path
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

    def _ensure_afterimage_import_path(self) -> None:
        """Add common local AI-AfterImage clone paths to sys.path."""
        try:
            from atlasforge_config import BASE_DIR, WORKSPACE_DIR
        except Exception:
            BASE_DIR = Path.cwd()
            WORKSPACE_DIR = BASE_DIR / "workspace"

        candidates = [
            WORKSPACE_DIR / "AI-AfterImage",
            WORKSPACE_DIR / "AfterImage",
            BASE_DIR.parent / "AI-AfterImage",
            Path.home() / "AI-AfterImage",
        ]
        for candidate in candidates:
            if candidate.exists():
                candidate_str = str(candidate)
                if candidate_str not in sys.path:
                    sys.path.insert(0, candidate_str)

    def _check_availability(self) -> bool:
        """Check if AfterImage is available."""
        try:
            self._ensure_afterimage_import_path()
            from afterimage import HybridSearch
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
            self._ensure_afterimage_import_path()
            from afterimage import HybridSearch

            # Get mission context for querying
            mission_statement = event.data.get("mission_statement", "")

            if mission_statement:
                # Query for similar past code using HybridSearch
                search = HybridSearch()
                results = search.search(mission_statement, limit=5)

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
            if isinstance(result, dict):
                file_path = result.get("file_path", "unknown")
                block_name = result.get("block_name", "unknown")
                code = result.get("code", result.get("new_code", ""))
                relevance = result.get("relevance", result.get("relevance_score", 0))
            else:
                file_path = getattr(result, "file_path", "unknown")
                block_name = getattr(result, "block_name", "unknown")
                code = getattr(result, "code", "") or getattr(result, "new_code", "")
                relevance = getattr(result, "relevance", getattr(result, "relevance_score", 0))

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
