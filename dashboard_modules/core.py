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


def _invalidate_ttl_cache_key(key: str) -> None:
    cache = _get_ttl_cache()
    if cache:
        try:
            cache.invalidate(key)
        except Exception:
            logger.debug("TTL cache invalidate failed for %s", key, exc_info=True)


def _ensure_build_only_implementation_plan(mission: dict, problem_statement: str) -> None:
    """Create the plan artifact Build Only expects when the mission came from direct instructions."""
    if not isinstance(mission, dict):
        return
    if mission.get("mission_type") != "build_only":
        return
    workspace = mission.get("mission_workspace")
    if not workspace:
        return

    plan_path = Path(workspace) / "artifacts" / "implementation_plan.md"
    if plan_path.exists() and plan_path.stat().st_size > 0:
        return

    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        "# Build Instructions\n\n"
        "This Build Only mission was set directly from Mission Control or a mission recommendation. "
        "Use the mission statement below as the implementation plan.\n\n"
        "## Mission\n\n"
        f"{problem_statement.strip()}\n",
        encoding="utf-8",
    )

# Create Blueprint
core_bp = Blueprint('core', __name__)

# Import shared allowed modes — prevents drift between core_bp and dashboard_v2
try:
    from dashboard_v2 import _ALLOWED_MODES
except ImportError:
    _ALLOWED_MODES = {"rd", "free"}

# Valid source_type values for recommendation filtering (must match DB CHECK constraint in suggestion_storage.py)
_VALID_SOURCE_TYPES = {'drift_halt', 'successful_completion', 'manual', 'merged'}

# Valid suggestion classification values (must match _validate_row in suggestion_storage.py)
_VALID_CLASSIFICATIONS = frozenset({'BUGFIX', 'TECH_DEBT', 'COMPLETION', 'EXPANSION', 'MANUAL'})

# Valid execution_profile values (must match orchestrator profiles)
_VALID_EXECUTION_PROFILES = frozenset({
    'full_rd', 'plan_only', 'build_only', 'test_red_team',
    'bug_hunt', 'research_only', 'review_existing',
})

_CLASSIFICATION_TO_PROFILE = {
    'BUGFIX': 'bug_hunt',
    'TECH_DEBT': 'build_only',
    'EXPANSION': 'plan_only',
}

_VALID_SUGGESTION_STATUSES = frozenset({'open', 'queued', 'completed', 'deprecated', 'proposed', 'rejected'})


def _project_registry_helpers():
    from Mission_Manager.project_registry import canonicalize_project_name, project_slug, infer_project_name
    return canonicalize_project_name, project_slug, infer_project_name


def _parse_project_name(data, *, required=False, fallback_suggestion=None):
    raw = data.get("project_name")
    if raw is None:
        raw = data.get("project")
    if raw is None or raw == "":
        if required:
            raise ValueError("project_name is required for new projects")
        if fallback_suggestion is not None:
            try:
                canonicalize_project_name, project_slug, infer_project_name = _project_registry_helpers()
                inferred = infer_project_name(fallback_suggestion, _known_project_names())
                return inferred, project_slug(inferred), "inferred"
            except Exception:
                return None, None, None
        return None, None, None
    if not isinstance(raw, str):
        raise ValueError("project_name must be a string")
    canonicalize_project_name, project_slug, _ = _project_registry_helpers()
    canonical = canonicalize_project_name(raw, _known_project_names())
    if not canonical:
        if required:
            raise ValueError("project_name is required")
        return None, None, None
    return canonical, project_slug(canonical), "explicit"


def _known_project_names(status=None):
    storage = _get_suggestion_storage()
    names = []
    if storage:
        try:
            names.extend(p.get("project_name") for p in storage.get_projects(status=status))
        except Exception:
            logger.debug("Failed to load suggestion DB projects", exc_info=True)
    try:
        from Mission_Manager.project_registry import workspace_projects
        names.extend(workspace_projects())
    except Exception:
        logger.debug("Failed to load workspace projects", exc_info=True)
    cleaned = []
    seen = set()
    try:
        canonicalize_project_name, project_slug, _ = _project_registry_helpers()
        for name in names:
            canonical = canonicalize_project_name(name)
            slug = project_slug(canonical)
            if canonical and slug and slug not in seen:
                cleaned.append(canonical)
                seen.add(slug)
    except Exception:
        cleaned = [str(n) for n in names if n]
    return sorted(cleaned, key=str.lower)


def _normalize_mission_title(data, default=None):
    if "mission_title" not in data:
        raw_title = default
    else:
        raw_title = data["mission_title"]
    if raw_title is None:
        raise ValueError("mission_title cannot be null")
    if not isinstance(raw_title, str):
        raise ValueError("mission_title must be a string")
    title = raw_title.strip()
    if len(title) < 3:
        raise ValueError("Mission title must be at least 3 characters")
    return title


