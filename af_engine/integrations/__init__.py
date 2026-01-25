"""
af_engine.integrations - Event-Driven Integration Handlers

This module provides integration handlers that subscribe to stage events
and perform actions in response. Each integration is decoupled from the
core orchestrator and can fail gracefully without blocking other integrations.

Event Types:
    - STAGE_STARTED: Emitted when a stage begins
    - STAGE_COMPLETED: Emitted when a stage completes successfully
    - STAGE_FAILED: Emitted when a stage fails
    - CYCLE_STARTED: Emitted when a new cycle begins
    - CYCLE_COMPLETED: Emitted when a cycle completes
    - MISSION_STARTED: Emitted when a mission starts
    - MISSION_COMPLETED: Emitted when a mission ends
    - RESPONSE_RECEIVED: Emitted when Claude's response is received

Integration Handlers:
    - AnalyticsIntegration: Token usage and cost tracking
    - TokenWatcherIntegration: Real-time token monitoring
    - RecoveryIntegration: Checkpoint and crash recovery
    - GitIntegration: Checkpoint-based git commits
    - DriftValidationIntegration: Mission drift detection
    - KnowledgeBaseIntegration: Learning extraction and injection
    - QueueSchedulerIntegration: Mission queue management
    - AfterImageIntegration: Episodic code memory
    - PlanBackupIntegration: Plan file backup before building
    - ArtifactManagerIntegration: Automated artifact management
    - PostMissionHooksIntegration: Custom post-mission scripts
    - SnapshotIntegration: Mission state snapshots
    - WebSocketIntegration: Real-time UI updates
    - EnhancerIntegration: AtlasForge enhancements
    - DecisionGraphIntegration: Tool invocation tracking
"""

from .base import (
    IntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

# Import all integration handlers
from .analytics import AnalyticsIntegration
from .token_watcher import TokenWatcherIntegration
from .recovery import RecoveryIntegration
from .git import GitIntegration
from .drift_validation import DriftValidationIntegration
from .knowledge_base import KnowledgeBaseIntegration
from .queue_scheduler import QueueSchedulerIntegration
from .afterimage import AfterImageIntegration
from .plan_backup import PlanBackupIntegration
from .artifact_manager import ArtifactManagerIntegration
from .post_mission_hooks import PostMissionHooksIntegration
from .snapshots import SnapshotIntegration
from .websocket_events import WebSocketIntegration
from .enhancer import EnhancerIntegration
from .decision_graph import DecisionGraphIntegration

__all__ = [
    # Protocol and data classes
    'IntegrationHandler',
    'Event',
    'StageEvent',
    'IntegrationPriority',
    # Integration handlers
    'AnalyticsIntegration',
    'TokenWatcherIntegration',
    'RecoveryIntegration',
    'GitIntegration',
    'DriftValidationIntegration',
    'KnowledgeBaseIntegration',
    'QueueSchedulerIntegration',
    'AfterImageIntegration',
    'PlanBackupIntegration',
    'ArtifactManagerIntegration',
    'PostMissionHooksIntegration',
    'SnapshotIntegration',
    'WebSocketIntegration',
    'EnhancerIntegration',
    'DecisionGraphIntegration',
]

# Default integrations to load (in priority order)
DEFAULT_INTEGRATIONS = [
    AnalyticsIntegration,
    TokenWatcherIntegration,
    RecoveryIntegration,
    WebSocketIntegration,
    DecisionGraphIntegration,
    GitIntegration,
    SnapshotIntegration,
    DriftValidationIntegration,
    KnowledgeBaseIntegration,
    AfterImageIntegration,
    PlanBackupIntegration,
    ArtifactManagerIntegration,
    QueueSchedulerIntegration,
    PostMissionHooksIntegration,
    EnhancerIntegration,
]


def get_default_integrations():
    """
    Get instances of all default integrations.

    Returns:
        List of integration handler instances
    """
    integrations = []
    for handler_class in DEFAULT_INTEGRATIONS:
        try:
            integrations.append(handler_class())
        except Exception:
            # Integration initialization failed - skip it
            pass
    return integrations
