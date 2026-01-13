#!/usr/bin/env python3
"""
Insight Extractor - AtlasForge Enhancement Feature 2.2

Extracts structured insights from exploration results.
Processes Claude's exploration output to identify:
- Files explored and their purposes
- Relationships discovered
- Patterns identified
- Key learnings

Works with ExplorationGraph to build persistent exploration memory.
"""

import re
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass

try:
    from .exploration_graph import ExplorationGraph, ExplorationNode
except ImportError:
    from exploration_graph import ExplorationGraph, ExplorationNode


# =============================================================================
# EXTRACTION PATTERNS
# =============================================================================

# Patterns to identify file references in text
FILE_PATTERNS = [
    r'`([^`]+\.[a-z]{2,4})`',  # Backtick-quoted files
    r'(?:file|path|reading|read|opened|opening)\s+["\']?([^\s"\']+\.[a-z]{2,4})',  # file references
    r'(?:in|from|to)\s+([/\w]+\.[a-z]{2,4})',  # in/from/to file references
    r'([/\w]+/[\w]+\.[a-z]{2,4})',  # path-like strings
]

# Patterns to identify relationships
RELATIONSHIP_PATTERNS = [
    (r'(\w+)\s+(?:imports?|requires?)\s+(\w+)', 'imports'),
    (r'(\w+)\s+(?:calls?|invokes?)\s+(\w+)', 'calls'),
    (r'(\w+)\s+(?:uses?|utilizes?)\s+(\w+)', 'uses'),
    (r'(\w+)\s+(?:extends?|inherits?(?:\s+from)?)\s+(\w+)', 'extends'),
    (r'(\w+)\s+(?:depends?\s+on)\s+(\w+)', 'depends_on'),
    (r'(\w+)\s+(?:implements?)\s+(\w+)', 'implements'),
]

# Patterns to identify insights/learnings
INSIGHT_INDICATORS = [
    'important:', 'note:', 'key point:', 'observation:',
    'i found', 'i discovered', 'i noticed', 'interesting',
    'pattern:', 'the codebase uses', 'this shows',
    'takeaway:', 'insight:', 'gotcha:', 'warning:',
    'best practice:', 'anti-pattern:'
]

# Patterns to identify concepts
CONCEPT_PATTERNS = [
    r'uses?\s+(?:the\s+)?(\w+)\s+pattern',  # Uses X pattern
    r'implements?\s+(\w+)',  # Implements X
    r'(\w+)\s+(?:approach|strategy|methodology)',  # X approach
    r'(\w+)\s+architecture',  # X architecture
]


# =============================================================================
# EXTRACTION FUNCTIONS
# =============================================================================

@dataclass
class ExtractedFile:
    """A file reference extracted from text."""
    path: str
    context: str  # Surrounding text
    line_number: Optional[int]


@dataclass
class ExtractedRelationship:
    """A relationship extracted from text."""
    source: str
    target: str
    relationship_type: str
    context: str


@dataclass
class ExtractedInsight:
    """An insight extracted from text."""
    text: str
    insight_type: str
    confidence: float
    indicators_found: List[str]


@dataclass
class ExtractionResult:
    """Complete extraction result from a text."""
    source_text: str
    files: List[ExtractedFile]
    relationships: List[ExtractedRelationship]
    insights: List[ExtractedInsight]
    concepts: List[str]
    summary_generated: str
    timestamp: str


def extract_file_references(text: str) -> List[ExtractedFile]:
    """
    Extract file path references from text.

    Args:
        text: The text to analyze

    Returns:
        List of ExtractedFile objects
    """
    files = []
    seen_paths = set()

    for pattern in FILE_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            path = match.group(1)

            # Basic validation - looks like a file path
            if '.' not in path or len(path) < 3:
                continue
            if path in seen_paths:
                continue

            seen_paths.add(path)

            # Get context (surrounding 100 chars)
            start = max(0, match.start() - 50)
            end = min(len(text), match.end() + 50)
            context = text[start:end].strip()

            files.append(ExtractedFile(
                path=path,
                context=context,
                line_number=None
            ))

    return files


