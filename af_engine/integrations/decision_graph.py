"""
af_engine.integrations.decision_graph - Tool Invocation Tracking

This integration tracks tool invocations to build a decision graph
for analysis and debugging.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class DecisionGraphIntegration(BaseIntegrationHandler):
    """
    Tracks tool invocations to build decision graph.

    Records the sequence of tools used and decisions made during
    mission execution for analysis and debugging.
    """

    name = "decision_graph"
    priority = IntegrationPriority.HIGH
    subscriptions = [
        StageEvent.MISSION_STARTED,
        StageEvent.STAGE_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.RESPONSE_RECEIVED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self):
        """Initialize decision graph tracking."""
        super().__init__()
        self.nodes = []
        self.edges = []
        self.current_stage = None

    def on_mission_started(self, event: Event) -> None:
        """Initialize graph for new mission."""
        self.nodes = []
        self.edges = []
        self.current_stage = None

        # Add mission start node
        self._add_node("MISSION_START", {
            "mission_id": event.mission_id,
            "timestamp": event.timestamp.isoformat(),
        })

    def on_stage_started(self, event: Event) -> None:
        """Track stage transition."""
        previous_stage = self.current_stage
        self.current_stage = event.stage

        # Add stage node
        node_id = self._add_node(f"STAGE_{event.stage}", {
            "stage": event.stage,
            "timestamp": event.timestamp.isoformat(),
        })

        # Add edge from previous stage
        if previous_stage:
            self._add_edge(
                f"STAGE_{previous_stage}",
                node_id,
                "transition"
            )
        else:
            self._add_edge("MISSION_START", node_id, "start")

    def on_stage_completed(self, event: Event) -> None:
        """Track stage completion with status."""
        # Update stage node with completion status
        for node in self.nodes:
            if node["id"] == f"STAGE_{event.stage}":
                node["data"]["status"] = event.data.get("status", "")
                node["data"]["completed_at"] = event.timestamp.isoformat()
                break

    def on_response_received(self, event: Event) -> None:
        """Track tool invocations in response."""
        tools_used = event.data.get("tools_used", [])

        for tool in tools_used:
            tool_node_id = self._add_node(f"TOOL_{tool}", {
                "tool": tool,
                "stage": self.current_stage,
                "timestamp": event.timestamp.isoformat(),
            })

            # Connect tool to current stage
            if self.current_stage:
                self._add_edge(
                    f"STAGE_{self.current_stage}",
                    tool_node_id,
                    "invoked"
                )

    def on_mission_completed(self, event: Event) -> None:
        """Finalize decision graph."""
        # Add mission complete node
        complete_node = self._add_node("MISSION_COMPLETE", {
            "mission_id": event.mission_id,
            "total_cycles": event.data.get("total_cycles", 0),
            "timestamp": event.timestamp.isoformat(),
        })

        # Connect final stage to complete
        if self.current_stage:
            self._add_edge(
                f"STAGE_{self.current_stage}",
                complete_node,
                "complete"
            )

        logger.info(f"Decision graph: {len(self.nodes)} nodes, {len(self.edges)} edges")

    def _add_node(self, node_id: str, data: Dict[str, Any]) -> str:
        """Add a node to the graph."""
        self.nodes.append({
            "id": node_id,
            "data": data,
            "created_at": datetime.now().isoformat(),
        })
        return node_id

    def _add_edge(self, from_id: str, to_id: str, edge_type: str) -> None:
        """Add an edge to the graph."""
        self.edges.append({
            "from": from_id,
            "to": to_id,
            "type": edge_type,
            "created_at": datetime.now().isoformat(),
        })

    def get_graph(self) -> Dict[str, Any]:
        """Get the complete decision graph."""
        return {
            "nodes": self.nodes,
            "edges": self.edges,
        }

    def get_node_count(self) -> int:
        """Get number of nodes in graph."""
        return len(self.nodes)
