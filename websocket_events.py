#!/usr/bin/env python3
"""
WebSocket Event Emitter Module

Centralized module for emitting WebSocket events from various parts of the AtlasForge system.
This module provides clean emit functions with:
- Lazy import of socketio (avoids circular imports)
- Rate limiting/debouncing for rapid events
- Consistent event format
- Error handling (silent failure to not block main operations)

Usage:
    from websocket_events import emit_file_created, emit_mission_updated

    # After creating a file
    emit_file_created('/path/to/file.py', 'code', 'mission_abc123')

    # After mission state change
    emit_mission_updated(mission_dict, 'stage_change')
"""

import time
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

# =============================================================================
# CONFIGURATION
# =============================================================================

# Rate limiting: max events per second per event type
MAX_EVENTS_PER_SECOND = 10
DEBOUNCE_WINDOW_MS = 100  # Debounce rapid-fire events

# Track last emit times for rate limiting
_last_emit_times: Dict[str, float] = {}
_emit_lock = threading.Lock()

# Cached socketio reference (lazy loaded)
_socketio = None

# Event queue for events generated before socketio is available
# This ensures no recommendations are lost if generated during dashboard startup
_event_queue: list = []
_event_queue_lock = threading.Lock()
MAX_QUEUED_EVENTS = 100  # Prevent memory bloat


# =============================================================================
# SOCKETIO ACCESS (LAZY LOADING)
# =============================================================================

def _get_socketio():
    """
    Get socketio instance with lazy loading to avoid circular imports.

    Returns:
        SocketIO instance or None if not available
    """
    global _socketio

    if _socketio is not None:
        return _socketio

    try:
        # Try to get socketio from dashboard_v2
        import sys
        if 'dashboard_v2' in sys.modules:
            dashboard = sys.modules['dashboard_v2']
            if hasattr(dashboard, 'socketio'):
                _socketio = dashboard.socketio
                return _socketio

        # Try direct import as fallback
        from dashboard_v2 import socketio
        _socketio = socketio
        return _socketio

    except ImportError:
        return None
    except Exception:
        return None


def set_socketio(socketio_instance):
    """
    Explicitly set the socketio instance.
    Call this from dashboard_v2.py after creating socketio.

    Args:
        socketio_instance: The Flask-SocketIO instance
    """
    global _socketio
    _socketio = socketio_instance
    # Flush any queued events now that socketio is available
    flush_queued_events()


def flush_queued_events():
    """
    Flush any events that were queued before socketio was available.

    Call this after socketio is initialized to deliver pending events.
    Thread-safe with locking.
    """
    global _event_queue

    socketio = _get_socketio()
    if socketio is None:
        return 0

    flushed = 0
    with _event_queue_lock:
        events_to_flush = _event_queue.copy()
        _event_queue = []

    for event in events_to_flush:
        try:
            socketio.emit(event['event'], {
                'room': event['room'],
                'data': event['data'],
                'timestamp': event.get('timestamp', datetime.now().isoformat()),
                'queued': True  # Mark as previously queued
            }, room=event['room'], namespace=event.get('namespace', '/widgets'))
            flushed += 1
        except Exception:
            pass  # Silent failure

    return flushed


def _queue_event(room: str, event: str, data: Dict[str, Any], namespace: str = '/widgets'):
    """
    Queue an event for later delivery when socketio becomes available.

    Args:
        room: The room to emit to
        event: Event name
        data: Event data
        namespace: WebSocket namespace
    """
    with _event_queue_lock:
        if len(_event_queue) < MAX_QUEUED_EVENTS:
            _event_queue.append({
                'room': room,
                'event': event,
                'data': data,
                'namespace': namespace,
                'timestamp': datetime.now().isoformat()
            })


# =============================================================================
# RATE LIMITING
# =============================================================================

def _should_emit(event_key: str) -> bool:
    """
    Check if an event should be emitted based on rate limiting.

    Args:
        event_key: Unique key for rate limiting (e.g., 'file_created:mission_abc')

    Returns:
        True if event should be emitted, False if rate limited
    """
    now = time.time()

    with _emit_lock:
        last_time = _last_emit_times.get(event_key, 0)
        min_interval = 1.0 / MAX_EVENTS_PER_SECOND

        if now - last_time < min_interval:
            return False

        _last_emit_times[event_key] = now

        # Cleanup old entries (older than 60 seconds)
        cutoff = now - 60
        keys_to_remove = [k for k, v in _last_emit_times.items() if v < cutoff]
        for k in keys_to_remove:
            del _last_emit_times[k]

        return True


def _safe_emit(room: str, event: str, data: Dict[str, Any], namespace: str = '/widgets', queue_if_unavailable: bool = False):
    """
    Safely emit a WebSocket event with error handling.

    Args:
        room: The room to emit to
        event: Event name
        data: Event data
        namespace: WebSocket namespace
        queue_if_unavailable: If True, queue the event when socketio is not available
    """
    socketio = _get_socketio()
    if socketio is None:
        if queue_if_unavailable:
            _queue_event(room, event, data, namespace)
        return

    try:
        socketio.emit(event, {
            'room': room,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }, room=room, namespace=namespace)
    except Exception:
        # Silent failure - don't block main operations
        pass


