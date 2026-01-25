"""
Multi-Cycle Budget Tests for the Modular af_engine.

These tests validate cycle budget handling:
1. cycle_budget is respected
2. Continuation prompts are generated correctly
3. Mission completes when budget exhausted
4. Cycle history is maintained

These are CRITICAL regression tests for cycle budget handling.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch


class TestSingleCycleMission:
    """Tests for single-cycle missions (cycle_budget=1)."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_single_cycle_completes_after_one_pass(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that cycle_budget=1 mission completes after one successful pass."""
        mission = mission_factory(
            cycle_budget=1,
            current_cycle=1
        )
        orch = orchestrator_factory(mission=mission)

        # Run through all stages to CYCLE_END
        stages = ["PLANNING", "BUILDING", "TESTING", "ANALYZING"]
        for stage in stages:
            response = claude_response_factory(stage)
            if stage == "ANALYZING":
                response = claude_response_factory(
                    "ANALYZING",
                    status="success",
                    recommendation="COMPLETE"
                )
            next_stage = orch.process_response(response)
            orch.update_stage(next_stage)

        # Now at CYCLE_END
        assert orch.current_stage == "CYCLE_END"

        # Process CYCLE_END - should go to COMPLETE
        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )
        next_stage = orch.process_response(response)

        assert next_stage == "COMPLETE"

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_single_cycle_does_not_continue(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that cycle_budget=1 does NOT generate continuation prompt."""
        # Context with cycle 1 of 1 (no more cycles)
        context = stage_context_factory(cycle_number=1, cycle_budget=1)

        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=1,
            cycle_budget=1
        )

        result = cycle_end_handler.process_response(response, context)

        # Should go to COMPLETE, not PLANNING
        assert result.next_stage == "COMPLETE"


class TestMultiCycleMission:
    """Tests for multi-cycle missions (cycle_budget > 1)."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_multi_cycle_continues_to_planning(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that cycle_budget=3 mission continues to PLANNING after cycle 1."""
        mission = mission_factory(
            cycle_budget=3,
            current_cycle=1
        )
        orch = orchestrator_factory(mission=mission)

        # Fast-forward to CYCLE_END
        orch.update_stage("CYCLE_END")

        # Process CYCLE_END with cycle_complete
        response = claude_response_factory(
            "CYCLE_END",
            status="cycle_complete",
            current_cycle=1,
            cycle_budget=3,
            continuation_prompt="Continue with cycle 2 tasks..."
        )
        next_stage = orch.process_response(response)

        # Should go to PLANNING for next cycle
        assert next_stage == "PLANNING"

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_continuation_prompt_generated(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that continuation_prompt is included in response."""
        context = stage_context_factory(cycle_number=1, cycle_budget=3)

        response = claude_response_factory(
            "CYCLE_END",
            status="cycle_complete",
            current_cycle=1,
            cycle_budget=3,
            continuation_prompt="Continue building feature X..."
        )

        result = cycle_end_handler.process_response(response, context)

        # Check event contains continuation prompt
        assert len(result.events_to_emit) > 0
        event_data = result.events_to_emit[0].data
        assert "continuation_prompt" in event_data
        assert event_data["continuation_prompt"] == "Continue building feature X..."

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_final_cycle_completes(
        self,
        orchestrator_factory,
        claude_response_factory,
        mission_factory
    ):
        """Test that final cycle (cycle 3 of 3) goes to COMPLETE."""
        mission = mission_factory(
            cycle_budget=3,
            current_cycle=3  # Final cycle
        )
        orch = orchestrator_factory(mission=mission)
        orch.update_stage("CYCLE_END")

        # Process CYCLE_END for final cycle
        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=3,
            cycle_budget=3
        )
        next_stage = orch.process_response(response)

        # Should go to COMPLETE
        assert next_stage == "COMPLETE"


class TestCycleManagerBehavior:
    """Tests for CycleManager functionality."""

    @pytest.mark.unit
    def test_cycle_manager_should_continue(
        self,
        tmp_path,
        mission_factory
    ):
        """Test CycleManager.should_continue returns correct values."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        # Create mission with cycle 1 of 3 (should continue)
        mission = mission_factory(cycle_budget=3, current_cycle=1)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        # cycle 1 of 3: should continue (1 < 3)
        assert cm.should_continue() is True

    @pytest.mark.unit
    def test_cycle_manager_is_last_cycle(
        self,
        real_state_manager,
        mission_factory,
        tmp_path
    ):
        """Test CycleManager.is_last_cycle detection."""
        from af_engine.cycle_manager import CycleManager

        # Test at final cycle
        mission = mission_factory(cycle_budget=3, current_cycle=3)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        from af_engine.state_manager import StateManager
        state = StateManager(mission_path)
        cm = CycleManager(state)

        assert cm.is_last_cycle is True

    @pytest.mark.unit
    def test_cycle_manager_cycles_remaining(
        self,
        real_state_manager,
        mission_factory,
        tmp_path
    ):
        """Test CycleManager.cycles_remaining calculation."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        # Cycle 2 of 5 means 3 remaining
        mission = mission_factory(cycle_budget=5, current_cycle=2)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        assert cm.cycles_remaining == 3  # 5 - 2 = 3

    @pytest.mark.unit
    def test_cycle_manager_advance_cycle(
        self,
        tmp_path,
        mission_factory
    ):
        """Test CycleManager.advance_cycle increments cycle number."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(cycle_budget=5, current_cycle=2)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path, auto_save=True)
        cm = CycleManager(state)

        # Advance cycle
        result = cm.advance_cycle("Continue to cycle 3")

        assert result["old_cycle"] == 2
        assert result["new_cycle"] == 3
        assert cm.current_cycle == 3


class TestCycleHistoryTracking:
    """Tests for cycle history management."""

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_cycle_completion_recorded(
        self,
        tmp_path,
        mission_factory
    ):
        """Test that cycle completion is recorded in history."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(cycle_budget=3, current_cycle=1)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path, auto_save=True)
        cm = CycleManager(state)

        # Record cycle completion
        cm.record_cycle_completion(
            summary="Built the foundation",
            status="completed",
            metrics={"files_created": 5}
        )

        # Check history
        history = cm.cycle_history
        assert len(history) == 1
        assert history[0]["summary"] == "Built the foundation"
        assert history[0]["status"] == "completed"

    @pytest.mark.regression
    @pytest.mark.regression_cycle_budget
    def test_cycle_history_persisted(
        self,
        tmp_path,
        mission_factory
    ):
        """Test that cycle history is persisted to disk."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(cycle_budget=3, current_cycle=1)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path, auto_save=True)
        cm = CycleManager(state)

        cm.record_cycle_completion("Cycle 1 done", "completed")

        # Read from disk
        with open(mission_path) as f:
            saved = json.load(f)

        assert "cycle_history" in saved
        assert len(saved["cycle_history"]) == 1


class TestContinuationPromptGeneration:
    """Tests for continuation prompt generation."""

    @pytest.mark.unit
    def test_generate_continuation_prompt_format(
        self,
        tmp_path,
        mission_factory
    ):
        """Test continuation prompt format."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(
            cycle_budget=3,
            current_cycle=1,
            problem_statement="Build a test system"
        )
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        prompt = cm.generate_continuation_prompt(
            cycle_summary="Built foundation",
            findings=["Pattern A works well", "Pattern B needs work"],
            next_objectives=["Implement feature X", "Add tests"]
        )

        # Check prompt contains expected elements
        assert "CONTINUATION" in prompt
        assert "Cycle 2" in prompt
        assert "Built foundation" in prompt or "PREVIOUS CYCLE SUMMARY" in prompt
        assert "Pattern A works well" in prompt or "KEY FINDINGS" in prompt

    @pytest.mark.unit
    def test_continuation_prompt_includes_original_mission(
        self,
        tmp_path,
        mission_factory
    ):
        """Test that continuation prompt includes original mission."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(problem_statement="Original mission text here")
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        prompt = cm.generate_continuation_prompt(
            cycle_summary="Done",
            findings=[],
            next_objectives=[]
        )

        assert "Original mission text here" in prompt or "ORIGINAL MISSION" in prompt


class TestCycleEndStagePrompt:
    """Tests for CYCLE_END stage prompt generation."""

    @pytest.mark.unit
    def test_cycle_end_prompt_shows_remaining_cycles(
        self,
        cycle_end_handler,
        stage_context_factory
    ):
        """Test that CYCLE_END prompt shows cycles remaining."""
        context = stage_context_factory(cycle_number=2, cycle_budget=5)

        prompt = cycle_end_handler.get_prompt(context)

        assert "cycle 2" in prompt.lower()
        assert "5" in prompt  # cycle_budget
        assert "3" in prompt or "remaining" in prompt.lower()  # cycles remaining

    @pytest.mark.unit
    def test_final_cycle_prompt_different(
        self,
        cycle_end_handler,
        stage_context_factory
    ):
        """Test that final cycle prompt is different from mid-cycle prompt."""
        # Mid-cycle context
        mid_context = stage_context_factory(cycle_number=2, cycle_budget=5)
        mid_prompt = cycle_end_handler.get_prompt(mid_context)

        # Final cycle context
        final_context = stage_context_factory(cycle_number=5, cycle_budget=5)
        final_prompt = cycle_end_handler.get_prompt(final_context)

        # Final prompt should mention "FINAL" or have different structure
        assert "FINAL" in final_prompt or "mission_complete" in final_prompt.lower()
        # Mid-cycle should mention continuation
        assert "continuation" in mid_prompt.lower() or "next cycle" in mid_prompt.lower()


class TestCycleEventEmission:
    """Tests for cycle-related event emission."""

    @pytest.mark.integration
    def test_cycle_completed_event_emitted(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that CYCLE_COMPLETED event is emitted."""
        context = stage_context_factory(cycle_number=1, cycle_budget=3)

        response = claude_response_factory(
            "CYCLE_END",
            status="cycle_complete",
            current_cycle=1,
            cycle_budget=3
        )

        result = cycle_end_handler.process_response(response, context)

        # Check for CYCLE_COMPLETED event
        cycle_events = [e for e in result.events_to_emit if "CYCLE" in str(e.type)]
        assert len(cycle_events) > 0

    @pytest.mark.integration
    def test_mission_completed_event_on_final_cycle(
        self,
        cycle_end_handler,
        stage_context_factory,
        claude_response_factory
    ):
        """Test that MISSION_COMPLETED event is emitted on final cycle."""
        context = stage_context_factory(cycle_number=3, cycle_budget=3)

        response = claude_response_factory(
            "CYCLE_END",
            status="mission_complete",
            current_cycle=3,
            cycle_budget=3
        )

        result = cycle_end_handler.process_response(response, context)

        # Check for MISSION_COMPLETED event
        mission_events = [e for e in result.events_to_emit if "MISSION_COMPLETED" in str(e.type)]
        assert len(mission_events) > 0


class TestCycleContextReport:
    """Tests for cycle context reporting."""

    @pytest.mark.unit
    def test_get_cycle_context(
        self,
        tmp_path,
        mission_factory
    ):
        """Test CycleManager.get_cycle_context returns expected fields."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(cycle_budget=5, current_cycle=3, iteration=2)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        context = cm.get_cycle_context()

        assert context["current_cycle"] == 3
        assert context["cycle_budget"] == 5
        assert context["cycles_remaining"] == 2
        assert context["is_last_cycle"] is False
        assert "iteration" in context

    @pytest.mark.unit
    def test_get_cycle_report(
        self,
        tmp_path,
        mission_factory
    ):
        """Test CycleManager.get_cycle_report generates report."""
        from af_engine.cycle_manager import CycleManager
        from af_engine.state_manager import StateManager

        mission = mission_factory(cycle_budget=3, current_cycle=2)
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        state = StateManager(mission_path)
        cm = CycleManager(state)

        report = cm.get_cycle_report()

        assert "Cycle Progress Report" in report or "cycle" in report.lower()
        assert "2" in report  # current cycle
        assert "3" in report  # budget


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
