"""
Real Claude API Integration Tests.

These tests validate prompt structure and Claude API integration
by making actual API calls. They are SKIPPED BY DEFAULT because they:
- Require a valid ANTHROPIC_API_KEY
- Consume API credits
- Take longer to run

To run these tests:
    pytest af_engine/tests/test_real_claude_integration.py -m real_claude -v

These tests serve as smoke tests to verify the full integration works
end-to-end with the actual Claude API.
"""

import os
import sys
import json
import pytest
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

# Ensure af_engine is importable
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


def has_claude_cli() -> bool:
    """Check if claude CLI is available."""
    try:
        result = subprocess.run(
            ["claude", "--version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def has_api_key() -> bool:
    """Check if ANTHROPIC_API_KEY is set."""
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


# Skip conditions
skip_no_api = pytest.mark.skipif(
    not has_api_key(),
    reason="ANTHROPIC_API_KEY not set"
)

skip_no_cli = pytest.mark.skipif(
    not has_claude_cli(),
    reason="claude CLI not available"
)


# ===========================================================================
# Prompt Structure Validation Tests
# ===========================================================================

class TestPromptStructure:
    """Tests that validate prompt structure without calling Claude."""

    @pytest.mark.real_claude
    def test_planning_prompt_contains_required_sections(self, tmp_path):
        """Verify PLANNING prompt contains all required sections."""
        from af_engine.stages.planning import PlanningStageHandler
        from af_engine.stages.base import StageContext

        handler = PlanningStageHandler()

        # Create minimal context
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "artifacts").mkdir()
        (workspace_dir / "research").mkdir()

        context = StageContext(
            mission={"current_stage": "PLANNING"},
            mission_id="test_prompt_structure",
            original_mission="Test mission",
            problem_statement="Build a test system",
            workspace_dir=str(workspace_dir),
            artifacts_dir=str(workspace_dir / "artifacts"),
            research_dir=str(workspace_dir / "research"),
            tests_dir=str(workspace_dir / "tests"),
            cycle_number=1,
            cycle_budget=1,
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=["Tests pass"],
        )

        prompt = handler.get_prompt(context)

        # Verify required sections
        assert "=== PLANNING STAGE ===" in prompt, "Missing PLANNING STAGE header"
        assert "=== RESEARCH PHASE" in prompt, "Missing RESEARCH PHASE section"
        assert "=== IMPLEMENTATION PLANNING ===" in prompt, "Missing IMPLEMENTATION PLANNING section"

        # Verify JSON response structure documented
        assert '"status": "plan_complete"' in prompt, "Missing plan_complete status in template"
        assert '"kb_learnings_applied"' in prompt, "Missing kb_learnings_applied in template"
        assert '"steps"' in prompt, "Missing steps in template"
        assert '"success_criteria"' in prompt, "Missing success_criteria in template"

    @pytest.mark.real_claude
    def test_building_prompt_contains_required_sections(self, tmp_path):
        """Verify BUILDING prompt contains all required sections."""
        from af_engine.stages.building import BuildingStageHandler
        from af_engine.stages.base import StageContext

        handler = BuildingStageHandler()

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "artifacts").mkdir()

        context = StageContext(
            mission={"current_stage": "BUILDING"},
            mission_id="test_prompt_structure",
            original_mission="Test mission",
            problem_statement="Build a test system",
            workspace_dir=str(workspace_dir),
            artifacts_dir=str(workspace_dir / "artifacts"),
            research_dir=str(workspace_dir / "research"),
            tests_dir=str(workspace_dir / "tests"),
            cycle_number=1,
            cycle_budget=1,
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=["Tests pass"],
        )

        prompt = handler.get_prompt(context)

        # Verify required sections
        assert "=== BUILDING STAGE ===" in prompt, "Missing BUILDING STAGE header"
        assert '"status": "build_complete"' in prompt, "Missing build_complete status in template"
        assert '"files_created"' in prompt, "Missing files_created in template"
        assert '"ready_for_testing"' in prompt, "Missing ready_for_testing in template"

    @pytest.mark.real_claude
    def test_analyzing_prompt_contains_required_sections(self, tmp_path):
        """Verify ANALYZING prompt contains all required sections."""
        from af_engine.stages.analyzing import AnalyzingStageHandler
        from af_engine.stages.base import StageContext

        handler = AnalyzingStageHandler()

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "artifacts").mkdir()

        context = StageContext(
            mission={"current_stage": "ANALYZING"},
            mission_id="test_prompt_structure",
            original_mission="Test mission",
            problem_statement="Build a test system",
            workspace_dir=str(workspace_dir),
            artifacts_dir=str(workspace_dir / "artifacts"),
            research_dir=str(workspace_dir / "research"),
            tests_dir=str(workspace_dir / "tests"),
            cycle_number=1,
            cycle_budget=1,
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=["Tests pass"],
        )

        prompt = handler.get_prompt(context)

        # Verify required sections
        assert "=== ANALYZING STAGE ===" in prompt, "Missing ANALYZING STAGE header"

        # Verify all possible statuses documented
        assert "success" in prompt, "Missing success status option"
        assert "needs_revision" in prompt, "Missing needs_revision status option"
        assert "needs_replanning" in prompt, "Missing needs_replanning status option"

        # Verify recommendation field
        assert "recommendation" in prompt, "Missing recommendation field"

    @pytest.mark.real_claude
    def test_testing_prompt_contains_required_sections(self, tmp_path):
        """Verify TESTING prompt contains all required sections."""
        from af_engine.stages.testing import TestingStageHandler
        from af_engine.stages.base import StageContext

        handler = TestingStageHandler()

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "artifacts").mkdir()

        context = StageContext(
            mission={"current_stage": "TESTING"},
            mission_id="test_prompt_structure",
            original_mission="Test mission",
            problem_statement="Build a test system",
            workspace_dir=str(workspace_dir),
            artifacts_dir=str(workspace_dir / "artifacts"),
            research_dir=str(workspace_dir / "research"),
            tests_dir=str(workspace_dir / "tests"),
            cycle_number=1,
            cycle_budget=1,
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=["Tests pass"],
        )

        prompt = handler.get_prompt(context)

        # Verify required sections
        assert "=== TESTING STAGE ===" in prompt, "Missing TESTING STAGE header"
        assert '"status"' in prompt, "Missing status in template"

    @pytest.mark.real_claude
    def test_cycle_end_prompt_contains_required_sections(self, tmp_path):
        """Verify CYCLE_END prompt contains all required sections."""
        from af_engine.stages.cycle_end import CycleEndStageHandler
        from af_engine.stages.base import StageContext

        handler = CycleEndStageHandler()

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "artifacts").mkdir()

        context = StageContext(
            mission={"current_stage": "CYCLE_END"},
            mission_id="test_prompt_structure",
            original_mission="Test mission",
            problem_statement="Build a test system",
            workspace_dir=str(workspace_dir),
            artifacts_dir=str(workspace_dir / "artifacts"),
            research_dir=str(workspace_dir / "research"),
            tests_dir=str(workspace_dir / "tests"),
            cycle_number=1,
            cycle_budget=3,  # Multi-cycle for continuation prompt test
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=["Tests pass"],
        )

        prompt = handler.get_prompt(context)

        # Verify required sections
        assert "CYCLE" in prompt.upper(), "Missing CYCLE header"
        assert "status" in prompt.lower(), "Missing status field"


