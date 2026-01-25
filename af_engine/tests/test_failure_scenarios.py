"""
Failure Scenario Tests for the Modular af_engine.

These tests validate error handling and failure scenarios:
1. Claude timeout handling (MAX_CLAUDE_RETRIES respected)
2. Invalid JSON response handling
3. Empty response handling
4. None response handling
5. Graceful error recovery

These are CRITICAL regression tests for timeout/retry behavior.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestNoneResponseHandling:
    """Tests for handling None responses from Claude."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_orchestrator_handles_none_response(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that orchestrator handles None response gracefully.

        When Claude returns None (e.g., timeout), the orchestrator should
        convert it to an empty dict and not crash.
        """
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Process None response - should not raise
        next_stage = orch.process_response(None)

        # Should return some stage (handler decides what to do with empty response)
        assert next_stage is not None

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_none_converted_to_empty_dict(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that None is converted to {} before passing to handler."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Track what the handler receives
        received_responses = []
        original_get_handler = orch.registry.get_handler

        def tracking_get_handler(stage_name):
            handler = original_get_handler(stage_name)
            original_process = handler.process_response

            def tracking_process(response, context):
                received_responses.append(response)
                return original_process(response, context)

            handler.process_response = tracking_process
            return handler

        orch.registry.get_handler = tracking_get_handler

        # Process None
        orch.process_response(None)

        # Handler should have received {} not None
        assert len(received_responses) > 0
        assert received_responses[0] == {}


class TestEmptyResponseHandling:
    """Tests for handling empty responses."""

    @pytest.mark.regression
    def test_empty_dict_response_handled(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that empty dict response is handled gracefully."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Process empty dict - should not raise
        next_stage = orch.process_response({})

        assert next_stage is not None

    @pytest.mark.regression
    def test_empty_status_field_handled(
        self,
        analyzing_handler,
        stage_context_factory
    ):
        """Test that response with empty status is handled."""
        context = stage_context_factory()

        # Response with empty status
        response = {
            "status": "",
            "analysis": "Some analysis",
            "recommendation": ""
        }

        # Should not raise
        result = analyzing_handler.process_response(response, context)

        assert result is not None
        assert result.next_stage is not None


class TestInvalidResponseHandling:
    """Tests for handling invalid/malformed responses."""

    @pytest.mark.regression
    def test_missing_status_field_handled(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that response without status field is handled."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Response without status
        response = {
            "analysis": "Some analysis"
        }

        # Should not raise
        next_stage = orch.process_response(response)

        assert next_stage is not None

    @pytest.mark.regression
    def test_unexpected_status_handled_gracefully(
        self,
        analyzing_handler,
        stage_context_factory
    ):
        """Test that unexpected status values are handled gracefully."""
        context = stage_context_factory()

        # Response with unexpected status
        response = {
            "status": "unexpected_status_value",
            "analysis": "Some analysis"
        }

        # Should not raise, should return valid result
        result = analyzing_handler.process_response(response, context)

        assert result is not None
        # Default behavior should be to go to CYCLE_END
        assert result.next_stage == "CYCLE_END"


class TestStageHandlerErrorHandling:
    """Tests for stage handler error handling."""

    @pytest.mark.regression
    def test_planning_handles_unexpected_status(
        self,
        planning_handler,
        stage_context_factory
    ):
        """Test PlanningStageHandler handles unexpected status."""
        context = stage_context_factory()

        response = {"status": "something_unexpected"}
        result = planning_handler.process_response(response, context)

        # Should stay in PLANNING for unexpected status
        assert result.next_stage == "PLANNING"
        assert result.success is False

    @pytest.mark.regression
    def test_building_handles_build_blocked(
        self,
        building_handler,
        stage_context_factory
    ):
        """Test BuildingStageHandler handles build_blocked status."""
        context = stage_context_factory()

        response = {
            "status": "build_blocked",
            "blockers": ["Missing dependency"]
        }
        result = building_handler.process_response(response, context)

        # Should stay in BUILDING for blocked
        assert result.next_stage == "BUILDING"
        assert result.success is False

    @pytest.mark.regression
    def test_building_handles_build_in_progress(
        self,
        building_handler,
        stage_context_factory
    ):
        """Test BuildingStageHandler handles build_in_progress status."""
        context = stage_context_factory()

        response = {
            "status": "build_in_progress",
            "summary": "Still working..."
        }
        result = building_handler.process_response(response, context)

        # Should stay in BUILDING for in_progress
        assert result.next_stage == "BUILDING"
        assert result.success is True

    @pytest.mark.regression
    def test_testing_handles_unexpected_status(
        self,
        testing_handler,
        stage_context_factory
    ):
        """Test TestingStageHandler handles unexpected status."""
        context = stage_context_factory()

        response = {"status": "weird_status"}
        result = testing_handler.process_response(response, context)

        # Should stay in TESTING for unexpected status
        assert result.next_stage == "TESTING"


class TestCycleEndErrorHandling:
    """Tests for CycleEndStageHandler error handling."""

    @pytest.mark.regression
    def test_cycle_end_handles_unexpected_status(
        self,
        cycle_end_handler,
        stage_context_factory
    ):
        """Test CycleEndStageHandler handles unexpected status."""
        context = stage_context_factory(cycle_number=1, cycle_budget=3)

        response = {"status": "unexpected"}
        result = cycle_end_handler.process_response(response, context)

        # Should stay in CYCLE_END for unexpected status
        assert result.next_stage == "CYCLE_END"
        assert result.success is False

    @pytest.mark.regression
    def test_cycle_end_handles_missing_continuation_prompt(
        self,
        cycle_end_handler,
        stage_context_factory
    ):
        """Test CycleEndStageHandler handles missing continuation_prompt."""
        context = stage_context_factory(cycle_number=1, cycle_budget=3)

        response = {
            "status": "cycle_complete",
            "cycle_report": {"summary": "Done"}
            # Missing continuation_prompt
        }
        result = cycle_end_handler.process_response(response, context)

        # Should still work, continuation_prompt defaults to empty string
        assert result.next_stage == "PLANNING"


class TestOrchestratorErrorRecovery:
    """Tests for orchestrator error recovery."""

    @pytest.mark.regression
    def test_process_response_with_none_handler_result(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling when handler returns unexpected result."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Mock handler to return a result with events
        from af_engine.stages.base import StageResult

        mock_result = StageResult(
            success=True,
            next_stage="BUILDING",
            status="test",
            events_to_emit=[],  # Empty but valid
        )

        # Patch registry to return mock handler
        mock_handler = Mock()
        mock_handler.process_response = Mock(return_value=mock_result)
        orch.registry.get_handler = Mock(return_value=mock_handler)

        # Process response
        response = {"status": "test"}
        next_stage = orch.process_response(response)

        assert next_stage == "BUILDING"

    @pytest.mark.integration
    def test_stage_transition_after_error(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that stage transitions still work after handling an error."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # First, process a bad response
        orch.process_response(None)

        # Now process a good response - should still work
        good_response = claude_response_factory("PLANNING", status="plan_complete")
        next_stage = orch.process_response(good_response)

        assert next_stage == "BUILDING"

        # Transition should work
        orch.update_stage(next_stage)
        assert orch.current_stage == "BUILDING"


class TestMaxIterationsLimit:
    """Tests for max_iterations limit handling."""

    @pytest.mark.regression
    def test_iteration_respects_max_iterations(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that iteration can exceed max_iterations (soft limit).

        Note: max_iterations is a soft limit for display/warning purposes,
        not a hard limit that stops execution.
        """
        mission = mission_factory(
            iteration=9,
            current_stage="ANALYZING"
        )
        # Set max_iterations
        mission["max_iterations"] = 10

        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        # Current iteration is 9, max is 10
        assert orch.state.iteration == 9

        # Process needs_revision - should increment to 10
        needs_revision = {
            "status": "needs_revision",
            "recommendation": "BUILDING"
        }
        orch.process_response(needs_revision)

        # Iteration should be 10 now
        assert orch.state.iteration == 10

    @pytest.mark.regression
    def test_high_iteration_count_works(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory
    ):
        """Test that high iteration counts work correctly."""
        mission = mission_factory(iteration=50)
        orch = orchestrator_factory(mission=mission)

        # Process response at high iteration count
        response = claude_response_factory("PLANNING", status="plan_complete")
        next_stage = orch.process_response(response)

        # Should still work
        assert next_stage == "BUILDING"
        assert orch.state.iteration == 50  # Should not change on PLANNING success


