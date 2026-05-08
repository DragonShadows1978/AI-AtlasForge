"""
dashboard_modules.mission_status_schema - Canonical Schema for mission_status WebSocket Events

Provides a single source of truth for all mission_status payloads emitted over WebSocket.
All emission paths (check_and_emit_widget_updates, watch_engine_stage, broadcast_state_change,
websocket_events.py, get_initial_room_data) MUST route through build_mission_status() to ensure
consistent field names and types.

Canonical Field Contract
========================

Required fields (always present, validated by validate_mission_status):

    Field           Type    Default     Description
    --------------- ------- ----------- -------------------------------------------
    rd_stage        str     'N/A'       Current R&D stage name (PLANNING, BUILDING,
                                        TESTING, ANALYZING, CYCLE_END, COMPLETE).
                                        Falsy values (None, '') default to 'N/A'.
    rd_iteration    int     0           Current iteration within the stage.
    current_cycle   int     1           Current cycle number (1-based).
    cycle_budget    int     1           Total cycles allocated to this mission.
    running         bool    False       Whether the mission engine is active.

Optional fields (copied from source or filled with defaults):

    mission_id, project_name, project_workspace, provider, mode, pid,
    boot_count, total_cycles, last_boot, current_task, mission,
    mission_preview, original_mission, stages, event, old_stage, timestamp.

Computed fields (auto-calculated unless explicitly provided):

    cycles_remaining    int     max(0, cycle_budget - current_cycle)
    is_last_cycle       bool    current_cycle >= cycle_budget

Legacy Field Normalization
==========================

The builder automatically maps legacy field names to canonical ones:

    current_stage -> rd_stage
    stage         -> rd_stage
    new_stage     -> rd_stage
    iteration     -> rd_iteration
    cycle         -> current_cycle

When both legacy and canonical names are present, the canonical value wins.

Usage Guide
===========

To emit a mission_status update from ANY code path:

    from dashboard_modules.mission_status_schema import emit_mission_status

    emit_mission_status(status_dict, event_type='my_event', old_stage='PLANNING')

Do NOT emit 'mission_status' WebSocket events directly via socketio.emit().
Always use emit_mission_status() to guarantee schema consistency.

To build a payload without emitting (e.g. for REST /api/status):

    from dashboard_modules.mission_status_schema import build_mission_status

    payload = build_mission_status(raw_status_dict)
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Required fields and their expected Python types
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {
    'rd_stage': str,
    'rd_iteration': int,
    'current_cycle': int,
    'cycle_budget': int,
    'running': bool,
}

# Optional fields with defaults
OPTIONAL_DEFAULTS: Dict[str, Any] = {
    'mission_id': '',
    'project_name': '',
    'project_workspace': '',
    'provider': '',
    'mode': 'unknown',
    'pid': None,
    'boot_count': 0,
    'total_cycles': 0,
    'last_boot': None,
    'current_task': None,
    'mission': '',
    'mission_preview': '',
    'original_mission': '',
    'cycles_remaining': 0,
    'is_last_cycle': False,
    'stages': [],
    'event': None,
    'old_stage': None,
    'timestamp': '',
    # Mission-type profile fields (cycle 2). Default to full_rd for
    # backwards-compat with pre-profile missions.
    'mission_type': 'full_rd',
    'mission_type_label': 'Full R&D',
    'enabled_stages': [],
    'stop_after_profile_complete': False,
}

# Legacy field name mappings: legacy_name -> canonical_name
_LEGACY_FIELD_MAP = {
    'current_stage': 'rd_stage',
    'stage': 'rd_stage',
    'new_stage': 'rd_stage',
    'iteration': 'rd_iteration',
    'cycle': 'current_cycle',
}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_mission_status(payload: dict) -> dict:
    """Validate a mission_status payload against the canonical schema.

    Checks that all required fields are present and have the correct type.
    Logs warnings for missing/wrong-type fields in production rather than
    raising, to avoid crashing the dashboard.  Returns the payload unchanged.

    Raises:
        ValueError: If a required field is missing or has wrong type.
    """
    errors: list[str] = []

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in payload:
            errors.append(f"Missing required field: '{field}'")
        elif not isinstance(payload[field], expected_type):
            actual = type(payload[field]).__name__
            errors.append(
                f"Wrong type for '{field}': expected {expected_type.__name__}, got {actual}"
            )

    if errors:
        msg = "mission_status payload validation failed: " + "; ".join(errors)
        logger.warning(msg)
        raise ValueError(msg)

    return payload


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_mission_status(
    status_data: dict,
    event_type: Optional[str] = None,
) -> dict:
    """Build a canonical mission_status payload from raw status data.

    Accepts dicts from any source (get_claude_status(), StateManager, legacy
    websocket_events) and normalizes field names, fills defaults, validates.

    Args:
        status_data: Raw status dict (may use legacy field names).
        event_type:  Optional event descriptor (e.g. 'engine_stage_change').

    Returns:
        A validated dict with canonical field names.
    """
    src = dict(status_data)  # shallow copy to avoid mutation

    # --- Normalize legacy field names ----------------------------------------
    for legacy, canonical in _LEGACY_FIELD_MAP.items():
        if legacy in src and canonical not in src:
            src[canonical] = src.pop(legacy)
        elif legacy in src:
            # Both present — drop legacy silently
            src.pop(legacy, None)

    # --- Assemble canonical payload ------------------------------------------
    payload: Dict[str, Any] = {}

    # Required fields (with sensible fallbacks so callers don't crash)
    payload['rd_stage'] = str(src.get('rd_stage') or 'N/A')
    payload['rd_iteration'] = _coerce_int(src.get('rd_iteration', 0))
    payload['current_cycle'] = _coerce_int(src.get('current_cycle', 1))
    payload['cycle_budget'] = _coerce_int(src.get('cycle_budget', 1))
    payload['running'] = bool(src.get('running', False))

    # Optional fields — copy from source or use default
    for field, default in OPTIONAL_DEFAULTS.items():
        if field in src:
            payload[field] = src[field]
        else:
            payload[field] = default

    # Computed convenience fields
    if 'cycles_remaining' not in src:
        payload['cycles_remaining'] = max(0, payload['cycle_budget'] - payload['current_cycle'])
    if 'is_last_cycle' not in src:
        payload['is_last_cycle'] = payload['current_cycle'] >= payload['cycle_budget']

    # Event metadata
    if event_type is not None:
        payload['event'] = event_type

    # Timestamp — always set
    if not payload.get('timestamp'):
        payload['timestamp'] = datetime.now().isoformat()

    return payload


# ---------------------------------------------------------------------------
# Emitter
# ---------------------------------------------------------------------------

# Cached socketio reference (lazy loaded)
_socketio = None


def set_socketio(socketio_instance):
    """Set the socketio instance. Called once from dashboard_v2 startup."""
    global _socketio
    _socketio = socketio_instance


def _get_socketio():
    """Lazy-load socketio instance."""
    global _socketio
    if _socketio is not None:
        return _socketio
    try:
        import sys
        if 'dashboard_v2' in sys.modules:
            dashboard = sys.modules['dashboard_v2']
            if hasattr(dashboard, 'socketio'):
                _socketio = dashboard.socketio
                return _socketio
    except Exception:
        pass
    return None


def emit_mission_status(
    status_data: dict,
    event_type: Optional[str] = None,
    old_stage: Optional[str] = None,
    socketio_instance=None,
) -> dict:
    """Single canonical emission point for all mission_status WebSocket payloads.

    Builds, validates, and emits the payload.  Returns the validated payload
    (useful for tests / callers that need the data).

    Args:
        status_data:       Raw status dict from any source.
        event_type:        Optional event descriptor.
        old_stage:         If a stage transition, the previous stage name.
        socketio_instance: Override socketio instance (for testing).

    Returns:
        The validated canonical payload dict.
    """
    payload = build_mission_status(status_data, event_type)
    if old_stage:
        payload['old_stage'] = old_stage

    # Validate — log warning on failure but don't crash dashboard
    try:
        validate_mission_status(payload)
    except ValueError:
        pass  # Warning already logged

    # Emit via socketio
    sio = socketio_instance or _get_socketio()
    if sio:
        try:
            sio.emit('update', {
                'room': 'mission_status',
                'data': payload,
                'timestamp': payload['timestamp'],
            }, room='mission_status', namespace='/widgets')
        except Exception as e:
            logger.debug(f"mission_status emit failed: {e}")

    return payload


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_int(val: Any) -> int:
    """Coerce a value to int, defaulting to 0."""
    if isinstance(val, int):
        return val
    try:
        return int(val)
    except (TypeError, ValueError, OverflowError):
        return 0
