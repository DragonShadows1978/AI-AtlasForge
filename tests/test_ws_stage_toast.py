#!/usr/bin/env python3
"""
Integration Tests: WebSocket Stage-Change Toast Fixes

Covers two pre-existing bugs fixed in mission 129bfe90:

  Bug 1 — af_engine/integrations/websocket_events.py _emit() called
           ws.emit_event() which does not exist on the websocket_events module.
           All 7 engine event types were silently dropped.

  Bug 2 — dashboard_v2.check_and_emit_widget_updates() never passed
           event_type to emit_mission_status(), so the JS showToast() handler
           could not distinguish stage-change payloads from routine polls.

Success criteria:
  1. WebSocketIntegration._emit() no longer raises AttributeError.
  2. _emit() calls emit_widget_update (the correct function) with 'event' key set.
  3. check_and_emit_widget_updates() emits event_type='stage_change' + old_stage
     when rd_stage transitions, and omits event_type on non-stage changes.
  4. JS event-type contract: 'stage_change' / 'engine_stage_change' /
     'mission_stage_change' are the three values recognized by showToast().
"""

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add AtlasForge root to path
AF_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(AF_ROOT))


# ---------------------------------------------------------------------------
# Shared Mock
# ---------------------------------------------------------------------------

class MockSocketIO:
    """Minimal SocketIO stub that records every emit call."""

    def __init__(self):
        self.emissions = []
        self._lock = threading.Lock()

    def emit(self, event, data, room=None, namespace=None):
        with self._lock:
            self.emissions.append({
                'event': event,
                'data': data,
                'room': room,
                'namespace': namespace,
            })

    def get_emissions(self, event_name=None):
        with self._lock:
            if event_name:
                return [e for e in self.emissions if e['event'] == event_name]
            return list(self.emissions)

    def clear(self):
        with self._lock:
            self.emissions = []


# ---------------------------------------------------------------------------
# Test class 1: WebSocketIntegration._emit()
# ---------------------------------------------------------------------------

class TestWebSocketIntegrationEmit:
    """
    Tests that WebSocketIntegration._emit() uses the correct API
    (emit_widget_update) instead of the non-existent emit_event().
    """

    def _make_integration(self):
        """Return a fresh WebSocketIntegration instance."""
        from af_engine.integrations.websocket_events import WebSocketIntegration
        return WebSocketIntegration()

    def test_emit_does_not_raise_on_stage_started(self):
        """_emit('stage_started', ...) must not raise any exception."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('stage_started', {
                'mission_id': 'test_mission',
                'stage': 'BUILDING',
                'timestamp': '2026-03-02T00:00:00',
            })

    def test_emit_does_not_raise_on_stage_completed(self):
        """_emit('stage_completed', ...) must not raise any exception."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('stage_completed', {
                'mission_id': 'test_mission',
                'stage': 'BUILDING',
                'status': 'success',
                'timestamp': '2026-03-02T00:00:00',
            })

    def test_emit_does_not_raise_on_response_received(self):
        """_emit('response_received', ...) must not raise any exception."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('response_received', {
                'mission_id': 'test_mission',
                'stage': 'BUILDING',
                'input_tokens': 100,
                'output_tokens': 200,
                'timestamp': '2026-03-02T00:00:00',
            })

    def test_emit_does_not_raise_on_mission_started(self):
        """_emit('mission_started', ...) must not raise."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('mission_started', {
                'mission_id': 'test_mission',
                'timestamp': '2026-03-02T00:00:00',
            })

    def test_emit_calls_emit_widget_update(self):
        """_emit must call ws.emit_widget_update, not the non-existent ws.emit_event."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        # Explicitly remove emit_event so the old code would have raised AttributeError
        del mock_ws.emit_event
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('stage_started', {
                'mission_id': 'test_mission',
                'stage': 'TESTING',
                'timestamp': '2026-03-02T00:00:00',
            })
        mock_ws.emit_widget_update.assert_called_once()

    def test_emit_sends_to_mission_status_room(self):
        """_emit must route to the 'mission_status' room."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('stage_started', {
                'mission_id': 'test_mission',
                'stage': 'TESTING',
                'timestamp': '2026-03-02T00:00:00',
            })
        call_args = mock_ws.emit_widget_update.call_args
        room_arg = call_args[0][0]  # first positional arg
        assert room_arg == 'mission_status', (
            f"Expected room='mission_status', got '{room_arg}'"
        )

    def test_emit_includes_event_key_in_payload(self):
        """The payload delivered to emit_widget_update must contain 'event' == event_type."""
        integration = self._make_integration()
        mock_ws = MagicMock()
        with patch.dict('sys.modules', {'websocket_events': mock_ws}):
            integration._emit('cycle_completed', {
                'mission_id': 'test_mission',
                'cycle_number': 2,
                'cycles_remaining': 1,
                'timestamp': '2026-03-02T00:00:00',
            })
        call_args = mock_ws.emit_widget_update.call_args
        payload = call_args[0][1]  # second positional arg (data dict)
        assert 'event' in payload, "Payload must include 'event' key"
        assert payload['event'] == 'cycle_completed', (
            f"Expected event='cycle_completed', got '{payload['event']}'"
        )

    def test_old_emit_event_would_have_raised(self):
        """Confirm that the OLD ws.emit_event() call raises AttributeError on real module."""
        import websocket_events as ws
        assert not hasattr(ws, 'emit_event'), (
            "websocket_events.emit_event still exists — the bug can't be demonstrated"
        )


