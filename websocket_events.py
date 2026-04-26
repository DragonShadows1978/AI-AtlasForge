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
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

# =============================================================================
# CONFIGURATION
# =============================================================================

logger = logging.getLogger(__name__)

# Rate limiting: max events per second per event type
MAX_EVENTS_PER_SECOND = 10
DEBOUNCE_WINDOW_MS = 100  # Debounce rapid-fire events
MAX_RATE_LIMIT_ENTRIES = 1000  # Cap on _last_emit_times dict size

# Track last emit times for rate limiting
_last_emit_times: Dict[str, float] = {}
_emit_lock = threading.Lock()

# Cached socketio reference (lazy loaded)
_socketio = None
_socketio_lock = threading.Lock()

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

    Thread-safe: uses double-checked locking to prevent duplicate init.

    Returns:
        SocketIO instance or None if not available
    """
    global _socketio

    # Fast path: already initialized (no lock needed for read)
    if _socketio is not None:
        return _socketio

    # Do the potentially-blocking import OUTSIDE the lock to avoid deadlock
    # if dashboard_v2 itself imports from this module during initialization.
    found = None
    try:
        import sys
        if 'dashboard_v2' in sys.modules:
            dashboard = sys.modules['dashboard_v2']
            if hasattr(dashboard, 'socketio'):
                found = dashboard.socketio
        if found is None:
            from dashboard_v2 import socketio as _sio
            found = _sio
    except ImportError:
        pass
    except Exception:
        pass

    # Assign under lock (double-checked) to prevent races between threads
    with _socketio_lock:
        if _socketio is None and found is not None:
            _socketio = found
        return _socketio


def set_socketio(socketio_instance):
    """
    Explicitly set the socketio instance.
    Call this from dashboard_v2.py after creating socketio.

    Args:
        socketio_instance: The Flask-SocketIO instance
    """
    global _socketio
    with _socketio_lock:
        _socketio = socketio_instance
    # Flush any queued events now that socketio is available
    flush_queued_events()


def flush_queued_events():
    """
    Flush any events that were queued before socketio was available.

    Call this after socketio is initialized to deliver pending events.
    Thread-safe with locking.

    Atomically swaps the queue under the lock so only pre-existing events are
    drained; any events enqueued AFTER the lock block are deferred to the next flush.
    """
    global _event_queue

    socketio = _get_socketio()
    if socketio is None:
        return 0

    # Atomically drain the pre-existing queue content.
    # Any events enqueued AFTER this lock block are deferred to the next flush.
    with _event_queue_lock:
        if not _event_queue:
            return 0
        events_to_flush = _event_queue
        _event_queue = []

    flushed = 0
    for event in events_to_flush:
        try:
            socketio.emit(event['event'], {
                'room': event['room'],
                'data': event['data'],
                'timestamp': event.get('timestamp', datetime.now().isoformat()),
                'queued': True  # Mark as previously queued
            }, room=event['room'], namespace=event.get('namespace', '/widgets'))
            flushed += 1
        except Exception as e:
            logger.warning("flush_queued_events failed: %s", e)

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
        if MAX_EVENTS_PER_SECOND <= 0:
            return True
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

        # Cap dict size to prevent unbounded growth
        if len(_last_emit_times) > MAX_RATE_LIMIT_ENTRIES:
            sorted_keys = sorted(_last_emit_times, key=_last_emit_times.__getitem__)
            for k in sorted_keys[:len(sorted_keys) // 2]:
                del _last_emit_times[k]

        return True


def _safe_emit(room: str, event: str, data: Dict[str, Any], namespace: str = '/widgets',
               queue_if_unavailable: bool = False, event_key: str = None):
    """
    Safely emit a WebSocket event with error handling and integrated rate limiting.

    Rate limiting is checked only after confirming socketio availability:
    - If socketio is unavailable and queue_if_unavailable=True: queue the event WITHOUT
      consuming a rate-limit credit (queued events are not deliveries).
    - If socketio is unavailable and queue_if_unavailable=False: drop silently WITHOUT
      consuming a rate-limit credit.
    - If socketio is available: check rate limit, then emit if allowed.

    Args:
        room: The room to emit to
        event: Event name
        data: Event data
        namespace: WebSocket namespace
        queue_if_unavailable: If True, queue the event when socketio is not available
        event_key: Rate-limit key; if None, rate limiting is skipped (always emit)
    """
    socketio = _get_socketio()
    if socketio is None:
        if queue_if_unavailable:
            _queue_event(room, event, data, namespace)
        # No rate-limit credit consumed when socketio unavailable (queue or drop)
        return

    # Rate limiting: only check when socketio is available (delivery is real)
    if event_key is not None and not _should_emit(event_key):
        return

    try:
        socketio.emit(event, {
            'room': room,
            'data': data,
            'timestamp': datetime.now().isoformat()
        }, room=room, namespace=namespace)
    except Exception as e:
        logger.debug("_safe_emit failed: %s", e)


def emit_widget_update(room: str, data: Dict[str, Any]):
    """
    Emit an 'update' event to a specific widget room on the /widgets namespace.

    This is the generic widget update function. Used by agent_stream_manager and
    other components that need to push real-time data to the dashboard.

    Args:
        room: The room name (must be in VALID_WS_ROOMS on the server)
        data: The event payload to deliver to all subscribers
    """
    # No event_key: no rate limiting for generic widget updates (caller controls frequency)
    _safe_emit(room, 'update', data, namespace='/widgets', queue_if_unavailable=False)


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
    event_key = f'file_created:{mission_id}:{file_path}'

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

    _safe_emit('file_events', 'update', data, event_key=event_key)


def emit_file_modified(file_path: str, mission_id: str, change_type: str = 'modified'):
    """
    Emit event when a file is modified during a mission.

    Args:
        file_path: Full path to the modified file
        mission_id: The mission that modified this file
        change_type: Type of change ('modified', 'appended', 'truncated')
    """
    event_key = f'file_modified:{mission_id}:{file_path}'

    path = Path(file_path)
    data = {
        'event': 'file_modified',
        'file_path': str(file_path),
        'file_name': path.name,
        'change_type': change_type,
        'mission_id': mission_id
    }

    _safe_emit('file_events', 'update', data, event_key=event_key)


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

    data = {
        'event': 'transcript_archived',
        'mission_id': mission_id,
        'archive_path': archive_path,
        'transcript_count': transcript_count,
        'stats': stats or {}
    }

    _safe_emit('glassbox_archive', 'update', data, event_key=event_key)

    # Also emit to glassbox room for widget refresh (no rate-limit key: always deliver)
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

    _safe_emit('recommendations', 'update', data,
               queue_if_unavailable=queue_if_unavailable, event_key=event_key)


# =============================================================================
# MISSION STATE EVENTS
# =============================================================================

def emit_mission_updated(mission_data: Dict, change_type: str):
    """
    Emit event when mission state changes.

    Routes through the canonical mission_status_schema to ensure consistent
    field names (rd_stage, rd_iteration, current_cycle) across all emitters.

    Args:
        mission_data: The current mission dict
        change_type: Type of change ('stage_change', 'iteration_change', 'started', 'stopped', 'completed')
    """
    mission_id = mission_data.get('mission_id', 'unknown')
    event_key = f'mission_updated:{mission_id}:{change_type}'

    socketio = _get_socketio()
    if socketio is None:
        return
    if not _should_emit(event_key):
        return

    from dashboard_modules.mission_status_schema import emit_mission_status
    # Map legacy field names to canonical via the schema builder
    status = {
        'rd_stage': mission_data.get('current_stage', mission_data.get('rd_stage', '')),
        'rd_iteration': mission_data.get('iteration', mission_data.get('rd_iteration', 0)),
        'current_cycle': mission_data.get('current_cycle', 1),
        'cycle_budget': mission_data.get('cycle_budget', 1),
        'mission_id': mission_id,
        'running': True,
    }
    emit_mission_status(status, event_type=f'mission_{change_type}')


def emit_stage_change(mission_id: str, old_stage: str, new_stage: str, iteration: int = 0):
    """
    Emit event when mission stage changes.

    Routes through the canonical mission_status_schema to ensure consistent
    field names across all emitters.

    Args:
        mission_id: The mission ID
        old_stage: Previous stage
        new_stage: New stage
        iteration: Current iteration
    """
    event_key = f'stage_change:{mission_id}:{new_stage}'

    socketio = _get_socketio()
    if socketio is None:
        return
    if not _should_emit(event_key):
        return

    from dashboard_modules.mission_status_schema import emit_mission_status
    status = {
        'rd_stage': new_stage,
        'rd_iteration': iteration,
        'mission_id': mission_id,
        'running': True,
    }
    emit_mission_status(status, event_type='mission_stage_change', old_stage=old_stage)


# =============================================================================
# RESEARCH AGENT EVENTS
# =============================================================================

def emit_research_event(
    agent_id: str,
    label: str,
    status: str,
    message: str,
    topic_index: int = 0,
    total_topics: int = 0,
    sources_found: int = 0
):
    """
    Emit a research progress event over the dedicated `research_progress` socket.io room.

    Called by the ResearchStreamEmitter in af_engine/orchestrator.py during
    the pre-planning research phase so progress is visible in Mission Activity.

    Args:
        agent_id: Unique ID for this research session (e.g. 'researcher_abc123')
        label: Display label ('Researcher Agent')
        status: 'running' | 'complete' | 'error'
        message: Human-readable progress message
        topic_index: Current topic being researched (1-based)
        total_topics: Total topics to research
        sources_found: Number of sources found so far
    """
    # M5 fix: validate status to prevent None/invalid values reaching the frontend
    _valid_statuses = ('running', 'complete', 'error')
    if status not in _valid_statuses:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "emit_research_event: invalid status %r; defaulting to 'running'", status)
        status = 'running'

    # Coerce message to str to handle None/int inputs safely
    message = str(message) if message is not None else ''
    # LT1 fix: include topic_index in rate-limit key to prevent 40-char prefix collisions
    event_key = f'research_event:{agent_id}:{topic_index}:{message[:30]}'

    data = {
        'event': 'research_progress',
        'agent_id': agent_id,
        'label': label,
        'status': status,
        'message': message,
        'topic_index': topic_index,
        'total_topics': total_topics,
        'sources_found': sources_found,
        'timestamp': datetime.now().isoformat()
    }

    # Research progress is rendered inside the Mission panel by activity-mission.js,
    # but it is NOT a stream-line event — the SSE transport for agent activity
    # only carries actual subprocess output. Send research progress over its own
    # dedicated socket.io room so the frontend can subscribe independently.
    _safe_emit('research_progress', 'update', data, event_key=event_key)


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

    event_data = {
        'event': event_type,
        'mission_id': mission_id,
        'details': data or {}
    }

    _safe_emit('glassbox', 'update', event_data, event_key=event_key)


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

    data = {
        'event': 'exploration_update',
        'mission_id': mission_id,
        'exploration': exploration_data
    }

    _safe_emit('exploration', 'update', data, event_key=event_key)


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
    }, event_key=event_key)


# =============================================================================
# JOURNAL EVENTS
# =============================================================================

def emit_journal_entry(entry: Dict):
    """
    Emit event when a new journal entry is added.

    Args:
        entry: The journal entry dict
    """
    _ts = entry.get("timestamp")
    event_key = f'journal:{_ts}' if _ts is not None else f'journal:{entry.get("type", "unknown")}'

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

    _safe_emit('journal', 'update', data, event_key=event_key)


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

    data = {
        'event': 'backup_created',
        'mission_id': mission_id,
        'snapshot_id': snapshot_id,
        'snapshot_type': snapshot_type
    }

    _safe_emit('backup_status', 'update', data, event_key=event_key)


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

    data = {
        'event': 'queue_updated',
        'missions': queue_data.get('missions', []),
        'settings': queue_data.get('settings', {}),
        'queue_length': len(queue_data.get('missions', [])),
        'last_updated': queue_data.get('last_updated'),
        'change_type': change_type
    }

    _safe_emit('queue_updated', 'update', data, event_key=event_key)


def emit_queue_paused(paused: bool, paused_at: str = None, reason: str = None):
    """
    Emit event when queue is paused.

    Args:
        paused: Whether queue is now paused
        paused_at: Timestamp when paused
        reason: Reason for pause
    """
    event_key = 'queue_paused'

    data = {
        'event': 'queue_paused',
        'paused': paused,
        'paused_at': paused_at,
        'pause_reason': reason
    }

    _safe_emit('queue_paused', 'update', data, event_key=event_key)


def emit_queue_resumed():
    """
    Emit event when queue is resumed.
    """
    event_key = 'queue_resumed'

    data = {
        'event': 'queue_resumed',
        'paused': False
    }

    _safe_emit('queue_resumed', 'update', data, event_key=event_key)


def emit_mission_auto_started(mission_id: str, mission_title: str, queue_id: str = None, source: str = "auto"):
    """
    Emit event when a mission is automatically started from the queue.

    This notifies the dashboard so it can:
    1. Display a toast notification
    2. Trigger browser notification (if enabled)
    3. Refresh queue display

    Args:
        mission_id: The new mission's ID
        mission_title: Title/description of the started mission
        queue_id: Original queue item ID (if available)
        source: Source of auto-start trigger:
                - "queue_auto" - Triggered by queue completion
                - "queue_next_button" - Manual "Start Next" button
                - "idle_auto_start" - Triggered by idle state detection
    """
    event_key = f'mission_auto_started:{mission_id}'

    data = {
        'event': 'mission_auto_started',
        'mission_id': mission_id,
        'mission_title': mission_title,
        'queue_id': queue_id,
        'source': source,
        'timestamp': datetime.now().isoformat()
    }

    _safe_emit('queue_auto_start', 'update', data,
               queue_if_unavailable=True, event_key=event_key)
