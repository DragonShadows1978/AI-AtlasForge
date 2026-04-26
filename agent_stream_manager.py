"""
agent_stream_manager.py — Real-time agent activity streaming for AtlasForge dashboard.

Architecture (post-rebuild):
  subprocess.Popen stdout → daemon writer thread (stamps `seq`, writes JSONL)
                                                    → AgentStreamWatcher thread
                                                          → broadcast(event):
                                                              1. ring_buffer[agent_id].append(event)
                                                              2. fan-out to per-connection subscriber Queues
                                                          → SSE generator → browser EventSource

JSONL is the durable log of record. The ring buffer is the live transport.
Per-agent monotonic `seq` enables Last-Event-ID resume.
"""

import json
import os
import queue
import threading
import time
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional, Tuple, List, Dict

# =============================================================================
# STREAM DIRECTORY
# =============================================================================

STREAM_DIR = Path('/home/vader/AI-AtlasForge/state/agent_streams')
CODEX_SESSIONS_DIR = Path.home() / '.codex' / 'sessions'
CODEX_TRANSCRIPT_DISCOVERY_TIMEOUT = 12.0
CODEX_TRANSCRIPT_POLL_INTERVAL = 0.2
CODEX_TRANSCRIPT_MAX_SCAN = 200
RECENT_AGENT_WINDOW_SECONDS = 1800
_MAX_ACTIVE_AGENTS_BYTES = 10 * 1024 * 1024  # 10 MB — OOM guard for active_agents.json

logger = logging.getLogger(__name__)

# Sentinel used by prewarm_active_agents() to claim a slot in self._agents
# before file I/O completes, eliminating the TOCTOU window between the
# first-check lock and the insert lock.
_PREWARM_SENTINEL = object()


def _ensure_stream_dir():
    STREAM_DIR.mkdir(parents=True, exist_ok=True)


# =============================================================================
# SEQ HELPERS — monotonic per-agent sequence numbers stamped onto JSONL lines
# =============================================================================

def _scan_max_seq(stream_file: Path) -> int:
    """Return the highest `seq` field found in a JSONL stream file.

    Existing files without a `seq` field on each line return 0 (legacy compat).
    Used by register_agent() to resume the per-agent counter across restarts.
    """
    if not stream_file.exists():
        return 0
    max_seq = 0
    try:
        with open(stream_file, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if not isinstance(obj, dict):
                    continue
                s = obj.get('seq')
                if isinstance(s, int) and not isinstance(s, bool) and s > max_seq:
                    max_seq = s
    except (OSError, IOError):
        pass
    return max_seq


def _wrap_with_seq(raw_line, seq: int) -> str:
    """Wrap a raw provider stdout line with a {seq, raw} envelope for JSONL persistence.

    The envelope is `{"seq": N, "raw": "<original line>"}` — single json.dumps,
    ~30 bytes overhead per line. The reader unwraps via _unwrap_seq_line().
    """
    if not isinstance(seq, int) or isinstance(seq, bool):
        raise TypeError(f"_wrap_with_seq: seq must be int, got {type(seq).__name__}: {seq!r}")
    if not isinstance(raw_line, str):
        raw_line = str(raw_line) if raw_line is not None else ''
    return json.dumps({'seq': seq, 'raw': raw_line.rstrip('\n')}) + '\n'


def _unwrap_seq_line(line: str) -> Tuple[Optional[int], str]:
    """Return (seq, raw_provider_line). Legacy lines without `seq` return (None, line)."""
    stripped = line.strip()
    if not stripped:
        return None, ''
    try:
        obj = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None, stripped
    if isinstance(obj, dict) and 'seq' in obj and 'raw' in obj:
        seq = obj.get('seq')
        raw = obj.get('raw')
        if not isinstance(raw, str):
            logger.warning("_unwrap_seq_line: non-string 'raw' field (type=%s) in seq envelope", type(raw).__name__)
            raw = None
        if isinstance(seq, int) and not isinstance(seq, bool):
            return seq, raw if raw is not None else ''
    # Legacy: the whole line IS the provider payload
    return None, stripped


# =============================================================================
# AGENT CONTEXT — per-agent metadata
# =============================================================================

class AgentContext:
    """Metadata for a single spawned agent subprocess."""

    def __init__(self, agent_id: str, context: str, label: str, pid: Optional[int] = None):
        if not isinstance(agent_id, str):
            raise TypeError(f"agent_id must be a str, got {type(agent_id).__name__}: {agent_id!r}")
        if not agent_id or not agent_id.strip():
            raise ValueError("agent_id must be a non-empty, non-whitespace string")
        if not isinstance(context, str):
            raise TypeError(f"context must be a str, got {type(context).__name__}: {context!r}")
        if context not in ('mission', 'investigation'):
            raise ValueError(f"context must be 'mission' or 'investigation', got {context!r}")
        self.agent_id = agent_id
        self.context = context      # 'mission' | 'investigation'
        self.label = label          # Display label e.g. "PLAN Agent 1", "Sub-0"
        # Sanitize agent_id and context to prevent path traversal: whitelist-only approach
        import re as _re
        safe_id = _re.sub(r'[^a-zA-Z0-9_\-]', '_', agent_id)
        safe_context = _re.sub(r'[^a-zA-Z0-9_\-]', '_', context)
        self.stream_file = STREAM_DIR / f"{safe_context}_{safe_id}.jsonl"
        self.status = 'running'     # 'running' | 'complete' | 'error'
        self.spawned_at = time.time()
        self.started_at: float = self.spawned_at   # Timing: set at construction
        self.completed_at: Optional[float] = None  # Timing: set by complete_agent()
        self.pid = pid
        self.error: Optional[str] = None
        self._parsed_lines: deque = deque(maxlen=200)  # Bounded buffer of parsed event dicts
        self._lock = threading.Lock()
        # Monotonic per-agent sequence counter; resumed from JSONL on registration.
        # Always read/incremented under seq_lock.
        self.next_seq: int = 1
        self.seq_lock = threading.Lock()
        # Ring buffer of broadcast events (deques of dicts with `seq` key).
        # Maxlen=500 is the live-replay window for Last-Event-ID resume.
        self.ring: deque = deque(maxlen=500)
        self._watcher_started = False
        self._watcher_lock = threading.Lock()
        self._tail_start_pos: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            'agent_id': self.agent_id,
            'context': self.context,
            'label': self.label,
            'status': self.status,
            'spawned_at': self.spawned_at,
            'started_at': self.started_at,
            'completed_at': self.completed_at,
            'pid': self.pid,
            'error': self.error,
        }


def _copy_agent_fields(info: dict) -> dict:
    """Normalize agent metadata for reconnect replay."""
    agent = dict(info)
    started_at = agent.get('started_at') or agent.get('spawned_at')
    completed_at = agent.get('completed_at')
    if started_at is not None:
        agent['started_at'] = started_at
    if completed_at is not None and started_at is not None:
        agent['duration_seconds'] = round(completed_at - started_at, 1)
    return agent


# =============================================================================
# JSONL LINE PARSER
# =============================================================================

def _parse_jsonl_line(line: str) -> Optional[dict]:
    """
    Parse one JSONL line from Claude CLI --output-format stream-json.

    Returns normalized event dict:
      {'event_type': str, 'display_text': str, 'raw': str}

    event_type: 'thinking' | 'tool_call' | 'tool_result' | 'error' | 'raw'
    """
    raw = line.strip()
    if not raw:
        return None

    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        # Not JSON — raw text line (e.g. from non-stream-json provider)
        return {'event_type': 'raw', 'display_text': raw[:400], 'raw': raw}

    if not isinstance(obj, dict):
        # Valid JSON but not an object (e.g. null, number, array) — treat as raw
        return {'event_type': 'raw', 'display_text': raw[:400], 'raw': raw}

    # Codex exec transcript format
    record_type = obj.get('type', '')
    payload = obj.get('payload', {})
    if record_type == 'event_msg' and isinstance(payload, dict):
        event_type = payload.get('type', '')
        if event_type == 'agent_message':
            message = str(payload.get('message', '')).strip()
            if message:
                return {
                    'event_type': 'thinking',
                    'display_text': message[:600],
                    'raw': raw,
                }
            return None
        if event_type in ('task_started', 'task_complete', 'token_count', 'user_message'):
            return None

    if record_type == 'response_item' and isinstance(payload, dict):
        item_type = payload.get('type', '')
        if item_type == 'function_call':
            name = payload.get('name', '?')
            args = payload.get('arguments', '')
            if not isinstance(args, str):
                try:
                    args = json.dumps(args)
                except Exception:
                    args = str(args)
            return {
                'event_type': 'tool_call',
                'display_text': f"{name}({args[:300]})",
                'raw': raw,
            }
        if item_type == 'function_call_output':
            output = payload.get('output', '')
            if not isinstance(output, str):
                try:
                    output = json.dumps(output)
                except Exception:
                    output = str(output)
            return {
                'event_type': 'tool_result',
                'display_text': output[:400],
                'raw': raw,
            }
        if item_type in ('message', 'reasoning'):
            return None

    msg_type = obj.get('type', '')
    message = obj.get('message', {})
    raw_content = message.get('content', []) if isinstance(message, dict) else []
    content_blocks = raw_content if isinstance(raw_content, list) else []

    # stream-json format: each line is a full turn
    if msg_type == 'assistant':
        # Scan tool_use first — if present it takes priority over any text blocks
        first_text = None
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get('type', '')
            if btype == 'tool_use':
                name = block.get('name', '?')
                inp = block.get('input', {})
                inp_str = json.dumps(inp)[:300] if inp else ''
                return {
                    'event_type': 'tool_call',
                    'display_text': f"{name}({inp_str})",
                    'raw': raw,
                }
            elif btype == 'text' and first_text is None:
                text = block.get('text', '')
                if not isinstance(text, str):
                    text = str(text) if text is not None else ''
                if text.strip():
                    first_text = text
        if first_text is not None:
            return {
                'event_type': 'thinking',
                'display_text': first_text[:600],
                'raw': raw,
            }

    elif msg_type == 'user':
        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            if block.get('type') == 'tool_result':
                content = block.get('content', '')
                if isinstance(content, list):
                    text_parts = [c.get('text', '') for c in content if isinstance(c, dict) and c.get('type') == 'text']
                    content = ' '.join(text_parts)
                return {
                    'event_type': 'tool_result',
                    'display_text': str(content)[:400],
                    'raw': raw,
                }

    elif msg_type in ('system', 'init', 'result'):
        # 'result' lines are completion metadata from claude CLI (cost, session_id, etc.) — not streamable
        return None

    # Fallback: return raw only for truly unrecognized message types
    # 'assistant' and 'user' are handled above (may produce None for whitespace/empty)
    if msg_type and msg_type not in ('assistant', 'user', 'system', 'init', 'result'):
        return {'event_type': 'raw', 'display_text': f"[{msg_type}] {raw[:200]}", 'raw': raw}
    return None


