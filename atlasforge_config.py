#!/usr/bin/env python3
"""
AtlasForge Configuration Module

Centralized configuration for AI-AtlasForge paths and settings.
All modules should import BASE_DIR and other paths from this module.

Environment Variables:
    ATLASFORGE_ROOT: Override the default base directory
    ATLASFORGE_PORT: Dashboard port (default: 5050)
    ATLASFORGE_DEBUG: Enable debug mode (default: false)
"""

import os
from pathlib import Path

# Determine BASE_DIR from script location or environment variable
_SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("ATLASFORGE_ROOT", str(_SCRIPT_DIR)))

# Core directories
STATE_DIR = BASE_DIR / "state"
WORKSPACE_DIR = BASE_DIR / "workspace"
LOG_DIR = BASE_DIR / "logs"
MISSIONS_DIR = BASE_DIR / "missions"
INVESTIGATIONS_DIR = BASE_DIR / "investigations"
RDE_DATA_DIR = BASE_DIR / "rde_data"
BACKUPS_DIR = BASE_DIR / "backups"
SCREENSHOTS_DIR = BASE_DIR / "screenshots"

# Workspace subdirectories
ARTIFACTS_DIR = WORKSPACE_DIR / "artifacts"
RESEARCH_DIR = WORKSPACE_DIR / "research"
TESTS_DIR = WORKSPACE_DIR / "tests"

# Data directories
KNOWLEDGE_BASE_DIR = RDE_DATA_DIR / "knowledge_base"
ANALYTICS_DIR = RDE_DATA_DIR / "analytics"
EXPLORATION_DIR = RDE_DATA_DIR / "exploration"

# Key files
MISSION_PATH = STATE_DIR / "mission.json"
CLAUDE_STATE_PATH = STATE_DIR / "claude_state.json"
MISSION_QUEUE_PATH = STATE_DIR / "mission_queue.json"
GROUND_RULES_PATH = BASE_DIR / "GROUND_RULES.md"
PID_PATH = BASE_DIR / "claude_autonomous.pid"

# Dashboard configuration
DASHBOARD_PORT = int(os.environ.get("ATLASFORGE_PORT", "5050"))
DEBUG_MODE = os.environ.get("ATLASFORGE_DEBUG", "false").lower() == "true"

# Template and static directories
TEMPLATES_DIR = BASE_DIR / "dashboard_templates"
STATIC_DIR = BASE_DIR / "dashboard_static"


def ensure_directories():
    """Create required directories if they don't exist."""
    for directory in [
        STATE_DIR,
        WORKSPACE_DIR,
        LOG_DIR,
        MISSIONS_DIR,
        INVESTIGATIONS_DIR,
        RDE_DATA_DIR,
        BACKUPS_DIR,
        SCREENSHOTS_DIR,
        ARTIFACTS_DIR,
        RESEARCH_DIR,
        TESTS_DIR,
        KNOWLEDGE_BASE_DIR,
        ANALYTICS_DIR,
        EXPLORATION_DIR,
    ]:
        directory.mkdir(parents=True, exist_ok=True)


def get_mission_workspace(mission_id: str) -> Path:
    """Get the workspace path for a specific mission."""
    return MISSIONS_DIR / mission_id / "workspace"


def get_transcript_dir() -> Path:
    """Get the Claude transcript directory for this installation."""
    transcripts_base = Path.home() / ".claude" / "projects"
    dir_name = "-" + str(BASE_DIR).replace("/", "-").lstrip("-")
    return transcripts_base / dir_name


# Ensure directories exist on import
ensure_directories()
