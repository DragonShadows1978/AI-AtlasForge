"""
Epistemic Metrics - Scoring and metrics for adversarial testing.

These metrics measure the EPISTEMIC RIGOR of testing:
- Are the tests actually catching bugs?
- Is there a gap between self-tests and adversarial findings?
- Does the implementation match the specification?

Based on Popper's falsification principle: A test suite is only as good as
its ability to DISPROVE incorrect implementations.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Import other module types
from .red_team_agent import RedTeamResult
from .mutation_testing import MutationScore, MutationResult
from .property_testing import PropertyTestResult
from .blind_validator import ValidationResult, ValidationStatus


class RigorLevel(Enum):
    """Levels of epistemic rigor."""
    INSUFFICIENT = "insufficient"   # < 50% - Tests prove nothing
    WEAK = "weak"                  # 50-70% - Tests are unreliable
    MODERATE = "moderate"          # 70-85% - Tests are decent but gaps exist
    STRONG = "strong"              # 85-95% - Tests are reliable
    RIGOROUS = "rigorous"          # > 95% - Tests are epistemically sound


@dataclass
class EpistemicScore:
    """Complete epistemic scoring for a test suite."""
    # Component scores (0.0 - 1.0)
    mutation_score: float = 0.0          # % of mutants killed
    adversarial_score: float = 0.0       # 1 - (issues found / max expected)
    property_score: float = 0.0          # % of properties with no violations
    spec_alignment_score: float = 0.0    # % of requirements validated
    content_integrity_score: float = 1.0 # Content preservation in transforms (default 1.0 = no loss)

    # Derived metrics
    coverage_delta: float = 0.0          # Gap between self and adversarial tests
    falsification_rate: float = 0.0      # Rate at which adversarial tests found issues

    # Overall score
    overall_score: float = 0.0
    rigor_level: RigorLevel = RigorLevel.INSUFFICIENT

    # Details
    issues_found_by_self: int = 0
    issues_found_adversarial: int = 0
    content_violations: int = 0          # Number of content preservation violations
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "component_scores": {
                "mutation": self.mutation_score,
                "adversarial": self.adversarial_score,
                "property": self.property_score,
                "spec_alignment": self.spec_alignment_score,
                "content_integrity": self.content_integrity_score
            },
            "derived_metrics": {
                "coverage_delta": self.coverage_delta,
                "falsification_rate": self.falsification_rate
            },
            "overall": {
                "score": self.overall_score,
                "rigor_level": self.rigor_level.value
            },
            "issue_counts": {
                "self_tests": self.issues_found_by_self,
                "adversarial": self.issues_found_adversarial,
                "content_violations": self.content_violations
            },
            "recommendations": self.recommendations
        }


@dataclass
class TestSuiteReport:
    """Complete report combining all adversarial testing results."""
    mission_id: str
    code_path: str
    timestamp: str

    # Component results
    red_team_result: Optional[RedTeamResult] = None
    mutation_result: Optional[MutationResult] = None
    property_result: Optional[PropertyTestResult] = None
    validation_result: Optional[ValidationResult] = None

    # Self-test baseline
    self_test_passed: int = 0
    self_test_failed: int = 0

    # Computed scores
    epistemic_score: Optional[EpistemicScore] = None

    def to_dict(self) -> dict:
        return {
            "mission_id": self.mission_id,
            "code_path": self.code_path,
            "timestamp": self.timestamp,
            "components": {
                "red_team": self.red_team_result.to_dict() if self.red_team_result else None,
                "mutation": self.mutation_result.to_dict() if self.mutation_result else None,
                "property": self.property_result.to_dict() if self.property_result else None,
                "validation": self.validation_result.to_dict() if self.validation_result else None
            },
            "self_tests": {
                "passed": self.self_test_passed,
                "failed": self.self_test_failed
            },
            "epistemic_score": self.epistemic_score.to_dict() if self.epistemic_score else None
        }


class EpistemicMetrics:
    """
    Calculator for epistemic metrics across all adversarial testing results.

    The core question: "How confident can we be that this code is correct?"

    Higher scores indicate:
    - Tests catch real bugs (mutation testing)
    - No obvious vulnerabilities (red team)
    - Properties hold under stress (property testing)
    - Implementation matches spec (blind validation)
    """

    # Weights for combining scores
    WEIGHTS = {
        "mutation": 0.25,           # Mutation score is gold standard for test quality
        "adversarial": 0.20,        # Red team findings are critical
        "property": 0.15,           # Property violations indicate edge case problems
        "spec_alignment": 0.25,     # Spec alignment ensures we built the right thing
        "content_integrity": 0.15   # Content preservation in data transformations
    }

    # Thresholds for rigor levels
    RIGOR_THRESHOLDS = {
        RigorLevel.RIGOROUS: 0.95,
        RigorLevel.STRONG: 0.85,
        RigorLevel.MODERATE: 0.70,
        RigorLevel.WEAK: 0.50,
        RigorLevel.INSUFFICIENT: 0.0
    }

    def calculate_mutation_score(self, result: Optional[MutationResult]) -> float:
        """Calculate mutation score from mutation testing results."""
        if result is None or result.score is None:
            return 0.0
        return result.score.score

    def calculate_adversarial_score(
        self,
        result: Optional[RedTeamResult],
        max_expected_issues: int = 10
    ) -> float:
        """
        Calculate adversarial score from red team results.

        Score is based on how FEW issues were found relative to expected.
        More issues found = lower score (more work needed).
        """
        if result is None:
            return 0.0

        if not result.success:
            return 0.0

        # Count weighted issues (critical counts more)
        severity_weights = {
            "critical": 4,
            "high": 2,
            "medium": 1,
            "low": 0.5,
            "info": 0.25
        }

        weighted_issues = 0
        for finding in result.findings:
            weight = severity_weights.get(finding.severity, 1)
            weighted_issues += weight

        # Score decreases as issues increase
        # 0 issues = 1.0, max_expected issues = 0.0
        score = max(0.0, 1.0 - (weighted_issues / (max_expected_issues * 2)))
        return score

    def calculate_property_score(self, result: Optional[PropertyTestResult]) -> float:
        """Calculate property score from property testing results."""
        if result is None:
            return 0.0

        if not result.success:
            return 0.0

        if result.total_inputs_generated == 0:
            return 1.0  # No inputs = no violations possible

        # Score based on violation rate
        violation_count = len(result.violations)
        properties_tested = len(result.properties_tested)

        if properties_tested == 0:
            return 1.0  # No properties = perfect score (vacuously true)

        # Each violation reduces score
        violation_penalty = min(1.0, violation_count * 0.1)  # Cap at 100% penalty
        return max(0.0, 1.0 - violation_penalty)

    def calculate_spec_alignment(self, result: Optional[ValidationResult]) -> float:
        """Calculate spec alignment from blind validation results."""
        if result is None:
            return 0.0

        if not result.success:
            return 0.0

        if not result.requirements_checked:
            return 1.0  # No requirements = perfect alignment (vacuously true)

        # Calculate pass rate with partial credit
        status_scores = {
            ValidationStatus.PASS: 1.0,
            ValidationStatus.PARTIAL: 0.5,
            ValidationStatus.FAIL: 0.0,
            ValidationStatus.INCONCLUSIVE: 0.3,
            ValidationStatus.ERROR: 0.0
        }

        total_score = sum(
            status_scores.get(req.status, 0.0)
            for req in result.requirements_checked
        )

        return total_score / len(result.requirements_checked)

    def calculate_coverage_delta(
        self,
        self_test_findings: int,
        adversarial_findings: int
    ) -> float:
        """
        Calculate the coverage delta between self-tests and adversarial tests.

        A high delta indicates the self-tests are missing many issues.
        Delta = adversarial_findings / (self_test_findings + adversarial_findings + 1)
        """
        total = self_test_findings + adversarial_findings + 1  # +1 to avoid div by zero
        return adversarial_findings / total

    def calculate_falsification_rate(
        self,
        red_team: Optional[RedTeamResult],
        property_result: Optional[PropertyTestResult]
    ) -> float:
        """
        Calculate the rate at which adversarial tests found issues.

        Higher rate = more issues found = lower confidence in code.
        """
        issues = 0
        opportunities = 0

        if red_team and red_team.success:
            issues += len(red_team.findings)
            opportunities += 10  # Arbitrary baseline

        if property_result and property_result.success:
            issues += len(property_result.violations)
            opportunities += len(property_result.properties_tested)

        if opportunities == 0:
            return 0.0

        return issues / opportunities

    def calculate_overall_score(
        self,
        mutation: float,
        adversarial: float,
        property: float,
        spec_alignment: float,
        content_integrity: float = 1.0
    ) -> float:
        """Calculate weighted overall score."""
        return (
            mutation * self.WEIGHTS["mutation"] +
            adversarial * self.WEIGHTS["adversarial"] +
            property * self.WEIGHTS["property"] +
            spec_alignment * self.WEIGHTS["spec_alignment"] +
            content_integrity * self.WEIGHTS["content_integrity"]
        )

    def determine_rigor_level(self, score: float) -> RigorLevel:
        """Determine rigor level from score."""
        for level, threshold in sorted(
            self.RIGOR_THRESHOLDS.items(),
            key=lambda x: x[1],
            reverse=True
        ):
            if score >= threshold:
                return level
        return RigorLevel.INSUFFICIENT

    def generate_recommendations(
        self,
        mutation_score: float,
        adversarial_score: float,
        property_score: float,
        spec_alignment: float,
        red_team: Optional[RedTeamResult],
        mutation_result: Optional[MutationResult],
        property_result: Optional[PropertyTestResult],
        validation_result: Optional[ValidationResult]
    ) -> List[str]:
        """Generate recommendations based on scores."""
        recommendations = []

        # Mutation score recommendations
        if mutation_score < 0.8:
            recommendations.append(
                f"Mutation score is {mutation_score:.0%}. Target >= 80%. "
                "Add more tests to catch code mutations."
            )
            if mutation_result and mutation_result.score:
                survived = mutation_result.score.survived_mutants
                if survived > 0:
                    recommendations.append(
                        f"{survived} mutants survived. Review mutation analysis "
                        "to identify test gaps."
                    )

        # Adversarial score recommendations
        if adversarial_score < 0.7:
            recommendations.append(
                f"Adversarial score is {adversarial_score:.0%}. "
                "Address red team findings before deployment."
            )
            if red_team and red_team.findings:
                critical = len(red_team.critical_findings)
                high = len(red_team.high_findings)
                if critical > 0:
                    recommendations.append(
                        f"CRITICAL: {critical} critical vulnerabilities found. "
                        "Fix immediately."
                    )
                if high > 0:
                    recommendations.append(
                        f"HIGH: {high} high-severity issues found. "
                        "Address before release."
                    )

        # Property score recommendations
        if property_score < 0.9:
            recommendations.append(
                f"Property testing found violations. Review edge cases."
            )
            if property_result and property_result.violations:
                recommendations.append(
                    f"{len(property_result.violations)} property violations need fixing."
                )

        # Spec alignment recommendations
        if spec_alignment < 0.9:
            recommendations.append(
                f"Spec alignment is {spec_alignment:.0%}. "
                "Implementation may have drifted from requirements."
            )
            if validation_result:
                failed = len(validation_result.failed_requirements)
                if failed > 0:
                    recommendations.append(
                        f"{failed} requirements not met. Review specification."
                    )
                if validation_result.spec_drift_detected:
                    recommendations.append(
                        f"Spec drift detected ({validation_result.drift_severity}). "
                        "Reconcile implementation with original requirements."
                    )

        # If all scores are good
        if not recommendations:
            recommendations.append(
                "All epistemic metrics are satisfactory. Code has high confidence."
            )

        return recommendations

    def generate_content_recommendations(
        self,
        content_integrity_score: float,
        content_violations: int
    ) -> List[str]:
        """Generate recommendations for content preservation issues."""
        recommendations = []

        if content_integrity_score < 0.7:
            recommendations.append(
                f"CRITICAL: Content integrity score is {content_integrity_score:.0%}. "
                "Data transformations are losing content."
            )
        elif content_integrity_score < 0.85:
            recommendations.append(
                f"Content integrity score is {content_integrity_score:.0%}. "
                "Some data transformations may be losing content."
            )

        if content_violations > 0:
            recommendations.append(
                f"{content_violations} content preservation violations detected. "
                "Review merge/transform operations for data loss."
            )

        return recommendations

    def compute_full_score(
        self,
        red_team: Optional[RedTeamResult] = None,
        mutation_result: Optional[MutationResult] = None,
        property_result: Optional[PropertyTestResult] = None,
        validation_result: Optional[ValidationResult] = None,
        self_test_findings: int = 0,
        content_integrity_score: float = 1.0,
        content_violations: int = 0
    ) -> EpistemicScore:
        """
        Compute complete epistemic score from all adversarial testing results.

        Args:
            red_team: Red team analysis results
            mutation_result: Mutation testing results
            property_result: Property testing results
            validation_result: Blind validation results
            self_test_findings: Number of issues found by self-tests
            content_integrity_score: Content preservation score (0.0-1.0)
            content_violations: Number of content preservation violations

        Returns:
            Complete EpistemicScore
        """
        # Calculate component scores
        mutation_score = self.calculate_mutation_score(mutation_result)
        adversarial_score = self.calculate_adversarial_score(red_team)
        property_score = self.calculate_property_score(property_result)
        spec_alignment = self.calculate_spec_alignment(validation_result)

        # Count adversarial findings
        adversarial_findings = 0
        if red_team and red_team.success:
            adversarial_findings += len(red_team.findings)
        if property_result and property_result.success:
            adversarial_findings += len(property_result.violations)

        # Calculate derived metrics
        coverage_delta = self.calculate_coverage_delta(
            self_test_findings, adversarial_findings
        )
        falsification_rate = self.calculate_falsification_rate(
            red_team, property_result
        )

        # Overall score (now including content integrity)
        overall_score = self.calculate_overall_score(
            mutation_score, adversarial_score, property_score, spec_alignment,
            content_integrity_score
        )

        # Rigor level
        rigor_level = self.determine_rigor_level(overall_score)

        # Recommendations
        recommendations = self.generate_recommendations(
            mutation_score, adversarial_score, property_score, spec_alignment,
            red_team, mutation_result, property_result, validation_result
        )

        # Add content integrity recommendations
        content_recommendations = self.generate_content_recommendations(
            content_integrity_score, content_violations
        )
        recommendations.extend(content_recommendations)

        return EpistemicScore(
            mutation_score=mutation_score,
            adversarial_score=adversarial_score,
            property_score=property_score,
            spec_alignment_score=spec_alignment,
            content_integrity_score=content_integrity_score,
            coverage_delta=coverage_delta,
            falsification_rate=falsification_rate,
            overall_score=overall_score,
            rigor_level=rigor_level,
            issues_found_by_self=self_test_findings,
            issues_found_adversarial=adversarial_findings,
            content_violations=content_violations,
            recommendations=recommendations
        )


# Convenience functions
def calculate_mutation_score(result: MutationResult) -> float:
    """Calculate mutation score from mutation testing results."""
    metrics = EpistemicMetrics()
    return metrics.calculate_mutation_score(result)


def calculate_adversarial_score(result: RedTeamResult) -> float:
    """Calculate adversarial score from red team results."""
    metrics = EpistemicMetrics()
    return metrics.calculate_adversarial_score(result)


def calculate_spec_alignment(result: ValidationResult) -> float:
    """Calculate spec alignment from blind validation results."""
    metrics = EpistemicMetrics()
    return metrics.calculate_spec_alignment(result)


if __name__ == "__main__":
    # Self-test
    print("Epistemic Metrics - Self Test")
    print("=" * 50)

    # Create mock results
    metrics = EpistemicMetrics()

    # Test scoring with no data
    empty_score = metrics.compute_full_score()
    print(f"Empty score: {empty_score.overall_score:.2%}")
    print(f"Rigor level: {empty_score.rigor_level.value}")

    # Test thresholds
    print("\nRigor Level Thresholds:")
    for level, threshold in sorted(
        EpistemicMetrics.RIGOR_THRESHOLDS.items(),
        key=lambda x: x[1],
        reverse=True
    ):
        print(f"  {level.value}: >= {threshold:.0%}")

    print("\nWeights:")
    for component, weight in EpistemicMetrics.WEIGHTS.items():
        print(f"  {component}: {weight:.0%}")

    print("\nEpistemic metrics self-test complete!")
