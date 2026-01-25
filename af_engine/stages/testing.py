"""
af_engine.stages.testing - Testing Stage Handler

This stage handles verification of the solution with epistemic rigor,
including adversarial testing.
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


class TestingStageHandler(BaseStageHandler):
    """
    Handler for the TESTING stage.

    The TESTING stage:
    - Runs self-tests and adversarial testing
    - Has full write access for test creation
    - Emphasizes epistemic rigor
    - Always transitions to ANALYZING (regardless of pass/fail)
    """

    stage_name = "TESTING"
    valid_from_stages = ["BUILDING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the TESTING stage prompt."""
        workspace_dir = context.workspace_dir
        artifacts_dir = context.artifacts_dir

        return f"""
=== TESTING STAGE ===
Your goal: Verify the solution works correctly with EPISTEMIC RIGOR.

IMPORTANT: You design your tests based on your code - of course they'll pass.
This is the "painter who loves their own work" problem. To build TRUE confidence,
you must include ADVERSARIAL TESTING - attempts to BREAK your own code.

=== PHASE 1: SELF-TESTS (Baseline) ===
Your own tests that verify basic functionality.

Tasks:
1. Create test script(s) in {workspace_dir}/tests/ if needed
2. Run the code and capture output
3. Verify against success criteria from your plan

=== PHASE 2: ADVERSARIAL TESTING (Epistemic Rigor) ===
The adversarial_testing module is available in the project's adversarial_testing/ directory.

Adversarial Testing Tasks:
1. **Red Team Analysis**: Use the AdversarialRunner to spawn a fresh agent that tries to BREAK your code
   - Fresh agent has no memory of how you built it
   - Looks for vulnerabilities, edge cases, logic flaws

2. **Property Testing**: Generate edge cases automatically
   - Empty inputs, null values, boundary conditions
   - Very large inputs, negative numbers, special characters

3. **Mutation Testing** (if tests exist): Check if your tests ACTUALLY catch bugs
   - Mutation score should be >= 80%
   - Low mutation score means tests are weak

4. **Blind Validation**: Compare implementation against ORIGINAL specification
   - Does the code do what was originally requested?
   - Has there been "spec drift" during implementation?

=== OUTPUT REQUIREMENTS ===

Document ALL test results in {artifacts_dir}/test_results.md including:
1. Self-test results (your own tests)
2. Adversarial findings (what the red team found)
3. Edge cases discovered (from property testing)
4. Mutation score (if applicable)
5. Spec alignment (from blind validation)

Respond with JSON:
{{
    "status": "tests_passed" | "tests_failed" | "tests_error",
    "self_tests": [
        {{"name": "test1", "passed": true, "output": "..."}},
        {{"name": "test2", "passed": false, "error": "..."}}
    ],
    "adversarial_testing": {{
        "red_team_issues": ["list of issues found by adversarial agent"],
        "property_violations": ["edge cases that broke the code"],
        "mutation_score": 0.0-1.0 or null,
        "spec_alignment": 0.0-1.0 or null,
        "epistemic_score": 0.0-1.0,
        "rigor_level": "insufficient|weak|moderate|strong|rigorous"
    }},
    "summary": "Overall test summary including adversarial findings",
    "success_criteria_met": ["which criteria were met"],
    "success_criteria_failed": ["which criteria failed"],
    "issues_to_fix": ["issues that need fixing before release"],
    "message_to_human": "Test results summary with adversarial analysis"
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the TESTING stage response.

        Always transitions to ANALYZING regardless of test outcome.
        """
        status = response.get("status", "")

        events = [
            Event(
                type=StageEvent.STAGE_COMPLETED,
                stage=self.stage_name,
                mission_id=context.mission_id,
                data={
                    "status": status,
                    "tests_passed": status == "tests_passed",
                    "adversarial_testing": response.get("adversarial_testing", {}),
                }
            )
        ]

        if status in ("tests_passed", "tests_failed", "tests_error"):
            return StageResult(
                success=True,
                next_stage="ANALYZING",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", f"Tests {status}, moving to analysis")
            )
        else:
            # Unexpected status, stay in testing
            return StageResult(
                success=True,
                next_stage="TESTING",
                status=status,
                output_data=response,
                message=response.get("message_to_human", "Continuing testing")
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get TESTING stage restrictions.

        TESTING has full write access for test creation and execution.
        """
        return StageRestrictions(
            allowed_tools=[],  # All allowed
            blocked_tools=[],
            allowed_write_paths=["*"],
            forbidden_write_paths=[],
            allow_bash=True,
            read_only=False
        )
