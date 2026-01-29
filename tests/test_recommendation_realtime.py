#!/usr/bin/env python3
"""
Integration Tests for Real-Time Recommendation Push

Tests the end-to-end flow of recommendation generation and WebSocket emission
when a mission completes (COMPLETE or DRIFT_HALT).

Success criteria:
- Recommendation appears in dashboard within 2 seconds of mission completion
- WebSocket event is emitted with correct payload
- Frontend handler receives and processes the event
- Event queue works when socketio is not initially available
"""

import json
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add AtlasForge root to path
AF_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(AF_ROOT))

import io_utils
from suggestion_storage import get_storage, reset_storage


class MockSocketIO:
    """Mock SocketIO for testing WebSocket emissions."""

    def __init__(self):
        self.emissions = []
        self.lock = threading.Lock()

    def emit(self, event, data, room=None, namespace=None):
        with self.lock:
            self.emissions.append({
                'event': event,
                'data': data,
                'room': room,
                'namespace': namespace,
                'timestamp': time.time()
            })

    def get_emissions(self, event_type=None):
        with self.lock:
            if event_type:
                return [e for e in self.emissions if e['event'] == event_type]
            return self.emissions.copy()

    def clear(self):
        with self.lock:
            self.emissions = []

    def wait_for_emission(self, event_type='update', timeout=2.0):
        """Wait for an emission of the specified type."""
        start = time.time()
        while time.time() - start < timeout:
            emissions = self.get_emissions(event_type)
            if emissions:
                return emissions[-1]
            time.sleep(0.05)
        return None


def test_event_queue_when_socketio_unavailable():
    """Test that events are queued when socketio is not available."""
    print("\n=== Test: Event Queue When SocketIO Unavailable ===")

    # Test the queue mechanism directly since _get_socketio() will try to
    # import dashboard_v2 which makes socketio available.
    # This tests the queue infrastructure itself.
    import websocket_events

    # Reset state
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}  # Reset rate limiter

    # Test _queue_event directly
    test_data = {
        'event': 'new_recommendation',
        'recommendation': {
            'id': f'rec_test_{uuid.uuid4().hex[:8]}',
            'title': 'Test Queued Recommendation',
        }
    }

    websocket_events._queue_event('recommendations', 'update', test_data)

    # Check event was queued
    with websocket_events._event_queue_lock:
        queue_len = len(websocket_events._event_queue)

    if queue_len == 0:
        print("❌ Event was not queued via _queue_event")
        return False

    print(f"✓ Event queued successfully (queue length: {queue_len})")

    # Check queue contents
    queued_event = websocket_events._event_queue[0]
    if queued_event['room'] != 'recommendations':
        print(f"❌ Wrong room in queue: {queued_event['room']}")
        return False

    if queued_event['event'] != 'update':
        print(f"❌ Wrong event type in queue: {queued_event['event']}")
        return False

    print(f"✓ Queued event structure correct")

    # Now test flush_queued_events with a mock socketio
    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio

    flushed = websocket_events.flush_queued_events()

    if flushed == 0:
        print("❌ No events flushed")
        websocket_events._socketio = None
        return False

    print(f"✓ {flushed} event(s) flushed")

    # Check emission occurred
    emissions = mock_socketio.get_emissions('update')
    if not emissions:
        print("❌ No emissions after flush")
        websocket_events._socketio = None
        return False

    # Verify emission data
    emission = emissions[0]
    rec_data = emission['data'].get('data', {}).get('recommendation', {})
    if rec_data.get('title') != 'Test Queued Recommendation':
        print(f"❌ Wrong recommendation title: {rec_data.get('title')}")
        websocket_events._socketio = None
        return False

    print(f"✓ Flushed event has correct data")
    print(f"  - Title: {rec_data.get('title')}")
    print(f"  - Queued flag: {emission['data'].get('queued', False)}")

    # Verify queue is now empty
    with websocket_events._event_queue_lock:
        if len(websocket_events._event_queue) != 0:
            print("❌ Queue not empty after flush")
            websocket_events._socketio = None
            return False

    print(f"✓ Queue is empty after flush")

    # Cleanup
    websocket_events._socketio = None
    websocket_events._event_queue = []

    print("\n✅ Event queue test passed")
    return True


