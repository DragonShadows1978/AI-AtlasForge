#!/usr/bin/env python3
"""
AI-AtlasForge Dashboard (Modular Architecture)

A clean, focused control center for the Claude R&D system.
Features:
    - Service control (start/stop Claude)
    - R&D Mission management
    - Chat interface
    - Real-time status

Access: http://localhost:5010

Architecture:
    This file serves as the main entry point and orchestrator.
    Route handlers are organized into modular blueprints in dashboard_modules/
    HTML templates are stored in dashboard_templates/
"""

import json
import os
import re
import secrets
import signal
import subprocess
import threading
import time
import gzip
from io import BytesIO
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template_string, jsonify, request, Response, send_file, abort, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room
import mimetypes

import logging

import io_utils

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).parent.resolve()
STATE_DIR = BASE_DIR / "state"
WORKSPACE_DIR = BASE_DIR / "workspace"
LOG_DIR = BASE_DIR / "logs"
TEMPLATES_DIR = BASE_DIR / "dashboard_templates"
STATIC_DIR = BASE_DIR / "dashboard_static"

# SSL Configuration
CERTS_DIR = BASE_DIR / "certs"
SSL_CERT = CERTS_DIR / "cert.pem"
SSL_KEY = CERTS_DIR / "key.pem"

# State files
CLAUDE_STATE_PATH = STATE_DIR / "claude_state.json"
CLAUDE_JOURNAL_PATH = STATE_DIR / "claude_journal.jsonl"
CLAUDE_PROMPT_PATH = STATE_DIR / "claude_prompt.json"
CHAT_HISTORY_PATH = STATE_DIR / "chat_history.json"
MISSION_PATH = STATE_DIR / "mission.json"
PROPOSALS_PATH = STATE_DIR / "proposals.json"
RECOMMENDATIONS_PATH = STATE_DIR / "recommendations.json"
MISSION_QUEUE_PATH = STATE_DIR / "mission_queue.json"
LLM_PROVIDER_PATH = STATE_DIR / "llm_provider.json"
PID_PATH = BASE_DIR / "atlasforge_conductor.pid"

# Ensure directories exist
STATE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# =============================================================================
# LOAD HTML TEMPLATES
# =============================================================================

def load_template(name):
    """Load an HTML template from the templates directory."""
    template_path = TEMPLATES_DIR / f"{name}.html"
    if template_path.exists():
        return template_path.read_text()
    # Fallback to simple placeholder
    return f"<html><body><h1>Template '{name}' not found</h1></body></html>"

HTML_TEMPLATE = load_template("main")
HTML_BUNDLED_TEMPLATE = load_template("main_bundled")
TIMELINE_PAGE_HTML = load_template("timeline")

# =============================================================================
# FLASK APP SETUP
# =============================================================================

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path='/static')
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', secrets.token_hex(32))

# Template toggle: use bundled template by default in production
# Set FLASK_USE_BUNDLED=false to use legacy template
app.config['USE_BUNDLED'] = os.environ.get('FLASK_USE_BUNDLED', 'true').lower() == 'true'

# Compression configuration
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'text/javascript',
    'application/javascript', 'application/json'
]
app.config['COMPRESS_MIN_SIZE'] = 500

socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    async_mode='threading',
    ping_interval=25,        # Server sends ping every 25s
    ping_timeout=120,        # 120s timeout tolerates VPN/DERP relay latency + browser-throttled tabs
    max_http_buffer_size=1_000_000,  # 1MB for large widget payloads
    logger=False,
    engineio_logger=False
)

# Track app start time for health checks
app._start_time = time.time()

# Track seen messages for deduplication (bounded deque prevents unbounded growth)
from collections import deque as _deque
seen_messages = _deque(maxlen=200)
_seen_messages_lock = threading.Lock()

# =============================================================================
# WEBSOCKET STATE TRACKING (for real-time push)
# =============================================================================

# State cache for change detection
_ws_state_cache = {
    'mission': {},
    'journal': [],
    'glassbox': {},
    'atlasforge_stats': {},
    'connected_clients': 0,
    'last_check': 0
}

# Rooms that widgets can subscribe to
VALID_WS_ROOMS = [
    'mission_status',    # Mission stage, iteration, running status
    'journal',           # Journal entries
    'atlasforge_stats',  # AtlasForge exploration stats, drift, coverage
    'glassbox',          # GlassBox introspection data
    'analytics',         # Cost/token analytics
    'semantic_updates',  # Semantic search alerts (drift, quality warnings)
    'exploration',       # Exploration graph updates
    'investigation',     # Investigation mode updates
    'backup_status',     # Backup health and stale alerts
    'recommendations',   # Mission recommendations (next mission suggestions)
    'file_events',       # File creation/modification events during missions
    'glassbox_archive',  # GlassBox transcript archival events
    'subprocess_gate',   # Intelligent Subprocess Gate status
    'mission_params',        # Active mission validated parameters + audit summary
    'research_progress',     # Pre-planning research progress (rendered in Mission panel)
    'pool_status',           # Subagent pool utilization updates
]
# Note: mission_agents and investigation_agents are now served via SSE
# (see dashboard_modules/agent_sse.py). The flask-socketio rooms with those
# names were removed during the SSE rebuild — clients connect over EventSource
# at /api/agents/stream?context=mission|investigation instead.

# Register websocket_events module with socketio reference
try:
    from websocket_events import set_socketio
    set_socketio(socketio)
except ImportError:
    pass

# Register mission_status_schema module with socketio reference
try:
    from dashboard_modules.mission_status_schema import set_socketio as set_schema_socketio
    set_schema_socketio(socketio)
except ImportError:
    pass

# Tell agent_stream_manager we are in the dashboard process (enables direct SocketIO emission)
try:
    from agent_stream_manager import set_dashboard_mode
    set_dashboard_mode(True)
except ImportError:
    pass

# Startup cleanup: immediately reap any stale agents from previous crashes
try:
    from agent_stream_manager import reap_dead_agents as _startup_reap
    _startup_reaped = _startup_reap()
    if _startup_reaped:
        print(f"[PID Reaper] Startup cleanup: reaped {len(_startup_reaped)} stale agents: {_startup_reaped}")
except Exception as e:
    logger.warning("Startup agent reap failed: %s", e)


def _start_agent_pid_reaper():
    """Start background thread that periodically reaps dead agent processes."""
    import logging as _reaper_logging
    _reaper_logger = _reaper_logging.getLogger('pid_reaper')

    def _reaper_loop():
        import time as _time
        while True:
            _time.sleep(3)
            try:
                from agent_stream_manager import reap_dead_agents
                reaped = reap_dead_agents()
                if reaped:
                    _reaper_logger.info(f"PID reaper: cleaned up {len(reaped)} dead agents: {reaped}")
            except Exception as e:
                _reaper_logger.debug("PID reaper loop error: %s", e)

    t = threading.Thread(target=_reaper_loop, daemon=True, name="agent-pid-reaper")
    t.start()


