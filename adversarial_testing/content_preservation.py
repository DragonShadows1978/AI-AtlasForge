"""
Content Preservation Validation - Detect silent data loss in transformations.

This module addresses the class of bugs where operations "succeed" but destroy
the data they're supposed to process. Examples:
- Merge operation replacing content with "merged mission" placeholder
- Transform operation returning "operation completed" instead of actual result
- Aggregate operation losing source data during combination

Key capabilities:
1. Content preservation assertions for all data transformation operations
2. Pre/post content hashing to detect silent data loss
3. Semantic content validation ensuring meaningful output
4. Specific test cases for merge, combine, transform, aggregate operations
5. Content integrity scoring for red team reports

The core principle: If input content disappears in output, that's a failure.
"""

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Dict, Any, Optional, Set, Tuple, Callable
import math


class ContentPreservationType(Enum):
    """Types of content-preserving operations."""
    MERGE = "merge"           # Multiple inputs -> single merged output
    TRANSFORM = "transform"   # Single input -> transformed output
    AGGREGATE = "aggregate"   # Multiple inputs -> summary/aggregate
    COMBINE = "combine"       # Multiple inputs -> combined output
    FILTER = "filter"         # Input -> filtered subset


@dataclass
class PreservationViolation:
    """Records a content preservation violation."""
    operation_type: ContentPreservationType
    severity: str  # "critical", "high", "medium", "low"
    description: str
    input_hashes: List[str]
    output_hash: str
    missing_content: List[str]  # Key terms/phrases that disappeared
    placeholder_detected: Optional[str]  # Detected placeholder pattern
    term_preservation_score: float  # 0.0 - 1.0
    semantic_similarity_score: float  # 0.0 - 1.0
    confidence: float  # Confidence in this being a real violation

    def to_dict(self) -> dict:
        return {
            "operation_type": self.operation_type.value,
            "severity": self.severity,
            "description": self.description,
            "input_hashes": self.input_hashes,
            "output_hash": self.output_hash,
            "missing_content": self.missing_content[:10],  # Limit for readability
            "placeholder_detected": self.placeholder_detected,
            "term_preservation_score": self.term_preservation_score,
            "semantic_similarity_score": self.semantic_similarity_score,
            "confidence": self.confidence
        }


@dataclass
class ContentIntegrityResult:
    """Complete result from content preservation testing."""
    operation_type: ContentPreservationType
    inputs_tested: int
    violations: List[PreservationViolation] = field(default_factory=list)

    # Scores (0.0 - 1.0, higher is better)
    integrity_score: float = 1.0  # Overall content integrity
    term_preservation_score: float = 1.0  # Key terms preserved
    semantic_similarity_score: float = 1.0  # Semantic meaning preserved
    placeholder_score: float = 0.0  # Placeholder detection (lower is better)

    # Metadata
    timestamp: str = ""
    duration_ms: float = 0
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "operation_type": self.operation_type.value,
            "inputs_tested": self.inputs_tested,
            "violations": [v.to_dict() for v in self.violations],
            "scores": {
                "integrity": self.integrity_score,
                "term_preservation": self.term_preservation_score,
                "semantic_similarity": self.semantic_similarity_score,
                "placeholder": self.placeholder_score
            },
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "error": self.error
        }

    @property
    def has_violations(self) -> bool:
        return len(self.violations) > 0

    @property
    def critical_violations(self) -> List[PreservationViolation]:
        return [v for v in self.violations if v.severity == "critical"]


# Common placeholder patterns that indicate data loss
PLACEHOLDER_PATTERNS = [
    # Generic placeholders
    r'^merged\s*(mission|data|content|result|output)?$',
    r'^combined\s*(mission|data|content|result|output)?$',
    r'^aggregated\s*(mission|data|content|result|output)?$',
    r'^transformed\s*(mission|data|content|result|output)?$',
    r'^operation\s+(completed|successful|done)$',
    r'^successfully\s+(merged|combined|transformed|processed)$',
    r'^result\s*:\s*success$',
    r'^data\s+(processed|merged|combined)$',

    # Status-only responses
    r'^ok$',
    r'^done$',
    r'^success$',
    r'^completed$',
    r'^processed$',
    r'^finished$',

    # Template markers
    r'\[.*placeholder.*\]',
    r'\{.*placeholder.*\}',
    r'<.*placeholder.*>',
    r'TODO:?\s*',
    r'FIXME:?\s*',
    r'INSERT\s+.*\s+HERE',

    # Empty-ish content
    r'^n/a$',
    r'^none$',
    r'^null$',
    r'^undefined$',
    r'^empty$',
    r'^-$',
    r'^\s*$',
]

