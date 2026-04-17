"""
agent_stream_manager.py — Real-time agent activity streaming for AtlasForge dashboard.

Manages per-agent JSONL stream files and pumps parsed events via SocketIO to
mission_agents and investigation_agents rooms. Called from:
  - atlasforge_conductor.py (invoke_llm) → 'mission' context
  - investigation_engine.py (invoke_claude) → 'investigation' context

Architecture:
  subprocess.Popen stdout → daemon writer thread → /tmp/atlasforge_agent_streams/{id}.jsonl
                                                    → AgentStreamWatcher thread → SocketIO emit
"""

import json
import os
import threading
import time
import urllib.request
import urllib.error
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Optional

# =============================================================================
# STREAM DIRECTORY
# =============================================================================

STREAM_DIR = Path('/home/vader/AI-AtlasForge/state/agent_streams')
CODEX_SESSIONS_DIR = Path.home() / '.codex' / 'sessions'
CODEX_TRANSCRIPT_DISCOVERY_TIMEOUT = 12.0
CODEX_TRANSCRIPT_POLL_INTERVAL = 0.2
CODEX_TRANSCRIPT_MAX_SCAN = 200
RECENT_AGENT_WINDOW_SECONDS = 1800

logger = logging.getLogger(__name__)

# Sentinel used by prewarm_active_agents() to claim a slot in self._agents
# before file I/O completes, eliminating the TOCTOU window between the
# first-check lock and the insert lock.
_PREWARM_SENTINEL = object()


