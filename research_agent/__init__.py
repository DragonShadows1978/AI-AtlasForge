"""
Research Agent - Active research capability for the PLANNING stage.

This module provides tools to search the web, fetch documentation,
find prior art, and synthesize findings into actionable recommendations.

The key insight: Don't rely solely on training corpora. Go out and learn
new things from the internet to make evidence-based decisions.

Key Components:
    - ResearchOrchestrator: Main coordinator for research activities
    - WebResearcher: Web search integration using WebSearch tool
    - DocFetcher: Documentation fetching using WebFetch tool
    - PriorArtFinder: Search for similar solutions
    - KnowledgeSynthesizer: Combine findings into recommendations

Usage:
    from research_agent import ResearchOrchestrator

    researcher = ResearchOrchestrator()
    findings = researcher.research_topic(
        topic="adversarial testing for LLMs",
        context="Building a testing framework for AI agents"
    )
"""

from .research_orchestrator import (
    ResearchOrchestrator,
    ResearchConfig,
    ResearchFindings
)
from .web_researcher import (
    WebResearcher,
    SearchResult,
    SearchQuery
)
from .knowledge_synthesizer import (
    KnowledgeSynthesizer,
    SynthesisResult,
    Recommendation
)

__all__ = [
    'ResearchOrchestrator',
    'ResearchConfig',
    'ResearchFindings',
    'WebResearcher',
    'SearchResult',
    'SearchQuery',
    'KnowledgeSynthesizer',
    'SynthesisResult',
    'Recommendation'
]