def _parse_stream_line(line: str) -> Tuple[Optional[int], Optional[dict]]:
    """Unwrap a {seq, raw} envelope (or legacy raw line) and parse the provider payload.

    Returns (seq, event_dict).  seq is None for legacy lines without an envelope;
    event_dict is None when the inner payload is not a streamable event.
    """
    seq, raw = _unwrap_seq_line(line)
    if not raw:
        return seq, None
    return seq, _parse_jsonl_line(raw)


def _workspace_paths_match(expected_workspace: str, candidate_workspace: str) -> bool:
    """Return True when candidate workspace is the same as or inside expected workspace."""
    expected = (expected_workspace or '').strip()
    candidate = (candidate_workspace or '').strip()
    if not expected or not candidate:
        return False

    try:
        expected_path = Path(expected).expanduser().resolve()
        candidate_path = Path(candidate).expanduser().resolve()
        return candidate_path == expected_path or expected_path in candidate_path.parents
    except (OSError, RuntimeError):
        return candidate == expected or candidate.startswith(expected + os.sep)


def _is_codex_exec_session_payload(payload: dict) -> bool:
    """Return True only for headless Codex exec sessions."""
    if not isinstance(payload, dict):
        return False

    originator = str(payload.get('originator') or '').strip().lower()
    source = str(payload.get('source') or '').strip().lower()

    if source and source != 'exec':
        return False
    if originator and originator != 'codex_exec':
        return False

    return source == 'exec' or originator == 'codex_exec'


def _codex_transcript_matches_workspace(jsonl_path: Path, workspace_path: str) -> bool:
    """Return True if the Codex transcript belongs to the workspace."""
    try:
        with open(jsonl_path, 'r', encoding='utf-8', errors='replace') as f:
            for idx, line in enumerate(f):
                if idx > 40:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict) or record.get('type') != 'session_meta':
                    continue
                payload = record.get('payload', {})
                if not _is_codex_exec_session_payload(payload):
                    return False
                return _workspace_paths_match(workspace_path, payload.get('cwd', ''))
    except (OSError, IOError):
        return False
    return False


