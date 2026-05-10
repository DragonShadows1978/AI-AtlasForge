"""
MissionReconstructor: Builds complete mission timeline from transcripts.

Links all sessions belonging to a mission, constructs agent spawn tree,
and maps stage transitions with durations.
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import json
import re

from .transcript_parser import TranscriptParser, TranscriptSession, TranscriptMessage


# Token pricing (per million tokens) - December 2024
PRICING = {
    'claude-opus-4-5-20251101': {'input': 15.0, 'output': 75.0},
    'claude-sonnet-4-20250514': {'input': 3.0, 'output': 15.0},
    'claude-3-5-sonnet-20241022': {'input': 3.0, 'output': 15.0},
    'claude-haiku-4-5-20251001': {'input': 0.80, 'output': 4.0},
    'claude-3-5-haiku-20241022': {'input': 0.80, 'output': 4.0},
    # Default for unknown models
    'default': {'input': 3.0, 'output': 15.0},
}


@dataclass
class AgentNode:
    """Node in the agent hierarchy tree."""
    agent_id: str
    session_id: str
    parent_agent_id: Optional[str] = None
    children: List['AgentNode'] = field(default_factory=list)

    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    # Metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation: int = 0
    total_cache_read: int = 0
    tool_calls_count: int = 0

    # Model info
    model: Optional[str] = None

    # Stage info (for root agents in stage-specific runs)
    stage: Optional[str] = None

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict for dashboard."""
        return {
            'agent_id': self.agent_id,
            'session_id': self.session_id,
            'parent_agent_id': self.parent_agent_id,
            'children': [c.to_dict() for c in self.children],
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': round(self.duration_seconds, 2),
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_tokens': self.total_tokens,
            'total_cache_creation': self.total_cache_creation,
            'total_cache_read': self.total_cache_read,
            'tool_calls_count': self.tool_calls_count,
            'model': self.model,
            'stage': self.stage,
        }


