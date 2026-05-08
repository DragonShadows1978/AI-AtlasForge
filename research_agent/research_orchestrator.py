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
import re as _re
import json
import logging
import importlib.util
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
from datetime import datetime
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Import investigation_engine without permanently mutating sys.path.
if "investigation_engine" not in sys.modules:
    _IE_PATH = Path(__file__).parent.parent / "investigation_engine.py"
    _ie_spec = importlib.util.spec_from_file_location("investigation_engine", str(_IE_PATH))
    _ie_mod = importlib.util.module_from_spec(_ie_spec)
    sys.modules["investigation_engine"] = _ie_mod
    _ie_spec.loader.exec_module(_ie_mod)
from investigation_engine import invoke_claude, ModelType
from .web_researcher import WebResearcher, WebResearchResult, SearchStrategy
from .knowledge_synthesizer import (
    KnowledgeSynthesizer,
    SynthesisResult,
    ConfidenceLevel,
    _sanitize_field,
)


def _extract_first_json_object(text: str, required_key: str = ""):
    """Return the first balanced {...} block from text that contains required_key, or None.

    Uses a character-level brace-depth scan that correctly skips braces
    inside string literals, so arbitrary nesting depth is handled without
    a depth-limited regex.  Preamble '{}' blocks that don't contain the
    required_key are skipped rather than returned.
    """
    if not text:
        return None
    text = text[:200_000]
    start = text.find('{')
    while start != -1:
        depth = 0
        in_string = False
        escape_next = False
        found_end = None
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
                    found_end = i
                    break
        if found_end is None:
            # No matching '}' exists from here; no later '{' can match either.
            break
        candidate = text[start:found_end + 1]
        # Match required_key as a JSON key (followed by optional whitespace and ':'),
        # not just anywhere in the string (which would false-positive on values).
        if not required_key or _re.search(
            _re.escape(required_key) + r'\s*:', candidate
        ):
            return candidate
        start = text.find('{', found_end + 1)
    return None


