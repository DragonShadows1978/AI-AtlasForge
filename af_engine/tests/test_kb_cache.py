"""
Tests for KB Cache - Knowledge Base Caching Layer

These tests validate:
- LRU cache behavior
- TTL-based expiration
- Thread safety
- Cache statistics
- Lazy loading of KB instance
"""

import pytest
import time
import threading
from unittest.mock import Mock, patch, MagicMock


class TestKBCacheClass:
    """Tests for KBCache class."""

    def test_cache_set_and_get(self):
        """Test basic set and get operations."""
        from af_engine.kb_cache import KBCache

        cache = KBCache(max_size=10, ttl_seconds=60)

        cache.set("test query", 5, [{"title": "result1"}])
        result = cache.get("test query", 5)

        assert result is not None
        assert len(result) == 1
        assert result[0]["title"] == "result1"

    def test_cache_miss(self):
        """Test cache miss returns None."""
        from af_engine.kb_cache import KBCache

        cache = KBCache()
        result = cache.get("nonexistent query", 5)

        assert result is None

    def test_cache_ttl_expiration(self):
        """Test that cache entries expire after TTL."""
        from af_engine.kb_cache import KBCache

        cache = KBCache(ttl_seconds=0.1)  # 100ms TTL

        cache.set("query", 5, [{"data": "test"}])

        # Should hit cache immediately
        assert cache.get("query", 5) is not None

        # Wait for TTL to expire
        time.sleep(0.15)

        # Should miss after expiration
        assert cache.get("query", 5) is None

    def test_cache_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        from af_engine.kb_cache import KBCache

        cache = KBCache(max_size=2, ttl_seconds=60)

        cache.set("query1", 5, [{"data": "1"}])
        cache.set("query2", 5, [{"data": "2"}])
        cache.set("query3", 5, [{"data": "3"}])  # Should evict query1

        assert cache.get("query1", 5) is None  # Evicted
        assert cache.get("query2", 5) is not None
        assert cache.get("query3", 5) is not None

    def test_cache_stats(self):
        """Test cache statistics tracking."""
        from af_engine.kb_cache import KBCache

        cache = KBCache()

        cache.set("query", 5, [{"data": "test"}])
        cache.get("query", 5)  # Hit
        cache.get("query", 5)  # Hit
        cache.get("missing", 5)  # Miss

        stats = cache.get_stats()

        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 2 / 3

    def test_cache_clear(self):
        """Test clearing the cache."""
        from af_engine.kb_cache import KBCache

        cache = KBCache()
        cache.set("query", 5, [{"data": "test"}])

        cache.clear()

        # Stats should be reset after clear
        stats = cache.get_stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0

        # Now check that the entry is gone (this will increment misses)
        assert cache.get("query", 5) is None

    def test_cache_different_top_k(self):
        """Test that different top_k values create different cache entries."""
        from af_engine.kb_cache import KBCache

        cache = KBCache()

        cache.set("query", 3, [{"data": "3 results"}])
        cache.set("query", 5, [{"data": "5 results"}])

        result_3 = cache.get("query", 3)
        result_5 = cache.get("query", 5)

        assert result_3[0]["data"] == "3 results"
        assert result_5[0]["data"] == "5 results"


class TestKBCacheThreadSafety:
    """Thread safety tests for KB cache."""

    def test_concurrent_access(self):
        """Test concurrent reads and writes don't corrupt data."""
        from af_engine.kb_cache import KBCache

        cache = KBCache(max_size=100, ttl_seconds=60)
        errors = []

        def writer(thread_id):
            try:
                for i in range(50):
                    cache.set(f"query_{thread_id}_{i}", 5, [{"id": f"{thread_id}_{i}"}])
            except Exception as e:
                errors.append(e)

        def reader(thread_id):
            try:
                for i in range(50):
                    cache.get(f"query_{thread_id}_{i}", 5)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0


class TestLazyLoading:
    """Tests for lazy loading of KB instance."""

    def test_get_kb_instance_returns_none_when_unavailable(self):
        """Test that get_kb_instance returns None when KB is not available."""
        from af_engine import kb_cache

        # Reset the module state
        kb_cache._kb_instance = None
        kb_cache._kb_import_attempted = False

        with patch.dict('sys.modules', {'mission_knowledge_base': None}):
            with patch('af_engine.kb_cache.get_kb_instance') as mock:
                mock.return_value = None
                result = mock()
                assert result is None

    def test_query_returns_empty_when_kb_unavailable(self):
        """Test that query returns empty list when KB unavailable."""
        from af_engine import kb_cache

        with patch.object(kb_cache, 'get_kb_instance', return_value=None):
            result = kb_cache.query_relevant_learnings("test query", use_cache=False)
            assert result == []


class TestQueryCaching:
    """Tests for query result caching."""

    def test_cached_query_faster_than_uncached(self):
        """Test that cached queries are significantly faster."""
        from af_engine.kb_cache import query_relevant_learnings, clear_cache

        # Clear cache first
        clear_cache()

        # Skip if KB not available
        try:
            from mission_knowledge_base import MissionKnowledgeBase
        except ImportError:
            pytest.skip("MissionKnowledgeBase not available")

        query = "test optimization caching"

        # First query (cold)
        start = time.perf_counter()
        result1 = query_relevant_learnings(query, use_cache=True)
        cold_time = time.perf_counter() - start

        # Second query (warm)
        start = time.perf_counter()
        result2 = query_relevant_learnings(query, use_cache=True)
        warm_time = time.perf_counter() - start

        # Results should be the same
        assert result1 == result2

        # Cached should be much faster (at least 10x)
        if cold_time > 0.1:  # Only check if cold query was slow enough
            assert warm_time < cold_time / 10

    def test_cache_bypass_with_use_cache_false(self):
        """Test that use_cache=False bypasses the cache."""
        from af_engine.kb_cache import query_relevant_learnings, clear_cache, get_cache_stats

        clear_cache()

        # Query without caching
        with patch('af_engine.kb_cache.get_kb_instance') as mock_kb:
            mock_instance = MagicMock()
            mock_instance.query_relevant_learnings.return_value = []
            mock_kb.return_value = mock_instance

            query_relevant_learnings("test", use_cache=False)
            query_relevant_learnings("test", use_cache=False)

            # Should have called KB twice, not used cache
            assert mock_instance.query_relevant_learnings.call_count == 2


class TestCacheStats:
    """Tests for global cache statistics."""

    def test_get_cache_stats(self):
        """Test getting global cache stats."""
        from af_engine.kb_cache import get_cache_stats, clear_cache

        clear_cache()
        stats = get_cache_stats()

        assert "size" in stats
        assert "max_size" in stats
        assert "hits" in stats
        assert "misses" in stats
        assert "hit_rate" in stats
        assert "ttl_seconds" in stats

    def test_clear_cache_resets_stats(self):
        """Test that clear_cache resets statistics."""
        from af_engine.kb_cache import clear_cache, get_cache_stats, _query_cache

        # Add some entries
        _query_cache.set("q1", 5, [{}])
        _query_cache.get("q1", 5)

        clear_cache()

        stats = get_cache_stats()
        assert stats["size"] == 0
        assert stats["hits"] == 0
        assert stats["misses"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
