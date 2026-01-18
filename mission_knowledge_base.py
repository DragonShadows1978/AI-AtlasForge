#!/usr/bin/env python3
"""
Mission Knowledge Base - Cross-Mission Learning System

This module provides persistent learning across missions by:
1. Extracting insights from completed missions
2. Storing them in a searchable format (SQLite + embeddings)
3. Querying relevant past learnings for new missions
4. Injecting context into PLANNING stage prompts

Usage:
    kb = MissionKnowledgeBase()

    # After mission completion
    kb.ingest_completed_mission(mission_log_path)

    # At mission start
    context = kb.generate_planning_context(new_problem_statement)
"""

import json
import sqlite3
import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field, asdict
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.cluster import AgglomerativeClustering

logger = logging.getLogger(__name__)

# Paths - use centralized configuration
from atlasforge_config import BASE_DIR, KNOWLEDGE_BASE_DIR, MISSIONS_DIR
KNOWLEDGE_DIR = KNOWLEDGE_BASE_DIR
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"

# Ensure directories exist
KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class MissionLearning:
    """A learning extracted from a completed mission"""
    learning_id: str
    mission_id: str
    learning_type: str  # technique, insight, gotcha, template, failure
    title: str
    description: str
    problem_domain: str  # e.g., "GPU optimization", "file parsing", "API integration"
    outcome: str  # success, partial, failure
    relevance_keywords: List[str] = field(default_factory=list)
    code_snippets: List[str] = field(default_factory=list)
    files_created: List[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    lesson_source: str = "unknown"  # cycle_summary, achievement, issue, history, final_summary
    source_type: str = "mission"  # 'mission' or 'investigation'
    source_investigation_id: Optional[str] = None  # investigation ID if from investigation
    investigation_query: Optional[str] = None  # original investigation query

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'MissionLearning':
        # Handle fields that might not exist in old data
        allowed_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        return cls(**filtered_data)


@dataclass
class MissionSummary:
    """Summary of a completed mission for the knowledge base"""
    mission_id: str
    problem_statement: str
    problem_domain: str
    outcome: str  # success, partial, failure
    approach_taken: str
    key_learnings: List[str]
    failures_encountered: List[str]
    files_created: List[str]
    duration_minutes: float
    cycles_used: int
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class SemanticIndex:
    """
    Manages TF-IDF vectorization and semantic similarity operations.

    This class provides:
    - TF-IDF vectorization of learning descriptions
    - Cosine similarity-based querying
    - Duplicate detection via similarity threshold
    - Agglomerative clustering of learnings
    - Incremental index updates (adds new learnings without full rebuild)
    - Hierarchical clustering with coherence scores
    """

    # Threshold for triggering full rebuild vs incremental update
    REBUILD_THRESHOLD = 0.2  # Rebuild if more than 20% new learnings

    def __init__(self, db_path: Path):
        """Initialize the semantic index.

        Args:
            db_path: Path to the SQLite database containing learnings
        """
        self.db_path = db_path
        self.vectorizer = TfidfVectorizer(
            min_df=1,  # Include terms that appear in at least 1 document (small corpus)
            max_df=0.95,  # Exclude terms in > 95% of documents
            ngram_range=(1, 2),  # Include unigrams and bigrams
            stop_words='english',  # Remove common English words
            sublinear_tf=True,  # Apply sublinear TF scaling
            norm='l2',  # L2 normalize for cosine similarity via dot product
            max_features=5000  # Limit vocabulary size
        )
        self.tfidf_matrix = None
        self.learning_ids: List[str] = []
        self.learning_descriptions: List[str] = []
        self._fitted = False
        self._cluster_cache: Optional[Dict[int, List[str]]] = None
        self._cluster_threshold: Optional[float] = None
        self._pending_additions: List[Tuple[str, str]] = []  # (learning_id, text) pairs
        self._hierarchical_cache: Optional[Dict[str, Any]] = None
        self._coherence_cache: Dict[int, float] = {}

    def fit(self) -> bool:
        """
        Load all learnings from database and fit the TF-IDF vectorizer.

        This performs a full rebuild from the database. Any pending additions
        are cleared since they're already stored in the database.

        Returns:
            True if successfully fitted, False otherwise
        """
        try:
            # Clear pending additions - they're already in the database
            self._pending_additions = []

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT learning_id, title, description, problem_domain
                    FROM learnings
                """)
                rows = cursor.fetchall()

            if not rows:
                logger.warning("No learnings found in database for SemanticIndex")
                self._fitted = False
                return False

            # Combine title, description, and domain for richer representation
            self.learning_ids = []
            self.learning_descriptions = []

            for learning_id, title, description, domain in rows:
                self.learning_ids.append(learning_id)
                # Combine fields for better TF-IDF representation
                combined_text = f"{title or ''} {description or ''} {domain or ''}".strip()
                self.learning_descriptions.append(combined_text if combined_text else "empty")

            # Fit and transform
            self.tfidf_matrix = self.vectorizer.fit_transform(self.learning_descriptions)
            self._fitted = True
            self._cluster_cache = None  # Invalidate cluster cache
            self._hierarchical_cache = None
            self._coherence_cache = {}

            logger.info(f"SemanticIndex fitted with {len(self.learning_ids)} learnings, "
                       f"vocabulary size: {len(self.vectorizer.vocabulary_)}")
            return True

        except Exception as e:
            logger.error(f"Failed to fit SemanticIndex: {e}")
            self._fitted = False
            return False

    def query(self, text: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Query the index and return similar learnings with scores.

        Args:
            text: Query text
            top_k: Maximum number of results to return

        Returns:
            List of (learning_id, similarity_score) tuples, sorted by score desc
        """
        # Ensure index is up to date (applies pending additions if any)
        if not self.ensure_up_to_date():
            return []

        if self.tfidf_matrix is None or len(self.learning_ids) == 0:
            return []

        try:
            # Transform query text
            query_vector = self.vectorizer.transform([text])

            # Compute cosine similarities
            similarities = cosine_similarity(query_vector, self.tfidf_matrix).flatten()

            # Get top k indices
            top_indices = np.argsort(similarities)[::-1][:top_k]

            # Return (learning_id, score) pairs
            results = []
            for idx in top_indices:
                if similarities[idx] > 0:  # Only include non-zero similarities
                    results.append((self.learning_ids[idx], float(similarities[idx])))

            return results

        except Exception as e:
            logger.error(f"Query failed: {e}")
            return []

    def find_duplicates(self, threshold: float = 0.85) -> List[Dict[str, Any]]:
        """
        Find groups of near-duplicate learnings.

        Args:
            threshold: Minimum cosine similarity to consider as duplicate (0.0-1.0)

        Returns:
            List of dicts with keys:
            - learning_ids: List of similar learning IDs
            - similarity: Average pairwise similarity
            - representative: ID of suggested learning to keep
        """
        # Ensure index is up to date
        if not self.ensure_up_to_date():
            return []

        if self.tfidf_matrix is None or len(self.learning_ids) < 2:
            return []

        try:
            # Compute pairwise similarities
            sim_matrix = cosine_similarity(self.tfidf_matrix)

            # Find groups using union-find approach
            n = len(self.learning_ids)
            visited = set()
            groups = []

            for i in range(n):
                if i in visited:
                    continue

                # Find all learnings similar to this one
                similar_indices = []
                for j in range(n):
                    if i != j and sim_matrix[i, j] >= threshold:
                        similar_indices.append(j)

                if similar_indices:
                    group_indices = [i] + similar_indices
                    group_ids = [self.learning_ids[idx] for idx in group_indices]

                    # Calculate average similarity within group
                    total_sim = 0
                    count = 0
                    for gi in range(len(group_indices)):
                        for gj in range(gi + 1, len(group_indices)):
                            total_sim += sim_matrix[group_indices[gi], group_indices[gj]]
                            count += 1
                    avg_sim = total_sim / count if count > 0 else threshold

                    # Mark all as visited
                    for idx in group_indices:
                        visited.add(idx)

                    # Representative is the one with longest description
                    descriptions = [self.learning_descriptions[idx] for idx in group_indices]
                    rep_idx = max(range(len(group_indices)),
                                  key=lambda x: len(descriptions[x]))

                    groups.append({
                        'learning_ids': group_ids,
                        'similarity': round(avg_sim, 3),
                        'representative': group_ids[rep_idx],
                        'count': len(group_ids)
                    })

            # Sort by group size (largest first)
            groups.sort(key=lambda x: x['count'], reverse=True)
            return groups

        except Exception as e:
            logger.error(f"Duplicate detection failed: {e}")
            return []

    def get_clusters(self, distance_threshold: float = 0.7) -> Dict[int, List[str]]:
        """
        Cluster learnings by semantic similarity using agglomerative clustering.

        Args:
            distance_threshold: Distance threshold for clustering (0.0-2.0)
                              Lower = more clusters, higher = fewer clusters

        Returns:
            Dict mapping cluster_id -> list of learning_ids
        """
        # Ensure index is up to date
        if not self.ensure_up_to_date():
            return {}

        if self.tfidf_matrix is None or len(self.learning_ids) < 2:
            return {}

        # Check cache
        if (self._cluster_cache is not None and
            self._cluster_threshold == distance_threshold):
            return self._cluster_cache

        try:
            # Convert sparse matrix to dense for clustering
            # Use cosine distance = 1 - cosine_similarity
            dense_matrix = self.tfidf_matrix.toarray()

            # Agglomerative clustering with cosine affinity
            clustering = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=distance_threshold,
                metric='cosine',
                linkage='average'
            )

            labels = clustering.fit_predict(dense_matrix)

            # Group learning IDs by cluster
            clusters: Dict[int, List[str]] = {}
            for idx, cluster_id in enumerate(labels):
                cluster_id = int(cluster_id)
                if cluster_id not in clusters:
                    clusters[cluster_id] = []
                clusters[cluster_id].append(self.learning_ids[idx])

            # Cache results
            self._cluster_cache = clusters
            self._cluster_threshold = distance_threshold

            logger.info(f"Created {len(clusters)} clusters from {len(self.learning_ids)} learnings")
            return clusters

        except Exception as e:
            logger.error(f"Clustering failed: {e}")
            return {}

    def get_top_terms(self, learning_ids: List[str], top_n: int = 5) -> List[str]:
        """
        Get the top TF-IDF terms for a set of learnings.

        Useful for generating cluster themes.

        Args:
            learning_ids: List of learning IDs to analyze
            top_n: Number of top terms to return

        Returns:
            List of top terms sorted by average TF-IDF weight
        """
        if not self._fitted or self.tfidf_matrix is None:
            return []

        try:
            # Get indices for the learning IDs
            indices = [self.learning_ids.index(lid) for lid in learning_ids
                      if lid in self.learning_ids]

            if not indices:
                return []

            # Get the TF-IDF vectors for these learnings
            subset_matrix = self.tfidf_matrix[indices]

            # Average across learnings
            avg_tfidf = np.asarray(subset_matrix.mean(axis=0)).flatten()

            # Get top terms
            feature_names = self.vectorizer.get_feature_names_out()
            top_indices = np.argsort(avg_tfidf)[::-1][:top_n]

            return [feature_names[i] for i in top_indices if avg_tfidf[i] > 0]

        except Exception as e:
            logger.error(f"Failed to get top terms: {e}")
            return []

    def compute_cluster_coherence(self, learning_ids: List[str]) -> float:
        """
        Compute the coherence score of a cluster.

        Coherence measures how similar the learnings in a cluster are to each other.
        A higher score (closer to 1.0) indicates tighter, more coherent clusters.

        Args:
            learning_ids: List of learning IDs in the cluster

        Returns:
            Coherence score between 0.0 and 1.0
        """
        if not self._fitted or self.tfidf_matrix is None:
            return 0.0

        if len(learning_ids) < 2:
            return 1.0  # Single item clusters are perfectly coherent

        try:
            # Get indices
            indices = [self.learning_ids.index(lid) for lid in learning_ids
                      if lid in self.learning_ids]

            if len(indices) < 2:
                return 1.0

            # Get subset of TF-IDF matrix
            subset_matrix = self.tfidf_matrix[indices]

            # Compute pairwise similarities within cluster
            sim_matrix = cosine_similarity(subset_matrix)

            # Extract upper triangle (excluding diagonal)
            n = len(indices)
            total_sim = 0.0
            count = 0
            for i in range(n):
                for j in range(i + 1, n):
                    total_sim += sim_matrix[i, j]
                    count += 1

            return total_sim / count if count > 0 else 0.0

        except Exception as e:
            logger.error(f"Failed to compute cluster coherence: {e}")
            return 0.0

    def get_representative_learning(self, learning_ids: List[str]) -> Optional[str]:
        """
        Find the most representative learning in a cluster.

        The representative is the learning with the highest average similarity
        to all other learnings in the cluster (the centroid-like learning).

        Args:
            learning_ids: List of learning IDs in the cluster

        Returns:
            Learning ID of the most representative learning, or None
        """
        if not self._fitted or self.tfidf_matrix is None:
            return None

        if len(learning_ids) < 2:
            return learning_ids[0] if learning_ids else None

        try:
            indices = [self.learning_ids.index(lid) for lid in learning_ids
                      if lid in self.learning_ids]

            if len(indices) < 2:
                return learning_ids[0]

            subset_matrix = self.tfidf_matrix[indices]
            sim_matrix = cosine_similarity(subset_matrix)

            # Find learning with highest average similarity to others
            avg_similarities = sim_matrix.sum(axis=1) / (len(indices) - 1)
            best_idx = np.argmax(avg_similarities)

            return self.learning_ids[indices[best_idx]]

        except Exception as e:
            logger.error(f"Failed to get representative learning: {e}")
            return learning_ids[0] if learning_ids else None

    def find_related_learnings(
        self,
        learning_id: str,
        threshold: float = 0.7,
        max_results: int = 10
    ) -> List[Tuple[str, float]]:
        """
        Find learnings related to a specific learning across all missions.

        This enables 'learning chains' showing how techniques evolved across missions.

        Args:
            learning_id: The learning ID to find relations for
            threshold: Minimum similarity threshold (0.0-1.0)
            max_results: Maximum number of related learnings to return

        Returns:
            List of (related_learning_id, similarity_score) tuples
        """
        if not self.ensure_up_to_date():
            return []

        if learning_id not in self.learning_ids:
            return []

        try:
            idx = self.learning_ids.index(learning_id)
            learning_vector = self.tfidf_matrix[idx]

            # Compute similarities to all other learnings
            similarities = cosine_similarity(learning_vector, self.tfidf_matrix).flatten()

            # Filter by threshold and exclude self
            related = []
            for i, sim in enumerate(similarities):
                if i != idx and sim >= threshold:
                    related.append((self.learning_ids[i], float(sim)))

            # Sort by similarity (highest first) and limit
            related.sort(key=lambda x: x[1], reverse=True)
            return related[:max_results]

        except Exception as e:
            logger.error(f"Failed to find related learnings: {e}")
            return []

    def get_hierarchical_clusters(
        self,
        levels: int = 2,
        top_level_threshold: float = 0.9,
        sub_level_threshold: float = 0.6
    ) -> Dict[str, Any]:
        """
        Get hierarchical clusters with parent themes and sub-clusters.

        Creates a two-level hierarchy:
        - Top level: Broad theme clusters (higher distance threshold)
        - Sub level: More specific clusters within each broad theme

        Args:
            levels: Number of hierarchy levels (currently supports 2)
            top_level_threshold: Distance threshold for top-level clusters
            sub_level_threshold: Distance threshold for sub-clusters

        Returns:
            Dict with hierarchical cluster structure:
            {
                'clusters': [{
                    'cluster_id': int,
                    'theme': str,
                    'coherence': float,
                    'size': int,
                    'sub_clusters': [{
                        'sub_cluster_id': int,
                        'theme': str,
                        'coherence': float,
                        'learning_ids': [str]
                    }]
                }]
            }
        """
        # Check cache
        cache_key = f"{levels}_{top_level_threshold}_{sub_level_threshold}"
        if self._hierarchical_cache is not None:
            cached = self._hierarchical_cache.get(cache_key)
            if cached is not None:
                return cached

        if not self.ensure_up_to_date():
            return {'clusters': []}

        if self.tfidf_matrix is None or len(self.learning_ids) < 2:
            return {'clusters': []}

        try:
            dense_matrix = self.tfidf_matrix.toarray()

            # Top-level clustering
            top_clustering = AgglomerativeClustering(
                n_clusters=None,
                distance_threshold=top_level_threshold,
                metric='cosine',
                linkage='average'
            )
            top_labels = top_clustering.fit_predict(dense_matrix)

            # Group by top-level cluster
            top_clusters: Dict[int, List[int]] = {}
            for idx, cluster_id in enumerate(top_labels):
                if cluster_id not in top_clusters:
                    top_clusters[cluster_id] = []
                top_clusters[cluster_id].append(idx)

            results = []
            for top_cluster_id, indices in top_clusters.items():
                learning_ids = [self.learning_ids[i] for i in indices]

                # Generate theme and coherence for top cluster
                top_terms = self.get_top_terms(learning_ids, top_n=3)
                theme = ', '.join([t.replace('_', ' ').title() for t in top_terms]) if top_terms else 'General'
                coherence = self.compute_cluster_coherence(learning_ids)

                sub_clusters = []

                # Sub-cluster if large enough
                if len(indices) >= 4:
                    # Get subset matrix for sub-clustering
                    subset_matrix = dense_matrix[indices]

                    sub_clustering = AgglomerativeClustering(
                        n_clusters=None,
                        distance_threshold=sub_level_threshold,
                        metric='cosine',
                        linkage='average'
                    )
                    sub_labels = sub_clustering.fit_predict(subset_matrix)

                    # Group sub-clusters
                    sub_groups: Dict[int, List[str]] = {}
                    for local_idx, sub_id in enumerate(sub_labels):
                        if sub_id not in sub_groups:
                            sub_groups[sub_id] = []
                        sub_groups[sub_id].append(learning_ids[local_idx])

                    for sub_id, sub_learning_ids in sub_groups.items():
                        sub_terms = self.get_top_terms(sub_learning_ids, top_n=2)
                        sub_theme = ', '.join([t.replace('_', ' ').title() for t in sub_terms]) if sub_terms else 'Misc'
                        sub_coherence = self.compute_cluster_coherence(sub_learning_ids)

                        sub_clusters.append({
                            'sub_cluster_id': int(sub_id),
                            'theme': sub_theme,
                            'coherence': round(sub_coherence, 3),
                            'learning_ids': sub_learning_ids,
                            'size': len(sub_learning_ids)
                        })
                else:
                    # Too small for sub-clustering, use single sub-cluster
                    sub_clusters.append({
                        'sub_cluster_id': 0,
                        'theme': theme,
                        'coherence': round(coherence, 3),
                        'learning_ids': learning_ids,
                        'size': len(learning_ids)
                    })

                results.append({
                    'cluster_id': int(top_cluster_id),
                    'theme': theme,
                    'coherence': round(coherence, 3),
                    'size': len(learning_ids),
                    'sub_clusters': sorted(sub_clusters, key=lambda x: x['size'], reverse=True)
                })

            # Sort by size
            results.sort(key=lambda x: x['size'], reverse=True)

            result = {'clusters': results, 'total_learnings': len(self.learning_ids)}

            # Cache result
            if self._hierarchical_cache is None:
                self._hierarchical_cache = {}
            self._hierarchical_cache[cache_key] = result

            return result

        except Exception as e:
            logger.error(f"Hierarchical clustering failed: {e}")
            return {'clusters': []}

    def invalidate(self, full: bool = True):
        """Mark the index as needing a rebuild.

        Args:
            full: If True, forces full rebuild. If False, allows incremental update.
        """
        if full:
            self._fitted = False
            self._cluster_cache = None
            self._cluster_threshold = None
            self._hierarchical_cache = None
            self._coherence_cache = {}
            self.tfidf_matrix = None
            self.learning_ids = []
            self.learning_descriptions = []
            self._pending_additions = []
        else:
            # Just invalidate caches, keep core data for incremental update
            self._cluster_cache = None
            self._cluster_threshold = None
            self._hierarchical_cache = None
            self._coherence_cache = {}

    def add_learning_incremental(self, learning_id: str, text: str) -> bool:
        """
        Add a single learning to the index incrementally (deferred).

        The learning is queued for the next index update. If too many learnings
        are pending, triggers a full rebuild instead.

        Args:
            learning_id: The learning ID to add
            text: Combined text (title + description + domain)

        Returns:
            True if learning was queued successfully
        """
        if learning_id in self.learning_ids:
            return False  # Already in index

        self._pending_additions.append((learning_id, text))

        # Invalidate caches but not the core index
        self.invalidate(full=False)

        # Check if we should trigger full rebuild due to too many pending
        if self._fitted and len(self.learning_ids) > 0:
            pending_ratio = len(self._pending_additions) / len(self.learning_ids)
            if pending_ratio > self.REBUILD_THRESHOLD:
                logger.info(f"Triggering full rebuild: {len(self._pending_additions)} pending additions")
                return self._apply_pending_additions()

        return True

    def _apply_pending_additions(self) -> bool:
        """
        Apply all pending additions to the index.

        Uses incremental transform if possible, otherwise triggers full rebuild.

        Returns:
            True if successful
        """
        if not self._pending_additions:
            return True

        if not self._fitted or self.tfidf_matrix is None:
            # Need full rebuild
            self._pending_additions = []
            return self.fit()

        try:
            # Transform pending additions using existing vocabulary
            new_texts = [text for _, text in self._pending_additions]
            new_ids = [lid for lid, _ in self._pending_additions]

            # Transform new texts using fitted vectorizer
            new_vectors = self.vectorizer.transform(new_texts)

            # Append to existing matrix
            from scipy.sparse import vstack
            self.tfidf_matrix = vstack([self.tfidf_matrix, new_vectors])

            # Update ID and description lists
            self.learning_ids.extend(new_ids)
            self.learning_descriptions.extend(new_texts)

            # Clear pending
            self._pending_additions = []

            logger.info(f"Incrementally added {len(new_ids)} learnings to index")
            return True

        except Exception as e:
            logger.warning(f"Incremental update failed, triggering full rebuild: {e}")
            self._pending_additions = []
            return self.fit()

    def ensure_up_to_date(self) -> bool:
        """
        Ensure the index is up to date before queries.

        Applies any pending additions and ensures the index is fitted.

        Returns:
            True if index is ready for queries
        """
        if self._pending_additions:
            if not self._apply_pending_additions():
                return False

        if not self._fitted:
            return self.fit()

        return True

    @property
    def is_fitted(self) -> bool:
        """Check if the index is fitted."""
        return self._fitted

    @property
    def pending_count(self) -> int:
        """Get count of pending additions."""
        return len(self._pending_additions)


