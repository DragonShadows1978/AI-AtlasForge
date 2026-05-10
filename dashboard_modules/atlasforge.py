"""
AtlasForge Enhancements API Routes Blueprint

Contains routes for:
- Exploration statistics
- Drift history and tracking
- Semantic search
- Exploration graph visualization
- Prior mission knowledge
- Transcript re-archival
- Decision graph population
"""

import logging
import re

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# Create Blueprint
atlasforge_bp = Blueprint('atlasforge', __name__, url_prefix='/api/atlasforge')

_BIDI_CONTROL_CHARS = "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
_MISSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_route_mission_id(mission_id: str) -> tuple[bool, str]:
    """Validate route mission_id before passing it to file/archive helpers."""
    if not isinstance(mission_id, str):
        return False, "mission_id must be a string"
    if not mission_id or not mission_id.strip():
        return False, "mission_id must be non-empty"
    if len(mission_id) > 255:
        return False, "mission_id exceeds 255 characters"
    if "/" in mission_id or "\\" in mission_id:
        return False, "mission_id must not contain path separators"
    if "\x00" in mission_id:
        return False, "mission_id must not contain null bytes"
    if any(ord(ch) < 32 or ord(ch) == 127 or ch in _BIDI_CONTROL_CHARS for ch in mission_id):
        return False, "mission_id contains unsafe control characters"
    if not _MISSION_ID_RE.fullmatch(mission_id):
        return False, "mission_id may contain only letters, numbers, underscores, and hyphens"
    return True, ""


def _get_ttl_cache():
    try:
        from dashboard_modules.cache import get_dashboard_cache
        return get_dashboard_cache()
    except Exception:
        return None


# =============================================================================
# EXPLORATION STATS AND HISTORY
# =============================================================================

@atlasforge_bp.route('/exploration-stats')
def api_af_exploration_stats():
    """Get exploration graph statistics for dashboard widget."""
    cache = _get_ttl_cache()
    if cache:
        cached = cache.get('exploration_stats')
        if cached is not None:
            return jsonify(cached)
    try:
        import exploration_hooks
        data = exploration_hooks.get_af_dashboard_data()
        if cache:
            cache.set('exploration_stats', data, ttl_seconds=5.0)
        return jsonify(data)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "exploration": {"total_nodes": 0, "total_insights": 0, "total_edges": 0},
            "drift_history": [],
            "recent_explorations": [],
            "coverage_pct": 0
        })


@atlasforge_bp.route('/drift-history')
def api_af_drift_history():
    """Get drift history for trend visualization."""
    try:
        import exploration_hooks
        history = exploration_hooks.get_drift_history()
        if not history:
            return jsonify({
                "history": [],
                "message": "Drift tracking requires multi-cycle missions. Data is captured at cycle boundaries."
            })
        return jsonify({"history": history})
    except Exception as e:
        return jsonify({"error": str(e), "history": []})


@atlasforge_bp.route('/recent-explorations')
def api_af_recent_explorations():
    """Get recently explored items."""
    try:
        import exploration_hooks
        limit = request.args.get('limit', 10, type=int)
        explorations = exploration_hooks.get_recent_explorations(limit)
        return jsonify(explorations)
    except Exception as e:
        return jsonify({"error": str(e), "explorations": []})


# =============================================================================
# SEMANTIC SEARCH
# =============================================================================

@atlasforge_bp.route('/semantic-search')
def api_af_semantic_search():
    """Perform semantic search on exploration graph."""
    try:
        import exploration_hooks
        query = request.args.get('q', '')
        top_k = request.args.get('top_k', 10, type=int)
        if not query:
            return jsonify({"error": "Missing query parameter 'q'", "results": []})
        results = exploration_hooks.semantic_search(query, top_k)
        return jsonify({"query": query, "results": results})
    except Exception as e:
        return jsonify({"error": str(e), "results": []})


@atlasforge_bp.route('/what-do-we-know')
def api_af_what_do_we_know():
    """Query exploration memory for knowledge on a topic."""
    try:
        import exploration_hooks
        topic = request.args.get('topic', '')
        if not topic:
            return jsonify({"error": "Missing query parameter 'topic'"})
        knowledge = exploration_hooks.what_do_we_know(topic)
        return jsonify(knowledge)
    except Exception as e:
        return jsonify({"error": str(e)})


