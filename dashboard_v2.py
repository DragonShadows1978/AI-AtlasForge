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

import io_utils

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
TIMELINE_PAGE_HTML = load_template("timeline")

# =============================================================================
# FLASK APP SETUP
# =============================================================================

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path='/static')
app.config['SECRET_KEY'] = 'atlasforge-secret'

# Template toggle: use bundled template by default in production
# Set FLASK_USE_BUNDLED=false to use legacy template
app.config['USE_BUNDLED'] = os.environ.get('FLASK_USE_BUNDLED', 'true').lower() == 'true'

# Compression configuration
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'text/javascript',
    'application/javascript', 'application/json'
]
app.config['COMPRESS_MIN_SIZE'] = 500

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Track app start time for health checks
app._start_time = time.time()

# Track seen messages for deduplication
seen_messages = set()

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
]

# Register websocket_events module with socketio reference
try:
    from websocket_events import set_socketio
    set_socketio(socketio)
except ImportError:
    pass

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def find_process(script_name: str) -> dict | None:
    """Find a running process by script name."""
    try:
        result = subprocess.run(
            ["pgrep", "-af", script_name],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split('\n'):
            if line and script_name in line and 'grep' not in line and 'pgrep' not in line:
                parts = line.split(None, 1)
                if parts:
                    return {"pid": int(parts[0]), "cmd": parts[1] if len(parts) > 1 else ""}
    except:
        pass
    return None


def get_claude_status() -> dict:
    """Get Claude autonomous status."""
    proc = find_process("atlasforge_conductor.py")
    state = io_utils.atomic_read_json(CLAUDE_STATE_PATH, {})
    mission = io_utils.atomic_read_json(MISSION_PATH, {})

    full_mission = mission.get("problem_statement", "No mission set")
    return {
        "running": proc is not None,
        "pid": proc["pid"] if proc else None,
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
                except:
                    pass
        except:
            pass
    return entries


def start_claude(mode: str = "rd") -> tuple[bool, str]:
    """Start Claude autonomous."""
    if find_process("atlasforge_conductor.py"):
        return False, "Already running"

    script_path = BASE_DIR / "atlasforge_conductor.py"
    if not script_path.exists():
        return False, "Script not found"

    try:
        log_file = LOG_DIR / "atlasforge_conductor.log"
        subprocess.Popen(
            ["python3", str(script_path), f"--mode={mode}"],
            cwd=str(BASE_DIR),
            stdout=open(log_file, 'a'),
            stderr=subprocess.STDOUT,
            start_new_session=True
        )
        time.sleep(2)

        if find_process("atlasforge_conductor.py"):
            return True, f"Started in {mode} mode"
        return False, "Failed to start"
    except Exception as e:
        return False, str(e)


def stop_claude() -> tuple[bool, str]:
    """Stop Claude autonomous."""
    proc = find_process("atlasforge_conductor.py")
    if not proc:
        return False, "Not running"

    try:
        os.kill(proc["pid"], signal.SIGTERM)
        time.sleep(2)

        if find_process("atlasforge_conductor.py"):
            os.kill(proc["pid"], signal.SIGKILL)
            time.sleep(1)

        return True, "Stopped"
    except ProcessLookupError:
        return True, "Already stopped"
    except Exception as e:
        return False, str(e)


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
except Exception:
    pass
init_semantic_blueprint(mission_workspace=current_mission_workspace, socketio=socketio, io_utils=io_utils)
init_version_blueprint(BASE_DIR)
init_bundle_version(STATIC_DIR, BASE_DIR)

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
                    if find_process("atlasforge_conductor.py"):
                        # Wait for grace period to allow old process to terminate
                        print(f"[QueueWatcher] Claude detected running, waiting {PROCESS_GRACE_PERIOD}s grace period...")
                        time.sleep(PROCESS_GRACE_PERIOD)

                        # Check again after grace period
                        if find_process("atlasforge_conductor.py"):
                            print(f"[QueueWatcher] Claude still running after grace period, incrementing retry count")
                            # Increment retry count and save back (don't delete signal)
                            signal_data["retry_count"] = retry_count + 1
                            io_utils.atomic_write_json(QUEUE_AUTO_START_SIGNAL_PATH, signal_data)
                            continue

                    # Start Claude in RD mode
                    print(f"[QueueWatcher] Starting Claude in RD mode for: {mission_title}")
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
                        except:
                            pass
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
                if find_process("atlasforge_conductor.py"):
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
                    resp = requests.post("http://localhost:5000/api/queue/next", timeout=10)
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

@app.route('/')
def index():
    # Select template based on configuration
    # Use bundled template in production (USE_BUNDLED=true)
    # Use legacy template in development (USE_BUNDLED=false)
    template_name = "main_bundled" if app.config['USE_BUNDLED'] else "main"
    # Get bundle version for cache-busting
    bundle_version = get_bundle_version()
    return render_template_string(
        load_template(template_name),
        bundle_js_version=bundle_version['js'],
        bundle_css_version=bundle_version['css']
    )


@app.route('/favicon.ico')
def favicon():
    """Serve favicon from static directory."""
    return send_file(STATIC_DIR / 'favicon.ico', mimetype='image/x-icon')


# =============================================================================
# SERVER-SIDE OPTIMIZATIONS (Gzip + Cache Headers)
# =============================================================================

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
    except Exception:
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
    except Exception:
        pass  # Keep original if compression fails

    return response


# =============================================================================
# CHAT HISTORY API (for polling fallback)
# =============================================================================

@app.route('/api/chat-history')
def api_chat_history():
    """Get chat history for polling fallback when WebSocket is unavailable."""
    history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
    return jsonify({'messages': history[-30:]})


# =============================================================================
# SOCKET EVENTS
# =============================================================================

@socketio.on('connect')
def handle_connect():
    global seen_messages
    history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
    for msg in history[-30:]:
        if msg.get('role') == 'claude':
            msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
            seen_messages.add(msg_id)
        emit('message', {'role': msg.get('role'), 'content': msg.get('content'), 'timestamp': msg.get('timestamp')})


@socketio.on('send_message')
def handle_send_message(data):
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
_client_subscriptions = {}  # Track which rooms each client subscribes to

@socketio.on('connect', namespace='/widgets')
def handle_widget_connect():
    """Handle widget namespace connection."""
    global _ws_state_cache
    from flask import request as flask_request
    client_id = flask_request.sid
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
    _ws_state_cache['connected_clients'] = max(0, _ws_state_cache['connected_clients'] - 1)
    if client_id in _client_subscriptions:
        del _client_subscriptions[client_id]


@socketio.on('subscribe', namespace='/widgets')
def handle_widget_subscribe(data):
    """Subscribe to specific widget updates."""
    from flask import request as flask_request
    client_id = flask_request.sid
    room = data.get('room')

    if room in VALID_WS_ROOMS:
        join_room(room)
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
            'message': f'Invalid room: {room}',
            'valid_rooms': VALID_WS_ROOMS
        })


