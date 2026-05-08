"""
Knowledge Synthesizer - Combines research findings into actionable recommendations.

Takes raw research results and produces:
1. Synthesized summary of key findings
2. Evidence-based recommendations
3. Confidence levels for recommendations
4. Source citations
5. Knowledge gaps identified

This ensures the implementation plan is EVIDENCE-BASED, not just
based on training corpora knowledge.
"""

import sys
import json
import re
import unicodedata
import importlib.util
from pathlib import Path
from dataclasses import dataclass, field, replace as _dc_replace
import copy as _copy
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Import investigation_engine without permanently mutating sys.path.
if "investigation_engine" not in sys.modules:
    _IE_PATH = Path(__file__).parent.parent / "investigation_engine.py"
    _ie_spec = importlib.util.spec_from_file_location("investigation_engine", str(_IE_PATH))
    _ie_mod = importlib.util.module_from_spec(_ie_spec)
    sys.modules["investigation_engine"] = _ie_mod
    _ie_spec.loader.exec_module(_ie_mod)
from investigation_engine import invoke_claude, ModelType
from .web_researcher import WebResearchResult, SearchResult

_INJECTION_PREFIXES = (
    "IGNORE", "<system>", "[INST]", "[/INST]", "###OVERRIDE", "SYSTEM:", "</s>",
    "<|im_start|>", "<|im_end|>", "<!--", "ASSISTANT:", "USER:", "HUMAN:",
)


def _extract_json_object(text: str):
    """Find the first balanced {...} block in text that contains a 'summary' key.

    Walks string counting brace depth, correctly skipping braces inside string
    literals so that {"summary": "has } inside"} is parsed correctly.
    Returns the raw JSON string on success, or None if nothing suitable is found.
    """
    if not text:
        return None
    # Limit input length to prevent O(N^2) worst case on adversarial inputs
    text = text[:200_000]
    start = text.find('{')
    while start != -1:
        depth = 0
        in_string = False
        escape_next = False
        end = None
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is None:
            # No closing brace found from here to end-of-string; no later '{' can
            # have a matching '}' either, so stop scanning.
            break
        candidate = text[start:end + 1]
        if '"summary"' in candidate:
            return candidate
        start = text.find('{', end + 1)
    return None


def _sanitize_field(text: str, max_len: int = 500) -> str:
    """Sanitize a web-search result field before embedding in an LLM prompt."""
    if not text:
        return ""
    # Coerce non-str to repr() so bytes/int inputs don't bypass prefix detection
    if not isinstance(text, str):
        text = repr(text)
    # NFKC normalization converts fullwidth/halfwidth variants (e.g. ＳＹＳＴＥＭ)
    # to their canonical ASCII equivalents.
    text = unicodedata.normalize('NFKC', text)
    # Delete ZWNJ (U+200C) and ZWJ (U+200D) — they survive NFKC and can be
    # inserted between prefix letters to produce "SYSTEM‌:" → "SYSTEM:" after deletion.
    # These chars have no legitimate use inside LLM prompt fields.
    text = text.replace('‌', '').replace('‍', '')
    # Strip combining diacritics (U+0300–U+036F and related blocks), variation
    # selectors (U+FE00–U+FE1F), AND Unicode tag characters (U+E0000–U+E01EF) —
    # all survive NFKC and can be inserted between prefix letters to camouflage
    # injection tokens. Tags are especially dangerous: U+E0100 between S and Y
    # produces "SYST\U000E0100EM:" which bypasses startswith checks.
    text = re.sub(r'[̀-ͯ᷀-᷿⃐-⃿︀-︟︠-︯\U000E0000-\U000E01EF]', '', text)
    # Strip ASCII control chars (including form-feed/VT which would otherwise
    # concatenate adjacent tokens and bypass prefix detection)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    clean_lines = []
    _prefixes_upper = tuple(p.upper() for p in _INJECTION_PREFIXES)
    for line in text.splitlines():
        # Remove Unicode invisible/format chars and collapse whitespace
        stripped = re.sub(r'[­͏؜ᅟᅠ឴឵'
                          r'᠋-᠎​-‏‪-‮'
                          r'⁠-⁯ㅤ﻿ﾠ]', ' ', line.lstrip())
        stripped = re.sub(r'  +', ' ', stripped).strip()
        upper = stripped.upper()
        # Drop lines where an injection prefix appears at start-of-line
        # (case-insensitive) OR anywhere mid-line (case-insensitive, word boundary).
        if any(upper.startswith(p) for p in _prefixes_upper):
            continue
        if any(
            bool(re.search(r'(?<![a-zA-Z0-9])' + re.escape(p), stripped, re.IGNORECASE))
            for p in _INJECTION_PREFIXES
        ):
            continue
        clean_lines.append(stripped)
    # Guard: None, NaN, and inf all cause TypeError/OverflowError in int().
    try:
        limit = max(0, int(max_len))
    except (TypeError, ValueError, OverflowError):
        limit = 500
    return "\n".join(clean_lines)[:limit]