# Start the PID reaper immediately (runs in dashboard process)
_start_agent_pid_reaper()

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def find_process(script_name: str) -> dict | None:
    """Find a running process by script name with PID file priority."""
    # First check PID file for exact match
    if PID_PATH.exists():
        try:
            pid = int(PID_PATH.read_text().strip())
            cmdline_path = Path(f"/proc/{pid}/cmdline")
            if cmdline_path.exists():
                cmdline = cmdline_path.read_text().replace('\x00', ' ')
                if script_name in cmdline and 'python' in cmdline:
                    return {"pid": pid, "cmd": cmdline}
        except Exception as e:
            logger.debug("find_process: PID file read failed: %s", e)

    # Fallback to pgrep with strict pattern
    try:
        # Use ^python3.*script_name to avoid matching 'tail -f' etc.
        pattern = f"python3.*{re.escape(script_name)}"
        result = subprocess.run(
            ["pgrep", "-af", pattern],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if line and script_name in line and 'python' in line and 'grep' not in line and 'pgrep' not in line:
                parts = line.split(None, 1)
                if parts:
                    return {"pid": int(parts[0]), "cmd": parts[1] if len(parts) > 1 else ""}
    except Exception as e:
        logger.debug("find_process: pgrep fallback failed: %s", e)
    return None


# Process detection cache: avoids repeated pgrep subprocess calls (50-500ms each)
_process_cache: dict = {'pid': None, 'valid': False, 'checked_at': 0.0}
_PROCESS_CACHE_TTL = 3.0  # seconds (was 1.0 — pgrep fallback costs 50-500ms)
_process_cache_lock = threading.Lock()


def find_process_cached(script_name: str) -> dict | None:
    """Cached wrapper for find_process() — avoids repeated pgrep subprocess calls.

    Returns cached result if checked within the last _PROCESS_CACHE_TTL seconds.
    Falls through to find_process() on cache miss or expiry.
    """
    now = time.time()
    with _process_cache_lock:
        if now - _process_cache['checked_at'] < _PROCESS_CACHE_TTL:
            if _process_cache['valid']:
                return {'pid': _process_cache['pid'], 'cmd': ''}
            return None

    result = find_process(script_name)
    with _process_cache_lock:
        _process_cache['pid'] = result.get('pid') if result else None
        _process_cache['valid'] = result is not None
        _process_cache['checked_at'] = now
    return result


def _normalize_provider(provider: str | None) -> str:
    """Normalize provider identifier to supported values."""
    if not provider:
        return "claude"

    normalized = str(provider).strip().lower()
    if normalized in ("claude", "codex", "gemini", "system", "suggestion"):
        return normalized
    return "claude"


def get_llm_provider() -> str:
    """Get the persisted LLM provider selection."""
    data = io_utils.atomic_read_json(LLM_PROVIDER_PATH, {})
    provider = data.get("provider")
    normalized = _normalize_provider(provider)

    # Self-heal invalid persisted values to prevent repeated fallback ambiguity.
    if provider != normalized:
        set_llm_provider(normalized)

    return normalized


def set_llm_provider(provider: str) -> str:
    """Persist the LLM provider selection and return normalized value."""
    normalized = _normalize_provider(provider)
    io_utils.atomic_write_json(LLM_PROVIDER_PATH, {
        "provider": normalized,
        "updated_at": datetime.now().isoformat()
    })
    return normalized


def _resolve_chat_provider(msg: dict, fallback_provider: str) -> str:
    """Resolve per-message provider with fallback to current provider."""
    if not isinstance(msg, dict):
        return fallback_provider
    return _normalize_provider(msg.get("provider") or fallback_provider)


def _load_env_file_values(env_path: Path) -> dict:
    """Load KEY=VALUE pairs from a local .env file (best-effort)."""
    values = {}
    if not env_path.exists():
        return values

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except Exception as e:
        # Never block dashboard operations on optional .env parsing.
        logger.debug(".env parse failed: %s", e)
        return {}

    return values


def _resolve_chat_display_role(msg: dict, fallback_provider: str) -> str:
    """
    Resolve display role for chat activity.

    Stored role remains `claude` for compatibility, but display role should
    reflect the active provider (`claude`/`codex`/`gemini`).
    """
    role = str((msg or {}).get("role", "")).strip().lower()
    if role == "claude":
        provider = _resolve_chat_provider(msg, fallback_provider)
        if provider in ("codex", "gemini", "system", "suggestion"):
            return provider
        return "claude"
    return role or "unknown"


def _serialize_chat_message(msg: dict, fallback_provider: str) -> dict:
    """Serialize chat message with provider-aware display role metadata."""
    role = str((msg or {}).get("role", "")).strip().lower() or "unknown"
    provider = _resolve_chat_provider(msg, fallback_provider)
    return {
        "role": role,
        "display_role": _resolve_chat_display_role(msg, fallback_provider),
        "provider": provider,
        "content": (msg or {}).get("content", ""),
        "timestamp": (msg or {}).get("timestamp"),
    }


def get_claude_status() -> dict:
    """Get Claude autonomous status."""
    proc = find_process_cached("atlasforge_conductor.py")
    state = io_utils.atomic_read_json(CLAUDE_STATE_PATH, {})
    mission = io_utils.atomic_read_json(MISSION_PATH, {})
    provider = get_llm_provider()

    full_mission = mission.get("problem_statement", "No mission set")
    return {
        "running": proc is not None,
        "pid": proc["pid"] if proc else None,
        "provider": provider,
        "mode": state.get("mode", "unknown"),
        "boot_count": state.get("boot_count", 0),
        "total_cycles": state.get("total_cycles", 0),
        "last_boot": state.get("last_boot"),
        "current_task": state.get("current_task"),
        "rd_stage": mission.get("current_stage", "N/A"),
        "rd_iteration": mission.get("iteration", 0),
        "mission": full_mission,
        "mission_preview": full_mission[:100] + "..." if len(full_mission) > 100 else full_mission,
        "current_cycle": mission.get("current_cycle", 1),
        "cycle_budget": mission.get("cycle_budget", 1),
        "original_mission": mission.get("original_problem_statement", ""),
        "project_name": mission.get("project_name", ""),
        "project_workspace": mission.get("project_workspace", "")
    }


def get_recent_journal(n: int = 10) -> list:
    """Get recent journal entries."""
    if n <= 0:
        return []
    entries = []
    if CLAUDE_JOURNAL_PATH.exists():
        try:
            with open(CLAUDE_JOURNAL_PATH, 'r') as f:
                lines = f.readlines()
            for line in lines[-n:]:
                try:
                    entry = json.loads(line)
                    full_msg = entry.get("message", entry.get("work_done", ""))
                    is_truncated = len(full_msg) > 100
                    entries.append({
                        "type": entry.get("type", "unknown"),
                        "timestamp": entry.get("timestamp", ""),
                        "status": entry.get("status", ""),
                        "message": full_msg[:100] if is_truncated else full_msg,
                        "full_message": full_msg,
                        "is_truncated": is_truncated
                    })
                except Exception as e:
                    logger.debug("get_recent_journal: skipping entry: %s", e)
        except Exception as e:
            logger.warning("get_recent_journal: failed to read journal: %s", e)
    return entries


_ALLOWED_MODES = {"rd", "free"}

def start_claude(mode: str = "rd") -> tuple[bool, str]:
    """Start Claude autonomous."""
    if mode not in _ALLOWED_MODES:
        return False, f"Invalid mode: {mode!r}. Allowed: {sorted(_ALLOWED_MODES)}"
    if find_process_cached("atlasforge_conductor.py"):
        return False, "Already running"

    script_path = BASE_DIR / "atlasforge_conductor.py"
    if not script_path.exists():
        return False, "Script not found"

    try:
        log_file = LOG_DIR / "atlasforge_conductor.log"
        provider = get_llm_provider()
        env = os.environ.copy()
        # Ensure dashboard-launched processes receive local .env overrides even
        # when dashboard itself is started via systemd with stale env values.
        env.update(_load_env_file_values(BASE_DIR / ".env"))
        env["ATLASFORGE_LLM_PROVIDER"] = provider
        env["ATLASFORGE_PORT"] = str(PORT)  # Ensure conductor subprocess uses same port for IPC

        venv_python = BASE_DIR / ".venv" / "bin" / "python3"
        python_bin = str(venv_python) if venv_python.exists() else "python3"
        log_fd = open(log_file, 'a')
        try:
            subprocess.Popen(
                [python_bin, str(script_path), f"--mode={mode}"],
                cwd=str(BASE_DIR),
                stdout=log_fd,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=env
            )
        finally:
            log_fd.close()
        time.sleep(2)

        if find_process_cached("atlasforge_conductor.py"):
            return True, f"Started in {mode} mode ({provider})"
        return False, "Failed to start"
    except Exception as e:
        logger.error(f"start_claude failed: {e}")
        return False, "An internal error occurred"


def stop_claude() -> tuple[bool, str]:
    """Stop Claude autonomous."""
    proc = find_process_cached("atlasforge_conductor.py")
    if not proc:
        return False, "Not running"

    try:
        os.kill(proc["pid"], signal.SIGTERM)
        time.sleep(2)

        fresh = find_process("atlasforge_conductor.py")
        if fresh:
            os.kill(fresh["pid"], signal.SIGKILL)
            time.sleep(1)

        return True, "Stopped"
    except ProcessLookupError:
        return True, "Already stopped"
    except Exception as e:
        logger.error(f"stop_claude failed: {e}")
        return False, "An internal error occurred"


def send_message_to_claude(message: str) -> bool:
    """Send a message to Claude via prompt file."""
    io_utils.atomic_write_json(CLAUDE_PROMPT_PATH, {
        "pending": True,
        "prompt": message,
        "from": "human",
        "timestamp": datetime.now().isoformat()
    })
    return True


def get_ssl_context():
    """Get SSL context if certificates exist and SSL is enabled.

    Returns:
        Tuple of (cert_path, key_path) if SSL is enabled and certs exist,
        None otherwise (falls back to HTTP).

    Environment:
        DASHBOARD_SSL: Set to 'false' to disable HTTPS (default: 'true')
    """
    ssl_enabled = os.environ.get('DASHBOARD_SSL', 'true').lower() == 'true'
    if ssl_enabled and SSL_CERT.exists() and SSL_KEY.exists():
        return (str(SSL_CERT), str(SSL_KEY))
    return None


# =============================================================================
# REGISTER EXTERNAL MODULES (BLUEPRINTS)
# =============================================================================

# GlassBox introspection system
import sys
sys.path.insert(0, str(Path(__file__).parent / "workspace"))
try:
    from glassbox.dashboard_routes import glassbox_bp
    GLASSBOX_AVAILABLE = True
except ImportError:
    GLASSBOX_AVAILABLE = False
    print("Warning: GlassBox not available")

# Register workspace blueprints
if GLASSBOX_AVAILABLE:
    app.register_blueprint(glassbox_bp)

# =============================================================================
# REGISTER DASHBOARD MODULE BLUEPRINTS
# =============================================================================
from dashboard_modules import (
    core_bp, init_core_blueprint,
    knowledge_base_bp,
    analytics_bp, init_analytics_blueprint,
    atlasforge_bp, register_archival_routes,
    recovery_bp, init_recovery_blueprint,
    investigation_bp, init_investigation_blueprint,
    services_bp,
    cache_bp,
    url_handlers_bp,
    queue_scheduler_bp, init_queue_scheduler_blueprint,
    semantic_bp, init_semantic_blueprint,
    version_bp, init_version_blueprint,
    get_bundle_version, init_bundle_version,
    artifact_health_bp, init_artifact_health_blueprint,
    mission_params_bp, init_mission_params_blueprint, get_mission_params,
    pool_manager_bp, init_pool_manager_blueprint,
)

# Initialize blueprints with dependencies
init_core_blueprint(
    base_dir=BASE_DIR,
    state_dir=STATE_DIR,
    workspace_dir=WORKSPACE_DIR,
    mission_path=MISSION_PATH,
    proposals_path=PROPOSALS_PATH,
    recommendations_path=RECOMMENDATIONS_PATH,
    io_utils_module=io_utils,
    status_fn=get_claude_status,
    start_fn=start_claude,
    stop_fn=stop_claude,
    send_msg_fn=send_message_to_claude,
    journal_fn=get_recent_journal,
    get_provider_fn=get_llm_provider,
    set_provider_fn=set_llm_provider,
    narrative_status_fn=None,
    narrative_start_fn=None,
    narrative_stop_fn=None,
    narrative_send_msg_fn=None,
    narrative_chat_fn=None,
    narrative_mission_path=None,
    mission_queue_path=MISSION_QUEUE_PATH
)

init_analytics_blueprint(MISSION_PATH, io_utils)
init_recovery_blueprint(MISSION_PATH, io_utils)
init_investigation_blueprint(BASE_DIR, STATE_DIR, io_utils, socketio)
init_queue_scheduler_blueprint(socketio)
# Semantic blueprint needs the mission workspace to find semantic_search_engine
# Default to the current mission workspace if available, using centralized resolver
current_mission_workspace = None
try:
    mission_data = io_utils.read_json(MISSION_PATH)
    if mission_data and 'mission_id' in mission_data:
        # Use centralized workspace resolver for correct path with shared/legacy support
        from dashboard_modules.workspace_resolver import resolve_mission_workspace
        missions_dir = BASE_DIR / 'missions'
        current_mission_workspace = str(resolve_mission_workspace(
            mission_data['mission_id'],
            missions_dir,
            WORKSPACE_DIR,
            io_utils,
            mission_data
        ))
except Exception as e:
    logger.warning("Failed to resolve mission workspace: %s", e)
init_semantic_blueprint(mission_workspace=current_mission_workspace, socketio=socketio, io_utils=io_utils)
init_version_blueprint(BASE_DIR)
init_bundle_version(STATIC_DIR, BASE_DIR)
init_artifact_health_blueprint(WORKSPACE_DIR / "artifacts")
init_mission_params_blueprint(MISSION_PATH, BASE_DIR / "missions", io_utils, emit_callback=lambda room, data: emit_widget_update(room, data))
init_pool_manager_blueprint(socketio)

# Register blueprints
app.register_blueprint(core_bp)
app.register_blueprint(knowledge_base_bp)
app.register_blueprint(analytics_bp)
app.register_blueprint(atlasforge_bp)
app.register_blueprint(recovery_bp)
app.register_blueprint(investigation_bp)
app.register_blueprint(services_bp)
app.register_blueprint(cache_bp)
app.register_blueprint(url_handlers_bp)
app.register_blueprint(queue_scheduler_bp)
app.register_blueprint(semantic_bp)
app.register_blueprint(version_bp)
app.register_blueprint(artifact_health_bp)
app.register_blueprint(mission_params_bp)
app.register_blueprint(pool_manager_bp)
print("[Dashboard] pool_manager_bp registered successfully at /api/pool/*")

# SSE transport for agent activity streams (replaces flask-socketio rooms
# mission_agents / investigation_agents).
try:
    from dashboard_modules.agent_sse import agent_sse_bp
    app.register_blueprint(agent_sse_bp)
    print("[Dashboard] agent_sse_bp registered (SSE on /api/agents/*)")
except Exception as _agent_sse_err:
    print(f"[Dashboard] agent_sse_bp registration failed: {_agent_sse_err}")

# Conductor status and control API (enhanced singleton with takeover support)
try:
    # Add ConductorTakeover to path so its internal imports resolve
    _conductor_path = str(Path(__file__).parent / "workspace" / "ConductorTakeover")
    if _conductor_path not in sys.path:
        sys.path.insert(0, _conductor_path)
    from workspace.ConductorTakeover.conductor_dashboard_api import conductor_bp
    app.register_blueprint(conductor_bp)
    print("[Conductor] API endpoints registered (/api/conductor/*)")
except ImportError as e:
    print(f"[Conductor] API not available: {e}")

# Register non-prefixed routes
register_archival_routes(app)

# =============================================================================
# REAL-TIME TOKEN WATCHER INTEGRATION
# =============================================================================

def start_realtime_token_watcher():
    """
    Start real-time token watching for the active mission.

    This provides live cost visibility in the dashboard during mission execution.
    """
    try:
        from realtime_token_watcher import get_token_watcher

        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get('mission_id')
        workspace = mission.get('mission_workspace')
        stage = mission.get('current_stage', 'unknown')

        # Only start if there's an active mission that hasn't completed
        if mission_id and stage not in ('COMPLETE', None, ''):
            watcher = get_token_watcher()
            success = watcher.start(
                mission_id=mission_id,
                workspace_path=workspace,
                socketio=socketio,
                stage=stage
            )
            if success:
                print(f"[TokenWatcher] Started real-time monitoring for {mission_id}")
            else:
                print(f"[TokenWatcher] Could not start for {mission_id} (no transcript dir)")
        else:
            print("[TokenWatcher] No active mission to monitor")
    except ImportError as e:
        print(f"[TokenWatcher] Module not available: {e}")
    except Exception as e:
        print(f"[TokenWatcher] Failed to start: {e}")

# Start watcher on dashboard load (deferred to avoid import issues)
threading.Thread(target=start_realtime_token_watcher, daemon=True).start()

# =============================================================================
# QUEUE AUTO-START WATCHER
# =============================================================================

QUEUE_AUTO_START_SIGNAL_PATH = STATE_DIR / "queue_auto_start_signal.json"

def queue_auto_start_watcher():
    """
    Watch for queue auto-start signals AND idle-state auto-start.

    Two triggers:
    1. Signal file exists (from /api/queue/next or atlasforge_engine)
    2. AtlasForge is idle + auto_start enabled + queue has ready items (idle-state check)

    This file is written by atlasforge_engine.py when a queued mission is ready.
    When detected, this watcher:
    1. Reads the signal file
    2. Waits for old process to terminate (grace period)
    3. Starts Claude in R&D mode if not already running
    4. Deletes the signal file ONLY on success
    """
    print("[QueueWatcher] Started watching for queue auto-start signals")
    idle_check_counter = 0
    IDLE_CHECK_INTERVAL = 6  # Check idle state every 30 seconds (6 * 5s)
    SIGNAL_STALE_SECONDS = 120  # Signals older than 2 minutes are stale
    MAX_RETRIES = 5  # Maximum retry attempts before giving up
    PROCESS_GRACE_PERIOD = 3  # Seconds to wait for old process to terminate

    while True:
        try:
            time.sleep(5)  # Check every 5 seconds

            # === Signal file detection (fixed logic) ===
            if QUEUE_AUTO_START_SIGNAL_PATH.exists():
                # Check queue processing lock before proceeding
                try:
                    from queue_processing_lock import is_queue_locked, get_queue_lock_info
                    if is_queue_locked():
                        lock_info = get_queue_lock_info()
                        print(f"[QueueWatcher] Queue locked by {lock_info.get('locked_by')}, waiting...")
                        continue
                except ImportError:
                    pass  # Lock module not available, proceed

                # Read the signal file
                signal_data = io_utils.atomic_read_json(QUEUE_AUTO_START_SIGNAL_PATH, {})
                if signal_data and signal_data.get("action") == "start_rd":
                    mission_id = signal_data.get("mission_id", "unknown")
                    mission_title = signal_data.get("mission_title", "Queued Mission")
                    retry_count = signal_data.get("retry_count", 0)
                    signaled_at = signal_data.get("signaled_at", "")
                    raw_signal_provider = signal_data.get("llm_provider")
                    signal_provider = _normalize_provider(raw_signal_provider) if raw_signal_provider else get_llm_provider()
                    if signal_provider not in ("claude", "codex", "gemini"):
                        signal_provider = get_llm_provider()

                    print(f"[QueueWatcher] Queue auto-start signal detected for {mission_id} (retry {retry_count})")

                    # Check if signal is stale (older than SIGNAL_STALE_SECONDS)
                    if signaled_at:
                        try:
                            signal_time = datetime.fromisoformat(signaled_at)
                            signal_age = (datetime.now() - signal_time).total_seconds()
                            if signal_age > SIGNAL_STALE_SECONDS:
                                print(f"[QueueWatcher] Signal is stale ({signal_age:.0f}s old), deleting")
                                try:
                                    QUEUE_AUTO_START_SIGNAL_PATH.unlink()
                                except FileNotFoundError:
                                    pass
                                continue
                        except (ValueError, TypeError):
                            pass  # Can't parse timestamp, proceed anyway

                    # Check if max retries exceeded
                    if retry_count >= MAX_RETRIES:
                        print(f"[QueueWatcher] Max retries ({MAX_RETRIES}) exceeded for {mission_id}, giving up")
                        try:
                            QUEUE_AUTO_START_SIGNAL_PATH.unlink()
                        except FileNotFoundError:
                            pass
                        continue

                    # Check if Claude is already running - with grace period
                    if find_process_cached("atlasforge_conductor.py"):
                        # Wait for grace period to allow old process to terminate
                        print(f"[QueueWatcher] Claude detected running, waiting {PROCESS_GRACE_PERIOD}s grace period...")
                        time.sleep(PROCESS_GRACE_PERIOD)

                        # Check again after grace period
                        if find_process_cached("atlasforge_conductor.py"):
                            print(f"[QueueWatcher] Claude still running after grace period, incrementing retry count")
                            # Increment retry count and save back (don't delete signal)
                            signal_data["retry_count"] = retry_count + 1
                            io_utils.atomic_write_json(QUEUE_AUTO_START_SIGNAL_PATH, signal_data)
                            continue

                    # Start the queued mission with the provider captured at queue time.
                    set_llm_provider(signal_provider)
                    print(f"[QueueWatcher] Starting {signal_provider} in RD mode for: {mission_title}")
                    success, msg = start_claude(mode="rd")

                    if success:
                        print(f"[QueueWatcher] Successfully started queued mission: {mission_id}")
                        # Delete signal file ONLY after successful start
                        try:
                            QUEUE_AUTO_START_SIGNAL_PATH.unlink()
                        except FileNotFoundError:
                            pass
                        # Broadcast to clients via socketio
                        try:
                            socketio.emit('queue_mission_started', {
                                'mission_id': mission_id,
                                'mission_title': mission_title,
                                'message': f"Started queued mission: {mission_title}"
                            })
                        except Exception as e:
                            logger.debug("queue_mission_started emit failed: %s", e)
                        # Emit auto-start notification for browser notifications
                        try:
                            from websocket_events import emit_mission_auto_started
                            emit_mission_auto_started(
                                mission_id=mission_id,
                                mission_title=mission_title,
                                source=signal_data.get("source", "queue_auto")
                            )
                        except ImportError:
                            pass
                    else:
                        print(f"[QueueWatcher] Failed to start queued mission: {msg}")
                        # Keep signal file for retry, increment retry count
                        signal_data["retry_count"] = retry_count + 1
                        signal_data["last_error"] = msg
                        io_utils.atomic_write_json(QUEUE_AUTO_START_SIGNAL_PATH, signal_data)
                    continue

            # === Idle-state auto-start (NEW) ===
            idle_check_counter += 1
            if idle_check_counter >= IDLE_CHECK_INTERVAL:
                idle_check_counter = 0

                # Check queue processing lock before proceeding
                try:
                    from queue_processing_lock import is_queue_locked
                    if is_queue_locked():
                        continue  # Queue is being processed elsewhere
                except ImportError:
                    pass  # Lock module not available, proceed

                # Check if Claude is already running
                if find_process_cached("atlasforge_conductor.py"):
                    continue  # Already running

                # Check mission state
                mission = io_utils.atomic_read_json(MISSION_PATH, {})
                current_stage = mission.get("current_stage", "")
                # Only auto-start if mission is complete or no mission exists
                if current_stage and current_stage not in ("COMPLETE", ""):
                    continue  # Mission in progress

                # Check queue settings
                queue_data = io_utils.atomic_read_json(MISSION_QUEUE_PATH, {})
                settings = queue_data.get("settings", {})
                auto_start = settings.get("auto_start", False)
                if not auto_start:
                    auto_start = queue_data.get("enabled", False)  # Fallback

                if not auto_start:
                    continue  # Auto-start disabled

                paused = queue_data.get("paused", False) or settings.get("paused", False)
                if paused:
                    continue  # Queue paused

                missions = queue_data.get("missions", []) or queue_data.get("queue", [])
                if not missions:
                    continue  # Queue empty

                # All conditions met - trigger auto-start
                next_mission = missions[0]
                problem_stmt = next_mission.get("problem_statement", "Queued Mission")
                mission_title = (problem_stmt[:80] + '...') if len(problem_stmt) > 80 else problem_stmt

                print(f"[QueueWatcher] Idle-state auto-start: Conditions met, starting next mission: {mission_title}")

                # Call queue/next endpoint to properly pop and create mission
                try:
                    import requests
                    _port = os.environ.get('ATLASFORGE_PORT', os.environ.get('PORT', '5010'))
                    resp = requests.post(f"http://localhost:{_port}/api/queue/next", timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        started_id = data.get('mission_id', 'unknown')
                        print(f"[QueueWatcher] Idle auto-start triggered: {started_id}")
                        # Emit auto-start notification for browser notifications
                        try:
                            from websocket_events import emit_mission_auto_started
                            emit_mission_auto_started(
                                mission_id=started_id,
                                mission_title=mission_title,
                                source="idle_auto_start"
                            )
                        except ImportError:
                            pass
                    else:
                        print(f"[QueueWatcher] Idle auto-start failed: {resp.text}")
                except Exception as e:
                    print(f"[QueueWatcher] Idle auto-start request failed: {e}")

        except Exception as e:
            print(f"[QueueWatcher] Error: {e}")
            time.sleep(10)  # Wait longer on error

# Start queue watcher thread
threading.Thread(target=queue_auto_start_watcher, daemon=True).start()

# =============================================================================
# MAIN ROUTE
# =============================================================================

# Pre-rendered HTML cache to avoid 5.7s render_template_string() on every request
_rendered_html_cache = {
    'html': None,
    'js_version': None,
    'css_version': None,
}

def get_rendered_index():
    """Get cached rendered HTML; re-render only when bundle version changes."""
    versions = get_bundle_version()
    js_v = versions.get('js')
    css_v = versions.get('css')
    if (_rendered_html_cache['html'] is not None and
            _rendered_html_cache['js_version'] == js_v and
            _rendered_html_cache['css_version'] == css_v):
        return _rendered_html_cache['html']
    # Render and cache
    template = HTML_BUNDLED_TEMPLATE if app.config['USE_BUNDLED'] else HTML_TEMPLATE
    html = render_template_string(template, bundle_js_version=js_v, bundle_css_version=css_v)
    _rendered_html_cache['html'] = html
    _rendered_html_cache['js_version'] = js_v
    _rendered_html_cache['css_version'] = css_v
    return html

@app.route('/')
def index():
    return get_rendered_index()


@app.route('/favicon.ico')
def favicon():
    """Serve favicon from static directory."""
    return send_file(STATIC_DIR / 'favicon.ico', mimetype='image/x-icon')


@app.route('/static/dist/<path:filename>')
def serve_dist_gz(filename):
    """Serve pre-compressed .gz files for dist assets when client accepts gzip."""
    dist_dir = STATIC_DIR / 'dist'
    dist_dir_resolved = dist_dir.resolve()
    try:
        gz_path = (dist_dir / (filename + '.gz')).resolve()
        plain_path = (dist_dir / filename).resolve()
    except Exception:
        abort(404)
    # Path traversal guard: BOTH resolved paths must stay within dist_dir.
    # Using AND (not OR) because each path is served independently — if gz_path
    # escapes dist/ but plain_path is inside, a gzip-accepting client could still
    # receive the unsafe gz file.
    dist_prefix = str(dist_dir_resolved) + os.sep
    if not (str(gz_path).startswith(dist_prefix) and str(plain_path).startswith(dist_prefix)):
        abort(404)

    if gz_path.exists() and 'gzip' in request.accept_encodings:
        mime = mimetypes.guess_type(filename)[0] or 'application/octet-stream'
        resp = send_file(gz_path, mimetype=mime)
        resp.headers['Content-Encoding'] = 'gzip'
        resp.headers['Vary'] = 'Accept-Encoding'
        return resp

    if plain_path.exists():
        return send_file(plain_path)

    abort(404)


# =============================================================================
# SERVER-SIDE OPTIMIZATIONS (Gzip + Cache Headers)
# =============================================================================

@app.after_request
def add_security_headers(response):
    """Add Content-Security-Policy and other security headers to mitigate XSS."""
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none';"
    )
    return response


@app.after_request
def add_server_push_headers(response):
    """Add Link headers for HTTP/2 server push."""
    if request.path == '/' and response.content_type and 'text/html' in response.content_type:
        push_links = [
            '</static/dist/bundle.min.css>; rel=preload; as=style; fetchpriority=high',
            '</static/dist/bundle.min.js>; rel=preload; as=script; fetchpriority=high',
        ]
        existing = response.headers.get('Link', '')
        new_links = ', '.join(push_links)
        response.headers['Link'] = f'{existing}, {new_links}' if existing else new_links
    return response

@app.after_request
def add_cache_headers(response):
    """Add appropriate cache headers based on asset type."""
    path = request.path

    # Hash-named chunks - cache forever (immutable)
    if '/static/dist/chunks/' in path:
        response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    # Entry points and manifest - always revalidate
    elif path.endswith('/bundle.min.js') or path.endswith('/bundle.min.css') or path.endswith('/manifest.json'):
        response.headers['Cache-Control'] = 'public, max-age=0, must-revalidate'
    # Other static assets (CSS, JS) - moderate cache
    elif path.startswith('/static/'):
        response.headers['Cache-Control'] = 'public, max-age=86400'

    return response


@app.after_request
def compress_response(response):
    """Apply gzip compression to eligible responses."""
    # Skip if already compressed or client doesn't accept gzip
    if (response.direct_passthrough or
        'gzip' not in request.accept_encodings or
        response.status_code < 200 or
        response.status_code >= 300 or
        'Content-Encoding' in response.headers):
        return response

    # Check content type
    content_type = response.content_type or ''
    compressible_types = app.config.get('COMPRESS_MIMETYPES', [])
    if not any(ct in content_type for ct in compressible_types):
        return response

    # Check minimum size
    min_size = app.config.get('COMPRESS_MIN_SIZE', 500)
    if response.content_length is not None and response.content_length < min_size:
        return response

    # Get response data
    try:
        data = response.get_data()
        if len(data) < min_size:
            return response
    except Exception as e:
        logger.debug("compress_response: get_data failed: %s", e)
        return response

    # Compress
    try:
        buffer = BytesIO()
        with gzip.GzipFile(mode='wb', fileobj=buffer, compresslevel=6) as gz:
            gz.write(data)
        compressed = buffer.getvalue()

        # Only use compressed version if it's actually smaller
        if len(compressed) < len(data):
            response.set_data(compressed)
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Content-Length'] = len(compressed)
            response.headers['Vary'] = 'Accept-Encoding'
    except Exception as e:
        logger.debug("Response compression failed, keeping original: %s", e)

    return response


# =============================================================================
# CHAT HISTORY API (for polling fallback)
# =============================================================================

@app.route('/api/chat-history')
def api_chat_history():
    """Get chat history for polling fallback when WebSocket is unavailable."""
    history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
    fallback_provider = get_llm_provider()
    messages = [
        _serialize_chat_message(msg, fallback_provider)
        for msg in history[-30:]
    ]
    return jsonify({'messages': messages})


# =============================================================================
# SOCKET EVENTS
# =============================================================================

@socketio.on('connect')
def handle_connect():
    global seen_messages
    # Chat history is loaded via REST /api/chat-history (loadInitialChatHistory)
    # Only populate seen_messages set here to avoid duplicate detection issues
    # Clear before re-seeding to prevent unbounded growth on reconnect
    with _seen_messages_lock:
        seen_messages.clear()
        history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
        for msg in history[-30:]:
            role = str(msg.get('role', '')).strip().lower()
            if role in ('claude', 'codex', 'gemini', 'system', 'suggestion'):
                msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
                seen_messages.append(msg_id)


@socketio.on('send_message')
def handle_send_message(data):
    if not isinstance(data, dict):
        return
    content = data.get('content', '')
    if content:
        def update_history(history):
            if not isinstance(history, list):
                history = []
            history.append({
                'role': 'human',
                'content': content,
                'timestamp': datetime.now().isoformat()
            })
            if len(history) > 500:
                history = history[-500:]
            return history

        io_utils.atomic_update_json(CHAT_HISTORY_PATH, update_history, [])
        send_message_to_claude(content)


# =============================================================================
# WIDGET REAL-TIME UPDATES (WebSocket Push System)
# =============================================================================

_widget_state = {}
_widget_state_ts: dict = {}  # key -> float timestamp of last write (for TTL pruning)
_widget_state_lock = threading.Lock()
_client_subscriptions = {}  # Track which rooms each client subscribes to

@socketio.on('connect', namespace='/widgets')
def handle_widget_connect():
    """Handle widget namespace connection."""
    global _ws_state_cache
    from flask import request as flask_request
    client_id = flask_request.sid
    with _widget_state_lock:
        _ws_state_cache['connected_clients'] += 1
        _client_subscriptions[client_id] = set()

    emit('connected', {
        'status': 'ok',
        'client_id': client_id,
        'available_rooms': VALID_WS_ROOMS,
        'timestamp': datetime.now().isoformat()
    })


@socketio.on('disconnect', namespace='/widgets')
def handle_widget_disconnect():
    """Handle widget namespace disconnection."""
    global _ws_state_cache
    from flask import request as flask_request
    client_id = flask_request.sid
    with _widget_state_lock:
        _ws_state_cache['connected_clients'] = max(0, _ws_state_cache['connected_clients'] - 1)
        if client_id in _client_subscriptions:
            del _client_subscriptions[client_id]


@socketio.on('subscribe', namespace='/widgets')
def handle_widget_subscribe(data):
    """Subscribe to specific widget updates."""
    if not isinstance(data, dict):
        return
    from flask import request as flask_request
    client_id = flask_request.sid
    room = data.get('room')

    if room in VALID_WS_ROOMS:
        join_room(room)
        with _widget_state_lock:
            if client_id in _client_subscriptions:
                _client_subscriptions[client_id].add(room)

        # Send initial data immediately after subscribing
        initial_data = get_initial_room_data(room)
        emit('subscribed', {
            'room': room,
            'timestamp': datetime.now().isoformat(),
            'initial_data': initial_data
        })
    else:
        emit('error', {
            'message': 'Invalid room specified',
            'valid_rooms': VALID_WS_ROOMS
        })


@socketio.on('unsubscribe', namespace='/widgets')
def handle_widget_unsubscribe(data):
    """Unsubscribe from widget updates."""
    from flask import request as flask_request
    client_id = flask_request.sid
    room = data.get('room')

    if room not in VALID_WS_ROOMS:
        emit('error', {
            'message': 'Invalid room specified',
            'valid_rooms': VALID_WS_ROOMS
        })
        return

    leave_room(room)
    with _widget_state_lock:
        if client_id in _client_subscriptions:
            _client_subscriptions[client_id].discard(room)
    emit('unsubscribed', {'room': room})


@socketio.on('ping', namespace='/widgets')
def handle_widget_ping():
    """Handle ping for connection health monitoring."""
    emit('pong', {'timestamp': datetime.now().isoformat()})


@socketio.on('subscribe_all', namespace='/widgets')
def handle_subscribe_all():
    """Subscribe to all available rooms at once."""
    from flask import request as flask_request
    client_id = flask_request.sid

    for room in VALID_WS_ROOMS:
        join_room(room)
    with _widget_state_lock:
        if client_id in _client_subscriptions:
            _client_subscriptions[client_id].update(VALID_WS_ROOMS)

    emit('subscribed_all', {
        'rooms': VALID_WS_ROOMS,
        'timestamp': datetime.now().isoformat()
    })


def get_initial_room_data(room: str) -> dict:
    """Get initial data to send when client subscribes to a room.

    Uses TTL cache where available to avoid redundant expensive calls
    (pgrep, rglob, etc.) when multiple clients connect simultaneously.
    """
    try:
        if room == 'mission_status':
            # Check TTL cache first — /api/status may have already computed this
            try:
                from dashboard_modules.cache import get_dashboard_cache
                cached = get_dashboard_cache().get('api_status')
                if cached is not None:
                    return cached
            except Exception as e:
                logger.debug("Cache operation failed: %s", e)
            from dashboard_modules.mission_status_schema import build_mission_status
            result = build_mission_status(get_claude_status())
            try:
                from dashboard_modules.cache import get_dashboard_cache
                get_dashboard_cache().set('api_status', result, ttl_seconds=0.75)
            except Exception as e:
                logger.debug("Cache operation failed: %s", e)
            return result
        elif room == 'journal':
            return {'entries': get_recent_journal(15)}
        elif room == 'atlasforge_stats':
            return get_atlasforge_exploration_stats()
        elif room == 'analytics':
            return get_analytics_summary()
        elif room == 'glassbox':
            return get_glassbox_summary()
        elif room == 'exploration':
            return get_exploration_data()
        elif room == 'backup_status':
            return get_backup_status_data()
        elif room == 'file_events':
            try:
                from dashboard_modules.cache import get_dashboard_cache
                cached = get_dashboard_cache().get('file_events')
                if cached is not None:
                    return cached
            except Exception as e:
                logger.debug("file_events cache get failed: %s", e)
            result = get_recent_file_events()
            try:
                from dashboard_modules.cache import get_dashboard_cache
                get_dashboard_cache().set('file_events', result, ttl_seconds=5.0)
            except Exception as e:
                logger.debug("file_events cache set failed: %s", e)
            return result
        elif room == 'glassbox_archive':
            return get_glassbox_archive_status()
        elif room == 'recommendations':
            return get_recommendations_summary()
        elif room == 'subprocess_gate':
            return get_subprocess_gate_status()
        elif room == 'mission_params':
            return get_mission_params()
    except Exception as e:
        logger.error(f'get_initial_room_data error for room {room}: {e}')
        return {'error': 'Internal error'}
    return {}


def get_atlasforge_exploration_stats() -> dict:
    """Get AtlasForge exploration stats for WebSocket push."""
    try:
        from exploration_hooks import get_af_dashboard_data
        return get_af_dashboard_data()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"get_atlasforge_exploration_stats failed: {e}")
    return {}


