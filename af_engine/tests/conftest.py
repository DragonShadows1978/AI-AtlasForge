"""
pytest fixtures for af_engine test suite.

Provides common test fixtures used across test modules.
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

# Ensure af_engine is importable
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


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


# Markers for test categorization
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
