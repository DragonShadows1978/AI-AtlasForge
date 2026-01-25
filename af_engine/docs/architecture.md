# af_engine Architecture

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              af_engine Package                               │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                        StageOrchestrator                               │ │
│  │                     (orchestrator.py ~300 lines)                       │ │
│  │                                                                        │ │
│  │  • Coordinates stage transitions                                       │ │
│  │  • Dispatches events to integrations                                   │ │
│  │  • Generates stage prompts                                             │ │
│  │  • Processes Claude responses                                          │ │
│  └───────────┬─────────────┬─────────────┬─────────────┬────────────────┘ │
│              │             │             │             │                   │
│              ▼             ▼             ▼             ▼                   │
│  ┌───────────────┐ ┌───────────────┐ ┌───────────────┐ ┌───────────────┐  │
│  │ StateManager  │ │ StageRegistry │ │ Integration-  │ │ CycleManager  │  │
│  │               │ │               │ │ Manager       │ │               │  │
│  │ • Load/Save   │ │ • Stage       │ │               │ │ • Track       │  │
│  │   mission.json│ │   handlers    │ │ • Event bus   │ │   cycles      │  │
│  │ • Get/Set     │ │ • Restrictions│ │ • 15+         │ │ • Continuation│  │
│  │   fields      │ │ • Plugin      │ │   handlers    │ │   prompts     │  │
│  │ • History     │ │   discovery   │ │ • Hot-reload  │ │ • Validation  │  │
│  └───────────────┘ └───────────────┘ └───────────────┘ └───────────────┘  │
│                                                                             │
│  ┌───────────────────────────────────────────────────────────────────────┐ │
│  │                          PromptFactory                                 │ │
│  │                                                                        │ │
│  │  • Load/cache ground rules                                            │ │
│  │  • Build StageContext                                                 │ │
│  │  • Inject KB learnings (cached)                                       │ │
│  │  • Inject AfterImage memories                                         │ │
│  │  • Inject crash recovery context                                      │ │
│  └───────────────────────────────────────────────────────────────────────┘ │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Stage Handler Flow

```
                    ┌────────────────────────────────────────┐
                    │           Stage Handlers               │
                    │      (af_engine/stages/*.py)           │
                    └────────────────────────────────────────┘
                                      │
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
        ▼                             ▼                             ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│   PLANNING    │           │   BUILDING    │           │   TESTING     │
│               │    ───►   │               │    ───►   │               │
│ • Read files  │           │ • Write code  │           │ • Run tests   │
│ • Create plan │           │ • Edit files  │           │ • Validate    │
│ • Research    │           │ • All tools   │           │ • All tools   │
└───────────────┘           └───────────────┘           └───────────────┘
                                                                │
        ┌─────────────────────────────┬─────────────────────────┘
        │                             │
        ▼                             ▼
┌───────────────┐           ┌───────────────┐           ┌───────────────┐
│  ANALYZING    │           │  CYCLE_END    │           │   COMPLETE    │
│               │    ───►   │               │    ───►   │               │
│ • Review      │           │ • Report      │           │ • Archive     │
│ • Assess      │           │ • Continue?   │           │ • Read only   │
│ • Read only   │           │ • Read only   │           │ • No writes   │
└───────────────┘           └───────────────┘           └───────────────┘
```

## Event Flow

```
┌─────────────────┐      ┌──────────────────┐      ┌─────────────────────┐
│ StageOrchestrator│ ──► │ IntegrationManager│ ──► │ Integration Handlers │
└─────────────────┘      └──────────────────┘      └─────────────────────┘
        │                         │                         │
        │ update_stage("X")       │ emit(Event)            │
        │                         │                         ▼
        ▼                         │                 ┌───────────────┐
┌───────────────┐                 │                 │ analytics     │
│ StateManager  │                 │                 ├───────────────┤
│ update_stage()│                 │                 │ recovery      │
└───────────────┘                 │                 ├───────────────┤
        │                         │                 │ snapshots     │
        │                         │                 ├───────────────┤
        ▼                         │                 │ git           │
┌───────────────┐                 │                 ├───────────────┤
│  Persist to   │                 │                 │ websocket     │
│ mission.json  │                 │                 ├───────────────┤
└───────────────┘                 │                 │ ... (15+)     │
                                  │                 └───────────────┘
                                  │
                          Event Types:
                          • STAGE_STARTED
                          • STAGE_COMPLETED
                          • CYCLE_STARTED
                          • CYCLE_COMPLETED
                          • MISSION_COMPLETED
```

