#!/usr/bin/env python3
"""
Bias Detector - AtlasForge Enhancement Feature 3.1

Detects common LLM bias patterns in Claude's responses.
Identifies patterns like sycophancy, anchoring, overconfidence, etc.

Used by the scaffold system to determine when corrective scaffolding is needed.

Inspired by attention manipulation experiments
which showed that how you prompt dramatically affects outcomes.
"""

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


# =============================================================================
# BIAS PATTERNS
# =============================================================================

class BiasType(Enum):
    """Types of cognitive biases we can detect."""
    SYCOPHANCY = "sycophancy"  # Excessive agreement/praise
    ANCHORING = "anchoring"  # Over-reliance on initial information
    OVERCONFIDENCE = "overconfidence"  # Unwarranted certainty
    UNDERCONFIDENCE = "underconfidence"  # Excessive hedging
    RECENCY = "recency"  # Over-weighting recent information
    CONFIRMATION = "confirmation"  # Seeking confirming evidence
    AUTHORITY = "authority"  # Over-deferring to authority
    VERBOSITY = "verbosity"  # Excessive elaboration
    TANGENT = "tangent"  # Going off-topic


@dataclass
class BiasIndicator:
    """An indicator that suggests a bias pattern."""
    pattern: str  # Regex pattern to match
    weight: float  # How strongly this indicates the bias (0.0-1.0)
    description: str  # What this pattern indicates


@dataclass
class BiasDetection:
    """A detected bias in text."""
    bias_type: BiasType
    confidence: float  # 0.0-1.0
    evidence: List[str]  # Matched text fragments
    indicators_matched: int
    recommendation: str


# =============================================================================
# INDICATOR DEFINITIONS
# =============================================================================

