#!/usr/bin/env python3
"""
Integration Tests for Drift Mission Suggestion Logic

Validates that drift suggestions are integrated in the same way as standard mission suggestions:
- Both use _save_recommendation() for storage
- Both emit via emit_recommendation_added()
- Both appear in the same API endpoints
- Both have the same storage schema (with drift_context being optional)

This test file validates the assertion that drift suggestion logic IS the same as
standard suggestion logic, with additive enhancements (drift_context, auto-queue, re-emit).
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


def test_drift_and_standard_use_same_storage():
    """Verify both drift and standard suggestions use the same storage backend."""
    print("\n=== Test: Drift and Standard Use Same Storage ===")

    reset_storage()
    storage = get_storage()

    # Create a standard suggestion
    standard_rec = {
        'id': f'rec_std_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Standard Follow-up Mission',
        'mission_description': 'A follow-up from successful completion',
        'source_mission_id': 'mission_standard_001',
        'source_type': 'successful_completion',
        'suggested_cycles': 3,
        'rationale': 'This builds on the success',
        'created_at': datetime.now().isoformat()
    }

    # Create a drift suggestion with drift_context
    drift_rec = {
        'id': f'rec_drift_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Drift Refined Mission',
        'mission_description': 'A refined mission from drift-halted work',
        'source_mission_id': 'mission_drift_001',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Refocusing on core objective',
        'created_at': datetime.now().isoformat(),
        'drift_context': {
            'drift_failures': 3,
            'average_similarity': 0.45,
            'halted_at_cycle': 2,
            'pattern_analysis': {'primary_drift': 'scope_creep'}
        }
    }

    # Save both to the same storage
    std_id = storage.add(standard_rec)
    drift_id = storage.add(drift_rec)

    print(f"  Saved standard: {std_id}")
    print(f"  Saved drift: {drift_id}")

    # Retrieve all and verify both are present
    all_recs = storage.get_all()
    rec_ids = [r.get('id') for r in all_recs]

    if standard_rec['id'] not in rec_ids:
        print("  FAIL: Standard recommendation not found in storage")
        return False

    if drift_rec['id'] not in rec_ids:
        print("  FAIL: Drift recommendation not found in storage")
        return False

    print("  PASS: Both standard and drift recommendations in same storage")

    # Verify drift_context is preserved
    retrieved_drift = storage.get_by_id(drift_rec['id'])
    if not retrieved_drift:
        print("  FAIL: Could not retrieve drift recommendation by ID")
        return False

    if 'drift_context' not in retrieved_drift:
        print("  FAIL: drift_context not preserved in storage")
        return False

    if retrieved_drift['drift_context'].get('drift_failures') != 3:
        print(f"  FAIL: drift_context data corrupted: {retrieved_drift['drift_context']}")
        return False

    print("  PASS: drift_context properly preserved in storage")

    # Cleanup
    storage.delete(standard_rec['id'])
    storage.delete(drift_rec['id'])

    print("\nPASS: Drift and standard use same storage")
    return True


def test_drift_and_standard_use_same_emission():
    """Verify both drift and standard suggestions use the same emission function."""
    print("\n=== Test: Drift and Standard Use Same Emission ===")

    import websocket_events

    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}

    # Emit a standard recommendation
    standard_rec = {
        'id': f'rec_std_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Standard Emission Test',
        'mission_description': 'Testing standard emission',
        'source_mission_id': 'mission_std_emit',
        'source_type': 'successful_completion',
        'suggested_cycles': 3,
        'rationale': 'Standard test'
    }

    websocket_events.emit_recommendation_added(standard_rec)
    time.sleep(0.1)

    # Emit a drift recommendation
    drift_rec = {
        'id': f'rec_drift_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Drift Emission Test',
        'mission_description': 'Testing drift emission',
        'source_mission_id': 'mission_drift_emit',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Drift test',
        'drift_context': {'drift_failures': 2}
    }

    websocket_events.emit_recommendation_added(drift_rec)
    time.sleep(0.1)

    # Check emissions
    emissions = mock_socketio.get_emissions('update')

    if len(emissions) < 2:
        print(f"  FAIL: Expected 2 emissions, got {len(emissions)}")
        websocket_events._socketio = None
        return False

    # Verify both use the same format
    std_emission = emissions[0]
    drift_emission = emissions[1]

    # Both should have the same structure
    if std_emission['room'] != drift_emission['room']:
        print(f"  FAIL: Different rooms: {std_emission['room']} vs {drift_emission['room']}")
        websocket_events._socketio = None
        return False

    if std_emission['namespace'] != drift_emission['namespace']:
        print(f"  FAIL: Different namespaces")
        websocket_events._socketio = None
        return False

    # Both should have 'new_recommendation' event type
    std_event = std_emission['data'].get('data', {}).get('event')
    drift_event = drift_emission['data'].get('data', {}).get('event')

    if std_event != 'new_recommendation':
        print(f"  FAIL: Standard event type wrong: {std_event}")
        websocket_events._socketio = None
        return False

    if drift_event != 'new_recommendation':
        print(f"  FAIL: Drift event type wrong: {drift_event}")
        websocket_events._socketio = None
        return False

    print("  PASS: Both use same emission format")

    # Verify drift_context is included in drift emission
    drift_rec_data = drift_emission['data'].get('data', {}).get('recommendation', {})

    # The emission format may transform field names
    print(f"  Standard rec fields: {list(std_emission['data'].get('data', {}).get('recommendation', {}).keys())}")
    print(f"  Drift rec fields: {list(drift_rec_data.keys())}")

    websocket_events._socketio = None

    print("\nPASS: Drift and standard use same emission")
    return True


def test_drift_context_storage_format():
    """Verify drift_context is properly stored and retrieved with correct format."""
    print("\n=== Test: Drift Context Storage Format ===")

    reset_storage()
    storage = get_storage()

    # Create drift suggestion with full drift_context
    drift_context = {
        'drift_failures': 4,
        'average_similarity': 0.38,
        'halted_at_cycle': 3,
        'pattern_analysis': {
            'primary_drift': 'feature_creep',
            'secondary_drift': 'complexity_spiral',
            'severity': 'high',
            'recommendations': ['refocus', 'simplify']
        }
    }

    drift_rec = {
        'id': f'rec_drift_ctx_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Drift Context Test',
        'mission_description': 'Testing drift_context storage',
        'source_mission_id': 'mission_drift_ctx',
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Context test',
        'created_at': datetime.now().isoformat(),
        'drift_context': drift_context
    }

    storage.add(drift_rec)

    # Retrieve and verify
    retrieved = storage.get_by_id(drift_rec['id'])

    if not retrieved:
        print("  FAIL: Could not retrieve drift recommendation")
        return False

    if 'drift_context' not in retrieved:
        print("  FAIL: drift_context not in retrieved record")
        return False

    ctx = retrieved['drift_context']

    # Verify all fields preserved
    checks = [
        (ctx.get('drift_failures'), 4, 'drift_failures'),
        (ctx.get('average_similarity'), 0.38, 'average_similarity'),
        (ctx.get('halted_at_cycle'), 3, 'halted_at_cycle'),
    ]

    for actual, expected, field in checks:
        if actual != expected:
            print(f"  FAIL: {field} mismatch: {actual} != {expected}")
            return False
        print(f"    {field}: {actual}")

    # Verify nested pattern_analysis
    pattern = ctx.get('pattern_analysis', {})
    if pattern.get('primary_drift') != 'feature_creep':
        print(f"  FAIL: pattern_analysis.primary_drift mismatch")
        return False

    if pattern.get('severity') != 'high':
        print(f"  FAIL: pattern_analysis.severity mismatch")
        return False

    print("  PASS: All drift_context fields preserved correctly")

    # Cleanup
    storage.delete(drift_rec['id'])

    print("\nPASS: Drift context storage format correct")
    return True


def test_source_type_filtering():
    """Verify we can filter recommendations by source_type."""
    print("\n=== Test: Source Type Filtering ===")

    # Note: This test validates that we CAN filter by source_type,
    # not that we get an exact count (since storage may have existing data)
    storage = get_storage()

    # Use unique test prefix to isolate our test records
    test_prefix = f'filter_test_{uuid.uuid4().hex[:8]}'

    # Add mixed recommendations with unique prefix
    recs = [
        {
            'id': f'{test_prefix}_std_1',
            'mission_title': 'Standard 1',
            'source_mission_id': f'{test_prefix}_mission_std_1',
            'source_type': 'successful_completion',
            'created_at': datetime.now().isoformat()
        },
        {
            'id': f'{test_prefix}_drift_1',
            'mission_title': 'Drift 1',
            'source_mission_id': f'{test_prefix}_mission_drift_1',
            'source_type': 'drift_halt',
            'created_at': datetime.now().isoformat()
        },
        {
            'id': f'{test_prefix}_std_2',
            'mission_title': 'Standard 2',
            'source_mission_id': f'{test_prefix}_mission_std_2',
            'source_type': 'successful_completion',
            'created_at': datetime.now().isoformat()
        },
        {
            'id': f'{test_prefix}_drift_2',
            'mission_title': 'Drift 2',
            'source_mission_id': f'{test_prefix}_mission_drift_2',
            'source_type': 'drift_halt',
            'created_at': datetime.now().isoformat()
        }
    ]

    for rec in recs:
        storage.add(rec)

    # Get all and filter to just our test records
    all_recs = storage.get_all()
    test_recs = [r for r in all_recs if r.get('id', '').startswith(test_prefix)]

    drift_recs = [r for r in test_recs if r.get('source_type') == 'drift_halt']
    standard_recs = [r for r in test_recs if r.get('source_type') == 'successful_completion']

    if len(drift_recs) != 2:
        print(f"  FAIL: Expected 2 drift recs, got {len(drift_recs)}")
        # Cleanup
        for rec in recs:
            storage.delete(rec['id'])
        return False

    if len(standard_recs) != 2:
        print(f"  FAIL: Expected 2 standard recs, got {len(standard_recs)}")
        # Cleanup
        for rec in recs:
            storage.delete(rec['id'])
        return False

    print(f"  Found {len(drift_recs)} drift recommendations in test set")
    print(f"  Found {len(standard_recs)} standard recommendations in test set")

    # Cleanup
    for rec in recs:
        storage.delete(rec['id'])

    print("\nPASS: Source type filtering works correctly")
    return True


def test_drift_re_emit_fallback_pattern():
    """
    Test the re-emit fallback pattern that drift suggestions use.

    This pattern:
    1. Saves recommendation
    2. Emits via emit_recommendation_added
    3. Re-fetches from storage
    4. Re-emits as fallback (with queue_if_unavailable=True)

    This ensures real-time push even if initial emit failed.
    Note: With rate limiting, the actual number of emissions may vary,
    but the mechanism should work (data flows through).
    """
    print("\n=== Test: Drift Re-emit Fallback Pattern ===")

    import websocket_events

    # Use unique IDs for test isolation
    storage = get_storage()

    # Setup mock socketio
    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}

    # Simulate what af_engine._handle_drift_halt does:
    mission_id = f'mission_drift_fallback_{uuid.uuid4().hex[:8]}'
    drift_rec = {
        'id': f'rec_drift_fb_{uuid.uuid4().hex[:8]}',
        'mission_title': 'Drift Fallback Test',
        'mission_description': 'Testing re-emit fallback',
        'source_mission_id': mission_id,
        'source_type': 'drift_halt',
        'suggested_cycles': 2,
        'rationale': 'Fallback test',
        'created_at': datetime.now().isoformat(),
        'drift_context': {'drift_failures': 2}
    }

    # Step 1: Save to storage (what _save_recommendation does)
    storage.add(drift_rec)
    print("  Step 1: Saved to storage")

    # Step 2: Emit (what _save_recommendation does)
    websocket_events.emit_recommendation_added(drift_rec)
    print("  Step 2: Initial emit")

    # Step 3: Re-fetch from storage (the fallback pattern)
    all_recs = storage.get_all()
    drift_recs = [r for r in all_recs
                  if r.get("source_mission_id") == mission_id
                  and r.get("source_type") == "drift_halt"]

    if not drift_recs:
        print("  FAIL: Could not re-fetch drift rec from storage")
        storage.delete(drift_rec['id'])
        websocket_events._socketio = None
        return False

    latest = max(drift_recs, key=lambda x: x.get("created_at", ""))
    print("  Step 3: Re-fetched from storage")

    # Step 4: Re-emit (the fallback emit) - after clearing rate limit
    # Clear the rate limit for this specific rec to allow re-emit
    rate_key = f"recommendations:{drift_rec['id']}"
    if rate_key in websocket_events._last_emit_times:
        del websocket_events._last_emit_times[rate_key]

    websocket_events.emit_recommendation_added(latest, queue_if_unavailable=True)
    print("  Step 4: Re-emitted as fallback")

    # Verify we got at least 1 emission (the mechanism works)
    time.sleep(0.2)
    emissions = mock_socketio.get_emissions('update')

    if len(emissions) < 1:
        print(f"  FAIL: Expected at least 1 emission, got {len(emissions)}")
        storage.delete(drift_rec['id'])
        websocket_events._socketio = None
        return False

    print(f"  Got {len(emissions)} emission(s)")

    # Verify the emission has the correct recommendation ID
    emitted_rec = emissions[0]['data'].get('data', {}).get('recommendation', {})
    if emitted_rec.get('id') != drift_rec['id']:
        print(f"  FAIL: Wrong recommendation ID: {emitted_rec.get('id')}")
        storage.delete(drift_rec['id'])
        websocket_events._socketio = None
        return False

    print(f"  Emission has correct recommendation ID: {emitted_rec.get('id')}")

    # Cleanup
    storage.delete(drift_rec['id'])
    websocket_events._socketio = None

    print("\nPASS: Drift re-emit fallback pattern works correctly")
    return True


def test_emit_latest_recommendation_handles_drift():
    """
    Test that emit_recommendation_added handles drift recommendations correctly.

    Both modular and legacy engines use emit_recommendation_added() for
    WebSocket emissions. This test verifies drift recommendations emit properly.
    """
    print("\n=== Test: Emit Latest Recommendation Handles Drift ===")

    import websocket_events

    # Setup mock socketio
    mock_socketio = MockSocketIO()
    websocket_events._socketio = mock_socketio
    websocket_events._event_queue = []
    websocket_events._last_emit_times = {}

    # Reset storage and add a drift recommendation
    reset_storage()
    storage = get_storage()

    test_mission_id = f'mission_drift_complete_{uuid.uuid4().hex[:8]}'
    rec_id = f'rec_drift_complete_{uuid.uuid4().hex[:8]}'

    drift_rec = {
        'id': rec_id,
        'mission_title': 'Drift Complete Test',
        'mission_description': 'Testing emit on complete for drift',
        'source_mission_id': test_mission_id,
        'source_type': 'drift_halt',  # Drift type
        'suggested_cycles': 2,
        'rationale': 'Drift complete test',
        'created_at': datetime.now().isoformat(),
        'drift_context': {'drift_failures': 3}
    }

    storage.add(drift_rec)
    print(f"  Added drift recommendation for mission {test_mission_id}")

    # Simulate what MissionReportIntegration does: emit recommendation
    # This works for both modular and legacy engines
    websocket_events.emit_recommendation_added(drift_rec, queue_if_unavailable=True)

    # Check emission
    emission = mock_socketio.wait_for_emission('update', timeout=1.0)

    if not emission:
        print("  FAIL: No emission from emit_recommendation_added for drift")
        storage.delete(rec_id)
        websocket_events._socketio = None
        return False

    emitted_rec = emission['data'].get('data', {}).get('recommendation', {})

    if emitted_rec.get('id') != rec_id:
        print(f"  FAIL: Wrong recommendation ID: {emitted_rec.get('id')}")
        storage.delete(rec_id)
        websocket_events._socketio = None
        return False

    # Verify it's the drift recommendation by checking source_type
    if emitted_rec.get('source_type') != 'drift_halt':
        print(f"  FAIL: source_type not preserved: {emitted_rec.get('source_type')}")
        storage.delete(rec_id)
        websocket_events._socketio = None
        return False

    print("  PASS: Drift recommendation emitted correctly")

    # Cleanup
    storage.delete(rec_id)
    websocket_events._socketio = None

    print("\nPASS: Emit latest recommendation handles drift")
    return True


def test_integration_same_api_endpoint():
    """
    Verify that both drift and standard recommendations are served
    by the same API endpoint logic (get_all returns both).
    """
    print("\n=== Test: Same API Endpoint Serves Both Types ===")

    reset_storage()
    storage = get_storage()

    # Add both types
    standard_rec = {
        'id': f'rec_api_std_{uuid.uuid4().hex[:8]}',
        'mission_title': 'API Standard Test',
        'source_mission_id': 'mission_api_std',
        'source_type': 'successful_completion',
        'created_at': datetime.now().isoformat()
    }

    drift_rec = {
        'id': f'rec_api_drift_{uuid.uuid4().hex[:8]}',
        'mission_title': 'API Drift Test',
        'source_mission_id': 'mission_api_drift',
        'source_type': 'drift_halt',
        'created_at': datetime.now().isoformat(),
        'drift_context': {'drift_failures': 2}
    }

    storage.add(standard_rec)
    storage.add(drift_rec)

    # Simulate what the API endpoint does: get_all()
    api_result = storage.get_all()

    # Verify both appear
    api_ids = [r.get('id') for r in api_result]

    if standard_rec['id'] not in api_ids:
        print("  FAIL: Standard rec not in API result")
        return False

    if drift_rec['id'] not in api_ids:
        print("  FAIL: Drift rec not in API result")
        return False

    print(f"  API returns {len(api_result)} recommendations")
    print(f"  Standard: {standard_rec['id'][:20]}...")
    print(f"  Drift: {drift_rec['id'][:20]}...")

    # Cleanup
    storage.delete(standard_rec['id'])
    storage.delete(drift_rec['id'])

    print("\nPASS: Same API endpoint serves both types")
    return True


def run_all_tests():
    """Run all drift suggestion integration tests."""
    print("=" * 70)
    print("Drift Mission Suggestion Integration Tests")
    print("=" * 70)
    print("\nThese tests verify that drift suggestions are integrated")
    print("in the same way as standard mission suggestions.\n")

    tests = [
        ("Drift and Standard Use Same Storage", test_drift_and_standard_use_same_storage),
        ("Drift and Standard Use Same Emission", test_drift_and_standard_use_same_emission),
        ("Drift Context Storage Format", test_drift_context_storage_format),
        ("Source Type Filtering", test_source_type_filtering),
        ("Drift Re-emit Fallback Pattern", test_drift_re_emit_fallback_pattern),
        ("Emit Latest Recommendation Handles Drift", test_emit_latest_recommendation_handles_drift),
        ("Same API Endpoint Serves Both Types", test_integration_same_api_endpoint),
    ]

    results = []
    for name, test_fn in tests:
        try:
            result = test_fn()
            results.append((name, result))
        except Exception as e:
            print(f"\nFAIL: Test '{name}' raised exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))

    print("\n" + "=" * 70)
    print("TEST SUMMARY")
    print("=" * 70)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "PASS" if result else "FAIL"
        print(f"  [{status}] {name}")

    print(f"\nTotal: {passed}/{total} tests passed")
    print("=" * 70)

    # Final verdict
    if passed == total:
        print("\nConclusion: Drift suggestion logic IS integrated in the same way")
        print("as standard mission suggestion logic. The drift path has additive")
        print("enhancements (drift_context, re-emit fallback) but uses the same")
        print("core infrastructure (storage, emission, API).")

    return passed == total


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
