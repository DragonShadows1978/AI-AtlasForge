# Adding New Stages to af_engine

This guide explains how to add new stages to the modular af_engine architecture.

## Overview

The modular af_engine uses a plugin-based architecture where each stage is an independent handler. To add a new stage:

1. Create a new handler class
2. Register it in the stage registry
3. Define stage transitions
4. Add to configuration

## Step 1: Create the Stage Handler

Create a new file in `af_engine/stages/`:

```python
# af_engine/stages/my_stage.py

from typing import Dict, Any
from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

class MyStageHandler(BaseStageHandler):
    """Handler for the MY_STAGE stage."""

    stage_name = "MY_STAGE"
    valid_from_stages = ["PREVIOUS_STAGE"]  # Which stages can transition to this

    def get_prompt(self, context: StageContext) -> str:
        """Generate the prompt for this stage."""
        return f"""
=== MY_STAGE ===
Mission: {context.problem_statement}
Iteration: {context.iteration}

Your task:
1. Do something
2. Do something else

Respond with JSON:
{{
    "status": "complete" | "in_progress",
    "result": "your result",
    "message_to_human": "Status message"
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """Process the response and determine next stage."""
        status = response.get("status", "")

        if status == "complete":
            # Emit events for integrations
            events = [
                Event(
                    type=StageEvent.STAGE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={"result": response.get("result")}
                )
            ]

            return StageResult(
                success=True,
                next_stage="NEXT_STAGE",
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", "Complete")
            )
        else:
            # Stay in current stage
            return StageResult(
                success=True,
                next_stage=self.stage_name,
                status=status,
                output_data=response,
                message=response.get("message_to_human", "In progress")
            )

    def get_restrictions(self) -> StageRestrictions:
        """Define what operations are allowed in this stage."""
        return StageRestrictions(
            allowed_tools=["Read", "Glob", "Grep", "Write", "Edit"],
            blocked_tools=["NotebookEdit"],
            allowed_write_paths=["*/my_stage/*"],
            forbidden_write_paths=[],
            allow_bash=True,
            read_only=False
        )
```

## Step 2: Register the Handler

Update `af_engine/stages/__init__.py`:

```python
from .my_stage import MyStageHandler

__all__ = [
    # ... existing handlers ...
    "MyStageHandler",
]
```

## Step 3: Update Stage Registry

Add your stage to `af_engine/config/stage_definitions.yaml`:

```yaml
stages:
  # ... existing stages ...

  MY_STAGE:
    handler: "stages.my_stage.MyStageHandler"
    description: "Description of what this stage does"
    valid_from:
      - PREVIOUS_STAGE
    valid_to:
      - NEXT_STAGE
    restrictions:
      read_only: false
      allow_bash: true
      blocked_tools:
        - NotebookEdit
```

## Step 4: Define Stage Transitions

Ensure your stage fits into the workflow:

```
PLANNING -> BUILDING -> TESTING -> MY_STAGE -> ANALYZING -> CYCLE_END -> COMPLETE
```

Update adjacent stages to recognize your new stage:
- In the previous stage handler, return `"next_stage": "MY_STAGE"`
- In your handler, return `"next_stage": "NEXT_STAGE"`

## Key Concepts

### StageContext

The context object provides access to mission state:

```python
context.mission_id       # Unique mission identifier
context.problem_statement # The mission description
context.iteration        # Current iteration number
context.cycle_number     # Current cycle number
context.cycle_budget     # Total cycles allocated
context.workspace_dir    # Path to workspace
context.artifacts_dir    # Path to artifacts
context.history          # List of history entries
context.cycle_history    # History by cycle
context.preferences      # User preferences
context.success_criteria # List of success criteria
```

### StageResult

Return a StageResult to control flow:

```python
StageResult(
    success=True,            # Whether the stage completed successfully
    next_stage="NEXT_STAGE", # Which stage to transition to
    status="complete",       # Status string for logging
    output_data={...},       # Data to persist
    events_to_emit=[...],    # Events for integrations
    message="Done"           # Human-readable message
)
```

### StageRestrictions

Control what Claude can do in this stage:

```python
StageRestrictions(
    allowed_tools=["Read", "Write"],  # Only these tools allowed
    blocked_tools=["Bash"],           # These tools blocked
    allowed_write_paths=["*/docs/*"], # Only these paths writable
    forbidden_write_paths=["*.py"],   # These patterns forbidden
    allow_bash=False,                 # Disable bash entirely
    read_only=True                    # No writes allowed
)
```

### Events

Emit events to notify integrations:

```python
from ..integrations.base import Event, StageEvent

Event(
    type=StageEvent.STAGE_COMPLETED,  # Event type
    stage="MY_STAGE",                 # Current stage
    mission_id=context.mission_id,    # Mission ID
    data={"key": "value"}             # Arbitrary data
)
```

Available event types:
- `StageEvent.STAGE_STARTED`
- `StageEvent.STAGE_COMPLETED`
- `StageEvent.MISSION_STARTED`
- `StageEvent.MISSION_COMPLETED`
- `StageEvent.CYCLE_COMPLETED`
- `StageEvent.ERROR`

## Testing Your Stage

Create tests in `workspace/tests/`:

```python
def test_my_stage_handler():
    from af_engine.stages.my_stage import MyStageHandler
    from af_engine.stages.base import StageContext

    handler = MyStageHandler()

    # Test prompt generation
    context = StageContext(...)
    prompt = handler.get_prompt(context)
    assert len(prompt) > 100
    assert "MY_STAGE" in prompt

    # Test response processing
    response = {"status": "complete", "result": "test"}
    result = handler.process_response(response, context)
    assert result.success
    assert result.next_stage == "NEXT_STAGE"

    # Test restrictions
    restrictions = handler.get_restrictions()
    assert not restrictions.read_only
```

## Best Practices

1. **Keep prompts focused** - Each stage should have a single clear purpose
2. **Emit events** - Integrations depend on events to function
3. **Handle edge cases** - Process unexpected responses gracefully
4. **Document restrictions** - Clearly define what's allowed/blocked
5. **Test thoroughly** - Cover prompt generation, response processing, and edge cases