## KB Caching Layer

```
┌─────────────────────────────────────────────────────────────────────┐
│                          kb_cache.py                                 │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────┐     ┌─────────────────┐     ┌──────────────┐ │
│  │ query_relevant_ │     │    KBCache      │     │   Mission-   │ │
│  │ learnings()     │ ──► │   (LRU+TTL)     │ ──► │ KnowledgeBase│ │
│  └─────────────────┘     └─────────────────┘     └──────────────┘ │
│                                  │                                 │
│                                  │                                 │
│                          Cache Stats:                              │
│                          • max_size: 100                           │
│                          • ttl: 5 minutes                          │
│                          • thread-safe (RLock)                     │
│                          • speedup: 603,263x                       │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

## File Layout

```
af_engine/
├── __init__.py              # Feature flag, exports
│
├── orchestrator.py          # StageOrchestrator - core coordinator
├── state_manager.py         # Mission state persistence
├── stage_registry.py        # Stage handler discovery
├── integration_manager.py   # Event bus
├── cycle_manager.py         # Multi-cycle logic
├── prompt_factory.py        # Prompt generation
├── kb_cache.py             # KB query caching
│
├── stages/                  # Stage handlers
│   ├── __init__.py
│   ├── base.py             # StageHandler protocol
│   ├── planning.py
│   ├── building.py
│   ├── testing.py
│   ├── analyzing.py
│   ├── cycle_end.py
│   └── complete.py
│
├── integrations/           # Integration handlers
│   ├── __init__.py
│   ├── base.py             # BaseIntegrationHandler
│   ├── analytics.py
│   ├── token_watcher.py
│   ├── recovery.py
│   ├── git.py
│   ├── drift_validation.py
│   ├── plan_backup.py
│   ├── artifact_manager.py
│   ├── post_mission_hooks.py
│   ├── snapshots.py
│   ├── websocket_events.py
│   ├── decision_graph.py
│   ├── knowledge_base.py
│   ├── afterimage.py
│   ├── queue_scheduler.py
│   └── enhancer.py
│
├── config/                 # Configuration
│   ├── __init__.py
│   └── stage_definitions.yaml
│
├── tests/                  # Test suite (88 tests)
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_integration_manager.py
│   ├── test_kb_cache.py
│   ├── test_orchestrator.py
│   ├── test_cycle_manager.py
│   └── test_prompt_factory.py
│
└── docs/                   # Documentation
    ├── README.md
    ├── adding_new_stages.md
    ├── adding_new_integrations.md
    ├── hot_reload_api.md
    ├── rollout_strategy.md
    ├── migration_guide.md
    └── architecture.md
```

## Data Flow Summary

1. **Mission Start**: `StateManager` loads `state/mission.json`
2. **Stage Prompt**: `PromptFactory` assembles prompt with KB context
3. **Claude Response**: `StageOrchestrator.process_response()` routes to handler
4. **Stage Handler**: Determines next stage, emits events
5. **Event Dispatch**: `IntegrationManager` notifies all subscribers
6. **State Update**: `StateManager` persists new stage
7. **Cycle Check**: `CycleManager` determines if more cycles needed

## Key Design Decisions

1. **Protocol-based handlers**: Stages and integrations implement protocols
2. **Event-driven integrations**: Loose coupling via event bus
3. **Lazy loading**: KB instance loaded on first use
4. **TTL caching**: KB queries cached for 5 minutes
5. **Feature flag**: Safe rollout with `USE_MODULAR_ENGINE`
6. **Backward compatible**: Same public API as legacy
