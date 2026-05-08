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

CODEX_REQUIRED_RED_TEAM_AGENT_COUNT = 3
CODEX_OFFICIAL_RED_TEAM_TIMEOUT_SECONDS = 2700


def _coerce_nonnegative_int(value: Any) -> int:
    """Convert simple numeric payloads to a non-negative int."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "complete", "completed"}
    return bool(value)


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

    @staticmethod
    def _is_codex_context(context: StageContext) -> bool:
        provider = str(
            (context.mission or {}).get("llm_provider")
            or (context.mission or {}).get("provider")
            or ""
        ).strip().lower()
        return provider == "codex"

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

=== NON-INTERACTIVE COMPLETION CONTRACT ===

AtlasForge invokes this TESTING stage as a one-shot non-interactive process.
Do not use ScheduleWakeup, Monitor, notification waits, or any other "I'll wait"
handoff as your final answer. If you launch background or parallel red-team
processes, wait synchronously in this invocation until they finish, then read
their result files and produce the JSON response below.

Every TESTING invocation must end with the strict JSON object requested below.
If a subprocess is still running and cannot be waited on safely, return
`"status": "tests_error"` with the partial results and the blocker instead of
returning prose about waiting.

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
        response = dict(response)
        raw_status = response.get("status", "")
        status = raw_status.lower().strip() if isinstance(raw_status, str) else ""

        # Log unrecognized status values for debugging
        valid_statuses = ["tests_passed", "tests_failed", "tests_error"]
        if status and status not in valid_statuses:
            logger.warning(f"TESTING: Unrecognized status '{raw_status}' (normalized: '{status}')")

        adversarial = response.get("adversarial_testing", {})
        if not isinstance(adversarial, dict):
            adversarial = {}
            response["adversarial_testing"] = adversarial
        red_team_agent_count = _coerce_nonnegative_int(
            adversarial.get("red_team_agent_count")
        )
        completion = adversarial.get("red_team_completion")
        if not isinstance(completion, dict):
            completion = {}
        reports_collected = _coerce_nonnegative_int(
            completion.get("agent_reports_collected")
        )
        agents_reached_report_phase = _coerce_nonnegative_int(
            completion.get("agents_reached_report_phase")
        )
        timed_out_agents = completion.get("timed_out_agents")
        if not isinstance(timed_out_agents, list):
            timed_out_agents = []
        has_completion_metadata = bool(completion)
        official_red_team_complete = red_team_agent_count >= CODEX_REQUIRED_RED_TEAM_AGENT_COUNT
        if has_completion_metadata:
            official_red_team_complete = (
                official_red_team_complete
                and reports_collected >= CODEX_REQUIRED_RED_TEAM_AGENT_COUNT
                and agents_reached_report_phase >= CODEX_REQUIRED_RED_TEAM_AGENT_COUNT
                and _coerce_bool(completion.get("all_agents_completed"))
                and not timed_out_agents
            )

        if self._is_codex_context(context) and status == "tests_passed" and not official_red_team_complete:
            logger.warning(
                "TESTING: Downgrading tests_passed to tests_error because "
                "red_team_agent_count=%s is below required %s",
                red_team_agent_count,
                CODEX_REQUIRED_RED_TEAM_AGENT_COUNT,
            )
            status = "tests_error"
            response["status"] = "tests_error"
            adversarial["red_team_agent_count"] = red_team_agent_count
            adversarial.setdefault("rigor_level", "insufficient")
            adversarial.setdefault("epistemic_score", 0.0)
            missing_msg = (
                "Official TESTING red-team gate incomplete: expected at least "
                f"{CODEX_REQUIRED_RED_TEAM_AGENT_COUNT} completed blind agents with "
                "collected reports. "
                f"agents={red_team_agent_count}, reports={reports_collected}, "
                f"report_phase={agents_reached_report_phase}, "
                f"timed_out={timed_out_agents}. BUILDING self-validation does "
                "not satisfy TESTING."
            )
            failed = response.get("success_criteria_failed")
            if not isinstance(failed, list):
                failed = []
            if missing_msg not in failed:
                failed.append(missing_msg)
            response["success_criteria_failed"] = failed
            issues = response.get("issues_to_fix")
            if not isinstance(issues, list):
                issues = []
            if missing_msg not in issues:
                issues.append(missing_msg)
            response["issues_to_fix"] = issues
            response["message_to_human"] = missing_msg

        events = [
            Event(
                type=StageEvent.STAGE_COMPLETED,
                stage=self.stage_name,
                mission_id=context.mission_id,
                data={
                    "status": status,
                    "tests_passed": status == "tests_passed",
                    "official_red_team_complete": official_red_team_complete,
                    "required_red_team_agent_count": CODEX_REQUIRED_RED_TEAM_AGENT_COUNT,
                    "red_team_agent_count": red_team_agent_count,
                    "red_team_reports_collected": reports_collected,
                    "red_team_agents_reached_report_phase": agents_reached_report_phase,
                    "red_team_timed_out_agents": timed_out_agents,
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