class MissionKnowledgeBase:
    """
    Cross-mission learning and memory system.

    Maintains a SQLite database of learnings from past missions,
    enabling retrieval of relevant insights for new problems.

    Supports hybrid retrieval combining TF-IDF with neural embeddings
    for improved semantic search quality.
    """

    def __init__(self, storage_path: Optional[Path] = None, use_hybrid: bool = True, fast_mode: bool = False):
        """
        Initialize the knowledge base.

        Args:
            storage_path: Path to store the database (default: KNOWLEDGE_DIR)
            use_hybrid: Whether to use hybrid retrieval (TF-IDF + embeddings)
            fast_mode: If True, disables embeddings for faster queries (TF-IDF only)
        """
        self.storage_path = storage_path or KNOWLEDGE_DIR
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.db_path = self.storage_path / "mission_knowledge.db"
        self._init_db()

        # Initialize semantic index based on configuration
        self._use_hybrid = use_hybrid and not fast_mode
        self._hybrid_available = False

        if self._use_hybrid:
            try:
                import sys
                # Add workspace to path for hybrid_retrieval import
                workspace_path = BASE_DIR / "workspace"
                if str(workspace_path) not in sys.path:
                    sys.path.insert(0, str(workspace_path))

                from hybrid_retrieval import HybridSemanticIndex, HybridScoreConfig

                # Configure based on fast_mode (for future extension)
                config = HybridScoreConfig()
                self._semantic_index = HybridSemanticIndex(self.db_path, config)
                self._hybrid_available = True
                logger.info("MissionKnowledgeBase initialized with hybrid retrieval")
            except ImportError as e:
                logger.warning(f"HybridSemanticIndex not available ({e}), using TF-IDF only")
                self._semantic_index = SemanticIndex(self.db_path)
            except Exception as e:
                logger.warning(f"Failed to initialize hybrid index ({e}), falling back to TF-IDF")
                self._semantic_index = SemanticIndex(self.db_path)
        else:
            self._semantic_index = SemanticIndex(self.db_path)
            logger.info("MissionKnowledgeBase initialized with TF-IDF only")

    def _init_db(self):
        """Initialize SQLite database schema"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Mission summaries table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS mission_summaries (
                    mission_id TEXT PRIMARY KEY,
                    problem_statement TEXT NOT NULL,
                    problem_domain TEXT,
                    outcome TEXT,
                    approach_taken TEXT,
                    key_learnings TEXT,  -- JSON list
                    failures_encountered TEXT,  -- JSON list
                    files_created TEXT,  -- JSON list
                    duration_minutes REAL,
                    cycles_used INTEGER,
                    timestamp TEXT NOT NULL
                )
            """)

            # Learnings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS learnings (
                    learning_id TEXT PRIMARY KEY,
                    mission_id TEXT NOT NULL,
                    learning_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    problem_domain TEXT,
                    outcome TEXT,
                    relevance_keywords TEXT,  -- JSON list
                    code_snippets TEXT,  -- JSON list
                    files_created TEXT,  -- JSON list
                    timestamp TEXT NOT NULL,
                    lesson_source TEXT DEFAULT 'unknown',  -- cycle_summary, achievement, issue, history, final_summary
                    FOREIGN KEY (mission_id) REFERENCES mission_summaries(mission_id)
                )
            """)

            # Add lesson_source column if it doesn't exist (migration for existing DBs)
            try:
                cursor.execute("ALTER TABLE learnings ADD COLUMN lesson_source TEXT DEFAULT 'unknown'")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # Add investigation-related columns (migration for KB-investigation integration)
            try:
                cursor.execute("ALTER TABLE learnings ADD COLUMN source_type TEXT DEFAULT 'mission'")
            except sqlite3.OperationalError:
                pass  # Column already exists

            try:
                cursor.execute("ALTER TABLE learnings ADD COLUMN source_investigation_id TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            try:
                cursor.execute("ALTER TABLE learnings ADD COLUMN investigation_query TEXT")
            except sqlite3.OperationalError:
                pass  # Column already exists

            # GitHub links table (mission-GitHub artifact linking)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS github_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mission_id TEXT NOT NULL,
                    link_type TEXT NOT NULL,  -- 'pr' or 'issue'
                    url TEXT NOT NULL,
                    number INTEGER,
                    title TEXT,
                    state TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(mission_id, url)
                )
            """)

            # Create indexes for efficient querying
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_learnings_domain ON learnings(problem_domain)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_learnings_type ON learnings(learning_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_learnings_outcome ON learnings(outcome)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_learnings_source_type ON learnings(source_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_learnings_investigation_id ON learnings(source_investigation_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_summaries_domain ON mission_summaries(problem_domain)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_summaries_outcome ON mission_summaries(outcome)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_github_links_mission ON github_links(mission_id)")

            conn.commit()

    def ingest_completed_mission(self, mission_log_path: Path) -> Dict[str, Any]:
        """
        Extract learnings from a completed mission log.

        Args:
            mission_log_path: Path to the mission log JSON file

        Returns:
            Dict with ingestion statistics
        """
        try:
            with open(mission_log_path) as f:
                mission_data = json.load(f)
        except Exception as e:
            logger.error(f"Failed to read mission log: {e}")
            return {"status": "error", "message": str(e)}

        mission_id = mission_data.get("mission_id", "unknown")

        # Extract mission summary
        summary = self._extract_summary(mission_data)
        self._store_summary(summary)

        # Extract individual learnings
        learnings = self._extract_learnings(mission_data)
        learnings_stored = 0
        for learning in learnings:
            self._store_learning(learning)
            learnings_stored += 1

        # Log warning if zero learnings extracted
        if learnings_stored == 0:
            logger.warning(
                f"Mission {mission_id}: Zero learnings extracted. "
                f"Data keys present: {list(mission_data.keys())}, "
                f"cycles count: {len(mission_data.get('cycles', []))}, "
                f"cycle_history count: {len(mission_data.get('cycle_history', []))}, "
                f"history count: {len(mission_data.get('history', []))}"
            )
        else:
            logger.info(f"Ingested mission {mission_id}: {learnings_stored} learnings")

        return {
            "status": "success",
            "mission_id": mission_id,
            "learnings_extracted": learnings_stored
        }

    def _extract_summary(self, mission_data: dict) -> MissionSummary:
        """Extract summary from mission data"""
        # Determine outcome from stage or status
        final_stage = mission_data.get("current_stage", "UNKNOWN")
        status = mission_data.get("status", "")
        outcome = "success" if final_stage == "COMPLETE" or status == "mission_complete" else "partial"

        # Calculate duration if timestamps available
        duration_minutes = 0.0
        try:
            # Try started_at/completed_at first (mission report format)
            if "started_at" in mission_data and "completed_at" in mission_data:
                start = datetime.fromisoformat(mission_data["started_at"])
                end = datetime.fromisoformat(mission_data["completed_at"])
                duration_minutes = (end - start).total_seconds() / 60
            # Fall back to created_at/last_updated (active mission format)
            elif "created_at" in mission_data and "last_updated" in mission_data:
                start = datetime.fromisoformat(mission_data["created_at"])
                end = datetime.fromisoformat(mission_data["last_updated"])
                duration_minutes = (end - start).total_seconds() / 60
        except Exception:
            pass

        # Extract key learnings from history OR cycles
        key_learnings = []
        failures = []

        # Try cycles array first (mission report format)
        for cycle in mission_data.get("cycles", []):
            cycle_summary = cycle.get("summary", "")
            if cycle_summary:
                key_learnings.append(cycle_summary[:200])
            # Also get from achievements
            for achievement in cycle.get("achievements", []):
                if achievement and isinstance(achievement, str):
                    key_learnings.append(achievement[:200])
            # Get failures from issues
            for issue in cycle.get("issues", []):
                if issue and isinstance(issue, str):
                    failures.append(issue[:200])

        # Fall back to history entries (active mission format)
        if not key_learnings:
            for entry in mission_data.get("history", []):
                entry_text = entry.get("entry", "")
                if "success" in entry_text.lower() or "completed" in entry_text.lower():
                    key_learnings.append(entry_text[:200])
                elif "error" in entry_text.lower() or "failed" in entry_text.lower():
                    failures.append(entry_text[:200])

        # Get problem statement: try original_mission first (report format), then problem_statement
        problem_statement = mission_data.get("original_mission", "") or mission_data.get("problem_statement", "")

        # Infer problem domain from problem statement
        problem_domain = self._infer_domain(problem_statement)

        # Get files created from various sources
        files_created = mission_data.get("all_files", []) or mission_data.get("artifacts", {}).get("code", [])

        return MissionSummary(
            mission_id=mission_data.get("mission_id", "unknown"),
            problem_statement=problem_statement,
            problem_domain=problem_domain,
            outcome=outcome,
            approach_taken=mission_data.get("preferences", {}).get("approach", "unknown"),
            key_learnings=key_learnings[:10],
            failures_encountered=failures[:10],
            files_created=files_created,
            duration_minutes=duration_minutes,
            cycles_used=mission_data.get("total_cycles", mission_data.get("current_cycle", 1))
        )

    def _extract_learnings(self, mission_data: dict) -> List[MissionLearning]:
        """Extract individual learnings from mission data"""
        learnings = []
        mission_id = mission_data.get("mission_id", "unknown")

        # Get problem statement: try original_mission first (report format), then problem_statement
        problem_statement = mission_data.get("original_mission", "") or mission_data.get("problem_statement", "")
        problem_domain = self._infer_domain(problem_statement)

        # Extract from cycles array (mission report format - this is the correct field)
        for cycle in mission_data.get("cycles", []):
            cycle_num = cycle.get("cycle", 0)

            # Extract cycle summary as technique learning
            summary = cycle.get("summary", "")
            if summary:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{mission_id}_cycle_{cycle_num}_summary".encode()
                    ).hexdigest()[:16],
                    mission_id=mission_id,
                    learning_type="technique",
                    title=f"Cycle {cycle_num} Summary",
                    description=summary[:500],
                    problem_domain=problem_domain,
                    outcome="success",
                    relevance_keywords=self._extract_keywords(summary),
                    files_created=cycle.get("files_generated", []) or cycle.get("files_created", []),
                    lesson_source="cycle_summary"
                )
                learnings.append(learning)

            # Extract achievements as technique learnings
            for i, achievement in enumerate(cycle.get("achievements", [])):
                if achievement and isinstance(achievement, str):
                    learning = MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{mission_id}_cycle_{cycle_num}_achievement_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=mission_id,
                        learning_type="technique",
                        title=f"Achievement: Cycle {cycle_num}",
                        description=achievement[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(achievement),
                        lesson_source="achievement"
                    )
                    learnings.append(learning)

            # Extract issues as gotcha learnings
            for i, issue in enumerate(cycle.get("issues", [])):
                if issue and isinstance(issue, str):
                    learning = MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{mission_id}_cycle_{cycle_num}_issue_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=mission_id,
                        learning_type="gotcha",
                        title=f"Issue: Cycle {cycle_num}",
                        description=issue[:500],
                        problem_domain=problem_domain,
                        outcome="partial",
                        relevance_keywords=self._extract_keywords(issue),
                        lesson_source="issue"
                    )
                    learnings.append(learning)

            # Extract continuation_prompt as insight (contains forward-looking guidance)
            continuation = cycle.get("continuation_prompt", "")
            if continuation and isinstance(continuation, str) and len(continuation) > 50:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{mission_id}_cycle_{cycle_num}_continuation".encode()
                    ).hexdigest()[:16],
                    mission_id=mission_id,
                    learning_type="insight",
                    title=f"Next Steps: Cycle {cycle_num}",
                    description=continuation[:500],
                    problem_domain=problem_domain,
                    outcome="success",
                    relevance_keywords=self._extract_keywords(continuation),
                    lesson_source="continuation"
                )
                learnings.append(learning)

        # Also try cycle_history for backward compatibility (active mission format)
        for cycle in mission_data.get("cycle_history", []):
            cycle_summary = cycle.get("summary", "")
            if cycle.get("status") == "completed" and cycle_summary:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{mission_id}_{cycle.get('cycle', 0)}".encode()
                    ).hexdigest()[:16],
                    mission_id=mission_id,
                    learning_type="technique",
                    title=f"Cycle {cycle.get('cycle', 'N/A')} approach",
                    description=cycle_summary[:500],
                    problem_domain=problem_domain,
                    outcome="success" if cycle.get("tests_passed") else "partial",
                    relevance_keywords=self._extract_keywords(cycle_summary),
                    files_created=cycle.get("files_created", []),
                    lesson_source="cycle_summary"
                )
                learnings.append(learning)

            # Also extract achievements/issues from cycle_history
            for i, achievement in enumerate(cycle.get("achievements", [])):
                if achievement and isinstance(achievement, str):
                    learning = MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{mission_id}_ch_{cycle.get('cycle', 0)}_achievement_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=mission_id,
                        learning_type="technique",
                        title=f"Achievement: Cycle {cycle.get('cycle', 'N/A')}",
                        description=achievement[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(achievement),
                        lesson_source="achievement"
                    )
                    learnings.append(learning)

            for i, issue in enumerate(cycle.get("issues", [])):
                if issue and isinstance(issue, str):
                    learning = MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{mission_id}_ch_{cycle.get('cycle', 0)}_issue_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=mission_id,
                        learning_type="gotcha",
                        title=f"Issue: Cycle {cycle.get('cycle', 'N/A')}",
                        description=issue[:500],
                        problem_domain=problem_domain,
                        outcome="partial",
                        relevance_keywords=self._extract_keywords(issue),
                        lesson_source="issue"
                    )
                    learnings.append(learning)

        # Extract from final_summary field when present
        final_summary = mission_data.get("final_summary", "")
        if final_summary and isinstance(final_summary, str) and len(final_summary) > 20:
            learning = MissionLearning(
                learning_id=hashlib.sha256(
                    f"{mission_id}_final_summary".encode()
                ).hexdigest()[:16],
                mission_id=mission_id,
                learning_type="technique",
                title="Mission Final Summary",
                description=final_summary[:500],
                problem_domain=problem_domain,
                outcome="success",
                relevance_keywords=self._extract_keywords(final_summary),
                lesson_source="final_summary"
            )
            learnings.append(learning)

        # Extract from deliverables list when present (completed work items)
        deliverables = mission_data.get("deliverables", [])
        if isinstance(deliverables, list):
            for i, deliverable in enumerate(deliverables):
                if deliverable and isinstance(deliverable, str) and len(deliverable) > 30:
                    learning = MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{mission_id}_deliverable_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=mission_id,
                        learning_type="technique",
                        title=f"Deliverable: {deliverable[:50]}...",
                        description=deliverable[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(deliverable),
                        lesson_source="deliverable"
                    )
                    learnings.append(learning)

        # Extract from history entries - enhanced with more stage transition detection
        # Keywords that indicate valuable stage completion insights
        success_keywords = ["insight", "discovered", "learned", "found", "realized",
                           "completed", "success", "achieved", "implemented", "fixed",
                           "resolved", "working", "passed", "verified"]
        failure_keywords = ["error", "failed", "mistake", "wrong", "bug", "issue",
                           "problem", "broken", "crash", "exception", "timeout"]

        for i, entry in enumerate(mission_data.get("history", [])):
            entry_text = entry.get("entry", "")
            stage = entry.get("stage", "UNKNOWN")
            details = entry.get("details", {})

            # Skip very short entries
            if not entry_text or len(entry_text) < 20:
                continue

            # Extract status from details if available
            status = details.get("status", "") if isinstance(details, dict) else ""

            # Look for significant success entries
            if any(word in entry_text.lower() for word in success_keywords) or status in ["success", "tests_passed", "build_complete"]:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{mission_id}_hist_{i}_{entry_text[:50]}".encode()
                    ).hexdigest()[:16],
                    mission_id=mission_id,
                    learning_type="insight",
                    title=f"Insight from {stage}",
                    description=entry_text[:500],
                    problem_domain=problem_domain,
                    outcome="success",
                    relevance_keywords=self._extract_keywords(entry_text),
                    lesson_source="history"
                )
                learnings.append(learning)

            # Capture failures as gotchas
            elif any(word in entry_text.lower() for word in failure_keywords) or status in ["tests_failed", "build_failed", "error"]:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{mission_id}_hist_{i}_{entry_text[:50]}".encode()
                    ).hexdigest()[:16],
                    mission_id=mission_id,
                    learning_type="gotcha",
                    title=f"Issue in {stage}",
                    description=entry_text[:500],
                    problem_domain=problem_domain,
                    outcome="failure",
                    relevance_keywords=self._extract_keywords(entry_text),
                    lesson_source="history"
                )
                learnings.append(learning)

        return learnings

    def _infer_domain(self, problem_statement: str) -> str:
        """
        Infer problem domain from statement using weighted scoring.

        Uses a scoring approach instead of first-match to avoid order-dependent
        results. Each domain gets a score based on how many keywords match,
        with more specific/longer keywords weighted higher.
        """
        ps_lower = problem_statement.lower()

        # Domain keywords with weights (longer/more specific keywords score higher)
        # Format: (keyword, weight) - higher weight = more indicative of domain
        # Expanded keyword lists for better categorization (Cycle 2 enhancement)
        domains = {
            "gpu_optimization": [
                ("pytorch", 4), ("cuda", 4), ("tensorflow", 4), ("tensor", 3),
                ("gpu", 3), ("vram", 3), ("neural", 2), ("training", 2),
                ("model", 1), ("deep learning", 4), ("machine learning", 3),
                ("nvidia", 3), ("batch", 1), ("epoch", 2), ("gradient", 2),
                ("backprop", 3), ("inference", 2), ("acceleration", 2)
            ],
            "api_integration": [
                ("api", 3), ("endpoint", 4), ("rest", 3), ("graphql", 4),
                ("http", 2), ("fetch", 2), ("request", 2), ("response", 2),
                ("webhook", 3), ("oauth", 3), ("jwt", 3), ("authentication", 2),
                ("authorization", 2), ("cors", 3), ("json", 1), ("xml", 2),
                ("websocket", 3), ("grpc", 4), ("openapi", 3), ("swagger", 3)
            ],
            "file_processing": [
                ("csv", 3), ("json", 2), ("xml", 2), ("yaml", 3), ("parse", 2),
                ("io", 2), ("file", 1), ("read", 1), ("write", 1), ("stream", 2),
                ("buffer", 2), ("encoding", 2), ("utf-8", 2), ("binary", 2),
                ("serialization", 3), ("deserialization", 3), ("path", 1),
                ("directory", 1), ("filesystem", 3)
            ],
            "database": [
                ("sqlite", 4), ("database", 3), ("sql", 3), ("query", 2),
                ("table", 2), ("record", 1), ("postgres", 4), ("mysql", 4),
                ("mongodb", 4), ("redis", 4), ("orm", 3), ("migration", 3),
                ("schema", 2), ("index", 2), ("transaction", 3), ("acid", 3),
                ("nosql", 3), ("crud", 2), ("join", 2), ("foreign key", 3)
            ],
            "ui_development": [
                ("frontend", 4), ("dashboard", 3), ("ui", 2), ("interface", 2),
                ("display", 1), ("widget", 3), ("react", 4), ("vue", 4),
                ("angular", 4), ("html", 2), ("css", 2), ("javascript", 2),
                ("dom", 3), ("component", 2), ("render", 2), ("layout", 2),
                ("responsive", 2), ("canvas", 3), ("svg", 3), ("animation", 2),
                ("chart", 2), ("graph", 2), ("visualization", 3)
            ],
            "testing": [
                ("unittest", 4), ("pytest", 4), ("test", 2), ("validation", 2),
                ("verify", 2), ("assert", 2), ("mock", 3), ("stub", 3),
                ("fixture", 3), ("coverage", 3), ("integration test", 4),
                ("e2e", 4), ("end-to-end", 4), ("selenium", 3), ("playwright", 4),
                ("ci/cd", 3), ("tdd", 4), ("bdd", 4), ("regression", 3),
                ("snapshot", 2), ("jest", 3), ("mocha", 3)
            ],
            "refactoring": [
                ("refactor", 4), ("restructure", 3), ("clean", 2), ("improve", 1),
                ("optimize", 2), ("performance", 2), ("bottleneck", 3),
                ("profil", 3), ("memory", 2), ("cpu", 2), ("latency", 3),
                ("throughput", 3), ("cache", 2), ("efficient", 2),
                ("technical debt", 4), ("code smell", 4), ("solid", 3),
                ("dry", 3), ("kiss", 3), ("yagni", 3)
            ],
            "documentation": [
                ("readme", 4), ("document", 2), ("docs", 2), ("comment", 1),
                ("explain", 1), ("markdown", 3), ("sphinx", 4), ("docstring", 3),
                ("api doc", 4), ("specification", 2), ("tutorial", 2),
                ("guide", 2), ("reference", 2), ("changelog", 3)
            ],
            "research": [
                ("research", 4), ("investigate", 3), ("analyze", 2), ("study", 2),
                ("explore", 2), ("experiment", 3), ("hypothesis", 3),
                ("prototype", 3), ("poc", 3), ("proof of concept", 4),
                ("benchmark", 3), ("comparison", 2), ("evaluation", 2),
                ("spike", 3), ("discovery", 2)
            ],
            "atlasforge_improvement": [
                ("atlasforge", 5), ("autonomous", 4), ("mission", 3), ("engine", 2),
                ("dashboard", 2), ("knowledge base", 4), ("learning", 2),
                ("extraction", 2), ("workflow", 2), ("stage", 2),
                ("cycle", 2), ("planning", 2), ("building", 2), ("testing", 1),
                ("continuity", 3), ("healing", 3)
            ],
            "devops": [
                ("docker", 4), ("kubernetes", 4), ("k8s", 4), ("container", 3),
                ("deployment", 3), ("pipeline", 3), ("ci/cd", 3), ("jenkins", 3),
                ("github actions", 4), ("terraform", 4), ("ansible", 4),
                ("infrastructure", 3), ("monitoring", 2), ("logging", 2),
                ("metrics", 2), ("observability", 3), ("helm", 3)
            ],
            "security": [
                ("security", 3), ("vulnerability", 4), ("exploit", 4),
                ("authentication", 3), ("authorization", 3), ("encryption", 3),
                ("hash", 2), ("ssl", 3), ("tls", 3), ("certificate", 3),
                ("xss", 4), ("csrf", 4), ("injection", 3), ("sanitize", 3),
                ("firewall", 3), ("penetration", 4), ("audit", 2)
            ],
            "cli_tools": [
                ("cli", 4), ("command line", 4), ("terminal", 3), ("shell", 3),
                ("bash", 3), ("argparse", 3), ("click", 3), ("typer", 3),
                ("subprocess", 2), ("stdin", 2), ("stdout", 2), ("pipe", 2)
            ],
            "async_concurrent": [
                ("async", 4), ("await", 3), ("asyncio", 4), ("concurrent", 3),
                ("parallel", 3), ("thread", 3), ("multiprocess", 4),
                ("coroutine", 4), ("future", 3), ("promise", 3), ("race", 2),
                ("lock", 2), ("semaphore", 3), ("deadlock", 4)
            ],
            "data_science": [
                ("pandas", 4), ("numpy", 4), ("scipy", 4), ("matplotlib", 4),
                ("seaborn", 3), ("jupyter", 3), ("notebook", 2), ("dataframe", 3),
                ("analysis", 2), ("statistics", 3), ("visualization", 2),
                ("plot", 2), ("chart", 2), ("regression", 3), ("correlation", 3)
            ]
        }

        # Score each domain by summing weights of matched keywords
        domain_scores = {}
        for domain, keyword_weights in domains.items():
            score = 0
            for keyword, weight in keyword_weights:
                if keyword in ps_lower:
                    score += weight
            if score > 0:
                domain_scores[domain] = score

        # Return highest scoring domain, or "general" if no matches
        if domain_scores:
            return max(domain_scores, key=domain_scores.get)

        return "general"

    def _extract_keywords(self, text: str) -> List[str]:
        """Extract relevance keywords from text"""
        # Simple keyword extraction (in production, could use NLP)
        import re
        words = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', text.lower())

        # Filter to significant words (>3 chars, not common stop words)
        stop_words = {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'have', 'been', 'will', 'are', 'was', 'were'}
        keywords = [w for w in words if len(w) > 3 and w not in stop_words]

        # Return top 20 most common
        from collections import Counter
        return [word for word, _ in Counter(keywords).most_common(20)]

    def _store_summary(self, summary: MissionSummary):
        """Store mission summary in database"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO mission_summaries
                (mission_id, problem_statement, problem_domain, outcome, approach_taken,
                 key_learnings, failures_encountered, files_created, duration_minutes,
                 cycles_used, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                summary.mission_id,
                summary.problem_statement,
                summary.problem_domain,
                summary.outcome,
                summary.approach_taken,
                json.dumps(summary.key_learnings),
                json.dumps(summary.failures_encountered),
                json.dumps(summary.files_created),
                summary.duration_minutes,
                summary.cycles_used,
                summary.timestamp
            ))
            conn.commit()

    def _store_learning(self, learning: MissionLearning):
        """Store learning in database and update semantic index incrementally."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO learnings
                (learning_id, mission_id, learning_type, title, description,
                 problem_domain, outcome, relevance_keywords, code_snippets,
                 files_created, timestamp, lesson_source, source_type,
                 source_investigation_id, investigation_query)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                learning.learning_id,
                learning.mission_id,
                learning.learning_type,
                learning.title,
                learning.description,
                learning.problem_domain,
                learning.outcome,
                json.dumps(learning.relevance_keywords),
                json.dumps(learning.code_snippets),
                json.dumps(learning.files_created),
                learning.timestamp,
                learning.lesson_source,
                learning.source_type,
                learning.source_investigation_id,
                learning.investigation_query
            ))
            conn.commit()

        # Use incremental index update instead of full invalidation
        if hasattr(self, '_semantic_index') and self._semantic_index is not None:
            combined_text = f"{learning.title or ''} {learning.description or ''} {learning.problem_domain or ''}".strip()
            self._semantic_index.add_learning_incremental(learning.learning_id, combined_text)

    def query_relevant_learnings(
        self,
        problem_statement: str,
        top_k: int = 5,
        learning_types: Optional[List[str]] = None,
        source_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Find past learnings relevant to a new problem using semantic similarity.

        Uses hybrid retrieval (TF-IDF + embeddings) when available, otherwise
        falls back to TF-IDF only.

        Args:
            problem_statement: The new problem to find learnings for
            top_k: Maximum number of learnings to return
            learning_types: Filter by specific types (technique, insight, gotcha, etc.)
            source_type: Filter by source type ('mission', 'investigation', or None for all)

        Returns:
            List of learning dicts with 'confidence_score' and optionally 'score_breakdown' fields
        """
        # Ensure semantic index is fitted
        if not self._semantic_index.is_fitted:
            self._semantic_index.fit()

        target_domain = self._infer_domain(problem_statement)

        # Use hybrid retrieval if available
        if self._hybrid_available and hasattr(self._semantic_index, 'query'):
            # HybridSemanticIndex.query returns (learning_id, hybrid_score, breakdown) tuples
            try:
                hybrid_results = self._semantic_index.query(
                    problem_statement,
                    top_k=top_k * 3,
                    target_domain=target_domain
                )

                if hybrid_results:
                    results = []
                    for learning_id, hybrid_score, breakdown in hybrid_results:
                        learning = self._get_learning_by_id(learning_id)
                        if learning is None:
                            continue

                        # Filter by source type if specified
                        if source_type and learning.source_type != source_type:
                            continue

                        # Filter by type if specified
                        if learning_types and learning.learning_type not in learning_types:
                            continue

                        # Convert to dict and add scores
                        result = learning.to_dict()
                        result['confidence_score'] = round(hybrid_score, 3)
                        result['score_breakdown'] = {
                            'tfidf': round(breakdown.get('tfidf', 0), 3),
                            'embedding': round(breakdown.get('embedding', 0), 3),
                            'recency': round(breakdown.get('recency', 0), 3)
                        }
                        results.append(result)

                        if len(results) >= top_k:
                            break

                    # Already sorted by hybrid score in query()
                    return results[:top_k]
            except Exception as e:
                logger.warning(f"Hybrid query failed ({e}), falling back to TF-IDF")

        # Fallback to SemanticIndex (TF-IDF only)
        tfidf_results = self._semantic_index.query(problem_statement, top_k=top_k * 3)

        if not tfidf_results:
            # Ultimate fallback to keyword-based
            return self._query_relevant_learnings_fallback(problem_statement, top_k, learning_types, source_type)

        results = []
        for learning_id, tfidf_score in tfidf_results:
            learning = self._get_learning_by_id(learning_id)
            if learning is None:
                continue

            # Filter by source type if specified
            if source_type and learning.source_type != source_type:
                continue

            # Filter by type if specified
            if learning_types and learning.learning_type not in learning_types:
                continue

            # Compute confidence score
            confidence = self._compute_confidence(
                tfidf_score=tfidf_score,
                domain_match=(learning.problem_domain == target_domain),
                outcome=learning.outcome,
                timestamp=learning.timestamp
            )

            # Convert to dict and add confidence score
            result = learning.to_dict()
            result['confidence_score'] = round(confidence, 3)
            # No score_breakdown for TF-IDF only mode
            results.append(result)

            if len(results) >= top_k:
                break

        # Sort by confidence score
        results.sort(key=lambda x: x['confidence_score'], reverse=True)
        return results[:top_k]

    def _query_relevant_learnings_fallback(
        self,
        problem_statement: str,
        top_k: int = 5,
        learning_types: Optional[List[str]] = None,
        source_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Fallback to keyword-based search when semantic index unavailable.

        Returns list of learning dicts with confidence_score field.
        """
        target_domain = self._infer_domain(problem_statement)
        target_keywords = set(self._extract_keywords(problem_statement))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM learnings WHERE 1=1"
            params = []

            if target_domain != "general":
                query += " AND (problem_domain = ? OR problem_domain = 'general')"
                params.append(target_domain)

            if learning_types:
                placeholders = ",".join(["?" for _ in learning_types])
                query += f" AND learning_type IN ({placeholders})"
                params.extend(learning_types)

            if source_type:
                query += " AND source_type = ?"
                params.append(source_type)

            cursor.execute(query, params)
            rows = cursor.fetchall()

        columns = [
            "learning_id", "mission_id", "learning_type", "title", "description",
            "problem_domain", "outcome", "relevance_keywords", "code_snippets",
            "files_created", "timestamp", "lesson_source", "source_type",
            "source_investigation_id", "investigation_query"
        ]

        scored_results = []
        for row in rows:
            data = dict(zip(columns, row))
            data["relevance_keywords"] = json.loads(data["relevance_keywords"] or "[]")
            data["code_snippets"] = json.loads(data["code_snippets"] or "[]")
            data["files_created"] = json.loads(data["files_created"] or "[]")

            learning_keywords = set(data["relevance_keywords"])
            overlap = len(target_keywords & learning_keywords)
            domain_bonus = 0.1 if data["problem_domain"] == target_domain else 0
            success_bonus = 0.05 if data["outcome"] == "success" else 0

            # Normalize keyword overlap to 0-1 range
            max_possible = max(len(target_keywords), 1)
            normalized_overlap = min(overlap / max_possible, 1.0)

            confidence = normalized_overlap + domain_bonus + success_bonus
            data['confidence_score'] = round(min(confidence, 1.0), 3)
            scored_results.append(data)

        scored_results.sort(key=lambda x: x['confidence_score'], reverse=True)
        return scored_results[:top_k]

    def _compute_confidence(
        self,
        tfidf_score: float,
        domain_match: bool,
        outcome: str,
        timestamp: str
    ) -> float:
        """
        Compute confidence score combining multiple relevance signals.

        Components:
        - TF-IDF cosine similarity: 0-1 (primary signal, 70% weight)
        - Domain match bonus: +0.1
        - Success outcome bonus: +0.05
        - Recency bonus: up to +0.05 (decays over 90 days)

        Returns:
            Confidence score in [0, 1]
        """
        # TF-IDF score is the primary signal (scale to 0.7 max)
        confidence = tfidf_score * 0.7

        # Domain match bonus
        if domain_match:
            confidence += 0.1

        # Success outcome bonus
        if outcome == 'success':
            confidence += 0.05

        # Recency bonus (decays over 90 days)
        try:
            learning_date = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            # Handle both aware and naive datetimes
            if learning_date.tzinfo is not None:
                learning_date = learning_date.replace(tzinfo=None)
            days_old = (datetime.now() - learning_date).days
            if days_old < 90:
                recency_bonus = 0.05 * (1 - days_old / 90)
                confidence += recency_bonus
        except Exception:
            pass  # Skip recency bonus on parse errors

        return min(1.0, confidence)

    def _get_learning_by_id(self, learning_id: str) -> Optional[MissionLearning]:
        """Retrieve a single learning by ID."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM learnings WHERE learning_id = ?", (learning_id,))
            row = cursor.fetchone()

        if not row:
            return None

        columns = [
            "learning_id", "mission_id", "learning_type", "title", "description",
            "problem_domain", "outcome", "relevance_keywords", "code_snippets",
            "files_created", "timestamp", "lesson_source", "source_type",
            "source_investigation_id", "investigation_query"
        ]

        # Handle case where row might have fewer columns (old DB schema)
        data = {}
        for i, col in enumerate(columns):
            if i < len(row):
                data[col] = row[i]
            else:
                data[col] = None

        data["relevance_keywords"] = json.loads(data["relevance_keywords"] or "[]")
        data["code_snippets"] = json.loads(data["code_snippets"] or "[]")
        data["files_created"] = json.loads(data["files_created"] or "[]")
        # Set defaults for investigation fields if not present
        data["source_type"] = data.get("source_type") or "mission"
        data["source_investigation_id"] = data.get("source_investigation_id")
        data["investigation_query"] = data.get("investigation_query")

        return MissionLearning.from_dict(data)

    def get_similar_missions(self, problem_statement: str, top_k: int = 3) -> List[MissionSummary]:
        """
        Find similar past missions.

        Args:
            problem_statement: The new problem to compare against
            top_k: Maximum number of missions to return

        Returns:
            List of similar MissionSummary objects
        """
        target_domain = self._infer_domain(problem_statement)
        target_keywords = set(self._extract_keywords(problem_statement))

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM mission_summaries")
            rows = cursor.fetchall()

        columns = [
            "mission_id", "problem_statement", "problem_domain", "outcome",
            "approach_taken", "key_learnings", "failures_encountered",
            "files_created", "duration_minutes", "cycles_used", "timestamp"
        ]

        scored_summaries = []
        for row in rows:
            data = dict(zip(columns, row))
            data["key_learnings"] = json.loads(data["key_learnings"] or "[]")
            data["failures_encountered"] = json.loads(data["failures_encountered"] or "[]")
            data["files_created"] = json.loads(data["files_created"] or "[]")

            summary = MissionSummary(**data)

            # Score by similarity
            summary_keywords = set(self._extract_keywords(summary.problem_statement))
            overlap = len(target_keywords & summary_keywords)
            domain_bonus = 3 if summary.problem_domain == target_domain else 0

            score = overlap + domain_bonus
            scored_summaries.append((score, summary))

        scored_summaries.sort(key=lambda x: x[0], reverse=True)
        return [summary for _, summary in scored_summaries[:top_k]]

    def generate_planning_context(self, problem_statement: str) -> str:
        """
        Generate context injection for PLANNING stage.

        Args:
            problem_statement: The new mission's problem statement

        Returns:
            Formatted context string to inject into prompt
        """
        # Get relevant learnings
        learnings = self.query_relevant_learnings(problem_statement, top_k=5)
        similar_missions = self.get_similar_missions(problem_statement, top_k=2)

        if not learnings and not similar_missions:
            return ""

        context_parts = []

        # Similar missions section
        if similar_missions:
            context_parts.append("### Similar Past Missions")
            for mission in similar_missions:
                outcome_emoji = "✓" if mission.outcome == "success" else "~" if mission.outcome == "partial" else "✗"
                context_parts.append(f"""
**{outcome_emoji} {mission.mission_id}** ({mission.outcome})
- Problem: {mission.problem_statement[:200]}...
- Approach: {mission.approach_taken or 'Not specified'}
- Duration: {mission.duration_minutes:.1f} minutes, {mission.cycles_used} cycles
""")

        # Relevant learnings section (learnings are now dicts with confidence_score and score_breakdown)
        if learnings:
            # Group by type
            techniques = [l for l in learnings if l.get("learning_type") == "technique"]
            insights = [l for l in learnings if l.get("learning_type") == "insight"]
            gotchas = [l for l in learnings if l.get("learning_type") == "gotcha"]

            def format_score_info(learning: dict) -> str:
                """Format confidence and optional score breakdown info."""
                confidence = learning.get("confidence_score", 0)
                breakdown = learning.get("score_breakdown")
                if breakdown:
                    # Show method breakdown when hybrid retrieval is active
                    return f"(score: {confidence:.2f} | tfidf: {breakdown.get('tfidf', 0):.2f}, emb: {breakdown.get('embedding', 0):.2f})"
                return f"(confidence: {confidence:.2f})"

            if techniques:
                context_parts.append("\n### Relevant Techniques from Past Missions")
                for t in techniques[:3]:
                    score_info = format_score_info(t)
                    desc = t.get("description", "")[:150]
                    context_parts.append(f"- **{t.get('title', 'Unknown')}** {score_info}: {desc}...")

            if insights:
                context_parts.append("\n### Relevant Insights")
                for i in insights[:3]:
                    score_info = format_score_info(i)
                    desc = i.get("description", "")[:200]
                    context_parts.append(f"- {score_info} {desc}")

            if gotchas:
                context_parts.append("\n### Gotchas to Avoid")
                for g in gotchas[:3]:
                    score_info = format_score_info(g)
                    desc = g.get("description", "")[:200]
                    context_parts.append(f"- ⚠️ {score_info} {desc}")

        if not context_parts:
            return ""

        return f"""
=== LEARNINGS FROM PAST MISSIONS ===
The knowledge base contains insights from similar past work.

{chr(10).join(context_parts)}

Use this context to inform your planning. Avoid repeating past failures.
=== END LEARNINGS ===
"""

    def ingest_all_mission_logs(self) -> Dict[str, Any]:
        """
        Ingest all mission logs from the mission_logs directory.

        Returns:
            Statistics about ingestion
        """
        stats = {"total": 0, "success": 0, "error": 0}

        if not MISSION_LOGS_DIR.exists():
            return {"status": "no_logs_dir", "stats": stats}

        for log_file in MISSION_LOGS_DIR.glob("*.json"):
            stats["total"] += 1
            result = self.ingest_completed_mission(log_file)
            if result.get("status") == "success":
                stats["success"] += 1
            else:
                stats["error"] += 1

        return {"status": "complete", "stats": stats}

    # =========================================================================
    # INVESTIGATION INGESTION
    # =========================================================================

    def ingest_investigation(self, investigation_dir: Path) -> Dict[str, Any]:
        """
        Extract learnings from a completed investigation.

        Parses:
        - artifacts/findings.json (subagent results)
        - artifacts/investigation_report.md (synthesized report)

        Args:
            investigation_dir: Path to the investigation workspace directory

        Returns:
            Dict with ingestion statistics
        """
        if isinstance(investigation_dir, str):
            investigation_dir = Path(investigation_dir)

        artifacts_dir = investigation_dir / "artifacts"
        findings_path = artifacts_dir / "findings.json"
        report_path = artifacts_dir / "investigation_report.md"

        # Read findings JSON
        try:
            if findings_path.exists():
                with open(findings_path) as f:
                    findings_data = json.load(f)
            else:
                logger.warning(f"No findings.json found in {artifacts_dir}")
                return {"status": "error", "message": "No findings.json found"}
        except Exception as e:
            logger.error(f"Failed to read findings.json: {e}")
            return {"status": "error", "message": str(e)}

        investigation_id = findings_data.get("investigation_id", "unknown")
        query = findings_data.get("query", "")
        subagent_results = findings_data.get("subagent_results", [])

        # Read report for additional context
        report_content = ""
        if report_path.exists():
            try:
                report_content = report_path.read_text()
            except Exception:
                pass

        # Extract learnings from subagent results
        learnings = []
        problem_domain = self._infer_domain(query)

        for result in subagent_results:
            if result.get("status") != "completed":
                continue

            focus_area = result.get("focus_area", "Unknown")
            findings = result.get("findings", "")

            if not findings or len(findings) < 20:
                continue

            # Try to extract structured findings from the response
            extracted = self._extract_investigation_findings(findings, focus_area, query)

            for finding in extracted:
                learning = MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{investigation_id}_{finding['id_suffix']}".encode()
                    ).hexdigest()[:16],
                    mission_id=investigation_id,  # Use investigation_id as mission_id
                    learning_type=finding['type'],
                    title=finding['title'],
                    description=finding['description'][:500],
                    problem_domain=problem_domain,
                    outcome="success",
                    relevance_keywords=self._extract_keywords(finding['description']),
                    files_created=finding.get('files', []),
                    lesson_source="investigation",
                    source_type="investigation",
                    source_investigation_id=investigation_id,
                    investigation_query=query
                )
                learnings.append(learning)

        # Also extract key insights from the synthesized report
        report_learnings = self._extract_report_learnings(
            report_content, investigation_id, query, problem_domain
        )
        learnings.extend(report_learnings)

        # Store all learnings
        learnings_stored = 0
        for learning in learnings:
            self._store_learning(learning)
            learnings_stored += 1

        if learnings_stored > 0:
            logger.info(f"Ingested investigation {investigation_id}: {learnings_stored} learnings")
        else:
            logger.warning(f"Investigation {investigation_id}: Zero learnings extracted")

        return {
            "status": "success",
            "investigation_id": investigation_id,
            "learnings_extracted": learnings_stored,
            "query": query
        }

    def _extract_investigation_findings(
        self,
        findings: str,
        focus_area: str,
        query: str
    ) -> List[Dict[str, Any]]:
        """
        Extract structured learnings from a subagent's findings.

        Attempts to parse JSON if present, otherwise extracts text-based findings.
        """
        extracted = []
        import re

        # Try to extract JSON from the response
        json_match = re.search(r'```json\s*(.*?)\s*```', findings, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))

                # Extract key_findings as technique learnings
                for i, finding in enumerate(data.get("key_findings", [])):
                    if finding and isinstance(finding, str) and len(finding) > 20:
                        extracted.append({
                            "id_suffix": f"finding_{focus_area[:10]}_{i}",
                            "type": "technique",
                            "title": f"Finding: {focus_area}",
                            "description": finding,
                            "files": []
                        })

                # Extract insights as insight learnings
                insights = data.get("insights", "")
                if insights and len(insights) > 30:
                    extracted.append({
                        "id_suffix": f"insight_{focus_area[:10]}",
                        "type": "insight",
                        "title": f"Insight: {focus_area}",
                        "description": insights,
                        "files": [f.get("path", "") for f in data.get("relevant_files", [])]
                    })

                # Extract follow-up questions as insight learnings
                for i, question in enumerate(data.get("follow_up_questions", [])):
                    if question and isinstance(question, str):
                        extracted.append({
                            "id_suffix": f"followup_{focus_area[:10]}_{i}",
                            "type": "insight",
                            "title": f"Follow-up: {focus_area}",
                            "description": question,
                            "files": []
                        })

                return extracted

            except json.JSONDecodeError:
                pass

        # Fallback: If no JSON, extract text as a single learning
        if len(findings) > 50:
            # Truncate very long findings
            description = findings[:1000] if len(findings) > 1000 else findings

            extracted.append({
                "id_suffix": f"raw_{focus_area[:10]}",
                "type": "insight",
                "title": f"Research: {focus_area}",
                "description": description,
                "files": []
            })

        return extracted

    def _extract_report_learnings(
        self,
        report_content: str,
        investigation_id: str,
        query: str,
        problem_domain: str
    ) -> List[MissionLearning]:
        """
        Extract learnings from the synthesized investigation report.

        Parses markdown sections for:
        - Executive Summary → technique learning
        - Key Findings → technique learnings
        - Recommendations → template learnings
        - Next Steps → insight learnings
        """
        learnings = []
        import re

        if not report_content or len(report_content) < 100:
            return learnings

        # Extract Executive Summary
        exec_match = re.search(
            r'##\s*Executive Summary\s*\n(.*?)(?=\n##|\Z)',
            report_content, re.DOTALL | re.IGNORECASE
        )
        if exec_match:
            summary = exec_match.group(1).strip()
            if len(summary) > 50:
                learnings.append(MissionLearning(
                    learning_id=hashlib.sha256(
                        f"{investigation_id}_exec_summary".encode()
                    ).hexdigest()[:16],
                    mission_id=investigation_id,
                    learning_type="technique",
                    title="Investigation Summary",
                    description=summary[:500],
                    problem_domain=problem_domain,
                    outcome="success",
                    relevance_keywords=self._extract_keywords(summary),
                    lesson_source="investigation_report",
                    source_type="investigation",
                    source_investigation_id=investigation_id,
                    investigation_query=query
                ))

        # Extract Key Findings as bullet points
        findings_match = re.search(
            r'##\s*Key Findings\s*\n(.*?)(?=\n##|\Z)',
            report_content, re.DOTALL | re.IGNORECASE
        )
        if findings_match:
            findings_section = findings_match.group(1)
            # Extract bullet points
            bullets = re.findall(r'[-*]\s+(.+?)(?=\n[-*]|\n\n|\Z)', findings_section, re.DOTALL)
            for i, bullet in enumerate(bullets[:5]):  # Limit to 5 findings
                bullet = bullet.strip()
                if len(bullet) > 30:
                    learnings.append(MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{investigation_id}_key_finding_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=investigation_id,
                        learning_type="technique",
                        title=f"Key Finding: {bullet[:50]}...",
                        description=bullet[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(bullet),
                        lesson_source="investigation_report",
                        source_type="investigation",
                        source_investigation_id=investigation_id,
                        investigation_query=query
                    ))

        # Extract Recommendations as template learnings
        rec_match = re.search(
            r'##\s*Recommendations?\s*\n(.*?)(?=\n##|\Z)',
            report_content, re.DOTALL | re.IGNORECASE
        )
        if rec_match:
            rec_section = rec_match.group(1)
            bullets = re.findall(r'[-*]\s+(.+?)(?=\n[-*]|\n\n|\Z)', rec_section, re.DOTALL)
            for i, bullet in enumerate(bullets[:5]):
                bullet = bullet.strip()
                if len(bullet) > 30:
                    learnings.append(MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{investigation_id}_recommendation_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=investigation_id,
                        learning_type="template",
                        title=f"Recommendation: {bullet[:50]}...",
                        description=bullet[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(bullet),
                        lesson_source="investigation_report",
                        source_type="investigation",
                        source_investigation_id=investigation_id,
                        investigation_query=query
                    ))

        # Extract Next Steps as insight learnings
        next_match = re.search(
            r'##\s*Next Steps?\s*\n(.*?)(?=\n##|\Z)',
            report_content, re.DOTALL | re.IGNORECASE
        )
        if next_match:
            next_section = next_match.group(1)
            bullets = re.findall(r'[-*\d.]\s+(.+?)(?=\n[-*\d.]|\n\n|\Z)', next_section, re.DOTALL)
            for i, bullet in enumerate(bullets[:5]):
                bullet = bullet.strip()
                if len(bullet) > 30:
                    learnings.append(MissionLearning(
                        learning_id=hashlib.sha256(
                            f"{investigation_id}_next_step_{i}".encode()
                        ).hexdigest()[:16],
                        mission_id=investigation_id,
                        learning_type="insight",
                        title=f"Next Step: {bullet[:50]}...",
                        description=bullet[:500],
                        problem_domain=problem_domain,
                        outcome="success",
                        relevance_keywords=self._extract_keywords(bullet),
                        lesson_source="investigation_report",
                        source_type="investigation",
                        source_investigation_id=investigation_id,
                        investigation_query=query
                    ))

        return learnings

    def ingest_all_investigations(self) -> Dict[str, Any]:
        """
        Ingest all investigations from the investigations directory.

        Returns:
            Statistics about ingestion
        """
        investigations_dir = BASE_DIR / "investigations"
        stats = {"total": 0, "success": 0, "error": 0, "learnings": 0}

        if not investigations_dir.exists():
            return {"status": "no_investigations_dir", "stats": stats}

        for inv_dir in investigations_dir.iterdir():
            if inv_dir.is_dir() and inv_dir.name.startswith("inv_"):
                stats["total"] += 1
                result = self.ingest_investigation(inv_dir)
                if result.get("status") == "success":
                    stats["success"] += 1
                    stats["learnings"] += result.get("learnings_extracted", 0)
                else:
                    stats["error"] += 1

        return {"status": "complete", "stats": stats}

    def get_investigation_learnings(
        self,
        investigation_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get learnings from investigations.

        Args:
            investigation_id: Optional specific investigation ID
            limit: Maximum number of learnings to return

        Returns:
            List of learning dicts
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            if investigation_id:
                cursor.execute("""
                    SELECT * FROM learnings
                    WHERE source_type = 'investigation'
                      AND source_investigation_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (investigation_id, limit))
            else:
                cursor.execute("""
                    SELECT * FROM learnings
                    WHERE source_type = 'investigation'
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))

            rows = cursor.fetchall()

        columns = [
            "learning_id", "mission_id", "learning_type", "title", "description",
            "problem_domain", "outcome", "relevance_keywords", "code_snippets",
            "files_created", "timestamp", "lesson_source", "source_type",
            "source_investigation_id", "investigation_query"
        ]

        learnings = []
        for row in rows:
            data = {}
            for i, col in enumerate(columns):
                if i < len(row):
                    data[col] = row[i]
                else:
                    data[col] = None

            data["relevance_keywords"] = json.loads(data["relevance_keywords"] or "[]")
            data["code_snippets"] = json.loads(data["code_snippets"] or "[]")
            data["files_created"] = json.loads(data["files_created"] or "[]")
            learnings.append(data)

        return learnings

    def get_investigation_stats(self) -> Dict[str, Any]:
        """
        Get statistics about investigation-sourced learnings.

        Returns:
            Dict with investigation statistics
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Count investigation learnings
            cursor.execute("""
                SELECT COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
            """)
            total_investigation_learnings = cursor.fetchone()[0]

            # Count distinct investigations
            cursor.execute("""
                SELECT COUNT(DISTINCT source_investigation_id) FROM learnings
                WHERE source_type = 'investigation'
            """)
            investigation_count = cursor.fetchone()[0]

            # Count by learning type
            cursor.execute("""
                SELECT learning_type, COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
                GROUP BY learning_type
            """)
            by_type = dict(cursor.fetchall())

            # Count by domain
            cursor.execute("""
                SELECT problem_domain, COUNT(*) FROM learnings
                WHERE source_type = 'investigation'
                GROUP BY problem_domain
            """)
            by_domain = dict(cursor.fetchall())

            # Get recent investigation queries
            cursor.execute("""
                SELECT DISTINCT investigation_query, source_investigation_id
                FROM learnings
                WHERE source_type = 'investigation'
                  AND investigation_query IS NOT NULL
                ORDER BY timestamp DESC
                LIMIT 10
            """)
            recent_queries = [{"query": r[0], "investigation_id": r[1]} for r in cursor.fetchall()]

        return {
            "total_investigation_learnings": total_investigation_learnings,
            "investigation_count": investigation_count,
            "by_type": by_type,
            "by_domain": by_domain,
            "recent_queries": recent_queries
        }

    def find_duplicate_learnings(self, threshold: float = 0.85) -> List[Dict[str, Any]]:
        """
        Find groups of semantically similar learnings that may be duplicates.

        Args:
            threshold: Minimum cosine similarity to consider as duplicate (0.0-1.0)
                      Higher = stricter matching. Default 0.85 is conservative.

        Returns:
            List of dicts with keys:
            - learning_ids: List of similar learning IDs in the group
            - similarity: Average pairwise similarity within the group
            - representative: ID of suggested learning to keep (has richest content)
            - count: Number of learnings in the group
        """
        # Ensure index is fitted
        if not self._semantic_index.is_fitted:
            self._semantic_index.fit()

        return self._semantic_index.find_duplicates(threshold)

    def merge_learnings(self, keep_id: str, merge_ids: List[str]) -> Dict[str, Any]:
        """
        Merge multiple learnings into one by combining their metadata.

        This is a SOFT merge - original learnings are marked as merged but not deleted.
        The kept learning gets combined keywords and code_snippets from all merged learnings.

        Args:
            keep_id: ID of the learning to keep (becomes the merged result)
            merge_ids: List of learning IDs to merge into keep_id

        Returns:
            Dict with merge result:
            - status: 'success' or 'error'
            - kept_learning: Updated learning dict
            - merged_count: Number of learnings merged
            - message: Description of what was done
        """
        # Get the learning to keep
        keep_learning = self._get_learning_by_id(keep_id)
        if keep_learning is None:
            return {"status": "error", "message": f"Learning {keep_id} not found"}

        # Collect data from all learnings to merge
        all_keywords = set(keep_learning.relevance_keywords)
        all_snippets = list(keep_learning.code_snippets)
        merged_count = 0

        for merge_id in merge_ids:
            if merge_id == keep_id:
                continue

            merge_learning = self._get_learning_by_id(merge_id)
            if merge_learning is None:
                continue

            # Combine keywords and snippets
            all_keywords.update(merge_learning.relevance_keywords)
            for snippet in merge_learning.code_snippets:
                if snippet not in all_snippets:
                    all_snippets.append(snippet)

            merged_count += 1

        if merged_count == 0:
            return {"status": "error", "message": "No valid learnings to merge"}

        # Update the kept learning with combined data
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Update kept learning
            cursor.execute("""
                UPDATE learnings
                SET relevance_keywords = ?,
                    code_snippets = ?
                WHERE learning_id = ?
            """, (
                json.dumps(list(all_keywords)),
                json.dumps(all_snippets),
                keep_id
            ))

            conn.commit()

        # Invalidate semantic index
        self._semantic_index.invalidate()

        # Return updated learning
        updated = self._get_learning_by_id(keep_id)
        return {
            "status": "success",
            "kept_learning": updated.to_dict() if updated else None,
            "merged_count": merged_count,
            "message": f"Merged {merged_count} learnings into {keep_id}"
        }

    def get_learning_clusters(
        self,
        distance_threshold: float = 0.7,
        min_cluster_size: int = 2
    ) -> List[Dict[str, Any]]:
        """
        Group learnings into semantic clusters using agglomerative clustering.

        Args:
            distance_threshold: Distance threshold for clustering (0.0-2.0)
                              Lower = more clusters, higher = fewer larger clusters
            min_cluster_size: Minimum learnings per cluster to include in results

        Returns:
            List of cluster dicts sorted by size (largest first):
            - cluster_id: Integer cluster identifier
            - theme: Auto-generated theme from top TF-IDF terms
            - coherence: Cluster coherence score (0.0-1.0)
            - size: Number of learnings in cluster
            - learnings: List of learning dicts in this cluster
        """
        # Ensure index is up to date
        if not self._semantic_index.ensure_up_to_date():
            return []

        clusters = self._semantic_index.get_clusters(distance_threshold)

        if not clusters:
            return []

        results = []
        for cluster_id, learning_ids in clusters.items():
            if len(learning_ids) < min_cluster_size:
                continue

            # Get full learning data
            learnings = []
            for lid in learning_ids:
                learning = self._get_learning_by_id(lid)
                if learning:
                    learnings.append(learning.to_dict())

            # Generate theme from top terms
            theme = self._generate_cluster_theme(learning_ids)

            # Compute cluster coherence
            coherence = self._semantic_index.compute_cluster_coherence(learning_ids)

            results.append({
                'cluster_id': cluster_id,
                'theme': theme,
                'coherence': round(coherence, 3),
                'size': len(learnings),
                'learnings': learnings
            })

        # Sort by size (largest first)
        results.sort(key=lambda x: x['size'], reverse=True)
        return results

    def _generate_cluster_theme(self, learning_ids: List[str]) -> str:
        """
        Generate a descriptive theme name for a cluster of learnings.

        Uses multiple strategies for theme generation:
        1. Top TF-IDF terms from the cluster
        2. If TF-IDF fails, use the most representative learning's title
        3. Fall back to 'General' if all else fails

        Args:
            learning_ids: List of learning IDs in the cluster

        Returns:
            Descriptive theme string
        """
        # Strategy 1: Use top TF-IDF terms
        top_terms = self._semantic_index.get_top_terms(learning_ids, top_n=3)

        if top_terms:
            # Clean up terms for display
            cleaned = [term.replace('_', ' ').title() for term in top_terms]
            return ', '.join(cleaned)

        # Strategy 2: Use representative learning's title as fallback
        if learning_ids:
            representative_id = self._semantic_index.get_representative_learning(learning_ids)
            if representative_id:
                learning = self._get_learning_by_id(representative_id)
                if learning and learning.title:
                    # Extract meaningful part of title
                    title = learning.title
                    # Remove common prefixes
                    for prefix in ['Achievement:', 'Issue:', 'Cycle', 'Deliverable:']:
                        if title.startswith(prefix):
                            title = title.split(':', 1)[-1].strip() if ':' in title else title
                            break
                    return title[:50] + ('...' if len(title) > 50 else '')

        return "General"

    def get_cluster_coherence(self, learning_ids: List[str]) -> float:
        """
        Get the coherence score for a group of learnings.

        Args:
            learning_ids: List of learning IDs to compute coherence for

        Returns:
            Coherence score between 0.0 and 1.0
        """
        return self._semantic_index.compute_cluster_coherence(learning_ids)

    def get_related_learnings(
        self,
        learning_id: str,
        threshold: float = 0.7,
        max_results: int = 10,
        exclude_same_mission: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Find learnings related to a specific learning.

        This enables 'learning chains' showing how techniques evolved across missions.

        Args:
            learning_id: The learning ID to find relations for
            threshold: Minimum similarity threshold (0.0-1.0)
            max_results: Maximum number of related learnings to return
            exclude_same_mission: If True, only return learnings from different missions

        Returns:
            List of related learning dicts with 'similarity' field added
        """
        # Get related learning IDs from semantic index
        related = self._semantic_index.find_related_learnings(
            learning_id, threshold, max_results * 2  # Fetch extra for filtering
        )

        if not related:
            return []

        # Get source learning's mission_id for filtering
        source_learning = self._get_learning_by_id(learning_id)
        source_mission = source_learning.mission_id if source_learning else None

        results = []
        for related_id, similarity in related:
            learning = self._get_learning_by_id(related_id)
            if learning is None:
                continue

            # Filter same mission if requested
            if exclude_same_mission and source_mission and learning.mission_id == source_mission:
                continue

            result = learning.to_dict()
            result['similarity'] = round(similarity, 3)
            results.append(result)

            if len(results) >= max_results:
                break

        return results

    def get_learning_chains(
        self,
        domain: Optional[str] = None,
        min_chain_length: int = 3,
        similarity_threshold: float = 0.6
    ) -> List[Dict[str, Any]]:
        """
        Find chains of related learnings across missions.

        A chain represents how a technique or concept evolved across multiple missions,
        showing knowledge transfer and refinement over time.

        Args:
            domain: Filter chains to a specific domain (optional)
            min_chain_length: Minimum number of learnings in a chain
            similarity_threshold: Minimum similarity for chain connections

        Returns:
            List of chain dicts:
            - chain_id: Unique identifier
            - theme: Auto-generated theme for the chain
            - coherence: How related the learnings in the chain are
            - learnings: List of learnings in chronological order
            - missions: List of unique mission IDs in the chain
        """
        # Get learnings, optionally filtered by domain
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if domain:
                cursor.execute("""
                    SELECT learning_id, mission_id, title, timestamp
                    FROM learnings
                    WHERE problem_domain = ?
                    ORDER BY timestamp
                """, (domain,))
            else:
                cursor.execute("""
                    SELECT learning_id, mission_id, title, timestamp
                    FROM learnings
                    ORDER BY timestamp
                """)
            rows = cursor.fetchall()

        if len(rows) < min_chain_length:
            return []

        # Build a graph of related learnings
        learning_ids = [row[0] for row in rows]
        learning_missions = {row[0]: row[1] for row in rows}

        # Find connected components using similarity
        visited = set()
        chains = []

        for start_id in learning_ids:
            if start_id in visited:
                continue

            # BFS to find chain
            chain = []
            queue = [start_id]
            chain_missions = set()

            while queue:
                current_id = queue.pop(0)
                if current_id in visited:
                    continue
                visited.add(current_id)
                chain.append(current_id)
                chain_missions.add(learning_missions.get(current_id))

                # Find related learnings (from different missions)
                related = self._semantic_index.find_related_learnings(
                    current_id, similarity_threshold, 5
                )
                for related_id, _ in related:
                    if related_id not in visited and related_id in learning_missions:
                        # Prefer cross-mission relations
                        if learning_missions.get(related_id) != learning_missions.get(current_id):
                            queue.append(related_id)

            # Only keep chains spanning multiple missions
            if len(chain) >= min_chain_length and len(chain_missions) >= 2:
                chains.append((chain, chain_missions))

        # Build result chains
        results = []
        for i, (chain_ids, missions) in enumerate(chains):
            # Get full learning data
            learnings = []
            for lid in chain_ids:
                learning = self._get_learning_by_id(lid)
                if learning:
                    learnings.append(learning.to_dict())

            # Sort by timestamp
            learnings.sort(key=lambda x: x.get('timestamp', ''))

            # Generate theme
            theme = self._generate_cluster_theme(chain_ids)
            coherence = self._semantic_index.compute_cluster_coherence(chain_ids)

            results.append({
                'chain_id': i,
                'theme': theme,
                'coherence': round(coherence, 3),
                'length': len(learnings),
                'learnings': learnings,
                'missions': list(missions)
            })

        # Sort by length (longest first)
        results.sort(key=lambda x: x['length'], reverse=True)
        return results

    def get_hierarchical_clusters(
        self,
        top_level_threshold: float = 0.9,
        sub_level_threshold: float = 0.6
    ) -> Dict[str, Any]:
        """
        Get hierarchical clusters with parent themes and sub-clusters.

        Args:
            top_level_threshold: Distance threshold for top-level clusters
            sub_level_threshold: Distance threshold for sub-clusters

        Returns:
            Dict with hierarchical cluster structure
        """
        return self._semantic_index.get_hierarchical_clusters(
            levels=2,
            top_level_threshold=top_level_threshold,
            sub_level_threshold=sub_level_threshold
        )

    def rebuild_semantic_index(self) -> bool:
        """
        Force rebuild of the semantic index.

        Call this after bulk data changes or if you suspect index corruption.

        Returns:
            True if index was rebuilt successfully
        """
        self._semantic_index.invalidate()
        return self._semantic_index.fit()

    # =========================================================================
    # GitHub Linking Methods
    # =========================================================================

    def link_github_artifact(
        self,
        mission_id: str,
        link_type: str,
        url: str,
        number: int = None,
        title: str = None,
        state: str = None
    ) -> bool:
        """
        Link a GitHub PR or issue to a mission.

        Args:
            mission_id: Mission ID to link to
            link_type: 'pr' or 'issue'
            url: GitHub URL
            number: PR/issue number
            title: PR/issue title
            state: Current state (open, closed, merged)

        Returns:
            True if linked successfully
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO github_links
                    (mission_id, link_type, url, number, title, state, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (mission_id, link_type, url, number, title, state, datetime.now().isoformat()))
                conn.commit()
            logger.info(f"Linked GitHub {link_type} {url} to mission {mission_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to link GitHub artifact: {e}")
            return False

    def get_mission_github_links(self, mission_id: str) -> List[Dict[str, Any]]:
        """
        Get all GitHub links for a mission.

        Args:
            mission_id: Mission ID to get links for

        Returns:
            List of GitHub link dictionaries
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM github_links WHERE mission_id = ?
                    ORDER BY created_at DESC
                ''', (mission_id,))
                rows = cursor.fetchall()
                return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Failed to get GitHub links for mission {mission_id}: {e}")
            return []

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics about the knowledge base"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # Count missions
            cursor.execute("SELECT COUNT(*) FROM mission_summaries")
            mission_count = cursor.fetchone()[0]

            # Count learnings by type
            cursor.execute("SELECT learning_type, COUNT(*) FROM learnings GROUP BY learning_type")
            learnings_by_type = dict(cursor.fetchall())

            # Count by domain
            cursor.execute("SELECT problem_domain, COUNT(*) FROM learnings GROUP BY problem_domain")
            learnings_by_domain = dict(cursor.fetchall())

            # Count by lesson source (Cycle 2 enhancement)
            cursor.execute("SELECT lesson_source, COUNT(*) FROM learnings GROUP BY lesson_source")
            learnings_by_source = dict(cursor.fetchall())

            # Success rate
            cursor.execute("SELECT outcome, COUNT(*) FROM mission_summaries GROUP BY outcome")
            outcomes = dict(cursor.fetchall())

        return {
            "total_missions": mission_count,
            "learnings_by_type": learnings_by_type,
            "learnings_by_domain": learnings_by_domain,
            "learnings_by_source": learnings_by_source,
            "mission_outcomes": outcomes,
            "total_learnings": sum(learnings_by_type.values()) if learnings_by_type else 0
        }


# Convenience function for quick access
_knowledge_base = None

def get_knowledge_base() -> MissionKnowledgeBase:
    """Get or create the global knowledge base instance"""
    global _knowledge_base
    if _knowledge_base is None:
        _knowledge_base = MissionKnowledgeBase()
    return _knowledge_base


if __name__ == "__main__":
    # Test/demo usage
    logging.basicConfig(level=logging.INFO)

    kb = MissionKnowledgeBase()

    # Ingest all existing logs
    print("Ingesting mission logs...")
    result = kb.ingest_all_mission_logs()
    print(f"Ingestion result: {result}")

    # Show statistics
    stats = kb.get_statistics()
    print(f"\nKnowledge Base Statistics:")
    print(f"  Total missions: {stats['total_missions']}")
    print(f"  Total learnings: {stats['total_learnings']}")
    print(f"  By type: {stats['learnings_by_type']}")
    print(f"  By domain: {stats['learnings_by_domain']}")
    print(f"  By source: {stats.get('learnings_by_source', {})}")

    # Demo query
    test_problem = "Improve the AtlasForge system by adding GPU acceleration for embeddings"
    context = kb.generate_planning_context(test_problem)
    print(f"\nPlanning context for '{test_problem[:50]}...':")
    print(context if context else "  (No relevant learnings found)")
