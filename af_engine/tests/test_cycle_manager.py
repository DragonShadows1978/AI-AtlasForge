"""
Tests for CycleManager - Multi-Cycle Iteration Logic

These tests validate:
- Cycle budget tracking
- Cycle advancement
- Continuation prompt generation
- Cycle history management
- Event creation
"""

import pytest
from unittest.mock import Mock, MagicMock
from pathlib import Path


class TestCycleManagerProperties:
    """Tests for CycleManager properties."""

    def test_current_cycle_property(self):
        """Test current_cycle property."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 3
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.current_cycle == 3

    def test_cycle_budget_property(self):
        """Test cycle_budget property."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 10

        mgr = CycleManager(mock_state)

        assert mgr.cycle_budget == 10

    def test_cycles_remaining(self):
        """Test cycles_remaining calculation."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 3
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.cycles_remaining == 2

    def test_cycles_remaining_at_budget(self):
        """Test cycles_remaining when at budget."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 5
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.cycles_remaining == 0

    def test_is_last_cycle(self):
        """Test is_last_cycle property."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 5
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.is_last_cycle is True

    def test_is_not_last_cycle(self):
        """Test is_last_cycle returns False when not last."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 3
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.is_last_cycle is False


class TestCycleContinuation:
    """Tests for cycle continuation logic."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_should_continue_true(self):
        """Test should_continue returns True when cycles remain."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.should_continue() is True

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_should_continue_false(self):
        """Test should_continue returns False when at budget."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 5
        mock_state.cycle_budget = 5

        mgr = CycleManager(mock_state)

        assert mgr.should_continue() is False


class TestCycleAdvancement:
    """Tests for cycle advancement."""

    def test_advance_cycle(self):
        """Test advancing to next cycle."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5
        mock_state.advance_cycle = Mock(return_value=3)

        mgr = CycleManager(mock_state)
        result = mgr.advance_cycle("Continue with next phase")

        assert result["old_cycle"] == 2
        assert result["new_cycle"] == 3
        assert result["continuation_prompt"] == "Continue with next phase"
        mock_state.advance_cycle.assert_called_once_with("Continue with next phase")


class TestContinuationPromptGeneration:
    """Tests for continuation prompt generation."""

    def test_generate_continuation_prompt(self):
        """Test generating continuation prompt."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5
        mock_state.get_field = Mock(side_effect=lambda key, default=None: {
            "original_problem_statement": "Build a search engine",
        }.get(key, default))

        mgr = CycleManager(mock_state)
        prompt = mgr.generate_continuation_prompt(
            cycle_summary="Implemented basic indexing",
            findings=["Indexing is fast", "Need more test coverage"],
            next_objectives=["Add search API", "Improve ranking"],
        )

        assert "Cycle 3 of 5" in prompt
        assert "Build a search engine" in prompt
        assert "Implemented basic indexing" in prompt
        assert "Indexing is fast" in prompt
        assert "Add search API" in prompt

    def test_generate_continuation_prompt_empty_findings(self):
        """Test prompt generation with empty findings."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 1
        mock_state.cycle_budget = 3
        mock_state.get_field = Mock(return_value="Test mission")

        mgr = CycleManager(mock_state)
        prompt = mgr.generate_continuation_prompt(
            cycle_summary="Did some work",
            findings=[],
            next_objectives=[],
        )

        assert "None documented" in prompt
        assert "Continue from previous cycle" in prompt


class TestCycleRecording:
    """Tests for cycle completion recording."""

    def test_record_cycle_completion(self):
        """Test recording cycle completion."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 2
        mock_state.iteration = 5
        mock_state.get_field = Mock(return_value=[])
        mock_state.set_field = Mock()

        mgr = CycleManager(mock_state)
        mgr.record_cycle_completion(
            summary="Completed phase 2",
            status="completed",
            metrics={"lines_changed": 100},
        )

        mock_state.set_field.assert_called_once()
        call_args = mock_state.set_field.call_args
        assert call_args[0][0] == "cycle_history"
        history = call_args[0][1]
        assert len(history) == 1
        assert history[0]["cycle"] == 2
        assert history[0]["status"] == "completed"
        assert history[0]["metrics"]["lines_changed"] == 100


class TestCycleContext:
    """Tests for cycle context retrieval."""

    def test_get_cycle_context(self):
        """Test getting cycle context."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 3
        mock_state.cycle_budget = 5
        mock_state.iteration = 7
        mock_state.cycle_history = [{"cycle": 1}, {"cycle": 2}]

        mgr = CycleManager(mock_state)
        context = mgr.get_cycle_context()

        assert context["current_cycle"] == 3
        assert context["cycle_budget"] == 5
        assert context["cycles_remaining"] == 2
        assert context["is_last_cycle"] is False
        assert context["iteration"] == 7


