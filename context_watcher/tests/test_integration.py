#!/usr/bin/env python3
"""
Integration Tests for ContextWatcher

Tests the full pipeline including:
- Mock JSONL file generation
- Session monitoring
- Threshold detection
- Handoff signal generation
- HANDOFF.md writing
"""

import json
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from workspace.ContextWatcher.context_watcher import (
    ContextWatcher,
    TokenState,
    SessionMonitor,
    HandoffSignal,
    HandoffLevel,
    is_p_mode_session,
    write_handoff_state,
    count_handoffs,
    GRACEFUL_THRESHOLD,
    EMERGENCY_THRESHOLD,
    LOW_CACHE_READ_THRESHOLD,
    CLAUDE_PROJECTS_DIR
)


def create_mock_jsonl_entry(
    entry_type: str,
    cache_creation: int = 0,
    cache_read: int = 0,
    input_tokens: int = 100,
    request_id: Optional[str] = None
) -> str:
    """Create a mock JSONL entry matching Claude's compact JSON format."""
    # Use separators to produce compact JSON like Claude does (no spaces)
    if entry_type == "user":
        return json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "Test message"},
            "requestId": request_id or f"req_{int(time.time()*1000)}"
        }, separators=(',', ':'))
    elif entry_type == "assistant":
        return json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Response text"}],
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": cache_creation,
                    "output_tokens": 500
                }
            },
            "requestId": request_id or f"req_{int(time.time()*1000)}"
        }, separators=(',', ':'))
    elif entry_type == "progress":
        return json.dumps({
            "type": "progress",
            "message": "Processing..."
        }, separators=(',', ':'))
    else:
        return json.dumps({"type": entry_type}, separators=(',', ':'))


def test_mock_jsonl_parsing():
    """Test parsing mock JSONL entries."""
    print("\n[TEST] Mock JSONL Parsing")

    # Create assistant entry with usage
    entry = create_mock_jsonl_entry(
        "assistant",
        cache_creation=80000,
        cache_read=50000,
        input_tokens=100
    )

    record = json.loads(entry)
    usage = record["message"]["usage"]

    tokens = TokenState.from_usage(usage)

    assert tokens.cache_creation_input_tokens == 80000
    assert tokens.cache_read_input_tokens == 50000
    assert tokens.total_context == 130100

    print("   PASSED")


def test_session_classification_mock():
    """Test session classification with mock files."""
    print("\n[TEST] Session Classification (Mock)")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create -p mode style JSONL (no progress events)
        p_mode_file = Path(tmpdir) / "p_mode.jsonl"
        with open(p_mode_file, 'w') as f:
            f.write(create_mock_jsonl_entry("user") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=5000) + "\n")

        is_p = is_p_mode_session(p_mode_file)
        assert is_p, "Should detect as -p mode"
        print("   -p mode file: correctly identified")

        # Create interactive style JSONL (has progress events)
        interactive_file = Path(tmpdir) / "interactive.jsonl"
        with open(interactive_file, 'w') as f:
            f.write(create_mock_jsonl_entry("user") + "\n")
            f.write(create_mock_jsonl_entry("progress") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=5000) + "\n")

        is_p = is_p_mode_session(interactive_file)
        assert not is_p, "Should NOT detect as -p mode"
        print("   Interactive file: correctly identified")

    print("   PASSED")


def test_threshold_detection_with_callback():
    """Test that threshold detection triggers callback."""
    print("\n[TEST] Threshold Detection with Callback")

    callback_triggered = threading.Event()
    received_signal = [None]

    def on_handoff(signal: HandoffSignal):
        received_signal[0] = signal
        callback_triggered.set()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create mock transcript directory structure
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test-workspace"
        transcript_dir.mkdir(parents=True)

        # Create JSONL file with escalating tokens
        jsonl_file = transcript_dir / "session.jsonl"

        # Write initial entries (below threshold)
        with open(jsonl_file, 'w') as f:
            f.write(create_mock_jsonl_entry("user", request_id="req1") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=50000, cache_read=100, request_id="req1") + "\n")

        # Create session monitor with mocked transcript dir finder
        monitor = SessionMonitor("test-session", str(tmpdir), on_handoff)
        monitor.transcript_dir = transcript_dir

        # Process initial entries - should not trigger
        signal = monitor.process_updates()
        assert signal is None, "Should not trigger on low tokens"
        assert not callback_triggered.is_set()
        print("   Below threshold: no trigger")

        # Write entry that crosses graceful threshold
        with open(jsonl_file, 'a') as f:
            f.write(create_mock_jsonl_entry("user", request_id="req2") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=135000, cache_read=100, request_id="req2") + "\n")

        # Process - should trigger graceful
        signal = monitor.process_updates()

        assert signal is not None, "Should trigger graceful handoff"
        assert signal.level == HandoffLevel.GRACEFUL
        assert callback_triggered.is_set()
        print("   Graceful threshold: trigger received")

        # Verify signal contents
        assert received_signal[0] is not None
        assert received_signal[0].tokens_used > 0
        print(f"   Signal tokens: {received_signal[0].tokens_used:,}")

    print("   PASSED")


def test_emergency_threshold():
    """Test emergency threshold detection."""
    print("\n[TEST] Emergency Threshold Detection")

    received_signals = []

    def on_handoff(signal: HandoffSignal):
        received_signals.append(signal)

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Write entry that directly hits emergency threshold
        with open(jsonl_file, 'w') as f:
            f.write(create_mock_jsonl_entry("user", request_id="req1") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=145000, cache_read=100, request_id="req1") + "\n")

        monitor = SessionMonitor("test-session", str(tmpdir), on_handoff)
        monitor.transcript_dir = transcript_dir

        signal = monitor.process_updates()

        assert signal is not None
        assert signal.level == HandoffLevel.EMERGENCY, f"Expected emergency, got {signal.level}"
        print(f"   Emergency trigger at {signal.cache_creation:,} tokens")

    print("   PASSED")


