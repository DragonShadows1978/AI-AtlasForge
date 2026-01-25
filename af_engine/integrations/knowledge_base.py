"""
af_engine.integrations.knowledge_base - Learning Extraction and Injection

This integration extracts learnings from completed missions and injects
relevant knowledge into new mission planning.
"""

import logging
from pathlib import Path
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
        self.current_mission_learnings: List[Dict[str, Any]] = []

    def _check_availability(self) -> bool:
        """Check if knowledge base is available."""
        try:
            from mission_knowledge_base import MissionKnowledgeBase
            return True
        except ImportError:
            logger.debug("Knowledge base not available")
            return False

    def on_mission_started(self, event: Event) -> None:
        """Query KB for relevant context for new mission."""
        self.current_mission_learnings = []

        try:
            from mission_knowledge_base import MissionKnowledgeBase
            kb = MissionKnowledgeBase()

            mission_statement = event.data.get("mission_statement", "")
            if mission_statement:
                # Query for similar past missions using correct API
                results = kb.query_relevant_learnings(mission_statement, top_k=5)
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
        """
        Trigger KB ingestion of completed mission.

        The MissionKnowledgeBase ingests mission logs automatically via
        ingest_completed_mission(). This handler logs the event for
        tracking purposes and can trigger manual re-ingestion if needed.
        """
        try:
            from mission_knowledge_base import MissionKnowledgeBase, MISSION_LOGS_DIR

            mission_id = event.mission_id
            mission_log_path = MISSION_LOGS_DIR / f"{mission_id}.json"

            if mission_log_path.exists():
                kb = MissionKnowledgeBase()
                result = kb.ingest_completed_mission(mission_log_path)
                logger.info(
                    f"KB ingested mission {mission_id}: "
                    f"{result.get('learnings_extracted', 0)} learnings extracted"
                )
            else:
                logger.debug(f"Mission log not found at {mission_log_path}, skipping KB ingestion")

        except Exception as e:
            logger.warning(f"KB learning extraction failed: {e}")

    def get_context_for_planning(self, mission_statement: str) -> str:
        """Get KB context formatted for planning stage prompt."""
        try:
            from mission_knowledge_base import MissionKnowledgeBase
            kb = MissionKnowledgeBase()
            # Use correct method name
            return kb.generate_planning_context(mission_statement)
        except Exception as e:
            logger.debug(f"Failed to get KB context: {e}")
            return ""
