"""
Tests for InitGuard stage restriction enforcement.

Verifies that InitGuard correctly validates tool usage and write paths
for each R&D stage.
"""

import pytest
import sys
from pathlib import Path

# Add project root to path for imports
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


# ===========================================================================
# Test RDStage enum
# ===========================================================================

class TestRDStageEnum:
    """Test RDStage enum values and behavior."""

    def test_all_stages_defined(self):
        """Verify all 6 stages are defined."""
        from init_guard import RDStage

        stages = list(RDStage)
        assert len(stages) == 6

        stage_names = [s.value for s in stages]
        assert "PLANNING" in stage_names
        assert "BUILDING" in stage_names
        assert "TESTING" in stage_names
        assert "ANALYZING" in stage_names
        assert "CYCLE_END" in stage_names
        assert "COMPLETE" in stage_names

    def test_stage_value_matches_name(self):
        """Verify stage values match their names."""
        from init_guard import RDStage

        for stage in RDStage:
            assert stage.value == stage.name


# ===========================================================================
# Test StageToolPolicy
# ===========================================================================

class TestStageToolPolicy:
    """Test StageToolPolicy class behavior."""

    def test_is_tool_allowed_with_blocked_tools(self):
        """Test is_tool_allowed when tool is in blocked_tools."""
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.PLANNING,
            allowed_tools={"Read", "Grep"},
            blocked_tools={"NotebookEdit"},
            write_paths_allowed=["*/artifacts/*"]
        )

        assert policy.is_tool_allowed("Read") is True
        assert policy.is_tool_allowed("NotebookEdit") is False

    def test_is_tool_allowed_with_allowed_tools(self):
        """Test is_tool_allowed when using allowed_tools whitelist."""
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.PLANNING,
            allowed_tools={"Read", "Grep"},
            blocked_tools=set(),
            write_paths_allowed=["*/artifacts/*"]
        )

        assert policy.is_tool_allowed("Read") is True
        assert policy.is_tool_allowed("Write") is False  # Not in allowed_tools

    def test_is_tool_allowed_no_restrictions(self):
        """Test is_tool_allowed when no restrictions set."""
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.BUILDING,
            allowed_tools=set(),  # Empty means all allowed
            blocked_tools=set(),
            write_paths_allowed=["*"]
        )

        # All tools should be allowed
        assert policy.is_tool_allowed("Read") is True
        assert policy.is_tool_allowed("Write") is True
        assert policy.is_tool_allowed("NotebookEdit") is True

    def test_can_write_path_matches_glob(self):
        """Test can_write_path matches glob patterns."""
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.PLANNING,
            allowed_tools=set(),
            blocked_tools=set(),
            write_paths_allowed=["*/artifacts/*", "*/research/*"]
        )

        assert policy.can_write_path("/home/user/project/artifacts/plan.md") is True
        assert policy.can_write_path("/home/user/project/research/notes.md") is True
        assert policy.can_write_path("/home/user/project/src/code.py") is False

    def test_can_write_path_empty_list(self):
        """Test can_write_path returns False when no paths allowed."""
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.COMPLETE,
            allowed_tools={"Read"},
            blocked_tools={"Write"},
            write_paths_allowed=[]
        )

        assert policy.can_write_path("/any/path") is False


# ===========================================================================
# Test STAGE_POLICIES configuration
# ===========================================================================

