# Changelog

## [1.12.0] - 2026-03-01

### Added

- feat: add vision module and claude_hooks as integrated repo components
- docs: update v1.11.0 changelog with full feature list

### Code Changes

- **Added** `claude_hooks/backup-core-files.py` — `get_backups_for_file`, `cleanup_old_backups`, `create_backup`, `is_core_file`, `main`
- **Added** `claude_hooks/bash_delete_guard.py` — `_is_whitelisted`, `_is_protected`, `_extract_targets`, `_command_hash`, `_was_already_shown`
- **Added** `claude_hooks/bash_write_guard.py` — `_detect_file_write`, `main`
- **Added** `claude_hooks/pre_tool_use.py` — `_structured_log`, `is_conductor_running`, `get_stage_from_lock`, `is_path_allowed`, `check_write_edit`
- **Added** `claude_hooks/stage_gate_hook.py` — `_debug_log`, `is_conductor_running`, `get_current_stage`, `is_path_allowed`, `check_write_edit`
- **Added** `vision/__init__.py`
- **Added** `vision/burst_capture.py` — `FrameInfo`, `BurstResult`, `to_dict`, `capture_burst`, `see_burst`
- **Added** `vision/burst_review.py` — `FrameAnnotation`, `to_dict`, `from_dict`, `FrameDiff`, `BurstMetadata`
- **Added** `vision/claude_vision.py` — `see`, `see_compact`, `describe_screen`, `save_screenshot`, `main`
- **Added** `vision/desktop_vision.py` — `capture_display`, `capture_to_base64`, `capture_and_save`, `main`
- **Added** `vision/screen_capture.py` — `ScreenCortex`, `__init__`, `capture`, `capture_to_png_bytes`, `capture_to_base64`
- **Added** `vision/x11_bindings.py` — `_load_library`, `XWindowAttributes`, `XImage`, `XShmSegmentInfo`, `X11Display`

### Stats

```
 vision/claude_vision.py           |  277 ++++++++
 vision/desktop_vision.py          |  253 +++++++
 vision/screen_capture.py          |  279 ++++++++
 vision/x11_bindings.py            |  401 ++++++++++++
 21 files changed, 4627 insertions(+), 78 deletions(-)
```

_Full diff: `git diff v1.11.0..v1.12.0`_


## [1.11.0] - 2026-02-28

### Added

- **Agent Activity Widget** - Real-time agent activity panel in the dashboard showing live spawned agents, streaming output, and completion status for both mission and investigation contexts; tabbed interface (AtlasForge / Mission / Investigation panels)
- **Agent Stream Manager** - New `agent_stream_manager.py` backend module; tracks agent lifecycle events (spawn, stream lines, complete, error), maintains live state, and broadcasts to dashboard via WebSocket
- **WebSocket Events Module** - New `websocket_events.py` dedicated module for all widget push events; decoupled from dashboard_v2.py for cleaner architecture
- **Subagent Pool Manager** - New `subagent_pool_manager.py` and `pool_manager.py` for managing concurrent subagent execution pools
- **Mission Status Schema** - New `dashboard_modules/mission_status_schema.py` for canonical mission state validation
- **TTL Cache** - `dashboard_modules/cache.py` now includes a thread-safe in-memory TTL cache for hot API endpoints (`/api/status`, `/api/journal`)
- **Investigation Validator** - Full investigation claim validation system (`claim_extractor.py`, `source_fetcher.py`, `validator_agent.py`, `orchestrator.py`, `filter.py`) made permanent from workspace

### Fixed

- **Agent Activity Widget Wiring** - Fixed broken WebSocket room subscriptions; `mission_agents` and `investigation_agents` rooms were missing from `socket.js` event handler registry and default subscriptions
- **Dead Function Reference** - Fixed `agent-activity.js` calling non-existent `window.subscribeToAgentRoom()` — corrected to `window.subscribeToSocketRoom()`
- **Handler Registration Gap** - Registered `handleMissionAgentEvent` and `handleInvestigationAgentEvent` in `widgets.js` `initWebSocketHandlers()` which previously left them unwired
- **pool_status room** - Added missing `pool_status` WebSocket room to socket registry

### Changed

- **Dashboard Architecture** - Significant backend refactor; modularized WebSocket event emission, agent tracking, and cache management out of `dashboard_v2.py` into dedicated modules
- **Restored `.gitignore`** - Rebuilt after Codex destructive rollback; added `certs/`, `recovery_*/`, `*.pre_recovery_backup` entries


## [1.10.0] - 2026-02-23

### Added

- **Automated Release Pipeline** - `python3 atlasforge_conductor.py --release` auto-increments semantic version, generates CHANGELOG.md entry, creates annotated git tag; supports `--dry-run`, `--push-tags`, `--publish-pypi`, `--bump=minor/patch`
- **Clean Push Script** - `scripts/clean_push.py` squashes [AF] mission artifact commits into a single clean commit before pushing to remote; keeps main branch history readable
- **Release Workflow Script** - `scripts/release_workflow.py` standalone release pipeline callable independently of the conductor
- **Pre-push Hook** - `scripts/pre_push_hook.sh` + `scripts/install_hooks.py` prevent accidental pushes of raw [AF] artifact commits to remote
- **Mission Params Blueprint** - `dashboard_modules/mission_params.py` canonical mission config validation and cross-mission health metrics
- **Git Strategy Documentation** - conductor header documents the three-tier commit strategy (real code on main, mission artifacts gitignored, checkpoints on orphan branch)