def _parse_suggested_cycles(data, default=3):
    if "suggested_cycles" not in data:
        return default
    raw_cycles = data["suggested_cycles"]
    if isinstance(raw_cycles, bool) or not isinstance(raw_cycles, int):
        raise ValueError("suggested_cycles must be an integer 1-10")
    if raw_cycles < 1 or raw_cycles > 10:
        raise ValueError("suggested_cycles must be an integer 1-10")
    return raw_cycles


def _optional_string_field(data, field_name, default="", none_value=""):
    raw_value = data.get(field_name, default)
    if raw_value is None:
        return none_value
    if not isinstance(raw_value, str):
        raise ValueError(f"{field_name} must be a string")
    return raw_value


def _parse_classification(data, default="EXPANSION", allow_none_default=True):
    raw = data.get("classification")
    if raw is None:
        raw = data.get("mission_classification")
    if raw is None:
        raw = data.get("mission_category")
    if raw is None:
        legacy = data.get("mission_type")
        if isinstance(legacy, str) and legacy.upper().strip() in _VALID_CLASSIFICATIONS:
            raw = legacy
    if raw is None:
        if allow_none_default:
            return default
        raise ValueError("classification cannot be null on update; omit the key to leave unchanged")
    if not isinstance(raw, str):
        raise ValueError("classification must be a string")
    normalized = raw.upper().strip()
    if normalized not in _VALID_CLASSIFICATIONS:
        raise ValueError(
            f"Invalid classification {raw!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_CLASSIFICATIONS))}"
        )
    return normalized


def _parse_execution_profile(data, default=None, allow_none_default=True):
    if "mission_type" in data:
        raw_mt = data.get("mission_type")
        if raw_mt is None:
            if allow_none_default:
                return default
            raise ValueError("mission_type cannot be null on update; omit the key to leave unchanged")
        if not isinstance(raw_mt, str):
            raise ValueError("mission_type must be a string")
        if raw_mt in _VALID_EXECUTION_PROFILES:
            return raw_mt
        if raw_mt.upper().strip() in _VALID_CLASSIFICATIONS:
            return default
        raise ValueError(
            f"Invalid mission_type {raw_mt!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_EXECUTION_PROFILES))}"
        )

    raw = data.get("execution_profile")
    if raw is None:
        if allow_none_default:
            return default
        raise ValueError("mission_type cannot be null on update; omit the key to leave unchanged")
    if raw == "" and allow_none_default:
        return default
    if not isinstance(raw, str):
        raise ValueError("execution_profile must be a string")
    if raw not in _VALID_EXECUTION_PROFILES:
        raise ValueError(
            f"Invalid execution_profile {raw!r}. "
            f"Must be one of: {', '.join(sorted(_VALID_EXECUTION_PROFILES))}"
        )
    return raw


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
get_llm_config = None

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


def _clean_llm_option(value):
    option = str(value or "").strip()
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_./:@\[\]-]{0,79}$", option):
        return option
    return None


def _coerce_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _update_active_mission_provider(provider, model=None, thinking=None, fast=None):
    """Keep mission metadata aligned with the model selection used for start/resume."""
    if not provider or not io_utils or not MISSION_PATH:
        return
    try:
        mission = io_utils.atomic_read_json(MISSION_PATH, {}) or {}
        if not isinstance(mission, dict) or not mission:
            return
        mission["llm_provider"] = provider
        cleaned_model = _clean_llm_option(model)
        cleaned_thinking = _clean_llm_option(thinking)
        if cleaned_model:
            mission["llm_model"] = cleaned_model
        if cleaned_thinking:
            mission["llm_thinking"] = cleaned_thinking
        if provider == "codex" and fast is not None:
            mission["llm_fast"] = _coerce_bool(fast)
        mission["last_updated"] = datetime.now(timezone.utc).isoformat()
        io_utils.atomic_write_json(MISSION_PATH, mission)
    except Exception:
        logger.exception("Failed to update active mission provider")


