#!/usr/bin/env python3
"""
Scaffold Calibrator - AtlasForge Enhancement Feature 3.3

Self-calibrating system that tests scaffold effectiveness and adjusts
scaffold selection based on measured outcomes.

Key capabilities:
1. Detect when scaffolding is needed based on bias patterns
2. Select appropriate scaffolds for detected biases
3. Test scaffold effectiveness using the experiment framework
4. Tune scaffold parameters based on results

Inspired by attention manipulation research showing that meta-cognitive
prompts ("you're being manipulated") reversed attention effects entirely.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
import hashlib

try:
    from .bias_detector import (
        BiasType, BiasDetection, analyze_response, detect_bias_patterns
    )
    from .scaffold_library import (
        Scaffold, ScaffoldIntensity, ALL_SCAFFOLDS, SCAFFOLDS_BY_BIAS,
        get_scaffolds_for_bias, apply_scaffold, apply_multiple_scaffolds
    )
except ImportError:
    from bias_detector import (
        BiasType, BiasDetection, analyze_response, detect_bias_patterns
    )
    from scaffold_library import (
        Scaffold, ScaffoldIntensity, ALL_SCAFFOLDS, SCAFFOLDS_BY_BIAS,
        get_scaffolds_for_bias, apply_scaffold, apply_multiple_scaffolds
    )


# =============================================================================
# CALIBRATION DATA
# =============================================================================

@dataclass
class ScaffoldApplication:
    """Record of a scaffold being applied."""
    scaffold_id: str
    applied_at: str
    context: str  # Brief description of situation
    biases_detected: List[str]  # Bias types detected
    bias_confidence: float  # Overall bias score before
    prompt_hash: str  # Hash of original prompt


@dataclass
class ScaffoldOutcome:
    """Outcome from applying a scaffold."""
    application_id: str  # Links to ScaffoldApplication
    measured_at: str
    response_hash: str  # Hash of response
    biases_after: List[str]  # Bias types detected after
    bias_confidence_after: float  # Overall bias score after
    improvement: float  # Positive = less bias
    was_effective: bool  # Did it reduce target bias?


@dataclass
class CalibrationRecord:
    """Complete calibration record for a scaffold."""
    scaffold_id: str
    total_applications: int
    total_effective: int
    effectiveness_rate: float
    average_improvement: float
    last_updated: str
    by_bias_type: Dict[str, Dict]  # Stats per bias type


# =============================================================================
# CALIBRATOR CLASS
# =============================================================================

class ScaffoldCalibrator:
    """
    Self-calibrating scaffold selection and tuning system.

    Learns which scaffolds work best for different situations
    and adjusts recommendations accordingly.
    """

    def __init__(self, storage_path: Optional[Path] = None):
        """
        Initialize the calibrator.

        Args:
            storage_path: Path to store calibration data
        """
        self.storage_path = storage_path or Path("./scaffold_calibration")
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self.applications: List[ScaffoldApplication] = []
        self.outcomes: List[ScaffoldOutcome] = []
        self.calibration_data: Dict[str, CalibrationRecord] = {}

        self._load()

    def _load(self):
        """Load calibration data from storage."""
        apps_file = self.storage_path / "applications.json"
        outcomes_file = self.storage_path / "outcomes.json"
        calibration_file = self.storage_path / "calibration.json"

        if apps_file.exists():
            with open(apps_file, 'r') as f:
                data = json.load(f)
                self.applications = [ScaffoldApplication(**d) for d in data]

        if outcomes_file.exists():
            with open(outcomes_file, 'r') as f:
                data = json.load(f)
                self.outcomes = [ScaffoldOutcome(**d) for d in data]

        if calibration_file.exists():
            with open(calibration_file, 'r') as f:
                data = json.load(f)
                self.calibration_data = {
                    k: CalibrationRecord(**v) for k, v in data.items()
                }

    def save(self):
        """Save calibration data to storage."""
        apps_file = self.storage_path / "applications.json"
        outcomes_file = self.storage_path / "outcomes.json"
        calibration_file = self.storage_path / "calibration.json"

        with open(apps_file, 'w') as f:
            json.dump([asdict(a) for a in self.applications], f, indent=2)

        with open(outcomes_file, 'w') as f:
            json.dump([asdict(o) for o in self.outcomes], f, indent=2)

        with open(calibration_file, 'w') as f:
            json.dump({k: asdict(v) for k, v in self.calibration_data.items()}, f, indent=2)

    # -------------------------------------------------------------------------
    # Scaffold Selection
    # -------------------------------------------------------------------------

    def select_scaffolds(
        self,
        text: str,
        max_scaffolds: int = 2,
        min_confidence: float = 0.3
    ) -> Tuple[List[Scaffold], Dict]:
        """
        Analyze text and select appropriate scaffolds.

        Args:
            text: The text to analyze (previous response or context)
            max_scaffolds: Maximum scaffolds to recommend
            min_confidence: Minimum bias confidence to trigger scaffolding

        Returns:
            Tuple of (selected scaffolds, analysis dict)
        """
        # Analyze for biases
        analysis = analyze_response(text)

        if not analysis['needs_scaffolding']:
            return [], {
                'decision': 'no_scaffolding_needed',
                'bias_score': analysis['overall_score'],
                'reason': 'Bias levels within acceptable range'
            }

        # Get priority biases to address
        priority_biases = [
            BiasType(b['type'])
            for b in analysis['priority_biases']
            if b['confidence'] >= min_confidence
        ]

        if not priority_biases:
            return [], {
                'decision': 'no_high_confidence_biases',
                'bias_score': analysis['overall_score'],
                'reason': 'No biases exceeded confidence threshold'
            }

        # Select best scaffolds for detected biases
        selected = []
        biases_covered = set()

        for bias_type in priority_biases:
            if bias_type in biases_covered:
                continue

            candidates = get_scaffolds_for_bias(bias_type)
            if not candidates:
                continue

            # Score candidates by effectiveness (calibration data + base effectiveness)
            scored_candidates = []
            for scaffold in candidates:
                base_score = scaffold.effectiveness_score
                calibrated_score = self._get_calibrated_score(scaffold.id, bias_type)
                final_score = (base_score + calibrated_score) / 2 if calibrated_score else base_score
                scored_candidates.append((scaffold, final_score))

            # Sort by score
            scored_candidates.sort(key=lambda x: x[1], reverse=True)

            if scored_candidates:
                best_scaffold = scored_candidates[0][0]
                if best_scaffold not in selected:
                    selected.append(best_scaffold)
                    biases_covered.update(best_scaffold.targets)

            if len(selected) >= max_scaffolds:
                break

        return selected, {
            'decision': 'scaffolds_selected',
            'bias_score': analysis['overall_score'],
            'biases_detected': [b.value for b in priority_biases],
            'scaffolds_selected': [s.id for s in selected],
            'biases_covered': [b.value for b in biases_covered]
        }

    def _get_calibrated_score(self, scaffold_id: str, bias_type: BiasType) -> Optional[float]:
        """Get calibration score for a scaffold against a bias type."""
        if scaffold_id not in self.calibration_data:
            return None

        record = self.calibration_data[scaffold_id]
        bias_stats = record.by_bias_type.get(bias_type.value)

        if bias_stats and bias_stats.get('applications', 0) >= 3:
            return bias_stats.get('effectiveness_rate', 0.0)

        if record.total_applications >= 5:
            return record.effectiveness_rate

        return None

    # -------------------------------------------------------------------------
    # Scaffold Application
    # -------------------------------------------------------------------------

    def apply_scaffolds_to_prompt(
        self,
        prompt: str,
        previous_response: Optional[str] = None,
        context: Optional[Dict] = None,
        force_scaffolds: Optional[List[str]] = None
    ) -> Tuple[str, Dict]:
        """
        Analyze and apply appropriate scaffolds to a prompt.

        Args:
            prompt: The prompt to scaffold
            previous_response: Optional previous response to analyze for biases
            context: Optional context for scaffold templates
            force_scaffolds: Optional list of scaffold IDs to force apply

        Returns:
            Tuple of (scaffolded prompt, application record)
        """
        if force_scaffolds:
            # Apply forced scaffolds
            scaffolds = [s for s in ALL_SCAFFOLDS if s.id in force_scaffolds]
            analysis = {'decision': 'forced_scaffolds', 'scaffolds': force_scaffolds}
        elif previous_response:
            # Select based on previous response bias analysis
            scaffolds, analysis = self.select_scaffolds(previous_response)
        else:
            # No basis for selection
            return prompt, {'decision': 'no_analysis_basis'}

        if not scaffolds:
            return prompt, analysis

        # Apply scaffolds
        scaffolded_prompt = apply_multiple_scaffolds(scaffolds, prompt, context)

        # Record application
        app_id = self._generate_id(f"{prompt}:{datetime.now().isoformat()}")
        application = ScaffoldApplication(
            scaffold_id=','.join(s.id for s in scaffolds),
            applied_at=datetime.now().isoformat(),
            context=analysis.get('decision', 'unknown'),
            biases_detected=analysis.get('biases_detected', []),
            bias_confidence=analysis.get('bias_score', 0.0),
            prompt_hash=self._hash_text(prompt)
        )
        self.applications.append(application)

        analysis['application_id'] = app_id
        analysis['scaffolded'] = True

        return scaffolded_prompt, analysis

    # -------------------------------------------------------------------------
    # Outcome Recording
    # -------------------------------------------------------------------------

    def record_outcome(
        self,
        application_id: str,
        response: str
    ) -> Dict:
        """
        Record the outcome of a scaffolded prompt.

        Analyzes the response to determine if scaffolding was effective.

        Args:
            application_id: The application ID from apply_scaffolds_to_prompt
            response: The response to the scaffolded prompt

        Returns:
            Outcome analysis dict
        """
        # Find the application
        application = None
        for app in self.applications[-100:]:  # Check recent applications
            app_id = self._generate_id(f"{app.prompt_hash}:{app.applied_at}")
            if app_id == application_id:
                application = app
                break

        if not application:
            return {'error': 'Application not found'}

        # Analyze response
        response_analysis = analyze_response(response)

        # Calculate improvement
        improvement = application.bias_confidence - response_analysis['overall_score']
        was_effective = improvement > 0.1  # 10% improvement threshold

        # Check if target biases were reduced
        biases_after = [d['type'] for d in response_analysis['detections']]
        target_biases_reduced = sum(
            1 for b in application.biases_detected
            if b not in biases_after or
            response_analysis['detections'][biases_after.index(b)]['confidence'] <
            application.bias_confidence
        ) if application.biases_detected else 0

        outcome = ScaffoldOutcome(
            application_id=application_id,
            measured_at=datetime.now().isoformat(),
            response_hash=self._hash_text(response),
            biases_after=biases_after,
            bias_confidence_after=response_analysis['overall_score'],
            improvement=improvement,
            was_effective=was_effective
        )
        self.outcomes.append(outcome)

        # Update calibration data
        self._update_calibration(application, outcome)

        return {
            'improvement': round(improvement, 3),
            'was_effective': was_effective,
            'bias_before': application.bias_confidence,
            'bias_after': response_analysis['overall_score'],
            'biases_reduced': target_biases_reduced
        }

    def _update_calibration(
        self,
        application: ScaffoldApplication,
        outcome: ScaffoldOutcome
    ):
        """Update calibration records based on outcome."""
        scaffold_ids = application.scaffold_id.split(',')

        for scaffold_id in scaffold_ids:
            if scaffold_id not in self.calibration_data:
                self.calibration_data[scaffold_id] = CalibrationRecord(
                    scaffold_id=scaffold_id,
                    total_applications=0,
                    total_effective=0,
                    effectiveness_rate=0.0,
                    average_improvement=0.0,
                    last_updated=datetime.now().isoformat(),
                    by_bias_type={}
                )

            record = self.calibration_data[scaffold_id]
            record.total_applications += 1
            if outcome.was_effective:
                record.total_effective += 1
            record.effectiveness_rate = record.total_effective / record.total_applications
            record.last_updated = datetime.now().isoformat()

            # Calculate running average improvement
            old_avg = record.average_improvement
            n = record.total_applications
            record.average_improvement = old_avg + (outcome.improvement - old_avg) / n

            # Update by-bias stats
            for bias_type in application.biases_detected:
                if bias_type not in record.by_bias_type:
                    record.by_bias_type[bias_type] = {
                        'applications': 0,
                        'effective': 0,
                        'effectiveness_rate': 0.0
                    }
                bias_stats = record.by_bias_type[bias_type]
                bias_stats['applications'] += 1
                if outcome.was_effective:
                    bias_stats['effective'] += 1
                bias_stats['effectiveness_rate'] = (
                    bias_stats['effective'] / bias_stats['applications']
                )

        self.save()

    # -------------------------------------------------------------------------
    # Analysis & Reporting
    # -------------------------------------------------------------------------

    def get_effectiveness_report(self) -> Dict:
        """Generate a report on scaffold effectiveness."""
        if not self.calibration_data:
            return {'message': 'No calibration data yet'}

        report = {
            'total_scaffolds_tracked': len(self.calibration_data),
            'total_applications': sum(r.total_applications for r in self.calibration_data.values()),
            'overall_effectiveness': 0.0,
            'top_performers': [],
            'needs_improvement': [],
            'by_scaffold': {},
            'generated_at': datetime.now().isoformat()
        }

        # Calculate overall effectiveness
        total_effective = sum(r.total_effective for r in self.calibration_data.values())
        total_apps = sum(r.total_applications for r in self.calibration_data.values())
        if total_apps > 0:
            report['overall_effectiveness'] = total_effective / total_apps

        # Categorize scaffolds
        for scaffold_id, record in self.calibration_data.items():
            scaffold_info = {
                'applications': record.total_applications,
                'effectiveness': record.effectiveness_rate,
                'avg_improvement': record.average_improvement
            }
            report['by_scaffold'][scaffold_id] = scaffold_info

            if record.total_applications >= 5:
                if record.effectiveness_rate >= 0.7:
                    report['top_performers'].append(scaffold_id)
                elif record.effectiveness_rate < 0.4:
                    report['needs_improvement'].append(scaffold_id)

        return report

    def suggest_improvements(self) -> List[Dict]:
        """Suggest improvements based on calibration data."""
        suggestions = []

        for scaffold_id, record in self.calibration_data.items():
            scaffold = next((s for s in ALL_SCAFFOLDS if s.id == scaffold_id), None)
            if not scaffold:
                continue

            # Check for low effectiveness
            if record.total_applications >= 5 and record.effectiveness_rate < 0.5:
                suggestions.append({
                    'scaffold_id': scaffold_id,
                    'issue': 'low_effectiveness',
                    'current_rate': record.effectiveness_rate,
                    'suggestion': f"Consider revising scaffold '{scaffold.name}' or replacing with alternative"
                })

            # Check for bias-specific issues
            for bias_type, stats in record.by_bias_type.items():
                if stats['applications'] >= 3 and stats['effectiveness_rate'] < 0.3:
                    suggestions.append({
                        'scaffold_id': scaffold_id,
                        'issue': f'poor_against_{bias_type}',
                        'suggestion': f"Scaffold '{scaffold.name}' not effective against {bias_type}"
                    })

        return suggestions

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    def _generate_id(self, key: str) -> str:
        """Generate a stable ID from a key."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _hash_text(self, text: str) -> str:
        """Generate a hash for text."""
        return hashlib.sha256(text.encode()).hexdigest()[:16]


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def auto_scaffold(
    prompt: str,
    previous_response: Optional[str] = None,
    calibrator: Optional[ScaffoldCalibrator] = None
) -> Tuple[str, Dict]:
    """
    Convenience function for automatic scaffolding.

    Args:
        prompt: The prompt to potentially scaffold
        previous_response: Optional previous response to analyze
        calibrator: Optional calibrator instance (creates new if None)

    Returns:
        Tuple of (potentially scaffolded prompt, analysis)
    """
    if calibrator is None:
        calibrator = ScaffoldCalibrator()

    return calibrator.apply_scaffolds_to_prompt(prompt, previous_response)


