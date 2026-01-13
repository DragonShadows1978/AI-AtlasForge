# AI-AtlasForge Architecture

This document describes the system architecture of AI-AtlasForge.

## System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         User Interface                           │
│  ┌─────────────────────┐    ┌─────────────────────────────────┐ │
│  │   Dashboard (Web)    │    │      CLI / API                  │ │
│  │   localhost:5050     │    │   claude_autonomous.py          │ │
│  └──────────┬──────────┘    └──────────────┬──────────────────┘ │
└─────────────┼───────────────────────────────┼───────────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Core Engine Layer                         │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                      af_engine.py                           ││
│  │  ┌───────────┐ ┌───────────┐ ┌───────────┐ ┌──────────────┐ ││
│  │  │ PLANNING  │→│ BUILDING  │→│ TESTING   │→│  ANALYZING   │ ││
│  │  └───────────┘ └───────────┘ └───────────┘ └──────────────┘ ││
│  │        │                                            │        ││
│  │        └────────────────────────────────────────────┘        ││
│  │                    (Cycle Iteration)                         ││
│  └─────────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Data & Integration Layer                    │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Knowledge Base │  │   Analytics  │  │  Decision Graph       │ │
│  │   (SQLite)     │  │   (SQLite)   │  │    (SQLite)           │ │
│  └───────────────┘  └──────────────┘  └───────────────────────┘ │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │ Mission State  │  │  Checkpoints │  │  Exploration Hooks    │ │
│  │   (JSON)       │  │   (JSON)     │  │  (Auto-tracking)      │ │
│  └───────────────┘  └──────────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
              │
              ▼
┌─────────────────────────────────────────────────────────────────┐
│                       External Services                          │
│  ┌───────────────┐  ┌──────────────┐  ┌───────────────────────┐ │
│  │  Claude API   │  │   Ollama     │  │   File System         │ │
│  │  (Anthropic)  │  │  (Optional)  │  │   (workspace/)        │ │
│  └───────────────┘  └──────────────┘  └───────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Core Components

### claude_autonomous.py

**Purpose**: Main execution loop and Claude integration.

**Responsibilities**:
- Spawn and manage Claude instances
- Handle graceful shutdown (SIGTERM, SIGINT)
- Manage conversation context
- Route responses to R&D engine

**Key Functions**:
- `main()` - Entry point
- `run_rd_loop()` - Main R&D execution loop
- `send_to_claude()` - API communication

### af_engine.py

**Purpose**: State machine for mission execution.

**Responsibilities**:
- Manage stage transitions
- Generate stage-specific prompts
- Enforce tool restrictions per stage
- Handle cycle iteration

**Stage Flow**:
```
PLANNING → BUILDING → TESTING → ANALYZING → CYCLE_END → COMPLETE
    ↑                                   │           │
    └───────────────────────────────────┘           │
              (if tests fail)                       │
    ↑                                               │
    └───────────────────────────────────────────────┘
              (if cycles remain)
```

**Key Classes**:
- `RDEngine` - Main state machine
- `StagePromptGenerator` - Creates prompts for each stage
- `ToolRestrictionChecker` - Enforces stage constraints

### dashboard_v2.py

**Purpose**: Web-based monitoring interface.

**Responsibilities**:
- Serve dashboard UI
- Real-time WebSocket updates
- REST API for mission control
- Aggregate data from modules

**Key Routes**:
- `/` - Main dashboard
- `/api/status` - Mission status
- `/api/mission/*` - Mission CRUD
- `/api/knowledge-base/*` - KB access
- `/api/analytics/*` - Usage stats

### atlasforge_config.py

**Purpose**: Centralized configuration.

**Exports**:
- `BASE_DIR` - Installation root
- `STATE_DIR`, `WORKSPACE_DIR`, etc. - Key directories
- `DASHBOARD_PORT` - Server port
- `ensure_directories()` - Setup function

## Data Layer

### Mission State

**File**: `state/mission.json`

```json
{
  "mission_id": "mission_abc123",
  "problem_statement": "...",
  "current_stage": "BUILDING",
  "iteration": 0,
  "cycle_budget": 3,
  "current_cycle": 1,
  "history": [...],
  "artifacts": {...}
}
```

### Knowledge Base

**File**: `atlasforge_data/knowledge_base/knowledge_base.db`

**Schema**:
```sql
CREATE TABLE learnings (
    id INTEGER PRIMARY KEY,
    mission_id TEXT,
    category TEXT,      -- technique, insight, gotcha
    content TEXT,
    embedding BLOB,     -- TF-IDF vector for similarity search
    created_at TIMESTAMP
);
```

### Decision Graph

**File**: `atlasforge_data/decision_graphs/<mission_id>.db`

