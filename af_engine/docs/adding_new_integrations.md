# Adding New Integrations to af_engine

This guide explains how to add new integrations (event subscribers) to the modular af_engine architecture.

## Overview

Integrations are event-driven plugins that react to stage transitions and mission events. They enable features like:

- Analytics and monitoring
- Git operations
- Knowledge base updates
- Snapshot creation
- WebSocket notifications

## Step 1: Create the Integration Handler

Create a new file in `af_engine/integrations/`:

```python
# af_engine/integrations/my_integration.py

import logging
from typing import Optional, Dict, Any

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class MyIntegration(BaseIntegrationHandler):
    """
    Description of what this integration does.

    Operates at MEDIUM priority.
    """

    name = "my_integration"
    priority = IntegrationPriority.MEDIUM

    # Events this integration subscribes to
    subscriptions = [
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the integration."""
        super().__init__()
        self.config = config or {}

    def _check_availability(self) -> bool:
        """
        Check if this integration can run.

        Return False if dependencies are missing.
        """
        try:
            # Check for required dependencies
            import some_required_module
            return True
        except ImportError:
            logger.debug("my_integration: dependency not available")
            return False

    def on_stage_completed(self, event: Event) -> None:
        """Handle stage completion events."""
        stage = event.stage
        mission_id = event.mission_id
        data = event.data

        logger.info(f"my_integration: Stage {stage} completed for {mission_id}")

        # Do something useful
        self._process_stage_completion(stage, data)

    def on_mission_completed(self, event: Event) -> None:
        """Handle mission completion events."""
        mission_id = event.mission_id
        final_report = event.data.get("final_report", {})

        logger.info(f"my_integration: Mission {mission_id} completed")

        # Do something useful
        self._finalize_mission(mission_id, final_report)

    def _process_stage_completion(self, stage: str, data: Dict) -> None:
        """Internal method to process stage completion."""
        # Your logic here
        pass

    def _finalize_mission(self, mission_id: str, report: Dict) -> None:
        """Internal method to finalize mission data."""
        # Your logic here
        pass
```

## Step 2: Register the Integration

Update `af_engine/integrations/__init__.py`:

```python
from .my_integration import MyIntegration

__all__ = [
    # ... existing integrations ...
    "MyIntegration",
]
```

## Step 3: Add to Configuration

Update `af_engine/config/integration_config.yaml`:

```yaml
integrations:
  # ... existing integrations ...

  my_integration:
    enabled: true
    priority: medium
    subscriptions:
      - STAGE_COMPLETED
      - MISSION_COMPLETED
    config:
      some_option: "value"
```

## Key Concepts

### Event Types

Available event types in `StageEvent`:

| Event | When Fired | Typical Data |
|-------|------------|--------------|
| `STAGE_STARTED` | When a stage begins | `{"stage": "BUILDING"}` |
| `STAGE_COMPLETED` | When a stage ends | `{"status": "complete", "output": {...}}` |
| `MISSION_STARTED` | When mission begins | `{"mission_id": "...", "problem_statement": "..."}` |
| `MISSION_COMPLETED` | When mission ends | `{"final_report": {...}, "deliverables": [...]}` |
| `CYCLE_COMPLETED` | When a cycle ends | `{"cycle_number": 1, "continuation_prompt": "..."}` |
| `ERROR` | When an error occurs | `{"error": "...", "stage": "..."}` |

### Priority Levels

Integrations run in priority order:

```python
class IntegrationPriority:
    CRITICAL = 0   # Run first (e.g., error recovery)
    HIGH = 1       # Important integrations
    MEDIUM = 2     # Standard integrations
    LOW = 3        # Background/optional integrations
```

### Event Structure

```python
@dataclass
class Event:
    type: StageEvent          # Event type
    stage: str                # Current stage name
    mission_id: str           # Mission identifier
    data: Dict[str, Any]      # Event-specific data
    timestamp: str            # ISO timestamp (auto-generated)
```

### Handler Methods

The base class routes events to handler methods by convention:

| Event Type | Handler Method |
|------------|----------------|
| `STAGE_STARTED` | `on_stage_started(event)` |
| `STAGE_COMPLETED` | `on_stage_completed(event)` |
| `MISSION_STARTED` | `on_mission_started(event)` |
| `MISSION_COMPLETED` | `on_mission_completed(event)` |
| `CYCLE_COMPLETED` | `on_cycle_completed(event)` |
| `ERROR` | `on_error(event)` |

You only need to implement the methods for events you subscribe to.

## Examples

### Analytics Integration

```python
class AnalyticsIntegration(BaseIntegrationHandler):
    name = "analytics"
    priority = IntegrationPriority.LOW
    subscriptions = [StageEvent.STAGE_COMPLETED]

    def on_stage_completed(self, event: Event) -> None:
        # Track stage timing
        duration = event.data.get("duration_seconds", 0)
        self._record_metric(event.stage, "duration", duration)
```

### Git Integration

```python
class GitIntegration(BaseIntegrationHandler):
    name = "git"
    priority = IntegrationPriority.MEDIUM
    subscriptions = [
        StageEvent.STAGE_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def on_stage_completed(self, event: Event) -> None:
        if event.stage == "BUILDING":
            self._create_checkpoint_commit(event.mission_id)

    def on_cycle_completed(self, event: Event) -> None:
        self._create_cycle_tag(event.mission_id, event.data.get("cycle_number"))
```

### WebSocket Notification

```python
class WebSocketIntegration(BaseIntegrationHandler):
    name = "websocket"
    priority = IntegrationPriority.HIGH
    subscriptions = [
        StageEvent.STAGE_STARTED,
        StageEvent.STAGE_COMPLETED,
        StageEvent.ERROR,
    ]

    def _check_availability(self) -> bool:
        return hasattr(self, 'socketio') and self.socketio is not None

    def on_stage_started(self, event: Event) -> None:
        self._broadcast("stage_update", {
            "stage": event.stage,
            "status": "started"
        })
```

## Error Handling

Integrations should handle errors gracefully:

```python
def on_stage_completed(self, event: Event) -> None:
    try:
        self._do_something_risky()
    except Exception as e:
        # Log the error but don't crash
        logger.warning(f"my_integration: Error processing event: {e}")
        # Optionally emit an error event
        # self._emit_error(event.mission_id, str(e))
```

The integration manager catches exceptions to prevent one integration from breaking others.

## Testing

Create tests for your integration:

```python
def test_my_integration():
    from af_engine.integrations.my_integration import MyIntegration
    from af_engine.integrations.base import Event, StageEvent

    handler = MyIntegration()

    # Test availability
    assert handler.is_available()

    # Test event handling
    event = Event(
        type=StageEvent.STAGE_COMPLETED,
        stage="BUILDING",
        mission_id="test_mission",
        data={"status": "complete"}
    )

    # Should not raise
    handler.on_stage_completed(event)
```

## Best Practices

1. **Check availability** - Return `False` from `_check_availability()` if dependencies are missing
2. **Use appropriate priority** - Don't use CRITICAL unless necessary
3. **Handle errors gracefully** - Don't let your integration crash the engine
4. **Log meaningfully** - Use appropriate log levels (debug for details, info for important events)
5. **Keep subscriptions minimal** - Only subscribe to events you actually handle
6. **Document your integration** - Explain what it does and how to configure it
