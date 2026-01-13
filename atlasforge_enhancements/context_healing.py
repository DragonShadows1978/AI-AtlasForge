#!/usr/bin/env python3
"""
Context Healing - AtlasForge Enhancement Feature 1.3

Generates "healing prompts" to restore mission alignment when drift is detected.
Uses fingerprint comparison to identify what's been lost and what needs reinforcement.

This module provides the intelligence behind mission recovery:
- Identifies specific concepts that have drifted
- Generates targeted prompts to restore alignment
- Can merge multiple diverged mission branches

Inspired by identity healing protocols and PCIT theory
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

try:
    from .fingerprint_extractor import (
        ConceptFingerprint,
        cosine_similarity,
        compute_ratios
    )
    from .mission_continuity_tracker import MissionContinuityTracker, ContinuityReport
except ImportError:
    from fingerprint_extractor import (
        ConceptFingerprint,
        cosine_similarity,
        compute_ratios
    )
    from mission_continuity_tracker import MissionContinuityTracker, ContinuityReport


# =============================================================================
# HEALING STRATEGIES
# =============================================================================

@dataclass
class HealingStrategy:
    """A strategy for healing mission drift."""
    name: str
    description: str
    applicability_threshold: float  # Minimum similarity to apply this strategy
    prompt_template: str


# Define healing strategies from gentle to aggressive
HEALING_STRATEGIES = [
    HealingStrategy(
        name="gentle_reminder",
        description="Light reminder of mission focus for minor drift",
        applicability_threshold=0.80,
        prompt_template="""
**Quick Mission Focus Check**

As you continue, keep in mind the core mission objectives:
{core_concepts}

Continue with your current approach while ensuring alignment with these goals.
"""
    ),
    HealingStrategy(
        name="concept_reinforcement",
        description="Reinforce specific concepts that have drifted",
        applicability_threshold=0.70,
        prompt_template="""
**Mission Alignment Needed**

Some key concepts from the original mission need more attention:

**Concepts to emphasize:**
{decreased_concepts}

**Areas to moderate (possible tangents):**
{increased_concepts}

Please refocus on the core mission while incorporating lessons learned.
"""
    ),
    HealingStrategy(
        name="mission_reset",
        description="Strong redirect back to original mission",
        applicability_threshold=0.60,
        prompt_template="""
**Mission Realignment Required**

Current work has drifted significantly from the original mission.

**Original Mission Intent:**
{original_mission}

**Core Concepts to Restore:**
{core_concepts}

**Action Required:**
1. Pause current exploratory work
2. Review original mission objectives
3. Identify what from current work directly serves the mission
4. Refocus on delivering the core mission requirements

Please acknowledge this realignment and proceed with mission-focused work.
"""
    ),
    HealingStrategy(
        name="full_context_restoration",
        description="Complete restoration for severe drift",
        applicability_threshold=0.0,  # Always applicable as last resort
        prompt_template="""
**Critical Mission Restoration**

Work has diverged substantially from the original mission.

**Original Mission:**
{original_mission}

**What the Mission Required:**
{core_concepts}

**What Current Work is Doing:**
{current_focus}

**Gap Analysis:**
{gap_analysis}

**Required Actions:**
1. Stop all non-mission-critical work immediately
2. Document any valuable discoveries from the divergent work
3. Return to the original mission requirements
4. Build a new plan that directly addresses mission objectives
5. Execute with focus on delivering stated goals