class ConfidenceLevel(Enum):
    """Confidence levels for recommendations."""
    HIGH = "high"          # Multiple reliable sources agree
    MEDIUM = "medium"      # Some sources support, or single reliable source
    LOW = "low"            # Limited or conflicting evidence
    SPECULATIVE = "speculative"  # Based on inference, not direct evidence


class RecommendationType(Enum):
    """Types of recommendations."""
    ARCHITECTURE = "architecture"      # System design decisions
    IMPLEMENTATION = "implementation"  # How to implement
    TOOL = "tool"                      # Which tools to use
    PATTERN = "pattern"                # Design patterns
    AVOID = "avoid"                    # What to avoid
    BEST_PRACTICE = "best_practice"    # General best practices


@dataclass
class Recommendation:
    """A single recommendation from research."""
    title: str
    description: str
    recommendation_type: RecommendationType
    confidence: ConfidenceLevel
    rationale: str  # Why this is recommended
    sources: List[str] = field(default_factory=list)  # URLs supporting this
    alternatives: List[str] = field(default_factory=list)
    caveats: List[str] = field(default_factory=list)


@dataclass
class KnowledgeGap:
    """An identified gap in research findings."""
    topic: str
    description: str
    importance: str  # "critical", "important", "nice_to_have"
    suggested_research: str  # What to search for


@dataclass
class SynthesisResult:
    """Complete synthesis of research findings."""
    topic: str
    summary: str = ""
    recommendations: List[Recommendation] = field(default_factory=list)
    knowledge_gaps: List[KnowledgeGap] = field(default_factory=list)
    sources_used: List[str] = field(default_factory=list)
    total_sources: int = 0
    primary_sources: int = 0
    synthesis_confidence: ConfidenceLevel = ConfidenceLevel.LOW
    timestamp: str = ""
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "summary": self.summary,
            "recommendations": [
                {
                    "title": r.title,
                    "description": r.description,
                    "type": r.recommendation_type.value if isinstance(r.recommendation_type, RecommendationType) else str(r.recommendation_type or ""),
                    "confidence": r.confidence.value if isinstance(r.confidence, ConfidenceLevel) else str(r.confidence or ""),
                    "rationale": r.rationale,
                    "sources": r.sources,
                    "alternatives": r.alternatives,
                    "caveats": r.caveats
                }
                for r in (self.recommendations or []) if r is not None
            ],
            "knowledge_gaps": [
                {
                    "topic": g.topic,
                    "description": g.description,
                    "importance": g.importance,
                    "suggested_research": g.suggested_research
                }
                for g in (self.knowledge_gaps or []) if g is not None
            ],
            "sources": {
                "total": self.total_sources,
                "primary": self.primary_sources,
                "urls": self.sources_used
            },
            "confidence": self.synthesis_confidence.value if isinstance(self.synthesis_confidence, ConfidenceLevel) else str(self.synthesis_confidence or ""),
            "timestamp": self.timestamp,
            "success": self.success,
            "error": self.error
        }

    def to_markdown(self) -> str:
        """Convert synthesis to markdown for documentation."""
        md = f"# Research Synthesis: {self.topic}\n\n"
        md += f"*Generated: {self.timestamp}*\n"
        md += f"*Confidence: {self.synthesis_confidence.value if isinstance(self.synthesis_confidence, ConfidenceLevel) else str(self.synthesis_confidence or '')}*\n\n"

        md += "## Summary\n\n"
        md += f"{self.summary}\n\n"

        if self.recommendations:
            md += "## Recommendations\n\n"
            for i, rec in enumerate(self.recommendations, 1):
                md += f"### {i}. {rec.title}\n\n"
                md += f"**Type:** {rec.recommendation_type.value if isinstance(rec.recommendation_type, RecommendationType) else str(rec.recommendation_type or '')} | "
                md += f"**Confidence:** {rec.confidence.value if isinstance(rec.confidence, ConfidenceLevel) else str(rec.confidence or '')}\n\n"
                md += f"{rec.description}\n\n"
                md += f"**Rationale:** {rec.rationale}\n\n"
                if rec.sources:
                    md += "**Sources:**\n"
                    for src in rec.sources[:3]:
                        md += f"- {src}\n"
                    md += "\n"
                if rec.caveats:
                    md += "**Caveats:**\n"
                    for caveat in rec.caveats:
                        md += f"- {caveat}\n"
                    md += "\n"

        if self.knowledge_gaps:
            md += "## Knowledge Gaps\n\n"
            for gap in self.knowledge_gaps:
                md += f"- **{gap.topic}** ({gap.importance}): {gap.description}\n"
            md += "\n"

        if self.sources_used:
            md += "## Sources\n\n"
            for src in self.sources_used[:10]:
                md += f"- {src}\n"

        return md


