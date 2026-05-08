"""
agent_sse.py — Server-Sent Events transport for agent activity streams.

Replaces the flask-socketio `mission_agents` / `investigation_agents` rooms.
Browser clients open EventSource on /api/agents/<id>/stream or
/api/agents/stream?context=mission|investigation. The browser handles
reconnect and Last-Event-ID natively.

Wire format:
    id: 42
    event: stream_line
    data: {"agent_id":"...","seq":42,...}

Lifecycle events use event names: agent_spawned, agent_complete, agent_error.
Slow-client backpressure surfaces as `event: gap` so clients can re-fetch.
"""

import json
import logging
import queue
from typing import Iterable, Optional

from flask import Blueprint, Response, request, jsonify, stream_with_context

import agent_stream_manager as _asm

logger = logging.getLogger(__name__)

agent_sse_bp = Blueprint('agent_sse', __name__)

# 15s heartbeat keeps proxies and intermediaries from closing the connection.
HEARTBEAT_SECONDS = 15.0


def _wire_event_name(payload: dict) -> str:
    """Map a broadcast payload to an SSE `event:` name."""
    if not isinstance(payload, dict):
        return 'stream_line'
    ev = payload.get('event')
    if ev in ('agent_spawned', 'agent_complete', 'agent_error', 'gap', 'agent_state_snapshot', 'agent_error_logged'):
        return ev
    return 'stream_line'


def _format_sse(payload: dict) -> str:
    """Format a payload as an SSE message with id, event, and data fields.

    Every event carries an `id:` line so the browser tracks lastEventID correctly.
    Lifecycle/gap events without an explicit seq use 0, which is safe because
    seq 0 is the sentinel for "before any real seq" and never appears in the ring buffer.
    """
    if not isinstance(payload, dict):
        payload = {'data': str(payload)}
    seq = payload.get('seq')
    name = _wire_event_name(payload)
    parts = []
    try:
        parts.append(f"id: {int(seq) if seq is not None else 0}")
    except (TypeError, ValueError):
        parts.append("id: 0")
    parts.append(f"event: {name}")
    parts.append(f"data: {json.dumps(payload, default=str)}")
    parts.append('')  # trailing blank line terminates the message
    parts.append('')
    return '\n'.join(parts)


def _resolve_last_seq() -> int:
    """Read Last-Event-ID header (browser-default) with ?since=N override."""
    raw = request.args.get('since')
    if raw is None:
        raw = request.headers.get('Last-Event-ID')
    try:
        return max(0, int(raw)) if raw else 0
    except (TypeError, ValueError):
        return 0


def _event_stream(key: str, last_seq: int, snapshot_prelude: Optional[Iterable[dict]] = None):
    """Yield SSE-formatted bytes for one connection.

    1. Atomic snapshot+register under the manager's broadcast lock.
    2. Yield optional snapshot_prelude (e.g. agent_state_snapshot).
    3. Drain the snapshot.
    4. Block on the per-connection queue; emit heartbeats on idle.
    5. On disconnect (GeneratorExit), unsubscribe.
    """
    snapshot, q = _asm._manager.subscribe(key, last_seq)
    try:
        if snapshot_prelude:
            for evt in snapshot_prelude:
                yield _format_sse(evt)
        for evt in snapshot:
            yield _format_sse(evt)
        # Initial flush comment — guarantees the browser receives headers
        # promptly so onopen fires even when the snapshot was empty.
        yield ': connected\n\n'
        while True:
            try:
                evt = q.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                yield ': keepalive\n\n'
                continue
            yield _format_sse(evt)
    except GeneratorExit:
        # Client disconnected — fall through to unsubscribe in finally.
        raise
    finally:
        _asm._manager.unsubscribe(key, q)


_SENTINEL_KEYS = frozenset(('__mission__', '__investigation__'))


@agent_sse_bp.route('/api/agents/<agent_id>/stream', methods=['GET'])
def agent_stream(agent_id: str):
    """Per-agent SSE endpoint. Honors Last-Event-ID."""
    if agent_id in _SENTINEL_KEYS:
        return jsonify({'error': 'Use /api/agents/stream?context=mission|investigation for fan-in'}), 400
    last_seq = _resolve_last_seq()
    return Response(
        stream_with_context(_event_stream(agent_id, last_seq)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


@agent_sse_bp.route('/api/agents/stream', methods=['GET'])
def agents_fanin_stream():
    """Fan-in SSE: all agents for a panel context (mission|investigation).

    Emits an `agent_state_snapshot` first event so the browser can render currently-
    active agents without a synchronous REST call, then streams per-agent events.
    """
    context = request.args.get('context', 'mission')
    if context not in ('mission', 'investigation'):
        context = 'mission'
    key = (
        _asm.AgentStreamManager.MISSION_KEY
        if context == 'mission'
        else _asm.AgentStreamManager.INVESTIGATION_KEY
    )
    last_seq = _resolve_last_seq()
    # Always send the active-agent snapshot for fan-in streams. EventSource may
    # reconnect with a persisted Last-Event-ID, but fan-in seq values are
    # per-agent rather than globally monotonic, so `subscribe()` intentionally
    # replays from 0. Keeping the snapshot tied to last_seq caused refreshed
    # dashboards to miss disk-hydrated agents until their next stream line.
    snapshot_prelude = [{
        'event': 'agent_state_snapshot',
        'context': context,
        'agents': _asm._manager.get_active_agent_snapshot(context=context),
    }]
    return Response(
        stream_with_context(_event_stream(key, last_seq, snapshot_prelude=snapshot_prelude)),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        },
    )


@agent_sse_bp.route('/api/agents/<agent_id>/history', methods=['GET'])
def agent_history(agent_id: str):
    """Cold-load JSON dump of ring-buffer entries with seq > since.

    Used by the frontend `gap` recovery path when the SSE queue dropped events.
    """
    if agent_id in _SENTINEL_KEYS:
        return jsonify({'error': 'reserved agent_id; use /api/agents/stream?context=... for fan-in'}), 400
    try:
        since = int(request.args.get('since', '0'))
    except (TypeError, ValueError):
        since = 0
    if since < 0:
        since = 0
    events = _asm._manager.get_ring_since(agent_id, since)
    return jsonify({'success': True, 'agent_id': agent_id, 'since': since, 'events': events})
