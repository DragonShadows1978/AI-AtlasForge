"""
Tests for StageOrchestrator - Core Orchestration Logic

These tests validate:
- Orchestrator initialization
- Stage transitions
- Prompt building
- Response processing
- Cycle management coordination
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path


class TestOrchestratorInitialization:
    """Tests for StageOrchestrator initialization."""

    def test_orchestrator_import(self):
        """Test that StageOrchestrator can be imported."""
        from af_engine.orchestrator import StageOrchestrator
        assert StageOrchestrator is not None

    def test_orchestrator_basic_init(self):
        """Test basic orchestrator initialization with mock state."""
        from af_engine.orchestrator import StageOrchestrator

        with patch('af_engine.orchestrator.StateManager') as mock_state:
            mock_state.return_value.mission = {"problem_statement": "test"}
            mock_state.return_value.current_stage = "PLANNING"
            mock_state.return_value.mission_id = "test_mission"
            mock_state.return_value.iteration = 0

            with patch('af_engine.orchestrator.StageRegistry'):
                with patch('af_engine.orchestrator.IntegrationManager') as mock_mgr:
                    mock_mgr.return_value.load_default_integrations = Mock()
                    mock_mgr.return_value.get_stats = Mock(return_value={
                        "handlers_registered": 5,
                        "handlers_available": 5,
                    })

                    orch = StageOrchestrator(
                        mission_path=Path("/tmp/test_mission.json")
                    )

                    assert orch is not None
                    assert orch.state is not None

    def test_backward_compat_alias(self):
        """Test that RDMissionController alias exists."""
        from af_engine.orchestrator import RDMissionController, StageOrchestrator
        assert RDMissionController is StageOrchestrator


class TestStageTransitions:
    """Tests for stage transition logic."""

    def test_valid_stages_list(self):
        """Test that STAGES constant contains expected stages."""
        from af_engine.orchestrator import StageOrchestrator

        expected = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]
        assert StageOrchestrator.STAGES == expected

    def test_update_stage_valid(self):
        """Test valid stage transition."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"
            orch.state.mission_id = "test"
            orch.state.iteration = 0
            orch.state.update_stage = Mock(return_value="PLANNING")
            orch.integrations = Mock()
            orch.integrations.emit_stage_completed = Mock()
            orch.integrations.emit_stage_started = Mock()
            orch.integrations.emit_mission_completed = Mock()

            orch.update_stage("BUILDING")

            orch.state.update_stage.assert_called_with("BUILDING")
            orch.integrations.emit_stage_completed.assert_called_once()
            orch.integrations.emit_stage_started.assert_called_once()

    def test_update_stage_to_complete(self):
        """Test transition to COMPLETE stage emits mission_completed."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]
            orch.state = Mock()
            orch.state.current_stage = "ANALYZING"
            orch.state.mission_id = "test"
            orch.state.iteration = 0
            orch.state.update_stage = Mock(return_value="ANALYZING")
            orch.integrations = Mock()
            orch.cycles = Mock()
            orch.cycles.current_cycle = 1

            orch.update_stage("COMPLETE")

            orch.integrations.emit_mission_completed.assert_called_once()


class TestPromptBuilding:
    """Tests for prompt building functionality."""

    def test_build_rd_prompt_structure(self):
        """Test that build_rd_prompt returns a string."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"
            orch.state.mission = {"problem_statement": "test mission"}
            orch.state.mission_id = "test"

            # Mock registry and handler
            mock_handler = Mock()
            mock_handler.get_prompt = Mock(return_value="Stage prompt content")
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            # Mock prompts factory
            mock_context = Mock()
            mock_context.problem_statement = "test mission"
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)
            orch.prompts.assemble_prompt = Mock(return_value="Full prompt")
            orch.prompts.inject_kb_context = Mock(return_value="Full prompt with KB")

            # Mock integrations
            orch.integrations = Mock()
            orch.integrations.get_handler = Mock(return_value=None)

            result = orch.build_rd_prompt()

            assert isinstance(result, str)
            orch.registry.get_handler.assert_called_with("PLANNING")


