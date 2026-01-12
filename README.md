# RDE - Research & Development Engine

An autonomous AI research system that runs long-duration missions using Claude as the execution engine.

## What is RDE?

RDE is not a chatbot wrapper. It's an **autonomous research platform** that:

- Runs multi-day missions without human intervention
- Maintains mission continuity across context windows
- Accumulates knowledge that persists across sessions
- Self-corrects when drifting from objectives
- Adversarially tests its own outputs

Built in 3.5 weeks. Running on spare parts in a basement.

## Architecture

```
                    ┌─────────────────┐
                    │  Mission State  │
                    │   (mission.json)│
                    └────────┬────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼─────────┐         ┌────────▼────────┐
    │  Claude Autonomous │         │    Dashboard    │
    │  (Execution Engine)│         │  (Monitoring)   │
    └─────────┬─────────┘         └─────────────────┘
              │
    ┌─────────▼─────────┐
    │    R&D Engine     │
    │  (State Machine)  │
    └─────────┬─────────┘
              │
    ┌─────────▼─────────┐
    │   Stage Pipeline  │
    │                   │
    │  PLANNING         │
    │      ↓            │
    │  BUILDING         │
    │      ↓            │
    │  TESTING          │
    │      ↓            │
    │  ANALYZING        │
    │      ↓            │
    │  CYCLE_END ──┐    │
    │      ↓       │    │
    │  COMPLETE    │    │
    │      ↑       │    │
    │      └───────┘    │
    │   (iterate if     │
    │    cycles remain) │
    └───────────────────┘
```

## Core Components

### claude_autonomous.py
Main execution loop. Spawns Claude instances, manages state, handles graceful shutdown.

### rd_engine.py
State machine for mission execution. Manages stages, enforces constraints, tracks progress.

### Mission Lifecycle
1. **PLANNING** - Understand objectives, research codebase, create plan
2. **BUILDING** - Implement the solution
3. **TESTING** - Validate implementation
4. **ANALYZING** - Evaluate results, identify issues
5. **CYCLE_END** - Generate reports, prepare continuation
6. **COMPLETE** - Mission finished

### Knowledge Base
SQLite database accumulating learnings across all missions:
- Techniques discovered
- Insights gained
- Gotchas encountered
- Code templates

### Adversarial Testing
Separate Claude instances that attempt to break implementations:
- RedTeam agents with no implementation knowledge
- Mutation testing
- Property-based testing
- Blind specification validation

### Glassbox
Introspection system for post-mission analysis:
- Transcript parsing
- Agent hierarchy reconstruction
- Stage timeline visualization

### Dashboard
Web-based monitoring:
- Active mission status
- Knowledge base queries
- Mission recommendations
- Analytics

## Key Features

### Mission Continuity
Missions survive context window limits through:
- Persistent mission.json state
- Cycle-based iteration
- Continuation prompts that preserve context

### Drift Detection
Monitors mission alignment and heals when work diverges:
- Compares current focus to original objectives
- Injects healing prompts when drift exceeds threshold
- Prevents tangential rabbit holes

### Autonomous Operation
Designed for unattended execution:
- Graceful crash recovery
- Stage checkpointing
- Automatic cycle progression

## Directory Structure

```
mini-mind-v2/
├── claude_autonomous.py    # Main entry point
├── rd_engine.py            # Stage state machine
├── dashboard_v2.py         # Web dashboard
├── adversarial_testing/    # Testing framework
├── glassbox/               # Introspection tools
├── rde_enhancements/       # Optional modules
├── state/                  # Runtime state
│   ├── mission.json        # Current mission
│   └── claude_state.json   # Execution state
├── missions/               # Mission workspaces
├── workspace/              # Active workspace
├── rde_data/
│   └── knowledge_base/     # Accumulated learnings
└── logs/                   # Execution logs
```

## Usage

### Start a Mission
```bash
# Load mission from dashboard recommendations
# Or create mission.json manually

python3 claude_autonomous.py --mode=rd
```

### Monitor Progress
```bash
# Start dashboard
python3 dashboard_v2.py

# Access at http://localhost:5000
```

### Check Status
```bash
# View current mission state
cat state/mission.json | python3 -m json.tool

# Tail execution log
tail -f logs/claude_autonomous.log
```

## Requirements

- Python 3.10+
- Claude API access (via Claude Code CLI)
- Linux environment (tested on Linux Mint)

## Philosophy

**First principles only.** No frameworks hiding integration failures. Every component built from scratch for full visibility.

**Speed of machine, not human.** Designed for autonomous operation. Check in when convenient, not when required.

**Knowledge accumulates.** Every mission adds to the knowledge base. The system gets better over time.

**Trust but verify.** Adversarial testing catches what regular testing misses. The same agent that writes code doesn't validate it.

## Origin

RDE emerged from necessity. Complex multi-system projects couldn't be held in a single Claude context. The solution: build infrastructure for mission continuity, knowledge persistence, and autonomous iteration.

Built in 3.5 weeks. 4,200+ learnings accumulated. Multiple 24+ hour missions completed successfully.

## License

MIT

## Author

Dave (DragonShadows1978)
