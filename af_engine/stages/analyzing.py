"""
af_engine.stages.analyzing - Analyzing Stage Handler

This stage evaluates test results and decides next steps.
"""

import json
import logging
from datetime import datetime
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

        red_team_report_path = f"{artifacts_dir}/../tests/Red_Team/report.md"
        impl_plan_path = f"{artifacts_dir}/implementation_plan.md"

        return f"""
{guard_prompt}

=== ANALYZING STAGE ===
Your goal: Evaluate results, enforce scope gates, and generate targeted continuation missions.

IMPORTANT: In ANALYZING stage, only write to research/ or artifacts/.
Do NOT fix bugs here. If fixes are needed, recommend BUILDING stage.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP A — READ ALL ARTIFACTS (required before any decision)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Read every file listed here. If a file is absent, note the gap and continue.

1. {artifacts_dir}/test_results.md — test pass/fail summary
2. {red_team_report_path} — Red Team bug report (REQUIRED if exists)
   Also read individual agent findings: {artifacts_dir}/../tests/Red_Team/agent*_findings.md
3. {impl_plan_path} — original implementation scope (which components are IN-SCOPE)
4. {artifacts_dir}/bug_fix_plan.md — previously scheduled fixes
5. {artifacts_dir}/../research/analysis.md — prior analysis notes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP B — IN-SCOPE BUG GATE (evaluate BEFORE generating missions)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Using implementation_plan.md, identify which components are IN-SCOPE for this mission.

For each Red Team finding:
  - Is the affected file/component listed in implementation_plan.md? → IN-SCOPE
  - Is it in a different component not listed? → OUT-OF-SCOPE

Gate rules for IN-SCOPE bugs:
  - CRITICAL or HIGH → status="needs_revision", recommendation="BUILDING" immediately.
    Do NOT generate continuation missions. The mission is not done.
  - MODERATE or LIGHT → status="needs_revision", recommendation="BUILDING".
    List exact bugs in proposed_fixes. Mission must be clean before advancing.
  - NONBLOCKING → log only, does not block advancement.

If ANY in-scope CRITICAL/HIGH/MODERATE/LIGHT bugs remain unfixed → reject to BUILDING.
Only proceed to Step C if all in-scope bugs are fixed or marked NONBLOCKING.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP C — GENERATE CONTINUATION MISSIONS (only when gate passes)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Generate missions in priority order. Each category is a SEPARATE mission.

CATEGORY 1 — BUGFIX (out-of-scope bugs, MANDATORY if any exist)
  - Group out-of-scope bugs by component. One mission per component.
  - List every bug with exact file + line number in source_bugs.
  - is_pure_bugfix: true — no new features allowed in these missions.
  - Title: "[BUGFIX] {{component}} — {{N}} bugs ({{severities}})"

CATEGORY 2 — TECH_DEBT (out-of-scope MODERATE/LIGHT bugs, lower priority)
  - Same as Category 1 but for lower-severity out-of-scope findings.
  - Title: "[TECH DEBT] {{component}} — {{N}} issues"

CATEGORY 3 — COMPLETION (if implementation_plan.md items were not finished)
  - One mission per unfinished scope item.
  - Title: "[COMPLETE] {{feature or component that was not finished}}"

CATEGORY 4 — EXPANSION (new features, forward-looking — ONLY after cats 1-3)
  - Suggest what to build next based on what was successfully completed.
  - Title: "[EXPAND] {{area}}" or "[FEATURE] {{name}}"

RULE: If out-of-scope bugs exist, Category 1 missions MUST appear before Category 4.
      Never fold bug information into an expansion mission.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TERMINOLOGY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- **Iteration**: One ANALYZING→BUILDING loop within a single cycle.
- **Cycle**: One complete PLANNING→BUILDING→TESTING→ANALYZING pass.
Do NOT call iterations "cycles" in reports.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write analysis to {research_dir}/analysis.md.
Write bug fix plan to {artifacts_dir}/bug_fix_plan.md (if bugs found).

Respond with JSON:
{{
    "status": "success" | "needs_revision" | "needs_replanning",
    "analysis": "Your analysis of the results",
    "issues_found": ["list of issues, or empty"],
    "proposed_fixes": ["list of fixes if needed, or empty"],
    "recommendation": "COMPLETE" | "BUILDING" | "PLANNING",
    "red_team_findings_count": 0,
    "red_team_blocking_bugs": [],
    "continuation_missions": [
        {{
            "priority": 1,
            "category": "BUGFIX",
            "is_pure_bugfix": true,
            "title": "[BUGFIX] component — N bugs (HIGH/MODERATE)",
            "description": "Detailed description of what to fix and why",
            "rationale": "Out-of-scope bugs found by Red Team in component X",
            "source_bugs": ["path/to/file.py:240", "path/to/file.py:265"],
            "blocks": []
        }}
    ],
    "message_to_human": "Analysis summary"
}}

Notes on continuation_missions:
- Include this field ONLY when status=success (gate passed).
- If no bugs and no unfinished scope, include only Category 4 missions.
- If no follow-up is needed at all, use an empty list [].
- BUGFIX/TECH_DEBT missions must have is_pure_bugfix=true.
- EXPANSION missions have is_pure_bugfix=false.

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
        # Normalize status for case-insensitive matching
        raw_status = response.get("status", "")
        status = raw_status.lower().strip() if isinstance(raw_status, str) else ""
        raw_rec = response.get("recommendation", "")
        recommendation = raw_rec.upper().strip() if isinstance(raw_rec, str) else ""

        # Log unrecognized status values for debugging
        valid_statuses = ["success", "needs_revision", "needs_replanning", "complete", "mission_complete", "done", "finished"]
        if status and status not in valid_statuses:
            logger.warning(f"ANALYZING: Unrecognized status '{raw_status}' (normalized: '{status}')")

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

        failure_signals = ["needs_revision"]
        replanning_signals = ["needs_replanning"]
        success_signals = ["success", "complete", "mission_complete", "done", "finished"]

        # IMPORTANT: failure/replanning checks MUST come before success/recommendation checks.
        # A response with status='needs_revision' + recommendation='COMPLETE' must route to
        # BUILDING — the status field takes priority over recommendation.
        if recommendation == "BUILDING" or status in failure_signals:
            # Need to make fixes - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs revision, returning to building")
            )

        elif status in replanning_signals or (recommendation == "PLANNING" and status not in success_signals):
            # Need to revise the plan - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="PLANNING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs replanning")
            )

        elif status in success_signals or recommendation == "COMPLETE":
            # Write continuation_missions.json manifest to artifacts/
            continuation_missions = response.get("continuation_missions", [])
            if isinstance(continuation_missions, list):
                self._write_continuation_manifest(continuation_missions, context)
            else:
                logger.warning("ANALYZING: continuation_missions is not a list, skipping manifest write")

            # Go to CYCLE_END instead of COMPLETE to handle cycle iteration
            return StageResult(
                success=True,
                next_stage="CYCLE_END",
                status=status,
                output_data={**response, "continuation_missions": continuation_missions},
                events_to_emit=events,
                message=response.get("message_to_human", "Analysis complete, moving to cycle end")
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

    def _write_continuation_manifest(self, missions: list, context: StageContext) -> None:
        """Write continuation_missions.json to artifacts/ for CYCLE_END to consume."""
        try:
            artifacts_dir = Path(context.artifacts_dir)
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "generated_at": datetime.now().isoformat(),
                "source_mission": context.mission_id,
                "missions": missions,
            }
            manifest_path = artifacts_dir / "continuation_missions.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            logger.info(f"ANALYZING: Wrote continuation manifest with {len(missions)} missions to {manifest_path}")
        except Exception as e:
            logger.error(f"ANALYZING: Failed to write continuation_missions.json: {e}")

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