This is a mission-critical realignment. Please confirm understanding and proceed.
"""
    )
]


# =============================================================================
# HEALING FUNCTIONS
# =============================================================================

def select_healing_strategy(similarity: float) -> HealingStrategy:
    """
    Select the appropriate healing strategy based on similarity score.

    Higher similarity = gentler healing.
    Lower similarity = more aggressive restoration.
    """
    for strategy in HEALING_STRATEGIES:
        if similarity >= strategy.applicability_threshold:
            return strategy
    return HEALING_STRATEGIES[-1]  # Full restoration as fallback


def identify_concept_gaps(
    baseline: ConceptFingerprint,
    current: ConceptFingerprint,
    threshold: float = 0.02
) -> Tuple[List[Dict], List[Dict]]:
    """
    Identify concepts that have decreased (lost focus) and increased (new tangents).

    Args:
        baseline: The baseline fingerprint
        current: The current fingerprint
        threshold: Minimum ratio to consider significant

    Returns:
        Tuple of (decreased_concepts, increased_concepts)
    """
    all_concepts = set(baseline.concept_ratios.keys()) | set(current.concept_ratios.keys())

    decreased = []
    increased = []

    for concept in all_concepts:
        baseline_ratio = baseline.concept_ratios.get(concept, 0.0)
        current_ratio = current.concept_ratios.get(concept, 0.0)
        change = current_ratio - baseline_ratio

        if baseline_ratio > threshold and change < -0.01:
            decreased.append({
                'concept': concept,
                'baseline_ratio': baseline_ratio,
                'current_ratio': current_ratio,
                'lost': baseline_ratio - current_ratio
            })
        elif current_ratio > threshold and change > 0.01:
            increased.append({
                'concept': concept,
                'baseline_ratio': baseline_ratio,
                'current_ratio': current_ratio,
                'gained': current_ratio - baseline_ratio
            })

    # Sort by magnitude of change
    decreased.sort(key=lambda x: x['lost'], reverse=True)
    increased.sort(key=lambda x: x['gained'], reverse=True)

    return decreased, increased


def generate_healing_prompt(
    tracker: MissionContinuityTracker,
    current_output: str,
    original_mission: str,
    strategy_override: Optional[str] = None
) -> Dict:
    """
    Generate a complete healing prompt with analysis.

    Args:
        tracker: The mission continuity tracker
        current_output: The current Claude output
        original_mission: The original mission statement
        strategy_override: Optional strategy name to force

    Returns:
        Dict containing healing prompt and analysis
    """
    from fingerprint_extractor import extract_fingerprint

    # Get baseline and current fingerprints
    baseline = tracker.baseline_fingerprint
    if baseline is None:
        return {
            "error": "No baseline fingerprint available",
            "recommendation": "Call tracker.set_baseline() first"
        }

    current = extract_fingerprint(current_output, source="current_output")

    # Calculate similarity
    similarity = cosine_similarity(baseline.concept_ratios, current.concept_ratios)

    # Select strategy
    if strategy_override:
        strategy = next(
            (s for s in HEALING_STRATEGIES if s.name == strategy_override),
            select_healing_strategy(similarity)
        )
    else:
        strategy = select_healing_strategy(similarity)

    # Identify concept gaps
    decreased, increased = identify_concept_gaps(baseline, current)

    # Extract core concepts from baseline
    core_concepts = sorted(
        baseline.concept_ratios.items(),
        key=lambda x: -x[1]
    )[:10]

    # Get current focus
    current_focus = sorted(
        current.concept_ratios.items(),
        key=lambda x: -x[1]
    )[:10]

    # Format prompt template variables
    format_vars = {
        'original_mission': original_mission[:1000],
        'core_concepts': "\n".join(
            f"  - **{c[0]}** ({c[1]:.1%})"
            for c in core_concepts[:7]
        ),
        'decreased_concepts': "\n".join(
            f"  - {c['concept']}: was {c['baseline_ratio']:.1%}, now {c['current_ratio']:.1%}"
            for c in decreased[:5]
        ) or "  (None significant)",
        'increased_concepts': "\n".join(
            f"  - {c['concept']}: was {c['baseline_ratio']:.1%}, now {c['current_ratio']:.1%}"
            for c in increased[:3]
        ) or "  (None significant)",
        'current_focus': "\n".join(
            f"  - {c[0]} ({c[1]:.1%})"
            for c in current_focus[:5]
        ),
        'gap_analysis': _generate_gap_analysis(baseline, current, decreased, increased)
    }

    # Generate healing prompt
    healing_prompt = strategy.prompt_template.format(**format_vars)

    return {
        "similarity": round(similarity, 4),
        "strategy_selected": strategy.name,
        "strategy_description": strategy.description,
        "healing_prompt": healing_prompt.strip(),
        "analysis": {
            "core_concepts_count": len(core_concepts),
            "decreased_concepts_count": len(decreased),
            "increased_concepts_count": len(increased),
            "top_decreased": [c['concept'] for c in decreased[:5]],
            "top_increased": [c['concept'] for c in increased[:3]]
        },
        "timestamp": datetime.now().isoformat()
    }


def _generate_gap_analysis(
    baseline: ConceptFingerprint,
    current: ConceptFingerprint,
    decreased: List[Dict],
    increased: List[Dict]
) -> str:
    """Generate a textual gap analysis."""
    lines = []

    if decreased:
        total_lost = sum(c['lost'] for c in decreased)
        lines.append(f"Lost focus on {len(decreased)} concepts (total drift: {total_lost:.1%})")
        for c in decreased[:3]:
            lines.append(f"  - '{c['concept']}' dropped from {c['baseline_ratio']:.1%} to {c['current_ratio']:.1%}")

    if increased:
        total_gained = sum(c['gained'] for c in increased)
        lines.append(f"New emphasis on {len(increased)} concepts (total: {total_gained:.1%})")
        for c in increased[:3]:
            lines.append(f"  - '{c['concept']}' rose from {c['baseline_ratio']:.1%} to {c['current_ratio']:.1%}")

    if not lines:
        lines.append("No significant concept shifts detected")

    return "\n".join(lines)


# =============================================================================
# BRANCH MERGING (Fork-Remerge)
# =============================================================================

def merge_diverged_branches(
    trackers: List[MissionContinuityTracker],
    weights: Optional[List[float]] = None
) -> Tuple[ConceptFingerprint, Dict]:
    """
    Merge fingerprints from diverged mission branches.

    This implements the fork-remerge capability from PCIT theory.
    The merged fingerprint can be closer to each source than sources are to each other.

    Args:
        trackers: List of trackers from diverged branches
        weights: Optional weights for each branch (default: equal)

    Returns:
        Tuple of (merged_fingerprint, merge_analysis)
    """
    if len(trackers) < 2:
        raise ValueError("Need at least 2 branches to merge")

    # Get the latest fingerprint from each branch
    fingerprints = []
    for tracker in trackers:
        if tracker.checkpoints:
            fingerprints.append(tracker.checkpoints[-1].fingerprint)
        elif tracker.baseline_fingerprint:
            fingerprints.append(tracker.baseline_fingerprint)
        else:
            raise ValueError(f"Tracker {tracker.mission_id} has no fingerprints")

    # Perform merge
    merged = trackers[0].merge_fingerprints(fingerprints, weights)

    # Analyze merge quality
    analysis = {
        "sources": [fp.source for fp in fingerprints],
        "merged_timestamp": merged.timestamp,
        "pairwise_similarities": {},
        "merged_to_source_similarities": {},
        "healing_bonus": {}
    }

    # Calculate pairwise similarities between sources
    for i in range(len(fingerprints)):
        for j in range(i + 1, len(fingerprints)):
            key = f"{fingerprints[i].source}_to_{fingerprints[j].source}"
            sim = cosine_similarity(
                fingerprints[i].concept_ratios,
                fingerprints[j].concept_ratios
            )
            analysis["pairwise_similarities"][key] = round(sim, 4)

    # Calculate merged-to-source similarities
    for fp in fingerprints:
        sim = cosine_similarity(merged.concept_ratios, fp.concept_ratios)
        analysis["merged_to_source_similarities"][fp.source] = round(sim, 4)

        # Calculate healing bonus (merged closer to source than other sources are)
        other_sims = [
            cosine_similarity(fp.concept_ratios, other_fp.concept_ratios)
            for other_fp in fingerprints if other_fp.source != fp.source
        ]
        avg_other = sum(other_sims) / len(other_sims) if other_sims else 0
        bonus = sim - avg_other
        analysis["healing_bonus"][fp.source] = round(bonus, 4)

    # Overall merge quality
    analysis["merge_successful"] = all(
        bonus > 0 for bonus in analysis["healing_bonus"].values()
    )
    analysis["interpretation"] = (
        "MERGE SUCCESSFUL - Merged fingerprint is closer to each source than sources are to each other"
        if analysis["merge_successful"]
        else "MERGE PARTIAL - Some sources may be too divergent"
    )

    return merged, analysis


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Context Healing - AtlasForge Enhancement")
    print("=" * 50)

    from fingerprint_extractor import extract_fingerprint

    # Create a tracker with baseline
    tracker = MissionContinuityTracker("healing_demo", Path("/tmp/healing_demo"))

    original_mission = """
    Implement a user authentication system with JWT tokens. The system should
    support login, logout, password reset, and session management. Security
    is paramount - use bcrypt for hashing and implement rate limiting.
    """
    tracker.set_baseline(original_mission, source="original_mission")

    # Simulate drifted output
    drifted_output = """
    I've been exploring different frontend frameworks. React seems promising
    for building the UI. Also looking at GraphQL as an alternative to REST.
    Considering a microservices architecture with Kubernetes deployment.
    The database might need to be MongoDB instead of PostgreSQL.
    """

    print("\nGenerating healing prompt for drifted output...")
    result = generate_healing_prompt(
        tracker,
        drifted_output,
        original_mission
    )

    print(f"\nSimilarity: {result['similarity']:.1%}")
    print(f"Strategy: {result['strategy_selected']}")
    print(f"\nHealing Prompt:")
    print("-" * 40)
    print(result['healing_prompt'])
    print("-" * 40)

    print(f"\nAnalysis:")
    print(f"  Decreased concepts: {result['analysis']['top_decreased']}")
    print(f"  Increased concepts: {result['analysis']['top_increased']}")

    print("\nDemo complete!")