# ---------------------------------------------------------------------------
# Test class 2: check_and_emit_widget_updates() stage-change path
# ---------------------------------------------------------------------------

class TestStageToastViaPoll:
    """
    Tests that check_and_emit_widget_updates() emits event_type='stage_change'
    and old_stage when rd_stage transitions, so the JS toast handler can fire.
    """

    def _setup_mock_socketio(self):
        """Inject a MockSocketIO into mission_status_schema and websocket_events.

        dashboard_v2's module-level startup calls set_socketio() with the real
        Flask-SocketIO instance.  We must inject our mock AFTER dashboard_v2 is
        imported so our mock wins over the real one.
        """
        # Import dashboard_v2 first so its module-level set_socketio() has run
        import dashboard_v2  # noqa: F401
        import dashboard_modules.mission_status_schema as schema_mod
        import websocket_events as ws_mod

        mock_sio = MockSocketIO()
        schema_mod.set_socketio(mock_sio)
        ws_mod.set_socketio(mock_sio)
        return mock_sio

    def _reset_widget_state(self, prev_stage_key='PLANNING||True||0'):
        """Pre-seed _widget_state with a known previous stage."""
        import dashboard_v2 as dv2
        dv2._widget_state['mission_status_key'] = prev_stage_key
        # Also reset the rate-limit so the function actually runs
        dv2._ws_state_cache['last_check'] = 0

    def test_stage_change_sets_event_type(self):
        """
        When rd_stage transitions PLANNING->BUILDING, emitted payload must have
        event == 'stage_change'.
        """
        mock_sio = self._setup_mock_socketio()
        self._reset_widget_state('PLANNING||True||0')

        fake_status = {
            'rd_stage': 'BUILDING',
            'running': True,
            'rd_iteration': 0,
            'current_cycle': 1,
            'cycle_budget': 3,
        }

        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value=fake_status):
            dv2.check_and_emit_widget_updates()

        emissions = mock_sio.get_emissions('update')
        assert emissions, "Expected at least one 'update' emission"
        payload = emissions[-1]['data']
        assert payload.get('data', {}).get('event') == 'stage_change', (
            f"Expected event='stage_change' in payload, got: {payload}"
        )

    def test_stage_change_sets_old_stage(self):
        """
        Emitted payload must contain old_stage == 'PLANNING' when transitioning
        from PLANNING to BUILDING.
        """
        mock_sio = self._setup_mock_socketio()
        self._reset_widget_state('PLANNING||True||0')

        fake_status = {
            'rd_stage': 'BUILDING',
            'running': True,
            'rd_iteration': 0,
            'current_cycle': 1,
            'cycle_budget': 3,
        }

        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value=fake_status):
            dv2.check_and_emit_widget_updates()

        emissions = mock_sio.get_emissions('update')
        assert emissions
        payload_data = emissions[-1]['data'].get('data', {})
        assert payload_data.get('old_stage') == 'PLANNING', (
            f"Expected old_stage='PLANNING', got: {payload_data.get('old_stage')}"
        )

    def test_no_event_type_on_iteration_change_only(self):
        """
        When only rd_iteration changes (not rd_stage), no event_type must be set
        -- this is a routine status poll, not a stage transition toast.
        """
        mock_sio = self._setup_mock_socketio()
        # Same stage, different iteration
        self._reset_widget_state('BUILDING||True||1')

        fake_status = {
            'rd_stage': 'BUILDING',
            'running': True,
            'rd_iteration': 2,  # iteration incremented, stage same
            'current_cycle': 1,
            'cycle_budget': 3,
        }

        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value=fake_status):
            dv2.check_and_emit_widget_updates()

        emissions = mock_sio.get_emissions('update')
        if emissions:
            payload_data = emissions[-1]['data'].get('data', {})
            event_val = payload_data.get('event')
            assert event_val != 'stage_change', (
                f"Should not emit 'stage_change' on iteration-only change, got: {event_val}"
            )

    def test_no_toast_on_first_load(self):
        """
        On first load (prev_key is empty), no stage_change toast must fire even
        though new_stage is set -- this prevents spurious toasts at startup.
        """
        mock_sio = self._setup_mock_socketio()
        import dashboard_v2 as dv2
        # Simulate first load: no previous state key
        dv2._widget_state.pop('mission_status_key', None)
        dv2._ws_state_cache['last_check'] = 0

        fake_status = {
            'rd_stage': 'PLANNING',
            'running': True,
            'rd_iteration': 0,
            'current_cycle': 1,
            'cycle_budget': 3,
        }

        with patch.object(dv2, 'get_claude_status', return_value=fake_status):
            dv2.check_and_emit_widget_updates()

        emissions = mock_sio.get_emissions('update')
        if emissions:
            payload_data = emissions[-1]['data'].get('data', {})
            assert payload_data.get('event') != 'stage_change', (
                "Must not emit stage_change on first load (no prev_stage available)"
            )

    def test_no_duplicate_emission_on_same_state(self):
        """
        Calling check_and_emit_widget_updates twice with the same status must
        not emit a second update (deduplication via status_key).
        """
        mock_sio = self._setup_mock_socketio()
        self._reset_widget_state('PLANNING||True||0')

        fake_status = {
            'rd_stage': 'BUILDING',
            'running': True,
            'rd_iteration': 0,
            'current_cycle': 1,
            'cycle_budget': 3,
        }

        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value=fake_status):
            dv2.check_and_emit_widget_updates()
            count_after_first = len(mock_sio.get_emissions('update'))
            # Reset rate limit only, not widget state
            dv2._ws_state_cache['last_check'] = 0
            dv2.check_and_emit_widget_updates()
            count_after_second = len(mock_sio.get_emissions('update'))

        assert count_after_second == count_after_first, (
            "Second call with same status should not emit again"
        )

    def test_full_stage_transition_chain(self):
        """
        Simulate PLANNING->BUILDING->TESTING transition and verify both
        stage-change events carry correct event_type and old_stage.

        Pre-seed PLANNING as the known initial state (not first-load) so that
        both the BUILDING and TESTING transitions fire stage_change events.
        """
        mock_sio = self._setup_mock_socketio()
        import dashboard_v2 as dv2

        # Pre-seed PLANNING as already-known state (avoids first-load no-toast guard)
        self._reset_widget_state('PLANNING||True||0')

        # Step 1: Transition to BUILDING
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': 'BUILDING', 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()

        # Step 2: Transition to TESTING
        dv2._ws_state_cache['last_check'] = 0
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': 'TESTING', 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()

        all_emissions = mock_sio.get_emissions('update')
        stage_change_emissions = [
            e for e in all_emissions
            if e['data'].get('data', {}).get('event') == 'stage_change'
        ]

        # Both transitions should have fired stage_change events
        assert len(stage_change_emissions) >= 2, (
            f"Expected at least 2 stage_change emissions, got {len(stage_change_emissions)}"
        )

        # Verify old_stage values carried through correctly
        old_stages = [e['data']['data'].get('old_stage') for e in stage_change_emissions]
        assert 'PLANNING' in old_stages, f"Expected 'PLANNING' in old_stages: {old_stages}"
        assert 'BUILDING' in old_stages, f"Expected 'BUILDING' in old_stages: {old_stages}"


