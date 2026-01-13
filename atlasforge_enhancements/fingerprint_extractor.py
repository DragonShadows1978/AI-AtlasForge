#!/usr/bin/env python3
"""
Fingerprint Extractor - AtlasForge Enhancement Feature 1.1

Extracts concept/pattern fingerprints from Claude's outputs during missions.
Targets mission-relevant concepts (problem domain terms, architectural decisions,
solution patterns) rather than phenomenology-focused fingerprinting.

Inspired by identity remerge concepts from RCFT exploration research.
"""

import re
import math
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class ConceptFingerprint:
    """
    A fingerprint representing concept frequencies in mission outputs.

    The fingerprint uses ratio-based tracking (inspired by RCFT theory),
    which preserves identity patterns even as absolute counts change.
    """
    source: str  # Mission ID or phase identifier
    timestamp: str
    concept_frequencies: Dict[str, int]  # Raw counts
    concept_ratios: Dict[str, float]  # Normalized to sum to 1.0
    domain_concepts: Dict[str, int]  # Domain-specific terms
    architectural_concepts: Dict[str, int]  # Architectural patterns
    action_concepts: Dict[str, int]  # Actions/verbs indicating work type
    meta_concepts: Dict[str, int]  # Meta-cognitive terms (planning, thinking, etc.)
    total_concepts: int

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ConceptFingerprint':
        return cls(**data)


# =============================================================================
# CONCEPT CATEGORIES
# =============================================================================

# Architectural concepts - indicate solution patterns
ARCHITECTURAL_CONCEPTS = {
    'module', 'function', 'class', 'method', 'interface', 'api', 'endpoint',
    'database', 'cache', 'queue', 'service', 'component', 'layer', 'pattern',
    'model', 'controller', 'view', 'handler', 'middleware', 'router',
    'config', 'schema', 'factory', 'singleton', 'observer', 'decorator',
    'state', 'reducer', 'action', 'store', 'context', 'provider',
    'graph', 'tree', 'list', 'map', 'set', 'array', 'struct',
    'async', 'await', 'promise', 'callback', 'stream', 'pipeline',
    'import', 'export', 'dependency', 'inject', 'container'
}

# Action concepts - indicate what kind of work is being done
ACTION_CONCEPTS = {
    'create', 'build', 'implement', 'develop', 'design', 'architect',
    'refactor', 'optimize', 'fix', 'debug', 'test', 'verify', 'validate',
    'read', 'write', 'parse', 'serialize', 'encode', 'decode', 'transform',
    'search', 'find', 'query', 'filter', 'sort', 'aggregate', 'reduce',
    'connect', 'disconnect', 'initialize', 'configure', 'setup', 'deploy',
    'analyze', 'measure', 'profile', 'monitor', 'log', 'trace',
    'extract', 'inject', 'merge', 'split', 'combine', 'separate'
}

# Meta-cognitive concepts - indicate reasoning patterns
META_CONCEPTS = {
    'plan', 'strategy', 'approach', 'method', 'technique', 'solution',
    'problem', 'issue', 'challenge', 'constraint', 'requirement',
    'assumption', 'hypothesis', 'theory', 'principle', 'rule',
    'pattern', 'trend', 'insight', 'observation', 'discovery',
    'decision', 'choice', 'tradeoff', 'consideration', 'factor',
    'goal', 'objective', 'target', 'milestone', 'deliverable',
    'risk', 'mitigation', 'fallback', 'alternative', 'option',
    'understand', 'comprehend', 'interpret', 'explain', 'clarify',
    'explore', 'investigate', 'examine', 'evaluate', 'assess'
}

# Stop words to filter out (common words that don't carry semantic weight)
STOP_WORDS = {
    'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for',
    'of', 'with', 'by', 'from', 'as', 'is', 'was', 'are', 'were', 'been',
    'be', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would',
    'could', 'should', 'may', 'might', 'must', 'shall', 'can', 'need',
    'this', 'that', 'these', 'those', 'it', 'its', 'he', 'she', 'they',
    'we', 'you', 'i', 'me', 'my', 'your', 'his', 'her', 'their', 'our',
    'what', 'which', 'who', 'whom', 'when', 'where', 'why', 'how',
    'not', 'no', 'yes', 'if', 'then', 'else', 'so', 'just', 'only',
    'very', 'too', 'also', 'more', 'most', 'some', 'any', 'all', 'each',
    'few', 'many', 'much', 'less', 'least', 'other', 'another', 'such'
}


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

