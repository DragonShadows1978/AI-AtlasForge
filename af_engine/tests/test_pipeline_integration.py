"""
Tests for full pipeline integration.

Tests the complete pipeline from orchestrator through prompt factory,
mocking invoke_llm to verify end-to-end flow without actual API calls.
"""

import pytest
import json
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

# Add project root to path for imports
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


# ===========================================================================
# Test Full Pipeline Happy Path
# ===========================================================================

class TestFullPipelineHappyPath:
    """Test complete mission lifecycle through the pipeline."""

    @pytest.mark.regression
    @pytest.mark.integration
    def test_complete_mission_single_cycle(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory
    ):
        """Test complete mission flow: PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE."""
        mission = mission_factory(
            cycle_budget=1,
            current_cycle=1,
            current_stage="PLANNING"
        )
        orch = orchestrator_factory(mission=mission)

        # Track stage transitions
        stages_visited = [orch.current_stage]

        # Process PLANNING
        planning_response = claude_response_factory("PLANNING", status="plan_complete")
        next_stage = orch.process_response(planning_response)
        orch.update_stage(next_stage)
        stages_visited.append(orch.current_stage)
        assert orch.current_stage == "BUILDING"

        # Process BUILDING
        building_response = claude_response_factory("BUILDING", status="build_complete")
        next_stage = orch.process_response(building_response)
        orch.update_stage(next_stage)
        stages_visited.append(orch.current_stage)
        assert orch.current_stage == "TESTING"

        # Process TESTING
        testing_response = claude_response_factory("TESTING", status="tests_passed")
        next_stage = orch.process_response(testing_response)
        orch.update_stage(next_stage)
        stages_visited.append(orch.current_stage)
        assert orch.current_stage == "ANALYZING"

        # Process ANALYZING (success path)
        analyzing_response = claude_response_factory("ANALYZING", status="success")
        next_stage = orch.process_response(analyzing_response)
        orch.update_stage(next_stage)
        stages_visited.append(orch.current_stage)
        assert orch.current_stage == "CYCLE_END"

        # Process CYCLE_END (final cycle)
        cycle_end_response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )
        next_stage = orch.process_response(cycle_end_response)
        orch.update_stage(next_stage)
        stages_visited.append(orch.current_stage)
        assert orch.current_stage == "COMPLETE"

        # Verify full stage order
        assert stages_visited == [
            "PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"
        ]

        # Verify iteration stayed at 0
        assert orch.state.iteration == 0

    @pytest.mark.regression
    @pytest.mark.integration
    def test_multi_cycle_mission(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory
    ):
        """Test multi-cycle mission with 2 cycles."""
        mission = mission_factory(
            cycle_budget=2,
            current_cycle=1,
            current_stage="PLANNING"
        )
        orch = orchestrator_factory(mission=mission)

        # === CYCLE 1 ===
        # PLANNING
        orch.process_response(claude_response_factory("PLANNING"))
        orch.update_stage("BUILDING")

        # BUILDING
        orch.process_response(claude_response_factory("BUILDING"))
        orch.update_stage("TESTING")

        # TESTING
        orch.process_response(claude_response_factory("TESTING"))
        orch.update_stage("ANALYZING")

        # ANALYZING
        orch.process_response(claude_response_factory("ANALYZING"))
        orch.update_stage("CYCLE_END")

        # CYCLE_END (not final cycle)
        cycle_end_response = claude_response_factory(
            "CYCLE_END",
            status="cycle_complete",
            current_cycle=1,
            cycle_budget=2,
            continuation_prompt="Continue with cycle 2"
        )
        next_stage = orch.process_response(cycle_end_response)

        # Should return to PLANNING for cycle 2
        assert next_stage == "PLANNING"
        orch.update_stage("PLANNING")

        # === CYCLE 2 ===
        # Advance cycle counter
        orch.state.mission["current_cycle"] = 2

        # PLANNING
        orch.process_response(claude_response_factory("PLANNING"))
        orch.update_stage("BUILDING")

        # BUILDING
        orch.process_response(claude_response_factory("BUILDING"))
        orch.update_stage("TESTING")

        # TESTING
        orch.process_response(claude_response_factory("TESTING"))
        orch.update_stage("ANALYZING")

        # ANALYZING
        orch.process_response(claude_response_factory("ANALYZING"))
        orch.update_stage("CYCLE_END")

        # CYCLE_END (final cycle)
        cycle_end_response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=2,
            cycle_budget=2
        )
        next_stage = orch.process_response(cycle_end_response)

        # Should go to COMPLETE
        assert next_stage == "COMPLETE"


# ===========================================================================
# Test Revision Flow
# ===========================================================================

