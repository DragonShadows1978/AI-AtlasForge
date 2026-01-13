"""
AtlasForge Enhancements Package

Three features to help Claude Code explore and build more effectively:

1. Cognitive Fingerprint Tracker - Mission continuity across cycles
2. Exploration Memory Graph - Remember what was explored
3. Self-Calibrating Prompt Scaffolding - Reduce cognitive biases

Usage:
    from atlasforge_enhancements import AtlasForgeEnhancer

    enhancer = AtlasForgeEnhancer(mission_id="my_mission")

    # Track mission continuity
    enhancer.set_mission_baseline(mission_statement)
    report = enhancer.check_continuity(current_output)

    # Remember explorations
    enhancer.record_exploration(path, summary)
    prior = enhancer.what_do_we_know("authentication")

    # Apply scaffolding
    prompt = enhancer.scaffold_prompt(prompt, previous_response)
"""

from .fingerprint_extractor import (
    ConceptFingerprint,
    extract_fingerprint,
    cosine_similarity,
    measure_drift,
    embedding_similarity,
    FingerprintEmbedding,
    fingerprint_to_text
)

from .mission_continuity_tracker import (
    MissionContinuityTracker,
    CycleCheckpoint,
    ContinuityReport,
    create_tracker_for_mission
)

from .context_healing import (
    generate_healing_prompt,
    HealingStrategy,
    HEALING_STRATEGIES
)

from .exploration_graph import (
    ExplorationGraph,
    ExplorationNode,
    ExplorationEdge,
    ExplorationInsight
)

from .insight_extractor import (
    extract_from_text,
    ExtractionResult,
    ExplorationAdvisor,
    populate_graph_from_extraction
)

from .bias_detector import (
    BiasType,
    BiasDetection,
    analyze_response,
    detect_bias_patterns
)

from .scaffold_library import (
    Scaffold,
    ScaffoldIntensity,
    ALL_SCAFFOLDS,
    get_scaffolds_for_bias,
    apply_scaffold
)

from .scaffold_calibrator import (
    ScaffoldCalibrator,
    auto_scaffold,
    quick_bias_check
)

from .atlasforge_enhancer import AtlasForgeEnhancer

from .knowledge_transfer import (
    KnowledgeTransfer,
    PriorMissionInfo,
    KnowledgeSearchResult,
    StartingPointSuggestion
)

from .exploration_graph import EmbeddingModel

__all__ = [
    # Main interface
    'AtlasForgeEnhancer',

    # Feature 1: Fingerprinting
    'ConceptFingerprint',
    'extract_fingerprint',
    'cosine_similarity',
    'measure_drift',
    'embedding_similarity',
    'FingerprintEmbedding',
    'fingerprint_to_text',
    'MissionContinuityTracker',
    'CycleCheckpoint',
    'ContinuityReport',
    'create_tracker_for_mission',
    'generate_healing_prompt',
    'HealingStrategy',
    'HEALING_STRATEGIES',

    # Feature 2: Exploration Memory
    'ExplorationGraph',
    'ExplorationNode',
    'ExplorationEdge',
    'ExplorationInsight',
    'extract_from_text',
    'ExtractionResult',
    'ExplorationAdvisor',
    'populate_graph_from_extraction',
    'EmbeddingModel',

    # Feature 3: Scaffolding
    'BiasType',
    'BiasDetection',
    'analyze_response',
    'detect_bias_patterns',
    'Scaffold',
    'ScaffoldIntensity',
    'ALL_SCAFFOLDS',
    'get_scaffolds_for_bias',
    'apply_scaffold',
    'ScaffoldCalibrator',
    'auto_scaffold',
    'quick_bias_check',

    # Feature 4: Knowledge Transfer
    'KnowledgeTransfer',
    'PriorMissionInfo',
    'KnowledgeSearchResult',
    'StartingPointSuggestion',
]
