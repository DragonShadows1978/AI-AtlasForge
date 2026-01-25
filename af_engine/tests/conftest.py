"""
pytest fixtures for af_engine test suite.

Provides common test fixtures used across test modules.

Enhanced for comprehensive end-to-end integration testing including:
- Mission factory fixtures
- Mock Claude response fixtures
- Orchestrator factory fixtures
- Subprocess mocking for Claude CLI
- Temporary mission workspaces
"""

import os
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, Mock, patch
from typing import Dict, Any, Optional, List

# Ensure af_engine is importable
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


# ===========================================================================
# Custom markers registration
# ===========================================================================

def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')"
    )
    config.addinivalue_line(
        "markers", "integration: marks tests as integration tests"
    )
    config.addinivalue_line(
        "markers", "unit: marks tests as unit tests"
    )
    config.addinivalue_line(
        "markers", "real_claude: marks tests requiring actual Claude API"
    )
    # Regression markers for specific bug categories
    config.addinivalue_line(
        "markers", "regression: marks tests as regression tests"
    )
    config.addinivalue_line(
        "markers", "regression_iteration_counter: regression tests for iteration counter bugs"
    )
    config.addinivalue_line(
        "markers", "regression_needs_replanning: regression tests for needs_replanning functionality"
    )
    config.addinivalue_line(
        "markers", "regression_needs_revision: regression tests for needs_revision functionality"
    )
    config.addinivalue_line(
        "markers", "regression_cycle_budget: regression tests for cycle budget handling"
    )
    config.addinivalue_line(
        "markers", "regression_timeout_retry: regression tests for timeout/retry logic"
    )


# ===========================================================================
# Basic fixtures (existing)
# ===========================================================================

@pytest.fixture
def integration_manager():
    """Provide a fresh IntegrationManager for tests."""
    from af_engine.integration_manager import IntegrationManager
    return IntegrationManager()


@pytest.fixture
def loaded_integration_manager():
    """Provide an IntegrationManager with default integrations loaded."""
    from af_engine.integration_manager import IntegrationManager
    mgr = IntegrationManager()
    mgr.load_default_integrations()
    return mgr


@pytest.fixture
def mock_state_manager():
    """Provide a mock StateManager."""
    mock = MagicMock()
    mock.mission_id = "test_mission"
    mock.cycle_number = 1
    mock.cycle_budget = 3
    mock.iteration = 0
    mock.mission = {
        "problem_statement": "Test mission",
        "current_stage": "PLANNING",
        "original_problem_statement": "Test mission",
    }
    mock.history = []
    mock.cycle_history = []
    mock.get_workspace_dir.return_value = Path("/tmp/test_workspace")
    mock.get_artifacts_dir.return_value = Path("/tmp/test_workspace/artifacts")
    mock.get_research_dir.return_value = Path("/tmp/test_workspace/research")
    mock.get_tests_dir.return_value = Path("/tmp/test_workspace/tests")
    return mock


@pytest.fixture
def sample_event():
    """Provide a sample Event for tests."""
    from af_engine.integrations.base import Event, StageEvent

    return Event(
        type=StageEvent.STAGE_STARTED,
        stage="PLANNING",
        mission_id="test_mission",
        data={"test": True},
        source="test_fixture"
    )


@pytest.fixture
def kb_cache():
    """Provide a fresh KB cache for tests."""
    from af_engine.kb_cache import KBCache
    return KBCache(max_size=10, ttl_seconds=60)


@pytest.fixture(autouse=True)
def clean_kb_cache():
    """Ensure KB cache is clean before each test."""
    yield
    # Clean up after test
    try:
        from af_engine.kb_cache import clear_cache
        clear_cache()
    except ImportError:
        pass


