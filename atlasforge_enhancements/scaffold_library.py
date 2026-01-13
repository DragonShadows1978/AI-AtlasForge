#!/usr/bin/env python3
"""
Scaffold Library - AtlasForge Enhancement Feature 3.2

A collection of evidence-based scaffolding prompts that can correct or prevent
cognitive bias patterns in Claude's responses.

Scaffolds are injected into prompts when bias patterns are detected.
Each scaffold is designed to address specific bias types while being
minimally intrusive to the main task.

Inspired by phenomenological scaffolding experiments that
showed a 37.5% reduction in bias with appropriate scaffolding.
"""

from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

try:
    from .bias_detector import BiasType
except ImportError:
    from bias_detector import BiasType


# =============================================================================
# SCAFFOLD TYPES
# =============================================================================

class ScaffoldIntensity(Enum):
    """How strongly the scaffold intervenes."""
    SUBTLE = "subtle"  # Light reminder, minimally intrusive
    MODERATE = "moderate"  # Clear guidance without being heavy-handed
    STRONG = "strong"  # Direct intervention for serious bias


class ScaffoldPosition(Enum):
    """Where the scaffold should be injected."""
    PREFIX = "prefix"  # Before the main prompt
    SUFFIX = "suffix"  # After the main prompt
    INLINE = "inline"  # Woven into the prompt


@dataclass
class Scaffold:
    """A scaffolding prompt designed to address bias."""
    id: str
    name: str
    description: str
    targets: List[BiasType]  # Which biases this addresses
    intensity: ScaffoldIntensity
    position: ScaffoldPosition
    template: str  # The actual scaffold text (may have {placeholders})
    effectiveness_score: float  # Historical effectiveness (0.0-1.0)
    usage_count: int  # How many times it's been used
    notes: str  # Additional notes on usage


# =============================================================================
# SCAFFOLD DEFINITIONS
# =============================================================================

# Anti-sycophancy scaffolds
ANTI_SYCOPHANCY_SCAFFOLDS = [
    Scaffold(
        id="syc_001",
        name="Honest Disagreement",
        description="Encourages honest disagreement when warranted",
        targets=[BiasType.SYCOPHANCY],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.PREFIX,
        template="""
Note: Provide honest, objective analysis. If the user's premise contains errors
or their approach could be improved, say so directly. Respectful disagreement
is more valuable than validation.
""".strip(),
        effectiveness_score=0.72,
        usage_count=0,
        notes="Works well for technical discussions"
    ),

    Scaffold(
        id="syc_002",
        name="Devil's Advocate",
        description="Explicitly asks for counterarguments",
        targets=[BiasType.SYCOPHANCY, BiasType.CONFIRMATION],
        intensity=ScaffoldIntensity.STRONG,
        position=ScaffoldPosition.SUFFIX,
        template="""
Before concluding, consider: What arguments against this approach might a
skeptical colleague raise? Include at least one significant counterpoint
in your response.
""".strip(),
        effectiveness_score=0.68,
        usage_count=0,
        notes="Good for design decisions and architectural choices"
    ),

    Scaffold(
        id="syc_003",
        name="Critique Mode",
        description="Frames the task as critical review",
        targets=[BiasType.SYCOPHANCY],
        intensity=ScaffoldIntensity.SUBTLE,
        position=ScaffoldPosition.PREFIX,
        template="""
Approach this as a critical reviewer would, evaluating claims objectively
rather than accepting them at face value.
""".strip(),
        effectiveness_score=0.65,
        usage_count=0,
        notes="Subtle reframing that works across many contexts"
    ),
]

# Anti-overconfidence scaffolds
ANTI_OVERCONFIDENCE_SCAFFOLDS = [
    Scaffold(
        id="conf_001",
        name="Uncertainty Acknowledgment",
        description="Reminds to acknowledge genuine uncertainty",
        targets=[BiasType.OVERCONFIDENCE],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.PREFIX,
        template="""
Express appropriate uncertainty. For claims you're confident about, explain
why. For claims with genuine uncertainty, acknowledge it. Avoid words like
"definitely" or "certainly" unless you have strong justification.
""".strip(),
        effectiveness_score=0.70,
        usage_count=0,
        notes="Effective for technical predictions"
    ),

    Scaffold(
        id="conf_002",
        name="Alternative Consideration",
        description="Requires consideration of alternatives",
        targets=[BiasType.OVERCONFIDENCE, BiasType.ANCHORING],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.SUFFIX,
        template="""
Before finalizing your answer, briefly consider: What alternative approaches
exist? What assumptions are you making? Under what conditions might your
recommendation not apply?
""".strip(),
        effectiveness_score=0.75,
        usage_count=0,
        notes="Strong for design/architecture discussions"
    ),

    Scaffold(
        id="conf_003",
        name="Epistemic Humility",
        description="Encourages epistemic humility",
        targets=[BiasType.OVERCONFIDENCE],
        intensity=ScaffoldIntensity.SUBTLE,
        position=ScaffoldPosition.PREFIX,
        template="""
Calibrate your confidence to your actual knowledge. What you know well,
state confidently. What you're less certain about, express appropriately.
""".strip(),
        effectiveness_score=0.62,
        usage_count=0,
        notes="Good general-purpose scaffold"
    ),
]

