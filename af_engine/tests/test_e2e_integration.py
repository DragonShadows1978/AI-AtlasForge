"""
End-to-End Integration Tests for the Modular af_engine.

These tests validate real mission execution through all stages:
- PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE

Tests verify:
1. Full lifecycle completion without revisions
2. Stage transitions occur in correct order
3. Iteration counter stays at 0 for successful pass-through
4. Stage restrictions are enforced
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestFullMissionLifecycle:
    """Tests for complete mission lifecycle without revisions."""

    @pytest.mark.integration
    @pytest.mark.regression
    def test_happy_path_single_cycle(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test complete mission with cycle_budget=1 succeeds with iteration=0.

        Validates:
        - Stage transitions: PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE
        - Iteration stays at 0 throughout
        - Mission completes successfully
        """
        # Create mission with single cycle
        mission = mission_factory(
            mission_id="test_happy_path",
            cycle_budget=1,
            iteration=0,
        )

        # Create orchestrator
        orch = orchestrator_factory(mission=mission)

        # Verify initial state
        assert orch.current_stage == "PLANNING"
        assert orch.state.iteration == 0

        # Process PLANNING stage
        planning_response = claude_response_factory("PLANNING", status="plan_complete")
        next_stage = orch.process_response(planning_response)

        assert next_stage == "BUILDING"
        assert orch.state.iteration == 0  # Should not increment on success
        orch.update_stage(next_stage)

        # Process BUILDING stage
        building_response = claude_response_factory("BUILDING", status="build_complete")
        next_stage = orch.process_response(building_response)

        assert next_stage == "TESTING"
        assert orch.state.iteration == 0
        orch.update_stage(next_stage)

        # Process TESTING stage
        testing_response = claude_response_factory("TESTING", status="tests_passed")
        next_stage = orch.process_response(testing_response)

        assert next_stage == "ANALYZING"
        assert orch.state.iteration == 0
        orch.update_stage(next_stage)

        # Process ANALYZING stage with success
        analyzing_response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )
        next_stage = orch.process_response(analyzing_response)

        assert next_stage == "CYCLE_END"
        assert orch.state.iteration == 0  # Should NOT increment on success
        orch.update_stage(next_stage)

        # Process CYCLE_END stage (final cycle)
        cycle_end_response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )
        next_stage = orch.process_response(cycle_end_response)

        assert next_stage == "COMPLETE"
        assert orch.state.iteration == 0  # Final check - should still be 0
        orch.update_stage(next_stage)

        # Verify final state
        assert orch.current_stage == "COMPLETE"
        assert orch.state.iteration == 0

    @pytest.mark.integration
    def test_stage_order_is_correct(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that stages are visited in the correct order."""
        mission = mission_factory(cycle_budget=1)
        orch = orchestrator_factory(mission=mission)

        stages_visited = []

        # Define responses for each stage
        responses = {
            "PLANNING": claude_response_factory("PLANNING"),
            "BUILDING": claude_response_factory("BUILDING"),
            "TESTING": claude_response_factory("TESTING"),
            "ANALYZING": claude_response_factory("ANALYZING", status="success"),
            "CYCLE_END": claude_response_factory(
                "CYCLE_END",
                status="mission_complete",
                current_cycle=1,
                cycle_budget=1
            ),
        }

        # Run through all stages
        while orch.current_stage != "COMPLETE":
            stages_visited.append(orch.current_stage)
            response = responses.get(orch.current_stage, {})
            next_stage = orch.process_response(response)
            if next_stage != orch.current_stage:
                orch.update_stage(next_stage)

        stages_visited.append("COMPLETE")

        expected_order = [
            "PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"
        ]
        assert stages_visited == expected_order


class TestStageHandlerProcessing:
    """Tests for individual stage handler response processing."""

    @pytest.mark.unit
    def test_planning_handler_transitions_to_building(
        self,
        planning_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test PlanningStageHandler transitions to BUILDING on plan_complete."""
        from pathlib import Path
        context = stage_context_factory()
        response = claude_response_factory("PLANNING", status="plan_complete")

        # Two-phase gate requires both artifacts to exist on disk
        Path(context.research_dir).mkdir(parents=True, exist_ok=True)
        Path(context.artifacts_dir).mkdir(parents=True, exist_ok=True)
        (Path(context.research_dir) / "research_findings.md").write_text("# Research\n\n" + "Findings. " * 60)
        (Path(context.artifacts_dir) / "implementation_plan.md").write_text("# Plan\n\n" + "Steps. " * 40)

        result = planning_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "BUILDING"
        assert result.status == "plan_complete"

    @pytest.mark.unit
    def test_planning_handler_materializes_missing_artifacts_from_response(
        self,
        planning_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """A valid plan response should not loop forever if the agent artifact write path failed."""
        from pathlib import Path

        context = stage_context_factory()
        response = claude_response_factory(
            "PLANNING",
            status="plan_complete",
            understanding="Build the requested feature in scoped increments.",
            research_conducted=["codebase scan: existing stage handlers own transitions"],
            sources_consulted=["local codebase"],
            research_summary={
                "topics_researched": ["stage artifact persistence"],
                "key_findings": ["The conductor can persist structured planning responses."],
                "knowledge_gaps_identified": [],
                "confidence_level": "medium",
            },
            key_requirements=["Persist implementation plan", "Persist research notes"],
            approach="Materialize artifacts from the structured planning response.",
            steps=[
                {"step": 1, "description": "Write fallback research artifact", "files": ["research/research_findings.md"]},
                {"step": 2, "description": "Write fallback plan artifact", "files": ["artifacts/implementation_plan.md"]},
            ],
            success_criteria=["Planning advances after artifacts are materialized"],
        )

        result = planning_handler.process_response(response, context)

        findings_path = Path(context.research_dir) / "research_findings.md"
        plan_path = Path(context.artifacts_dir) / "implementation_plan.md"
        assert result.success is True
        assert result.next_stage == "BUILDING"
        assert findings_path.exists()
        assert findings_path.stat().st_size >= 500
        assert plan_path.exists()
        assert plan_path.stat().st_size >= 200

    @pytest.mark.unit
    def test_plan_only_planning_transitions_to_analyzing(
        self,
        planning_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Plan-only missions must reach ANALYZING to create the gated Build Only follow-up."""
        from pathlib import Path
        mission = {
            "mission_id": "plan_only_test",
            "problem_statement": "Create a plan for a build-only follow-up",
            "original_problem_statement": "Create a plan for a build-only follow-up",
            "mission_type": "plan_only",
            "current_stage": "PLANNING",
            "enabled_stages": ["PLANNING", "ANALYZING"],
            "max_iterations": 10,
            "history": [],
            "cycle_history": [],
            "preferences": {},
            "success_criteria": [],
        }
        context = stage_context_factory(mission=mission)
        response = claude_response_factory("PLANNING", status="plan_complete")

        Path(context.research_dir).mkdir(parents=True, exist_ok=True)
        Path(context.artifacts_dir).mkdir(parents=True, exist_ok=True)
        (Path(context.research_dir) / "research_findings.md").write_text("# Research\n\n" + "Findings. " * 60)
        (Path(context.artifacts_dir) / "implementation_plan.md").write_text("# Plan\n\n" + "Steps. " * 40)

        result = planning_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "ANALYZING"
        assert result.status == "plan_complete"

    @pytest.mark.unit
    def test_building_handler_transitions_to_testing(
        self,
        building_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test BuildingStageHandler transitions to TESTING on build_complete."""
        context = stage_context_factory()
        response = claude_response_factory("BUILDING", status="build_complete")

        result = building_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "TESTING"
        assert result.status == "build_complete"

    @pytest.mark.unit
    def test_testing_handler_transitions_to_analyzing(
        self,
        testing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test TestingStageHandler transitions to ANALYZING regardless of pass/fail."""
        context = stage_context_factory()

        # Test with tests_passed
        response_passed = claude_response_factory("TESTING", status="tests_passed")
        result = testing_handler.process_response(response_passed, context)
        assert result.next_stage == "ANALYZING"

        # Test with tests_failed - should STILL go to ANALYZING
        response_failed = claude_response_factory("TESTING", status="tests_failed")
        result = testing_handler.process_response(response_failed, context)
        assert result.next_stage == "ANALYZING"

    @pytest.mark.unit
    def test_testing_handler_requires_three_red_team_agents_to_pass(
        self,
        testing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """TESTING cannot pass on BUILDING self-validation alone."""
        context = stage_context_factory()
        context.mission["llm_provider"] = "codex"
        response = claude_response_factory(
            "TESTING",
            status="tests_passed",
            adversarial_testing={
                "red_team_issues": [],
                "red_team_agent_count": 0,
                "red_team_duration_seconds": 0,
                "red_team_completion": {
                    "agent_reports_collected": 0,
                    "all_agents_completed": False,
                    "agents_reached_report_phase": 0,
                    "timed_out_agents": [],
                },
                "property_violations": [],
                "mutation_score": None,
                "spec_alignment": None,
                "epistemic_score": 0.0,
                "rigor_level": "insufficient",
            },
        )

        result = testing_handler.process_response(response, context)

        assert result.next_stage == "ANALYZING"
        assert result.status == "tests_error"
        assert result.output_data["status"] == "tests_error"
        assert "red-team gate incomplete" in result.message
        assert result.events_to_emit[0].data["official_red_team_complete"] is False

    @pytest.mark.unit
    def test_testing_handler_allows_early_close_when_agents_finish(
        self,
        testing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """TESTING can pass before timeout when all required reports are complete."""
        context = stage_context_factory()
        context.mission["llm_provider"] = "codex"
        response = claude_response_factory(
            "TESTING",
            status="tests_passed",
            adversarial_testing={
                "red_team_issues": [],
                "red_team_agent_count": 3,
                "red_team_duration_seconds": 600,
                "red_team_completion": {
                    "agent_reports_collected": 3,
                    "all_agents_completed": True,
                    "agents_reached_report_phase": 3,
                    "timed_out_agents": [],
                },
                "property_violations": [],
                "mutation_score": None,
                "spec_alignment": None,
                "epistemic_score": 0.8,
                "rigor_level": "strong",
            },
        )

        result = testing_handler.process_response(response, context)

        assert result.status == "tests_passed"
        assert result.events_to_emit[0].data["official_red_team_complete"] is True
        assert result.events_to_emit[0].data["red_team_reports_collected"] == 3

    @pytest.mark.unit
    def test_testing_handler_rejects_agent_timeout_even_with_count(
        self,
        testing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Agent count alone is insufficient when completion metadata says timeout."""
        context = stage_context_factory()
        context.mission["llm_provider"] = "codex"
        response = claude_response_factory(
            "TESTING",
            status="tests_passed",
            adversarial_testing={
                "red_team_issues": [],
                "red_team_agent_count": 3,
                "red_team_duration_seconds": 2700,
                "red_team_completion": {
                    "agent_reports_collected": 2,
                    "all_agents_completed": False,
                    "agents_reached_report_phase": 2,
                    "timed_out_agents": [2],
                },
                "property_violations": [],
                "mutation_score": None,
                "spec_alignment": None,
                "epistemic_score": 0.4,
                "rigor_level": "weak",
            },
        )

        result = testing_handler.process_response(response, context)

        assert result.status == "tests_error"
        assert result.events_to_emit[0].data["official_red_team_complete"] is False
        assert result.events_to_emit[0].data["red_team_timed_out_agents"] == [2]

    @pytest.mark.unit
    def test_analyzing_handler_transitions_to_cycle_end_on_success(
        self,
        analyzing_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test AnalyzingStageHandler transitions to CYCLE_END on success."""
        context = stage_context_factory()
        response = claude_response_factory(
            "ANALYZING",
            status="success",
            recommendation="COMPLETE"
        )

        result = analyzing_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "CYCLE_END"
        # Should NOT set _increment_iteration on success
        assert result.output_data.get("_increment_iteration") is not True

    @pytest.mark.unit
    def test_cycle_end_handler_final_cycle(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test CycleEndStageHandler transitions to COMPLETE on final cycle."""
        # Create context for final cycle (cycle 1 of 1)
        context = stage_context_factory(cycle_number=1, cycle_budget=1)
        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )

        result = cycle_end_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "COMPLETE"
        assert result.status == "mission_complete"

    @pytest.mark.unit
    def test_cycle_end_handler_more_cycles(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test CycleEndStageHandler transitions to PLANNING when more cycles remain."""
        # Create context with cycles remaining (cycle 1 of 3)
        context = stage_context_factory(cycle_number=1, cycle_budget=3)
        response = claude_response_factory(
            "CYCLE_END",
            status="cycle_complete",
            current_cycle=1,
            cycle_budget=3,
            continuation_prompt="Continue to cycle 2"
        )

        result = cycle_end_handler.process_response(response, context)

        assert result.success is True
        assert result.next_stage == "PLANNING"
        assert result.status == "cycle_complete"


class TestOrchestratorEventEmission:
    """Tests for orchestrator event emission during transitions."""

    @pytest.mark.integration
    def test_stage_started_event_emitted(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that STAGE_STARTED event is emitted on stage transition."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Track emitted events
        emitted_events = []
        original_emit = orch.integrations.emit_stage_started

        def tracking_emit(*args, **kwargs):
            emitted_events.append(("stage_started", args, kwargs))
            return original_emit(*args, **kwargs)

        orch.integrations.emit_stage_started = tracking_emit

        # Transition to BUILDING
        orch.update_stage("BUILDING")

        # Verify event was emitted
        assert len(emitted_events) > 0
        assert emitted_events[0][0] == "stage_started"
        assert emitted_events[0][2].get("stage") == "BUILDING"

    @pytest.mark.integration
    def test_stage_completed_event_emitted(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that STAGE_COMPLETED event is emitted on stage transition."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        emitted_events = []
        original_emit = orch.integrations.emit_stage_completed

        def tracking_emit(*args, **kwargs):
            emitted_events.append(("stage_completed", args, kwargs))
            return original_emit(*args, **kwargs)

        orch.integrations.emit_stage_completed = tracking_emit

        # Transition from PLANNING to BUILDING
        orch.update_stage("BUILDING")

        assert len(emitted_events) > 0
        assert emitted_events[0][2].get("stage") == "PLANNING"

    @pytest.mark.integration
    def test_mission_completed_event_on_complete(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that MISSION_COMPLETED event is emitted on COMPLETE stage."""
        mission = mission_factory(current_stage="CYCLE_END")
        orch = orchestrator_factory(mission=mission)

        emitted_events = []
        original_emit = orch.integrations.emit_mission_completed

        def tracking_emit(*args, **kwargs):
            emitted_events.append(("mission_completed", args, kwargs))
            return original_emit(*args, **kwargs)

        orch.integrations.emit_mission_completed = tracking_emit

        # Transition to COMPLETE
        orch.update_stage("COMPLETE")

        assert len(emitted_events) > 0


class TestOrchestratorStatePersistence:
    """Tests for mission state persistence."""

    @pytest.mark.integration
    def test_state_persisted_on_stage_transition(
        self,
        orchestrator_factory,
        mission_factory,
        tmp_path
    ):
        """Test that state is saved to disk after stage transition."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Get mission path
        mission_path = tmp_path / "state" / "mission.json"

        # Transition to BUILDING
        orch.update_stage("BUILDING")

        # Read state from disk
        with open(mission_path) as f:
            saved_state = json.load(f)

        assert saved_state["current_stage"] == "BUILDING"

    @pytest.mark.integration
    def test_iteration_persisted_on_change(
        self,
        orchestrator_factory,
        mission_factory,
        tmp_path
    ):
        """Test that iteration counter is persisted when incremented."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)
        mission_path = tmp_path / "state" / "mission.json"

        # Initial iteration should be 0
        assert orch.state.iteration == 0

        # Increment iteration
        orch.state.increment_iteration()

        # Read from disk
        with open(mission_path) as f:
            saved_state = json.load(f)

        assert saved_state["iteration"] == 1


class TestOrchestratorStatus:
    """Tests for orchestrator status reporting."""

    @pytest.mark.unit
    def test_get_status_returns_correct_fields(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that get_status returns all expected fields."""
        mission = mission_factory(
            cycle_budget=3,
            current_cycle=2,
            iteration=1
        )
        orch = orchestrator_factory(mission=mission)

        status = orch.get_status()

        assert "mission_id" in status
        assert "current_stage" in status
        assert "iteration" in status
        assert "cycle" in status
        assert "cycle_budget" in status
        assert "cycles_remaining" in status
        assert "integrations" in status

        assert status["cycle_budget"] == 3
        assert status["cycle"] == 2

    @pytest.mark.unit
    def test_stage_restrictions_returned_correctly(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that get_stage_restrictions returns correct restrictions."""
        mission = mission_factory()
        orch = orchestrator_factory(mission=mission)

        # Get restrictions for PLANNING
        restrictions = orch.get_stage_restrictions("PLANNING")

        assert "allowed_tools" in restrictions
        assert "blocked_tools" in restrictions
        assert "allowed_write_paths" in restrictions
        assert "forbidden_write_paths" in restrictions
        assert "allow_bash" in restrictions
        assert "read_only" in restrictions

        # PLANNING should block NotebookEdit
        assert "NotebookEdit" in restrictions["blocked_tools"]

        # Get restrictions for COMPLETE
        complete_restrictions = orch.get_stage_restrictions("COMPLETE")

        # COMPLETE should be read_only
        assert complete_restrictions["read_only"] is True
        assert "Edit" in complete_restrictions["blocked_tools"]
        assert "Write" in complete_restrictions["blocked_tools"]


class TestOrchestratorMissionSetup:
    """Tests for mission setup and initialization."""

    @pytest.mark.integration
    def test_set_mission_initializes_correctly(self, tmp_path):
        """Test that set_mission initializes mission state correctly."""
        from af_engine.orchestrator import StageOrchestrator

        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)

        # Create empty mission file
        with open(mission_path, 'w') as f:
            json.dump({}, f)

        # Create required directories for set_mission
        (tmp_path / "missions").mkdir(exist_ok=True)
        (tmp_path / "workspace").mkdir(exist_ok=True)

        orch = StageOrchestrator(
            mission_path=mission_path,
            atlasforge_root=tmp_path
        )

        # Set a new mission - orchestrator uses atlasforge_root for paths
        orch.set_mission(
            problem_statement="Test problem",
            cycle_budget=5,
            mission_id="test_setup"
        )

        # Verify mission is initialized correctly
        assert orch.mission["problem_statement"] == "Test problem"
        assert orch.mission["cycle_budget"] == 5
        assert orch.mission["current_stage"] == "PLANNING"
        assert orch.mission["iteration"] == 0
        assert orch.mission["current_cycle"] == 1

    @pytest.mark.integration
    def test_reset_mission_preserves_problem_statement(
        self,
        orchestrator_factory,
        mission_factory
    ):
        """Test that reset_mission keeps the problem statement."""
        mission = mission_factory(
            problem_statement="Original problem",
            current_stage="BUILDING",
            iteration=5
        )
        orch = orchestrator_factory(mission=mission)

        orch.reset_mission()

        # Problem statement should be preserved
        assert orch.mission["problem_statement"] == "Original problem"
        # But stage and iteration should be reset
        assert orch.mission["current_stage"] == "PLANNING"
        assert orch.mission["iteration"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
