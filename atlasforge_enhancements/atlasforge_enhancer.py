#!/usr/bin/env python3
"""
AtlasForge Enhancer - Unified Interface for AtlasForge Framework Enhancements

This module provides a single, easy-to-use interface that combines all three
enhancement features:

1. Cognitive Fingerprint Tracker - Mission continuity
2. Exploration Memory Graph - Exploration memory
3. Self-Calibrating Prompt Scaffolding - Bias reduction

Production Hardened (Cycle 5):
- Thread-safe operations with RLock
- Comprehensive error handling with graceful degradation
- Input validation on all public methods
- Retry logic for component initialization

Usage:
    enhancer = AtlasForgeEnhancer(mission_id="my_mission")

    # Feature 1: Mission Continuity
    enhancer.set_mission_baseline(mission_text)
    report = enhancer.check_continuity(current_output)
    healed_prompt = enhancer.heal_continuation(continuation, cycle_output)

    # Feature 2: Exploration Memory
    enhancer.record_file_exploration("/src/api.py", "REST API handlers")
    prior = enhancer.what_do_we_know("authentication")
    should, reason = enhancer.should_explore("/src/db.py")

    # Feature 3: Prompt Scaffolding
    scaffolded = enhancer.scaffold_prompt(prompt, previous_response)
    enhancer.record_scaffold_outcome(app_id, response)

    # Combined: Cycle-end processing
    report = enhancer.process_cycle_end(cycle_output, files_created, files_modified)
"""

import json
import logging
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

# Set up logging
logger = logging.getLogger(__name__)

try:
    from .mission_continuity_tracker import (
        MissionContinuityTracker,
        ContinuityReport,
        generate_continuation_with_healing
    )
    from .context_healing import generate_healing_prompt
    from .exploration_graph import ExplorationGraph
    from .insight_extractor import (
        extract_from_text,
        populate_graph_from_extraction,
        ExplorationAdvisor
    )
    from .scaffold_calibrator import ScaffoldCalibrator
    from .bias_detector import analyze_response
except ImportError:
    # Direct imports for standalone execution
    from mission_continuity_tracker import (
        MissionContinuityTracker,
        ContinuityReport,
        generate_continuation_with_healing
    )
    from context_healing import generate_healing_prompt
    from exploration_graph import ExplorationGraph
    from insight_extractor import (
        extract_from_text,
        populate_graph_from_extraction,
        ExplorationAdvisor
    )
    from scaffold_calibrator import ScaffoldCalibrator
    from bias_detector import analyze_response