def init_core_blueprint(
    base_dir, state_dir, workspace_dir,
    mission_path, proposals_path, recommendations_path,
    io_utils_module,
    status_fn, start_fn, stop_fn, send_msg_fn, journal_fn,
    get_provider_fn=None, set_provider_fn=None, get_provider_config_fn=None,
    narrative_status_fn=None, narrative_start_fn=None, narrative_stop_fn=None,
    narrative_send_msg_fn=None, narrative_chat_fn=None, narrative_mission_path=None,
    mission_queue_path=None
):
    """Initialize the core blueprint with required dependencies."""
    global BASE_DIR, STATE_DIR, WORKSPACE_DIR, MISSION_PATH, PROPOSALS_PATH, RECOMMENDATIONS_PATH
    global MISSION_LOGS_DIR, io_utils, MISSION_QUEUE_PATH
    global get_claude_status, start_claude, stop_claude, send_message_to_claude, get_recent_journal
    global get_llm_provider, set_llm_provider, get_llm_config
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
    get_llm_config = get_provider_config_fn

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
        # Mission-type profile fields (cycle 2). Defaults match pre-profile
        # missions; populated from mission.json below when available.
        "mission_type": "full_rd",
        "mission_type_label": "Full R&D",
        "enabled_stages": [],
        "stop_after_profile_complete": False,
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

        # Mission-type profile surfacing — read straight from mission.json so
        # the field appears even if StateManager hasn't been extended yet.
        try:
            import io_utils as _io_utils
            _mission_doc = _io_utils.atomic_read_json(MISSION_PATH, {}) or {}
            engine_data["mission_type"] = _mission_doc.get("mission_type", "full_rd") or "full_rd"
            engine_data["mission_type_label"] = (
                _mission_doc.get("mission_type_label") or "Full R&D"
            )
            _es = _mission_doc.get("enabled_stages") or []
            engine_data["enabled_stages"] = list(_es) if isinstance(_es, list) else []
            engine_data["stop_after_profile_complete"] = bool(
                _mission_doc.get("stop_after_profile_complete", False)
            )
        except Exception:
            logger.debug("api_engine_status: mission_type read failed", exc_info=True)
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
            set_llm_provider(
                normalized_provider,
                model=data.get("model"),
                thinking=data.get("thinking"),
                fast=data.get("fast"),
            )
        _update_active_mission_provider(
            normalized_provider,
            model=data.get("model"),
            thinking=data.get("thinking"),
            fast=data.get("fast"),
        )
    success, message = start_claude(mode)
    return jsonify({"success": success, "message": message, "provider": normalized_provider})


@core_bp.route('/api/stop', methods=['POST'])
def api_stop():
    success, message = stop_claude()
    if success:
        try:
            from suggestion_lifecycle import mark_active_suggestion_open_if_incomplete
            mark_active_suggestion_open_if_incomplete()
        except Exception:
            logger.warning("Failed to restore active suggestion status on stop", exc_info=True)
    return jsonify({"success": success, "message": message})


