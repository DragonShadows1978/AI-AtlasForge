"""
af_engine.kb_cache - Knowledge Base Caching Layer

This module provides a caching layer for Knowledge Base queries to reduce
prompt generation latency from ~750ms to <200ms.

Performance Profile (before caching):
- KB Import: ~912ms
- KB Init: ~1ms
- KB Query: ~756ms
- Total: ~1669ms

With caching:
- First query: ~1669ms (cold cache)
- Subsequent queries: <10ms (cache hit)

Cache Strategy:
- LRU cache for query results
- TTL-based invalidation (5 minutes default)
- Memory-bounded (max 100 entries)
- Thread-safe with RLock
"""

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Module-level lazy-loaded KB instance
_kb_instance: Optional[Any] = None
_kb_lock = threading.RLock()
_kb_import_attempted = False


class KBCache:
    """
    LRU cache for Knowledge Base query results.

    Provides:
    - Thread-safe access
    - TTL-based expiration
    - Memory-bounded storage
    - Cache statistics
    """

    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0):
        """
        Initialize KB cache.

        Args:
            max_size: Maximum number of cached entries
            ttl_seconds: Time-to-live for cache entries (default 5 min)
        """
        self._cache: OrderedDict[str, Tuple[Any, float]] = OrderedDict()
        self._lock = threading.RLock()
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds

        # Statistics
        self.hits = 0
        self.misses = 0

    def _make_key(self, query: str, top_k: int) -> str:
        """Generate cache key from query parameters."""
        return f"{query[:200]}:{top_k}"

    def get(self, query: str, top_k: int = 5) -> Optional[List[Dict]]:
        """
        Get cached result if available and not expired.

        Args:
            query: Search query string
            top_k: Number of results requested

        Returns:
            Cached results or None if not found/expired
        """
        key = self._make_key(query, top_k)

        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None

            result, timestamp = self._cache[key]

            # Check TTL
            if time.time() - timestamp > self.ttl_seconds:
                del self._cache[key]
                self.misses += 1
                return None

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            self.hits += 1
            return result

    def set(self, query: str, top_k: int, result: List[Dict]) -> None:
        """
        Cache a query result.

        Args:
            query: Search query string
            top_k: Number of results requested
            result: Query results to cache
        """
        key = self._make_key(query, top_k)

        with self._lock:
            # Remove oldest if at capacity
            while len(self._cache) >= self.max_size:
                self._cache.popitem(last=False)

            self._cache[key] = (result, time.time())

    def clear(self) -> None:
        """Clear all cached entries."""
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            total = self.hits + self.misses
            hit_rate = self.hits / total if total > 0 else 0.0
            return {
                "size": len(self._cache),
                "max_size": self.max_size,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": hit_rate,
                "ttl_seconds": self.ttl_seconds,
            }


# Global cache instance
_query_cache = KBCache(max_size=100, ttl_seconds=300.0)


def get_kb_instance() -> Optional[Any]:
    """
    Lazy-load and return the Knowledge Base instance.

    Thread-safe singleton pattern that defers the expensive import
    until actually needed.

    Returns:
        MissionKnowledgeBase instance or None if unavailable
    """
    global _kb_instance, _kb_import_attempted

    with _kb_lock:
        if _kb_instance is not None:
            return _kb_instance

        if _kb_import_attempted:
            return None

        _kb_import_attempted = True

        try:
            start = time.perf_counter()
            from mission_knowledge_base import MissionKnowledgeBase
            _kb_instance = MissionKnowledgeBase()
            elapsed = (time.perf_counter() - start) * 1000
            logger.debug(f"KB lazy-loaded in {elapsed:.1f}ms")
            return _kb_instance
        except ImportError as e:
            logger.debug(f"KB not available: {e}")
            return None
        except Exception as e:
            logger.warning(f"KB initialization failed: {e}")
            return None


def query_relevant_learnings(
    query: str,
    top_k: int = 5,
    use_cache: bool = True,
) -> List[Dict]:
    """
    Query KB for relevant learnings with caching.

    Args:
        query: Search query string
        top_k: Number of results to return
        use_cache: Whether to use cache (default True)

    Returns:
        List of relevant learning dictionaries
    """
    # Check cache first
    if use_cache:
        cached = _query_cache.get(query, top_k)
        if cached is not None:
            logger.debug(f"KB cache hit for query")
            return cached

    # Get KB instance (lazy-loaded)
    kb = get_kb_instance()
    if kb is None:
        return []

    try:
        start = time.perf_counter()
        results = kb.query_relevant_learnings(query, top_k=top_k)
        elapsed = (time.perf_counter() - start) * 1000

        # Cache the results
        if use_cache and results:
            _query_cache.set(query, top_k, results)

        logger.debug(f"KB query completed in {elapsed:.1f}ms, {len(results)} results")
        return results or []

    except Exception as e:
        logger.warning(f"KB query failed: {e}")
        return []


def generate_planning_context(mission_statement: str) -> str:
    """
    Generate planning context from KB with caching.

    Args:
        mission_statement: Mission problem statement

    Returns:
        Formatted KB context for planning stage
    """
    kb = get_kb_instance()
    if kb is None:
        return ""

    try:
        # Check if generate_planning_context exists
        if hasattr(kb, 'generate_planning_context'):
            return kb.generate_planning_context(mission_statement)

        # Fallback: format learnings manually
        learnings = query_relevant_learnings(mission_statement, top_k=5)
        if not learnings:
            return ""

        lines = [
            "=== LEARNINGS FROM PAST MISSIONS ===",
            "",
        ]

        for learning in learnings:
            title = learning.get("title", "Untitled")
            content = learning.get("content", "")[:500]
            category = learning.get("category", "general")

            lines.append(f"**{title}** [{category}]")
            lines.append(content)
            lines.append("")

        lines.append("=== END LEARNINGS ===")
        return "\n".join(lines)

    except Exception as e:
        logger.warning(f"Failed to generate planning context: {e}")
        return ""


def get_cache_stats() -> Dict[str, Any]:
    """Get cache statistics."""
    return _query_cache.get_stats()


def clear_cache() -> None:
    """Clear the KB cache."""
    _query_cache.clear()
    logger.debug("KB cache cleared")


def preload_kb() -> bool:
    """
    Preload KB instance for faster first query.

    Can be called during engine startup to amortize
    the import cost.

    Returns:
        True if KB loaded successfully
    """
    kb = get_kb_instance()
    return kb is not None
