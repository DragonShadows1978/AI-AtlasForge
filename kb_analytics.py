#!/usr/bin/env python3
"""
Knowledge Base Analytics Module

Provides cross-mission analytics for the Knowledge Base:
1. Learning accumulation over time (per mission)
2. Learning types distribution
3. Top themes with frequency counts
4. Inter-mission learning transfer rate

Integrates with mission_analytics.py infrastructure and uses
learning chains data from MissionKnowledgeBase.

Usage:
    from kb_analytics import KBAnalytics

    analytics = KBAnalytics()

    # Get all analytics for dashboard
    data = analytics.get_dashboard_data()

    # Individual metrics
    accumulation = analytics.get_learning_accumulation()
    types_dist = analytics.get_type_distribution()
    themes = analytics.get_top_themes()
    transfer_rate = analytics.get_transfer_rate()
"""

import json
import sqlite3
import logging
import functools
import time
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from collections import Counter, defaultdict

logger = logging.getLogger(__name__)

# =============================================================================
# CACHING LAYER
# =============================================================================

_cache = {}
_cache_timestamps = {}
CACHE_TTL_SECONDS = 300  # 5 minutes
CHAIN_CACHE_TTL_SECONDS = 600  # 10 minutes for chains (expensive to compute)

# Cache statistics for monitoring
_cache_stats = {
    'hits': 0,
    'misses': 0,
    'last_reset': time.time()
}


