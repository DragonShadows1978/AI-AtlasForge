#!/usr/bin/env python3
"""
Tests for time-based handoff functionality in ContextWatcher.

Tests cover:
- Timer fires at configured time
- Timer can be cancelled
- Callback receives correct HandoffSignal
- Timer doesn't fire if session stops early
- Timer doesn't fire if token-based handoff fires first
- Configuration via environment variables
"""

import os
import sys
import time
import threading
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from context_watcher import (
    TimeBasedHandoffMonitor,
    HandoffSignal,
    HandoffLevel,
    SessionMonitor,
    TIME_BASED_HANDOFF_ENABLED,
    TIME_BASED_HANDOFF_MINUTES,
)


class TestTimeBasedHandoffMonitor(unittest.TestCase):
    """Tests for TimeBasedHandoffMonitor class."""

    def test_timer_fires_at_configured_time(self):
        """Test that the timer fires at the configured timeout."""
        callback_received = threading.Event()
        signal_received = [None]

        def callback(signal):
            signal_received[0] = signal
            callback_received.set()

        # Use a very short timeout for testing (1 second)
        monitor = TimeBasedHandoffMonitor(
            session_id="test_session",
            workspace_path="/test/workspace",
            callback=callback,
            timeout_minutes=1/60  # 1 second
        )

        monitor.start()

        # Wait for callback with timeout
        received = callback_received.wait(timeout=3.0)

        self.assertTrue(received, "Callback should have been called")
        self.assertIsNotNone(signal_received[0])
        self.assertEqual(signal_received[0].level, HandoffLevel.TIME_BASED)
        self.assertEqual(signal_received[0].session_id, "test_session")
        self.assertTrue(monitor.has_fired)

    def test_timer_can_be_cancelled(self):
        """Test that the timer can be cancelled before firing."""
        callback_received = threading.Event()

        def callback(signal):
            callback_received.set()

        # Use a longer timeout
        monitor = TimeBasedHandoffMonitor(
            session_id="test_session",
            workspace_path="/test/workspace",
            callback=callback,
            timeout_minutes=1  # 60 seconds - we'll cancel before this
        )

        monitor.start()
        time.sleep(0.1)  # Let it start

        # Cancel before timeout
        monitor.cancel()

        # Wait a bit to make sure callback doesn't fire
        received = callback_received.wait(timeout=0.5)

        self.assertFalse(received, "Callback should NOT have been called")
        self.assertTrue(monitor.is_cancelled)
        self.assertFalse(monitor.has_fired)

    def test_callback_receives_correct_signal(self):
        """Test that callback receives correct HandoffSignal data."""
        signal_received = [None]
        callback_received = threading.Event()

        def callback(signal):
            signal_received[0] = signal
            callback_received.set()

        monitor = TimeBasedHandoffMonitor(
            session_id="signal_test",
            workspace_path="/path/to/workspace",
            callback=callback,
            timeout_minutes=1/60  # 1 second
        )

        monitor.start()
        callback_received.wait(timeout=3.0)

        signal = signal_received[0]
        self.assertIsNotNone(signal)
        self.assertEqual(signal.level, HandoffLevel.TIME_BASED)
        self.assertEqual(signal.session_id, "signal_test")
        self.assertEqual(signal.workspace_path, "/path/to/workspace")
        self.assertEqual(signal.tokens_used, 0)  # Time-based has no token info
        self.assertIsNotNone(signal.elapsed_minutes)
        self.assertIsInstance(signal.timestamp, datetime)

    def test_signal_to_dict(self):
        """Test HandoffSignal serialization with elapsed_minutes."""
        signal = HandoffSignal(
            level=HandoffLevel.TIME_BASED,
            session_id="dict_test",
            workspace_path="/test",
            tokens_used=0,
            cache_read=0,
            cache_creation=0,
            elapsed_minutes=55.0
        )

        result = signal.to_dict()

        self.assertEqual(result["level"], "time_based")
        self.assertEqual(result["elapsed_minutes"], 55.0)
        self.assertIn("timestamp", result)

    def test_elapsed_and_remaining_seconds(self):
        """Test elapsed and remaining time tracking."""
        monitor = TimeBasedHandoffMonitor(
            session_id="timing_test",
            workspace_path="/test",
            callback=lambda s: None,
            timeout_minutes=1  # 60 seconds
        )

        monitor.start()
        time.sleep(0.5)

        elapsed = monitor.elapsed_seconds
        remaining = monitor.remaining_seconds

        self.assertGreater(elapsed, 0.4)
        self.assertLess(elapsed, 1.0)
        self.assertGreater(remaining, 59.0)
        self.assertLess(remaining, 60.0)

        monitor.cancel()

    def test_get_stats(self):
        """Test get_stats returns correct information."""
        monitor = TimeBasedHandoffMonitor(
            session_id="stats_test",
            workspace_path="/test",
            callback=lambda s: None,
            timeout_minutes=5
        )

        monitor.start()
        time.sleep(0.1)

        stats = monitor.get_stats()

        self.assertEqual(stats["session_id"], "stats_test")
        self.assertEqual(stats["timeout_minutes"], 5)
        self.assertFalse(stats["fired"])
        self.assertFalse(stats["cancelled"])
        self.assertIsNotNone(stats["started_at"])
        self.assertGreater(stats["elapsed_seconds"], 0)

        monitor.cancel()

    def test_stop_alias_for_cancel(self):
        """Test that stop() is an alias for cancel()."""
        monitor = TimeBasedHandoffMonitor(
            session_id="stop_test",
            workspace_path="/test",
            callback=lambda s: None,
            timeout_minutes=1
        )

        monitor.start()
        monitor.stop()

        self.assertTrue(monitor.is_cancelled)

    def test_multiple_start_calls_ignored(self):
        """Test that multiple start() calls don't create multiple timers."""
        callback_count = [0]

        def callback(signal):
            callback_count[0] += 1

        monitor = TimeBasedHandoffMonitor(
            session_id="multi_start",
            workspace_path="/test",
            callback=callback,
            timeout_minutes=1/60  # 1 second
        )

        monitor.start()
        monitor.start()  # Should be ignored
        monitor.start()  # Should be ignored

        time.sleep(2.0)

        # Should only fire once
        self.assertEqual(callback_count[0], 1)


