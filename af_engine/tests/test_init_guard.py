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
        """Test can_write_path matches glob patterns.

        Iter-3: absolute paths must resolve under a configured workspace
        root. We use `/home/vader/AI-AtlasForge/...` since that's the
        default workspace root in this environment.
        """
        from init_guard import StageToolPolicy, RDStage

        policy = StageToolPolicy(
            stage=RDStage.PLANNING,
            allowed_tools=set(),
            blocked_tools=set(),
            write_paths_allowed=["*/artifacts/*", "*/research/*"]
        )

        assert policy.can_write_path("/home/vader/AI-AtlasForge/workspace/proj/artifacts/plan.md") is True
        assert policy.can_write_path("/home/vader/AI-AtlasForge/workspace/proj/research/notes.md") is True
        assert policy.can_write_path("/home/vader/AI-AtlasForge/workspace/proj/src/code.py") is False
        # Iter-3: workspace allow-list rejects out-of-workspace absolute paths
        assert policy.can_write_path("/home/other/project/artifacts/plan.md") is False

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
        """Verify unknown stage fails CLOSED (denies the tool).

        Regression: previously returned (True, ...) on typo'd stage names,
        silently disabling stage-based enforcement. Now fail-closed.
        """
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("UNKNOWN", "AnyTool")
        assert allowed is False
        assert "unknown" in reason.lower()

    def test_validate_tool_usage_typo_stage_name(self):
        """Regression: a typo of PLANNING must not bypass enforcement."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("PLANING", "Edit")
        assert allowed is False


class TestInitGuardValidateWritePath:
    """Test InitGuard.validate_write_path() method."""

    @pytest.mark.regression
    def test_validate_write_path_planning_artifacts_allowed(self):
        """Verify artifacts path allowed in PLANNING (under workspace root)."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path(
            "PLANNING", "/home/vader/AI-AtlasForge/workspace/proj/artifacts/plan.md"
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
        """Verify all in-workspace paths allowed in BUILDING.

        Iter-4 H2: BUILDING's `["*"]` pattern still allows ANY filename, but
        the path must resolve under the workspace root. Paths outside the
        workspace (/any/path, /src/code.py, /home/user/...) are rejected
        regardless of stage — the `*` is a filename wildcard, not a scope
        escape hatch.
        """
        from init_guard import InitGuard

        paths = [
            "/home/vader/AI-AtlasForge/workspace/proj/any_file.py",
            "/home/vader/AI-AtlasForge/src/code.py",
            "/home/vader/AI-AtlasForge/workspace/proj/main.py",
        ]
        for path in paths:
            allowed, reason = InitGuard.validate_write_path("BUILDING", path)
            assert allowed is True, f"Path {path} should be allowed in BUILDING"

    @pytest.mark.regression
    def test_validate_write_path_building_out_of_workspace_rejected(self):
        """Iter-4 H2: BUILDING rejects paths outside the workspace.

        The previous contract allowed /any/path/file.py in BUILDING because
        `write_paths_allowed=["*"]` was treated as a scope-escape. Iter-4
        decouples pattern-match from workspace-scope: the `*` pattern still
        matches any filename, but the workspace allow-list is always enforced.
        """
        from init_guard import InitGuard

        out_of_scope = [
            "/any/path/file.py",
            "/home/otheruser/id_rsa",
            "/opt/secrets/token",
        ]
        for path in out_of_scope:
            allowed, reason = InitGuard.validate_write_path("BUILDING", path)
            assert allowed is False, (
                f"Path {path} should be REJECTED in BUILDING (out of workspace)"
            )

    @pytest.mark.regression
    def test_validate_write_path_complete_none_allowed(self):
        """Verify no paths allowed in COMPLETE."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path(
            "COMPLETE", "/any/path"
        )
        assert allowed is False

    def test_validate_write_path_unknown_stage(self):
        """Verify unknown stage fails CLOSED.

        Regression: previously returned (True, ...) on unknown stage, letting
        any write through if the orchestrator passed a typo'd stage name.
        """
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path("UNKNOWN", "/any/path")
        assert allowed is False
        assert "unknown" in reason.lower()


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
        """Verify is_write_allowed fails CLOSED on unknown stage.

        Regression: previously returned True, letting writes through when
        the orchestrator passed a typo'd stage name.
        """
        from init_guard import is_write_allowed

        assert is_write_allowed("UNKNOWN") is False

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
# Test InitGuard.get_disallowed_tools_for_cli
# ===========================================================================

class TestInitGuardGetDisallowedToolsForCli:
    """Test InitGuard.get_disallowed_tools_for_cli() method."""

    def test_disallowed_includes_base_tools(self):
        """WebSearch/WebFetch and plan-mode tools are always blocked."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("BUILDING")
        for tool in ("WebSearch", "WebFetch", "EnterPlanMode", "ExitPlanMode"):
            assert tool in disallowed

    def test_disallowed_complete_includes_edit_write(self):
        """COMPLETE stage blocks Edit/Write/Bash."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("COMPLETE")
        assert "Edit" in disallowed
        assert "Write" in disallowed
        assert "Bash" in disallowed

    def test_disallowed_tools_unknown_stage_includes_writes(self):
        """Unknown stage must fail-closed: all write tools blocked.

        Regression: previously fell through to base_blocked only, leaving
        Edit/Write/Bash/NotebookEdit callable on a typo'd stage name.
        """
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("FAKE_STAGE")
        for tool in ("Edit", "Write", "Bash", "NotebookEdit"):
            assert tool in disallowed, (
                f"{tool} must be in disallowed list for unknown stage; got: {disallowed}"
            )

    def test_disallowed_tools_unknown_stage_allows_reads(self):
        """Unknown stage still permits read-only tools (Read/Glob/Grep)."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("FAKE_STAGE").split(",")
        for tool in ("Read", "Glob", "Grep"):
            assert tool not in disallowed, (
                f"{tool} must remain allowed for unknown stage; got disallowed={disallowed}"
            )

    def test_disallowed_tools_typo_stage_fails_closed(self):
        """A typo of PLANNING must not bypass enforcement."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("PLANING")
        assert "Edit" in disallowed
        assert "Write" in disallowed


# ===========================================================================
# Non-string / adversarial input hardening (new in iteration 1)
# ===========================================================================

class TestInitGuardNonStringInputs:
    """validate_tool_usage / validate_write_path must never raise on bad input.

    Regression: previously `validate_write_path('BUILDING', None)` raised
    TypeError from fnmatch, and `validate_tool_usage('BUILDING', None)` could
    trigger a crash. Both must fail-closed silently with a diagnostic reason.
    """

    def test_validate_write_path_none_path_fail_closed(self):
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path("BUILDING", None)
        assert allowed is False
        assert "str" in reason.lower()

    def test_validate_write_path_int_path_fail_closed(self):
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path("BUILDING", 123)
        assert allowed is False
        assert "str" in reason.lower()

    def test_validate_write_path_bytes_path_fail_closed(self):
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_write_path("BUILDING", b"/tmp/x")
        assert allowed is False

    def test_validate_tool_usage_none_tool_name(self):
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("BUILDING", None)
        assert allowed is False
        assert "str" in reason.lower()

    def test_validate_tool_usage_int_tool_name(self):
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("BUILDING", 42)
        assert allowed is False

    def test_validate_tool_usage_non_string_stage(self):
        """Non-string stage returns fail-closed (no TypeError from enum)."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage(None, "Read")
        assert allowed is False
        # The reason mentions unknown stage (None stringified).
        assert "unknown" in reason.lower() or "str" in reason.lower()

    def test_is_write_allowed_non_string_stage(self):
        from init_guard import is_write_allowed

        assert is_write_allowed(None) is False
        assert is_write_allowed(123) is False
        assert is_write_allowed([]) is False


