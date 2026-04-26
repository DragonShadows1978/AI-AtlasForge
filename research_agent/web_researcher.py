"""
Web Researcher - Searches the web for current information.

Provides structured web search capabilities that:
1. Break complex topics into searchable queries
2. Filter and rank results by relevance
3. Extract key information from search results
4. Handle multiple search strategies

This module is designed to be used during the PLANNING stage
to gather current best practices and techniques.
"""

import sys
import json
import re
import logging
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiment_framework import invoke_fresh_llm, ModelType


class SearchStrategy(Enum):
    """Search strategies for different types of research."""
    BROAD = "broad"               # General topic exploration
    TECHNICAL = "technical"       # Technical documentation focus
    COMPARISON = "comparison"     # Compare approaches/tools
    TUTORIAL = "tutorial"         # How-to guides
    ACADEMIC = "academic"         # Research papers
    RECENT = "recent"             # Latest developments


@dataclass
class SearchQuery:
    """A search query with metadata."""
    query: str
    strategy: SearchStrategy
    year: int = 2026
    domain_filter: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None


@dataclass
class SearchResult:
    """A single search result."""
    title: str
    url: str
    snippet: str
    source: str  # Domain
    relevance_score: float = 0.0  # 0.0 - 1.0
    is_primary_source: bool = False  # Official docs, original papers
    fetched_content: str = ""  # Content if fetched
    extracted_insights: List[str] = field(default_factory=list)


@dataclass
class WebResearchResult:
    """Complete results from web research."""
    topic: str
    queries_executed: List[SearchQuery]
    results: List[SearchResult] = field(default_factory=list)
    timestamp: str = ""
    total_results: int = 0
    top_sources: List[str] = field(default_factory=list)
    key_findings: List[str] = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "queries": [
                {"query": q.query, "strategy": q.strategy.value}
                for q in self.queries_executed
            ],
            "results": [
                {
                    "title": r.title,
                    "url": r.url,
                    "snippet": (r.snippet or "")[:200],
                    "source": r.source,
                    "relevance": r.relevance_score,
                    "is_primary": r.is_primary_source
                }
                for r in self.results
            ],
            "timestamp": self.timestamp,
            "total_results": self.total_results,
            "top_sources": self.top_sources,
            "key_findings": self.key_findings,
            "success": self.success,
            "error": self.error
        }