class TestRevisionFlow:
    """Test needs_revision and needs_replanning flows."""

    @pytest.mark.regression
    @pytest.mark.regression_needs_revision
    @pytest.mark.integration
    def test_needs_revision_returns_to_building(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory,
        needs_revision_response
    ):
        """Test needs_revision returns to BUILDING and increments iteration."""
        mission = mission_factory(current_stage="ANALYZING", iteration=0)
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        initial_iteration = orch.state.iteration
        assert initial_iteration == 0

        # Process needs_revision
        next_stage = orch.process_response(needs_revision_response)

        # Should return to BUILDING
        assert next_stage == "BUILDING"

        # Iteration should increment
        assert orch.state.iteration == initial_iteration + 1

    @pytest.mark.regression
    @pytest.mark.regression_needs_replanning
    @pytest.mark.integration
    def test_needs_replanning_returns_to_planning(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory,
        needs_replanning_response
    ):
        """Test needs_replanning returns to PLANNING and increments iteration."""
        mission = mission_factory(current_stage="ANALYZING", iteration=0)
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        initial_iteration = orch.state.iteration
        assert initial_iteration == 0

        # Process needs_replanning
        next_stage = orch.process_response(needs_replanning_response)

        # Should return to PLANNING
        assert next_stage == "PLANNING"

        # Iteration should increment
        assert orch.state.iteration == initial_iteration + 1

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    @pytest.mark.integration
    def test_revision_then_success(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory,
        needs_revision_response
    ):
        """Test revision cycle followed by success."""
        mission = mission_factory(current_stage="ANALYZING", iteration=0)
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        # First pass: needs_revision
        next_stage = orch.process_response(needs_revision_response)
        assert next_stage == "BUILDING"
        assert orch.state.iteration == 1

        orch.update_stage("BUILDING")

        # Complete BUILDING again
        orch.process_response(claude_response_factory("BUILDING"))
        orch.update_stage("TESTING")

        # Complete TESTING again
        orch.process_response(claude_response_factory("TESTING"))
        orch.update_stage("ANALYZING")

        # Second pass: success
        success_response = claude_response_factory("ANALYZING", status="success")
        next_stage = orch.process_response(success_response)

        assert next_stage == "CYCLE_END"
        assert orch.state.iteration == 1  # Still 1, didn't increment again


# ===========================================================================
# Test Prompt Building
# ===========================================================================

