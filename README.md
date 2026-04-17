# AI-AtlasForge

An autonomous AI research and development platform with multi-provider LLM support (Claude, Codex, Gemini). Run long-duration missions, accumulate cross-session knowledge, and build software autonomously.

## What is AI-AtlasForge?

AI-AtlasForge is not a chatbot wrapper. It's an **autonomous research engine** that:

- Runs multi-day missions without human intervention
- Maintains mission continuity across context windows
- Accumulates knowledge that persists across sessions
- Self-corrects when drifting from objectives
- Adversarially tests its own outputs
- **Multi-provider**: Supports Claude, OpenAI Codex, and Google Gemini as LLM backends

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

## Web Proxy & Thin MCP

AI-AtlasForge ships with a **local web proxy** that every AtlasForge-spawned Claude Code subagent uses in place of Claude's built-in `WebSearch` / `WebFetch`.

### What it is

Everything lives under the `WebProxy/` package at the repo root:

- **`WebProxy/service.py`** — a local HTTP service (default `http://127.0.0.1:8765`) that wraps Brave Search / DuckDuckGo search and a raw HTML fetcher. Endpoints: `/search`, `/fetch`, `/research`, `/image_search`, `/cache`, `/stats`, `/health`.
- **`WebProxy/mcp_server.py`** — the **thin MCP server**. It advertises tools named `WebSearch` and `WebFetch` (the exact names of Claude Code's built-ins) over JSON-RPC stdin/stdout. Spawn sites thread `--disallowedTools WebSearch,WebFetch` so the model's built-in calls are transparently redirected through the MCP → HTTP proxy.
- **`WebProxy/supervisor.py`** — dashboard-side auto-start helper. When you run `make dashboard`, the proxy comes up alongside it; when you Ctrl-C the dashboard, the proxy exits too (atexit hook). Opt out with `ATLASFORGE_DISABLE_PROXY_AUTOSTART=1` when you'd rather use the systemd unit.
- **`.mcp.json`** (stays at repo root) — Claude Code's **project-level** MCP config. Auto-loaded when you launch Claude Code from the repo root; no per-user configuration required.
- **`WebProxy/configs/mcp.json`** — the same MCP config, threaded explicitly via `--mcp-config` by AtlasForge when spawning subagents.

### Why

- **~22× more content per query** than Claude's filtered backend.
- Survives **domain blocks** (Reddit, niche forums, adult domains) — subagents doing adversarial verification need the raw source.
- Returns **verbatim HTML** for source verification.
- **24h fetch cache / 30m search cache** for repeatability and cost control.

### How it's integrated

| Surface | How it hooks in |
|---|---|
| **Dashboard auto-start** | `dashboard_v2.py` calls `WebProxy.supervisor.ensure_proxy_running()` on launch. If the proxy's port 8765 is already up (e.g. from the systemd unit), it no-ops; otherwise it spawns the proxy as a managed subprocess and terminates it on dashboard shutdown. |
| **Dashboard widget** | `localhost:5050` → "Web Proxy" card shows live cached-search / cached-fetch counters and provider breakdown. |
| **Dashboard API** | `GET /api/web-proxy/stats` on the dashboard returns live proxy stats. |
| **Systemd** | User-level unit `atlasforge-web-proxy.service` (installed by `./install.sh` → `scripts/setup_services.sh` from the template at `WebProxy/systemd/atlasforge-web-proxy.service`). |
| **MCP auto-load** | `.mcp.json` at the repo root — points at `WebProxy/mcp_server.py`. Claude Code auto-discovers project-level MCP configs. |
| **Subagent wiring** | `WebProxy.proxy_cli_args()` appends `--mcp-config WebProxy/configs/mcp.json --disallowedTools WebSearch,WebFetch` to every `claude -p` spawn in `atlasforge_conductor.build_llm_command()`, `investigation_engine.py`, and `adversarial_testing/blind_agent_runner.py`. |

### Thin MCP explainer

> **If you're new to MCP:** [Model Context Protocol](https://modelcontextprotocol.io) is the stdin/stdout JSON-RPC protocol Claude Code uses to load tools provided by external processes. A "thin MCP" is just a small process that advertises some tool schemas and forwards calls elsewhere.

The AtlasForge thin MCP (`WebProxy/mcp_server.py`) does two things:

1. **Advertises** MCP tools named `WebSearch`, `WebFetch`, `WebResearch`, and `ImageSearch` — the first two are the **exact same names** as Claude Code's built-ins.
2. **Forwards** each call as an HTTP request to the local proxy service and streams the response back.

The redirection works because of a tiny trick in Claude Code's tool-resolution order:

- When Claude Code sees `--disallowedTools WebSearch,WebFetch`, it refuses the **built-in** tools with those names.
- But the thin MCP has advertised tools under the **same names**, and the disallowlist doesn't apply to MCP-provided tools.
- So the model's call to `WebSearch(...)` resolves to the MCP version, which forwards to our proxy.

From the model's perspective, nothing changed: it still calls `WebSearch(...)` and `WebFetch(...)`. Under the hood, those calls now route through the local proxy.

This is what "rolling the MCP into AtlasForge itself" means: `.mcp.json` sits at the repo root and Claude Code auto-loads it the moment you launch from that directory. No per-user MCP configuration required — clone the repo, run `install.sh`, and the tools re-route themselves.

### Quick commands

```bash
make dashboard      # starts dashboard AND auto-starts the proxy
make proxy-start    # systemctl --user start atlasforge-web-proxy
make proxy-status   # show unit status
make proxy-logs     # journalctl --user -u atlasforge-web-proxy -f
make proxy-health   # curl http://127.0.0.1:8765/health
```

### Configuration

Set `BRAVE_API_KEY` to use Brave Search (recommended); otherwise the proxy falls back to DuckDuckGo HTML scraping. Set `ATLASFORGE_DISABLE_PROXY_AUTOSTART=1` if you prefer the systemd unit over dashboard-managed startup. See `.env.example` for all proxy-related environment variables.

For the full API reference, see [WebProxy/docs/LOCAL_WEB_PROXY.md](WebProxy/docs/LOCAL_WEB_PROXY.md).

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full release history. Highlights of recent releases:

- **v2.3.0** — `WebProxy/` package: local HTTP proxy + thin MCP server transparently replaces Claude Code's `WebSearch` / `WebFetch` with unfiltered verbatim-source web access. SSRF hardening, Reddit JSON auto-routing, image pipeline, systemd unit with externalized secrets, dashboard auto-start and live stats widget, validator proxy-first fetching.
- **v2.2.0** — Token budget system (`WorkBudgetManager`), dashboard file upload for mission creation, blind validator coordinator-owns-budget pattern.
- **v2.1.0** — Adversarial hardening, conductor expansion, dashboard overhaul.
- **v2.0.0** — Automated release pipeline, scripts modularization, agent streaming.
- **v1.8.4** — Handoff system overhaul, widget toggles, dashboard drag & drop, systemd auto-start.

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
+-- .af_archived/           # Archived legacy files (pre-modular engine backups)
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

## Recent Changes

### v1.9.1 (2026-02-20)
- **Dashboard Filter Persistence** - All dashboard filters, sorts, and search state now persist across page reloads via versioned localStorage schema
- **Mission Suggestion Sort/Filter Persistence** - Sort field, sort direction, tag filter, and health filter all persist (schema v2 with migration from legacy flat-map)
- **Analytics Period Persistence** - Selected analytics time period persists across sessions
- **Glassbox UI Persistence** - Search query, date range, and selected mission persist in Glassbox viewer
- **Global Preference Registry** - Centralized `ALL_PREFERENCE_KEYS` list and `clearAllPreferences()` for one-click reset
- **Stage Gate Lock File Fix** - Hook now bypasses all enforcement when no active Conductor process is detected via lock file; fixes normal Claude Code terminal usage being blocked post-mission
- **Stage Normalization** - Stage names normalized to uppercase when read from lock file; prevents silent bypass on lowercase stage values

### v1.9.0 (2026-02-20)
- **Modular Engine Only** - Retired legacy monolithic `af_engine.py` (3,688 lines); modular `af_engine/` package is now the sole engine implementation
- **Archival Module** - Migrated transcript archival functions to `af_engine/core/archival.py`; removed `importlib.util` dynamic loading hack
- **Engine Init Simplified** - `af_engine/__init__.py` reduced from ~150 lines to ~50; `USE_MODULAR_ENGINE` feature flag removed entirely
- **Dashboard WebSocket Push** - Live stage updates pushed to connected clients when af_engine stage changes; no polling required
- **Analytics Integration** - Dashboard analytics endpoints enriched with engine-native metrics (success rate, execution time, task counts)
- **Stage Gate Enforcement** - Two-layer stage enforcement: CLI `--disallowedTools` per stage + hook-level path restrictions

### v1.8.7 (2026-02-19)
- **Widget Settings Popup** - Mobile panel reordering via widget settings buttons
- **Collapsed Card Improvements** - Stage indicator and health summary remain visible when widgets are collapsed
- **Dashboard CSS** - Refined collapsed card styling and status card layout

### v1.8.6 (2026-02-19)
- **Widget Control Mechanism** - Overhauled widget visibility toggle system; widgets can be hidden/shown independently of backend services
- **Token Sanity Check** - New integration that validates token counts before handoff to prevent corrupt context windows
- **Transcript Archival** - Improved automatic transcript archival integration
- **Orchestrator Updates** - Enhanced stage orchestration reliability
- **Dashboard Queue Scheduler** - Improved mission queue scheduling and priority handling
- **Dashboard Drag-Drop** - Refined drag-and-drop widget reordering with better touch support

### v1.8.5 (2026-02-18)
- **CLAUDECODE env fix** - Conductor now strips `CLAUDECODE` env var before spawning Claude subprocesses, preventing "nested session" crash when launched from an active Claude Code session
- **Multiple mission completions** - AtlasLab fork mission, StoryForge missions, and several R&D cycles completed autonomously
- **Widget visibility toggles** - Dashboard widgets can now be hidden without disabling backend
- **Handoff system overhaul** - Major rework of session handoff and continuity system

### v1.8.4 (2026-02-15)
- Drag-and-drop widget reordering in dashboard
- Handoff system overhaul with improved continuity
- Widget visibility toggles

### v1.7.0 (2026-02-06)
- **OpenAI Codex Support** - Multi-provider LLM backend: run missions and investigations with Claude or Codex. Provider-aware ground rules, prompts, and transcript handling
- **Ground Rules Loader** - Provider-aware ground rules system with overlay support for Claude/Codex/investigation modes
- **Enhanced Context Watcher** - Major overhaul with improved token tracking, time-based handoff, and Haiku-powered summaries
- **Experiment Framework** - Expanded scientific experiment orchestration with multi-hypothesis testing
- **Investigation Engine** - Enhanced multi-subagent investigation system with provider selection
- **Dashboard Improvements** - New widgets system, improved chat interface, better WebSocket handling
- **PromptFactory Enhancements** - Provider-aware caching, AfterImage integration with fallback paths
- **Conductor Hardening** - Improved session management, singleton protocol, crash recovery
- **Transcript Archival** - New integration for automatic transcript archival
- **Research Agent** - Improved web researcher and knowledge synthesizer
- 110 files changed, 3500+ lines added across the platform

### v1.6.9 (2026-02-02)
- Fixed GlassBox visualization issues

### v1.6.8 (2026-02-01)
- Fixed zombie timer bug - stale session cleanup now stops timer threads
- Fixed continuation prompt bug - cycle progression now updates problem_statement
- Added conductor singleton with takeover protocol (prevents multiple instances)

### v1.6.7 (2026-02-01)
- Fixed JSON response parsing bug in conductor (handles markdown code blocks)
- ContextWatcher stability improvements

### v1.6.5 (2026-01-31)
- Build checkpoint improvements
- Mission state persistence fixes

## License

MIT License - see [LICENSE](LICENSE) for details.

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## Related Projects

- **[AI-AfterImage](https://github.com/DragonShadows1978/AI-AfterImage)** - Episodic memory for AI coding agents. Gives Claude Code persistent memory of code it has written across sessions. Works great with AtlasForge for cross-mission code recall.

## Acknowledgments

Built on Claude by Anthropic. Special thanks to the Claude Code team for making autonomous AI development possible.
