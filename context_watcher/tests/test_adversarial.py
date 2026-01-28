#!/usr/bin/env python3
"""
Adversarial Tests for ContextWatcher

This module attempts to BREAK the ContextWatcher implementation through:
1. Edge case injection
2. Boundary condition testing
3. Race condition simulation
4. Malformed input handling
5. Resource exhaustion scenarios

The goal is epistemic rigor: tests designed by the same person
who wrote the code tend to pass. These tests try to find bugs
the author didn't think of.
"""

import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict
import random
import string

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from context_watcher.context_watcher import (
    ContextWatcher,
    TokenState,
    SessionMonitor,
    HandoffSignal,
    HandoffLevel,
    is_p_mode_session,
    write_handoff_state,
    count_handoffs,
    find_transcript_dir,
    get_context_watcher,
    GRACEFUL_THRESHOLD,
    EMERGENCY_THRESHOLD,
    LOW_CACHE_READ_THRESHOLD,
    EARLY_FAILURE_THRESHOLD,
    STALE_SESSION_TIMEOUT,
)


# =============================================================================
# EDGE CASE GENERATORS
# =============================================================================

def generate_malformed_json_lines() -> List[str]:
    """Generate various malformed JSON lines to test parser resilience."""
    return [
        "",  # Empty line
        "   ",  # Whitespace only
        "{",  # Incomplete JSON
        '{"type":',  # Truncated
        '{"type":"assistant"',  # Missing closing brace
        '{"type":"assistant","message":{"usage":{}}}',  # Empty usage
        'not json at all',  # Plain text
        '{"type":"assistant","message":{"usage":null}}',  # Null usage
        '{"type":"assistant","message":"not an object"}',  # Wrong message type
        '["array", "not", "object"]',  # Array instead of object
        '{"type":"assistant","message":{"usage":{"cache_read_input_tokens":"not a number"}}}',  # Wrong type
        '{"type":"assistant","message":{"usage":{"cache_read_input_tokens":-1}}}',  # Negative tokens
        '{"type":"assistant","message":{"usage":{"cache_read_input_tokens":9999999999999999999999}}}',  # Overflow
    ]


def generate_boundary_token_values() -> List[Dict[str, int]]:
    """Generate token values at exact boundaries."""
    return [
        # Exactly at thresholds
        {"cache_creation": GRACEFUL_THRESHOLD, "cache_read": LOW_CACHE_READ_THRESHOLD - 1},
        {"cache_creation": GRACEFUL_THRESHOLD - 1, "cache_read": LOW_CACHE_READ_THRESHOLD - 1},
        {"cache_creation": EMERGENCY_THRESHOLD, "cache_read": LOW_CACHE_READ_THRESHOLD - 1},
        {"cache_creation": EMERGENCY_THRESHOLD - 1, "cache_read": LOW_CACHE_READ_THRESHOLD - 1},

        # Exactly at cache_read boundary
        {"cache_creation": 135000, "cache_read": LOW_CACHE_READ_THRESHOLD},
        {"cache_creation": 135000, "cache_read": LOW_CACHE_READ_THRESHOLD + 1},
        {"cache_creation": 135000, "cache_read": LOW_CACHE_READ_THRESHOLD - 1},

        # Zero values
        {"cache_creation": 0, "cache_read": 0},
        {"cache_creation": GRACEFUL_THRESHOLD, "cache_read": 0},

        # Very large values (but valid)
        {"cache_creation": 200_000, "cache_read": 100},
        {"cache_creation": 155_000, "cache_read": 0},  # Known max context

        # Early failure pattern
        {"cache_creation": EARLY_FAILURE_THRESHOLD - 1, "cache_read": 0},
    ]


# =============================================================================
# ADVERSARIAL TESTS
# =============================================================================

