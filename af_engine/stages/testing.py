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

IMPORTANT: You design your tests based on your code — of course they'll pass.
This is the "painter who loves their own work" problem. To build TRUE confidence,
you must include ADVERSARIAL TESTING — launching real blind agents that try to BREAK
your code without any knowledge of how you built it.

=== PHASE 1: SELF-TESTS (Baseline) ===
Your own tests that verify basic functionality.

Tasks:
1. Create test script(s) in {workspace_dir}/tests/ if needed
2. Run the code and capture output
3. Verify against success criteria from your plan

=== PHASE 2: ADVERSARIAL TESTING — BLIND AGENT RED TEAM ===

**Launch the red team BEFORE or DURING self-tests for maximum parallelism.**

    The adversarial_testing module provides `BlindAgentRedTeam` — a parallel team of
    REAL configured-provider CLI subprocess agents that explore your workspace, run
    your tests, and try to break your code. These are NOT single LLM calls with code
    pasted in; they are full agents with tool loops that navigate the codebase
    themselves. Each agent appears as its own tab in the Mission Activity dashboard.

**How to launch:**

```python
import sys
sys.path.insert(0, '/home/vader/AI-AtlasForge')
from adversarial_testing.blind_agent_runner import BlindAgentRedTeam

team = BlindAgentRedTeam(n_agents=3, timeout=300)
result = team.launch_parallel_team(
    workspace_dir='{workspace_dir}',
    mission_desc='<one-sentence description of what your code does>',
)
print(f"Red team findings: {{result.total_issues}}")
print(f"Critical: {{len(result.critical_findings)}}")
for f in result.findings:
    print(f"  [{{f.severity.upper()}}] {{f.title}} — {{f.affected_code}}")
```

**Or via RedTeamAgent.analyze_workspace():**

```python
from adversarial_testing.red_team_agent import RedTeamAgent
agent = RedTeamAgent()
result = agent.analyze_workspace(
    workspace_dir='{workspace_dir}',
    mission_desc='<description>',
    n_agents=3,
    timeout=300,
)
```

**What the blind agents do (one agent per attack focus):**
- Agent 0 — Injection: command injection, path traversal, SQL/template/config injection
- Agent 1 — Boundary/Type: empty inputs, None, 0, -1, wrong types
- Agent 2 — Logic/State: off-by-one, state machine bypass, invariant violations
- Agent 3 — Concurrency/Resources: race conditions, leaks, missing timeouts

Each agent independently reads your code, runs your tests, and tries adversarial
inputs. Results are written to {workspace_dir}/tests/red_team_agent_N.json and
aggregated into a single RedTeamResult.

**Timing:** Blind agents run for 60–300 seconds (they are real Claude sessions).
If result.total_issues == 0 AND result.duration_ms < 10000, the agents likely
timed out — check result.error for details.

=== PHASE 3: ADDITIONAL ADVERSARIAL CHECKS ===

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
2. Blind agent red team findings (by agent: boundary/injection/logic/concurrency)
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
        "red_team_issues": ["list of issues found by blind agent team"],
        "red_team_agent_count": 3,
        "red_team_duration_seconds": 120,
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
        # Normalize status for case-insensitive matching
        raw_status = response.get("status", "")
        status = raw_status.lower().strip() if isinstance(raw_status, str) else ""

        # Log unrecognized status values for debugging
        valid_statuses = ["tests_passed", "tests_failed", "tests_error"]
        if status and status not in valid_statuses:
            logger.warning(f"TESTING: Unrecognized status '{raw_status}' (normalized: '{status}')")

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
            # Unexpected status — still forward to ANALYZING (never loop)
            logger.warning(f"TESTING: Unexpected status '{raw_status}', forwarding to ANALYZING")
            return StageResult(
                success=True,
                next_stage="ANALYZING",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", "Unexpected status, moving to analysis")
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
