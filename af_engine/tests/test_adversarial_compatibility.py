"""
ADVERSARIAL TEST CASES
StageOrchestrator Compatibility Testing

These tests are designed to BREAK the new implementation and expose edge cases
not covered by existing tests.

Run with: pytest af_engine/tests/test_adversarial_compatibility.py -v
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import tempfile
import json


class TestCriticalIssues:
    """Tests for CRITICAL severity issues."""

    @pytest.mark.regression
    def test_queue_processing_flag_not_checked(self):
        """CRITICAL: log_history doesn't check _queue_processing flag."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch._queue_processing = True

            orch.log_history("test entry", {"key": "value"})
            orch.state.log_history.assert_called_once()

    @pytest.mark.regression
    def test_mission_dir_property_missing(self):
        """CRITICAL: StateManager doesn't expose mission_dir property."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock(spec=['mission', 'current_stage', 'mission_id'])

            with pytest.raises(AttributeError):
                _ = orch.mission_dir

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_missing_cycle_advancement(self):
        """CRITICAL: process_response() doesn't handle CYCLE_END properly."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()
            orch.mission = {"current_cycle": 1, "cycle_budget": 3}

            mock_result = StageResult(
                status="cycle_complete",
                next_stage="PLANNING",
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

            response = {"status": "cycle_complete", "continuation_prompt": "test"}
            next_stage = orch.process_response(response)

            assert next_stage == "PLANNING"


class TestHighSeverityIssues:
    """Tests for HIGH severity issues."""

    @pytest.mark.regression
    def test_get_recent_history_broken(self):
        """HIGH: get_recent_history() accesses wrong property."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock(spec=['mission', 'log_history'])
            orch.state.mission = {
                "history": [
                    {"entry": "first"},
                    {"entry": "second"},
                ]
            }

            with pytest.raises(AttributeError):
                _ = orch.get_recent_history(1)

    @pytest.mark.regression
    def test_invalid_stage_silent_fail(self):
        """HIGH: update_stage() silently fails with invalid stage."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"
            orch.state.mission_id = "test"
            orch.state.iteration = 0
            orch.integrations = Mock()

            result = orch.update_stage("INVALID_STAGE")

            assert result is None
            orch.state.update_stage.assert_not_called()

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_non_dict_response_crashes(self):
        """HIGH: process_response() only guards against None."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "PLANNING"

            mock_handler = Mock()
            mock_handler.process_response = Mock(side_effect=AttributeError("'str' object has no attribute 'get'"))

            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)
            orch.integrations = Mock()

            for invalid_response in ["", 42, []]:
                with pytest.raises(AttributeError):
                    orch.process_response(invalid_response)

    @pytest.mark.regression
    def test_reset_mission_incomplete(self):
        """HIGH: reset_mission() drops critical fields."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()

            original_mission = {
                "mission_id": "my_mission_123",
                "problem_statement": "test problem",
                "preferences": {"lang": "python"},
                "success_criteria": ["criterion1", "criterion2"],
                "cycle_budget": 3,
                "current_cycle": 2,
                "artifacts": {"code": ["file.py"]},
                "current_stage": "BUILDING",
                "iteration": 5,
            }

            orch.state = Mock()
            orch.state.mission = original_mission

            orch.reset_mission()

            reset_mission = orch.state.mission

            assert reset_mission["problem_statement"] == "test problem"
            assert reset_mission["preferences"] == {"lang": "python"}

            assert "mission_id" not in reset_mission or reset_mission.get("mission_id") != "my_mission_123"
            assert "success_criteria" not in reset_mission
            assert "cycle_budget" not in reset_mission

    @pytest.mark.regression
    def test_load_mission_json_error(self):
        """HIGH: load_mission_from_file() handles invalid JSON gracefully.

        The function should return False when given invalid JSON, not crash.
        """
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.mission = {}
            orch.save_mission = Mock()

            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
                f.write("{invalid json")
                temp_file = Path(f.name)

            try:
                # The function should handle JSON errors gracefully and return False
                result = orch.load_mission_from_file(temp_file)
                assert result is False, "load_mission_from_file should return False for invalid JSON"
            finally:
                temp_file.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