def get_analytics_summary() -> dict:
    """Get analytics data for WebSocket push."""
    try:
        from mission_analytics import get_current_mission_analytics
        return get_current_mission_analytics()
    except ImportError:
        return {}


def get_glassbox_summary() -> dict:
    """Get GlassBox summary data for WebSocket push."""
    try:
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get('mission_id')
        if mission_id:
            from glassbox.mission_archiver import load_mission_archive
            archive = load_mission_archive(mission_id)
            if archive:
                return {
                    'mission_id': mission_id,
                    'agent_count': archive.get_agent_count(),
                    'total_events': archive.get_total_events()
                }
    except ImportError:
        pass
    return {}


def get_exploration_data() -> dict:
    """Get exploration graph data for WebSocket push."""
    try:
        from exploration_hooks import get_visualization_data
        return get_visualization_data()
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"get_exploration_data failed: {e}")
    return {}


def get_backup_status_data() -> dict:
    """Get backup status data for WebSocket push."""
    try:
        from mission_snapshot_manager import get_backup_status_data as _get_backup_status
        return _get_backup_status()
    except ImportError:
        return {'error': 'Snapshot module not available'}
    except Exception as e:
        logger.error(f'get_backup_status_data error: {e}')
        return {'error': 'Internal error'}


def get_recent_file_events() -> dict:
    """Get recent file events for the current mission."""
    try:
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_workspace = mission.get('mission_workspace')
        if not mission_workspace:
            return {'files': [], 'mission_id': None}

        workspace_path = Path(mission_workspace)
        if not workspace_path.exists():
            return {'files': [], 'mission_id': mission.get('mission_id')}

        # Get recently modified files in the workspace
        recent_files = []
        for f in workspace_path.rglob('*'):
            if f.is_file() and not f.name.startswith('.'):
                try:
                    stat = f.stat()
                    recent_files.append({
                        'name': f.name,
                        'path': str(f.relative_to(workspace_path)),
                        'modified': stat.st_mtime,
                        'size': stat.st_size
                    })
                except OSError:
                    pass

        # Sort by modification time, most recent first
        recent_files.sort(key=lambda x: x['modified'], reverse=True)

        return {
            'files': recent_files[:20],
            'mission_id': mission.get('mission_id'),
            'workspace': str(mission_workspace)
        }
    except Exception as e:
        logger.error(f'get_recent_file_events error: {e}')
        return {'error': 'Internal error', 'files': []}


