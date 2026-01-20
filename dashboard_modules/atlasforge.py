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

from flask import Blueprint, jsonify, request

# Create Blueprint
atlasforge_bp = Blueprint('atlasforge', __name__, url_prefix='/api/atlasforge')


# =============================================================================
# EXPLORATION STATS AND HISTORY
# =============================================================================

@atlasforge_bp.route('/exploration-stats')
def api_af_exploration_stats():
    """Get exploration graph statistics for dashboard widget."""
    try:
        import exploration_hooks
        data = exploration_hooks.get_af_dashboard_data()
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
        try:
            from af_engine import rearchive_mission
            result = rearchive_mission(mission_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)})

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
        try:
            import exploration_hooks
            result = exploration_hooks.populate_from_mission_archive(mission_id)
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "mission_id": mission_id})

    @app.route('/api/populate-decision-graph/all', methods=['POST'])
    def api_populate_decision_graph_all():
        """Populate decision graph data from all archived missions."""
        try:
            import exploration_hooks
            result = exploration_hooks.populate_all_archived_missions()
            return jsonify(result)
        except Exception as e:
            return jsonify({"error": str(e), "missions_processed": 0})
