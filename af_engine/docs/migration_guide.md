# Migration Guide: Legacy to Modular af_engine

This guide covers migrating from `af_engine_legacy.py` to the modular `af_engine` package.

## Overview

The modular engine maintains backward compatibility while providing:
- 90% code reduction (3098 lines -> ~300 line orchestrator)
- Event-driven integrations
- Hot-reload capability
- Better testability (88 tests)
- ~750ms faster KB queries (with caching)

## Step 1: Enable Modular Engine

The modular engine is enabled by default (`USE_MODULAR_ENGINE=true`).

To verify:
```bash
python3 -c "from af_engine import USE_MODULAR_ENGINE; print(USE_MODULAR_ENGINE)"
# Should print: True
```

To disable (fall back to legacy):
```bash
export USE_MODULAR_ENGINE=false
```

## Step 2: API Compatibility Check

### RDMissionController

The main interface remains identical:

```python
# Both legacy and modular support this API
from af_engine import RDMissionController

controller = RDMissionController()
controller.update_stage("BUILDING")
prompt = controller.build_rd_prompt()
next_stage = controller.process_response(response)
```

### New Modular-Only APIs

When `USE_MODULAR_ENGINE=true`, additional APIs are available:

```python
from af_engine import (
    RDMissionController,
    StateManager,
    StageRegistry,
    IntegrationManager,
    CycleManager,
    PromptFactory,
)

# Access components directly
orchestrator = RDMissionController()
orchestrator.integrations.get_stats()
orchestrator.cycles.get_cycle_context()
orchestrator.prompts.get_ground_rules()
```

## Step 3: Integration Migration

### Legacy Pattern
```python
# Legacy: Hardcoded in update_stage()
if self.mission.get("current_stage") == "BUILDING":
    self._do_building_stuff()
    self._log_analytics()
    self._check_recovery()
```

### Modular Pattern
```python
# Modular: Event-driven
from af_engine.integrations.base import BaseIntegrationHandler, Event, StageEvent

class MyIntegration(BaseIntegrationHandler):
    name = "my_integration"
    subscriptions = [StageEvent.STAGE_STARTED]

    def handle_event(self, event: Event) -> None:
        if event.stage == "BUILDING":
            self._do_building_stuff()
```

## Step 4: Configuration Migration

### Legacy Pattern
```python
# Legacy: Hardcoded stage restrictions
STAGE_RESTRICTIONS = {
    "PLANNING": {
        "blocked_tools": ["NotebookEdit"],
        "allowed_write_paths": ["artifacts/", "research/"],
    },
    # ...
}
```

### Modular Pattern
```yaml
# af_engine/config/stage_definitions.yaml (optional)
stages:
  PLANNING:
    blocked_tools:
      - NotebookEdit
    allowed_write_paths:
      - artifacts/
      - research/
```

Or in code:
```python
restrictions = orchestrator.get_stage_restrictions("PLANNING")
```

## Step 5: Testing Your Migration

### Run the Test Suite
```bash
cd /home/vader/AI-AtlasForge
python3 -m pytest af_engine/tests/ -v
```

### Verify Stage Transitions
```python
from af_engine import RDMissionController

orch = RDMissionController()
print(f"Current: {orch.current_stage}")

# Test each stage
for stage in ["PLANNING", "BUILDING", "TESTING", "ANALYZING"]:
    orch.update_stage(stage)
    print(f"Transitioned to: {orch.current_stage}")
```

### Verify Integration Loading
```python
from af_engine.integration_manager import IntegrationManager

mgr = IntegrationManager()
mgr.load_default_integrations()

handlers = mgr.get_all_handlers()
available = mgr.get_available_handlers()

print(f"Registered: {len(handlers)}")
print(f"Available: {len(available)}")
print(f"Available integrations: {list(available.keys())}")
```

## Rollback Procedure

If issues occur during migration:

1. Set environment variable:
   ```bash
   export USE_MODULAR_ENGINE=false
   ```

2. Restart the dashboard/engine

3. Verify legacy mode:
   ```python
   from af_engine import USE_MODULAR_ENGINE
   assert not USE_MODULAR_ENGINE
   ```

## Known Differences

### Event Timing

Legacy: Events happen inline during `update_stage()`
Modular: Events dispatched through IntegrationManager (slightly different ordering)

### Error Handling

Legacy: Errors in one integration can affect others
Modular: Each integration is isolated; failures logged but don't block

### Hot Reload

Legacy: Requires restart for code changes
Modular: Supports hot-reload of integrations:
```python
mgr.reload_integration("analytics")
mgr.reload_all_integrations()
```

## Performance Comparison

| Operation | Legacy | Modular |
|-----------|--------|---------|
| Module load | ~200ms | ~150ms |
| Stage transition | ~100ms | ~50ms |
| KB query (cold) | ~750ms | ~750ms |
| KB query (warm) | ~750ms | <0.01ms |
| Integration dispatch | N/A | ~10ms |

## Troubleshooting

### "ModuleNotFoundError: No module named 'af_engine.xxx'"

Ensure PYTHONPATH includes the AtlasForge root:
```bash
export PYTHONPATH=/home/vader/AI-AtlasForge:$PYTHONPATH
```

### "Integration 'xxx' not available"

Some integrations require optional dependencies:
- `afterimage`: Requires `hybrid_search` module
- `knowledge_base`: Requires `mission_knowledge_base` module

Check availability:
```python
mgr = IntegrationManager()
mgr.load_default_integrations()
info = mgr.get_integration_info("afterimage")
print(f"Available: {info['available']}")
print(f"Reason: {info.get('unavailable_reason', 'N/A')}")
```

### "Fallback to legacy triggered"

The modular engine auto-falls-back on import errors. Check logs for:
```
WARNING - Falling back to legacy engine
ERROR - Failed to import modular engine components: <error>
```

## Support

For issues with the modular engine:
1. Check existing tests in `af_engine/tests/`
2. Review docs in `af_engine/docs/`
3. Temporarily fall back with `USE_MODULAR_ENGINE=false`
