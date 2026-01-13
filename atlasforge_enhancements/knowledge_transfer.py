#!/usr/bin/env python3
"""
Knowledge Transfer - Cross-Mission Knowledge Retrieval

Enables loading and querying exploration graphs from prior missions.
Provides relevance scoring and starting point suggestions based on
prior mission knowledge.

Key Features:
1. Discover prior missions with exploration data
2. Load exploration graphs from prior missions
3. Compute relevance scores for prior missions
4. Search across prior missions semantically
5. Suggest starting points based on prior knowledge
6. Merge relevant insights from prior missions
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
import numpy as np

try:
    from .exploration_graph import ExplorationGraph, ExplorationNode, ExplorationInsight, EmbeddingModel
except ImportError:
    from exploration_graph import ExplorationGraph, ExplorationNode, ExplorationInsight, EmbeddingModel


@dataclass
class PriorMissionInfo:
    """Information about a prior mission."""
    mission_id: str
    mission_name: str
    created_at: str
    last_modified: str
    node_count: int
    insight_count: int
    edge_count: int
    top_tags: List[str]
    workspace_path: str

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class KnowledgeSearchResult:
    """A search result from prior mission knowledge."""
    mission_id: str
    node_id: str
    node_type: str
    name: str
    path: Optional[str]
    summary: str
    tags: List[str]
    mission_relevance: float  # 0-1, how relevant the mission is
    search_relevance: float   # 0-1, how relevant this result is to query
    combined_score: float     # Combined ranking score

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class StartingPointSuggestion:
    """A suggested starting point based on prior missions."""
    path: Optional[str]
    name: str
    suggestion_type: str  # 'file', 'concept', 'insight'
    description: str
    source_mission: str
    relevance_score: float
    exploration_count: int

    def to_dict(self) -> Dict:
        return asdict(self)


class KnowledgeTransfer:
    """
    Transfer and query knowledge from prior missions.

    Enables cross-mission learning by loading exploration graphs
    from completed missions and querying them semantically.
    """

    def __init__(
        self,
        current_mission_id: str,
        missions_base: Path,
        current_mission_context: Optional[str] = None
    ):
        """
        Initialize Knowledge Transfer.

        Args:
            current_mission_id: ID of the current mission
            missions_base: Path to /missions/ directory
            current_mission_context: Optional description/context of current mission
        """
        self.current_mission_id = current_mission_id
        self.missions_base = Path(missions_base)
        self.current_mission_context = current_mission_context or ""

        # Cached data
        self._prior_graphs: Dict[str, ExplorationGraph] = {}
        self._prior_info: Dict[str, PriorMissionInfo] = {}
        self._relevance_cache: Dict[str, float] = {}

        # Embedding of current mission context (for relevance scoring)
        self._context_embedding: Optional[np.ndarray] = None

    def set_current_context(self, context: str):
        """Set the current mission context for relevance scoring."""
        self.current_mission_context = context
        self._context_embedding = None  # Clear cache
        self._relevance_cache.clear()

    def _get_context_embedding(self) -> Optional[np.ndarray]:
        """Get embedding of current mission context."""
        if self._context_embedding is not None:
            return self._context_embedding

        if not self.current_mission_context or not EmbeddingModel.is_available():
            return None

        embedding = EmbeddingModel.encode_single(self.current_mission_context)
        if embedding:
            self._context_embedding = np.array(embedding)
        return self._context_embedding

    def discover_prior_missions(self, limit: int = 50) -> List[PriorMissionInfo]:
        """
        Find all missions with exploration data.

        Scans the missions directory for missions with AtlasForge exploration data.

        Args:
            limit: Maximum number of missions to return

        Returns:
            List of PriorMissionInfo sorted by last modified date (newest first)
        """
        missions = []

        if not self.missions_base.exists():
            return missions

        # Scan for mission_* directories
        for mission_dir in self.missions_base.iterdir():
            if not mission_dir.is_dir():
                continue

            # Skip current mission
            mission_id = mission_dir.name
            if mission_id == self.current_mission_id:
                continue

            # Check for exploration data
            exploration_path = mission_dir / "workspace" / "atlasforge_data" / "exploration"
            nodes_file = exploration_path / "nodes.json"

            if not nodes_file.exists():
                continue

            try:
                # Load basic stats
                with open(nodes_file, 'r') as f:
                    nodes_data = json.load(f)

                edges_file = exploration_path / "edges.json"
                edges_count = 0
                if edges_file.exists():
                    with open(edges_file, 'r') as f:
                        edges_count = len(json.load(f))

                insights_file = exploration_path / "insights.json"
                insights_count = 0
                if insights_file.exists():
                    with open(insights_file, 'r') as f:
                        insights_count = len(json.load(f))

                # Extract top tags
                tag_counts: Dict[str, int] = {}
                for node in nodes_data:
                    for tag in node.get('tags', []):
                        tag_counts[tag] = tag_counts.get(tag, 0) + 1
                top_tags = sorted(tag_counts.keys(), key=lambda t: tag_counts[t], reverse=True)[:5]

                # Get modification times
                created_at = datetime.fromtimestamp(nodes_file.stat().st_ctime).isoformat()
                last_modified = datetime.fromtimestamp(nodes_file.stat().st_mtime).isoformat()

                # Try to get mission name from mission.json
                mission_name = mission_id
                mission_json = mission_dir / "workspace" / "mission.json"
                if mission_json.exists():
                    try:
                        with open(mission_json, 'r') as f:
                            mission_data = json.load(f)
                            mission_name = mission_data.get('problem_statement', mission_id)[:100]
                    except:
                        pass

                info = PriorMissionInfo(
                    mission_id=mission_id,
                    mission_name=mission_name,
                    created_at=created_at,
                    last_modified=last_modified,
                    node_count=len(nodes_data),
                    insight_count=insights_count,
                    edge_count=edges_count,
                    top_tags=top_tags,
                    workspace_path=str(mission_dir / "workspace")
                )

                missions.append(info)
                self._prior_info[mission_id] = info

            except Exception as e:
                # Skip missions with corrupted data
                print(f"[KnowledgeTransfer] Skipping {mission_id}: {e}")
                continue

        # Sort by last modified (newest first)
        missions.sort(key=lambda m: m.last_modified, reverse=True)
        return missions[:limit]

    def load_prior_mission(self, mission_id: str) -> Optional[ExplorationGraph]:
        """
        Load exploration graph from a prior mission.

        Args:
            mission_id: The mission ID to load

        Returns:
            ExplorationGraph or None if not found/loadable
        """
        # Check cache
        if mission_id in self._prior_graphs:
            return self._prior_graphs[mission_id]

        # Find mission path
        mission_dir = self.missions_base / mission_id
        exploration_path = mission_dir / "workspace" / "atlasforge_data" / "exploration"

        if not exploration_path.exists():
            return None

        try:
            graph = ExplorationGraph(storage_path=exploration_path)
            self._prior_graphs[mission_id] = graph
            return graph
        except Exception as e:
            print(f"[KnowledgeTransfer] Failed to load {mission_id}: {e}")
            return None

    def compute_relevance(self, prior_mission_id: str) -> float:
        """
        Score how relevant a prior mission is to the current context.

        Uses embedding similarity between current mission context and
        the prior mission's key concepts and tags.

        Args:
            prior_mission_id: The prior mission to score

        Returns:
            Relevance score 0.0-1.0
        """
        # Check cache
        if prior_mission_id in self._relevance_cache:
            return self._relevance_cache[prior_mission_id]

        # Default low relevance
        default_relevance = 0.3

        # Load prior mission graph
        graph = self.load_prior_mission(prior_mission_id)
        if not graph:
            return default_relevance

        # If no context or embeddings, return default
        context_emb = self._get_context_embedding()
        if context_emb is None:
            self._relevance_cache[prior_mission_id] = default_relevance
            return default_relevance

        # Build text representation of prior mission
        prior_texts = []

        # Add top node summaries
        for node in list(graph.nodes.values())[:20]:
            prior_texts.append(node.summary)

        # Add insight titles
        for insight in list(graph.insights.values())[:10]:
            prior_texts.append(insight.title)

        # Add top tags
        info = self._prior_info.get(prior_mission_id)
        if info and info.top_tags:
            prior_texts.append(" ".join(info.top_tags))

        if not prior_texts:
            self._relevance_cache[prior_mission_id] = default_relevance
            return default_relevance

        # Generate embedding for prior mission
        prior_text = " ".join(prior_texts)
        prior_emb = EmbeddingModel.encode_single(prior_text)
        if prior_emb is None:
            self._relevance_cache[prior_mission_id] = default_relevance
            return default_relevance

        prior_emb = np.array(prior_emb)

        # Compute cosine similarity
        similarity = np.dot(context_emb, prior_emb) / (
            np.linalg.norm(context_emb) * np.linalg.norm(prior_emb)
        )
        relevance = float(max(0.0, min(1.0, similarity)))

        self._relevance_cache[prior_mission_id] = relevance
        return relevance

    def get_relevant_prior_knowledge(
        self,
        query: str,
        top_k: int = 10,
        min_mission_relevance: float = 0.3,
        min_search_relevance: float = 0.3
    ) -> List[KnowledgeSearchResult]:
        """
        Search across all prior missions for relevant knowledge.

        Args:
            query: The search query
            top_k: Maximum results to return
            min_mission_relevance: Minimum mission relevance threshold
            min_search_relevance: Minimum search result relevance

        Returns:
            List of KnowledgeSearchResult sorted by combined score
        """
        results = []

        # Discover prior missions
        prior_missions = self.discover_prior_missions()

        for mission_info in prior_missions:
            # Check mission relevance
            mission_relevance = self.compute_relevance(mission_info.mission_id)
            if mission_relevance < min_mission_relevance:
                continue

            # Load and search the graph
            graph = self.load_prior_mission(mission_info.mission_id)
            if not graph:
                continue

            # Perform semantic search
            search_results = graph.semantic_search(
                query=query,
                top_k=top_k,
                min_similarity=min_search_relevance
            )

            for node, search_score in search_results:
                combined_score = mission_relevance * 0.3 + search_score * 0.7

                results.append(KnowledgeSearchResult(
                    mission_id=mission_info.mission_id,
                    node_id=node.id,
                    node_type=node.node_type,
                    name=node.name,
                    path=node.path,
                    summary=node.summary,
                    tags=node.tags,
                    mission_relevance=round(mission_relevance, 4),
                    search_relevance=round(search_score, 4),
                    combined_score=round(combined_score, 4)
                ))

        # Sort by combined score
        results.sort(key=lambda r: r.combined_score, reverse=True)
        return results[:top_k]

    def suggest_starting_points(
        self,
        current_mission_description: Optional[str] = None,
        top_k: int = 10
    ) -> List[StartingPointSuggestion]:
        """
        Suggest exploration starting points based on prior missions.

        Identifies the most relevant files, concepts, and insights from
        prior missions that might be useful for the current mission.

        Args:
            current_mission_description: Description of current mission (uses context if None)
            top_k: Maximum suggestions to return

        Returns:
            List of StartingPointSuggestion sorted by relevance
        """
        suggestions = []

        # Use provided description or current context
        context = current_mission_description or self.current_mission_context
        if context:
            self.set_current_context(context)

        # Get relevant prior missions
        prior_missions = self.discover_prior_missions()

        for mission_info in prior_missions:
            relevance = self.compute_relevance(mission_info.mission_id)
            if relevance < 0.3:
                continue

            graph = self.load_prior_mission(mission_info.mission_id)
            if not graph:
                continue

            # Get most-explored files from this mission
            for node in graph.get_most_explored(limit=5):
                if node.node_type == 'file' and node.path:
                    suggestions.append(StartingPointSuggestion(
                        path=node.path,
                        name=node.name,
                        suggestion_type='file',
                        description=node.summary,
                        source_mission=mission_info.mission_id,
                        relevance_score=relevance * (0.5 + node.exploration_count * 0.1),
                        exploration_count=node.exploration_count
                    ))
                elif node.node_type == 'concept':
                    suggestions.append(StartingPointSuggestion(
                        path=None,
                        name=node.name,
                        suggestion_type='concept',
                        description=node.summary,
                        source_mission=mission_info.mission_id,
                        relevance_score=relevance * 0.8,
                        exploration_count=node.exploration_count
                    ))

            # Add high-confidence insights
            for insight in list(graph.insights.values())[:3]:
                if insight.confidence >= 0.7:
                    suggestions.append(StartingPointSuggestion(
                        path=None,
                        name=insight.title,
                        suggestion_type='insight',
                        description=insight.description,
                        source_mission=mission_info.mission_id,
                        relevance_score=relevance * insight.confidence,
                        exploration_count=0
                    ))

        # Sort by relevance and deduplicate by name
        suggestions.sort(key=lambda s: s.relevance_score, reverse=True)

        # Deduplicate
        seen_names = set()
        unique_suggestions = []
        for s in suggestions:
            key = f"{s.suggestion_type}:{s.name}"
            if key not in seen_names:
                seen_names.add(key)
                unique_suggestions.append(s)

        return unique_suggestions[:top_k]

    def search_prior_insights(
        self,
        query: str,
        top_k: int = 10
    ) -> List[Dict]:
        """
        Search insights across all prior missions.

        Args:
            query: Search query
            top_k: Maximum results

        Returns:
            List of matching insights with mission attribution
        """
        results = []
        query_lower = query.lower()

        prior_missions = self.discover_prior_missions()

        for mission_info in prior_missions:
            graph = self.load_prior_mission(mission_info.mission_id)
            if not graph:
                continue

            mission_relevance = self.compute_relevance(mission_info.mission_id)

            for insight in graph.insights.values():
                # Keyword matching for now (semantic search on insights added separately)
                if (query_lower in insight.title.lower() or
                    query_lower in insight.description.lower() or
                    any(query_lower in tag for tag in insight.tags)):

                    results.append({
                        'mission_id': mission_info.mission_id,
                        'insight_id': insight.id,
                        'title': insight.title,
                        'type': insight.insight_type,
                        'description': insight.description,
                        'confidence': insight.confidence,
                        'mission_relevance': round(mission_relevance, 4),
                        'tags': insight.tags
                    })

        # Sort by mission relevance and confidence
        results.sort(key=lambda r: r['mission_relevance'] * r['confidence'], reverse=True)
        return results[:top_k]

    def merge_prior_insights(
        self,
        current_graph: ExplorationGraph,
        prior_mission_ids: Optional[List[str]] = None,
        min_confidence: float = 0.7,
        max_imports: int = 20
    ) -> Dict:
        """
        Import relevant insights from prior missions into current graph.

        Args:
            current_graph: The current mission's ExplorationGraph
            prior_mission_ids: Specific missions to import from (all relevant if None)
            min_confidence: Minimum insight confidence to import
            max_imports: Maximum insights to import

        Returns:
            Summary of import operation
        """
        imported = 0
        skipped_duplicate = 0
        skipped_low_confidence = 0

        # Determine which missions to import from
        if prior_mission_ids:
            mission_ids = prior_mission_ids
        else:
            # Use all relevant missions
            prior_missions = self.discover_prior_missions()
            mission_ids = [m.mission_id for m in prior_missions if self.compute_relevance(m.mission_id) >= 0.4]

        for mission_id in mission_ids:
            if imported >= max_imports:
                break

            graph = self.load_prior_mission(mission_id)
            if not graph:
                continue

            for insight in graph.insights.values():
                if imported >= max_imports:
                    break

                if insight.confidence < min_confidence:
                    skipped_low_confidence += 1
                    continue

                # Check for duplicates (similar title)
                is_duplicate = False
                for existing in current_graph.insights.values():
                    if insight.title.lower() == existing.title.lower():
                        is_duplicate = True
                        break

                if is_duplicate:
                    skipped_duplicate += 1
                    continue

                # Import with source attribution
                current_graph.add_insight(
                    insight_type=insight.insight_type,
                    title=f"[From {mission_id}] {insight.title}",
                    description=insight.description,
                    mission_id=self.current_mission_id,
                    tags=insight.tags + ['imported'],
                    confidence=insight.confidence * 0.8  # Reduce confidence for imported
                )
                imported += 1

        return {
            'imported': imported,
            'skipped_duplicate': skipped_duplicate,
            'skipped_low_confidence': skipped_low_confidence,
            'missions_searched': len(mission_ids)
        }

    def get_stats(self) -> Dict:
        """Get statistics about available prior knowledge."""
        prior_missions = self.discover_prior_missions()

        total_nodes = sum(m.node_count for m in prior_missions)
        total_insights = sum(m.insight_count for m in prior_missions)
        total_edges = sum(m.edge_count for m in prior_missions)

        # Get top tags across all missions
        all_tags: Dict[str, int] = {}
        for mission in prior_missions:
            for tag in mission.top_tags:
                all_tags[tag] = all_tags.get(tag, 0) + 1

        top_tags = sorted(all_tags.keys(), key=lambda t: all_tags[t], reverse=True)[:10]

        return {
            'total_prior_missions': len(prior_missions),
            'total_nodes': total_nodes,
            'total_insights': total_insights,
            'total_edges': total_edges,
            'top_tags': top_tags,
            'loaded_graphs': len(self._prior_graphs),
            'generated_at': datetime.now().isoformat()
        }


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("Knowledge Transfer - Demo")
    print("=" * 50)

    # Create transfer instance
    kt = KnowledgeTransfer(
        current_mission_id="test_mission",
        missions_base=Path(__file__).resolve().parent.parent / "missions",
        current_mission_context="Building a REST API with authentication and user management"
    )

    # Discover prior missions
    print("\nDiscovering prior missions...")
    missions = kt.discover_prior_missions()
    print(f"Found {len(missions)} prior missions with exploration data:")
    for m in missions[:5]:
        print(f"  - {m.mission_id}: {m.node_count} nodes, {m.insight_count} insights")

    if missions:
        # Compute relevance
        print("\nComputing relevance scores...")
        for m in missions[:3]:
            relevance = kt.compute_relevance(m.mission_id)
            print(f"  - {m.mission_id}: {relevance:.2%} relevant")

        # Search prior knowledge
        print("\nSearching for 'authentication' across prior missions...")
        results = kt.get_relevant_prior_knowledge("authentication", top_k=5)
        for r in results:
            print(f"  [{r.mission_id}] {r.name}: {r.combined_score:.3f}")

        # Get starting point suggestions
        print("\nSuggested starting points:")
        suggestions = kt.suggest_starting_points()
        for s in suggestions[:5]:
            print(f"  [{s.suggestion_type}] {s.name} ({s.relevance_score:.2f})")

    # Stats
    print("\nKnowledge Transfer Stats:")
    stats = kt.get_stats()
    print(f"  Total prior missions: {stats['total_prior_missions']}")
    print(f"  Total nodes: {stats['total_nodes']}")
    print(f"  Total insights: {stats['total_insights']}")

    print("\nDemo complete!")
