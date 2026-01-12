"""
Research Orchestrator - Main coordinator for research activities.

Coordinates all research components to provide comprehensive
research capability during the PLANNING stage:

1. Topic Analysis - Break down complex topics
2. Web Search - Find current information
3. Documentation Fetch - Get official docs
4. Synthesis - Combine findings into recommendations
5. Report Generation - Create research documentation

Usage:
    orchestrator = ResearchOrchestrator()
    findings = orchestrator.research_for_planning(
        mission="Build an adversarial testing framework",
        topics=["mutation testing", "property-based testing", "red teaming"]
    )
"""

import sys
import json
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import ModelType
from .web_researcher import WebResearcher, WebResearchResult, SearchStrategy
from .knowledge_synthesizer import (
    KnowledgeSynthesizer,
    SynthesisResult,
    ConfidenceLevel
)


@dataclass
class ResearchConfig:
    """Configuration for research activities."""
    model: ModelType = ModelType.CLAUDE_SONNET
    max_topics: int = 5
    max_queries_per_topic: int = 3
    max_results_per_query: int = 5
    timeout_seconds: int = 300  # 5 minutes total
    enable_parallel: bool = True
    max_workers: int = 3
    simulate_search: bool = False  # Use for testing


@dataclass
class ResearchFindings:
    """Complete research findings for a mission."""
    mission: str
    topics_researched: List[str] = field(default_factory=list)
    research_results: List[WebResearchResult] = field(default_factory=list)
    synthesis: Optional[SynthesisResult] = None
    total_sources: int = 0
    primary_sources: int = 0
    timestamp: str = ""
    duration_ms: float = 0
    success: bool = True
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mission": self.mission,
            "topics_researched": self.topics_researched,
            "research_count": len(self.research_results),
            "synthesis": self.synthesis.to_dict() if self.synthesis else None,
            "sources": {
                "total": self.total_sources,
                "primary": self.primary_sources
            },
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "success": self.success,
            "errors": self.errors
        }

    def to_markdown(self) -> str:
        """Generate markdown research report."""
        md = f"# Research Report: {self.mission}\n\n"
        md += f"*Generated: {self.timestamp}*\n"
        md += f"*Duration: {self.duration_ms/1000:.1f}s*\n\n"

        md += "## Topics Researched\n\n"
        for topic in self.topics_researched:
            md += f"- {topic}\n"
        md += "\n"

        md += "## Source Summary\n\n"
        md += f"- Total sources: {self.total_sources}\n"
        md += f"- Primary sources: {self.primary_sources}\n\n"

        if self.synthesis:
            md += self.synthesis.to_markdown()
        else:
            md += "## Synthesis\n\nNo synthesis available.\n"

        if self.errors:
            md += "\n## Errors\n\n"
            for error in self.errors:
                md += f"- {error}\n"

        return md

    def save_report(self, filepath: Path) -> Path:
        """Save research report to markdown file."""
        filepath = Path(filepath)
        filepath.write_text(self.to_markdown())
        return filepath