def test_recommendation_emission_on_save():
    """Test that emit_recommendation_added emits correct event format."""
    print("\n=== Test: Recommendation Emission Format ===")

    import websocket_events

    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []

    test_rec = {
        'id': f'rec_format_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Format Test Mission',
        'mission_description': 'Testing emission format',
        'source_mission_id': 'mission_format_test',
        'source_type': 'successful_completion',
        'suggested_cycles': 3,
        'rationale': 'Test rationale'
    }

    websocket_events.emit_recommendation_added(test_rec)

    emissions = mock_socketio.get_emissions('update')
    if not emissions:
        print("❌ No emission received")
        websocket_events._socketio = None
        return False

    emission = emissions[0]

    # Verify structure
    if emission['room'] != 'recommendations':
        print(f"❌ Wrong room: {emission['room']}")
        websocket_events._socketio = None
        return False

    if emission['namespace'] != '/widgets':
        print(f"❌ Wrong namespace: {emission['namespace']}")
        websocket_events._socketio = None
        return False

    data = emission['data'].get('data', {})
    if data.get('event') != 'new_recommendation':
        print(f"❌ Wrong event type: {data.get('event')}")
        websocket_events._socketio = None
        return False

    rec = data.get('recommendation', {})
    expected_fields = ['id', 'title', 'description', 'source_mission', 'source_type', 'suggested_cycles', 'rationale']
    missing = [f for f in expected_fields if f not in rec]
    if missing:
        print(f"❌ Missing fields: {missing}")
        websocket_events._socketio = None
        return False

    print(f"✓ Emission format correct")
    print(f"  - Room: {emission['room']}")
    print(f"  - Namespace: {emission['namespace']}")
    print(f"  - Event type: {data.get('event')}")
    print(f"  - Recommendation fields: {list(rec.keys())}")

    websocket_events._socketio = None
    print("\n✅ Emission format test passed")
    return True


def test_storage_and_emission_integration():
    """Test that saving to storage triggers emission."""
    print("\n=== Test: Storage and Emission Integration ===")

    import websocket_events

    # Setup mock socketio
    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []

    # Reset storage
    reset_storage()
    storage = get_storage()

    # Simulate what af_engine does: save to storage then emit
    rec_id = f'rec_integration_{uuid.uuid4().hex[:8]}'
    rec_entry = {
        'id': rec_id,
        'mission_title': 'Integration Test Mission',
        'mission_description': 'Testing storage + emission integration',
        'source_mission_id': 'mission_integration_test',
        'source_type': 'successful_completion',
        'suggested_cycles': 2,
        'rationale': 'Integration test',
        'created_at': datetime.now().isoformat()
    }

    # Save to storage
    saved_id = storage.add(rec_entry)
    print(f"✓ Saved recommendation to storage: {saved_id}")

    # Emit (as af_engine does)
    websocket_events.emit_recommendation_added(rec_entry)

    # Check emission occurred
    emissions = mock_socketio.get_emissions('update')
    if not emissions:
        print("❌ No emission after save")
        websocket_events._socketio = None
        return False

    emission = emissions[-1]
    emitted_rec = emission['data'].get('data', {}).get('recommendation', {})

    if emitted_rec.get('id') != rec_id:
        print(f"❌ Wrong recommendation ID in emission: {emitted_rec.get('id')}")
        websocket_events._socketio = None
        return False

    print(f"✓ Emission received with correct ID")

    # Verify storage has the recommendation
    stored = storage.get_by_id(rec_id)
    if not stored:
        print(f"❌ Recommendation not found in storage")
        websocket_events._socketio = None
        return False

    print(f"✓ Recommendation exists in storage")

    # Cleanup
    storage.delete(rec_id)
    websocket_events._socketio = None

    print("\n✅ Storage and emission integration test passed")
    return True


def test_timing_under_2_seconds():
    """Test that emission occurs within 2 seconds of recommendation creation."""
    print("\n=== Test: Timing Under 2 Seconds ===")

    import websocket_events

    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []

    start_time = time.time()

    test_rec = {
        'id': f'rec_timing_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Timing Test Mission',
        'mission_description': 'Testing emission timing',
        'source_mission_id': 'mission_timing_test',
        'source_type': 'successful_completion'
    }

    websocket_events.emit_recommendation_added(test_rec)

    # Wait for emission
    emission = mock_socketio.wait_for_emission('update', timeout=2.0)

    end_time = time.time()
    elapsed = end_time - start_time

    if not emission:
        print(f"❌ No emission received within 2 seconds")
        websocket_events._socketio = None
        return False

    if elapsed > 2.0:
        print(f"❌ Emission took too long: {elapsed:.3f}s")
        websocket_events._socketio = None
        return False

    print(f"✓ Emission received in {elapsed:.3f}s (< 2s)")

    websocket_events._socketio = None
    print("\n✅ Timing test passed")
    return True


