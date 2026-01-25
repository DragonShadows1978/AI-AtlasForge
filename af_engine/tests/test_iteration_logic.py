"""
Iteration Counter Logic Tests for the Modular af_engine.

These tests validate the iteration counter behavior:
1. Iteration stays at 0 for successful pass-through (no revisions)
2. Iteration increments exactly once per revision cycle (needs_revision)
3. Iteration increments exactly once per replanning (needs_replanning)
4. Multiple revision cycles increment correctly

These are CRITICAL regression tests for the iteration counter bugs.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch


class TestIterationZeroOnSuccess:
    """Tests verifying iteration stays at 0 for successful missions."""

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_iteration_zero_throughout_happy_path(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that iteration remains 0 when all stages succeed without revision.

        CRITICAL REGRESSION TEST: This validates that successful missions
        do not erroneously increment the iteration counter.
        """
        mission = mission_factory(iteration=0, cycle_budget=1)
        orch = orchestrator_factory(mission=mission)

        # Track iteration at each stage
        iteration_history = [("INIT", orch.state.iteration)]

        # PLANNING -> BUILDING
        response = claude_response_factory("PLANNING", status="plan_complete")
        next_stage = orch.process_response(response)
        iteration_history.append(("PLANNING_COMPLETE", orch.state.iteration))
        orch.update_stage(next_stage)

        # BUILDING -> TESTING
        response = claude_response_factory("BUILDING", status="build_complete")
        next_stage = orch.process_response(response)
        iteration_history.append(("BUILDING_COMPLETE", orch.state.iteration))
        orch.update_stage(next_stage)

        # TESTING -> ANALYZING
        response = claude_response_factory("TESTING", status="tests_passed")
        next_stage = orch.process_response(response)
        iteration_history.append(("TESTING_COMPLETE", orch.state.iteration))
        orch.update_stage(next_stage)

        # ANALYZING -> CYCLE_END (success path)
        response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )
        next_stage = orch.process_response(response)
        iteration_history.append(("ANALYZING_COMPLETE", orch.state.iteration))
        orch.update_stage(next_stage)

        # CYCLE_END -> COMPLETE
        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )
        next_stage = orch.process_response(response)
        iteration_history.append(("CYCLE_END_COMPLETE", orch.state.iteration))
        orch.update_stage(next_stage)

        # Verify all iterations were 0
        for stage, iteration in iteration_history:
            assert iteration == 0, f"Iteration was {iteration} at {stage}, expected 0"

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_analyzing_success_does_not_set_increment_flag(
        self,
        analyzing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that ANALYZING success response does NOT set _increment_iteration.

        This is the key mechanism - the _increment_iteration flag in output_data
        signals the orchestrator to increment. Success should NOT set this.
        """
        context = stage_context_factory()

        # Test with status="success"
        response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )
        result = analyzing_handler.process_response(response, context)

        assert result.output_data.get("_increment_iteration") is not True

        # Test with recommendation="COMPLETE" (alternative success path)
        response = claude_response_factory(
            "ANALYZING",
            status="",  # Empty status
            recommendation="COMPLETE"
        )
        result = analyzing_handler.process_response(response, context)

        assert result.output_data.get("_increment_iteration") is not True


class TestIterationIncrementOnNeedsRevision:
    """Tests for iteration increment on needs_revision."""

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    @pytest.mark.regression_needs_revision
    def test_needs_revision_increments_iteration(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory,
        needs_revision_response
    ):
        """Test that needs_revision increments iteration by exactly 1.

        CRITICAL REGRESSION TEST: When ANALYZING returns needs_revision,
        the iteration must increment by exactly 1 before returning to BUILDING.
        """
        mission = mission_factory(iteration=0, current_stage="ANALYZING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        # Initial state
        assert orch.state.iteration == 0

        # Process needs_revision response
        next_stage = orch.process_response(needs_revision_response)

        # Iteration should have incremented
        assert orch.state.iteration == 1, (
            f"Iteration should be 1 after needs_revision, got {orch.state.iteration}"
        )
        assert next_stage == "BUILDING"

    @pytest.mark.regression
    @pytest.mark.regression_needs_revision
    def test_needs_revision_sets_increment_flag(
        self,
        analyzing_handler,
        stage_context_factory,
        needs_revision_response
    ):
        """Test that needs_revision response sets _increment_iteration flag."""
        context = stage_context_factory()

        result = analyzing_handler.process_response(needs_revision_response, context)

        assert result.output_data.get("_increment_iteration") is True
        assert result.next_stage == "BUILDING"

    @pytest.mark.regression
    @pytest.mark.regression_needs_revision
    def test_needs_revision_with_recommendation_building(
        self,
        analyzing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that recommendation=BUILDING also triggers iteration increment."""
        context = stage_context_factory()

        # Using recommendation instead of status
        response = claude_response_factory(
            "ANALYZING",
            status="",  # Empty status
            recommendation="BUILDING",
            issues_found=["Bug found"]
        )

        result = analyzing_handler.process_response(response, context)

        assert result.output_data.get("_increment_iteration") is True
        assert result.next_stage == "BUILDING"


class TestIterationIncrementOnNeedsReplanning:
    """Tests for iteration increment on needs_replanning."""

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    @pytest.mark.regression_needs_replanning
    def test_needs_replanning_increments_iteration(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory,
        needs_replanning_response
    ):
        """Test that needs_replanning increments iteration by exactly 1.

        CRITICAL REGRESSION TEST: When ANALYZING returns needs_replanning,
        the iteration must increment by exactly 1 before returning to PLANNING.
        """
        mission = mission_factory(iteration=0, current_stage="ANALYZING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        # Initial state
        assert orch.state.iteration == 0

        # Process needs_replanning response
        next_stage = orch.process_response(needs_replanning_response)

        # Iteration should have incremented
        assert orch.state.iteration == 1, (
            f"Iteration should be 1 after needs_replanning, got {orch.state.iteration}"
        )
        assert next_stage == "PLANNING"

    @pytest.mark.regression
    @pytest.mark.regression_needs_replanning
    def test_needs_replanning_sets_increment_flag(
        self,
        analyzing_handler,
        stage_context_factory,
        needs_replanning_response
    ):
        """Test that needs_replanning response sets _increment_iteration flag."""
        context = stage_context_factory()

        result = analyzing_handler.process_response(needs_replanning_response, context)

        assert result.output_data.get("_increment_iteration") is True
        assert result.next_stage == "PLANNING"

    @pytest.mark.regression
    @pytest.mark.regression_needs_replanning
    def test_needs_replanning_with_recommendation_planning(
        self,
        analyzing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that recommendation=PLANNING also triggers iteration increment."""
        context = stage_context_factory()

        # Using recommendation instead of status
        response = claude_response_factory(
            "ANALYZING",
            status="",  # Empty status
            recommendation="PLANNING",
            issues_found=["Architecture issue"]
        )

        result = analyzing_handler.process_response(response, context)

        assert result.output_data.get("_increment_iteration") is True
        assert result.next_stage == "PLANNING"


class TestMultipleRevisionCycles:
    """Tests for iteration behavior across multiple revision cycles."""

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_iteration_increments_each_revision_cycle(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that iteration increments exactly once per revision cycle.

        Simulates:
        1. First pass: PLANNING -> BUILDING -> TESTING -> ANALYZING (needs_revision) -> iteration=1
        2. Second pass: BUILDING -> TESTING -> ANALYZING (needs_revision) -> iteration=2
        3. Third pass: BUILDING -> TESTING -> ANALYZING (success) -> iteration stays 2
        """
        mission = mission_factory(iteration=0, cycle_budget=1)
        orch = orchestrator_factory(mission=mission)

        # === First pass (iteration should stay 0 until needs_revision) ===
        # PLANNING
        response = claude_response_factory("PLANNING", status="plan_complete")
        orch.process_response(response)
        orch.update_stage("BUILDING")
        assert orch.state.iteration == 0

        # BUILDING
        response = claude_response_factory("BUILDING", status="build_complete")
        orch.process_response(response)
        orch.update_stage("TESTING")
        assert orch.state.iteration == 0

        # TESTING
        response = claude_response_factory("TESTING", status="tests_failed")
        orch.process_response(response)
        orch.update_stage("ANALYZING")
        assert orch.state.iteration == 0

        # ANALYZING - needs_revision
        response = claude_response_factory(
            "ANALYZING",
            status="needs_revision",
            recommendation="BUILDING"
        )
        next_stage = orch.process_response(response)
        assert orch.state.iteration == 1  # Should increment
        assert next_stage == "BUILDING"
        orch.update_stage(next_stage)

        # === Second pass ===
        # BUILDING (iteration 1)
        response = claude_response_factory("BUILDING", status="build_complete")
        orch.process_response(response)
        orch.update_stage("TESTING")
        assert orch.state.iteration == 1

        # TESTING (iteration 1)
        response = claude_response_factory("TESTING", status="tests_failed")
        orch.process_response(response)
        orch.update_stage("ANALYZING")
        assert orch.state.iteration == 1

        # ANALYZING - needs_revision again
        response = claude_response_factory(
            "ANALYZING",
            status="needs_revision",
            recommendation="BUILDING"
        )
        next_stage = orch.process_response(response)
        assert orch.state.iteration == 2  # Should increment again
        assert next_stage == "BUILDING"
        orch.update_stage(next_stage)

        # === Third pass (success) ===
        # BUILDING (iteration 2)
        response = claude_response_factory("BUILDING", status="build_complete")
        orch.process_response(response)
        orch.update_stage("TESTING")
        assert orch.state.iteration == 2

        # TESTING (iteration 2)
        response = claude_response_factory("TESTING", status="tests_passed")
        orch.process_response(response)
        orch.update_stage("ANALYZING")
        assert orch.state.iteration == 2

        # ANALYZING - success
        response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )
        orch.process_response(response)
        assert orch.state.iteration == 2  # Should NOT increment on success

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_mixed_revision_and_replanning_cycles(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test iteration with both needs_revision and needs_replanning.

        Simulates:
        1. First pass: needs_revision -> iteration=1
        2. Second pass: needs_replanning -> iteration=2
        3. Third pass: success -> iteration stays 2
        """
        mission = mission_factory(iteration=0, cycle_budget=1)
        orch = orchestrator_factory(mission=mission)

        # Quick path to ANALYZING
        for stage, next_s in [("PLANNING", "BUILDING"), ("BUILDING", "TESTING"), ("TESTING", "ANALYZING")]:
            response = claude_response_factory(stage)
            orch.process_response(response)
            orch.update_stage(next_s)

        # needs_revision -> BUILDING
        response = claude_response_factory(
            "ANALYZING",
            status="needs_revision",
            recommendation="BUILDING"
        )
        orch.process_response(response)
        orch.update_stage("BUILDING")
        assert orch.state.iteration == 1

        # Back to ANALYZING
        for stage, next_s in [("BUILDING", "TESTING"), ("TESTING", "ANALYZING")]:
            response = claude_response_factory(stage)
            orch.process_response(response)
            orch.update_stage(next_s)

        # needs_replanning -> PLANNING
        response = claude_response_factory(
            "ANALYZING",
            status="needs_replanning",
            recommendation="PLANNING"
        )
        orch.process_response(response)
        orch.update_stage("PLANNING")
        assert orch.state.iteration == 2

        # Complete the cycle
        for stage, next_s in [("PLANNING", "BUILDING"), ("BUILDING", "TESTING"), ("TESTING", "ANALYZING")]:
            response = claude_response_factory(stage)
            orch.process_response(response)
            orch.update_stage(next_s)

        # success -> CYCLE_END
        response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )
        orch.process_response(response)
        assert orch.state.iteration == 2  # Should NOT increment


class TestIterationPersistence:
    """Tests for iteration counter persistence to disk."""

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_iteration_persisted_after_increment(
        self,
        orchestrator_factory,
        mission_factory,
        needs_revision_response,
        tmp_path
    ):
        """Test that iteration is saved to disk after increment."""
        mission = mission_factory(iteration=0, current_stage="ANALYZING")
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("ANALYZING")

        mission_path = tmp_path / "state" / "mission.json"

        # Process needs_revision
        orch.process_response(needs_revision_response)

        # Read from disk
        with open(mission_path) as f:
            saved_state = json.load(f)

        assert saved_state["iteration"] == 1

    @pytest.mark.regression
    @pytest.mark.regression_iteration_counter
    def test_iteration_loaded_correctly(
        self,
        tmp_path,
        mission_factory
    ):
        """Test that iteration is loaded correctly from disk."""
        from af_engine.orchestrator import StageOrchestrator

        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)

        # Create mission with iteration=5
        mission = mission_factory(iteration=5)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        # Create orchestrator (should load mission)
        orch = StageOrchestrator(
            mission_path=mission_path,
            atlasforge_root=tmp_path
        )

        assert orch.state.iteration == 5


class TestOrchestratorIncrementIteration:
    """Tests for the orchestrator's increment_iteration method."""

    @pytest.mark.unit
    def test_increment_iteration_returns_new_value(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that increment_iteration returns the new iteration value."""
        mission = mission_factory(iteration=3)
        orch = orchestrator_factory(mission=mission)

        new_iteration = orch.increment_iteration()

        assert new_iteration == 4
        assert orch.state.iteration == 4

    @pytest.mark.unit
    def test_state_manager_increment_iteration(
        self,
        real_state_manager
    ):
        """Test StateManager.increment_iteration directly."""
        initial = real_state_manager.iteration
        new_value = real_state_manager.increment_iteration()

        assert new_value == initial + 1
        assert real_state_manager.iteration == initial + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
