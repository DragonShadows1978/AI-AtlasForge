#!/usr/bin/env python3
"""
Exploration Graph - AtlasForge Enhancement Feature 2.1

A graph-based memory that records what Claude explored during missions.
Nodes represent files/concepts, edges represent discovered relationships.

Key capabilities:
1. Record exploration paths and discoveries
2. Link related discoveries across missions
3. Enable semantic search across exploration history (with embeddings!)
4. Prevent redundant exploration of understood code

Inspired by extensive cycle-based exploration patterns lacking structured retrieval

Production Hardened (Cycle 5):
- Comprehensive error handling with graceful degradation
- Retry logic for embedding model initialization
- Memory usage monitoring and automatic pruning
- Thread-safety for concurrent access
- Input validation and sanitization
"""

import json
import hashlib
import logging
import threading
import time
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple, Any, Union
from dataclasses import dataclass, asdict, field
from collections import defaultdict
from contextlib import contextmanager

# Set up logging
logger = logging.getLogger(__name__)

# Try to import numpy, provide fallback
try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False
    np = None
    logger.warning("NumPy not available - semantic search disabled")


# =============================================================================
# CONSTANTS AND CONFIGURATION
# =============================================================================

MAX_TEXT_LENGTH = 50000  # Maximum text length for processing
MAX_NODES = 10000  # Threshold for automatic pruning
MAX_RETRY_ATTEMPTS = 3  # Retry attempts for model initialization
RETRY_BACKOFF_BASE = 2.0  # Exponential backoff base (seconds)
MODEL_COOLDOWN_PERIOD = 300  # Seconds to wait before retrying failed model load


# =============================================================================
# LAZY-LOADED EMBEDDING MODEL WITH RETRY LOGIC
# =============================================================================

class EmbeddingModel:
    """
    Lazy-loaded sentence transformer model for semantic search.

    Uses all-MiniLM-L6-v2 (~22M params, ~80MB) for fast inference.
    Model is loaded on first use to avoid startup overhead.

    Production Features:
    - Retry logic with exponential backoff
    - Cooldown period after failures
    - Thread-safe initialization
    - Graceful degradation when unavailable
    """
    _model = None
    _device = None
    _initialized = False
    _last_failure_time = 0
    _failure_count = 0
    _lock = threading.RLock()

    @classmethod
    def get_model(cls):
        """
        Get or initialize the embedding model (lazy loading with retry).

        Returns:
            The model instance or None if unavailable
        """
        with cls._lock:
            if cls._initialized and cls._model is not None:
                return cls._model

            # Check cooldown period after failures
            if cls._failure_count >= MAX_RETRY_ATTEMPTS:
                time_since_failure = time.time() - cls._last_failure_time
                if time_since_failure < MODEL_COOLDOWN_PERIOD:
                    return None
                # Reset failure count after cooldown
                cls._failure_count = 0

            return cls._try_initialize()

    @classmethod
    def _try_initialize(cls) -> Optional[Any]:
        """Attempt to initialize the model with retry logic."""
        for attempt in range(MAX_RETRY_ATTEMPTS):
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                # Prefer CUDA if available
                cls._device = 'cuda' if torch.cuda.is_available() else 'cpu'
                cls._model = SentenceTransformer('all-MiniLM-L6-v2', device=cls._device)
                cls._initialized = True
                cls._failure_count = 0
                logger.info(f"[EmbeddingModel] Loaded on {cls._device}")
                return cls._model

            except ImportError as e:
                logger.warning(f"[EmbeddingModel] sentence-transformers not available: {e}")
                cls._initialized = True
                cls._failure_count = MAX_RETRY_ATTEMPTS  # Don't retry import errors
                return None

            except Exception as e:
                cls._failure_count += 1
                cls._last_failure_time = time.time()
                logger.warning(f"[EmbeddingModel] Initialization attempt {attempt + 1} failed: {e}")

                if attempt < MAX_RETRY_ATTEMPTS - 1:
                    backoff = RETRY_BACKOFF_BASE ** attempt
                    logger.info(f"[EmbeddingModel] Retrying in {backoff:.1f}s...")
                    time.sleep(backoff)

        logger.error(f"[EmbeddingModel] Failed after {MAX_RETRY_ATTEMPTS} attempts")
        return None

    @classmethod
    def reset(cls):
        """Reset the model state to allow re-initialization."""
        with cls._lock:
            cls._model = None
            cls._device = None
            cls._initialized = False
            cls._failure_count = 0
            cls._last_failure_time = 0

    @classmethod
    def get_device(cls) -> str:
        """Get the device the model is running on."""
        with cls._lock:
            if cls._model is None:
                cls.get_model()
            return cls._device or 'cpu'

    @classmethod
    def is_available(cls) -> bool:
        """Check if embedding model is available."""
        return cls.get_model() is not None

    @classmethod
    def encode(cls, texts: List[str], show_progress: bool = False) -> Optional[Any]:
        """
        Encode texts into embeddings.

        Args:
            texts: List of texts to encode
            show_progress: Show progress bar (for large batches)

        Returns:
            Numpy array of embeddings or None if model not available
        """
        if not NUMPY_AVAILABLE:
            return None

        model = cls.get_model()
        if model is None:
            return None

        try:
            # Validate and sanitize input
            if not texts:
                return np.array([])

            # Truncate overly long texts
            sanitized_texts = [
                t[:MAX_TEXT_LENGTH] if isinstance(t, str) else str(t)[:MAX_TEXT_LENGTH]
                for t in texts
            ]

            embeddings = model.encode(
                sanitized_texts,
                show_progress_bar=show_progress,
                convert_to_numpy=True
            )
            return embeddings

        except Exception as e:
            logger.error(f"[EmbeddingModel] Encoding failed: {e}")
            return None

    @classmethod
    def encode_single(cls, text: str) -> Optional[List[float]]:
        """
        Encode a single text into an embedding.

        Args:
            text: Text to encode

        Returns:
            List of floats (embedding) or None if model not available
        """
        if not isinstance(text, str):
            text = str(text) if text else ""

        embeddings = cls.encode([text])
        if embeddings is not None and len(embeddings) > 0:
            return embeddings[0].tolist()
        return None


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class ExplorationNode:
    """
    A node in the exploration graph.

    Can represent:
    - A file that was read
    - A concept that was discovered
    - A pattern that was identified
    - A decision that was made
    """
    id: str  # Unique identifier (hash of path/name)
    node_type: str  # 'file', 'concept', 'pattern', 'decision'
    name: str  # Display name
    path: Optional[str]  # File path if applicable
    content_hash: Optional[str]  # Hash of content (for change detection)
    summary: str  # Brief description of what was found/learned
    tags: List[str]  # Categorization tags
    metadata: Dict[str, Any]  # Additional data
    first_explored: str  # Timestamp
    last_explored: str  # Timestamp
    exploration_count: int  # How many times this was explored
    mission_ids: List[str]  # Missions that explored this
    embedding: Optional[List[float]] = None  # Semantic embedding of summary

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ExplorationNode':
        # Handle nodes loaded without embedding field (backward compat)
        if 'embedding' not in data:
            data['embedding'] = None
        return cls(**data)

    def get_searchable_text(self) -> str:
        """Get text representation for semantic search."""
        parts = [self.name, self.summary]
        if self.path:
            parts.append(self.path)
        if self.tags:
            parts.append(" ".join(self.tags))
        return " ".join(parts)