@core_bp.route('/api/llm-provider', methods=['GET', 'POST'])
def api_llm_provider():
    """Get or set the active LLM provider for AtlasForge starts."""
    if request.method == 'GET':
        if get_llm_config:
            config = get_llm_config()
            config["supported"] = ["claude", "codex", "gemini"]
            return jsonify(config)
        provider = get_llm_provider() if get_llm_provider else "claude"
        return jsonify({"provider": provider, "supported": ["claude", "codex", "gemini"]})

    if not set_llm_provider:
        return jsonify({"success": False, "message": "Provider configuration unavailable"}), 503

    data = request.get_json(silent=True) or {}
    provider = data.get("provider", "")
    if not provider:
        return jsonify({"success": False, "message": "Missing provider"}), 400

    normalized = set_llm_provider(
        provider,
        model=data.get("model"),
        thinking=data.get("thinking"),
        options=data.get("options"),
        fast=data.get("fast"),
    )
    _update_active_mission_provider(
        normalized,
        model=data.get("model"),
        thinking=data.get("thinking"),
        fast=data.get("fast"),
    )
    config = get_llm_config() if get_llm_config else {"provider": normalized}
    return jsonify({
        "success": True,
        "provider": normalized,
        "selected": config.get("selected"),
        "options": config.get("options"),
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
        if story_genre is not None and not isinstance(story_genre, str):
            return jsonify({"success": False, "message": "story_genre must be a string"}), 400
        if story_logline is not None and not isinstance(story_logline, str):
            return jsonify({"success": False, "message": "story_logline must be a string"}), 400

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

        # Validate mission_type early (must be a known profile key or absent)
        try:
            from af_engine.mission_profiles import is_valid_mission_type
            mt_submitted = data.get('mission_type')
            if mt_submitted is not None and not is_valid_mission_type(mt_submitted):
                return jsonify({
                    "success": False,
                    "message": f"Invalid mission_type: {mt_submitted!r}",
                }), 400
        except ImportError:
            pass

        raw_cb = data.get('cycle_budget')
        _logging.getLogger(__name__).info(f"[MISSION] cycle_budget submitted={raw_cb!r}")

        problem_statement = data.get('problem_statement') or data.get('mission', '')
        user_project_name = data.get('project_name')

        if problem_statement:
            mission_id = f"mission_{uuid.uuid4().hex[:8]}"
            missions_dir = BASE_DIR / "missions"
            mission_dir = missions_dir / mission_id
            try:
                mission_dir.resolve().relative_to(missions_dir.resolve())
            except ValueError:
                return jsonify({"success": False, "error": "Invalid mission path"}), 400

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

            active_provider = (
                _normalize_runtime_provider(data.get("llm_provider") or data.get("provider"))
                or (get_llm_provider() if get_llm_provider else "claude")
            )
            active_model = _clean_llm_option(data.get("llm_model") or data.get("model"))
            active_thinking = _clean_llm_option(data.get("llm_thinking") or data.get("thinking"))
            active_fast = (
                _coerce_bool(data.get("llm_fast")) if "llm_fast" in data else _coerce_bool(data.get("fast"))
            ) if active_provider == "codex" else False
            if _mc_available:
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
                if active_provider:
                    new_mission["llm_provider"] = active_provider
                if active_model:
                    new_mission["llm_model"] = active_model
                if active_thinking:
                    new_mission["llm_thinking"] = active_thinking
                if active_provider == "codex":
                    new_mission["llm_fast"] = active_fast
                _ensure_build_only_implementation_plan(new_mission, problem_statement)
                io_utils.atomic_write_json(MISSION_PATH, new_mission)
                _invalidate_ttl_cache_key('api_status')
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
                if active_model:
                    new_mission["llm_model"] = active_model
                if active_thinking:
                    new_mission["llm_thinking"] = active_thinking
                if active_provider == "codex":
                    new_mission["llm_fast"] = active_fast
                # Apply mission type profile (sets current_stage, enabled_stages,
                # stop_after_profile_complete, mission_profile, mission_type_label).
                try:
                    from af_engine.mission_profiles import apply_mission_type_profile
                    apply_mission_type_profile(new_mission, data.get('mission_type'))
                except ImportError:
                    pass
                _ensure_build_only_implementation_plan(new_mission, problem_statement)
                io_utils.atomic_write_json(MISSION_PATH, new_mission)
                _invalidate_ttl_cache_key('api_status')
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
        try:
            _, project_slug, infer_project_name = _project_registry_helpers()
            known = _known_project_names(status="open")
            inferred = infer_project_name({
                "mission_title": problem_statement,
                "mission_description": problem_statement,
            }, known_projects=known)
            result["suggested_name"] = inferred
            result["suggested_slug"] = project_slug(inferred)
            result["existing_projects"] = known
        except Exception:
            pass
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
    status_filter = request.args.get('status', 'open')
    project_filter = request.args.get('project') or request.args.get('project_name')

    if source_type_filter and source_type_filter not in _VALID_SOURCE_TYPES:
        return jsonify({"items": [], "error": "Invalid source_type"}), 400
    if status_filter != 'all' and status_filter not in _VALID_SUGGESTION_STATUSES:
        return jsonify({"items": [], "error": "Invalid status"}), 400

    try:
        try:
            from suggestion_lifecycle import reconcile_suggestion_statuses
            reconcile_suggestion_statuses(storage)
        except Exception:
            logger.debug("recommendations status reconciliation failed", exc_info=True)

        effective_status = None if status_filter == 'all' else status_filter
        project_slug_value = None
        if project_filter:
            try:
                _, project_slug, _ = _project_registry_helpers()
                project_slug_value = project_slug(project_filter)
            except Exception:
                project_slug_value = None
        if source_type_filter or effective_status or project_slug_value:
            items = storage.get_filtered(
                source_type=source_type_filter,
                status=effective_status,
                project_slug=project_slug_value,
            )
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
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object"}), 400
    import uuid

    raw_source_type = data.get("source_type", "manual")
    if not isinstance(raw_source_type, str):
        return jsonify({"success": False, "error": "source_type must be a string"}), 400
    if raw_source_type not in _VALID_SOURCE_TYPES:
        return jsonify({"success": False, "error": "Invalid source_type"}), 400
    source_type = raw_source_type

    try:
        mission_title = _normalize_mission_title(data, default="Untitled Mission")
        suggested_cycles = _parse_suggested_cycles(data, default=3)
        raw_desc = _optional_string_field(data, "mission_description", default="", none_value="")
        raw_rationale = _optional_string_field(data, "rationale", default="", none_value="")
        source_mission_id = _optional_string_field(data, "source_mission_id", default=None, none_value=None)
        source_mission_summary = _optional_string_field(data, "source_mission_summary", default="", none_value="")
        project_name, project_slug_value, project_source = _parse_project_name(
            data,
            required=bool(data.get("new_project")),
            fallback_suggestion={
                "mission_title": mission_title,
                "mission_description": raw_desc,
                "rationale": raw_rationale,
                "source_mission_summary": source_mission_summary,
            },
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    try:
        classification = _parse_classification(data, default="EXPANSION")
        execution_profile = _parse_execution_profile(
            data,
            default=_CLASSIFICATION_TO_PROFILE.get(classification, "full_rd"),
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    recommendation = {
        "id": f"rec_{uuid.uuid4().hex[:8]}",
        "mission_title": mission_title,
        "mission_description": raw_desc,
        "suggested_cycles": suggested_cycles,
        "source_mission_id": source_mission_id,
        "source_mission_summary": source_mission_summary,
        "rationale": raw_rationale,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_type": source_type,
        "classification": classification,
        "mission_type": execution_profile,
        "execution_profile": execution_profile,
        "status": "open",
    }
    if project_name:
        recommendation["project_name"] = project_name
        recommendation["project_slug"] = project_slug_value
        recommendation["project_source"] = project_source

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
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
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
            try:
                from suggestion_lifecycle import reconcile_suggestion_statuses
                reconcile_suggestion_statuses(storage)
            except Exception:
                logger.debug("merge-candidates status reconciliation failed", exc_info=True)
            items = storage.get_filtered(status="open")
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
            "created_at": item.get("created_at"),
            "source_type": item.get("source_type", ""),
            "classification": item.get("classification", "EXPANSION"),
            "mission_type": item.get("mission_type", ""),
            "execution_profile": item.get("execution_profile", ""),
            "project_name": item.get("project_name", ""),
            "project_slug": item.get("project_slug", ""),
            "project_source": item.get("project_source", ""),
            "health_status": item.get("health_status", ""),
            "status": item.get("status", "open"),
            "accepted_mission_id": item.get("accepted_mission_id"),
            "queued_at": item.get("queued_at"),
            "completed_at": item.get("completed_at"),
            "reopened_at": item.get("reopened_at"),
            "closed_reason": item.get("closed_reason"),
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
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object"}), 400
    source_ids = data.get("source_ids", [])
    merged_data = data.get("merged_data", {})
    if not isinstance(merged_data, dict):
        return jsonify({"success": False, "error": "merged_data must be an object"}), 400
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
            _merged_title = _normalize_mission_title(merged_data, default="Merged Suggestion")
            _merged_cycles = _parse_suggested_cycles(merged_data, default=3)
            _merged_desc = _optional_string_field(merged_data, "mission_description", default="", none_value="")
            _merged_rationale = _optional_string_field(merged_data, "rationale", default="", none_value="")
            _merged_project, _merged_project_slug, _merged_project_source = _parse_project_name(
                merged_data,
                fallback_suggestion={
                    "mission_title": _merged_title,
                    "mission_description": _merged_desc,
                    "rationale": _merged_rationale,
                },
            )
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        _raw_merge_profile = merged_data.get("execution_profile", "full_rd")
        if not isinstance(_raw_merge_profile, str):
            return jsonify({"success": False, "error": "execution_profile must be a string"}), 400
        if _raw_merge_profile not in _VALID_EXECUTION_PROFILES:
            return jsonify({"success": False, "error": "Invalid execution_profile"}), 400
        try:
            _merged_classification = _parse_classification(merged_data, default="EXPANSION")
        except ValueError as exc:
            return jsonify({"success": False, "error": str(exc)}), 400
        new_rec = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": _merged_title,
            "mission_description": _merged_desc,
            "suggested_cycles": _merged_cycles,
            "rationale": _merged_rationale,
            "source_type": "merged",
            "merged_from": source_ids,
            "merged_source_descriptions": source_descriptions,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "classification": _merged_classification,
            "mission_type": _raw_merge_profile,
            "execution_profile": _raw_merge_profile,
        }
        if _merged_project:
            new_rec["project_name"] = _merged_project
            new_rec["project_slug"] = _merged_project_slug
            new_rec["project_source"] = _merged_project_source or "merged"

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
    Return analyzed recommendations for the widget.

    The default path is intentionally read-only and fast: it returns already
    stored analyzer fields for open suggestions. Use ?refresh=1 for the
    expensive full analyzer pass that recomputes tags, priorities, health, and
    writes analyzer metadata back to SQLite.

    Returns:
        JSON with items sorted by priority, health_report, and total count
    """
    try:
        storage = _get_suggestion_storage()
        if storage:
            try:
                from suggestion_lifecycle import reconcile_suggestion_statuses
                reconcile_suggestion_statuses(storage)
            except Exception:
                logger.debug("analyze status reconciliation failed", exc_info=True)
        project_filter = request.args.get("project") or request.args.get("project_name")

        refresh = str(request.args.get("refresh", "")).strip().lower() in {"1", "true", "yes", "on"}
        if refresh:
            from suggestion_analyzer import get_analyzer
            analyzer = get_analyzer()
            result = analyzer.analyze_all(persist=True)
            all_items = result.get("items", [])
            visible_items = [
                item for item in all_items
                if item.get("status", "open") == "open"
            ]
        elif storage:
            project_slug_value = None
            if project_filter:
                try:
                    _, project_slug, _ = _project_registry_helpers()
                    project_slug_value = project_slug(project_filter)
                except Exception:
                    project_slug_value = None
            visible_items = storage.get_filtered(status="open", project_slug=project_slug_value)
            all_items = storage.get_all()
            health_counts = {
                'healthy': 0,
                'stale': 0,
                'orphaned': 0,
                'needs_review': 0,
                'hot': 0,
            }
            for item in visible_items:
                status = item.get('health_status', 'healthy')
                health_counts[status] = health_counts.get(status, 0) + 1
            result = {
                "items": visible_items,
                "health_report": health_counts,
                "total": len(visible_items),
            }
        else:
            recommendations = io_utils.atomic_read_json(RECOMMENDATIONS_PATH, {"items": []})
            all_items = recommendations.get("items", [])
            visible_items = [
                item for item in all_items
                if item.get("status", "open") == "open"
            ]
            result = {
                "items": visible_items,
                "health_report": {},
                "total": len(visible_items),
            }

        if project_filter and refresh:
            try:
                _, project_slug, _ = _project_registry_helpers()
                wanted = project_slug(project_filter)
                visible_items = [item for item in visible_items if item.get("project_slug") == wanted]
            except Exception:
                pass
        result = dict(result)
        result["items"] = visible_items
        result["total"] = len(visible_items)
        result["all_total"] = len(all_items)
        result["refreshed"] = refresh
        return jsonify(result)
    except Exception:
        logger.exception("analyze_recommendations failed")
        return jsonify({"error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/projects', methods=['GET'])
def api_recommendation_projects():
    """Return project options represented in the mission suggestion DB."""
    storage = _get_suggestion_storage()
    if not storage:
        return jsonify({"projects": [], "items": []}), 503
    status_filter = request.args.get('status', 'open')
    if status_filter != 'all' and status_filter not in _VALID_SUGGESTION_STATUSES:
        return jsonify({"projects": [], "items": [], "error": "Invalid status"}), 400
    try:
        effective_status = None if status_filter == 'all' else status_filter
        projects = storage.get_projects(status=effective_status)
        return jsonify({
            "projects": [p["project_name"] for p in projects],
            "items": projects,
        })
    except Exception:
        logger.exception("recommendation projects failed")
        return jsonify({"projects": [], "items": [], "error": "An internal error occurred"}), 500


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
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object"}), 400

    try:
        if "suggested_cycles" in data:
            _parse_suggested_cycles(data)
        if "mission_title" in data:
            _normalize_mission_title(data)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    try:
        # Get current record to preserve originals
        current = storage.get_by_id(rec_id)
        if not current:
            return jsonify({"success": False, "error": "Recommendation not found"}), 404

        updates = {}
        # Preserve originals if first edit
        original_fields = (
            "original_mission_title",
            "original_mission_description",
            "original_rationale",
            "original_suggested_cycles",
        )
        if all(current.get(field) is None for field in original_fields):
            updates["original_mission_title"] = current.get("mission_title")
            updates["original_mission_description"] = current.get("mission_description")
            updates["original_rationale"] = current.get("rationale")
            updates["original_suggested_cycles"] = current.get("suggested_cycles")
        # Update fields
        if "mission_title" in data:
            updates["mission_title"] = _normalize_mission_title(data)
        if "mission_description" in data:
            val = data["mission_description"]
            if val is None:
                val = ""
            elif not isinstance(val, str):
                return jsonify({
                    "success": False,
                    "error": "mission_description must be a string"
                }), 400
            updates["mission_description"] = val
        if "suggested_cycles" in data and data["suggested_cycles"] is not None:
            updates["suggested_cycles"] = _parse_suggested_cycles(data)
        if "rationale" in data:
            val = data["rationale"]
            if val is None:
                val = ""
            elif not isinstance(val, str):
                return jsonify({
                    "success": False,
                    "error": "rationale must be a string"
                }), 400
            updates["rationale"] = val
        if "execution_profile" in data:
            raw_ep = data["execution_profile"]
            if raw_ep is None:
                return jsonify({
                    "success": False,
                    "error": "execution_profile cannot be null on update"
                }), 400
            if not isinstance(raw_ep, str):
                return jsonify({
                    "success": False,
                    "error": "execution_profile must be a string"
                }), 400
            if raw_ep not in _VALID_EXECUTION_PROFILES:
                return jsonify({
                    "success": False,
                    "error": (
                        f"Invalid execution_profile {raw_ep!r}. "
                        f"Must be one of: {', '.join(sorted(_VALID_EXECUTION_PROFILES))}"
                    )
                }), 400
            updates["execution_profile"] = raw_ep
            updates["mission_type"] = raw_ep
        if any(key in data for key in ("classification", "mission_classification", "mission_category")):
            try:
                updates["classification"] = _parse_classification(data, allow_none_default=False)
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
        if "mission_type" in data:
            raw_mt = data["mission_type"]
            if raw_mt is None:
                return jsonify({
                    "success": False,
                    "error": "mission_type cannot be null on update; omit the key to leave unchanged"
                }), 400
            if not isinstance(raw_mt, str):
                return jsonify({
                    "success": False,
                    "error": "mission_type must be a string"
                }), 400
            legacy_classification = raw_mt.upper().strip()
            if legacy_classification in _VALID_CLASSIFICATIONS:
                updates["classification"] = legacy_classification
            elif raw_mt in _VALID_EXECUTION_PROFILES:
                updates["mission_type"] = raw_mt
                updates["execution_profile"] = raw_mt
            else:
                return jsonify({
                    "success": False,
                    "error": (
                        f"Invalid mission_type {raw_mt!r}. "
                        f"Must be one of: {', '.join(sorted(_VALID_EXECUTION_PROFILES))}"
                    )
                }), 400
        if "status" in data:
            raw_status = data["status"]
            if not isinstance(raw_status, str) or raw_status not in _VALID_SUGGESTION_STATUSES:
                return jsonify({
                    "success": False,
                    "error": (
                        f"Invalid status {raw_status!r}. "
                        f"Must be one of: {', '.join(sorted(_VALID_SUGGESTION_STATUSES))}"
                    )
                }), 400
            updates["status"] = raw_status
        if "project_name" in data or "project" in data:
            try:
                project_name, project_slug_value, project_source = _parse_project_name(data, required=True)
            except ValueError as exc:
                return jsonify({"success": False, "error": str(exc)}), 400
            updates["project_name"] = project_name
            updates["project_slug"] = project_slug_value
            updates["project_source"] = project_source
        updates["last_edited_at"] = datetime.now(timezone.utc).isoformat()
        if not storage.update(rec_id, updates):
            return jsonify({"success": False, "error": "Recommendation not found"}), 404
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        logger.exception("SQLite update failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500


@core_bp.route('/api/recommendations/<rec_id>/set-mission', methods=['POST'])
def api_set_mission_from_recommendation(rec_id):
    """Set a mission from a recommendation and mark it queued.

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
    _raw_pn = data.get("project_name")
    user_project_name = _raw_pn if isinstance(_raw_pn, str) and _raw_pn else None

    # Accept either `execution_profile` (canonical) or `mission_type` (alias).
    # Treat None/empty/non-string in the canonical key as "fall through to alias"
    # so callers sending `false` or `""` still get the intended profile.
    _raw_exec = data.get("execution_profile")
    if not isinstance(_raw_exec, str) or not _raw_exec:
        _raw_exec = data.get("mission_type")
    if not isinstance(_raw_exec, str) or not _raw_exec:
        _raw_exec = "full_rd"
    execution_profile = _raw_exec if _raw_exec in _VALID_EXECUTION_PROFILES else "full_rd"

    try:
        try:
            from suggestion_lifecycle import reconcile_suggestion_statuses
            reconcile_suggestion_statuses(storage)
        except Exception:
            logger.debug("set-mission status reconciliation failed", exc_info=True)
        target_rec = storage.get_by_id(rec_id)
    except Exception:
        import logging
        logging.exception("SQLite get failed")
        return jsonify({"success": False, "error": "An internal error occurred"}), 500

    if not target_rec:
        return jsonify({"success": False, "error": "Recommendation not found"}), 404
    if target_rec.get("status", "open") != "open":
        return jsonify({
            "success": False,
            "error": f"Recommendation is already {target_rec.get('status')}"
        }), 409
    requires_build_review = bool(target_rec.get("requires_user_build_approval"))
    build_approval_action = None
    build_review_notes = ""
    if requires_build_review:
        raw_action = data.get("build_approval_action")
        build_approval_action = raw_action.strip().lower() if isinstance(raw_action, str) else ""
        if build_approval_action not in {"approve", "review"}:
            return jsonify({
                "success": False,
                "error": "Build plan requires explicit approval or review with modification"
            }), 400
        if build_approval_action == "review":
            raw_notes = data.get("build_review_notes")
            build_review_notes = raw_notes.strip() if isinstance(raw_notes, str) else ""
            if not build_review_notes:
                return jsonify({
                    "success": False,
                    "error": "Review with modification requires user instructions"
                }), 400
            execution_profile = "plan_only"
    if not user_project_name:
        user_project_name = target_rec.get("project_name") or None

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
    if requires_build_review:
        source_plan_path = target_rec.get("source_plan_path")
        if build_approval_action == "approve":
            problem_statement = (
                f"{problem_statement}\n\n"
                "## Build Approval\n"
                "The user explicitly approved this build plan for implementation."
            ).strip()
        elif build_approval_action == "review":
            problem_statement = (
                f"{problem_statement}\n\n"
                "## User Plan Review Request\n"
                f"{build_review_notes}\n\n"
                "Revise and expand the implementation plan. Do not build code yet."
            ).strip()
        if source_plan_path:
            problem_statement = f"{problem_statement}\n\nSource implementation plan: {source_plan_path}".strip()

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
    try:
        mission_dir.resolve().relative_to(missions_dir.resolve())
    except ValueError:
        return jsonify({"success": False, "error": "Invalid mission path"}), 400

    resolved_project_name = None
    try:
        from project_name_resolver import resolve_project_name
        resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
        mission_workspace = WORKSPACE_DIR / resolved_project_name / mission_id
    except ImportError:
        mission_workspace = mission_dir / "workspace"
    except Exception as _e:
        _rlog.warning("resolve_project_name failed: %s; falling back to mission_dir/workspace", _e)
        mission_workspace = mission_dir / "workspace"

    try:
        mission_dir.mkdir(parents=True, exist_ok=True)
        (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)
    except Exception as _e:
        _rlog.exception("Failed to create mission directories")
        try:
            import shutil as _shutil
            if mission_dir.exists():
                _shutil.rmtree(mission_dir, ignore_errors=True)
        except Exception:
            pass
        return jsonify({
            "success": False,
            "error": "Failed to create mission workspace",
        }), 500

    active_provider = (
        _normalize_runtime_provider(data.get("llm_provider") or data.get("provider"))
        or (get_llm_provider() if get_llm_provider else "claude")
    )
    active_model = _clean_llm_option(data.get("llm_model") or data.get("model"))
    active_thinking = _clean_llm_option(data.get("llm_thinking") or data.get("thinking"))
    active_fast = (
        _coerce_bool(data.get("llm_fast")) if "llm_fast" in data else _coerce_bool(data.get("fast"))
    ) if active_provider == "codex" else False
    if _mcr_ok:
        _req_r = dict(data)
        _req_r["problem_statement"] = problem_statement
        # execution_profile from the suggestion modal always takes precedence as mission_type
        _req_r["mission_type"] = execution_profile
        if resolved_project_name:
            # The modal may send a human display name ("Stick Figure Fighter").
            # MissionConfig validates filesystem-safe project ids, so pass the
            # resolver output used for the workspace path.
            _req_r["project_name"] = resolved_project_name
        if active_provider and "llm_provider" not in _req_r:
            _req_r["llm_provider"] = active_provider
        _cfg_r, _aud_r = _MCR.from_request(_req_r, mission_id=mission_id)
        new_mission = _cfg_r.to_mission_dict(
            mission_id=mission_id, mission_workspace=mission_workspace,
            mission_dir=mission_dir, resolved_project_name=resolved_project_name,
            source_recommendation_id=rec_id, audit=_aud_r,
        )
        if active_provider:
            new_mission["llm_provider"] = active_provider
        if active_model:
            new_mission["llm_model"] = active_model
        if active_thinking:
            new_mission["llm_thinking"] = active_thinking
        if active_provider == "codex":
            new_mission["llm_fast"] = active_fast
        _ensure_build_only_implementation_plan(new_mission, problem_statement)
        io_utils.atomic_write_json(MISSION_PATH, new_mission)
        _invalidate_ttl_cache_key('api_status')
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
            "llm_provider": active_provider,
            "source_recommendation_id": rec_id
        }
        if active_model:
            new_mission["llm_model"] = active_model
        if active_thinking:
            new_mission["llm_thinking"] = active_thinking
        if active_provider == "codex":
            new_mission["llm_fast"] = active_fast
        io_utils.atomic_write_json(MISSION_PATH, new_mission)
        _invalidate_ttl_cache_key('api_status')
        applied_cycle_budget = cycle_budget

    try:
        from mission_analytics import get_analytics
        get_analytics().start_mission(mission_id, problem_statement)
    except Exception:
        _rlog.warning("Analytics: Failed to register mission", exc_info=True)

    try:
        from suggestion_lifecycle import mark_suggestion_status
        if requires_build_review:
            try:
                storage.update(rec_id, {
                    "build_approval_status": "approved" if build_approval_action == "approve" else "review_requested",
                    "build_review_notes": build_review_notes or None,
                    "last_edited_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                _rlog.warning("SQLite build approval update failed", exc_info=True)
        mark_suggestion_status(
            rec_id,
            "queued",
            storage,
            mission_id=mission_id,
            closed_reason="mission_control",
        )
        if resolved_project_name:
            try:
                from Mission_Manager.project_registry import canonicalize_project_name, project_slug
                canonical_project = canonicalize_project_name(resolved_project_name, _known_project_names())
                storage.update(rec_id, {
                    "project_name": canonical_project,
                    "project_slug": project_slug(canonical_project),
                    "project_source": "explicit" if user_project_name else "inferred",
                })
            except Exception:
                pass
    except Exception:
        _rlog.warning("SQLite status update failed", exc_info=True)

    response_msg = f"Mission set with {applied_cycle_budget} cycle(s)."
    if resolved_project_name:
        response_msg += f" Project: {resolved_project_name}."
    response_msg += " Click 'Start Mission' to begin."

    return jsonify({
        "success": True,
        "message": response_msg,
        "mission_id": mission_id,
        "mission_workspace": str(mission_workspace),
        "project_name": resolved_project_name,
        "mission": {
            "mission_id": mission_id,
            "problem_statement": new_mission.get("problem_statement"),
            "original_problem_statement": new_mission.get("original_problem_statement"),
            "cycle_budget": new_mission.get("cycle_budget"),
            "current_cycle": new_mission.get("current_cycle"),
            "current_stage": new_mission.get("current_stage"),
            "max_iterations": new_mission.get("max_iterations"),
            "mission_type": new_mission.get("mission_type") or execution_profile,
            "mission_type_label": new_mission.get("mission_type_label"),
            "project_name": resolved_project_name or new_mission.get("project_name"),
            "mission_workspace": str(mission_workspace),
            "source_recommendation_id": rec_id,
        },
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

    # The resolved containment check above is authoritative. O_NOFOLLOW in
    # _safe_read_bytes still protects against a final-component symlink swap.
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
        active_mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_data = active_mission if active_mission.get("mission_id") == mission_id else None
        mission_workspace = resolve_mission_workspace(mission_id, missions_dir, WORKSPACE_DIR, io_utils, mission_data=mission_data)
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
