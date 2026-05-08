"""
Findings Filter - Process validation results to modify findings before synthesis.

Handles filtering and annotation of claims based on validation verdicts.
"""

import re
import json
import logging
from typing import List, Dict, Any, Optional

try:
    from .models import (
        Claim,
        ValidationResult,
        ValidationVerdict,
        FilterMode,
    )
except ImportError:
    from models import (
        Claim,
        ValidationResult,
        ValidationVerdict,
        FilterMode,
    )

logger = logging.getLogger("findings_filter")


def annotate_claims(
    claims: List[Claim],
    validation_results: Dict[str, ValidationResult]
) -> List[Claim]:
    """
    Annotate claims with their validation results.

    Modifies claim objects in-place to add validation metadata.

    Args:
        claims: List of claims to annotate
        validation_results: Dict mapping claim_id to ValidationResult

    Returns:
        The same claims list, with validation annotations added
    """
    for claim in claims:
        result = validation_results.get(claim.id)

        if not result:
            # No validation result - mark as unvalidated
            claim.validation_note = "No validation performed"
            continue

        if result.verdict == ValidationVerdict.SUPPORTED:
            claim.validated = True
            claim.confidence_modifier = 1.0 + (result.confidence * 0.2)  # Boost
            claim.supporting_evidence = result.supporting_quote
            claim.validation_note = f"Validated (confidence: {result.confidence:.2f})"

        elif result.verdict == ValidationVerdict.PARTIALLY_SUPPORTED:
            claim.validated = True
            claim.confidence_modifier = 0.7 + (result.confidence * 0.2)
            claim.supporting_evidence = result.supporting_quote
            claim.validation_note = f"Partially validated: {result.reasoning}"

        elif result.verdict == ValidationVerdict.UNVERIFIABLE:
            claim.validated = False
            claim.confidence_modifier = 0.5
            claim.validation_note = f"Could not verify: {result.reasoning}"

        elif result.verdict == ValidationVerdict.UNSUPPORTED:
            claim.validated = False
            claim.flagged = True
            claim.flag_reason = result.reasoning
            claim.confidence_modifier = 0.1
            claim.validation_note = f"Not supported by source: {result.reasoning}"

    return claims


def filter_findings(
    subagent_results: List[Any],
    claims: List[Claim],
    validation_results: Dict[str, ValidationResult],
    filter_mode: FilterMode = FilterMode.BALANCED
) -> str:
    """
    Filter and format findings for synthesis.

    Creates a text representation of the findings with validation annotations.

    Args:
        subagent_results: Original SubagentResult objects
        claims: Annotated claim objects
        validation_results: Dict of validation results
        filter_mode: How to handle different validation verdicts

    Returns:
        Formatted findings text for synthesis
    """
    # Group claims by subagent
    claims_by_subagent: Dict[str, List[Claim]] = {}
    for claim in claims:
        if claim.subagent_id not in claims_by_subagent:
            claims_by_subagent[claim.subagent_id] = []
        claims_by_subagent[claim.subagent_id].append(claim)

    # Build filtered output
    output_parts = []

    for result in subagent_results:
        if hasattr(result, 'status') and result.status != "completed":
            continue

        subagent_id = getattr(result, 'subagent_id', 'unknown')
        focus_area = getattr(result, 'focus_area', 'Unknown Area')
        original_findings = getattr(result, 'findings', '')

        subagent_claims = claims_by_subagent.get(subagent_id, [])

        # Format based on filter mode
        if filter_mode == FilterMode.STRICT:
            filtered = _filter_strict(focus_area, subagent_claims, original_findings)
        elif filter_mode == FilterMode.ANNOTATED:
            filtered = _filter_annotated(focus_area, subagent_claims, original_findings)
        else:  # BALANCED
            filtered = _filter_balanced(focus_area, subagent_claims, original_findings)

        if filtered:
            output_parts.append(filtered)

    return "\n\n".join(output_parts)


def _filter_strict(focus_area: str, claims: List[Claim], original: str) -> str:
    """
    Strict filtering - only include supported claims.

    Removes any unsupported or unverifiable claims entirely.
    """
    supported_claims = [c for c in claims if c.validated and not c.flagged]

    if not supported_claims:
        return f"### {focus_area}\n\n*No verified claims available for this area.*"

    output = f"### {focus_area}\n\n"
    output += "**Verified Findings:**\n\n"

    for claim in supported_claims:
        output += f"- {claim.text}\n"
        if claim.supporting_evidence:
            output += f"  - *Evidence: \"{claim.supporting_evidence[:200]}...\"*\n"

    return output


def _filter_annotated(focus_area: str, claims: List[Claim], original: str) -> str:
    """
    Annotated filtering - include all claims with validation markers.

    All claims pass through but are marked with their validation status.
    """
    if not claims:
        # Fall back to original if no claims extracted
        return f"### {focus_area}\n\n{original}"

    output = f"### {focus_area}\n\n"

    for claim in claims:
        verdict_icon = _get_verdict_icon(claim)
        output += f"{verdict_icon} {claim.text}\n"

        if claim.validation_note:
            output += f"   *{claim.validation_note}*\n"

    return output