def test_malformed_jsonl_handling():
    """Test that malformed JSONL lines don't crash the parser."""
    print("\n[ADVERSARIAL] Malformed JSONL Handling")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Write malformed lines followed by valid line
        malformed_lines = generate_malformed_json_lines()

        with open(jsonl_file, 'w') as f:
            for line in malformed_lines:
                f.write(line + "\n")

            # Add one valid entry at the end
            valid_entry = json.dumps({
                "type": "assistant",
                "message": {
                    "usage": {
                        "cache_creation_input_tokens": 50000,
                        "cache_read_input_tokens": 100,
                        "input_tokens": 100,
                        "output_tokens": 500
                    }
                },
                "requestId": "valid_req"
            }, separators=(',', ':'))
            f.write(valid_entry + "\n")

        signals = []
        monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir

        try:
            signal = monitor.process_updates()
            print(f"   Processed {len(malformed_lines)} malformed + 1 valid line")
            print(f"   Last tokens: {monitor.last_tokens}")
            if monitor.last_tokens is None:
                failures.append("Valid entry was not parsed after malformed lines")
        except Exception as e:
            failures.append(f"Parser crashed on malformed input: {e}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   PASSED")
    return True


def test_boundary_conditions():
    """Test exact boundary conditions for threshold detection."""
    print("\n[ADVERSARIAL] Boundary Condition Testing")

    failures = []
    boundaries = generate_boundary_token_values()

    for i, boundary in enumerate(boundaries):
        cache_creation = boundary["cache_creation"]
        cache_read = boundary["cache_read"]

        # Calculate expected result
        expected_level = None
        if cache_read < LOW_CACHE_READ_THRESHOLD:
            if cache_creation >= EMERGENCY_THRESHOLD:
                expected_level = "emergency"
            elif cache_creation >= GRACEFUL_THRESHOLD:
                expected_level = "graceful"

        # Test
        with tempfile.TemporaryDirectory() as tmpdir:
            transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
            transcript_dir.mkdir(parents=True)

            jsonl_file = transcript_dir / "session.jsonl"

            entry = json.dumps({
                "type": "assistant",
                "message": {
                    "usage": {
                        "cache_creation_input_tokens": cache_creation,
                        "cache_read_input_tokens": cache_read,
                        "input_tokens": 100,
                        "output_tokens": 500
                    }
                },
                "requestId": f"req_{i}"
            }, separators=(',', ':'))

            with open(jsonl_file, 'w') as f:
                f.write(entry + "\n")

            signals = []
            monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
            monitor.transcript_dir = transcript_dir

            signal = monitor.process_updates()

            actual_level = signal.level.value if signal else None

            if actual_level != expected_level:
                failures.append(
                    f"cache_creation={cache_creation}, cache_read={cache_read}: "
                    f"expected {expected_level}, got {actual_level}"
                )

    if failures:
        print(f"   FAILED on {len(failures)} cases:")
        for f in failures[:5]:  # Show first 5
            print(f"     - {f}")
        return False

    print(f"   Tested {len(boundaries)} boundary conditions")
    print("   PASSED")
    return True


