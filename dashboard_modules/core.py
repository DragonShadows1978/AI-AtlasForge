"""
Core Dashboard Routes Blueprint

Contains essential routes for:
- Status (Claude status, health check)
- Start/Stop controls
- Journal entries
- Mission management
- Proposals
- Recommendations
- Mission logs
- File downloads

These routes depend on functions from the main dashboard_v2.py module.
"""

from flask import Blueprint, jsonify, request, abort, send_file
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import logging
import mimetypes
import re

logger = logging.getLogger(__name__)

# TTL cache for hot endpoints
def _get_ttl_cache():
    try:
        from dashboard_modules.cache import get_dashboard_cache
        return get_dashboard_cache()
    except Exception as e:
        logger.debug("TTL cache unavailable: %s", e)
        return None

# Create Blueprint
core_bp = Blueprint('core', __name__)

# Import shared allowed modes — prevents drift between core_bp and dashboard_v2
try:
    from dashboard_v2 import _ALLOWED_MODES
except ImportError:
    _ALLOWED_MODES = {"rd", "free"}

# Valid source_type values for recommendation filtering (must match DB CHECK constraint in suggestion_storage.py)
_VALID_SOURCE_TYPES = {'drift_halt', 'successful_completion', 'manual', 'merged'}

# Constants - will be set by init function
BASE_DIR = None
STATE_DIR = None
WORKSPACE_DIR = None
MISSION_PATH = None
PROPOSALS_PATH = None
RECOMMENDATIONS_PATH = None
MISSION_LOGS_DIR = None

# SQLite storage backend for suggestions
_suggestion_storage = None

def _get_suggestion_storage():
    """Get the SQLite suggestion storage backend (lazy import)."""
    global _suggestion_storage
    if _suggestion_storage is None:
        try:
            from suggestion_storage import get_storage
            _suggestion_storage = get_storage()
        except ImportError:
            _suggestion_storage = None
    return _suggestion_storage

# Function references - will be set by init function
io_utils = None
get_claude_status = None
start_claude = None
stop_claude = None
send_message_to_claude = None
get_recent_journal = None
get_llm_provider = None
set_llm_provider = None

# Narrative-specific functions
get_narrative_status = None
start_narrative = None
stop_narrative = None
send_message_to_narrative = None
get_narrative_chat_history = None
NARRATIVE_MISSION_PATH = None

# Mission queue
MISSION_QUEUE_PATH = None


def _normalize_runtime_provider(provider):
    """Normalize provider values that can actually run the conductor."""
    normalized = str(provider or "").strip().lower()
    if normalized in {"claude", "codex", "gemini"}:
        return normalized
    return None


def _update_active_mission_provider(provider):
    """Keep mission metadata aligned with the provider used for start/resume."""
    if not provider or not io_utils or not MISSION_PATH:
        return
    try:
        mission = io_utils.atomic_read_json(MISSION_PATH, {}) or {}
        if not isinstance(mission, dict) or not mission:
            return
        mission["llm_provider"] = provider
        mission["last_updated"] = datetime.now(timezone.utc).isoformat()
        io_utils.atomic_write_json(MISSION_PATH, mission)
    except Exception:
        logger.exception("Failed to update active mission provider")


def init_core_blueprint(
    base_dir, state_dir, workspace_dir,
    mission_path, proposals_path, recommendations_path,
    io_utils_module,
    status_fn, start_fn, stop_fn, send_msg_fn, journal_fn,
    get_provider_fn=None, set_provider_fn=None,
    narrative_status_fn=None, narrative_start_fn=None, narrative_stop_fn=None,
    narrative_send_msg_fn=None, narrative_chat_fn=None, narrative_mission_path=None,
    mission_queue_path=None
):
    """Initialize the core blueprint with required dependencies."""
    global BASE_DIR, STATE_DIR, WORKSPACE_DIR, MISSION_PATH, PROPOSALS_PATH, RECOMMENDATIONS_PATH
    global MISSION_LOGS_DIR, io_utils, MISSION_QUEUE_PATH
    global get_claude_status, start_claude, stop_claude, send_message_to_claude, get_recent_journal
    global get_llm_provider, set_llm_provider
    global get_narrative_status, start_narrative, stop_narrative, send_message_to_narrative
    global get_narrative_chat_history, NARRATIVE_MISSION_PATH

    BASE_DIR = base_dir
    STATE_DIR = state_dir
    WORKSPACE_DIR = workspace_dir
    MISSION_PATH = mission_path
    PROPOSALS_PATH = proposals_path
    RECOMMENDATIONS_PATH = recommendations_path
    MISSION_LOGS_DIR = base_dir / "missions" / "mission_logs"
    io_utils = io_utils_module

    get_claude_status = status_fn
    start_claude = start_fn
    stop_claude = stop_fn
    send_message_to_claude = send_msg_fn
    get_recent_journal = journal_fn
    get_llm_provider = get_provider_fn
    set_llm_provider = set_provider_fn

    # Narrative functions (optional)
    get_narrative_status = narrative_status_fn
    start_narrative = narrative_start_fn
    stop_narrative = narrative_stop_fn
    send_message_to_narrative = narrative_send_msg_fn
    get_narrative_chat_history = narrative_chat_fn
    NARRATIVE_MISSION_PATH = narrative_mission_path

    # Mission queue (optional)
    MISSION_QUEUE_PATH = mission_queue_path


# =============================================================================
# STATUS ROUTES
# =============================================================================

@core_bp.route('/api/status')
def api_status():
    cache = _get_ttl_cache()
    if cache:
        cached = cache.get('api_status')
        if cached is not None:
            return jsonify(cached)
    # Route through canonical schema so REST and WebSocket payloads match
    from dashboard_modules.mission_status_schema import build_mission_status
    result = build_mission_status(get_claude_status())
    if cache:
        cache.set('api_status', result, ttl_seconds=0.75)
    return jsonify(result)


