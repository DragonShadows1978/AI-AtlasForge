"""
dashboard_modules.mission_params - Mission Parameter Display Blueprint

Provides API endpoints for inspecting the active mission's parameters and
audit history. Integrates with the WebSocket widget system for real-time
parameter display on the dashboard.

Routes:
  GET /api/mission/parameters                  - Active mission params + audit summary
  GET /api/mission/parameter-audit             - Full audit log for current mission
  GET /api/mission/parameter-audit/<mission_id> - Audit log for specific mission
  GET /api/mission/parameter-audit/history     - Cross-mission trend (last N missions)
"""

import json
import time
from flask import Blueprint, jsonify, request
from pathlib import Path

mission_params_bp = Blueprint('mission_params', __name__)

# Dependencies injected via init function
_MISSION_PATH = None
_MISSIONS_DIR = None
_io_utils = None

# ---------------------------------------------------------------------------
# History cache (30s TTL — avoids scanning 363+ dirs on every request)
# ---------------------------------------------------------------------------
_HISTORY_CACHE: dict = {"data": None, "ts": 0.0}
HISTORY_CACHE_TTL: float = 30.0


def init_mission_params_blueprint(mission_path, missions_dir, io_utils_module):
    """Initialize the blueprint with required dependencies."""
    global _MISSION_PATH, _MISSIONS_DIR, _io_utils
    _MISSION_PATH = Path(mission_path)
    _MISSIONS_DIR = Path(missions_dir)
    _io_utils = io_utils_module


def get_mission_params() -> dict:
    """Get active mission parameters and audit summary.

    This function is also called directly by dashboard_v2.py for WebSocket pushes.
    Returns a dict suitable for JSON serialisation.
    """
    try:
        if _io_utils:
            mission = _io_utils.atomic_read_json(_MISSION_PATH, {})
        else:
            if _MISSION_PATH and _MISSION_PATH.exists():
                with open(_MISSION_PATH) as f:
                    mission = json.load(f)
            else:
                mission = {}

        mission_id = mission.get("mission_id")
        if not mission_id:
            return {
                "mission_id": None,
                "parameters": {},
                "audit_summary": None,
                "health": "unknown",
                "timestamp": _now_iso(),
            }

        params = {
            "cycle_budget": mission.get("cycle_budget"),
            "max_iterations": mission.get("max_iterations"),
            "llm_provider": mission.get("llm_provider"),
            "current_cycle": mission.get("current_cycle", 1),
            "current_stage": mission.get("current_stage"),
            "project_name": mission.get("project_name"),
        }

        # Use embedded audit summary if present, else load from file
        audit_summary = mission.get("parameter_audit_summary")
        if audit_summary is None:
            audit_summary = _load_audit_summary(mission_id)

        # Compute health from cross-mission history
        history = _get_cached_history(limit=20)
        health = _compute_param_health(history)

        return {
            "mission_id": mission_id,
            "parameters": params,
            "audit_summary": audit_summary,
            "health": health,
            "timestamp": _now_iso(),
        }
    except Exception as e:
        return {
            "mission_id": None,
            "parameters": {},
            "audit_summary": None,
            "health": "unknown",
            "error": str(e),
            "timestamp": _now_iso(),
        }


def _load_audit_summary(mission_id: str):
    """Load just the audit summary fields from parameter_audit.json."""
    try:
        from af_engine.mission_config import load_audit_log
        if _MISSIONS_DIR:
            mission_dir = _MISSIONS_DIR / mission_id
            audit = load_audit_log(mission_dir)
            if audit:
                return {
                    "is_valid": audit.get("is_valid", True),
                    "override_count": audit.get("override_count", 0),
                    "overrides": audit.get("overrides", []),
                    "submitted_at": audit.get("timestamp"),
                    "validation_errors": audit.get("validation_errors", []),
                }
    except Exception:
        pass
    return None


def _load_full_audit(mission_id: str):
    """Load full audit record for a mission."""
    try:
        from af_engine.mission_config import load_audit_log
        if _MISSIONS_DIR:
            return load_audit_log(_MISSIONS_DIR / mission_id)
    except Exception:
        pass
    return None


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Cross-mission history helpers
# ---------------------------------------------------------------------------

def _build_history() -> list:
    """Scan missions dir and build sorted history list (oldest first).

    Only reads mission_config.json — does not load full audit files unless
    needed for override_count (checks parameter_audit.json existence).
    """
    from af_engine.mission_config import migrate_config, load_audit_log

    if not _MISSIONS_DIR or not _MISSIONS_DIR.exists():
        return []

    entries = []
    for entry in _MISSIONS_DIR.iterdir():
        if not entry.is_dir():
            continue
        config_file = entry / "mission_config.json"
        if not config_file.exists():
            continue
        try:
            with open(config_file) as f:
                raw = json.load(f)
            cfg = migrate_config(raw)

            mission_id = cfg.get("mission_id", entry.name)
            created_at = cfg.get("created_at", "")

            # Check audit existence + load override_count
            audit_path = entry / "parameter_audit.json"
            has_audit = audit_path.exists()
            override_count = 0
            is_valid = True
            if has_audit:
                audit = load_audit_log(entry)
                if audit:
                    override_count = audit.get("override_count", 0)
                    is_valid = audit.get("is_valid", True)

            entries.append({
                "mission_id": mission_id,
                "created_at": created_at,
                "cycle_budget": cfg.get("cycle_budget"),
                "max_iterations": cfg.get("max_iterations"),
                "llm_provider": cfg.get("llm_provider"),
                "config_version": cfg.get("config_version", 0),
                "has_audit": has_audit,
                "override_count": override_count,
                "is_valid": is_valid,
            })
        except Exception:
            continue

    # Sort oldest-first (empty created_at sorts last)
    entries.sort(key=lambda x: x.get("created_at") or "")
    return entries