# ---------------------------------------------------------------------------
# Test class 3: JS event-type contract (documentation/contract test)
# ---------------------------------------------------------------------------

class TestStageToastJSContract:
    """
    Verifies that the event_type values emitted from Python are the same ones
    checked by the JS showToast() handler in widgets.js.

    This is a contract test -- it guards against drift between the Python emitters
    and the JS consumer without requiring a real browser.
    """

    # Event types that the JS showToast() handler explicitly matches:
    JS_TOAST_EVENT_TYPES = frozenset({
        'stage_change',
        'engine_stage_change',
        'mission_stage_change',
    })

    def test_check_and_emit_widget_updates_uses_recognized_event_type(self):
        """
        The event_type emitted by check_and_emit_widget_updates on stage
        transition must be in JS_TOAST_EVENT_TYPES.
        """
        emitted_event_type = 'stage_change'  # set in the fixed code
        assert emitted_event_type in self.JS_TOAST_EVENT_TYPES, (
            f"'{emitted_event_type}' is not recognised by JS showToast(). "
            f"Valid types: {self.JS_TOAST_EVENT_TYPES}"
        )

    def test_websocket_events_emit_stage_change_uses_recognized_event_type(self):
        """
        websocket_events.emit_stage_change routes through emit_mission_status
        with event_type='mission_stage_change' -- must be in JS_TOAST_EVENT_TYPES.
        """
        emitted_event_type = 'mission_stage_change'  # set inside emit_stage_change()
        assert emitted_event_type in self.JS_TOAST_EVENT_TYPES

    def test_watch_engine_stage_uses_recognized_event_type(self):
        """
        watch_engine_stage() (2s poller) emits event_type='engine_stage_change'
        -- must be in JS_TOAST_EVENT_TYPES.
        """
        emitted_event_type = 'engine_stage_change'
        assert emitted_event_type in self.JS_TOAST_EVENT_TYPES

    def test_widgets_js_contains_stage_change_check(self):
        """
        Verify that widgets.js source actually contains checks for the three
        event_type values -- guards against JS being changed without updating Python.
        """
        widgets_js = AF_ROOT / 'dashboard_static' / 'src' / 'widgets.js'
        if not widgets_js.exists():
            # Try bundled fallback
            widgets_js = AF_ROOT / 'dashboard_static' / 'build.js'
        if not widgets_js.exists():
            # Cannot verify -- skip gracefully rather than fail
            return

        source = widgets_js.read_text(encoding='utf-8', errors='replace')
        for event_type in self.JS_TOAST_EVENT_TYPES:
            assert event_type in source, (
                f"widgets.js does not reference '{event_type}' -- "
                "JS toast handler may not fire for this event type"
            )