@pytest.fixture
def temp_workspace(tmp_path):
    """Provide a temporary workspace directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "artifacts").mkdir()
    (workspace / "research").mkdir()
    (workspace / "tests").mkdir()
    return workspace


# ===========================================================================
# Mission factory fixtures
# ===========================================================================

@pytest.fixture
def mission_factory(tmp_path):
    """Factory fixture for creating fully-initialized missions.

    Returns a callable that creates mission state dicts with configurable
    cycle_budget, iteration, and stage.
    """
    def _create_mission(
        mission_id: str = "test_mission",
        problem_statement: str = "Test mission for integration testing",
        cycle_budget: int = 1,
        current_cycle: int = 1,
        iteration: int = 0,
        current_stage: str = "PLANNING",
        history: List[Dict] = None,
        cycle_history: List[Dict] = None,
    ) -> Dict[str, Any]:
        workspace_dir = tmp_path / "workspace" / mission_id
        workspace_dir.mkdir(parents=True, exist_ok=True)
        (workspace_dir / "artifacts").mkdir(exist_ok=True)
        (workspace_dir / "research").mkdir(exist_ok=True)
        (workspace_dir / "tests").mkdir(exist_ok=True)

        mission_dir = tmp_path / "missions" / mission_id
        mission_dir.mkdir(parents=True, exist_ok=True)

        return {
            "mission_id": mission_id,
            "problem_statement": problem_statement,
            "original_problem_statement": problem_statement,
            "preferences": {},
            "success_criteria": ["Test passes"],
            "current_stage": current_stage,
            "iteration": iteration,
            "max_iterations": 10,
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": history or [],
            "created_at": datetime.now().isoformat(),
            "cycle_started_at": datetime.now().isoformat(),
            "cycle_budget": cycle_budget,
            "current_cycle": current_cycle,
            "cycle_history": cycle_history or [],
            "mission_workspace": str(workspace_dir),
            "mission_dir": str(mission_dir),
            "project_name": f"test_project_{mission_id}",
            "metadata": {}
        }

    return _create_mission


@pytest.fixture
def default_mission(mission_factory):
    """Provide a default mission for simple tests."""
    return mission_factory()


# ===========================================================================
# Claude response fixtures
# ===========================================================================

@pytest.fixture
def claude_response_factory():
    """Factory for creating mock Claude responses for each stage.

    Returns a callable that generates appropriate JSON responses
    for each stage of the workflow.
    """
    def _create_response(
        stage: str,
        status: str = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a mock Claude response for the given stage."""

        stage = stage.upper()

        if stage == "PLANNING":
            return {
                "status": status or "plan_complete",
                "understanding": kwargs.get("understanding", "Implementation plan for test"),
                "kb_learnings_applied": kwargs.get("kb_learnings_applied", []),
                "research_conducted": kwargs.get("research_conducted", []),
                "sources_consulted": kwargs.get("sources_consulted", []),
                "key_requirements": kwargs.get("key_requirements", ["req1"]),
                "assumptions": kwargs.get("assumptions", []),
                "approach": kwargs.get("approach", "Test approach"),
                "approach_rationale": kwargs.get("approach_rationale", "Test rationale"),
                "steps": kwargs.get("steps", [{"step": 1, "description": "Test step", "files": ["test.py"]}]),
                "success_criteria": kwargs.get("success_criteria", ["Tests pass"]),
                "estimated_files": kwargs.get("estimated_files", ["test.py"]),
                "message_to_human": kwargs.get("message_to_human", "Planning complete.")
            }

        elif stage == "BUILDING":
            return {
                "status": status or "build_complete",
                "files_created": kwargs.get("files_created", ["test.py"]),
                "files_modified": kwargs.get("files_modified", []),
                "summary": kwargs.get("summary", "Built test implementation"),
                "ready_for_testing": kwargs.get("ready_for_testing", True),
                "blockers": kwargs.get("blockers", []),
                "message_to_human": kwargs.get("message_to_human", "Build complete.")
            }

        elif stage == "TESTING":
            return {
                "status": status or "tests_passed",
                "self_tests": kwargs.get("self_tests", [
                    {"name": "test_main", "passed": True, "output": "OK"}
                ]),
                "adversarial_testing": kwargs.get("adversarial_testing", {
                    "red_team_issues": [],
                    "property_violations": [],
                    "mutation_score": 0.85,
                    "spec_alignment": 0.95,
                    "epistemic_score": 0.80,
                    "rigor_level": "strong"
                }),
                "summary": kwargs.get("summary", "All tests passed"),
                "success_criteria_met": kwargs.get("success_criteria_met", ["Tests pass"]),
                "success_criteria_failed": kwargs.get("success_criteria_failed", []),
                "issues_to_fix": kwargs.get("issues_to_fix", []),
                "message_to_human": kwargs.get("message_to_human", "Tests passed.")
            }

        elif stage == "ANALYZING":
            # Default to success - tests can override for needs_revision/needs_replanning
            return {
                "status": status or "success",
                "analysis": kwargs.get("analysis", "All tests passed, implementation is correct."),
                "issues_found": kwargs.get("issues_found", []),
                "proposed_fixes": kwargs.get("proposed_fixes", []),
                "recommendation": kwargs.get("recommendation", "COMPLETE"),
                "final_report": kwargs.get("final_report", "Test completed successfully"),
                "deliverables": kwargs.get("deliverables", ["test.py"]),
                "message_to_human": kwargs.get("message_to_human", "Analysis complete.")
            }

        elif stage == "CYCLE_END":
            current_cycle = kwargs.get("current_cycle", 1)
            cycle_budget = kwargs.get("cycle_budget", 1)

            if current_cycle >= cycle_budget:
                # Final cycle
                return {
                    "status": status or "mission_complete",
                    "total_cycles": cycle_budget,
                    "final_report": {
                        "summary": kwargs.get("summary", "Mission complete"),
                        "all_files": kwargs.get("all_files", ["test.py"]),
                        "key_achievements": kwargs.get("key_achievements", ["Built test"]),
                        "challenges_overcome": kwargs.get("challenges_overcome", []),
                        "lessons_learned": kwargs.get("lessons_learned", [])
                    },
                    "deliverables": kwargs.get("deliverables", ["test.py"]),
                    "next_mission_recommendation": kwargs.get("next_mission_recommendation", {
                        "mission_title": "Follow-up mission",
                        "mission_description": "Continue work",
                        "suggested_cycles": 3,
                        "rationale": "Natural extension"
                    }),
                    "message_to_human": kwargs.get("message_to_human", "Mission complete.")
                }
            else:
                # More cycles remain
                return {
                    "status": status or "cycle_complete",
                    "cycle_number": current_cycle,
                    "cycle_report": {
                        "summary": kwargs.get("summary", f"Completed cycle {current_cycle}"),
                        "files_created": kwargs.get("files_created", ["test.py"]),
                        "files_modified": kwargs.get("files_modified", []),
                        "achievements": kwargs.get("achievements", ["Completed phase 1"]),
                        "issues": kwargs.get("issues", [])
                    },
                    "continuation_prompt": kwargs.get(
                        "continuation_prompt",
                        f"Continue with cycle {current_cycle + 1}"
                    ),
                    "message_to_human": kwargs.get("message_to_human", f"Cycle {current_cycle} complete.")
                }

        elif stage == "COMPLETE":
            return {
                "status": status or "mission_complete",
                "summary": kwargs.get("summary", "Mission completed successfully"),
                "deliverables": kwargs.get("deliverables", ["test.py"]),
                "lessons_learned": kwargs.get("lessons_learned", []),
                "message_to_human": kwargs.get("message_to_human", "Mission complete.")
            }

        else:
            raise ValueError(f"Unknown stage: {stage}")

    return _create_response