def _get_cached_history(limit: int = 20) -> list:
    """Return cached history, rebuilding if TTL expired."""
    now = time.time()
    if _HISTORY_CACHE["data"] is not None and now - _HISTORY_CACHE["ts"] < HISTORY_CACHE_TTL:
        return _HISTORY_CACHE["data"][:limit]
    data = _build_history()
    _HISTORY_CACHE["data"] = data
    _HISTORY_CACHE["ts"] = now
    return data[:limit]


def _compute_param_health(history: list) -> str:
    """Compute parameter health indicator from last 10 audited missions.

    Returns:
        'green'   - No overrides in last 10 audited missions
        'yellow'  - 1-3 override events
        'red'     - 4+ overrides or any invalid submission
        'unknown' - No audited missions yet
    """
    recent = [h for h in history if h.get("has_audit")][-10:]
    if not recent:
        return "unknown"
    override_total = sum(h.get("override_count", 0) for h in recent)
    any_invalid = any(not h.get("is_valid", True) for h in recent)
    if any_invalid or override_total >= 4:
        return "red"
    elif override_total >= 1:
        return "yellow"
    return "green"


# =============================================================================
# Routes
# =============================================================================

@mission_params_bp.route('/api/mission/parameters')
def api_mission_parameters():
    """Get active mission's validated parameters and audit summary."""
    return jsonify(get_mission_params())


@mission_params_bp.route('/api/mission/parameter-audit')
def api_mission_parameter_audit():
    """Get the full parameter audit log for the current active mission."""
    try:
        if _io_utils:
            mission = _io_utils.atomic_read_json(_MISSION_PATH, {})
        else:
            mission = {}

        mission_id = mission.get("mission_id")
        if not mission_id:
            return jsonify({"error": "No active mission", "audit": None}), 404

        audit = _load_full_audit(mission_id)
        if audit is None:
            return jsonify({
                "mission_id": mission_id,
                "audit": None,
                "message": "No audit log found (mission may predate this feature)"
            })

        return jsonify({"mission_id": mission_id, "audit": audit})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mission_params_bp.route('/api/mission/parameter-audit/history')
def api_mission_parameter_audit_history():
    """Get cross-mission parameter audit history for trend analysis.

    Query params:
        limit (int): Max missions to return (default 20, max 100)

    Response:
        {
          "history": [...],   // oldest first
          "count": N,
          "health": "green"|"yellow"|"red"|"unknown",
          "cached": true|false
        }
    """
    try:
        limit = min(int(request.args.get("limit", 20)), 100)
    except (ValueError, TypeError):
        limit = 20

    try:
        now = time.time()
        cached = (
            _HISTORY_CACHE["data"] is not None
            and now - _HISTORY_CACHE["ts"] < HISTORY_CACHE_TTL
        )
        history = _get_cached_history(limit=limit)
        health = _compute_param_health(history)
        return jsonify({
            "history": history,
            "count": len(history),
            "health": health,
            "cached": cached,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mission_params_bp.route('/api/mission/parameter-audit/<mission_id>')
def api_mission_parameter_audit_by_id(mission_id: str):
    """Get the full parameter audit log for a specific mission by ID."""
    try:
        audit = _load_full_audit(mission_id)
        if audit is None:
            return jsonify({
                "mission_id": mission_id,
                "audit": None,
                "message": "No audit log found for this mission"
            }), 404
        return jsonify({"mission_id": mission_id, "audit": audit})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@mission_params_bp.route('/api/mission/parameter-audit/prune', methods=['POST'])
def prune_audit_logs():
    """Admin endpoint to manually prune old audit logs.

    Request body (JSON):
        max_missions (int): Maximum missions to retain (default 200).
        max_total_mb (float): Maximum total size in MB to retain (default 500.0).
        dry_run (bool): If true, report what would be pruned without deleting (default true).

    Response:
        {
          "pruned": ["mission_id", ...],
          "pruned_count": N,
          "dry_run": true|false
        }
    """
    from af_engine.mission_config import prune_old_audit_logs

    body = request.get_json(silent=True) or {}
    try:
        max_missions = int(body.get('max_missions', 200))
        max_total_mb = float(body.get('max_total_mb', 500.0))
        dry_run = bool(body.get('dry_run', True))
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'Invalid parameter: {e}'}), 400

    if not _MISSIONS_DIR or not _MISSIONS_DIR.exists():
        return jsonify({'error': 'missions directory not configured'}), 503

    try:
        pruned = prune_old_audit_logs(
            _MISSIONS_DIR,
            max_missions=max_missions,
            max_total_mb=max_total_mb,
            dry_run=dry_run,
        )
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    # Invalidate history cache after a real prune so next request rescans
    if not dry_run:
        _HISTORY_CACHE['data'] = None
        _HISTORY_CACHE['ts'] = 0.0

    return jsonify({
        'pruned': pruned,
        'pruned_count': len(pruned),
        'dry_run': dry_run,
    })
