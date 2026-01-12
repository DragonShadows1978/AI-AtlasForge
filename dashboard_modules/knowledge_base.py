"""
Knowledge Base API Routes Blueprint

Contains routes for:
- Knowledge base statistics
- Learning CRUD operations
- Search and filtering
- Clustering and duplicate detection
- Analytics and themes
"""

from flask import Blueprint, jsonify, request
import json

# Create Blueprint
knowledge_base_bp = Blueprint('knowledge_base', __name__, url_prefix='/api/knowledge-base')


# =============================================================================
# BASIC OPERATIONS
# =============================================================================

@knowledge_base_bp.route('/stats')
def api_kb_stats():
    """Get knowledge base statistics."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        stats = kb.get_statistics()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e), "total_missions": 0, "total_learnings": 0})


@knowledge_base_bp.route('/learnings')
def api_kb_learnings():
    """Get all learnings from knowledge base."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        domain = request.args.get('domain', '')
        learning_type = request.args.get('type', '')
        source_type = request.args.get('source_type', '')  # 'mission', 'investigation', or '' for all
        limit = min(request.args.get('limit', 50, type=int), 500)  # Cap at 500

        import sqlite3
        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM learnings WHERE 1=1"
            params = []

            if domain:
                query += " AND problem_domain = ?"
                params.append(domain)
            if learning_type:
                query += " AND learning_type = ?"
                params.append(learning_type)
            if source_type:
                query += " AND source_type = ?"
                params.append(source_type)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            rows = cursor.fetchall()

        columns = [
            "learning_id", "mission_id", "learning_type", "title", "description",
            "problem_domain", "outcome", "relevance_keywords", "code_snippets",
            "files_created", "timestamp", "lesson_source", "source_type",
            "source_investigation_id", "investigation_query"
        ]

        learnings = []
        for row in rows:
            # Handle rows with fewer columns (older DB schema)
            data = {}
            for i, col in enumerate(columns):
                if i < len(row):
                    data[col] = row[i]
                else:
                    data[col] = None

            data["relevance_keywords"] = json.loads(data["relevance_keywords"] or "[]")
            data["code_snippets"] = json.loads(data["code_snippets"] or "[]")
            data["files_created"] = json.loads(data["files_created"] or "[]")
            # Default source_type if not present
            data["source_type"] = data.get("source_type") or "mission"
            learnings.append(data)

        return jsonify({"learnings": learnings})
    except Exception as e:
        return jsonify({"error": str(e), "learnings": []})


@knowledge_base_bp.route('/learnings/<learning_id>')
def api_kb_learning_detail(learning_id):
    """Get details of a specific learning, including investigation source info."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        import sqlite3
        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM learnings WHERE learning_id = ?", (learning_id,))
            row = cursor.fetchone()

        if not row:
            return jsonify({"error": "Learning not found"}), 404

        # Updated columns list to include new investigation-related fields
        columns = [
            "learning_id", "mission_id", "learning_type", "title", "description",
            "problem_domain", "outcome", "relevance_keywords", "code_snippets",
            "files_created", "timestamp", "lesson_source", "source_type",
            "source_investigation_id", "investigation_query"
        ]

        # Handle rows with fewer columns (older DB schema)
        data = {}
        for i, col in enumerate(columns):
            if i < len(row):
                data[col] = row[i]
            else:
                data[col] = None

        data["relevance_keywords"] = json.loads(data["relevance_keywords"] or "[]")
        data["code_snippets"] = json.loads(data["code_snippets"] or "[]")
        data["files_created"] = json.loads(data["files_created"] or "[]")

        # Default source_type if not present
        data["source_type"] = data.get("source_type") or "mission"

        # Add investigation report path if from investigation
        if data.get("source_type") == "investigation" and data.get("source_investigation_id"):
            from pathlib import Path
            from atlasforge_config import INVESTIGATIONS_DIR
            inv_id = data["source_investigation_id"]
            report_path = INVESTIGATIONS_DIR / inv_id / "artifacts" / "investigation_report.md"
            if report_path.exists():
                data["investigation_report_path"] = str(report_path)

        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)})


@knowledge_base_bp.route('/learnings/<learning_id>', methods=['PATCH'])
def api_kb_update_learning(learning_id):
    """Update a learning's fields (title, description)."""
    try:
        from mission_knowledge_base import get_knowledge_base
        import sqlite3

        kb = get_knowledge_base()
        data = request.get_json()

        if not data:
            return jsonify({"error": "Missing JSON body"}), 400

        allowed_fields = ['title', 'description']
        updates = {k: v for k, v in data.items() if k in allowed_fields}

        if not updates:
            return jsonify({"error": "No valid fields to update"}), 400

        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            set_clause = ', '.join([f"{k} = ?" for k in updates.keys()])
            cursor.execute(
                f"UPDATE learnings SET {set_clause} WHERE learning_id = ?",
                list(updates.values()) + [learning_id]
            )

            if cursor.rowcount == 0:
                return jsonify({"error": "Learning not found"}), 404

        kb._semantic_index.invalidate()

        return jsonify({"success": True, "updated": list(updates.keys())})
    except Exception as e:
        return jsonify({"error": str(e)})