def cached_query(key_prefix: str, ttl: int = CACHE_TTL_SECONDS):
    """
    Decorator for caching query results with TTL.

    Tracks cache hits and misses for monitoring via get_cache_stats().

    Args:
        key_prefix: Prefix for cache key (method name)
        ttl: Time-to-live in seconds (default 5 min)
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            global _cache_stats
            # Build cache key from prefix + args
            cache_key = f"{key_prefix}:{hash((args, tuple(sorted(kwargs.items()))))}"
            now = time.time()

            # Check for valid cached result
            if cache_key in _cache:
                cached_at = _cache_timestamps.get(cache_key, 0)
                if now - cached_at < ttl:
                    _cache_stats['hits'] += 1
                    logger.debug(f"Cache hit: {cache_key}")
                    return _cache[cache_key]

            # Cache miss - execute query and cache result
            _cache_stats['misses'] += 1
            result = func(self, *args, **kwargs)
            _cache[cache_key] = result
            _cache_timestamps[cache_key] = now
            logger.debug(f"Cache miss: {cache_key}, cached new result")
            return result
        return wrapper
    return decorator


def clear_kb_cache():
    """Clear all KB analytics cache."""
    global _cache, _cache_timestamps, _cache_stats
    _cache = {}
    _cache_timestamps = {}
    _cache_stats = {'hits': 0, 'misses': 0, 'last_reset': time.time()}
    logger.info("KB analytics cache cleared")


def get_cache_stats() -> Dict[str, Any]:
    """
    Get cache statistics for monitoring.

    Returns:
        Dict with:
        - hits: Number of cache hits since last reset
        - misses: Number of cache misses since last reset
        - hit_rate: Hit rate percentage (0-100)
        - cache_entries: Current number of cached entries
        - uptime_seconds: Seconds since cache was last reset
    """
    total = _cache_stats['hits'] + _cache_stats['misses']
    hit_rate = (_cache_stats['hits'] / total * 100) if total > 0 else 0
    return {
        'hits': _cache_stats['hits'],
        'misses': _cache_stats['misses'],
        'hit_rate': round(hit_rate, 1),
        'cache_entries': len(_cache),
        'uptime_seconds': round(time.time() - _cache_stats['last_reset'], 0),
        'ttl_seconds': CACHE_TTL_SECONDS,
        'chain_ttl_seconds': CHAIN_CACHE_TTL_SECONDS
    }


# =============================================================================
# FAST LEARNING CHAINS - Optimized implementation
# =============================================================================

class FastChainComputer:
    """
    Optimized learning chain computation using pre-computed similarity matrix.

    Algorithm: Union-Find (Disjoint Set Union)
    -----------------------------------------
    This class uses the Union-Find algorithm for fast connected component
    detection. Instead of O(n^2) similarity lookups per chain computation,
    we pre-compute the full similarity matrix once (O(n^2) space) and then
    use Union-Find operations which are O(n * alpha(n)) where alpha is the
    inverse Ackermann function (effectively constant).

    Performance Trade-offs:
    - Memory: O(n^2) for similarity matrix (~21MB for 2313 learnings)
    - Time: ~0.5s for chain computation vs ~5s previously
    - Speedup: 10-20x faster than per-learning similarity lookups

    Caching Strategy:
    - Similarity matrix is computed once and cached until invalidated
    - Chains are cached for 10 minutes (CHAIN_CACHE_TTL_SECONDS)
    - Call invalidate() when learnings are added/modified/deleted

    Implementation Notes:
    - Uses TF-IDF vectorization with (1,2)-gram features for text similarity
    - max_features=3000 limits vocabulary size for speed
    - Chains require cross-mission similarity (same-mission links excluded)
    - Theme generation uses most common domain in the chain

    Usage:
        chain_computer = get_fast_chain_computer()
        chains = chain_computer.get_learning_chains_fast(
            min_chain_length=2,
            similarity_threshold=0.5
        )

        # When learnings change:
        chain_computer.invalidate()
    """

    _instance = None
    _similarity_matrix = None
    _learning_ids = None
    _learning_missions = None
    _chains_cache = None
    _chains_cache_time = 0

    def __new__(cls, db_path: Path = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.db_path = db_path or KB_DB_PATH
        return cls._instance

    def _ensure_matrix(self) -> bool:
        """Build or reuse the similarity matrix."""
        if self._similarity_matrix is not None:
            return True

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity
            import numpy as np

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                SELECT learning_id, mission_id, title, description, timestamp
                FROM learnings
                ORDER BY timestamp ASC
            """)
            rows = cursor.fetchall()
            conn.close()

            if len(rows) < 3:
                return False

            # Store learning metadata
            self._learning_ids = [r[0] for r in rows]
            self._learning_missions = {r[0]: r[1] for r in rows}

            # Build texts for TF-IDF
            texts = [f"{r[2] or ''} {r[3] or ''}" for r in rows]

            # Vectorize
            vectorizer = TfidfVectorizer(
                min_df=1,
                max_df=0.95,
                ngram_range=(1, 2),
                stop_words='english',
                max_features=3000  # Reduced for speed
            )
            tfidf_matrix = vectorizer.fit_transform(texts)

            # Pre-compute full similarity matrix (this is the key optimization!)
            # For 2313 learnings, this is ~21MB but computed once
            self._similarity_matrix = cosine_similarity(tfidf_matrix)

            logger.info(f"FastChainComputer: Built {len(rows)}x{len(rows)} similarity matrix")
            return True

        except Exception as e:
            logger.error(f"Failed to build similarity matrix: {e}")
            return False

    def invalidate(self):
        """Invalidate cached data (call when learnings change)."""
        self._similarity_matrix = None
        self._learning_ids = None
        self._learning_missions = None
        self._chains_cache = None
        self._chains_cache_time = 0

    def get_learning_chains_fast(
        self,
        min_chain_length: int = 2,
        similarity_threshold: float = 0.5
    ) -> List[Dict[str, Any]]:
        """
        Get learning chains using pre-computed similarity matrix.

        This is 10-20x faster than the original implementation.

        Args:
            min_chain_length: Minimum learnings in a chain
            similarity_threshold: Minimum similarity for chain membership

        Returns:
            List of chain dicts with theme, coherence, learnings, missions
        """
        import numpy as np

        # Check cache first
        now = time.time()
        cache_key = f"chains_{min_chain_length}_{similarity_threshold}"
        if (self._chains_cache is not None and
            now - self._chains_cache_time < CHAIN_CACHE_TTL_SECONDS):
            return self._chains_cache

        if not self._ensure_matrix():
            return []

        n = len(self._learning_ids)
        if n < min_chain_length:
            return []

        # Use Union-Find for fast connected components
        parent = list(range(n))

        def find(x):
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        # Build chains by connecting similar learnings from different missions
        for i in range(n):
            for j in range(i + 1, n):
                if self._similarity_matrix[i, j] >= similarity_threshold:
                    # Only connect if from different missions (cross-mission chains)
                    if self._learning_missions[self._learning_ids[i]] != \
                       self._learning_missions[self._learning_ids[j]]:
                        union(i, j)

        # Group by connected component
        components = defaultdict(list)
        for i in range(n):
            components[find(i)].append(i)

        # Filter chains by size and cross-mission requirement
        chains = []
        for component_id, indices in components.items():
            if len(indices) < min_chain_length:
                continue

            # Get missions in this chain
            chain_missions = set(
                self._learning_missions[self._learning_ids[i]]
                for i in indices
            )

            # Only keep chains spanning 2+ missions
            if len(chain_missions) < 2:
                continue

            # Compute coherence (average pairwise similarity)
            if len(indices) > 1:
                sub_matrix = self._similarity_matrix[np.ix_(indices, indices)]
                # Get upper triangle (exclude diagonal)
                upper = np.triu_indices(len(indices), k=1)
                coherence = float(np.mean(sub_matrix[upper]))
            else:
                coherence = 1.0

            # Generate theme from top terms
            theme = self._generate_theme(indices)

            chains.append({
                'chain_id': len(chains),
                'theme': theme,
                'coherence': round(coherence, 3),
                'length': len(indices),
                'learning_ids': [self._learning_ids[i] for i in indices],
                'missions': list(chain_missions)
            })

        # Sort by length (longest first)
        chains.sort(key=lambda x: x['length'], reverse=True)

        # Cache results
        self._chains_cache = chains
        self._chains_cache_time = now

        return chains

    def _generate_theme(self, indices: List[int]) -> str:
        """Generate a theme name from learning indices using similarity."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()

            # Get titles for learnings in this chain
            placeholders = ','.join(['?' for _ in indices])
            learning_ids = [self._learning_ids[i] for i in indices]

            cursor.execute(f"""
                SELECT title, problem_domain FROM learnings
                WHERE learning_id IN ({placeholders})
            """, learning_ids)

            rows = cursor.fetchall()
            conn.close()

            # Count domain occurrences
            domain_counts = Counter()
            for title, domain in rows:
                if domain:
                    domain_counts[domain] += 1

            # Use most common domain as theme
            if domain_counts:
                theme = domain_counts.most_common(1)[0][0]
                return theme.replace('_', ' ').title()

            # Fallback: use first title prefix
            if rows and rows[0][0]:
                title = rows[0][0]
                return title[:30] + "..." if len(title) > 30 else title

            return f"Chain {len(indices)}"

        except Exception as e:
            logger.error(f"Failed to generate theme: {e}")
            return f"Chain {len(indices)}"


def get_fast_chain_computer() -> FastChainComputer:
    """Get or create the fast chain computer singleton."""
    return FastChainComputer()

# Paths
BASE_DIR = Path("/home/vader/mini-mind-v2")
KNOWLEDGE_DIR = BASE_DIR / "rde_data" / "knowledge_base"
KB_DB_PATH = KNOWLEDGE_DIR / "mission_knowledge.db"


class KBAnalytics:
    """
    Cross-mission analytics for the Knowledge Base.

    Provides insights into:
    - How knowledge accumulates over missions
    - What types of learnings are most common
    - Which themes appear most frequently
    - How often learnings transfer between missions
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialize the KB Analytics module.

        Args:
            db_path: Path to knowledge base SQLite DB (default: KB_DB_PATH)
        """
        self.db_path = db_path or KB_DB_PATH

    def _get_connection(self) -> sqlite3.Connection:
        """Get a database connection with PRAGMA optimizations."""
        conn = sqlite3.connect(self.db_path)
        # SQLite PRAGMA optimizations for read-heavy workloads
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    # =========================================================================
    # LEARNING ACCUMULATION OVER TIME
    # =========================================================================

    def get_learning_accumulation(self) -> Dict[str, Any]:
        """
        Get learning accumulation data showing total learnings per mission.

        Returns a time series of cumulative learning counts, useful for
        visualizing knowledge growth over time.

        Returns:
            Dict with:
            - missions: List of {mission_id, timestamp, count, cumulative}
            - total_learnings: Total count across all missions
            - avg_per_mission: Average learnings per mission
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get learnings grouped by mission, ordered by timestamp
            cursor.execute("""
                SELECT
                    l.mission_id,
                    MIN(l.timestamp) as first_learning,
                    COUNT(*) as learning_count
                FROM learnings l
                GROUP BY l.mission_id
                ORDER BY first_learning ASC
            """)

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return {
                    "missions": [],
                    "total_learnings": 0,
                    "avg_per_mission": 0.0,
                    "mission_count": 0
                }

            # Build accumulation data
            missions = []
            cumulative = 0

            for mission_id, timestamp, count in rows:
                cumulative += count
                missions.append({
                    "mission_id": mission_id,
                    "timestamp": timestamp,
                    "count": count,
                    "cumulative": cumulative
                })

            total = cumulative
            avg = total / len(missions) if missions else 0.0

            return {
                "missions": missions,
                "total_learnings": total,
                "avg_per_mission": round(avg, 2),
                "mission_count": len(missions)
            }

        except Exception as e:
            logger.error(f"Error getting learning accumulation: {e}")
            return {
                "missions": [],
                "total_learnings": 0,
                "avg_per_mission": 0.0,
                "mission_count": 0,
                "error": str(e)
            }

    # =========================================================================
    # LEARNING TYPES DISTRIBUTION
    # =========================================================================

    @cached_query("type_distribution")
    def get_type_distribution(self) -> Dict[str, Any]:
        """
        Get distribution of learning types across all missions.

        Returns counts for each learning_type (technique, insight, gotcha, etc.)
        Results are cached for 5 minutes.

        Returns:
            Dict with:
            - distribution: Dict of {type: count}
            - percentages: Dict of {type: percentage}
            - total: Total learning count
            - most_common: The most common type
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT learning_type, COUNT(*) as count
                FROM learnings
                GROUP BY learning_type
                ORDER BY count DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return {
                    "distribution": {},
                    "percentages": {},
                    "total": 0,
                    "most_common": None
                }

            distribution = {}
            total = 0

            for learning_type, count in rows:
                distribution[learning_type or "unknown"] = count
                total += count

            # Calculate percentages
            percentages = {}
            for lt, count in distribution.items():
                percentages[lt] = round((count / total) * 100, 1) if total > 0 else 0

            most_common = max(distribution.keys(), key=lambda k: distribution[k]) if distribution else None

            return {
                "distribution": distribution,
                "percentages": percentages,
                "total": total,
                "most_common": most_common
            }

        except Exception as e:
            logger.error(f"Error getting type distribution: {e}")
            return {
                "distribution": {},
                "percentages": {},
                "total": 0,
                "most_common": None,
                "error": str(e)
            }

    # =========================================================================
    # TOP THEMES WITH FREQUENCY
    # =========================================================================

    @cached_query("top_themes")
    def get_top_themes(self, top_n: int = 10) -> Dict[str, Any]:
        """
        Get top themes across all learning chains with frequency counts.
        Results are cached for 5 minutes.

        Extracts themes from:
        1. Problem domains
        2. Relevance keywords (parsed from JSON)
        3. Clusters (if semantic index is available)

        Args:
            top_n: Number of top themes to return

        Returns:
            Dict with:
            - themes: List of {theme, count, percentage}
            - total_themes: Total unique themes
            - domain_themes: Themes from problem_domain field
            - keyword_themes: Themes from relevance_keywords
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get domain distribution
            cursor.execute("""
                SELECT problem_domain, COUNT(*) as count
                FROM learnings
                WHERE problem_domain IS NOT NULL AND problem_domain != ''
                GROUP BY problem_domain
                ORDER BY count DESC
            """)
            domain_rows = cursor.fetchall()

            # Get all relevance keywords
            cursor.execute("""
                SELECT relevance_keywords FROM learnings
                WHERE relevance_keywords IS NOT NULL
            """)
            keyword_rows = cursor.fetchall()
            conn.close()

            # Count domain themes
            domain_counter = Counter()
            for domain, count in domain_rows:
                if domain:
                    # Clean up domain name
                    clean_domain = domain.replace("_", " ").title()
                    domain_counter[clean_domain] = count

            # Count keyword themes
            keyword_counter = Counter()
            for (kw_json,) in keyword_rows:
                try:
                    keywords = json.loads(kw_json) if kw_json else []
                    for kw in keywords:
                        if kw and len(kw) > 3:  # Skip very short keywords
                            keyword_counter[kw.lower()] += 1
                except (json.JSONDecodeError, TypeError):
                    continue

            # Combine for top themes (weighted: domains x3, keywords x1)
            combined = Counter()
            for domain, count in domain_counter.items():
                combined[domain] += count * 3
            for kw, count in keyword_counter.items():
                # Clean up keyword
                clean_kw = kw.replace("_", " ").title()
                combined[clean_kw] += count

            # Get top themes
            total_mentions = sum(combined.values())
            top_themes = []
            for theme, count in combined.most_common(top_n):
                top_themes.append({
                    "theme": theme,
                    "count": count,
                    "percentage": round((count / total_mentions) * 100, 1) if total_mentions > 0 else 0
                })

            # Domain-only themes for separate display
            domain_themes = [
                {"theme": d, "count": c}
                for d, c in domain_counter.most_common(top_n)
            ]

            # Top keywords for separate display
            keyword_themes = [
                {"theme": k.title(), "count": c}
                for k, c in keyword_counter.most_common(top_n)
            ]

            return {
                "themes": top_themes,
                "total_themes": len(combined),
                "domain_themes": domain_themes,
                "keyword_themes": keyword_themes
            }

        except Exception as e:
            logger.error(f"Error getting top themes: {e}")
            return {
                "themes": [],
                "total_themes": 0,
                "domain_themes": [],
                "keyword_themes": [],
                "error": str(e)
            }

    # =========================================================================
    # INTER-MISSION LEARNING TRANSFER RATE
    # =========================================================================

    def get_transfer_rate(self) -> Dict[str, Any]:
        """
        Calculate inter-mission learning transfer rate.

        Measures how often learnings from one mission inform subsequent missions.
        This is computed by:
        1. Counting missions with related learnings from previous missions
        2. Analyzing learning chains that span multiple missions
        3. Measuring domain continuity between consecutive missions

        Returns:
            Dict with:
            - transfer_rate: Percentage of missions with knowledge transfer (0-100)
            - missions_with_transfer: Count of missions that built on previous learnings
            - total_missions: Total mission count
            - chain_count: Number of learning chains spanning missions
            - avg_chain_length: Average length of cross-mission chains
            - domain_continuity: Rate of missions in same domain as previous
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get all missions ordered by timestamp
            cursor.execute("""
                SELECT mission_id, problem_domain, timestamp
                FROM mission_summaries
                ORDER BY timestamp ASC
            """)
            missions = cursor.fetchall()

            # Get learnings with their missions
            cursor.execute("""
                SELECT learning_id, mission_id, problem_domain, title, description
                FROM learnings
                ORDER BY mission_id, timestamp
            """)
            learnings = cursor.fetchall()
            conn.close()

            if not missions or len(missions) < 2:
                return {
                    "transfer_rate": 0.0,
                    "missions_with_transfer": 0,
                    "total_missions": len(missions) if missions else 0,
                    "chain_count": 0,
                    "avg_chain_length": 0.0,
                    "domain_continuity": 0.0
                }

            # Build mission order and domain map
            mission_order = [m[0] for m in missions]
            mission_domains = {m[0]: m[1] for m in missions}

            # Group learnings by mission
            learnings_by_mission = defaultdict(list)
            for lid, mid, domain, title, desc in learnings:
                learnings_by_mission[mid].append({
                    "id": lid,
                    "domain": domain,
                    "title": title or "",
                    "desc": desc or ""
                })

            # Calculate domain continuity
            domain_matches = 0
            for i in range(1, len(mission_order)):
                prev_domain = mission_domains.get(mission_order[i-1])
                curr_domain = mission_domains.get(mission_order[i])
                if prev_domain and curr_domain and prev_domain == curr_domain:
                    domain_matches += 1

            domain_continuity = (domain_matches / (len(mission_order) - 1)) * 100 if len(mission_order) > 1 else 0

            # Estimate transfer by looking for shared keywords between consecutive missions
            missions_with_transfer = 0

            for i in range(1, len(mission_order)):
                prev_mission = mission_order[i-1]
                curr_mission = mission_order[i]

                prev_learnings = learnings_by_mission.get(prev_mission, [])
                curr_learnings = learnings_by_mission.get(curr_mission, [])

                if not prev_learnings or not curr_learnings:
                    continue

                # Extract keywords from previous mission
                prev_keywords = set()
                for l in prev_learnings:
                    words = (l["title"] + " " + l["desc"]).lower().split()
                    prev_keywords.update(w for w in words if len(w) > 4)

                # Check if current mission uses similar concepts
                curr_text = " ".join(l["title"] + " " + l["desc"] for l in curr_learnings).lower()

                # Count keyword overlaps
                overlap_count = sum(1 for kw in prev_keywords if kw in curr_text)

                # If significant overlap (>5 keywords), count as transfer
                if overlap_count > 5:
                    missions_with_transfer += 1

            total_missions = len(mission_order)
            transfer_rate = (missions_with_transfer / (total_missions - 1)) * 100 if total_missions > 1 else 0

            # Try to get learning chains from the KB
            try:
                from mission_knowledge_base import MissionKnowledgeBase
                kb = MissionKnowledgeBase()
                chains = kb.get_learning_chains(min_chain_length=2, similarity_threshold=0.5)
                chain_count = len(chains)
                avg_chain_length = sum(c.get("length", 0) for c in chains) / len(chains) if chains else 0
            except Exception:
                chain_count = 0
                avg_chain_length = 0.0

            return {
                "transfer_rate": round(transfer_rate, 1),
                "missions_with_transfer": missions_with_transfer,
                "total_missions": total_missions,
                "chain_count": chain_count,
                "avg_chain_length": round(avg_chain_length, 1),
                "domain_continuity": round(domain_continuity, 1)
            }

        except Exception as e:
            logger.error(f"Error calculating transfer rate: {e}")
            return {
                "transfer_rate": 0.0,
                "missions_with_transfer": 0,
                "total_missions": 0,
                "chain_count": 0,
                "avg_chain_length": 0.0,
                "domain_continuity": 0.0,
                "error": str(e)
            }

    def get_transfer_rate_fast(self, chain_count: int = 0, avg_chain_length: float = 0.0) -> Dict[str, Any]:
        """
        Fast version of transfer rate calculation that skips the slow learning chains call.
        Uses pre-computed chain data passed from get_dashboard_data.

        Args:
            chain_count: Pre-computed count of learning chains
            avg_chain_length: Pre-computed average chain length

        Returns:
            Dict with transfer rate metrics
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get all missions ordered by timestamp
            cursor.execute("""
                SELECT mission_id, problem_domain, timestamp
                FROM mission_summaries
                ORDER BY timestamp ASC
            """)
            missions = cursor.fetchall()

            # Get learnings with their missions
            cursor.execute("""
                SELECT learning_id, mission_id, problem_domain, title, description
                FROM learnings
                ORDER BY mission_id, timestamp
            """)
            learnings = cursor.fetchall()
            conn.close()

            if not missions or len(missions) < 2:
                return {
                    "transfer_rate": 0.0,
                    "missions_with_transfer": 0,
                    "total_missions": len(missions) if missions else 0,
                    "chain_count": chain_count,
                    "avg_chain_length": round(avg_chain_length, 1),
                    "domain_continuity": 0.0
                }

            # Build mission order and domain map
            mission_order = [m[0] for m in missions]
            mission_domains = {m[0]: m[1] for m in missions}

            # Group learnings by mission
            learnings_by_mission = defaultdict(list)
            for lid, mid, domain, title, desc in learnings:
                learnings_by_mission[mid].append({
                    "id": lid,
                    "domain": domain,
                    "title": title or "",
                    "desc": desc or ""
                })

            # Calculate domain continuity
            domain_matches = 0
            for i in range(1, len(mission_order)):
                prev_domain = mission_domains.get(mission_order[i-1])
                curr_domain = mission_domains.get(mission_order[i])
                if prev_domain and curr_domain and prev_domain == curr_domain:
                    domain_matches += 1

            domain_continuity = (domain_matches / (len(mission_order) - 1)) * 100 if len(mission_order) > 1 else 0

            # Estimate transfer by looking for shared keywords between consecutive missions
            missions_with_transfer = 0

            for i in range(1, len(mission_order)):
                prev_mission = mission_order[i-1]
                curr_mission = mission_order[i]

                prev_learnings = learnings_by_mission.get(prev_mission, [])
                curr_learnings = learnings_by_mission.get(curr_mission, [])

                if not prev_learnings or not curr_learnings:
                    continue

                # Extract keywords from previous mission
                prev_keywords = set()
                for l in prev_learnings:
                    words = (l["title"] + " " + l["desc"]).lower().split()
                    prev_keywords.update(w for w in words if len(w) > 4)

                # Check if current mission uses similar concepts
                curr_text = " ".join(l["title"] + " " + l["desc"] for l in curr_learnings).lower()

                # Count keyword overlaps
                overlap_count = sum(1 for kw in prev_keywords if kw in curr_text)

                # If significant overlap (>5 keywords), count as transfer
                if overlap_count > 5:
                    missions_with_transfer += 1

            total_missions = len(mission_order)
            transfer_rate = (missions_with_transfer / (total_missions - 1)) * 100 if total_missions > 1 else 0

            return {
                "transfer_rate": round(transfer_rate, 1),
                "missions_with_transfer": missions_with_transfer,
                "total_missions": total_missions,
                "chain_count": chain_count,
                "avg_chain_length": round(avg_chain_length, 1),
                "domain_continuity": round(domain_continuity, 1)
            }

        except Exception as e:
            logger.error(f"Error calculating transfer rate (fast): {e}")
            return {
                "transfer_rate": 0.0,
                "missions_with_transfer": 0,
                "total_missions": 0,
                "chain_count": chain_count,
                "avg_chain_length": round(avg_chain_length, 1),
                "domain_continuity": 0.0,
                "error": str(e)
            }

    # =========================================================================
    # SOURCE DISTRIBUTION
    # =========================================================================

    @cached_query("source_distribution")
    def get_source_distribution(self) -> Dict[str, Any]:
        """
        Get distribution of learning sources (cycle_summary, achievement, etc.)
        Results are cached for 5 minutes.

        Returns:
            Dict with source -> count mapping
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT lesson_source, COUNT(*) as count
                FROM learnings
                GROUP BY lesson_source
                ORDER BY count DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            distribution = {}
            for source, count in rows:
                distribution[source or "unknown"] = count

            return {
                "distribution": distribution,
                "total": sum(distribution.values())
            }

        except Exception as e:
            logger.error(f"Error getting source distribution: {e}")
            return {"distribution": {}, "total": 0, "error": str(e)}

    # =========================================================================
    # INVESTIGATION ANALYTICS
    # =========================================================================

    @cached_query("investigation_analytics")
    def get_investigation_analytics(self) -> Dict[str, Any]:
        """
        Get analytics specific to investigation-sourced learnings.

        Results are cached for 5 minutes.

        Returns:
            Dict with:
            - total_investigations: Count of distinct investigations
            - total_learnings: Total learnings from investigations
            - by_type: Distribution by learning type
            - by_domain: Distribution by problem domain
            - recent_queries: Recent investigation queries with IDs
            - source_type_distribution: mission vs investigation counts
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Count investigation vs mission learnings
            cursor.execute("""
                SELECT source_type, COUNT(*) as count
                FROM learnings
                GROUP BY source_type
            """)
            source_type_dist = dict(cursor.fetchall())
            # Default source_type to 'mission' for rows where it's NULL
            if None in source_type_dist:
                source_type_dist['mission'] = source_type_dist.get('mission', 0) + source_type_dist.pop(None)

            # Count distinct investigations
            cursor.execute("""
                SELECT COUNT(DISTINCT source_investigation_id) FROM learnings
                WHERE source_type = 'investigation'
            """)
            investigation_count = cursor.fetchone()[0]

            # Count investigation learnings
            cursor.execute("""
                SELECT COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
            """)
            investigation_learnings = cursor.fetchone()[0]

            # Count by learning type for investigations
            cursor.execute("""
                SELECT learning_type, COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
                GROUP BY learning_type
            """)
            by_type = dict(cursor.fetchall())

            # Count by domain for investigations
            cursor.execute("""
                SELECT problem_domain, COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
                GROUP BY problem_domain
            """)
            by_domain = dict(cursor.fetchall())

            # Get recent investigation queries
            cursor.execute("""
                SELECT DISTINCT investigation_query, source_investigation_id, timestamp
                FROM learnings
                WHERE source_type = 'investigation'
                  AND investigation_query IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 10
            """)
            recent_queries = [
                {"query": r[0], "investigation_id": r[1], "timestamp": r[2]}
                for r in cursor.fetchall()
            ]

            conn.close()

            return {
                "total_investigations": investigation_count,
                "total_learnings": investigation_learnings,
                "by_type": by_type,
                "by_domain": by_domain,
                "recent_queries": recent_queries,
                "source_type_distribution": source_type_dist
            }

        except Exception as e:
            logger.error(f"Error getting investigation analytics: {e}")
            return {
                "total_investigations": 0,
                "total_learnings": 0,
                "by_type": {},
                "by_domain": {},
                "recent_queries": [],
                "source_type_distribution": {"mission": 0, "investigation": 0},
                "error": str(e)
            }

    # =========================================================================
    # OUTCOME ANALYSIS
    # =========================================================================

    @cached_query("outcome_analysis")
    def get_outcome_analysis(self) -> Dict[str, Any]:
        """
        Analyze outcomes across missions and learnings.
        Results are cached for 5 minutes.

        Returns:
            Dict with outcome metrics
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Mission outcomes
            cursor.execute("""
                SELECT outcome, COUNT(*) as count
                FROM mission_summaries
                GROUP BY outcome
            """)
            mission_outcomes = dict(cursor.fetchall())

            # Learning outcomes
            cursor.execute("""
                SELECT outcome, COUNT(*) as count
                FROM learnings
                GROUP BY outcome
            """)
            learning_outcomes = dict(cursor.fetchall())

            conn.close()

            # Calculate success rates
            total_missions = sum(mission_outcomes.values())
            successful_missions = mission_outcomes.get("success", 0)
            mission_success_rate = (successful_missions / total_missions * 100) if total_missions > 0 else 0

            return {
                "mission_outcomes": mission_outcomes,
                "learning_outcomes": learning_outcomes,
                "mission_success_rate": round(mission_success_rate, 1),
                "total_missions": total_missions
            }

        except Exception as e:
            logger.error(f"Error getting outcome analysis: {e}")
            return {
                "mission_outcomes": {},
                "learning_outcomes": {},
                "mission_success_rate": 0.0,
                "total_missions": 0,
                "error": str(e)
            }

    # =========================================================================
    # LEARNINGS BY THEME (DRILL-DOWN)
    # =========================================================================

    def get_learnings_by_theme(self, theme: str, theme_type: str = 'domain') -> Dict[str, Any]:
        """
        Get learnings that contributed to a specific theme.

        Args:
            theme: The theme name to search for
            theme_type: 'domain' for problem_domain, 'keyword' for relevance_keywords

        Returns:
            Dict with learnings grouped by mission
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            if theme_type == 'domain':
                # Clean theme back to domain format
                domain = theme.lower().replace(" ", "_")
                cursor.execute("""
                    SELECT learning_id, mission_id, title, description, learning_type, timestamp
                    FROM learnings
                    WHERE LOWER(problem_domain) = ?
                    ORDER BY mission_id, timestamp
                """, (domain,))
            else:
                # Search in relevance_keywords JSON
                cursor.execute("""
                    SELECT learning_id, mission_id, title, description, learning_type, timestamp
                    FROM learnings
                    WHERE relevance_keywords LIKE ?
                    ORDER BY mission_id, timestamp
                """, (f'%"{theme.lower()}"%',))

            rows = cursor.fetchall()
            conn.close()

            # Group by mission
            grouped = defaultdict(list)
            for row in rows:
                grouped[row[1]].append({
                    'learning_id': row[0],
                    'title': row[2],
                    'description': row[3],
                    'type': row[4],
                    'timestamp': row[5]
                })

            return {
                'theme': theme,
                'theme_type': theme_type,
                'total_learnings': len(rows),
                'mission_count': len(grouped),
                'by_mission': dict(grouped)
            }

        except Exception as e:
            logger.error(f"Error getting learnings by theme: {e}")
            return {
                'theme': theme,
                'theme_type': theme_type,
                'total_learnings': 0,
                'mission_count': 0,
                'by_mission': {},
                'error': str(e)
            }

    # =========================================================================
    # MISSION PROFILE (FOR COMPARISON)
    # =========================================================================

    def get_mission_profile(self, mission_id: str) -> Dict[str, Any]:
        """
        Get full analytics profile for a single mission.

        Args:
            mission_id: The mission ID to get profile for

        Returns:
            Dict with mission analytics data
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Get learnings types for this mission
            cursor.execute("""
                SELECT learning_type, COUNT(*) FROM learnings
                WHERE mission_id = ? GROUP BY learning_type
            """, (mission_id,))
            types = dict(cursor.fetchall())

            # Get themes (domains)
            cursor.execute("""
                SELECT problem_domain, COUNT(*) FROM learnings
                WHERE mission_id = ? AND problem_domain IS NOT NULL
                GROUP BY problem_domain ORDER BY COUNT(*) DESC LIMIT 5
            """, (mission_id,))
            themes = cursor.fetchall()

            # Get total count
            cursor.execute("SELECT COUNT(*) FROM learnings WHERE mission_id = ?", (mission_id,))
            total = cursor.fetchone()[0]

            # Get timestamp range
            cursor.execute("""
                SELECT MIN(timestamp), MAX(timestamp) FROM learnings
                WHERE mission_id = ?
            """, (mission_id,))
            timestamps = cursor.fetchone()

            conn.close()

            return {
                'mission_id': mission_id,
                'total_learnings': total,
                'type_distribution': types,
                'top_themes': [{'theme': t[0].replace('_', ' ').title() if t[0] else 'Unknown', 'count': t[1]} for t in themes],
                'first_learning': timestamps[0] if timestamps else None,
                'last_learning': timestamps[1] if timestamps else None
            }

        except Exception as e:
            logger.error(f"Error getting mission profile: {e}")
            return {
                'mission_id': mission_id,
                'total_learnings': 0,
                'type_distribution': {},
                'top_themes': [],
                'error': str(e)
            }

    # =========================================================================
    # LIST ALL MISSIONS (FOR COMPARISON SELECTOR)
    # =========================================================================

    def get_all_missions(self) -> List[Dict[str, Any]]:
        """
        Get list of all missions with basic stats for selection UI.

        Returns:
            List of mission summaries
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    l.mission_id,
                    COUNT(*) as learning_count,
                    MIN(l.timestamp) as first_learning,
                    MAX(l.timestamp) as last_learning
                FROM learnings l
                GROUP BY l.mission_id
                ORDER BY first_learning DESC
            """)

            rows = cursor.fetchall()
            conn.close()

            return [{
                'mission_id': row[0],
                'learning_count': row[1],
                'first_learning': row[2],
                'last_learning': row[3]
            } for row in rows]

        except Exception as e:
            logger.error(f"Error getting all missions: {e}")
            return []

    # =========================================================================
    # LEARNING CHAINS (FOR VISUALIZATION)
    # =========================================================================

    def get_learning_chains(self) -> List[Dict[str, Any]]:
        """
        Get learning chains data for visualization.

        Uses FastChainComputer for 10-20x speedup over original implementation.

        Returns:
            List of chain objects with missions and themes
        """
        try:
            # Use the optimized fast chain computer
            chain_computer = get_fast_chain_computer()
            chains = chain_computer.get_learning_chains_fast(
                min_chain_length=2,
                similarity_threshold=0.5
            )
            return chains
        except Exception as e:
            logger.error(f"Error getting learning chains (fast): {e}")
            # Fallback to original implementation
            try:
                from mission_knowledge_base import MissionKnowledgeBase
                kb = MissionKnowledgeBase()
                chains = kb.get_learning_chains(min_chain_length=2, similarity_threshold=0.5)
                return chains
            except Exception as e2:
                logger.error(f"Error getting learning chains (fallback): {e2}")
                return []

    # =========================================================================
    # DATE FILTERED ACCUMULATION
    # =========================================================================

    def get_learning_accumulation_filtered(self, start_date: str = None, end_date: str = None, source_type: str = None) -> Dict[str, Any]:
        """
        Get learning accumulation data with optional date and source filtering.

        Args:
            start_date: ISO format date string for start filter
            end_date: ISO format date string for end filter
            source_type: 'mission', 'investigation', or None for all

        Returns:
            Dict with filtered accumulation data
        """
        try:
            conn = self._get_connection()
            cursor = conn.cursor()

            # Build query with optional filters
            where_clause = ""
            params = []

            if start_date:
                where_clause += " AND l.timestamp >= ?"
                params.append(start_date)
            if end_date:
                where_clause += " AND l.timestamp <= ?"
                params.append(end_date)
            if source_type:
                where_clause += " AND l.source_type = ?"
                params.append(source_type)

            cursor.execute(f"""
                SELECT
                    l.mission_id,
                    MIN(l.timestamp) as first_learning,
                    COUNT(*) as learning_count
                FROM learnings l
                WHERE 1=1 {where_clause}
                GROUP BY l.mission_id
                ORDER BY first_learning ASC
            """, params)

            rows = cursor.fetchall()
            conn.close()

            if not rows:
                return {
                    "missions": [],
                    "total_learnings": 0,
                    "avg_per_mission": 0.0,
                    "mission_count": 0,
                    "start_date": start_date,
                    "end_date": end_date
                }

            # Build accumulation data
            missions = []
            cumulative = 0

            for mission_id, timestamp, count in rows:
                cumulative += count
                missions.append({
                    "mission_id": mission_id,
                    "timestamp": timestamp,
                    "count": count,
                    "cumulative": cumulative
                })

            total = cumulative
            avg = total / len(missions) if missions else 0.0

            return {
                "missions": missions,
                "total_learnings": total,
                "avg_per_mission": round(avg, 2),
                "mission_count": len(missions),
                "start_date": start_date,
                "end_date": end_date
            }

        except Exception as e:
            logger.error(f"Error getting filtered learning accumulation: {e}")
            return {
                "missions": [],
                "total_learnings": 0,
                "avg_per_mission": 0.0,
                "mission_count": 0,
                "error": str(e)
            }

    # =========================================================================
    # COMBINED DASHBOARD DATA
    # =========================================================================

    def get_dashboard_data(self, start_date: str = None, end_date: str = None, source_type: str = None) -> Dict[str, Any]:
        """
        Get all analytics data for the dashboard widget.

        Args:
            start_date: Optional ISO format date string for start filter
            end_date: Optional ISO format date string for end filter
            source_type: Optional filter for 'mission' or 'investigation' sourced learnings

        Returns:
            Dict containing all analytics metrics
        """
        # Use filtered accumulation if any filter provided
        if start_date or end_date or source_type:
            accumulation = self.get_learning_accumulation_filtered(start_date, end_date, source_type)
        else:
            accumulation = self.get_learning_accumulation()

        # Get learning chains ONCE to avoid duplicate calls (transfer_rate also uses chains)
        learning_chains = self.get_learning_chains()
        chain_count = len(learning_chains) if isinstance(learning_chains, list) else 0
        avg_chain_length = sum(c.get("length", 0) for c in learning_chains) / len(learning_chains) if learning_chains else 0

        # Get transfer rate using pre-computed chains
        transfer_rate = self.get_transfer_rate_fast(chain_count, avg_chain_length)

        return {
            "accumulation": accumulation,
            "type_distribution": self.get_type_distribution(),
            "top_themes": self.get_top_themes(),
            "transfer_rate": transfer_rate,
            "source_distribution": self.get_source_distribution(),
            "outcome_analysis": self.get_outcome_analysis(),
            "learning_chains": learning_chains,
            "investigation_analytics": self.get_investigation_analytics(),
            "generated_at": datetime.now().isoformat()
        }


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

_kb_analytics_instance = None

def get_kb_analytics() -> KBAnalytics:
    """Get or create the global KB analytics instance."""
    global _kb_analytics_instance
    if _kb_analytics_instance is None:
        _kb_analytics_instance = KBAnalytics()
    return _kb_analytics_instance


# =============================================================================
# MAIN (Self-test)
# =============================================================================

if __name__ == "__main__":
    import pprint

    print("=" * 60)
    print("KB Analytics - Self Test")
    print("=" * 60)

    analytics = KBAnalytics()

    print("\n[1] Learning Accumulation:")
    accum = analytics.get_learning_accumulation()
    print(f"    Total learnings: {accum['total_learnings']}")
    print(f"    Mission count: {accum['mission_count']}")
    print(f"    Avg per mission: {accum['avg_per_mission']}")
    if accum['missions']:
        print(f"    Sample missions: {accum['missions'][:3]}")

    print("\n[2] Type Distribution:")
    types = analytics.get_type_distribution()
    print(f"    Distribution: {types['distribution']}")
    print(f"    Most common: {types['most_common']}")

    print("\n[3] Top Themes:")
    themes = analytics.get_top_themes(top_n=5)
    print(f"    Total themes: {themes['total_themes']}")
    for t in themes['themes'][:5]:
        print(f"    - {t['theme']}: {t['count']} ({t['percentage']}%)")

    print("\n[4] Transfer Rate:")
    transfer = analytics.get_transfer_rate()
    print(f"    Transfer rate: {transfer['transfer_rate']}%")
    print(f"    Missions with transfer: {transfer['missions_with_transfer']}/{transfer['total_missions']}")
    print(f"    Domain continuity: {transfer['domain_continuity']}%")
    print(f"    Learning chains: {transfer['chain_count']}")

    print("\n[5] Source Distribution:")
    sources = analytics.get_source_distribution()
    print(f"    Sources: {sources['distribution']}")

    print("\n[6] Outcome Analysis:")
    outcomes = analytics.get_outcome_analysis()
    print(f"    Mission outcomes: {outcomes['mission_outcomes']}")
    print(f"    Success rate: {outcomes['mission_success_rate']}%")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
