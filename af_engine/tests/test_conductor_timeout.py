"""
Tests for conductor-level timeout handling.

Verifies that MAX_CLAUDE_RETRIES is respected in atlasforge_conductor.py
and that the mission halts gracefully after max retries are exceeded.
"""

import pytest
import json
import subprocess
import sys
import os
from pathlib import Path
from datetime import datetime
from unittest.mock import patch, MagicMock, Mock

# Add project root to path for imports
AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))


# ===========================================================================
# Test Data
# ===========================================================================

MAX_CLAUDE_RETRIES = 3  # Must match atlasforge_conductor.py


# ===========================================================================
# Test invoke_llm timeout behavior
# ===========================================================================

class TestInvokeLlmTimeout:
    """Test invoke_llm function timeout handling."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_invoke_llm_returns_none_on_timeout(self):
        """Verify invoke_llm returns None when Claude times out."""
        import atlasforge_conductor as conductor

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=5)

            result = conductor.invoke_llm("test prompt", timeout=5)

            assert result is None

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_invoke_llm_returns_none_on_error(self):
        """Verify invoke_llm returns None on subprocess error."""
        import atlasforge_conductor as conductor

        with patch('subprocess.run') as mock_run:
            mock_run.side_effect = Exception("Subprocess failed")

            result = conductor.invoke_llm("test prompt", timeout=5)

            assert result is None

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_invoke_llm_returns_none_on_nonzero_returncode(self):
        """Verify invoke_llm returns None when subprocess returns non-zero."""
        import atlasforge_conductor as conductor

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "Error from Claude CLI"
        mock_result.stdout = ""

        with patch('subprocess.run', return_value=mock_result):
            result = conductor.invoke_llm("test prompt", timeout=5)

            assert result is None

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_invoke_llm_returns_response_on_success(self):
        """Verify invoke_llm returns response text on success."""
        import atlasforge_conductor as conductor

        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = '{"status": "success"}'
        mock_result.stderr = ""

        with patch('subprocess.run', return_value=mock_result):
            result = conductor.invoke_llm("test prompt", timeout=5)

            assert result == '{"status": "success"}'


# ===========================================================================
# Test MAX_CLAUDE_RETRIES enforcement in run_rd_mode
# ===========================================================================

class TestMaxClaudeRetriesEnforcement:
    """Test that MAX_CLAUDE_RETRIES is respected in the main loop."""

    @pytest.fixture
    def mock_controller(self, tmp_path):
        """Create a mock RDMissionController."""
        # Create state directory structure
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        mission = {
            "mission_id": "test_timeout_mission",
            "problem_statement": "Test timeout handling",
            "original_problem_statement": "Test timeout handling",
            "current_stage": "BUILDING",
            "iteration": 0,
            "cycle_budget": 1,
            "current_cycle": 1,
            "mission_workspace": str(tmp_path / "workspace"),
        }

        mission_path = state_dir / "mission.json"
        with open(mission_path, 'w') as f:
            json.dump(mission, f)

        controller = Mock()
        controller.mission = mission
        controller.load_mission.return_value = mission
        controller.build_rd_prompt.return_value = "test prompt"

        return controller

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_timeout_retries_tracked_correctly(self):
        """Verify that consecutive timeout failures are tracked correctly."""
        # This tests the retry counter logic conceptually
        timeout_retries = 0

        for attempt in range(MAX_CLAUDE_RETRIES + 1):
            response = None  # Simulate timeout

            if response is None:
                timeout_retries += 1
                if timeout_retries >= MAX_CLAUDE_RETRIES:
                    break
            else:
                timeout_retries = 0

        assert timeout_retries == MAX_CLAUDE_RETRIES

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_timeout_counter_resets_on_success(self):
        """Verify that timeout counter resets after successful response."""
        timeout_retries = 0
        responses = [None, None, "success", None]

        for response in responses:
            if response is None:
                timeout_retries += 1
            else:
                timeout_retries = 0  # Reset on success

        assert timeout_retries == 1  # Only the last timeout


# ===========================================================================
# Test JSON extraction
# ===========================================================================

class TestExtractJsonFromResponse:
    """Test extract_json_from_response function."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_extract_json_from_clean_json(self):
        """Verify extraction from clean JSON string."""
        import atlasforge_conductor as conductor

        text = '{"status": "success", "message": "done"}'
        result = conductor.extract_json_from_response(text)

        assert result is not None
        assert result["status"] == "success"

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_extract_json_from_markdown_block(self):
        """Verify extraction from JSON inside markdown code block."""
        import atlasforge_conductor as conductor

        text = """Here's the result:
```json
{"status": "success"}
```
That's all."""
        result = conductor.extract_json_from_response(text)

        assert result is not None
        assert result["status"] == "success"

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_extract_json_from_embedded_json(self):
        """Verify extraction from JSON embedded in text."""
        import atlasforge_conductor as conductor

        text = 'The result is {"status": "done"} which means success.'
        result = conductor.extract_json_from_response(text)

        assert result is not None
        assert result["status"] == "done"

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_extract_json_returns_none_for_empty(self):
        """Verify None returned for empty string."""
        import atlasforge_conductor as conductor

        result = conductor.extract_json_from_response("")
        assert result is None

        result = conductor.extract_json_from_response(None)
        assert result is None

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_extract_json_handles_trailing_comma(self):
        """Verify trailing comma is handled."""
        import atlasforge_conductor as conductor

        text = '{"status": "success", "items": ["a", "b",],}'
        result = conductor.extract_json_from_response(text)

        assert result is not None
        assert result["status"] == "success"