# ===========================================================================
# Real Claude API Smoke Tests
# ===========================================================================

@pytest.mark.real_claude
@pytest.mark.slow
@skip_no_cli
class TestRealClaudeIntegration:
    """
    Smoke tests that make actual Claude API calls.

    These tests verify the full integration works end-to-end.
    They use the claude CLI with minimal prompts to validate
    prompt structure and response parsing.
    """

    def call_claude(
        self,
        prompt: str,
        timeout: int = 60,
        model: str = "claude-sonnet-4-20250514"
    ) -> Optional[str]:
        """Call Claude CLI with the given prompt."""
        try:
            result = subprocess.run(
                ["claude", "-p", prompt, "--model", model, "--output-format", "text"],
                capture_output=True,
                text=True,
                timeout=timeout
            )

            if result.returncode != 0:
                print(f"Claude CLI error: {result.stderr}")
                return None

            return result.stdout.strip()

        except subprocess.TimeoutExpired:
            print(f"Claude call timed out after {timeout}s")
            return None
        except Exception as e:
            print(f"Claude call failed: {e}")
            return None

    def test_simple_json_response(self):
        """Verify Claude can generate valid JSON response to simple prompt."""
        prompt = '''
        Respond with ONLY valid JSON in this exact format:
        {"status": "ok", "message": "test successful"}

        Do not include any text before or after the JSON.
        '''

        response = self.call_claude(prompt.strip())

        assert response is not None, "No response from Claude"

        # Try to parse JSON - it might be in a code block
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
            assert "status" in data, "Missing status field"
            assert data["status"] == "ok", f"Unexpected status: {data['status']}"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {response[:500]}... Error: {e}")

    def test_planning_style_response(self):
        """Verify Claude can generate planning-style response."""
        prompt = '''
        You are planning an implementation task.
        Task: Write a simple hello world function.

        Respond with ONLY valid JSON in this exact format:
        {
            "status": "plan_complete",
            "understanding": "brief description",
            "steps": [{"step": 1, "description": "write function"}],
            "estimated_files": ["hello.py"]
        }

        Do not include any text before or after the JSON.
        '''

        response = self.call_claude(prompt.strip())

        assert response is not None, "No response from Claude"

        # Extract JSON
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
            assert data.get("status") == "plan_complete", f"Unexpected status: {data.get('status')}"
            assert "understanding" in data, "Missing understanding field"
            assert "steps" in data, "Missing steps field"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {response[:500]}... Error: {e}")

    def test_analyzing_style_response(self):
        """Verify Claude can generate analyzing-style response."""
        prompt = '''
        You are analyzing code that was just built and tested.
        The tests all passed.

        Respond with ONLY valid JSON in this exact format:
        {
            "status": "success",
            "analysis": "All tests passed",
            "issues_found": [],
            "recommendation": "COMPLETE"
        }

        Do not include any text before or after the JSON.
        '''

        response = self.call_claude(prompt.strip())

        assert response is not None, "No response from Claude"

        # Extract JSON
        json_str = response
        if "```json" in response:
            json_str = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            json_str = response.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(json_str)
            assert data.get("status") == "success", f"Unexpected status: {data.get('status')}"
            assert "recommendation" in data, "Missing recommendation field"
        except json.JSONDecodeError as e:
            pytest.fail(f"Invalid JSON response: {response[:500]}... Error: {e}")


