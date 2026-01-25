# af_engine - Modular R&D Engine Architecture

The modular af_engine provides a clean, extensible architecture for the AtlasForge R&D workflow system. It replaces the monolithic 3098-line `af_engine_legacy.py` with a composable set of specialized modules.

## Quick Start

```python
from af_engine import RDMissionController

# Create orchestrator (loads state from state/mission.json)
orchestrator = RDMissionController()

# Get current status
print(f"Stage: {orchestrator.current_stage}")
print(f"Cycle: {orchestrator.cycles.current_cycle}/{orchestrator.cycles.cycle_budget}")

# Build prompt for current stage
prompt = orchestrator.build_rd_prompt()

# Process Claude's response
next_stage = orchestrator.process_response(response)

# Transition to next stage
orchestrator.update_stage(next_stage)
```

## Architecture Overview

```
af_engine/
├── __init__.py           # Feature flag + exports
├── orchestrator.py       # Core StageOrchestrator (~300 lines)
├── state_manager.py      # Mission state persistence
├── stage_registry.py     # Plugin discovery/registration
├── integration_manager.py # Event bus for integrations
├── cycle_manager.py      # Multi-cycle iteration logic
├── prompt_factory.py     # Template-based prompts
├── kb_cache.py          # KB query caching layer
│
├── stages/              # Stage handlers (one per stage)
│   ├── base.py          # StageHandler protocol + StageContext
│   ├── planning.py      # PlanningStageHandler
│   ├── building.py      # BuildingStageHandler
│   ├── testing.py       # TestingStageHandler
│   ├── analyzing.py     # AnalyzingStageHandler
│   ├── cycle_end.py     # CycleEndStageHandler
│   └── complete.py      # CompleteStageHandler
│
├── integrations/        # Event-driven integrations
│   ├── base.py          # BaseIntegrationHandler + Event types
│   ├── analytics.py     # MissionAnalyticsHandler
│   ├── recovery.py      # CrashRecoveryHandler
│   ├── snapshots.py     # SnapshotHandler
│   └── ... (15+ integrations)
│
├── config/              # YAML configuration
│   └── __init__.py      # Stage definitions loader
│
├── tests/               # pytest test suite (88 tests)
│   ├── conftest.py      # Shared fixtures
│   ├── test_integration_manager.py
│   ├── test_kb_cache.py
│   ├── test_orchestrator.py
│   ├── test_cycle_manager.py
│   └── test_prompt_factory.py
│
└── docs/                # Documentation
    ├── adding_new_stages.md
    ├── adding_new_integrations.md
    ├── hot_reload_api.md
    └── rollout_strategy.md
```

## Feature Flag

The `USE_MODULAR_ENGINE` environment variable controls which implementation is used:

```bash
# Use modular engine (default)
export USE_MODULAR_ENGINE=true

# Fall back to legacy
export USE_MODULAR_ENGINE=false
```

The flag defaults to `true` since Cycle 3 validation confirmed 100% test pass rate.

## Core Components

### StageOrchestrator

The central coordinator that replaces the legacy `update_stage()` method:

```python
from af_engine.orchestrator import StageOrchestrator

orch = StageOrchestrator()

# Stage transitions emit events to all integrations
orch.update_stage("BUILDING")

# Build stage-specific prompts with context injection
prompt = orch.build_rd_prompt()

# Process responses through stage handlers
next_stage = orch.process_response(response)
```

### IntegrationManager

Event bus for cross-cutting concerns:

```python
from af_engine.integration_manager import IntegrationManager

mgr = IntegrationManager()
mgr.load_default_integrations()

# Emit events
mgr.emit_stage_started("BUILDING", mission_id)
mgr.emit_stage_completed("BUILDING", mission_id)

# Hot-reload integrations
mgr.reload_integration("analytics")
mgr.reload_all_integrations()
```

### CycleManager

Multi-cycle iteration logic:

```python
from af_engine.cycle_manager import CycleManager

cycles = CycleManager(state_manager)

if cycles.should_continue():
    prompt = cycles.generate_continuation_prompt(
        cycle_summary="Completed phase 1",
        findings=["Found X", "Discovered Y"],
        next_objectives=["Implement Z"]
    )
    cycles.advance_cycle(prompt)
```

### KB Query Caching

The `kb_cache` module provides a 603,263x speedup for repeated KB queries:

```python
from af_engine.kb_cache import query_relevant_learnings, get_cache_stats

# First query: ~750ms (cold)
learnings = query_relevant_learnings("optimization patterns")

# Second query: <0.01ms (warm)
learnings = query_relevant_learnings("optimization patterns")

# Check cache stats
stats = get_cache_stats()
print(f"Hit rate: {stats['hit_rate']:.1%}")
```

## Adding New Stages

See `docs/adding_new_stages.md`. Quick example:

```python
from af_engine.stages.base import StageHandler, StageContext, StageResult

class CustomStageHandler(StageHandler):
    name = "CUSTOM"

    def get_prompt(self, context: StageContext) -> str:
        return "Your stage prompt here"

    def process_response(self, response: dict, context: StageContext) -> StageResult:
        return StageResult(
            status=response.get("status", "unknown"),
            next_stage="NEXT_STAGE",
            success=True,
            events_to_emit=[],
        )
```

## Adding New Integrations

See `docs/adding_new_integrations.md`. Quick example:

```python
from af_engine.integrations.base import BaseIntegrationHandler, Event, StageEvent

class CustomHandler(BaseIntegrationHandler):
    name = "custom"
    subscriptions = [StageEvent.STAGE_STARTED, StageEvent.STAGE_COMPLETED]

    def handle_event(self, event: Event) -> None:
        if event.type == StageEvent.STAGE_STARTED:
            print(f"Stage {event.stage} started for {event.mission_id}")
```

## Running Tests

```bash
# Run all tests
cd /home/vader/AI-AtlasForge
python3 -m pytest af_engine/tests/ -v

# Run with coverage
python3 -m pytest af_engine/tests/ --cov=af_engine --cov-report=term-missing

# Run specific test file
python3 -m pytest af_engine/tests/test_integration_manager.py -v
```

## Performance Benchmarks

| Metric | Target | Actual |
|--------|--------|--------|
| Prompt generation | <200ms | <1ms (cached KB) |
| Stage transition | <100ms | ~50ms |
| Integration dispatch | <50ms | ~10ms |
| KB query (cold) | <2000ms | ~750ms |
| KB query (warm) | <10ms | <0.01ms |

## Migration from Legacy

The modular engine is backward compatible. To migrate:

1. Set `USE_MODULAR_ENGINE=true` (default)
2. Test your workflows
3. If issues occur, set `USE_MODULAR_ENGINE=false` to fall back

See `docs/rollout_strategy.md` for detailed migration guidance.

## Available Integrations

| Integration | Purpose |
|-------------|---------|
| analytics | Mission analytics and timing |
| token_watcher | Token usage tracking |
| recovery | Crash recovery |
| git | Git operations integration |
| drift_validation | Mission drift detection |
| plan_backup | Plan file backups |
| artifact_manager | Artifact lifecycle |
| post_mission_hooks | Post-mission processing |
| snapshots | State snapshots |
| websocket_events | Real-time dashboard updates |
| decision_graph | Exploration tracking |
| knowledge_base | KB integration |
| afterimage | Episodic code memory |
| queue_scheduler | Mission queue |
| enhancer | AtlasForge enhancements |

## License

Part of the AtlasForge project. Internal use only.