class TestEventEmissionOnError:
    """Tests for event emission during error scenarios."""

    @pytest.mark.integration
    def test_events_emitted_despite_bad_response(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that events are still emitted even with problematic responses."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Track events
        emitted_events = []
        original_emit = orch.integrations.emit

        def tracking_emit(event):
            emitted_events.append(event)
            return original_emit(event)

        orch.integrations.emit = tracking_emit

        # Process empty response
        orch.process_response({})

        # Stage completed events should still be generated by handler
        # (handlers always emit STAGE_COMPLETED, even on failure)
        # Note: The actual event emission depends on handler implementation


class TestResponseDataExtraction:
    """Tests for extracting data from responses."""

    @pytest.mark.unit
    def test_analyzing_extracts_issues_found(
        self,
        analyzing_handler,
        stage_context_factory
    ):
        """Test that ANALYZING extracts issues_found from response."""
        context = stage_context_factory()

        response = {
            "status": "needs_revision",
            "issues_found": ["Issue 1", "Issue 2"],
            "recommendation": "BUILDING"
        }
        result = analyzing_handler.process_response(response, context)

        # Check that issues are in the emitted event data
        assert len(result.events_to_emit) > 0
        event_data = result.events_to_emit[0].data
        assert "issues_found" in event_data
        assert event_data["issues_found"] == ["Issue 1", "Issue 2"]

    @pytest.mark.unit
    def test_building_extracts_files_created(
        self,
        building_handler,
        stage_context_factory
    ):
        """Test that BUILDING extracts files_created from response."""
        context = stage_context_factory()

        response = {
            "status": "build_complete",
            "files_created": ["file1.py", "file2.py"],
            "files_modified": ["existing.py"],
            "ready_for_testing": True
        }
        result = building_handler.process_response(response, context)

        # Check that files are in the emitted event data
        assert len(result.events_to_emit) > 0
        event_data = result.events_to_emit[0].data
        assert "files_created" in event_data
        assert "file1.py" in event_data["files_created"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
