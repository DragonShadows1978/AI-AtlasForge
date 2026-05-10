"""
GlassBox: AtlasForge Introspection System

Provides deep visibility into mission execution, agent hierarchies,
and resource consumption by parsing Claude session transcripts.
"""

from .transcript_parser import (
    TranscriptParser,
    TranscriptMessage,
    TranscriptSession,
    ToolCall,
)
from .mission_reconstructor import (
    MissionReconstructor,
    AgentNode,
    StageTimeline,
    MissionTimeline,
)
from .mission_archiver import (
    MissionArchiver,
    list_archived_missions,
    load_mission_archive,
)
from .dashboard_routes import glassbox_bp

__all__ = [
    # Parser
    'TranscriptParser',
    'TranscriptMessage',
    'TranscriptSession',
    'ToolCall',
    # Reconstructor
    'MissionReconstructor',
    'AgentNode',
    'StageTimeline',
    'MissionTimeline',
    # Archiver
    'MissionArchiver',
    'list_archived_missions',
    'load_mission_archive',
    # Dashboard
    'glassbox_bp',
]

__version__ = '1.0.0'
