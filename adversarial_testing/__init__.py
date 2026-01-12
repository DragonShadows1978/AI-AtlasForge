"""
Adversarial Testing Framework for Epistemic Rigor.

This module provides tools to break the "painter who loves their own work" problem
by spawning independent agents to adversarially test code, generate property-based
tests, perform mutation analysis, and validate implementations against original specs.

Key Components:
    - AdversarialRunner: Main orchestrator for adversarial testing
    - EnhancedAdversarialRunner: Production-ready runner with cost estimation & resilience
    - RedTeamAgent: Spawns fresh Claude instances to break code
    - PropertyTesting: Generates edge cases via property-based testing
    - MutationTesting: Verifies test quality through code mutation
    - BlindValidator: Independent validation against original specification
    - EpistemicMetrics: Scoring and metrics for epistemic rigor
    - CostEstimator: Estimate API costs before running
    - VulnerabilityDatabase: Persistent storage for discovered patterns
    - ResilientRunner: Error handling with retries and graceful degradation

Usage:
    # Simple usage
    from adversarial_testing import AdversarialRunner

    runner = AdversarialRunner(mission_id="my_mission")
    results = runner.run_full_suite(
        code_path=Path("/path/to/code.py"),
        specification="The original requirements..."
    )

    # Enhanced usage with cost estimation
    from adversarial_testing import (
        EnhancedAdversarialRunner,
        AdversarialMode,
        run_adversarial_with_estimation
    )

    # Get cost estimate first
    runner = EnhancedAdversarialRunner(mode=AdversarialMode.STANDARD)
    estimate = runner.estimate_cost(code_path=Path("code.py"))
    print(f"Estimated cost: ${estimate.total_estimated_cost:.4f}")

    # Run with budget tracking
    results = runner.run(
        code_path=Path("code.py"),
        specification="Requirements...",
        progress_callback=print
    )
"""

# Core components
from .adversarial_runner import (
    AdversarialRunner,
    AdversarialConfig,
    AdversarialResults,
    run_adversarial_testing
)
from .red_team_agent import RedTeamAgent, RedTeamResult, AttackCategory
from .property_testing import PropertyTester, PropertyTestResult, PropertyType
from .mutation_testing import MutationTester, MutationResult, MutationScore
from .blind_validator import BlindValidator, ValidationResult, ValidationStatus
from .epistemic_metrics import (
    EpistemicMetrics,
    EpistemicScore,
    TestSuiteReport,
    RigorLevel,
    calculate_mutation_score,
    calculate_adversarial_score,
    calculate_spec_alignment
)

# Cycle 2 additions
from .cost_estimator import (
    CostEstimator,
    CostEstimate,
    BudgetTracker,
    CostTier,
    ModelPricing
)
from .vulnerability_database import (
    VulnerabilityDatabase,
    VulnerabilityPattern,
    VulnerabilityCategory,
    record_red_team_findings
)
from .resilience import (
    ResilientRunner,
    ProgressTracker,
    ProgressReport,
    RetryConfig,
    ErrorType,
    with_retry
)
from .enhanced_runner import (
    EnhancedAdversarialRunner,
    EnhancedConfig,
    EnhancedResults,
    AdversarialMode,
    run_adversarial_with_estimation
)

# Mission drift validation
from .mission_drift_validator import (
    MissionDriftValidator,
    MissionDriftResult,
    DriftTrackingState,
    DriftSeverity,
    DriftDecision,
    validate_mission_continuation,
    load_tracking_state,
    save_tracking_state,
    save_validation_result
)

# Phase-aware drift detection (Cycle 3 enhancement)
from .phase_aware_drift import (
    PhaseStatus,
    MissionPhase,
    PhaseTrackingState,
    PhaseExtractor,
    PhaseCompletionDetector,
    PhaseAwareComparator,
    AccompanyingDocsDiscovery,
    load_phase_state,
    save_phase_state,
    initialize_phase_tracking
)

from .phase_aware_validator import (
    PhaseAwareMissionDriftValidator,
    PhaseAwareDriftResult,
    validate_continuation_phase_aware
)

# Content preservation validation (new)
from .content_preservation import (
    ContentPreservationTester,
    ContentIntegrityResult,
    PreservationViolation,
    ContentPreservationType,
    test_content_preservation,
    validate_merge_preserves_content,
    validate_transform_preserves_content
)

__all__ = [
    # Core
    'AdversarialRunner',
    'AdversarialConfig',
    'AdversarialResults',
    'run_adversarial_testing',

    # Red team
    'RedTeamAgent',
    'RedTeamResult',
    'AttackCategory',

    # Property testing
    'PropertyTester',
    'PropertyTestResult',
    'PropertyType',

    # Mutation testing
    'MutationTester',
    'MutationResult',
    'MutationScore',

    # Blind validation
    'BlindValidator',
    'ValidationResult',
    'ValidationStatus',

    # Epistemic metrics
    'EpistemicMetrics',
    'EpistemicScore',
    'TestSuiteReport',
    'RigorLevel',
    'calculate_mutation_score',
    'calculate_adversarial_score',
    'calculate_spec_alignment',

    # Cost estimation (Cycle 2)
    'CostEstimator',
    'CostEstimate',
    'BudgetTracker',
    'CostTier',
    'ModelPricing',

    # Vulnerability database (Cycle 2)
    'VulnerabilityDatabase',
    'VulnerabilityPattern',
    'VulnerabilityCategory',
    'record_red_team_findings',

    # Resilience (Cycle 2)
    'ResilientRunner',
    'ProgressTracker',
    'ProgressReport',
    'RetryConfig',
    'ErrorType',
    'with_retry',

    # Enhanced runner (Cycle 2)
    'EnhancedAdversarialRunner',
    'EnhancedConfig',
    'EnhancedResults',
    'AdversarialMode',
    'run_adversarial_with_estimation',

    # Mission drift validation
    'MissionDriftValidator',
    'MissionDriftResult',
    'DriftTrackingState',
    'DriftSeverity',
    'DriftDecision',
    'validate_mission_continuation',
    'load_tracking_state',
    'save_tracking_state',
    'save_validation_result',

    # Phase-aware drift detection (Cycle 3)
    'PhaseStatus',
    'MissionPhase',
    'PhaseTrackingState',
    'PhaseExtractor',
    'PhaseCompletionDetector',
    'PhaseAwareComparator',
    'AccompanyingDocsDiscovery',
    'load_phase_state',
    'save_phase_state',
    'initialize_phase_tracking',
    'PhaseAwareMissionDriftValidator',
    'PhaseAwareDriftResult',
    'validate_continuation_phase_aware',

    # Content preservation (new)
    'ContentPreservationTester',
    'ContentIntegrityResult',
    'PreservationViolation',
    'ContentPreservationType',
    'test_content_preservation',
    'validate_merge_preserves_content',
    'validate_transform_preserves_content',
]