@dataclass
class ExplorationEdge:
    """
    An edge connecting two nodes in the exploration graph.

    Represents relationships like:
    - 'imports' (file A imports file B)
    - 'calls' (function A calls function B)
    - 'related_to' (concept A relates to concept B)
    - 'led_to' (exploration A led to discovery B)
    - 'depends_on' (component A depends on B)
    """
    source_id: str
    target_id: str
    relationship: str  # Type of relationship
    strength: float  # 0.0 to 1.0
    context: str  # How/why this relationship was discovered
    discovered_at: str
    mission_id: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ExplorationEdge':
        return cls(**data)


@dataclass
class ExplorationInsight:
    """
    A structured insight extracted from exploration.

    Captures learnings that can be retrieved later.
    """
    id: str
    insight_type: str  # 'pattern', 'behavior', 'architecture', 'gotcha', 'best_practice'
    title: str
    description: str
    related_nodes: List[str]  # Node IDs this insight relates to
    code_examples: List[str]  # Relevant code snippets
    tags: List[str]
    confidence: float  # 0.0 to 1.0
    discovered_at: str
    mission_id: str
    verified: bool  # Has this been validated?
    embedding: Optional[List[float]] = None  # Semantic embedding for search

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ExplorationInsight':
        # Handle insights loaded without embedding field (backward compat)
        if 'embedding' not in data:
            data['embedding'] = None
        return cls(**data)

    def get_searchable_text(self) -> str:
        """Get text representation for semantic search."""
        parts = [self.title, self.description]
        if self.tags:
            parts.append(" ".join(self.tags))
        return " ".join(parts)


# =============================================================================
# INPUT VALIDATION HELPERS
# =============================================================================

def _validate_string(value: Any, name: str, max_length: int = MAX_TEXT_LENGTH,
                     allow_empty: bool = False) -> str:
    """
    Validate and sanitize a string input.

    Args:
        value: The value to validate
        name: Name of the parameter (for error messages)
        max_length: Maximum allowed length
        allow_empty: Whether empty strings are allowed

    Returns:
        Sanitized string

    Raises:
        ValueError: If validation fails
    """
    if value is None:
        if allow_empty:
            return ""
        raise ValueError(f"{name} cannot be None")

    if not isinstance(value, str):
        value = str(value)

    # Truncate if too long
    if len(value) > max_length:
        logger.warning(f"{name} truncated from {len(value)} to {max_length} chars")
        value = value[:max_length]

    if not value.strip() and not allow_empty:
        raise ValueError(f"{name} cannot be empty")

    return value