# =============================================================================
# SEARCH AND FILTERING
# =============================================================================

@knowledge_base_bp.route('/search')
def api_kb_search():
    """Search the knowledge base."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        query = request.args.get('q', '')
        domain = request.args.get('domain', '')
        learning_type = request.args.get('type', '')
        top_k = min(request.args.get('top_k', 10, type=int), 100)  # Cap at 100

        if not query:
            return jsonify({"error": "Missing query parameter 'q'", "results": []})

        learning_types = [learning_type] if learning_type else None
        learnings = kb.query_relevant_learnings(query, top_k=top_k, learning_types=learning_types)

        results = []
        for l in learnings:
            # Handle both dict and object types
            if isinstance(l, dict):
                if domain and l.get('problem_domain') != domain:
                    continue
                results.append(l)
            else:
                if domain and getattr(l, 'problem_domain', None) != domain:
                    continue
                results.append(l.to_dict() if hasattr(l, 'to_dict') else l.__dict__)

        return jsonify({"query": query, "results": results})
    except Exception as e:
        return jsonify({"error": str(e), "results": []})


@knowledge_base_bp.route('/missions')
def api_kb_missions():
    """Get missions in knowledge base."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        import sqlite3
        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT mission_id, problem_statement, problem_domain, outcome,
                       duration_minutes, cycles_used, timestamp
                FROM mission_summaries
                ORDER BY timestamp DESC
                LIMIT 50
            """)
            rows = cursor.fetchall()

        missions = []
        for row in rows:
            missions.append({
                "mission_id": row[0],
                "problem_statement": row[1][:200] if row[1] else "",
                "problem_domain": row[2],
                "outcome": row[3],
                "duration_minutes": row[4],
                "cycles_used": row[5],
                "timestamp": row[6]
            })

        return jsonify({"missions": missions})
    except Exception as e:
        return jsonify({"error": str(e), "missions": []})


@knowledge_base_bp.route('/domains')
def api_kb_domains():
    """Get list of available domains."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        import sqlite3
        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT problem_domain FROM learnings")
            domains = [row[0] for row in cursor.fetchall() if row[0]]

        return jsonify({"domains": domains})
    except Exception as e:
        return jsonify({"error": str(e), "domains": []})


# =============================================================================
# CLUSTERING AND DUPLICATES
# =============================================================================

@knowledge_base_bp.route('/clusters')
def api_kb_clusters():
    """Get learning clusters with themes and coherence."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        threshold = float(request.args.get('threshold', 0.7))
        clusters = kb.get_learning_clusters(distance_threshold=threshold)

        return jsonify({
            "clusters": clusters,
            "count": len(clusters)
        })
    except Exception as e:
        return jsonify({"error": str(e), "clusters": []})


@knowledge_base_bp.route('/hierarchical-clusters')
def api_kb_hierarchical_clusters():
    """Get hierarchical clusters with themes and coherence."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        result = kb.get_hierarchical_clusters()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "clusters": []})


