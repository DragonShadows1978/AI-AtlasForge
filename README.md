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
- Anthropic API key (get one at https://console.anthropic.com/)
- Linux environment (tested on Ubuntu 22.04+, Debian 12+)

> **Platform Notes:**
> - **Windows:** Use WSL2 (Windows Subsystem for Linux)
> - **macOS:** Should work but is untested. Please report issues.

### Option 1: Standard Installation

```bash
# Clone the repository
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge

# Run the installer
./install.sh

# Configure your API key
export ANTHROPIC_API_KEY='your-key-here'
# Or edit config.yaml / .env

# Verify installation
./verify.sh
```

### Option 2: One-Liner Install

```bash
curl -sSL https://raw.githubusercontent.com/DragonShadows1978/AI-AtlasForge/main/quick_install.sh | bash
```

### Option 3: Docker Installation

```bash
git clone https://github.com/DragonShadows1978/AI-AtlasForge.git
cd AI-AtlasForge
docker compose up -d
# Dashboard at http://localhost:5050
```

For detailed installation options, see [INSTALL.md](INSTALL.md) or [QUICKSTART.md](QUICKSTART.md).

### Running Your First Mission

1. **Start the Dashboard** (optional, for monitoring):
   ```bash
   make dashboard
   # Or: python3 dashboard_v2.py
   # Access at http://localhost:5050
   ```

2. **Create a Mission**:
   - Via Dashboard: Click "Create Mission" and enter your objectives
   - Via Sample: Run `make sample-mission` to load a hello-world mission
   - Via JSON: Create `state/mission.json` manually

3. **Start the Engine**:
   ```bash
   make run
   # Or: python3 atlasforge_conductor.py --mode=rd
   ```

### Development Commands

Run `make help` to see all available commands:

```bash
make install      # Full installation
make verify       # Verify installation
make dashboard    # Start dashboard
make run          # Start autonomous agent
make docker       # Start with Docker
make sample-mission  # Load sample mission
```

## What's New in v1.5.1

- **Improved Version Checker** - Smarter update detection that distinguishes between "behind" (update available), "ahead" (local customizations), and "diverged" (both). Users with custom local commits no longer get false "Update Required" warnings
- **Bug Fixes** - Fixed duplicate log entries, WebSocket SSL reconnection issues

## What's New in v1.5.0

- **Modular Engine Architecture** - The R&D engine has been refactored into a plugin-based system with StageOrchestrator, stage handlers, and event-driven integrations
- **Mission Queue System** - Queue multiple missions with auto-start capability. Missions automatically chain when one completes
- **Cycle Budget Management** - Set how many improvement cycles a mission can run before completing
- **17+ Integrations** - Event-driven handlers for analytics, git, recovery, knowledge base, and more

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
    |    AtlasForge     |         |    Dashboard    |
    | (Execution Engine)|         |   (Monitoring)  |
    +---------+---------+         +-----------------+
              |
    +---------v---------+         +-------------------+
    |  Modular Engine   |<------->|  Context Watcher  |
    | (StageOrchestrator)|        | (Token + Time)    |
    +---------+---------+         +-------------------+
              |
    +---------v-------------------+
    |     Stage Handlers          |
    |                             |
    |  PLANNING -> BUILDING ->    |
    |  TESTING -> ANALYZING ->    |
    |  CYCLE_END -> COMPLETE      |
    +-----------------------------+
              |
    +---------v-------------------+
    |   Integration Manager       |
    |   (Event-Driven Hooks)      |
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

### atlasforge.py
Main execution loop. Spawns Claude instances, manages state, handles graceful shutdown.

### af_engine/ (Modular Engine)
Plugin-based mission execution system:
- **StageOrchestrator** - Core workflow orchestrator (~300 lines)
- **Stage Handlers** - Pluggable handlers for each stage (Planning, Building, Testing, Analyzing, CycleEnd, Complete)
- **IntegrationManager** - Event-driven integration coordination
- **PromptFactory** - Template-based prompt generation

### Mission Queue
Queue multiple missions to run sequentially:
- Auto-start next mission when current completes
- Set cycle budgets per mission
- Priority ordering
- Dashboard integration for queue management

### Context Watcher
Real-time context monitoring to prevent timeout waste:
- **Token-based detection**: Monitors JSONL transcripts for context exhaustion (130K/140K thresholds)
- **Time-based detection**: Proactive handoff at 55 minutes before 1-hour timeout
- **Haiku-powered summaries**: Generates intelligent HANDOFF.md via Claude Haiku
- **Automatic recovery**: Sessions continue from HANDOFF.md on restart

See [context_watcher/README.md](context_watcher/README.md) for detailed documentation.

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

### Display Layer (Windows)
Visual environment for graphical application testing:
- Screenshot capture from virtual display
- Web-accessible display via noVNC (localhost:6080)
- Web terminal via ttyd (localhost:7681)
- Browser support for OAuth flows and web testing
- Automatic GPU detection with software fallback

See [docs/DISPLAY_LAYER.md](workspace/docs/DISPLAY_LAYER.md) for the user guide.

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
+-- atlasforge_conductor.py # Main orchestrator
+-- af_engine/              # Modular engine package
|   +-- orchestrator.py     # StageOrchestrator
|   +-- stages/             # Stage handlers
|   +-- integrations/       # Event-driven integrations
+-- af_engine_legacy.py     # Legacy engine (fallback)
+-- context_watcher/        # Context monitoring module
|   +-- context_watcher.py  # Token + time-based handoff
|   +-- tests/              # Context watcher tests
+-- dashboard_v2.py         # Web dashboard
+-- adversarial_testing/    # Testing framework
+-- atlasforge_enhancements/  # Enhancement modules
+-- workspace/              # Active workspace
|   +-- glassbox/           # Introspection tools
|   +-- artifacts/          # Plans, reports
|   +-- research/           # Notes, findings
|   +-- tests/              # Test scripts
+-- state/                  # Runtime state
|   +-- mission.json        # Current mission
|   +-- claude_state.json   # Execution state
+-- missions/               # Mission workspaces
+-- atlasforge_data/
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
| `USE_MODULAR_ENGINE` | `true` | Use new modular engine (set to `false` for legacy) |

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
- Node.js 18+ (optional, for dashboard JS modifications)
- Anthropic API key
- Linux environment (Ubuntu 22.04+, Debian 12+)

### Python Dependencies

See `requirements.txt` or `pyproject.toml` for full list.

## Documentation

- [QUICKSTART.md](QUICKSTART.md) - Get started in 5 minutes
- [INSTALL.md](INSTALL.md) - Detailed installation guide
- [USAGE.md](USAGE.md) - How to use AI-AtlasForge
- [ARCHITECTURE.md](ARCHITECTURE.md) - System architecture
- [DISPLAY_LAYER.md](workspace/docs/DISPLAY_LAYER.md) - Display Layer user guide (Windows)
- [TROUBLESHOOTING.md](workspace/docs/TROUBLESHOOTING.md) - Display Layer troubleshooting

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## Related Projects

- **[AI-AfterImage](https://github.com/DragonShadows1978/AI-AfterImage)** - Episodic memory for AI coding agents. Gives Claude Code persistent memory of code it has written across sessions. Works great with AtlasForge for cross-mission code recall.

## Acknowledgments

Built on Claude by Anthropic. Special thanks to the Claude Code team for making autonomous AI development possible.