class TestResponseProcessing:
    """Tests for response processing."""

    def test_process_response_returns_next_stage(self):
        """Test that process_response returns next stage."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"
            orch.state.increment_iteration = Mock()

            mock_result = StageResult(
                status="plan_complete",
                next_stage="BUILDING",
                success=True,
                events_to_emit=[],
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()

            response = {"status": "plan_complete"}
            next_stage = orch.process_response(response)

            assert next_stage == "BUILDING"
            # Note: increment_iteration is only called when _increment_iteration is True
            # in output_data (e.g., needs_revision/needs_replanning). Normal transitions
            # do NOT increment iteration.
            orch.state.increment_iteration.assert_not_called()

    def test_process_response_handles_none(self):
        """Test that process_response handles None response."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"
            orch.state.increment_iteration = Mock()

            mock_result = StageResult(
                status="error",
                next_stage="PLANNING",
                success=False,
                events_to_emit=[],
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()

            # None response should be converted to empty dict
            next_stage = orch.process_response(None)

            mock_handler.process_response.assert_called_once()
            call_args = mock_handler.process_response.call_args
            assert call_args[0][0] == {}  # First arg should be empty dict


class TestCycleManagement:
    """Tests for cycle management coordination."""

    def test_should_continue_cycle(self):
        """Test cycle continuation check."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.cycles = Mock()
            orch.cycles.should_continue = Mock(return_value=True)

            assert orch.should_continue_cycle() is True

    def test_get_cycle_status(self):
        """Test getting cycle status."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.cycles = Mock()
            orch.cycles.get_cycle_context = Mock(return_value={
                "current_cycle": 2,
                "cycle_budget": 5,
            })

            status = orch.get_cycle_status()

            assert status["current_cycle"] == 2
            assert status["cycle_budget"] == 5


class TestStageRestrictions:
    """Tests for stage restriction methods."""

    def test_get_stage_restrictions(self):
        """Test getting stage restrictions."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"

            mock_restrictions = Mock()
            mock_restrictions.allowed_tools = ["Read", "Grep", "Glob"]
            mock_restrictions.blocked_tools = ["NotebookEdit"]
            mock_restrictions.allowed_write_paths = ["artifacts/"]
            mock_restrictions.forbidden_write_paths = ["src/"]
            mock_restrictions.allow_bash = True
            mock_restrictions.read_only = False

            orch.registry = Mock()
            orch.registry.get_restrictions = Mock(return_value=mock_restrictions)

            restrictions = orch.get_stage_restrictions()

            assert "allowed_tools" in restrictions
            assert "blocked_tools" in restrictions
            assert "NotebookEdit" in restrictions["blocked_tools"]

    def test_is_tool_allowed_blocked(self):
        """Test that blocked tools are not allowed."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "COMPLETE"

            mock_restrictions = Mock()
            mock_restrictions.allowed_tools = []
            mock_restrictions.blocked_tools = ["Edit", "Write", "Bash"]
            mock_restrictions.allowed_write_paths = []
            mock_restrictions.forbidden_write_paths = []
            mock_restrictions.allow_bash = False
            mock_restrictions.read_only = True

            orch.registry = Mock()
            orch.registry.get_restrictions = Mock(return_value=mock_restrictions)

            assert orch.is_tool_allowed("Edit") is False
            assert orch.is_tool_allowed("Write") is False
            assert orch.is_tool_allowed("Read") is True


class TestUtilityMethods:
    """Tests for utility methods."""

    def test_log_history(self):
        """Test logging to history."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()

            orch.log_history("Test entry", {"key": "value"})

            orch.state.log_history.assert_called_once_with(
                "Test entry", {"key": "value"}
            )

    def test_get_status(self):
        """Test getting orchestrator status."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.mission_id = "test_mission"
            orch.state.current_stage = "BUILDING"
            orch.state.iteration = 3

            orch.cycles = Mock()
            orch.cycles.current_cycle = 2
            orch.cycles.cycle_budget = 5
            orch.cycles.cycles_remaining = 3

            orch.integrations = Mock()
            orch.integrations.get_stats = Mock(return_value={
                "handlers_registered": 10,
            })

            status = orch.get_status()

            assert status["mission_id"] == "test_mission"
            assert status["current_stage"] == "BUILDING"
            assert status["iteration"] == 3
            assert status["cycle"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
