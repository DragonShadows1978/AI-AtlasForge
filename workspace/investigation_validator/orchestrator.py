"""
Validation Orchestrator - Coordinates the full validation pipeline.

Brings together claim extraction, source fetching, validation, and filtering.
"""

import logging
import time
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass

try:
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
    from .validator_agent import validate_claims_parallel
    from .filter import filter_findings, annotate_claims
except ImportError:
    from models import (
        Claim,
        FetchedSource,
        ValidationResult,
        ValidationConfig,
        ValidatedFindings,
        ValidationVerdict,
    )
    from claim_extractor import extract_claims
    from source_fetcher import SourceFetcher
    from validator_agent import validate_claims_parallel
    from filter import filter_findings, annotate_claims

logger = logging.getLogger("validation_orchestrator")


@dataclass
class ValidationStats:
    """Statistics from a validation run."""
    total_claims: int = 0
    supported: int = 0
    unsupported: int = 0
    unverifiable: int = 0
    partially_supported: int = 0

    unique_urls: int = 0
    urls_accessible: int = 0
    urls_failed: int = 0

    extraction_time: float = 0.0
    fetch_time: float = 0.0
    validation_time: float = 0.0
    total_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total_claims": self.total_claims,
            "supported": self.supported,
            "unsupported": self.unsupported,
            "unverifiable": self.unverifiable,
            "partially_supported": self.partially_supported,
            "unique_urls": self.unique_urls,
            "urls_accessible": self.urls_accessible,
            "urls_failed": self.urls_failed,
            "extraction_time_seconds": self.extraction_time,
            "fetch_time_seconds": self.fetch_time,
            "validation_time_seconds": self.validation_time,
            "total_time_seconds": self.total_time,
        }


class ValidationOrchestrator:
    """
    Orchestrates the complete validation pipeline.

    Flow:
    1. Extract claims from subagent findings
    2. Collect unique URLs to fetch
    3. Fetch source content in parallel
    4. Validate claims against sources in parallel
    5. Filter/annotate findings based on validation
    6. Return validated findings for synthesis
    """

    def __init__(self, config: Optional[ValidationConfig] = None):
        self.config = config or ValidationConfig()
        self.source_fetcher = SourceFetcher(self.config)
        self.stats = ValidationStats()
        self.progress_callback: Optional[Callable[[str], None]] = None

    def validate(
        self,
        subagent_results: List[Any],
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> ValidatedFindings:
        """
        Run the complete validation pipeline.

        Args:
            subagent_results: List of SubagentResult objects from investigation
            progress_callback: Optional callback for progress updates

        Returns:
            ValidatedFindings ready for synthesis
        """
        self.progress_callback = progress_callback
        start_time = time.time()

        self._log("Starting adversarial validation pipeline...")

        # Check if validation is enabled
        if not self.config.enabled:
            self._log("Validation disabled, passing findings through unvalidated")
            return ValidatedFindings(
                original_findings=subagent_results,
                claims=[],
                validation_results={},
            )

        # Step 1: Extract claims
        self._log("Step 1: Extracting claims from findings...")
        extract_start = time.time()
        claims = extract_claims(subagent_results)
        self.stats.extraction_time = time.time() - extract_start
        self.stats.total_claims = len(claims)

        if not claims:
            self._log("No claims extracted, skipping validation")
            return ValidatedFindings(
                original_findings=subagent_results,
                claims=[],
                validation_results={},
            )

        self._log(f"  Extracted {len(claims)} claims from {len(subagent_results)} subagents")

        # Step 2: Collect unique URLs
        urls = list(set(c.source_url for c in claims if c.source_url))
        self.stats.unique_urls = len(urls)
        self._log(f"  Found {len(urls)} unique URLs to fetch")

        # Step 3: Fetch sources
        self._log("Step 2: Fetching source content...")
        fetch_start = time.time()
        sources = self.source_fetcher.fetch_sources(urls)
        self.stats.fetch_time = time.time() - fetch_start

        self.stats.urls_accessible = sum(1 for s in sources.values() if s.accessible)
        self.stats.urls_failed = len(sources) - self.stats.urls_accessible
        self._log(f"  Fetched {self.stats.urls_accessible}/{len(urls)} sources successfully")

        # Step 4: Validate claims
        self._log("Step 3: Running blind validation agents...")
        validation_start = time.time()
        validation_results = validate_claims_parallel(claims, sources, self.config)
        self.stats.validation_time = time.time() - validation_start

        # Update stats
        for result in validation_results.values():
            if result.verdict == ValidationVerdict.SUPPORTED:
                self.stats.supported += 1
            elif result.verdict == ValidationVerdict.UNSUPPORTED:
                self.stats.unsupported += 1
            elif result.verdict == ValidationVerdict.UNVERIFIABLE:
                self.stats.unverifiable += 1
            elif result.verdict == ValidationVerdict.PARTIALLY_SUPPORTED:
                self.stats.partially_supported += 1

        self._log(f"  Validation results: {self.stats.supported} supported, "
                  f"{self.stats.unsupported} unsupported, {self.stats.unverifiable} unverifiable")

        # Step 5: Annotate claims with validation results
        annotated_claims = annotate_claims(claims, validation_results)

        # Step 6: Filter findings based on validation
        self._log("Step 4: Filtering findings based on validation...")
        filtered_text = filter_findings(
            subagent_results,
            annotated_claims,
            validation_results,
            self.config.filter_mode
        )

        # Build result
        validated = ValidatedFindings(
            original_findings=subagent_results,
            claims=annotated_claims,
            validation_results=validation_results,
        )
        validated.compute_stats()
        validated.filtered_findings_text = filtered_text

        self.stats.total_time = time.time() - start_time
        self._log(f"Validation complete in {self.stats.total_time:.1f}s")

        return validated

    def get_stats(self) -> ValidationStats:
        """Get validation statistics."""
        return self.stats

    def _log(self, message: str):
        """Log a message and call progress callback if set."""
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)


def validate_subagent_findings(
    subagent_results: List[Any],
    config: Optional[ValidationConfig] = None,
    progress_callback: Optional[Callable[[str], None]] = None
) -> ValidatedFindings:
    """
    Convenience function to validate subagent findings.

    This is the main entry point for the validation pipeline.

    Args:
        subagent_results: List of SubagentResult objects
        config: Optional validation configuration
        progress_callback: Optional callback for progress updates

    Returns:
        ValidatedFindings ready for synthesis
    """
    orchestrator = ValidationOrchestrator(config)
    return orchestrator.validate(subagent_results, progress_callback)