class WebResearcher:
    """
    Performs web research using search capabilities.

    The researcher:
    1. Breaks topics into targeted queries
    2. Executes searches with different strategies
    3. Filters and ranks results
    4. Extracts key insights

    NOTE: This module depends on a web-search-capable model via the
    experiment framework's fresh instance spawning.
    In the AtlasForge context, this module provides structured research
    patterns for whichever LLM provider is active.
    """

    # Prompt for query generation
    QUERY_GENERATION_PROMPT = """Generate search queries for researching this topic.

Topic: {topic}
Context: {context}
Year: {year}

Generate 3-5 search queries that will find:
1. Official documentation and primary sources
2. Best practices and recommendations
3. Recent developments (2024-2026)
4. Comparisons and alternatives
5. Practical tutorials and examples

For each query, specify:
- The search query string
- The search strategy (broad, technical, comparison, tutorial, academic, recent)
- Optional domain filters (e.g., github.com, docs.python.org)

Respond in JSON:
{{
    "queries": [
        {{
            "query": "search query here",
            "strategy": "technical",
            "domains": ["github.com"] or null
        }}
    ]
}}
"""

    # Prompt for insight extraction
    INSIGHT_EXTRACTION_PROMPT = """Extract key insights from these search results about: $topic

Search Results:
$results

Extract:
1. Key findings relevant to the topic
2. Best practices mentioned
3. Important warnings or caveats
4. Recommended tools or approaches
5. Sources to cite

Respond in JSON:
{{
    "key_findings": ["finding1", "finding2"],
    "best_practices": ["practice1", "practice2"],
    "warnings": ["warning1"],
    "recommended_tools": ["tool1"],
    "top_sources": ["source_url1", "source_url2"]
}}
"""

    # Primary source domains (higher trust)
    PRIMARY_SOURCE_DOMAINS = [
        "github.com",
        "docs.python.org",
        "arxiv.org",
        "developer.mozilla.org",
        "learn.microsoft.com",
        "cloud.google.com",
        "aws.amazon.com",
        "anthropic.com",
        "openai.com",
        "pytorch.org",
        "tensorflow.org",
        "huggingface.co"
    ]

    def __init__(
        self,
        model: ModelType = ModelType.BALANCED,
        max_results_per_query: int = 5,
        timeout_seconds: int = 60,
        use_web_search: bool = False
    ):
        """
        Initialize web researcher.

        Args:
            model: Model for query generation and insight extraction
            max_results_per_query: Maximum results to keep per query
            timeout_seconds: Timeout for each operation
            use_web_search: If True, invoke Claude with --allowedTools WebSearch for real results
        """
        self.model = model
        self.max_results_per_query = max_results_per_query
        self.timeout_seconds = timeout_seconds
        self.use_web_search = use_web_search

    def generate_queries(
        self,
        topic: str,
        context: str = "",
        year: int = 2026
    ) -> List[SearchQuery]:
        """
        Generate search queries for a topic.

        Args:
            topic: The topic to research
            context: Additional context about why we're researching
            year: Current year for recent searches

        Returns:
            List of SearchQuery objects
        """
        # Use string replacement instead of .format() to avoid KeyError when
        # topic/context contain literal {} characters (S1 fix)
        prompt = (self.QUERY_GENERATION_PROMPT
                  .replace("{topic}", str(topic))
                  .replace("{context}", str(context or "General research"))
                  .replace("{year}", str(year)))

        response, _ = invoke_fresh_llm(
            prompt=prompt,
            model=self.model,
            timeout=self.timeout_seconds
        )

        queries = []
        try:
            parsed = self._extract_json(response)
            if parsed and "queries" in parsed:
                for q in parsed["queries"]:
                    strategy = SearchStrategy.BROAD
                    try:
                        strategy = SearchStrategy(q.get("strategy", "broad"))
                    except ValueError:
                        pass

                    queries.append(SearchQuery(
                        query=q.get("query", ""),
                        strategy=strategy,
                        year=year,
                        domain_filter=q.get("domains")
                    ))
        except Exception as e:
            # E1 fix: log at WARNING so failures are visible without DEBUG mode
            logger.warning("[WebResearcher] generate_queries failed: %s", e, exc_info=True)
            # Fallback to basic query
            queries.append(SearchQuery(
                query=f"{topic} {year}",
                strategy=SearchStrategy.BROAD,
                year=year
            ))

        return queries

    def execute_search(
        self,
        query: SearchQuery,
        simulate: bool = False
    ) -> List[SearchResult]:
        """
        Execute a search query.

        Uses real web search via --allowedTools WebSearch when self.use_web_search is True.
        Falls back to LLM-prompted search otherwise.

        Args:
            query: The search query to execute
            simulate: If True, return simulated results

        Returns:
            List of SearchResult objects
        """
        if simulate:
            return self._simulate_search(query)

        if self.use_web_search:
            return self._invoke_claude_with_websearch(query)

        # Fallback: prompt LLM to reason about results (no real search)
        search_prompt = f"""Use web search to find information about: {query.query}

Look for:
- Official documentation
- Recent articles (2024-2026)
- Technical guides
- Best practices

After searching, provide results in this JSON format:
{{
    "results": [
        {{
            "title": "Result title",
            "url": "https://...",
            "snippet": "Brief description",
            "source": "domain.com"
        }}
    ]
}}
"""

        response, _ = invoke_fresh_llm(
            prompt=search_prompt,
            model=self.model,
            system_prompt="You have access to web search. Find current, reliable information.",
            timeout=self.timeout_seconds
        )

        return self._parse_search_results(response)

    def _invoke_claude_with_websearch(self, query: SearchQuery) -> List[SearchResult]:
        """
        Invoke Claude CLI with --allowedTools WebSearch to perform real web search.

        Builds a focused search prompt, passes WebSearch as an allowed tool,
        then parses the response for structured results. Falls back to empty list
        on failure so callers are never broken.

        Args:
            query: The search query to execute

        Returns:
            List of SearchResult objects from real web search
        """
        import subprocess
        import os
        import logging

        logger = logging.getLogger(__name__)

        # Sanitize query: strip control characters, zero-width unicode, and
        # injection-relevant characters. Limit length to prevent overflow.
        safe_query = re.sub(r'[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202f\ufeff]', '', query.query)
        safe_query = re.sub(r'[`<>{}\[\]\\|]', '', safe_query)
        safe_query = safe_query.strip()[:500]
        if not safe_query:
            logger.warning("[WebSearch] Query became empty after sanitization; skipping")
            return []

        search_prompt = f"""Search the web for: {safe_query}

Find the most authoritative and recent sources (2024-2026).
Focus on:
- Official documentation and primary sources
- GitHub repositories with real code
- Research papers or technical blogs
- Best practices and comparisons

After searching, respond ONLY with JSON in this exact format:
{{
    "results": [
        {{
            "title": "Exact page title",
            "url": "https://actual-url.com/page",
            "snippet": "Key information from this source",
            "source": "domain.com"
        }}
    ]
}}
"""

        try:
            from atlasforge_config import BASE_DIR
            from WebProxy import proxy_cli_args
            cmd = [
                "claude", "-p",
            ]
            cmd.extend(proxy_cli_args(""))

            _safe_keys = {"HOME", "PATH", "TERM", "LANG", "LC_ALL", "USER",
                          "ANTHROPIC_API_KEY", "TMPDIR", "TMP", "TEMP"}
            env = {k: v for k, v in os.environ.items() if k in _safe_keys}
            env.pop("CLAUDECODE", None)

            result = subprocess.run(
                cmd,
                input=search_prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True
            )

            if result.returncode == 0:
                response = result.stdout.strip()
                parsed = self._parse_search_results(response)
                if parsed:
                    return parsed
                # M6 fix: warn when web search returned parseable-but-empty results
                logger.warning("[WebSearch] Response parsed but returned no results (len=%d); check Claude WebSearch output", len(response))
            else:
                logger.warning("[WebSearch] Claude CLI returned non-zero: %s", result.stderr[:200])

        except subprocess.TimeoutExpired:
            logger.warning("[WebSearch] Timed out after %ds for query: %s", self.timeout_seconds, query.query[:60])
        except FileNotFoundError:
            logger.warning("[WebSearch] claude CLI not found; falling back to LLM search")
        except Exception as exc:
            logger.warning("[WebSearch] Unexpected error: %s", exc)

        return []

    def _simulate_search(self, query: SearchQuery) -> List[SearchResult]:
        """Simulate search results for testing."""
        return [
            SearchResult(
                title=f"Documentation for {query.query}",
                url=f"https://docs.example.com/{query.query.replace(' ', '-')}",
                snippet=f"Official documentation about {query.query}...",
                source="docs.example.com",
                relevance_score=0.9,
                is_primary_source=True
            ),
            SearchResult(
                title=f"Tutorial: {query.query}",
                url=f"https://tutorial.example.com/{query.query.replace(' ', '-')}",
                snippet=f"Learn how to use {query.query} effectively...",
                source="tutorial.example.com",
                relevance_score=0.7,
                is_primary_source=False
            )
        ]

    def _parse_search_results(self, response: str) -> List[SearchResult]:
        """Parse search results from response."""
        results = []

        try:
            parsed = self._extract_json(response)
            if parsed and "results" in parsed:
                for r in parsed["results"]:
                    source = r.get("source", "")
                    if not source and r.get("url"):
                        # Extract domain from URL
                        import urllib.parse
                        source = urllib.parse.urlparse(r["url"]).netloc

                    is_primary = any(
                        domain in source.lower()
                        for domain in self.PRIMARY_SOURCE_DOMAINS
                    )

                    results.append(SearchResult(
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        snippet=r.get("snippet", ""),
                        source=source,
                        relevance_score=r.get("relevance", 0.5),
                        is_primary_source=is_primary
                    ))
        except Exception as e:
            # E2 fix: log at DEBUG so parse failures are diagnosable
            logger.debug("[WebResearcher] _parse_search_results parse error: %s", e)

        return results

    def extract_insights(
        self,
        topic: str,
        results: List[SearchResult]
    ) -> Dict[str, Any]:
        """
        Extract insights from search results.

        Args:
            topic: The research topic
            results: Search results to analyze

        Returns:
            Dict with key findings, best practices, etc.
        """
        if not results:
            return {}

        results_text = "\n\n".join([
            f"Title: {r.title}\nURL: {r.url}\nSnippet: {r.snippet}"
            for r in results[:10]  # Limit to top 10
        ])

        import string as _string
        prompt = _string.Template(self.INSIGHT_EXTRACTION_PROMPT).safe_substitute(
            topic=topic,
            results=results_text
        )

        response, _ = invoke_fresh_llm(
            prompt=prompt,
            model=self.model,
            timeout=self.timeout_seconds
        )

        if response is None:
            return {}

        try:
            return self._extract_json(response) or {}
        except Exception as e:
            # E3 fix: log at DEBUG so parse failures are diagnosable
            logger.debug("[WebResearcher] extract_insights parse error: %s", e)
            return {}

    def research_topic(
        self,
        topic: str,
        context: str = "",
        max_queries: int = 3,
        simulate: bool = False
    ) -> WebResearchResult:
        """
        Perform complete research on a topic.

        Args:
            topic: Topic to research
            context: Additional context
            max_queries: Maximum number of queries to execute
            simulate: Use simulated results for testing

        Returns:
            WebResearchResult with all findings
        """
        result = WebResearchResult(
            topic=topic,
            queries_executed=[],
            timestamp=datetime.now().isoformat()
        )

        # Generate queries
        queries = self.generate_queries(topic, context)[:max_queries]
        result.queries_executed = queries

        # Execute searches
        all_results = []
        for query in queries:
            search_results = self.execute_search(query, simulate=simulate)
            all_results.extend(search_results)

        # Deduplicate by URL
        seen_urls = set()
        unique_results = []
        for r in all_results:
            if r.url not in seen_urls:
                seen_urls.add(r.url)
                unique_results.append(r)

        # Sort by relevance and primary source status
        unique_results.sort(
            key=lambda r: (r.is_primary_source, r.relevance_score),
            reverse=True
        )

        # M1 fix: guard against empty queries list to prevent [:0] truncating all results
        _max_keep = self.max_results_per_query * len(queries) if queries else len(unique_results)
        result.results = unique_results[:_max_keep]
        result.total_results = len(result.results)

        # Extract insights
        if result.results:
            insights = self.extract_insights(topic, result.results)
            result.key_findings = insights.get("key_findings", [])
            result.top_sources = insights.get("top_sources", [])

        return result

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

        # Balanced-brace scan handles nested JSON objects (M2+M3 fix).
        start = text.find('{')
        if start != -1:
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == '{':
                    depth += 1
                elif ch == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break

        return None


if __name__ == "__main__":
    # Self-test
    print("Web Researcher - Self Test")
    print("=" * 50)

    researcher = WebResearcher(model=ModelType.FAST)

    print("\nGenerating queries for 'mutation testing Python'...")
    queries = researcher.generate_queries(
        topic="mutation testing Python",
        context="Building a test quality framework"
    )

    print(f"\nGenerated {len(queries)} queries:")
    for q in queries:
        print(f"  [{q.strategy.value}] {q.query}")

    print("\nRunning simulated search...")
    result = researcher.research_topic(
        topic="mutation testing Python",
        context="Building a test quality framework",
        simulate=True  # Use simulation for self-test
    )

    print(f"\nResults:")
    print(f"  Total results: {result.total_results}")
    print(f"  Success: {result.success}")

    for r in result.results[:3]:
        print(f"\n  - {r.title}")
        print(f"    URL: {r.url}")
        print(f"    Primary source: {r.is_primary_source}")

    print("\nWeb researcher self-test complete!")