@knowledge_base_bp.route('/duplicates')
def api_kb_duplicates():
    """Get duplicate learning groups."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        threshold = float(request.args.get('threshold', 0.85))
        duplicates = kb.find_duplicate_learnings(threshold=threshold)

        return jsonify({
            "duplicate_groups": duplicates,
            "count": len(duplicates)
        })
    except Exception as e:
        return jsonify({"error": str(e), "duplicate_groups": []})


@knowledge_base_bp.route('/merge', methods=['POST'])
def api_kb_merge():
    """Merge duplicate learnings, keeping the canonical one."""
    try:
        from mission_knowledge_base import get_knowledge_base
        import sqlite3

        kb = get_knowledge_base()
        data = request.get_json()

        if not data:
            return jsonify({"error": "Missing JSON body"}), 400

        keep_id = data.get('keep_id')
        merge_ids = data.get('merge_ids', [])

        if not keep_id or not merge_ids:
            return jsonify({"error": "Missing keep_id or merge_ids"}), 400

        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join(['?' for _ in merge_ids])
            cursor.execute(
                f"DELETE FROM learnings WHERE learning_id IN ({placeholders})",
                merge_ids
            )
            deleted = cursor.rowcount

        kb._semantic_index.invalidate()

        return jsonify({
            "success": True,
            "merged": deleted,
            "kept": keep_id
        })
    except Exception as e:
        return jsonify({"error": str(e), "success": False})


@knowledge_base_bp.route('/batch-delete', methods=['POST'])
def api_kb_batch_delete():
    """Delete multiple learnings at once."""
    try:
        from mission_knowledge_base import get_knowledge_base
        import sqlite3

        kb = get_knowledge_base()
        data = request.get_json()

        if not data:
            return jsonify({"error": "Missing JSON body"}), 400

        learning_ids = data.get('learning_ids', [])
        if not learning_ids:
            return jsonify({"error": "No learning IDs provided"}), 400

        if len(learning_ids) > 100:
            return jsonify({"error": "Maximum 100 deletions per batch"}), 400

        with sqlite3.connect(kb.db_path) as conn:
            cursor = conn.cursor()
            placeholders = ','.join(['?' for _ in learning_ids])
            cursor.execute(
                f"DELETE FROM learnings WHERE learning_id IN ({placeholders})",
                learning_ids
            )
            deleted = cursor.rowcount

        kb._semantic_index.invalidate()

        return jsonify({"success": True, "deleted": deleted})
    except Exception as e:
        return jsonify({"error": str(e)})


# =============================================================================
# INDEX MANAGEMENT
# =============================================================================

@knowledge_base_bp.route('/ingest', methods=['POST'])
def api_kb_ingest():
    """Manually trigger knowledge base ingestion."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        result = kb.ingest_all_mission_logs()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"})


@knowledge_base_bp.route('/rebuild-index', methods=['POST'])
def api_kb_rebuild_index():
    """Rebuild the semantic index."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        success = kb.rebuild_semantic_index()

        return jsonify({
            "status": "success" if success else "error",
            "success": success
        })
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"})


@knowledge_base_bp.route('/index-status')
def api_kb_index_status():
    """Get semantic index status."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        index = kb._semantic_index

        return jsonify({
            "fitted": index._fitted,
            "learning_count": len(index.learning_ids) if index._fitted else 0,
            "pending_count": len(getattr(index, '_pending_additions', [])),
            "has_cache": index._cluster_cache is not None
        })
    except Exception as e:
        return jsonify({"error": str(e), "fitted": False})


# =============================================================================
# RELATED LEARNINGS AND CHAINS
# =============================================================================

@knowledge_base_bp.route('/related/<learning_id>')
def api_kb_related(learning_id):
    """Get learnings related to a specific learning."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        threshold = float(request.args.get('threshold', 0.6))
        max_results = int(request.args.get('max', 10))

        related = kb.get_related_learnings(
            learning_id,
            threshold=threshold,
            max_results=max_results
        )

        return jsonify({
            "related": related,
            "learning_id": learning_id
        })
    except Exception as e:
        return jsonify({"error": str(e), "related": []})


@knowledge_base_bp.route('/learning-chains')
def api_kb_learning_chains():
    """Get learning chains across missions."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        min_length = int(request.args.get('min_length', 3))

        chains = kb.get_learning_chains(min_chain_length=min_length)

        return jsonify({
            "chains": chains,
            "count": len(chains)
        })
    except Exception as e:
        return jsonify({"error": str(e), "chains": []})


# =============================================================================
# ANALYTICS
# =============================================================================