@dataclass
class ResearchConfig:
    """Configuration for research activities."""
    orchestrator_model: ModelType = ModelType.CLAUDE_SONNET   # topic extraction + synthesis
    researcher_model: ModelType = ModelType.CLAUDE_HAIKU      # web queries
    max_topics: int = 10          # ceiling; Sonnet decides actual count
    max_queries_per_topic: int = 3
    max_results_per_query: int = 5
    timeout_seconds: int = 300  # 5 minutes total
    enable_parallel: bool = True
    max_workers: int = 10         # ceiling; scales with topic count
    simulate_search: bool = False  # Use for testing
    use_web_search: bool = False  # Enable real web search via --allowedTools WebSearch

    def __post_init__(self):
        # TD-MOD-1: reject non-positive-integer values BEFORE any coercion.
        # bool subclasses int, so check it first. Also reject floats — they
        # cause slice TypeError at runtime (slice indices must be integers).
        def _require_positive_int(val, name: str) -> None:
            if isinstance(val, bool) or isinstance(val, float) or not isinstance(val, int) or val <= 0:
                raise ValueError(f"{name} must be a positive integer, got {val!r}")
        _require_positive_int(self.timeout_seconds, "timeout_seconds")
        _require_positive_int(self.max_results_per_query, "max_results_per_query")
        _require_positive_int(self.max_queries_per_topic, "max_queries_per_topic")
        # Coerce remaining fields with None/invalid to safe defaults
        if self.timeout_seconds is None or not isinstance(self.timeout_seconds, int):
            self.timeout_seconds = 300
        if isinstance(self.max_workers, bool) or not isinstance(self.max_workers, int) or self.max_workers < 1:
            self.max_workers = 1
        if isinstance(self.max_topics, bool) or not isinstance(self.max_topics, int) or self.max_topics < 1:
            self.max_topics = 10
        if self.orchestrator_model is None:
            self.orchestrator_model = ModelType.CLAUDE_SONNET
        if self.researcher_model is None:
            self.researcher_model = ModelType.CLAUDE_HAIKU


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

    def save_report(self, filepath: Path, base_dir: Optional[Path] = None) -> Path:
        """Save research report to markdown file."""
        filepath = Path(filepath).resolve()
        if base_dir is not None:
            base_dir = Path(base_dir).resolve()
            try:
                filepath.relative_to(base_dir)
            except ValueError:
                raise ValueError(f"save_report: filepath outside allowed base_dir: {filepath}")
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

    TOPIC_EXTRACTION_PROMPT = """Analyze this mission and identify the appropriate research topics.

Mission: $mission

Identify the right number of research topics (1–10) based on mission complexity:
- Simple, single-component missions: 1–2 topics
- Moderate missions with clear scope: 3–4 topics
- Complex multi-component missions: 5–7 topics
- Large cross-cutting architectural missions: 8–10 topics

Consider:
1. Core technologies/techniques needed
2. Best practices for this type of work
3. Existing tools or frameworks
4. Common pitfalls to avoid
5. Recent developments (2024-2025)

Respond in JSON:
{
    "topics": [
        {
            "topic": "topic name",
            "why": "why this is important to research",
            "priority": "high|medium|low"
        }
    ]
}
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
            model=self.config.researcher_model,
            max_results_per_query=self.config.max_results_per_query,
            timeout_seconds=max(1, self.config.timeout_seconds // 3),
            use_web_search=self.config.use_web_search
        )

        self.synthesizer = KnowledgeSynthesizer(
            model=self.config.orchestrator_model,
            timeout_seconds=max(1, self.config.timeout_seconds // 2)
        )

    def extract_topics(self, mission: str) -> List[Dict[str, str]]:
        """
        Extract research topics from a mission statement.

        Args:
            mission: The mission statement

        Returns:
            List of topic dicts with topic, why, and priority
        """
        import string as _string
        prompt = _string.Template(self.TOPIC_EXTRACTION_PROMPT).safe_substitute(
            mission=_sanitize_field(mission, max_len=4000)
        )

        response, _ = invoke_claude(
            prompt=prompt,
            model=self.config.orchestrator_model,
            timeout=60
        )
        response = response or ""

        try:
            _candidate = _extract_first_json_object(response, required_key='"topics"')
            if _candidate:
                parsed = json.loads(_candidate)
                return parsed.get("topics", [])
        except (json.JSONDecodeError, ValueError) as _e:
            logger.debug("JSON parse error in extract_research_topics: %s", _e)

        # Fallback: extract keywords from mission (sanitize to prevent injection propagation)
        return [{"topic": _sanitize_field(mission, max_len=500), "why": "Main mission topic", "priority": "high"}]

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
                try:
                    progress_callback(msg)
                except Exception as _cb_exc:
                    logger.debug("[Research] progress_callback raised: %s", _cb_exc)

        # Extract topics if not provided (empty list [] is treated as caller-supplied)
        if topics is None:
            log_progress("Extracting research topics from mission...")
            topic_data = self.extract_topics(mission)
            topics = [t.get("topic", "") for t in topic_data if t.get("topic")]
            topics = topics[:self.config.max_topics]
        else:
            # Guard against non-iterable callers (int, float, etc.)
            if not hasattr(topics, '__iter__') or isinstance(topics, str):
                raise TypeError(f"topics must be a list or None, got {type(topics).__name__}")
            # Cap caller-supplied topics by max_topics ceiling; filter None/empty
            topics = [str(t) for t in topics if t and str(t).strip()][:self.config.max_topics]

        # Sanitize topics before use: ensures topics_researched and r.topic in research
        # results stay consistent (failed_topics comparison uses same sanitized values).
        topics = [s for s in (_sanitize_field(t, max_len=500) for t in topics) if s]

        if not topics:
            logger.warning("Research: no topics extracted from mission — skipping research phase")
            log_progress("Warning: could not extract research topics from mission statement.")

        findings.topics_researched = topics
        log_progress(f"Researching {len(topics)} topics: {', '.join(str(t) for t in topics[:3])}...")

        # Research each topic
        if self.config.enable_parallel and len(topics) > 1:
            findings.research_results = self._research_parallel(topics, context, log_progress)
        else:
            findings.research_results = self._research_sequential(topics, context, log_progress)

        # Calculate source counts
        for result in findings.research_results:
            _results = result.results or []
            findings.total_sources += len(_results)
            findings.primary_sources += sum(
                1 for r in _results if r.is_primary_source
            )

        # TD-MOD-2: record topics that produced zero results so callers can
        # distinguish genuine failure from zero-result research.
        topics_with_results = {r.topic for r in findings.research_results if r.results}
        failed_topics = [t for t in topics if t not in topics_with_results]
        if failed_topics:
            for t in failed_topics:
                findings.errors.append(f"No results for topic: {t}")
            if not findings.research_results or not any(r.results for r in findings.research_results):
                findings.success = False

        # Synthesize findings using per-topic synthesis + merge
        if findings.research_results:
            log_progress("Synthesizing research findings...")
            try:
                per_topic = self._synthesize_per_topic(
                    findings.research_results, context, log_progress
                )
            except Exception as e:
                per_topic = []
                log_progress(f"Per-topic synthesis failed ({e}), falling back to flat synthesis...")

            if len(per_topic) > 1:
                log_progress(f"Merging {len(per_topic)} per-topic syntheses...")
                merged = self.synthesizer.merge_syntheses(per_topic)
                merged.topic = mission
                findings.synthesis = merged
                log_progress(f"Synthesis complete. Confidence: {findings.synthesis.synthesis_confidence.value}")
            elif len(per_topic) == 1:
                findings.synthesis = per_topic[0]
                findings.synthesis.topic = mission
                log_progress(f"Synthesis complete. Confidence: {findings.synthesis.synthesis_confidence.value}")
            else:
                try:
                    findings.synthesis = self.synthesizer.synthesize(
                        topic=mission,
                        research_results=findings.research_results,
                        context=context
                    )
                    log_progress(f"Fallback synthesis complete. Confidence: {findings.synthesis.synthesis_confidence.value}")
                except Exception as e2:
                    findings.errors.append(f"Synthesis failed: {e2}")

        # Extract and inject code to AI-AfterImage
        try:
            self._extract_and_inject_code(findings, mission, log_progress)
        except Exception as e:
            findings.errors.append(f"Code extraction failed (non-fatal): {e}")

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

        executor = ThreadPoolExecutor(max_workers=max(1, min(len(topics), self.config.max_workers)))
        try:
            futures = {
                executor.submit(self.research_topic, topic, context): topic
                for topic in topics
            }

            try:
                for future in as_completed(futures, timeout=self.config.timeout_seconds):
                    topic = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        log_progress(f"Completed research: {topic}")
                    except Exception as e:
                        log_progress(f"Failed to research {topic}: {e}")
            except (TimeoutError, concurrent.futures.TimeoutError):
                log_progress(f"Parallel research timed out after {self.config.timeout_seconds}s; returning {len(results)} results")
                # Cancel pending futures to avoid blocking shutdown
                for f in futures:
                    f.cancel()
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        return results

    def _synthesize_per_topic(
        self,
        research_results: List[WebResearchResult],
        context: str,
        log_progress: Callable[[str], None]
    ) -> List[SynthesisResult]:
        """
        Synthesize research results grouped by topic.

        Each WebResearchResult has a .topic field. This method calls
        synthesize_single() for each result, producing a per-topic
        SynthesisResult that can later be merged.

        Args:
            research_results: List of WebResearchResult (each has .topic)
            context: Additional context for synthesis
            log_progress: Progress callback

        Returns:
            List of SynthesisResult, one per topic
        """
        syntheses = []
        for result in research_results:
            if not result.results:
                continue
            log_progress(f"Synthesizing topic: {result.topic}")
            # BUG-COR-1: wrap each topic in try/except so one failure does not
            # discard all already-synthesized topics for other topics.
            try:
                synthesis = self.synthesizer.synthesize_single(
                    topic=result.topic,
                    research=result,
                    context=context
                )
                syntheses.append(synthesis)
            except Exception as e:
                log_progress(f"Synthesis failed for topic '{result.topic}': {e}")
        return syntheses

    def merge_research_syntheses(
        self,
        syntheses: List[SynthesisResult]
    ) -> SynthesisResult:
        """
        Merge multiple SynthesisResult objects into one.

        Public convenience method for callers who run multiple
        quick_research() calls and want to combine the results.

        Args:
            syntheses: List of SynthesisResult to merge

        Returns:
            Merged SynthesisResult with deduplicated recommendations and gaps
        """
        return self.synthesizer.merge_syntheses(syntheses)

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

    def _extract_and_inject_code(
        self,
        findings: ResearchFindings,
        mission: str,
        log_progress: Callable[[str], None]
    ):
        """
        Extract code blocks from research findings and inject to AI-AfterImage.

        This enables code discovered during the Planning Stage research to be
        stored in the Code DB for future reference.

        Args:
            findings: The research findings containing potential code
            mission: The mission/query being researched
            log_progress: Callback for progress updates
        """
        try:
            # BUG-SEC-3: use importlib with an explicit absolute path instead of
            # sys.path.insert(0, ...) to prevent workspace-relative module hijacking.
            import importlib.util
            import uuid
            from pathlib import Path

            _injector_path = Path(__file__).parent.parent / "workspace" / "Investigation" / "afterimage_injector.py"
            if not _injector_path.exists():
                raise ImportError(f"afterimage_injector not found at {_injector_path}")
            _spec = importlib.util.spec_from_file_location("afterimage_injector", str(_injector_path))
            _mod = importlib.util.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)
            ResearchCodePipeline = _mod.ResearchCodePipeline
            InjectionConfig = _mod.InjectionConfig

            # Generate a research session ID
            research_id = f"research_{uuid.uuid4().hex[:8]}"

            # Configure the pipeline
            config = InjectionConfig(
                min_confidence=0.4,
                session_id=research_id,
            )

            pipeline = ResearchCodePipeline(injector_config=config)

            # Convert findings to dict format for extraction
            findings_dict = findings.to_dict()

            # Process the research findings
            result = pipeline.process_research_findings(
                findings=findings_dict,
                research_id=research_id,
                query=mission
            )

            if result.injected_count > 0:
                log_progress(f"Injected {result.injected_count} code blocks to AI-AfterImage")

            pipeline.close()

        except ImportError:
            # AI-AfterImage or extraction module not available - silent skip
            pass
        except Exception as e:
            # Log but don't fail the research
            import logging
            logging.getLogger(__name__).warning(f"Code extraction skipped: {e}")


def research_for_mission(
    mission: str,
    topics: Optional[List[str]] = None,
    orchestrator_model: ModelType = ModelType.CLAUDE_SONNET,
    researcher_model: ModelType = ModelType.CLAUDE_HAIKU,
    simulate: bool = False,
    use_web_search: bool = False
) -> ResearchFindings:
    """
    Convenience function to research for a mission.

    Args:
        mission: Mission statement
        topics: Optional specific topics
        orchestrator_model: Model for topic extraction and synthesis (default: Sonnet)
        researcher_model: Model for web queries (default: Haiku)
        simulate: Use simulated searches
        use_web_search: Enable real web search via --allowedTools WebSearch

    Returns:
        ResearchFindings
    """
    config = ResearchConfig(
        orchestrator_model=orchestrator_model,
        researcher_model=researcher_model,
        simulate_search=simulate,
        use_web_search=use_web_search
    )
    orchestrator = ResearchOrchestrator(config)
    return orchestrator.research_for_planning(mission, topics)


if __name__ == "__main__":
    # Self-test
    print("Research Orchestrator - Self Test")
    print("=" * 50)

    config = ResearchConfig(
        orchestrator_model=ModelType.CLAUDE_HAIKU,
        researcher_model=ModelType.CLAUDE_HAIKU,
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