def get_glassbox_archive_status() -> dict:
    """Get GlassBox archive status for current mission."""
    try:
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get('mission_id')
        if not mission_id:
            return {'archived': False, 'mission_id': None}

        # Check if archive exists
        archive_dir = BASE_DIR / 'artifacts' / 'transcripts' / mission_id
        if archive_dir.exists():
            manifest_path = archive_dir / 'manifest.json'
            if manifest_path.exists():
                manifest = io_utils.atomic_read_json(manifest_path, {})
                return {
                    'archived': True,
                    'mission_id': mission_id,
                    'transcript_count': manifest.get('transcript_count', 0),
                    'archive_path': str(archive_dir),
                    'archived_at': manifest.get('archived_at')
                }

        return {
            'archived': False,
            'mission_id': mission_id
        }
    except Exception as e:
        logger.error(f'get_glassbox_archive_status error: {e}')
        return {'error': 'Internal error', 'archived': False}


def get_recommendations_summary() -> dict:
    """Get recommendations summary for WebSocket push."""
    try:
        try:
            from suggestion_storage import get_storage
            storage = get_storage()
            items = storage.get_all()
        except Exception as e:
            logger.debug("get_recommendations_summary: SQLite fallback to JSON: %s", e)
            recommendations_data = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
            items = recommendations_data.get("items", [])
        return {
            'count': len(items),
            'recent': items[-5:] if items else [],
            'has_new': len(items) > 0
        }
    except Exception as e:
        logger.error(f'get_recommendations_summary error: {e}')
        return {'error': 'Internal error', 'count': 0, 'recent': []}