@knowledge_base_bp.route('/analytics')
def api_kb_analytics():
    """Get comprehensive KB analytics for dashboard widget.

    Query params:
        start_date: ISO date string for start filter
        end_date: ISO date string for end filter
        source_type: 'mission', 'investigation', or '' for all
    """
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()

        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        source_type = request.args.get('source_type', '')

        # Pass source_type to get_dashboard_data for filtering
        result = analytics.get_dashboard_data(start_date, end_date, source_type if source_type else None)

        # If source_type filter is applied, add to response
        if source_type:
            result['source_filter'] = source_type

        return jsonify(result)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "accumulation": {"missions": [], "total_learnings": 0},
            "type_distribution": {"distribution": {}, "total": 0},
            "top_themes": {"themes": [], "total_themes": 0},
            "transfer_rate": {"transfer_rate": 0, "total_missions": 0},
            "learning_chains": []
        })


@knowledge_base_bp.route('/analytics/accumulation')
def api_kb_analytics_accumulation():
    """Get learning accumulation data (time series)."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify(analytics.get_learning_accumulation())
    except Exception as e:
        return jsonify({"error": str(e), "missions": [], "total_learnings": 0})


@knowledge_base_bp.route('/analytics/types')
def api_kb_analytics_types():
    """Get learning types distribution."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify(analytics.get_type_distribution())
    except Exception as e:
        return jsonify({"error": str(e), "distribution": {}, "total": 0})


@knowledge_base_bp.route('/analytics/themes')
def api_kb_analytics_themes():
    """Get top themes with frequency counts."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        top_n = min(request.args.get('top_n', 10, type=int), 100)  # Cap at 100
        return jsonify(analytics.get_top_themes(top_n=top_n))
    except Exception as e:
        return jsonify({"error": str(e), "themes": [], "total_themes": 0})


@knowledge_base_bp.route('/analytics/transfer')
def api_kb_analytics_transfer():
    """Get inter-mission learning transfer rate."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify(analytics.get_transfer_rate())
    except Exception as e:
        return jsonify({"error": str(e), "transfer_rate": 0, "total_missions": 0})


@knowledge_base_bp.route('/analytics/learnings-by-theme')
def api_kb_learnings_by_theme():
    """Get learnings for a specific theme (for drill-down modal)."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()

        theme = request.args.get('theme', '')
        theme_type = request.args.get('type', 'domain')

        if not theme:
            return jsonify({"error": "Theme parameter required"}), 400

        return jsonify(analytics.get_learnings_by_theme(theme, theme_type))
    except Exception as e:
        return jsonify({"error": str(e), "by_mission": {}, "total_learnings": 0})


@knowledge_base_bp.route('/analytics/mission-profile')
def api_kb_mission_profile():
    """Get full analytics profile for a single mission."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()

        mission_id = request.args.get('mission_id', '')

        if not mission_id:
            return jsonify({"error": "mission_id parameter required"}), 400

        return jsonify(analytics.get_mission_profile(mission_id))
    except Exception as e:
        return jsonify({"error": str(e), "total_learnings": 0})


@knowledge_base_bp.route('/analytics/missions')
def api_kb_all_missions():
    """Get list of all missions for comparison selector."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify({"missions": analytics.get_all_missions()})
    except Exception as e:
        return jsonify({"error": str(e), "missions": []})


@knowledge_base_bp.route('/analytics/chains')
def api_kb_analytics_chains():
    """Get learning chains for graph visualization (analytics)."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify({"chains": analytics.get_learning_chains()})
    except Exception as e:
        return jsonify({"error": str(e), "chains": []})


@knowledge_base_bp.route('/cache-stats')
def api_kb_cache_stats():
    """
    Get cache statistics for KB analytics monitoring.

    Returns cache hit/miss rates and other performance metrics.
    Useful for tuning TTL values in production.
    """
    try:
        from kb_analytics import get_cache_stats
        return jsonify(get_cache_stats())
    except Exception as e:
        return jsonify({
            "error": str(e),
            "hits": 0,
            "misses": 0,
            "hit_rate": 0
        })


# =============================================================================
# INVESTIGATION-SOURCED LEARNINGS
# =============================================================================