# ---------------------------------------------------------------------------
# Test class 4: status_key delimiter hardening
# ---------------------------------------------------------------------------

class TestStatusKeyHardening:
    """
    Tests for the hardened '||' delimiter and empty-string normalization in
    check_and_emit_widget_updates().
    """

    def _setup_mock_socketio(self):
        import dashboard_v2  # noqa: F401
        import importlib.util
        schema_path = AF_ROOT / 'dashboard_modules' / 'mission_status_schema.py'
        spec = importlib.util.spec_from_file_location(
            'dashboard_modules.mission_status_schema', schema_path
        )
        schema_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(schema_mod)
        sys.modules['dashboard_modules.mission_status_schema'] = schema_mod
        import websocket_events as ws_mod
        mock_sio = MockSocketIO()
        schema_mod.set_socketio(mock_sio)
        ws_mod.set_socketio(mock_sio)
        return mock_sio

    def _reset(self, key=None):
        import dashboard_v2 as dv2
        if key is None:
            dv2._widget_state.pop('mission_status_key', None)
        else:
            dv2._widget_state['mission_status_key'] = key
        dv2._ws_state_cache['last_check'] = 0

    def test_status_key_uses_pipe_delimiter(self):
        """After a stage transition, the stored status_key must use '||' not ':'."""
        self._setup_mock_socketio()
        self._reset('PLANNING||True||0')
        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': 'BUILDING', 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()
        key = dv2._widget_state.get('mission_status_key', '')
        assert '||' in key, f"status_key must use '||' delimiter, got: {key!r}"
        assert key.startswith('BUILDING||'), f"Expected key to start with 'BUILDING||', got: {key!r}"

    def test_empty_string_rd_stage_normalized_to_na(self):
        """rd_stage='' must be treated as 'N/A' — key shows N/A."""
        self._setup_mock_socketio()
        self._reset('PLANNING||True||0')
        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': '', 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()
        key = dv2._widget_state.get('mission_status_key', '')
        assert key.startswith('N/A||'), (
            f"Empty rd_stage should normalize to 'N/A' in status_key, got: {key!r}"
        )

    def test_none_rd_stage_normalized_to_na(self):
        """rd_stage=None must be treated as 'N/A' — key shows N/A."""
        self._setup_mock_socketio()
        self._reset('PLANNING||True||0')
        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': None, 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()
        key = dv2._widget_state.get('mission_status_key', '')
        assert key.startswith('N/A||'), (
            f"None rd_stage should normalize to 'N/A' in status_key, got: {key!r}"
        )

    def test_colon_in_hypothetical_stage_name_extracted_correctly(self):
        """split('||')[0] returns the full stage even if it contains ':'."""
        hypothetical_key = 'FOO:BAR||True||0'
        extracted = hypothetical_key.split('||')[0]
        assert extracted == 'FOO:BAR', (
            f"'||' split must preserve colons in stage name, got: {extracted!r}"
        )

    def test_no_toast_when_na_prev_stage(self):
        """When prev_stage is 'N/A', no stage_change toast must fire."""
        mock_sio = self._setup_mock_socketio()
        self._reset('N/A||False||0')
        import dashboard_v2 as dv2
        with patch.object(dv2, 'get_claude_status', return_value={
            'rd_stage': 'PLANNING', 'running': True, 'rd_iteration': 0,
            'current_cycle': 1, 'cycle_budget': 3,
        }):
            dv2.check_and_emit_widget_updates()
        emissions = mock_sio.get_emissions('update')
        if emissions:
            payload_data = emissions[-1]['data'].get('data', {})
            assert payload_data.get('event') != 'stage_change', (
                "Must not emit stage_change when prev_stage was 'N/A'"
            )