def get_subprocess_gate_status() -> dict:
    """Get Intelligent Subprocess Gate status for WebSocket push."""
    try:
        import sys as _sys
        _gate_path = os.path.join(os.path.dirname(__file__), 'workspace', 'project_bbf2ba08')
        if os.path.isdir(_gate_path) and _gate_path not in _sys.path:
            _sys.path.insert(0, _gate_path)
        from orchestration.intelligent_gate import IntelligentSubprocessGate
        gate = IntelligentSubprocessGate(
            monitored_pid=os.getpid(),
            workspace_path=str(WORKSPACE_DIR),
        )
        decision = gate.should_defer_handoff()
        status = gate.get_status_summary()
        return {
            'available': True,
            'should_defer': decision.should_defer,
            'process_type': decision.process_type.value if decision.process_type else None,
            'confidence': decision.confidence,
            'timeout_seconds': decision.timeout_seconds,
            'is_stalled': decision.is_stalled,
            'reason': decision.reason,
            'active_children': status.get('active_children', 0),
            'stalled_pids': status.get('stalled_pids', []),
            'timestamp': datetime.now().isoformat(),
        }
    except ImportError:
        return {'available': False, 'timestamp': datetime.now().isoformat()}
    except Exception as e:
        logger.error(f'get_subprocess_gate_status error: {e}')
        return {'available': False, 'error': 'Internal error', 'timestamp': datetime.now().isoformat()}