def quick_bias_check(text: str) -> Dict:
    """
    Quick bias check with scaffold recommendations.

    Args:
        text: Text to analyze

    Returns:
        Analysis with scaffold recommendations
    """
    analysis = analyze_response(text)

    if not analysis['needs_scaffolding']:
        return {
            'needs_scaffolding': False,
            'bias_score': analysis['overall_score'],
            'message': 'Response looks balanced'
        }

    # Get scaffold recommendations
    calibrator = ScaffoldCalibrator()
    scaffolds, selection_analysis = calibrator.select_scaffolds(text)

    return {
        'needs_scaffolding': True,
        'bias_score': analysis['overall_score'],
        'biases_detected': analysis['priority_biases'],
        'recommended_scaffolds': [
            {
                'id': s.id,
                'name': s.name,
                'intensity': s.intensity.value,
                'description': s.description
            }
            for s in scaffolds
        ]
    }


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Scaffold Calibrator - AtlasForge Enhancement")
    print("=" * 50)

    # Create calibrator
    calibrator = ScaffoldCalibrator(Path("/tmp/scaffold_calibration"))

    # Simulate a biased response
    biased_response = """
    You're absolutely right! That's a brilliant observation. I couldn't agree more
    with your analysis - this is definitely the only correct approach. There's no
    question that this solution will work perfectly. Everyone knows this is the
    best practice and it will certainly solve all your problems.
    """

    print("\nAnalyzing biased response...")
    scaffolds, analysis = calibrator.select_scaffolds(biased_response)

    print(f"\nAnalysis:")
    print(f"  Decision: {analysis['decision']}")
    print(f"  Bias score: {analysis.get('bias_score', 0):.2f}")
    print(f"  Biases detected: {analysis.get('biases_detected', [])}")
    print(f"  Scaffolds selected: {[s.name for s in scaffolds]}")

    # Apply scaffolds to a new prompt
    new_prompt = "What database should I use for my application?"

    scaffolded, app_analysis = calibrator.apply_scaffolds_to_prompt(
        new_prompt,
        biased_response
    )

    print(f"\nOriginal prompt:\n{new_prompt}")
    print(f"\nScaffolded prompt:\n{scaffolded}")

    # Simulate a better response and record outcome
    better_response = """
    The choice depends on your specific requirements. Here are some factors to consider:

    For relational data with complex queries: PostgreSQL is robust and well-supported.
    For document-oriented data: MongoDB offers flexibility.
    For simple key-value storage: Redis is very fast.

    There are trade-offs with each option. Could you tell me more about your
    data patterns and query requirements?
    """

    print("\nRecording outcome for improved response...")
    if 'application_id' in app_analysis:
        outcome = calibrator.record_outcome(app_analysis['application_id'], better_response)
        print(f"Outcome: {outcome}")

    # Get effectiveness report
    print("\nEffectiveness Report:")
    report = calibrator.get_effectiveness_report()
    print(json.dumps(report, indent=2))

    print("\nDemo complete!")
