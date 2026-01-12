"""
Mission Analytics API Routes Blueprint

Contains routes for:
- Aggregate analytics (30-day and all-time)
- Per-mission analytics
- Current mission cost tracking
- Historical spending charts
"""

from flask import Blueprint, jsonify, request
from pathlib import Path

# Create Blueprint
analytics_bp = Blueprint('analytics', __name__, url_prefix='/api/analytics')

# Configuration - set via init function
MISSION_PATH = None
io_utils = None


def init_analytics_blueprint(mission_path, io_utils_module):
    """Initialize the analytics blueprint with required dependencies."""
    global MISSION_PATH, io_utils
    MISSION_PATH = mission_path
    io_utils = io_utils_module


@analytics_bp.route('/summary')
def api_analytics_summary():
    """Get aggregate analytics (30-day and all-time)."""
    try:
        from mission_analytics import get_analytics
        analytics = get_analytics()
        return jsonify({
            "aggregate_30d": analytics.get_aggregate_stats(days=30),
            "all_time": analytics.get_aggregate_stats(days=0),
            "recent_missions": analytics.get_recent_missions(limit=10)
        })
    except Exception as e:
        return jsonify({"error": str(e), "aggregate_30d": {}, "all_time": {}})


@analytics_bp.route('/mission/<mission_id>')
def api_analytics_mission(mission_id):
    """Get analytics for a specific mission."""
    try:
        from mission_analytics import get_analytics
        analytics = get_analytics()
        summary = analytics.get_mission_summary(mission_id)
        if summary:
            return jsonify(summary.to_dict())
        return jsonify({"error": "Mission not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)})


@analytics_bp.route('/current')
def api_analytics_current():
    """Get analytics for current mission."""
    try:
        from mission_analytics import get_current_mission_analytics
        # Use the new function that queries token_events directly
        result = get_current_mission_analytics()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "tokens": 0, "cost": 0})


@analytics_bp.route('/history')
def api_analytics_history():
    """Get historical spending data for charts."""
    try:
        from mission_analytics import get_analytics
        days = request.args.get('days', 30, type=int)
        analytics = get_analytics()
        missions = analytics.get_recent_missions(limit=50)
        return jsonify({"missions": missions, "period_days": days})
    except Exception as e:
        return jsonify({"error": str(e), "missions": []})


@analytics_bp.route('/daily')
def api_analytics_daily():
    """Get daily aggregates for trend chart."""
    try:
        from mission_analytics import get_daily_aggregates
        days = request.args.get('days', 30, type=int)
        data = get_daily_aggregates(days)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "daily": []})


@analytics_bp.route('/by-stage')
def api_analytics_by_stage():
    """Get stage-level aggregates."""
    try:
        from mission_analytics import get_stage_aggregates
        days = request.args.get('days', 30, type=int)
        data = get_stage_aggregates(days)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "stages": {}})


@analytics_bp.route('/by-model')
def api_analytics_by_model():
    """Get model-level aggregates."""
    try:
        from mission_analytics import get_model_aggregates
        days = request.args.get('days', 30, type=int)
        data = get_model_aggregates(days)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "models": {}})


@analytics_bp.route('/mission/<mission_id>/stages')
def api_analytics_mission_stages(mission_id):
    """Get per-mission stage breakdown."""
    try:
        from mission_analytics import get_mission_stage_breakdown
        data = get_mission_stage_breakdown(mission_id)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "stages": []})


@analytics_bp.route('/watcher/status')
def api_watcher_status():
    """Get real-time token watcher status."""
    try:
        from realtime_token_watcher import get_token_watcher
        watcher = get_token_watcher()
        return jsonify(watcher.get_stats())
    except ImportError:
        return jsonify({
            "error": "Watcher module not available",
            "running": False,
            "enabled": False
        })
    except Exception as e:
        return jsonify({"error": str(e), "running": False})


@analytics_bp.route('/watcher/start', methods=['POST'])
def api_watcher_start():
    """Start real-time token watcher for current mission."""
    try:
        from realtime_token_watcher import get_token_watcher

        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_id = mission.get('mission_id')
        workspace = mission.get('mission_workspace')
        stage = mission.get('current_stage', 'unknown')

        if not mission_id:
            return jsonify({"error": "No active mission", "started": False})

        watcher = get_token_watcher()
        success = watcher.start(
            mission_id=mission_id,
            workspace_path=workspace,
            stage=stage
        )

        return jsonify({
            "started": success,
            "mission_id": mission_id,
            "stats": watcher.get_stats()
        })
    except ImportError:
        return jsonify({"error": "Watcher module not available", "started": False})
    except Exception as e:
        return jsonify({"error": str(e), "started": False})


@analytics_bp.route('/watcher/stop', methods=['POST'])
def api_watcher_stop():
    """Stop real-time token watcher."""
    try:
        from realtime_token_watcher import get_token_watcher
        watcher = get_token_watcher()
        watcher.stop()
        return jsonify({"stopped": True, "stats": watcher.get_stats()})
    except ImportError:
        return jsonify({"error": "Watcher module not available", "stopped": False})
    except Exception as e:
        return jsonify({"error": str(e), "stopped": False})