def emit_widget_update(room: str, data: dict):
    """Emit update to specific widget room."""
    socketio.emit('update', {
        'room': room,
        'data': data,
        'timestamp': datetime.now().isoformat()
    }, room=room, namespace='/widgets')


def broadcast_state_change(event_type: str, data: dict):
    """Broadcast a state change to all relevant rooms.

    This is the main entry point for pushing updates.
    Call this from anywhere in the codebase when state changes.
    """
    timestamp = datetime.now().isoformat()

    # Mission-status events route through the canonical schema helper
    _mission_status_events = {
        'mission_stage_change', 'mission_iteration_change',
        'mission_started', 'mission_stopped',
    }

    if event_type in _mission_status_events:
        try:
            from dashboard_modules.mission_status_schema import emit_mission_status
            # Merge incoming data with full status to ensure complete payload
            status = get_claude_status()
            status.update(data)
            emit_mission_status(status, event_type=event_type)
        except Exception as e:
            logger.warning("Failed to emit mission status: %s", e)
        return

    # Non-mission-status events use the original path
    room_mapping = {
        'journal_entry': 'journal',
        'atlasforge_exploration': 'atlasforge_stats',
        'atlasforge_drift_alert': 'atlasforge_stats',
        'analytics_update': 'analytics',
        'glassbox_event': 'glassbox',
        'exploration_update': 'exploration',
    }

    room = room_mapping.get(event_type)
    if room:
        socketio.emit('state_change', {
            'event': event_type,
            'room': room,
            'data': data,
            'timestamp': timestamp
        }, room=room, namespace='/widgets')


def check_and_emit_widget_updates():
    """Check for widget data changes and emit updates.

    This function is called periodically to detect state changes
    and push updates to subscribed clients.
    """
    global _widget_state

    # Track timing for rate limiting
    now = time.time()
    with _widget_state_lock:
        if now - _ws_state_cache.get('last_check', 0) < 1.5:  # Rate limit to ~0.67Hz (reduced from 2Hz for lower I/O load)
            return
        _ws_state_cache['last_check'] = now

    # Prune stale _widget_state on mission transition to prevent unbounded accumulation
    try:
        current_status = get_claude_status()
        current_mission_id = current_status.get('mission_id')
        with _widget_state_lock:
            if current_mission_id and _widget_state.get('_tracked_mission_id') != current_mission_id:
                _widget_state.clear()
                _widget_state_ts.clear()
                _widget_state['_tracked_mission_id'] = current_mission_id
    except Exception as e:
        logger.debug("Widget state prune failed: %s", e)

    # TTL pruning: belt-and-suspenders for long missions — remove keys with timestamps >600s old
    try:
        TTL = 600
        with _widget_state_lock:
            stale_keys = [k for k, ts in list(_widget_state_ts.items())
                          if now - ts > TTL and k != '_tracked_mission_id']
            for k in stale_keys:
                _widget_state.pop(k, None)
                _widget_state_ts.pop(k, None)
    except Exception as e:
        logger.debug("TTL widget state prune failed: %s", e)

    # Mission status check — routed through canonical schema
    try:
        from dashboard_modules.mission_status_schema import emit_mission_status
        current_status = get_claude_status()
        new_stage = current_status.get('rd_stage') or None
        if not new_stage:
            new_stage = 'N/A'
        # Use '||' as delimiter — safe against any valid stage name (PLANNING,
        # BUILDING, TESTING, ANALYZING, CYCLE_END, COMPLETE) which never contain '||'.
        status_key = f"{new_stage}||{current_status.get('running')}||{current_status.get('rd_iteration')}"
        with _widget_state_lock:
            _status_changed = _widget_state.get('mission_status_key') != status_key
            prev_key = _widget_state.get('mission_status_key') or ''
            if _status_changed:
                _widget_state['mission_status_key'] = status_key
                _widget_state_ts['mission_status_key'] = now
        if _status_changed:
            prev_stage = prev_key.split('||')[0] if prev_key else ''
            if prev_stage and prev_stage != 'N/A' and prev_stage != new_stage:
                # Stage transition — set event_type and old_stage so the JS
                # toast handler (showToast) fires for this status update.
                emit_mission_status(current_status, event_type='stage_change', old_stage=prev_stage)
            else:
                # Running/iteration changed only — plain status update, no toast
                emit_mission_status(current_status)
    except Exception as e:
        logger.debug("Mission status widget update failed: %s", e)

    # Journal check
    try:
        journal = get_recent_journal(15)
        journal_key = f"{len(journal)}:{journal[0]['timestamp'] if journal else ''}"
        with _widget_state_lock:
            _journal_changed = _widget_state.get('journal_key') != journal_key
            if _journal_changed:
                _widget_state['journal_key'] = journal_key
                _widget_state_ts['journal_key'] = now
        if _journal_changed:
            emit_widget_update('journal', {'entries': journal})
    except Exception as e:
        logger.debug("Journal widget update failed: %s", e)

    # AtlasForge stats check (less frequent - every 10 seconds)
    try:
        with _widget_state_lock:
            _af_due = now - _widget_state.get('atlasforge_last_check', 0) > 10
            if _af_due:
                _widget_state['atlasforge_last_check'] = now
                _widget_state_ts['atlasforge_last_check'] = now
        if _af_due:
            atlasforge_data = get_atlasforge_exploration_stats()
            atlasforge_key = str(atlasforge_data.get('exploration', {}).get('total_insights', 0))
            with _widget_state_lock:
                _af_changed = _widget_state.get('atlasforge_key') != atlasforge_key
                if _af_changed:
                    _widget_state['atlasforge_key'] = atlasforge_key
                    _widget_state_ts['atlasforge_key'] = now
            if _af_changed:
                emit_widget_update('atlasforge_stats', atlasforge_data)
    except Exception as e:
        logger.debug("AtlasForge stats widget update failed: %s", e)

    # Recommendations check - detect new mission recommendations
    # Uses SQLite storage (primary) with JSON fallback for consistency with af_engine
    try:
        items = []
        try:
            from suggestion_storage import get_storage
            storage = get_storage()
            items = storage.get_all()
        except Exception as e:
            logger.debug("Recommendations SQLite read failed, falling back to JSON: %s", e)
            recommendations_data = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
            items = recommendations_data.get("items", [])

        rec_count = len(items)
        latest_rec_id = items[0].get("id") if items else None  # SQLite returns sorted by priority
        rec_key = f"{rec_count}:{latest_rec_id}"

        with _widget_state_lock:
            _rec_changed = _widget_state.get('recommendations_key') != rec_key and rec_count > 0
            _prev_rec_key = _widget_state.get('recommendations_key', '0:')
            if _rec_changed:
                _widget_state['recommendations_key'] = rec_key
                _widget_state_ts['recommendations_key'] = now
        if _rec_changed:
            # New recommendation detected
            prev_count = int(_prev_rec_key.split(':')[0]) if _prev_rec_key else 0
            if rec_count > prev_count and items:
                # There's a new recommendation - emit notification
                # Find most recently created item (not highest priority)
                latest = max(items, key=lambda x: x.get('created_at', ''))
                emit_widget_update('recommendations', {
                    'event': 'new_recommendation',
                    'recommendation': {
                        'id': latest.get('id'),
                        'title': latest.get('mission_title', 'New Mission'),
                        'description': (latest.get('mission_description', '') or '')[:200],
                        'source_mission': latest.get('source_mission_id'),
                        'source_type': latest.get('source_type', 'successful_completion')
                    },
                    'total_count': rec_count
                })
    except Exception as e:
        logger.debug("Recommendations widget update failed: %s", e)

    # Subprocess gate check (every 5 seconds)
    try:
        with _widget_state_lock:
            _gate_due = now - _widget_state.get('gate_last_check', 0) > 5
            if _gate_due:
                _widget_state['gate_last_check'] = now
                _widget_state_ts['gate_last_check'] = now
        if _gate_due:
            gate_data = get_subprocess_gate_status()
            gate_key = f"{gate_data.get('should_defer')}:{gate_data.get('process_type')}:{gate_data.get('is_stalled')}"
            with _widget_state_lock:
                _gate_changed = _widget_state.get('gate_key') != gate_key
                if _gate_changed:
                    _widget_state['gate_key'] = gate_key
                    _widget_state_ts['gate_key'] = now
            if _gate_changed:
                emit_widget_update('subprocess_gate', gate_data)
    except Exception as e:
        logger.debug("Subprocess gate widget update failed: %s", e)

    # Mission params check (every 30 seconds — parameters rarely change mid-mission)
    try:
        with _widget_state_lock:
            _params_due = now - _widget_state.get('mission_params_last_check', 0) > 30
            if _params_due:
                _widget_state['mission_params_last_check'] = now
                _widget_state_ts['mission_params_last_check'] = now
        if _params_due:
            params_data = get_mission_params()
            _p = params_data.get('parameters', {})
            params_key = f"{params_data.get('mission_id')}:{_p.get('current_cycle')}:{_p.get('current_stage')}:{_p.get('cycle_budget')}:{_p.get('max_iterations')}"
            with _widget_state_lock:
                _params_changed = _widget_state.get('mission_params_key') != params_key
                if _params_changed:
                    _widget_state['mission_params_key'] = params_key
                    _widget_state_ts['mission_params_key'] = now
            if _params_changed:
                emit_widget_update('mission_params', params_data)
    except Exception as e:
        logger.debug("Mission params widget update failed: %s", e)


