"""
Investigation Validator - Adversarial Citation Validation System

Pre-synthesis fact-checking for investigation subagents.
Blind validators verify that cited sources support the claims made.
"""

from .models import (
    Claim,
    FetchedSource,
    ValidationResult,
    ValidationConfig,
    ValidatedFindings,
    ValidationVerdict,
)
from .claim_extractor import extract_claims
from .source_fetcher import SourceFetcher
from .validator_agent import validate_claim, validate_claims_parallel
from .orchestrator import ValidationOrchestrator
from .filter import filter_findings

__all__ = [
    # Models
    "Claim",
    "FetchedSource",
    "ValidationResult",
    "ValidationConfig",
    "ValidatedFindings",
    "ValidationVerdict",
    # Functions
    "extract_claims",
    "SourceFetcher",
    "validate_claim",
    "validate_claims_parallel",
    "ValidationOrchestrator",
    "filter_findings",
]