def tokenize(text: str) -> List[str]:
    """
    Tokenize text into lowercase words, filtering punctuation.
    """
    # Convert to lowercase and split on non-alphanumeric
    words = re.findall(r'\b[a-z][a-z0-9_]*\b', text.lower())
    # Filter very short words and stop words
    return [w for w in words if len(w) > 2 and w not in STOP_WORDS]


def extract_domain_concepts(text: str, domain_terms: Optional[Set[str]] = None) -> Dict[str, int]:
    """
    Extract domain-specific concepts from text.

    Args:
        text: The text to analyze
        domain_terms: Optional set of domain-specific terms to look for.
                      If None, attempts to identify them from context.

    Returns:
        Dictionary of domain concept frequencies
    """
    tokens = tokenize(text)

    if domain_terms:
        # Count occurrences of specified domain terms
        domain_counts = Counter(t for t in tokens if t in domain_terms)
    else:
        # Auto-detect: terms that appear multiple times but aren't in predefined categories
        all_counts = Counter(tokens)
        domain_counts = Counter({
            term: count for term, count in all_counts.items()
            if count >= 2  # Appears at least twice
            and term not in ARCHITECTURAL_CONCEPTS
            and term not in ACTION_CONCEPTS
            and term not in META_CONCEPTS
        })

    return dict(domain_counts)


def extract_architectural_concepts(text: str) -> Dict[str, int]:
    """Extract architectural pattern concepts from text."""
    tokens = tokenize(text)
    return dict(Counter(t for t in tokens if t in ARCHITECTURAL_CONCEPTS))


def extract_action_concepts(text: str) -> Dict[str, int]:
    """Extract action/verb concepts indicating work type."""
    tokens = tokenize(text)
    return dict(Counter(t for t in tokens if t in ACTION_CONCEPTS))


def extract_meta_concepts(text: str) -> Dict[str, int]:
    """Extract meta-cognitive concepts indicating reasoning patterns."""
    tokens = tokenize(text)
    return dict(Counter(t for t in tokens if t in META_CONCEPTS))


def compute_ratios(frequencies: Dict[str, int]) -> Dict[str, float]:
    """
    Normalize frequencies to ratios that sum to 1.0.

    This ratio-based representation is key to fingerprint stability -
    the pattern persists even as absolute counts scale up or down.
    """
    total = sum(frequencies.values())
    if total == 0:
        return {}
    return {k: v / total for k, v in frequencies.items()}


def extract_fingerprint(
    text: str,
    source: str = "unknown",
    domain_terms: Optional[Set[str]] = None
) -> ConceptFingerprint:
    """
    Extract a complete concept fingerprint from text.

    Args:
        text: The text to analyze (Claude's response, plan, etc.)
        source: Identifier for the source (mission ID, phase, etc.)
        domain_terms: Optional set of domain-specific terms

    Returns:
        ConceptFingerprint with categorized concept frequencies and ratios
    """
    tokens = tokenize(text)
    all_frequencies = dict(Counter(tokens))

    # Extract by category
    domain = extract_domain_concepts(text, domain_terms)
    architectural = extract_architectural_concepts(text)
    action = extract_action_concepts(text)
    meta = extract_meta_concepts(text)

    # Compute overall ratios (from all significant concepts)
    significant = {**domain, **architectural, **action, **meta}
    ratios = compute_ratios(significant)

    return ConceptFingerprint(
        source=source,
        timestamp=datetime.now().isoformat(),
        concept_frequencies=all_frequencies,
        concept_ratios=ratios,
        domain_concepts=domain,
        architectural_concepts=architectural,
        action_concepts=action,
        meta_concepts=meta,
        total_concepts=len(tokens)
    )


# =============================================================================
# COMPARISON FUNCTIONS
# =============================================================================

def cosine_similarity(fp_a: Dict[str, float], fp_b: Dict[str, float]) -> float:
    """
    Calculate cosine similarity between two fingerprint ratio dicts.

    Returns value between 0.0 (orthogonal) and 1.0 (identical).
    """
    all_concepts = set(fp_a.keys()) | set(fp_b.keys())

    dot_product = 0.0
    norm_a = 0.0
    norm_b = 0.0

    for concept in all_concepts:
        val_a = fp_a.get(concept, 0.0)
        val_b = fp_b.get(concept, 0.0)

        dot_product += val_a * val_b
        norm_a += val_a * val_a
        norm_b += val_b * val_b

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return dot_product / (math.sqrt(norm_a) * math.sqrt(norm_b))


# =============================================================================
# EMBEDDING-BASED COMPARISON
# =============================================================================