# ===========================================================================
# Orchestrator factory fixture
# ===========================================================================

@pytest.fixture
def orchestrator_factory(tmp_path, mission_factory, claude_response_factory):
    """Factory for creating StageOrchestrator instances with mocked dependencies.

    Creates orchestrators that can process stages through mocked handlers
    without actually calling Claude.
    """
    def _create_orchestrator(
        mission: Dict[str, Any] = None,
        mock_handlers: bool = True,
        auto_responses: Dict[str, Dict] = None,
    ):
        """Create a StageOrchestrator with optional mocking.

        Args:
            mission: Mission dict to initialize with (uses default if None)
            mock_handlers: Whether to mock stage handlers
            auto_responses: Dict mapping stage -> response dict for auto-responses
        """
        from af_engine.orchestrator import StageOrchestrator
        from af_engine.state_manager import StateManager
        from af_engine.stages.base import StageResult, StageRestrictions

        # Create mission file path
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True, exist_ok=True)

        # Use provided mission or create default
        if mission is None:
            mission = mission_factory()

        # Write mission to disk
        with open(mission_path, 'w') as f:
            json.dump(mission, f, indent=2)

        # Create orchestrator
        orch = StageOrchestrator(
            mission_path=mission_path,
            atlasforge_root=tmp_path
        )

        if mock_handlers:
            # Create mock responses dictionary
            responses = auto_responses or {}

            # Create a mock handler factory
            def create_mock_handler(stage_name: str):
                mock_handler = Mock()
                mock_handler.stage_name = stage_name

                def mock_get_prompt(context):
                    return f"Mock prompt for {stage_name}"

                def mock_process_response(response, context):
                    # Use auto_responses if provided
                    if stage_name in responses:
                        response = responses[stage_name]

                    # Determine next stage based on stage logic
                    from af_engine.stages import (
                        planning, building, testing, analyzing, cycle_end, complete
                    )

                    handlers = {
                        "PLANNING": planning.PlanningStageHandler(),
                        "BUILDING": building.BuildingStageHandler(),
                        "TESTING": testing.TestingStageHandler(),
                        "ANALYZING": analyzing.AnalyzingStageHandler(),
                        "CYCLE_END": cycle_end.CycleEndStageHandler(),
                        "COMPLETE": complete.CompleteStageHandler(),
                    }

                    handler = handlers.get(stage_name)
                    if handler:
                        return handler.process_response(response, context)

                    return StageResult(
                        success=True,
                        next_stage=stage_name,
                        status="unknown",
                    )

                def mock_get_restrictions():
                    return StageRestrictions()

                mock_handler.get_prompt = mock_get_prompt
                mock_handler.process_response = mock_process_response
                mock_handler.get_restrictions = mock_get_restrictions

                return mock_handler

            # Patch the registry's get_handler to return mocks
            original_get_handler = orch.registry.get_handler

            def patched_get_handler(stage_name):
                # Use real handlers but mock the process_response to use auto_responses
                return original_get_handler(stage_name)

            orch.registry.get_handler = patched_get_handler

        return orch

    return _create_orchestrator