# Compiled placeholder patterns for efficiency
_COMPILED_PLACEHOLDER_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in PLACEHOLDER_PATTERNS
]


class ContentPreservationTester:
    """
    Tests content preservation across data transformation operations.

    The core insight: If input content disappears in output, that's a failure.

    Usage:
        tester = ContentPreservationTester()
        result = tester.test_merge_operation(
            inputs=["mission A details", "mission B details"],
            output="merged mission"  # This would FAIL - placeholder detected
        )

        if result.has_violations:
            print(f"Content lost! Score: {result.integrity_score:.0%}")
    """

    # Weights for computing overall integrity score
    SCORE_WEIGHTS = {
        "term_preservation": 0.35,
        "semantic_similarity": 0.30,
        "no_placeholder": 0.20,  # Weight for NOT having placeholders
        "hash_match_bonus": 0.15
    }

    # Thresholds for severity classification
    SEVERITY_THRESHOLDS = {
        "critical": 0.3,  # < 30% preservation = critical
        "high": 0.5,      # < 50% preservation = high
        "medium": 0.7,    # < 70% preservation = medium
        "low": 0.85       # < 85% preservation = low
    }

    # Minimum content length to consider (avoid false positives on short content)
    MIN_CONTENT_LENGTH = 10

    # Stop words to exclude from term extraction
    # Includes articles, conjunctions, prepositions, pronouns, common verbs, and adverbs
    STOP_WORDS = {
        # Articles
        'a', 'an', 'the',
        # Conjunctions
        'and', 'or', 'but', 'nor', 'yet', 'so',
        # Common prepositions
        'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'from', 'as',
        'over', 'under', 'into', 'onto', 'about', 'after', 'before', 'between',
        'through', 'during', 'without', 'within', 'across', 'against', 'along',
        'among', 'around', 'behind', 'below', 'beneath', 'beside', 'besides',
        'beyond', 'down', 'except', 'inside', 'outside', 'since', 'throughout',
        'toward', 'towards', 'upon', 'up', 'off', 'out',
        # Common verbs
        'is', 'was', 'are', 'were', 'been', 'be', 'being',
        'have', 'has', 'had', 'having',
        'do', 'does', 'did', 'doing',
        'will', 'would', 'could', 'should', 'may', 'might', 'must', 'shall', 'can',
        'get', 'got', 'gets', 'getting',
        # Pronouns
        'this', 'that', 'these', 'those', 'it', 'its',
        'i', 'me', 'my', 'mine', 'myself',
        'you', 'your', 'yours', 'yourself',
        'he', 'him', 'his', 'himself',
        'she', 'her', 'hers', 'herself',
        'we', 'us', 'our', 'ours', 'ourselves',
        'they', 'them', 'their', 'theirs', 'themselves',
        'who', 'whom', 'whose', 'which', 'what', 'where', 'when', 'why', 'how',
        # Common adverbs and determiners
        'just', 'also', 'only', 'even', 'still', 'then', 'than', 'such',
        'very', 'most', 'much', 'more', 'less', 'least',
        'each', 'some', 'any', 'all', 'both', 'other', 'another',
        'no', 'not', 'now', 'here', 'there', 'while', 'like', 'near',
        # Other common words
        'if', 'else', 'because', 'although', 'though', 'unless', 'until',
        'whether', 'either', 'neither', 'however', 'therefore', 'thus'
    }

    def __init__(
        self,
        min_term_preservation: float = 0.5,
        min_semantic_similarity: float = 0.4,
        custom_placeholders: Optional[List[str]] = None
    ):
        """
        Initialize content preservation tester.

        Args:
            min_term_preservation: Minimum acceptable term preservation ratio
            min_semantic_similarity: Minimum acceptable semantic similarity
            custom_placeholders: Additional placeholder patterns to detect
        """
        self.min_term_preservation = min_term_preservation
        self.min_semantic_similarity = min_semantic_similarity

        # Add custom placeholders
        self.placeholder_patterns = list(_COMPILED_PLACEHOLDER_PATTERNS)
        if custom_placeholders:
            for pattern in custom_placeholders:
                self.placeholder_patterns.append(re.compile(pattern, re.IGNORECASE))

    def hash_content(self, content: str) -> str:
        """
        Generate a content hash for exact match detection.

        Creates a case-insensitive SHA256 hash (truncated to 16 chars) for
        comparing content identity. Empty/None content returns "empty".

        Args:
            content: The text content to hash

        Returns:
            16-character hex hash string, or "empty" for empty content
        """
        if not content:
            return "empty"
        normalized = content.strip().lower()
        return hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]

    def extract_key_terms(self, text: str, min_length: int = 3) -> Set[str]:
        """
        Extract significant words from text for content comparison.

        Extracts lowercase alphanumeric words, filtering out stop words
        (articles, prepositions, common verbs) and short words.

        Args:
            text: The text to extract terms from
            min_length: Minimum word length to include (default: 3)

        Returns:
            Set of normalized key terms for comparison
        """
        if not text:
            return set()

        # Normalize text
        text = text.lower()

        # Extract words (alphanumeric sequences)
        words = re.findall(r'\b[a-z0-9]+\b', text)

        # Filter stop words and short words
        key_terms = {
            word for word in words
            if len(word) >= min_length and word not in self.STOP_WORDS
        }

        return key_terms

    def extract_phrases(self, text: str, min_words: int = 2, max_words: int = 4) -> Set[str]:
        """
        Extract significant phrases from text.

        Returns n-grams that may be semantically important.
        """
        if not text:
            return set()

        # Normalize and split into words
        text = text.lower()
        words = re.findall(r'\b[a-z0-9]+\b', text)

        # Filter stop words
        filtered_words = [w for w in words if w not in self.STOP_WORDS and len(w) >= 3]

        # Generate n-grams
        phrases = set()
        for n in range(min_words, max_words + 1):
            for i in range(len(filtered_words) - n + 1):
                phrase = ' '.join(filtered_words[i:i+n])
                phrases.add(phrase)

        return phrases

    def calculate_term_preservation(
        self,
        input_terms: Set[str],
        output_terms: Set[str]
    ) -> Tuple[float, List[str]]:
        """
        Calculate what percentage of input terms appear in output.

        Returns:
            Tuple of (preservation_score, missing_terms)
        """
        if not input_terms:
            return 1.0, []

        preserved = input_terms & output_terms
        missing = list(input_terms - output_terms)

        score = len(preserved) / len(input_terms)
        return score, missing[:20]  # Limit missing terms list

    def calculate_semantic_similarity(
        self,
        input_text: str,
        output_text: str
    ) -> float:
        """
        Calculate semantic similarity using TF-IDF based approach.

        This is a lightweight alternative to embedding-based similarity.
        """
        if not input_text or not output_text:
            return 0.0 if not output_text else 1.0

        # Build term frequency vectors
        input_terms = Counter(self.extract_key_terms(input_text))
        output_terms = Counter(self.extract_key_terms(output_text))

        if not input_terms or not output_terms:
            return 0.0

        # Get all terms
        all_terms = set(input_terms.keys()) | set(output_terms.keys())

        # Compute cosine similarity
        dot_product = sum(
            input_terms.get(term, 0) * output_terms.get(term, 0)
            for term in all_terms
        )

        input_magnitude = math.sqrt(sum(v**2 for v in input_terms.values()))
        output_magnitude = math.sqrt(sum(v**2 for v in output_terms.values()))

        if input_magnitude == 0 or output_magnitude == 0:
            return 0.0

        return dot_product / (input_magnitude * output_magnitude)

    def detect_placeholder(self, text: str) -> Optional[str]:
        """
        Detect if text appears to be a placeholder/generic response.

        Returns the matched placeholder pattern if detected, None otherwise.
        """
        if not text:
            return "empty"

        text = text.strip()

        # Check against known placeholder patterns
        for pattern in self.placeholder_patterns:
            if pattern.search(text):
                return pattern.pattern

        # Check for suspiciously short output (possible placeholder)
        if len(text) < 20 and not re.search(r'[.!?,;:]', text):
            # Short text without punctuation is suspicious
            return f"suspiciously_short:{len(text)}_chars"

        return None

    def calculate_integrity_score(
        self,
        term_preservation: float,
        semantic_similarity: float,
        placeholder_detected: bool,
        hash_match: bool = False
    ) -> float:
        """
        Calculate overall content integrity score.

        Higher score = better content preservation.
        """
        # Component scores
        scores = {
            "term_preservation": term_preservation,
            "semantic_similarity": semantic_similarity,
            "no_placeholder": 0.0 if placeholder_detected else 1.0,
            "hash_match_bonus": 1.0 if hash_match else 0.5  # Bonus for exact match
        }

        # Weighted average
        total = sum(
            scores[key] * self.SCORE_WEIGHTS[key]
            for key in self.SCORE_WEIGHTS
        )

        return min(1.0, max(0.0, total))

    def determine_severity(self, integrity_score: float) -> str:
        """Determine violation severity from integrity score."""
        if integrity_score < self.SEVERITY_THRESHOLDS["critical"]:
            return "critical"
        elif integrity_score < self.SEVERITY_THRESHOLDS["high"]:
            return "high"
        elif integrity_score < self.SEVERITY_THRESHOLDS["medium"]:
            return "medium"
        elif integrity_score < self.SEVERITY_THRESHOLDS["low"]:
            return "low"
        return "info"

    def test_merge_operation(
        self,
        inputs: List[str],
        output: str,
        operation_name: str = "merge"
    ) -> ContentIntegrityResult:
        """
        Test a merge operation for content preservation.

        Verifies that content from all inputs appears in the merged output.

        Args:
            inputs: List of input strings that were merged
            output: The merged output string
            operation_name: Name of the operation for reporting

        Returns:
            ContentIntegrityResult with violations if content was lost
        """
        start_time = datetime.now()

        result = ContentIntegrityResult(
            operation_type=ContentPreservationType.MERGE,
            inputs_tested=len(inputs),
            timestamp=start_time.isoformat()
        )

        if not inputs:
            result.error = "No inputs provided"
            result.success = False
            return result

        # Compute hashes
        input_hashes = [self.hash_content(inp) for inp in inputs]
        output_hash = self.hash_content(output)

        # Check for placeholder in output
        placeholder = self.detect_placeholder(output)
        result.placeholder_score = 1.0 if placeholder else 0.0

        # Combine all input content for comparison
        combined_input = ' '.join(inputs)

        # Extract terms from combined input and output
        input_terms = self.extract_key_terms(combined_input)
        output_terms = self.extract_key_terms(output)

        # Also check phrases
        input_phrases = self.extract_phrases(combined_input)
        output_phrases = self.extract_phrases(output)

        # Calculate preservation scores
        term_score, missing_terms = self.calculate_term_preservation(input_terms, output_terms)
        phrase_score, missing_phrases = self.calculate_term_preservation(input_phrases, output_phrases)

        # Use the more lenient of term/phrase scores
        result.term_preservation_score = max(term_score, phrase_score)

        # Semantic similarity
        result.semantic_similarity_score = self.calculate_semantic_similarity(
            combined_input, output
        )

        # Overall integrity
        result.integrity_score = self.calculate_integrity_score(
            result.term_preservation_score,
            result.semantic_similarity_score,
            placeholder is not None
        )

        # Check for violations
        if placeholder or result.integrity_score < self.min_term_preservation:
            severity = self.determine_severity(result.integrity_score)

            violation = PreservationViolation(
                operation_type=ContentPreservationType.MERGE,
                severity=severity,
                description=f"Content loss detected in {operation_name} operation",
                input_hashes=input_hashes,
                output_hash=output_hash,
                missing_content=missing_terms,
                placeholder_detected=placeholder,
                term_preservation_score=result.term_preservation_score,
                semantic_similarity_score=result.semantic_similarity_score,
                confidence=1.0 if placeholder else 0.8
            )
            result.violations.append(violation)

        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        return result

    def test_transform_operation(
        self,
        input_text: str,
        output_text: str,
        operation_name: str = "transform",
        allow_reduction: bool = False,
        min_preservation: Optional[float] = None
    ) -> ContentIntegrityResult:
        """
        Test a transformation operation for content preservation.

        Args:
            input_text: Original input
            output_text: Transformed output
            operation_name: Name of the operation for reporting
            allow_reduction: If True, allows some content loss (e.g., summarization)
            min_preservation: Override minimum preservation threshold

        Returns:
            ContentIntegrityResult with violations if content was lost
        """
        start_time = datetime.now()

        result = ContentIntegrityResult(
            operation_type=ContentPreservationType.TRANSFORM,
            inputs_tested=1,
            timestamp=start_time.isoformat()
        )

        # Use override threshold if provided
        threshold = min_preservation if min_preservation is not None else self.min_term_preservation
        if allow_reduction:
            # Use a fixed minimum threshold of 0.25 for summarization operations
            # This allows up to 75% content reduction without flagging as violation
            threshold = min(threshold * 0.5, 0.25)

        # Compute hashes
        input_hash = self.hash_content(input_text)
        output_hash = self.hash_content(output_text)
        hash_match = input_hash == output_hash

        # Check for placeholder
        placeholder = self.detect_placeholder(output_text)
        result.placeholder_score = 1.0 if placeholder else 0.0

        # Extract terms
        input_terms = self.extract_key_terms(input_text)
        output_terms = self.extract_key_terms(output_text)

        # Calculate preservation
        result.term_preservation_score, missing_terms = self.calculate_term_preservation(
            input_terms, output_terms
        )
        result.semantic_similarity_score = self.calculate_semantic_similarity(
            input_text, output_text
        )

        # Overall integrity
        result.integrity_score = self.calculate_integrity_score(
            result.term_preservation_score,
            result.semantic_similarity_score,
            placeholder is not None,
            hash_match
        )

        # Check for violations
        if placeholder or result.integrity_score < threshold:
            severity = self.determine_severity(result.integrity_score)

            # Adjust severity if reduction is allowed - downgrade by one level
            if allow_reduction:
                severity_downgrade = {
                    "critical": "high",
                    "high": "medium",
                    "medium": "low",
                    "low": "info"
                }
                severity = severity_downgrade.get(severity, severity)

            violation = PreservationViolation(
                operation_type=ContentPreservationType.TRANSFORM,
                severity=severity,
                description=f"Content loss detected in {operation_name} operation",
                input_hashes=[input_hash],
                output_hash=output_hash,
                missing_content=missing_terms,
                placeholder_detected=placeholder,
                term_preservation_score=result.term_preservation_score,
                semantic_similarity_score=result.semantic_similarity_score,
                confidence=1.0 if placeholder else 0.7
            )
            result.violations.append(violation)

        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        return result

    def test_aggregate_operation(
        self,
        inputs: List[str],
        output: str,
        operation_name: str = "aggregate",
        min_inputs_represented: float = 0.5
    ) -> ContentIntegrityResult:
        """
        Test an aggregation operation for content preservation.

        Aggregations may legitimately summarize/reduce content, but should
        still preserve key concepts from most inputs.

        Args:
            inputs: List of input strings being aggregated
            output: The aggregated output
            operation_name: Name of the operation for reporting
            min_inputs_represented: Minimum fraction of inputs that should be represented

        Returns:
            ContentIntegrityResult
        """
        start_time = datetime.now()

        result = ContentIntegrityResult(
            operation_type=ContentPreservationType.AGGREGATE,
            inputs_tested=len(inputs),
            timestamp=start_time.isoformat()
        )

        if not inputs:
            result.error = "No inputs provided"
            result.success = False
            return result

        # Check for placeholder
        placeholder = self.detect_placeholder(output)
        result.placeholder_score = 1.0 if placeholder else 0.0

        # Check how many inputs are represented in output
        output_terms = self.extract_key_terms(output)
        inputs_represented = 0
        total_term_preservation = 0.0
        all_missing_terms = []

        for inp in inputs:
            input_terms = self.extract_key_terms(inp)
            if input_terms:
                overlap = input_terms & output_terms
                preservation = len(overlap) / len(input_terms)
                total_term_preservation += preservation

                if preservation > 0.2:  # At least 20% terms preserved = represented
                    inputs_represented += 1
                else:
                    # Collect missing terms from underrepresented inputs
                    all_missing_terms.extend(list(input_terms - output_terms)[:5])

        # Calculate scores
        representation_ratio = inputs_represented / len(inputs) if inputs else 0
        result.term_preservation_score = total_term_preservation / len(inputs) if inputs else 0

        # Semantic similarity against combined input
        combined_input = ' '.join(inputs)
        result.semantic_similarity_score = self.calculate_semantic_similarity(
            combined_input, output
        )

        # Integrity score (more lenient for aggregation)
        result.integrity_score = self.calculate_integrity_score(
            result.term_preservation_score,
            result.semantic_similarity_score,
            placeholder is not None
        )

        # Check violations
        if placeholder or representation_ratio < min_inputs_represented:
            severity = "critical" if placeholder else "high"

            violation = PreservationViolation(
                operation_type=ContentPreservationType.AGGREGATE,
                severity=severity,
                description=f"Content loss in {operation_name}: only {inputs_represented}/{len(inputs)} inputs represented",
                input_hashes=[self.hash_content(inp) for inp in inputs],
                output_hash=self.hash_content(output),
                missing_content=all_missing_terms[:20],
                placeholder_detected=placeholder,
                term_preservation_score=result.term_preservation_score,
                semantic_similarity_score=result.semantic_similarity_score,
                confidence=0.9 if placeholder else 0.6
            )
            result.violations.append(violation)

        result.duration_ms = (datetime.now() - start_time).total_seconds() * 1000
        return result

    def test_combine_operation(
        self,
        inputs: List[str],
        output: str,
        operation_name: str = "combine"
    ) -> ContentIntegrityResult:
        """
        Test a combine operation for content preservation.

        Combines are expected to include all input content (like merge but possibly formatted differently).
        """
        # Combine uses the same logic as merge but may have different tolerance
        return self.test_merge_operation(inputs, output, operation_name)

    def run_content_tests(
        self,
        operation_type: ContentPreservationType,
        inputs: List[str],
        output: str,
        operation_name: str = "",
        **kwargs
    ) -> ContentIntegrityResult:
        """
        Run appropriate content preservation tests based on operation type.

        Args:
            operation_type: Type of operation to test
            inputs: Input content(s)
            output: Output content
            operation_name: Name for reporting
            **kwargs: Additional arguments for specific test types

        Returns:
            ContentIntegrityResult
        """
        if not operation_name:
            operation_name = operation_type.value

        if operation_type == ContentPreservationType.MERGE:
            return self.test_merge_operation(inputs, output, operation_name)
        elif operation_type == ContentPreservationType.TRANSFORM:
            return self.test_transform_operation(
                inputs[0] if inputs else "",
                output,
                operation_name,
                **kwargs
            )
        elif operation_type == ContentPreservationType.AGGREGATE:
            return self.test_aggregate_operation(inputs, output, operation_name, **kwargs)
        elif operation_type == ContentPreservationType.COMBINE:
            return self.test_combine_operation(inputs, output, operation_name)
        elif operation_type == ContentPreservationType.FILTER:
            # Filter should have subset relationship
            return self.test_transform_operation(
                inputs[0] if inputs else "",
                output,
                operation_name,
                allow_reduction=True,
                **kwargs
            )
        else:
            # Default to transform
            return self.test_transform_operation(
                ' '.join(inputs),
                output,
                operation_name,
                **kwargs
            )