def extract_relationships(text: str) -> List[ExtractedRelationship]:
    """
    Extract relationships between components from text.

    Args:
        text: The text to analyze

    Returns:
        List of ExtractedRelationship objects
    """
    relationships = []
    seen = set()

    for pattern, rel_type in RELATIONSHIP_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            source = match.group(1).lower()
            target = match.group(2).lower()

            # Skip if already seen this relationship
            key = f"{source}:{rel_type}:{target}"
            if key in seen:
                continue
            seen.add(key)

            # Get context
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            context = text[start:end].strip()

            relationships.append(ExtractedRelationship(
                source=source,
                target=target,
                relationship_type=rel_type,
                context=context
            ))

    return relationships


def extract_insights(text: str) -> List[ExtractedInsight]:
    """
    Extract insights and learnings from text.

    Args:
        text: The text to analyze

    Returns:
        List of ExtractedInsight objects
    """
    insights = []
    text_lower = text.lower()

    # Split into sentences
    sentences = re.split(r'[.!?]\s+', text)

    for sentence in sentences:
        sentence_lower = sentence.lower()
        indicators = []

        # Check for insight indicators
        for indicator in INSIGHT_INDICATORS:
            if indicator in sentence_lower:
                indicators.append(indicator)

        if indicators:
            # Determine insight type
            if any(i in sentence_lower for i in ['gotcha', 'warning', 'careful']):
                insight_type = 'gotcha'
            elif any(i in sentence_lower for i in ['pattern', 'design']):
                insight_type = 'pattern'
            elif any(i in sentence_lower for i in ['best practice', 'should']):
                insight_type = 'best_practice'
            elif any(i in sentence_lower for i in ['anti-pattern', 'avoid', "don't"]):
                insight_type = 'anti_pattern'
            else:
                insight_type = 'observation'

            # Calculate confidence based on indicator strength
            confidence = min(0.5 + len(indicators) * 0.15, 1.0)

            insights.append(ExtractedInsight(
                text=sentence.strip(),
                insight_type=insight_type,
                confidence=confidence,
                indicators_found=indicators
            ))

    return insights


def extract_concepts(text: str) -> List[str]:
    """
    Extract concept/pattern names from text.

    Args:
        text: The text to analyze

    Returns:
        List of concept names
    """
    concepts = set()

    for pattern in CONCEPT_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            concept = match.group(1).lower()
            if len(concept) >= 3:  # Filter very short matches
                concepts.add(concept)

    return list(concepts)


def generate_summary(
    files: List[ExtractedFile],
    relationships: List[ExtractedRelationship],
    insights: List[ExtractedInsight],
    concepts: List[str]
) -> str:
    """Generate a brief summary of the extraction."""
    parts = []

    if files:
        parts.append(f"Explored {len(files)} files")

    if relationships:
        parts.append(f"Found {len(relationships)} relationships")

    if insights:
        parts.append(f"Extracted {len(insights)} insights")

    if concepts:
        parts.append(f"Identified concepts: {', '.join(concepts[:5])}")

    return ". ".join(parts) if parts else "No significant extractions"


def extract_from_text(text: str) -> ExtractionResult:
    """
    Perform complete extraction from text.

    Args:
        text: The exploration output to analyze

    Returns:
        ExtractionResult with all extracted information
    """
    files = extract_file_references(text)
    relationships = extract_relationships(text)
    insights = extract_insights(text)
    concepts = extract_concepts(text)
    summary = generate_summary(files, relationships, insights, concepts)

    return ExtractionResult(
        source_text=text[:500] + "..." if len(text) > 500 else text,
        files=files,
        relationships=relationships,
        insights=insights,
        concepts=concepts,
        summary_generated=summary,
        timestamp=datetime.now().isoformat()
    )


# =============================================================================
# GRAPH INTEGRATION
# =============================================================================

