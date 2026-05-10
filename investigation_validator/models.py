"""
Data models for the Investigation Validator.

Defines all dataclasses used in the validation pipeline.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime
import uuid


class ValidationVerdict(Enum):
    """Verdict from a validator agent."""
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIABLE = "unverifiable"
    PARTIALLY_SUPPORTED = "partially_supported"


class SourceType(Enum):
    """Type of source being cited."""
    URL = "url"
    PAPER = "paper"
    DOCUMENTATION = "documentation"
    FILE = "file"
    UNKNOWN = "unknown"


class FilterMode(Enum):
    """How to handle validation results."""
    STRICT = "strict"  # Only supported claims pass
    ANNOTATED = "annotated"  # All claims pass with validation flags
    BALANCED = "balanced"  # Remove unsupported, keep unverifiable with warnings


@dataclass
class Claim:
    """
    A single claim extracted from subagent findings.

    Represents an assertion made by a subagent with its cited source.
    """
    id: str
    text: str  # The claim itself
    source_url: Optional[str]  # The cited source URL
    source_reference: Optional[str]  # Original reference text from findings
    source_type: SourceType
    subagent_id: str  # Which subagent made this claim
    focus_area: str  # The focus area being investigated

    # Post-validation fields
    validated: bool = False
    flagged: bool = False
    flag_reason: Optional[str] = None
    confidence_modifier: float = 1.0
    validation_note: Optional[str] = None
    supporting_evidence: Optional[str] = None

    @classmethod
    def create(cls, text: str, subagent_id: str, focus_area: str,
               source_url: Optional[str] = None, source_reference: Optional[str] = None):
        """Factory method to create a Claim with auto-generated ID."""
        return cls(
            id=f"claim_{uuid.uuid4().hex[:8]}",
            text=text,
            source_url=source_url,
            source_reference=source_reference,
            source_type=cls._infer_source_type(source_url),
            subagent_id=subagent_id,
            focus_area=focus_area,
        )

    @staticmethod
    def _infer_source_type(url: Optional[str]) -> SourceType:
        """Infer source type from URL."""
        if not url:
            return SourceType.UNKNOWN
        url_lower = url.lower()
        if "arxiv" in url_lower or ".pdf" in url_lower:
            return SourceType.PAPER
        if "docs." in url_lower or "documentation" in url_lower or "readme" in url_lower:
            return SourceType.DOCUMENTATION
        if url.startswith("/") or url.startswith("file://"):
            return SourceType.FILE
        return SourceType.URL


@dataclass
class FetchedSource:
    """
    Result of fetching a source URL.

    Contains the extracted text content for validation.
    """
    url: str
    content: str  # Extracted text
    accessible: bool
    error: Optional[str] = None
    fetch_time: datetime = field(default_factory=datetime.now)
    content_type: str = "unknown"  # html, pdf, text, json
    truncated: bool = False
    original_length: int = 0
    from_wayback: bool = False

    @property
    def is_valid(self) -> bool:
        """Check if source was successfully fetched."""
        return self.accessible and len(self.content) > 0


@dataclass
class ValidationResult:
    """
    Result from a single validator agent.

    Contains the verdict and supporting evidence.
    """
    claim_id: str
    verdict: ValidationVerdict
    confidence: float  # 0.0 - 1.0
    supporting_quote: Optional[str] = None
    reasoning: str = ""
    validator_model: str = "haiku"
    elapsed_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def is_supported(self) -> bool:
        return self.verdict in (ValidationVerdict.SUPPORTED, ValidationVerdict.PARTIALLY_SUPPORTED)

    @property
    def is_rejected(self) -> bool:
        return self.verdict == ValidationVerdict.UNSUPPORTED and self.confidence > 0.5

    def to_dict(self) -> dict:
        return {
            "claim_id": self.claim_id,
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "supporting_quote": self.supporting_quote,
            "reasoning": self.reasoning,
            "validator_model": self.validator_model,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }


@dataclass
class ValidatedFindings:
    """
    Container for validated findings from all subagents.

    Used to pass validated data to the synthesis agent.
    """
    original_findings: List[Any]  # Original SubagentResult objects
    claims: List[Claim]
    validation_results: Dict[str, ValidationResult]  # claim_id -> result

    # Statistics
    total_claims: int = 0
    supported_claims: int = 0
    unsupported_claims: int = 0
    unverifiable_claims: int = 0

    # Filtered output
    filtered_findings_text: Optional[str] = None

    def compute_stats(self):
        """Compute validation statistics."""
        self.total_claims = len(self.claims)
        self.supported_claims = sum(
            1 for r in self.validation_results.values()
            if r.verdict == ValidationVerdict.SUPPORTED
        )
        self.unsupported_claims = sum(
            1 for r in self.validation_results.values()
            if r.verdict == ValidationVerdict.UNSUPPORTED
        )
        self.unverifiable_claims = sum(
            1 for r in self.validation_results.values()
            if r.verdict == ValidationVerdict.UNVERIFIABLE
        )

    def to_dict(self) -> dict:
        return {
            "total_claims": self.total_claims,
            "supported_claims": self.supported_claims,
            "unsupported_claims": self.unsupported_claims,
            "unverifiable_claims": self.unverifiable_claims,
            "claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "source_url": c.source_url,
                    "focus_area": c.focus_area,
                    "validated": c.validated,
                    "flagged": c.flagged,
                }
                for c in self.claims
            ],
            "validation_results": {
                k: v.to_dict()
                for k, v in self.validation_results.items()
            },
        }


@dataclass
class ValidationConfig:
    """
    Configuration for the validation pipeline.
    """
    enabled: bool = True
    model: str = "haiku"  # Validator model
    parallel_validators: int = 10
    fetch_timeout_seconds: int = 30
    cache_ttl_hours: int = 24

    # Filtering mode
    filter_mode: FilterMode = FilterMode.BALANCED

    # Thresholds
    min_confidence_to_keep: float = 0.3
    unsupported_threshold: float = 0.7  # Below this confidence = unsupported

    # Performance
    max_claims_per_finding: int = 20
    max_source_chars: int = 50000  # Truncate large sources

    # Timeout for validation phase (separate from source fetching)
    validation_timeout_minutes: int = 5

    # Cache directory - if None, uses default
    cache_dir: Optional[str] = None

    def __post_init__(self) -> None:
        self._validate_int_field("parallel_validators", min_value=1)
        self._validate_int_field("fetch_timeout_seconds", min_value=1)
        self._validate_int_field("cache_ttl_hours", min_value=0)
        self._validate_int_field("max_claims_per_finding", min_value=1)
        self._validate_int_field("max_source_chars", min_value=1)

    def _validate_int_field(self, field_name: str, *, min_value: int) -> None:
        value = getattr(self, field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name} must be an integer, got {type(value).__name__}")
        if value < min_value:
            comparator = ">=" if min_value == 0 else ">"
            threshold = min_value if min_value == 0 else min_value - 1
            raise ValueError(f"{field_name} must be {comparator} {threshold}, got {value}")

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "parallel_validators": self.parallel_validators,
            "fetch_timeout_seconds": self.fetch_timeout_seconds,
            "cache_ttl_hours": self.cache_ttl_hours,
            "filter_mode": self.filter_mode.value,
            "min_confidence_to_keep": self.min_confidence_to_keep,
            "unsupported_threshold": self.unsupported_threshold,
            "max_claims_per_finding": self.max_claims_per_finding,
            "max_source_chars": self.max_source_chars,
            "validation_timeout_minutes": self.validation_timeout_minutes,
            "cache_dir": self.cache_dir,
        }
