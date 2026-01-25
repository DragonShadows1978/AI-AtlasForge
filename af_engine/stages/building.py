"""
af_engine.stages.building - Building Stage Handler

This stage handles implementation of the solution based on the plan.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List

from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class BuildingStageHandler(BaseStageHandler):
    """
    Handler for the BUILDING stage.

    The BUILDING stage:
    - Has full write access to implement the solution
    - Injects AfterImage code memory from implementation plan
    - Triggers plan backup before starting
    - Transitions to TESTING when build is complete
    """

    stage_name = "BUILDING"
    valid_from_stages = ["PLANNING", "ANALYZING", "BUILDING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the BUILDING stage prompt."""
        afterimage_context = context.afterimage_context or ""
        artifacts_dir = context.artifacts_dir
        workspace_dir = context.workspace_dir

        return f"""
{afterimage_context}
=== BUILDING STAGE ===
Your goal: Implement the solution based on your plan.

Tasks:
1. Read your plan from {artifacts_dir}/implementation_plan.md
2. Write code to {workspace_dir}/
3. Create all necessary files and directories
4. Ensure code is complete and runnable
5. Follow any style preferences specified

Respond with JSON:
{{
    "status": "build_complete" | "build_in_progress" | "build_blocked",
    "files_created": ["list of files created"],
    "files_modified": ["list of files modified"],
    "summary": "What was built",
    "ready_for_testing": true | false,
    "blockers": ["any blockers, or empty list"],
    "message_to_human": "Build status message"
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the BUILDING stage response.

        Transitions to TESTING when build is complete and ready for testing.
        """
        status = response.get("status", "")
        ready_for_testing = response.get("ready_for_testing", False)
        files_created = response.get("files_created", [])
        files_modified = response.get("files_modified", [])

        if status == "build_complete" and ready_for_testing:
            events = [
                Event(
                    type=StageEvent.STAGE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "status": status,
                        "files_created": files_created,
                        "files_modified": files_modified,
                    }
                )
            ]

            return StageResult(
                success=True,
                next_stage="TESTING",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", "Build complete, moving to testing")
            )

        elif status == "build_in_progress":
            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data=response,
                message=response.get("message_to_human", "Build in progress")
            )

        elif status == "build_blocked":
            logger.warning("Build blocked - may need replanning")
            return StageResult(
                success=False,
                next_stage="BUILDING",
                status=status,
                output_data=response,
                message=response.get("message_to_human", "Build blocked")
            )

        else:
            # Default: stay in building
            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data=response,
                message=response.get("message_to_human", "Continuing build")
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get BUILDING stage restrictions.

        BUILDING has full write access.
        """
        return StageRestrictions(
            allowed_tools=[],  # Empty means all allowed
            blocked_tools=[],
            allowed_write_paths=["*"],
            forbidden_write_paths=[],
            allow_bash=True,
            read_only=False
        )
