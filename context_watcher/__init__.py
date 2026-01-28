"""
ContextWatcher: Real-Time JSONL Token Monitor for AtlasForge Conductor

Monitors Claude's context usage and triggers early handoffs when approaching limits.
"""

from .context_watcher import (
    ContextWatcher,
    TokenState,
    SessionMonitor,
    HandoffSignal,
    HandoffLevel,
    TimeBasedHandoffMonitor,
    get_context_watcher,
    start_context_watching,
    stop_context_watching,
    stop_all_context_watching,
    write_handoff_state,
    count_handoffs,
    find_transcript_dir,
    is_p_mode_session,
    GRACEFUL_THRESHOLD,
    EMERGENCY_THRESHOLD,
    LOW_CACHE_READ_THRESHOLD,
    CONTEXT_WATCHER_ENABLED,
    TIME_BASED_HANDOFF_ENABLED,
    TIME_BASED_HANDOFF_MINUTES,
)

__all__ = [
    "ContextWatcher",
    "TokenState",
    "SessionMonitor",
    "HandoffSignal",
    "HandoffLevel",
    "TimeBasedHandoffMonitor",
    "get_context_watcher",
    "start_context_watching",
    "stop_context_watching",
    "stop_all_context_watching",
    "write_handoff_state",
    "count_handoffs",
    "find_transcript_dir",
    "is_p_mode_session",
    "GRACEFUL_THRESHOLD",
    "EMERGENCY_THRESHOLD",
    "LOW_CACHE_READ_THRESHOLD",
    "CONTEXT_WATCHER_ENABLED",
    "TIME_BASED_HANDOFF_ENABLED",
    "TIME_BASED_HANDOFF_MINUTES",
]