# Anti-underconfidence scaffolds
ANTI_UNDERCONFIDENCE_SCAFFOLDS = [
    Scaffold(
        id="under_001",
        name="Directness Encouragement",
        description="Encourages direct, confident statements",
        targets=[BiasType.UNDERCONFIDENCE],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.PREFIX,
        template="""
Be direct and confident in your response. If you know something, state it
clearly. Excessive hedging reduces the value of your expertise. Save
qualifications for genuine uncertainty.
""".strip(),
        effectiveness_score=0.68,
        usage_count=0,
        notes="Good when excessive hedging detected"
    ),

    Scaffold(
        id="under_002",
        name="Expertise Activation",
        description="Reminds of relevant expertise",
        targets=[BiasType.UNDERCONFIDENCE],
        intensity=ScaffoldIntensity.SUBTLE,
        position=ScaffoldPosition.PREFIX,
        template="""
You have extensive knowledge in this area. Draw on it confidently while
being honest about any genuine limitations.
""".strip(),
        effectiveness_score=0.60,
        usage_count=0,
        notes="Subtle confidence boost"
    ),
]

# Focus/verbosity scaffolds
FOCUS_SCAFFOLDS = [
    Scaffold(
        id="focus_001",
        name="Conciseness",
        description="Encourages concise responses",
        targets=[BiasType.VERBOSITY],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.PREFIX,
        template="""
Be concise. Focus on the essential information. Avoid repetition and
unnecessary elaboration. If something can be said in fewer words, do so.
""".strip(),
        effectiveness_score=0.72,
        usage_count=0,
        notes="Effective for reducing bloat"
    ),

    Scaffold(
        id="focus_002",
        name="Task Focus",
        description="Reminds to stay on task",
        targets=[BiasType.TANGENT, BiasType.VERBOSITY],
        intensity=ScaffoldIntensity.MODERATE,
        position=ScaffoldPosition.PREFIX,
        template="""
Stay focused on the specific question or task. Avoid tangents and
tangentially related topics. If additional context would be helpful,
mention it briefly rather than exploring it fully.
""".strip(),
        effectiveness_score=0.65,
        usage_count=0,
        notes="Good for keeping responses targeted"
    ),

    Scaffold(
        id="focus_003",
        name="Mission Alignment",
        description="Reminds to align with mission objectives",
        targets=[BiasType.TANGENT],
        intensity=ScaffoldIntensity.STRONG,
        position=ScaffoldPosition.PREFIX,
        template="""
Focus on what directly serves the mission objectives:
{mission_objectives}

Avoid exploring tangential areas unless they clearly contribute to these goals.
""".strip(),
        effectiveness_score=0.78,
        usage_count=0,
        notes="Requires mission_objectives placeholder"
    ),
]

# Meta-cognitive scaffolds
META_SCAFFOLDS = [
    Scaffold(
        id="meta_001",
        name="Self-Awareness",
        description="Activates meta-cognitive awareness",
        targets=[BiasType.SYCOPHANCY, BiasType.CONFIRMATION, BiasType.ANCHORING],
        intensity=ScaffoldIntensity.STRONG,
        position=ScaffoldPosition.PREFIX,
        template="""
Be aware that you may have cognitive biases affecting your response:
- Tendency to agree with the user (sycophancy)
- Seeking evidence that confirms initial impressions (confirmation bias)
- Over-weighting early information (anchoring)

Actively counteract these tendencies by seeking disconfirming evidence
and challenging your initial impressions.
""".strip(),
        effectiveness_score=0.80,
        usage_count=0,
        notes="Most effective but most intrusive - use for serious bias"
    ),

    Scaffold(
        id="meta_002",
        name="Red Team Thinking",
        description="Activates adversarial perspective",
        targets=[BiasType.OVERCONFIDENCE, BiasType.CONFIRMATION],
        intensity=ScaffoldIntensity.STRONG,
        position=ScaffoldPosition.SUFFIX,
        template="""
Red team your own response: If someone wanted to find flaws in this approach,
what would they point to? Include any significant weaknesses or risks.
""".strip(),
        effectiveness_score=0.75,
        usage_count=0,
        notes="Good for critical decisions"
    ),
]


# =============================================================================
# SCAFFOLD REGISTRY
# =============================================================================

ALL_SCAFFOLDS: List[Scaffold] = (
    ANTI_SYCOPHANCY_SCAFFOLDS +
    ANTI_OVERCONFIDENCE_SCAFFOLDS +
    ANTI_UNDERCONFIDENCE_SCAFFOLDS +
    FOCUS_SCAFFOLDS +
    META_SCAFFOLDS
)

