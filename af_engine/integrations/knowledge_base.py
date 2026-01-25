"""
af_engine.integrations.knowledge_base - Learning Extraction and Injection

This integration extracts learnings from completed missions and injects
relevant knowledge into new mission planning.
"""

import logging
from typing import List, Optional, Dict, Any

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseIntegration(BaseIntegrationHandler):
    """
    Extracts learnings from completed missions and provides context
    for new mission planning.

    Operates at LOW priority since it's primarily for background
    learning extraction.
    """

    name = "knowledge_base"
    priority = IntegrationPriority.LOW
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.MISSION_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self, kb_path: Optional[str] = None):
        """Initialize knowledge base integration."""
        super().__init__()
        self.kb_path = kb_path
        self.current_mission_learnings = []

    def _check_availability(self) -> bool:
        """Check if knowledge base is available."""
        try:
            from knowledge_base import KnowledgeBase
            return True
        except ImportError:
            logger.debug("Knowledge base not available")
            return False

    def on_mission_started(self, event: Event) -> None:
        """Query KB for relevant context for new mission."""
        self.current_mission_learnings = []

        try:
            from knowledge_base import KnowledgeBase
            kb = KnowledgeBase()

            mission_statement = event.data.get("mission_statement", "")
            if mission_statement:
                # Query for similar past missions
                results = kb.semantic_search(mission_statement, limit=5)
                if results:
                    logger.info(f"Found {len(results)} relevant KB entries for mission")
        except Exception as e:
            logger.warning(f"KB context retrieval failed: {e}")

    def on_cycle_completed(self, event: Event) -> None:
        """Capture learnings from completed cycle."""
        cycle_report = event.data.get("cycle_report", {})
        issues = cycle_report.get("issues", [])
        achievements = cycle_report.get("achievements", [])

        # Track issues and achievements as potential learnings
        for issue in issues:
            self.current_mission_learnings.append({
                "type": "gotcha",
                "content": issue,
                "cycle": event.data.get("cycle_number", 0),
            })

        for achievement in achievements:
            self.current_mission_learnings.append({
                "type": "technique",
                "content": achievement,
                "cycle": event.data.get("cycle_number", 0),
            })

    def on_mission_completed(self, event: Event) -> None:
        """Extract and store learnings from completed mission."""
        try:
            from knowledge_base import KnowledgeBase
            kb = KnowledgeBase()

            final_report = event.data.get("final_report", {})
            lessons = final_report.get("lessons_learned", [])

            # Store lessons in KB
            for lesson in lessons:
                kb.add_learning(
                    content=lesson,
                    mission_id=event.mission_id,
                    learning_type="lesson",
                )

            # Store accumulated learnings
            for learning in self.current_mission_learnings:
                kb.add_learning(
                    content=learning["content"],
                    mission_id=event.mission_id,
                    learning_type=learning["type"],
                )

            logger.info(f"Extracted {len(lessons) + len(self.current_mission_learnings)} learnings to KB")

        except Exception as e:
            logger.warning(f"KB learning extraction failed: {e}")

    def get_context_for_planning(self, mission_statement: str) -> str:
        """Get KB context formatted for planning stage prompt."""
        try:
            from knowledge_base import KnowledgeBase
            kb = KnowledgeBase()
            return kb.get_planning_context(mission_statement)
        except Exception:
            return ""