class TestDeliverableValidation:
    """Tests for deliverable validation."""

    def test_validate_cycle_progress_found(self, tmp_path):
        """Test validation when deliverables exist."""
        from af_engine.cycle_manager import CycleManager

        # Create test files
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "report.md").write_text("Test report")
        (artifacts / "plan.md").write_text("Test plan")

        mock_state = Mock()
        mock_state.cycle_number = 1

        mgr = CycleManager(mock_state)
        result = mgr.validate_cycle_progress(
            expected_deliverables=["report.md", "plan.md"],
            artifacts_dir=artifacts,
        )

        assert result["valid"] is True
        assert len(result["found"]) == 2
        assert len(result["missing"]) == 0

    def test_validate_cycle_progress_missing(self, tmp_path):
        """Test validation when deliverables are missing."""
        from af_engine.cycle_manager import CycleManager

        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "report.md").write_text("Test report")

        mock_state = Mock()
        mock_state.cycle_number = 1

        mgr = CycleManager(mock_state)
        result = mgr.validate_cycle_progress(
            expected_deliverables=["report.md", "missing.md"],
            artifacts_dir=artifacts,
        )

        assert result["valid"] is False
        assert len(result["found"]) == 1
        assert "missing.md" in result["missing"]


class TestEventCreation:
    """Tests for event creation."""

    def test_create_cycle_started_event(self):
        """Test creating CYCLE_STARTED event."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.integrations.base import StageEvent

        mock_state = Mock()
        mock_state.mission_id = "test_mission"
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5
        mock_state.iteration = 0
        mock_state.cycle_history = []

        mgr = CycleManager(mock_state)
        event = mgr.create_cycle_started_event()

        assert event.type == StageEvent.CYCLE_STARTED
        assert event.stage == "PLANNING"
        assert event.mission_id == "test_mission"

    def test_create_cycle_completed_event(self):
        """Test creating CYCLE_COMPLETED event."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.integrations.base import StageEvent

        mock_state = Mock()
        mock_state.mission_id = "test_mission"
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5
        mock_state.iteration = 10
        mock_state.cycle_history = []

        mgr = CycleManager(mock_state)
        event = mgr.create_cycle_completed_event(
            summary="Finished cycle 2",
            next_stage="PLANNING",
        )

        assert event.type == StageEvent.CYCLE_COMPLETED
        assert event.data["summary"] == "Finished cycle 2"
        assert event.data["next_stage"] == "PLANNING"


class TestCycleReporting:
    """Tests for cycle reporting."""

    def test_get_cycle_report(self):
        """Test generating cycle report."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_number = 3
        mock_state.cycle_budget = 5
        mock_state.iteration = 8
        mock_state.cycle_history = [
            {"cycle": 1, "status": "completed", "summary": "Phase 1 done"},
            {"cycle": 2, "status": "completed", "summary": "Phase 2 done"},
        ]

        mgr = CycleManager(mock_state)
        report = mgr.get_cycle_report()

        assert "Cycle Progress Report" in report
        assert "Current Cycle: 3 of 5" in report
        assert "Iterations in Cycle: 8" in report
        assert "Phase 1 done" in report
        assert "Phase 2 done" in report

    def test_format_cycle_history_for_prompt(self):
        """Test formatting cycle history for prompts."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_history = [
            {"cycle": 1, "status": "completed", "summary": "Built core", "iterations": 5},
            {"cycle": 2, "status": "partial", "summary": "Added tests", "iterations": 3},
        ]

        mgr = CycleManager(mock_state)
        formatted = mgr.format_cycle_history_for_prompt()

        assert "Cycle 1 (completed, 5 iterations)" in formatted
        assert "Built core" in formatted
        assert "Cycle 2 (partial, 3 iterations)" in formatted

    def test_format_empty_cycle_history(self):
        """Test formatting empty cycle history."""
        from af_engine.cycle_manager import CycleManager

        mock_state = Mock()
        mock_state.cycle_history = []

        mgr = CycleManager(mock_state)
        formatted = mgr.format_cycle_history_for_prompt()

        assert "No previous cycles" in formatted


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
