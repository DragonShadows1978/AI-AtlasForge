"""
af_engine.stages.analyzing - Analyzing Stage Handler

This stage evaluates test results and decides next steps.
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


class AnalyzingStageHandler(BaseStageHandler):
    """
    Handler for the ANALYZING stage.

    The ANALYZING stage:
    - Evaluates test results
    - Decides next steps (COMPLETE, BUILDING, or PLANNING)
    - Only allows writes to research/ and artifacts/
    - Transitions to CYCLE_END on success
    """

    stage_name = "ANALYZING"
    valid_from_stages = ["TESTING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the ANALYZING stage prompt."""
        # Get analyzing restrictions
        guard_prompt = self._get_guard_prompt()
        research_dir = context.research_dir
        artifacts_dir = context.artifacts_dir

        return f"""
{guard_prompt}

=== ANALYZING STAGE ===
Your goal: Evaluate results and decide next steps.

IMPORTANT: In ANALYZING stage, only write to research/ or artifacts/.
Do NOT fix bugs here. If fixes are needed, recommend BUILDING stage.

Tasks:
1. Review test results from {artifacts_dir}/test_results.md
2. If tests passed: Prepare completion report
3. If tests failed: Diagnose issues and plan fixes
4. Document analysis in {research_dir}/analysis.md

Respond with JSON:
{{
    "status": "success" | "needs_revision" | "needs_replanning",
    "analysis": "Your analysis of the results",
    "issues_found": ["list of issues, or empty"],
    "proposed_fixes": ["list of fixes if needed, or empty"],
    "recommendation": "COMPLETE" | "BUILDING" | "PLANNING",
    "message_to_human": "Analysis summary"
}}

If recommending COMPLETE, also include:
{{
    ...
    "final_report": "Summary of what was accomplished",
    "deliverables": ["list of files/artifacts produced"]
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the ANALYZING stage response.

        Determines next stage based on recommendation.
        """
        status = response.get("status", "")
        recommendation = response.get("recommendation", "").upper()

        events = [
            Event(
                type=StageEvent.STAGE_COMPLETED,
                stage=self.stage_name,
                mission_id=context.mission_id,
                data={
                    "status": status,
                    "recommendation": recommendation,
                    "issues_found": response.get("issues_found", []),
                }
            )
        ]

        if status == "success" or recommendation == "COMPLETE":
            # Go to CYCLE_END instead of COMPLETE to handle cycle iteration
            return StageResult(
                success=True,
                next_stage="CYCLE_END",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", "Analysis complete, moving to cycle end")
            )

        elif status == "needs_revision" or recommendation == "BUILDING":
            # Need to make fixes - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs revision, returning to building")
            )

        elif status == "needs_replanning" or recommendation == "PLANNING":
            # Need to revise the plan - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="PLANNING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs replanning")
            )

        else:
            # Default to CYCLE_END
            logger.warning(f"ANALYZING: Unexpected status '{status}', defaulting to CYCLE_END")
            return StageResult(
                success=True,
                next_stage="CYCLE_END",
                status=status,
                output_data=response,
                events_to_emit=events,
                message="Analysis complete"
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get ANALYZING stage restrictions.

        Only allows writes to research/ and artifacts/.
        Matches legacy init_guard.py STAGE_POLICIES["ANALYZING"].
        """
        return StageRestrictions(
            allowed_tools=[
                "Read", "Glob", "Grep", "Write", "Edit",
                "WebFetch", "WebSearch", "Task"
            ],
            blocked_tools=[],
            allowed_write_paths=[
                "*/artifacts/*",
                "*/research/*",
                "*analysis.md",
                "*report.md",
                "*test_results.md"
            ],
            forbidden_write_paths=["*.py", "*.js", "*.ts"],
            allow_bash=False,
            read_only=False
        )

    def _get_guard_prompt(self) -> str:
        """Get the InitGuard prompt for analyzing restrictions."""
        try:
            from init_guard import InitGuard
            return InitGuard.get_analyzing_system_prompt()
        except ImportError:
            return ""