@dataclass
class StageTimeline:
    """Timeline entry for a stage."""
    stage: str  # INITIALIZE, PLANNING, BUILDING, TESTING, ANALYZING, COMPLETE
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    tokens_used: int = 0
    agents_spawned: int = 0

    @property
    def duration_seconds(self) -> float:
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict."""
        return {
            'stage': self.stage,
            'start_time': self.start_time.isoformat() if self.start_time else None,
            'end_time': self.end_time.isoformat() if self.end_time else None,
            'duration_seconds': round(self.duration_seconds, 2),
            'tokens_used': self.tokens_used,
            'agents_spawned': self.agents_spawned,
        }


@dataclass
class DecisionEvent:
    """Key decision moment in the mission."""
    event_type: str  # 'stage_transition', 'file_write', 'error', 'tool_call'
    timestamp: datetime
    description: str
    agent_id: Optional[str] = None
    message_uuid: Optional[str] = None
    details: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            'event_type': self.event_type,
            'timestamp': self.timestamp.isoformat(),
            'description': self.description,
            'agent_id': self.agent_id,
            'message_uuid': self.message_uuid,
            'details': self.details,
        }


@dataclass
class MissionTimeline:
    """Complete reconstructed mission."""
    mission_id: str
    archive_path: str
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None

    # Agent hierarchy
    root_agents: List[AgentNode] = field(default_factory=list)
    all_agents: List[AgentNode] = field(default_factory=list)

    # Stage timeline (Gantt-style)
    stages: List[StageTimeline] = field(default_factory=list)

    # Aggregate metrics
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cache_creation: int = 0
    total_cache_read: int = 0
    estimated_cost_usd: float = 0.0

    # Key events
    decision_log: List[DecisionEvent] = field(default_factory=list)

    # Sessions for transcript viewing
    sessions: List[TranscriptSession] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def total_duration_seconds(self) -> float:
        if self.created_at and self.completed_at:
            return (self.completed_at - self.created_at).total_seconds()
        return 0.0

    def to_dict(self) -> Dict:
        """Convert to JSON-serializable dict for API responses."""
        return {
            'mission_id': self.mission_id,
            'archive_path': self.archive_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'total_duration_seconds': round(self.total_duration_seconds, 2),
            'total_input_tokens': self.total_input_tokens,
            'total_output_tokens': self.total_output_tokens,
            'total_tokens': self.total_tokens,
            'total_cache_creation': self.total_cache_creation,
            'total_cache_read': self.total_cache_read,
            'estimated_cost_usd': round(self.estimated_cost_usd, 4),
            'stages': [s.to_dict() for s in self.stages],
            'root_agents': [a.to_dict() for a in self.root_agents],
            'agent_count': len(self.all_agents),
            'decision_log': [d.to_dict() for d in self.decision_log],
        }


class MissionReconstructor:
    """Reconstructs mission timeline from archived transcripts."""

    STAGE_ORDER = ['INITIALIZE', 'PLANNING', 'BUILDING', 'TESTING', 'ANALYZING', 'COMPLETE']

    def __init__(self, transcripts_dir: Path, mission_metadata: Optional[Dict] = None):
        """
        Initialize with archived transcripts directory.

        Args:
            transcripts_dir: Path to mission archive directory
            mission_metadata: Optional mission.json data
        """
        self.transcripts_dir = Path(transcripts_dir)
        self.mission_metadata = mission_metadata or {}
        self.parser = TranscriptParser()

    def reconstruct(self) -> MissionTimeline:
        """
        Build complete mission timeline from transcripts.

        Returns:
            MissionTimeline with full agent hierarchy and metrics
        """
        # Parse all sessions
        sessions = self.parser.parse_directory(self.transcripts_dir)

        # Get mission ID from path or metadata
        mission_id = self.mission_metadata.get('mission_id') or self.transcripts_dir.name

        timeline = MissionTimeline(
            mission_id=mission_id,
            archive_path=str(self.transcripts_dir),
            sessions=sessions,
        )

        # Build agent tree
        self._build_agent_tree(sessions, timeline)

        # Extract stages
        self._extract_stages(sessions, timeline)

        # Calculate totals
        self._calculate_totals(timeline)

        # Calculate costs
        self._calculate_costs(sessions, timeline)

        # Extract decision log
        self._extract_decision_log(sessions, timeline)

        # Set time bounds
        if sessions:
            all_starts = [s.start_time for s in sessions if s.start_time]
            all_ends = [s.end_time for s in sessions if s.end_time]
            if all_starts:
                timeline.created_at = min(all_starts)
            if all_ends:
                timeline.completed_at = max(all_ends)

        return timeline

    def _build_agent_tree(self, sessions: List[TranscriptSession], timeline: MissionTimeline):
        """
        Construct parent-child agent hierarchy.

        Strategy:
        1. Root sessions are UUID-named files (not agent-*.jsonl)
        2. Subagents are agent-*.jsonl files
        3. Link via agentId in messages and parentUuid chains
        """
        # Separate root and subagent sessions
        root_sessions = [s for s in sessions if not s.is_subagent]
        subagent_sessions = [s for s in sessions if s.is_subagent]

        # Create agent nodes for all sessions
        agent_nodes: Dict[str, AgentNode] = {}

        # Create root agents
        for session in root_sessions:
            node = AgentNode(
                agent_id=session.session_id or session.source_file.replace('.jsonl', ''),
                session_id=session.session_id,
                start_time=session.start_time,
                end_time=session.end_time,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                total_cache_creation=session.total_cache_creation,
                total_cache_read=session.total_cache_read,
                tool_calls_count=len(session.tool_calls),
            )
            # Get model from first assistant message
            for msg in session.messages:
                if msg.model:
                    node.model = msg.model
                    break

            agent_nodes[node.agent_id] = node
            timeline.root_agents.append(node)

        # Create subagent nodes
        for session in subagent_sessions:
            agent_id = session.agent_id or session.source_file.replace('.jsonl', '').replace('agent-', '')
            node = AgentNode(
                agent_id=agent_id,
                session_id=session.session_id,
                start_time=session.start_time,
                end_time=session.end_time,
                total_input_tokens=session.total_input_tokens,
                total_output_tokens=session.total_output_tokens,
                total_cache_creation=session.total_cache_creation,
                total_cache_read=session.total_cache_read,
                tool_calls_count=len(session.tool_calls),
            )
            # Get model from first assistant message
            for msg in session.messages:
                if msg.model:
                    node.model = msg.model
                    break

            agent_nodes[agent_id] = node

        # Try to establish parent-child relationships
        # Look at sessionId references in subagent files
        for session in subagent_sessions:
            agent_id = session.agent_id or session.source_file.replace('.jsonl', '').replace('agent-', '')
            child_node = agent_nodes.get(agent_id)
            if not child_node:
                continue

            # Look for parent session ID in the messages
            parent_session_id = None
            for msg in session.messages:
                if msg.session_id and msg.session_id != session.session_id:
                    parent_session_id = msg.session_id
                    break

            # The sessionId in subagent files often refers to their parent
            if session.session_id:
                # Check if there's a root session with this ID
                for root_node in timeline.root_agents:
                    if root_node.session_id == session.session_id:
                        child_node.parent_agent_id = root_node.agent_id
                        root_node.children.append(child_node)
                        break

        timeline.all_agents = list(agent_nodes.values())

    def _extract_stages(self, sessions: List[TranscriptSession], timeline: MissionTimeline):
        """
        Extract stage transitions from messages.

        Uses heuristics to detect stage boundaries:
        1. Stage markers in prompts/responses
        2. Status fields in JSON responses
        3. Mission history if available
        """
        # Collect all stage transitions with timestamps
        stage_events: List[tuple] = []  # (timestamp, stage, session)

        for session in sessions:
            transitions = self.parser.extract_stage_transitions(session)
            for trans in transitions:
                try:
                    ts = datetime.fromisoformat(trans['timestamp'])
                    stage_events.append((ts, trans['stage'], session))
                except (ValueError, KeyError):
                    pass

        # Sort by timestamp
        stage_events.sort(key=lambda x: x[0])

        if not stage_events:
            # No explicit transitions found, create single stage from overall timeline
            if sessions:
                all_starts = [s.start_time for s in sessions if s.start_time]
                all_ends = [s.end_time for s in sessions if s.end_time]
                if all_starts and all_ends:
                    timeline.stages.append(StageTimeline(
                        stage='BUILDING',  # Default assumption
                        start_time=min(all_starts),
                        end_time=max(all_ends),
                        tokens_used=sum(s.total_tokens for s in sessions),
                        agents_spawned=len(sessions),
                    ))
            return

        # Build stage timeline from events
        current_stage = None
        stage_start = None
        stage_tokens = 0
        stage_agents = 0

        for i, (timestamp, stage, session) in enumerate(stage_events):
            if current_stage and current_stage != stage:
                # Stage changed, close previous
                timeline.stages.append(StageTimeline(
                    stage=current_stage,
                    start_time=stage_start,
                    end_time=timestamp,
                    tokens_used=stage_tokens,
                    agents_spawned=stage_agents,
                ))
                stage_tokens = 0
                stage_agents = 0

            if current_stage != stage:
                current_stage = stage
                stage_start = timestamp

            stage_tokens += session.total_tokens
            stage_agents += 1

        # Close final stage
        if current_stage:
            end_time = stage_events[-1][2].end_time or stage_events[-1][0]
            timeline.stages.append(StageTimeline(
                stage=current_stage,
                start_time=stage_start,
                end_time=end_time,
                tokens_used=stage_tokens,
                agents_spawned=stage_agents,
            ))

    def _calculate_totals(self, timeline: MissionTimeline):
        """Aggregate token totals from all agents."""
        for agent in timeline.all_agents:
            timeline.total_input_tokens += agent.total_input_tokens
            timeline.total_output_tokens += agent.total_output_tokens
            timeline.total_cache_creation += agent.total_cache_creation
            timeline.total_cache_read += agent.total_cache_read

    def _calculate_costs(self, sessions: List[TranscriptSession], timeline: MissionTimeline):
        """
        Calculate estimated cost based on model and token usage.

        Uses per-model pricing with cache discounts.
        """
        total_cost = 0.0

        for session in sessions:
            # Get model from first message
            model = None
            for msg in session.messages:
                if msg.model:
                    model = msg.model
                    break

            # Get pricing
            pricing = PRICING.get(model, PRICING['default'])

            # Calculate costs (per million tokens)
            input_cost = (session.total_input_tokens / 1_000_000) * pricing['input']
            output_cost = (session.total_output_tokens / 1_000_000) * pricing['output']

            # Cache discounts
            cache_creation_cost = (session.total_cache_creation / 1_000_000) * pricing['input'] * 1.25  # 25% premium
            cache_read_cost = (session.total_cache_read / 1_000_000) * pricing['input'] * 0.1  # 90% discount

            total_cost += input_cost + output_cost + cache_creation_cost + cache_read_cost

        timeline.estimated_cost_usd = total_cost

    def _extract_decision_log(self, sessions: List[TranscriptSession], timeline: MissionTimeline):
        """
        Extract key decision moments from transcripts.

        Events: stage transitions, file writes, errors, significant tool calls
        """
        events = []

        for session in sessions:
            agent_id = session.agent_id or session.session_id

            # Add stage transitions
            transitions = self.parser.extract_stage_transitions(session)
            for trans in transitions:
                try:
                    ts = datetime.fromisoformat(trans['timestamp'])
                    events.append(DecisionEvent(
                        event_type='stage_transition',
                        timestamp=ts,
                        description=f"Stage: {trans['stage']}",
                        agent_id=agent_id,
                        message_uuid=trans.get('message_uuid'),
                    ))
                except (ValueError, KeyError):
                    pass

            # Add significant tool calls (Write, Edit)
            for tc in session.tool_calls:
                if tc.name in ('Write', 'Edit'):
                    file_path = tc.input_params.get('file_path', 'unknown')
                    # Extract just filename
                    if '/' in file_path:
                        file_path = file_path.split('/')[-1]
                    events.append(DecisionEvent(
                        event_type='file_write',
                        timestamp=tc.timestamp,
                        description=f"{tc.name}: {file_path}",
                        agent_id=agent_id,
                        details={'tool': tc.name, 'path': file_path},
                    ))

            # Look for error messages
            for msg in session.messages:
                text = msg.text_content.lower()
                if 'error' in text or 'failed' in text or 'exception' in text:
                    # Extract first line with error
                    for line in msg.text_content.split('\n'):
                        if 'error' in line.lower() or 'failed' in line.lower():
                            events.append(DecisionEvent(
                                event_type='error',
                                timestamp=msg.timestamp,
                                description=line[:100],
                                agent_id=agent_id,
                                message_uuid=msg.uuid,
                            ))
                            break

        # Sort by timestamp and limit
        events.sort(key=lambda e: e.timestamp)
        timeline.decision_log = events[:100]  # Limit to 100 events

    def get_session_by_agent_id(self, agent_id: str) -> Optional[TranscriptSession]:
        """Find session by agent ID for transcript viewing."""
        sessions = self.parser.parse_directory(self.transcripts_dir)
        for session in sessions:
            if session.agent_id == agent_id or session.session_id == agent_id:
                return session
            # Check filename
            if agent_id in session.source_file:
                return session
        return None
