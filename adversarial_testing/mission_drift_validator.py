#!/usr/bin/env python3
"""
Mission Drift Validator - Prevents scope creep in multi-cycle missions.

The key insight: In multi-cycle missions, Claude autonomously generates continuation
prompts at CYCLE_END. Without validation, these prompts can drift from the original
mission specification, leading to compounding scope creep.

This validator implements a graduated intervention strategy:
- Failures 1-3: Log and allow (collect data)
- Failure 4: Inject critical warning into continuation
- Failure 5: Halt mission and generate drift recap

The validator uses LLM-as-judge evaluation with a fresh Claude instance that has
zero knowledge of the implementation details, ensuring unbiased assessment.
"""

import sys
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime
from enum import Enum

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_claude, ModelType


class DriftSeverity(Enum):
    """Severity levels for mission drift."""
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DriftDecision(Enum):
    """Decisions based on drift detection."""
    ALLOW = "allow"             # Continue normally
    LOG_WARNING = "log_warning" # Log but continue
    INJECT_WARNING = "inject_warning"  # Prepend warning to continuation
    HALT = "halt"               # Stop mission, generate report


@dataclass
class MissionDriftResult:
    """Result of mission drift validation."""
    validation_id: str
    cycle_number: int
    original_mission_hash: str
    continuation_hash: str
    drift_detected: bool
    drift_severity: DriftSeverity
    semantic_similarity: float  # 0.0-1.0
    requirement_coverage: Dict[str, bool]  # {requirement: covered}
    added_scope: List[str]  # New objectives not in original
    lost_focus: List[str]   # Original objectives deprioritized
    evaluator_reasoning: str
    confidence: float  # 0.0-1.0
    decision: DriftDecision
    timestamp: str
    duration_ms: float
    raw_response: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d['drift_severity'] = self.drift_severity.value
        d['decision'] = self.decision.value
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'MissionDriftResult':
        data['drift_severity'] = DriftSeverity(data['drift_severity'])
        data['decision'] = DriftDecision(data['decision'])
        return cls(**data)