def test_race_condition_simulation():
    """Simulate race conditions in multi-threaded access."""
    print("\n[ADVERSARIAL] Race Condition Simulation")

    failures = []
    watcher = ContextWatcher()

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Pre-create JSONL with valid content
        entry = json.dumps({
            "type": "assistant",
            "message": {
                "usage": {
                    "cache_creation_input_tokens": 50000,
                    "cache_read_input_tokens": 100,
                    "input_tokens": 100,
                    "output_tokens": 500
                }
            },
            "requestId": "initial"
        }, separators=(',', ':'))

        with open(jsonl_file, 'w') as f:
            f.write(entry + "\n")

        signals = []
        errors = []
        lock = threading.Lock()

        def on_handoff(signal):
            with lock:
                signals.append(signal)

        def writer_thread():
            """Rapidly append to JSONL file."""
            for i in range(50):
                try:
                    entry = json.dumps({
                        "type": "assistant",
                        "message": {
                            "usage": {
                                "cache_creation_input_tokens": 50000 + i * 1000,
                                "cache_read_input_tokens": 100,
                                "input_tokens": 100,
                                "output_tokens": 500
                            }
                        },
                        "requestId": f"write_{i}"
                    }, separators=(',', ':'))

                    with open(jsonl_file, 'a') as f:
                        f.write(entry + "\n")

                    time.sleep(0.01)
                except Exception as e:
                    with lock:
                        errors.append(f"Writer error: {e}")

        def reader_thread(monitor):
            """Rapidly read and process updates."""
            for _ in range(50):
                try:
                    monitor.process_updates()
                    time.sleep(0.01)
                except Exception as e:
                    with lock:
                        errors.append(f"Reader error: {e}")

        # Create monitor
        monitor = SessionMonitor("race-test", tmpdir, on_handoff)
        monitor.transcript_dir = transcript_dir

        # Start concurrent threads
        writer = threading.Thread(target=writer_thread)
        reader = threading.Thread(target=reader_thread, args=(monitor,))

        writer.start()
        reader.start()

        writer.join(timeout=5)
        reader.join(timeout=5)

        if errors:
            failures.extend(errors[:5])

    if failures:
        print(f"   FAILED with errors:")
        for f in failures:
            print(f"     - {f}")
        return False

    print(f"   Concurrent access: {len(signals)} signals, no crashes")
    print("   PASSED")
    return True


def test_file_disappears_mid_read():
    """Test behavior when JSONL file is deleted during read."""
    print("\n[ADVERSARIAL] File Disappears Mid-Read")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Create file
        entry = json.dumps({
            "type": "assistant",
            "message": {
                "usage": {"cache_creation_input_tokens": 50000}
            },
            "requestId": "test"
        }, separators=(',', ':'))

        with open(jsonl_file, 'w') as f:
            f.write(entry + "\n")

        signals = []
        monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir

        # First read
        monitor.process_updates()

        # Delete the file
        jsonl_file.unlink()

        # Try to read again - should not crash
        try:
            monitor.process_updates()
            print("   File deletion handled gracefully")
        except Exception as e:
            failures.append(f"Crashed on deleted file: {e}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   PASSED")
    return True


def test_extremely_long_jsonl_line():
    """Test handling of extremely long JSON lines."""
    print("\n[ADVERSARIAL] Extremely Long JSONL Line")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Create entry with very long content (1MB)
        long_content = "x" * (1024 * 1024)

        entry = json.dumps({
            "type": "assistant",
            "message": {
                "content": [{"type": "text", "text": long_content}],
                "usage": {
                    "cache_creation_input_tokens": 50000,
                    "cache_read_input_tokens": 100,
                    "input_tokens": 100,
                    "output_tokens": 500
                }
            },
            "requestId": "long_entry"
        }, separators=(',', ':'))

        with open(jsonl_file, 'w') as f:
            f.write(entry + "\n")

        signals = []
        monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir

        try:
            monitor.process_updates()
            print(f"   Handled 1MB line, tokens: {monitor.last_tokens.total_context if monitor.last_tokens else 'None'}")
        except Exception as e:
            failures.append(f"Failed on long line: {e}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   PASSED")
    return True