# ===========================================================================
# State manager fixture with file backing
# ===========================================================================

@pytest.fixture
def real_state_manager(tmp_path, mission_factory):
    """Provide a real StateManager with file backing."""
    from af_engine.state_manager import StateManager

    mission_path = tmp_path / "state" / "mission.json"
    mission_path.parent.mkdir(parents=True, exist_ok=True)

    # Create initial mission
    mission = mission_factory()
    with open(mission_path, 'w') as f:
        json.dump(mission, f, indent=2)

    return StateManager(mission_path, auto_save=True)


# ===========================================================================
# Cycle manager fixture
# ===========================================================================

@pytest.fixture
def cycle_manager(real_state_manager):
    """Provide a CycleManager with real state."""
    from af_engine.cycle_manager import CycleManager
    return CycleManager(real_state_manager)


# ===========================================================================
# Stage handler fixtures
# ===========================================================================

@pytest.fixture
def planning_handler():
    """Provide a PlanningStageHandler instance."""
    from af_engine.stages.planning import PlanningStageHandler
    return PlanningStageHandler()


@pytest.fixture
def building_handler():
    """Provide a BuildingStageHandler instance."""
    from af_engine.stages.building import BuildingStageHandler
    return BuildingStageHandler()


@pytest.fixture
def testing_handler():
    """Provide a TestingStageHandler instance."""
    from af_engine.stages.testing import TestingStageHandler
    return TestingStageHandler()


@pytest.fixture
def analyzing_handler():
    """Provide an AnalyzingStageHandler instance."""
    from af_engine.stages.analyzing import AnalyzingStageHandler
    return AnalyzingStageHandler()