def check_content_preservation(
    inputs: List[str],
    output: str,
    operation_type: ContentPreservationType = ContentPreservationType.MERGE,
    operation_name: str = ""
) -> ContentIntegrityResult:
    """
    Convenience function to test content preservation.

    Validates that output content preserves key information from inputs.
    Use this for quick validation of transformation operations.

    Args:
        inputs: Input content(s) - list of strings that were transformed
        output: Output content - the result of the transformation
        operation_type: Type of operation (MERGE, TRANSFORM, AGGREGATE, etc.)
        operation_name: Name for reporting and diagnostics

    Returns:
        ContentIntegrityResult with integrity scores and any violations

    Example:
        result = check_content_preservation(
            inputs=["Mission A details", "Mission B details"],
            output="Combined: Mission A and B details",
            operation_type=ContentPreservationType.MERGE
        )
        if result.has_violations:
            print(f"Content loss detected: {result.integrity_score:.0%}")
    """
    tester = ContentPreservationTester()
    return tester.run_content_tests(
        operation_type=operation_type,
        inputs=inputs,
        output=output,
        operation_name=operation_name
    )


# Alias for backward compatibility
test_content_preservation = check_content_preservation


def validate_merge_preserves_content(
    inputs: List[str],
    output: str,
    min_preservation: float = 0.5
) -> Tuple[bool, Optional[str]]:
    """
    Quick validation that a merge preserved content.

    Returns:
        Tuple of (is_valid, error_message)
    """
    tester = ContentPreservationTester(min_term_preservation=min_preservation)
    result = tester.test_merge_operation(inputs, output)

    if result.has_violations:
        critical = result.critical_violations
        if critical:
            return False, f"Critical content loss: {critical[0].description}"
        return False, f"Content preservation below threshold: {result.integrity_score:.0%}"

    return True, None