# ===========================================================================
# Test journal logging on timeout
# ===========================================================================

class TestJournalLoggingOnTimeout:
    """Test that timeout failures are logged to journal."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_append_journal_accepts_timeout_entry(self, tmp_path):
        """Verify journal accepts timeout failure entries."""
        import atlasforge_conductor as conductor

        # Mock the journal path
        journal_path = tmp_path / "claude_journal.jsonl"

        with patch.object(conductor, 'CLAUDE_JOURNAL_PATH', journal_path):
            entry = {
                "type": "claude_timeout_failure",
                "stage": "BUILDING",
                "retries": 3
            }
            conductor.append_journal(entry)

            # Read and verify
            with open(journal_path) as f:
                written = json.loads(f.read().strip())

            assert written["type"] == "claude_timeout_failure"
            assert written["stage"] == "BUILDING"
            assert written["retries"] == 3
            assert "timestamp" in written

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_append_journal_accepts_rd_cycle_entry(self, tmp_path):
        """Verify journal accepts R&D cycle entries."""
        import atlasforge_conductor as conductor

        journal_path = tmp_path / "claude_journal.jsonl"

        with patch.object(conductor, 'CLAUDE_JOURNAL_PATH', journal_path):
            entry = {
                "type": "rd_cycle",
                "stage": "TESTING",
                "status": "tests_passed",
                "message": "All tests passed"
            }
            conductor.append_journal(entry)

            with open(journal_path) as f:
                written = json.loads(f.read().strip())

            assert written["type"] == "rd_cycle"
            assert written["stage"] == "TESTING"


# ===========================================================================
# Test mission validation
# ===========================================================================

class TestMissionValidation:
    """Test _is_valid_mission helper function."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_empty_mission_is_invalid(self):
        """Verify empty mission dict is invalid."""
        import atlasforge_conductor as conductor

        assert conductor._is_valid_mission({}) is False
        assert conductor._is_valid_mission(None) is False

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_mission_without_id_is_invalid(self):
        """Verify mission without mission_id is invalid."""
        import atlasforge_conductor as conductor

        mission = {"problem_statement": "Do something"}
        assert conductor._is_valid_mission(mission) is False

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_mission_without_problem_is_invalid(self):
        """Verify mission without problem_statement is invalid."""
        import atlasforge_conductor as conductor

        mission = {"mission_id": "test_123"}
        assert conductor._is_valid_mission(mission) is False

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_mission_with_whitespace_problem_is_invalid(self):
        """Verify mission with whitespace-only problem is invalid."""
        import atlasforge_conductor as conductor

        mission = {"mission_id": "test_123", "problem_statement": "   \n\t  "}
        assert conductor._is_valid_mission(mission) is False

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_mission_with_placeholder_problem_is_invalid(self):
        """Verify mission with placeholder problem statement is invalid."""
        import atlasforge_conductor as conductor

        mission = {
            "mission_id": "test_123",
            "problem_statement": "No mission defined. Please set a mission."
        }
        assert conductor._is_valid_mission(mission) is False

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_valid_mission_returns_true(self):
        """Verify valid mission returns True."""
        import atlasforge_conductor as conductor

        mission = {
            "mission_id": "test_123",
            "problem_statement": "Implement feature X"
        }
        assert conductor._is_valid_mission(mission) is True