@core_bp.route('/api/health')
def api_health():
    """Health check endpoint for verifying dashboard connectivity."""
    import time
    start = time.time()

    services = {
        "mission_file": False,
        "state_dir": False,
        "signal_file_writable": False
    }

    # Check mission file accessibility
    try:
        if MISSION_PATH.exists():
            with open(MISSION_PATH, 'r') as f:
                json.load(f)
            services["mission_file"] = True
        else:
            services["mission_file"] = True
    except Exception as e:
        logger.debug("Health check: mission_file failed: %s", e)
        services["mission_file"] = False

    # Check state directory is writable
    try:
        test_file = STATE_DIR / ".health_check"
        test_file.write_text("ok")
        test_file.unlink()
        services["state_dir"] = True
    except Exception as e:
        logger.debug("Health check: state_dir not writable: %s", e)
        services["state_dir"] = False

    # Check signal file can be written
    try:
        signal_path = STATE_DIR / "auto_advance_signal.json"
        if signal_path.exists():
            with open(signal_path, 'r') as f:
                json.load(f)
            services["signal_file_writable"] = True
        else:
            test_signal = STATE_DIR / ".signal_test.json"
            test_signal.write_text('{"test": true}')
            test_signal.unlink()
            services["signal_file_writable"] = True
    except Exception as e:
        logger.debug("Health check: signal_file not writable: %s", e)
        services["signal_file_writable"] = False

    # Check af_engine module health
    try:
        import sys as _sys
        _af_root = str(Path(__file__).parent.parent)
        if _af_root not in _sys.path:
            _sys.path.insert(0, _af_root)
        from af_engine import StateManager as _SM
        _sm_check = _SM(MISSION_PATH)
        _ = _sm_check.current_stage  # force state load
        services["af_engine"] = True
    except Exception as e:
        logger.debug("Health check: af_engine not healthy: %s", e)
        services["af_engine"] = False

    elapsed_ms = (time.time() - start) * 1000
    overall_healthy = all(services.values())

    from flask import current_app
    uptime_seconds = None
    if hasattr(current_app, '_start_time'):
        uptime_seconds = time.time() - current_app._start_time

    return jsonify({
        "healthy": overall_healthy,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": uptime_seconds,
        "services": services,
        "latency_ms": round(elapsed_ms, 2)
    })


@core_bp.route('/api/engine/status')
def api_engine_status():
    """Live engine status endpoint reading directly from af_engine."""
    import time as _time
    import sys as _sys
    _start = _time.time()

    engine_data = {
        "available": False,
        "version": None,
        "mission_id": None,
        "stage": None,
        "iteration": None,
        "cycle": None,
        "cycle_budget": None,
        "stages": None,
        "error": None,
    }

    try:
        _af_root = str(Path(__file__).parent.parent)
        if _af_root not in _sys.path:
            _sys.path.insert(0, _af_root)

        from af_engine import StateManager, STAGES
        sm = StateManager(MISSION_PATH)
        cycles_remaining = max(0, sm.cycle_budget - sm.cycle_number)
        engine_data.update({
            "available": True,
            "mission_id": sm.mission_id,
            "stage": sm.current_stage,
            "iteration": sm.iteration,
            "cycle": sm.cycle_number,
            "cycle_budget": sm.cycle_budget,
            "cycles_remaining": cycles_remaining,
            "is_last_cycle": sm.cycle_number >= sm.cycle_budget,
            "cycle_history": sm.cycle_history,
            "stage_history": sm.history[-10:] if sm.history else [],
            "stages": STAGES,
            "error": None,
        })
    except ImportError:
        logger.warning("af_engine import error", exc_info=True)
        engine_data["error"] = "af_engine not importable"
    except Exception:
        logger.exception("Engine state error")
        engine_data["error"] = "Internal server error"

    elapsed_ms = (_time.time() - _start) * 1000
    engine_data["latency_ms"] = round(elapsed_ms, 2)
    engine_data["timestamp"] = datetime.now(timezone.utc).isoformat()

    return jsonify(engine_data)


@core_bp.route('/api/start/<mode>', methods=['POST'])
def api_start(mode):
    if mode not in _ALLOWED_MODES:
        return jsonify({"success": False, "message": "Invalid mode"}), 400
    data = request.get_json(silent=True) or {}
    requested_provider = data.get("llm_provider") or data.get("provider")
    normalized_provider = None
    if requested_provider:
        normalized_provider = _normalize_runtime_provider(requested_provider)
        if not normalized_provider:
            return jsonify({"success": False, "message": "Invalid provider"}), 400
        if set_llm_provider:
            set_llm_provider(normalized_provider)
        _update_active_mission_provider(normalized_provider)
    success, message = start_claude(mode)
    return jsonify({"success": success, "message": message, "provider": normalized_provider})


@core_bp.route('/api/stop', methods=['POST'])
def api_stop():
    success, message = stop_claude()
    return jsonify({"success": success, "message": message})


@core_bp.route('/api/llm-provider', methods=['GET', 'POST'])
def api_llm_provider():
    """Get or set the active LLM provider for AtlasForge starts."""
    if request.method == 'GET':
        if get_llm_provider:
            provider = get_llm_provider()
        else:
            provider = "claude"
        return jsonify({"provider": provider, "supported": ["claude", "codex", "gemini"]})

    if not set_llm_provider:
        return jsonify({"success": False, "message": "Provider configuration unavailable"}), 503

    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    if not provider:
        return jsonify({"success": False, "message": "Missing provider"}), 400

    normalized = set_llm_provider(provider)
    return jsonify({
        "success": True,
        "provider": normalized,
        "message": f"Provider set to {normalized}"
    })


# =============================================================================
# NARRATIVE AUTONOMOUS ROUTES
# =============================================================================

