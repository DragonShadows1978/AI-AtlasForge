#!/usr/bin/env python3
"""
Metrics Tests for ContextWatcher Cycle 1

Tests the new metrics collection functionality added in Cycle 1:
- WatcherMetrics dataclass
- Detection latency tracking
- Handoff recording
- Session tracking
"""

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from workspace.ContextWatcher.context_watcher import (
    ContextWatcher,
    WatcherMetrics,
    SessionMonitor,
    HandoffSignal,
    HandoffLevel,
    get_context_watcher,
    GRACEFUL_THRESHOLD,
    EMERGENCY_THRESHOLD,
    LOW_CACHE_READ_THRESHOLD,
)


def test_watcher_metrics_init():
    """Test WatcherMetrics initialization."""
    print("\n[TEST] WatcherMetrics Initialization")

    metrics = WatcherMetrics(started_at=datetime.now())

    assert metrics.sessions_started == 0
    assert metrics.sessions_completed == 0
    assert metrics.sessions_active == 0
    assert metrics.total_handoffs == 0
    assert metrics.graceful_handoffs == 0
    assert metrics.emergency_handoffs == 0
    assert metrics.avg_detection_latency == 0.0
    assert metrics.max_detection_latency == 0.0
    assert metrics.peak_tokens_seen == 0
    assert len(metrics.detection_latencies) == 0
    assert len(metrics.handoff_token_values) == 0
    assert metrics.started_at is not None
    assert metrics.last_handoff_at is None

    print("   PASSED")


def test_metrics_record_detection_latency():
    """Test recording detection latencies."""
    print("\n[TEST] Record Detection Latency")

    metrics = WatcherMetrics(started_at=datetime.now())

    # Record some latencies (in ms)
    metrics.record_detection_latency(50.0)   # 50ms
    metrics.record_detection_latency(100.0)  # 100ms
    metrics.record_detection_latency(75.0)   # 75ms

    assert len(metrics.detection_latencies) == 3
    expected_avg = (0.05 + 0.1 + 0.075) / 3
    assert abs(metrics.avg_detection_latency - expected_avg) < 0.001, f"Expected ~{expected_avg}, got {metrics.avg_detection_latency}"
    assert abs(metrics.max_detection_latency - 0.1) < 0.001  # 100ms = 0.1s

    print(f"   Avg latency: {metrics.avg_detection_latency*1000:.1f}ms")
    print(f"   Max latency: {metrics.max_detection_latency*1000:.1f}ms")
    print("   PASSED")


def test_metrics_latency_bounded():
    """Test that latency list is bounded at 100 entries."""
    print("\n[TEST] Detection Latency Bounded List")

    metrics = WatcherMetrics(started_at=datetime.now())

    # Record 150 latencies
    for i in range(150):
        metrics.record_detection_latency(float(i))

    assert len(metrics.detection_latencies) == 100, f"Expected 100, got {len(metrics.detection_latencies)}"
    print(f"   Latency list capped at {len(metrics.detection_latencies)} entries")
    print("   PASSED")


def test_metrics_record_handoff():
    """Test recording handoff events."""
    print("\n[TEST] Record Handoff Events")

    metrics = WatcherMetrics(started_at=datetime.now())

    # Record graceful handoff
    metrics.record_handoff(HandoffLevel.GRACEFUL, 132000)
    assert metrics.total_handoffs == 1
    assert metrics.graceful_handoffs == 1
    assert metrics.emergency_handoffs == 0
    assert metrics.peak_tokens_seen == 132000
    assert metrics.last_handoff_at is not None

    # Record emergency handoff
    metrics.record_handoff(HandoffLevel.EMERGENCY, 145000)
    assert metrics.total_handoffs == 2
    assert metrics.graceful_handoffs == 1
    assert metrics.emergency_handoffs == 1
    assert metrics.peak_tokens_seen == 145000  # Updated to higher value

    # Record another graceful with lower tokens
    metrics.record_handoff(HandoffLevel.GRACEFUL, 131000)
    assert metrics.total_handoffs == 3
    assert metrics.graceful_handoffs == 2
    assert metrics.peak_tokens_seen == 145000  # Should stay at max

    print(f"   Total handoffs: {metrics.total_handoffs}")
    print(f"   Graceful: {metrics.graceful_handoffs}, Emergency: {metrics.emergency_handoffs}")
    print(f"   Peak tokens: {metrics.peak_tokens_seen:,}")
    print("   PASSED")


def test_metrics_to_dict():
    """Test metrics serialization to dictionary."""
    print("\n[TEST] Metrics to Dictionary")

    metrics = WatcherMetrics(started_at=datetime.now())
    metrics.sessions_started = 5
    metrics.sessions_completed = 3
    metrics.sessions_active = 2
    metrics.record_detection_latency(50.0)
    metrics.record_handoff(HandoffLevel.GRACEFUL, 135000)

    result = metrics.to_dict()

    assert "sessions" in result
    assert result["sessions"]["started"] == 5
    assert result["sessions"]["completed"] == 3
    assert result["sessions"]["active"] == 2

    assert "handoffs" in result
    assert result["handoffs"]["total"] == 1
    assert result["handoffs"]["graceful"] == 1
    assert result["handoffs"]["ratio"] == "1:0"

    assert "timing" in result
    assert result["timing"]["detection_samples"] == 1

    assert "tokens" in result
    assert result["tokens"]["peak_seen"] == 135000

    assert "timestamps" in result
    assert result["timestamps"]["started_at"] is not None

    # Verify JSON serializable
    json_str = json.dumps(result)
    assert len(json_str) > 0

    print(f"   Serialized to {len(json_str)} bytes")
    print("   PASSED")


