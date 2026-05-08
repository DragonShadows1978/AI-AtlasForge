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
import unicodedata
import logging
import importlib.util
import urllib.parse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)

# Import investigation_engine without permanently mutating sys.path.
# Register in sys.modules so the module identity (enum classes, etc.) is shared
# across all importers within the same process.
if "investigation_engine" not in sys.modules:
    _IE_PATH = Path(__file__).parent.parent / "investigation_engine.py"
    _ie_spec = importlib.util.spec_from_file_location("investigation_engine", str(_IE_PATH))
    _ie_mod = importlib.util.module_from_spec(_ie_spec)
    sys.modules["investigation_engine"] = _ie_mod
    _ie_spec.loader.exec_module(_ie_mod)
from investigation_engine import invoke_claude, ModelType

_INJECTION_PREFIXES = (
    "IGNORE", "<system>", "[INST]", "[/INST]", "###OVERRIDE", "SYSTEM:", "</s>",
    "<|im_start|>", "<|im_end|>", "<!--", "ASSISTANT:", "USER:", "HUMAN:",
)


def _sanitize_field(text: str, max_len: int = 500) -> str:
    """Sanitize user/external-controlled text before embedding in an LLM prompt."""
    if not text:
        return ""
    # Coerce non-str to repr() so bytes/int inputs don't bypass prefix detection
    if not isinstance(text, str):
        text = repr(text)
    # NFKC normalization converts fullwidth/halfwidth variants (e.g. ＳＹＳＴＥＭ)
    # to their canonical ASCII equivalents.
    text = unicodedata.normalize('NFKC', text)
    # Delete ZWNJ (U+200C) and ZWJ (U+200D) — they survive NFKC and can be
    # inserted between prefix letters to bypass prefix detection.
    text = text.replace('‌', '').replace('‍', '')
    # Strip combining diacritics, variation selectors (U+FE00–FE1F), AND Unicode
    # tag characters (U+E0000–U+E01EF) — all survive NFKC and can be inserted
    # between prefix letters to camouflage injection tokens.
    text = re.sub(r'[̀-ͯ᷀-᷿⃐-⃿︀-︟︠-︯\U000E0000-\U000E01EF]', '', text)
    # Replace ASCII control chars with spaces to prevent token concatenation.
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
{
    "queries": [
        {
            "query": "search query here",
            "strategy": "technical",
            "domains": ["github.com"] or null
        }
    ]
}
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
{
    "key_findings": ["finding1", "finding2"],
    "best_practices": ["practice1", "practice2"],
    "warnings": ["warning1"],
    "recommended_tools": ["tool1"],
    "top_sources": ["source_url1", "source_url2"]
}
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
        model: ModelType = ModelType.CLAUDE_HAIKU,
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
        # Use re.sub with a dispatch dict for a single-pass replacement so that
        # a topic/context value containing "{context}" or "{year}" does not cause
        # a second substitution in the chained-replace approach.
        _subs = {
            "{topic}": _sanitize_field(str(topic), max_len=500),
            "{context}": _sanitize_field(str(context or "General research"), max_len=500),
            "{year}": _sanitize_field(str(year), max_len=10),
        }
        _pattern = re.compile("|".join(re.escape(k) for k in _subs))
        prompt = _pattern.sub(lambda m: _subs[m.group(0)], self.QUERY_GENERATION_PROMPT)

        response, _ = invoke_claude(
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

        # Fallback covers both parse failure and None/empty LLM response.
        # Sanitize topic so the fallback query doesn't carry injection payloads.
        if not queries:
            queries.append(SearchQuery(
                query=f"{_sanitize_field(str(topic), max_len=200)} {_sanitize_field(str(year), max_len=10)}",
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
        # BUG-SEC-7: sanitize query before embedding in prompt
        safe_query_fallback = _sanitize_field(query.query, max_len=500)
        search_prompt = f"""Use web search to find information about: {safe_query_fallback}

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

        response, _ = invoke_claude(
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

        # Sanitize via unified _sanitize_field to ensure injection prefix stripping
        safe_query = _sanitize_field(query.query, max_len=500).strip()
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

            # BUG-COR-3 / TD-MOD-4: use Popen + killpg so the full subprocess tree
            # is killed on timeout rather than leaving orphaned claude CLI processes.
            import signal
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(BASE_DIR),
                env=env,
                start_new_session=True,
            )
            try:
                stdout_bytes, stderr_bytes = proc.communicate(
                    input=search_prompt.encode(),
                    timeout=self.timeout_seconds,
                )
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, OSError):
                    proc.kill()
                proc.wait()
                logger.warning("[WebSearch] Timed out after %ds — process group killed for query: %s", self.timeout_seconds, query.query[:60])
                return []

            if proc.returncode == 0:
                response = stdout_bytes.decode(errors='replace').strip()
                parsed = self._parse_search_results(response)
                if parsed:
                    return parsed
                # M6 fix: warn when web search returned parseable-but-empty results
                logger.warning("[WebSearch] Response parsed but returned no results (len=%d); check Claude WebSearch output", len(response))
            else:
                logger.warning("[WebSearch] Claude CLI returned non-zero: %s", stderr_bytes.decode(errors='replace')[:200])

        except FileNotFoundError:
            logger.warning("[WebSearch] claude CLI not found; falling back to LLM search")
        except Exception as exc:
            logger.warning("[WebSearch] Unexpected error: %s", exc)

        return []

    def _simulate_search(self, query: SearchQuery) -> List[SearchResult]:
        """Simulate search results for testing."""
        encoded = urllib.parse.quote(query.query, safe='')
        return [
            SearchResult(
                title=f"Documentation for {query.query}",
                url=f"https://docs.example.com/{encoded}",
                snippet=f"Official documentation about {query.query}...",
                source="docs.example.com",
                relevance_score=0.9,
                is_primary_source=True
            ),
            SearchResult(
                title=f"Tutorial: {query.query}",
                url=f"https://tutorial.example.com/{encoded}",
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
                    try:
                        if not isinstance(r, dict):
                            continue
                        source = r.get("source") or ""
                        if not source and r.get("url"):
                            import urllib.parse
                            source = urllib.parse.urlparse(r["url"]).netloc

                        _src_lower = source.lower()
                        is_primary = any(
                            _src_lower == domain or _src_lower.endswith('.' + domain)
                            for domain in self.PRIMARY_SOURCE_DOMAINS
                        )

                        try:
                            relevance = float(r.get("relevance", 0.5) or 0.5)
                        except (TypeError, ValueError):
                            relevance = 0.5

                        results.append(SearchResult(
                            title=r.get("title", ""),
                            url=r.get("url", ""),
                            snippet=r.get("snippet", ""),
                            source=source,
                            relevance_score=relevance,
                            is_primary_source=is_primary
                        ))
                    except Exception as item_exc:
                        logger.debug("[WebResearcher] _parse_search_results item error: %s", item_exc)
        except Exception as e:
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
            f"Title: {_sanitize_field(r.title or '', 200)}\nURL: {_sanitize_field(r.url or '', 300)}\nSnippet: {_sanitize_field((r.snippet or '')[:300], 300)}"
            for r in results[:10]  # Limit to top 10
        ])

        import string as _string
        prompt = _string.Template(self.INSIGHT_EXTRACTION_PROMPT).safe_substitute(
            topic=_sanitize_field(topic, max_len=500),
            results=results_text
        )

        response, _ = invoke_claude(
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

        def _relevance_key(r):
            try:
                return (r.is_primary_source, float(r.relevance_score or 0))
            except (TypeError, ValueError):
                return (r.is_primary_source, 0.0)

        unique_results.sort(key=_relevance_key, reverse=True)

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
        # Greedy {.*} so nested objects aren't truncated at first inner '}'.
        json_match = re.search(r'```(?:json)?\s*(\{.*\})\s*```', text[:10_000], re.DOTALL)
        if json_match:
            try:
                result = json.loads(json_match.group(1))
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        # Balanced-brace scan; correctly skips braces inside string literals.
        # Limited to 200k chars to prevent O(N^2) worst case on adversarial input.
        # Collects ALL valid top-level dicts and returns the one with the most keys —
        # this avoids returning a small preamble error-dict instead of the real data dict.
        text = text[:200_000]
        candidates = []
        start = text.find('{')
        while start != -1:
            depth = 0
            in_string = False
            escape_next = False
            end = -1
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
            if end == -1:
                break
            try:
                result = json.loads(text[start:end + 1])
                if isinstance(result, dict):
                    candidates.append(result)
            except json.JSONDecodeError:
                pass
            start = text.find('{', (end + 1) if end != -1 else (start + 1))

        if candidates:
            return max(candidates, key=lambda d: len(d))
        return None


if __name__ == "__main__":
    # Self-test
    print("Web Researcher - Self Test")
    print("=" * 50)

    researcher = WebResearcher(model=ModelType.CLAUDE_HAIKU)

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