class ResearchOrchestrator:
    """
    Orchestrates comprehensive research for the PLANNING stage.

    The orchestrator:
    1. Analyzes the mission to identify research topics
    2. Executes web searches for each topic
    3. Synthesizes findings into recommendations
    4. Generates a research report

    Usage:
        orchestrator = ResearchOrchestrator()
        findings = orchestrator.research_for_planning(
            mission="Build an adversarial testing framework"
        )

        # Save report
        findings.save_report(Path("research/research_findings.md"))
    """

    TOPIC_EXTRACTION_PROMPT = """Analyze this mission and identify research topics.

Mission: {mission}

Identify 3-5 specific topics to research that would help plan this mission.
Consider:
1. Core technologies/techniques needed
2. Best practices for this type of work
3. Existing tools or frameworks
4. Common pitfalls to avoid
5. Recent developments (2024-2025)

Respond in JSON:
{{
    "topics": [
        {{
            "topic": "topic name",
            "why": "why this is important to research",
            "priority": "high|medium|low"
        }}
    ]
}}
"""

    def __init__(self, config: Optional[ResearchConfig] = None):
        """
        Initialize research orchestrator.

        Args:
            config: Research configuration (uses defaults if not provided)
        """
        self.config = config or ResearchConfig()

        # Initialize components
        self.web_researcher = WebResearcher(
            model=self.config.model,
            max_results_per_query=self.config.max_results_per_query,
            timeout_seconds=self.config.timeout_seconds // 3
        )

        self.synthesizer = KnowledgeSynthesizer(
            model=self.config.model,
            timeout_seconds=self.config.timeout_seconds // 2
        )

    def extract_topics(self, mission: str) -> List[Dict[str, str]]:
        """
        Extract research topics from a mission statement.

        Args:
            mission: The mission statement

        Returns:
            List of topic dicts with topic, why, and priority
        """
        from experiment_framework import invoke_fresh_claude

        prompt = self.TOPIC_EXTRACTION_PROMPT.format(mission=mission)

        response, _ = invoke_fresh_claude(
            prompt=prompt,
            model=self.config.model,
            timeout=60
        )

        try:
            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group(0))
                return parsed.get("topics", [])
        except Exception:
            pass

        # Fallback: extract keywords from mission
        return [{"topic": mission, "why": "Main mission topic", "priority": "high"}]

    def research_topic(
        self,
        topic: str,
        context: str = ""
    ) -> WebResearchResult:
        """
        Research a single topic.

        Args:
            topic: Topic to research
            context: Additional context

        Returns:
            WebResearchResult
        """
        return self.web_researcher.research_topic(
            topic=topic,
            context=context,
            max_queries=self.config.max_queries_per_topic,
            simulate=self.config.simulate_search
        )

    def research_for_planning(
        self,
        mission: str,
        topics: Optional[List[str]] = None,
        context: str = "",
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> ResearchFindings:
        """
        Perform comprehensive research for mission planning.

        Args:
            mission: The mission statement
            topics: Optional list of specific topics (auto-extracted if not provided)
            context: Additional context for research
            progress_callback: Optional callback for progress updates

        Returns:
            ResearchFindings with complete research results
        """
        start_time = datetime.now()

        findings = ResearchFindings(
            mission=mission,
            timestamp=start_time.isoformat()
        )

        def log_progress(msg: str):
            if progress_callback:
                progress_callback(msg)

        # Extract topics if not provided
        if not topics:
            log_progress("Extracting research topics from mission...")
            topic_data = self.extract_topics(mission)
            topics = [t.get("topic", "") for t in topic_data if t.get("topic")]
            topics = topics[:self.config.max_topics]

        findings.topics_researched = topics
        log_progress(f"Researching {len(topics)} topics: {', '.join(topics[:3])}...")

        # Research each topic
        if self.config.enable_parallel and len(topics) > 1:
            findings.research_results = self._research_parallel(topics, context, log_progress)
        else:
            findings.research_results = self._research_sequential(topics, context, log_progress)

        # Calculate source counts
        for result in findings.research_results:
            findings.total_sources += len(result.results)
            findings.primary_sources += sum(
                1 for r in result.results if r.is_primary_source
            )

        # Synthesize findings
        if findings.research_results:
            log_progress("Synthesizing research findings...")
            try:
                findings.synthesis = self.synthesizer.synthesize(
                    topic=mission,
                    research_results=findings.research_results,
                    context=context
                )
                log_progress(f"Synthesis complete. Confidence: {findings.synthesis.synthesis_confidence.value}")
            except Exception as e:
                findings.errors.append(f"Synthesis failed: {e}")

        # Finalize
        end_time = datetime.now()
        findings.duration_ms = (end_time - start_time).total_seconds() * 1000

        log_progress(f"Research complete. {findings.total_sources} sources found.")

        return findings

    def _research_sequential(
        self,
        topics: List[str],
        context: str,
        log_progress: Callable[[str], None]
    ) -> List[WebResearchResult]:
        """Research topics sequentially."""
        results = []
        for i, topic in enumerate(topics, 1):
            log_progress(f"Researching topic {i}/{len(topics)}: {topic}")
            try:
                result = self.research_topic(topic, context)
                results.append(result)
            except Exception as e:
                log_progress(f"Failed to research {topic}: {e}")
        return results

    def _research_parallel(
        self,
        topics: List[str],
        context: str,
        log_progress: Callable[[str], None]
    ) -> List[WebResearchResult]:
        """Research topics in parallel."""
        results = []

        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            futures = {
                executor.submit(self.research_topic, topic, context): topic
                for topic in topics
            }

            for future in futures:
                topic = futures[future]
                try:
                    result = future.result(timeout=self.config.timeout_seconds)
                    results.append(result)
                    log_progress(f"Completed research: {topic}")
                except Exception as e:
                    log_progress(f"Failed to research {topic}: {e}")

        return results

    def quick_research(
        self,
        topic: str,
        context: str = ""
    ) -> SynthesisResult:
        """
        Quick research on a single topic with synthesis.

        Args:
            topic: Topic to research
            context: Additional context

        Returns:
            SynthesisResult with recommendations
        """
        result = self.research_topic(topic, context)
        return self.synthesizer.synthesize_single(topic, result, context)


def research_for_mission(
    mission: str,
    topics: Optional[List[str]] = None,
    model: ModelType = ModelType.CLAUDE_SONNET,
    simulate: bool = False
) -> ResearchFindings:
    """
    Convenience function to research for a mission.

    Args:
        mission: Mission statement
        topics: Optional specific topics
        model: Model to use
        simulate: Use simulated searches

    Returns:
        ResearchFindings
    """
    config = ResearchConfig(
        model=model,
        simulate_search=simulate
    )
    orchestrator = ResearchOrchestrator(config)
    return orchestrator.research_for_planning(mission, topics)


if __name__ == "__main__":
    # Self-test
    print("Research Orchestrator - Self Test")
    print("=" * 50)

    config = ResearchConfig(
        model=ModelType.CLAUDE_HAIKU,
        max_topics=2,
        max_queries_per_topic=2,
        simulate_search=True  # Use simulation for self-test
    )

    orchestrator = ResearchOrchestrator(config)

    print("\nExtracting topics from mission...")
    topics = orchestrator.extract_topics(
        "Build an adversarial testing framework for AI agents"
    )
    print(f"Extracted {len(topics)} topics:")
    for t in topics[:3]:
        print(f"  - [{t.get('priority', 'medium')}] {t.get('topic')}")

    print("\nRunning research (simulated)...")
    findings = orchestrator.research_for_planning(
        mission="Build an adversarial testing framework",
        topics=["mutation testing", "red team testing"],
        progress_callback=lambda msg: print(f"  {msg}")
    )

    print(f"\nResults:")
    print(f"  Topics researched: {len(findings.topics_researched)}")
    print(f"  Total sources: {findings.total_sources}")
    print(f"  Primary sources: {findings.primary_sources}")
    print(f"  Duration: {findings.duration_ms:.0f}ms")

    if findings.synthesis:
        print(f"\nSynthesis:")
        print(f"  Confidence: {findings.synthesis.synthesis_confidence.value}")
        print(f"  Recommendations: {len(findings.synthesis.recommendations)}")
        print(f"  Knowledge gaps: {len(findings.synthesis.knowledge_gaps)}")

    # Test markdown generation
    md = findings.to_markdown()
    print(f"\nGenerated {len(md)} character markdown report")

    print("\nResearch orchestrator self-test complete!")