BIAS_INDICATORS: Dict[BiasType, List[BiasIndicator]] = {
    BiasType.SYCOPHANCY: [
        BiasIndicator(
            r"(?:you'?re|that'?s)\s+(?:absolutely|completely|totally)\s+(?:right|correct)",
            0.8,
            "Strong agreement phrases"
        ),
        BiasIndicator(
            r"(?:great|excellent|wonderful|brilliant)\s+(?:question|point|idea|observation)",
            0.7,
            "Excessive praise for user input"
        ),
        BiasIndicator(
            r"I\s+(?:completely|totally|absolutely)\s+agree",
            0.9,
            "Total agreement statements"
        ),
        BiasIndicator(
            r"(?:couldn't|could\s+not)\s+(?:agree|have\s+said\s+it)\s+(?:more|better)",
            0.8,
            "Emphatic agreement"
        ),
        BiasIndicator(
            r"(?:exactly|precisely)\s+(?:right|what\s+I\s+(?:was\s+)?think)",
            0.7,
            "Echo chamber responses"
        ),
    ],

    BiasType.OVERCONFIDENCE: [
        BiasIndicator(
            r"(?:definitely|certainly|absolutely|undoubtedly|clearly)\s+(?:is|will|should)",
            0.6,
            "Strong certainty language"
        ),
        BiasIndicator(
            r"(?:there'?s\s+)?no\s+(?:doubt|question)\s+(?:that|about)",
            0.8,
            "Eliminating uncertainty"
        ),
        BiasIndicator(
            r"(?:always|never|everyone|no\s+one)\s+(?:should|must|will)",
            0.7,
            "Universal statements"
        ),
        BiasIndicator(
            r"(?:the\s+)?(?:only|best|correct|right)\s+(?:way|approach|solution)",
            0.8,
            "Single solution claims"
        ),
        BiasIndicator(
            r"(?:this\s+)?(?:will\s+)?(?:definitely|certainly)\s+(?:work|solve|fix)",
            0.7,
            "Guaranteed success claims"
        ),
    ],

    BiasType.UNDERCONFIDENCE: [
        BiasIndicator(
            r"(?:I\s+)?(?:think|believe|suppose|guess)\s+(?:that\s+)?(?:maybe|perhaps)",
            0.6,
            "Double hedging"
        ),
        BiasIndicator(
            r"I'?m\s+not\s+(?:sure|certain|confident)\s+(?:but|if|whether)",
            0.7,
            "Uncertainty disclaimers"
        ),
        BiasIndicator(
            r"(?:might|could|may)\s+(?:possibly|potentially|perhaps)",
            0.6,
            "Stacked probability modifiers"
        ),
        BiasIndicator(
            r"(?:don't|do\s+not)\s+(?:quote|hold)\s+me\s+(?:on|to)\s+(?:this|that)",
            0.8,
            "Responsibility disclaimers"
        ),
        BiasIndicator(
            r"(?:take\s+)?(?:this|it)\s+with\s+a\s+grain\s+of\s+salt",
            0.7,
            "Credibility disclaimers"
        ),
    ],

    BiasType.VERBOSITY: [
        BiasIndicator(
            r"(?:in\s+other\s+words|to\s+put\s+it\s+(?:another|differently)|that\s+is\s+to\s+say)",
            0.5,
            "Rephrasing markers"
        ),
        BiasIndicator(
            r"(?:let\s+me\s+)?(?:elaborate|explain|expand)\s+(?:on|further|more)",
            0.4,
            "Expansion markers"
        ),
        BiasIndicator(
            r"(?:as\s+I\s+(?:mentioned|said|noted))\s+(?:earlier|before|previously|above)",
            0.5,
            "Self-reference to prior content"
        ),
    ],

    BiasType.TANGENT: [
        BiasIndicator(
            r"(?:while\s+)?(?:we'?re|I'?m)\s+(?:on|at)\s+(?:the\s+)?(?:topic|subject|it)",
            0.6,
            "Topic shift markers"
        ),
        BiasIndicator(
            r"(?:as\s+)?(?:a|an)\s+(?:side\s+)?(?:note|aside|tangent)",
            0.8,
            "Explicit tangent markers"
        ),
        BiasIndicator(
            r"(?:this|it)\s+(?:reminds|makes)\s+me\s+(?:of|think)",
            0.5,
            "Association drift"
        ),
        BiasIndicator(
            r"(?:speaking|talking)\s+of\s+(?:which|\w+)",
            0.6,
            "Topic pivot markers"
        ),
    ],

    BiasType.AUTHORITY: [
        BiasIndicator(
            r"(?:according\s+to|as\s+(?:per|\w+\s+said))\s+(?:the\s+)?(?:documentation|official|standard)",
            0.4,
            "Authority citation (neutral - may be appropriate)"
        ),
        BiasIndicator(
            r"(?:the\s+)?(?:experts?|professionals?|authority|authorities)\s+(?:say|recommend|suggest)",
            0.5,
            "Appeal to expert authority"
        ),
        BiasIndicator(
            r"(?:everyone|all\s+(?:the\s+)?(?:experts?|developers?))\s+(?:knows?|agrees?|uses?)",
            0.7,
            "Appeal to consensus"
        ),
    ],

    BiasType.CONFIRMATION: [
        BiasIndicator(
            r"(?:this|that)\s+(?:confirms?|validates?|proves?|supports?)\s+(?:that|what)",
            0.5,
            "Confirmation language"
        ),
        BiasIndicator(
            r"(?:as\s+)?(?:you|we)\s+(?:expected|predicted|thought|suspected)",
            0.6,
            "Expectation confirmation"
        ),
        BiasIndicator(
            r"(?:just\s+)?(?:as|like)\s+(?:I|you|we)\s+(?:said|thought|expected)",
            0.6,
            "Prior belief validation"
        ),
    ],

    BiasType.ANCHORING: [
        BiasIndicator(
            r"(?:based\s+on|given|considering)\s+(?:the\s+)?(?:initial|original|first)",
            0.5,
            "Anchoring to initial info"
        ),
        BiasIndicator(
            r"(?:starting|beginning)\s+(?:from|with)\s+(?:the|your|that)",
            0.4,
            "Start point emphasis"
        ),
    ],
}


# =============================================================================
# DETECTION FUNCTIONS
# =============================================================================

def detect_bias_patterns(text: str) -> List[BiasDetection]:
    """
    Detect bias patterns in text.

    Args:
        text: The text to analyze

    Returns:
        List of BiasDetection objects, sorted by confidence
    """
    detections = []
    text_lower = text.lower()

    for bias_type, indicators in BIAS_INDICATORS.items():
        matches = []
        total_weight = 0.0
        matched_count = 0

        for indicator in indicators:
            for match in re.finditer(indicator.pattern, text_lower):
                matches.append(match.group())
                total_weight += indicator.weight
                matched_count += 1

        if matched_count > 0:
            # Calculate confidence (bounded)
            confidence = min(total_weight / (matched_count * 0.5), 1.0)

            # Normalize by text length (longer text naturally has more matches)
            length_factor = min(len(text) / 1000, 3.0)  # Cap at 3x
            adjusted_confidence = confidence / length_factor if length_factor > 1 else confidence

            # Only report if confidence is significant
            if adjusted_confidence > 0.2:
                detection = BiasDetection(
                    bias_type=bias_type,
                    confidence=round(adjusted_confidence, 3),
                    evidence=matches[:5],  # Top 5 matches
                    indicators_matched=matched_count,
                    recommendation=_get_recommendation(bias_type)
                )
                detections.append(detection)

    # Sort by confidence
    detections.sort(key=lambda d: d.confidence, reverse=True)
    return detections


