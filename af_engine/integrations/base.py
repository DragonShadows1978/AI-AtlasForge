"""
af_engine.integrations.base - Integration Handler Protocol and Data Classes

This module defines the core interfaces and data structures for integration handlers.
All integration handlers must implement the IntegrationHandler protocol.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Dict, Any, List, Optional, Protocol, runtime_checkable


class StageEvent(Enum):
    """
    Events that can be emitted during mission execution.

    Integration handlers subscribe to these events to perform
    actions at specific points in the mission lifecycle.
    """
    # Stage lifecycle events
    STAGE_STARTED = "stage_started"
    STAGE_COMPLETED = "stage_completed"
    STAGE_FAILED = "stage_failed"

    # Cycle lifecycle events
    CYCLE_STARTED = "cycle_started"
    CYCLE_COMPLETED = "cycle_completed"

    # Mission lifecycle events
    MISSION_STARTED = "mission_started"
    MISSION_COMPLETED = "mission_completed"
    MISSION_FAILED = "mission_failed"

    # Response events
    RESPONSE_RECEIVED = "response_received"
    PROMPT_GENERATED = "prompt_generated"

    # State events
    STATE_SAVED = "state_saved"
    STATE_LOADED = "state_loaded"

    # Integration-specific events
    CHECKPOINT_CREATED = "checkpoint_created"
    SNAPSHOT_CREATED = "snapshot_created"
    DRIFT_DETECTED = "drift_detected"
    LEARNING_EXTRACTED = "learning_extracted"


class IntegrationPriority(Enum):
    """
    Priority levels for integration handlers.

    Higher priority integrations are called first. Use CRITICAL for
    integrations that must run before others (e.g., analytics tracking).
    """
    CRITICAL = 0    # Always runs first (analytics, token tracking)
    HIGH = 10       # Important but not critical (recovery, websocket)
    NORMAL = 20     # Default priority (git, snapshots)
    LOW = 30        # Background operations (learning extraction)
    BACKGROUND = 40 # Non-essential (enhancer)


@dataclass
class Event:
    """
    Event data passed to integration handlers.

    Contains all context needed by handlers to process the event.
    """
    # Event type
    type: StageEvent

    # Stage context
    stage: str
    mission_id: str

    # Timing
    timestamp: datetime = field(default_factory=datetime.now)

    # Event-specific data
    data: Dict[str, Any] = field(default_factory=dict)

    # Source information
    source: Optional[str] = None  # e.g., "orchestrator", "cycle_manager"

    def __post_init__(self):
        """Ensure timestamp is set."""
        if self.timestamp is None:
            self.timestamp = datetime.now()


@runtime_checkable
class IntegrationHandler(Protocol):
    """
    Protocol defining the interface for integration handlers.

    Integration handlers subscribe to events and perform actions
    in response. They must handle errors gracefully and never
    block other integrations from running.
    """

    # Integration name for logging and configuration
    name: str

    # Priority determines execution order (lower = higher priority)
    priority: IntegrationPriority

    def handle_event(self, event: Event) -> None:
        """
        Handle an event.

        This method should:
        - Be idempotent (safe to call multiple times)
        - Handle errors internally (don't propagate exceptions)
        - Execute quickly (offload long operations if needed)

        Args:
            event: The event to handle
        """
        ...

    def get_subscriptions(self) -> List[StageEvent]:
        """
        Get the list of events this handler subscribes to.

        Returns:
            List of StageEvent types this handler wants to receive
        """
        ...

    def is_available(self) -> bool:
        """
        Check if this integration is available/enabled.

        Returns:
            True if the integration can be used, False otherwise
        """
        ...


class BaseIntegrationHandler:
    """
    Base class providing default implementations for integration handlers.

    Integration handlers can inherit from this class to get default behavior
    and only override what they need to customize.
    """

    name: str = "base"
    priority: IntegrationPriority = IntegrationPriority.NORMAL

    # Subscribed events (override in subclasses)
    subscriptions: List[StageEvent] = []

    # Internal state
    _available: bool = True
    _enabled: bool = True

    def __init__(self):
        """Initialize the integration handler."""
        self._available = self._check_availability()

    def handle_event(self, event: Event) -> None:
        """
        Handle an event by dispatching to specific handler methods.

        Override specific methods like on_stage_started() instead of
        this method for cleaner code.
        """
        if not self.is_available():
            return

        # Dispatch to specific handler method
        handler_method = f"on_{event.type.value}"
        if hasattr(self, handler_method):
            try:
                getattr(self, handler_method)(event)
            except Exception as e:
                self._handle_error(event, e)

    def get_subscriptions(self) -> List[StageEvent]:
        """Get subscribed events."""
        return self.subscriptions

    def is_available(self) -> bool:
        """Check if integration is available and enabled."""
        return self._available and self._enabled

    def enable(self) -> None:
        """Enable this integration."""
        self._enabled = True

    def disable(self) -> None:
        """Disable this integration."""
        self._enabled = False

    def _check_availability(self) -> bool:
        """
        Check if required dependencies are available.

        Override in subclasses to check for specific dependencies.
        """
        return True

    def _handle_error(self, event: Event, error: Exception) -> None:
        """
        Handle an error that occurred during event processing.

        Override to customize error handling (logging, reporting, etc.)
        """
        import logging
        logger = logging.getLogger(f"af_engine.integrations.{self.name}")
        logger.warning(f"Error handling {event.type.value}: {error}")

    # Event handler stubs - override in subclasses
    def on_stage_started(self, event: Event) -> None:
        """Called when a stage starts."""
        pass

    def on_stage_completed(self, event: Event) -> None:
        """Called when a stage completes successfully."""
        pass

    def on_stage_failed(self, event: Event) -> None:
        """Called when a stage fails."""
        pass

    def on_cycle_started(self, event: Event) -> None:
        """Called when a new cycle begins."""
        pass

    def on_cycle_completed(self, event: Event) -> None:
        """Called when a cycle completes."""
        pass

    def on_mission_started(self, event: Event) -> None:
        """Called when a mission starts."""
        pass

    def on_mission_completed(self, event: Event) -> None:
        """Called when a mission completes."""
        pass

    def on_mission_failed(self, event: Event) -> None:
        """Called when a mission fails."""
        pass

    def on_response_received(self, event: Event) -> None:
        """Called when Claude's response is received."""
        pass

    def on_prompt_generated(self, event: Event) -> None:
        """Called when a prompt is generated."""
        pass