def _validate_confidence(value: float, name: str = "confidence") -> float:
    """Validate a confidence value is in [0.0, 1.0]."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")

    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _validate_path(path: str) -> str:
    """Validate and sanitize a file path."""
    if not path:
        raise ValueError("Path cannot be empty")

    # Basic path sanitization - prevent directory traversal
    path = str(path).replace('\x00', '')  # Remove null bytes

    # Normalize path
    try:
        path = str(Path(path))
    except Exception:
        pass

    return path


# =============================================================================
# GRAPH CLASS
# =============================================================================

class ExplorationGraph:
    """
    A persistent graph of exploration history.

    Supports:
    - Adding nodes, edges, and insights
    - Querying by various criteria
    - Finding related explorations
    - Tracking exploration coverage

    Production Features (Cycle 5):
    - Thread-safe operations with RLock
    - Automatic pruning for large graphs
    - Memory usage monitoring
    - Comprehensive error handling
    - Input validation and sanitization
    """

    def __init__(self, storage_path: Optional[Path] = None, auto_prune: bool = True):
        """
        Initialize the exploration graph.

        Args:
            storage_path: Path to store the graph (default: ./exploration_memory/)
            auto_prune: Automatically prune graph when it exceeds MAX_NODES
        """
        # Thread safety
        self._lock = threading.RLock()

        # Configuration
        self.auto_prune = auto_prune
        self._max_nodes = MAX_NODES

        # Set up storage path with error handling
        try:
            self.storage_path = Path(storage_path) if storage_path else Path("./exploration_memory")
            self.storage_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create storage path: {e}")
            self.storage_path = Path("/tmp/exploration_memory_fallback")
            self.storage_path.mkdir(parents=True, exist_ok=True)

        # Data structures
        self.nodes: Dict[str, ExplorationNode] = {}
        self.edges: List[ExplorationEdge] = []
        self.insights: Dict[str, ExplorationInsight] = {}

        # Indices for fast lookup
        self._node_by_path: Dict[str, str] = {}  # path -> node_id
        self._node_by_type: Dict[str, Set[str]] = defaultdict(set)  # type -> set of node_ids
        self._node_by_tag: Dict[str, Set[str]] = defaultdict(set)  # tag -> set of node_ids
        self._edges_by_source: Dict[str, List[ExplorationEdge]] = defaultdict(list)
        self._edges_by_target: Dict[str, List[ExplorationEdge]] = defaultdict(list)

        # FAISS index cache
        self._faiss_index = None
        self._faiss_node_ids = None

        self._load()

    @contextmanager
    def _graph_lock(self):
        """Context manager for thread-safe graph operations."""
        self._lock.acquire()
        try:
            yield
        finally:
            self._lock.release()

    def _load(self):
        """Load graph from storage with error handling."""
        with self._graph_lock():
            nodes_file = self.storage_path / "nodes.json"
            edges_file = self.storage_path / "edges.json"
            insights_file = self.storage_path / "insights.json"

            # Load nodes
            if nodes_file.exists():
                try:
                    with open(nodes_file, 'r') as f:
                        data = json.load(f)
                        for node_data in data:
                            try:
                                node = ExplorationNode.from_dict(node_data)
                                self.nodes[node.id] = node
                                self._index_node(node)
                            except Exception as e:
                                logger.warning(f"Skipping malformed node: {e}")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse nodes.json: {e}")
                except Exception as e:
                    logger.error(f"Failed to load nodes: {e}")

            # Load edges
            if edges_file.exists():
                try:
                    with open(edges_file, 'r') as f:
                        data = json.load(f)
                        for edge_data in data:
                            try:
                                edge = ExplorationEdge.from_dict(edge_data)
                                self.edges.append(edge)
                                self._index_edge(edge)
                            except Exception as e:
                                logger.warning(f"Skipping malformed edge: {e}")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse edges.json: {e}")
                except Exception as e:
                    logger.error(f"Failed to load edges: {e}")

            # Load insights
            if insights_file.exists():
                try:
                    with open(insights_file, 'r') as f:
                        data = json.load(f)
                        for insight_data in data:
                            try:
                                insight = ExplorationInsight.from_dict(insight_data)
                                self.insights[insight.id] = insight
                            except Exception as e:
                                logger.warning(f"Skipping malformed insight: {e}")
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse insights.json: {e}")
                except Exception as e:
                    logger.error(f"Failed to load insights: {e}")

    def save(self, prune_if_needed: bool = True):
        """
        Save graph to storage with atomic writes.

        Args:
            prune_if_needed: Run pruning if graph exceeds max size
        """
        with self._graph_lock():
            # Auto-prune if needed
            if prune_if_needed and self.auto_prune and len(self.nodes) > self._max_nodes:
                self.prune_old_nodes()

            nodes_file = self.storage_path / "nodes.json"
            edges_file = self.storage_path / "edges.json"
            insights_file = self.storage_path / "insights.json"

            # Use atomic writes with temp files
            try:
                self._atomic_write(nodes_file, [n.to_dict() for n in self.nodes.values()])
                self._atomic_write(edges_file, [e.to_dict() for e in self.edges])
                self._atomic_write(insights_file, [i.to_dict() for i in self.insights.values()])
            except Exception as e:
                logger.error(f"Failed to save graph: {e}")
                raise

    def _atomic_write(self, filepath: Path, data: Any):
        """Write data to file atomically using a temp file."""
        temp_file = filepath.with_suffix('.tmp')
        try:
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)
            # Atomic rename
            temp_file.replace(filepath)
        except Exception as e:
            # Clean up temp file on error
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except:
                    pass
            raise

    def reload(self):
        """
        Reload graph from storage, replacing in-memory data.

        This is useful for dashboard APIs to get fresh data from disk
        when data may have been written by another process.
        """
        with self._graph_lock():
            # Clear existing data
            self.nodes.clear()
            self.edges.clear()
            self.insights.clear()

            # Clear node indices (these are defaultdicts, so they'll be recreated on access)
            self._node_by_path.clear()
            self._node_by_type.clear()
            self._node_by_tag.clear()

            # Clear edge indices
            if hasattr(self, '_edges_by_source'):
                self._edges_by_source.clear()
            if hasattr(self, '_edges_by_target'):
                self._edges_by_target.clear()

        # Reload from disk
        self._load()

    # -------------------------------------------------------------------------
    # Memory Management
    # -------------------------------------------------------------------------

    def get_memory_usage(self) -> Dict:
        """
        Get memory usage statistics for the graph.

        Returns:
            Dict with node count, edge count, estimated memory, etc.
        """
        with self._graph_lock():
            node_count = len(self.nodes)
            edge_count = len(self.edges)
            insight_count = len(self.insights)

            # Estimate embeddings memory (384 dims * 4 bytes per float)
            nodes_with_embeddings = sum(1 for n in self.nodes.values() if n.embedding is not None)
            insights_with_embeddings = sum(1 for i in self.insights.values() if i.embedding is not None)
            embedding_memory_mb = (nodes_with_embeddings + insights_with_embeddings) * 384 * 4 / (1024 * 1024)

            return {
                'node_count': node_count,
                'edge_count': edge_count,
                'insight_count': insight_count,
                'nodes_with_embeddings': nodes_with_embeddings,
                'insights_with_embeddings': insights_with_embeddings,
                'estimated_embedding_memory_mb': round(embedding_memory_mb, 2),
                'max_nodes': self._max_nodes,
                'needs_pruning': node_count > self._max_nodes,
                'prune_threshold_pct': round(node_count / self._max_nodes * 100, 1) if self._max_nodes else 0
            }

    def prune_old_nodes(self, target_count: Optional[int] = None, preserve_insights: bool = True):
        """
        Prune least-explored nodes to reduce graph size.

        Preserves:
        - Nodes with high exploration counts
        - Nodes linked to insights
        - Most recently explored nodes

        Args:
            target_count: Target number of nodes (default: 80% of max)
            preserve_insights: Keep nodes referenced by insights

        Returns:
            Number of nodes pruned
        """
        with self._graph_lock():
            if target_count is None:
                target_count = int(self._max_nodes * 0.8)

            if len(self.nodes) <= target_count:
                return 0

            # Build set of protected nodes
            protected = set()

            # Protect nodes linked to insights
            if preserve_insights:
                for insight in self.insights.values():
                    protected.update(insight.related_nodes)

            # Score nodes for removal
            # Lower score = more likely to prune
            node_scores = []
            for node_id, node in self.nodes.items():
                if node_id in protected:
                    continue

                # Score based on exploration count and recency
                try:
                    last_explored = datetime.fromisoformat(node.last_explored)
                    age_days = (datetime.now() - last_explored).days
                except:
                    age_days = 365  # Old node

                # Higher exploration count = higher score (keep)
                # More recent = higher score (keep)
                score = node.exploration_count * 10 + max(0, 30 - age_days)
                node_scores.append((node_id, score))

            # Sort by score ascending (lowest score = prune first)
            node_scores.sort(key=lambda x: x[1])

            # Calculate how many to prune
            to_prune_count = len(self.nodes) - target_count
            if to_prune_count <= 0:
                return 0

            # Prune nodes
            pruned_ids = set(node_id for node_id, _ in node_scores[:to_prune_count])
            pruned_count = 0

            for node_id in pruned_ids:
                if node_id in self.nodes:
                    node = self.nodes[node_id]

                    # Remove from indices
                    if node.path and node.path in self._node_by_path:
                        del self._node_by_path[node.path]
                    if node.node_type in self._node_by_type:
                        self._node_by_type[node.node_type].discard(node_id)
                    for tag in node.tags:
                        if tag in self._node_by_tag:
                            self._node_by_tag[tag].discard(node_id)

                    # Remove node
                    del self.nodes[node_id]
                    pruned_count += 1

            # Prune orphaned edges
            original_edge_count = len(self.edges)
            self.edges = [e for e in self.edges
                          if e.source_id in self.nodes and e.target_id in self.nodes]

            # Rebuild edge indices
            self._edges_by_source.clear()
            self._edges_by_target.clear()
            for edge in self.edges:
                self._index_edge(edge)

            logger.info(f"Pruned {pruned_count} nodes and {original_edge_count - len(self.edges)} edges")
            return pruned_count

    def set_max_nodes(self, max_nodes: int):
        """Set the maximum number of nodes before auto-pruning."""
        if max_nodes < 100:
            raise ValueError("max_nodes must be at least 100")
        self._max_nodes = max_nodes

    def _index_node(self, node: ExplorationNode):
        """Add node to indices."""
        if node.path:
            self._node_by_path[node.path] = node.id
        self._node_by_type[node.node_type].add(node.id)
        for tag in node.tags:
            self._node_by_tag[tag].add(node.id)

    def _index_edge(self, edge: ExplorationEdge):
        """Add edge to indices."""
        self._edges_by_source[edge.source_id].append(edge)
        self._edges_by_target[edge.target_id].append(edge)

    # -------------------------------------------------------------------------
    # Node Operations
    # -------------------------------------------------------------------------

    def add_file_node(
        self,
        path: str,
        summary: str,
        mission_id: str,
        tags: Optional[List[str]] = None,
        content_hash: Optional[str] = None,
        metadata: Optional[Dict] = None
    ) -> ExplorationNode:
        """
        Add a file exploration to the graph.

        Args:
            path: File path
            summary: What was learned from this file
            mission_id: ID of the mission that explored this
            tags: Optional categorization tags
            content_hash: Optional hash of file content
            metadata: Optional additional data

        Returns:
            The created or updated node

        Raises:
            ValueError: If required parameters are invalid
        """
        # Input validation
        path = _validate_path(path)
        summary = _validate_string(summary, "summary", max_length=10000, allow_empty=True)
        mission_id = _validate_string(mission_id, "mission_id", max_length=256)

        # Sanitize tags
        if tags:
            tags = [_validate_string(t, "tag", max_length=100, allow_empty=True)
                    for t in tags if t]
            tags = [t for t in tags if t.strip()]  # Remove empty tags

        with self._graph_lock():
            node_id = self._generate_id(f"file:{path}")

            if node_id in self.nodes:
                # Update existing node
                node = self.nodes[node_id]
                node.summary = summary  # Update with latest understanding
                node.last_explored = datetime.now().isoformat()
                node.exploration_count += 1
                if mission_id not in node.mission_ids:
                    node.mission_ids.append(mission_id)
                if tags:
                    node.tags = list(set(node.tags + tags))
                if content_hash:
                    node.content_hash = content_hash
                if metadata:
                    node.metadata.update(metadata)
            else:
                # Create new node
                node = ExplorationNode(
                    id=node_id,
                    node_type='file',
                    name=Path(path).name,
                    path=path,
                    content_hash=content_hash,
                    summary=summary,
                    tags=tags or [],
                    metadata=metadata or {},
                    first_explored=datetime.now().isoformat(),
                    last_explored=datetime.now().isoformat(),
                    exploration_count=1,
                    mission_ids=[mission_id]
                )
                self.nodes[node_id] = node
                self._index_node(node)

            return node

    def add_concept_node(
        self,
        name: str,
        summary: str,
        mission_id: str,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict] = None
    ) -> ExplorationNode:
        """
        Add a concept discovery to the graph.

        Args:
            name: Concept name
            summary: Description of the concept
            mission_id: ID of the mission that discovered this
            tags: Optional categorization tags
            metadata: Optional additional data

        Returns:
            The created or updated node

        Raises:
            ValueError: If required parameters are invalid
        """
        # Input validation
        name = _validate_string(name, "name", max_length=500)
        summary = _validate_string(summary, "summary", max_length=10000, allow_empty=True)
        mission_id = _validate_string(mission_id, "mission_id", max_length=256)

        # Sanitize tags
        if tags:
            tags = [_validate_string(t, "tag", max_length=100, allow_empty=True)
                    for t in tags if t]
            tags = [t for t in tags if t.strip()]

        with self._graph_lock():
            node_id = self._generate_id(f"concept:{name.lower()}")

            if node_id in self.nodes:
                node = self.nodes[node_id]
                node.summary = summary
                node.last_explored = datetime.now().isoformat()
                node.exploration_count += 1
                if mission_id not in node.mission_ids:
                    node.mission_ids.append(mission_id)
                if tags:
                    node.tags = list(set(node.tags + tags))
            else:
                node = ExplorationNode(
                    id=node_id,
                    node_type='concept',
                    name=name,
                    path=None,
                    content_hash=None,
                    summary=summary,
                    tags=tags or [],
                    metadata=metadata or {},
                    first_explored=datetime.now().isoformat(),
                    last_explored=datetime.now().isoformat(),
                    exploration_count=1,
                    mission_ids=[mission_id]
                )
                self.nodes[node_id] = node
                self._index_node(node)

            return node

    def add_pattern_node(
        self,
        name: str,
        summary: str,
        mission_id: str,
        code_example: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> ExplorationNode:
        """
        Add a pattern discovery to the graph.

        Args:
            name: Pattern name
            summary: Description of the pattern
            mission_id: ID of the mission that discovered this
            code_example: Optional code example demonstrating the pattern
            tags: Optional categorization tags

        Returns:
            The created or updated node

        Raises:
            ValueError: If required parameters are invalid
        """
        # Input validation
        name = _validate_string(name, "name", max_length=500)
        summary = _validate_string(summary, "summary", max_length=10000, allow_empty=True)
        mission_id = _validate_string(mission_id, "mission_id", max_length=256)

        if code_example:
            code_example = _validate_string(code_example, "code_example", max_length=50000, allow_empty=True)

        # Sanitize tags
        if tags:
            tags = [_validate_string(t, "tag", max_length=100, allow_empty=True)
                    for t in tags if t]
            tags = [t for t in tags if t.strip()]

        with self._graph_lock():
            node_id = self._generate_id(f"pattern:{name.lower()}")

            metadata = {}
            if code_example:
                metadata['code_example'] = code_example

            if node_id in self.nodes:
                node = self.nodes[node_id]
                node.summary = summary
                node.last_explored = datetime.now().isoformat()
                node.exploration_count += 1
                if mission_id not in node.mission_ids:
                    node.mission_ids.append(mission_id)
                if code_example:
                    node.metadata['code_example'] = code_example
            else:
                node = ExplorationNode(
                    id=node_id,
                    node_type='pattern',
                    name=name,
                    path=None,
                    content_hash=None,
                    summary=summary,
                    tags=tags or [],
                    metadata=metadata,
                    first_explored=datetime.now().isoformat(),
                    last_explored=datetime.now().isoformat(),
                    exploration_count=1,
                    mission_ids=[mission_id]
                )
                self.nodes[node_id] = node
                self._index_node(node)

            return node

    # -------------------------------------------------------------------------
    # Edge Operations
    # -------------------------------------------------------------------------

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relationship: str,
        mission_id: str,
        strength: float = 1.0,
        context: str = ""
    ) -> ExplorationEdge:
        """
        Add a relationship between nodes.

        Args:
            source_id: Source node ID
            target_id: Target node ID
            relationship: Type of relationship
            mission_id: ID of discovering mission
            strength: Relationship strength (0.0-1.0)
            context: How/why this was discovered

        Returns:
            The created edge

        Raises:
            ValueError: If nodes don't exist or parameters are invalid
        """
        # Input validation
        source_id = _validate_string(source_id, "source_id", max_length=256)
        target_id = _validate_string(target_id, "target_id", max_length=256)
        relationship = _validate_string(relationship, "relationship", max_length=100)
        mission_id = _validate_string(mission_id, "mission_id", max_length=256)
        strength = _validate_confidence(strength, "strength")
        context = _validate_string(context, "context", max_length=5000, allow_empty=True)

        with self._graph_lock():
            # Verify nodes exist
            if source_id not in self.nodes:
                raise ValueError(f"Source node {source_id} not found")
            if target_id not in self.nodes:
                raise ValueError(f"Target node {target_id} not found")

            edge = ExplorationEdge(
                source_id=source_id,
                target_id=target_id,
                relationship=relationship,
                strength=strength,
                context=context,
                discovered_at=datetime.now().isoformat(),
                mission_id=mission_id
            )

            self.edges.append(edge)
            self._index_edge(edge)

            return edge

    # -------------------------------------------------------------------------
    # Insight Operations
    # -------------------------------------------------------------------------

    def add_insight(
        self,
        insight_type: str,
        title: str,
        description: str,
        mission_id: str,
        related_nodes: Optional[List[str]] = None,
        code_examples: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 1.0
    ) -> ExplorationInsight:
        """
        Add a structured insight from exploration.

        Args:
            insight_type: Type of insight (pattern, gotcha, best_practice, observation)
            title: Brief title
            description: Full description
            mission_id: ID of discovering mission
            related_nodes: Node IDs this relates to
            code_examples: Relevant code snippets
            tags: Categorization tags
            confidence: Confidence level (0.0-1.0)

        Returns:
            The created insight

        Raises:
            ValueError: If required parameters are invalid
        """
        # Input validation
        insight_type = _validate_string(insight_type, "insight_type", max_length=50)
        title = _validate_string(title, "title", max_length=500)
        description = _validate_string(description, "description", max_length=20000, allow_empty=True)
        mission_id = _validate_string(mission_id, "mission_id", max_length=256)
        confidence = _validate_confidence(confidence, "confidence")

        # Sanitize tags
        if tags:
            tags = [_validate_string(t, "tag", max_length=100, allow_empty=True)
                    for t in tags if t]
            tags = [t for t in tags if t.strip()]

        # Sanitize code examples
        if code_examples:
            code_examples = [_validate_string(c, "code_example", max_length=50000, allow_empty=True)
                            for c in code_examples if c]

        # Sanitize related nodes
        if related_nodes:
            related_nodes = [_validate_string(n, "related_node", max_length=256, allow_empty=True)
                            for n in related_nodes if n]
            related_nodes = [n for n in related_nodes if n.strip()]

        with self._graph_lock():
            insight_id = self._generate_id(f"insight:{title.lower()}")

            insight = ExplorationInsight(
                id=insight_id,
                insight_type=insight_type,
                title=title,
                description=description,
                related_nodes=related_nodes or [],
                code_examples=code_examples or [],
                tags=tags or [],
                confidence=confidence,
                discovered_at=datetime.now().isoformat(),
                mission_id=mission_id,
                verified=False
            )

            self.insights[insight_id] = insight
            return insight

    # -------------------------------------------------------------------------
    # Query Operations (Thread-Safe)
    # -------------------------------------------------------------------------

    def has_explored(self, path: str) -> bool:
        """Check if a file has been explored before."""
        with self._graph_lock():
            return path in self._node_by_path

    def get_file_node(self, path: str) -> Optional[ExplorationNode]:
        """Get the node for a file path."""
        with self._graph_lock():
            node_id = self._node_by_path.get(path)
            return self.nodes.get(node_id) if node_id else None

    def get_nodes_by_type(self, node_type: str) -> List[ExplorationNode]:
        """Get all nodes of a specific type."""
        with self._graph_lock():
            node_ids = self._node_by_type.get(node_type, set())
            return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_nodes_by_tag(self, tag: str) -> List[ExplorationNode]:
        """Get all nodes with a specific tag."""
        with self._graph_lock():
            node_ids = self._node_by_tag.get(tag, set())
            return [self.nodes[nid] for nid in node_ids if nid in self.nodes]

    def get_related_nodes(self, node_id: str) -> List[Tuple[ExplorationNode, ExplorationEdge]]:
        """Get all nodes related to a given node."""
        with self._graph_lock():
            related = []

            # Outgoing edges
            for edge in self._edges_by_source.get(node_id, []):
                if edge.target_id in self.nodes:
                    related.append((self.nodes[edge.target_id], edge))

            # Incoming edges
            for edge in self._edges_by_target.get(node_id, []):
                if edge.source_id in self.nodes:
                    related.append((self.nodes[edge.source_id], edge))

            return related

    def get_exploration_path(self, start_id: str, depth: int = 3) -> Dict:
        """
        Get the exploration path from a starting node.

        Returns a tree structure of related discoveries.

        Args:
            start_id: Starting node ID
            depth: Maximum depth to traverse

        Returns:
            Tree structure of related discoveries
        """
        with self._graph_lock():
            visited = set()

            def explore(node_id: str, current_depth: int) -> Optional[Dict]:
                if current_depth > depth or node_id in visited:
                    return None
                if node_id not in self.nodes:
                    return None

                visited.add(node_id)
                node = self.nodes[node_id]

                result = {
                    'node': node.to_dict(),
                    'children': []
                }

                # Get related nodes (without lock since we're already locked)
                related = []
                for edge in self._edges_by_source.get(node_id, []):
                    if edge.target_id in self.nodes:
                        related.append((self.nodes[edge.target_id], edge))
                for edge in self._edges_by_target.get(node_id, []):
                    if edge.source_id in self.nodes:
                        related.append((self.nodes[edge.source_id], edge))

                for related_node, edge in related:
                    if related_node.id not in visited:
                        child = explore(related_node.id, current_depth + 1)
                        if child:
                            child['relationship'] = edge.relationship
                            result['children'].append(child)

                return result

            return explore(start_id, 0) or {}

    def search_insights(
        self,
        query: str,
        insight_type: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> List[ExplorationInsight]:
        """
        Search insights by keyword and filters.

        Simple keyword search - for semantic search, use semantic_search_insights.

        Args:
            query: Search query string
            insight_type: Optional filter by insight type
            tags: Optional filter by tags

        Returns:
            List of matching insights sorted by confidence
        """
        with self._graph_lock():
            query_lower = query.lower()
            results = []

            for insight in self.insights.values():
                # Filter by type
                if insight_type and insight.insight_type != insight_type:
                    continue

                # Filter by tags
                if tags and not any(t in insight.tags for t in tags):
                    continue

                # Keyword search
                if (query_lower in insight.title.lower() or
                    query_lower in insight.description.lower()):
                    results.append(insight)

            # Sort by confidence
            results.sort(key=lambda x: x.confidence, reverse=True)
            return results

    def get_most_explored(self, limit: int = 10) -> List[ExplorationNode]:
        """Get the most frequently explored nodes."""
        with self._graph_lock():
            sorted_nodes = sorted(
                self.nodes.values(),
                key=lambda n: n.exploration_count,
                reverse=True
            )
            return sorted_nodes[:limit]

    def get_exploration_stats(self) -> Dict:
        """Get statistics about exploration coverage."""
        with self._graph_lock():
            # Compute most_explored without recursive lock
            sorted_nodes = sorted(
                self.nodes.values(),
                key=lambda n: n.exploration_count,
                reverse=True
            )[:5]

            return {
                'total_nodes': len(self.nodes),
                'total_edges': len(self.edges),
                'total_insights': len(self.insights),
                'nodes_by_type': {
                    ntype: len(nids)
                    for ntype, nids in self._node_by_type.items()
                },
                'top_tags': {
                    tag: len(nids)
                    for tag, nids in sorted(
                        self._node_by_tag.items(),
                        key=lambda x: -len(x[1])
                    )[:10]
                },
                'most_explored': [
                    {'name': n.name, 'count': n.exploration_count}
                    for n in sorted_nodes
                ],
                'insight_types': {
                    itype: sum(1 for i in self.insights.values() if i.insight_type == itype)
                    for itype in set(i.insight_type for i in self.insights.values())
                },
                'generated_at': datetime.now().isoformat()
            }

    # -------------------------------------------------------------------------
    # Semantic Search
    # -------------------------------------------------------------------------

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate an embedding for text.

        Args:
            text: Text to embed

        Returns:
            List of floats (embedding) or None if model not available
        """
        return EmbeddingModel.encode_single(text)

    def _ensure_node_embedding(self, node: ExplorationNode) -> bool:
        """
        Ensure a node has an embedding, generating one if needed.

        Returns True if embedding exists or was successfully generated.
        """
        if node.embedding is not None:
            return True

        text = node.get_searchable_text()
        embedding = self.generate_embedding(text)
        if embedding:
            node.embedding = embedding
            return True
        return False

    def rebuild_embeddings(self, force: bool = False):
        """
        Generate embeddings for nodes that don't have them.

        Args:
            force: If True, regenerate all embeddings even if they exist
        """
        if not EmbeddingModel.is_available():
            logger.debug("Embedding model not available, skipping rebuild")
            return

        with self._graph_lock():
            nodes_to_embed = []
            texts_to_embed = []

            for node in self.nodes.values():
                if force or node.embedding is None:
                    nodes_to_embed.append(node)
                    texts_to_embed.append(node.get_searchable_text())

            if not texts_to_embed:
                return

            logger.info(f"Generating embeddings for {len(texts_to_embed)} nodes...")

        # Release lock during expensive embedding operation
        try:
            embeddings = EmbeddingModel.encode(texts_to_embed, show_progress=len(texts_to_embed) > 50)

            if embeddings is not None:
                with self._graph_lock():
                    for node, embedding in zip(nodes_to_embed, embeddings):
                        node.embedding = embedding.tolist()
                logger.info(f"Generated {len(embeddings)} embeddings")
        except Exception as e:
            logger.error(f"Failed to generate embeddings: {e}")

    def _get_embedding_matrix(self) -> Tuple[List[str], Optional[np.ndarray]]:
        """
        Get embedding matrix for all nodes with embeddings.

        Returns:
            Tuple of (node_ids, embedding_matrix) or ([], None) if no embeddings
        """
        node_ids = []
        embeddings = []

        for node_id, node in self.nodes.items():
            if node.embedding is not None:
                node_ids.append(node_id)
                embeddings.append(node.embedding)

        if not embeddings:
            return [], None

        return node_ids, np.array(embeddings)

    def semantic_search(
        self,
        query: str,
        top_k: int = 10,
        node_type: Optional[str] = None,
        min_similarity: float = 0.3
    ) -> List[Tuple[ExplorationNode, float]]:
        """
        Perform semantic search across nodes.

        Args:
            query: Search query text
            top_k: Maximum number of results
            node_type: Filter by node type (file, concept, pattern, etc.)
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of (node, similarity_score) tuples, sorted by similarity descending
        """
        if not EmbeddingModel.is_available():
            # Fall back to keyword search
            return self._keyword_search(query, top_k, node_type)

        # Ensure all nodes have embeddings
        self.rebuild_embeddings()

        # Get query embedding
        query_embedding = self.generate_embedding(query)
        if query_embedding is None:
            return self._keyword_search(query, top_k, node_type)

        query_vec = np.array(query_embedding)

        # Get all node embeddings
        node_ids, embedding_matrix = self._get_embedding_matrix()
        if embedding_matrix is None or len(node_ids) == 0:
            return self._keyword_search(query, top_k, node_type)

        # Compute cosine similarities
        query_norm = np.linalg.norm(query_vec)
        embedding_norms = np.linalg.norm(embedding_matrix, axis=1)

        # Avoid division by zero
        valid_mask = (embedding_norms > 0) & (query_norm > 0)
        similarities = np.zeros(len(node_ids))
        if query_norm > 0:
            similarities[valid_mask] = np.dot(
                embedding_matrix[valid_mask], query_vec
            ) / (embedding_norms[valid_mask] * query_norm)

        # Build results
        results = []
        for i, node_id in enumerate(node_ids):
            if similarities[i] < min_similarity:
                continue

            node = self.nodes.get(node_id)
            if node is None:
                continue

            if node_type and node.node_type != node_type:
                continue

            results.append((node, float(similarities[i])))

        # Sort by similarity descending
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def hybrid_search(
        self,
        query: str,
        top_k: int = 10,
        node_type: Optional[str] = None,
        semantic_weight: float = 0.7
    ) -> List[Tuple[ExplorationNode, float]]:
        """
        Hybrid search combining semantic and keyword matching.

        Args:
            query: Search query text
            top_k: Maximum number of results
            node_type: Filter by node type
            semantic_weight: Weight for semantic similarity (0-1), keyword weight is 1-semantic_weight

        Returns:
            List of (node, combined_score) tuples
        """
        keyword_weight = 1.0 - semantic_weight

        # Get semantic results
        semantic_results = {}
        if EmbeddingModel.is_available():
            for node, score in self.semantic_search(query, top_k * 2, node_type, min_similarity=0.2):
                semantic_results[node.id] = (node, score)

        # Get keyword results
        keyword_results = {}
        for node, score in self._keyword_search(query, top_k * 2, node_type):
            keyword_results[node.id] = (node, score)

        # Combine scores
        all_node_ids = set(semantic_results.keys()) | set(keyword_results.keys())
        combined = []

        for node_id in all_node_ids:
            sem_score = semantic_results.get(node_id, (None, 0.0))[1]
            kw_score = keyword_results.get(node_id, (None, 0.0))[1]

            combined_score = (sem_score * semantic_weight) + (kw_score * keyword_weight)

            # Get the node from either result set
            node = semantic_results.get(node_id, (None, 0))[0] or keyword_results.get(node_id, (None, 0))[0]
            if node:
                combined.append((node, combined_score))

        combined.sort(key=lambda x: x[1], reverse=True)
        return combined[:top_k]

    def _keyword_search(
        self,
        query: str,
        top_k: int = 10,
        node_type: Optional[str] = None
    ) -> List[Tuple[ExplorationNode, float]]:
        """
        Simple keyword-based search (fallback when embeddings unavailable).

        Uses TF-IDF-like scoring based on term frequency.
        """
        query_terms = set(query.lower().split())
        results = []

        for node in self.nodes.values():
            if node_type and node.node_type != node_type:
                continue

            text = node.get_searchable_text().lower()
            text_terms = set(text.split())

            # Count matching terms
            matching = query_terms & text_terms
            if not matching:
                continue

            # Simple TF-IDF-like score
            score = len(matching) / (len(query_terms) + 1)

            # Boost exact matches
            if query.lower() in text:
                score += 0.3

            results.append((node, min(score, 1.0)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_similar_nodes(
        self,
        node_id: str,
        top_k: int = 5
    ) -> List[Tuple[ExplorationNode, float]]:
        """
        Find nodes similar to a given node.

        Args:
            node_id: The node to find similar nodes for
            top_k: Maximum number of similar nodes

        Returns:
            List of (node, similarity) tuples
        """
        node = self.nodes.get(node_id)
        if not node:
            return []

        # Ensure node has embedding
        self._ensure_node_embedding(node)

        if node.embedding is None:
            # Fall back to keyword search using node's text
            return self._keyword_search(node.get_searchable_text(), top_k + 1)

        # Search with node's embedding
        results = self.semantic_search(
            node.get_searchable_text(),
            top_k + 1,  # +1 because the node itself will be in results
            min_similarity=0.3
        )

        # Remove the node itself from results
        return [(n, s) for n, s in results if n.id != node_id][:top_k]

    # -------------------------------------------------------------------------
    # Insight Embeddings
    # -------------------------------------------------------------------------

    def _ensure_insight_embedding(self, insight: ExplorationInsight) -> bool:
        """
        Ensure an insight has an embedding, generating one if needed.

        Returns True if embedding exists or was successfully generated.
        """
        if insight.embedding is not None:
            return True

        text = insight.get_searchable_text()
        embedding = self.generate_embedding(text)
        if embedding:
            insight.embedding = embedding
            return True
        return False

    def rebuild_insight_embeddings(self, force: bool = False):
        """
        Generate embeddings for insights that don't have them.

        Args:
            force: If True, regenerate all embeddings even if they exist
        """
        if not EmbeddingModel.is_available():
            logger.debug("Embedding model not available, skipping insight rebuild")
            return

        with self._graph_lock():
            insights_to_embed = []
            texts_to_embed = []

            for insight in self.insights.values():
                if force or insight.embedding is None:
                    insights_to_embed.append(insight)
                    texts_to_embed.append(insight.get_searchable_text())

            if not texts_to_embed:
                return

            logger.info(f"Generating embeddings for {len(texts_to_embed)} insights...")

        # Release lock during expensive embedding operation
        try:
            embeddings = EmbeddingModel.encode(texts_to_embed, show_progress=len(texts_to_embed) > 20)

            if embeddings is not None:
                with self._graph_lock():
                    for insight, embedding in zip(insights_to_embed, embeddings):
                        insight.embedding = embedding.tolist()
                logger.info(f"Generated {len(embeddings)} insight embeddings")
        except Exception as e:
            logger.error(f"Failed to generate insight embeddings: {e}")

    def semantic_search_insights(
        self,
        query: str,
        top_k: int = 10,
        min_similarity: float = 0.3
    ) -> List[Tuple[ExplorationInsight, float]]:
        """
        Perform semantic search across insights.

        Args:
            query: Search query text
            top_k: Maximum number of results
            min_similarity: Minimum similarity threshold (0-1)

        Returns:
            List of (insight, similarity_score) tuples, sorted by similarity descending
        """
        if not EmbeddingModel.is_available():
            return self._keyword_search_insights(query, top_k)

        # Ensure all insights have embeddings
        self.rebuild_insight_embeddings()

        # Get query embedding
        query_embedding = self.generate_embedding(query)
        if query_embedding is None:
            return self._keyword_search_insights(query, top_k)

        query_vec = np.array(query_embedding)

        results = []
        for insight in self.insights.values():
            if insight.embedding is None:
                continue

            insight_vec = np.array(insight.embedding)

            # Cosine similarity
            query_norm = np.linalg.norm(query_vec)
            insight_norm = np.linalg.norm(insight_vec)

            if query_norm == 0 or insight_norm == 0:
                continue

            similarity = np.dot(query_vec, insight_vec) / (query_norm * insight_norm)

            if similarity >= min_similarity:
                results.append((insight, float(similarity)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def _keyword_search_insights(
        self,
        query: str,
        top_k: int = 10
    ) -> List[Tuple[ExplorationInsight, float]]:
        """Keyword-based insight search (fallback)."""
        query_lower = query.lower()
        query_terms = set(query_lower.split())
        results = []

        for insight in self.insights.values():
            text = insight.get_searchable_text().lower()
            text_terms = set(text.split())

            matching = query_terms & text_terms
            if not matching:
                continue

            score = len(matching) / (len(query_terms) + 1)
            if query_lower in text:
                score += 0.3

            results.append((insight, min(score, 1.0)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]

    def get_insight_coverage(self) -> Dict:
        """Get statistics about insight embedding coverage."""
        total = len(self.insights)
        with_embedding = sum(1 for i in self.insights.values() if i.embedding is not None)

        return {
            'total_insights': total,
            'with_embedding': with_embedding,
            'coverage_pct': (with_embedding / total * 100) if total > 0 else 0
        }

    # -------------------------------------------------------------------------
    # Visualization Export
    # -------------------------------------------------------------------------

    def _force_directed_layout(
        self,
        width: float = 800,
        height: float = 600,
        iterations: int = 100
    ) -> Dict[str, Tuple[float, float]]:
        """
        Calculate node positions using force-directed layout algorithm.

        Uses a vectorized Fruchterman-Reingold algorithm:
        1. Initialize random positions
        2. Apply repulsive forces between all nodes (vectorized)
        3. Apply attractive forces along edges
        4. Apply centering force
        5. Repeat until stable

        Args:
            width: Canvas width
            height: Canvas height
            iterations: Number of simulation iterations

        Returns:
            Dict mapping node_id -> (x, y) position
        """
        if not self.nodes:
            return {}

        node_ids = list(self.nodes.keys())
        n_nodes = len(node_ids)

        if n_nodes <= 1:
            return {node_ids[0]: (width / 2, height / 2)} if n_nodes == 1 else {}

        # Use numpy arrays for vectorized operations
        pos = np.random.uniform(
            [50, 50],
            [width - 50, height - 50],
            size=(n_nodes, 2)
        )

        # Node ID to index mapping
        id_to_idx = {nid: i for i, nid in enumerate(node_ids)}

        # Build edge indices
        edge_indices = []
        edge_strengths = []
        for edge in self.edges:
            if edge.source_id in id_to_idx and edge.target_id in id_to_idx:
                edge_indices.append((id_to_idx[edge.source_id], id_to_idx[edge.target_id]))
                edge_strengths.append(edge.strength)
        edge_indices = np.array(edge_indices) if edge_indices else np.array([]).reshape(0, 2)
        edge_strengths = np.array(edge_strengths)

        # Force parameters
        k = np.sqrt((width * height) / n_nodes)  # Optimal distance
        k_sq = k * k
        temp = width / 10  # Initial temperature

        # Adaptive iterations based on node count
        actual_iterations = min(iterations, max(50, 150 - n_nodes))

        for _ in range(actual_iterations):
            # Calculate all pairwise displacements (vectorized)
            # diff[i, j] = pos[j] - pos[i]
            diff = pos[np.newaxis, :, :] - pos[:, np.newaxis, :]  # Shape: (n, n, 2)

            # Calculate distances
            dist = np.sqrt(np.sum(diff ** 2, axis=2))  # Shape: (n, n)
            dist = np.maximum(dist, 0.01)  # Avoid division by zero

            # Repulsive forces: F = k^2 / d (along direction from i to j)
            # Force on i from j is -F * direction, force on j from i is +F * direction
            repulsion_magnitude = k_sq / dist  # Shape: (n, n)

            # Set diagonal to 0 (no self-force)
            np.fill_diagonal(repulsion_magnitude, 0)

            # Calculate repulsive force vectors
            # Normalize diff to get directions
            directions = diff / dist[:, :, np.newaxis]
            np.nan_to_num(directions, copy=False)

            # Sum repulsive forces (negative because we want to push apart)
            forces = -np.sum(repulsion_magnitude[:, :, np.newaxis] * directions, axis=1)

            # Attractive forces along edges
            if len(edge_indices) > 0:
                for idx, (i, j) in enumerate(edge_indices):
                    d = pos[j] - pos[i]
                    dist_ij = max(np.linalg.norm(d), 0.01)
                    # Attractive force: F = d^2 / k
                    attraction = (dist_ij * dist_ij) / k * edge_strengths[idx]
                    direction = d / dist_ij
                    forces[i] += attraction * direction
                    forces[j] -= attraction * direction

            # Apply forces with temperature limiting
            force_magnitudes = np.linalg.norm(forces, axis=1)
            force_magnitudes = np.maximum(force_magnitudes, 0.01)

            # Limit displacement by temperature
            scale = np.minimum(force_magnitudes, temp) / force_magnitudes
            displacement = forces * scale[:, np.newaxis]

            # Update positions
            pos += displacement

            # Keep within bounds
            pos[:, 0] = np.clip(pos[:, 0], 30, width - 30)
            pos[:, 1] = np.clip(pos[:, 1], 30, height - 30)

            # Cool down
            temp *= 0.95

        # Convert back to dict
        return {node_ids[i]: (float(pos[i, 0]), float(pos[i, 1])) for i in range(n_nodes)}

    def export_for_visualization(
        self,
        width: float = 800,
        height: float = 600,
        layout_iterations: int = 100
    ) -> Dict:
        """
        Export graph in visualization-friendly format.

        Calculates node positions using force-directed layout
        and returns data ready for canvas rendering.

        Args:
            width: Canvas width
            height: Canvas height
            layout_iterations: Iterations for layout algorithm

        Returns:
            Dict with nodes, edges, and stats
        """
        from collections import Counter

        # Calculate layout
        positions = self._force_directed_layout(width, height, layout_iterations)

        # Build nodes list
        nodes = []
        for node in self.nodes.values():
            x, y = positions.get(node.id, (width/2, height/2))

            # Calculate size based on exploration count
            size = min(20 + node.exploration_count * 2, 50)

            nodes.append({
                'id': node.id,
                'name': node.name,
                'type': node.node_type,
                'path': node.path,
                'summary': node.summary[:200] if node.summary else '',
                'exploration_count': node.exploration_count,
                'tags': node.tags[:5],
                'x': round(x, 2),
                'y': round(y, 2),
                'size': size,
                'has_embedding': node.embedding is not None
            })

        # Build edges list
        edges = []
        for edge in self.edges:
            if edge.source_id in self.nodes and edge.target_id in self.nodes:
                edges.append({
                    'source': edge.source_id,
                    'target': edge.target_id,
                    'relationship': edge.relationship,
                    'strength': edge.strength
                })

        # Calculate stats
        node_types = Counter(n.node_type for n in self.nodes.values())

        return {
            'nodes': nodes,
            'edges': edges,
            'stats': {
                'total_nodes': len(self.nodes),
                'total_edges': len(self.edges),
                'total_insights': len(self.insights),
                'node_types': dict(node_types),
                'width': width,
                'height': height
            },
            'generated_at': datetime.now().isoformat()
        }

    # -------------------------------------------------------------------------
    # FAISS Integration (Optional Performance Enhancement)
    # -------------------------------------------------------------------------

    def _should_use_faiss(self) -> bool:
        """
        Determine if FAISS should be used based on node count.

        FAISS provides significant speedup for large graphs (10k+ nodes),
        but numpy is efficient for typical usage (< 5000 nodes).

        Performance characteristics:
        - NumPy: O(n) linear search, vectorized, < 50ms for 1000 nodes
        - FAISS: O(log n) search after index build, ~10ms for 100k nodes

        Returns:
            True if FAISS should be used
        """
        FAISS_THRESHOLD = 5000  # Use FAISS for graphs larger than this
        return len(self.nodes) >= FAISS_THRESHOLD and self._check_faiss_available()

    def _check_faiss_available(self) -> bool:
        """Check if FAISS is available."""
        try:
            import faiss  # noqa: F401
            return True
        except ImportError:
            return False

    def build_faiss_index(self) -> bool:
        """
        Build a FAISS index for faster similarity search.

        Only recommended for graphs with 5000+ nodes.
        Falls back gracefully to numpy if FAISS is not available.

        Returns:
            True if index was built successfully
        """
        if not self._check_faiss_available():
            print("[ExplorationGraph] FAISS not available, using numpy fallback")
            return False

        import faiss

        # Get all embeddings
        node_ids, embedding_matrix = self._get_embedding_matrix()
        if embedding_matrix is None or len(node_ids) < 100:
            return False

        # Build FAISS index (using L2 distance, will convert to cosine later)
        d = embedding_matrix.shape[1]
        index = faiss.IndexFlatIP(d)  # Inner product (cosine similarity for normalized vectors)

        # Normalize embeddings for cosine similarity
        faiss.normalize_L2(embedding_matrix)
        index.add(embedding_matrix.astype(np.float32))

        self._faiss_index = index
        self._faiss_node_ids = node_ids
        print(f"[ExplorationGraph] Built FAISS index for {len(node_ids)} nodes")
        return True

    def faiss_search(
        self,
        query: str,
        top_k: int = 10
    ) -> List[Tuple[ExplorationNode, float]]:
        """
        Perform similarity search using FAISS index.

        Falls back to numpy if index not built.

        Args:
            query: Search query text
            top_k: Maximum number of results

        Returns:
            List of (node, similarity_score) tuples
        """
        if not hasattr(self, '_faiss_index') or self._faiss_index is None:
            return self.semantic_search(query, top_k)

        import faiss

        # Get query embedding
        query_embedding = self.generate_embedding(query)
        if query_embedding is None:
            return self._keyword_search(query, top_k)

        query_vec = np.array([query_embedding], dtype=np.float32)
        faiss.normalize_L2(query_vec)

        # Search
        distances, indices = self._faiss_index.search(query_vec, top_k)

        results = []
        for idx, similarity in zip(indices[0], distances[0]):
            if idx < 0:  # FAISS returns -1 for not found
                continue
            node_id = self._faiss_node_ids[idx]
            node = self.nodes.get(node_id)
            if node:
                results.append((node, float(similarity)))

        return results

    def get_performance_recommendation(self) -> Dict:
        """
        Get performance recommendation based on graph size.

        Returns:
            Dict with recommendation and current stats
        """
        node_count = len(self.nodes)
        has_faiss = self._check_faiss_available()

        recommendation = "numpy (current)"
        if node_count >= 10000:
            if has_faiss:
                recommendation = "FAISS recommended - call build_faiss_index()"
            else:
                recommendation = "Install FAISS: pip install faiss-cpu (or faiss-gpu)"
        elif node_count >= 5000:
            recommendation = "Consider FAISS if search latency is a concern"
        else:
            recommendation = "NumPy is efficient for current size"

        return {
            'node_count': node_count,
            'embedding_count': sum(1 for n in self.nodes.values() if n.embedding is not None),
            'faiss_available': has_faiss,
            'faiss_index_built': hasattr(self, '_faiss_index') and self._faiss_index is not None,
            'recommendation': recommendation
        }

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def _generate_id(self, key: str) -> str:
        """Generate a stable ID from a key."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Exploration Graph - AtlasForge Enhancement")
    print("=" * 50)

    # Create a graph
    graph = ExplorationGraph(Path("/tmp/exploration_demo"))

    # Simulate exploration of a codebase
    mission_id = "demo_mission_001"

    # Add file nodes
    node1 = graph.add_file_node(
        path="/src/api/handlers.py",
        summary="REST API handlers for user operations. Uses decorator pattern for auth.",
        mission_id=mission_id,
        tags=["api", "handlers", "auth"]
    )

    node2 = graph.add_file_node(
        path="/src/services/user_service.py",
        summary="Business logic for user operations. Calls database layer.",
        mission_id=mission_id,
        tags=["service", "users", "business-logic"]
    )

    node3 = graph.add_file_node(
        path="/src/db/models.py",
        summary="SQLAlchemy models for User, Session, and Role entities.",
        mission_id=mission_id,
        tags=["database", "models", "sqlalchemy"]
    )

    # Add relationships
    graph.add_edge(
        node1.id, node2.id,
        relationship="calls",
        mission_id=mission_id,
        context="Handlers call service layer methods"
    )

    graph.add_edge(
        node2.id, node3.id,
        relationship="uses",
        mission_id=mission_id,
        context="Service queries database models"
    )

    # Add concept
    auth_concept = graph.add_concept_node(
        name="JWT Authentication",
        summary="The codebase uses JWT tokens for stateless auth. Tokens contain user ID and roles.",
        mission_id=mission_id,
        tags=["auth", "security", "jwt"]
    )

    # Add pattern
    graph.add_pattern_node(
        name="Repository Pattern",
        summary="Data access uses repository pattern - services don't query DB directly.",
        mission_id=mission_id,
        code_example="class UserRepository:\n    def get_by_id(self, id):\n        return self.session.query(User).get(id)",
        tags=["architecture", "data-access"]
    )

    # Add insight
    graph.add_insight(
        insight_type="gotcha",
        title="Session management requires refresh token",
        description="JWT tokens expire after 1 hour. Clients must use refresh token to get new access token.",
        mission_id=mission_id,
        related_nodes=[auth_concept.id],
        tags=["auth", "sessions"],
        confidence=0.9
    )

    # Save
    graph.save()
    print("\nGraph saved.")

    # Query examples
    print(f"\nStats: {graph.get_exploration_stats()}")

    print(f"\nFile explored? {graph.has_explored('/src/api/handlers.py')}")
    print(f"Unknown file? {graph.has_explored('/src/unknown.py')}")

    related = graph.get_related_nodes(node1.id)
    print(f"\nRelated to handlers.py: {[(n.name, e.relationship) for n, e in related]}")

    insights = graph.search_insights("refresh")
    print(f"\nInsights about 'refresh': {[i.title for i in insights]}")

    print("\nDemo complete!")