# =============================================================================
# WEBSOCKET CONNECTION STATUS API
# =============================================================================

@app.route('/api/ws/status')
def api_ws_status():
    """Get WebSocket connection status and statistics."""
    with _widget_state_lock:
        subs_snapshot = {k: list(v) for k, v in _client_subscriptions.items()}
        clients = _ws_state_cache.get('connected_clients', 0)
        last_chk = _ws_state_cache.get('last_check', 0)
    return jsonify({
        'connected_clients': clients,
        'available_rooms': VALID_WS_ROOMS,
        'client_subscriptions': subs_snapshot,
        'last_check': last_chk,
        'timestamp': datetime.now().isoformat()
    })


# =============================================================================
# CONTEXT WATCHER API
# =============================================================================

@app.route('/api/context-watcher/stats')
def api_context_watcher_stats():
    """Get ContextWatcher metrics and active session info.

    Returns JSON with:
    - enabled: Whether ContextWatcher is enabled
    - running: Whether it's actively monitoring
    - using_watchdog: Whether using inotify or polling
    - active_sessions: Count of monitored sessions
    - total_handoffs: Total handoff events triggered
    - graceful_handoffs: Count of graceful (130K) handoffs
    - emergency_handoffs: Count of emergency (140K) handoffs
    - sessions: Details of each active session
    - thresholds: Current token thresholds
    - metrics: Detailed timing and token metrics
    """
    try:
        from workspace.ContextWatcher.context_watcher import get_context_watcher
        watcher = get_context_watcher()
        stats = watcher.get_all_stats()
        stats['timestamp'] = datetime.now().isoformat()
        return jsonify(stats)
    except ImportError:
        return jsonify({
            'error': 'ContextWatcher module not available',
            'enabled': False,
            'running': False,
            'timestamp': datetime.now().isoformat()
        }), 503
    except Exception as e:
        logger.error(f'api_context_watcher_stats error: {e}')
        return jsonify({
            'error': 'Internal error',
            'enabled': False,
            'running': False,
            'timestamp': datetime.now().isoformat()
        }), 500


# =============================================================================
# SUBPROCESS GATE API
# =============================================================================

@app.route('/api/subprocess-gate/status')
def api_subprocess_gate_status():
    """Get Intelligent Subprocess Gate status."""
    return jsonify(get_subprocess_gate_status())


# =============================================================================
# WEB PROXY STATS API (WEB_PROXY_INVESTIGATION_01)
# Exposes the local AtlasForge web proxy's /stats counters so the dashboard
# can show investigation-pipeline activity (cached searches/fetches, provider
# breakdown). Graceful when the proxy is down.
# =============================================================================

@app.route('/api/web-proxy/stats')
def api_web_proxy_stats():
    """Return live counters from the AtlasForge local web proxy.

    Response shape on success:
        {"status": "ok", "cached_fetches": N, "cached_searches": N,
         "cached_images": N, "cached_image_searches": N,
         "providers": {...}, "cache_dir": "..."}

    On proxy unavailability:
        {"status": "error", "error": "<reason>"}
    """
    try:
        from WebProxy import get_proxy_stats_safe
        return jsonify(get_proxy_stats_safe())
    except Exception as e:
        return jsonify({"status": "error", "error": f"handler: {e}"}), 500


# =============================================================================
# RESTART STATS API (Cycle 3)
# Aggregates restart statistics from journal for Activity Log visibility
# =============================================================================

@app.route('/api/restart-stats')
def api_restart_stats():
    """Get aggregated restart statistics from the journal.

    Returns JSON with:
    - graceful_restarts: Count of graceful handoffs (context, time-based)
    - error_restarts: Count of retriable error restarts
    - blocking_errors: Count of blocking errors that halted the mission
    - breakdown: Detailed counts by restart reason
    - recent_errors: Last 5 error entries with details

    This helps users understand at-a-glance:
    - How many restarts were expected (graceful) vs problematic (errors)
    - What types of errors occurred
    - Whether the mission is healthy
    """
    try:
        stats = {
            'graceful_restarts': 0,
            'error_restarts': 0,
            'blocking_errors': 0,
            'breakdown': {
                'context_exhaustion': 0,
                'time_based_handoff': 0,
                'context_overflow': 0,
                'cli_timeout': 0,
                'api_error_500': 0,
                'tool_call_bug': 0,
                'rate_limited': 0,
                'auth_failed': 0,
                'network_error': 0,
                'overloaded': 0,
                'unknown': 0
            },
            'recent_errors': [],
            'mission_id': None,
            'timestamp': datetime.now().isoformat()
        }

        # Get current mission ID
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        stats['mission_id'] = mission.get('mission_id')

        # Parse journal for restart/error entries
        if CLAUDE_JOURNAL_PATH.exists():
            with open(CLAUDE_JOURNAL_PATH, 'r') as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        entry_type = entry.get('type', '')

                        # Graceful handoffs - don't count as errors
                        if entry_type == 'graceful_handoff_restart':
                            stats['graceful_restarts'] += 1
                            reason = entry.get('restart_reason', 'unknown')
                            if reason in stats['breakdown']:
                                stats['breakdown'][reason] += 1

                        # Timeout failures (retriable errors that hit max retries)
                        elif entry_type == 'claude_timeout_failure':
                            stats['error_restarts'] += 1
                            reason = entry.get('error_category', 'unknown')
                            if reason in stats['breakdown']:
                                stats['breakdown'][reason] += 1
                            # Add to recent errors
                            stats['recent_errors'].append({
                                'timestamp': entry.get('timestamp'),
                                'stage': entry.get('stage'),
                                'category': reason,
                                'explanation': entry.get('error_explanation', ''),
                                'retries': entry.get('retries', 0)
                            })

                        # Blocking errors (immediate halt)
                        elif entry_type == 'claude_blocking_error':
                            stats['blocking_errors'] += 1
                            reason = entry.get('error_category', 'unknown')
                            if reason in stats['breakdown']:
                                stats['breakdown'][reason] += 1
                            # Add to recent errors
                            stats['recent_errors'].append({
                                'timestamp': entry.get('timestamp'),
                                'stage': entry.get('stage'),
                                'category': reason,
                                'explanation': entry.get('error_explanation', ''),
                                'blocking': True
                            })

                    except json.JSONDecodeError:
                        continue

        # Keep only last 5 recent errors
        stats['recent_errors'] = stats['recent_errors'][-5:]

        # Calculate health score
        total_events = stats['graceful_restarts'] + stats['error_restarts'] + stats['blocking_errors']
        if total_events > 0:
            # Health: graceful restarts are fine, errors reduce health
            graceful_pct = stats['graceful_restarts'] / total_events
            stats['health_score'] = round(graceful_pct * 100, 1)
        else:
            stats['health_score'] = 100.0  # No events = healthy

        return jsonify(stats)

    except Exception as e:
        logger.error(f'api_restart_stats error: {e}')
        return jsonify({
            'error': 'Internal error',
            'graceful_restarts': 0,
            'error_restarts': 0,
            'blocking_errors': 0,
            'breakdown': {},
            'timestamp': datetime.now().isoformat()
        }), 500


# =============================================================================
# BACKGROUND WATCHER
# =============================================================================

def watch_chat():
    """Watch for new messages from Claude."""
    global seen_messages

    try:
        history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
        with _seen_messages_lock:
            for msg in history:
                role = str(msg.get('role', '')).strip().lower()
                if role in ('claude', 'codex', 'gemini', 'system', 'suggestion'):
                    msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
                    seen_messages.append(msg_id)
    except Exception as e:
        logger.warning("watch_chat: failed to seed seen_messages from history: %s", e)

    while True:
        try:
            history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
            fallback_provider = get_llm_provider()

            for msg in history[-30:]:
                role = str(msg.get('role', '')).strip().lower()
                if role in ('claude', 'codex', 'gemini', 'system', 'suggestion'):
                    msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
                    with _seen_messages_lock:
                        if msg_id not in seen_messages:
                            seen_messages.append(msg_id)
                            socketio.emit('message', _serialize_chat_message(msg, fallback_provider))

            check_and_emit_widget_updates()

        except Exception as e:
            print(f"Watch error: {e}")

        time.sleep(2)


def watch_engine_stage():
    """Watch af_engine stage changes and push live updates to mission_status WS room."""
    _last_stage = None
    _last_mission_id = None

    while True:
        try:
            time.sleep(2)
            from af_engine import StateManager, STAGES
            from dashboard_modules.mission_status_schema import emit_mission_status
            sm = StateManager(MISSION_PATH)
            current_stage = sm.current_stage
            current_mission = sm.mission_id

            if current_stage != _last_stage or current_mission != _last_mission_id:
                old_stage = _last_stage
                _last_stage = current_stage
                _last_mission_id = current_mission
                # Build full status from get_claude_status() + engine-specific fields
                status = get_claude_status()
                status['stages'] = STAGES
                status['cycles_remaining'] = max(0, sm.cycle_budget - sm.cycle_number)
                status['is_last_cycle'] = sm.cycle_number >= sm.cycle_budget
                emit_mission_status(status, event_type='engine_stage_change', old_stage=old_stage)
        except Exception as e:
            logger.debug("watch_engine_stage: iteration failed: %s", e)


# =============================================================================
# MAIN
# =============================================================================