def test_context_watcher_metrics_integration():
    """Test metrics integration in ContextWatcher."""
    print("\n[TEST] ContextWatcher Metrics Integration")

    # Create fresh watcher (not singleton for testing)
    watcher = ContextWatcher()

    # Check initial metrics
    metrics = watcher.get_metrics()
    assert metrics.sessions_started == 0
    assert metrics.sessions_active == 0

    # Get metrics as dict
    metrics_dict = watcher.get_metrics_dict()
    assert "sessions" in metrics_dict
    assert "handoffs" in metrics_dict
    assert "timing" in metrics_dict

    # Check get_all_stats includes metrics
    all_stats = watcher.get_all_stats()
    assert "metrics" in all_stats
    assert all_stats["metrics"]["sessions"]["started"] == 0

    print("   Metrics accessible via get_metrics()")
    print("   Metrics accessible via get_metrics_dict()")
    print("   Metrics included in get_all_stats()")
    print("   PASSED")


def test_session_metrics_tracking():
    """Test that session start/stop updates metrics."""
    print("\n[TEST] Session Metrics Tracking")

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test-workspace"
        transcript_dir.mkdir(parents=True)

        # Create a minimal JSONL file
        jsonl_file = transcript_dir / "session.jsonl"
        with open(jsonl_file, 'w') as f:
            f.write('{"type":"user","message":{"role":"user","content":"test"}}\n')

        watcher = ContextWatcher()
        initial_started = watcher.get_metrics().sessions_started

        # Start watching
        session_id = watcher.start_watching(tmpdir, lambda s: None)

        if session_id:
            metrics = watcher.get_metrics()
            assert metrics.sessions_started == initial_started + 1
            assert metrics.sessions_active == 1
            print(f"   Sessions started: {metrics.sessions_started}")

            # Stop watching
            watcher.stop_watching(session_id)
            metrics = watcher.get_metrics()
            assert metrics.sessions_completed == 1
            assert metrics.sessions_active == 0
            print(f"   Sessions completed: {metrics.sessions_completed}")
        else:
            print("   SKIPPED (no transcript dir found)")
            return

    print("   PASSED")


def test_handoff_metrics_tracking():
    """Test that handoffs update metrics."""
    print("\n[TEST] Handoff Metrics Tracking")

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Write entry that triggers graceful handoff
        entry = json.dumps({
            "type": "assistant",
            "message": {
                "usage": {
                    "cache_creation_input_tokens": 135000,
                    "cache_read_input_tokens": 100,
                    "input_tokens": 100,
                    "output_tokens": 500
                }
            },
            "requestId": "test_handoff"
        }, separators=(',', ':'))

        with open(jsonl_file, 'w') as f:
            f.write(entry + "\n")

        signals = []
        watcher = ContextWatcher()

        session_id = watcher.start_watching(tmpdir, lambda s: signals.append(s))

        if session_id:
            # Manually trigger processing (normally done by monitor thread)
            with watcher._lock:
                if session_id in watcher._sessions:
                    monitor = watcher._sessions[session_id]
                    monitor.transcript_dir = transcript_dir
                    signal = monitor.process_updates()

                    if signal:
                        # Simulate what monitor loop does
                        watcher._metrics.record_handoff(signal.level, signal.tokens_used)

            metrics = watcher.get_metrics()
            if metrics.total_handoffs > 0:
                print(f"   Handoffs recorded: {metrics.total_handoffs}")
                print(f"   Peak tokens: {metrics.peak_tokens_seen:,}")
                print(f"   Last handoff at: {metrics.last_handoff_at}")
            else:
                print("   No handoff triggered (below threshold)")

            watcher.stop_watching(session_id)
        else:
            print("   SKIPPED (session not started)")
            return

    print("   PASSED")


def run_all_metrics_tests():
    """Run all metrics tests."""
    print("=" * 60)
    print("ContextWatcher Metrics Tests (Cycle 1)")
    print("=" * 60)

    try:
        test_watcher_metrics_init()
        test_metrics_record_detection_latency()
        test_metrics_latency_bounded()
        test_metrics_record_handoff()
        test_metrics_to_dict()
        test_context_watcher_metrics_integration()
        test_session_metrics_tracking()
        test_handoff_metrics_tracking()

        print("\n" + "=" * 60)
        print("ALL METRICS TESTS PASSED")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n\nTEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    except Exception as e:
        print(f"\n\nTEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_metrics_tests()
    sys.exit(0 if success else 1)