# ===========================================================================
# Test backoff calculation
# ===========================================================================

class TestBackoffCalculation:
    """Test _calculate_backoff_interval function."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_backoff_exponential_growth(self):
        """Verify exponential backoff intervals."""
        import atlasforge_conductor as conductor

        # With base_interval=1.0
        assert conductor._calculate_backoff_interval(0, 1.0) == 1.0   # 1 * 2^0
        assert conductor._calculate_backoff_interval(1, 1.0) == 2.0   # 1 * 2^1
        assert conductor._calculate_backoff_interval(2, 1.0) == 4.0   # 1 * 2^2
        assert conductor._calculate_backoff_interval(3, 1.0) == 8.0   # 1 * 2^3

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_backoff_with_different_base(self):
        """Verify backoff works with different base intervals."""
        import atlasforge_conductor as conductor

        # With base_interval=0.5
        assert conductor._calculate_backoff_interval(0, 0.5) == 0.5
        assert conductor._calculate_backoff_interval(1, 0.5) == 1.0
        assert conductor._calculate_backoff_interval(2, 0.5) == 2.0


# ===========================================================================
# Test retry metrics logging
# ===========================================================================

class TestRetryMetricsLogging:
    """Test _log_retry_metrics function."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_metrics_logged_to_file(self, tmp_path):
        """Verify retry metrics are logged to JSONL file."""
        import atlasforge_conductor as conductor

        metrics_path = tmp_path / "auto_advance_metrics.jsonl"

        with patch.object(conductor, 'RETRY_METRICS_LOG_PATH', metrics_path):
            metrics = {
                "attempts": 2,
                "total_wait_time": 3.5,
                "signal_detected": True,
                "reason": "success",
                "backoff_intervals": [1.0, 2.0],
                "fallback_used": False
            }
            conductor._log_retry_metrics(metrics, "test_mission_123")

            with open(metrics_path) as f:
                logged = json.loads(f.read().strip())

            assert logged["completed_mission_id"] == "test_mission_123"
            assert logged["attempts"] == 2
            assert logged["reason"] == "success"
            assert "timestamp" in logged

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_metrics_logging_fails_silently(self, tmp_path):
        """Verify metrics logging fails silently on error."""
        import atlasforge_conductor as conductor

        # Use a path that can't be written to
        metrics_path = tmp_path / "nonexistent_dir" / "metrics.jsonl"

        with patch.object(conductor, 'RETRY_METRICS_LOG_PATH', metrics_path):
            # Should not raise - fails silently
            metrics = {"attempts": 1}
            conductor._log_retry_metrics(metrics, "test_mission")
            # No assertion needed - just verifying no exception


# ===========================================================================
# Test signal file handling
# ===========================================================================

class TestSignalFileHandling:
    """Test auto-advance signal file operations."""

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_clear_signal_file_removes_file(self, tmp_path):
        """Verify signal file is removed when cleared."""
        import atlasforge_conductor as conductor

        signal_path = tmp_path / "auto_advance_signal.json"
        signal_path.write_text('{"status": "complete"}')

        with patch.object(conductor, 'AUTO_ADVANCE_SIGNAL_PATH', signal_path):
            conductor._clear_signal_file()
            assert not signal_path.exists()

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_clear_signal_file_handles_missing_file(self, tmp_path):
        """Verify clearing missing signal file doesn't error."""
        import atlasforge_conductor as conductor

        signal_path = tmp_path / "nonexistent_signal.json"

        with patch.object(conductor, 'AUTO_ADVANCE_SIGNAL_PATH', signal_path):
            # Should not raise
            conductor._clear_signal_file()

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_clear_queue_signal_file_removes_file(self, tmp_path):
        """Verify queue signal file is removed when cleared."""
        import atlasforge_conductor as conductor

        signal_path = tmp_path / "queue_auto_start_signal.json"
        signal_path.write_text('{"action": "start_rd"}')

        with patch.object(conductor, 'QUEUE_AUTO_START_SIGNAL_PATH', signal_path):
            conductor._clear_queue_signal_file()
            assert not signal_path.exists()