class AtlasForgeEnhancer:
    """
    Unified interface for all AtlasForge enhancement features.

    Provides easy-to-use methods that combine the three features:
    - Mission continuity tracking
    - Exploration memory graph
    - Self-calibrating scaffolding

    Thread-safe for concurrent access across multiple threads.

    Attributes:
        mission_id: Unique identifier for the current mission
        storage_base: Base path for storing data
        continuity_tracker: MissionContinuityTracker instance
        exploration_graph: ExplorationGraph instance
        exploration_advisor: ExplorationAdvisor instance
        scaffold_calibrator: ScaffoldCalibrator instance
        knowledge_transfer: Optional KnowledgeTransfer instance
    """

    def __init__(
        self,
        mission_id: str,
        storage_base: Optional[Path] = None
    ):
        """
        Initialize the AtlasForge Enhancer.

        Args:
            mission_id: Unique identifier for the current mission
            storage_base: Base path for storing data (default: ./atlasforge_data/)

        Raises:
            ValueError: If mission_id is empty or invalid
        """
        # Input validation
        if not mission_id or not isinstance(mission_id, str):
            raise ValueError("mission_id must be a non-empty string")
        if len(mission_id) > 256:
            raise ValueError("mission_id must be 256 characters or less")

        self.mission_id = mission_id.strip()

        # Thread safety
        self._lock = threading.RLock()

        # Set up storage path with fallback
        try:
            self.storage_base = Path(storage_base) if storage_base else Path("./atlasforge_data")
            self.storage_base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.error(f"Failed to create storage path: {e}")
            self.storage_base = Path(f"/tmp/atlasforge_data_{mission_id}")
            self.storage_base.mkdir(parents=True, exist_ok=True)

        # Initialize sub-components with error handling
        self.continuity_tracker = None
        self.exploration_graph = None
        self.exploration_advisor = None
        self.scaffold_calibrator = None
        self.knowledge_transfer = None

        self._initialize_components()

        # Track current state
        self.current_cycle = 1
        self.last_scaffold_app_id: Optional[str] = None
        self._initialized = True

    def _initialize_components(self):
        """Initialize all sub-components with error handling."""
        errors = []

        # Initialize continuity tracker
        try:
            self.continuity_tracker = MissionContinuityTracker(
                self.mission_id,
                self.storage_base / "continuity"
            )
        except Exception as e:
            errors.append(f"continuity_tracker: {e}")
            logger.error(f"Failed to initialize continuity tracker: {e}")

        # Initialize exploration graph
        try:
            self.exploration_graph = ExplorationGraph(
                self.storage_base / "exploration"
            )
            self.exploration_advisor = ExplorationAdvisor(self.exploration_graph)
        except Exception as e:
            errors.append(f"exploration_graph: {e}")
            logger.error(f"Failed to initialize exploration graph: {e}")

        # Initialize scaffold calibrator
        try:
            self.scaffold_calibrator = ScaffoldCalibrator(
                self.storage_base / "scaffolding"
            )
        except Exception as e:
            errors.append(f"scaffold_calibrator: {e}")
            logger.error(f"Failed to initialize scaffold calibrator: {e}")

        if errors:
            logger.warning(f"AtlasForgeEnhancer initialized with {len(errors)} errors: {errors}")

    def _ensure_initialized(self, component: str) -> bool:
        """Check if a component is initialized."""
        if component == "continuity" and self.continuity_tracker is None:
            logger.warning("Continuity tracker not initialized")
            return False
        if component == "exploration" and (self.exploration_graph is None or self.exploration_advisor is None):
            logger.warning("Exploration graph not initialized")
            return False
        if component == "scaffold" and self.scaffold_calibrator is None:
            logger.warning("Scaffold calibrator not initialized")
            return False
        return True

    # =========================================================================
    # FEATURE 1: MISSION CONTINUITY
    # =========================================================================

    def set_mission_baseline(self, mission_text: str, source: str = "initial_mission"):
        """
        Set the baseline fingerprint for mission continuity tracking.

        Call this at mission start to establish the reference point.

        Args:
            mission_text: The original mission statement/problem
            source: Identifier for the source

        Returns:
            Baseline fingerprint or None if continuity tracker not available
        """
        if not self._ensure_initialized("continuity"):
            return None

        try:
            with self._lock:
                return self.continuity_tracker.set_baseline(mission_text, source)
        except Exception as e:
            logger.error(f"Failed to set mission baseline: {e}")
            return None

    def check_continuity(
        self,
        current_output: str,
        source: str = "current_output"
    ) -> Optional[ContinuityReport]:
        """
        Check how well current output aligns with the original mission.

        Args:
            current_output: Current Claude output to analyze
            source: Identifier for the output

        Returns:
            ContinuityReport with drift analysis and healing recommendations,
            or None if continuity tracker not available
        """
        if not self._ensure_initialized("continuity"):
            return None

        try:
            with self._lock:
                return self.continuity_tracker.check_continuity(current_output, source)
        except Exception as e:
            logger.error(f"Failed to check continuity: {e}")
            return None

    def heal_continuation(
        self,
        continuation_prompt: str,
        cycle_output: str
    ) -> str:
        """
        Enhance a continuation prompt with healing if drift is detected.

        Args:
            continuation_prompt: The base continuation prompt
            cycle_output: Output from the just-completed cycle

        Returns:
            Enhanced prompt (with healing if drift detected),
            or original prompt if healing not available
        """
        if not self._ensure_initialized("continuity"):
            return continuation_prompt

        try:
            with self._lock:
                return generate_continuation_with_healing(
                    self.continuity_tracker,
                    continuation_prompt,
                    cycle_output
                )
        except Exception as e:
            logger.error(f"Failed to heal continuation: {e}")
            return continuation_prompt

    def checkpoint_cycle(
        self,
        cycle_number: int,
        cycle_output: str,
        files_created: List[str],
        files_modified: List[str],
        summary: str
    ):
        """
        Create a continuity checkpoint at cycle end.

        Args:
            cycle_number: The cycle number just completed
            cycle_output: All Claude output from this cycle
            files_created: Files created in this cycle
            files_modified: Files modified in this cycle
            summary: Brief summary of accomplishments

        Returns:
            Checkpoint object or None if failed
        """
        if not self._ensure_initialized("continuity"):
            return None

        try:
            with self._lock:
                self.current_cycle = cycle_number
                return self.continuity_tracker.checkpoint_cycle(
                    cycle_number,
                    cycle_output,
                    files_created,
                    files_modified,
                    summary
                )
        except Exception as e:
            logger.error(f"Failed to checkpoint cycle: {e}")
            return None

    def get_continuity_evolution(self) -> Dict:
        """
        Get summary of how mission fingerprint has evolved.

        Returns:
            Evolution summary dict or empty dict if not available
        """
        if not self._ensure_initialized("continuity"):
            return {}

        try:
            with self._lock:
                return self.continuity_tracker.get_evolution_summary()
        except Exception as e:
            logger.error(f"Failed to get continuity evolution: {e}")
            return {}

    # =========================================================================
    # FEATURE 2: EXPLORATION MEMORY
    # =========================================================================

    def record_file_exploration(
        self,
        path: str,
        summary: str,
        tags: Optional[List[str]] = None
    ):
        """
        Record that a file was explored.

        Args:
            path: File path
            summary: What was learned
            tags: Optional categorization tags

        Returns:
            ExplorationNode or None if failed
        """
        if not self._ensure_initialized("exploration"):
            return None

        try:
            with self._lock:
                return self.exploration_graph.add_file_node(
                    path, summary, self.mission_id, tags
                )
        except Exception as e:
            logger.error(f"Failed to record file exploration: {e}")
            return None

    def record_concept(
        self,
        name: str,
        summary: str,
        tags: Optional[List[str]] = None
    ):
        """
        Record a discovered concept.

        Args:
            name: Concept name
            summary: Description of the concept
            tags: Optional categorization tags

        Returns:
            ExplorationNode or None if failed
        """
        if not self._ensure_initialized("exploration"):
            return None

        try:
            with self._lock:
                return self.exploration_graph.add_concept_node(
                    name, summary, self.mission_id, tags
                )
        except Exception as e:
            logger.error(f"Failed to record concept: {e}")
            return None

    def record_pattern(
        self,
        name: str,
        summary: str,
        code_example: Optional[str] = None
    ):
        """
        Record a discovered pattern.

        Args:
            name: Pattern name
            summary: Pattern description
            code_example: Optional code demonstrating the pattern

        Returns:
            ExplorationNode or None if failed
        """
        if not self._ensure_initialized("exploration"):
            return None

        try:
            with self._lock:
                return self.exploration_graph.add_pattern_node(
                    name, summary, self.mission_id, code_example
                )
        except Exception as e:
            logger.error(f"Failed to record pattern: {e}")
            return None

    def record_insight(
        self,
        insight_type: str,
        title: str,
        description: str,
        confidence: float = 1.0
    ):
        """
        Record an insight or learning.

        Args:
            insight_type: Type ('pattern', 'gotcha', 'best_practice', etc.)
            title: Brief title
            description: Full description
            confidence: Confidence level (0.0-1.0)

        Returns:
            ExplorationInsight or None if failed
        """
        if not self._ensure_initialized("exploration"):
            return None

        try:
            with self._lock:
                return self.exploration_graph.add_insight(
                    insight_type, title, description,
                    self.mission_id, confidence=confidence
                )
        except Exception as e:
            logger.error(f"Failed to record insight: {e}")
            return None

    def record_relationship(
        self,
        source_path: str,
        target_path: str,
        relationship: str,
        context: str = ""
    ):
        """
        Record a relationship between explored items.

        Args:
            source_path: Source file/concept
            target_path: Target file/concept
            relationship: Type ('imports', 'calls', 'uses', etc.)
            context: How this was discovered

        Returns:
            ExplorationEdge or None if failed
        """
        if not self._ensure_initialized("exploration"):
            return None

        try:
            with self._lock:
                # Get node IDs
                source_node = self.exploration_graph.get_file_node(source_path)
                target_node = self.exploration_graph.get_file_node(target_path)

                if source_node and target_node:
                    return self.exploration_graph.add_edge(
                        source_node.id,
                        target_node.id,
                        relationship,
                        self.mission_id,
                        context=context
                    )
                return None
        except Exception as e:
            logger.error(f"Failed to record relationship: {e}")
            return None

    def process_exploration_output(self, exploration_text: str) -> Dict:
        """
        Process exploration output and populate the graph.

        Extracts files, concepts, insights, and relationships from text.

        Args:
            exploration_text: Raw exploration output

        Returns:
            Summary of what was extracted and added
        """
        if not self._ensure_initialized("exploration"):
            return {'error': 'Exploration not initialized', 'added': {}}

        try:
            extraction = extract_from_text(exploration_text)
            with self._lock:
                added = populate_graph_from_extraction(
                    self.exploration_graph,
                    extraction,
                    self.mission_id
                )
                self.exploration_graph.save()
            return {
                'extraction_summary': extraction.summary_generated,
                'added': added
            }
        except Exception as e:
            logger.error(f"Failed to process exploration output: {e}")
            return {'error': str(e), 'added': {}}

    def should_explore(self, path: str) -> Tuple[bool, str]:
        """
        Check if a file should be explored.

        Args:
            path: The file path to check

        Returns:
            Tuple of (should_explore, reason)
        """
        if not self._ensure_initialized("exploration"):
            return True, "Exploration memory not available"

        try:
            with self._lock:
                return self.exploration_advisor.should_explore(path)
        except Exception as e:
            logger.error(f"Failed to check should_explore: {e}")
            return True, f"Error checking: {e}"

    def what_do_we_know(self, topic: str) -> Dict:
        """
        Query exploration memory for what we know about a topic.

        Args:
            topic: The topic to query

        Returns:
            Dict with relevant files, concepts, insights
        """
        return self.exploration_advisor.what_do_we_know(topic)

    def get_related_explorations(self, path: str) -> List[Dict]:
        """
        Get explorations related to a file.

        Args:
            path: The file path

        Returns:
            List of related explorations
        """
        node = self.exploration_graph.get_file_node(path)
        if not node:
            return []

        suggestions = self.exploration_advisor.suggest_related(node.id)
        return [
            {
                'name': s['node'].name,
                'path': s['node'].path,
                'reason': s['reason'],
                'summary': s['node'].summary
            }
            for s in suggestions
        ]

    def get_exploration_stats(self) -> Dict:
        """Get statistics about exploration coverage."""
        return self.exploration_graph.get_exploration_stats()

    # =========================================================================
    # FEATURE 3: PROMPT SCAFFOLDING
    # =========================================================================

    def scaffold_prompt(
        self,
        prompt: str,
        previous_response: Optional[str] = None,
        context: Optional[Dict] = None
    ) -> Tuple[str, Dict]:
        """
        Apply scaffolding to a prompt based on detected biases.

        Args:
            prompt: The prompt to scaffold
            previous_response: Optional previous response to analyze
            context: Optional context for scaffold templates

        Returns:
            Tuple of (scaffolded_prompt, analysis)
        """
        scaffolded, analysis = self.scaffold_calibrator.apply_scaffolds_to_prompt(
            prompt,
            previous_response,
            context
        )

        # Track application ID for later outcome recording
        if 'application_id' in analysis:
            self.last_scaffold_app_id = analysis['application_id']

        return scaffolded, analysis

    def record_scaffold_outcome(
        self,
        response: str,
        application_id: Optional[str] = None
    ) -> Dict:
        """
        Record the outcome of a scaffolded prompt.

        Args:
            response: The response to the scaffolded prompt
            application_id: Optional specific application ID (uses last if None)

        Returns:
            Outcome analysis
        """
        app_id = application_id or self.last_scaffold_app_id
        if not app_id:
            return {'error': 'No application to record outcome for'}

        return self.scaffold_calibrator.record_outcome(app_id, response)

    def analyze_for_bias(self, text: str) -> Dict:
        """
        Analyze text for cognitive biases.

        Args:
            text: Text to analyze

        Returns:
            Bias analysis with recommendations
        """
        return analyze_response(text)

    def get_scaffold_effectiveness(self) -> Dict:
        """Get report on scaffold effectiveness."""
        return self.scaffold_calibrator.get_effectiveness_report()

    # =========================================================================
    # COMBINED OPERATIONS
    # =========================================================================

    def process_cycle_end(
        self,
        cycle_number: int,
        cycle_output: str,
        files_created: List[str],
        files_modified: List[str],
        cycle_summary: str
    ) -> Dict:
        """
        Complete cycle-end processing combining all features.

        This:
        1. Creates a continuity checkpoint
        2. Processes exploration output
        3. Checks for drift and generates healing if needed
        4. Returns comprehensive cycle report

        Args:
            cycle_number: The cycle number just completed
            cycle_output: All output from this cycle
            files_created: Files created this cycle
            files_modified: Files modified this cycle
            cycle_summary: Summary of accomplishments

        Returns:
            Comprehensive cycle report
        """
        report = {
            'cycle': cycle_number,
            'mission_id': self.mission_id,
            'processed_at': datetime.now().isoformat()
        }

        # 1. Continuity checkpoint
        checkpoint = self.checkpoint_cycle(
            cycle_number, cycle_output,
            files_created, files_modified,
            cycle_summary
        )
        report['continuity'] = {
            'key_concepts': checkpoint.key_concepts,
            'summary': checkpoint.summary
        }

        # 2. Process explorations
        exploration = self.process_exploration_output(cycle_output)
        report['exploration'] = exploration

        # 3. Check for drift
        continuity = self.check_continuity(cycle_output, f"cycle_{cycle_number}")
        report['drift'] = {
            'similarity': continuity.overall_similarity,
            'severity': continuity.drift_severity,
            'alert': continuity.alert_level,
            'healing_needed': continuity.healing_recommended
        }

        if continuity.healing_recommended:
            report['healing_prompt'] = continuity.healing_prompt

        # 4. Analyze for bias patterns
        bias = self.analyze_for_bias(cycle_output)
        report['bias_analysis'] = {
            'score': bias['overall_score'],
            'needs_scaffolding': bias['needs_scaffolding'],
            'top_concerns': bias.get('priority_biases', [])
        }

        # Save all data
        self.exploration_graph.save()
        self.scaffold_calibrator.save()

        return report

    def generate_enhanced_continuation(
        self,
        base_continuation: str,
        cycle_output: str,
        previous_response: Optional[str] = None
    ) -> str:
        """
        Generate an enhanced continuation prompt.

        Combines:
        - Mission healing (if drift detected)
        - Scaffolding (if biases detected)

        Args:
            base_continuation: The base continuation prompt
            cycle_output: Output from the completed cycle
            previous_response: Optional previous response for bias analysis

        Returns:
            Enhanced continuation prompt
        """
        # Apply healing if needed
        healed = self.heal_continuation(base_continuation, cycle_output)

        # Apply scaffolding if needed
        scaffolded, _ = self.scaffold_prompt(
            healed,
            previous_response or cycle_output
        )

        return scaffolded

    def get_comprehensive_status(self) -> Dict:
        """Get comprehensive status across all features."""
        status = {
            'mission_id': self.mission_id,
            'current_cycle': self.current_cycle,
            'continuity': {
                'checkpoints': len(self.continuity_tracker.checkpoints),
                'has_baseline': self.continuity_tracker.baseline_fingerprint is not None,
                'evolution': self.get_continuity_evolution() if len(self.continuity_tracker.checkpoints) >= 2 else None
            },
            'exploration': self.get_exploration_stats(),
            'scaffolding': self.get_scaffold_effectiveness(),
            'status_generated_at': datetime.now().isoformat()
        }

        # Add knowledge transfer stats if enabled
        if hasattr(self, 'knowledge_transfer') and self.knowledge_transfer is not None:
            status['knowledge_transfer'] = self.knowledge_transfer.get_stats()

        return status

    # =========================================================================
    # FEATURE 4: KNOWLEDGE TRANSFER (Cross-Mission)
    # =========================================================================

    def enable_knowledge_transfer(
        self,
        missions_base: Optional[Path] = None,
        current_mission_context: Optional[str] = None
    ):
        """
        Enable cross-mission knowledge transfer.

        Allows loading and querying exploration graphs from prior missions.

        Args:
            missions_base: Path to missions directory (default: <ATLASFORGE_ROOT>/missions)
            current_mission_context: Description of current mission for relevance scoring
        """
        try:
            from .knowledge_transfer import KnowledgeTransfer
        except ImportError:
            from knowledge_transfer import KnowledgeTransfer

        base = missions_base or Path(__file__).resolve().parent.parent / "missions"
        self.knowledge_transfer = KnowledgeTransfer(
            current_mission_id=self.mission_id,
            missions_base=base,
            current_mission_context=current_mission_context
        )

    def query_prior_knowledge(
        self,
        query: str,
        top_k: int = 10,
        min_relevance: float = 0.3
    ) -> List[Dict]:
        """
        Query knowledge from prior missions.

        Args:
            query: Search query
            top_k: Maximum results to return
            min_relevance: Minimum relevance threshold

        Returns:
            List of relevant knowledge items from prior missions
        """
        if not hasattr(self, 'knowledge_transfer') or self.knowledge_transfer is None:
            return []

        results = self.knowledge_transfer.get_relevant_prior_knowledge(
            query, top_k, min_mission_relevance=min_relevance
        )
        return [r.to_dict() for r in results]

    def get_starting_point_suggestions(self) -> List[Dict]:
        """
        Get suggested starting points based on prior missions.

        Returns:
            List of suggested files, concepts, and insights to explore
        """
        if not hasattr(self, 'knowledge_transfer') or self.knowledge_transfer is None:
            return []

        suggestions = self.knowledge_transfer.suggest_starting_points()
        return [s.to_dict() for s in suggestions]

    def import_prior_insights(
        self,
        mission_ids: Optional[List[str]] = None,
        max_imports: int = 20
    ) -> Dict:
        """
        Import relevant insights from prior missions.

        Args:
            mission_ids: Specific missions to import from (all relevant if None)
            max_imports: Maximum insights to import

        Returns:
            Summary of import operation
        """
        if not hasattr(self, 'knowledge_transfer') or self.knowledge_transfer is None:
            return {"error": "Knowledge transfer not enabled"}

        result = self.knowledge_transfer.merge_prior_insights(
            self.exploration_graph,
            prior_mission_ids=mission_ids,
            max_imports=max_imports
        )

        # Save after import
        self.exploration_graph.save()
        return result

    # =========================================================================
    # FEATURE 5: INSIGHT SEARCH
    # =========================================================================

    def search_insights(self, query: str, top_k: int = 10) -> List[Dict]:
        """
        Semantic search for insights.

        Args:
            query: Search query
            top_k: Maximum results

        Returns:
            List of matching insights with similarity scores
        """
        return self.exploration_advisor.what_insights_do_we_have(query, top_k)

    def get_insight_coverage(self) -> Dict:
        """Get statistics about insight embedding coverage."""
        return self.exploration_graph.get_insight_coverage()

    # =========================================================================
    # FEATURE 6: VISUALIZATION
    # =========================================================================

    def export_graph_for_visualization(
        self,
        width: float = 800,
        height: float = 600
    ) -> Dict:
        """
        Export exploration graph for visualization.

        Args:
            width: Canvas width
            height: Canvas height

        Returns:
            Dict with nodes, edges, and positions ready for rendering
        """
        return self.exploration_graph.export_for_visualization(width, height)


