#!/usr/bin/env python3
"""
Mission Continuity Tracker - AtlasForge Enhancement Feature 1.2

Tracks concept fingerprints across multi-cycle missions to:
1. Detect when Claude is "drifting off-mission"
2. Enable context healing when resuming missions
3. Provide continuity metrics across cycles

Integrates with AtlasForge's cycle system to checkpoint fingerprints at CYCLE_END.

Inspired by identity fingerprinting patterns and RCFT theory
"""

import json
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict

try:
    from .fingerprint_extractor import (
        ConceptFingerprint,
        extract_fingerprint,
        cosine_similarity,
        measure_drift,
        save_fingerprint,
        load_fingerprint,
        compute_ratios
    )
except ImportError:
    from fingerprint_extractor import (
        ConceptFingerprint,
        extract_fingerprint,
        cosine_similarity,
        measure_drift,
        save_fingerprint,
        load_fingerprint,
        compute_ratios
    )


# =============================================================================
# CONFIGURATION
# =============================================================================

# Default thresholds
DRIFT_THRESHOLD_WARNING = 0.80  # Yellow alert
DRIFT_THRESHOLD_CRITICAL = 0.65  # Red alert
HEALING_SIMILARITY_TARGET = 0.90  # Target similarity after healing


@dataclass
class CycleCheckpoint:
    """Checkpoint of mission state at cycle boundary."""
    cycle_number: int
    mission_id: str
    fingerprint: ConceptFingerprint
    files_created: List[str]
    files_modified: List[str]
    key_concepts: List[str]
    summary: str
    timestamp: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d['fingerprint'] = self.fingerprint.to_dict()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'CycleCheckpoint':
        fp_data = data.pop('fingerprint')
        data['fingerprint'] = ConceptFingerprint.from_dict(fp_data)
        return cls(**data)


@dataclass
class ContinuityReport:
    """Report on mission continuity status."""
    mission_id: str
    current_cycle: int
    baseline_fingerprint_source: str
    current_fingerprint_source: str
    overall_similarity: float
    drift_severity: str
    alert_level: str
    category_analysis: Dict[str, float]
    top_drifting_concepts: List[Dict]
    healing_recommended: bool
    healing_prompt: Optional[str]
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)


# =============================================================================
# TRACKER CLASS
# =============================================================================