def validate_transform_preserves_content(
    input_text: str,
    output_text: str,
    min_preservation: float = 0.5
) -> Tuple[bool, Optional[str]]:
    """
    Quick validation that a transform preserved content.

    Returns:
        Tuple of (is_valid, error_message)
    """
    tester = ContentPreservationTester(min_term_preservation=min_preservation)
    result = tester.test_transform_operation(input_text, output_text)

    if result.has_violations:
        critical = result.critical_violations
        if critical:
            return False, f"Critical content loss: {critical[0].description}"
        return False, f"Content preservation below threshold: {result.integrity_score:.0%}"

    return True, None


if __name__ == "__main__":
    # Self-test
    print("Content Preservation Tester - Self Test")
    print("=" * 60)

    tester = ContentPreservationTester()

    # Test 1: Good merge
    print("\n[Test 1] Good merge operation:")
    good_inputs = [
        "Mission Alpha: Implement user authentication with OAuth2",
        "Mission Beta: Create database schema for user profiles"
    ]
    good_output = """Combined Mission: Implement user authentication with OAuth2
    and create database schema for user profiles. This will enable secure login
    and persistent user data storage."""

    result = tester.test_merge_operation(good_inputs, good_output, "mission merge")
    print(f"  Integrity Score: {result.integrity_score:.0%}")
    print(f"  Term Preservation: {result.term_preservation_score:.0%}")
    print(f"  Violations: {len(result.violations)}")
    assert not result.has_violations, "Good merge should not have violations"

    # Test 2: Bad merge (placeholder detected)
    print("\n[Test 2] Bad merge with placeholder:")
    bad_inputs = [
        "Mission Alpha: Implement complex feature with detailed specifications",
        "Mission Beta: Another detailed mission with important content"
    ]
    bad_output = "merged mission"

    result = tester.test_merge_operation(bad_inputs, bad_output, "mission merge")
    print(f"  Integrity Score: {result.integrity_score:.0%}")
    print(f"  Placeholder Detected: {result.violations[0].placeholder_detected if result.violations else 'None'}")
    print(f"  Violations: {len(result.violations)}")
    assert result.has_violations, "Bad merge should have violations"
    assert result.violations[0].severity == "critical", "Placeholder should be critical"

    # Test 3: Transform operation
    print("\n[Test 3] Transform operation:")
    transform_input = "The quick brown fox jumps over the lazy dog"
    transform_output = "operation completed"

    result = tester.test_transform_operation(transform_input, transform_output)
    print(f"  Integrity Score: {result.integrity_score:.0%}")
    print(f"  Violations: {len(result.violations)}")
    assert result.has_violations, "Placeholder transform should have violations"

    # Test 4: Aggregate operation
    print("\n[Test 4] Aggregate operation:")
    agg_inputs = [
        "User authentication requires OAuth2",
        "Database needs PostgreSQL schema",
        "API should follow REST conventions"
    ]
    agg_output = "Summary: Authentication via OAuth2, PostgreSQL database, RESTful API design"

    result = tester.test_aggregate_operation(agg_inputs, agg_output, "requirements summary")
    print(f"  Integrity Score: {result.integrity_score:.0%}")
    print(f"  Term Preservation: {result.term_preservation_score:.0%}")
    print(f"  Violations: {len(result.violations)}")

    # Test 5: Content hash detection
    print("\n[Test 5] Content hash detection:")
    hash1 = tester.hash_content("Hello World")
    hash2 = tester.hash_content("hello world")  # Should be same (case-insensitive)
    hash3 = tester.hash_content("Different content")
    print(f"  Hash 1: {hash1}")
    print(f"  Hash 2: {hash2}")
    print(f"  Hash 3: {hash3}")
    assert hash1 == hash2, "Hashes should be case-insensitive"
    assert hash1 != hash3, "Different content should have different hashes"

    print("\n" + "=" * 60)
    print("Content Preservation self-test PASSED!")
