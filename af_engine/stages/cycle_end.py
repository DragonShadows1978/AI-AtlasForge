"""
af_engine.stages.cycle_end - Cycle End Stage Handler

This stage handles cycle completion, report generation, and continuation.
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


class CycleEndStageHandler(BaseStageHandler):
    """
    Handler for the CYCLE_END stage.

    The CYCLE_END stage:
    - Generates cycle reports
    - Handles multi-cycle iteration
    - Generates continuation prompts for next cycle
    - Transitions to PLANNING or COMPLETE based on cycle budget
    """

    stage_name = "CYCLE_END"
    valid_from_stages = ["ANALYZING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the CYCLE_END stage prompt."""
        current_cycle = context.cycle_number
        cycle_budget = context.cycle_budget
        cycles_remaining = cycle_budget - current_cycle
        original_mission = context.original_mission[:500] if context.original_mission else context.problem_statement[:500]

        if cycles_remaining > 0:
            return f"""
=== CYCLE END STAGE ===
You have completed cycle {current_cycle} of {cycle_budget}.
Cycles remaining: {cycles_remaining}

ORIGINAL MISSION: {original_mission}

Your task:
1. Generate a comprehensive report of what was accomplished this cycle
2. List ALL files created or modified this cycle
3. Summarize key achievements and any issues encountered
4. Write a CONTINUATION PROMPT for the next cycle

The continuation prompt should:
- Build on what was accomplished
- Address any remaining work
- Be a complete, standalone mission statement for the next cycle
- Reference specific files/code if needed

Respond with JSON:
{{
    "status": "cycle_complete",
    "cycle_number": {current_cycle},
    "cycle_report": {{
        "summary": "What was accomplished this cycle",
        "files_created": ["list of new files"],
        "files_modified": ["list of modified files"],
        "achievements": ["key accomplishments"],
        "issues": ["any issues encountered"]
    }},
    "continuation_prompt": "The complete mission statement for the next cycle. Be specific and detailed.",
    "message_to_human": "Cycle {current_cycle}/{cycle_budget} complete. Continuing to next cycle..."
}}
"""
        else:
            return f"""
=== CYCLE END STAGE (FINAL) ===
You have completed the FINAL cycle ({current_cycle} of {cycle_budget}).

ORIGINAL MISSION: {original_mission}

Your task:
1. Generate a comprehensive FINAL report of everything accomplished across ALL cycles
2. List ALL files created or modified across the entire mission
3. Summarize the complete journey from start to finish
4. Provide lessons learned and recommendations
5. **IMPORTANT**: Suggest ONE follow-up mission that would naturally extend or build upon this work

Respond with JSON:
{{
    "status": "mission_complete",
    "total_cycles": {cycle_budget},
    "final_report": {{
        "summary": "Complete summary of what was accomplished across all cycles",
        "all_files": ["list of all files created/modified"],
        "key_achievements": ["major accomplishments"],
        "challenges_overcome": ["problems solved"],
        "lessons_learned": ["insights for future missions"]
    }},
    "deliverables": ["final list of deliverables"],
    "next_mission_recommendation": {{
        "mission_title": "A concise title for the recommended next mission",
        "mission_description": "A detailed description of what the next mission should accomplish. Be specific and actionable.",
        "suggested_cycles": 3,
        "rationale": "Why this mission would be valuable to pursue next"
    }},
    "message_to_human": "Mission complete after {cycle_budget} cycles."
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the CYCLE_END stage response.

        Determines whether to continue to next cycle or complete mission.
        """
        status = response.get("status", "")
        current_cycle = context.cycle_number
        cycle_budget = context.cycle_budget
        cycles_remaining = cycle_budget - current_cycle

        if status == "cycle_complete" and cycles_remaining > 0:
            # More cycles remaining - continue
            continuation_prompt = response.get("continuation_prompt", "")
            cycle_report = response.get("cycle_report", {})

            events = [
                Event(
                    type=StageEvent.CYCLE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "cycle_number": current_cycle,
                        "cycles_remaining": cycles_remaining,
                        "continuation_prompt": continuation_prompt,
                        "cycle_report": cycle_report,
                    }
                )
            ]

            return StageResult(
                success=True,
                next_stage="PLANNING",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", f"Cycle {current_cycle} complete, continuing")
            )

        elif status == "mission_complete" or cycles_remaining <= 0:
            # Mission complete
            final_report = response.get("final_report", {})
            deliverables = response.get("deliverables", [])
            next_mission = response.get("next_mission_recommendation", {})

            events = [
                Event(
                    type=StageEvent.CYCLE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "cycle_number": current_cycle,
                        "final": True,
                        "final_report": final_report,
                    }
                ),
                Event(
                    type=StageEvent.MISSION_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "total_cycles": cycle_budget,
                        "deliverables": deliverables,
                        "next_mission_recommendation": next_mission,
                        "final_report": final_report,
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

        else:
            # Unexpected status
            logger.warning(f"CYCLE_END: Unexpected status '{status}'")
            return StageResult(
                success=False,
                next_stage="CYCLE_END",
                status=status,
                output_data=response,
                message=f"Unexpected status: {status}"
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get CYCLE_END stage restrictions.

        Only allows writes to artifacts/ and research/.
        """
        return StageRestrictions(
            allowed_tools=[
                "Read", "Glob", "Grep", "Write", "Edit"
            ],
            blocked_tools=["Bash", "NotebookEdit"],
            allowed_write_paths=["*/artifacts/*", "*/research/*"],
            forbidden_write_paths=["*.py", "*.js", "*.ts"],
            allow_bash=False,
            read_only=False
        )