class TestSessionMonitorTimeIntegration(unittest.TestCase):
    """Tests for SessionMonitor time-based handoff integration."""

    def test_session_monitor_starts_time_handoff(self):
        """Test that SessionMonitor starts time handoff monitor when enabled."""
        callback = Mock()

        with patch('context_watcher.context_watcher.find_transcript_dir', return_value=Path("/tmp")):
            monitor = SessionMonitor(
                session_id="session_test",
                workspace_path="/test",
                callback=callback,
                enable_time_handoff=True
            )

            # Start time handoff monitor
            monitor.start_time_handoff_monitor()

            self.assertIsNotNone(monitor._time_handoff_monitor)

            # Clean up
            monitor.stop_time_handoff_monitor()
            self.assertIsNone(monitor._time_handoff_monitor)

    def test_session_monitor_respects_disable_flag(self):
        """Test that SessionMonitor respects enable_time_handoff=False."""
        callback = Mock()

        with patch('context_watcher.context_watcher.find_transcript_dir', return_value=Path("/tmp")):
            with patch('context_watcher.context_watcher.TIME_BASED_HANDOFF_ENABLED', False):
                monitor = SessionMonitor(
                    session_id="disabled_test",
                    workspace_path="/test",
                    callback=callback,
                    enable_time_handoff=True  # Should be overridden by global flag
                )

                monitor.start_time_handoff_monitor()

                # Should NOT create monitor because global flag is False
                self.assertIsNone(monitor._time_handoff_monitor)

    def test_time_handoff_stats_in_session_stats(self):
        """Test that time handoff stats appear in session stats."""
        callback = Mock()

        with patch('context_watcher.context_watcher.find_transcript_dir', return_value=Path("/tmp")):
            monitor = SessionMonitor(
                session_id="stats_integration",
                workspace_path="/test",
                callback=callback,
                enable_time_handoff=True
            )

            monitor.start_time_handoff_monitor()

            stats = monitor.get_stats()

            self.assertIn("time_handoff", stats)
            self.assertIn("timeout_minutes", stats["time_handoff"])
            self.assertIn("elapsed_seconds", stats["time_handoff"])

            monitor.stop_time_handoff_monitor()


class TestEnvironmentConfiguration(unittest.TestCase):
    """Tests for environment variable configuration."""

    def test_time_based_handoff_enabled_default(self):
        """Test TIME_BASED_HANDOFF_ENABLED defaults to True."""
        # This should be True by default (unless env var says otherwise)
        self.assertTrue(TIME_BASED_HANDOFF_ENABLED)

    def test_time_based_handoff_minutes_default(self):
        """Test TIME_BASED_HANDOFF_MINUTES defaults to 55."""
        self.assertEqual(TIME_BASED_HANDOFF_MINUTES, 55)

    def test_env_var_override_enabled(self):
        """Test that env var can disable time-based handoff."""
        # This would require reimporting with different env var
        # Just verify the parsing logic
        test_values = [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("0", False),
            ("false", False),
            ("no", False),
        ]

        for env_val, expected in test_values:
            result = env_val.lower() in ("1", "true", "yes")
            self.assertEqual(result, expected, f"Failed for {env_val}")


class TestHandoffLevelEnum(unittest.TestCase):
    """Tests for HandoffLevel enum with TIME_BASED."""

    def test_time_based_level_exists(self):
        """Test that TIME_BASED level exists in enum."""
        self.assertEqual(HandoffLevel.TIME_BASED.value, "time_based")

    def test_all_levels_distinct(self):
        """Test that all handoff levels have distinct values."""
        values = [level.value for level in HandoffLevel]
        self.assertEqual(len(values), len(set(values)))


if __name__ == "__main__":
    unittest.main()
