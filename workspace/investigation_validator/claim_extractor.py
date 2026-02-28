"""
Claim Extractor - Extract claims with citations from subagent findings.

Takes raw subagent findings and extracts individual claims that can be validated.
"""

import json
import re
import logging
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlparse

try:
    from .models import Claim, SourceType
except ImportError:
    from models import Claim, SourceType

logger = logging.getLogger("claim_extractor")


def extract_claims(subagent_results: List[Any]) -> List[Claim]:
    """
    Extract all claims from subagent findings.

    Args:
        subagent_results: List of SubagentResult objects from investigation

    Returns:
        List of Claim objects ready for validation
    """
    all_claims = []

    for result in subagent_results:
        if hasattr(result, 'status') and result.status != "completed":
            continue

        subagent_id = getattr(result, 'subagent_id', 'unknown')
        focus_area = getattr(result, 'focus_area', 'unknown')
        findings = getattr(result, 'findings', '')

        claims = _extract_claims_from_text(findings, subagent_id, focus_area)
        all_claims.extend(claims)

    logger.info(f"Extracted {len(all_claims)} claims from {len(subagent_results)} subagent results")
    return all_claims


def _extract_claims_from_text(text: str, subagent_id: str, focus_area: str) -> List[Claim]:
    """
    Extract claims from a single subagent's findings text.

    Handles both JSON-structured and free-text findings.
    """
    claims = []

    # Try to parse as JSON first
    parsed_json = _try_parse_json(text)

    if parsed_json:
        claims.extend(_extract_from_json(parsed_json, subagent_id, focus_area))
    else:
        claims.extend(_extract_from_freetext(text, subagent_id, focus_area))

    return claims