def populate_graph_from_extraction(
    graph: ExplorationGraph,
    extraction: ExtractionResult,
    mission_id: str,
    base_path: Optional[str] = None
) -> Dict:
    """
    Populate an exploration graph from extraction results.

    Args:
        graph: The ExplorationGraph to populate
        extraction: The ExtractionResult to process
        mission_id: ID of the current mission
        base_path: Optional base path to prepend to relative paths

    Returns:
        Summary of what was added
    """
    added = {
        'files': 0,
        'concepts': 0,
        'relationships': 0,
        'insights': 0
    }

    # Add file nodes
    file_nodes = {}
    for f in extraction.files:
        path = f.path
        if base_path and not path.startswith('/'):
            path = f"{base_path}/{path}"

        node = graph.add_file_node(
            path=path,
            summary=f.context,
            mission_id=mission_id,
            tags=_infer_tags_from_path(path)
        )
        file_nodes[f.path] = node
        added['files'] += 1

    # Add concept nodes
    concept_nodes = {}
    for concept in extraction.concepts:
        node = graph.add_concept_node(
            name=concept,
            summary=f"Concept identified during exploration",
            mission_id=mission_id,
            tags=[concept]
        )
        concept_nodes[concept] = node
        added['concepts'] += 1

    # Add insights
    for insight in extraction.insights:
        graph.add_insight(
            insight_type=insight.insight_type,
            title=insight.text[:50] + "..." if len(insight.text) > 50 else insight.text,
            description=insight.text,
            mission_id=mission_id,
            tags=insight.indicators_found,
            confidence=insight.confidence
        )
        added['insights'] += 1

    # Add relationships (best effort - source/target must exist as nodes)
    for rel in extraction.relationships:
        # Try to find matching nodes
        source_node = None
        target_node = None

        # Check file nodes
        for path, node in file_nodes.items():
            if rel.source.lower() in path.lower():
                source_node = node
            if rel.target.lower() in path.lower():
                target_node = node

        # Check concept nodes
        if rel.source in concept_nodes:
            source_node = concept_nodes[rel.source]
        if rel.target in concept_nodes:
            target_node = concept_nodes[rel.target]

        if source_node and target_node:
            graph.add_edge(
                source_node.id,
                target_node.id,
                relationship=rel.relationship_type,
                mission_id=mission_id,
                context=rel.context
            )
            added['relationships'] += 1

    return added


def _infer_tags_from_path(path: str) -> List[str]:
    """Infer tags from a file path."""
    tags = []
    path_lower = path.lower()

    # File type tags
    if path.endswith('.py'):
        tags.append('python')
    elif path.endswith('.js') or path.endswith('.ts'):
        tags.append('javascript')
    elif path.endswith('.md'):
        tags.append('documentation')
    elif path.endswith('.json') or path.endswith('.yaml'):
        tags.append('config')

    # Directory-based tags
    if '/test' in path_lower or 'test_' in path_lower:
        tags.append('test')
    if '/api' in path_lower or 'handler' in path_lower:
        tags.append('api')
    if '/model' in path_lower or '/db' in path_lower:
        tags.append('database')
    if '/service' in path_lower:
        tags.append('service')
    if '/util' in path_lower or '/helper' in path_lower:
        tags.append('utility')
    if '/config' in path_lower:
        tags.append('config')

    return tags


# =============================================================================
# EXPLORATION ADVISOR
# =============================================================================

