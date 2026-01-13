#!/usr/bin/env python3
"""
Exploration Hooks - Callable functions for AtlasForge exploration tracking.

These can be invoked during exploration to record discoveries
and query prior knowledge. Provides a simple API for Claude to
remember what was explored and query that knowledge.

Usage in Claude's context:
    from exploration_hooks import record_exploration, what_do_we_know, should_explore

    # After reading a file
    record_exploration("/src/api.py", "REST API handlers for user management")

    # Before exploring a new file
    should_explore("/src/db.py")  # -> (True, "Not explored yet")

    # Query prior knowledge
    what_do_we_know("authentication")  # -> {"summary": "...", "files": [...]}
"""

import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from atlasforge_config import (
    MISSION_PATH,
    MISSIONS_DIR,
    EXPLORATION_DIR,
    ARTIFACTS_DIR,
)

# Decision Graph integration (optional)
DECISION_GRAPH_AVAILABLE = False
try:
    from decision_graph import get_decision_logger
    DECISION_GRAPH_AVAILABLE = True
except ImportError:
    pass

# Edge Detection Integration
EDGE_DETECTION_AVAILABLE = False
try:
    from edge_detector import detect_edges_for_read, detect_edges_for_grep, sync_explored_files, get_edge_detector
    EDGE_DETECTION_AVAILABLE = True
except ImportError:
    pass

# Real-Time Streaming Integration
REALTIME_STREAMING_AVAILABLE = False
_stream_manager = None
try:
    from realtime_graph_streaming import get_stream_manager
    REALTIME_STREAMING_AVAILABLE = True
except ImportError:
    pass

# Predictive Drift Prevention Integration
PREDICTIVE_DRIFT_AVAILABLE = False
_drift_predictor = None
_drift_nudge_generator = None
_explored_files_cache = []
_similarity_history_cache = []
_velocity_history_cache = []
PREDICTIVE_RISK_THRESHOLD = 0.5  # Warn when risk exceeds this
try:
    from predictive_drift.drift_predictor import DriftPredictor, predict_drift_risk
    from predictive_drift.proactive_nudges import ProactiveNudgeGenerator
    PREDICTIVE_DRIFT_AVAILABLE = True
except ImportError:
    pass


def _get_stream_manager():
    """Get the stream manager instance if available."""
    global _stream_manager
    if not REALTIME_STREAMING_AVAILABLE:
        return None
    if _stream_manager is None:
        _stream_manager = get_stream_manager()
    return _stream_manager


def _get_drift_predictor():
    """Get the drift predictor singleton instance."""
    global _drift_predictor
    if not PREDICTIVE_DRIFT_AVAILABLE:
        return None
    if _drift_predictor is None:
        _drift_predictor = DriftPredictor.get_instance()
    return _drift_predictor


def _get_nudge_generator():
    """Get the nudge generator singleton instance."""
    global _drift_nudge_generator
    if not PREDICTIVE_DRIFT_AVAILABLE:
        return None
    if _drift_nudge_generator is None:
        _drift_nudge_generator = ProactiveNudgeGenerator()
    return _drift_nudge_generator


def _emit_predictive_drift_event(prediction, nudge=None):
    """
    Emit predictive drift warning via WebSocket.

    Sends events to the drift_prevention room for real-time
    dashboard updates.
    """
    from datetime import datetime
    try:
        from dashboard_v2 import socketio

        # Emit prediction update
        socketio.emit('update', {
            'room': 'drift_prevention',
            'event': 'predictive_drift_update',
            'data': prediction.to_dict(),
            'timestamp': datetime.now().isoformat()
        }, namespace='/widgets', room='drift_prevention')

        # Emit nudge if provided
        if nudge:
            socketio.emit('update', {
                'room': 'drift_prevention',
                'event': 'drift_nudge',
                'data': nudge.to_dict(),
                'timestamp': datetime.now().isoformat()
            }, namespace='/widgets', room='drift_prevention')

    except Exception:
        pass  # Non-blocking - don't break tool execution


def check_predictive_drift_risk(file_path: str) -> Optional[Dict]:
    """
    Check predictive drift risk for a file exploration.

    Called before logging tool invocations to predict if this
    exploration might lead to drift. Emits WebSocket events
    if risk exceeds threshold.

    Args:
        file_path: Path of the file being explored

    Returns:
        Prediction dict if risk exceeds threshold, None otherwise
    """
    global _explored_files_cache, _similarity_history_cache, _velocity_history_cache

    if not PREDICTIVE_DRIFT_AVAILABLE:
        return None

    try:
        import io_utils
        from pathlib import Path as PathLib

        # Get mission info
        mission_id, _ = _get_mission_info()
        if not mission_id:
            return None

        # Load mission data
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        mission_text = mission.get('problem_statement', '')

        if not mission_text:
            return None

        # Load drift tracking state for similarity/velocity history
        TRACKING_PATH = MISSIONS_DIR / mission_id / "drift_tracking_state.json"
        tracking = io_utils.atomic_read_json(TRACKING_PATH, {})

        similarity_history = tracking.get('similarity_history', [0.9])
        velocity_history = tracking.get('similarity_velocities', [0.0])
        failure_count = tracking.get('failure_count', 0)
        current_cycle = mission.get('current_cycle', 1)

        # Update explored files cache
        if file_path not in _explored_files_cache:
            _explored_files_cache.append(file_path)

        # Get prediction
        prediction = predict_drift_risk(
            mission_text=mission_text,
            current_file=file_path,
            explored_files=_explored_files_cache,
            similarity_history=similarity_history,
            velocity_history=velocity_history,
            failure_count=failure_count,
            current_cycle=current_cycle,
        )

        # Check if we should warn
        if prediction.should_warn:
            # Generate nudge
            nudge_generator = _get_nudge_generator()
            nudge = None
            if nudge_generator:
                nudge = nudge_generator.generate_nudge(
                    risk_score=prediction.risk_score,
                    confidence=prediction.confidence,
                    current_file=file_path,
                    mission_text=mission_text[:500],
                    mission_id=mission_id,
                )

            # Emit WebSocket events
            _emit_predictive_drift_event(prediction, nudge)

            return prediction.to_dict()

        return None

    except Exception:
        return None  # Silent failure