def _get_recommendation(bias_type: BiasType) -> str:
    """Get recommendation for addressing a bias type."""
    recommendations = {
        BiasType.SYCOPHANCY: "Consider whether you're truly agreeing or just being agreeable. Challenge assumptions when appropriate.",
        BiasType.OVERCONFIDENCE: "Add appropriate uncertainty. Consider alternative viewpoints and edge cases.",
        BiasType.UNDERCONFIDENCE: "Be more direct. If you have knowledge, share it confidently while acknowledging real limitations.",
        BiasType.VERBOSITY: "Be more concise. Focus on essential information without repetition.",
        BiasType.TANGENT: "Stay focused on the main topic. Save tangential ideas for follow-up if relevant.",
        BiasType.AUTHORITY: "Evaluate claims on their merits, not just source authority. Cite specific reasoning.",
        BiasType.CONFIRMATION: "Actively seek disconfirming evidence. Consider what would change your conclusion.",
        BiasType.ANCHORING: "Consider the problem fresh. Don't over-weight initial information.",
        BiasType.RECENCY: "Balance recent and historical information appropriately.",
    }
    return recommendations.get(bias_type, "Review response for this bias pattern.")


def get_overall_bias_score(detections: List[BiasDetection]) -> Tuple[float, str]:
    """
    Calculate an overall bias score and summary.

    Args:
        detections: List of detected biases

    Returns:
        Tuple of (score 0.0-1.0, summary string)
    """
    if not detections:
        return 0.0, "No significant bias patterns detected"

    # Weight by confidence
    total_bias = sum(d.confidence for d in detections)
    max_possible = len(BiasType) * 1.0  # Maximum if all biases at 1.0 confidence

    score = min(total_bias / 3.0, 1.0)  # Normalize (3+ high-confidence biases = max)

    # Generate summary
    top_biases = [d.bias_type.value for d in detections[:3]]
    summary = f"Detected {len(detections)} bias patterns. Top concerns: {', '.join(top_biases)}"

    return round(score, 3), summary


def analyze_response(text: str) -> Dict:
    """
    Complete bias analysis of a response.

    Args:
        text: The response to analyze

    Returns:
        Dict with full analysis results
    """
    detections = detect_bias_patterns(text)
    score, summary = get_overall_bias_score(detections)

    # Determine if scaffolding is recommended
    needs_scaffolding = score > 0.3 or any(d.confidence > 0.6 for d in detections)

    # Identify priority biases to address
    priority_biases = [
        {
            'type': d.bias_type.value,
            'confidence': d.confidence,
            'recommendation': d.recommendation
        }
        for d in detections if d.confidence > 0.4
    ][:3]

    return {
        'overall_score': score,
        'summary': summary,
        'needs_scaffolding': needs_scaffolding,
        'detections': [
            {
                'type': d.bias_type.value,
                'confidence': d.confidence,
                'evidence_count': len(d.evidence),
                'evidence_sample': d.evidence[:2],
                'recommendation': d.recommendation
            }
            for d in detections
        ],
        'priority_biases': priority_biases,
        'text_length': len(text)
    }


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Bias Detector - AtlasForge Enhancement")
    print("=" * 50)

    # Test texts with different bias patterns
    test_texts = {
        "Sycophantic response": """
        You're absolutely right! That's an excellent question and your intuition is brilliant.
        I couldn't agree more with your analysis. Your approach is exactly what I was thinking.
        """,

        "Overconfident response": """
        This will definitely work and is undoubtedly the best solution. There's no question
        that this is the only correct approach. Everyone knows this is the right way to do it.
        This will certainly solve all your problems without any issues.
        """,

        "Underconfident response": """
        I think maybe perhaps this could possibly work, but I'm not sure. Don't quote me on this,
        but it might potentially help. Take this with a grain of salt - I'm not certain if this
        is right. It could maybe be useful, I suppose.
        """,

        "Balanced response": """
        Based on my analysis, this approach should work well for your use case. There are some
        trade-offs to consider: better performance but slightly more complexity. You might want
        to test it in your environment to verify. Let me know if you'd like to explore alternatives.
        """
    }

    for name, text in test_texts.items():
        print(f"\n{'='*50}")
        print(f"Testing: {name}")
        print("-" * 50)

        analysis = analyze_response(text)
        print(f"Overall bias score: {analysis['overall_score']}")
        print(f"Summary: {analysis['summary']}")
        print(f"Needs scaffolding: {analysis['needs_scaffolding']}")

        if analysis['detections']:
            print("Detections:")
            for d in analysis['detections'][:3]:
                print(f"  - {d['type']}: {d['confidence']:.2f} confidence")
                print(f"    Evidence: {d['evidence_sample']}")

    print("\n" + "=" * 50)
    print("Demo complete!")