@app.route('/api/agent-stream/history')
def get_agent_stream_history():
    """Return list of past agent stream manifests from persistent store."""
    try:
        from agent_stream_manager import get_stream_history
        mission_id = request.args.get('mission_id')
        limit = int(request.args.get('limit', 20))
        limit = min(max(limit, 1), 100)
        manifests = get_stream_history(mission_id=mission_id, limit=limit)
        return jsonify({'success': True, 'agents': manifests, 'count': len(manifests)})
    except Exception as e:
        logger.error(f"API error in get_agent_stream_history: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred'})


@app.route('/api/agent-stream/<agent_id>')
def get_agent_stream(agent_id):
    """Return accumulated stream lines for an agent (for reconnect replay)."""
    try:
        from agent_stream_manager import get_agent_stream_lines
        lines = get_agent_stream_lines(agent_id, limit=200)
        return jsonify({'success': True, 'lines': lines})
    except Exception as e:
        logger.error(f"API error in get_agent_stream: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred'})


@app.route('/api/active-agents')
def get_active_agents_endpoint():
    """Return currently active (running) agents grouped by context.
    Used by the frontend reconciliation check to close ghost tabs."""
    try:
        from agent_stream_manager import get_active_agents
        agents = get_active_agents()
        return jsonify({'success': True, 'agents': agents})
    except Exception as e:
        logger.error(f"API error in get_active_agents: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred', 'agents': {'mission': [], 'investigation': []}})


@app.route('/api/internal/reload-agent-stream', methods=['POST'])
def reload_agent_stream_module():
    """Force-reload agent_stream_manager and set dashboard mode. Called after code updates."""
    try:
        import importlib
        import sys
        if 'agent_stream_manager' in sys.modules:
            importlib.reload(sys.modules['agent_stream_manager'])
        from agent_stream_manager import set_dashboard_mode
        set_dashboard_mode(True)
        return jsonify({'success': True, 'message': 'agent_stream_manager reloaded and set to dashboard mode'})
    except Exception as e:
        logger.error(f"API error in reload_agent_stream_module: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred'})


@app.route('/api/internal/reload-template', methods=['POST'])
def reload_html_template():
    """Hot-reload the HTML template from disk without restarting the server."""
    global HTML_BUNDLED_TEMPLATE, HTML_TEMPLATE
    try:
        HTML_BUNDLED_TEMPLATE = load_template("main_bundled")
        HTML_TEMPLATE = load_template("main")
        # Invalidate the pre-rendered HTML cache so next request re-renders from new template
        _rendered_html_cache['html'] = None
        return jsonify({'success': True, 'message': 'Templates reloaded from disk'})
    except Exception as e:
        logger.error(f"API error in reload_html_template: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred'})


@app.route('/api/agent-stream/test-emit', methods=['POST'])
def test_agent_emit():
    """
    Cycle 3 verification endpoint: fire a mock agent lifecycle sequence
    from within the dashboard process so SocketIO events reach real browsers.
    POST body: {"context": "mission"|"investigation", "label": "..."}
    """
    try:
        import uuid, time, threading
        from agent_stream_manager import (
            register_agent, complete_agent, STREAM_DIR,
            _next_seq_for_writer, _wrap_with_seq,
        )
        data = request.get_json(silent=True) or {}
        context = data.get('context', 'mission')
        if context not in ('mission', 'investigation'):
            context = 'mission'
        label = data.get('label', 'Test-Agent')
        if not isinstance(label, str):
            label = 'Test-Agent'
        # Strip non-printable chars and truncate
        label = re.sub(r'[^\x20-\x7E]', '', label)[:100]
        agent_id = f"test_{uuid.uuid4().hex[:8]}"

        stream_file = register_agent(context, agent_id, label=label)

        def _write_and_complete():
            events = [
                '{"type":"assistant","message":{"content":[{"type":"text","text":"Running test analysis..."}]}}',
                '{"type":"assistant","message":{"content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/tmp/test.py"}}]}}',
                '{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"t1","content":[{"type":"text","text":"Test result OK."}]}]}}',
                '{"type":"assistant","message":{"content":[{"type":"text","text":"Test complete."}]}}',
            ]
            with open(str(stream_file), 'a') as f:
                for evt in events:
                    seq = _next_seq_for_writer(agent_id)
                    if seq > 0:
                        f.write(_wrap_with_seq(evt, seq))
                    else:
                        f.write(evt + '\n')
                    f.flush()
                    time.sleep(0.4)
            time.sleep(0.5)
            complete_agent(agent_id)

        t = threading.Thread(target=_write_and_complete, daemon=True)
        t.start()
        return jsonify({'success': True, 'agent_id': agent_id, 'context': context, 'label': label})
    except Exception as e:
        logger.error(f"API error in test_agent_emit: {e}")
        return jsonify({'success': False, 'error': 'An internal error occurred'})


if __name__ == '__main__':
    # Start background watchers
    threading.Thread(target=watch_chat, daemon=True).start()
    threading.Thread(target=watch_engine_stage, daemon=True).start()

    # Auto-start local web proxy alongside the dashboard.
    # Subagents spawned later (conductor/investigation/blind-agent) rely on it
    # for WebSearch/WebFetch. Opt-out: ATLASFORGE_DISABLE_PROXY_AUTOSTART=1
    # (useful when you prefer the systemd unit).
    try:
        from WebProxy.supervisor import ensure_proxy_running
        _proxy_result = ensure_proxy_running()
        print(f"[WebProxy] {_proxy_result['status']}: {_proxy_result['detail']}")
    except Exception as _proxy_err:
        print(f"[WebProxy] Auto-start failed (non-fatal): {_proxy_err}")

    # Auto-start AfterImage Embedder Daemon
    # This indexes code for episodic memory retrieval
    try:
        import subprocess
        import psutil

        # Check if embedder is already running
        embedder_running = False
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                cmdline = proc.info.get('cmdline') or []
                if any('afterimage_embedder' in str(arg) for arg in cmdline):
                    embedder_running = True
                    print(f"[AfterImage] Embedder daemon already running (PID {proc.pid})")
                    break
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if not embedder_running:
            # Launch the embedder daemon
            embedder_path = Path("/home/vader/Shared/AI-AfterImage")
            if embedder_path.exists():
                env = os.environ.copy()
                env['PYTHONPATH'] = str(embedder_path) + ':' + env.get('PYTHONPATH', '')
                env['EMBEDDER_WEB_DASHBOARD_ENABLED'] = 'true'
                env['EMBEDDER_WEB_DASHBOARD_HOST'] = '0.0.0.0'
                subprocess.Popen(
                    [sys.executable, '-m', 'afterimage_embedder'],
                    cwd=str(embedder_path),
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                print("[AfterImage] Started embedder daemon (dashboard on :8080)")
            else:
                print("[AfterImage] Embedder path not found, skipping")
    except ImportError:
        print("[AfterImage] psutil not available, skipping embedder check")
    except Exception as e:
        print(f"[AfterImage] Failed to start embedder: {e}")

    # Start snapshot scheduler for hourly backups during active missions
    try:
        from mission_snapshot_manager import (
            get_snapshot_scheduler,
            get_stale_backup_monitor,
            check_recovery_needed
        )

        # Check if mission recovery is needed
        recovery_info = check_recovery_needed()
        if recovery_info:
            print(f"[Recovery] Crashed mission detected: {recovery_info['mission_id']}")
            print(f"[Recovery] Stage: {recovery_info['current_stage']}, Iteration: {recovery_info['iteration']}")
            if recovery_info.get('latest_snapshot'):
                print(f"[Recovery] Latest snapshot: {recovery_info['latest_snapshot']['snapshot_id']}")

        # Start snapshot scheduler
        scheduler = get_snapshot_scheduler()
        scheduler.start()
        print("[SnapshotScheduler] Started hourly backup scheduler")

        # Start stale backup monitor with socketio for alerts
        monitor = get_stale_backup_monitor()
        monitor.set_socketio(socketio)
        monitor.start()
        print("[StaleBackupMonitor] Started backup health monitor")
    except ImportError as e:
        print(f"[Snapshot] Module not available: {e}")
    except Exception as e:
        print(f"[Snapshot] Failed to start scheduler: {e}")

    # Verify af_engine is healthy at startup
    try:
        import sys as _sys
        _af_root = str(BASE_DIR)
        if _af_root not in _sys.path:
            _sys.path.insert(0, _af_root)
        from af_engine import StateManager as _StartupSM, STAGES as _StartupSTAGES
        _startup_sm = _StartupSM(MISSION_PATH)
        _startup_stage = _startup_sm.current_stage
        print(f"[af_engine] Health OK — stage={_startup_stage}, stages={len(_StartupSTAGES)}")
    except ImportError as e:
        print(f"[af_engine] WARNING: Import failed: {e}")
    except Exception as e:
        print(f"[af_engine] WARNING: Health check failed: {e}")

    print("=" * 50)
    print("AI-AtlasForge Dashboard")
    print("         [MODULAR ARCHITECTURE]")
    print("=" * 50)
    print(f"Templates: {TEMPLATES_DIR}")
    print(f"Modules: dashboard_modules/")
    print("=" * 50)
    try:
        PORT = int(os.environ.get('PORT', 5010))
    except (ValueError, TypeError):
        PORT = 5010

    # Get SSL context if available
    ssl_ctx = get_ssl_context()
    protocol = "https" if ssl_ctx else "http"

    if ssl_ctx:
        print(f"SSL: Enabled (certificates in {CERTS_DIR})")
    else:
        ssl_reason = "disabled via DASHBOARD_SSL=false" if os.environ.get('DASHBOARD_SSL', 'true').lower() == 'false' else "certificates not found"
        print(f"SSL: Disabled ({ssl_reason})")

    print(f"Access at: {protocol}://localhost:{PORT}")
    print("=" * 50)

    # Pre-warm the template cache so the first request doesn't pay 5.7s penalty
    with app.app_context():
        _warm_start = time.time()
        get_rendered_index()
        _warm_ms = (time.time() - _warm_start) * 1000
        print(f"[Startup] Template cache warmed in {_warm_ms:.0f}ms")

    # Pre-warm agent stream cache (avoids 30-40s cold-start disk reads on first client reconnect)
    try:
        from agent_stream_manager import prewarm_active_agents as _prewarm
        _prewarm_start = time.time()
        _prewarm_n = _prewarm()
        _prewarm_ms = (time.time() - _prewarm_start) * 1000
        print(f"[AgentStream] Pre-warm complete: {_prewarm_n} agents cached in {_prewarm_ms:.0f}ms")
    except Exception as e:
        print(f"[AgentStream] Pre-warm failed (non-critical): {e}")

    socketio.run(app, host='::', port=PORT, ssl_context=ssl_ctx, allow_unsafe_werkzeug=True, use_reloader=False)