# Rate limiter for streaming events (max 10 events/second)
class StreamingRateLimiter:
    """
    Rate limiter for streaming events.

    Prevents flooding during rapid exploration by limiting
    the number of events emitted per second.
    """

    def __init__(self, max_events_per_second: int = 10):
        self.max_events = max_events_per_second
        self.window_seconds = 1.0
        self.event_times = []
        self._lock = None  # Lazy init for thread safety

    def _get_lock(self):
        import threading
        if self._lock is None:
            self._lock = threading.Lock()
        return self._lock

    def allow_event(self) -> bool:
        """
        Check if an event should be allowed based on rate limit.

        Returns True if the event should be emitted, False if rate limited.
        """
        import time

        with self._get_lock():
            now = time.time()
            cutoff = now - self.window_seconds

            # Remove old events outside the window
            self.event_times = [t for t in self.event_times if t > cutoff]

            if len(self.event_times) >= self.max_events:
                return False

            self.event_times.append(now)
            return True

    def reset(self):
        """Reset the rate limiter."""
        with self._get_lock():
            self.event_times = []


# Global rate limiter instance
_streaming_rate_limiter = StreamingRateLimiter(max_events_per_second=10)


def get_streaming_rate_limiter() -> StreamingRateLimiter:
    """Get the global streaming rate limiter."""
    return _streaming_rate_limiter


# Mission Analytics integration (optional)
ANALYTICS_AVAILABLE = False
try:
    from mission_analytics import get_analytics
    ANALYTICS_AVAILABLE = True
except ImportError:
    pass

# Global enhancer instance (lazy initialized)
_enhancer = None
_current_enhancer = None  # Alias for external use
_cached_mission_id = None  # Track which mission the enhancer was created for


def set_current_enhancer(enhancer):
    """
    Manually set the current AtlasForge enhancer.

    Used for testing or when manually managing enhancer lifecycle.
    """
    global _enhancer, _current_enhancer
    _enhancer = enhancer
    _current_enhancer = enhancer


def _get_mission_info() -> Tuple[Optional[str], Optional[str]]:
    """Get current mission ID and workspace from state file."""
    try:
        import io_utils
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        return mission.get('mission_id'), mission.get('mission_workspace')
    except Exception:
        return None, None


def _get_mission_stage() -> Optional[str]:
    """Get current mission stage from state file."""
    try:
        import io_utils
        mission = io_utils.atomic_read_json(MISSION_PATH, {})
        return mission.get('current_stage', 'UNKNOWN')
    except Exception:
        return None


def get_current_enhancer(force_reload: bool = False):
    """
    Get or create the AtlasForge enhancer for the current mission.

    Lazily initializes the enhancer on first call and caches it.
    Returns None if AtlasForge enhancements are not available or mission
    is not properly configured.

    Args:
        force_reload: If True, forces reloading exploration graph from disk
    """
    global _enhancer, _cached_mission_id

    # Get current mission info
    mission_id, workspace = _get_mission_info()

    # Check if mission has changed - if so, reset the enhancer
    if mission_id and _cached_mission_id and mission_id != _cached_mission_id:
        _enhancer = None
        _cached_mission_id = None

    # Return cached enhancer if available (and mission hasn't changed)
    if _enhancer is not None:
        # Force reload exploration graph from disk if requested
        if force_reload and hasattr(_enhancer, 'exploration_graph'):
            try:
                _enhancer.exploration_graph.reload()
            except Exception:
                pass  # Ignore reload errors, use cached data
        return _enhancer

    try:
        from atlasforge_enhancements import AtlasForgeEnhancer

        if mission_id and workspace:
            _enhancer = AtlasForgeEnhancer(
                mission_id=mission_id,
                storage_base=Path(workspace) / 'atlasforge_data'
            )
            _cached_mission_id = mission_id
            return _enhancer
    except ImportError:
        pass
    except Exception as e:
        print(f"Could not initialize exploration enhancer: {e}")

    return None


def reset_enhancer():
    """Reset the enhancer (useful when starting a new mission)."""
    global _enhancer
    _enhancer = None


