"""
af_engine.stages.planning - Planning Stage Handler

This stage handles mission understanding and implementation planning.
It injects KB context and AfterImage code memory for informed planning.
"""

import logging
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class PlanningStageHandler(BaseStageHandler):
    """
    Handler for the PLANNING stage.

    The PLANNING stage:
    - Combines mission understanding with plan creation
    - Injects Knowledge Base context for past learnings
    - Injects AfterImage code memory for similar past code
    - Only allows writes to artifacts/ and research/
    - Transitions to BUILDING when plan is complete
    """

    stage_name = "PLANNING"
    valid_from_stages = ["CYCLE_END", "ANALYZING", "PLANNING", None]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the PLANNING stage prompt."""
        # Build prompt components
        guard_prompt = self._get_guard_prompt()
        kb_context = context.kb_context or ""
        afterimage_context = context.afterimage_context or ""
        resumption_content = self._get_resumption_content(context)

        workspace_dir = context.workspace_dir
        artifacts_dir = context.artifacts_dir
        research_dir = context.research_dir

        return f"""
{guard_prompt}
{kb_context}
{afterimage_context}
=== PLANNING STAGE ===
Your goal: Understand the mission AND create a detailed implementation plan.
{resumption_content}

IMPORTANT: You are AUTONOMOUS. Do NOT ask clarifying questions. Make reasonable assumptions and proceed.

In PLANNING stage, you may ONLY write to artifacts/ or research/ directories.
Do NOT write actual code yet. Save implementation for BUILDING stage.

=== RESEARCH PHASE (BEFORE Implementation Planning) ===
Your implementation plan should be EVIDENCE-BASED, not just based on training data.

MANDATORY: Knowledge Base Consultation
The Knowledge Base context above (if present) contains SEMANTIC SEARCH RESULTS from past missions.
- These are learnings from similar problems you've solved before
- PAY ATTENTION to "Gotchas to Avoid" - these are past failures to prevent
- Apply "Relevant Techniques" if they match your current problem
- Similar Past Missions show approaches that worked (or didn't)

Research Tasks:
1. **FIRST**: Review any Knowledge Base context above and incorporate relevant learnings
2. Use WebSearch to find current best practices for the task (include year 2025)
3. Use WebFetch to get official documentation if relevant technologies are involved
4. Search for similar problems and their solutions (prior art)
5. Look for common pitfalls and "what NOT to do" guidance
6. Document research findings in {research_dir}/research_findings.md

Research Questions to Answer:
- What are the current best practices for this type of work?
- What tools or frameworks are commonly used?
- What are known failure modes or antipatterns?
- What recent developments (2024-2025) are relevant?
- **What did past missions teach us about similar problems?**

Cite sources for architectural decisions when available.
If research reveals better approaches than initially considered, adjust your plan.

=== IMPLEMENTATION PLANNING ===

Tasks (in order):
1. Read and understand the problem statement above
2. Conduct active research using WebSearch/WebFetch (see Research Phase above)
3. Explore the codebase to understand existing patterns
4. Identify key requirements and constraints
5. Make reasonable assumptions for any ambiguities
6. Break down the problem into concrete steps
7. Identify files to create/modify in {workspace_dir}/
8. Define clear success criteria
9. Consider 2-3 alternative approaches (informed by research)
10. Write research findings to {research_dir}/research_findings.md
11. Write your plan to {artifacts_dir}/implementation_plan.md

Respond with JSON:
{{
    "status": "plan_complete",
    "understanding": "Your summary of what needs to be built",
    "kb_learnings_applied": ["list any KB learnings you incorporated, or empty if none"],
    "research_conducted": ["topic1: key finding", "topic2: key finding"],
    "sources_consulted": ["url1", "url2"],
    "key_requirements": ["requirement1", "requirement2", ...],
    "assumptions": ["any assumptions you made"],
    "approach": "Brief description of chosen approach",
    "approach_rationale": "Why this approach (cite KB learnings and sources if available)",
    "steps": [
        {{"step": 1, "description": "...", "files": ["file1.py"]}},
        {{"step": 2, "description": "...", "files": ["file2.py"]}}
    ],
    "success_criteria": ["criterion1", "criterion2"],
    "estimated_files": ["list of files to create"],
    "message_to_human": "Planning complete. Ready to build."
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the PLANNING stage response.

        Transitions to BUILDING when plan is complete.
        """
        status = response.get("status", "")

        if status == "plan_complete":
            # Create events for stage completion
            events = [
                Event(
                    type=StageEvent.STAGE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "status": status,
                        "kb_learnings": response.get("kb_learnings_applied", []),
                        "steps_planned": len(response.get("steps", [])),
                    }
                )
            ]

            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", "Plan complete, moving to building")
            )
        else:
            logger.warning(f"PLANNING: Unexpected status '{status}', staying in PLANNING")
            return StageResult(
                success=False,
                next_stage="PLANNING",
                status=status,
                output_data=response,
                message=f"Unexpected status: {status}"
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get PLANNING stage restrictions.

        Only allows writes to artifacts/ and research/ directories.
        Matches legacy init_guard.py STAGE_POLICIES["PLANNING"].
        """
        return StageRestrictions(
            allowed_tools=[
                "Read", "Glob", "Grep", "Write", "Edit",
                "Bash", "WebFetch", "WebSearch", "Task"
            ],
            blocked_tools=["NotebookEdit"],
            allowed_write_paths=[
                "*/artifacts/*",
                "*/research/*",
                "*implementation_plan.md"
            ],
            forbidden_write_paths=["*.py", "*.js", "*.ts"],
            allow_bash=True,
            read_only=False
        )

    def _get_guard_prompt(self) -> str:
        """Get the InitGuard prompt for planning restrictions."""
        try:
            from init_guard import InitGuard
            return InitGuard.get_planning_system_prompt()
        except ImportError:
            return ""

    def _get_resumption_content(self, context: StageContext) -> str:
        """Get resumption instructions if available."""
        if context.resumption_file:
            try:
                resumption_path = Path(context.resumption_file)
                if resumption_path.exists():
                    content = resumption_path.read_text()
                    return f"""
=== RESUMPTION INSTRUCTIONS ===
{content}
=== END RESUMPTION ===
"""
            except Exception as e:
                logger.warning(f"Failed to read resumption file: {e}")
        return ""
