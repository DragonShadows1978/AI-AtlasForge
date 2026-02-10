"""
Tests for PromptFactory - Template-Based Prompt Generation

These tests validate:
- Ground rules loading
- Context building
- KB context injection
- AfterImage context injection
- Recovery context injection
- Prompt assembly
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path


class TestPromptFactoryInit:
    """Tests for PromptFactory initialization."""

    def test_prompt_factory_import(self):
        """Test that PromptFactory can be imported."""
        from af_engine.prompt_factory import PromptFactory
        assert PromptFactory is not None

    def test_prompt_factory_init(self):
        """Test basic initialization."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory(atlasforge_root=Path("/tmp"))

        assert pf.root == Path("/tmp")
        assert pf._ground_rules_cache is None


class TestGroundRulesLoading:
    """Tests for ground rules loading."""

    def test_get_ground_rules_caches(self, tmp_path):
        """Test that ground rules are cached after first load."""
        from af_engine.prompt_factory import PromptFactory

        rules_file = tmp_path / "GROUND_RULES.md"
        rules_file.write_text("# Ground Rules\nBe awesome.")

        pf = PromptFactory(atlasforge_root=tmp_path)
        pf.GROUND_RULES_FILE = "GROUND_RULES.md"

        # First call loads from file
        rules1 = pf.get_ground_rules()
        assert "Be awesome" in rules1

        # Second call should use cache
        rules2 = pf.get_ground_rules()
        assert rules1 == rules2

    def test_get_ground_rules_missing_file(self, tmp_path):
        """Test behavior when ground rules file is missing."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory(atlasforge_root=tmp_path)
        pf.GROUND_RULES_FILE = "NONEXISTENT.md"

        rules = pf.get_ground_rules()

        assert rules == ""


class TestContextBuilding:
    """Tests for StageContext building."""

    def test_build_context(self):
        """Test building StageContext from StateManager."""
        from af_engine.prompt_factory import PromptFactory
        from af_engine.stages.base import StageContext

        mock_state = Mock()
        mock_state.mission = {
            "problem_statement": "Build a thing",
            "original_problem_statement": "Build a thing",
            "current_stage": "PLANNING",
            "preferences": {"style": "verbose"},
            "success_criteria": ["Works", "Fast"],
        }
        mock_state.mission_id = "test_mission"
        mock_state.cycle_number = 2
        mock_state.cycle_budget = 5
        mock_state.iteration = 3
        mock_state.history = []
        mock_state.cycle_history = []
        mock_state.get_workspace_dir = Mock(return_value=Path("/tmp/workspace"))
        mock_state.get_artifacts_dir = Mock(return_value=Path("/tmp/workspace/artifacts"))
        mock_state.get_research_dir = Mock(return_value=Path("/tmp/workspace/research"))
        mock_state.get_tests_dir = Mock(return_value=Path("/tmp/workspace/tests"))

        pf = PromptFactory()
        context = pf.build_context(mock_state)

        assert isinstance(context, StageContext)
        assert context.mission_id == "test_mission"
        assert context.problem_statement == "Build a thing"
        assert context.cycle_number == 2
        assert context.iteration == 3


class TestKBContextInjection:
    """Tests for Knowledge Base context injection."""

    def test_inject_kb_context_no_learnings(self):
        """Test KB injection when no learnings found."""
        from af_engine.prompt_factory import PromptFactory

        with patch('af_engine.kb_cache.query_relevant_learnings') as mock_query:
            mock_query.return_value = []

            pf = PromptFactory()
            result = pf.inject_kb_context("Original prompt", "test mission")

            assert result == "Original prompt"

    def test_inject_kb_context_with_learnings(self):
        """Test KB injection with learnings."""
        from af_engine.prompt_factory import PromptFactory

        with patch('af_engine.kb_cache.query_relevant_learnings') as mock_query:
            mock_query.return_value = [
                {
                    "title": "Past Learning",
                    "content": "Do it this way",
                    "mission_id": "past_mission",
                    "category": "technique",
                }
            ]

            pf = PromptFactory()
            result = pf.inject_kb_context(
                "Before === CURRENT MISSION === After",
                "test mission"
            )

            assert "LEARNINGS FROM PAST MISSIONS" in result
            assert "Past Learning" in result
            assert "Do it this way" in result

    def test_inject_kb_context_exception_handling(self):
        """Test that KB injection handles exceptions gracefully."""
        from af_engine.prompt_factory import PromptFactory

        with patch('af_engine.kb_cache.query_relevant_learnings') as mock_query:
            mock_query.side_effect = Exception("KB unavailable")

            pf = PromptFactory()
            result = pf.inject_kb_context("Original prompt", "test")

            assert result == "Original prompt"


class TestAfterImageInjection:
    """Tests for AfterImage context injection."""

    def test_inject_afterimage_no_provider(self):
        """Test AfterImage injection when provider unavailable."""
        from af_engine.prompt_factory import PromptFactory

        with patch.dict('sys.modules', {'atlasforge_enhancements.afterimage': None}):
            pf = PromptFactory()
            result = pf.inject_afterimage_context("Original prompt", "query")

            # Should return original when import fails
            assert "Original" in result

    def test_inject_afterimage_with_memories(self):
        """Test AfterImage injection with memories."""
        from af_engine.prompt_factory import PromptFactory

        mock_provider = Mock()
        mock_provider.search = Mock(return_value=[
            {
                "file_path": "src/utils.py",
                "snippet": "def helper(): pass",
                "context": "Utility function",
            }
        ])

        pf = PromptFactory()
        result = pf.inject_afterimage_context(
            "Original prompt",
            "helper function",
            afterimage_provider=mock_provider
        )

        assert "CODE MEMORY" in result
        assert "src/utils.py" in result
        assert "def helper" in result


class TestRecoveryContextInjection:
    """Tests for recovery context injection."""

    def test_inject_recovery_no_info(self):
        """Test recovery injection with no info."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.inject_recovery_context("Original prompt", None)

        assert result == "Original prompt"

    def test_inject_recovery_with_info(self):
        """Test recovery injection with crash info."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.inject_recovery_context(
            "Before === CURRENT MISSION === After",
            {
                "stage": "BUILDING",
                "mission_id": "crashed_mission",
                "iteration": 5,
                "cycle": 2,
                "hint": "Check the tests",
            }
        )

        assert "CRASH RECOVERY" in result
        assert "BUILDING" in result
        assert "crashed_mission" in result
        assert "Check the tests" in result


class TestFormatting:
    """Tests for formatting methods."""

    def test_format_preferences(self):
        """Test preference formatting."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_preferences({
            "coding_style": "verbose",
            "test_coverage": "high",
        })

        assert "User Preferences" in result
        assert "Coding Style: verbose" in result
        assert "Test Coverage: high" in result

    def test_format_preferences_empty(self):
        """Test formatting empty preferences."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_preferences({})

        assert result == ""

    def test_format_success_criteria(self):
        """Test success criteria formatting."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_success_criteria([
            "All tests pass",
            "Coverage > 80%",
        ])

        assert "Success Criteria" in result
        assert "1. All tests pass" in result
        assert "2. Coverage > 80%" in result

    def test_format_success_criteria_empty(self):
        """Test formatting empty criteria."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_success_criteria([])

        assert result == ""

    def test_format_history(self):
        """Test history formatting."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_history([
            {
                "timestamp": "2024-01-01T12:00:00",
                "stage": "PLANNING",
                "event": "Started planning",
            },
            {
                "timestamp": "2024-01-01T12:30:00",
                "stage": "BUILDING",
                "event": "Started building",
            },
        ])

        assert "Recent History" in result
        assert "PLANNING" in result
        assert "BUILDING" in result

    def test_format_history_empty(self):
        """Test formatting empty history."""
        from af_engine.prompt_factory import PromptFactory

        pf = PromptFactory()
        result = pf.format_history([])

        assert "No history" in result