@knowledge_base_bp.route('/investigations')
def api_kb_investigations():
    """Get learnings sourced from investigations."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        investigation_id = request.args.get('investigation_id')
        limit = min(request.args.get('limit', 50, type=int), 500)

        learnings = kb.get_investigation_learnings(investigation_id=investigation_id, limit=limit)
        return jsonify({"learnings": learnings})
    except Exception as e:
        return jsonify({"error": str(e), "learnings": []})


@knowledge_base_bp.route('/investigations/stats')
def api_kb_investigation_stats():
    """Get statistics about investigation-sourced learnings."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()

        stats = kb.get_investigation_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "total_investigation_learnings": 0,
            "investigation_count": 0,
            "by_type": {},
            "by_domain": {},
            "recent_queries": []
        })


@knowledge_base_bp.route('/investigations/ingest', methods=['POST'])
def api_kb_ingest_investigations():
    """Manually trigger investigation ingestion into the knowledge base."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        result = kb.ingest_all_investigations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"})


@knowledge_base_bp.route('/investigations/ingest/<investigation_id>', methods=['POST'])
def api_kb_ingest_single_investigation(investigation_id):
    """Ingest a specific investigation into the knowledge base."""
    try:
        from mission_knowledge_base import get_knowledge_base
        from pathlib import Path
        from atlasforge_config import INVESTIGATIONS_DIR

        kb = get_knowledge_base()

        # Find investigation directory
        investigations_dir = INVESTIGATIONS_DIR
        inv_dir = investigations_dir / investigation_id

        if not inv_dir.exists():
            return jsonify({"error": f"Investigation {investigation_id} not found"}), 404

        result = kb.ingest_investigation(inv_dir)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"})


@knowledge_base_bp.route('/analytics/investigations')
def api_kb_analytics_investigations():
    """Get analytics specific to investigation-sourced learnings."""
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        return jsonify(analytics.get_investigation_analytics())
    except Exception as e:
        return jsonify({
            "error": str(e),
            "total_investigations": 0,
            "total_learnings": 0,
            "by_type": {},
            "by_domain": {},
            "recent_queries": [],
            "source_type_distribution": {}
        })


# =============================================================================
# MISSION RECOMMENDATIONS (From Investigations)
# =============================================================================

@knowledge_base_bp.route('/recommendations')
def api_kb_recommendations():
    """
    Get pending mission recommendations from investigations.

    Query Parameters:
        page: Page number (default: 1)
        per_page: Items per page (default: 20, max: 100)
        complexity: Filter by complexity (low/medium/high)
        priority: Filter by priority level (1-5)
        investigation_id: Filter by source investigation
        start_date: Filter by created_at >= start_date (ISO date)
        end_date: Filter by created_at <= end_date (ISO date)
        search: Keyword search in title/description

    Response:
        {
            "recommendations": [...],
            "total": 156,
            "page": 1,
            "per_page": 20,
            "pages": 8,
            "has_next": true,
            "has_prev": false
        }
    """
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        # Parse query parameters
        page = request.args.get('page', 1, type=int)
        per_page = min(request.args.get('per_page', 20, type=int), 100)
        complexity = request.args.get('complexity')
        priority = request.args.get('priority', type=int)
        investigation_id = request.args.get('investigation_id')
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        search = request.args.get('search')

        # Use the new paginated method
        result = engine.get_recommendations_paginated(
            page=page,
            per_page=per_page,
            complexity=complexity,
            priority=priority,
            investigation_id=investigation_id,
            start_date=start_date,
            end_date=end_date,
            search=search
        )

        return jsonify(result)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "recommendations": [],
            "total": 0,
            "page": 1,
            "per_page": 20,
            "pages": 1,
            "has_next": False,
            "has_prev": False
        })


@knowledge_base_bp.route('/recommendations/investigations')
def api_kb_recommendation_investigations():
    """Get list of distinct investigations with pending recommendations."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        investigations = engine.get_distinct_investigations()
        return jsonify({"investigations": investigations})
    except Exception as e:
        return jsonify({"error": str(e), "investigations": []})