@pytest.fixture
def cycle_end_handler():
    """Provide a CycleEndStageHandler instance."""
    from af_engine.stages.cycle_end import CycleEndStageHandler
    return CycleEndStageHandler()


@pytest.fixture
def complete_handler():
    """Provide a CompleteStageHandler instance."""
    from af_engine.stages.complete import CompleteStageHandler
    return CompleteStageHandler()


# ===========================================================================
# Stage context fixture
# ===========================================================================

@pytest.fixture
def stage_context_factory(mission_factory, tmp_path):
    """Factory for creating StageContext instances."""
    from af_engine.stages.base import StageContext

    def _create_context(
        mission: Dict[str, Any] = None,
        cycle_number: int = 1,
        cycle_budget: int = 1,
        iteration: int = 0,
        **overrides
    ) -> StageContext:
        if mission is None:
            mission = mission_factory(
                cycle_budget=cycle_budget,
                current_cycle=cycle_number,
                iteration=iteration,
            )

        workspace_dir = mission.get("mission_workspace", str(tmp_path / "workspace"))

        return StageContext(
            mission=mission,
            mission_id=mission.get("mission_id", "test_mission"),
            original_mission=mission.get("original_problem_statement", "Test"),
            problem_statement=mission.get("problem_statement", "Test"),
            workspace_dir=workspace_dir,
            artifacts_dir=f"{workspace_dir}/artifacts",
            research_dir=f"{workspace_dir}/research",
            tests_dir=f"{workspace_dir}/tests",
            cycle_number=cycle_number,
            cycle_budget=cycle_budget,
            iteration=iteration,
            max_iterations=mission.get("max_iterations", 10),
            history=mission.get("history", []),
            cycle_history=mission.get("cycle_history", []),
            preferences=mission.get("preferences", {}),
            success_criteria=mission.get("success_criteria", []),
            **overrides
        )

    return _create_context


# ===========================================================================
# Test data fixtures
# ===========================================================================

@pytest.fixture
def needs_revision_response():
    """Response that triggers needs_revision path."""
    return {
        "status": "needs_revision",
        "analysis": "Found issues that need fixing",
        "issues_found": ["Bug in line 42"],
        "proposed_fixes": ["Fix the bug"],
        "recommendation": "BUILDING",
        "message_to_human": "Needs revision, returning to building"
    }


@pytest.fixture
def needs_replanning_response():
    """Response that triggers needs_replanning path."""
    return {
        "status": "needs_replanning",
        "analysis": "Approach needs to be reconsidered",
        "issues_found": ["Architecture issue"],
        "proposed_fixes": ["Redesign component"],
        "recommendation": "PLANNING",
        "message_to_human": "Needs replanning"
    }


@pytest.fixture
def success_response():
    """Response that triggers success path."""
    return {
        "status": "success",
        "analysis": "All tests passed",
        "issues_found": [],
        "proposed_fixes": [],
        "recommendation": "COMPLETE",
        "final_report": "Test completed successfully",
        "deliverables": ["test.py"],
        "message_to_human": "Analysis complete, moving to cycle end"
    }


# ===========================================================================
# Helper functions
# ===========================================================================

def run_stage_transition(
    orchestrator,
    stage_responses: Dict[str, Dict[str, Any]],
    max_transitions: int = 20
) -> List[str]:
    """Helper to run a mission through stages with given responses.

    Args:
        orchestrator: StageOrchestrator instance
        stage_responses: Dict mapping stage name to response dict
        max_transitions: Maximum number of transitions to prevent infinite loops

    Returns:
        List of stages visited in order
    """
    stages_visited = [orchestrator.current_stage]

    for i in range(max_transitions):
        current_stage = orchestrator.current_stage

        if current_stage == "COMPLETE":
            break

        # Get response for current stage
        response = stage_responses.get(current_stage, {})

        # Process response
        next_stage = orchestrator.process_response(response)

        # Transition to next stage
        if next_stage != current_stage:
            orchestrator.update_stage(next_stage)
            stages_visited.append(next_stage)

    return stages_visited
