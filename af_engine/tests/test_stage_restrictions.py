"""
Stage Restrictions Tests for the Modular af_engine.

These tests validate tool permissions for each stage:
1. Each stage has correct allowed_tools
2. Each stage has correct blocked_tools
3. Each stage has correct write path restrictions
4. COMPLETE stage is read-only

These tests ensure stage restrictions are ENFORCED, not just configured.
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch


class TestPlanningStageRestrictions:
    """Tests for PLANNING stage restrictions."""

    @pytest.mark.unit
    def test_planning_allowed_tools(self, planning_handler):
        """Test PLANNING stage allowed tools."""
        restrictions = planning_handler.get_restrictions()

        # Should allow these tools
        expected_allowed = ["Read", "Glob", "Grep", "Write", "Edit", "Bash", "WebFetch", "WebSearch", "Task"]
        for tool in expected_allowed:
            assert tool in restrictions.allowed_tools, f"PLANNING should allow {tool}"

    @pytest.mark.unit
    def test_planning_blocked_tools(self, planning_handler):
        """Test PLANNING stage blocked tools."""
        restrictions = planning_handler.get_restrictions()

        # Should block NotebookEdit
        assert "NotebookEdit" in restrictions.blocked_tools

    @pytest.mark.unit
    def test_planning_write_paths(self, planning_handler):
        """Test PLANNING stage write path restrictions."""
        restrictions = planning_handler.get_restrictions()

        # Should allow artifacts and research
        assert any("artifacts" in p for p in restrictions.allowed_write_paths)
        assert any("research" in p for p in restrictions.allowed_write_paths)

        # Should forbid source code
        assert any(".py" in p for p in restrictions.forbidden_write_paths)
        assert any(".js" in p for p in restrictions.forbidden_write_paths)
        assert any(".ts" in p for p in restrictions.forbidden_write_paths)

    @pytest.mark.unit
    def test_planning_allows_bash(self, planning_handler):
        """Test PLANNING stage allows bash."""
        restrictions = planning_handler.get_restrictions()
        assert restrictions.allow_bash is True

    @pytest.mark.unit
    def test_planning_not_read_only(self, planning_handler):
        """Test PLANNING stage is not read-only."""
        restrictions = planning_handler.get_restrictions()
        assert restrictions.read_only is False


class TestBuildingStageRestrictions:
    """Tests for BUILDING stage restrictions."""

    @pytest.mark.unit
    def test_building_has_full_access(self, building_handler):
        """Test BUILDING stage has full write access."""
        restrictions = building_handler.get_restrictions()

        # BUILDING should have no blocked tools
        assert len(restrictions.blocked_tools) == 0

        # Should allow all paths
        assert "*" in restrictions.allowed_write_paths

        # Should not forbid any paths
        assert len(restrictions.forbidden_write_paths) == 0

    @pytest.mark.unit
    def test_building_allows_bash(self, building_handler):
        """Test BUILDING stage allows bash."""
        restrictions = building_handler.get_restrictions()
        assert restrictions.allow_bash is True

    @pytest.mark.unit
    def test_building_not_read_only(self, building_handler):
        """Test BUILDING stage is not read-only."""
        restrictions = building_handler.get_restrictions()
        assert restrictions.read_only is False


class TestTestingStageRestrictions:
    """Tests for TESTING stage restrictions."""

    @pytest.mark.unit
    def test_testing_has_full_access(self, testing_handler):
        """Test TESTING stage has full write access for test creation."""
        restrictions = testing_handler.get_restrictions()

        # TESTING should have full access like BUILDING
        assert len(restrictions.blocked_tools) == 0
        assert "*" in restrictions.allowed_write_paths

    @pytest.mark.unit
    def test_testing_allows_bash(self, testing_handler):
        """Test TESTING stage allows bash for running tests."""
        restrictions = testing_handler.get_restrictions()
        assert restrictions.allow_bash is True


class TestAnalyzingStageRestrictions:
    """Tests for ANALYZING stage restrictions."""

    @pytest.mark.unit
    def test_analyzing_allowed_tools(self, analyzing_handler):
        """Test ANALYZING stage allowed tools."""
        restrictions = analyzing_handler.get_restrictions()

        # Should allow these tools
        expected_allowed = ["Read", "Glob", "Grep", "Write", "Edit", "WebFetch", "WebSearch", "Task"]
        for tool in expected_allowed:
            assert tool in restrictions.allowed_tools, f"ANALYZING should allow {tool}"

    @pytest.mark.unit
    def test_analyzing_write_paths(self, analyzing_handler):
        """Test ANALYZING stage write path restrictions."""
        restrictions = analyzing_handler.get_restrictions()

        # Should allow artifacts and research
        assert any("artifacts" in p for p in restrictions.allowed_write_paths)
        assert any("research" in p for p in restrictions.allowed_write_paths)

        # Should forbid source code files
        assert any(".py" in p for p in restrictions.forbidden_write_paths)

    @pytest.mark.unit
    def test_analyzing_no_bash(self, analyzing_handler):
        """Test ANALYZING stage does not allow bash (for safety)."""
        restrictions = analyzing_handler.get_restrictions()
        assert restrictions.allow_bash is False


class TestCycleEndStageRestrictions:
    """Tests for CYCLE_END stage restrictions."""

    @pytest.mark.unit
    def test_cycle_end_allowed_tools(self, cycle_end_handler):
        """Test CYCLE_END stage allowed tools."""
        restrictions = cycle_end_handler.get_restrictions()

        # Should allow read and write tools
        expected_allowed = ["Read", "Glob", "Grep", "Write", "Edit", "Task"]
        for tool in expected_allowed:
            assert tool in restrictions.allowed_tools, f"CYCLE_END should allow {tool}"

    @pytest.mark.unit
    def test_cycle_end_write_paths(self, cycle_end_handler):
        """Test CYCLE_END stage write path restrictions."""
        restrictions = cycle_end_handler.get_restrictions()

        # Should allow artifacts, research, reports, mission_logs
        assert any("artifacts" in p for p in restrictions.allowed_write_paths)
        assert any("research" in p for p in restrictions.allowed_write_paths)
        assert any("report" in p for p in restrictions.allowed_write_paths)

        # Should forbid source code
        assert any(".py" in p for p in restrictions.forbidden_write_paths)

    @pytest.mark.unit
    def test_cycle_end_no_bash(self, cycle_end_handler):
        """Test CYCLE_END stage does not allow bash."""
        restrictions = cycle_end_handler.get_restrictions()
        assert restrictions.allow_bash is False


class TestCompleteStageRestrictions:
    """Tests for COMPLETE stage restrictions."""

    @pytest.mark.unit
    @pytest.mark.regression
    def test_complete_is_read_only(self, complete_handler):
        """Test COMPLETE stage is read-only.

        CRITICAL: COMPLETE stage should not allow any writes.
        """
        restrictions = complete_handler.get_restrictions()

        assert restrictions.read_only is True

    @pytest.mark.unit
    @pytest.mark.regression
    def test_complete_blocked_tools(self, complete_handler):
        """Test COMPLETE stage blocks write tools."""
        restrictions = complete_handler.get_restrictions()

        # Must block these tools
        must_block = ["Edit", "Write", "NotebookEdit", "Bash"]
        for tool in must_block:
            assert tool in restrictions.blocked_tools, f"COMPLETE must block {tool}"

    @pytest.mark.unit
    def test_complete_allowed_tools(self, complete_handler):
        """Test COMPLETE stage only allows read tools."""
        restrictions = complete_handler.get_restrictions()

        # Should only allow read tools
        expected_allowed = ["Read", "Glob", "Grep"]
        assert set(restrictions.allowed_tools) == set(expected_allowed)

    @pytest.mark.unit
    def test_complete_no_write_paths(self, complete_handler):
        """Test COMPLETE stage has no allowed write paths."""
        restrictions = complete_handler.get_restrictions()

        # Should have no allowed write paths (or empty list)
        assert len(restrictions.allowed_write_paths) == 0

        # Should forbid all paths
        assert "*" in restrictions.forbidden_write_paths

    @pytest.mark.unit
    def test_complete_no_bash(self, complete_handler):
        """Test COMPLETE stage does not allow bash."""
        restrictions = complete_handler.get_restrictions()
        assert restrictions.allow_bash is False


class TestOrchestratorStageRestrictions:
    """Tests for orchestrator's stage restriction methods."""

    @pytest.mark.unit
    def test_get_stage_restrictions_for_planning(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test get_stage_restrictions returns correct restrictions for PLANNING."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        restrictions = orch.get_stage_restrictions("PLANNING")

        assert "NotebookEdit" in restrictions["blocked_tools"]
        assert restrictions["allow_bash"] is True

    @pytest.mark.unit
    def test_get_stage_restrictions_for_complete(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test get_stage_restrictions returns correct restrictions for COMPLETE."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        restrictions = orch.get_stage_restrictions("COMPLETE")

        assert restrictions["read_only"] is True
        assert "Edit" in restrictions["blocked_tools"]
        assert "Write" in restrictions["blocked_tools"]
        assert "Bash" in restrictions["blocked_tools"]

    @pytest.mark.unit
    def test_is_tool_allowed_building(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test is_tool_allowed returns True for all tools in BUILDING."""
        mission = mission_factory(current_stage="BUILDING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        # All tools should be allowed in BUILDING
        assert orch.is_tool_allowed("Edit") is True
        assert orch.is_tool_allowed("Write") is True
        assert orch.is_tool_allowed("Bash") is True
        assert orch.is_tool_allowed("NotebookEdit") is True

    @pytest.mark.unit
    def test_is_tool_allowed_complete(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test is_tool_allowed returns False for write tools in COMPLETE."""
        mission = mission_factory(current_stage="COMPLETE")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("COMPLETE")

        # Write tools should be blocked in COMPLETE
        assert orch.is_tool_allowed("Edit") is False
        assert orch.is_tool_allowed("Write") is False
        assert orch.is_tool_allowed("Bash") is False

        # Read tools should be allowed
        assert orch.is_tool_allowed("Read") is True
        assert orch.is_tool_allowed("Glob") is True
        assert orch.is_tool_allowed("Grep") is True


class TestStageRegistryRestrictions:
    """Tests for StageRegistry restriction retrieval."""

    @pytest.mark.unit
    def test_registry_returns_handler_restrictions(self):
        """Test StageRegistry returns restrictions from handlers."""
        from af_engine.stage_registry import StageRegistry

        registry = StageRegistry()

        # Get restrictions for each stage
        planning = registry.get_restrictions("PLANNING")
        assert "NotebookEdit" in planning.blocked_tools

        complete = registry.get_restrictions("COMPLETE")
        assert complete.read_only is True

    @pytest.mark.unit
    def test_registry_all_stages_have_restrictions(self):
        """Test that all registered stages have valid restrictions."""
        from af_engine.stage_registry import StageRegistry

        registry = StageRegistry()

        for stage_name in registry.get_all_stages():
            restrictions = registry.get_restrictions(stage_name)

            # Every stage should have restrictions
            assert restrictions is not None
            assert hasattr(restrictions, 'allowed_tools')
            assert hasattr(restrictions, 'blocked_tools')
            assert hasattr(restrictions, 'allowed_write_paths')
            assert hasattr(restrictions, 'forbidden_write_paths')
            assert hasattr(restrictions, 'allow_bash')
            assert hasattr(restrictions, 'read_only')


class TestWritePathEnforcement:
    """Tests for write path enforcement logic."""

    @pytest.mark.unit
    def test_planning_allows_artifacts_path(self, planning_handler):
        """Test PLANNING allows writing to artifacts/."""
        restrictions = planning_handler.get_restrictions()

        # Check pattern matching for artifacts
        allowed = restrictions.allowed_write_paths
        # Should match */artifacts/* or similar
        artifacts_allowed = any("artifacts" in p for p in allowed)
        assert artifacts_allowed

    @pytest.mark.unit
    def test_planning_forbids_py_files(self, planning_handler):
        """Test PLANNING forbids writing .py files."""
        restrictions = planning_handler.get_restrictions()

        # Should forbid .py files
        py_forbidden = any(".py" in p for p in restrictions.forbidden_write_paths)
        assert py_forbidden

    @pytest.mark.unit
    def test_building_allows_all_paths(self, building_handler):
        """Test BUILDING allows writing to all paths."""
        restrictions = building_handler.get_restrictions()

        # Should have "*" in allowed paths
        assert "*" in restrictions.allowed_write_paths

        # Should have no forbidden paths
        assert len(restrictions.forbidden_write_paths) == 0


class TestStageRestrictionsConsistency:
    """Tests for consistency between stage restrictions."""

    @pytest.mark.unit
    def test_building_has_more_permissions_than_planning(
        self,
        planning_handler,
        building_handler
    ):
        """Test BUILDING has more permissions than PLANNING."""
        planning = planning_handler.get_restrictions()
        building = building_handler.get_restrictions()

        # BUILDING should have fewer blocked tools
        assert len(building.blocked_tools) <= len(planning.blocked_tools)

        # BUILDING should have more write paths
        # (BUILDING has "*" which covers everything)
        assert "*" in building.allowed_write_paths

    @pytest.mark.unit
    def test_complete_has_fewest_permissions(
        self,
        planning_handler,
        building_handler,
        analyzing_handler,
        complete_handler
    ):
        """Test COMPLETE has the most restrictive permissions."""
        handlers = [planning_handler, building_handler, analyzing_handler]
        complete = complete_handler.get_restrictions()

        # COMPLETE should be read-only
        assert complete.read_only is True

        # COMPLETE should have the most blocked tools
        for handler in handlers:
            restrictions = handler.get_restrictions()
            assert len(complete.blocked_tools) >= len(restrictions.blocked_tools)


class TestStageRestrictionsPersistence:
    """Tests for stage restrictions consistency across instances."""

    @pytest.mark.unit
    def test_restrictions_consistent_across_instances(self):
        """Test that creating new handler instances gives same restrictions."""
        from af_engine.stages.planning import PlanningStageHandler
        from af_engine.stages.complete import CompleteStageHandler

        # Create two instances
        handler1 = PlanningStageHandler()
        handler2 = PlanningStageHandler()

        r1 = handler1.get_restrictions()
        r2 = handler2.get_restrictions()

        # Should be equivalent
        assert r1.blocked_tools == r2.blocked_tools
        assert r1.allowed_write_paths == r2.allowed_write_paths
        assert r1.allow_bash == r2.allow_bash
        assert r1.read_only == r2.read_only


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