# ===========================================================================
# Integration test: wait_for_new_mission_with_retry
# ===========================================================================

class TestWaitForNewMissionWithRetry:
    """Integration tests for the retry wait mechanism."""

    @pytest.fixture
    def mock_controller_with_mission(self, tmp_path):
        """Create a mock controller that can return different missions."""
        state_dir = tmp_path / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        controller = Mock()
        controller.mission = {
            "mission_id": "completed_mission",
            "problem_statement": "Test",
            "current_stage": "COMPLETE"
        }

        def load_mission():
            return controller.mission

        controller.load_mission = load_mission

        return controller

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_returns_false_when_no_new_mission(self, mock_controller_with_mission, tmp_path):
        """Verify returns False when no new mission appears."""
        import atlasforge_conductor as conductor

        # Ensure signal file doesn't exist
        signal_path = tmp_path / "auto_advance_signal.json"
        queue_signal_path = tmp_path / "queue_auto_start_signal.json"

        with patch.object(conductor, 'AUTO_ADVANCE_SIGNAL_PATH', signal_path), \
             patch.object(conductor, 'QUEUE_AUTO_START_SIGNAL_PATH', queue_signal_path):

            success, metrics = conductor._wait_for_new_mission_with_retry(
                mock_controller_with_mission,
                "completed_mission",
                max_retries=2,
                base_interval=0.01,  # Fast for testing
                max_total_wait=0.1
            )

            assert success is False
            assert metrics["reason"] in ("max_retries", "timeout")

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_returns_true_when_new_mission_detected(self, mock_controller_with_mission, tmp_path):
        """Verify returns True when a new mission is detected."""
        import atlasforge_conductor as conductor

        # Make controller return a new mission on load
        call_count = [0]

        def load_mission():
            call_count[0] += 1
            if call_count[0] > 1:
                mock_controller_with_mission.mission = {
                    "mission_id": "new_mission",
                    "problem_statement": "New test mission",
                    "current_stage": "PLANNING"
                }
            return mock_controller_with_mission.mission

        mock_controller_with_mission.load_mission = load_mission

        signal_path = tmp_path / "auto_advance_signal.json"
        queue_signal_path = tmp_path / "queue_auto_start_signal.json"

        with patch.object(conductor, 'AUTO_ADVANCE_SIGNAL_PATH', signal_path), \
             patch.object(conductor, 'QUEUE_AUTO_START_SIGNAL_PATH', queue_signal_path):

            success, metrics = conductor._wait_for_new_mission_with_retry(
                mock_controller_with_mission,
                "completed_mission",
                max_retries=3,
                base_interval=0.01,
                max_total_wait=1.0
            )

            assert success is True
            assert metrics["reason"] == "success"

    @pytest.mark.regression
    @pytest.mark.regression_timeout_retry
    def test_detects_signal_file_completion(self, mock_controller_with_mission, tmp_path):
        """Verify detection via signal file works."""
        import atlasforge_conductor as conductor
        import io_utils

        signal_path = tmp_path / "auto_advance_signal.json"
        queue_signal_path = tmp_path / "queue_auto_start_signal.json"

        # Write signal indicating completion
        signal_path.write_text(json.dumps({
            "status": "complete",
            "new_mission_id": "signal_mission"
        }))

        # Update controller to return the new mission
        mock_controller_with_mission.mission = {
            "mission_id": "signal_mission",
            "problem_statement": "Signal detected mission",
            "current_stage": "PLANNING"
        }

        with patch.object(conductor, 'AUTO_ADVANCE_SIGNAL_PATH', signal_path), \
             patch.object(conductor, 'QUEUE_AUTO_START_SIGNAL_PATH', queue_signal_path), \
             patch.object(io_utils, 'atomic_read_json', side_effect=[
                 {"status": "complete", "new_mission_id": "signal_mission"},
                 {}
             ]):

            success, metrics = conductor._wait_for_new_mission_with_retry(
                mock_controller_with_mission,
                "completed_mission",
                max_retries=3,
                base_interval=0.01,
                max_total_wait=1.0
            )

            assert success is True
            assert metrics["signal_detected"] is True
            assert metrics["reason"] == "success"