@dataclass
class DriftTrackingState:
    """Persistent state for tracking drift across cycles.

    Extended in Cycle 2 to include velocity tracking:
    - similarity_history: List of similarity scores over cycles
    - similarity_velocities: Rate of change in similarity between cycles
    - velocity_acceleration: Rate of change of velocity (drift acceleration)
    - dynamic_threshold_adjustment: Tolerance adjustment based on velocity

    Extended in Cycle 3 to include phase awareness:
    - phase_aware_mode: Whether phase-aware validation is enabled
    - active_phase_name: Name of the currently active phase
    - completed_phases: List of completed phase names
    - phase_transitions: History of phase transitions
    - false_positive_overrides: Count of false positive overrides applied
    """
    failure_count: int = 0
    consecutive_failures: int = 0
    failure_history: List[Dict] = field(default_factory=list)
    warning_issued: bool = False
    total_validations: int = 0
    average_similarity: float = 1.0
    last_validation_cycle: int = 0

    # Velocity tracking fields (Cycle 2 enhancement)
    similarity_history: List[float] = field(default_factory=list)
    similarity_velocities: List[float] = field(default_factory=list)
    velocity_acceleration: float = 0.0
    dynamic_threshold_adjustment: float = 0.0  # Negative = stricter, positive = more lenient

    # Phase awareness fields (Cycle 3 enhancement)
    phase_aware_mode: bool = True  # Enable phase-aware validation by default
    active_phase_name: Optional[str] = None
    completed_phases: List[str] = field(default_factory=list)
    phase_transitions: List[Dict] = field(default_factory=list)
    false_positive_overrides: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'DriftTrackingState':
        # Handle old data that doesn't have new fields
        allowed_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered_data = {k: v for k, v in data.items() if k in allowed_fields}
        return cls(**filtered_data)

    def update_velocity(self, new_similarity: float):
        """Update velocity tracking with a new similarity score.

        This computes:
        - Velocity: rate of change in similarity (delta between consecutive cycles)
        - Acceleration: rate of change of velocity (is drift speeding up?)
        - Dynamic threshold: lower tolerance if drift is accelerating

        Args:
            new_similarity: The latest semantic similarity score (0-1)
        """
        self.similarity_history.append(new_similarity)

        # Compute velocity (change from previous cycle)
        if len(self.similarity_history) >= 2:
            velocity = new_similarity - self.similarity_history[-2]
            self.similarity_velocities.append(velocity)

            # Compute acceleration (change in velocity)
            if len(self.similarity_velocities) >= 2:
                self.velocity_acceleration = (
                    self.similarity_velocities[-1] - self.similarity_velocities[-2]
                )

                # Dynamic threshold adjustment
                # If drift is accelerating (velocity becoming more negative), lower tolerance
                if self.velocity_acceleration < -0.05:
                    # Drift is accelerating - tighten thresholds
                    self.dynamic_threshold_adjustment = min(
                        self.dynamic_threshold_adjustment - 0.5,
                        -2.0  # Max adjustment: reduce tolerance by 2 failures
                    )
                elif self.velocity_acceleration > 0.05:
                    # Drift is decelerating - can relax slightly
                    self.dynamic_threshold_adjustment = min(
                        self.dynamic_threshold_adjustment + 0.25,
                        0.5  # Max relaxation: add 0.5 failures tolerance
                    )

    def get_effective_thresholds(self, base_warn: int = 4, base_halt: int = 5) -> tuple:
        """Get effective thresholds after velocity-based adjustment.

        Returns:
            Tuple of (warn_threshold, halt_threshold)
        """
        adjustment = int(self.dynamic_threshold_adjustment)
        warn = max(2, base_warn + adjustment)  # Never go below 2
        halt = max(3, base_halt + adjustment)  # Never go below 3
        return (warn, halt)

    def get_velocity_summary(self) -> Dict[str, Any]:
        """Get a summary of velocity metrics."""
        return {
            'current_similarity': self.similarity_history[-1] if self.similarity_history else None,
            'current_velocity': self.similarity_velocities[-1] if self.similarity_velocities else None,
            'acceleration': self.velocity_acceleration,
            'threshold_adjustment': self.dynamic_threshold_adjustment,
            'trend': self._determine_trend(),
            'samples': len(self.similarity_history)
        }

    def _determine_trend(self) -> str:
        """Determine the overall trend in drift."""
        if not self.similarity_velocities:
            return 'unknown'

        recent_velocities = self.similarity_velocities[-3:]
        avg_velocity = sum(recent_velocities) / len(recent_velocities)

        if avg_velocity < -0.1:
            return 'deteriorating'
        elif avg_velocity > 0.05:
            return 'improving'
        elif abs(avg_velocity) < 0.03:
            return 'stable'
        else:
            return 'drifting'


