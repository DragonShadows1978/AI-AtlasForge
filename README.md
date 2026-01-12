# AI-AtlasForge

An autonomous AI research and development platform powered by Claude. Run long-duration missions, accumulate cross-session knowledge, and build software autonomously.

## What is AI-AtlasForge?

AI-AtlasForge is not a chatbot wrapper. It's an **autonomous research engine** that:

- Runs multi-day missions without human intervention
- Maintains mission continuity across context windows
- Accumulates knowledge that persists across sessions
- Self-corrects when drifting from objectives
- Adversarially tests its own outputs

## Quick Start

### Prerequisites

- Python 3.10+
- Claude API access (via Claude Code CLI - `claude` command)
- Linux environment (tested on Ubuntu/Linux Mint)

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/AI-AtlasForge.git
cd AI-AtlasForge

# Install Python dependencies
pip install -r requirements.txt

# Install Node dependencies (for dashboard)
npm install

# Create required directories
mkdir -p state logs workspace/{artifacts,research,tests} missions rde_data/knowledge_base
```

### Running Your First Mission

1. **Start the Dashboard** (optional, for monitoring):
   ```bash
   python3 dashboard_v2.py
   # Access at http://localhost:5050
   ```

2. **Create a Mission**:
   - Via Dashboard: Click "Create Mission" and enter your objectives
   - Via JSON: Create `state/mission.json` manually

3. **Start the Engine**:
   ```bash
   python3 claude_autonomous.py --mode=rd
   ```

## Architecture

```
                    +-------------------+
                    |   Mission State   |
                    |  (mission.json)   |
                    +--------+----------+
                             |
              +--------------+--------------+
              |                             |
    +---------v---------+         +--------v--------+
    | Claude Autonomous |         |    Dashboard    |
    | (Execution Engine)|         |   (Monitoring)  |
    +---------+---------+         +-----------------+
              |
    +---------v---------+
    |    R&D Engine     |
    |   (State Machine) |
    +---------+---------+
              |
    +---------v-------------------+
    |     Stage Pipeline          |
    |                             |
    |  PLANNING -> BUILDING ->    |
    |  TESTING -> ANALYZING ->    |
    |  CYCLE_END -> COMPLETE      |
    +-----------------------------+
```

## Mission Lifecycle

1. **PLANNING** - Understand objectives, research codebase, create implementation plan
2. **BUILDING** - Implement the solution
3. **TESTING** - Validate implementation
4. **ANALYZING** - Evaluate results, identify issues
5. **CYCLE_END** - Generate reports, prepare continuation
6. **COMPLETE** - Mission finished

Missions can iterate through multiple cycles until success criteria are met.

## Core Components

### claude_autonomous.py
Main execution loop. Spawns Claude instances, manages state, handles graceful shutdown.

### rd_engine.py
State machine for mission execution. Manages stages, enforces constraints, tracks progress.

### dashboard_v2.py
Web-based monitoring interface showing mission status, knowledge base, and analytics.

### Knowledge Base
SQLite database accumulating learnings across all missions:
- Techniques discovered
- Insights gained
- Gotchas encountered
- Reusable code patterns

### Adversarial Testing
Separate Claude instances that test implementations:
- RedTeam agents with no implementation knowledge
- Mutation testing
- Property-based testing

### GlassBox
Post-mission introspection system:
- Transcript parsing
- Agent hierarchy reconstruction
- Stage timeline visualization

## Key Features

### Mission Continuity
Missions survive context window limits through:
- Persistent mission.json state
- Cycle-based iteration
- Continuation prompts that preserve context

### Knowledge Accumulation
Every mission adds to the knowledge base. The system improves over time as it learns patterns, gotchas, and techniques.

### Autonomous Operation
Designed for unattended execution:
- Graceful crash recovery
- Stage checkpointing
- Automatic cycle progression

## Directory Structure

```
AI-AtlasForge/
+-- claude_autonomous.py    # Main entry point
+-- rd_engine.py            # Stage state machine
+-- dashboard_v2.py         # Web dashboard
+-- adversarial_testing/    # Testing framework
+-- rde_enhancements/       # Enhancement modules
+-- workspace/              # Active workspace
|   +-- glassbox/           # Introspection tools
|   +-- artifacts/          # Plans, reports
|   +-- research/           # Notes, findings
|   +-- tests/              # Test scripts
+-- state/                  # Runtime state
|   +-- mission.json        # Current mission
|   +-- claude_state.json   # Execution state
+-- missions/               # Mission workspaces
+-- rde_data/
|   +-- knowledge_base/     # Accumulated learnings
+-- logs/                   # Execution logs
```

## Configuration

AI-AtlasForge uses environment variables for configuration:

| Variable | Default | Description |
|----------|---------|-------------|
| `ATLASFORGE_PORT` | `5050` | Dashboard port |
| `ATLASFORGE_ROOT` | (script directory) | Base directory |
| `ATLASFORGE_DEBUG` | `false` | Enable debug logging |

## Dashboard Features

The web dashboard provides real-time monitoring:

- **Mission Status** - Current stage, progress, timing
- **Activity Feed** - Live log of agent actions
- **Knowledge Base** - Search and browse learnings
- **Analytics** - Token usage, cost tracking
- **Mission Queue** - Queue and schedule missions
- **GlassBox** - Post-mission analysis

## Philosophy

**First principles only.** No frameworks hiding integration failures. Every component built from scratch for full visibility.

**Speed of machine, not human.** Designed for autonomous operation. Check in when convenient, not when required.

**Knowledge accumulates.** Every mission adds to the knowledge base. The system gets better over time.

**Trust but verify.** Adversarial testing catches what regular testing misses. The same agent that writes code doesn't validate it.

## Requirements

- Python 3.10+
- Node.js 18+ (for dashboard build)
- Claude API access (via Claude Code CLI)
- Linux environment

### Python Dependencies

```
flask
flask-socketio
simple-websocket
anthropic
```

See `requirements.txt` for full list.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## Acknowledgments

Built on Claude by Anthropic. Special thanks to the Claude Code team for making autonomous AI development possible.