@knowledge_base_bp.route('/recommendations/delete-all', methods=['DELETE'])
def api_kb_delete_all_recommendations():
    """Delete all pending recommendations."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        result = engine.delete_all_pending_recommendations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "success": False})


@knowledge_base_bp.route('/recommendations/bulk-delete', methods=['POST'])
def api_kb_bulk_delete_recommendations():
    """Delete multiple recommendations by IDs."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        data = request.get_json() or {}
        ids = data.get('ids', [])

        if not ids:
            return jsonify({"error": "No IDs provided", "success": False}), 400

        if len(ids) > 100:
            return jsonify({"error": "Maximum 100 deletions per batch", "success": False}), 400

        result = engine.delete_recommendations_by_ids(ids)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "success": False})


@knowledge_base_bp.route('/recommendations/<recommendation_id>')
def api_kb_recommendation_detail(recommendation_id):
    """Get details of a specific recommendation."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        rec = engine.get_recommendation(recommendation_id)
        if not rec:
            return jsonify({"error": "Recommendation not found"}), 404

        return jsonify(rec)
    except Exception as e:
        return jsonify({"error": str(e)})


@knowledge_base_bp.route('/recommendations/<recommendation_id>/accept', methods=['POST'])
def api_kb_accept_recommendation(recommendation_id):
    """Accept a recommendation for conversion to mission."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        success = engine.accept_recommendation(recommendation_id)
        if success:
            return jsonify({"status": "accepted", "recommendation_id": recommendation_id})
        else:
            return jsonify({"error": "Failed to accept recommendation"}), 400
    except Exception as e:
        return jsonify({"error": str(e)})


@knowledge_base_bp.route('/recommendations/<recommendation_id>/reject', methods=['POST'])
def api_kb_reject_recommendation(recommendation_id):
    """Reject a recommendation."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        success = engine.reject_recommendation(recommendation_id)
        if success:
            return jsonify({"status": "rejected", "recommendation_id": recommendation_id})
        else:
            return jsonify({"error": "Failed to reject recommendation"}), 400
    except Exception as e:
        return jsonify({"error": str(e)})


@knowledge_base_bp.route('/recommendations/<recommendation_id>/convert', methods=['POST'])
def api_kb_convert_recommendation(recommendation_id):
    """Convert a recommendation to a mission-ready format."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        data = request.get_json() or {}
        cycle_budget = data.get('cycle_budget', 3)

        mission_data = engine.convert_to_mission(recommendation_id, cycle_budget=cycle_budget)
        if not mission_data:
            return jsonify({"error": "Failed to convert recommendation"}), 400

        # Mark as completed so it's removed from pending list
        # Use "pending_suggestion" as placeholder until actual mission is created
        engine.mark_converted(recommendation_id, "pending_suggestion")

        return jsonify({
            "status": "converted",
            "mission_data": mission_data
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@knowledge_base_bp.route('/recommendations/generate', methods=['POST'])
def api_kb_generate_recommendations():
    """Generate recommendations from all investigations."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        result = engine.generate_from_all_investigations()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "status": "error"})


@knowledge_base_bp.route('/recommendations/stats')
def api_kb_recommendation_stats():
    """Get recommendation statistics."""
    try:
        from mission_recommendations import get_recommendation_engine
        engine = get_recommendation_engine()

        stats = engine.get_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({
            "error": str(e),
            "total_recommendations": 0,
            "by_status": {},
            "by_complexity": {},
            "by_investigation": [],
            "conversion_rate": 0
        })


# =============================================================================
# INVESTIGATION REPORTS
# =============================================================================

@knowledge_base_bp.route('/investigations/<investigation_id>/report')
def api_kb_investigation_report(investigation_id):
    """Get the investigation report content for a given investigation ID."""
    try:
        from pathlib import Path
        import markdown
        from atlasforge_config import INVESTIGATIONS_DIR

        investigations_dir = INVESTIGATIONS_DIR
        report_path = investigations_dir / investigation_id / "artifacts" / "investigation_report.md"

        if not report_path.exists():
            return jsonify({"error": f"Report not found for investigation {investigation_id}"}), 404

        content = report_path.read_text(encoding='utf-8')

        # Try to convert to HTML if markdown library is available
        try:
            html_content = markdown.markdown(content, extensions=['tables', 'fenced_code'])
        except:
            html_content = None

        return jsonify({
            "investigation_id": investigation_id,
            "report_path": str(report_path),
            "content": content,
            "html_content": html_content,
            "exists": True
        })
    except Exception as e:
        return jsonify({"error": str(e), "exists": False})