class FingerprintEmbedding:
    """
    Embedding-based fingerprint comparison using sentence transformers.

    Provides more robust drift detection than TF-IDF for paraphrased content.
    Uses lazy loading to avoid startup overhead.
    """
    _model = None
    _initialized = False

    @classmethod
    def _get_model(cls):
        """Get or initialize the embedding model (lazy loading)."""
        if not cls._initialized:
            try:
                import torch
                from sentence_transformers import SentenceTransformer

                device = 'cuda' if torch.cuda.is_available() else 'cpu'
                cls._model = SentenceTransformer('all-MiniLM-L6-v2', device=device)
                cls._initialized = True
            except ImportError:
                cls._initialized = True  # Mark as initialized to avoid retrying
        return cls._model

    @classmethod
    def is_available(cls) -> bool:
        """Check if embedding model is available."""
        return cls._get_model() is not None

    @classmethod
    def encode(cls, text: str) -> Optional[List[float]]:
        """Encode text to embedding."""
        model = cls._get_model()
        if model is None:
            return None
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()


def fingerprint_to_text(fp: ConceptFingerprint) -> str:
    """
    Convert a fingerprint to a text representation for embedding.

    Combines key concepts into a coherent text.
    """
    parts = []

    # Add top domain concepts
    if fp.domain_concepts:
        top_domain = sorted(fp.domain_concepts.items(), key=lambda x: -x[1])[:10]
        parts.append("Domain: " + ", ".join(c for c, _ in top_domain))

    # Add top architectural concepts
    if fp.architectural_concepts:
        top_arch = sorted(fp.architectural_concepts.items(), key=lambda x: -x[1])[:10]
        parts.append("Architecture: " + ", ".join(c for c, _ in top_arch))

    # Add top action concepts
    if fp.action_concepts:
        top_action = sorted(fp.action_concepts.items(), key=lambda x: -x[1])[:10]
        parts.append("Actions: " + ", ".join(c for c, _ in top_action))

    # Add top meta concepts
    if fp.meta_concepts:
        top_meta = sorted(fp.meta_concepts.items(), key=lambda x: -x[1])[:10]
        parts.append("Meta: " + ", ".join(c for c, _ in top_meta))

    return " ".join(parts)


def embedding_similarity(
    fp_a: ConceptFingerprint,
    fp_b: ConceptFingerprint
) -> Optional[float]:
    """
    Calculate embedding-based similarity between two fingerprints.

    Uses sentence transformer embeddings for more robust semantic matching.

    Args:
        fp_a: First fingerprint
        fp_b: Second fingerprint

    Returns:
        Similarity score (0.0-1.0) or None if embeddings unavailable
    """
    if not FingerprintEmbedding.is_available():
        return None

    # Convert fingerprints to text
    text_a = fingerprint_to_text(fp_a)
    text_b = fingerprint_to_text(fp_b)

    # Get embeddings
    emb_a = FingerprintEmbedding.encode(text_a)
    emb_b = FingerprintEmbedding.encode(text_b)

    if emb_a is None or emb_b is None:
        return None

    # Compute cosine similarity
    import numpy as np
    vec_a = np.array(emb_a)
    vec_b = np.array(emb_b)

    dot = np.dot(vec_a, vec_b)
    norm_a = np.linalg.norm(vec_a)
    norm_b = np.linalg.norm(vec_b)

    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot / (norm_a * norm_b))