def test_multiple_recommendations_no_rate_limit_collision():
    """Test that multiple recommendations don't collide due to rate limiting."""
    print("\n=== Test: Multiple Recommendations Rate Limiting ===")

    import websocket_events

    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}  # Reset rate limiter

    rec_ids = []
    for i in range(3):
        rec_id = f'rec_multi_{uuid.uuid4().hex[:8]}'
        rec_ids.append(rec_id)

        test_rec = {
            'id': rec_id,
            'mission_title': f'Multi Test Mission {i+1}',
            'mission_description': f'Testing multiple recommendations {i+1}',
            'source_mission_id': f'mission_multi_{i}',
            'source_type': 'successful_completion'
        }

        websocket_events.emit_recommendation_added(test_rec)
        time.sleep(0.15)  # Small delay between emissions

    time.sleep(0.2)  # Wait for all emissions

    emissions = mock_socketio.get_emissions('update')

    if len(emissions) < 3:
        print(f"❌ Only {len(emissions)} emissions received, expected 3")
        websocket_events._socketio = None
        return False

    # Verify all recommendations were emitted
    emitted_ids = set()
    for em in emissions:
        rec = em['data'].get('data', {}).get('recommendation', {})
        emitted_ids.add(rec.get('id'))

    missing = set(rec_ids) - emitted_ids
    if missing:
        print(f"❌ Missing recommendation IDs: {missing}")
        websocket_events._socketio = None
        return False

    print(f"✓ All {len(rec_ids)} recommendations emitted successfully")

    websocket_events._socketio = None
    print("\n✅ Multiple recommendations test passed")
    return True


def test_emit_latest_recommendation_on_complete():
    """Test that emit_recommendation_added works for COMPLETE stage recommendations.

    Both modular and legacy engines use emit_recommendation_added() for
    WebSocket emissions. This test validates the emission works correctly.
    """
    print("\n=== Test: Emit Latest Recommendation on Complete ===")

    import websocket_events

    # Setup mock socketio
    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}

    # Reset storage and add a test recommendation
    reset_storage()
    storage = get_storage()

    test_mission_id = f'mission_complete_test_{uuid.uuid4().hex[:8]}'
    rec_id = f'rec_complete_{uuid.uuid4().hex[:8]}'

    rec_entry = {
        'id': rec_id,
        'mission_title': 'Complete Test Recommendation',
        'mission_description': 'Testing emit on complete',
        'source_mission_id': test_mission_id,
        'source_type': 'successful_completion',
        'suggested_cycles': 3,
        'rationale': 'Complete test',
        'created_at': datetime.now().isoformat()
    }

    storage.add(rec_entry)
    print(f"  Added test recommendation for mission {test_mission_id}")

    # Clear emissions before test
    mock_socketio.clear()

    # Simulate what MissionReportIntegration does: emit recommendation
    # This works for both modular and legacy engines
    websocket_events.emit_recommendation_added(rec_entry, queue_if_unavailable=True)

    # Check emission
    emission = mock_socketio.wait_for_emission('update', timeout=1.0)

    if not emission:
        print("  No emission from emit_recommendation_added")
        storage.delete(rec_id)
        websocket_events._socketio = None
        return False

    emitted_rec = emission['data'].get('data', {}).get('recommendation', {})
    if emitted_rec.get('id') != rec_id:
        print(f"  Wrong recommendation ID: {emitted_rec.get('id')}")
        storage.delete(rec_id)
        websocket_events._socketio = None
        return False

    print(f"  Correct recommendation emitted on complete")

    # Cleanup
    storage.delete(rec_id)
    websocket_events._socketio = None

    print("\n  Emit on complete test passed")
    return True


def test_drift_halt_suggestion_emission():
    """Test that drift-halt suggestions emit correctly with drift_context."""
    print("\n=== Test: Drift Halt Suggestion Emission ===")

    import websocket_events

    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}

    # Create a drift-halt recommendation with drift_context
    drift_rec = {
        'id': f'rec_drift_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Drift Refined Mission',
        'mission_description': 'A mission refined after drift detection',
        'source_mission_id': 'mission_drift_test',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Refocusing on core objective after scope drift',
        'drift_context': {
            'drift_failures': 3,
            'average_similarity': 0.42,
            'halted_at_cycle': 2,
            'pattern_analysis': {'primary_drift': 'scope_creep'}
        }
    }

    websocket_events.emit_recommendation_added(drift_rec)

    emission = mock_socketio.wait_for_emission('update', timeout=1.0)

    if not emission:
        print("  No emission received")
        websocket_events._socketio = None
        return False

    # Verify emission structure
    data = emission['data'].get('data', {})
    if data.get('event') != 'new_recommendation':
        print(f"  Wrong event type: {data.get('event')}")
        websocket_events._socketio = None
        return False

    rec = data.get('recommendation', {})

    # Verify source_type is preserved
    if rec.get('source_type') != 'drift_halt':
        print(f"  source_type not preserved: {rec.get('source_type')}")
        websocket_events._socketio = None
        return False

    print(f"  source_type preserved: {rec.get('source_type')}")
    print(f"  Drift recommendation emitted successfully")

    websocket_events._socketio = None
    print("\n  Drift halt suggestion emission test passed")
    return True