class TestInitGuardPathTraversalAnchored:
    """can_write_path uses PurePosixPath.match so `*` does not cross `/`.

    Regression: previously used fnmatch, so `*implementation_plan.md` matched
    `/etc/shadow/implementation_plan.md` because fnmatch globs cross slashes.
    """

    def test_planning_rejects_etc_traversal(self):
        """/etc/.../implementation_plan.md must NOT match PLANNING's pattern."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "/etc/shadow/implementation_plan.md"
        )
        assert allowed is False

    def test_planning_rejects_root_traversal(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "/root/implementation_plan.md"
        )
        assert allowed is False

    def test_planning_rejects_dotdot_traversal(self):
        """Explicit `..` in path is rejected regardless of pattern match."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "../../etc/implementation_plan.md"
        )
        assert allowed is False

    def test_planning_allows_legitimate_implementation_plan(self):
        """The intended path still works."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "artifacts/implementation_plan.md"
        )
        assert allowed is True

    def test_planning_allows_workspace_artifacts(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "/home/vader/AI-AtlasForge/workspace/proj/artifacts/plan.md"
        )
        assert allowed is True

    def test_analyzing_rejects_etc_traversal(self):
        """ANALYZING's `*test_results.md` must not match system paths."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "ANALYZING", "/etc/cron.d/test_results.md"
        )
        assert allowed is False