class TestPromptAssembly:
    """Tests for prompt assembly."""

    def test_assemble_prompt_basic(self):
        """Test basic prompt assembly."""
        from af_engine.prompt_factory import PromptFactory
        from af_engine.stages.base import StageContext

        pf = PromptFactory()
        pf._ground_rules_cache_by_provider = {"gemini": "# Rules\nBe good."}

        context = StageContext(
            mission={"current_stage": "BUILDING", "llm_provider": "gemini"},
            mission_id="test",
            original_mission="Build stuff",
            problem_statement="Build stuff now",
            workspace_dir="/tmp/ws",
            artifacts_dir="/tmp/ws/artifacts",
            research_dir="/tmp/ws/research",
            tests_dir="/tmp/ws/tests",
            cycle_number=1,
            cycle_budget=3,
            iteration=0,
            max_iterations=10,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=[],
        )

        result = pf.assemble_prompt(
            stage_prompt="Do the building stage",
            context=context,
            include_ground_rules=True,
            include_mission_header=True,
        )

        assert "GROUND RULES" in result
        assert "Be good" in result
        assert "CURRENT MISSION" in result
        assert "Build stuff now" in result
        assert "Do the building stage" in result

    def test_assemble_prompt_no_ground_rules(self):
        """Test assembly without ground rules."""
        from af_engine.prompt_factory import PromptFactory
        from af_engine.stages.base import StageContext

        pf = PromptFactory()

        context = StageContext(
            mission={"current_stage": "TESTING"},
            mission_id="test",
            original_mission="Test",
            problem_statement="Test now",
            workspace_dir="/tmp",
            artifacts_dir="/tmp",
            research_dir="/tmp",
            tests_dir="/tmp",
            cycle_number=1,
            cycle_budget=1,
            iteration=0,
            max_iterations=5,
            history=[],
            cycle_history=[],
            preferences={},
            success_criteria=[],
        )

        result = pf.assemble_prompt(
            stage_prompt="Do testing",
            context=context,
            include_ground_rules=False,
            include_mission_header=True,
        )

        assert "GROUND RULES" not in result
        assert "Do testing" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
