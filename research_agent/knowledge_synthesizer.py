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
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_llm, ModelType
from .web_researcher import WebResearchResult, SearchResult


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
    summary: str
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
                    "type": r.recommendation_type.value,
                    "confidence": r.confidence.value,
                    "rationale": r.rationale,
                    "sources": r.sources,
                    "alternatives": r.alternatives,
                    "caveats": r.caveats
                }
                for r in self.recommendations
            ],
            "knowledge_gaps": [
                {
                    "topic": g.topic,
                    "description": g.description,
                    "importance": g.importance,
                    "suggested_research": g.suggested_research
                }
                for g in self.knowledge_gaps
            ],
            "sources": {
                "total": self.total_sources,
                "primary": self.primary_sources,
                "urls": self.sources_used
            },
            "confidence": self.synthesis_confidence.value,
            "timestamp": self.timestamp,
            "success": self.success,
            "error": self.error
        }

    def to_markdown(self) -> str:
        """Convert synthesis to markdown for documentation."""
        md = f"# Research Synthesis: {self.topic}\n\n"
        md += f"*Generated: {self.timestamp}*\n"
        md += f"*Confidence: {self.synthesis_confidence.value}*\n\n"

        md += "## Summary\n\n"
        md += f"{self.summary}\n\n"

        if self.recommendations:
            md += "## Recommendations\n\n"
            for i, rec in enumerate(self.recommendations, 1):
                md += f"### {i}. {rec.title}\n\n"
                md += f"**Type:** {rec.recommendation_type.value} | "
                md += f"**Confidence:** {rec.confidence.value}\n\n"
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

Topic: {topic}
Context: {context}

Research Findings:
{findings}

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
{{
    "summary": "Brief synthesis summary",
    "recommendations": [
        {{
            "title": "Recommendation title",
            "description": "What to do",
            "type": "architecture|implementation|tool|pattern|avoid|best_practice",
            "confidence": "high|medium|low|speculative",
            "rationale": "Why this is recommended based on evidence",
            "sources": ["url1", "url2"],
            "alternatives": ["alternative approach"],
            "caveats": ["things to watch out for"]
        }}
    ],
    "knowledge_gaps": [
        {{
            "topic": "Gap topic",
            "description": "What we don't know",
            "importance": "critical|important|nice_to_have",
            "suggested_research": "What to search for"
        }}
    ],
    "overall_confidence": "high|medium|low|speculative"
}}
"""

    def __init__(
        self,
        model: ModelType = ModelType.BALANCED,
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

        for research in research_results:
            for r in research.results:
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
        prompt = self.SYNTHESIS_PROMPT.format(
            topic=topic,
            context=context or "General research",
            findings=findings_text
        )

        response, _ = invoke_fresh_llm(
            prompt=prompt,
            model=self.model,
            timeout=self.timeout_seconds
        )

        # Parse synthesis response
        try:
            parsed = self._extract_json(response)
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
            # Multiple primary sources increase confidence
            pass
        elif primary_count == 0:
            # No primary sources decrease confidence
            if result.synthesis_confidence == ConfidenceLevel.HIGH:
                result.synthesis_confidence = ConfidenceLevel.MEDIUM

        return result

    def _format_findings(self, results: List[SearchResult]) -> str:
        """Format search results for synthesis prompt."""
        findings = []
        for i, r in enumerate(results[:15], 1):  # Limit to 15 results
            finding = f"{i}. {r.title}\n"
            finding += f"   Source: {r.source}"
            if r.is_primary_source:
                finding += " (PRIMARY SOURCE)"
            finding += f"\n   URL: {r.url}\n"
            finding += f"   Snippet: {r.snippet[:300]}\n"
            if r.extracted_insights:
                finding += f"   Insights: {', '.join(r.extracted_insights[:3])}\n"
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
        seen_titles = set()
        for synthesis in syntheses:
            for rec in synthesis.recommendations:
                if rec.title not in seen_titles:
                    seen_titles.add(rec.title)
                    merged.recommendations.append(rec)

        # Combine knowledge gaps
        seen_gaps = set()
        for synthesis in syntheses:
            for gap in synthesis.knowledge_gaps:
                if gap.topic not in seen_gaps:
                    seen_gaps.add(gap.topic)
                    merged.knowledge_gaps.append(gap)

        # Combine sources
        all_sources = []
        total = 0
        primary = 0
        for synthesis in syntheses:
            all_sources.extend(synthesis.sources_used)
            total += synthesis.total_sources
            primary += synthesis.primary_sources

        merged.sources_used = list(set(all_sources))[:20]
        merged.total_sources = total
        merged.primary_sources = primary

        # Determine overall confidence (lowest of all)
        confidence_order = [
            ConfidenceLevel.SPECULATIVE,
            ConfidenceLevel.LOW,
            ConfidenceLevel.MEDIUM,
            ConfidenceLevel.HIGH
        ]
        min_conf_idx = min(
            confidence_order.index(s.synthesis_confidence)
            for s in syntheses
        )
        merged.synthesis_confidence = confidence_order[min_conf_idx]

        return merged

    def _extract_json(self, text: str) -> Optional[dict]:
        """Extract JSON from response text."""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON in code blocks
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find raw JSON object
        json_match = re.search(r'\{[^{}]*"summary".*?\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(0))
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
    synthesizer = KnowledgeSynthesizer(model=ModelType.FAST)
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