class ExplorationAdvisor:
    """
    Provides advice on what to explore based on exploration history.

    Helps avoid redundant exploration and suggests related areas.
    """

    def __init__(self, graph: ExplorationGraph):
        self.graph = graph

    def should_explore(self, path: str, force: bool = False) -> Tuple[bool, str]:
        """
        Advise whether a file should be explored.

        Args:
            path: The file path to consider
            force: If True, always recommend exploration

        Returns:
            Tuple of (should_explore, reason)
        """
        if force:
            return True, "Forced exploration requested"

        existing = self.graph.get_file_node(path)
        if existing is None:
            return True, "File has not been explored before"

        # Check exploration recency
        last_explored = datetime.fromisoformat(existing.last_explored)
        age_days = (datetime.now() - last_explored).days

        if age_days > 30:
            return True, f"File hasn't been explored in {age_days} days"

        if existing.exploration_count < 2:
            return True, "File has only been briefly explored"

        # Check if content might have changed (if we have a hash)
        if existing.content_hash:
            return False, f"File explored {existing.exploration_count} times, last {age_days} days ago"

        return False, f"File already well-explored: {existing.summary[:100]}"

    def suggest_related(self, node_id: str, limit: int = 5) -> List[Dict]:
        """
        Suggest related nodes to explore.

        Args:
            node_id: The current node ID
            limit: Maximum suggestions

        Returns:
            List of suggestion dicts
        """
        suggestions = []
        seen = {node_id}

        # Get directly related nodes
        related = self.graph.get_related_nodes(node_id)
        for node, edge in related:
            if node.id not in seen:
                suggestions.append({
                    'node': node,
                    'reason': f"Related via '{edge.relationship}' relationship",
                    'priority': 'high'
                })
                seen.add(node.id)

        # Get nodes with same tags
        current_node = self.graph.nodes.get(node_id)
        if current_node:
            for tag in current_node.tags[:3]:  # Top 3 tags
                for node in self.graph.get_nodes_by_tag(tag):
                    if node.id not in seen:
                        suggestions.append({
                            'node': node,
                            'reason': f"Shares tag '{tag}'",
                            'priority': 'medium'
                        })
                        seen.add(node.id)

        return suggestions[:limit]

    def get_unexplored_areas(self, limit: int = 10) -> List[str]:
        """
        Identify areas that might need more exploration.

        Returns tags/areas with few explorations.
        """
        tag_counts = {}
        for tag, nids in self.graph._node_by_tag.items():
            total_explorations = sum(
                self.graph.nodes[nid].exploration_count
                for nid in nids if nid in self.graph.nodes
            )
            tag_counts[tag] = total_explorations / len(nids) if nids else 0

        # Sort by average exploration count (ascending)
        sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1])
        return [tag for tag, _ in sorted_tags[:limit]]

    def what_do_we_know(self, topic: str, use_semantic: bool = True) -> Dict:
        """
        Answer "what do we already know about X?"

        Uses semantic search when available for better matching of paraphrased queries.

        Args:
            topic: The topic to query
            use_semantic: If True, use semantic search (default); falls back to keyword

        Returns:
            Dict with relevant knowledge
        """
        result = {
            'topic': topic,
            'files': [],
            'concepts': [],
            'insights': [],
            'relationships': [],
            'search_method': 'semantic' if use_semantic else 'keyword'
        }

        # Use semantic search if available
        if use_semantic:
            # Semantic search for nodes
            search_results = self.graph.hybrid_search(topic, top_k=20)

            for node, score in search_results:
                item = {
                    'name': node.name,
                    'summary': node.summary,
                    'relevance_score': round(score, 3),
                    'exploration_count': node.exploration_count
                }

                if node.node_type == 'file':
                    item['path'] = node.path
                    result['files'].append(item)
                elif node.node_type == 'concept':
                    result['concepts'].append(item)
                elif node.node_type == 'pattern':
                    result['concepts'].append(item)

            # Also search insights with keyword matching (insights don't have embeddings yet)
            topic_lower = topic.lower()
            for insight in self.graph.insights.values():
                if (topic_lower in insight.title.lower() or
                    topic_lower in insight.description.lower()):
                    result['insights'].append({
                        'title': insight.title,
                        'type': insight.insight_type,
                        'confidence': insight.confidence
                    })
        else:
            # Fall back to pure keyword search
            result = self._keyword_what_do_we_know(topic)

        # Summary
        result['summary'] = (
            f"Found {len(result['files'])} files, {len(result['concepts'])} concepts, "
            f"and {len(result['insights'])} insights related to '{topic}' "
            f"(using {result['search_method']} search)"
        )

        return result

    def _keyword_what_do_we_know(self, topic: str) -> Dict:
        """Legacy keyword-based what_do_we_know implementation."""
        topic_lower = topic.lower()
        result = {
            'topic': topic,
            'files': [],
            'concepts': [],
            'insights': [],
            'relationships': [],
            'search_method': 'keyword'
        }

        # Search nodes
        for node in self.graph.nodes.values():
            if (topic_lower in node.name.lower() or
                topic_lower in node.summary.lower() or
                any(topic_lower in tag for tag in node.tags)):

                if node.node_type == 'file':
                    result['files'].append({
                        'path': node.path,
                        'name': node.name,
                        'summary': node.summary,
                        'exploration_count': node.exploration_count
                    })
                elif node.node_type == 'concept':
                    result['concepts'].append({
                        'name': node.name,
                        'summary': node.summary
                    })

        # Search insights
        for insight in self.graph.insights.values():
            if (topic_lower in insight.title.lower() or
                topic_lower in insight.description.lower()):
                result['insights'].append({
                    'title': insight.title,
                    'type': insight.insight_type,
                    'confidence': insight.confidence
                })

        return result

    def semantic_search(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Perform direct semantic search on the exploration graph.

        Args:
            query: Search query
            top_k: Max results

        Returns:
            List of search results with relevance scores
        """
        results = self.graph.hybrid_search(query, top_k=top_k)
        return [
            {
                'id': node.id,
                'type': node.node_type,
                'name': node.name,
                'path': node.path,
                'summary': node.summary,
                'score': round(score, 3),
                'tags': node.tags
            }
            for node, score in results
        ]

    def what_insights_do_we_have(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Semantic search for insights.

        Searches through all recorded insights using semantic similarity.

        Args:
            query: Search query (can be natural language)
            top_k: Maximum number of results to return

        Returns:
            List of matching insights with similarity scores
        """
        results = self.graph.semantic_search_insights(query, top_k)
        return [
            {
                'id': insight.id,
                'title': insight.title,
                'type': insight.insight_type,
                'description': insight.description,
                'confidence': insight.confidence,
                'similarity': round(score, 4),
                'tags': insight.tags,
                'verified': insight.verified,
                'mission_id': insight.mission_id
            }
            for insight, score in results
        ]

    def find_related_insights(self, insight_id: str, top_k: int = 5) -> List[Dict]:
        """
        Find insights similar to a given insight.

        Args:
            insight_id: The insight ID to find similar insights for
            top_k: Maximum number of results

        Returns:
            List of similar insights with scores
        """
        insight = self.graph.insights.get(insight_id)
        if not insight:
            return []

        # Use the insight's text as query
        query = insight.get_searchable_text()
        results = self.graph.semantic_search_insights(query, top_k + 1)

        # Filter out the original insight
        return [
            {
                'id': i.id,
                'title': i.title,
                'type': i.insight_type,
                'description': i.description[:200],
                'similarity': round(score, 4)
            }
            for i, score in results if i.id != insight_id
        ][:top_k]


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Insight Extractor - AtlasForge Enhancement")
    print("=" * 50)

    # Sample exploration output
    exploration_text = """
    I've explored the codebase and found several interesting patterns.

    Reading `/src/api/handlers.py`, I discovered that the API uses a decorator pattern
    for authentication. The handlers module imports the auth_service module.

    Important: The codebase uses the repository pattern for data access.
    Services don't query the database directly - they go through repositories.

    Looking at `/src/services/user_service.py`, I noticed it calls the user_repository.
    The user_service depends on both the repository and the cache module.

    Gotcha: Sessions expire after 1 hour and require manual refresh token handling.

    The project implements JWT authentication with role-based access control.
    This is a best practice: tokens are short-lived with refresh token rotation.
    """

    print("\nExtracting from sample exploration output...")
    result = extract_from_text(exploration_text)

    print(f"\nExtraction Summary: {result.summary_generated}")

    print(f"\nFiles found ({len(result.files)}):")
    for f in result.files:
        print(f"  - {f.path}")

    print(f"\nRelationships found ({len(result.relationships)}):")
    for r in result.relationships:
        print(f"  - {r.source} {r.relationship_type} {r.target}")

    print(f"\nInsights found ({len(result.insights)}):")
    for i in result.insights:
        print(f"  - [{i.insight_type}] {i.text[:60]}...")

    print(f"\nConcepts found ({len(result.concepts)}):")
    for c in result.concepts:
        print(f"  - {c}")

    # Populate a graph
    print("\nPopulating exploration graph...")
    graph = ExplorationGraph(Path("/tmp/insight_demo"))
    added = populate_graph_from_extraction(graph, result, "demo_mission")
    graph.save()

    print(f"Added to graph: {added}")

    # Test advisor
    print("\nTesting ExplorationAdvisor...")
    advisor = ExplorationAdvisor(graph)

    should, reason = advisor.should_explore("/src/api/handlers.py")
    print(f"Should explore handlers.py? {should} - {reason}")

    knowledge = advisor.what_do_we_know("authentication")
    print(f"\nWhat we know about 'authentication': {knowledge['summary']}")

    print("\nDemo complete!")