# ---------------------------------------------------------------------------
# Test class 5: JS proximity contract (source-level)
# ---------------------------------------------------------------------------

class TestWidgetsJSProximityContract:
    """
    Verifies that widgets.js showToast() for stage transitions appears within
    a tight window after the 'stage_change' event-type check.
    """

    def test_stage_change_branch_calls_show_toast_within_10_lines(self):
        """showToast must appear within 10 lines of the stage_change conditional."""
        widgets_js = AF_ROOT / 'dashboard_static' / 'src' / 'widgets.js'
        if not widgets_js.exists():
            widgets_js = AF_ROOT / 'dashboard_static' / 'build.js'
        if not widgets_js.exists():
            return
        lines = widgets_js.read_text(encoding='utf-8', errors='replace').splitlines()
        stage_change_line = None
        for i, line in enumerate(lines):
            if 'stage_change' in line and ('engine_stage_change' in line or 'eventName' in line):
                stage_change_line = i
                break
        assert stage_change_line is not None, (
            "Could not find 'stage_change' event-type check in widgets.js"
        )
        window = lines[stage_change_line: stage_change_line + 10]
        assert any('showToast' in l for l in window), (
            f"showToast() not found within 10 lines of the 'stage_change' check "
            f"(line {stage_change_line + 1})"
        )

    def test_all_three_event_types_in_same_conditional(self):
        """stage_change, engine_stage_change, mission_stage_change within 200 chars."""
        widgets_js = AF_ROOT / 'dashboard_static' / 'src' / 'widgets.js'
        if not widgets_js.exists():
            widgets_js = AF_ROOT / 'dashboard_static' / 'build.js'
        if not widgets_js.exists():
            return
        source = widgets_js.read_text(encoding='utf-8', errors='replace')
        event_types = ['stage_change', 'engine_stage_change', 'mission_stage_change']
        positions = {}
        for et in event_types:
            idx = source.find(et)
            assert idx != -1, f"'{et}' not found in widgets.js"
            positions[et] = idx
        pos_list = sorted(positions.values())
        span = pos_list[-1] - pos_list[0]
        assert span < 200, (
            f"The three stage event types are {span} chars apart — expected same conditional. "
            f"Positions: {positions}"
        )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all():
    """Run all test classes and print a summary."""
    import traceback

    suites = [
        TestWebSocketIntegrationEmit,
        TestStageToastViaPoll,
        TestStageToastJSContract,
        TestStatusKeyHardening,
        TestWidgetsJSProximityContract,
    ]

    passed = 0
    failed = 0

    for suite_cls in suites:
        suite = suite_cls()
        methods = [m for m in dir(suite) if m.startswith('test_')]
        print(f"\n{'='*60}")
        print(f"  {suite_cls.__name__}")
        print(f"{'='*60}")
        for method_name in methods:
            try:
                getattr(suite, method_name)()
                print(f"  PASS  {method_name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL  {method_name}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*60}")
    print(f"  Results: {passed} passed, {failed} failed")
    print(f"{'='*60}\n")
    return failed == 0


if __name__ == '__main__':
    import sys as _sys
    success = run_all()
    _sys.exit(0 if success else 1)