class MissionDriftValidator:
    """
    Validates continuation prompts against original mission specification.

    Uses a fresh Claude instance (LLM-as-judge) to assess whether the
    continuation prompt stays within the scope of the original mission.
    """

    # System prompt for the drift evaluator
    EVALUATOR_SYSTEM_PROMPT = """You are a mission drift evaluator. Your job is to assess whether a continuation prompt
stays true to the ORIGINAL mission specification.

You must be OBJECTIVE and STRICT. The continuation should only include work that is
directly related to accomplishing the original mission objectives.

Your evaluation criteria:
1. SCOPE ADHERENCE: Does the continuation stay within the original scope?
2. REQUIREMENT COVERAGE: Are the original requirements still being addressed?
3. SCOPE EXPANSION: Are new objectives being introduced that weren't in the original?
4. FOCUS LOSS: Are original objectives being deprioritized or abandoned?

Types of acceptable changes:
- Necessary technical adjustments discovered during implementation
- Better understanding leading to clearer sub-objectives
- Bug fixes and error handling for original requirements

Types of DRIFT (should be flagged):
- Adding features not in the original specification
- Expanding scope beyond original intent
- Pursuing tangential improvements
- Abandoning original objectives for new ones

Be strict but fair. Mission drift compounds across cycles."""

    VALIDATION_PROMPT_TEMPLATE = """Evaluate whether this continuation prompt stays true to the original mission.

## ORIGINAL MISSION SPECIFICATION:
{original_mission}

## CONTINUATION PROMPT (Cycle {cycle_number}):
{continuation_prompt}

## YOUR TASK:
1. Extract the key requirements/objectives from the original mission
2. Assess if the continuation addresses these requirements
3. Identify any NEW objectives not in the original (scope expansion)
4. Identify any ORIGINAL objectives being deprioritized (focus loss)
5. Assign a drift severity and provide reasoning

Respond in JSON format ONLY:
{{
    "original_requirements": ["requirement 1", "requirement 2", ...],
    "requirement_coverage": {{"requirement 1": true/false, "requirement 2": true/false, ...}},
    "added_scope": ["new objective 1", "new objective 2", ...],
    "lost_focus": ["deprioritized objective 1", ...],
    "drift_detected": true/false,
    "drift_severity": "none|low|medium|high|critical",
    "semantic_similarity": 0.0-1.0,
    "confidence": 0.0-1.0,
    "reasoning": "Detailed explanation of drift assessment"
}}

SEVERITY GUIDE:
- none: Continuation directly addresses original mission
- low: Minor additions that support the original mission
- medium: Some scope expansion but core mission maintained
- high: Significant scope expansion, original objectives partially deprioritized
- critical: Major deviation from original mission, original objectives largely abandoned"""

    def __init__(
        self,
        model: ModelType = ModelType.CLAUDE_SONNET,
        timeout_seconds: int = 120,
        failure_threshold_warn: int = 4,
        failure_threshold_halt: int = 5
    ):
        """
        Initialize the mission drift validator.

        Args:
            model: Model to use for evaluation
            timeout_seconds: Timeout for evaluation
            failure_threshold_warn: Number of failures before warning injection
            failure_threshold_halt: Number of failures before mission halt
        """
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.failure_threshold_warn = failure_threshold_warn
        self.failure_threshold_halt = failure_threshold_halt

    def validate_continuation(
        self,
        original_mission: str,
        continuation_prompt: str,
        cycle_number: int,
        tracking_state: Optional[DriftTrackingState] = None
    ) -> Tuple[MissionDriftResult, DriftTrackingState]:
        """
        Validate a continuation prompt against the original mission.

        Args:
            original_mission: The original mission specification
            continuation_prompt: The generated continuation prompt
            cycle_number: Current cycle number
            tracking_state: Optional existing tracking state

        Returns:
            Tuple of (validation result, updated tracking state)
        """
        import time

        start_time = time.time()

        # Initialize or use existing tracking state
        if tracking_state is None:
            tracking_state = DriftTrackingState()

        # Create hashes for traceability
        orig_hash = hashlib.md5(original_mission.encode()).hexdigest()[:12]
        cont_hash = hashlib.md5(continuation_prompt.encode()).hexdigest()[:12]
        validation_id = f"val_{orig_hash}_{cycle_number:03d}"

        # Build evaluation prompt
        prompt = self.VALIDATION_PROMPT_TEMPLATE.format(
            original_mission=original_mission,
            continuation_prompt=continuation_prompt,
            cycle_number=cycle_number
        )

        # Invoke fresh Claude instance for unbiased evaluation
        response, response_ms = invoke_fresh_claude(
            prompt=prompt,
            model=self.model,
            system_prompt=self.EVALUATOR_SYSTEM_PROMPT,
            timeout=self.timeout_seconds
        )

        duration_ms = (time.time() - start_time) * 1000

        # Parse the response
        result = self._parse_validation_response(
            response=response,
            validation_id=validation_id,
            cycle_number=cycle_number,
            orig_hash=orig_hash,
            cont_hash=cont_hash,
            duration_ms=duration_ms
        )

        # Update tracking state based on result
        tracking_state = self._update_tracking_state(
            tracking_state=tracking_state,
            result=result,
            cycle_number=cycle_number
        )

        # Determine decision based on tracking state
        result.decision = self._determine_decision(tracking_state, result)

        return result, tracking_state

    def _parse_validation_response(
        self,
        response: str,
        validation_id: str,
        cycle_number: int,
        orig_hash: str,
        cont_hash: str,
        duration_ms: float
    ) -> MissionDriftResult:
        """Parse the LLM evaluation response."""
        import re

        # Default result for error cases
        default_result = MissionDriftResult(
            validation_id=validation_id,
            cycle_number=cycle_number,
            original_mission_hash=orig_hash,
            continuation_hash=cont_hash,
            drift_detected=False,
            drift_severity=DriftSeverity.NONE,
            semantic_similarity=1.0,
            requirement_coverage={},
            added_scope=[],
            lost_focus=[],
            evaluator_reasoning="",
            confidence=0.0,
            decision=DriftDecision.ALLOW,
            timestamp=datetime.now().isoformat(),
            duration_ms=duration_ms,
            raw_response=response
        )

        if response.startswith("ERROR:"):
            default_result.evaluator_reasoning = f"Evaluation error: {response}"
            return default_result

        # Try to extract JSON from response
        try:
            parsed = self._extract_json(response)
            if not parsed:
                default_result.evaluator_reasoning = "Failed to parse JSON response"
                return default_result

            # Map severity string to enum
            severity_str = parsed.get("drift_severity", "none").lower()
            try:
                severity = DriftSeverity(severity_str)
            except ValueError:
                severity = DriftSeverity.NONE

            return MissionDriftResult(
                validation_id=validation_id,
                cycle_number=cycle_number,
                original_mission_hash=orig_hash,
                continuation_hash=cont_hash,
                drift_detected=parsed.get("drift_detected", False),
                drift_severity=severity,
                semantic_similarity=float(parsed.get("semantic_similarity", 1.0)),
                requirement_coverage=parsed.get("requirement_coverage", {}),
                added_scope=parsed.get("added_scope", []),
                lost_focus=parsed.get("lost_focus", []),
                evaluator_reasoning=parsed.get("reasoning", ""),
                confidence=float(parsed.get("confidence", 0.5)),
                decision=DriftDecision.ALLOW,  # Will be set later
                timestamp=datetime.now().isoformat(),
                duration_ms=duration_ms,
                raw_response=response
            )

        except Exception as e:
            default_result.evaluator_reasoning = f"Parse error: {str(e)}"
            return default_result

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        import re

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find raw JSON object (more permissive)
        json_match = re.search(r'\{[^{}]*"drift_detected"[^{}]*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
            except json.JSONDecodeError:
                pass

        # Try to find large JSON block
        start = text.find('{')
        if start >= 0:
            depth = 0
            for i, c in enumerate(text[start:], start):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i+1])
                        except json.JSONDecodeError:
                            break

        return None

    def _update_tracking_state(
        self,
        tracking_state: DriftTrackingState,
        result: MissionDriftResult,
        cycle_number: int
    ) -> DriftTrackingState:
        """Update tracking state based on validation result.

        Enhanced in Cycle 2 to include velocity tracking for drift acceleration detection.
        """
        tracking_state.total_validations += 1
        tracking_state.last_validation_cycle = cycle_number

        # Update running average similarity
        n = tracking_state.total_validations
        tracking_state.average_similarity = (
            (tracking_state.average_similarity * (n - 1) + result.semantic_similarity) / n
        )

        # Update velocity tracking (Cycle 2 enhancement)
        tracking_state.update_velocity(result.semantic_similarity)

        if result.drift_detected and result.drift_severity in (
            DriftSeverity.MEDIUM, DriftSeverity.HIGH, DriftSeverity.CRITICAL
        ):
            # Count as a failure
            tracking_state.failure_count += 1
            tracking_state.consecutive_failures += 1
            tracking_state.failure_history.append({
                "cycle": cycle_number,
                "severity": result.drift_severity.value,
                "similarity": result.semantic_similarity,
                "velocity": tracking_state.similarity_velocities[-1] if tracking_state.similarity_velocities else 0,
                "acceleration": tracking_state.velocity_acceleration,
                "added_scope": result.added_scope[:5],  # Limit size
                "lost_focus": result.lost_focus[:5],
                "timestamp": result.timestamp
            })
        else:
            # Not a failure - reset consecutive counter (but keep total)
            tracking_state.consecutive_failures = 0

        return tracking_state

    def _determine_decision(
        self,
        tracking_state: DriftTrackingState,
        result: MissionDriftResult
    ) -> DriftDecision:
        """Determine the appropriate decision based on tracking state.

        Enhanced in Cycle 2 to use velocity-adjusted dynamic thresholds.
        If drift is accelerating, thresholds are lowered automatically.
        """
        failures = tracking_state.failure_count

        # Get effective thresholds (may be adjusted based on velocity)
        effective_warn, effective_halt = tracking_state.get_effective_thresholds(
            self.failure_threshold_warn,
            self.failure_threshold_halt
        )

        if failures >= effective_halt:
            return DriftDecision.HALT
        elif failures >= effective_warn:
            tracking_state.warning_issued = True
            return DriftDecision.INJECT_WARNING
        elif result.drift_detected:
            return DriftDecision.LOG_WARNING
        else:
            return DriftDecision.ALLOW

    def generate_warning_message(
        self,
        result: MissionDriftResult,
        tracking_state: DriftTrackingState,
        original_mission: str
    ) -> str:
        """Generate a warning message to inject into continuation prompt."""
        failures = tracking_state.failure_count
        remaining = self.failure_threshold_halt - failures

        warning = f"""
{'='*60}
WARNING: MISSION DRIFT DETECTED (Attempt {failures}/{self.failure_threshold_halt})
{'='*60}

Mission drift has been detected in {failures} consecutive cycles.
Semantic similarity to original mission: {result.semantic_similarity:.1%}
Drift severity: {result.drift_severity.value.upper()}

ADDED SCOPE NOT IN ORIGINAL MISSION:
{chr(10).join(f'  - {s}' for s in result.added_scope[:5]) or '  (none)'}

ORIGINAL OBJECTIVES BEING DEPRIORITIZED:
{chr(10).join(f'  - {f}' for f in result.lost_focus[:5]) or '  (none)'}

{'!'*60}
{'ONE MORE DRIFT DETECTION WILL HALT THIS MISSION.' if remaining == 1 else f'{remaining} MORE DRIFT DETECTIONS WILL HALT THIS MISSION.'}
{'!'*60}

REFOCUS ON THE ORIGINAL MISSION:
{original_mission[:500]}{'...' if len(original_mission) > 500 else ''}

Ensure the next continuation stays STRICTLY within original mission scope.
{'='*60}

"""
        return warning

    def generate_drift_recap(
        self,
        tracking_state: DriftTrackingState,
        original_mission: str,
        mission_id: str
    ) -> Dict[str, Any]:
        """Generate a comprehensive drift recap when mission is halted."""
        # Analyze patterns in failure history
        all_added_scope = []
        all_lost_focus = []
        severity_progression = []
        similarity_progression = []

        for failure in tracking_state.failure_history:
            all_added_scope.extend(failure.get("added_scope", []))
            all_lost_focus.extend(failure.get("lost_focus", []))
            severity_progression.append(failure.get("severity", "unknown"))
            similarity_progression.append(failure.get("similarity", 0))

        # Count frequency of added scope items
        from collections import Counter
        added_scope_counts = Counter(all_added_scope)
        lost_focus_counts = Counter(all_lost_focus)

        # Determine if drift was accelerating
        accelerating = False
        if len(similarity_progression) >= 3:
            # Check if later similarities are lower than earlier ones
            early_avg = sum(similarity_progression[:2]) / 2
            late_avg = sum(similarity_progression[-2:]) / 2
            accelerating = late_avg < early_avg - 0.1  # 10% threshold

        return {
            "mission_id": mission_id,
            "original_mission": original_mission,
            "status": "HALTED_DUE_TO_DRIFT",
            "total_drift_failures": tracking_state.failure_count,
            "total_validations": tracking_state.total_validations,
            "average_similarity": tracking_state.average_similarity,
            "drift_pattern_analysis": {
                "consistently_added_scope": [
                    {"item": item, "count": count}
                    for item, count in added_scope_counts.most_common(10)
                ],
                "consistently_lost_focus": [
                    {"item": item, "count": count}
                    for item, count in lost_focus_counts.most_common(10)
                ],
                "severity_progression": severity_progression,
                "similarity_trend": similarity_progression,
                "drift_accelerating": accelerating
            },
            "failure_history": tracking_state.failure_history,
            "recommendations": self._generate_recommendations(
                added_scope_counts, lost_focus_counts, accelerating
            ),
            "suggested_refined_mission": self._suggest_refined_mission(
                original_mission, added_scope_counts, lost_focus_counts
            ),
            "generated_at": datetime.now().isoformat()
        }

    def _generate_recommendations(
        self,
        added_scope_counts: Dict[str, int],
        lost_focus_counts: Dict[str, int],
        accelerating: bool
    ) -> List[str]:
        """Generate actionable recommendations based on drift patterns."""
        recommendations = []

        if added_scope_counts:
            top_additions = list(added_scope_counts.keys())[:3]
            if len(top_additions) > 1:
                recommendations.append(
                    f"Consider creating a separate mission for: {', '.join(top_additions)}"
                )
            else:
                recommendations.append(
                    f"The feature '{top_additions[0]}' emerged repeatedly - may warrant its own mission"
                )

        if lost_focus_counts:
            top_lost = list(lost_focus_counts.keys())[:3]
            recommendations.append(
                f"Original objectives being neglected: {', '.join(top_lost)}. "
                "Consider whether the original scope was realistic."
            )

        if accelerating:
            recommendations.append(
                "Drift was accelerating across cycles - suggests the original mission "
                "scope may have been too narrow for the discovered complexity."
            )

        if not recommendations:
            recommendations.append(
                "Drift pattern unclear - consider reviewing mission boundaries "
                "and scope definition for future missions."
            )

        return recommendations

    def _suggest_refined_mission(
        self,
        original_mission: str,
        added_scope_counts: Dict[str, int],
        lost_focus_counts: Dict[str, int]
    ) -> str:
        """Suggest a refined mission based on drift patterns."""
        parts = [
            "REFINED MISSION (incorporating discovered requirements):",
            "",
            "Original core mission:",
            original_mission[:300],
            ""
        ]

        if added_scope_counts:
            top_adds = list(added_scope_counts.keys())[:5]
            parts.append("Consider including these discovered requirements:")
            for add in top_adds:
                parts.append(f"  - {add}")
            parts.append("")

        if lost_focus_counts:
            top_lost = list(lost_focus_counts.keys())[:3]
            parts.append("Ensure these original objectives are explicitly addressed:")
            for lost in top_lost:
                parts.append(f"  - {lost}")

        return "\n".join(parts)


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def validate_mission_continuation(
    original_mission: str,
    continuation_prompt: str,
    cycle_number: int,
    tracking_state: Optional[Dict] = None,
    model: ModelType = ModelType.CLAUDE_SONNET
) -> Tuple[Dict, Dict]:
    """
    Convenience function to validate a mission continuation.

    Args:
        original_mission: Original mission specification
        continuation_prompt: Generated continuation prompt
        cycle_number: Current cycle number
        tracking_state: Optional tracking state dict
        model: Model to use for evaluation

    Returns:
        Tuple of (result_dict, updated_tracking_state_dict)
    """
    validator = MissionDriftValidator(model=model)

    state = DriftTrackingState.from_dict(tracking_state) if tracking_state else None
    result, updated_state = validator.validate_continuation(
        original_mission=original_mission,
        continuation_prompt=continuation_prompt,
        cycle_number=cycle_number,
        tracking_state=state
    )

    return result.to_dict(), updated_state.to_dict()