def record_exploration(
    path: str,
    summary: str,
    tags: Optional[List[str]] = None,
    parent_path: Optional[str] = None
) -> Dict:
    """
    Record a file exploration to the graph.

    Call this after reading a file to remember what was learned.

    Args:
        path: The file path that was explored
        summary: What was learned from this file
        tags: Optional categorization tags
        parent_path: Optional path of the file that led to this exploration

    Returns:
        Status dict with 'status' key ('recorded' or 'error')

    Example:
        record_exploration(
            "/src/auth/jwt.py",
            "JWT token generation and validation",
            tags=["authentication", "security"]
        )
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            enhancer.record_file_exploration(path, summary, tags)
            enhancer.exploration_graph.save()

            # Log to decision graph for visualization
            if DECISION_GRAPH_AVAILABLE:
                try:
                    mission_id, _ = _get_mission_info()
                    stage = _get_mission_stage() or "UNKNOWN"
                    if mission_id:
                        logger = get_decision_logger()
                        logger.log_invocation(
                            mission_id=mission_id,
                            stage=stage,
                            tool_name="Read",
                            input_summary={"file_path": path},
                            output_summary={"summary": summary[:100]},
                            status="success"
                        )
                except Exception:
                    pass  # Don't break exploration if graph logging fails

            # Emit real-time streaming event (rate limited)
            stream_manager = _get_stream_manager()
            rate_limiter = get_streaming_rate_limiter()
            if stream_manager and rate_limiter.allow_event():
                try:
                    from pathlib import Path as P
                    node_name = P(path).name
                    stream_manager.emit_node_added(
                        node_id=path,
                        name=node_name,
                        node_type='file',
                        path=path,
                        summary=summary,
                        tags=tags or [],
                        parent_id=parent_path
                    )
                    # If there's a parent, emit the edge too (uses separate rate check)
                    if parent_path and rate_limiter.allow_event():
                        stream_manager.emit_edge_added(
                            source_id=parent_path,
                            target_id=path,
                            relationship='explored_next'
                        )
                except Exception:
                    pass  # Don't break exploration if streaming fails

            return {
                "status": "recorded",
                "path": path,
                "summary": summary[:100]
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "no_enhancer", "message": "AtlasForge enhancements not available"}


def record_concept(
    name: str,
    summary: str,
    tags: Optional[List[str]] = None
) -> Dict:
    """
    Record a discovered concept.

    Args:
        name: Concept name (e.g., "JWT Authentication", "Rate Limiting")
        summary: Description of the concept
        tags: Optional tags

    Returns:
        Status dict
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            enhancer.record_concept(name, summary, tags)
            enhancer.exploration_graph.save()
            return {"status": "recorded", "concept": name}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "no_enhancer"}


def record_insight(
    title: str,
    description: str,
    insight_type: str = "observation",
    confidence: float = 1.0
) -> Dict:
    """
    Record an insight or learning.

    Args:
        title: Brief title for the insight
        description: Full description
        insight_type: Type ('pattern', 'gotcha', 'best_practice', 'observation')
        confidence: Confidence level (0.0-1.0)

    Returns:
        Status dict
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            enhancer.record_insight(insight_type, title, description, confidence)
            enhancer.exploration_graph.save()
            return {"status": "recorded", "title": title}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "no_enhancer"}


def should_explore(path: str) -> Tuple[bool, str]:
    """
    Check if a file should be explored.

    Use this before reading a file to avoid redundant exploration.

    Args:
        path: The file path to check

    Returns:
        Tuple of (should_explore: bool, reason: str)

    Example:
        should, reason = should_explore("/src/db.py")
        if should:
            # Read the file
            ...
        else:
            print(f"Skipping: {reason}")
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.should_explore(path)
        except Exception as e:
            return True, f"Error checking: {e}"
    return True, "No exploration memory available"


def what_do_we_know(topic: str) -> Dict:
    """
    Query exploration memory for what we know about a topic.

    Args:
        topic: The topic to query (e.g., "authentication", "database")

    Returns:
        Dict with:
            - summary: Brief summary of knowledge
            - files: List of relevant files explored
            - concepts: Related concepts
            - insights: Related insights

    Example:
        knowledge = what_do_we_know("authentication")
        print(knowledge['summary'])
        for f in knowledge.get('files', []):
            print(f"  - {f['path']}: {f['summary']}")
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.what_do_we_know(topic)
        except Exception as e:
            return {
                "summary": f"Error querying: {e}",
                "files": [],
                "concepts": [],
                "insights": []
            }
    return {
        "summary": "No exploration memory available",
        "files": [],
        "concepts": [],
        "insights": []
    }


def get_related_to(path: str) -> List[Dict]:
    """
    Get explorations related to a file.

    Args:
        path: The file path

    Returns:
        List of related explorations with 'name', 'path', 'reason', 'summary'
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.get_related_explorations(path)
        except Exception as e:
            return []
    return []


def get_exploration_stats() -> Dict:
    """
    Get statistics about exploration coverage.

    Returns:
        Dict with total_nodes, total_edges, total_insights, etc.
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.get_exploration_stats()
        except Exception as e:
            return {"error": str(e)}
    return {"total_nodes": 0, "total_insights": 0, "message": "No enhancer"}


def get_exploration_context() -> str:
    """
    Get a brief exploration context summary for prompts.

    Returns a short string suitable for including in prompts.
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            stats = enhancer.get_exploration_stats()
            if stats.get('total_nodes', 0) > 0:
                return (
                    f"Exploration Memory: {stats.get('total_nodes', 0)} files, "
                    f"{stats.get('total_insights', 0)} insights. "
                    "Use `what_do_we_know(topic)` to query prior knowledge."
                )
        except Exception:
            pass
    return ""