Records all tool invocations for post-mission analysis.

**Schema**:
```sql
CREATE TABLE invocations (
    id INTEGER PRIMARY KEY,
    tool TEXT,
    args TEXT,
    result TEXT,
    timestamp TIMESTAMP,
    stage TEXT
);
```

## Enhancement Modules

### atlasforge_enhancements/

| Module | Purpose |
|--------|---------|
| `atlasforge_enhancer.py` | Coordinator for all enhancements |
| `exploration_graph.py` | Tracks file exploration patterns |
| `fingerprint_extractor.py` | Extracts cognitive fingerprints |
| `mission_continuity_tracker.py` | Cross-cycle context |

### exploration_hooks.py

Automatically tracks:
- File reads (`log_read_tool()`)
- Searches (`log_grep_tool()`, `log_glob_tool()`)
- Tool invocations
- Cognitive drift detection

### adversarial_testing/

| Module | Purpose |
|--------|---------|
| `AdversarialRunner` | Orchestrates testing |
| `red_team.py` | Fresh-context code review |
| `property_testing.py` | Edge case generation |
| `mutation_testing.py` | Test quality validation |

## Data Flow

### Mission Execution

```
1. User creates mission (dashboard/JSON)
         ↓
2. claude_autonomous.py loads mission
         ↓
3. af_engine.py generates PLANNING prompt
         ↓
4. Claude creates implementation plan
         ↓
5. af_engine.py transitions to BUILDING
         ↓
6. Claude implements solution
         ↓
7. af_engine.py transitions to TESTING
         ↓
8. Claude + adversarial agents test
         ↓
9. af_engine.py transitions to ANALYZING
         ↓
10. Claude evaluates results
         ↓
11. If success → CYCLE_END → COMPLETE
    If failure → Back to PLANNING (next cycle)
```

### Dashboard Updates

```
exploration_hooks.py ──┐
                       │
mission_analytics.py ──┼──→ dashboard_v2.py ──→ WebSocket ──→ Browser
                       │         ↑
decision_graph.py ─────┘         │
                                 │
                    Flask REST API
```

## Extension Points

### Adding a New Stage

1. Add stage enum in `af_engine.py`
2. Create prompt generator in `_get_stage_prompt()`
3. Define tool restrictions in `init_guard.py`
4. Update transition logic in `advance_stage()`

### Adding Dashboard Widgets

1. Create Flask blueprint in `dashboard_modules/`
2. Register routes in `dashboard_v2.py`
3. Add frontend component in `dashboard_static/`
4. Update `main_bundled.html`

### Adding Knowledge Extractors

1. Implement extractor in `mission_knowledge_base.py`
2. Register in `extract_learnings()`
3. Add category handling in KB search

## Security Considerations

### API Key Protection

- Keys stored in `.env` or `config.yaml` (gitignored)
- Never logged or displayed
- Environment variable precedence

### Tool Restrictions

- Stage-specific tool blocking via `init_guard.py`
- Write paths restricted in analysis stages
- No code execution in COMPLETE stage

### File Protection

- Core files auto-backed up before modification
- Backups in `backups/auto_backups/`
- Protected file list in `backup_utils.py`

## Performance Considerations

### Token Management

- Mission summaries minimize context size
- Cycle reports archive verbose details
- KB queries use TF-IDF for efficiency

### Database Design

- SQLite for simplicity and portability
- Indexes on frequently queried columns
- Separate DBs per mission for isolation

### WebSocket Efficiency

- Batched updates for high-frequency events
- Client-side throttling for rendering
- Selective widget updates

## Directory Reference

```
AI-AtlasForge/
├── claude_autonomous.py    # Main entry point
├── af_engine.py            # Stage state machine
├── dashboard_v2.py         # Web dashboard
├── atlasforge_config.py    # Centralized config
├── atlasforge_tray.py      # System tray (optional)
│
├── dashboard_modules/      # Flask blueprints
│   ├── core.py            # Mission control
│   ├── knowledge_base.py  # KB widget
│   └── analytics.py       # Analytics widget
│
├── dashboard_static/       # Frontend assets
│   ├── js/                # JavaScript
│   ├── css/               # Stylesheets
│   └── build.js           # esbuild bundler
│
├── atlasforge_enhancements/# Enhancement modules
│   ├── atlasforge_enhancer.py
│   ├── exploration_graph.py
│   └── ...
│
├── adversarial_testing/    # Testing framework
│   ├── __init__.py
│   ├── red_team.py
│   └── ...
│
├── state/                  # Runtime state
├── workspace/              # Active workspace
├── missions/               # Mission archives
├── atlasforge_data/        # Databases
├── logs/                   # Log files
└── backups/                # Auto-backups
```