@core_bp.route('/api/narrative-autonomous/status')
def api_narrative_status():
    """Get status of the narrative autonomous workflow."""
    if get_narrative_status:
        return jsonify(get_narrative_status())
    return jsonify({"error": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/start', methods=['POST'])
def api_narrative_start():
    """Start narrative autonomous workflow."""
    if start_narrative:
        success, message = start_narrative()
        return jsonify({"success": success, "message": message})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/stop', methods=['POST'])
def api_narrative_stop():
    """Stop narrative autonomous workflow."""
    if stop_narrative:
        success, message = stop_narrative()
        return jsonify({"success": success, "message": message})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/chat', methods=['GET', 'POST'])
def api_narrative_chat():
    """Get or send chat messages to narrative workflow."""
    if request.method == 'POST':
        if send_message_to_narrative:
            data = request.get_json(silent=True) or {}
            message = data.get('message', '')
            if message:
                send_message_to_narrative(message)
                return jsonify({"success": True, "message": "Message sent to narrative workflow"})
            return jsonify({"success": False, "message": "No message provided"})
        return jsonify({"success": False, "message": "Narrative not available"}), 503
    else:
        if get_narrative_chat_history:
            return jsonify(get_narrative_chat_history(50))
        return jsonify([])


@core_bp.route('/api/narrative-autonomous/mission', methods=['GET', 'POST'])
def api_narrative_mission():
    """Get or set the narrative mission."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"error": "Narrative not available"}), 503

    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        story_number = data.get('story_number')
        story_title = data.get('story_title')
        story_genre = data.get('story_genre')
        story_logline = data.get('story_logline')

        if story_number is None or not story_title:
            return jsonify({"success": False, "message": "story_number and story_title required"})
        if not isinstance(story_title, str):
            return jsonify({"success": False, "message": "story_title must be a string"}), 400

        try:
            story_number = int(story_number)
        except (ValueError, TypeError, OverflowError):
            return jsonify({"success": False, "message": "story_number must be an integer"}), 400
        if not (0 <= story_number <= 99999):
            return jsonify({"success": False, "message": "story_number must be between 0 and 99999"}), 400

        import uuid
        # Strip null bytes and control characters before using in path
        story_title = re.sub(r'[\x00-\x1f\x7f]', '', story_title)
        if not story_title:
            return jsonify({"success": False, "message": "story_title cannot be empty"}), 400
        safe_title = re.sub(r'[^\w\-]', '_', story_title)[:50]
        safe_title = re.sub(r'_+', '_', safe_title).strip('_')
        if not safe_title:
            return jsonify({"success": False, "message": "story_title contains no usable characters"}), 400
        project_base = Path("/media/vader/TIE-FIGHTER/RCFT - Narrative Project/01 - Narrative Research/Completed")
        story_workspace = (project_base / f"{story_number:03d}_{safe_title}").resolve()
        _proj_base = str(project_base.resolve()) + "/"
        if not (str(story_workspace) + "/").startswith(_proj_base):
            return jsonify({"success": False, "message": "Invalid story title"}), 400
        story_workspace.mkdir(parents=True, exist_ok=True)

        new_mission = {
            "mission_id": f"narrative_{uuid.uuid4().hex[:8]}",
            "story_number": story_number,
            "story_title": story_title,
            "story_genre": story_genre,
            "story_logline": story_logline,
            "current_step": "INIT",
            "status": "running",
            "step_results": [],
            "files_created": [],
            "story_workspace": str(story_workspace),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "history": [],
            "approval_pending": None,
        }
        io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, new_mission)

        return jsonify({
            "success": True,
            "message": f"Narrative mission set for '{story_title}'",
            "mission": new_mission
        })
    else:
        mission = io_utils.atomic_read_json(NARRATIVE_MISSION_PATH, {})
        return jsonify(mission)


@core_bp.route('/api/narrative-autonomous/approve', methods=['POST'])
def api_narrative_approve():
    """Approve a step waiting for user approval."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"success": False, "message": "Narrative not available"}), 503

    mission = io_utils.atomic_read_json(NARRATIVE_MISSION_PATH, {})
    if mission.get("status") != "waiting_approval":
        return jsonify({"success": False, "message": "No step waiting for approval"})

    mission["status"] = "running"
    mission["approval_pending"] = None
    io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, mission)

    if send_message_to_narrative:
        send_message_to_narrative("approve")

    return jsonify({"success": True, "message": "Step approved"})


@core_bp.route('/api/narrative-autonomous/pause', methods=['POST'])
def api_narrative_pause():
    """Pause the narrative workflow."""
    if send_message_to_narrative:
        send_message_to_narrative("pause")
        return jsonify({"success": True, "message": "Pause command sent"})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/resume', methods=['POST'])
def api_narrative_resume():
    """Resume the narrative workflow."""
    if send_message_to_narrative:
        send_message_to_narrative("resume")
        return jsonify({"success": True, "message": "Resume command sent"})
    return jsonify({"success": False, "message": "Narrative not available"}), 503


@core_bp.route('/api/narrative-autonomous/reset', methods=['POST'])
def api_narrative_reset():
    """Reset the narrative mission to initial state."""
    if not NARRATIVE_MISSION_PATH:
        return jsonify({"success": False, "message": "Narrative not available"}), 503

    default_mission = {
        "mission_id": "narrative_default",
        "story_number": None,
        "story_title": None,
        "story_genre": None,
        "story_logline": None,
        "current_step": "INIT",
        "status": "pending",
        "step_results": [],
        "files_created": [],
        "story_workspace": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "history": [],
        "approval_pending": None,
    }
    io_utils.atomic_write_json(NARRATIVE_MISSION_PATH, default_mission)
    if send_message_to_narrative:
        send_message_to_narrative("reset")
    return jsonify({"success": True, "message": "Narrative mission reset"})


# =============================================================================
# JOURNAL ROUTES
# =============================================================================

@core_bp.route('/api/journal')
def api_journal():
    cache = _get_ttl_cache()
    if cache:
        cached = cache.get('api_journal')
        if cached is not None:
            return jsonify(cached)
    result = get_recent_journal(15)
    if cache:
        cache.set('api_journal', result, ttl_seconds=1.0)
    return jsonify(result)


# =============================================================================
# MISSION ROUTES
# =============================================================================

@core_bp.route('/api/mission', methods=['GET', 'POST'])
def api_mission():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        import logging as _logging
        import uuid

        # Pre-flight validation via canonical MissionConfig
        try:
            from af_engine.mission_config import MissionConfig, validate_mission_params, save_audit_log
            _mc_available = True
        except ImportError:
            _mc_available = False

        if _mc_available:
            is_valid, param_errors = validate_mission_params(data)
            if not is_valid:
                return jsonify({"success": False, "errors": param_errors,
                                "message": "; ".join(param_errors)}), 400

        raw_cb = data.get('cycle_budget')
        _logging.getLogger(__name__).info(f"[MISSION] cycle_budget submitted={raw_cb!r}")

        problem_statement = data.get('problem_statement') or data.get('mission', '')
        user_project_name = data.get('project_name')

        if problem_statement:
            mission_id = f"mission_{uuid.uuid4().hex[:8]}"
            missions_dir = BASE_DIR / "missions"
            mission_dir = missions_dir / mission_id

            resolved_project_name = None
            try:
                from project_name_resolver import resolve_project_name
                resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
                mission_workspace = (BASE_DIR / "workspace") / resolved_project_name / mission_id
            except ImportError:
                mission_workspace = mission_dir / "workspace"

            mission_dir.mkdir(parents=True, exist_ok=True)
            (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

            if _mc_available:
                active_provider = get_llm_provider() if get_llm_provider else None
                req = dict(data)
                req["problem_statement"] = problem_statement
                if active_provider and "llm_provider" not in req:
                    req["llm_provider"] = active_provider
                config, audit = MissionConfig.from_request(req, mission_id=mission_id)
                new_mission = config.to_mission_dict(
                    mission_id=mission_id, mission_workspace=mission_workspace,
                    mission_dir=mission_dir, resolved_project_name=resolved_project_name,
                    audit=audit,
                )
                io_utils.atomic_write_json(MISSION_PATH, new_mission)
                mission_config_path = mission_dir / "mission_config.json"
                with open(mission_config_path, 'w') as f:
                    json.dump(config.to_config_dict(
                        mission_id=mission_id, mission_workspace=mission_workspace,
                        resolved_project_name=resolved_project_name,
                        created_at=new_mission["created_at"],
                    ), f, indent=2)
                save_audit_log(audit, mission_dir)
                applied_cycle_budget = config.cycle_budget
            else:
                _raw_cb = data.get('cycle_budget', 3)
                if isinstance(_raw_cb, bool):
                    return jsonify({"success": False, "message": "Invalid cycle_budget: must be an integer, not bool"}), 400
                try:
                    cycle_budget = max(1, int(_raw_cb))
                except (ValueError, TypeError, OverflowError):
                    return jsonify({"success": False, "message": "Invalid cycle_budget: must be an integer"}), 400
                active_provider = (
                    _normalize_runtime_provider(data.get("llm_provider") or data.get("provider"))
                    or (get_llm_provider() if get_llm_provider else "claude")
                )
                new_mission = {
                    "mission_id": mission_id, "problem_statement": problem_statement,
                    "original_problem_statement": problem_statement,
                    "preferences": {}, "success_criteria": [],
                    "current_stage": "PLANNING", "iteration": 0, "max_iterations": 10,
                    "artifacts": {"plan": None, "code": [], "tests": []}, "history": [],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "last_updated": datetime.now(timezone.utc).isoformat(),
                    "cycle_started_at": datetime.now(timezone.utc).isoformat(),
                    "cycle_budget": cycle_budget, "current_cycle": 1, "cycle_history": [],
                    "mission_workspace": str(mission_workspace), "mission_dir": str(mission_dir),
                    "project_name": resolved_project_name, "llm_provider": active_provider,
                    "metadata": data.get('metadata', {})
                }
                io_utils.atomic_write_json(MISSION_PATH, new_mission)
                applied_cycle_budget = cycle_budget

            try:
                from mission_analytics import get_analytics
                analytics = get_analytics()
                analytics.start_mission(mission_id, problem_statement)
            except Exception:
                _logging.warning("Analytics: Failed to register mission", exc_info=True)

            response_msg = f"Mission saved with {applied_cycle_budget} cycle(s)."
            if resolved_project_name:
                response_msg += f" Project: {resolved_project_name}."
            response_msg += " Click 'Start R&D' to begin."

            return jsonify({
                "success": True,
                "message": response_msg,
                "mission_id": mission_id,
                "mission_workspace": str(mission_workspace),
                "project_name": resolved_project_name
            })
        return jsonify({"success": False, "message": "No mission provided"})
    else:
        try:
            from af_engine import StateManager
            mission = StateManager(MISSION_PATH).mission
        except Exception as e:
            logger.debug("StateManager unavailable, falling back to atomic_read_json: %s", e)
            mission = io_utils.atomic_read_json(MISSION_PATH, {})
        return jsonify(mission)


@core_bp.route('/api/mission/reset', methods=['POST'])
def api_mission_reset():
    if send_message_to_claude is None:
        return jsonify({"error": "Message handler not initialized"}), 503
    send_message_to_claude("reset")
    return jsonify({"success": True, "message": "Reset command sent"})


@core_bp.route('/api/suggest-project-name', methods=['POST'])
def api_suggest_project_name():
    """
    Suggest a project name based on problem statement text.
    Returns suggested name, strategies tried, and existing projects.
    """
    data = request.get_json(silent=True) or {}
    problem_statement = data.get('problem_statement', '')

    if not problem_statement or len(problem_statement) < 5:
        return jsonify({"error": "Problem statement too short"}), 400

    try:
        from project_name_resolver import suggest_project_name
        result = suggest_project_name(problem_statement)
        return jsonify(result)
    except ImportError:
        return jsonify({"error": "project_name_resolver module not found"}), 500
    except Exception:
        logger.exception("suggest_project_name failed")
        return jsonify({"error": "An internal error occurred"}), 500


# =============================================================================
# PROPOSALS ROUTES
# =============================================================================

@core_bp.route('/api/proposals', methods=['GET'])
def api_proposals():
    """Get all pending proposals."""
    proposals = io_utils.atomic_read_json(PROPOSALS_PATH, {"pending": [], "approved": [], "rejected": []})
    return jsonify(proposals)


@core_bp.route('/api/proposals/<proposal_id>/approve', methods=['POST'])
def api_approve_proposal(proposal_id):
    """Approve a specific proposal."""
    def update_fn(proposals):
        for i, p in enumerate(proposals.get("pending", [])):
            if p.get("id") == proposal_id:
                p["status"] = "approved"
                p["approved_at"] = datetime.now(timezone.utc).isoformat()
                proposals.setdefault("approved", []).append(p)
                proposals["pending"].pop(i)
                break
        return proposals

    io_utils.atomic_update_json(PROPOSALS_PATH, update_fn, {"pending": [], "approved": [], "rejected": []})
    return jsonify({"success": True})


@core_bp.route('/api/proposals/<proposal_id>/reject', methods=['POST'])
def api_reject_proposal(proposal_id):
    """Reject a specific proposal."""
    def update_fn(proposals):
        for i, p in enumerate(proposals.get("pending", [])):
            if p.get("id") == proposal_id:
                p["status"] = "rejected"
                p["rejected_at"] = datetime.now(timezone.utc).isoformat()
                proposals.setdefault("rejected", []).append(p)
                proposals["pending"].pop(i)
                break
        return proposals

    io_utils.atomic_update_json(PROPOSALS_PATH, update_fn, {"pending": [], "approved": [], "rejected": []})
    return jsonify({"success": True})


# =============================================================================
# RECOMMENDATIONS ROUTES
# =============================================================================

@core_bp.route('/api/recommendations', methods=['GET'])
def api_recommendations():
    """Get all mission recommendations with optional filtering.

    Query Parameters:
        source_type: Filter by source type ('drift_halt' or 'successful_completion')

    Returns:
        JSON with filtered or all recommendations from SQLite storage
    """
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"items": [], "error": "Storage not available"}), 503

    source_type_filter = request.args.get('source_type')

    if source_type_filter and source_type_filter not in _VALID_SOURCE_TYPES:
        return jsonify({"items": [], "error": "Invalid source_type"}), 400

    try:
        if source_type_filter:
            items = storage.get_filtered(source_type=source_type_filter)
        else:
            items = storage.get_all()
        return jsonify({"items": items})
    except Exception:
        logger.exception("SQLite read failed")
        return jsonify({"items": [], "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations', methods=['POST'])
def api_add_recommendation():
    """Add a new mission recommendation with auto-tagging and similarity check."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json(silent=True) or {}
    import uuid

    source_type = data.get("source_type", "manual")
    if source_type not in _VALID_SOURCE_TYPES:
        return jsonify({"success": False, "error": "Invalid source_type"}), 400

    try:
        suggested_cycles = max(1, min(10, int(data.get("suggested_cycles", 3))))
    except (ValueError, TypeError, OverflowError):
        return jsonify({"success": False, "error": "suggested_cycles must be an integer 1-10"}), 400

    recommendation = {
        "id": f"rec_{uuid.uuid4().hex[:8]}",
        "mission_title": data.get("mission_title", "Untitled Mission"),
        "mission_description": data.get("mission_description", ""),
        "suggested_cycles": suggested_cycles,
        "source_mission_id": data.get("source_mission_id"),
        "source_mission_summary": data.get("source_mission_summary", ""),
        "rationale": data.get("rationale", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type
    }

    # Auto-tag and check for similar suggestions
    similar_to = []
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        recommendation = analyzer.on_new_suggestion(recommendation)
        similar_to = recommendation.get('similar_to', [])
        if not isinstance(similar_to, list):
            similar_to = []
    except Exception:
        import logging
        logging.warning("Auto-tagging failed", exc_info=True)

    try:
        # Strip similar_to before storage — it is not in ALLOWED_COLUMNS
        recommendation.pop('similar_to', None)
        storage.add(recommendation)
        response = {"success": True, "recommendation": recommendation}
        if similar_to:
            response["merge_candidates"] = similar_to
            response["has_similar"] = True
        return jsonify(response)
    except Exception:
        logger.exception("SQLite add failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/merge-candidates', methods=['GET'])
def api_merge_candidates():
    """Return all suggestions as merge candidates (no similarity gating)."""
    import logging
    storage = _get_suggestion_storage()
    if storage:
        try:
            items = storage.get_all()
        except Exception:
            logging.exception("merge-candidates storage error")
            return jsonify({"error": "Failed to load suggestions"}), 500
    else:
        recommendations = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
        items = recommendations.get("items", [])

    candidates = []
    for item in items:
        candidates.append({
            "id": item.get("id"),
            "mission_title": item.get("mission_title", ""),
            "mission_description": (item.get("mission_description", "") or "")[:200],
            "suggested_cycles": item.get("suggested_cycles", 3),
            "auto_tags": item.get("auto_tags", []),
            "priority_score": item.get("priority_score", 50),
        })

    return jsonify({
        "candidates": candidates,
        "total": len(candidates),
    })


@core_bp.route('/api/recommendations/merge', methods=['POST'])
def api_merge_recommendations():
    """Merge multiple recommendations into one, preserving source descriptions."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    import uuid
    data = request.get_json(silent=True) or {}
    source_ids = data.get("source_ids", [])
    merged_data = data.get("merged_data", {})
    if not isinstance(merged_data, dict):
        merged_data = {}
    delete_sources = data.get("delete_sources", True)

    if not isinstance(source_ids, list):
        return jsonify({"success": False, "error": "source_ids must be a list"}), 400
    # Validate each element is a hashable scalar (string or int) before dedup
    for sid in source_ids:
        if not isinstance(sid, (str, int)):
            return jsonify({"success": False, "error": "source_ids elements must be strings or integers"}), 400
    # Deduplicate while preserving order
    seen = set()
    deduped = []
    for sid in source_ids:
        if sid not in seen:
            seen.add(sid)
            deduped.append(sid)
    source_ids = deduped

    if len(source_ids) < 2:
        return jsonify({"success": False, "error": "Need at least 2 unique recommendations to merge"}), 400

    try:
        # Get source descriptions from database
        source_descriptions = []
        for source_id in source_ids:
            rec = storage.get_by_id(source_id)
            if rec:
                source_descriptions.append({
                    "id": rec.get("id"),
                    "title": rec.get("mission_title", ""),
                    "description": rec.get("mission_description", "")
                })

        # Validate all source_ids were found before proceeding
        if len(source_descriptions) < len(source_ids):
            return jsonify({"success": False,
                            "error": "One or more source recommendations not found"}), 404

        try:
            _merged_cycles = max(1, min(10, int(merged_data.get("suggested_cycles", 3))))
        except (ValueError, TypeError, OverflowError):
            _merged_cycles = 3
        new_rec = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": merged_data.get("mission_title", "Merged Suggestion"),
            "mission_description": merged_data.get("mission_description", ""),
            "suggested_cycles": _merged_cycles,
            "rationale": merged_data.get("rationale", ""),
            "source_type": "merged",
            "merged_from": source_ids,
            "merged_source_descriptions": source_descriptions,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        # Add new record first, then delete sources (atomic safety: if add fails, sources remain)
        storage.add(new_rec)
        if delete_sources:
            try:
                storage.delete_multiple(source_ids)
            except Exception:
                # Rollback: remove newly added to prevent duplicates
                try:
                    storage.delete(new_rec["id"])
                except Exception:
                    logger.error("Rollback failed: could not delete merged rec %s", new_rec["id"])
                raise
        return jsonify({"success": True, "new_recommendation": new_rec})
    except Exception:
        logger.exception("SQLite merge failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/analyze', methods=['GET'])
def api_analyze_recommendations():
    """
    Analyze all recommendations: auto-tag, prioritize, and health-check.

    Returns:
        JSON with items sorted by priority, health_report, and total count
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.analyze_all(persist=True)
        return jsonify(result)
    except Exception:
        logger.exception("analyze_recommendations failed")
        return jsonify({"error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/auto-tag', methods=['POST'])
def api_auto_tag_recommendations():
    """
    Run auto-tagging on all suggestions.

    Returns:
        JSON with tagged_count and tag_distribution
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.auto_tag_all(persist=True)
        return jsonify(result)
    except Exception:
        logger.exception("auto_tag_recommendations failed")
        return jsonify({"error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/health-report', methods=['GET'])
def api_health_report():
    """
    Get health summary of all suggestions.

    Returns:
        JSON with counts (healthy, stale, orphaned, needs_review, hot),
        total, stale_items, orphaned_items, needs_analysis
    """
    try:
        from suggestion_analyzer import get_analyzer
        analyzer = get_analyzer()
        result = analyzer.get_health_report()
        return jsonify(result)
    except Exception:
        logger.exception("health_report failed")
        return jsonify({"error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['GET'])
def api_get_recommendation(rec_id):
    """Get a specific recommendation."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"error": "Storage not available"}), 503

    try:
        rec = storage.get_by_id(rec_id)
        if rec:
            return jsonify(rec)
        return jsonify({"error": "Recommendation not found"}), 404
    except Exception:
        logger.exception("SQLite get failed")
        return jsonify({"error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['DELETE'])
def api_delete_recommendation(rec_id):
    """Delete a recommendation."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    try:
        deleted = storage.delete(rec_id)
        return jsonify({"success": True, "deleted": deleted})
    except Exception:
        logger.exception("SQLite delete failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/<rec_id>', methods=['PUT'])
def api_update_recommendation(rec_id):
    """Update a recommendation (edit mode)."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json(silent=True) or {}

    # Input validation
    if "suggested_cycles" in data:
        try:
            cycles = int(data["suggested_cycles"])
            if cycles < 1 or cycles > 10:
                return jsonify({
                    "success": False,
                    "error": "Cycle count must be between 1 and 10"
                }), 400
        except (ValueError, TypeError, OverflowError):
            return jsonify({
                "success": False,
                "error": "Cycle count must be a valid number"
            }), 400

    if "mission_title" in data:
        title = str(data["mission_title"]).strip()
        if len(title) < 3:
            return jsonify({
                "success": False,
                "error": "Mission title must be at least 3 characters"
            }), 400

    try:
        # Get current record to preserve originals
        current = storage.get_by_id(rec_id)
        if not current:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404

        updates = {}
        # Preserve originals if first edit
        if "original_mission_title" not in current:
            updates["original_mission_title"] = current.get("mission_title")
            updates["original_mission_description"] = current.get("mission_description")
            updates["original_rationale"] = current.get("rationale")
            updates["original_suggested_cycles"] = current.get("suggested_cycles")
        # Update fields
        if "mission_title" in data:
            updates["mission_title"] = str(data["mission_title"]).strip()
        if "mission_description" in data:
            updates["mission_description"] = data["mission_description"]
        if "suggested_cycles" in data and data["suggested_cycles"] is not None:
            try:
                updates["suggested_cycles"] = int(data["suggested_cycles"])
            except (ValueError, TypeError):
                return jsonify({"success": False, "error": "suggested_cycles must be an integer"}), 400
        if "rationale" in data:
            updates["rationale"] = data["rationale"]
        updates["last_edited_at"] = datetime.now(timezone.utc).isoformat()
        storage.update(rec_id, updates)
        return jsonify({"success": True})
    except Exception:
        logger.exception("SQLite update failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/<rec_id>/set-mission', methods=['POST'])
def api_set_mission_from_recommendation(rec_id):
    """Set a mission from a recommendation and remove it from the list.

    Supports shared workspaces via project_name resolution.
    """
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"success": False, "error": "Storage not available"}), 503

    data = request.get_json(silent=True) or {}
    _raw_cb_rec = data.get("cycle_budget", 3)
    if isinstance(_raw_cb_rec, bool):
        return jsonify({"success": False, "error": "cycle_budget must be an integer, not bool"}), 400
    try:
        cycle_budget = max(1, min(10, int(_raw_cb_rec)))
    except (ValueError, TypeError, OverflowError):
        return jsonify({"success": False, "error": "cycle_budget must be a valid integer"}), 400
    user_project_name = data.get("project_name")  # Optional user-specified project name

    try:
        target_rec = storage.get_by_id(rec_id)
    except Exception:
        import logging
        logging.exception("SQLite get failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

    if not target_rec:
        return jsonify({"success": False, "error": "Recommendation not found"}), 404

    import uuid as _uuid_r
    import logging as _rlog
    mission_id = f"mission_{_uuid_r.uuid4().hex[:8]}"

    # Build problem statement (special handling for merged recommendations)
    if target_rec.get("source_type") == "merged" and target_rec.get("merged_source_descriptions"):
        parts = []
        user_desc = (target_rec.get("mission_description") or "").strip()
        if user_desc:
            parts.append(f"## Summary\n{user_desc}")
        for _src in target_rec.get("merged_source_descriptions", []):
            if _src.get("description"):
                parts.append(f"## {_src.get('title', 'Source')}\n{_src['description']}")
        problem_statement = "\n\n".join(parts) if parts else target_rec.get("mission_title", "")
    else:
        problem_statement = target_rec.get("mission_description") or target_rec.get("mission_title") or ""

    # Pre-flight validation via canonical MissionConfig
    try:
        from af_engine.mission_config import MissionConfig as _MCR, validate_mission_params as _vmpr, save_audit_log as _salr
        _mcr_ok = True
    except ImportError:
        _mcr_ok = False

    if _mcr_ok:
        _ok, _errs = _vmpr({"problem_statement": problem_statement, "cycle_budget": data.get("cycle_budget")})
        if not _ok:
            return jsonify({"success": False, "errors": _errs, "message": "; ".join(_errs)}), 400

    missions_dir = BASE_DIR / "missions"
    mission_dir = missions_dir / mission_id

    resolved_project_name = None
    try:
        from project_name_resolver import resolve_project_name
        resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
        mission_workspace = WORKSPACE_DIR / resolved_project_name / mission_id
    except ImportError:
        mission_workspace = mission_dir / "workspace"

    mission_dir.mkdir(parents=True, exist_ok=True)
    (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
    (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
    (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

    if _mcr_ok:
        active_provider = get_llm_provider() if get_llm_provider else None
        _req_r = dict(data)
        _req_r["problem_statement"] = problem_statement
        if active_provider and "llm_provider" not in _req_r:
            _req_r["llm_provider"] = active_provider
        _cfg_r, _aud_r = _MCR.from_request(_req_r, mission_id=mission_id)
        new_mission = _cfg_r.to_mission_dict(
            mission_id=mission_id, mission_workspace=mission_workspace,
            mission_dir=mission_dir, resolved_project_name=resolved_project_name,
            source_recommendation_id=rec_id, audit=_aud_r,
        )
        io_utils.atomic_write_json(MISSION_PATH, new_mission)
        with open(mission_dir / "mission_config.json", 'w') as _f:
            json.dump(_cfg_r.to_config_dict(
                mission_id=mission_id, mission_workspace=mission_workspace,
                resolved_project_name=resolved_project_name,
                source_recommendation_id=rec_id,
                created_at=new_mission["created_at"],
            ), _f, indent=2)
        _salr(_aud_r, mission_dir)
        applied_cycle_budget = _cfg_r.cycle_budget
    else:
        new_mission = {
            "mission_id": mission_id, "problem_statement": problem_statement,
            "original_problem_statement": problem_statement,
            "preferences": {}, "success_criteria": [],
            "current_stage": "PLANNING", "iteration": 0, "max_iterations": 10,
            "artifacts": {"plan": None, "code": [], "tests": []}, "history": [],
            "created_at": datetime.now(timezone.utc).isoformat(), "last_updated": datetime.now(timezone.utc).isoformat(),
            "cycle_started_at": datetime.now(timezone.utc).isoformat(),
            "cycle_budget": max(1, cycle_budget), "current_cycle": 1, "cycle_history": [],
            "mission_workspace": str(mission_workspace), "mission_dir": str(mission_dir),
            "project_name": resolved_project_name,
            "llm_provider": (
                _normalize_runtime_provider(data.get("llm_provider") or data.get("provider"))
                or (get_llm_provider() if get_llm_provider else "claude")
            ),
            "source_recommendation_id": rec_id
        }
        io_utils.atomic_write_json(MISSION_PATH, new_mission)
        applied_cycle_budget = cycle_budget

    try:
        from mission_analytics import get_analytics
        get_analytics().start_mission(mission_id, problem_statement)
    except Exception:
        _rlog.warning("Analytics: Failed to register mission", exc_info=True)

    try:
        storage.delete(rec_id)
    except Exception:
        _rlog.warning("SQLite delete failed", exc_info=True)

    response_msg = f"Mission set with {applied_cycle_budget} cycle(s)."
    if resolved_project_name:
        response_msg += f" Project: {resolved_project_name}."
    response_msg += " Click 'Start Mission' to begin."

    return jsonify({
        "success": True,
        "message": response_msg,
        "mission_id": mission_id,
        "mission_workspace": str(mission_workspace),
        "project_name": resolved_project_name
    })


# =============================================================================
# MISSION LOGS ROUTES
# =============================================================================

@core_bp.route('/api/mission-logs')
def api_mission_logs():
    """List all mission log files."""
    logs = []
    if MISSION_LOGS_DIR and MISSION_LOGS_DIR.exists():
        for log_file in MISSION_LOGS_DIR.glob("*_report.json"):
            try:
                with open(log_file, 'r') as f:
                    data = json.load(f)
                logs.append({
                    "mission_id": data.get("mission_id", log_file.stem),
                    "original_mission": data.get("original_mission", ""),
                    "total_cycles": data.get("total_cycles", 0),
                    "total_iterations": data.get("total_iterations", 0),
                    "started_at": data.get("started_at"),
                    "completed_at": data.get("completed_at"),
                    "file_name": log_file.name
                })
            except Exception:
                logger.warning("Skipping unreadable log file %s", log_file.name, exc_info=True)
                continue

    logs.sort(key=lambda x: x.get("completed_at") or "", reverse=True)
    return jsonify({"logs": logs, "total": len(logs)})


@core_bp.route('/api/mission-logs/<mission_id>')
def api_mission_log_detail(mission_id):
    """Get details of a specific mission log."""
    if not MISSION_LOGS_DIR or not MISSION_LOGS_DIR.exists():
        return jsonify({"error": "Mission logs directory not found"}), 404

    # Validate mission_id before using in glob to prevent glob injection
    if not re.match(r'^[a-zA-Z0-9_-]+$', mission_id):
        return jsonify({"error": "Invalid mission_id"}), 400

    for log_file in MISSION_LOGS_DIR.glob(f"{mission_id}_report.json"):
        try:
            with open(log_file, 'r') as f:
                data = json.load(f)
            return jsonify(data)
        except Exception:
            logger.exception("Error reading log %s", log_file.name)
            return jsonify({"error": "An internal error occurred"}), 500

    return jsonify({"error": "Mission log not found"}), 404


# =============================================================================
# FILE DOWNLOAD ROUTES
# =============================================================================

_MISSION_ID_RE = re.compile(r'^[a-zA-Z0-9_-]+\Z')


def _validate_mission_id(mission_id: str) -> bool:
    """Validate mission_id against path traversal attacks."""
    return bool(isinstance(mission_id, str) and mission_id and _MISSION_ID_RE.match(mission_id))

@core_bp.route('/api/download/<path:filepath>')
def download_file(filepath):
    """Serve files from workspace for download.

    Supports both global workspace and mission-specific paths:
    - /api/download/artifacts/file.txt - global workspace
    - /api/download/mission/{mission_id}/artifacts/file.txt - mission workspace

    Uses centralized workspace resolver for correct path resolution
    with both shared and legacy workspaces.
    """
    if not filepath or not filepath.strip():
        abort(400)

    # Check if this is a mission-specific path
    if filepath.startswith('mission/'):
        parts = filepath.split('/', 2)
        if len(parts) >= 3:
            mission_id = parts[1]
            if not _validate_mission_id(mission_id):
                abort(400)
            relative_path = parts[2]
            if not relative_path or not relative_path.strip():
                abort(400)

            # Use centralized workspace resolver
            from .workspace_resolver import resolve_mission_workspace
            missions_dir = BASE_DIR / "missions"
            mission_workspace = resolve_mission_workspace(
                mission_id, missions_dir, WORKSPACE_DIR, io_utils
            )

            # C1: Validate mission_workspace itself is within server root to prevent
            # attacker-crafted mission_ids from setting allowed_base to an arbitrary path.
            _server_root = WORKSPACE_DIR.resolve()
            if not mission_workspace.resolve().is_relative_to(_server_root):
                abort(403)

            full_path = (mission_workspace / relative_path).resolve()
            allowed_base = mission_workspace.resolve()
        else:
            abort(404)
    else:
        full_path = (WORKSPACE_DIR / filepath).resolve()
        allowed_base = WORKSPACE_DIR.resolve()

    # Authoritative pre-open containment check using resolved paths.
    # resolve() eliminates '..' and symlinks, making this system-independent.
    if not full_path.is_relative_to(allowed_base):
        abort(403)

    mime_type, _ = mimetypes.guess_type(str(full_path))

    from io import BytesIO

    # Enforce download size cap to prevent memory exhaustion DoS
    _MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
    try:
        file_size = full_path.stat().st_size
    except OSError:
        abort(403)
    if file_size > _MAX_DOWNLOAD_BYTES:
        abort(413)  # Request Entity Too Large

    # Pass the *unresolved* path so O_NOFOLLOW sees symlinks,
    # and allowed_base for post-open fd containment check.
    try:
        data = _safe_read_bytes(full_path, max_bytes=_MAX_DOWNLOAD_BYTES, allowed_base=allowed_base)
    except IOError:
        abort(500)  # read error (distinct from access denial)
    if data is None:
        abort(403)  # symlink, not found, or access denied

    return send_file(
        BytesIO(data),
        mimetype=mime_type or 'application/octet-stream',
        as_attachment=True,
        download_name=re.sub(r'[^\w.\-]', '_', full_path.name)
    )


def _safe_read_bytes(path, max_bytes=None, allowed_base=None):
    """Read file bytes using O_NOFOLLOW to prevent symlink TOCTOU attacks.

    O_NOFOLLOW only blocks symlinks in the final path component. Directory
    component symlinks are not blocked by the kernel flag. The post-open
    /proc/self/fd/ containment check provides defense-in-depth against
    directory component symlink attacks.

    If allowed_base is provided, performs post-open containment check by
    reading the fd's real path from /proc/self/fd/ and verifying it resides
    inside the allowed directory.  This prevents TOCTOU races where a symlink
    is swapped in between a pre-open resolve() and the actual open().
    """
    try:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None  # symlink or access denied
    # Post-open containment: verify the *actual* file the fd points to
    if allowed_base is not None:
        try:
            real_path = Path(os.readlink(f"/proc/self/fd/{fd}")).resolve()
            allowed_resolved = Path(allowed_base).resolve()
            real_path.relative_to(allowed_resolved)
        except (ValueError, OSError) as _e:
            logger.warning("_safe_read_bytes: post-open containment check failed: %s", _e)
            os.close(fd)
            return None  # fd points outside allowed_base or allowed_base is invalid
        except Exception as _e:
            logger.warning("_safe_read_bytes: unexpected error in containment check: %s", _e)
            os.close(fd)
            return None
    try:
        f = os.fdopen(fd, 'rb')
    except Exception:
        os.close(fd)
        return None
    if max_bytes is not None and (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0):
        f.close()
        raise ValueError(f"max_bytes must be a non-negative integer, got {max_bytes!r}")
    try:
        return f.read(max_bytes) if max_bytes is not None else f.read()
    except Exception as e:
        raise IOError(f"Read error: {e}") from e
    finally:
        try:
            f.close()
        except Exception:
            pass


_TEXT_MIME_TYPES = {
    'application/json', 'application/javascript', 'application/xml',
    'application/yaml', 'application/x-yaml', 'application/toml',
    'application/x-sh', 'application/x-python',
}


def _classify_file_type(mime_type):
    """Return 'image', 'text', or 'binary' for a given mime type."""
    if mime_type is None:
        return 'binary'
    if mime_type.startswith('image/'):
        return 'image'
    if mime_type.startswith('text/') or mime_type in _TEXT_MIME_TYPES:
        return 'text'
    return 'binary'


@core_bp.route('/api/file-content/<path:filepath>')
def get_file_content(filepath):
    """Return file content as JSON for inline preview (text) or base64 (image)."""
    import base64 as _b64
    MAX_TEXT_BYTES = 100 * 1024  # 100KB truncation limit

    if filepath.startswith('mission/'):
        parts = filepath.split('/', 2)
        if len(parts) < 3:
            return jsonify({"error": "Invalid path"}), 400
        mission_id = parts[1]
        if not _validate_mission_id(mission_id):
            return jsonify({"error": "Invalid mission_id"}), 400
        relative_path = parts[2]
        if not relative_path or not relative_path.strip():
            return jsonify({"error": "Empty file path"}), 400
        from .workspace_resolver import resolve_mission_workspace
        missions_dir = BASE_DIR / "missions"
        mission_workspace = resolve_mission_workspace(mission_id, missions_dir, WORKSPACE_DIR, io_utils)
        full_path = (mission_workspace / relative_path).resolve()
        allowed_base = mission_workspace.resolve()
    else:
        if not filepath or not filepath.strip():
            return jsonify({"error": "Empty file path"}), 400
        full_path = (WORKSPACE_DIR / filepath).resolve()
        allowed_base = WORKSPACE_DIR.resolve()

    # Containment check on resolved path — catches symlinks and traversal attempts.
    if not full_path.is_relative_to(allowed_base):
        return jsonify({"error": "Access denied"}), 403

    mime_type, _ = mimetypes.guess_type(str(full_path))
    mime_type = mime_type or 'application/octet-stream'
    file_type = _classify_file_type(mime_type)

    try:
        if file_type == 'image':
            raw = _safe_read_bytes(full_path, allowed_base=allowed_base)
            if raw is None:
                return jsonify({"error": "Access denied — symlink not allowed"}), 403
            data = _b64.b64encode(raw).decode('utf-8')
            return jsonify({
                "name": full_path.name,
                "file_type": "image",
                "mime_type": mime_type,
                "size": len(raw),
                "content": data,
                "truncated": False
            })
        elif file_type == 'text':
            raw = _safe_read_bytes(full_path, max_bytes=MAX_TEXT_BYTES + 1, allowed_base=allowed_base)
            if raw is None:
                return jsonify({"error": "Access denied — symlink not allowed"}), 403
            read_size = len(raw)
            truncated = read_size > MAX_TEXT_BYTES
            if truncated:
                raw = raw[:MAX_TEXT_BYTES]
            content = raw.decode('utf-8', errors='replace')
            return jsonify({
                "name": full_path.name,
                "file_type": "text",
                "mime_type": mime_type,
                "size": read_size if not truncated else None,
                "content": content,
                "truncated": truncated
            })
        else:
            # Single safe read — no second open to avoid TOCTOU race
            raw = _safe_read_bytes(full_path, allowed_base=allowed_base)
            if raw is None:
                return jsonify({"error": "Access denied — symlink not allowed"}), 403
            size = len(raw)
            return jsonify({
                "name": full_path.name,
                "file_type": "binary",
                "mime_type": mime_type,
                "size": size,
                "content": None,
                "truncated": False
            })
    except IOError:
        return jsonify({"error": "Internal read error"}), 500


@core_bp.route('/api/files')
def list_files():
    """List files in current mission workspace (or global workspace if no mission)."""
    cache = _get_ttl_cache()
    if cache:
        cached = cache.get('api_files')
        if cached is not None:
            return jsonify(cached)

    files = []

    # Get mission workspace if an active mission exists
    mission = io_utils.atomic_read_json(MISSION_PATH, {})
    mission_workspace = mission.get('mission_workspace')
    mission_id = mission.get('mission_id')

    # Use mission-specific workspace when available — validate containment to prevent
    # attacker-crafted mission_id from enumerating files outside the workspace root.
    if mission_workspace:
        workspace_base = Path(mission_workspace)
        if not workspace_base.resolve().is_relative_to(WORKSPACE_DIR.resolve()):
            workspace_base = WORKSPACE_DIR
    else:
        workspace_base = WORKSPACE_DIR

    # Exclusion set for directories that shouldn't appear in the files widget
    exclude_dirs = {"__pycache__", ".git", "node_modules", "atlasforge_data", ".mypy_cache", ".pytest_cache"}

    seen_paths = set()

    if workspace_base.exists():
        for f in workspace_base.rglob("*"):
            if f.is_file():
                # Skip files inside excluded directories
                if any(part in exclude_dirs for part in f.relative_to(workspace_base).parts):
                    continue
                try:
                    rel_path = f.relative_to(workspace_base)
                    path_str = str(rel_path)

                    if path_str in seen_paths:
                        continue
                    seen_paths.add(path_str)

                    stat = f.stat()
                    # Build download/content URLs with mission context if needed
                    if mission_workspace:
                        if not _validate_mission_id(mission_id):
                            logger.warning(f"list_files: invalid mission_id {mission_id!r}, skipping URL construction")
                            download_path = path_str
                        else:
                            download_path = f"mission/{mission_id}/{path_str}"
                    else:
                        download_path = path_str

                    mime_type, _ = mimetypes.guess_type(str(f))
                    mime_type = mime_type or 'application/octet-stream'
                    file_type = _classify_file_type(mime_type)

                    files.append({
                        "name": f.name,
                        "path": path_str,
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "download_url": f"/api/download/{download_path}",
                        "content_url": f"/api/file-content/{download_path}",
                        "mime_type": mime_type,
                        "file_type": file_type,
                        "mission_id": mission_id if mission_workspace else None
                    })
                except (OSError, IOError, ValueError):
                    continue

    files.sort(key=lambda x: x["modified"], reverse=True)
    result = files[:50]
    if cache:
        cache.set('api_files', result, ttl_seconds=5.0)
    return jsonify(result)


# =============================================================================
# MISSING ENDPOINT STUBS (to prevent 404 console errors)
# =============================================================================

@core_bp.route('/api/vitals/batch', methods=['POST'])
def vitals_batch():
    """
    Stub endpoint for web vitals batch reporting.
    Frontend may send performance metrics here - we accept and ignore them.
    """
    return jsonify({"status": "ok", "received": True})


@core_bp.route('/favicon.ico')
def favicon():
    """Return empty favicon to prevent 404 errors."""
    # Return a minimal 1x1 transparent ICO
    # This is a valid minimal ICO file (16x16 transparent)
    ico_data = bytes([
        0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x10, 0x10,
        0x00, 0x00, 0x01, 0x00, 0x20, 0x00, 0x68, 0x04,
        0x00, 0x00, 0x16, 0x00, 0x00, 0x00
    ])
    # Pad to make a valid icon
    ico_data += bytes(1024)  # Add padding
    from flask import Response
    return Response(ico_data[:1086], mimetype='image/x-icon')