class TestPromptBuilding:
    """Test prompt factory and prompt assembly."""

    @pytest.mark.integration
    def test_build_rd_prompt_contains_mission(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test build_rd_prompt includes mission information."""
        mission = mission_factory(
            problem_statement="Build a test feature",
            current_stage="PLANNING"
        )
        orch = orchestrator_factory(mission=mission)

        prompt = orch.build_rd_prompt()

        # Should contain mission info
        assert "Build a test feature" in prompt or "test feature" in prompt.lower()

    @pytest.mark.integration
    def test_build_rd_prompt_contains_stage(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test build_rd_prompt includes current stage."""
        mission = mission_factory(current_stage="BUILDING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        prompt = orch.build_rd_prompt()

        # Should indicate BUILDING stage
        assert "BUILDING" in prompt

    @pytest.mark.integration
    def test_build_rd_prompt_contains_iteration(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test build_rd_prompt includes iteration count."""
        mission = mission_factory(current_stage="BUILDING", iteration=2)
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        prompt = orch.build_rd_prompt()

        # Should contain iteration info
        assert "ITERATION" in prompt.upper() or "2" in prompt


# ===========================================================================
# Test Response Parsing Edge Cases
# ===========================================================================

class TestResponseParsingEdgeCases:
    """Test handling of various response formats."""

    @pytest.mark.integration
    def test_response_with_extra_fields(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling response with unexpected extra fields."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        response = {
            "status": "plan_complete",
            "understanding": "Test",
            "unexpected_field": "should be ignored",
            "another_extra": {"nested": "data"}
        }

        # Should not raise
        next_stage = orch.process_response(response)
        assert next_stage == "BUILDING"

    @pytest.mark.integration
    def test_response_with_minimal_fields(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling response with only required fields."""
        mission = mission_factory(current_stage="BUILDING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        # Minimal response with required fields for build_complete
        response = {"status": "build_complete", "ready_for_testing": True}

        # Should work with minimal response
        next_stage = orch.process_response(response)
        assert next_stage == "TESTING"

    @pytest.mark.integration
    def test_response_with_null_values(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling response with null values."""
        mission = mission_factory(current_stage="TESTING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("TESTING")

        response = {
            "status": "tests_passed",
            "summary": None,
            "issues_to_fix": None
        }

        # Should handle nulls gracefully
        next_stage = orch.process_response(response)
        assert next_stage == "ANALYZING"


# ===========================================================================
# Test State Persistence
# ===========================================================================

class TestStatePersistence:
    """Test that state is properly persisted across operations."""

    @pytest.mark.integration
    def test_stage_persisted_after_transition(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory,
        tmp_path
    ):
        """Test stage is saved to disk after transition."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        # Transition to BUILDING
        orch.process_response(claude_response_factory("PLANNING"))
        orch.update_stage("BUILDING")

        # Reload state and verify (load_mission refreshes from disk)
        orch.state.load_mission()
        assert orch.current_stage == "BUILDING"

    @pytest.mark.integration
    def test_iteration_persisted_after_increment(
        self,
        orchestrator_factory,
        mission_factory,
        needs_revision_response
    ):
        """Test iteration is saved to disk after increment."""
        mission = mission_factory(current_stage="ANALYZING", iteration=0)
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        # Trigger iteration increment
        orch.process_response(needs_revision_response)

        # Reload and verify (load_mission refreshes from disk)
        orch.state.load_mission()
        assert orch.state.iteration == 1


# ===========================================================================
# Test Event Emission
# ===========================================================================

class TestEventEmission:
    """Test that events are properly emitted during pipeline execution."""

    @pytest.mark.integration
    def test_stage_started_event(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory
    ):
        """Test stage_started event is emitted on update_stage."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        # Track emitted events
        emitted_events = []

        def track_event(event):
            emitted_events.append(event)

        # Subscribe to events (use integrations, not integration_manager)
        orch.integrations.subscribe("stage_started", track_event)

        # Transition
        orch.process_response(claude_response_factory("PLANNING"))
        orch.update_stage("BUILDING")

        # Verify event was emitted
        stage_started_events = [e for e in emitted_events if e.type.value == "stage_started"]
        assert len(stage_started_events) >= 1

    @pytest.mark.integration
    def test_stage_completed_event(
        self,
        orchestrator_factory,
        mission_factory,
        claude_response_factory
    ):
        """Test stage_completed event is emitted on process_response."""
        mission = mission_factory(current_stage="BUILDING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("BUILDING")

        # Track emitted events
        emitted_events = []

        def track_event(event):
            emitted_events.append(event)

        # Subscribe to events (use integrations, not integration_manager)
        orch.integrations.subscribe("stage_completed", track_event)

        # Complete stage
        orch.process_response(claude_response_factory("BUILDING"))

        # Verify event was emitted
        stage_completed_events = [e for e in emitted_events if e.type.value == "stage_completed"]
        assert len(stage_completed_events) >= 1


# ===========================================================================
# Test Cycle Management
# ===========================================================================

class TestCycleManagement:
    """Test cycle manager integration with pipeline."""

    @pytest.mark.integration
    def test_should_continue_true_for_remaining_cycles(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test should_continue returns True when cycles remain."""
        mission = mission_factory(
            cycle_budget=3,
            current_cycle=1,
            current_stage="CYCLE_END"
        )
        orch = orchestrator_factory(mission=mission)

        assert orch.should_continue_cycle() is True

    @pytest.mark.integration
    def test_should_continue_false_at_budget(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test should_continue returns False at cycle budget."""
        mission = mission_factory(
            cycle_budget=3,
            current_cycle=3,
            current_stage="CYCLE_END"
        )
        orch = orchestrator_factory(mission=mission)

        assert orch.should_continue_cycle() is False

    @pytest.mark.integration
    def test_get_cycle_status(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test get_cycle_status returns correct info."""
        mission = mission_factory(
            cycle_budget=5,
            current_cycle=2,
            current_stage="BUILDING"
        )
        orch = orchestrator_factory(mission=mission)

        status = orch.get_cycle_status()

        assert status["current_cycle"] == 2
        assert status["cycle_budget"] == 5
        assert status["cycles_remaining"] == 3


# ===========================================================================
# Test Error Recovery
# ===========================================================================

class TestErrorRecovery:
    """Test error recovery in the pipeline."""

    @pytest.mark.integration
    def test_recover_from_invalid_stage(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling of invalid stage value."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        # Try to get handler for invalid stage - should raise KeyError
        with pytest.raises(KeyError):
            orch.registry.get_handler("INVALID_STAGE")

    @pytest.mark.integration
    def test_process_response_with_empty_dict(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling empty response dict."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        # Process empty response
        next_stage = orch.process_response({})

        # Should stay in current stage or handle gracefully
        assert next_stage is not None

    @pytest.mark.integration
    def test_process_response_with_none(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test handling None response."""
        mission = mission_factory(current_stage="PLANNING")
        orch = orchestrator_factory(mission=mission)

        # Process None response
        next_stage = orch.process_response(None)

        # Should handle gracefully
        assert next_stage is not None