### Changed

- **Conductor shutdown fix** - `HAS_ENHANCED_CONDUCTOR or is_shutdown_requested()` corrected to `and`; conductor was exiting immediately on every launch
- **Stage key typo fix** - `XXcurrent_stageXX` corrected to `current_stage` in main loop
- **Mission announcement deduplication** - conductor now tracks `_announced_mission_id` to avoid duplicate chat announcements per mission
- **af_engine orchestrator** - stage transitions, state manager, and git integration improvements
- **Dashboard mission params** - pre-flight validation via canonical MissionConfig, cross-mission history cache (30s TTL)

### Fixed

- **Conductor immediate shutdown bug** - `or` vs `and` logic error caused conductor to exit within 1 second of every launch after enhanced conductor module was introduced
- **Stage gate hook post-mission blocking** - hook now correctly bypasses enforcement when no active conductor lock file exists


All notable changes to AI-AtlasForge will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-01-13

### Added

- **Autonomous R&D Engine**: Claude-powered autonomous research and development system
  - Multi-stage workflow (PLANNING, BUILDING, TESTING, ANALYZING, CYCLE_END, COMPLETE)
  - Cycle-based iteration with configurable budgets (1-10 cycles)
  - Automatic stage restrictions to enforce clean execution patterns

- **AtlasForge Dashboard**: Real-time web-based mission monitoring
  - Live mission status and progress tracking
  - Interactive decision graph visualization
  - Exploration memory and drift monitoring widgets
  - GlassBox transcript viewer with search and filtering
  - WebSocket-based real-time updates
  - Keyboard shortcuts for power users

- **Knowledge Base Integration**: Cross-mission learning system
  - Semantic search for relevant past learnings
  - Automatic injection of relevant techniques during planning
  - Gotcha avoidance from documented past failures

- **Mission Analytics**: Cost and performance tracking
  - Token usage monitoring per stage
  - API cost estimation
  - Stage timing breakdowns

- **AtlasForge Enhancements**: Cognitive enhancement modules
  - Exploration graph tracking
  - Fingerprint extraction for behavioral analysis
  - Mission continuity tracking
  - Bias detection and context healing

- **Hierarchical Framework**: Parallel agent orchestration
  - Multi-agent task splitting
  - Checkpoint-based synchronization
  - Timeout budget management
  - Result aggregation

- **Experiment Framework**: Systematic testing infrastructure
  - Controlled Claude instance spawning
  - Multi-condition experiments
  - Cross-model comparison support

- **Installation System**
  - `install.sh` automated installer with dependency management
  - Virtual environment support
  - System service integration (systemd)
  - Environment detection and generation

- **Documentation**
  - README.md with quick start guide
  - INSTALL.md with detailed installation instructions
  - USAGE.md with operational guide
  - ARCHITECTURE.md with system design overview
  - GROUND_RULES.md for autonomous agent guidance

- **Configuration**
  - `config.example.yaml` template
  - `.env.example` for environment variables
  - `ENVIRONMENT.example.md` hardware profile template
  - Centralized configuration via `atlasforge_config.py`

### Changed

- Rebranded from "RDE" (Research & Development Engine) to "AtlasForge"
  - All module names updated (rd_engine -> af_engine, rde -> atlasforge)
  - Dashboard routes updated (/api/rde/* -> /api/atlasforge/*)
  - Enhancement package renamed (rde_enhancements -> atlasforge_enhancements)

- Dashboard improvements
  - ES6 module system for JavaScript
  - Improved caching and performance
  - Enhanced error handling
  - Modular widget architecture

### Removed

- Legacy RDE naming and modules
  - `rd_engine.py` (replaced by `af_engine.py`)
  - `rde_tray.py` (replaced by `atlasforge_tray.py`)
  - `rde_enhancements/` directory (replaced by `atlasforge_enhancements/`)
  - `dashboard_modules/rde.py` (replaced by `dashboard_modules/atlasforge.py`)

### Security

- Hardcoded paths removed and replaced with configurable alternatives
- Secrets and credentials properly excluded via `.gitignore`
- File protection system for core components with automatic backups
- Dashboard import policy to prevent cross-mission contamination

### Notes

This is the initial public release of AI-AtlasForge, forked and evolved from the
mini-mind-v2 project. The platform enables autonomous AI agents to perform complex
software engineering tasks with minimal human supervision while maintaining
transparency and reproducibility.

---

## [Unreleased]

### Planned

- Docker support with Dockerfile and docker-compose.yml
- Expanded documentation with tutorials
- Additional widget visualizations
- Enhanced mission comparison features