class KnowledgeSynthesizer:
    """
    Synthesizes research findings into actionable recommendations.

    The synthesizer:
    1. Analyzes multiple sources for common themes
    2. Weighs evidence by source reliability
    3. Identifies consensus and conflicts
    4. Produces confidence-weighted recommendations
    5. Highlights knowledge gaps
    """

    SYNTHESIS_PROMPT = """Synthesize these research findings into actionable recommendations.

Topic: $topic
Context: $context

Research Findings:
$findings

Analyze the findings and produce:
1. A brief summary (2-3 sentences)
2. Recommendations with confidence levels
3. Knowledge gaps that need more research

For each recommendation:
- Consider how many sources support it
- Note if sources are primary (official docs) or secondary
- Identify any conflicting advice
- Provide rationale based on evidence

Confidence levels:
- high: Multiple reliable sources agree
- medium: Some sources support, or single reliable source
- low: Limited or conflicting evidence
- speculative: Based on inference

Respond in JSON:
{
    "summary": "Brief synthesis summary",
    "recommendations": [
        {
            "title": "Recommendation title",
            "description": "What to do",
            "type": "architecture|implementation|tool|pattern|avoid|best_practice",
            "confidence": "high|medium|low|speculative",
            "rationale": "Why this is recommended based on evidence",
            "sources": ["url1", "url2"],
            "alternatives": ["alternative approach"],
            "caveats": ["things to watch out for"]
        }
    ],
    "knowledge_gaps": [
        {
            "topic": "Gap topic",
            "description": "What we don't know",
            "importance": "critical|important|nice_to_have",
            "suggested_research": "What to search for"
        }
    ],
    "overall_confidence": "high|medium|low|speculative"
}
"""

    def __init__(
        self,
        model: ModelType = ModelType.CLAUDE_SONNET,
        timeout_seconds: int = 120
    ):
        """
        Initialize synthesizer.

        Args:
            model: Model for synthesis
            timeout_seconds: Timeout for synthesis
        """
        self.model = model
        self.timeout_seconds = timeout_seconds

    def synthesize(
        self,
        topic: str,
        research_results: List[WebResearchResult],
        context: str = ""
    ) -> SynthesisResult:
        """
        Synthesize multiple research results into recommendations.

        Args:
            topic: The research topic
            research_results: List of research results to synthesize
            context: Additional context for synthesis

        Returns:
            SynthesisResult with recommendations and gaps
        """
        result = SynthesisResult(
            topic=topic,
            timestamp=datetime.now().isoformat()
        )

        # Collect all findings
        all_results = []
        all_sources = []
        primary_count = 0

        for research in (research_results or []):
            if research is None:
                continue
            for r in (research.results or []):
                all_results.append(r)
                if r.url:
                    all_sources.append(r.url)
                if r.is_primary_source:
                    primary_count += 1

        result.sources_used = list(set(all_sources))[:20]
        result.total_sources = len(all_results)
        result.primary_sources = primary_count

        if not all_results:
            result.summary = "No research findings to synthesize."
            result.synthesis_confidence = ConfidenceLevel.SPECULATIVE
            return result

        # Format findings for synthesis
        findings_text = self._format_findings(all_results)

        # Run synthesis
        import string as _string
        prompt = _string.Template(self.SYNTHESIS_PROMPT).safe_substitute(
            topic=_sanitize_field(topic, max_len=500),
            context=_sanitize_field(context or "General research", max_len=500),
            findings=findings_text
        )

        response, _ = invoke_claude(
            prompt=prompt,
            model=self.model,
            timeout=self.timeout_seconds
        )

        # Parse synthesis response
        try:
            parsed = self._extract_json(response)
            if not parsed:
                result.error = "Failed to parse synthesis response: no valid JSON returned by LLM"
                result.synthesis_confidence = ConfidenceLevel.SPECULATIVE
            if parsed:
                result.summary = parsed.get("summary", "")

                # Parse recommendations
                for rec_data in parsed.get("recommendations", []):
                    try:
                        rec = Recommendation(
                            title=rec_data.get("title", ""),
                            description=rec_data.get("description", ""),
                            recommendation_type=RecommendationType(
                                rec_data.get("type", "implementation")
                            ),
                            confidence=ConfidenceLevel(
                                rec_data.get("confidence", "medium")
                            ),
                            rationale=rec_data.get("rationale", ""),
                            sources=rec_data.get("sources", []),
                            alternatives=rec_data.get("alternatives", []),
                            caveats=rec_data.get("caveats", [])
                        )
                        result.recommendations.append(rec)
                    except (ValueError, KeyError):
                        continue

                # Parse knowledge gaps
                for gap_data in parsed.get("knowledge_gaps", []):
                    gap = KnowledgeGap(
                        topic=gap_data.get("topic", ""),
                        description=gap_data.get("description", ""),
                        importance=gap_data.get("importance", "nice_to_have"),
                        suggested_research=gap_data.get("suggested_research", "")
                    )
                    result.knowledge_gaps.append(gap)

                # Overall confidence
                try:
                    result.synthesis_confidence = ConfidenceLevel(
                        parsed.get("overall_confidence", "medium")
                    )
                except ValueError:
                    result.synthesis_confidence = ConfidenceLevel.MEDIUM

        except Exception as e:
            result.error = f"Failed to parse synthesis: {e}"
            result.synthesis_confidence = ConfidenceLevel.SPECULATIVE

        # Adjust confidence based on source quality
        if primary_count >= 3:
            if result.synthesis_confidence == ConfidenceLevel.MEDIUM:
                result.synthesis_confidence = ConfidenceLevel.HIGH
        elif primary_count == 0:
            # No primary sources decrease confidence
            if result.synthesis_confidence == ConfidenceLevel.HIGH:
                result.synthesis_confidence = ConfidenceLevel.MEDIUM

        return result

    def _format_findings(self, results: List[SearchResult]) -> str:
        """Format search results for synthesis prompt."""
        findings = []
        for i, r in enumerate(results[:15], 1):  # Limit to 15 results
            if r is None:
                continue
            # BUG-SEC-6: sanitize all web-search fields before prompt embedding
            # BUG-COR-2: guard r.snippet against None before slicing
            title = _sanitize_field(r.title or '', 200)
            source = _sanitize_field(r.source or '', 100)
            url = _sanitize_field(r.url or '', 300)
            snippet = _sanitize_field((r.snippet or '')[:300], 300)
            finding = f"{i}. {title}\n"
            finding += f"   Source: {source}"
            if r.is_primary_source:
                finding += " (PRIMARY SOURCE)"
            finding += f"\n   URL: {url}\n"
            finding += f"   Snippet: {snippet}\n"
            if isinstance(r.extracted_insights, list) and r.extracted_insights:
                safe_insights = [_sanitize_field(str(ins), 200) for ins in r.extracted_insights[:3]]
                non_empty = [s for s in safe_insights if s]
                if non_empty:
                    finding += f"   Insights: {', '.join(non_empty)}\n"
            findings.append(finding)

        return "\n".join(findings)

    def synthesize_single(
        self,
        topic: str,
        research: WebResearchResult,
        context: str = ""
    ) -> SynthesisResult:
        """
        Synthesize a single research result.

        Convenience method for single research result.

        Args:
            topic: Research topic
            research: Single research result
            context: Additional context

        Returns:
            SynthesisResult
        """
        return self.synthesize(topic, [research], context)

    def merge_syntheses(
        self,
        syntheses: List[SynthesisResult]
    ) -> SynthesisResult:
        """
        Merge multiple synthesis results.

        Args:
            syntheses: List of synthesis results to merge

        Returns:
            Combined SynthesisResult
        """
        syntheses = [s for s in (syntheses or []) if s is not None]
        if not syntheses:
            return SynthesisResult(
                topic="No topic",
                timestamp=datetime.now().isoformat()
            )

        merged = SynthesisResult(
            topic=syntheses[0].topic,
            timestamp=datetime.now().isoformat()
        )

        # Combine summaries
        summaries = [s.summary for s in syntheses if s.summary]
        merged.summary = " ".join(summaries)

        # Combine recommendations (deduplicate by title)
        # Use deep-copy-of-list-fields so merged recs don't alias the originals.
        seen_titles = set()
        for synthesis in syntheses:
            for rec in (synthesis.recommendations or []):
                if rec.title not in seen_titles:
                    seen_titles.add(rec.title)
                    merged.recommendations.append(_dc_replace(
                        rec,
                        sources=list(rec.sources),
                        alternatives=list(rec.alternatives),
                        caveats=list(rec.caveats),
                    ))

        # Combine knowledge gaps
        seen_gaps = set()
        for synthesis in syntheses:
            for gap in (synthesis.knowledge_gaps or []):
                if gap.topic not in seen_gaps:
                    seen_gaps.add(gap.topic)
                    merged.knowledge_gaps.append(_dc_replace(
                        gap,
                        suggested_research=str(gap.suggested_research),
                    ))

        # Combine sources
        all_sources = []
        total = 0
        primary = 0
        for synthesis in syntheses:
            all_sources.extend(synthesis.sources_used or [])
            total += synthesis.total_sources
            primary += synthesis.primary_sources

        merged.sources_used = list(set(all_sources))[:20]
        merged.total_sources = total
        merged.primary_sources = primary

        # Determine overall confidence (lowest of all valid confidences)
        confidence_order = [
            ConfidenceLevel.SPECULATIVE,
            ConfidenceLevel.LOW,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.HIGH
        ]
        valid_confidences = [
            s.synthesis_confidence for s in syntheses
            if s.synthesis_confidence in confidence_order
        ]
        if not valid_confidences:
            merged.synthesis_confidence = ConfidenceLevel.MEDIUM
        else:
            min_conf_idx = min(confidence_order.index(c) for c in valid_confidences)
            merged.synthesis_confidence = confidence_order[min_conf_idx]

        return merged

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        if not text:
            return None
        if not isinstance(text, str):
            return None
        # Try direct parse
        try:
            result = json.loads(text)
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks; cap at 10 KB to prevent ReDoS.
        # Use greedy {.*} so nested objects like {"a": {"b": 1}} aren't truncated
        # at the first inner '}' (which the non-greedy {.*?} would do).
        json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text[:10_000], re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # TD-MOD-3: use a balanced-brace extractor so nested objects before the
        # 'summary' key are handled correctly instead of matching too eagerly.
        _candidate = _extract_json_object(text)
        if _candidate:
            try:
                result = json.loads(_candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        return None


if __name__ == "__main__":
    # Self-test
    print("Knowledge Synthesizer - Self Test")
    print("=" * 50)

    from .web_researcher import WebResearchResult, SearchResult, SearchQuery, SearchStrategy

    # Create mock research results
    mock_research = WebResearchResult(
        topic="mutation testing Python",
        queries_executed=[
            SearchQuery("mutation testing Python 2025", SearchStrategy.TECHNICAL)
        ],
        results=[
            SearchResult(
                title="MutPy - Mutation Testing for Python",
                url="https://github.com/mutpy/mutpy",
                snippet="MutPy is a mutation testing tool for Python programs. It generates mutants by applying mutation operators.",
                source="github.com",
                relevance_score=0.9,
                is_primary_source=True
            ),
            SearchResult(
                title="Introduction to Mutation Testing",
                url="https://example.com/mutation-testing-guide",
                snippet="Mutation testing is a powerful technique for assessing test suite quality by introducing small changes to code.",
                source="example.com",
                relevance_score=0.7,
                is_primary_source=False
            )
        ],
        timestamp=datetime.now().isoformat(),
        total_results=2,
        success=True
    )

    print("Synthesizing mock research results...")
    synthesizer = KnowledgeSynthesizer(model=ModelType.CLAUDE_HAIKU)
    result = synthesizer.synthesize_single(
        topic="mutation testing Python",
        research=mock_research,
        context="Building a test quality framework"
    )

    print(f"\nSummary: {result.summary[:200]}...")
    print(f"Confidence: {result.synthesis_confidence.value}")
    print(f"Total sources: {result.total_sources}")
    print(f"Primary sources: {result.primary_sources}")

    print(f"\nRecommendations ({len(result.recommendations)}):")
    for rec in result.recommendations[:3]:
        print(f"  - [{rec.confidence.value}] {rec.title}")

    print(f"\nKnowledge gaps ({len(result.knowledge_gaps)}):")
    for gap in result.knowledge_gaps[:3]:
        print(f"  - [{gap.importance}] {gap.topic}")

    print("\nKnowledge synthesizer self-test complete!")