SCAFFOLDS_BY_BIAS: Dict[BiasType, List[Scaffold]] = {}
for scaffold in ALL_SCAFFOLDS:
    for target in scaffold.targets:
        if target not in SCAFFOLDS_BY_BIAS:
            SCAFFOLDS_BY_BIAS[target] = []
        SCAFFOLDS_BY_BIAS[target].append(scaffold)


def get_scaffolds_for_bias(bias_type: BiasType) -> List[Scaffold]:
    """Get all scaffolds that target a specific bias type."""
    return SCAFFOLDS_BY_BIAS.get(bias_type, [])


def get_scaffold_by_id(scaffold_id: str) -> Optional[Scaffold]:
    """Get a specific scaffold by ID."""
    for scaffold in ALL_SCAFFOLDS:
        if scaffold.id == scaffold_id:
            return scaffold
    return None


def get_scaffolds_by_intensity(intensity: ScaffoldIntensity) -> List[Scaffold]:
    """Get all scaffolds of a specific intensity."""
    return [s for s in ALL_SCAFFOLDS if s.intensity == intensity]


def get_most_effective_scaffolds(top_n: int = 5) -> List[Scaffold]:
    """Get the most effective scaffolds by historical score."""
    sorted_scaffolds = sorted(
        ALL_SCAFFOLDS,
        key=lambda s: s.effectiveness_score,
        reverse=True
    )
    return sorted_scaffolds[:top_n]


# =============================================================================
# SCAFFOLD APPLICATION
# =============================================================================

def apply_scaffold(
    scaffold: Scaffold,
    prompt: str,
    context: Optional[Dict] = None
) -> str:
    """
    Apply a scaffold to a prompt.

    Args:
        scaffold: The scaffold to apply
        prompt: The original prompt
        context: Optional context dict for placeholder substitution

    Returns:
        The scaffolded prompt
    """
    # Process template with context
    scaffold_text = scaffold.template
    if context:
        for key, value in context.items():
            placeholder = f"{{{key}}}"
            if placeholder in scaffold_text:
                scaffold_text = scaffold_text.replace(placeholder, str(value))

    # Apply based on position
    if scaffold.position == ScaffoldPosition.PREFIX:
        return f"{scaffold_text}\n\n{prompt}"
    elif scaffold.position == ScaffoldPosition.SUFFIX:
        return f"{prompt}\n\n{scaffold_text}"
    else:  # INLINE
        # For inline, insert after first paragraph
        paragraphs = prompt.split('\n\n')
        if len(paragraphs) > 1:
            return f"{paragraphs[0]}\n\n{scaffold_text}\n\n" + '\n\n'.join(paragraphs[1:])
        return f"{prompt}\n\n{scaffold_text}"


def apply_multiple_scaffolds(
    scaffolds: List[Scaffold],
    prompt: str,
    context: Optional[Dict] = None
) -> str:
    """
    Apply multiple scaffolds to a prompt.

    Scaffolds are applied in order: prefix first, then inline, then suffix.
    """
    prefix_scaffolds = [s for s in scaffolds if s.position == ScaffoldPosition.PREFIX]
    inline_scaffolds = [s for s in scaffolds if s.position == ScaffoldPosition.INLINE]
    suffix_scaffolds = [s for s in scaffolds if s.position == ScaffoldPosition.SUFFIX]

    result = prompt

    # Apply prefix scaffolds
    for scaffold in reversed(prefix_scaffolds):
        result = apply_scaffold(scaffold, result, context)

    # Apply inline scaffolds
    for scaffold in inline_scaffolds:
        result = apply_scaffold(scaffold, result, context)

    # Apply suffix scaffolds
    for scaffold in suffix_scaffolds:
        result = apply_scaffold(scaffold, result, context)

    return result


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Scaffold Library - AtlasForge Enhancement")
    print("=" * 50)

    print(f"\nTotal scaffolds: {len(ALL_SCAFFOLDS)}")

    print("\nScaffolds by bias type:")
    for bias_type, scaffolds in SCAFFOLDS_BY_BIAS.items():
        print(f"  {bias_type.value}: {len(scaffolds)} scaffolds")

    print("\nMost effective scaffolds:")
    for scaffold in get_most_effective_scaffolds(3):
        print(f"  - {scaffold.name}: {scaffold.effectiveness_score:.0%}")
        print(f"    Targets: {[t.value for t in scaffold.targets]}")

    print("\nApplying scaffold to sample prompt...")
    sample_prompt = "Should I use microservices or a monolith for my new project?"

    scaffold = get_scaffold_by_id("conf_002")
    if scaffold:
        scaffolded = apply_scaffold(scaffold, sample_prompt)
        print(f"\nOriginal prompt:\n{sample_prompt}")
        print(f"\nScaffolded prompt:\n{scaffolded}")

    print("\nDemo complete!")