@socketio.on('unsubscribe', namespace='/widgets')
def handle_widget_unsubscribe(data):
    """Unsubscribe from widget updates."""
    from flask import request as flask_request
    client_id = flask_request.sid
    room = data.get('room')

    leave_room(room)
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
        if client_id in _client_subscriptions:
            _client_subscriptions[client_id].add(room)

    emit('subscribed_all', {
        'rooms': VALID_WS_ROOMS,
        'timestamp': datetime.now().isoformat()
    })


def get_initial_room_data(room: str) -> dict:
    """Get initial data to send when client subscribes to a room."""
    try:
        if room == 'mission_status':
            return get_claude_status()
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
            return get_recent_file_events()
        elif room == 'glassbox_archive':
            return get_glassbox_archive_status()
        elif room == 'recommendations':
            return get_recommendations_summary()
    except Exception as e:
        return {'error': str(e)}
    return {}


def get_atlasforge_exploration_stats() -> dict:
    """Get AtlasForge exploration stats for WebSocket push."""
    try:
        from atlasforge_enhancements.exploration_graph import get_exploration_graph
        graph = get_exploration_graph()
        if graph:
            return {
                'exploration': {
                    'nodes_by_type': graph.get_node_counts_by_type(),
                    'total_insights': graph.get_insight_count(),
                    'total_edges': graph.get_edge_count()
                },
                'coverage_pct': graph.get_coverage_percentage(),
                'drift_history': graph.get_drift_history(),
                'recent_explorations': graph.get_recent_explorations(8)
            }
    except ImportError:
        pass
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
            from glassbox.archive_loader import load_mission_archive
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
        from atlasforge_enhancements.exploration_graph import get_exploration_graph
        graph = get_exploration_graph()
        if graph:
            return {
                'nodes': graph.get_recent_nodes(20),
                'edges': graph.get_recent_edges(30),
                'insights': graph.get_recent_insights(10)
            }
    except ImportError:
        pass
    return {}


def get_backup_status_data() -> dict:
    """Get backup status data for WebSocket push."""
    try:
        from mission_snapshot_manager import get_backup_status_data as _get_backup_status
        return _get_backup_status()
    except ImportError:
        return {'error': 'Snapshot module not available'}
    except Exception as e:
        return {'error': str(e)}


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
        return {'error': str(e), 'files': []}


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
        return {'error': str(e), 'archived': False}


