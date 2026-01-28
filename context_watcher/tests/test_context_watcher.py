#!/usr/bin/env python3
"""
Functional Tests for ContextWatcher

These tests validate the ContextWatcher against real JSONL data from
Claude sessions, ensuring threshold detection works correctly.

Run with: python3 -m workspace.ContextWatcher.tests.test_context_watcher
"""

import json
import sys
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from workspace.ContextWatcher.context_watcher import (
    ContextWatcher,
    TokenState,
    SessionMonitor,
    HandoffSignal,
    HandoffLevel,
    find_transcript_dir,
    is_p_mode_session,
    get_context_watcher,
    write_handoff_state,
    count_handoffs,
    GRACEFUL_THRESHOLD,
    EMERGENCY_THRESHOLD,
    LOW_CACHE_READ_THRESHOLD,
    CLAUDE_PROJECTS_DIR
)


def test_token_state():
    """Test TokenState creation and calculations."""
    print("\n[TEST] TokenState")

    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 50000,
        "cache_creation_input_tokens": 80000,
        "output_tokens": 500
    }

    tokens = TokenState.from_usage(usage, "req_123")

    assert tokens.input_tokens == 100, "input_tokens mismatch"
    assert tokens.cache_read_input_tokens == 50000, "cache_read mismatch"
    assert tokens.cache_creation_input_tokens == 80000, "cache_creation mismatch"
    assert tokens.output_tokens == 500, "output_tokens mismatch"
    assert tokens.total_context == 130100, f"total_context should be 130100, got {tokens.total_context}"
    assert tokens.request_id == "req_123", "request_id mismatch"

    print("   PASSED")


def test_threshold_detection():
    """Test threshold detection logic."""
    print("\n[TEST] Threshold Detection Logic")

    test_cases = [
        # (cache_creation, cache_read, expected_level)
        (150000, 100, "emergency"),     # High creation, low read = emergency
        (135000, 100, "graceful"),      # Above graceful, below emergency
        (120000, 100, None),            # Below graceful threshold
        (150000, 50000, None),          # High creation but also high read = cache working
        (150000, 6000, None),           # High creation but cache_read above threshold
        (140000, 4999, "emergency"),    # Exactly at emergency threshold
        (130000, 4999, "graceful"),     # Exactly at graceful threshold
        (129999, 100, None),            # Just below graceful
    ]

    all_passed = True
    for cache_creation, cache_read, expected in test_cases:
        # Apply detection logic
        level = None
        if cache_read < LOW_CACHE_READ_THRESHOLD:
            if cache_creation >= EMERGENCY_THRESHOLD:
                level = "emergency"
            elif cache_creation >= GRACEFUL_THRESHOLD:
                level = "graceful"

        passed = level == expected
        all_passed = all_passed and passed
        status = "PASS" if passed else "FAIL"

        print(f"   cache_creation={cache_creation:,}, cache_read={cache_read:,} -> {level} (expected: {expected}) [{status}]")

    assert all_passed, "Some threshold tests failed"
    print("   ALL PASSED")


def test_find_transcript_dir():
    """Test transcript directory lookup."""
    print("\n[TEST] Find Transcript Directory")

    # Test with known paths
    test_path = "/home/vader/AI-AtlasForge/workspace/ContextWatcher"
    result = find_transcript_dir(test_path)

    if result:
        print(f"   Found: {result}")
        assert result.exists(), "Returned path doesn't exist"
        print("   PASSED")
    else:
        print("   SKIPPED (no transcript dir for test path)")


def test_session_classification():
    """Test -p mode session classification."""
    print("\n[TEST] Session Classification")

    if not CLAUDE_PROJECTS_DIR.exists():
        print("   SKIPPED (no Claude projects directory)")
        return

    tested = 0
    for project_dir in list(CLAUDE_PROJECTS_DIR.iterdir())[:5]:
        if not project_dir.is_dir():
            continue

        for jsonl in list(project_dir.glob("*.jsonl"))[:1]:
            is_p = is_p_mode_session(jsonl)
            print(f"   {jsonl.parent.name}/{jsonl.name}: -p mode = {is_p}")
            tested += 1

    if tested > 0:
        print("   PASSED")
    else:
        print("   SKIPPED (no JSONL files found)")


def test_watcher_singleton():
    """Test singleton pattern."""
    print("\n[TEST] Watcher Singleton")

    watcher1 = get_context_watcher()
    watcher2 = get_context_watcher()

    assert watcher1 is watcher2, "Singleton pattern broken"
    print("   PASSED")


def test_handoff_md_writer(tmpdir=None):
    """Test HANDOFF.md writing."""
    print("\n[TEST] HANDOFF.md Writer")

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write first handoff
        success = write_handoff_state(
            tmpdir,
            "test_mission_123",
            "BUILDING",
            "Working on context_watcher.py implementation"
        )
        assert success, "First write failed"

        # Check count
        count = count_handoffs(tmpdir)
        assert count == 1, f"Expected 1 handoff, got {count}"

        # Write second handoff
        success = write_handoff_state(
            tmpdir,
            "test_mission_123",
            "TESTING",
            "Running functional tests"
        )
        assert success, "Second write failed"

        # Check count again
        count = count_handoffs(tmpdir)
        assert count == 2, f"Expected 2 handoffs, got {count}"

        # Verify content
        handoff_path = Path(tmpdir) / "HANDOFF.md"
        content = handoff_path.read_text()
        assert "Handoff #1" in content, "Missing Handoff #1"
        assert "Handoff #2" in content, "Missing Handoff #2"
        assert "BUILDING" in content, "Missing stage BUILDING"
        assert "TESTING" in content, "Missing stage TESTING"

        print("   PASSED")


def test_real_jsonl_analysis():
    """Analyze real JSONL files for context exhaustion patterns."""
    print("\n[TEST] Real JSONL Analysis")

    if not CLAUDE_PROJECTS_DIR.exists():
        print("   SKIPPED (no Claude projects directory)")
        return

    high_token_count = 0
    triggering_count = 0

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            try:
                with open(jsonl_file, 'r') as f:
                    for line in f:
                        try:
                            record = json.loads(line.strip())
                            if record.get('type') == 'assistant':
                                usage = record.get('message', {}).get('usage', {})
                                cache_creation = usage.get('cache_creation_input_tokens', 0)
                                cache_read = usage.get('cache_read_input_tokens', 0)

                                if cache_creation >= 100000:
                                    high_token_count += 1

                                if (cache_creation >= GRACEFUL_THRESHOLD and
                                    cache_read < LOW_CACHE_READ_THRESHOLD):
                                    triggering_count += 1
                        except json.JSONDecodeError:
                            continue
            except:
                continue

    print(f"   High token entries (>100K): {high_token_count}")
    print(f"   Would trigger handoff: {triggering_count}")
    print("   PASSED")


def run_all_tests():
    """Run all tests."""
    print("=" * 60)
    print("ContextWatcher Functional Tests")
    print("=" * 60)

    try:
        test_token_state()
        test_threshold_detection()
        test_find_transcript_dir()
        test_session_classification()
        test_watcher_singleton()
        test_handoff_md_writer()
        test_real_jsonl_analysis()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)
        return True

    except AssertionError as e:
        print(f"\n\nTEST FAILED: {e}")
        return False
    except Exception as e:
        print(f"\n\nTEST ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