# =============================================================================
# FILE EVENTS
# =============================================================================

def emit_file_created(file_path: str, file_type: str, mission_id: str, metadata: Dict = None):
    """
    Emit event when a file is created during a mission.

    Args:
        file_path: Full path to the created file
        file_type: Type of file ('plan', 'code', 'test', 'artifact', 'research')
        mission_id: The mission that created this file
        metadata: Optional additional metadata
    """
    event_key = f'file_created:{mission_id}'
    if not _should_emit(event_key):
        return

    path = Path(file_path)
    data = {
        'event': 'file_created',
        'file_path': str(file_path),
        'file_name': path.name,
        'file_type': file_type,
        'file_extension': path.suffix,
        'mission_id': mission_id,
        'metadata': metadata or {}
    }

    _safe_emit('file_events', 'update', data)


def emit_file_modified(file_path: str, mission_id: str, change_type: str = 'modified'):
    """
    Emit event when a file is modified during a mission.

    Args:
        file_path: Full path to the modified file
        mission_id: The mission that modified this file
        change_type: Type of change ('modified', 'appended', 'truncated')
    """
    event_key = f'file_modified:{mission_id}:{file_path}'
    if not _should_emit(event_key):
        return

    path = Path(file_path)
    data = {
        'event': 'file_modified',
        'file_path': str(file_path),
        'file_name': path.name,
        'change_type': change_type,
        'mission_id': mission_id
    }

    _safe_emit('file_events', 'update', data)


# =============================================================================
# TRANSCRIPT ARCHIVAL EVENTS
# =============================================================================

def emit_transcript_archived(
    mission_id: str,
    archive_path: str,
    transcript_count: int,
    stats: Dict = None
):
    """
    Emit event when GlassBox transcripts are archived for a mission.

    Args:
        mission_id: The mission whose transcripts were archived
        archive_path: Path to the archive directory
        transcript_count: Number of transcripts archived
        stats: Optional manifest/stats data
    """
    event_key = f'transcript_archived:{mission_id}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'transcript_archived',
        'mission_id': mission_id,
        'archive_path': archive_path,
        'transcript_count': transcript_count,
        'stats': stats or {}
    }

    _safe_emit('glassbox_archive', 'update', data)

    # Also emit to glassbox room for widget refresh
    _safe_emit('glassbox', 'state_change', {
        'event': 'transcript_archived',
        'mission_id': mission_id,
        'transcript_count': transcript_count
    })


# =============================================================================
# RECOMMENDATION EVENTS
# =============================================================================

def emit_recommendation_added(recommendation: Dict, queue_if_unavailable: bool = True):
    """
    Emit event when a new mission recommendation is added.

    Args:
        recommendation: The recommendation dict with id, mission_title, etc.
        queue_if_unavailable: If True, queue the event if socketio is not available.
                             This ensures recommendations are not lost during startup.
    """
    rec_id = recommendation.get('id', 'unknown')
    event_key = f'recommendation_added:{rec_id}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'new_recommendation',
        'recommendation': {
            'id': recommendation.get('id'),
            'title': recommendation.get('mission_title', 'New Mission'),
            'description': (recommendation.get('mission_description', '') or '')[:200],
            'source_mission': recommendation.get('source_mission_id'),
            'source_type': recommendation.get('source_type', 'successful_completion'),
            'suggested_cycles': recommendation.get('suggested_cycles', 3),
            'rationale': recommendation.get('rationale', '')
        }
    }

    _safe_emit('recommendations', 'update', data, queue_if_unavailable=queue_if_unavailable)


# =============================================================================
# MISSION STATE EVENTS
# =============================================================================

def emit_mission_updated(mission_data: Dict, change_type: str):
    """
    Emit event when mission state changes.

    Args:
        mission_data: The current mission dict
        change_type: Type of change ('stage_change', 'iteration_change', 'started', 'stopped', 'completed')
    """
    mission_id = mission_data.get('mission_id', 'unknown')
    event_key = f'mission_updated:{mission_id}:{change_type}'
    if not _should_emit(event_key):
        return

    data = {
        'event': change_type,
        'mission_id': mission_id,
        'current_stage': mission_data.get('current_stage'),
        'iteration': mission_data.get('iteration', 0),
        'current_cycle': mission_data.get('current_cycle', 1),
        'cycle_budget': mission_data.get('cycle_budget', 1),
        'running': True  # Assumed running if we're emitting updates
    }

    _safe_emit('mission_status', 'state_change', {
        'event': f'mission_{change_type}',
        'data': data
    })


