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

import threading
import time

# =============================================================================
# TTL CACHE - In-memory cache with per-key expiry for hot API endpoints
# =============================================================================

class TTLCache:
    """Simple thread-safe in-memory cache with per-key TTL expiry.

    Designed for hot API endpoints like /api/status and /api/journal
    where re-computing the result on every request is expensive.

    Usage:
        _cache = TTLCache()
        data = _cache.get('status')
        if data is None:
            data = expensive_compute()
            _cache.set('status', data, ttl_seconds=0.75)
    """

    def __init__(self):
        self._store = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        """Return cached value if still valid, else None."""
        with self._lock:
            entry = self._store.get(key)
            if entry and time.time() < entry['expires']:
                return entry['data']
            return None

    def set(self, key: str, data, ttl_seconds: float):
        """Store value with TTL expiry."""
        with self._lock:
            self._store[key] = {
                'data': data,
                'expires': time.time() + ttl_seconds
            }

    def invalidate(self, key: str):
        """Remove a specific key from cache."""
        with self._lock:
            self._store.pop(key, None)

    def clear(self):
        """Clear all cached entries."""
        with self._lock:
            self._store.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        with self._lock:
            now = time.time()
            total = len(self._store)
            live = sum(1 for e in self._store.values() if now < e['expires'])
            return {'total_keys': total, 'live_keys': live, 'expired_keys': total - live}


# Singleton instance used by dashboard modules
_dashboard_ttl_cache = TTLCache()


def get_dashboard_cache() -> TTLCache:
    """Get the global dashboard TTL cache instance."""
    return _dashboard_ttl_cache


logger = logging.getLogger(__name__)

# Create Blueprint
cache_bp = Blueprint('cache', __name__, url_prefix='/api/cache')


@cache_bp.route('/status')
def cache_status():
    """Get status of all caches."""
    try:
        caches = {}

        # TTL cache stats
        try:
            caches['ttl_cache'] = get_dashboard_cache().stats()
        except Exception as e:
            caches['ttl_cache'] = {"error": str(e)}

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

    # TTL cache
    try:
        get_dashboard_cache().clear()
        results['ttl_cache'] = 'cleared'
    except Exception as e:
        results['ttl_cache'] = f'error: {e}'

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