class TestInitGuardStageReasonBounded:
    """Reason strings and log messages must not reflect arbitrarily large
    stage inputs. Regression: oversized stage strings blew up log/reason
    size with no bound."""

    def test_oversized_stage_reason_bounded(self):
        from init_guard import InitGuard

        giant = "X" * 10000
        allowed, reason = InitGuard.validate_tool_usage(giant, "Read")
        assert allowed is False
        # 64-char repr + small prefix + ellipsis; well under 200 chars.
        assert len(reason) < 200

    def test_newline_in_stage_escaped(self):
        """Newlines in stage input must not break structured log output."""
        from init_guard import InitGuard

        allowed, reason = InitGuard.validate_tool_usage("PLAN\nNING", "Edit")
        assert allowed is False
        # The reason includes the stage repr; newlines are escaped.
        assert "\n" not in reason.replace("\\n", "")


# ===========================================================================
# Iter-3 Red Team hardening regression tests
# ===========================================================================

class TestIter3CliAllowlistPrecedence:
    """C1: get_disallowed_tools_for_cli must combine blocklist AND allowlist.

    Regression: PLANNING's policy has BOTH blocked={NotebookEdit} AND a
    constrained allowed set. The iter-2 `if blocked_tools:` short-circuit
    skipped the allowlist computation, so TodoWrite/AskUserQuestion (absent
    from allowlist) passed through.
    """

    def test_planning_cli_blocks_todowrite_and_askuser(self):
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("PLANNING")
        assert "TodoWrite" in disallowed, f"TodoWrite must be blocked in PLANNING; got {disallowed}"
        assert "AskUserQuestion" in disallowed, f"AskUserQuestion must be blocked in PLANNING; got {disallowed}"

    def test_planning_cli_still_blocks_notebookedit(self):
        """Original blocklist entry must still be honored."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("PLANNING")
        assert "NotebookEdit" in disallowed

    def test_planning_cli_allows_allowlisted_tools(self):
        """Tools in the allowlist are NOT in disallowed."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("PLANNING").split(",")
        for tool in ("Read", "Glob", "Grep", "Write", "Edit", "Bash", "Task"):
            assert tool not in disallowed, (
                f"{tool} is in PLANNING's allowlist and must NOT be disallowed; got {disallowed}"
            )

    def test_complete_combines_both(self):
        """COMPLETE has both; still must block write tools."""
        from init_guard import InitGuard

        disallowed = InitGuard.get_disallowed_tools_for_cli("COMPLETE").split(",")
        for tool in ("Edit", "Write", "Bash", "NotebookEdit"):
            assert tool in disallowed, f"{tool} must be blocked in COMPLETE"


