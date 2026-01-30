"""
Tests for Cycle Continuation Logic - Bug Fix Validation

These tests validate the fix for the bug where continuation prompt
doesn't trigger when going from Cycle Budget 1 to Cycle Budget 2
in multi-cycle missions.

Bug Root Cause:
The original condition `if continuation_prompt and self.should_continue_cycle()`
required BOTH a non-empty continuation_prompt AND should_continue_cycle() to be True.
If Claude's CYCLE_END response didn't include a continuation_prompt, the cycle
would not advance even though cycles remained.

Fix:
Changed to check should_continue_cycle() FIRST, then use a default continuation
prompt if Claude didn't provide one.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestCycleEndToPlanningTransition:
    """Tests for CYCLE_END -> PLANNING transitions with continuation."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_cycle_transition_with_continuation_prompt(self):
        """Test normal case: cycle transitions when continuation_prompt is provided."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()
            orch.state.get_field = Mock(return_value="Original mission statement")

            # Set up cycles to have budget remaining
            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 3
            orch.cycles.should_continue = Mock(return_value=True)
            orch.cycles.create_cycle_completed_event = Mock(return_value=Mock())
            orch.cycles.advance_cycle = Mock(return_value={"old_cycle": 1, "new_cycle": 2})
            orch.cycles.create_cycle_started_event = Mock(return_value=Mock())

            # Mock handler to return PLANNING as next stage with continuation_prompt
            mock_result = StageResult(
                status="cycle_complete",
                next_stage="PLANNING",
                success=True,
                events_to_emit=[],
                output_data={"continuation_prompt": "Continue with phase 2"}
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()
            orch.update_stage = Mock()

            response = {"status": "cycle_complete", "continuation_prompt": "Continue with phase 2"}
            next_stage = orch.process_response(response)

            # Should have called advance_to_next_cycle
            orch.cycles.advance_cycle.assert_called_once_with("Continue with phase 2")

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_cycle_transition_empty_continuation_prompt_bug_scenario(self):
        """Test BUG SCENARIO: cycle should still transition when continuation_prompt is empty."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()
            orch.state.get_field = Mock(return_value="Original mission statement")

            # Set up cycles to have budget remaining
            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 2  # Exactly the bug scenario: 2 cycles
            orch.cycles.should_continue = Mock(return_value=True)
            orch.cycles.create_cycle_completed_event = Mock(return_value=Mock())
            orch.cycles.advance_cycle = Mock(return_value={"old_cycle": 1, "new_cycle": 2})
            orch.cycles.create_cycle_started_event = Mock(return_value=Mock())

            # Mock handler to return PLANNING but with EMPTY continuation_prompt
            mock_result = StageResult(
                status="cycle_complete",
                next_stage="PLANNING",
                success=True,
                events_to_emit=[],
                output_data={"continuation_prompt": ""}  # EMPTY! This was the bug trigger
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()
            orch.update_stage = Mock()

            # Add the _generate_default_continuation method
            orch._generate_default_continuation = Mock(return_value="Default continuation prompt")

            response = {"status": "cycle_complete", "continuation_prompt": ""}
            next_stage = orch.process_response(response)

            # With the fix, should have called _generate_default_continuation
            orch._generate_default_continuation.assert_called_once()
            # And should have advanced the cycle with the default prompt
            orch.cycles.advance_cycle.assert_called_once_with("Default continuation prompt")

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_cycle_transition_missing_continuation_key(self):
        """Test that missing continuation_prompt key is handled (uses default)."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()
            orch.state.get_field = Mock(return_value="Original mission")

            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 3
            orch.cycles.should_continue = Mock(return_value=True)
            orch.cycles.create_cycle_completed_event = Mock(return_value=Mock())
            orch.cycles.advance_cycle = Mock(return_value={"old_cycle": 1, "new_cycle": 2})
            orch.cycles.create_cycle_started_event = Mock(return_value=Mock())

            # Response has NO continuation_prompt key at all
            mock_result = StageResult(
                status="cycle_complete",
                next_stage="PLANNING",
                success=True,
                events_to_emit=[],
                output_data={}  # No continuation_prompt key
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()
            orch.update_stage = Mock()

            orch._generate_default_continuation = Mock(return_value="Default prompt")

            response = {"status": "cycle_complete"}
            orch.process_response(response)

            orch._generate_default_continuation.assert_called_once()
            orch.cycles.advance_cycle.assert_called_once_with("Default prompt")


class TestDefaultContinuationGenerator:
    """Tests for the default continuation prompt generator."""

    def test_generate_default_continuation_format(self):
        """Test that default continuation prompt has expected format."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.get_field = Mock(side_effect=lambda key, default=None: {
                "original_problem_statement": "Build a task manager app",
                "problem_statement": "Build a task manager app",
            }.get(key, default))

            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 3

            prompt = orch._generate_default_continuation()

            assert "Cycle 2 of 3" in prompt
            assert "Build a task manager app" in prompt
            assert "ORIGINAL MISSION" in prompt
            assert "PREVIOUS CYCLE NOTE" in prompt
            assert "did not provide a specific continuation prompt" in prompt

    def test_generate_default_continuation_uses_original_mission(self):
        """Test that generator prefers original_problem_statement."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()

            # Return original_problem_statement
            orch.state = Mock()
            orch.state.get_field = Mock(side_effect=lambda key, default=None: {
                "original_problem_statement": "Original mission text",
                "problem_statement": "Modified mission text",
            }.get(key, default))

            orch.cycles = Mock()
            orch.cycles.current_cycle = 2
            orch.cycles.cycle_budget = 5

            prompt = orch._generate_default_continuation()

            assert "Original mission text" in prompt
            assert "Modified mission text" not in prompt

    def test_generate_default_continuation_fallback_to_problem_statement(self):
        """Test fallback to problem_statement when original not available."""
        from af_engine.orchestrator import StageOrchestrator

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()

            # Only return problem_statement (original is None)
            orch.state = Mock()
            orch.state.get_field = Mock(side_effect=lambda key, default=None: {
                "original_problem_statement": None,
                "problem_statement": "Fallback mission text",
            }.get(key, "Continue the mission" if default else None))

            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 2

            prompt = orch._generate_default_continuation()

            assert "Fallback mission text" in prompt or "Continue the mission" in prompt


class TestTwoCycleMissionScenario:
    """End-to-end style tests for 2-cycle mission scenario."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_two_cycle_mission_advances_correctly(self):
        """Test that a 2-cycle mission correctly advances from cycle 1 to cycle 2."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()
            orch.state.get_field = Mock(return_value="Two-cycle test mission")

            # Exactly 2 cycles - the bug scenario
            orch.cycles = Mock()
            orch.cycles.current_cycle = 1
            orch.cycles.cycle_budget = 2
            orch.cycles.should_continue = Mock(return_value=True)
            orch.cycles.create_cycle_completed_event = Mock(return_value=Mock())
            orch.cycles.advance_cycle = Mock(return_value={"old_cycle": 1, "new_cycle": 2})
            orch.cycles.create_cycle_started_event = Mock(return_value=Mock())

            # Simulate Claude providing empty continuation
            mock_result = StageResult(
                status="cycle_complete",
                next_stage="PLANNING",
                success=True,
                events_to_emit=[],
                output_data={"continuation_prompt": "", "cycle_report": {"summary": "Cycle 1 done"}}
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()
            orch.update_stage = Mock()
            orch._generate_default_continuation = Mock(return_value="Default continuation")

            response = {"status": "cycle_complete"}
            orch.process_response(response)

            # Verify cycle advancement happened
            orch.cycles.should_continue.assert_called()
            orch._generate_default_continuation.assert_called_once()
            orch.cycles.advance_cycle.assert_called_once_with("Default continuation")

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_no_advancement_when_cycles_exhausted(self):
        """Test that no cycle advancement happens when budget is exhausted."""
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.stages.base import StageResult

        with patch.object(StageOrchestrator, '__init__', lambda x, **kwargs: None):
            orch = StageOrchestrator()
            orch.state = Mock()
            orch.state.current_stage = "CYCLE_END"
            orch.state.increment_iteration = Mock()

            # At budget limit
            orch.cycles = Mock()
            orch.cycles.current_cycle = 2
            orch.cycles.cycle_budget = 2
            orch.cycles.should_continue = Mock(return_value=False)  # No more cycles
            orch.cycles.advance_cycle = Mock()

            # Response indicates mission complete
            mock_result = StageResult(
                status="mission_complete",
                next_stage="COMPLETE",
                success=True,
                events_to_emit=[],
                output_data={}
            )

            mock_handler = Mock()
            mock_handler.process_response = Mock(return_value=mock_result)
            orch.registry = Mock()
            orch.registry.get_handler = Mock(return_value=mock_handler)

            mock_context = Mock()
            orch.prompts = Mock()
            orch.prompts.build_context = Mock(return_value=mock_context)

            orch.integrations = Mock()

            response = {"status": "mission_complete"}
            next_stage = orch.process_response(response)

            # Should NOT advance cycle
            orch.cycles.advance_cycle.assert_not_called()
            assert next_stage == "COMPLETE"


class TestCycleEndHandlerWarning:
    """Tests for warning logging in CycleEndStageHandler."""

    def test_warning_logged_for_empty_continuation_prompt(self):
        """Test that a warning is logged when continuation_prompt is empty."""
        from af_engine.stages.cycle_end import CycleEndStageHandler
        from af_engine.stages.base import StageContext
        import logging

        handler = CycleEndStageHandler()

        mock_context = Mock(spec=StageContext)
        mock_context.cycle_number = 1
        mock_context.cycle_budget = 3
        mock_context.mission_id = "test_mission"

        response = {
            "status": "cycle_complete",
            "continuation_prompt": "",  # Empty
            "cycle_report": {"summary": "Done"}
        }

        with patch('af_engine.stages.cycle_end.logger') as mock_logger:
            result = handler.process_response(response, mock_context)

            # Warning should have been logged
            mock_logger.warning.assert_called_once()
            call_args = mock_logger.warning.call_args[0][0]
            assert "No continuation_prompt" in call_args
            assert "cycle 1" in call_args

    def test_no_warning_when_continuation_prompt_provided(self):
        """Test that no warning is logged when continuation_prompt is provided."""
        from af_engine.stages.cycle_end import CycleEndStageHandler
        from af_engine.stages.base import StageContext

        handler = CycleEndStageHandler()

        mock_context = Mock(spec=StageContext)
        mock_context.cycle_number = 1
        mock_context.cycle_budget = 3
        mock_context.mission_id = "test_mission"

        response = {
            "status": "cycle_complete",
            "continuation_prompt": "Continue with phase 2",  # Provided
            "cycle_report": {"summary": "Done"}
        }

        with patch('af_engine.stages.cycle_end.logger') as mock_logger:
            result = handler.process_response(response, mock_context)

            # Warning should NOT have been logged
            mock_logger.warning.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
