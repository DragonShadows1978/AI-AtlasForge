# Changelog

## [1.10.0] - 2026-02-22

### Added

- v1.8.0: Google Gemini provider support
- v1.7.1: Update PyPI metadata with Codex support in README
- Add Codex support to README and changelog
- Update README for v1.5.0 features
- Add HTTPS/SSL support for dashboard
- v1.3.4: Add semantic search dashboard module
- Add workspace deduplication with smart project naming
- Fix mission suggestions loading + add WebSocket events
- Add Related Projects section linking to AI-AfterImage, fix pyproject.toml license

### Changed

- Release v1.8.7 - Widget settings popup, collapsed card improvements
- Release v1.8.6 - Widget controls, token sanity check, dashboard improvements
- v1.6.3: Version bump for PyPI release
- Release v1.5.1: Improved version checker
- [Auto] Dashboard update from mission mission_938d2123
- [Auto] Dashboard update from mission mission_e8ecb36e
- [Auto] Dashboard update from mission mission_cd7eb987
- [Auto] Dashboard update from mission mission_0e18dceb
- [Auto] Dashboard update from mission mission_6ee0cf34
- Revert "[Auto] Dashboard update from mission mission_c6140e2e"
- [Auto] Dashboard update from mission mission_c6140e2e
- Release v1.4.0: Major refactoring and engine migration
- [Auto] Dashboard update from mission mission_0a7b9841
- v1.3.6: Minor updates
- [Auto] Dashboard update from mission mission_64d51f7f
- [Auto] Dashboard update from mission mission_6f914c0b
- [Auto] Dashboard update from mission mission_c1c4a4e8
- [Auto] Dashboard update from mission mission_2b356be9
- [Auto] Dashboard update from mission mission_35e142d2
- [Auto] Dashboard update from mission mission_f13f7848

### Fixed

- Release v1.9.1 - Dashboard persistence and stage gate fixes
- Release v1.8.5 - CLAUDECODE env fix and mission completions
- v1.8.3: Test harness improvements and stability fixes
- v1.8.2: Bug fixes for null handling and storage fallback
- v1.8.1: Fix dashboard services configuration
- v1.6.9 - GlassBox visualization fixes
- v1.6.8 - Session/timer fixes and conductor singleton
- v1.6.7 - Fix conductor JSON parsing bug
- v1.6.5: Release with mission f14912e5 fixes
- v1.6.4: Version bump for PyPI release (correct source)
- v1.6.2: ContextWatcher fixes and plan mode disabled
- Fix: Use CLI subscription for Haiku instead of API key
- Fix mission queue auto-start: conductor now detects queue signal
- Release v1.4.1: Fix mission queue race conditions
- v1.3.5: Major bug fixes
- Fix race condition in mission suggestions storage
- Fix AfterImage dashboard to bind 0.0.0.0 for remote access
- Fix BASE_DIR import bug in 3 files
- Fix multi-part dashboard and mission system issues
- Fix dashboard widget cascade failure

### Removed

- v1.8.4 - Handoff overhaul, widget visibility, drag-drop reordering
- Remove auto-generated CI workflow (not needed for release)

### Other

- Release v1.9.0 - Modular engine only, legacy af_engine.py retired
- v1.7.0: Ground rules, context watcher overhaul, experiment framework
- Release v1.6.0 - ContextWatcher & StenoAI
- Release v1.5.0: Modular Engine & Mission Queue System
- Release v1.4.3: Version status indicators in dashboard header
- Release v1.4.2: Dashboard header redesign
- Enable AfterImage web dashboard by default
- Auto-start AfterImage embedder daemon with dashboard
- Streamline installation process for better accessibility

### Stats

```
 timeout_budget.py                                  |   50 +-
 verify.sh                                          |  353 ++
 vision                                             |    1 +
 websocket_events.py                                |  644 ++++
 296 files changed, 54256 insertions(+), 1771 deletions(-)
```

_Full diff: `git diff v1.0.0..v1.10.0`_


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