# ===========================================================================
# Prompt Factory Integration Tests
# ===========================================================================

class TestPromptFactoryIntegration:
    """Tests for PromptFactory integration."""

    @pytest.mark.real_claude
    def test_prompt_factory_builds_valid_context(self, tmp_path):
        """Verify PromptFactory builds valid StageContext."""
        from af_engine.prompt_factory import PromptFactory
        from af_engine.state_manager import StateManager
        import json

        # Create mission file
        mission_path = tmp_path / "state" / "mission.json"
        mission_path.parent.mkdir(parents=True)

        workspace_dir = tmp_path / "workspace" / "test_mission"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "artifacts").mkdir()
        (workspace_dir / "research").mkdir()
        (workspace_dir / "tests").mkdir()

        mission = {
            "mission_id": "test_prompt_factory",
            "problem_statement": "Test problem",
            "original_problem_statement": "Test problem",
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "cycle_budget": 1,
            "current_cycle": 1,
            "history": [],
            "cycle_history": [],
            "preferences": {"style": "concise"},
            "success_criteria": ["Tests pass"],
            "mission_workspace": str(workspace_dir),
            "mission_dir": str(tmp_path / "missions" / "test_mission"),
        }

        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        # Build context
        state_manager = StateManager(mission_path)
        factory = PromptFactory(tmp_path)
        context = factory.build_context(state_manager)

        # Verify context fields
        assert context.mission_id == "test_prompt_factory"
        assert context.problem_statement == "Test problem"
        assert context.iteration == 0
        assert context.cycle_budget == 1
        assert context.cycle_number == 1
        assert "style" in context.preferences

    @pytest.mark.real_claude
    def test_prompt_factory_formats_preferences(self):
        """Verify PromptFactory formats preferences correctly."""
        from af_engine.prompt_factory import PromptFactory

        factory = PromptFactory()

        # Test dict preferences
        prefs = {"coding_style": "concise", "error_handling": "verbose"}
        formatted = factory.format_preferences(prefs)

        assert "User Preferences:" in formatted
        assert "Coding Style:" in formatted
        assert "concise" in formatted

        # Test empty preferences
        assert factory.format_preferences({}) == ""

    @pytest.mark.real_claude
    def test_prompt_factory_formats_success_criteria(self):
        """Verify PromptFactory formats success criteria correctly."""
        from af_engine.prompt_factory import PromptFactory

        factory = PromptFactory()

        criteria = ["Tests pass", "No lint errors", "Documentation complete"]
        formatted = factory.format_success_criteria(criteria)

        assert "Success Criteria:" in formatted
        assert "1. Tests pass" in formatted
        assert "2. No lint errors" in formatted
        assert "3. Documentation complete" in formatted

        # Test empty criteria
        assert factory.format_success_criteria([]) == ""

    @pytest.mark.real_claude
    def test_prompt_factory_formats_history(self):
        """Verify PromptFactory formats history correctly."""
        from af_engine.prompt_factory import PromptFactory

        factory = PromptFactory()

        history = [
            {"timestamp": "2025-01-01T10:00:00", "stage": "PLANNING", "event": "Started planning"},
            {"timestamp": "2025-01-01T10:30:00", "stage": "BUILDING", "event": "Building code"},
        ]

        formatted = factory.format_history(history)

        assert "Recent History:" in formatted
        assert "PLANNING: Started planning" in formatted
        assert "BUILDING: Building code" in formatted

        # Test empty history
        assert "No history" in factory.format_history([])