def process_exploration_text(text: str) -> Dict:
    """
    Process exploration text and extract insights automatically.

    Parses the text to extract file references, relationships, and insights,
    then adds them to the exploration graph.

    Args:
        text: Raw exploration output text

    Returns:
        Summary of what was extracted and added
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.process_exploration_output(text)
        except Exception as e:
            return {"error": str(e)}
    return {"error": "No enhancer available"}


def semantic_search(query: str, top_k: int = 10) -> List[Dict]:
    """
    Perform semantic search on the exploration graph.

    Uses sentence transformer embeddings for similarity matching.
    Returns semantically similar results even for paraphrased queries.

    Args:
        query: Search query (can be natural language)
        top_k: Maximum number of results to return

    Returns:
        List of search results with relevance scores
    """
    enhancer = get_current_enhancer()
    if enhancer:
        try:
            return enhancer.exploration_advisor.semantic_search(query, top_k)
        except Exception as e:
            return [{"error": str(e)}]
    return []


def get_drift_history(force_reload: bool = True) -> List[Dict]:
    """
    Get history of mission drift measurements.

    Useful for dashboard visualization of continuity trends.

    Args:
        force_reload: If True, reload data from disk (default: True for dashboard)

    Returns:
        List of drift measurements with cycle, similarity, severity
    """
    from atlasforge_enhancements.fingerprint_extractor import measure_drift

    enhancer = get_current_enhancer(force_reload=force_reload)
    if enhancer:
        try:
            # Get checkpoints from continuity tracker
            checkpoints = enhancer.continuity_tracker.checkpoints
            baseline = enhancer.continuity_tracker.baseline_fingerprint
            history = []

            for cp in checkpoints:
                # Compute drift from baseline for this checkpoint
                if baseline and hasattr(cp, 'fingerprint') and cp.fingerprint:
                    drift = measure_drift(baseline, cp.fingerprint, use_embeddings=False)
                    history.append({
                        'cycle': cp.cycle_number,
                        'similarity': drift.get('overall_similarity', 1.0),
                        'severity': drift.get('drift_severity', 'MINIMAL'),
                        'alert': drift.get('alert_level', 'GREEN'),
                        'timestamp': cp.timestamp,
                        'summary': cp.summary
                    })
                else:
                    # First checkpoint or no baseline - assume perfect alignment
                    history.append({
                        'cycle': cp.cycle_number,
                        'similarity': 1.0,
                        'severity': 'MINIMAL',
                        'alert': 'GREEN',
                        'timestamp': cp.timestamp,
                        'summary': cp.summary
                    })

            return history
        except Exception as e:
            return [{"error": str(e)}]
    return []


def get_recent_explorations(limit: int = 10, force_reload: bool = True) -> List[Dict]:
    """
    Get recently explored items.

    Args:
        limit: Maximum number of explorations to return
        force_reload: If True, reload data from disk (default: True for dashboard)

    Returns:
        List of recent explorations with timestamps
    """
    from atlasforge_enhancements import ExplorationGraph

    # First try mission-specific enhancer
    enhancer = get_current_enhancer(force_reload=force_reload)
    graph = None

    if enhancer and len(enhancer.exploration_graph.nodes) > 0:
        graph = enhancer.exploration_graph
    else:
        # Try global exploration data
        global_graph_path = EXPLORATION_DIR
        if global_graph_path.exists():
            try:
                global_graph = ExplorationGraph(storage_path=global_graph_path)
                if len(global_graph.nodes) > 0:
                    graph = global_graph
            except Exception:
                pass

    if graph:
        try:
            # Get all nodes sorted by last_explored timestamp
            nodes = list(graph.nodes.values())
            nodes.sort(key=lambda n: n.last_explored, reverse=True)

            return [
                {
                    'id': node.id,
                    'name': node.name,
                    'type': node.node_type,
                    'path': node.path,
                    'summary': node.summary[:100] + '...' if len(node.summary) > 100 else node.summary,
                    'last_explored': node.last_explored,
                    'exploration_count': node.exploration_count
                }
                for node in nodes[:limit]
            ]
        except Exception as e:
            return [{"error": str(e)}]
    return []


def get_af_dashboard_data(force_reload: bool = True) -> Dict:
    """
    Get all data needed for AtlasForge dashboard widgets.

    Returns comprehensive data for visualization:
    - Exploration stats
    - Drift history
    - Recent explorations
    - Scaffold effectiveness

    Args:
        force_reload: If True, reload data from disk (default: True for dashboard)

    Returns:
        Dict with all dashboard data
    """
    from atlasforge_enhancements import ExplorationGraph
    from datetime import datetime

    enhancer = get_current_enhancer(force_reload=force_reload)

    # If no enhancer or empty graph, try global exploration data
    if not enhancer or len(enhancer.exploration_graph.nodes) == 0:
        global_graph_path = EXPLORATION_DIR
        if global_graph_path.exists():
            try:
                global_graph = ExplorationGraph(storage_path=global_graph_path)
                if len(global_graph.nodes) > 0:
                    # Return global graph stats
                    stats = global_graph.get_exploration_stats()
                    return {
                        "exploration": {
                            "total_nodes": stats.get('total_nodes', 0),
                            "total_edges": stats.get('total_edges', 0),
                            "total_insights": stats.get('total_insights', 0),
                            "nodes_by_type": stats.get('nodes_by_type', {}),
                            "top_tags": stats.get('top_tags', {}),
                            "most_explored": stats.get('most_explored', [])
                        },
                        "drift_history": get_drift_history(),
                        "recent_explorations": get_recent_explorations(10),
                        "coverage_pct": 0.0,
                        "insight_coverage": global_graph.get_insight_coverage(),
                        "scaffold_effectiveness": {"message": "No calibration data yet"},
                        "generated_at": datetime.now().isoformat(),
                        "source": "global"
                    }
            except Exception:
                pass  # Fall through to default response

    if not enhancer:
        return {
            "error": "No enhancer available",
            "exploration": {"total_nodes": 0, "total_insights": 0, "total_edges": 0},
            "drift_history": [],
            "recent_explorations": [],
            "coverage_pct": 0
        }

    try:
        stats = enhancer.get_exploration_stats()

        # Calculate coverage percentage (nodes with embeddings / total nodes)
        nodes_with_embeddings = sum(
            1 for n in enhancer.exploration_graph.nodes.values()
            if n.embedding is not None
        )
        coverage_pct = (nodes_with_embeddings / max(stats.get('total_nodes', 1), 1)) * 100

        # Also get insight coverage
        insight_coverage = enhancer.exploration_graph.get_insight_coverage()

        return {
            "exploration": {
                "total_nodes": stats.get('total_nodes', 0),
                "total_edges": stats.get('total_edges', 0),
                "total_insights": stats.get('total_insights', 0),
                "nodes_by_type": stats.get('nodes_by_type', {}),
                "top_tags": stats.get('top_tags', {}),
                "most_explored": stats.get('most_explored', [])
            },
            "drift_history": get_drift_history(),
            "recent_explorations": get_recent_explorations(10),
            "coverage_pct": round(coverage_pct, 1),
            "insight_coverage": insight_coverage,
            "scaffold_effectiveness": enhancer.get_scaffold_effectiveness() if hasattr(enhancer, 'get_scaffold_effectiveness') else {},
            "generated_at": enhancer.exploration_graph.get_exploration_stats().get('generated_at')
        }
    except Exception as e:
        return {"error": str(e)}


def get_visualization_data(width: float = 800, height: float = 600, force_reload: bool = True) -> Dict:
    """
    Get exploration graph visualization data.

    Returns node positions and edge data for canvas rendering.

    Args:
        width: Canvas width
        height: Canvas height
        force_reload: If True, reload data from disk (default: True for dashboard)

    Returns:
        Dict with nodes, edges, and stats for visualization
    """
    from atlasforge_enhancements import ExplorationGraph

    # First try mission-specific enhancer
    enhancer = get_current_enhancer(force_reload=force_reload)
    graph = None

    if enhancer and len(enhancer.exploration_graph.nodes) > 0:
        graph = enhancer.exploration_graph
    else:
        # Try global exploration data
        global_graph_path = EXPLORATION_DIR
        if global_graph_path.exists():
            try:
                global_graph = ExplorationGraph(storage_path=global_graph_path)
                if len(global_graph.nodes) > 0:
                    graph = global_graph
            except Exception:
                pass

    if not graph:
        return {
            "error": "No exploration data available",
            "nodes": [],
            "edges": [],
            "stats": {"total_nodes": 0, "total_edges": 0, "total_insights": 0}
        }

    try:
        return graph.export_for_visualization(width, height)
    except Exception as e:
        return {"error": str(e), "nodes": [], "edges": []}


def search_insights(query: str, top_k: int = 10, force_reload: bool = True) -> List[Dict]:
    """
    Semantic search for insights.

    Args:
        query: Search query
        top_k: Maximum results
        force_reload: If True, reload data from disk (default: True for dashboard)

    Returns:
        List of matching insights with similarity scores
    """
    enhancer = get_current_enhancer(force_reload=force_reload)
    if not enhancer:
        return []

    try:
        return enhancer.exploration_advisor.what_insights_do_we_have(query, top_k)
    except Exception as e:
        return [{"error": str(e)}]


def query_prior_missions(query: str, top_k: int = 10) -> Dict:
    """
    Query knowledge from prior missions.

    Args:
        query: Search query
        top_k: Maximum results

    Returns:
        Dict with prior knowledge results and stats
    """
    enhancer = get_current_enhancer()
    if not enhancer:
        return {"error": "No enhancer available", "results": []}

    try:
        if not hasattr(enhancer, 'knowledge_transfer') or enhancer.knowledge_transfer is None:
            return {"error": "Knowledge transfer not enabled", "results": []}

        results = enhancer.knowledge_transfer.get_relevant_prior_knowledge(query, top_k)
        stats = enhancer.knowledge_transfer.get_stats()

        return {
            "query": query,
            "results": [r.to_dict() for r in results],
            "stats": stats
        }
    except Exception as e:
        return {"error": str(e), "results": []}


def get_prior_mission_suggestions() -> List[Dict]:
    """
    Get starting point suggestions from prior missions.

    Returns:
        List of suggested starting points with relevance scores
    """
    enhancer = get_current_enhancer()
    if not enhancer:
        return []

    try:
        if not hasattr(enhancer, 'knowledge_transfer') or enhancer.knowledge_transfer is None:
            return []

        suggestions = enhancer.knowledge_transfer.suggest_starting_points()
        return [s.to_dict() for s in suggestions]
    except Exception as e:
        return [{"error": str(e)}]


def get_prior_missions_list() -> List[Dict]:
    """
    Get list of prior missions with exploration data.

    Returns:
        List of prior mission info
    """
    enhancer = get_current_enhancer()
    if not enhancer:
        return []

    try:
        if not hasattr(enhancer, 'knowledge_transfer') or enhancer.knowledge_transfer is None:
            return []

        missions = enhancer.knowledge_transfer.discover_prior_missions()
        return [m.to_dict() for m in missions]
    except Exception as e:
        return [{"error": str(e)}]


# =============================================================================
# TOOL INVOCATION LOGGING - For Decision Graph Integration
# =============================================================================

import time

def log_tool_invocation(
    tool_name: str,
    input_summary: Dict,
    output_summary: Dict = None,
    status: str = "success",
    error_message: str = "",
    duration_ms: int = 0
) -> Optional[str]:
    """
    Log a tool invocation to the decision graph.

    This is the central logging function for all tool types:
    - Read, Write, Edit, Glob, Grep, Bash, etc.

    Args:
        tool_name: Name of the tool (Read, Write, Edit, Bash, Glob, Grep, etc.)
        input_summary: Summary of input parameters
        output_summary: Summary of output (optional)
        status: Invocation status (success, error, timeout, blocked)
        error_message: Error message if status is error
        duration_ms: Execution duration in milliseconds

    Returns:
        Invocation ID if logged, None otherwise
    """
    if not DECISION_GRAPH_AVAILABLE:
        return None

    try:
        mission_id, _ = _get_mission_info()
        stage = _get_mission_stage() or "UNKNOWN"

        if not mission_id:
            return None

        logger = get_decision_logger()
        return logger.log_invocation(
            mission_id=mission_id,
            stage=stage,
            tool_name=tool_name,
            input_summary=input_summary,
            output_summary=output_summary or {},
            status=status,
            error_message=error_message,
            duration_ms=duration_ms
        )
    except Exception as e:
        # Silent failure - don't break tool execution
        return None


def log_read_tool(file_path: str, lines_read: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Read tool invocation with predictive drift check."""
    # Check predictive drift risk before logging
    if success and file_path:
        check_predictive_drift_risk(file_path)

    return log_tool_invocation(
        tool_name="Read",
        input_summary={"file_path": file_path},
        output_summary={"lines_read": lines_read},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_write_tool(file_path: str, content_length: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Write tool invocation."""
    return log_tool_invocation(
        tool_name="Write",
        input_summary={"file_path": file_path, "content_length": content_length},
        output_summary={"success": success},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_edit_tool(file_path: str, old_string_preview: str = "", new_string_preview: str = "", replace_all: bool = False, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log an Edit tool invocation."""
    return log_tool_invocation(
        tool_name="Edit",
        input_summary={
            "file_path": file_path,
            "old_string": old_string_preview[:100] + "..." if len(old_string_preview) > 100 else old_string_preview,
            "new_string": new_string_preview[:100] + "..." if len(new_string_preview) > 100 else new_string_preview,
            "replace_all": replace_all
        },
        output_summary={"success": success},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_bash_tool(command: str, exit_code: int = 0, output_preview: str = "", success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Bash tool invocation."""
    return log_tool_invocation(
        tool_name="Bash",
        input_summary={"command": command[:200] + "..." if len(command) > 200 else command},
        output_summary={
            "exit_code": exit_code,
            "output_preview": output_preview[:200] + "..." if len(output_preview) > 200 else output_preview
        },
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_glob_tool(pattern: str, path: str = None, matches_count: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Glob tool invocation."""
    return log_tool_invocation(
        tool_name="Glob",
        input_summary={"pattern": pattern, "path": path},
        output_summary={"matches_count": matches_count},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_grep_tool(pattern: str, path: str = None, output_mode: str = "files_with_matches", matches_count: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Grep tool invocation."""
    return log_tool_invocation(
        tool_name="Grep",
        input_summary={"pattern": pattern, "path": path, "output_mode": output_mode},
        output_summary={"matches_count": matches_count},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_web_fetch_tool(url: str, prompt: str = "", content_length: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a WebFetch tool invocation."""
    return log_tool_invocation(
        tool_name="WebFetch",
        input_summary={"url": url, "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt},
        output_summary={"content_length": content_length},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_web_search_tool(query: str, results_count: int = 0, success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a WebSearch tool invocation."""
    return log_tool_invocation(
        tool_name="WebSearch",
        input_summary={"query": query},
        output_summary={"results_count": results_count},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


def log_task_tool(subagent_type: str, prompt_preview: str = "", success: bool = True, error: str = "", duration_ms: int = 0) -> Optional[str]:
    """Log a Task tool invocation (subagent spawn)."""
    return log_tool_invocation(
        tool_name="Task",
        input_summary={
            "subagent_type": subagent_type,
            "prompt_preview": prompt_preview[:100] + "..." if len(prompt_preview) > 100 else prompt_preview
        },
        output_summary={"success": success},
        status="success" if success else "error",
        error_message=error,
        duration_ms=duration_ms
    )


class ToolInvocationTimer:
    """Context manager for timing and logging tool invocations."""

    def __init__(self, tool_name: str, input_summary: Dict):
        self.tool_name = tool_name
        self.input_summary = input_summary
        self.start_time = None
        self.output_summary = {}
        self.status = "success"
        self.error_message = ""

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration_ms = int((time.time() - self.start_time) * 1000)

        if exc_type is not None:
            self.status = "error"
            self.error_message = str(exc_val)

        log_tool_invocation(
            tool_name=self.tool_name,
            input_summary=self.input_summary,
            output_summary=self.output_summary,
            status=self.status,
            error_message=self.error_message,
            duration_ms=duration_ms
        )

        # Don't suppress exceptions
        return False

    def set_output(self, **kwargs):
        """Set output summary fields."""
        self.output_summary.update(kwargs)

    def set_error(self, error_message: str):
        """Mark as error with message."""
        self.status = "error"
        self.error_message = error_message


# =============================================================================
# TRANSCRIPT PARSING AND DATA POPULATION
# =============================================================================

def populate_from_transcript(transcript_path: Path, mission_id: str = None) -> Dict:
    """
    Parse a transcript JSONL file and populate exploration and decision graph data.

    This retroactively records tool invocations from historical transcripts,
    enabling the dashboard widgets to show data from past missions.

    Args:
        transcript_path: Path to the .jsonl transcript file
        mission_id: Mission ID (will be inferred from path if not provided)

    Returns:
        Dict with parsing statistics
    """
    from datetime import datetime
    import re

    stats = {
        "file": str(transcript_path),
        "mission_id": mission_id,
        "tool_calls_found": 0,
        "file_reads": 0,
        "file_writes": 0,
        "bash_commands": 0,
        "errors": []
    }

    # Infer mission_id from path if not provided
    if not mission_id:
        # Try to extract from path like /artifacts/transcripts/mission_xyz/file.jsonl
        path_str = str(transcript_path)
        match = re.search(r'mission[_-]([a-f0-9]+)', path_str)
        if match:
            mission_id = f"mission_{match.group(1)}"
            stats["mission_id"] = mission_id

    if not mission_id:
        stats["errors"].append("Could not determine mission_id")
        return stats

    # Get decision logger
    try:
        from decision_graph import get_decision_logger
        logger = get_decision_logger()
    except ImportError:
        stats["errors"].append("decision_graph not available")
        logger = None

    # Parse transcript
    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue

                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Look for assistant messages with tool calls
                if record.get("type") == "assistant":
                    message = record.get("message", {})
                    content = message.get("content", [])

                    # Get timestamp if available
                    timestamp = None
                    if "timestamp" in record:
                        try:
                            timestamp = datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00"))
                        except:
                            pass

                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            tool_name = block.get("name", "")
                            tool_input = block.get("input", {})
                            stats["tool_calls_found"] += 1

                            # Log to decision graph
                            if logger:
                                try:
                                    logger.log_invocation(
                                        mission_id=mission_id,
                                        stage="HISTORICAL",  # Mark as historical import
                                        tool_name=tool_name,
                                        input_summary=_summarize_tool_input(tool_name, tool_input),
                                        output_summary={},
                                        status="success"
                                    )
                                except Exception as log_error:
                                    stats["errors"].append(f"Log error: {str(log_error)}")
                                    pass

                            # Track specific tool types
                            if tool_name == "Read":
                                stats["file_reads"] += 1
                            elif tool_name in ("Write", "Edit"):
                                stats["file_writes"] += 1
                            elif tool_name == "Bash":
                                stats["bash_commands"] += 1

    except Exception as e:
        stats["errors"].append(f"Parse error: {str(e)}")

    return stats


def _summarize_tool_input(tool_name: str, tool_input: dict) -> dict:
    """Summarize tool input for logging (truncate large values)."""
    summary = {}
    for key, value in tool_input.items():
        if isinstance(value, str):
            if len(value) > 200:
                summary[key] = value[:200] + "..."
            else:
                summary[key] = value
        else:
            summary[key] = value
    return summary


def populate_from_mission_archive(mission_id: str) -> Dict:
    """
    Populate exploration and decision graph data from a mission's archived transcripts.

    Args:
        mission_id: The mission ID to process

    Returns:
        Dict with aggregate statistics
    """
    from pathlib import Path

    archive_dir = ARTIFACTS_DIR / "transcripts" / mission_id

    results = {
        "mission_id": mission_id,
        "transcripts_processed": 0,
        "total_tool_calls": 0,
        "errors": []
    }

    if not archive_dir.exists():
        results["errors"].append(f"Archive not found: {archive_dir}")
        return results

    for jsonl_file in archive_dir.glob("*.jsonl"):
        if jsonl_file.name == "manifest.json":
            continue

        stats = populate_from_transcript(jsonl_file, mission_id)
        results["transcripts_processed"] += 1
        results["total_tool_calls"] += stats.get("tool_calls_found", 0)
        if stats.get("errors"):
            results["errors"].extend(stats["errors"])

    return results


def populate_all_archived_missions() -> Dict:
    """
    Populate exploration and decision graph data from all archived missions.

    This is useful for backfilling historical data after enabling new tracking features.

    Returns:
        Dict with aggregate statistics
    """
    from pathlib import Path

    archive_dir = ARTIFACTS_DIR / "transcripts"

    results = {
        "missions_processed": 0,
        "total_transcripts": 0,
        "total_tool_calls": 0,
        "errors": []
    }

    if not archive_dir.exists():
        results["errors"].append(f"Archive directory not found: {archive_dir}")
        return results

    for mission_dir in archive_dir.iterdir():
        if not mission_dir.is_dir():
            continue

        mission_id = mission_dir.name
        mission_results = populate_from_mission_archive(mission_id)

        results["missions_processed"] += 1
        results["total_transcripts"] += mission_results.get("transcripts_processed", 0)
        results["total_tool_calls"] += mission_results.get("total_tool_calls", 0)
        if mission_results.get("errors"):
            results["errors"].extend([f"{mission_id}: {e}" for e in mission_results["errors"]])

    return results


def populate_exploration_from_transcripts(limit_missions: int = 10) -> Dict:
    """
    Populate the exploration graph from archived transcripts.

    Parses Read tool calls to extract file paths that were explored
    and adds them to the exploration graph.

    Args:
        limit_missions: Maximum missions to process (most recent first)

    Returns:
        Dict with population statistics
    """
    from pathlib import Path
    from atlasforge_enhancements import ExplorationGraph

    archive_dir = ARTIFACTS_DIR / "transcripts"
    graph = ExplorationGraph(storage_path=EXPLORATION_DIR)

    results = {
        "missions_processed": 0,
        "files_added": 0,
        "edges_added": 0,
        "errors": []
    }

    if not archive_dir.exists():
        results["errors"].append(f"Archive directory not found: {archive_dir}")
        return results

    # Sort by modification time, most recent first
    mission_dirs = sorted(
        [d for d in archive_dir.iterdir() if d.is_dir()],
        key=lambda x: x.stat().st_mtime,
        reverse=True
    )[:limit_missions]

    for mission_dir in mission_dirs:
        mission_id = mission_dir.name
        prev_file_path = None

        for jsonl_file in mission_dir.glob("*.jsonl"):
            if jsonl_file.name == "manifest.json":
                continue

            try:
                with open(jsonl_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue

                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Look for Read tool calls
                        if record.get("type") == "assistant":
                            message = record.get("message", {})
                            content = message.get("content", [])

                            for block in content:
                                if isinstance(block, dict) and block.get("type") == "tool_use":
                                    tool_name = block.get("name", "")
                                    tool_input = block.get("input", {})

                                    if tool_name == "Read":
                                        file_path = tool_input.get("file_path", "")
                                        if file_path and file_path.startswith("/"):
                                            # Add node using correct method
                                            try:
                                                graph.add_file_node(
                                                    path=file_path,
                                                    summary=f"Explored in {mission_id}",
                                                    mission_id=mission_id,
                                                    tags=["historical"]
                                                )
                                                results["files_added"] += 1

                                                # Add edge to previous file
                                                if prev_file_path and prev_file_path != file_path:
                                                    graph.add_edge(
                                                        source_path=prev_file_path,
                                                        target_path=file_path,
                                                        relationship="explored_next"
                                                    )
                                                    results["edges_added"] += 1

                                                prev_file_path = file_path
                                            except Exception:
                                                pass  # Skip invalid paths

            except Exception as e:
                results["errors"].append(f"{mission_id}/{jsonl_file.name}: {str(e)}")

        results["missions_processed"] += 1

    # Save the graph
    graph.save()
    results["total_nodes"] = len(graph.nodes)
    results["total_edges"] = len(graph.edges)

    return results


# =============================================================================
# DEMO / SELF-TEST
# =============================================================================

if __name__ == "__main__":
    print("Exploration Hooks - Self Test")
    print("=" * 50)

    # Test without a proper mission (should gracefully fail)
    print("\nTesting without proper mission setup:")
    print(f"  should_explore: {should_explore('/test.py')}")
    print(f"  what_do_we_know: {what_do_we_know('test')}")
    print(f"  get_stats: {get_exploration_stats()}")

    # Test tool logging functions
    print("\nTesting tool logging functions (without mission):")
    print(f"  log_read_tool: {log_read_tool('/test.py', 100)}")
    print(f"  log_write_tool: {log_write_tool('/test.py', 500)}")
    print(f"  log_bash_tool: {log_bash_tool('ls -la', 0, 'output...')}")

    print("\nExploration hooks ready for use!")
    print("Import with: from exploration_hooks import record_exploration, what_do_we_know")
    print("For tool logging: from exploration_hooks import log_tool_invocation, log_read_tool, etc.")