def test_no_false_positive_high_cache_read():
    """Test that high cache_read prevents false positive."""
    print("\n[TEST] No False Positive with High cache_read")

    triggered = []

    def on_handoff(signal):
        triggered.append(signal)

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # High cache_creation BUT also high cache_read = cache is working
        with open(jsonl_file, 'w') as f:
            f.write(create_mock_jsonl_entry("user", request_id="req1") + "\n")
            f.write(create_mock_jsonl_entry("assistant", cache_creation=150000, cache_read=50000, request_id="req1") + "\n")

        monitor = SessionMonitor("test-session", str(tmpdir), on_handoff)
        monitor.transcript_dir = transcript_dir

        signal = monitor.process_updates()

        assert signal is None, "Should NOT trigger when cache_read is high"
        assert len(triggered) == 0
        print(f"   High cache_creation ({150000:,}) + high cache_read ({50000:,}): NO trigger")

    print("   PASSED")


def test_handoff_only_triggers_once():
    """Test that handoff only triggers once per session."""
    print("\n[TEST] Single Trigger per Session")

    trigger_count = [0]

    def on_handoff(signal):
        trigger_count[0] += 1

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        monitor = SessionMonitor("test-session", str(tmpdir), on_handoff)
        monitor.transcript_dir = transcript_dir

        # Write multiple entries that cross threshold
        with open(jsonl_file, 'w') as f:
            for i in range(5):
                f.write(create_mock_jsonl_entry("user", request_id=f"req{i}") + "\n")
                f.write(create_mock_jsonl_entry("assistant", cache_creation=135000+i*1000, cache_read=100, request_id=f"req{i}") + "\n")

        # Process all updates
        for _ in range(3):
            monitor.process_updates()

        assert trigger_count[0] == 1, f"Should trigger exactly once, got {trigger_count[0]}"
        print(f"   Trigger count: {trigger_count[0]} (expected: 1)")

    print("   PASSED")


def test_handoff_md_accumulation():
    """Test that HANDOFF.md properly accumulates entries."""
    print("\n[TEST] HANDOFF.md Accumulation")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write 3 handoff entries
        for i in range(3):
            success = write_handoff_state(
                tmpdir,
                f"mission_{i}",
                "BUILDING",
                f"Working on task {i}"
            )
            assert success, f"Write {i} failed"

        count = count_handoffs(tmpdir)
        assert count == 3, f"Expected 3 handoffs, got {count}"

        # Verify content integrity
        handoff_path = Path(tmpdir) / "HANDOFF.md"
        content = handoff_path.read_text()

        assert "Handoff #1" in content
        assert "Handoff #2" in content
        assert "Handoff #3" in content
        assert "mission_0" in content
        assert "mission_2" in content

        print(f"   Accumulated {count} handoff entries")

    print("   PASSED")


def test_stale_session_detection():
    """Test detection of stale sessions."""
    print("\n[TEST] Stale Session Detection")

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        monitor = SessionMonitor("test-session", str(tmpdir), lambda s: None)
        monitor.transcript_dir = transcript_dir

        # Initially not stale
        assert not monitor.is_stale(), "Should not be stale initially"

        # Force stale by backdating last_activity
        from datetime import timedelta
        monitor.last_activity = datetime.now() - timedelta(seconds=400)

        assert monitor.is_stale(), "Should be stale after timeout"
        print("   Stale detection working")

    print("   PASSED")


def test_real_jsonl_integration():
    """Test with actual JSONL files from Claude projects."""
    print("\n[TEST] Real JSONL Integration")

    if not CLAUDE_PROJECTS_DIR.exists():
        print("   SKIPPED (no Claude projects directory)")
        return

    # Find a recent -p mode session
    found_file = None
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        for jsonl in sorted(project_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:1]:
            if is_p_mode_session(jsonl):
                found_file = jsonl
                break
        if found_file:
            break

    if not found_file:
        print("   SKIPPED (no -p mode sessions found)")
        return

    print(f"   Testing with: {found_file.parent.name}/{found_file.name}")

    # Read and parse
    entry_count = 0
    assistant_count = 0
    max_cache_creation = 0

    with open(found_file, 'r') as f:
        for line in f:
            try:
                record = json.loads(line.strip())
                entry_count += 1

                if record.get('type') == 'assistant':
                    assistant_count += 1
                    usage = record.get('message', {}).get('usage', {})
                    cache_creation = usage.get('cache_creation_input_tokens', 0)
                    max_cache_creation = max(max_cache_creation, cache_creation)
            except:
                continue

    print(f"   Entries: {entry_count}, Assistant messages: {assistant_count}")
    print(f"   Max cache_creation: {max_cache_creation:,}")

    # Test would-trigger logic
    would_trigger = max_cache_creation >= GRACEFUL_THRESHOLD
    print(f"   Would trigger handoff: {would_trigger}")

    print("   PASSED")


def run_all_integration_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("ContextWatcher Integration Tests")
    print("=" * 60)

    try:
        test_mock_jsonl_parsing()
        test_session_classification_mock()
        test_threshold_detection_with_callback()
        test_emergency_threshold()
        test_no_false_positive_high_cache_read()
        test_handoff_only_triggers_once()
        test_handoff_md_accumulation()
        test_stale_session_detection()
        test_real_jsonl_integration()

        print("\n" + "=" * 60)
        print("ALL INTEGRATION TESTS PASSED")
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
    success = run_all_integration_tests()
    sys.exit(0 if success else 1)