def test_drift_context_storage():
    """Test that drift_context is properly stored and retrieved."""
    print("\n=== Test: Drift Context Storage ===")

    reset_storage()
    storage = get_storage()

    drift_context = {
        'drift_failures': 4,
        'average_similarity': 0.35,
        'halted_at_cycle': 3,
        'pattern_analysis': {
            'primary_drift': 'feature_creep',
            'severity': 'high'
        }
    }

    rec_id = f'rec_ctx_{uuid.uuid4().hex[:8]}'
    drift_rec = {
        'id': rec_id,
        'mission_title': 'Context Storage Test',
        'mission_description': 'Testing drift context storage',
        'source_mission_id': 'mission_ctx_test',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Context test',
        'created_at': datetime.now().isoformat(),
        'drift_context': drift_context
    }

    storage.add(drift_rec)

    # Retrieve and verify
    retrieved = storage.get_by_id(rec_id)

    if not retrieved:
        print("  Could not retrieve recommendation")
        return False

    if 'drift_context' not in retrieved:
        print("  drift_context not preserved")
        return False

    ctx = retrieved['drift_context']
    if ctx.get('drift_failures') != 4:
        print(f"  drift_failures mismatch: {ctx.get('drift_failures')}")
        storage.delete(rec_id)
        return False

    if ctx.get('average_similarity') != 0.35:
        print(f"  average_similarity mismatch: {ctx.get('average_similarity')}")
        storage.delete(rec_id)
        return False

    pattern = ctx.get('pattern_analysis', {})
    if pattern.get('primary_drift') != 'feature_creep':
        print(f"  pattern_analysis.primary_drift mismatch")
        storage.delete(rec_id)
        return False

    print(f"  drift_context preserved correctly:")
    print(f"    - drift_failures: {ctx.get('drift_failures')}")
    print(f"    - average_similarity: {ctx.get('average_similarity')}")
    print(f"    - pattern_analysis: {pattern}")

    storage.delete(rec_id)
    print("\n  Drift context storage test passed")
    return True


def test_drift_auto_queue_mechanism():
    """Test that drift suggestions can be auto-queued (mechanism only, not full integration)."""
    print("\n=== Test: Drift Auto-Queue Mechanism ===")

    # This tests the data structure that would be used for auto-queuing
    # The actual auto-queue depends on queue_scheduler being available

    drift_rec = {
        'id': f'rec_aq_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Auto-Queue Test Mission',
        'mission_description': 'Testing auto-queue data structure',
        'source_mission_id': 'mission_aq_test',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Auto-queue test',
        'drift_context': {
            'drift_failures': 2,
            'average_similarity': 0.45,
            'halted_at_cycle': 1
        }
    }

    # Verify the recommendation has all fields needed for auto-queuing
    required_fields = ['mission_title', 'mission_description', 'suggested_cycles']
    missing = [f for f in required_fields if f not in drift_rec]

    if missing:
        print(f"  Missing fields for auto-queue: {missing}")
        return False

    print(f"  All required fields present for auto-queue:")
    print(f"    - mission_title: {drift_rec['mission_title']}")
    print(f"    - mission_description: {drift_rec['mission_description'][:50]}...")
    print(f"    - suggested_cycles: {drift_rec['suggested_cycles']}")

    # Verify the source_type is correct
    if drift_rec['source_type'] != 'drift_halt':
        print(f"  Wrong source_type: {drift_rec['source_type']}")
        return False

    print(f"    - source_type: {drift_rec['source_type']}")

    # Verify drift_context is present
    if 'drift_context' not in drift_rec:
        print("  drift_context missing")
        return False

    print(f"    - drift_context present with {len(drift_rec['drift_context'])} fields")

    print("\n  Drift auto-queue mechanism test passed")
    return True


def run_all_tests():
    """Run all integration tests."""
    print("=" * 60)
    print("Real-Time Recommendation Push Integration Tests")
    print("=" * 60)

    tests = [
        ("Event Queue When SocketIO Unavailable", test_event_queue_when_socketio_unavailable),
        ("Recommendation Emission Format", test_recommendation_emission_on_save),
        ("Storage and Emission Integration", test_storage_and_emission_integration),
        ("Timing Under 2 Seconds", test_timing_under_2_seconds),
        ("Multiple Recommendations Rate Limiting", test_multiple_recommendations_no_rate_limit_collision),
        ("Emit Latest Recommendation on Complete", test_emit_latest_recommendation_on_complete),
        # Drift-specific tests
        ("Drift Halt Suggestion Emission", test_drift_halt_suggestion_emission),
        ("Drift Context Storage", test_drift_context_storage),
        ("Drift Auto-Queue Mechanism", test_drift_auto_queue_mechanism),
    ]

    results = []
    for name, test_fn in tests:
        try:
            result = test_fn()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ Test '{name}' raised exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("=" * 60)

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