# ===========================================================================
# Ground Rules Loading Test
# ===========================================================================

class TestGroundRulesLoading:
    """Tests for ground rules loading."""

    @pytest.mark.real_claude
    def test_ground_rules_loaded(self):
        """Verify ground rules can be loaded."""
        from af_engine.prompt_factory import PromptFactory

        # Point to actual AtlasForge root
        factory = PromptFactory(AF_ROOT)
        ground_rules = factory.get_ground_rules()

        # If GROUND_RULES.md exists, verify content
        ground_rules_path = AF_ROOT / "GROUND_RULES.md"
        if ground_rules_path.exists():
            assert len(ground_rules) > 100, "Ground rules too short"
            assert "AUTONOMOUS" in ground_rules or "autonomous" in ground_rules.lower(), \
                "Ground rules missing autonomy directive"
        else:
            # OK if file doesn't exist - just verifies no crash
            assert ground_rules == ""

    @pytest.mark.real_claude
    def test_ground_rules_caching(self):
        """Verify ground rules are cached after first load."""
        from af_engine.prompt_factory import PromptFactory

        factory = PromptFactory(AF_ROOT)

        # First call
        rules1 = factory.get_ground_rules()

        # Second call should use cache
        rules2 = factory.get_ground_rules()

        # Same reference if cached
        assert rules1 is rules2, "Ground rules not cached"