def load_tracking_state(mission_dir: Path) -> Optional[DriftTrackingState]:
    """Load tracking state from mission directory."""
    state_path = mission_dir / "drift_tracking_state.json"
    if state_path.exists():
        try:
            with open(state_path, 'r') as f:
                data = json.load(f)
            return DriftTrackingState.from_dict(data)
        except Exception as e:
            print(f"Warning: Could not load tracking state: {e}")
    return None


def save_tracking_state(tracking_state: DriftTrackingState, mission_dir: Path):
    """Save tracking state to mission directory."""
    state_path = mission_dir / "drift_tracking_state.json"
    mission_dir.mkdir(parents=True, exist_ok=True)
    with open(state_path, 'w') as f:
        json.dump(tracking_state.to_dict(), f, indent=2)


def save_validation_result(result: MissionDriftResult, mission_dir: Path):
    """Save validation result to mission directory."""
    results_dir = mission_dir / "drift_validations"
    results_dir.mkdir(parents=True, exist_ok=True)

    result_path = results_dir / f"validation_cycle_{result.cycle_number:03d}.json"
    with open(result_path, 'w') as f:
        json.dump(result.to_dict(), f, indent=2)


# =============================================================================
# SELF TEST
# =============================================================================

if __name__ == "__main__":
    print("Mission Drift Validator - Self Test")
    print("=" * 50)

    # Test with synthetic examples
    original_mission = """
    Build a Redis caching layer for the API to reduce database load.
    Requirements:
    1. Implement cache get/set operations
    2. Configure TTL settings
    3. Add cache invalidation logic
    4. Integrate with authentication middleware
    """

    # Good continuation (no drift)
    good_continuation = """
    Continue implementing the Redis caching layer:
    - Complete the TTL configuration for different cache types
    - Test cache invalidation with the auth middleware
    - Add metrics for cache hit/miss rates
    """

    # Drifting continuation
    bad_continuation = """
    Next steps for the project:
    - Migrate database to PostgreSQL from SQLite
    - Add GraphQL API alongside REST
    - Implement real-time notifications with WebSockets
    - Create admin dashboard for monitoring
    - Set up CI/CD pipeline with Docker
    """

    print(f"\nOriginal Mission: {original_mission[:100]}...")
    print(f"\nTesting good continuation (expecting no drift)...")

    validator = MissionDriftValidator(model=ModelType.CLAUDE_HAIKU)  # Use Haiku for speed

    result, state = validator.validate_continuation(
        original_mission=original_mission,
        continuation_prompt=good_continuation,
        cycle_number=1
    )

    print(f"  Drift detected: {result.drift_detected}")
    print(f"  Severity: {result.drift_severity.value}")
    print(f"  Similarity: {result.semantic_similarity:.1%}")
    print(f"  Decision: {result.decision.value}")

    print(f"\nTesting drifting continuation (expecting HIGH drift)...")

    result2, state2 = validator.validate_continuation(
        original_mission=original_mission,
        continuation_prompt=bad_continuation,
        cycle_number=2,
        tracking_state=state
    )

    print(f"  Drift detected: {result2.drift_detected}")
    print(f"  Severity: {result2.drift_severity.value}")
    print(f"  Similarity: {result2.semantic_similarity:.1%}")
    print(f"  Decision: {result2.decision.value}")
    print(f"  Added scope: {result2.added_scope[:3]}")

    print(f"\nTracking state:")
    print(f"  Total failures: {state2.failure_count}")
    print(f"  Average similarity: {state2.average_similarity:.1%}")

    print("\nSelf test complete!")