class TestIter3PathHardening:
    """Iter-3 attack-surface closure on can_write_path."""

    def test_case_folded_system_root_rejected(self):
        """/ETC/.../plan.md, /Etc/... same as /etc/..."""
        from init_guard import InitGuard

        for path in ("/ETC/artifacts/plan.md",
                     "/Etc/artifacts/plan.md",
                     "/USR/artifacts/plan.md",
                     "/VAR/log/artifacts/plan.md"):
            allowed, _ = InitGuard.validate_write_path("PLANNING", path)
            assert allowed is False, f"case-folded system root {path} must fail"

    def test_whitespace_prefix_rejected(self):
        """`"  /etc/...implementation_plan.md"` must NOT pass."""
        from init_guard import InitGuard

        for path in ("  /etc/artifacts/plan.md",
                     "\t/home/vader/AI-AtlasForge/artifacts/plan.md",
                     "/home/vader/AI-AtlasForge/artifacts/plan.md "):
            allowed, _ = InitGuard.validate_write_path("PLANNING", path)
            assert allowed is False, f"whitespace-{repr(path)} must fail"

    def test_url_schemed_path_rejected(self):
        """`http://evil.com/artifacts/plan.md` must NOT pass."""
        from init_guard import InitGuard

        for path in ("http://evil.com/artifacts/plan.md",
                     "https://attacker.example/artifacts/plan.md",
                     "file:///etc/artifacts/plan.md"):
            allowed, _ = InitGuard.validate_write_path("PLANNING", path)
            assert allowed is False, f"url-schemed {path} must fail"

    def test_nul_byte_rejected(self):
        """NUL byte in path must fail-closed (TOCTOU risk)."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", "/home/vader/AI-AtlasForge/artifacts/plan\x00.md"
        )
        assert allowed is False

    def test_out_of_workspace_absolute_rejected(self):
        """`/opt/`, `/srv/`, `/mnt/`, `/tmp/other/`, `/home/other/` all
        fail even when the filename matches the pattern, because they're
        outside the workspace allow-list."""
        from init_guard import InitGuard

        for path in ("/opt/evil/artifacts/plan.md",
                     "/srv/evil/artifacts/plan.md",
                     "/mnt/usb/artifacts/plan.md",
                     "/home/other_user/artifacts/plan.md",
                     "/media/cdrom/artifacts/plan.md"):
            allowed, _ = InitGuard.validate_write_path("PLANNING", path)
            assert allowed is False, f"out-of-workspace {path} must fail"

    def test_in_workspace_absolute_allowed(self):
        """Absolute path under workspace root still works."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "PLANNING",
            "/home/vader/AI-AtlasForge/workspace/proj/artifacts/plan.md",
        )
        assert allowed is True

    def test_building_star_wildcard_still_workspace_scoped(self):
        """Iter-4 H2: BUILDING's `["*"]` pattern allows ANY filename inside
        the workspace, but does NOT permit writes outside the workspace.

        Iter-3 treated `"*"` in write_paths_allowed as a scope escape,
        allowing `/tmp/foo/bar.py` and `/home/otheruser/id_rsa`. Red Team
        iter-3 flagged this as HIGH (H2): an unbounded wildcard lets the
        agent exfiltrate to or overwrite files outside the project during
        BUILDING. Iter-4 fix: the workspace allow-list always runs; `"*"`
        is just a filename wildcard.
        """
        from init_guard import InitGuard

        # In-workspace path still allowed under `["*"]`.
        allowed, _ = InitGuard.validate_write_path(
            "BUILDING",
            "/home/vader/AI-AtlasForge/workspace/proj/src/code.py",
        )
        assert allowed is True

        # Out-of-workspace paths now REJECTED despite the `["*"]` pattern.
        for path in (
            "/tmp/foo/bar.py",
            "/home/otheruser/id_rsa",
            "/opt/secrets/token",
        ):
            allowed, reason = InitGuard.validate_write_path("BUILDING", path)
            assert allowed is False, (
                f"Iter-4 H2: BUILDING must reject out-of-workspace {path}: {reason}"
            )


class TestIter3CycleEndExtensionAllowlist:
    """H8/H14: cycle_report pattern must not accept .sh/.py/.exe."""

    def test_cycle_report_md_allowed(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END",
            "/home/vader/AI-AtlasForge/workspace/proj/cycle_report_final.md",
        )
        assert allowed is True

    def test_cycle_report_json_allowed(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END",
            "/home/vader/AI-AtlasForge/workspace/proj/cycle_report_final.json",
        )
        assert allowed is True

    def test_cycle_report_sh_rejected(self):
        """Code-smuggling via report name must fail."""
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END",
            "/home/vader/AI-AtlasForge/workspace/proj/cycle_report_evil.sh",
        )
        assert allowed is False

    def test_cycle_report_py_rejected(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END",
            "/home/vader/AI-AtlasForge/workspace/proj/cycle_report_evil.py",
        )
        assert allowed is False

    def test_cycle_report_exe_rejected(self):
        from init_guard import InitGuard

        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END",
            "/home/vader/AI-AtlasForge/workspace/proj/my_cycle_report.exe",
        )
        assert allowed is False


class TestIter3StageReprControlEscapes:
    """LIGHT finding: _safe_stage_repr must escape all C0 controls, not just \\r\\n."""

    def test_tab_escaped(self):
        from init_guard import _safe_stage_repr

        out = _safe_stage_repr("FOO\tBAR")
        assert "\t" not in out
        assert "\\x09" in out

    def test_bell_escaped(self):
        from init_guard import _safe_stage_repr

        out = _safe_stage_repr("FOO\x07BAR")
        assert "\x07" not in out

    def test_ansi_escape_sequence_sanitized(self):
        """ANSI escape (\\x1b[31m) must be rendered inert."""
        from init_guard import _safe_stage_repr

        out = _safe_stage_repr("FOO\x1b[31mEVIL")
        assert "\x1b" not in out
        assert "\\x1b" in out


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