def emit_stage_change(mission_id: str, old_stage: str, new_stage: str, iteration: int = 0):
    """
    Emit event when mission stage changes.

    Args:
        mission_id: The mission ID
        old_stage: Previous stage
        new_stage: New stage
        iteration: Current iteration
    """
    event_key = f'stage_change:{mission_id}:{new_stage}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'stage_change',
        'mission_id': mission_id,
        'old_stage': old_stage,
        'new_stage': new_stage,
        'iteration': iteration,
        'timestamp': datetime.now().isoformat()
    }

    _safe_emit('mission_status', 'state_change', {
        'event': 'mission_stage_change',
        'data': data
    })


# =============================================================================
# GLASSBOX EVENTS
# =============================================================================

def emit_glassbox_event(event_type: str, mission_id: str, data: Dict = None):
    """
    Emit generic GlassBox introspection event.

    Args:
        event_type: Type of GlassBox event
        mission_id: The mission ID
        data: Optional event data
    """
    event_key = f'glassbox:{mission_id}:{event_type}'
    if not _should_emit(event_key):
        return

    event_data = {
        'event': event_type,
        'mission_id': mission_id,
        'details': data or {}
    }

    _safe_emit('glassbox', 'update', event_data)


# =============================================================================
# EXPLORATION EVENTS (AtlasForge)
# =============================================================================

def emit_exploration_update(mission_id: str, exploration_data: Dict):
    """
    Emit event when exploration graph is updated.

    Args:
        mission_id: The mission ID
        exploration_data: Exploration statistics and data
    """
    event_key = f'exploration:{mission_id}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'exploration_update',
        'mission_id': mission_id,
        'exploration': exploration_data
    }

    _safe_emit('exploration', 'update', data)


def emit_drift_alert(mission_id: str, alert_level: str, similarity: float, details: Dict = None):
    """
    Emit drift alert event.

    Args:
        mission_id: The mission ID
        alert_level: Alert level ('GREEN', 'YELLOW', 'ORANGE', 'RED')
        similarity: Current similarity score (0-1)
        details: Optional drift analysis details
    """
    event_key = f'drift_alert:{mission_id}:{alert_level}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'drift_alert',
        'mission_id': mission_id,
        'alert_level': alert_level,
        'similarity': similarity,
        'details': details or {}
    }

    _safe_emit('atlasforge_stats', 'state_change', {
        'event': 'atlasforge_drift_alert',
        'data': data
    })


# =============================================================================
# JOURNAL EVENTS
# =============================================================================

def emit_journal_entry(entry: Dict):
    """
    Emit event when a new journal entry is added.

    Args:
        entry: The journal entry dict
    """
    event_key = f'journal:{entry.get("timestamp", time.time())}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'new_entry',
        'entry': {
            'type': entry.get('type', 'unknown'),
            'timestamp': entry.get('timestamp'),
            'status': entry.get('status', ''),
            'message': (entry.get('message', '') or entry.get('work_done', ''))[:100],
            'full_message': entry.get('message', '') or entry.get('work_done', ''),
            'is_truncated': len(entry.get('message', '') or entry.get('work_done', '')) > 100
        }
    }

    _safe_emit('journal', 'update', data)


# =============================================================================
# BACKUP EVENTS
# =============================================================================

def emit_backup_created(mission_id: str, snapshot_id: str, snapshot_type: str = 'manual'):
    """
    Emit event when a backup snapshot is created.

    Args:
        mission_id: The mission ID
        snapshot_id: The snapshot identifier
        snapshot_type: Type of snapshot ('manual', 'scheduled', 'stage_transition')
    """
    event_key = f'backup:{mission_id}:{snapshot_id}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'backup_created',
        'mission_id': mission_id,
        'snapshot_id': snapshot_id,
        'snapshot_type': snapshot_type
    }

    _safe_emit('backup_status', 'update', data)


# =============================================================================
# QUEUE EVENTS
# =============================================================================

def emit_queue_updated(queue_data: Dict, change_type: str = 'updated'):
    """
    Emit event when queue is modified (mission added/removed/reordered).

    Args:
        queue_data: Queue data dict with missions, settings, etc.
        change_type: Type of change ('added', 'removed', 'reordered', 'updated')
    """
    event_key = f'queue_updated:{change_type}'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'queue_updated',
        'missions': queue_data.get('missions', []),
        'settings': queue_data.get('settings', {}),
        'queue_length': len(queue_data.get('missions', [])),
        'last_updated': queue_data.get('last_updated'),
        'change_type': change_type
    }

    _safe_emit('queue_updated', 'update', data)


def emit_queue_paused(paused: bool, paused_at: str = None, reason: str = None):
    """
    Emit event when queue is paused.

    Args:
        paused: Whether queue is now paused
        paused_at: Timestamp when paused
        reason: Reason for pause
    """
    event_key = 'queue_paused'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'queue_paused',
        'paused': paused,
        'paused_at': paused_at,
        'pause_reason': reason
    }

    _safe_emit('queue_paused', 'update', data)


def emit_queue_resumed():
    """
    Emit event when queue is resumed.
    """
    event_key = 'queue_resumed'
    if not _should_emit(event_key):
        return

    data = {
        'event': 'queue_resumed',
        'paused': False
    }

    _safe_emit('queue_resumed', 'update', data)