class TestStagePoliciesConfiguration:
    """Test STAGE_POLICIES dict is correctly configured."""

    def test_all_stages_have_policies(self):
        """Verify all 6 stages have defined policies."""
        from init_guard import STAGE_POLICIES, RDStage

        for stage in RDStage:
            assert stage in STAGE_POLICIES, f"Missing policy for {stage}"

    @pytest.mark.regression
    def test_planning_stage_policy(self):
        """Verify PLANNING stage restrictions."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.PLANNING]

        # Check allowed tools
        assert "Read" in policy.allowed_tools
        assert "Glob" in policy.allowed_tools
        assert "Grep" in policy.allowed_tools
        assert "Bash" in policy.allowed_tools

        # Check blocked tools
        assert "NotebookEdit" in policy.blocked_tools

        # Check write paths
        assert "*/artifacts/*" in policy.write_paths_allowed
        assert "*/research/*" in policy.write_paths_allowed

    @pytest.mark.regression
    def test_building_stage_policy(self):
        """Verify BUILDING stage has full access."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.BUILDING]

        # Empty sets mean no restrictions
        assert len(policy.allowed_tools) == 0
        assert len(policy.blocked_tools) == 0
        assert "*" in policy.write_paths_allowed

    @pytest.mark.regression
    def test_testing_stage_policy(self):
        """Verify TESTING stage has full access."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.TESTING]

        assert len(policy.blocked_tools) == 0
        assert "*" in policy.write_paths_allowed

    @pytest.mark.regression
    def test_analyzing_stage_policy(self):
        """Verify ANALYZING stage restrictions."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.ANALYZING]

        # Check allowed tools
        assert "Read" in policy.allowed_tools
        assert "Write" in policy.allowed_tools

        # Check write paths are limited
        assert "*/artifacts/*" in policy.write_paths_allowed
        assert "*" not in policy.write_paths_allowed  # Not everything allowed

    @pytest.mark.regression
    def test_cycle_end_stage_policy(self):
        """Verify CYCLE_END stage restrictions."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.CYCLE_END]

        # Check allowed tools
        assert "Read" in policy.allowed_tools
        assert "Write" in policy.allowed_tools

        # Check write paths include mission_logs
        assert "*mission_logs/*" in policy.write_paths_allowed

    @pytest.mark.regression
    def test_complete_stage_policy(self):
        """Verify COMPLETE stage is read-only."""
        from init_guard import STAGE_POLICIES, RDStage

        policy = STAGE_POLICIES[RDStage.COMPLETE]

        # Check only read tools allowed
        assert "Read" in policy.allowed_tools
        assert "Glob" in policy.allowed_tools
        assert "Grep" in policy.allowed_tools

        # Check write tools blocked
        assert "Edit" in policy.blocked_tools
        assert "Write" in policy.blocked_tools
        assert "Bash" in policy.blocked_tools

        # No write paths allowed
        assert len(policy.write_paths_allowed) == 0


# ===========================================================================
# Test InitGuard class methods
# ===========================================================================

class TestInitGuardGetBlockedTools:
    """Test InitGuard.get_blocked_tools() method."""

    @pytest.mark.regression
    def test_get_blocked_tools_planning(self):
        """Verify blocked tools for PLANNING stage."""
        from init_guard import InitGuard

        blocked = InitGuard.get_blocked_tools("PLANNING")
        assert "NotebookEdit" in blocked

    @pytest.mark.regression
    def test_get_blocked_tools_complete(self):
        """Verify blocked tools for COMPLETE stage."""
        from init_guard import InitGuard

        blocked = InitGuard.get_blocked_tools("COMPLETE")
        assert "Edit" in blocked
        assert "Write" in blocked
        assert "Bash" in blocked

    def test_get_blocked_tools_building(self):
        """Verify no blocked tools for BUILDING stage."""
        from init_guard import InitGuard

        blocked = InitGuard.get_blocked_tools("BUILDING")
        assert len(blocked) == 0

    def test_get_blocked_tools_unknown_stage(self):
        """Verify empty list for unknown stage."""
        from init_guard import InitGuard

        blocked = InitGuard.get_blocked_tools("UNKNOWN_STAGE")
        assert blocked == []


class TestInitGuardGetAllowedTools:
    """Test InitGuard.get_allowed_tools() method."""

    @pytest.mark.regression
    def test_get_allowed_tools_planning(self):
        """Verify allowed tools for PLANNING stage."""
        from init_guard import InitGuard

        allowed = InitGuard.get_allowed_tools("PLANNING")
        assert "Read" in allowed
        assert "Glob" in allowed
        assert "Grep" in allowed

    @pytest.mark.regression
    def test_get_allowed_tools_complete(self):
        """Verify allowed tools for COMPLETE stage."""
        from init_guard import InitGuard

        allowed = InitGuard.get_allowed_tools("COMPLETE")
        assert "Read" in allowed
        assert "Glob" in allowed
        assert "Grep" in allowed
        # These should NOT be in allowed
        assert "Edit" not in allowed
        assert "Write" not in allowed

    def test_get_allowed_tools_building(self):
        """Verify empty list for BUILDING (all allowed)."""
        from init_guard import InitGuard

        allowed = InitGuard.get_allowed_tools("BUILDING")
        # Empty because all tools are allowed
        assert len(allowed) == 0

    def test_get_allowed_tools_unknown_stage(self):
        """Verify empty list for unknown stage."""
        from init_guard import InitGuard

        allowed = InitGuard.get_allowed_tools("UNKNOWN_STAGE")
        assert allowed == []


class TestInitGuardValidateToolUsage:
    """Test InitGuard.validate_tool_usage() method."""

    @pytest.mark.regression
    def test_validate_tool_usage_planning_allowed(self):
        """Verify allowed tools pass validation in PLANNING."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("PLANNING", "Read")
        assert allowed is True

        allowed, reason = InitGuard.validate_tool_usage("PLANNING", "Grep")
        assert allowed is True

    @pytest.mark.regression
    def test_validate_tool_usage_planning_blocked(self):
        """Verify blocked tools fail validation in PLANNING."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("PLANNING", "NotebookEdit")
        assert allowed is False
        assert "blocked" in reason.lower()

    @pytest.mark.regression
    def test_validate_tool_usage_complete_blocked(self):
        """Verify write tools blocked in COMPLETE stage."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("COMPLETE", "Edit")
        assert allowed is False

        allowed, reason = InitGuard.validate_tool_usage("COMPLETE", "Write")
        assert allowed is False

        allowed, reason = InitGuard.validate_tool_usage("COMPLETE", "Bash")
        assert allowed is False

    @pytest.mark.regression
    def test_validate_tool_usage_building_all_allowed(self):
        """Verify all tools allowed in BUILDING stage."""
        from init_guard import InitGuard

        tools = ["Read", "Write", "Edit", "Bash", "NotebookEdit", "Glob", "Grep"]
        for tool in tools:
            allowed, reason = InitGuard.validate_tool_usage("BUILDING", tool)
            assert allowed is True, f"Tool {tool} should be allowed in BUILDING"

    def test_validate_tool_usage_unknown_stage(self):
        """Verify unknown stage returns True (permissive)."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("UNKNOWN", "AnyTool")
        assert allowed is True
        assert "unknown" in reason.lower()


class TestInitGuardValidateWritePath:
    """Test InitGuard.validate_write_path() method."""

    @pytest.mark.regression
    def test_validate_write_path_planning_artifacts_allowed(self):
        """Verify artifacts path allowed in PLANNING."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path(
            "PLANNING", "/home/user/project/artifacts/plan.md"
        )
        assert allowed is True

    @pytest.mark.regression
    def test_validate_write_path_planning_code_blocked(self):
        """Verify code paths blocked in PLANNING."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path(
            "PLANNING", "/home/user/project/src/code.py"
        )
        assert allowed is False

    @pytest.mark.regression
    def test_validate_write_path_building_all_allowed(self):
        """Verify all paths allowed in BUILDING."""
        from init_guard import InitGuard

        paths = [
            "/any/path/file.py",
            "/src/code.py",
            "/home/user/project/main.py"
        ]
        for path in paths:
            allowed, reason = InitGuard.validate_write_path("BUILDING", path)
            assert allowed is True, f"Path {path} should be allowed in BUILDING"

    @pytest.mark.regression
    def test_validate_write_path_complete_none_allowed(self):
        """Verify no paths allowed in COMPLETE."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path(
            "COMPLETE", "/any/path"
        )
        assert allowed is False

    def test_validate_write_path_unknown_stage(self):
        """Verify unknown stage returns True (permissive)."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path("UNKNOWN", "/any/path")
        assert allowed is True


# ===========================================================================
# Test InitGuard system prompt generation
# ===========================================================================

class TestInitGuardSystemPrompts:
    """Test InitGuard system prompt generation."""

    def test_get_planning_system_prompt_content(self):
        """Verify PLANNING system prompt contains key instructions."""
        from init_guard import InitGuard

        prompt = InitGuard.get_planning_system_prompt()

        assert "PLANNING" in prompt
        assert "artifacts" in prompt.lower()
        assert "research" in prompt.lower()
        assert "FORBIDDEN" in prompt or "forbidden" in prompt.lower()

    def test_get_analyzing_system_prompt_content(self):
        """Verify ANALYZING system prompt contains key instructions."""
        from init_guard import InitGuard

        prompt = InitGuard.get_analyzing_system_prompt()

        assert "ANALYZING" in prompt
        assert "report" in prompt.lower() or "analysis" in prompt.lower()

    def test_get_cycle_end_system_prompt_content(self):
        """Verify CYCLE_END system prompt contains key instructions."""
        from init_guard import InitGuard

        prompt = InitGuard.get_cycle_end_system_prompt()

        assert "CYCLE_END" in prompt
        assert "continuation" in prompt.lower() or "cycle" in prompt.lower()

    def test_get_stage_prompt_returns_correct_prompt(self):
        """Verify get_stage_prompt returns correct prompt for each stage."""
        from init_guard import InitGuard

        planning_prompt = InitGuard.get_stage_prompt("PLANNING")
        assert "PLANNING" in planning_prompt

        analyzing_prompt = InitGuard.get_stage_prompt("ANALYZING")
        assert "ANALYZING" in analyzing_prompt

        cycle_end_prompt = InitGuard.get_stage_prompt("CYCLE_END")
        assert "CYCLE_END" in cycle_end_prompt

    def test_get_stage_prompt_returns_empty_for_other_stages(self):
        """Verify get_stage_prompt returns empty for stages without prompts."""
        from init_guard import InitGuard

        assert InitGuard.get_stage_prompt("BUILDING") == ""
        assert InitGuard.get_stage_prompt("TESTING") == ""
        assert InitGuard.get_stage_prompt("COMPLETE") == ""


# ===========================================================================
# Test convenience functions
# ===========================================================================

class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_is_write_allowed_building(self):
        """Verify is_write_allowed returns True for BUILDING."""
        from init_guard import is_write_allowed

        assert is_write_allowed("BUILDING") is True

    def test_is_write_allowed_complete(self):
        """Verify is_write_allowed returns False for COMPLETE."""
        from init_guard import is_write_allowed

        assert is_write_allowed("COMPLETE") is False

    def test_is_write_allowed_unknown_stage(self):
        """Verify is_write_allowed returns True for unknown stage."""
        from init_guard import is_write_allowed

        assert is_write_allowed("UNKNOWN") is True

    def test_get_stage_restrictions_returns_dict(self):
        """Verify get_stage_restrictions returns correct dict structure."""
        from init_guard import get_stage_restrictions

        restrictions = get_stage_restrictions("PLANNING")

        assert "stage" in restrictions
        assert restrictions["stage"] == "PLANNING"
        assert "allowed_tools" in restrictions
        assert "blocked_tools" in restrictions
        assert "write_paths" in restrictions

    def test_get_stage_restrictions_building(self):
        """Verify get_stage_restrictions for BUILDING shows full access."""
        from init_guard import get_stage_restrictions

        restrictions = get_stage_restrictions("BUILDING")

        assert restrictions["allowed_tools"] == "all"
        assert restrictions["blocked_tools"] == "none"

    def test_get_stage_restrictions_complete(self):
        """Verify get_stage_restrictions for COMPLETE shows read-only."""
        from init_guard import get_stage_restrictions

        restrictions = get_stage_restrictions("COMPLETE")

        assert "Read" in restrictions["allowed_tools"]
        assert restrictions["write_paths"] == "none"

    def test_get_stage_restrictions_unknown_stage(self):
        """Verify get_stage_restrictions handles unknown stage."""
        from init_guard import get_stage_restrictions

        restrictions = get_stage_restrictions("UNKNOWN")

        assert "error" in restrictions


# ===========================================================================
# Integration with orchestrator stage restrictions
# ===========================================================================

class TestInitGuardOrchestratorIntegration:
    """Test InitGuard integration with orchestrator stage restrictions."""

    @pytest.mark.regression
    def test_planning_restrictions_match_init_guard(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Verify orchestrator PLANNING restrictions match InitGuard."""
        from init_guard import InitGuard

        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("PLANNING")

        # Get restrictions from both sources
        orch_restrictions = orch.get_stage_restrictions()
        guard_blocked = InitGuard.get_blocked_tools("PLANNING")

        # Verify NotebookEdit is blocked in both
        assert "NotebookEdit" in guard_blocked
        if orch_restrictions["blocked_tools"]:
            assert "NotebookEdit" in orch_restrictions["blocked_tools"]

    @pytest.mark.regression
    def test_complete_restrictions_match_init_guard(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Verify orchestrator COMPLETE restrictions match InitGuard."""
        from init_guard import InitGuard

        mission = mission_factory(current_stage="COMPLETE")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("COMPLETE")

        # Get restrictions from both sources
        orch_restrictions = orch.get_stage_restrictions()
        guard_blocked = InitGuard.get_blocked_tools("COMPLETE")

        # Verify write tools are blocked in both
        assert "Edit" in guard_blocked
        assert "Write" in guard_blocked

        # Verify read_only (dict access)
        assert orch_restrictions["read_only"] is True

    @pytest.mark.regression
    def test_building_full_access_matches(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Verify orchestrator BUILDING has full access like InitGuard."""
        from init_guard import InitGuard

        mission = mission_factory(current_stage="BUILDING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        # Get restrictions
        orch_restrictions = orch.get_stage_restrictions()
        guard_blocked = InitGuard.get_blocked_tools("BUILDING")

        # Both should have no blocked tools
        assert len(guard_blocked) == 0
        assert not orch_restrictions["blocked_tools"] or len(orch_restrictions["blocked_tools"]) == 0