def _try_parse_json(text: str) -> Optional[Dict]:
    """
    Safely parse JSON from text, handling various formats.

    Uses bracket matching with depth tracking to avoid invalid JSON from
    nested structures (safer than first '{' to last '}').
    """
    if not text:
        return None

    # Method 1: Try direct parse (ideal case)
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass

    # Method 2: Try extracting from code block (```json ... ```)
    json_match = re.search(r'```json\s*(.*?)\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Method 3: Use bracket matching with depth tracking
    start_idx = text.find('{')
    if start_idx < 0:
        return None

    depth = 0
    in_string = False
    escape_next = False
    end_idx = -1

    for i, char in enumerate(text[start_idx:], start=start_idx):
        if escape_next:
            escape_next = False
            continue

        if char == '\\' and in_string:
            escape_next = True
            continue

        if char == '"' and not escape_next:
            in_string = not in_string
            continue

        if not in_string:
            if char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    end_idx = i + 1
                    break

    if end_idx > start_idx:
        candidate = text[start_idx:end_idx]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            logger.debug(f"Bracket-matched JSON invalid in claim_extractor")

    return None


def _extract_from_json(data: Dict, subagent_id: str, focus_area: str) -> List[Claim]:
    """Extract claims from JSON-structured findings."""
    claims = []
    sources = _extract_sources(data)

    # Extract from key_findings
    key_findings = data.get("key_findings", [])
    if isinstance(key_findings, list):
        for i, finding in enumerate(key_findings):
            if isinstance(finding, str):
                # Match finding to most relevant source
                source_url, source_ref = _match_claim_to_source(finding, sources)
                claim = Claim.create(
                    text=finding,
                    subagent_id=subagent_id,
                    focus_area=focus_area,
                    source_url=source_url,
                    source_reference=source_ref,
                )
                claims.append(claim)

    # Extract from insights (often contains factual claims)
    insights = data.get("insights", "")
    if isinstance(insights, str) and insights:
        insight_claims = _split_into_claims(insights)
        for claim_text in insight_claims:
            source_url, source_ref = _match_claim_to_source(claim_text, sources)
            claim = Claim.create(
                text=claim_text,
                subagent_id=subagent_id,
                focus_area=focus_area,
                source_url=source_url,
                source_reference=source_ref,
            )
            claims.append(claim)

    # Extract from recommendations (may contain factual assertions)
    recommendations = data.get("recommendations", [])
    if isinstance(recommendations, list):
        for rec in recommendations:
            if isinstance(rec, str) and _is_factual_claim(rec):
                source_url, source_ref = _match_claim_to_source(rec, sources)
                claim = Claim.create(
                    text=rec,
                    subagent_id=subagent_id,
                    focus_area=focus_area,
                    source_url=source_url,
                    source_reference=source_ref,
                )
                claims.append(claim)

    return claims


def _extract_sources(data: Dict) -> List[Dict[str, str]]:
    """Extract all sources from JSON findings."""
    sources = []

    # Standard sources field
    raw_sources = data.get("sources", [])
    if isinstance(raw_sources, list):
        for src in raw_sources:
            if isinstance(src, dict):
                sources.append({
                    "url": src.get("reference", src.get("url", "")),
                    "type": src.get("type", "web"),
                    "relevance": src.get("relevance", ""),
                })
            elif isinstance(src, str):
                sources.append({"url": src, "type": "web", "relevance": ""})

    return sources


def _match_claim_to_source(claim_text: str, sources: List[Dict]) -> Tuple[Optional[str], Optional[str]]:
    """
    Match a claim to its most likely source.

    Uses keyword matching between claim text and source relevance descriptions.
    """
    if not sources:
        return None, None

    # Simple heuristic: find source with most keyword overlap
    claim_words = set(claim_text.lower().split())

    best_match = None
    best_score = 0

    for src in sources:
        relevance = src.get("relevance", "").lower()
        url = src.get("url", "")

        # Score based on word overlap with relevance
        relevance_words = set(relevance.split())
        overlap = len(claim_words & relevance_words)

        # Bonus for URL keywords in claim
        url_parts = url.lower().replace("/", " ").replace("-", " ").replace("_", " ")
        url_words = set(url_parts.split())
        overlap += len(claim_words & url_words) * 0.5

        if overlap > best_score:
            best_score = overlap
            best_match = src

    if best_match and best_score > 0:
        return best_match.get("url"), best_match.get("relevance")

    # Default to first source if no good match
    if sources:
        return sources[0].get("url"), sources[0].get("relevance")

    return None, None


def _extract_from_freetext(text: str, subagent_id: str, focus_area: str) -> List[Claim]:
    """Extract claims from free-text findings."""
    claims = []

    # Find URLs in text
    urls = _extract_urls(text)

    # Split text into sentences/claims
    sentences = _split_into_claims(text)

    for sentence in sentences:
        if _is_factual_claim(sentence):
            # Match sentence to closest URL by proximity
            source_url = _find_nearest_url(sentence, text, urls)
            claim = Claim.create(
                text=sentence,
                subagent_id=subagent_id,
                focus_area=focus_area,
                source_url=source_url,
            )
            claims.append(claim)

    return claims


def _is_valid_url(url: str) -> bool:
    """
    Validate a URL for safety and correctness.

    Args:
        url: URL to validate

    Returns:
        True if URL is valid and safe
    """
    if not url:
        return False

    # Must start with http:// or https://
    if not url.startswith("http://") and not url.startswith("https://"):
        return False

    # Dangerous characters that should never be in URLs (potential injection)
    dangerous_chars = [';', '|', '&', '`', '\n', '\r', '\x00']
    for char in dangerous_chars:
        if char in url:
            logger.debug(f"Rejected URL with dangerous character: {url[:50]}")
            return False

    try:
        parsed = urlparse(url)

        # Must have valid scheme and netloc
        if parsed.scheme not in ('http', 'https'):
            return False
        if not parsed.netloc:
            return False

        # Netloc should look like a domain (basic check)
        netloc = parsed.netloc.strip()
        if not netloc or '.' not in netloc:
            # Allow localhost for testing
            if netloc not in ('localhost', '127.0.0.1'):
                return False

        return True

    except Exception:
        return False


def _extract_urls(text: str) -> List[str]:
    """
    Extract and validate all URLs from text.

    Filters out malformed URLs and those with trailing punctuation.
    """
    # Pattern to match URLs - be more conservative to avoid trailing punctuation
    url_pattern = r'https?://[^\s<>"\')\]]+[^\s<>"\')\].,!?;:]'
    raw_urls = re.findall(url_pattern, text)

    # Validate and clean each URL
    valid_urls = []
    for url in raw_urls:
        # Strip common trailing punctuation that regex might capture
        url = url.rstrip('.,!?;:)]\'"')

        # Validate the cleaned URL
        if _is_valid_url(url):
            valid_urls.append(url)
        else:
            logger.debug(f"Filtered invalid URL: {url[:80]}")

    return valid_urls


def _split_into_claims(text: str) -> List[str]:
    """Split text into individual claim-like sentences."""
    # Clean up text
    text = re.sub(r'\n+', ' ', text)
    text = re.sub(r'\s+', ' ', text)

    # Split on sentence boundaries
    sentences = re.split(r'(?<=[.!?])\s+', text)

    # Filter and clean
    claims = []
    for s in sentences:
        s = s.strip()
        # Skip very short or very long sentences
        if len(s) < 20 or len(s) > 500:
            continue
        # Skip questions
        if s.endswith('?'):
            continue
        claims.append(s)

    return claims


def _is_factual_claim(text: str) -> bool:
    """
    Determine if text is a factual claim worth validating.

    Filters out opinions, questions, meta-statements.
    """
    text_lower = text.lower()

    # Skip non-factual patterns
    skip_patterns = [
        r'^(i think|i believe|in my opinion)',
        r'^(should|could|might|may)\b',
        r'\?$',  # Questions
        r'^(let me|i will|i\'ll|we should)',
        r'^(here are|the following|this is)',
    ]

    for pattern in skip_patterns:
        if re.search(pattern, text_lower):
            return False

    # Look for factual indicators
    factual_patterns = [
        r'\bis\b',  # "X is Y"
        r'\bwas\b',
        r'\bare\b',
        r'\bwere\b',
        r'\bhas\b',
        r'\bhave\b',
        r'\bcan\b.*\bused\b',  # "X can be used for Y"
        r'\bprovides\b',
        r'\bsupports\b',
        r'\bcontains\b',
        r'\d',  # Contains numbers (statistics, dates)
    ]

    for pattern in factual_patterns:
        if re.search(pattern, text_lower):
            return True

    return False


def _find_nearest_url(claim: str, full_text: str, urls: List[str]) -> Optional[str]:
    """Find the URL nearest to a claim in the original text."""
    if not urls:
        return None

    claim_pos = full_text.find(claim)
    if claim_pos < 0:
        return urls[0] if urls else None

    # Find nearest URL
    min_distance = float('inf')
    nearest_url = None

    for url in urls:
        url_pos = full_text.find(url)
        if url_pos >= 0:
            distance = abs(claim_pos - url_pos)
            if distance < min_distance:
                min_distance = distance
                nearest_url = url

    return nearest_url


# Utility function for testing
def extract_claims_from_findings_dict(findings_dict: Dict, subagent_id: str = "test") -> List[Claim]:
    """
    Extract claims from a findings dictionary directly.

    Useful for testing with sample data.
    """
    focus_area = findings_dict.get("focus_area", "unknown")
    return _extract_from_json(findings_dict, subagent_id, focus_area)