def measure_drift(
    baseline: ConceptFingerprint,
    current: ConceptFingerprint,
    use_embeddings: bool = True
) -> Dict[str, any]:
    """
    Measure how much the current fingerprint has drifted from baseline.

    Uses hybrid approach combining TF-IDF and embedding similarity when available.

    Returns detailed drift analysis including:
    - Overall similarity (hybrid of TF-IDF and embeddings)
    - Category-specific drift
    - Top concepts that changed
    - Embedding similarity (if available)

    Args:
        baseline: Baseline fingerprint to compare against
        current: Current fingerprint
        use_embeddings: If True, use embedding similarity when available
    """
    # TF-IDF based similarity
    tfidf_sim = cosine_similarity(baseline.concept_ratios, current.concept_ratios)

    # Try embedding-based similarity
    emb_sim = None
    if use_embeddings:
        emb_sim = embedding_similarity(baseline, current)

    # Hybrid similarity: prefer embeddings but fall back to TF-IDF
    if emb_sim is not None:
        # Weighted combination: embeddings are better for paraphrases
        overall_sim = (emb_sim * 0.6) + (tfidf_sim * 0.4)
    else:
        overall_sim = tfidf_sim

    # Category-specific similarities (TF-IDF based)
    arch_sim = cosine_similarity(
        compute_ratios(baseline.architectural_concepts),
        compute_ratios(current.architectural_concepts)
    )
    action_sim = cosine_similarity(
        compute_ratios(baseline.action_concepts),
        compute_ratios(current.action_concepts)
    )
    meta_sim = cosine_similarity(
        compute_ratios(baseline.meta_concepts),
        compute_ratios(current.meta_concepts)
    )

    # Find concepts with biggest changes
    all_concepts = set(baseline.concept_ratios.keys()) | set(current.concept_ratios.keys())
    changes = []
    for concept in all_concepts:
        baseline_ratio = baseline.concept_ratios.get(concept, 0.0)
        current_ratio = current.concept_ratios.get(concept, 0.0)
        change = current_ratio - baseline_ratio
        if abs(change) > 0.01:  # Only significant changes
            changes.append({
                'concept': concept,
                'baseline_ratio': round(baseline_ratio, 4),
                'current_ratio': round(current_ratio, 4),
                'change': round(change, 4),
                'direction': 'increased' if change > 0 else 'decreased'
            })

    # Sort by absolute change
    changes.sort(key=lambda x: abs(x['change']), reverse=True)

    # Determine drift severity
    if overall_sim >= 0.95:
        severity = "MINIMAL"
        alert = "GREEN"
    elif overall_sim >= 0.85:
        severity = "LOW"
        alert = "GREEN"
    elif overall_sim >= 0.75:
        severity = "MODERATE"
        alert = "YELLOW"
    elif overall_sim >= 0.65:
        severity = "SIGNIFICANT"
        alert = "ORANGE"
    else:
        severity = "CRITICAL"
        alert = "RED"

    result = {
        'overall_similarity': round(overall_sim, 4),
        'tfidf_similarity': round(tfidf_sim, 4),
        'category_similarities': {
            'architectural': round(arch_sim, 4),
            'action': round(action_sim, 4),
            'meta': round(meta_sim, 4)
        },
        'drift_severity': severity,
        'alert_level': alert,
        'top_changes': changes[:10],  # Top 10 changes
        'baseline_source': baseline.source,
        'current_source': current.source,
        'comparison_timestamp': datetime.now().isoformat(),
        'comparison_method': 'hybrid' if emb_sim is not None else 'tfidf'
    }

    # Include embedding similarity if available
    if emb_sim is not None:
        result['embedding_similarity'] = round(emb_sim, 4)

    return result


# =============================================================================
# PERSISTENCE
# =============================================================================

def save_fingerprint(fingerprint: ConceptFingerprint, filepath: Path):
    """Save fingerprint to JSON file."""
    with open(filepath, 'w') as f:
        json.dump(fingerprint.to_dict(), f, indent=2)


def load_fingerprint(filepath: Path) -> ConceptFingerprint:
    """Load fingerprint from JSON file."""
    with open(filepath, 'r') as f:
        data = json.load(f)
    return ConceptFingerprint.from_dict(data)


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Fingerprint Extractor - AtlasForge Enhancement")
    print("=" * 50)

    # Example: Extract fingerprint from a sample mission response
    sample_response = """
    I'll implement a caching layer for the API using Redis. The approach involves:

    1. Create a cache module with connection pooling
    2. Implement a decorator pattern for automatic cache invalidation
    3. Add configuration options for TTL and cache key generation

    The architecture will use a middleware component to intercept requests
    before they hit the database. This should optimize query performance
    significantly.

    My plan is to:
    - Build the cache service interface first
    - Then implement the Redis adapter
    - Finally integrate with the existing API layer

    This strategy minimizes risk while ensuring we can validate at each step.
    """

    print("\nExtracting fingerprint from sample response...")
    fp = extract_fingerprint(sample_response, source="sample_mission")

    print(f"\nFingerprint Summary:")
    print(f"  Source: {fp.source}")
    print(f"  Total concepts: {fp.total_concepts}")
    print(f"  Architectural concepts: {len(fp.architectural_concepts)}")
    print(f"  Action concepts: {len(fp.action_concepts)}")
    print(f"  Meta concepts: {len(fp.meta_concepts)}")
    print(f"  Domain concepts: {len(fp.domain_concepts)}")

    print(f"\nTop architectural concepts:")
    for concept, count in sorted(fp.architectural_concepts.items(), key=lambda x: -x[1])[:5]:
        print(f"    {concept}: {count}")

    print(f"\nTop action concepts:")
    for concept, count in sorted(fp.action_concepts.items(), key=lambda x: -x[1])[:5]:
        print(f"    {concept}: {count}")

    print(f"\nTop meta concepts:")
    for concept, count in sorted(fp.meta_concepts.items(), key=lambda x: -x[1])[:5]:
        print(f"    {concept}: {count}")

    print("\nFingerprint extraction complete!")