def get_recommendations_summary() -> dict:
    """Get recommendations summary for WebSocket push."""
    try:
        recommendations_data = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
        items = recommendations_data.get("items", [])
        return {
            'count': len(items),
            'recent': items[-5:] if items else [],
            'has_new': len(items) > 0
        }
    except Exception as e:
        return {'error': str(e), 'count': 0, 'recent': []}


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

    # Determine which room(s) to notify based on event type
    room_mapping = {
        'mission_stage_change': 'mission_status',
        'mission_iteration_change': 'mission_status',
        'mission_started': 'mission_status',
        'mission_stopped': 'mission_status',
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
    if now - _ws_state_cache.get('last_check', 0) < 0.5:  # Rate limit to 2Hz
        return
    _ws_state_cache['last_check'] = now

    # Mission status check
    try:
        current_status = get_claude_status()
        status_key = f"{current_status.get('rd_stage')}:{current_status.get('running')}:{current_status.get('rd_iteration')}"
        if _widget_state.get('mission_status_key') != status_key:
            _widget_state['mission_status_key'] = status_key
            emit_widget_update('mission_status', current_status)
    except Exception:
        pass

    # Journal check
    try:
        journal = get_recent_journal(15)
        journal_key = f"{len(journal)}:{journal[0]['timestamp'] if journal else ''}"
        if _widget_state.get('journal_key') != journal_key:
            _widget_state['journal_key'] = journal_key
            emit_widget_update('journal', {'entries': journal})
    except Exception:
        pass

    # AtlasForge stats check (less frequent - every 10 seconds)
    try:
        if now - _widget_state.get('atlasforge_last_check', 0) > 10:
            _widget_state['atlasforge_last_check'] = now
            atlasforge_data = get_atlasforge_exploration_stats()
            atlasforge_key = str(atlasforge_data.get('exploration', {}).get('total_insights', 0))
            if _widget_state.get('atlasforge_key') != atlasforge_key:
                _widget_state['atlasforge_key'] = atlasforge_key
                emit_widget_update('atlasforge_stats', atlasforge_data)
    except Exception:
        pass

    # Recommendations check - detect new mission recommendations
    # Uses SQLite storage (primary) with JSON fallback for consistency with af_engine
    try:
        items = []
        try:
            from suggestion_storage import get_storage
            storage = get_storage()
            items = storage.get_all()
        except Exception:
            # Fallback to JSON if SQLite fails
            recommendations_data = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
            items = recommendations_data.get("items", [])

        rec_count = len(items)
        latest_rec_id = items[0].get("id") if items else None  # SQLite returns sorted by priority
        rec_key = f"{rec_count}:{latest_rec_id}"

        if _widget_state.get('recommendations_key') != rec_key and rec_count > 0:
            # New recommendation detected
            prev_count = int(_widget_state.get('recommendations_key', '0:').split(':')[0]) if _widget_state.get('recommendations_key') else 0
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
            _widget_state['recommendations_key'] = rec_key
    except Exception:
        pass


# =============================================================================
# WEBSOCKET CONNECTION STATUS API
# =============================================================================

@app.route('/api/ws/status')
def api_ws_status():
    """Get WebSocket connection status and statistics."""
    return jsonify({
        'connected_clients': _ws_state_cache.get('connected_clients', 0),
        'available_rooms': VALID_WS_ROOMS,
        'client_subscriptions': {k: list(v) for k, v in _client_subscriptions.items()},
        'last_check': _ws_state_cache.get('last_check', 0),
        'timestamp': datetime.now().isoformat()
    })


# =============================================================================
# BACKGROUND WATCHER
# =============================================================================

def watch_chat():
    """Watch for new messages from Claude."""
    global seen_messages

    try:
        history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
        for msg in history:
            if msg.get('role') == 'claude':
                msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
                seen_messages.add(msg_id)
    except:
        pass

    while True:
        try:
            history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])

            for msg in history[-10:]:
                if msg.get('role') == 'claude':
                    msg_id = f"{msg.get('timestamp')}:{msg.get('content', '')[:50]}"
                    if msg_id not in seen_messages:
                        seen_messages.add(msg_id)
                        socketio.emit('message', {
                            'role': 'claude',
                            'content': msg.get('content', ''),
                            'timestamp': msg.get('timestamp')
                        })

            if len(seen_messages) > 500:
                seen_messages = set(list(seen_messages)[-250:])

            check_and_emit_widget_updates()

        except Exception as e:
            print(f"Watch error: {e}")

        time.sleep(2)


# =============================================================================
# MAIN
# =============================================================================

if __name__ == '__main__':
    # Start background watcher
    threading.Thread(target=watch_chat, daemon=True).start()

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

    print("=" * 50)
    print("AI-AtlasForge Dashboard")
    print("         [MODULAR ARCHITECTURE]")
    print("=" * 50)
    print(f"Templates: {TEMPLATES_DIR}")
    print(f"Modules: dashboard_modules/")
    print("=" * 50)
    PORT = int(os.environ.get('PORT', 5010))

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

    socketio.run(app, host='::', port=PORT, ssl_context=ssl_ctx, allow_unsafe_werkzeug=True)