def test_rapid_threshold_oscillation():
    """Test rapid oscillation around thresholds."""
    print("\n[ADVERSARIAL] Rapid Threshold Oscillation")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Create entries that oscillate around graceful threshold
        entries = []
        for i in range(20):
            # Alternate above/below threshold
            cache_creation = GRACEFUL_THRESHOLD + (1000 if i % 2 == 0 else -1000)
            entries.append({
                "type": "assistant",
                "message": {
                    "usage": {
                        "cache_creation_input_tokens": cache_creation,
                        "cache_read_input_tokens": 100,  # Low, so thresholds apply
                    }
                },
                "requestId": f"osc_{i}"
            })

        with open(jsonl_file, 'w') as f:
            for entry in entries:
                f.write(json.dumps(entry, separators=(',', ':')) + "\n")

        signals = []
        monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir

        # Process all
        for _ in range(5):
            monitor.process_updates()

        # Should trigger exactly once (first time above threshold)
        if len(signals) != 1:
            failures.append(f"Expected 1 signal, got {len(signals)}")
        elif signals[0].level != HandoffLevel.GRACEFUL:
            failures.append(f"Expected GRACEFUL, got {signals[0].level}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print(f"   Oscillation handled: triggered once despite alternating values")
    print("   PASSED")
    return True


def test_duplicate_request_ids():
    """Test that duplicate request IDs are deduplicated."""
    print("\n[ADVERSARIAL] Duplicate Request ID Handling")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Write same request ID multiple times with different values
        with open(jsonl_file, 'w') as f:
            for i in range(10):
                entry = json.dumps({
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "cache_creation_input_tokens": 100000 + i * 10000,
                            "cache_read_input_tokens": 100,
                        }
                    },
                    "requestId": "same_id"  # Same ID every time
                }, separators=(',', ':'))
                f.write(entry + "\n")

        signals = []
        monitor = SessionMonitor("test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir

        monitor.process_updates()

        # Should only process first entry (100000), not reach threshold
        if len(signals) != 0:
            failures.append(f"Duplicates should be filtered, but got {len(signals)} signals")

        # Verify dedup happened
        if len(monitor.seen_request_ids) != 1:
            failures.append(f"Expected 1 seen ID, got {len(monitor.seen_request_ids)}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print(f"   Deduplication working: 10 entries with same ID processed once")
    print("   PASSED")
    return True


def test_session_classification_edge_cases():
    """Test edge cases in session classification."""
    print("\n[ADVERSARIAL] Session Classification Edge Cases")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: Empty file
        empty_file = Path(tmpdir) / "empty.jsonl"
        empty_file.touch()

        if not is_p_mode_session(empty_file):
            failures.append("Empty file should be classified as -p mode")

        # Test 2: File with only whitespace lines
        whitespace_file = Path(tmpdir) / "whitespace.jsonl"
        with open(whitespace_file, 'w') as f:
            f.write("\n\n   \n\t\n")

        if not is_p_mode_session(whitespace_file):
            failures.append("Whitespace-only file should be classified as -p mode")

        # Test 3: Progress type buried after 50 lines
        late_progress = Path(tmpdir) / "late_progress.jsonl"
        with open(late_progress, 'w') as f:
            # 60 user/assistant pairs (120 lines)
            for i in range(60):
                f.write(json.dumps({"type": "user"}, separators=(',', ':')) + "\n")
                f.write(json.dumps({"type": "assistant"}, separators=(',', ':')) + "\n")
            # Progress after 120 lines - should NOT be detected
            f.write(json.dumps({"type": "progress"}, separators=(',', ':')) + "\n")

        if not is_p_mode_session(late_progress):
            failures.append("Progress after 50 lines should not affect classification")

        # Test 4: Non-existent file
        if is_p_mode_session(Path(tmpdir) / "nonexistent.jsonl"):
            failures.append("Non-existent file should return False")

        # Test 5: Binary file
        binary_file = Path(tmpdir) / "binary.jsonl"
        with open(binary_file, 'wb') as f:
            f.write(b'\x00\x01\x02\x03\xff\xfe\xfd')

        # Should not crash
        try:
            result = is_p_mode_session(binary_file)
            print(f"   Binary file result: {result}")
        except Exception as e:
            failures.append(f"Crashed on binary file: {e}")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   All edge cases handled correctly")
    print("   PASSED")
    return True


def test_handoff_md_concurrent_writes():
    """Test concurrent writes to HANDOFF.md."""
    print("\n[ADVERSARIAL] HANDOFF.md Concurrent Writes")

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        errors = []
        lock = threading.Lock()

        def write_handoff(thread_id):
            for i in range(10):
                try:
                    success = write_handoff_state(
                        tmpdir,
                        f"mission_{thread_id}_{i}",
                        "BUILDING",
                        f"Thread {thread_id} iteration {i}"
                    )
                    if not success:
                        with lock:
                            errors.append(f"Write failed: thread {thread_id}, iter {i}")
                except Exception as e:
                    with lock:
                        errors.append(f"Exception: {e}")

        # Start 5 concurrent writers
        threads = [
            threading.Thread(target=write_handoff, args=(i,))
            for i in range(5)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=10)

        if errors:
            failures.extend(errors[:5])
        else:
            # Verify all writes happened
            count = count_handoffs(tmpdir)
            expected = 5 * 10
            if count != expected:
                failures.append(f"Expected {expected} handoffs, got {count}")
            else:
                print(f"   All {count} concurrent writes succeeded")

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   PASSED")
    return True


def test_memory_leak_simulation():
    """Test for memory leaks in long-running scenarios."""
    print("\n[ADVERSARIAL] Memory Leak Simulation")

    import gc

    failures = []

    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        # Track seen_request_ids growth
        monitor = SessionMonitor("leak-test", tmpdir, lambda s: None)
        monitor.transcript_dir = transcript_dir

        # Write 10000 entries with unique IDs
        with open(jsonl_file, 'w') as f:
            for i in range(10000):
                entry = json.dumps({
                    "type": "assistant",
                    "message": {
                        "usage": {
                            "cache_creation_input_tokens": 50000,
                            "cache_read_input_tokens": 100,
                        }
                    },
                    "requestId": f"req_{i}"
                }, separators=(',', ':'))
                f.write(entry + "\n")

        # Process all
        monitor.process_updates()

        # The implementation should cap seen_request_ids at 5000
        # (it drops oldest half when exceeding that)
        if len(monitor.seen_request_ids) > 5000:
            failures.append(
                f"seen_request_ids should be bounded, but has {len(monitor.seen_request_ids)} entries"
            )
        else:
            print(f"   Processed 10000 entries, seen_request_ids capped at {len(monitor.seen_request_ids)}")

    gc.collect()

    if failures:
        print(f"   FAILED: {failures}")
        return False

    print("   PASSED")
    return True


# =============================================================================
# SPEC ALIGNMENT CHECK
# =============================================================================

def test_spec_alignment():
    """Verify implementation matches original specification."""
    print("\n[SPEC ALIGNMENT] Checking Against Original Requirements")

    failures = []

    # Spec requirement 1: Graceful threshold at 130K
    if GRACEFUL_THRESHOLD != 130_000:
        failures.append(f"GRACEFUL_THRESHOLD should be 130000, got {GRACEFUL_THRESHOLD}")

    # Spec requirement 2: Emergency threshold at 140K
    if EMERGENCY_THRESHOLD != 140_000:
        failures.append(f"EMERGENCY_THRESHOLD should be 140000, got {EMERGENCY_THRESHOLD}")

    # Spec requirement 3: Low cache read threshold at 5K
    if LOW_CACHE_READ_THRESHOLD != 5_000:
        failures.append(f"LOW_CACHE_READ_THRESHOLD should be 5000, got {LOW_CACHE_READ_THRESHOLD}")

    # Spec requirement 4: Detection logic
    # "Context exhaustion is detected by: cache_creation_input_tokens > 130K AND cache_read_input_tokens < 5K"

    # Test graceful detection
    with tempfile.TemporaryDirectory() as tmpdir:
        transcript_dir = Path(tmpdir) / ".claude" / "projects" / "-test"
        transcript_dir.mkdir(parents=True)

        jsonl_file = transcript_dir / "session.jsonl"

        entry = json.dumps({
            "type": "assistant",
            "message": {
                "usage": {
                    "cache_creation_input_tokens": 131000,  # > 130K
                    "cache_read_input_tokens": 4000,  # < 5K
                }
            },
            "requestId": "spec_test"
        }, separators=(',', ':'))

        with open(jsonl_file, 'w') as f:
            f.write(entry + "\n")

        signals = []
        monitor = SessionMonitor("spec-test", tmpdir, lambda s: signals.append(s))
        monitor.transcript_dir = transcript_dir
        monitor.process_updates()

        if not signals or signals[0].level != HandoffLevel.GRACEFUL:
            failures.append("Spec: 131K/4K should trigger GRACEFUL handoff")

    # Spec requirement 5: -p mode only
    # "ContextWatcher should ONLY monitor -p (print/non-interactive) mode sessions"
    with tempfile.TemporaryDirectory() as tmpdir:
        interactive = Path(tmpdir) / "interactive.jsonl"
        with open(interactive, 'w') as f:
            f.write(json.dumps({"type": "progress"}, separators=(',', ':')) + "\n")

        if is_p_mode_session(interactive):
            failures.append("Spec: Interactive sessions (with progress) should be skipped")

    # Spec requirement 6: HANDOFF.md is append-only
    with tempfile.TemporaryDirectory() as tmpdir:
        # Write two handoffs
        write_handoff_state(tmpdir, "m1", "BUILDING", "First")
        content1 = (Path(tmpdir) / "HANDOFF.md").read_text()

        write_handoff_state(tmpdir, "m2", "TESTING", "Second")
        content2 = (Path(tmpdir) / "HANDOFF.md").read_text()

        if "First" not in content2:
            failures.append("Spec: HANDOFF.md should preserve previous entries (append-only)")

        if content2.count("Handoff #") != 2:
            failures.append("Spec: HANDOFF.md should have numbered handoff sections")

    if failures:
        print(f"   FAILED: {len(failures)} spec violations")
        for f in failures:
            print(f"     - {f}")
        return False

    print("   All spec requirements verified")
    print("   PASSED")
    return True


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_all_adversarial_tests():
    """Run all adversarial tests and report results."""
    print("=" * 60)
    print("ContextWatcher Adversarial Tests")
    print("=" * 60)
    print("Attempting to break the implementation...")

    tests = [
        ("Malformed JSONL", test_malformed_jsonl_handling),
        ("Boundary Conditions", test_boundary_conditions),
        ("Race Conditions", test_race_condition_simulation),
        ("File Disappears", test_file_disappears_mid_read),
        ("Long Lines", test_extremely_long_jsonl_line),
        ("Threshold Oscillation", test_rapid_threshold_oscillation),
        ("Duplicate IDs", test_duplicate_request_ids),
        ("Classification Edge Cases", test_session_classification_edge_cases),
        ("Concurrent HANDOFF.md", test_handoff_md_concurrent_writes),
        ("Memory Leak", test_memory_leak_simulation),
        ("Spec Alignment", test_spec_alignment),
    ]

    results = []
    for name, test_func in tests:
        try:
            passed = test_func()
            results.append((name, passed, None))
        except Exception as e:
            import traceback
            results.append((name, False, str(e)))
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("ADVERSARIAL TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, p, _ in results if p)
    failed = len(results) - passed

    for name, passed_test, error in results:
        status = "PASS" if passed_test else "FAIL"
        error_msg = f" ({error})" if error else ""
        print(f"  [{status}] {name}{error_msg}")

    print(f"\nTotal: {passed}/{len(results)} passed")

    if failed == 0:
        print("\nAll adversarial tests passed - implementation is robust!")
        return True
    else:
        print(f"\n{failed} adversarial tests found issues!")
        return False


if __name__ == "__main__":
    success = run_all_adversarial_tests()
    sys.exit(0 if success else 1)