def _ensure_stream_dir():
    STREAM_DIR.mkdir(parents=True, exist_ok=True)


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
    content_blocks = message.get('content', []) if isinstance(message, dict) else []

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
    except OSError:
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

    try:
        files = sorted(
            CODEX_SESSIONS_DIR.rglob('*.jsonl'),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
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
    Daemon thread that tails a single agent's JSONL stream file and emits
    parsed events via SocketIO to the appropriate room.

    Stops when:
    - Agent is marked complete/error
    - 600s inactivity timeout elapses
    """

    INACTIVITY_TIMEOUT = 600  # seconds

    def __init__(self, ctx: AgentContext):
        super().__init__(daemon=True, name=f"watcher-{ctx.agent_id}")
        self.ctx = ctx

    def run(self):
        _emit_agent_event(self.ctx, {
            'event': 'agent_spawned',
            'agent_id': self.ctx.agent_id,
            'label': self.ctx.label,
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
                with open(stream_file, 'r') as f:
                    f.seek(file_pos)
                    lines = f.readlines()
                    new_pos = f.tell()

                if lines:
                    last_activity = time.time()
                    file_pos = new_pos
                    for line in lines:
                        event = _parse_jsonl_line(line)
                        if event is None:
                            continue
                        # Store in context buffer
                        with self.ctx._lock:
                            self.ctx._parsed_lines.append({
                                **event,
                                'timestamp': datetime.now().isoformat(),
                            })
                        # Emit via SocketIO
                        _emit_agent_stream_line(self.ctx, event)
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
        _emit_agent_event(self.ctx, {
            'event': 'agent_complete' if self.ctx.status != 'error' else 'agent_error',
            'agent_id': self.ctx.agent_id,
            'label': self.ctx.label,
            'error': self.ctx.error,
            'duration_seconds': duration,
            'line_count': line_count,
            'timestamp': datetime.now().isoformat(),
        })


# =============================================================================
# SOCKETIO EMIT HELPERS
# =============================================================================

# Dashboard IPC: port where the dashboard HTTPS server listens.
# Subprocesses POST agent events here so the dashboard process can re-emit
# them to connected browser clients via its own SocketIO instance.
def _get_ipc_url():
    _DEFAULT_PORT = 5010
    try:
        port = int(os.environ.get('ATLASFORGE_PORT', str(_DEFAULT_PORT)))
        if not (1 <= port <= 65535):
            port = _DEFAULT_PORT
    except (ValueError, TypeError):
        port = _DEFAULT_PORT
    return f'https://localhost:{port}/api/internal/agent-event'

# Module-level flag: True when this code is running INSIDE the dashboard process
_running_in_dashboard: bool = False


def set_dashboard_mode(in_dashboard: bool = True):
    """Call this from dashboard_v2.py to indicate we are in the dashboard process."""
    global _running_in_dashboard
    _running_in_dashboard = in_dashboard


def _emit_via_ipc(room: str, payload: dict, retries: int = 2):
    """
    Post an agent event to the dashboard's internal IPC endpoint.

    Used when running in a subprocess (conductor, investigation_engine) that
    cannot access the dashboard process's SocketIO instance directly.
    Runs in a daemon thread to avoid blocking the caller.
    Falls back silently on any error — streaming is non-critical.

    retries: number of additional attempts after the first on transient failure.
    agent_spawned events use retries=2 to reduce dropped initial registrations.
    """
    def _post():
        import ssl
        import logging as _logging
        log = _logging.getLogger(__name__)
        body = json.dumps({'room': room, 'payload': payload}).encode('utf-8')
        ipc_url = _get_ipc_url()
        ssl_ctx = ssl.create_default_context()
        # SSL verification intentionally disabled for localhost-only IPC.
        # The dashboard runs on localhost (HTTP, not HTTPS) in development.
        # Risk: any local process could impersonate the dashboard endpoint.
        # Acceptable: this is internal tooling, not internet-facing infrastructure.
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(
                    ipc_url,
                    data=body,
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                with urllib.request.urlopen(req, timeout=3, context=ssl_ctx) as resp:
                    pass  # Fire-and-forget; ignore response body
                return  # Success
            except Exception as e:
                if attempt < retries:
                    time.sleep(0.2 * (attempt + 1))
                else:
                    log.warning(f"IPC emit failed after {retries + 1} attempts (room={room}): {e}")

    t = threading.Thread(target=_post, daemon=True)
    t.start()


def _emit_to_socketio(room: str, payload: dict):
    """
    Emit directly via websocket_events (only works when in the dashboard process).
    """
    try:
        from websocket_events import emit_widget_update
        emit_widget_update(room, payload)
    except Exception:
        pass  # Non-critical


def _emit(room: str, payload: dict, retries: int = 0):
    """
    Route emission: direct SocketIO when in dashboard process, HTTP IPC otherwise.
    retries is passed through to _emit_via_ipc for lifecycle events that need reliability.
    """
    if _running_in_dashboard:
        _emit_to_socketio(room, payload)
    else:
        _emit_via_ipc(room, payload, retries=retries)


def _emit_agent_event(ctx: AgentContext, payload: dict):
    """Emit an agent lifecycle event to the appropriate SocketIO room.

    agent_spawned events use retries=2 so transient IPC failures don't silently
    drop the initial registration that makes the agent tab appear in the dashboard.
    """
    room = 'mission_agents' if ctx.context == 'mission' else 'investigation_agents'
    retries = 2 if payload.get('event') == 'agent_spawned' else 0
    _emit(room, payload, retries=retries)


def _emit_agent_stream_line(ctx: AgentContext, event: dict):
    """Emit a parsed stream line event to the appropriate SocketIO room."""
    room = 'mission_agents' if ctx.context == 'mission' else 'investigation_agents'
    _emit(room, {
        'event': 'agent_stream_line',
        'agent_id': ctx.agent_id,
        'label': ctx.label,
        'event_type': event['event_type'],
        'text': event['display_text'],
        'timestamp': datetime.now().isoformat(),
    })


# =============================================================================
# AGENT STREAM MANAGER — singleton
# =============================================================================

class AgentStreamManager:
    """
    Singleton. Tracks all active agents, manages state file, spawns/reaps watcher threads.
    """

    STATE_FILE = STREAM_DIR / 'active_agents.json'

    def __init__(self):
        self._agents: dict = {}  # agent_id -> AgentContext
        self._lock = threading.Lock()
        _ensure_stream_dir()

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
        _ensure_stream_dir()
        ctx = AgentContext(agent_id=agent_id, context=context, label=label, pid=pid)

        with self._lock:
            self._agents[agent_id] = ctx
            self._save_state_locked()

        # Spawn watcher thread
        watcher = AgentStreamWatcher(ctx)
        watcher.start()

        return ctx.stream_file

    def update_pid(self, agent_id: str, pid: int):
        """Update PID after process is spawned (if PID wasn't known at register time)."""
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
            with open(tmp, 'w') as f:
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
        """Write agent error to journal log, GlassBox, and emit via SocketIO."""
        try:
            mission_id = self._get_current_mission_id()

            # 1. Emit to dashboard via SocketIO (or IPC if in subprocess)
            try:
                room = 'mission_agents' if ctx.context == 'mission' else 'investigation_agents'
                _emit(room, {
                    'event': 'agent_error_logged',
                    'agent_id': ctx.agent_id,
                    'label': ctx.label,
                    'context': ctx.context,
                    'error': error,
                    'mission_id': mission_id,
                    'timestamp': datetime.now().isoformat(),
                })
            except Exception:
                pass

            # 2. Append to journal log
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
            with open(errors_path, 'a', buffering=1) as f:
                f.write(json.dumps(entry) + '\n')
        except Exception:
            pass

    def _get_current_mission_id(self) -> Optional[str]:
        """Read current mission_id from state/mission.json."""
        try:
            af_root = Path(__file__).parent
            mission_path = af_root / 'state' / 'mission.json'
            if mission_path.exists():
                with open(mission_path, 'r') as f:
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
            with open(journal_path, 'a', buffering=1) as f:
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
                with open(self.STATE_FILE, 'r') as f:
                    return json.load(f)
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
            if snap_path and Path(snap_path).exists():
                try:
                    with open(snap_path) as f:
                        data = json.load(f)
                    lines_all = data.get('lines', [])
                    return lines_all if limit is None else lines_all[-limit:]
                except Exception:
                    pass
            # Snapshot missing — fall back to JSONL
            stream_file = entry.get('stream_file')
            if stream_file and Path(stream_file).exists():
                return self._parse_jsonl_file(Path(stream_file), limit)
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
                    event = _parse_jsonl_line(raw_line)
                    if event:
                        disk_lines.append({**event, 'timestamp': ''})
        except Exception:
            pass
        if limit is not None and limit > 0:
            return disk_lines[-limit:]
        return disk_lines

    def _get_stream_lines_from_disk(self, agent_id: str, limit: int) -> list:
        """Scan STREAM_DIR for any file matching *_{agent_id}.jsonl and parse it."""
        _ensure_stream_dir()
        import re as _re_safe
        safe_id = _re_safe.sub(r'[^a-zA-Z0-9_\-]', '_', agent_id)
        for context in ('mission', 'investigation'):
            candidate = STREAM_DIR / f"{context}_{safe_id}.jsonl"
            if candidate.exists():
                return self._parse_jsonl_file(candidate, limit)
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

        # Sort newest-first; trim to limit*3 before expensive enrichment
        candidates.sort(key=lambda t: t[0], reverse=True)
        if limit is None:
            limit = len(candidates)  # no-op limit: keep all
        candidates = candidates[:limit * 3]

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
        if isinstance(max_age_hours, bool) or max_age_hours is None or max_age_hours <= 0:
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
            # Never delete the snapshot whose base .jsonl is still active
            if p.suffix == '.json':
                base_jsonl = p.with_suffix('.jsonl')
                if base_jsonl in active_paths:
                    continue
            try:
                if p.stat().st_mtime < cutoff:
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

        # Collect candidates from in-memory state (skip tombstones and sentinels)
        with self._lock:
            candidates = [
                (aid, entry.pid, entry.spawned_at)
                for aid, entry in self._agents.items()
                if not isinstance(entry, dict)
                and entry is not _PREWARM_SENTINEL
                and entry.status == 'running'
                and entry.pid is not None and entry.pid > 0
            ]

        # Also check disk state for cross-process agents (registered by conductor)
        with self._lock:
            disk_agents = self._load_disk_agents()
            for aid, info in disk_agents.items():
                if info.get('status') == 'running' and (info.get('pid') or 0) > 0:
                    if aid not in self._agents:
                        candidates.append((aid, info['pid'], info.get('spawned_at', 0)))

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
                        self._hydrate_disk_agent(agent_id, disk_agents.get(agent_id, {}))
                        ctx_for_io = self._agents[agent_id]
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
        ctx = AgentContext(
            agent_id=agent_id,
            context=disk_info.get('context', 'mission'),
            label=disk_info.get('label', agent_id),
            pid=disk_info.get('pid') or None,
        )
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
            ctx = AgentContext(
                agent_id=agent_id,
                context=info.get('context', 'mission'),
                label=info.get('label', agent_id),
                pid=info.get('pid') or None,
            )
            ctx.spawned_at = info.get('spawned_at', time.time())
            ctx.started_at = info.get('started_at', ctx.spawned_at)
            ctx.status = info.get('status', 'running')
            ctx.completed_at = info.get('completed_at')

            if ctx.stream_file.exists():
                try:
                    for raw_line in ctx.stream_file.read_text().splitlines():
                        event = _parse_jsonl_line(raw_line)
                        if event:
                            ctx._parsed_lines.append({
                                **event,
                                'timestamp': datetime.fromtimestamp(ctx.spawned_at).isoformat(),
                            })
                except Exception:
                    pass

            # If already completed, write snapshot and tombstone immediately
            if ctx.status in ('complete', 'error'):
                self._write_snapshot(ctx)
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
                with self._lock:
                    # Replace sentinel with tombstone; skip if another thread claimed slot
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        self._agents[agent_id] = tombstone
                        warmed += 1
            else:
                with self._lock:
                    # Replace sentinel with live ctx; skip if another thread claimed slot
                    if self._agents.get(agent_id) is _PREWARM_SENTINEL:
                        self._agents[agent_id] = ctx
                        warmed += 1
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
            with open(tmp, 'w') as f:
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
                with open(stat_path, 'r') as f:
                    parts = f.read().split()
                    # Field 22 (0-indexed: 21) is starttime in clock ticks
                    if len(parts) > 21:
                        boot_ticks = int(parts[21])
                        clock_hz = os.sysconf('SC_CLK_TCK')
                        with open('/proc/stat', 'r') as sf:
                            for line in sf:
                                if line.startswith('btime'):
                                    boot_time = int(line.split()[1])
                                    proc_start = boot_time + (boot_ticks / clock_hz)
                                    # If proc started > 30s after agent was spawned, it's PID reuse
                                    if proc_start > expected_start_time + 30:
                                        return False
                                    break
            except (OSError, ValueError, IndexError):
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

def stream_stdout_to_file(proc, stream_file: Path, agent_id: str):
    """
    Daemon thread target: read proc.stdout line-by-line and write to stream_file.

    Usage:
        t = threading.Thread(target=stream_stdout_to_file, args=(proc, stream_file, agent_id), daemon=True)
        t.start()
    """
    try:
        with open(stream_file, 'a', buffering=1) as f:
            for line in proc.stdout:
                f.write(line)
                f.flush()
    except Exception:
        pass
    finally:
        try:
            proc.wait()
            error = None if proc.returncode == 0 else f"Process exited with code {proc.returncode}"
        except Exception as _wait_exc:
            error = f"Process wait failed: {_wait_exc}"
        complete_agent(agent_id, error=error)


def stream_codex_session_to_file(proc, stream_file: Path, workspace_path: str, started_at: Optional[float] = None):
    """Mirror matching Codex exec transcript entries into the agent stream file."""
    started_at = started_at or time.time()
    deadline = time.time() + CODEX_TRANSCRIPT_DISCOVERY_TIMEOUT
    transcript_path = None

    while time.time() < deadline:
        transcript_path = _find_codex_transcript_for_workspace(workspace_path, started_at)
        if transcript_path is not None:
            break
        time.sleep(CODEX_TRANSCRIPT_POLL_INTERVAL)

    if transcript_path is None:
        logger.warning("No Codex transcript found for workspace %s", workspace_path)
        return

    idle_after_exit = 0
    file_pos = 0

    try:
        with open(stream_file, 'a', buffering=1, encoding='utf-8') as dest:
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
                    dest.write(chunk)
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
        content = stream_file.read_text()
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
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            text_parts.append(line)
            continue

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
            if result_text:
                return result_text.strip()

    return '\n'.join(text_parts).strip()