class MissionContinuityTracker:
    """
    Tracks mission continuity across cycles using concept fingerprinting.

    Key capabilities:
    - Checkpoint fingerprints at cycle boundaries
    - Detect drift during active work
    - Generate healing prompts when drift is detected
    - Provide continuity metrics for cycle reports
    """

    def __init__(self, mission_id: str, storage_dir: Optional[Path] = None):
        """
        Initialize tracker for a mission.

        Args:
            mission_id: Unique identifier for the mission
            storage_dir: Directory for storing checkpoints (default: ./continuity/)
        """
        self.mission_id = mission_id
        self.storage_dir = storage_dir or Path("./continuity")
        self.mission_dir = self.storage_dir / mission_id
        self.mission_dir.mkdir(parents=True, exist_ok=True)

        self.checkpoints: List[CycleCheckpoint] = []
        self.baseline_fingerprint: Optional[ConceptFingerprint] = None
        self._load_existing_checkpoints()

    def _load_existing_checkpoints(self):
        """Load any existing checkpoints for this mission."""
        checkpoint_files = sorted(self.mission_dir.glob("checkpoint_cycle_*.json"))
        for cp_file in checkpoint_files:
            try:
                with open(cp_file, 'r') as f:
                    data = json.load(f)
                checkpoint = CycleCheckpoint.from_dict(data)
                self.checkpoints.append(checkpoint)
            except Exception as e:
                print(f"Warning: Could not load checkpoint {cp_file}: {e}")

        # Set baseline from first checkpoint if available
        if self.checkpoints:
            self.baseline_fingerprint = self.checkpoints[0].fingerprint

        # Also try to load explicit baseline
        baseline_path = self.mission_dir / "baseline_fingerprint.json"
        if baseline_path.exists():
            self.baseline_fingerprint = load_fingerprint(baseline_path)

    def set_baseline(self, text: str, source: str = "initial_mission"):
        """
        Set the baseline fingerprint from initial mission text.

        This should be called at mission start to capture the original intent.
        """
        self.baseline_fingerprint = extract_fingerprint(text, source=source)
        save_fingerprint(
            self.baseline_fingerprint,
            self.mission_dir / "baseline_fingerprint.json"
        )
        return self.baseline_fingerprint

    def checkpoint_cycle(
        self,
        cycle_number: int,
        cycle_output: str,
        files_created: List[str],
        files_modified: List[str],
        summary: str
    ) -> CycleCheckpoint:
        """
        Create a checkpoint at the end of a cycle.

        Args:
            cycle_number: The cycle number just completed
            cycle_output: All Claude output from this cycle
            files_created: List of files created
            files_modified: List of files modified
            summary: Brief summary of cycle accomplishments

        Returns:
            CycleCheckpoint object
        """
        # Extract fingerprint from cycle output
        fingerprint = extract_fingerprint(
            cycle_output,
            source=f"cycle_{cycle_number}"
        )

        # Identify key concepts (top by ratio)
        key_concepts = sorted(
            fingerprint.concept_ratios.items(),
            key=lambda x: -x[1]
        )[:20]
        key_concept_names = [c[0] for c in key_concepts]

        checkpoint = CycleCheckpoint(
            cycle_number=cycle_number,
            mission_id=self.mission_id,
            fingerprint=fingerprint,
            files_created=files_created,
            files_modified=files_modified,
            key_concepts=key_concept_names,
            summary=summary,
            timestamp=datetime.now().isoformat()
        )

        # Save checkpoint
        checkpoint_path = self.mission_dir / f"checkpoint_cycle_{cycle_number:03d}.json"
        with open(checkpoint_path, 'w') as f:
            json.dump(checkpoint.to_dict(), f, indent=2)

        self.checkpoints.append(checkpoint)

        # Set baseline if this is the first checkpoint
        if self.baseline_fingerprint is None:
            self.baseline_fingerprint = fingerprint
            save_fingerprint(
                fingerprint,
                self.mission_dir / "baseline_fingerprint.json"
            )

        return checkpoint

    def check_continuity(
        self,
        current_text: str,
        source: str = "current_output"
    ) -> ContinuityReport:
        """
        Check how well current output aligns with mission baseline.

        Args:
            current_text: Current Claude output to analyze
            source: Identifier for the current output

        Returns:
            ContinuityReport with drift analysis and recommendations
        """
        if self.baseline_fingerprint is None:
            raise ValueError("No baseline fingerprint set. Call set_baseline() first.")

        current_fp = extract_fingerprint(current_text, source=source)
        drift = measure_drift(self.baseline_fingerprint, current_fp)

        healing_recommended = drift['alert_level'] in ('ORANGE', 'RED')
        healing_prompt = None

        if healing_recommended:
            healing_prompt = self._generate_healing_prompt(
                drift,
                self.baseline_fingerprint,
                current_fp
            )

        return ContinuityReport(
            mission_id=self.mission_id,
            current_cycle=len(self.checkpoints) + 1,
            baseline_fingerprint_source=self.baseline_fingerprint.source,
            current_fingerprint_source=source,
            overall_similarity=drift['overall_similarity'],
            drift_severity=drift['drift_severity'],
            alert_level=drift['alert_level'],
            category_analysis=drift['category_similarities'],
            top_drifting_concepts=drift['top_changes'][:5],
            healing_recommended=healing_recommended,
            healing_prompt=healing_prompt,
            timestamp=datetime.now().isoformat()
        )

    def _generate_healing_prompt(
        self,
        drift: Dict,
        baseline: ConceptFingerprint,
        current: ConceptFingerprint
    ) -> str:
        """
        Generate a healing prompt to restore mission alignment.

        The prompt reminds Claude of key concepts that have drifted
        and suggests refocusing on the original mission intent.
        """
        # Find concepts that decreased (mission focus lost)
        decreased = [
            c for c in drift['top_changes']
            if c['direction'] == 'decreased' and c['baseline_ratio'] > 0.02
        ]

        # Find concepts that increased (possible drift into tangents)
        increased = [
            c for c in drift['top_changes']
            if c['direction'] == 'increased' and c['current_ratio'] > 0.02
        ]

        prompt_parts = [
            "**Mission Continuity Healing**",
            "",
            f"Current alignment with original mission: {drift['overall_similarity']:.1%}",
            f"Drift severity: {drift['drift_severity']}",
            "",
        ]

        if decreased:
            prompt_parts.append("Key concepts from the original mission that need more focus:")
            for c in decreased[:5]:
                prompt_parts.append(f"  - **{c['concept']}** (was {c['baseline_ratio']:.1%}, now {c['current_ratio']:.1%})")
            prompt_parts.append("")

        if increased:
            prompt_parts.append("Areas that may be getting too much attention relative to mission:")
            for c in increased[:3]:
                prompt_parts.append(f"  - {c['concept']} (now {c['current_ratio']:.1%})")
            prompt_parts.append("")

        prompt_parts.extend([
            "To restore mission alignment:",
            "1. Review the original mission objectives",
            "2. Ensure current work directly addresses those objectives",
            "3. Avoid tangential explorations unless they serve the mission",
            ""
        ])

        return "\n".join(prompt_parts)

    def get_evolution_summary(self) -> Dict:
        """
        Get a summary of how the mission fingerprint has evolved across cycles.

        Returns trend data showing how key concepts have shifted over time.
        """
        if len(self.checkpoints) < 2:
            return {
                "message": "Need at least 2 checkpoints for evolution analysis",
                "checkpoint_count": len(self.checkpoints)
            }

        # Track concept ratios across cycles
        concept_evolution = {}
        for cp in self.checkpoints:
            for concept, ratio in cp.fingerprint.concept_ratios.items():
                if concept not in concept_evolution:
                    concept_evolution[concept] = []
                concept_evolution[concept].append({
                    'cycle': cp.cycle_number,
                    'ratio': ratio
                })

        # Find concepts with biggest changes from first to last
        changes = []
        for concept, history in concept_evolution.items():
            if len(history) >= 2:
                first_ratio = history[0]['ratio']
                last_ratio = history[-1]['ratio']
                change = last_ratio - first_ratio
                changes.append({
                    'concept': concept,
                    'first_cycle_ratio': round(first_ratio, 4),
                    'last_cycle_ratio': round(last_ratio, 4),
                    'total_change': round(change, 4),
                    'trend': 'increasing' if change > 0 else 'decreasing' if change < 0 else 'stable'
                })

        changes.sort(key=lambda x: abs(x['total_change']), reverse=True)

        # Calculate inter-cycle similarities
        cycle_similarities = []
        for i in range(len(self.checkpoints) - 1):
            sim = cosine_similarity(
                self.checkpoints[i].fingerprint.concept_ratios,
                self.checkpoints[i + 1].fingerprint.concept_ratios
            )
            cycle_similarities.append({
                'from_cycle': self.checkpoints[i].cycle_number,
                'to_cycle': self.checkpoints[i + 1].cycle_number,
                'similarity': round(sim, 4)
            })

        return {
            'checkpoint_count': len(self.checkpoints),
            'first_cycle': self.checkpoints[0].cycle_number,
            'last_cycle': self.checkpoints[-1].cycle_number,
            'top_changing_concepts': changes[:10],
            'inter_cycle_similarities': cycle_similarities,
            'average_inter_cycle_similarity': round(
                sum(s['similarity'] for s in cycle_similarities) / len(cycle_similarities),
                4
            ) if cycle_similarities else None,
            'generated_at': datetime.now().isoformat()
        }

    def merge_fingerprints(
        self,
        fingerprints: List[ConceptFingerprint],
        weights: Optional[List[float]] = None
    ) -> ConceptFingerprint:
        """
        Merge multiple fingerprints into one (for fork-remerge scenarios).

        This implements the identity healing concept from PCIT theory -
        merged fingerprint can be closer to each source than sources are to each other.

        Args:
            fingerprints: List of fingerprints to merge
            weights: Optional weights for each fingerprint (default: equal)

        Returns:
            Merged fingerprint
        """
        if not fingerprints:
            raise ValueError("No fingerprints to merge")

        if weights is None:
            weights = [1.0 / len(fingerprints)] * len(fingerprints)

        if len(weights) != len(fingerprints):
            raise ValueError("Weights length must match fingerprints length")

        # Normalize weights
        total_weight = sum(weights)
        weights = [w / total_weight for w in weights]

        # Merge concept ratios
        all_concepts = set()
        for fp in fingerprints:
            all_concepts.update(fp.concept_ratios.keys())

        merged_ratios = {}
        for concept in all_concepts:
            weighted_sum = sum(
                fp.concept_ratios.get(concept, 0.0) * w
                for fp, w in zip(fingerprints, weights)
            )
            merged_ratios[concept] = weighted_sum

        # Re-normalize
        total = sum(merged_ratios.values())
        if total > 0:
            merged_ratios = {k: v / total for k, v in merged_ratios.items()}

        # Reconstruct frequencies (approximate from ratios)
        avg_total = sum(fp.total_concepts for fp in fingerprints) // len(fingerprints)
        merged_frequencies = {k: int(v * avg_total) for k, v in merged_ratios.items()}

        # Merge category concepts (combine and re-weight)
        def merge_category(attr_name):
            merged = {}
            for fp, w in zip(fingerprints, weights):
                for concept, count in getattr(fp, attr_name).items():
                    merged[concept] = merged.get(concept, 0) + int(count * w)
            return merged

        return ConceptFingerprint(
            source=f"merged_{len(fingerprints)}_fingerprints",
            timestamp=datetime.now().isoformat(),
            concept_frequencies=merged_frequencies,
            concept_ratios=merged_ratios,
            domain_concepts=merge_category('domain_concepts'),
            architectural_concepts=merge_category('architectural_concepts'),
            action_concepts=merge_category('action_concepts'),
            meta_concepts=merge_category('meta_concepts'),
            total_concepts=avg_total
        )


