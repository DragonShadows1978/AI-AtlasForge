"""
af_engine.stages.complete - Complete Stage Handler

This stage is entered when the mission has finished all cycles.
It generates a final summary and marks the mission as complete.
"""

import logging
from typing import Dict, Any, List

from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class CompleteStageHandler(BaseStageHandler):
    """
    Handler for the COMPLETE stage.

    This is the final stage of a mission. It:
    - Generates a final summary
    - Is read-only (no modifications allowed)
    - Emits MISSION_COMPLETED event
    """

    stage_name = "COMPLETE"
    valid_from_stages = ["CYCLE_END", "ANALYZING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the COMPLETE stage prompt."""
        return """
=== COMPLETE STAGE ===
The mission has been completed!

Generate a final summary:

Respond with JSON:
{
    "status": "mission_complete",
    "summary": "What was accomplished",
    "deliverables": ["list of deliverables"],
    "lessons_learned": ["any insights for future missions"],
    "message_to_human": "Mission complete message"
}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the COMPLETE stage response.

        The COMPLETE stage always stays in COMPLETE.
        """
        status = response.get("status", "mission_complete")

        # Extract lessons learned for KB integration
        lessons = response.get("lessons_learned", [])
        deliverables = response.get("deliverables", [])
        summary = response.get("summary", "Mission completed")

        # Create MISSION_COMPLETED event
        events = [
            Event(
                type=StageEvent.MISSION_COMPLETED,
                stage=self.stage_name,
                mission_id=context.mission_id,
                data={
                    "status": status,
                    "summary": summary,
                    "deliverables": deliverables,
                    "lessons_learned": lessons,
                    "cycle_count": context.cycle_number,
                }
            )
        ]

        return StageResult(
            success=True,
            next_stage="COMPLETE",
            status=status,
            output_data=response,
            events_to_emit=events,
            message=response.get("message_to_human", "Mission complete")
        )

    def validate_transition(
        self,
        from_stage: str,
        context: StageContext
    ) -> bool:
        """
        Validate transition to COMPLETE stage.

        Can only transition from CYCLE_END or ANALYZING.
        """
        return from_stage in self.valid_from_stages

    def get_restrictions(self) -> StageRestrictions:
        """
        Get COMPLETE stage restrictions.

        COMPLETE is read-only - no writes or modifications allowed.
        """
        return StageRestrictions(
            allowed_tools=["Read", "Glob", "Grep"],
            blocked_tools=["Edit", "Write", "NotebookEdit", "Bash"],
            allowed_write_paths=[],
            forbidden_write_paths=["*"],
            allow_bash=False,
            read_only=True
        )