def _filter_balanced(focus_area: str, claims: List[Claim], original: str) -> str:
    """
    Balanced filtering - remove unsupported, warn about unverifiable.

    - Supported claims: Include normally
    - Partially supported: Include with caveat
    - Unverifiable: Include with warning
    - Unsupported: Remove or mark as disputed
    """
    if not claims:
        return f"### {focus_area}\n\n{original}"

    output = f"### {focus_area}\n\n"

    # Categorize claims
    verified = []
    partial = []
    unverified = []
    disputed = []

    for claim in claims:
        result_id = claim.id
        if claim.validated and not claim.flagged:
            if claim.confidence_modifier >= 0.9:
                verified.append(claim)
            else:
                partial.append(claim)
        elif claim.flagged:
            disputed.append(claim)
        else:
            unverified.append(claim)

    # Output verified claims
    if verified:
        output += "**Verified:**\n"
        for c in verified:
            output += f"- {c.text}\n"
        output += "\n"

    # Output partially verified
    if partial:
        output += "**Partially Verified:**\n"
        for c in partial:
            output += f"- {c.text} *(caveat: {c.validation_note})*\n"
        output += "\n"

    # Output unverified with warning
    if unverified:
        output += "**Unverified (treat with caution):**\n"
        for c in unverified:
            output += f"- {c.text}\n"
        output += "\n"

    # Note disputed claims
    if disputed:
        output += "**Disputed (sources do not support):**\n"
        for c in disputed:
            output += f"- ~~{c.text}~~ *({c.flag_reason})*\n"
        output += "\n"

    return output


def _get_verdict_icon(claim: Claim) -> str:
    """Get an icon representing the claim's validation status."""
    if claim.validated and not claim.flagged:
        if claim.confidence_modifier >= 0.9:
            return "✅"  # Verified
        else:
            return "🔶"  # Partially verified
    elif claim.flagged:
        return "❌"  # Disputed
    else:
        return "⚠️"  # Unverified


def generate_validation_summary(
    claims: List[Claim],
    validation_results: Dict[str, ValidationResult]
) -> str:
    """
    Generate a summary of validation results.

    Useful for including in the synthesis prompt to inform the
    synthesis agent about what was validated.
    """
    total = len(claims)
    if total == 0:
        return "No claims were extracted for validation."

    supported = sum(1 for c in claims if c.validated and not c.flagged)
    disputed = sum(1 for c in claims if c.flagged)
    unverified = total - supported - disputed

    support_rate = (supported / total) * 100 if total > 0 else 0

    summary = f"""
## Validation Summary

- **Total claims analyzed:** {total}
- **Verified by sources:** {supported} ({support_rate:.0f}%)
- **Disputed (unsupported):** {disputed}
- **Unverifiable:** {unverified}

"""

    if disputed > 0:
        summary += "### Disputed Claims (removed from findings)\n\n"
        for claim in claims:
            if claim.flagged:
                summary += f"- {claim.text[:100]}... - *{claim.flag_reason}*\n"

    return summary


def filter_json_findings(
    findings_json: Dict,
    claims: List[Claim],
    validation_results: Dict[str, ValidationResult],
    filter_mode: FilterMode = FilterMode.BALANCED
) -> Dict:
    """
    Filter JSON-structured findings.

    Modifies the JSON structure to remove or annotate invalid claims.

    Args:
        findings_json: Original JSON findings structure
        claims: Annotated claims
        validation_results: Validation results
        filter_mode: Filter mode

    Returns:
        Modified JSON with validation applied
    """
    result = findings_json.copy()

    # Create lookup of claims by text
    claim_lookup = {c.text: c for c in claims}

    # Filter key_findings
    if "key_findings" in result:
        filtered_findings = []
        for finding in result["key_findings"]:
            if isinstance(finding, str):
                claim = claim_lookup.get(finding)
                if claim:
                    if filter_mode == FilterMode.STRICT:
                        if claim.validated and not claim.flagged:
                            filtered_findings.append(finding)
                    elif filter_mode == FilterMode.ANNOTATED:
                        icon = _get_verdict_icon(claim)
                        filtered_findings.append(f"{icon} {finding}")
                    else:  # BALANCED
                        if not claim.flagged:
                            filtered_findings.append(finding)
                else:
                    # Keep findings without extracted claims
                    filtered_findings.append(finding)

        result["key_findings"] = filtered_findings

    # Add validation metadata
    result["_validation"] = {
        "applied": True,
        "filter_mode": filter_mode.value,
        "total_claims": len(claims),
        "validated": sum(1 for c in claims if c.validated),
        "flagged": sum(1 for c in claims if c.flagged),
    }

    return result