# =============================================================================
# INTEGRATION FUNCTIONS
# =============================================================================

def create_tracker_for_mission(mission_data: Dict) -> MissionContinuityTracker:
    """
    Create a tracker from mission data (from atlasforge_engine).

    Args:
        mission_data: Mission dict from atlasforge_engine.py

    Returns:
        Configured MissionContinuityTracker
    """
    mission_id = mission_data.get('mission_id', 'unknown')
    mission_workspace = mission_data.get('mission_workspace')

    if mission_workspace:
        storage_dir = Path(mission_workspace) / 'continuity'
    else:
        storage_dir = Path('./continuity')

    tracker = MissionContinuityTracker(mission_id, storage_dir)

    # Set baseline from original problem statement if available
    original = mission_data.get('original_problem_statement') or mission_data.get('problem_statement', '')
    if original and tracker.baseline_fingerprint is None:
        tracker.set_baseline(original, source="original_mission")

    return tracker


def generate_continuation_with_healing(
    tracker: MissionContinuityTracker,
    continuation_prompt: str,
    cycle_output: str
) -> str:
    """
    Enhance a continuation prompt with healing if needed.

    Args:
        tracker: The mission tracker
        continuation_prompt: The base continuation prompt
        cycle_output: Output from the just-completed cycle

    Returns:
        Enhanced continuation prompt (with healing if drift detected)
    """
    report = tracker.check_continuity(cycle_output, source="cycle_output")

    if report.healing_recommended and report.healing_prompt:
        return f"{report.healing_prompt}\n\n---\n\n{continuation_prompt}"

    return continuation_prompt


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Mission Continuity Tracker - AtlasForge Enhancement")
    print("=" * 50)

    # Create a tracker
    tracker = MissionContinuityTracker("demo_mission", Path("/tmp/demo_continuity"))

    # Set baseline
    mission_statement = """
    Build a caching layer for our API using Redis. The goal is to reduce database
    load and improve response times. We need to implement cache invalidation,
    configure TTL settings, and integrate with the existing authentication layer.
    """
    tracker.set_baseline(mission_statement, source="initial_mission")
    print("\nBaseline fingerprint set from mission statement")

    # Simulate cycle 1 output (good alignment)
    cycle1_output = """
    I've started implementing the Redis cache layer. Created a cache module
    with connection pooling and basic get/set operations. The architecture
    uses a decorator pattern for automatic cache invalidation. Next step is
    to integrate with the authentication middleware and configure TTL settings.
    """
    cp1 = tracker.checkpoint_cycle(
        cycle_number=1,
        cycle_output=cycle1_output,
        files_created=["cache/redis_client.py", "cache/decorators.py"],
        files_modified=[],
        summary="Implemented basic Redis cache layer"
    )
    print(f"\nCycle 1 checkpoint created")

    # Check continuity
    report = tracker.check_continuity(cycle1_output, "cycle_1_check")
    print(f"\nContinuity Report:")
    print(f"  Similarity: {report.overall_similarity:.1%}")
    print(f"  Severity: {report.drift_severity}")
    print(f"  Alert: {report.alert_level}")
    print(f"  Healing recommended: {report.healing_recommended}")

    # Simulate cycle 2 output (some drift)
    cycle2_output = """
    I've been exploring different database optimization strategies. Looking at
    query optimization, indexing strategies, and considering a complete rewrite
    of the data access layer. Also researching GraphQL as an alternative to REST.
    The original Redis plan might need rethinking based on these discoveries.
    """
    report2 = tracker.check_continuity(cycle2_output, "cycle_2_check")
    print(f"\nCycle 2 Continuity Report:")
    print(f"  Similarity: {report2.overall_similarity:.1%}")
    print(f"  Severity: {report2.drift_severity}")
    print(f"  Alert: {report2.alert_level}")
    print(f"  Healing recommended: {report2.healing_recommended}")

    if report2.healing_prompt:
        print(f"\nHealing prompt generated:")
        print(report2.healing_prompt[:500])

    print("\nDemo complete!")