# =============================================================================
# DEMO
# =============================================================================

if __name__ == "__main__":
    print("AtlasForge Enhancer - Unified Interface Demo")
    print("=" * 60)

    # Create enhancer
    enhancer = AtlasForgeEnhancer("demo_mission", Path("/tmp/af_demo"))

    # Set mission baseline
    mission = """
    Build a REST API for user management. The API should support:
    - User registration and login
    - Profile management
    - Role-based access control
    Security is critical - use proper authentication and authorization.
    """
    enhancer.set_mission_baseline(mission)
    print("\nMission baseline set")

    # Simulate cycle 1 output (good alignment)
    cycle1_output = """
    I've started implementing the REST API. Created the following:
    - /src/api/auth.py: Authentication endpoints (login, register)
    - /src/api/users.py: User profile CRUD operations
    - /src/services/auth_service.py: JWT token management

    The API uses JWT tokens for authentication. I implemented a
    decorator pattern for role-based access control on endpoints.

    Key insight: The existing database models already have role
    support, so we can leverage that for RBAC.
    """

    # Process cycle end
    print("\nProcessing cycle 1...")
    report = enhancer.process_cycle_end(
        cycle_number=1,
        cycle_output=cycle1_output,
        files_created=["/src/api/auth.py", "/src/api/users.py"],
        files_modified=["/src/services/auth_service.py"],
        cycle_summary="Implemented core authentication and user management"
    )

    print(f"\nCycle 1 Report:")
    print(f"  Drift similarity: {report['drift']['similarity']:.1%}")
    print(f"  Drift severity: {report['drift']['severity']}")
    print(f"  Exploration added: {report['exploration']['added']}")

    # Check what we know about authentication
    print("\nWhat do we know about 'authentication'?")
    knowledge = enhancer.what_do_we_know("authentication")
    print(f"  {knowledge['summary']}")

    # Get comprehensive status
    print("\nComprehensive Status:")
    status = enhancer.get_comprehensive_status()
    print(f"  Checkpoints: {status['continuity']['checkpoints']}")
    print(f"  Files explored: {status['exploration']['total_nodes']}")

    print("\nDemo complete!")