@atlasforge_bp.route('/search-insights')
def api_af_search_insights():
    """Semantic search for insights."""
    try:
        import exploration_hooks
        query = request.args.get('q', '')
        if not query:
            return jsonify({"error": "Missing query parameter 'q'", "insights": []})
        top_k = request.args.get('top_k', 10, type=int)
        insights = exploration_hooks.search_insights(query, top_k)
        return jsonify({"query": query, "insights": insights})
    except Exception as e:
        return jsonify({"error": str(e), "insights": []})


# =============================================================================
# VISUALIZATION
# =============================================================================

@atlasforge_bp.route('/exploration-graph')
def api_af_exploration_graph():
    """Get exploration graph for visualization."""
    try:
        import exploration_hooks
        width = request.args.get('width', 800, type=float)
        height = request.args.get('height', 600, type=float)
        data = exploration_hooks.get_visualization_data(width, height)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e), "nodes": [], "edges": []})


# =============================================================================
# PRIOR MISSIONS KNOWLEDGE
# =============================================================================

@atlasforge_bp.route('/prior-missions')
def api_af_prior_missions():
    """Get list of prior missions with exploration data."""
    try:
        import exploration_hooks
        missions = exploration_hooks.get_prior_missions_list()
        return jsonify({"missions": missions})
    except Exception as e:
        return jsonify({"error": str(e), "missions": []})


@atlasforge_bp.route('/query-prior-knowledge')
def api_af_query_prior_knowledge():
    """Query knowledge from prior missions."""
    try:
        import exploration_hooks
        query = request.args.get('q', '')
        if not query:
            return jsonify({"error": "Missing query parameter 'q'", "results": []})
        top_k = request.args.get('top_k', 10, type=int)
        result = exploration_hooks.query_prior_missions(query, top_k)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "results": []})


@atlasforge_bp.route('/starting-suggestions')
def api_af_starting_suggestions():
    """Get starting point suggestions from prior missions."""
    try:
        import exploration_hooks
        suggestions = exploration_hooks.get_prior_mission_suggestions()
        return jsonify({"suggestions": suggestions})
    except Exception as e:
        return jsonify({"error": str(e), "suggestions": []})


# =============================================================================
# RE-ARCHIVAL AND DECISION GRAPH POPULATION
# =============================================================================

# These routes don't have the /api/atlasforge prefix in the original, so we need
# to register them separately. They're included here for reference but
# will be registered on the main app or a separate blueprint.


def register_archival_routes(app):
    """Register archival routes that don't follow the /api/atlasforge prefix pattern."""

    @app.route('/api/rearchive/mission/<mission_id>', methods=['POST'])
    def api_rearchive_mission(mission_id):
        """Re-archive a specific mission's transcripts."""
        ok, reason = _validate_route_mission_id(mission_id)
        if not ok:
            return jsonify({"success": False, "error": reason}), 400
        try:
            from af_engine import rearchive_mission
            result = rearchive_mission(mission_id)
            return jsonify(result)
        except Exception as e:
            logger.exception("rearchive mission failed for mission_id=%r", mission_id)
            return jsonify({"success": False, "error": "rearchive failed"}), 500

    @app.route('/api/rearchive/all', methods=['POST'])
    def api_rearchive_all():
        """Re-archive all missions with empty/missing transcript data."""
        try:
            from af_engine import rearchive_all_missions
            result = rearchive_all_missions()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "total_checked": 0, "rearchived": 0})

    @app.route('/api/populate-decision-graph/<mission_id>', methods=['POST'])
    def api_populate_decision_graph_mission(mission_id):
        """Populate decision graph data from a mission's transcripts."""
        ok, reason = _validate_route_mission_id(mission_id)
        if not ok:
            return jsonify({"error": reason, "mission_id": None}), 400
        try:
            import exploration_hooks
            result = exploration_hooks.populate_from_mission_archive(mission_id)
            return jsonify(result)
        except Exception as e:
            logger.exception(
                "populate decision graph failed for mission_id=%r", mission_id
            )
            return jsonify({"error": "populate decision graph failed", "mission_id": mission_id}), 500

    @app.route('/api/populate-decision-graph/all', methods=['POST'])
    def api_populate_decision_graph_all():
        """Populate decision graph data from all archived missions."""
        try:
            import exploration_hooks
            result = exploration_hooks.populate_all_archived_missions()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "missions_processed": 0})