def _find_codex_transcript_for_workspace(workspace_path: str, started_at: float) -> Optional[Path]:
    """Find the newest matching Codex exec transcript for a workspace."""
    if not CODEX_SESSIONS_DIR.exists():
        return None

    _codex_root = CODEX_SESSIONS_DIR.resolve()
    try:
        _all = sorted(
            CODEX_SESSIONS_DIR.rglob('*.jsonl'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        files = [
            p for p in _all
            if not p.is_symlink() and p.resolve().is_relative_to(_codex_root)
        ]
    except OSError:
        return None

    lower_bound = started_at - 30.0
    for path in files[:CODEX_TRANSCRIPT_MAX_SCAN]:
        try:
            if path.stat().st_mtime < lower_bound:
                break
        except OSError:
            continue
        if _codex_transcript_matches_workspace(path, workspace_path):
            return path
    return None


# =============================================================================
# AGENT STREAM WATCHER — daemon thread tailing JSONL file
# =============================================================================

class AgentStreamWatcher(threading.Thread):
    """
    Daemon thread that tails a single agent's JSONL stream file and broadcasts
    parsed events to the SSE ring buffer/subscriber queues.

    Stops when:
    - Agent is marked complete/error
    - 600s inactivity timeout elapses
    """

    INACTIVITY_TIMEOUT = 600  # seconds

    def __init__(self, ctx: AgentContext, tail_from_end: bool = False, emit_spawn: bool = True):
        super().__init__(daemon=True, name=f"watcher-{ctx.agent_id}")
        self.ctx = ctx
        self.tail_from_end = tail_from_end
        self.emit_spawn = emit_spawn

    def run(self):
        # Lifecycle: agent_spawned fans out via the SSE ring buffer.
        if self.emit_spawn:
            _manager.broadcast_lifecycle(self.ctx, {
                'event': 'agent_spawned',
                'agent_id': self.ctx.agent_id,
                'label': self.ctx.label,
                'context': self.ctx.context,
                'timestamp': datetime.now().isoformat(),
            })

        stream_file = self.ctx.stream_file
        last_activity = time.time()
        file_pos = 0
        last_inode = None  # track inode to detect file rotation

        # Wait up to 5s for stream file to appear
        for _ in range(50):
            if stream_file.exists():
                break
            time.sleep(0.1)

        if self.tail_from_end and stream_file.exists():
            try:
                with open(stream_file, 'r', encoding='utf-8', errors='replace') as f:
                    tail_start = getattr(self.ctx, '_tail_start_pos', None)
                    if tail_start is None:
                        f.seek(0, os.SEEK_END)
                        file_pos = f.tell()
                    else:
                        f.seek(0, os.SEEK_END)
                        eof_pos = f.tell()
                        file_pos = min(max(0, int(tail_start)), eof_pos)
                last_inode = os.stat(stream_file).st_ino
            except OSError:
                file_pos = 0

        while True:
            # Check for inactivity timeout
            if time.time() - last_activity > self.INACTIVITY_TIMEOUT:
                logger.warning(
                    f"Stream watcher for agent {self.ctx.agent_id} exiting after "
                    f"{self.INACTIVITY_TIMEOUT}s inactivity"
                )
                _manager.complete_agent(self.ctx.agent_id, error=None)
                break

            # Check if agent was marked done externally
            if self.ctx.status in ('complete', 'error'):
                break

            if not stream_file.exists():
                time.sleep(0.2)
                continue

            # Detect file rotation: if the inode changed, reset position to 0
            try:
                current_inode = os.stat(stream_file).st_ino
                if last_inode is not None and current_inode != last_inode:
                    logger.debug(
                        "Stream file rotated for agent %s (inode %d -> %d), resetting position",
                        self.ctx.agent_id, last_inode, current_inode,
                    )
                    file_pos = 0
                last_inode = current_inode
            except OSError:
                pass

            try:
                with open(stream_file, 'r', encoding='utf-8', errors='replace') as f:
                    f.seek(file_pos)
                    lines = f.readlines()
                    new_pos = f.tell()

                if lines:
                    last_activity = time.time()
                    file_pos = new_pos
                    for line in lines:
                        seq, event = _parse_stream_line(line)
                        if event is None:
                            continue
                        # Assign a seq if the line came in legacy (unwrapped) format.
                        if seq is None:
                            with self.ctx.seq_lock:
                                seq = self.ctx.next_seq
                                self.ctx.next_seq = seq + 1
                        ts = datetime.now().isoformat()
                        # Store in context buffer (history endpoint, completion snapshot)
                        with self.ctx._lock:
                            self.ctx._parsed_lines.append({
                                **event,
                                'seq': seq,
                                'timestamp': ts,
                            })
                        # Broadcast to ring buffer + SSE subscribers
                        _manager.broadcast_stream_line(self.ctx, event, seq, ts)
                else:
                    time.sleep(0.15)

            except (OSError, IOError):
                time.sleep(0.2)

        # Emit completion event
        duration = None
        if self.ctx.completed_at and self.ctx.started_at:
            duration = round(self.ctx.completed_at - self.ctx.started_at, 1)
        with self.ctx._lock:
            line_count = len(self.ctx._parsed_lines)
        completion_payload = {
            'event': 'agent_complete' if self.ctx.status != 'error' else 'agent_error',
            'agent_id': self.ctx.agent_id,
            'label': self.ctx.label,
            'context': self.ctx.context,
            'error': self.ctx.error,
            'duration_seconds': duration,
            'line_count': line_count,
            'timestamp': datetime.now().isoformat(),
        }
        _manager.broadcast_lifecycle(self.ctx, completion_payload)


# =============================================================================
# LEGACY EMIT SHIMS — kept as no-ops for backwards-compat with any callers
# that still import set_dashboard_mode. The SSE transport replaced flask-socketio
# rooms (mission_agents / investigation_agents) entirely; subprocess→Flask→Socket.IO
# IPC was deleted along with the /api/internal/agent-event endpoint.
# =============================================================================

def set_dashboard_mode(in_dashboard: bool = True):
    """No-op shim — retained so existing import sites in dashboard_v2 don't break.

    The dashboard process used to call this to enable direct SocketIO emission
    from this module. The SSE rebuild routes everything through the in-process
    ring buffer, so this is no longer needed.
    """
    return None


def _emit_agent_event(ctx: 'AgentContext', payload: dict):
    """Legacy lifecycle emit — superseded by AgentStreamManager.broadcast_lifecycle.

    The watcher still calls this for the agent_spawned/complete/error events to
    keep this no-op shim tolerant of in-flight callers, but with the rooms
    deleted there are no socket.io subscribers anymore.
    """
    return None


def _emit_agent_stream_line(ctx: 'AgentContext', event: dict):
    """Legacy stream-line emit — superseded by AgentStreamManager.broadcast_stream_line."""
    return None


# =============================================================================
# AGENT STREAM MANAGER — singleton
# =============================================================================

class AgentStreamManager:
    """
    Singleton. Tracks all active agents, manages state file, spawns/reaps watcher threads.
    """

    STATE_FILE = STREAM_DIR / 'active_agents.json'

    # SSE / ring buffer constants
    MISSION_KEY = '__mission__'
    INVESTIGATION_KEY = '__investigation__'
    SUBSCRIBER_QUEUE_MAXSIZE = 1000  # slow-client threshold for gap signaling

    def __init__(self):
        self._agents: dict = {}  # agent_id -> AgentContext
        self._lock = threading.Lock()
        # SSE subscriber registry: key (agent_id or fan-in marker) -> list of queue.Queue
        self._subscribers: Dict[str, List[queue.Queue]] = {}
        # broadcast_lock serializes ring-buffer append + subscriber fan-out so a new
        # subscriber's atomic snapshot+register sees no gap between snapshot and live.
        self._broadcast_lock = threading.RLock()
        self._disk_sync_thread: Optional[threading.Thread] = None
        self._disk_sync_lock = threading.Lock()
        _ensure_stream_dir()

    # -----------------------------------------------------------------
    # SSE: ring buffer + subscribe/unsubscribe/broadcast
    # -----------------------------------------------------------------

    def _fanin_key(self, context: str) -> str:
        if context not in ('mission', 'investigation'):
            raise ValueError(f"_fanin_key: unknown context {context!r}")
        return self.MISSION_KEY if context == 'mission' else self.INVESTIGATION_KEY

    def _build_event_payload(
        self,
        ctx: 'AgentContext',
        event: dict,
        seq: int,
        ts: str,
    ) -> dict:
        return {
            'event': 'agent_stream_line',
            'seq': seq,
            'agent_id': ctx.agent_id,
            'context': ctx.context,
            'label': ctx.label,
            'event_type': event.get('event_type', 'raw'),
            'text': event.get('display_text', ''),
            'timestamp': ts,
        }

    def _start_watcher(self, ctx: 'AgentContext', tail_from_end: bool = False, emit_spawn: bool = True) -> bool:
        """Start one watcher thread for a context if it is not already running."""
        with ctx._watcher_lock:
            if ctx._watcher_started:
                return False
            ctx._watcher_started = True
        watcher = AgentStreamWatcher(ctx, tail_from_end=tail_from_end, emit_spawn=emit_spawn)
        watcher.start()
        return True

    def _populate_context_from_disk(self, ctx: 'AgentContext') -> int:
        """Load existing JSONL lines into parsed history and SSE replay ring."""
        if not ctx.stream_file.exists():
            ctx._tail_start_pos = 0
            return 0
        loaded = 0
        max_seq_seen = 0
        raw_lines = self._read_jsonl_lines_capped(ctx.stream_file, max_bytes=self._MAX_JSONL_BYTES)
        ts = datetime.fromtimestamp(ctx.spawned_at).isoformat()
        for raw_line in raw_lines:
            seq, event = _parse_stream_line(raw_line)
            if event is None:
                continue
            if seq is None or seq <= 0:
                max_seq_seen += 1
                seq = max_seq_seen
            else:
                max_seq_seen = max(max_seq_seen, seq)
            parsed_entry = {
                **event,
                'seq': seq,
                'timestamp': ts,
            }
            ctx._parsed_lines.append(parsed_entry)
            ctx.ring.append(self._build_event_payload(ctx, event, seq, ts))
            loaded += 1
        if max_seq_seen >= ctx.next_seq:
            ctx.next_seq = max_seq_seen + 1
        try:
            ctx._tail_start_pos = ctx.stream_file.stat().st_size
        except OSError:
            ctx._tail_start_pos = None
        return loaded

    def broadcast_stream_line(
        self,
        ctx: 'AgentContext',
        event: dict,
        seq: int,
        ts: Optional[str] = None,
    ) -> dict:
        """Append a stream-line event to the ring buffer and fan out to subscribers.

        Returns the wire-format event dict that was broadcast.
        """
        ts = ts or datetime.now().isoformat()
        payload = self._build_event_payload(ctx, event, seq, ts)
        self._enqueue_event(ctx, payload)
        return payload

    def broadcast_lifecycle(self, ctx: 'AgentContext', payload: dict) -> dict:
        """Append a lifecycle event (agent_spawned/complete/error) to ring + subscribers.

        Lifecycle events also get a seq, drawn from the agent's counter.
        """
        if not isinstance(ctx, AgentContext):
            raise TypeError(f"broadcast_lifecycle: ctx must be AgentContext, got {type(ctx).__name__}")
        with ctx.seq_lock:
            seq = ctx.next_seq
            ctx.next_seq = seq + 1
        wire = dict(payload)
        wire['seq'] = seq
        wire.setdefault('agent_id', ctx.agent_id)
        wire.setdefault('label', ctx.label)
        wire.setdefault('context', ctx.context)
        wire.setdefault('timestamp', datetime.now().isoformat())
        self._enqueue_event(ctx, wire)
        return wire

    def _enqueue_event(self, ctx: 'AgentContext', payload: dict) -> None:
        """Atomically: append to ring buffer + fan-out to all subscriber queues."""
        agent_key = ctx.agent_id
        fanin_key = self._fanin_key(ctx.context)
        with self._broadcast_lock:
            # 1. Ring buffer (per agent) — bounded deque(maxlen=500)
            ctx.ring.append(payload)
            # 2. Fan-out to subscribers under the same lock so atomic snapshots work.
            for key in (agent_key, fanin_key):
                subs = self._subscribers.get(key)
                if not subs:
                    continue
                # Iterate over a snapshot — _put_or_drop may unregister broken queues.
                for q in list(subs):
                    self._put_or_drop(key, q, payload)

    def _put_or_drop(self, key: str, q: queue.Queue, payload: dict) -> None:
        """Try to enqueue; on Full drop the oldest entry and emit a `gap` event.

        The `gap` lets the client re-fetch via /api/agents/<id>/history?since=N.
        """
        if not isinstance(payload, dict):
            logger.warning("_put_or_drop: non-dict payload for %s, dropping: %r", key, type(payload))
            return
        try:
            q.put_nowait(payload)
            return
        except queue.Full:
            pass
        dropped_seq: Optional[int] = None
        try:
            dropped = q.get_nowait()
            if isinstance(dropped, dict):
                dropped_seq = dropped.get('seq')
        except queue.Empty:
            pass
        gap = {
            'event': 'gap',
            'agent_id': payload.get('agent_id'),
            'context': payload.get('context'),
            'dropped_from': dropped_seq,
            'dropped_to': payload.get('seq'),
            'timestamp': datetime.now().isoformat(),
        }
        logger.warning(
            "SSE backpressure: subscriber queue full for %s; dropped seq %s, gap to seq %s",
            key, dropped_seq, payload.get('seq'),
        )
        try:
            q.put_nowait(gap)
        except queue.Full:
            pass
        try:
            q.put_nowait(payload)
        except queue.Full:
            logger.warning(
                "SSE backpressure: queue still full after gap drop for %s; payload seq=%s lost",
                key, payload.get('seq'),
            )

    def _fanout_existing_ring(self, ctx: 'AgentContext') -> None:
        """Send a newly-hydrated agent's current ring to existing subscribers."""
        agent_key = ctx.agent_id
        fanin_key = self._fanin_key(ctx.context)
        with self._broadcast_lock:
            events = list(ctx.ring)
            if not events:
                return
            for key in (agent_key, fanin_key):
                subs = self._subscribers.get(key)
                if not subs:
                    continue
                for payload in events:
                    for q in list(subs):
                        self._put_or_drop(key, q, payload)

    def ensure_disk_sync_thread(self) -> None:
        """Start the dashboard-side cross-process agent discovery loop once."""
        with self._disk_sync_lock:
            if self._disk_sync_thread and self._disk_sync_thread.is_alive():
                return
            self._disk_sync_thread = threading.Thread(
                target=self._disk_sync_loop,
                daemon=True,
                name='agent-stream-disk-sync',
            )
            self._disk_sync_thread.start()

    def _disk_sync_loop(self) -> None:
        """Poll active_agents.json so existing SSE clients see future agents."""
        while True:
            try:
                self.sync_running_disk_agents(context='mission')
                self.sync_running_disk_agents(context='investigation')
            except Exception:
                logger.exception("agent stream disk sync failed")
            time.sleep(0.5)

    def subscribe(
        self,
        key: str,
        last_seq: int,
    ) -> Tuple[List[dict], queue.Queue]:
        """Atomically snapshot ring-buffer entries with seq > last_seq and register a queue.

        Returns (snapshot_events, q). Caller must call unsubscribe(key, q) when done.
        For per-agent keys the snapshot draws from that agent's ring.
        For fan-in keys (__mission__/__investigation__) the snapshot merges all matching agents.
        """
        if last_seq is None:
            last_seq = 0
        elif not isinstance(last_seq, int) or isinstance(last_seq, bool):
            try:
                last_seq = int(last_seq) if str(last_seq).strip().lstrip('-').isdigit() else 0
            except (TypeError, ValueError):
                last_seq = 0
        if last_seq < 0:
            last_seq = 0
        q: queue.Queue = queue.Queue(maxsize=self.SUBSCRIBER_QUEUE_MAXSIZE)
        snapshot: List[dict] = []
        if key in (self.MISSION_KEY, self.INVESTIGATION_KEY):
            self.ensure_disk_sync_thread()
            ctx_filter = 'mission' if key == self.MISSION_KEY else 'investigation'
            # Fan-in streams multiplex agents whose seq values are per-agent, not
            # globally monotonic. A single Last-Event-ID from EventSource cannot
            # safely filter all agents, so always replay the current ring window.
            last_seq = 0
            self.sync_running_disk_agents(context=ctx_filter)
        with self._broadcast_lock:
            if key in (self.MISSION_KEY, self.INVESTIGATION_KEY):
                ctx_filter = 'mission' if key == self.MISSION_KEY else 'investigation'
                # Fan-in: merge all live AgentContexts for this context, sort by seq.
                merged: List[dict] = []
                with self._lock:
                    entries = list(self._agents.values())
                for entry in entries:
                    if isinstance(entry, dict) or entry is _PREWARM_SENTINEL:
                        continue
                    if entry.context != ctx_filter:
                        continue
                    for ev in entry.ring:
                        if ev.get('seq', 0) > last_seq:
                            merged.append(ev)
                merged.sort(key=lambda e: e.get('seq', 0))
                snapshot.extend(merged)
            else:
                # Per-agent
                with self._lock:
                    entry = self._agents.get(key)
                if entry is not None and not isinstance(entry, dict) and entry is not _PREWARM_SENTINEL:
                    for ev in entry.ring:
                        if ev.get('seq', 0) > last_seq:
                            snapshot.append(ev)
                    snapshot.sort(key=lambda e: e.get('seq', 0))
            # Register the queue under the same lock so no broadcast slips between
            # snapshot completion and registration.
            self._subscribers.setdefault(key, []).append(q)
        return snapshot, q

    def sync_running_disk_agents(self, context: Optional[str] = None) -> int:
        """Hydrate running disk-known agents into this process and tail their JSONL.

        Mission and investigation workers are launched by sibling processes, so
        their in-memory ring buffers are not shared with the Flask/SSE process.
        This method rebuilds enough local state from active_agents.json + JSONL
        for the dashboard process to own the live SSE fan-out.
        """
        disk_agents = self._load_disk_agents()
        started = 0
        for agent_id, info in disk_agents.items():
            if not isinstance(info, dict):
                continue
            if info.get('status') != 'running':
                continue
            agent_context = info.get('context') or 'mission'
            if context and agent_context != context:
                continue

            ctx = None
            claimed = False
            newly_claimed = False
            with self._lock:
                existing = self._agents.get(agent_id)
                if isinstance(existing, AgentContext):
                    ctx = existing
                elif existing is _PREWARM_SENTINEL:
                    continue
                else:
                    self._agents[agent_id] = _PREWARM_SENTINEL
                    claimed = True

            if claimed:
                newly_claimed = True
                try:
                    ctx = AgentContext(
                        agent_id=agent_id,
                        context=agent_context,
                        label=info.get('label') or agent_id,
                        pid=info.get('pid') or None,
                    )
                except (ValueError, TypeError) as exc:
                    logger.warning("sync_running_disk_agents: skipping %r — invalid disk record: %s", agent_id, exc)
                    with self._lock:
                        if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                            del self._agents[agent_id]
                    continue
                ctx.spawned_at = info.get('spawned_at', time.time())
                ctx.started_at = info.get('started_at', ctx.spawned_at)
                ctx.status = 'running'
                ctx.completed_at = None
                self._populate_context_from_disk(ctx)
                with self._lock:
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        self._agents[agent_id] = ctx
                    else:
                        ctx = None

            if ctx is not None and ctx.status == 'running':
                if newly_claimed:
                    self._fanout_existing_ring(ctx)
                if self._start_watcher(ctx, tail_from_end=True, emit_spawn=False):
                    started += 1
        return started

    def unsubscribe(self, key: str, q: queue.Queue) -> None:
        """Remove a previously-registered subscriber queue."""
        with self._broadcast_lock:
            subs = self._subscribers.get(key)
            if not subs:
                return
            try:
                subs.remove(q)
            except ValueError:
                pass
            if not subs:
                self._subscribers.pop(key, None)

    def get_ring_since(self, agent_id: str, last_seq) -> List[dict]:
        """Return ring-buffer entries for agent_id with seq > last_seq, sorted by seq."""
        if last_seq is None or not isinstance(last_seq, int) or isinstance(last_seq, bool):
            last_seq = 0
        with self._lock:
            entry = self._agents.get(agent_id)
        if entry is None or isinstance(entry, dict) or entry is _PREWARM_SENTINEL:
            return []
        with self._broadcast_lock:
            return sorted(
                (ev for ev in entry.ring if ev.get('seq', 0) > last_seq),
                key=lambda e: e.get('seq', 0),
            )

    def get_active_agent_snapshot(self, context: Optional[str] = None) -> List[dict]:
        """Return a list of currently-running agents (for SSE initial agent_state_snapshot)."""
        self.sync_running_disk_agents(context=context)
        result: List[dict] = []
        with self._lock:
            for entry in self._agents.values():
                if isinstance(entry, dict) or entry is _PREWARM_SENTINEL:
                    continue
                if entry.status != 'running':
                    continue
                if context and entry.context != context:
                    continue
                result.append({
                    'agent_id': entry.agent_id,
                    'label': entry.label,
                    'context': entry.context,
                    'status': entry.status,
                    'started_at': entry.started_at,
                })
        return result

    def register_agent(self, context: str, agent_id: str, label: str, pid: Optional[int] = None) -> Path:
        """
        Called at agent spawn time.

        Args:
            context: 'mission' | 'investigation'
            agent_id: Unique agent identifier
            label: Display label (e.g. "PLAN Agent 1")
            pid: Subprocess PID (None if not yet known)

        Returns:
            Path to the agent's stream file (write stdout here)
        """
        _RESERVED_IDS = (self.MISSION_KEY, self.INVESTIGATION_KEY)
        if agent_id in _RESERVED_IDS:
            raise ValueError(f"register_agent: agent_id {agent_id!r} is reserved for SSE fan-in keys")
        _ensure_stream_dir()

        # Idempotency + TOCTOU guard: claim slot atomically with sentinel before
        # doing file I/O, then replace with real ctx. Two concurrent calls for the
        # same agent_id will both try to claim; only one succeeds, the other returns
        # the existing context once it appears.
        with self._lock:
            existing = self._agents.get(agent_id)
            if isinstance(existing, AgentContext):
                logging.warning("register_agent: duplicate call for %r — returning existing", agent_id)
                return existing.stream_file
            if existing is _PREWARM_SENTINEL:
                # Another thread is mid-registration; fall through, will be overwritten.
                pass
            # Claim slot atomically so no concurrent call can race past this point.
            self._agents[agent_id] = _PREWARM_SENTINEL

        try:
            ctx = AgentContext(agent_id=agent_id, context=context, label=label, pid=pid)
        except Exception:
            with self._lock:
                if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                    del self._agents[agent_id]
            raise

        # Resume per-agent seq counter from any existing JSONL file. Lines without
        # a `seq` field count as 0; counter starts at max_seen + 1.
        try:
            existing_max = _scan_max_seq(ctx.stream_file)
        except Exception:
            existing_max = 0
        ctx.next_seq = existing_max + 1

        with self._lock:
            current = self._agents.get(agent_id)
            if isinstance(current, AgentContext):
                # Concurrent caller already committed — return the winner
                return current.stream_file
            self._agents[agent_id] = ctx
            self._save_state_locked()

        self._start_watcher(ctx, tail_from_end=False, emit_spawn=True)

        return ctx.stream_file

    def update_pid(self, agent_id: str, pid: int):
        """Update PID after process is spawned (if PID wasn't known at register time)."""
        if pid is not None and (not isinstance(pid, int) or isinstance(pid, bool)):
            raise TypeError(f"update_pid: pid must be int, got {type(pid).__name__}: {pid!r}")
        with self._lock:
            if agent_id in self._agents:
                entry = self._agents[agent_id]
                # Skip sentinel — plain object() has no .pid attribute
                if entry is _PREWARM_SENTINEL:
                    return
                entry.pid = pid
                self._save_state_locked()

    def _write_snapshot(self, ctx: 'AgentContext'):
        """Write a .snapshot.json for a completed agent (fast replay for reconnects)."""
        try:
            snapshot_path = ctx.stream_file.with_suffix('.snapshot.json')
            with ctx._lock:
                lines = list(ctx._parsed_lines)
            snapshot = {
                'agent_id': ctx.agent_id,
                'label': ctx.label,
                'context': ctx.context,
                'status': ctx.status,
                'completed_at': ctx.completed_at,
                'lines': lines,  # full buffer (deque already bounded at maxlen=200)
            }
            tmp = snapshot_path.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8', errors='replace') as f:
                json.dump(snapshot, f)
            tmp.replace(snapshot_path)
        except Exception as e:
            logger.warning("_write_snapshot failed: %s", e)

    def complete_agent(self, agent_id: str, error: Optional[str] = None):
        """Mark agent as complete or error. Called when subprocess exits."""
        ctx = None
        with self._lock:
            if agent_id not in self._agents:
                return
            entry = self._agents[agent_id]
            # Already a tombstone — nothing to do
            if isinstance(entry, dict) and entry.get('_tombstone'):
                return
            # Guard against _PREWARM_SENTINEL: a plain object() has no .status attribute.
            # Race: prewarm_active_agents placed a sentinel; real AgentContext not yet registered.
            if entry is _PREWARM_SENTINEL:
                return
            # Guard against double-complete: only transition from 'running'
            if entry.status != 'running':
                return
            ctx = entry
            ctx.status = 'error' if error else 'complete'
            ctx.error = error
            ctx.completed_at = time.time()
            self._save_state_locked()
            # Replace heavy AgentContext with lightweight tombstone atomically —
            # no gap between status change and tombstone visibility to readers.
            self._agents[agent_id] = {
                '_tombstone': True,
                'agent_id': agent_id,
                'context': ctx.context,
                'label': ctx.label,
                'stream_file': str(ctx.stream_file),
                'snapshot_file': str(ctx.stream_file.with_suffix('.snapshot.json')),
                'status': ctx.status,
                'started_at': ctx.started_at,
                'completed_at': ctx.completed_at,
                'error': ctx.error,
            }

        # Non-critical post-completion work outside lock
        if error:
            self._log_agent_error(ctx, error)

        # Write snapshot for fast replay on reconnect
        self._write_snapshot(ctx)

    def _log_agent_error(self, ctx: 'AgentContext', error: str):
        """Write agent error to journal log and GlassBox archive.

        Live notification of the error reaches the browser via the SSE
        broadcast inside complete_agent() — no socket.io emission needed here.
        """
        try:
            mission_id = self._get_current_mission_id()

            # Append to journal log
            try:
                self._append_journal_error(ctx, error, mission_id)
            except Exception:
                pass

            # 3. Append to agent_errors.jsonl for GlassBox archival
            try:
                self._append_glassbox_error(ctx, error, mission_id)
            except Exception:
                pass
        except Exception:
            pass  # Never raise from error logging

    def _append_glassbox_error(self, ctx: 'AgentContext', error: str, mission_id: Optional[str]):
        """Append agent error entry to state/agent_errors.jsonl for GlassBox archival."""
        try:
            af_root = Path(__file__).parent
            errors_path = af_root / 'state' / 'agent_errors.jsonl'
            duration = None
            if ctx.completed_at and ctx.started_at:
                duration = round(ctx.completed_at - ctx.started_at, 2)
            entry = {
                'type': 'agent_error',
                'timestamp': datetime.now().isoformat(),
                'mission_id': mission_id,
                'agent_id': ctx.agent_id,
                'agent_label': ctx.label,
                'context': ctx.context,
                'error': error,
                'duration_seconds': duration,
            }
            with open(errors_path, 'a', buffering=1, encoding='utf-8', errors='replace') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    def _get_current_mission_id(self) -> Optional[str]:
        """Read current mission_id from state/mission.json."""
        try:
            af_root = Path(__file__).parent
            mission_path = af_root / 'state' / 'mission.json'
            if mission_path.exists():
                with open(mission_path, 'r', encoding='utf-8', errors='replace') as f:
                    data = json.load(f)
                return data.get('mission_id')
        except Exception:
            pass
        return None

    def _append_journal_error(self, ctx: 'AgentContext', error: str, mission_id: Optional[str]):
        """Append agent error entry to state/claude_journal.jsonl."""
        try:
            af_root = Path(__file__).parent
            journal_path = af_root / 'state' / 'claude_journal.jsonl'
            entry = {
                'type': 'agent_error',
                'timestamp': datetime.now().isoformat(),
                'mission_id': mission_id,
                'agent_id': ctx.agent_id,
                'agent_label': ctx.label,
                'context': ctx.context,
                'error': error,
            }
            with open(journal_path, 'a', buffering=1, encoding='utf-8', errors='replace') as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    def get_active_agents(self) -> dict:
        """Returns current active agents grouped by context, for initial_data on WS connect.

        Merges in-memory state with on-disk state (active_agents.json) so that agents
        registered by subprocess contexts (conductor, investigation_engine) are visible
        to the dashboard process when a browser client connects.
        """
        with self._lock:
            memory_agents = {}
            for agent_id, entry in self._agents.items():
                # Skip tombstones (completed agents replaced with lightweight dicts)
                if isinstance(entry, dict):
                    continue
                # Skip prewarm sentinels — plain object() has no .status
                if entry is _PREWARM_SENTINEL:
                    continue
                if entry.status == 'running':
                    memory_agents[agent_id] = entry.to_dict()

        # Merge in disk state for cross-process agents
        disk_agents = self._load_disk_agents()
        for agent_id, info in disk_agents.items():
            if agent_id not in memory_agents and info.get('status') == 'running':
                memory_agents[agent_id] = info

        mission = [v for v in memory_agents.values() if v.get('context') == 'mission']
        investigation = [v for v in memory_agents.values() if v.get('context') == 'investigation']
        return {'mission': mission, 'investigation': investigation}

    def get_recent_agents(self, limit: int = 6, max_age_seconds: int = RECENT_AGENT_WINDOW_SECONDS) -> dict:
        """Return recent running/completed agents grouped by context for reconnect replay."""
        if limit is not None and limit < 0:
            raise ValueError(f'limit must be non-negative, got {limit}')
        now = time.time()
        by_id = {}

        with self._lock:
            for agent_id, entry in self._agents.items():
                if entry is _PREWARM_SENTINEL:
                    continue
                info = dict(entry) if isinstance(entry, dict) else entry.to_dict()
                status = info.get('status')
                if status not in ('running', 'complete', 'error'):
                    continue
                ts = info.get('completed_at') or info.get('started_at') or info.get('spawned_at') or 0
                if max_age_seconds and ts and (now - ts) > max_age_seconds:
                    continue
                by_id[agent_id] = _copy_agent_fields(info)

        for agent_id, info in self._load_disk_agents().items():
            if agent_id in by_id or not isinstance(info, dict):
                continue
            status = info.get('status')
            if status not in ('running', 'complete', 'error'):
                continue
            ts = info.get('completed_at') or info.get('started_at') or info.get('spawned_at') or 0
            if max_age_seconds and ts and (now - ts) > max_age_seconds:
                continue
            by_id[agent_id] = _copy_agent_fields(info)

        def _sort_key(info: dict):
            status_rank = 0 if info.get('status') == 'running' else 1
            ts = info.get('completed_at') or info.get('started_at') or info.get('spawned_at') or 0
            return (status_rank, -ts)

        recent = sorted(by_id.values(), key=_sort_key)

        mission = [v for v in recent if v.get('context') == 'mission']
        investigation = [v for v in recent if v.get('context') == 'investigation']
        if limit is not None and limit >= 0:
            mission = mission[:limit]
            investigation = investigation[:limit]
        return {'mission': mission, 'investigation': investigation}

    def _load_disk_agents(self) -> dict:
        """Load agent state from active_agents.json (written by all process contexts)."""
        try:
            if self.STATE_FILE.exists():
                try:
                    sz = self.STATE_FILE.stat().st_size
                except OSError:
                    return {}
                if sz > _MAX_ACTIVE_AGENTS_BYTES:
                    logger.warning(
                        'active_agents.json exceeds size cap (%d bytes) — skipping load',
                        sz,
                    )
                    return {}
                with open(self.STATE_FILE, 'r', encoding='utf-8', errors='replace') as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def get_agent_stream_lines(self, agent_id: str, limit: int = 200) -> list:
        """Return accumulated parsed stream lines for an agent (for reconnect replay).

        Priority:
          1. Live AgentContext in-memory buffer (running agents)
          2. Tombstone: read .snapshot.json (completed agents — fast, no JSONL re-parse)
          3. Disk fallback: scan STREAM_DIR for matching .jsonl (cold-start / cross-process)
        """
        # limit=None means "return all lines" (no limit).
        # limit=0 means "return no lines" (empty).
        # limit<0 is invalid — raise ValueError (ASM-NEW-4: consistent with get_stream_history).
        if limit is not None and limit < 0:
            raise ValueError(f'limit must be non-negative, got {limit}')
        if limit == 0:
            return []

        with self._lock:
            entry = self._agents.get(agent_id)

        if entry is None:
            # Not in memory at all — scan disk
            return self._get_stream_lines_from_disk(agent_id, limit)

        # Tombstone: completed agent stored as plain dict — read snapshot for fast replay
        if isinstance(entry, dict):
            snap_path = entry.get('snapshot_file')
            if snap_path:
                try:
                    snap_resolved = Path(snap_path).resolve()
                    if (snap_resolved.is_relative_to(STREAM_DIR.resolve())
                            and not Path(snap_path).is_symlink()
                            and snap_resolved.is_file()):
                        try:
                            with open(snap_resolved, encoding='utf-8', errors='replace') as f:
                                data = json.load(f)
                            lines_all = data.get('lines', [])
                            return lines_all if limit is None else lines_all[-limit:]
                        except Exception:
                            pass
                except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
                    pass
            # Snapshot missing — fall back to JSONL
            stream_file = entry.get('stream_file')
            if stream_file:
                try:
                    sf_resolved = Path(stream_file).resolve()
                    if (sf_resolved.is_relative_to(STREAM_DIR.resolve())
                            and not Path(stream_file).is_symlink()
                            and sf_resolved.is_file()):
                        return self._parse_jsonl_file(sf_resolved, limit)
                except (OSError, RuntimeError):
                    pass
            return []

        # Sentinel: prewarm slot not yet replaced with a real AgentContext
        if entry is _PREWARM_SENTINEL:
            return self._get_stream_lines_from_disk(agent_id, limit)

        # Live AgentContext
        ctx = entry
        with ctx._lock:
            all_lines = list(ctx._parsed_lines)
            lines = all_lines if limit is None else all_lines[-limit:]

        # Disk fallback: if buffer is empty but stream file exists, re-parse from disk
        if not lines and ctx.stream_file.exists():
            try:
                disk_lines = self._parse_jsonl_file(ctx.stream_file, limit=None)
                lines = disk_lines if limit is None else disk_lines[-limit:]
                # Populate in-memory buffer from disk so future calls are fast
                with ctx._lock:
                    if not ctx._parsed_lines:
                        ctx._parsed_lines.extend(disk_lines)
            except Exception:
                pass

        return lines

    _MAX_JSONL_BYTES = 10 * 1024 * 1024  # 10 MiB default cap

    def _parse_jsonl_file(
        self,
        path: Path,
        limit: Optional[int] = None,
        max_bytes: int = _MAX_JSONL_BYTES,
    ) -> list:
        """Parse a JSONL stream file with a configurable size cap."""
        # ASM-NEW-4: internal consistency — reject negative limits uniformly.
        # Public callers (get_agent_stream_lines) already validate, but this
        # catches programming errors in future callers of this private method.
        if limit is not None and limit < 0:
            raise ValueError(f'_parse_jsonl_file: limit must be non-negative, got {limit}')
        # C3-B fix (optimized): early-exit before any I/O when limit=0
        if limit == 0:
            return []
        disk_lines = []
        try:
            bytes_read = 0
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                for raw_line in fh:
                    bytes_read += len(raw_line.encode('utf-8', errors='replace'))
                    if bytes_read > max_bytes:
                        break
                    seq, event = _parse_stream_line(raw_line)
                    if event:
                        entry = {**event}
                        entry.setdefault('timestamp', '')
                        if seq is not None:
                            entry['seq'] = seq
                        disk_lines.append(entry)
        except Exception:
            pass
        if limit is not None and limit > 0:
            return disk_lines[-limit:]
        return disk_lines

    def _read_jsonl_lines_capped(self, path: Path, max_bytes: int = _MAX_JSONL_BYTES) -> list:
        """Return raw text lines from a JSONL file, stopping at max_bytes."""
        lines = []
        try:
            bytes_read = 0
            with open(path, 'r', encoding='utf-8', errors='replace') as fh:
                for raw_line in fh:
                    bytes_read += len(raw_line.encode('utf-8', errors='replace'))
                    if bytes_read > max_bytes:
                        break
                    lines.append(raw_line)
        except (OSError, IOError, UnicodeDecodeError):
            pass
        return lines

    def _get_stream_lines_from_disk(self, agent_id: str, limit: int) -> list:
        """Scan STREAM_DIR for any file matching *_{agent_id}.jsonl and parse it."""
        _ensure_stream_dir()
        import re as _re_safe
        safe_id = _re_safe.sub(r'[^a-zA-Z0-9_\-]', '_', agent_id)
        for context in ('mission', 'investigation'):
            candidate = STREAM_DIR / f"{context}_{safe_id}.jsonl"
            try:
                resolved = candidate.resolve()
                if not resolved.is_relative_to(STREAM_DIR.resolve()):
                    continue
            except (OSError, RuntimeError):
                continue
            if resolved.exists():
                return self._parse_jsonl_file(resolved, limit)
        return []

    def get_stream_history(self, mission_id: Optional[str] = None, limit: int = 20) -> list:
        """
        Return list of past agent stream manifests from the persistent store.

        Scans STREAM_DIR for .jsonl files. Optionally filters by mission_id.
        Returns newest-first, up to limit entries.
        """
        # ASM-NEW-3: reject negative limit
        if limit is not None and limit < 0:
            raise ValueError(f'limit must be non-negative, got {limit}')
        _ensure_stream_dir()

        # Collect currently running stream file names (skip tombstones and sentinels)
        with self._lock:
            active_files = {
                entry.stream_file.name
                for entry in self._agents.values()
                if not isinstance(entry, dict)
                and entry is not _PREWARM_SENTINEL
                and entry.status == 'running'
            }

        # Phase 1: Fast scan — collect mtime only (no file reads) so we can sort+limit before any I/O
        candidates = []
        for p in STREAM_DIR.iterdir():
            if p.name == 'active_agents.json':
                continue
            if p.suffix != '.jsonl':
                continue
            if p.name in active_files:
                continue  # Skip running agents
            try:
                stat = p.stat()
                candidates.append((stat.st_mtime, p))
            except (OSError, FileNotFoundError):
                pass

        # Sort newest-first; enrich all candidates before applying final limit
        candidates.sort(key=lambda t: t[0], reverse=True)
        if limit is None:
            limit = len(candidates)  # no-op limit: keep all

        manifests = []
        current_mission_id = self._get_current_mission_id()
        for mtime, p in candidates:
            try:
                # Parse context and agent_id from filename: {context}_{safe_id}.jsonl
                fname = p.stem
                parts = fname.split('_', 1)
                context = parts[0] if len(parts) == 2 else 'unknown'
                agent_id = parts[1] if len(parts) == 2 else fname

                manifest = {
                    'agent_id': agent_id,
                    'label': agent_id,
                    'context': context,
                    'status': 'complete',
                    'completed_at': mtime,
                    'line_count': None,
                    'stream_file': p.name,
                    'mission_id': None,
                }

                # Enrich from in-memory if available (no file read needed)
                with self._lock:
                    entry = self._agents.get(agent_id)
                    if entry is not None:
                        if isinstance(entry, dict):
                            # Tombstone: extract fields from dict
                            manifest['completed_at'] = entry.get('completed_at') or mtime
                            manifest['mission_id'] = current_mission_id
                        else:
                            # Live AgentContext
                            manifest['label'] = entry.label
                            manifest['started_at'] = entry.started_at
                            manifest['completed_at'] = entry.completed_at or mtime
                            manifest['mission_id'] = current_mission_id
                            with entry._lock:
                                manifest['line_count'] = len(entry._parsed_lines)
                            if entry.completed_at and entry.started_at:
                                manifest['duration_seconds'] = round(entry.completed_at - entry.started_at, 1)

                manifests.append(manifest)
            except (OSError, FileNotFoundError):
                pass

        # Apply mission_id filter
        if mission_id:
            manifests = [m for m in manifests if m.get('mission_id') == mission_id]

        return manifests[:limit] if limit is not None else manifests

    def cleanup_old_stream_files(self, max_age_hours: int = 24) -> int:
        """
        Delete JSONL and .snapshot.json stream files older than max_age_hours
        that are not referenced by currently active agents.

        Returns: count of files deleted.
        """
        # ASM-NEW-1: reject non-positive, None, or bool max_age_hours to prevent
        # deleting all files. bool is a subtype of int (True==1, False==0) so
        # we must reject it explicitly before the numeric check.
        if (isinstance(max_age_hours, bool)
                or not isinstance(max_age_hours, int)
                or max_age_hours is None
                or max_age_hours <= 0):
            raise ValueError(f'max_age_hours must be a positive integer, got {max_age_hours!r}')
        _ensure_stream_dir()
        cutoff = time.time() - (max_age_hours * 3600)
        deleted = 0

        # Collect active stream file paths under lock (skip tombstones and sentinels)
        with self._lock:
            active_paths = {
                entry.stream_file
                for entry in self._agents.values()
                if not isinstance(entry, dict) and entry is not _PREWARM_SENTINEL
            }

        for p in STREAM_DIR.iterdir():
            if p.name == 'active_agents.json':
                continue  # Never delete state file
            if p.suffix not in ('.jsonl', '.json'):
                continue
            # For .json files, only delete .snapshot.json files
            if p.suffix == '.json' and not p.name.endswith('.snapshot.json'):
                continue
            # Never delete the .jsonl for an active agent
            if p.suffix == '.jsonl' and p in active_paths:
                continue
            # Never delete the snapshot whose base .jsonl is still active.
            # foo.snapshot.json → foo.jsonl (not foo.snapshot.jsonl)
            if p.suffix == '.json':
                base_name = p.name[: -len('.snapshot.json')] + '.jsonl'
                base_jsonl = p.parent / base_name
                if base_jsonl in active_paths:
                    continue
            try:
                if p.stat().st_mtime < cutoff:
                    with self._lock:
                        current_active = {
                            entry.stream_file
                            for entry in self._agents.values()
                            if not isinstance(entry, dict) and entry is not _PREWARM_SENTINEL
                        }
                        # Active check and unlink are atomic inside the lock to prevent
                        # register_agent() from making p live again between the check and unlink.
                        if p.suffix == '.jsonl':
                            if p in current_active:
                                continue
                        else:
                            base_name = p.name[: -len('.snapshot.json')] + '.jsonl'
                            if (p.parent / base_name) in current_active:
                                continue
                        p.unlink()
                        deleted += 1
            except (OSError, FileNotFoundError):
                pass  # Non-critical

        return deleted

    def reap_dead_agents(self) -> list:
        """Check all 'running' agents and mark dead ones as complete.

        Returns list of agent_ids that were reaped.
        """
        reaped = []

        # Collect candidates from in-memory and disk state in a single lock acquisition.
        # Two sequential acquisitions would create a TOCTOU window where a live agent
        # registers between the first and second lock, causing double-tombstoning.
        disk_agents = self._load_disk_agents()  # file I/O outside lock — safe, idempotent
        with self._lock:
            candidates = [
                (aid, entry.pid, entry.spawned_at)
                for aid, entry in self._agents.items()
                if not isinstance(entry, dict)
                and entry is not _PREWARM_SENTINEL
                and entry.status == 'running'
                and entry.pid is not None and entry.pid > 0
            ]
            for aid, info in disk_agents.items():
                raw_pid = info.get('pid')
                if not isinstance(raw_pid, int) or isinstance(raw_pid, bool):
                    continue
                if info.get('status') == 'running' and raw_pid > 0:
                    if aid not in self._agents:
                        candidates.append((aid, raw_pid, info.get('spawned_at', 0)))

        for agent_id, pid, spawned_at in candidates:
            if not _is_pid_alive(pid, spawned_at):
                # Atomically hydrate (if disk-only) and tombstone under a single lock
                # acquisition so no concurrent thread can slip in between.
                ctx_for_io = None
                with self._lock:
                    entry = self._agents.get(agent_id)
                    if entry is not None:
                        if isinstance(entry, dict):
                            continue  # Already tombstoned
                        if entry is _PREWARM_SENTINEL:
                            continue  # Prewarm claimed this slot; let prewarm handle it
                        if entry.status != 'running':
                            continue  # Already completed by another thread
                        ctx_for_io = entry
                    else:
                        # Agent only exists on disk — hydrate a minimal ctx
                        try:
                            self._hydrate_disk_agent(agent_id, disk_agents.get(agent_id, {}))
                        except Exception as exc:
                            logging.warning("reap_dead_agents: skipping %r — hydration error: %s", agent_id, exc)
                            continue
                        ctx_for_io = self._agents.get(agent_id)
                        if ctx_for_io is None or not isinstance(ctx_for_io, AgentContext):
                            continue
                    # Complete atomically inside the same lock block
                    ctx_for_io.status = 'error'
                    ctx_for_io.error = 'process_died'
                    ctx_for_io.completed_at = time.time()
                    self._save_state_locked()
                    # Replace with tombstone immediately so no other thread sees 'running'
                    self._agents[agent_id] = {
                        '_tombstone': True,
                        'agent_id': agent_id,
                        'context': ctx_for_io.context,
                        'label': ctx_for_io.label,
                        'stream_file': str(ctx_for_io.stream_file),
                        'snapshot_file': str(ctx_for_io.stream_file.with_suffix('.snapshot.json')),
                        'status': 'error',
                        'started_at': ctx_for_io.started_at,
                        'completed_at': ctx_for_io.completed_at,
                        'error': ctx_for_io.error,
                    }
                # I/O outside the lock (non-critical, idempotent)
                self._log_agent_error(ctx_for_io, 'process_died')
                self._write_snapshot(ctx_for_io)
                reaped.append(agent_id)

        return reaped

    def _hydrate_disk_agent(self, agent_id: str, disk_info: dict):
        """Create a minimal in-memory AgentContext from disk state so complete_agent works."""
        raw_ctx = disk_info.get('context') or 'mission'
        try:
            ctx = AgentContext(
                agent_id=agent_id,
                context=raw_ctx,
                label=disk_info.get('label') or agent_id,
                pid=disk_info.get('pid') or None,
            )
        except (ValueError, TypeError) as exc:
            logging.warning("_hydrate_disk_agent: skipping %r — invalid disk record: %s", agent_id, exc)
            return
        ctx.spawned_at = disk_info.get('spawned_at', time.time())
        ctx.started_at = disk_info.get('started_at', ctx.spawned_at)
        # Must be called with self._lock held
        self._agents[agent_id] = ctx

    def prewarm_active_agents(self) -> int:
        """Pre-populate _parsed_lines for all agents found on disk.

        Called once at dashboard startup before any clients connect.
        Converts cold disk reads into warm in-memory buffers so the first
        reconnect serves lines from memory instead of re-parsing JSONL files.
        Returns the number of agents warmed.
        """
        disk_agents = self._load_disk_agents()
        warmed = 0
        for agent_id, info in disk_agents.items():
            with self._lock:
                if agent_id in self._agents:
                    continue  # Already tracked in memory (AgentContext or tombstone)
                # Claim the slot with a sentinel so concurrent threads skip this agent
                # while we do file I/O below. Without this, two threads could both see
                # agent_id absent, both build a ctx, and one would overwrite the other.
                self._agents[agent_id] = _PREWARM_SENTINEL
            try:
                ctx = AgentContext(
                    agent_id=agent_id,
                    context=info.get('context') or 'mission',
                    label=info.get('label') or agent_id,
                    pid=info.get('pid') or None,
                )
            except (ValueError, TypeError) as exc:
                logger.warning("prewarm_active_agents: skipping %r — invalid disk record: %s", agent_id, exc)
                with self._lock:
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        del self._agents[agent_id]
                continue
            ctx.spawned_at = info.get('spawned_at', time.time())
            ctx.started_at = info.get('started_at', ctx.spawned_at)
            ctx.status = info.get('status', 'running')
            ctx.completed_at = info.get('completed_at')

            try:
                self._populate_context_from_disk(ctx)
            except (OSError, IOError, UnicodeDecodeError) as exc:
                logger.warning("prewarm_active_agents: error reading %s: %s", ctx.stream_file, exc)

            # If already completed, write snapshot and tombstone immediately
            if ctx.status in ('complete', 'error'):
                tombstone = {
                    '_tombstone': True,
                    'agent_id': agent_id,
                    'context': ctx.context,
                    'label': ctx.label,
                    'stream_file': str(ctx.stream_file),
                    'snapshot_file': str(ctx.stream_file.with_suffix('.snapshot.json')),
                    'status': ctx.status,
                    'started_at': ctx.started_at,
                    'completed_at': ctx.completed_at,
                    'error': ctx.error,
                }
                snapshot_needed = False
                with self._lock:
                    # Replace sentinel with tombstone only if no live registration raced us.
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        self._agents[agent_id] = tombstone
                        warmed += 1
                        snapshot_needed = True
                # snapshot_needed is a local bool set inside the lock — no race possible.
                if snapshot_needed:
                    self._write_snapshot(ctx)
            else:
                should_start = False
                with self._lock:
                    # Replace sentinel with live ctx; skip if another thread claimed slot
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        self._agents[agent_id] = ctx
                        warmed += 1
                        should_start = ctx.status == 'running'
                if should_start:
                    self._start_watcher(ctx, tail_from_end=True, emit_spawn=False)
        return warmed

    def _save_state_locked(self):
        """Write active agent state to STATE_FILE (call with self._lock held)."""
        try:
            state = {
                agent_id: (entry if isinstance(entry, dict) else entry.to_dict())
                for agent_id, entry in self._agents.items()
                if entry is not _PREWARM_SENTINEL
                and not (isinstance(entry, dict) and entry.get('_tombstone'))
            }
            tmp = self.STATE_FILE.with_suffix('.tmp')
            with open(tmp, 'w', encoding='utf-8', errors='replace') as f:
                json.dump(state, f, indent=2)
            tmp.replace(self.STATE_FILE)
        except Exception as e:
            logger.warning("_save_state_locked failed: %s", e)


# =============================================================================
# PID HEALTH CHECK — used by reaper to detect dead agent processes
# =============================================================================

def _is_pid_alive(pid, expected_start_time: float = 0) -> bool:
    """Check if a process is still alive, with PID reuse protection.

    Uses /proc/{pid}/stat (Linux-native, zero-overhead) with os.kill fallback.
    Guards against PID reuse by comparing process start time against expected_start_time.
    """
    if pid is None or pid <= 0:
        return False
    try:
        stat_path = f'/proc/{pid}/stat'
        if not os.path.exists(stat_path):
            return False

        # PID reuse guard: check process start time
        if expected_start_time > 0:
            try:
                with open(stat_path, 'r', encoding='utf-8', errors='replace') as f:
                    stat_content = f.read()
                # Find closing paren — accounts for spaces in comm field
                rparen = stat_content.rfind(')')
                if rparen == -1:
                    raise ValueError("malformed /proc/pid/stat")
                remainder = stat_content[rparen + 2:]  # skip ') '
                fields = remainder.split()
                # starttime is the 20th field after '(comm) ' (0-indexed: 19)
                if len(fields) > 19:
                    boot_ticks = int(fields[19])
                    clock_hz = os.sysconf('SC_CLK_TCK')
                    with open('/proc/stat', 'r', encoding='utf-8', errors='replace') as sf:
                        for line in sf:
                            if line.startswith('btime'):
                                boot_time = int(line.split()[1])
                                proc_start = boot_time + (boot_ticks / clock_hz)
                                # Reject if start times differ by > 30s in either direction (PID reuse)
                                if abs(proc_start - expected_start_time) > 30:
                                    return False
                                break
            except (OSError, ValueError, IndexError, UnicodeDecodeError):
                pass  # Can't verify start time — fall through to basic alive check

        # Fallback: os.kill(pid, 0) — raises if process doesn't exist
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # Process exists but we can't signal it
    except (OSError, ValueError):
        return False


# Module-level singleton
_manager = AgentStreamManager()


# =============================================================================
# MODULE-LEVEL API
# =============================================================================

def register_agent(context: str, agent_id: str, label: str, pid: Optional[int] = None) -> Path:
    """Register a new agent and return its stream file path."""
    return _manager.register_agent(context, agent_id, label, pid)


def update_agent_pid(agent_id: str, pid: int):
    """Update agent PID after spawn."""
    _manager.update_pid(agent_id, pid)


def complete_agent(agent_id: str, error: Optional[str] = None):
    """Mark agent complete or error when subprocess exits."""
    _manager.complete_agent(agent_id, error)


def get_active_agents() -> dict:
    """Return active agents grouped by context (for WebSocket initial_data)."""
    return _manager.get_active_agents()


def get_recent_agents(limit: int = 6, max_age_seconds: int = RECENT_AGENT_WINDOW_SECONDS) -> dict:
    """Return recent running/completed agents grouped by context."""
    return _manager.get_recent_agents(limit=limit, max_age_seconds=max_age_seconds)


def get_agent_stream_lines(agent_id: str, limit: int = 200) -> list:
    """Return buffered parsed stream lines for replay on reconnect."""
    return _manager.get_agent_stream_lines(agent_id, limit)


def cleanup_old_stream_files(max_age_hours: int = 24) -> int:
    """Delete JSONL files older than max_age_hours (active agents preserved)."""
    return _manager.cleanup_old_stream_files(max_age_hours)


def get_stream_history(mission_id: Optional[str] = None, limit: int = 20) -> list:
    """Return list of past agent stream manifests from the persistent store."""
    return _manager.get_stream_history(mission_id=mission_id, limit=limit)


def reap_dead_agents() -> list:
    """Check all 'running' agents and mark dead ones as complete. Returns reaped agent_ids."""
    return _manager.reap_dead_agents()


def prewarm_active_agents() -> int:
    """Pre-warm stream cache for all disk-known agents. Call once at dashboard startup.
    Returns the number of agents warmed."""
    return _manager.prewarm_active_agents()


# =============================================================================
# STDOUT-TO-FILE STREAMING UTILITY
# =============================================================================

def _next_seq_for_writer(agent_id: str) -> int:
    """Allocate the next seq for a writer thread (called from same process as register_agent).

    Re-acquires _lock after ctx.seq_lock to detect dead ctx replacement (TOCTOU guard).
    """
    with _manager._lock:
        ctx = _manager._agents.get(agent_id)
    if ctx is None or isinstance(ctx, dict) or ctx is _PREWARM_SENTINEL:
        # Fallback: writer is running but agent isn't tracked — allocate without coordination.
        # Watcher will normalize via _parse_stream_line on read.
        return 0
    with ctx.seq_lock:
        seq = ctx.next_seq
        ctx.next_seq = seq + 1
    # TOCTOU guard: verify ctx wasn't replaced under us (complete_agent() swaps to tombstone).
    with _manager._lock:
        live_ctx = _manager._agents.get(agent_id)
    if live_ctx is not ctx:
        logger.warning("_next_seq_for_writer: ctx replaced for %s after seq allocation; returning 0", agent_id)
        return 0
    return seq


def stream_stdout_to_file(proc, stream_file: Path, agent_id: str):
    """
    Daemon thread target: read proc.stdout line-by-line, stamp each with a
    monotonic per-agent `seq`, and append to stream_file as a JSONL envelope:
        {"seq": N, "raw": "<original provider line>"}

    Legacy raw lines without an envelope are tolerated by readers (treated as seq=0
    and re-stamped by the watcher).
    """
    try:
        with open(stream_file, 'a', buffering=1, encoding='utf-8', errors='replace') as f:
            for line in proc.stdout:
                if not line:
                    continue
                seq = _next_seq_for_writer(agent_id)
                if seq > 0:
                    f.write(_wrap_with_seq(line, seq))
                else:
                    # No tracked context — write the raw line; watcher will normalize.
                    f.write(line)
                f.flush()
    except Exception as exc:
        logging.error('stream write failed for agent %s: %s', agent_id, exc)
        # Drain stdout so proc can exit without blocking on a full pipe.
        # Do NOT call complete_agent here — finally always runs and will call it.
        try:
            if proc.stdout:
                proc.stdout.read()
        except Exception:
            pass
    finally:
        try:
            proc.wait(timeout=10)
            error = None if proc.returncode == 0 else f"Process exited with code {proc.returncode}"
        except Exception as _wait_exc:
            error = f"Process wait failed: {_wait_exc}"
            try:
                proc.kill()
            except Exception:
                pass
        complete_agent(agent_id, error=error)


def stream_codex_session_to_file(
    proc,
    stream_file: Path,
    workspace_path: str,
    started_at: Optional[float] = None,
    agent_id: Optional[str] = None,
):
    """Mirror matching Codex exec transcript entries into the agent stream file.

    Each transcript line is wrapped as {"seq": N, "raw": "..."} when agent_id is
    supplied so the per-agent monotonic counter is honored.
    """
    started_at = started_at or time.time()
    deadline = time.time() + CODEX_TRANSCRIPT_DISCOVERY_TIMEOUT
    transcript_path = None

    while time.time() < deadline:
        transcript_path = _find_codex_transcript_for_workspace(workspace_path, started_at)
        if transcript_path is not None:
            break
        if proc.poll() is not None:
            break  # Process already exited — stop polling early
        time.sleep(CODEX_TRANSCRIPT_POLL_INTERVAL)

    if transcript_path is None:
        logger.warning("No Codex transcript found for workspace %s", workspace_path)
        return

    idle_after_exit = 0
    file_pos = 0
    pending = ''  # carry partial trailing line between chunk reads

    try:
        with open(stream_file, 'a', buffering=1, encoding='utf-8', errors='replace') as dest:
            while True:
                chunk = ''
                try:
                    with open(transcript_path, 'r', encoding='utf-8', errors='replace') as src:
                        src.seek(file_pos)
                        chunk = src.read()
                        file_pos = src.tell()
                except (OSError, IOError):
                    chunk = ''

                if chunk:
                    pending += chunk
                    # Split out complete lines; leave any trailing partial line in pending.
                    lines = pending.split('\n')
                    pending = lines.pop()
                    for line in lines:
                        if not line:
                            continue
                        if agent_id:
                            seq = _next_seq_for_writer(agent_id)
                            if seq > 0:
                                dest.write(_wrap_with_seq(line, seq))
                                continue
                        dest.write(line + '\n')
                    dest.flush()

                if proc.poll() is None:
                    idle_after_exit = 0
                    time.sleep(CODEX_TRANSCRIPT_POLL_INTERVAL)
                    continue

                if chunk:
                    idle_after_exit = 0
                else:
                    idle_after_exit += 1
                    if idle_after_exit >= 10:
                        # Flush any trailing partial line before exit
                        if pending.strip():
                            if agent_id:
                                seq = _next_seq_for_writer(agent_id)
                                if seq > 0:
                                    dest.write(_wrap_with_seq(pending, seq))
                                    pending = ''
                            if pending:
                                dest.write(pending if pending.endswith('\n') else pending + '\n')
                            dest.flush()
                        break
                time.sleep(CODEX_TRANSCRIPT_POLL_INTERVAL)
    except Exception:
        logger.exception("Failed to mirror Codex transcript for workspace %s", workspace_path)


def reconstruct_text_from_stream_file(stream_file: Path, provider: str = 'claude') -> str:
    """
    Read JSONL stream file and reconstruct the full text response.

    For Claude stream-json format, concatenates all 'text' content blocks.
    For other providers, returns raw file content.

    Preserves existing invoke_llm() return value behavior after switching from
    communicate() to streaming Popen.
    """
    if not stream_file.exists():
        return ''

    try:
        content = stream_file.read_text(encoding='utf-8', errors='replace')
    except (OSError, IOError):
        return ''

    if provider != 'claude':
        return content.strip()

    # Parse JSONL and concatenate text blocks
    text_parts = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        # Unwrap a {seq, raw} envelope so downstream parsing sees the original provider line.
        _seq, unwrapped = _unwrap_seq_line(line)
        # If _unwrap_seq_line returns empty string (e.g. non-string 'raw' field),
        # skip entirely — never fall back to the envelope JSON, which an attacker
        # could craft with "type":"result" to inject content.
        if not unwrapped:
            continue
        target = unwrapped
        try:
            obj = json.loads(target)
        except (json.JSONDecodeError, ValueError):
            continue  # skip non-JSON lines — never inject into reconstructed output

        msg_type = obj.get('type', '')
        if msg_type == 'assistant':
            message = obj.get('message', {})
            if isinstance(message, dict):
                for block in message.get('content', []):
                    if isinstance(block, dict) and block.get('type') == 'text':
                        text = block.get('text', '')
                        if text:
                            text_parts.append(text)
        elif msg_type == 'result':
            # Final result block is the canonical complete response
            result_text = obj.get('result', '')
            if isinstance(result_text, str) and result_text:
                return result_text.strip()

    return '\n'.join(text_parts).strip()
