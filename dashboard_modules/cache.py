"""
Cache Management Blueprint

Provides API endpoints for managing various caches in the AtlasForge system:
- KB analytics cache
- Decision graph cache
- Template cache
- Generic cache invalidation
"""

from flask import Blueprint, jsonify, request
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Create Blueprint
cache_bp = Blueprint('cache', __name__, url_prefix='/api/cache')


@cache_bp.route('/status')
def cache_status():
    """Get status of all caches."""
    try:
        caches = {}

        # KB analytics cache
        try:
            from kb_analytics import get_cache_stats
            caches['kb_analytics'] = get_cache_stats()
        except Exception as e:
            caches['kb_analytics'] = {"error": str(e)}

        # Semantic index cache
        try:
            from mission_knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            index = kb._semantic_index
            caches['semantic_index'] = {
                "fitted": index._fitted,
                "learning_count": len(index.learning_ids) if index._fitted else 0,
                "has_cache": index._cluster_cache is not None
            }
        except Exception as e:
            caches['semantic_index'] = {"error": str(e)}

        return jsonify({"caches": caches})
    except Exception as e:
        return jsonify({"error": str(e)})


@cache_bp.route('/invalidate/kb', methods=['POST'])
def invalidate_kb_cache():
    """Invalidate knowledge base caches."""
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        kb._semantic_index.invalidate()

        # Also invalidate KB analytics cache if available
        try:
            from kb_analytics import get_kb_analytics
            analytics = get_kb_analytics()
            if hasattr(analytics, '_cache'):
                analytics._cache.clear()
        except:
            pass

        return jsonify({"status": "invalidated", "cache": "kb"})
    except Exception as e:
        return jsonify({"error": str(e)})


@cache_bp.route('/invalidate/all', methods=['POST'])
def invalidate_all_caches():
    """Invalidate all caches."""
    results = {}

    # KB cache
    try:
        from mission_knowledge_base import get_knowledge_base
        kb = get_knowledge_base()
        kb._semantic_index.invalidate()
        results['kb'] = 'invalidated'
    except Exception as e:
        results['kb'] = f'error: {e}'

    # KB analytics cache
    try:
        from kb_analytics import get_kb_analytics
        analytics = get_kb_analytics()
        if hasattr(analytics, '_cache'):
            analytics._cache.clear()
        results['kb_analytics'] = 'invalidated'
    except Exception as e:
        results['kb_analytics'] = f'error: {e}'

    return jsonify({"status": "completed", "results": results})
