#!/usr/bin/env python3
"""
R&D Engine: A state machine for autonomous research and development.

Stages (6-stage workflow with cycle iteration):
    PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE
        ^                                  |              |
        |__________________________________|              |
                 (if tests fail)                          |
        |_________________________________________________|
                  (if more cycles remain)

Stage Restrictions:
    - PLANNING: Understand mission + write plan to artifacts/research.
    - BUILDING: Full write access.
    - TESTING: Full write access.
    - ANALYZING: Write only to reports/analysis.
    - CYCLE_END: Generate cycle reports, write continuation prompts.
    - COMPLETE: Read-only.

Mission Workspace:
    Each mission gets its own workspace folder under missions/mission_<UUID>/
    containing workspace/, artifacts/, research/, and tests/ subdirectories.
"""

import json
import logging
import shutil
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

import io_utils
from init_guard import InitGuard, get_stage_restrictions

# Project name resolver for workspace deduplication
PROJECT_NAME_RESOLVER_AVAILABLE = False
resolve_project_name = None
try:
    from project_name_resolver import resolve_project_name
    PROJECT_NAME_RESOLVER_AVAILABLE = True
except ImportError:
    pass  # Project name resolver not available - will use legacy workspace paths

# AtlasForge Enhancement integration (optional - gracefully degrades if not available)
AtlasForge_ENHANCER_AVAILABLE = False
AtlasForgeEnhancer = None
try:
    from atlasforge_enhancements import AtlasForgeEnhancer
    AtlasForge_ENHANCER_AVAILABLE = True
except ImportError:
    pass  # AtlasForge enhancements not installed - features will be disabled

# Crash recovery integration (optional)
RECOVERY_AVAILABLE = False
try:
    from stage_checkpoint_recovery import (
        detect_incomplete_mission,
        save_stage_progress,
        clear_current_checkpoint,
        get_recovery_context,
        StageCheckpoint
    )
    RECOVERY_AVAILABLE = True
except ImportError:
    pass  # Recovery module not available

# Decision graph integration (optional)
DECISION_GRAPH_AVAILABLE = False
try:
    from decision_graph import get_decision_logger, log_tool_invocation
    DECISION_GRAPH_AVAILABLE = True
except ImportError:
    pass  # Decision graph module not available

# Mission Analytics integration (optional)
ANALYTICS_AVAILABLE = False
try:
    from mission_analytics import get_analytics, track_stage_start, track_stage_end
    ANALYTICS_AVAILABLE = True
except ImportError:
    pass  # Analytics module not available

# Real-time token watcher integration (optional)
TOKEN_WATCHER_AVAILABLE = False
try:
    from realtime_token_watcher import (
        get_token_watcher, start_watching_mission,
        stop_watching_mission, update_mission_stage
    )
    TOKEN_WATCHER_AVAILABLE = True
except ImportError:
    pass  # Token watcher module not available

# Knowledge Base integration (optional)
KB_AVAILABLE = False
try:
    from mission_knowledge_base import get_knowledge_base
    KB_AVAILABLE = True
except ImportError:
    pass  # Knowledge base module not available

# AI-AfterImage integration (optional - episodic code memory)
AFTERIMAGE_AVAILABLE = False
try:
    import sys
    from atlasforge_config import WORKSPACE_DIR
    afterimage_path = str(WORKSPACE_DIR / "AI-AfterImage")
    sys.path.insert(0, afterimage_path)
    from afterimage.search import HybridSearch as AfterImageSearch
    AFTERIMAGE_AVAILABLE = True
except ImportError:
    pass  # AfterImage module not available

# Plan Backup integration (optional)
PLAN_BACKUP_AVAILABLE = False
try:
    from plan_backup import backup_planned_files
    PLAN_BACKUP_AVAILABLE = True
except ImportError:
    pass  # Plan backup module not available

# Post-Mission Hooks integration (optional)
POST_MISSION_HOOKS_AVAILABLE = False
try:
    from post_mission_hooks import run_post_mission_hooks
    POST_MISSION_HOOKS_AVAILABLE = True
except ImportError:
    pass  # Post-mission hooks module not available

# Git Mission Integration (optional - for checkpoint-based commits)
GIT_INTEGRATION_AVAILABLE = False
try:
    from git_mission_integration import handle_stage_transition as git_handle_stage_transition
    GIT_INTEGRATION_AVAILABLE = True
except ImportError:
    pass  # Git integration module not available

# Mission Snapshot integration (optional - for backup/recovery)
SNAPSHOT_AVAILABLE = False
try:
    from mission_snapshot_manager import create_mission_snapshot, get_snapshot_manager
    SNAPSHOT_AVAILABLE = True
except ImportError:
    pass  # Snapshot module not available

# Mission Drift Validation integration (optional - prevents scope creep in multi-cycle missions)
DRIFT_VALIDATION_AVAILABLE = False
MissionDriftValidator = None
DriftTrackingState = None
DriftDecision = None
try:
    from adversarial_testing.mission_drift_validator import (
        MissionDriftValidator,
        DriftTrackingState,
        DriftDecision,
        load_tracking_state,
        save_tracking_state,
        save_validation_result
    )
    DRIFT_VALIDATION_AVAILABLE = True
except ImportError:
    pass  # Drift validation module not available

# Phase-aware drift validation integration (extends base drift validation)
PHASE_AWARE_DRIFT_AVAILABLE = False
PhaseAwareMissionDriftValidator = None
PhaseTrackingState = None
try:
    from adversarial_testing.phase_aware_validator import (
        PhaseAwareMissionDriftValidator
    )
    from adversarial_testing.phase_aware_drift import (
        PhaseTrackingState,
        load_phase_state,
        save_phase_state,
        initialize_phase_tracking
    )
    PHASE_AWARE_DRIFT_AVAILABLE = True
except ImportError:
    pass  # Phase-aware drift validation not available

# Queue Scheduler and Notifications integration (optional)
QUEUE_SCHEDULER_AVAILABLE = False
QUEUE_NOTIFICATIONS_AVAILABLE = False
try:
    from mission_queue_scheduler import get_scheduler as get_queue_scheduler
    QUEUE_SCHEDULER_AVAILABLE = True
except ImportError:
    pass  # Queue scheduler module not available

try:
    from queue_notifications import (
        get_notifier as get_queue_notifier,
        notify_queue_empty,
        notify_mission_failed,
        notify_mission_completed
    )
    QUEUE_NOTIFICATIONS_AVAILABLE = True
except ImportError:
    pass  # Queue notifications module not available

# Artifact Manager integration (optional - automated artifact management at CYCLE_END)
ARTIFACT_MANAGER_AVAILABLE = False
run_cycle_end_artifact_processing = None
try:
    from workspace.AtlasForge.artifact_manager import run_cycle_end_artifact_processing
    ARTIFACT_MANAGER_AVAILABLE = True
except ImportError:
    pass  # Artifact manager module not available

# Paths - Import from centralized config
from atlasforge_config import (
    BASE_DIR, STATE_DIR, WORKSPACE_DIR, ARTIFACTS_DIR, RESEARCH_DIR,
    MISSION_PATH, get_transcript_dir
)
from ground_rules_loader import load_ground_rules, get_active_llm_provider

# Auto-advance signaling (file-based IPC)
AUTO_ADVANCE_SIGNAL_PATH = STATE_DIR / "auto_advance_signal.json"

# Transcript archival paths
CLAUDE_TRANSCRIPTS_BASE = Path.home() / ".claude" / "projects"
CLAUDE_TRANSCRIPTS_DIR = get_transcript_dir()
TRANSCRIPTS_ARCHIVE_DIR = ARTIFACTS_DIR / "transcripts"


def _workspace_to_transcript_dir(workspace_path: str) -> Path:
    """
    Convert a workspace path to Claude's transcript directory format.

    Claude stores transcripts in: ~/.claude/projects/-{path-with-dashes}
    e.g., /path/to/AI-AtlasForge/missions/mission_xyz/workspace
       -> ~/.claude/projects/-path-to-AI-AtlasForge-missions-mission-xyz-workspace

    Note: Claude converts underscores to dashes in directory names.

    Args:
        workspace_path: Absolute path to workspace directory

    Returns:
        Path to the corresponding Claude transcript directory
    """
    if not workspace_path:
        return CLAUDE_TRANSCRIPTS_DIR

    # Normalize path and convert to Claude's format
    workspace_path = str(workspace_path).rstrip('/')
    # Claude replaces both / and _ with -
    escaped = workspace_path.replace('/', '-').replace('_', '-')

    return CLAUDE_TRANSCRIPTS_BASE / escaped


def _get_all_transcript_dirs_for_mission(mission: Dict) -> List[Path]:
    """
    Get all possible transcript directories for a mission.

    This handles:
    1. Main project directory (legacy missions)
    2. Mission-specific workspace directory
    3. Any mission dir variations

    Args:
        mission: Mission dict with workspace paths

    Returns:
        List of transcript directories to search
    """
    dirs_to_check = []

    # 1. Main project directory (always check as fallback)
    if CLAUDE_TRANSCRIPTS_DIR.exists():
        dirs_to_check.append(CLAUDE_TRANSCRIPTS_DIR)

    # 2. Mission workspace directory
    workspace = mission.get('mission_workspace')
    if workspace:
        workspace_dir = _workspace_to_transcript_dir(workspace)
        if workspace_dir.exists() and workspace_dir not in dirs_to_check:
            dirs_to_check.append(workspace_dir)

    # 3. Mission base directory (in case workspace is under mission dir)
    mission_dir = mission.get('mission_dir')
    if mission_dir:
        # Also check the workspace subdirectory
        workspace_under_mission = Path(mission_dir) / 'workspace'
        if workspace_under_mission.exists():
            mission_workspace_dir = _workspace_to_transcript_dir(str(workspace_under_mission))
            if mission_workspace_dir.exists() and mission_workspace_dir not in dirs_to_check:
                dirs_to_check.append(mission_workspace_dir)

    return dirs_to_check

logger = logging.getLogger("af_engine")

# Valid stages (6-stage workflow with CYCLE_END)
STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]

# Mission logs directory
MISSIONS_DIR = BASE_DIR / "missions"
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"
MISSIONS_DIR.mkdir(exist_ok=True)
MISSION_LOGS_DIR.mkdir(exist_ok=True)


# =============================================================================
# TRANSCRIPT ARCHIVAL FUNCTIONS
# =============================================================================

def _find_transcripts_in_window(
    start_dt: datetime,
    end_dt: datetime,
    transcript_dirs: List[Path] = None,
    mission: Dict = None
) -> List[Path]:
    """
    Find .jsonl files modified within the mission time window.

    Searches multiple transcript directories to handle:
    - Main project directory (legacy missions)
    - Mission-specific workspace directories

    Args:
        start_dt: Mission start datetime
        end_dt: Mission end datetime
        transcript_dirs: Optional list of directories to search
        mission: Optional mission dict to auto-detect directories

    Returns:
        List of Path objects for matching transcript files
    """
    # Determine which directories to search
    if transcript_dirs is None:
        if mission is not None:
            transcript_dirs = _get_all_transcript_dirs_for_mission(mission)
        else:
            transcript_dirs = [CLAUDE_TRANSCRIPTS_DIR] if CLAUDE_TRANSCRIPTS_DIR.exists() else []

    if not transcript_dirs:
        logger.warning("No transcript directories found to search")
        return []

    matching_files = []
    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()
    seen_files = set()  # Avoid duplicates

    for transcript_dir in transcript_dirs:
        if not transcript_dir.exists():
            logger.debug(f"Transcript directory not found: {transcript_dir}")
            continue

        for jsonl_file in transcript_dir.glob("*.jsonl"):
            # Skip if we've already seen this file (by name, to handle symlinks/duplicates)
            file_key = jsonl_file.name
            if file_key in seen_files:
                continue

            try:
                mtime = os.path.getmtime(jsonl_file)
                # Include files modified within the window (with small buffer for edge cases)
                if start_ts - 60 <= mtime <= end_ts + 60:
                    matching_files.append(jsonl_file)
                    seen_files.add(file_key)
            except OSError as e:
                logger.warning(f"Could not get mtime for {jsonl_file}: {e}")

    logger.info(f"Found {len(matching_files)} transcripts in {len(transcript_dirs)} directories")
    return matching_files


def _parse_transcript_usage(transcript_path: Path) -> Dict[str, int]:
    """
    Parse token usage from a transcript file.

    Reads each line as JSON, looks for assistant messages with usage data,
    and sums up all token counts.

    Args:
        transcript_path: Path to the .jsonl file

    Returns:
        Dict with input_tokens, output_tokens, total_tokens, and cache tokens
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0
    }

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    # Look for assistant messages with usage data
                    if record.get("type") == "assistant":
                        msg = record.get("message", {})
                        msg_usage = msg.get("usage", {})
                        if msg_usage:
                            usage["input_tokens"] += msg_usage.get("input_tokens", 0)
                            usage["output_tokens"] += msg_usage.get("output_tokens", 0)
                            usage["cache_creation_input_tokens"] += msg_usage.get("cache_creation_input_tokens", 0)
                            usage["cache_read_input_tokens"] += msg_usage.get("cache_read_input_tokens", 0)
                except json.JSONDecodeError:
                    # Skip malformed lines
                    continue
    except Exception as e:
        logger.warning(f"Error parsing transcript {transcript_path}: {e}")

    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def _generate_manifest(mission_id: str, archive_dir: Path,
                       transcripts: List[Path], usage_data: List[Dict],
                       start_dt: datetime, end_dt: datetime) -> Dict:
    """
    Generate manifest.json for the archive.

    Args:
        mission_id: The mission identifier
        archive_dir: Path to the archive directory
        transcripts: List of transcript file paths
        usage_data: List of usage dicts from _parse_transcript_usage
        start_dt: Mission start time
        end_dt: Mission end time

    Returns:
        The manifest dict
    """
    manifest = {
        "mission_id": mission_id,
        "archived_at": datetime.now().isoformat(),
        "time_window": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        },
        "transcripts": [],
        "totals": {
            "transcript_count": len(transcripts),
            "total_size_bytes": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_creation_input_tokens": 0,
            "total_cache_read_input_tokens": 0,
            "total_tokens": 0
        }
    }

    for transcript, usage in zip(transcripts, usage_data):
        try:
            size = transcript.stat().st_size
            mtime = datetime.fromtimestamp(os.path.getmtime(transcript))
        except OSError:
            size = 0
            mtime = datetime.now()

        manifest["transcripts"].append({
            "filename": transcript.name,
            "size_bytes": size,
            "modified_at": mtime.isoformat(),
            "token_usage": usage
        })

        manifest["totals"]["total_size_bytes"] += size
        manifest["totals"]["total_input_tokens"] += usage.get("input_tokens", 0)
        manifest["totals"]["total_output_tokens"] += usage.get("output_tokens", 0)
        manifest["totals"]["total_cache_creation_input_tokens"] += usage.get("cache_creation_input_tokens", 0)
        manifest["totals"]["total_cache_read_input_tokens"] += usage.get("cache_read_input_tokens", 0)
        manifest["totals"]["total_tokens"] += usage.get("total_tokens", 0)

    return manifest


def archive_mission_transcripts(mission: Dict) -> Dict:
    """
    Archive all transcripts from the mission time window.

    This function:
    1. Calculates the time window from mission created_at to last_updated
    2. Finds all .jsonl files modified in that window
    3. Copies them to artifacts/transcripts/{mission_id}/
    4. Parses token usage and generates manifest.json

    Args:
        mission: The mission dict with created_at, last_updated, mission_id

    Returns:
        Dict with archival results (success, count, path, errors)
    """
    result = {
        "success": False,
        "transcripts_archived": 0,
        "archive_path": None,
        "errors": []
    }

    try:
        # Get mission ID
        mission_id = mission.get("mission_id")
        if not mission_id:
            # Generate mission ID from timestamp
            created_at = mission.get("created_at", datetime.now().isoformat())
            timestamp_clean = created_at.replace(":", "-").replace(".", "-")[:19]
            mission_id = f"mission_{timestamp_clean}"

        # Get time window
        try:
            start_dt = datetime.fromisoformat(mission.get("created_at", datetime.now().isoformat()))
        except (ValueError, TypeError):
            logger.warning("Invalid created_at, using epoch")
            start_dt = datetime(1970, 1, 1)

        try:
            end_dt = datetime.fromisoformat(mission.get("last_updated", datetime.now().isoformat()))
        except (ValueError, TypeError):
            end_dt = datetime.now()

        # Find transcripts in window (searches mission-specific and main directories)
        transcripts = _find_transcripts_in_window(start_dt, end_dt, mission=mission)

        # Create archive directory
        archive_dir = TRANSCRIPTS_ARCHIVE_DIR / mission_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        result["archive_path"] = str(archive_dir)

        # Copy transcripts and parse usage
        usage_data = []
        copied_files = []

        for transcript in transcripts:
            try:
                dest_path = archive_dir / transcript.name
                shutil.copy2(transcript, dest_path)
                copied_files.append(transcript)
                usage = _parse_transcript_usage(transcript)
                usage_data.append(usage)
                logger.info(f"Archived transcript: {transcript.name}")
            except Exception as e:
                error_msg = f"Failed to copy {transcript.name}: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)

        # Generate and save manifest
        manifest = _generate_manifest(
            mission_id, archive_dir, copied_files, usage_data, start_dt, end_dt
        )
        manifest_path = archive_dir / "manifest.json"
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        result["success"] = True
        result["transcripts_archived"] = len(copied_files)
        result["manifest"] = manifest

        logger.info(f"Archived {len(copied_files)} transcripts to {archive_dir}")

        # Emit WebSocket event for transcript archival
        try:
            from websocket_events import emit_transcript_archived
            emit_transcript_archived(
                mission_id=mission_id,
                archive_path=str(archive_dir),
                transcript_count=len(copied_files),
                stats=manifest
            )
        except ImportError:
            pass

    except Exception as e:
        error_msg = f"Transcript archival failed: {e}"
        logger.error(error_msg)
        result["errors"].append(error_msg)

    return result


def rearchive_mission(mission_id: str) -> Dict:
    """
    Re-archive a specific mission's transcripts.

    This is useful for fixing missions that were archived with empty/missing
    transcript data due to the old hardcoded directory path bug.

    Args:
        mission_id: The mission ID to re-archive

    Returns:
        Dict with archival results
    """
    # Load mission log to get mission details
    mission_log_path = MISSION_LOGS_DIR / f"{mission_id}.json"
    archive_manifest_path = TRANSCRIPTS_ARCHIVE_DIR / mission_id / "manifest.json"

    mission = None

    # Try to get mission info from various sources
    if mission_log_path.exists():
        try:
            with open(mission_log_path, 'r') as f:
                mission_log = json.load(f)
            mission = {
                'mission_id': mission_id,
                'created_at': mission_log.get('created_at'),
                'last_updated': mission_log.get('completed_at'),
                'mission_workspace': mission_log.get('mission_workspace'),
                'mission_dir': mission_log.get('mission_dir')
            }
        except (json.JSONDecodeError, KeyError):
            pass

    # Try archive manifest as fallback
    if mission is None and archive_manifest_path.exists():
        try:
            with open(archive_manifest_path, 'r') as f:
                manifest = json.load(f)
            time_window = manifest.get('time_window', {})
            mission = {
                'mission_id': mission_id,
                'created_at': time_window.get('start'),
                'last_updated': time_window.get('end')
            }
        except (json.JSONDecodeError, KeyError):
            pass

    # Construct mission workspace path from mission_id if not available
    if mission is None or not mission.get('mission_workspace'):
        # Infer workspace path from mission_id
        inferred_workspace = str(MISSIONS_DIR / mission_id / 'workspace')
        if mission is None:
            mission = {
                'mission_id': mission_id,
                'mission_workspace': inferred_workspace,
                'mission_dir': str(MISSIONS_DIR / mission_id)
            }
        else:
            mission['mission_workspace'] = inferred_workspace
            mission['mission_dir'] = str(MISSIONS_DIR / mission_id)

    # Run the archival
    return archive_mission_transcripts(mission)


def rearchive_all_missions() -> Dict:
    """
    Re-archive all missions that have empty or missing transcript data.

    Returns:
        Dict with summary of rearchived missions
    """
    results = {
        'total_checked': 0,
        'rearchived': 0,
        'errors': [],
        'details': []
    }

    if not TRANSCRIPTS_ARCHIVE_DIR.exists():
        return results

    for archive_dir in TRANSCRIPTS_ARCHIVE_DIR.iterdir():
        if not archive_dir.is_dir():
            continue

        mission_id = archive_dir.name
        results['total_checked'] += 1

        # Check if archive needs re-archiving (empty or zero tokens)
        manifest_path = archive_dir / "manifest.json"
        needs_rearchive = False

        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                totals = manifest.get('totals', {})
                if totals.get('transcript_count', 0) == 0:
                    needs_rearchive = True
            except (json.JSONDecodeError, KeyError):
                needs_rearchive = True
        else:
            needs_rearchive = True

        if needs_rearchive:
            try:
                result = rearchive_mission(mission_id)
                if result['success'] and result['transcripts_archived'] > 0:
                    results['rearchived'] += 1
                    results['details'].append({
                        'mission_id': mission_id,
                        'transcripts': result['transcripts_archived'],
                        'tokens': result.get('manifest', {}).get('totals', {}).get('total_tokens', 0)
                    })
            except Exception as e:
                results['errors'].append(f"{mission_id}: {str(e)}")

    return results


class RDMissionController:
    """
    Manages the lifecycle of an R&D mission.

    Stage Flow (6 stages with cycle iteration):
        PLANNING: Understand mission + create implementation plan
        BUILDING: Write the code/solution
        TESTING: Run tests, verify solution works
        ANALYZING: Evaluate results, decide next steps
        CYCLE_END: Generate report, continue to next cycle or complete
        COMPLETE: Mission accomplished

    Cycle Iteration:
        Missions can have a cycle_budget (default 1).
        After ANALYZING success, enters CYCLE_END where Claude:
        - Generates a cycle report
        - If more cycles remain: writes continuation prompt, returns to PLANNING
        - If budget exhausted: generates final report, moves to COMPLETE
    """

    def __init__(self):
        self.mission = self.load_mission()
        # Flag to prevent log_history from saving during queue processing
        self._queue_processing = False
        # Initialize stage if not set (default to PLANNING)
        if "current_stage" not in self.mission:
            self.mission["current_stage"] = "PLANNING"
            self.save_mission()
        # Initialize cycle tracking if not present
        if "cycle_budget" not in self.mission:
            self.mission["cycle_budget"] = 1
        if "current_cycle" not in self.mission:
            self.mission["current_cycle"] = 1
        if "cycle_history" not in self.mission:
            self.mission["cycle_history"] = []

    def load_mission(self) -> dict:
        """Load mission from disk."""
        default_mission = {
            "mission_id": "default",
            "problem_statement": "No mission defined. Please set a mission.",
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "preferences": {},
            "success_criteria": [],
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            # Cycle iteration fields
            "cycle_budget": 1,
            "current_cycle": 1,
            "cycle_history": [],
            "original_problem_statement": None  # Keeps root mission for multi-cycle
        }
        return io_utils.atomic_read_json(MISSION_PATH, default_mission)

    def save_mission(self):
        """Save mission to disk."""
        io_utils.atomic_write_json(MISSION_PATH, self.mission)

    def _get_enhancer(self):
        """
        Lazy initialization of AtlasForge enhancer.

        Returns an AtlasForgeEnhancer instance if available and configured,
        otherwise returns None. Safe to call even if enhancements
        are not installed.
        """
        if not hasattr(self, '_enhancer'):
            self._enhancer = None
        if self._enhancer is None and AtlasForge_ENHANCER_AVAILABLE:
            mission_id = self.mission.get('mission_id')
            workspace = self.mission.get('mission_workspace')
            if mission_id and workspace:
                try:
                    self._enhancer = AtlasForgeEnhancer(
                        mission_id=mission_id,
                        storage_base=Path(workspace) / 'af_data'
                    )
                    logger.info(f"AtlasForge Enhancer initialized for mission {mission_id}")
                except Exception as e:
                    logger.warning(f"Could not initialize AtlasForgeEnhancer: {e}")
        return self._enhancer

    def update_stage(self, new_stage: str):
        """Update the current R&D stage."""
        if new_stage not in STAGES:
            logger.warning(f"Invalid stage: {new_stage}. Valid stages: {STAGES}")
            return

        old_stage = self.mission.get("current_stage", "UNKNOWN")
        self.mission["current_stage"] = new_stage
        self.mission["last_updated"] = datetime.now().isoformat()
        self.save_mission()
        logger.info(f"R&D Stage: {old_stage} -> {new_stage}")

        # Emit WebSocket event for stage change
        try:
            from websocket_events import emit_stage_change
            emit_stage_change(
                mission_id=self.mission.get("mission_id", "unknown"),
                old_stage=old_stage,
                new_stage=new_stage,
                iteration=self.mission.get("iteration", 0)
            )
        except ImportError:
            pass

        # Analytics: Track stage transitions
        if ANALYTICS_AVAILABLE:
            try:
                mission_id = self.mission.get("mission_id")
                iteration = self.mission.get("iteration", 0)
                cycle = self.mission.get("current_cycle", 1)

                # End old stage
                if old_stage and old_stage not in ("UNKNOWN", "COMPLETE"):
                    track_stage_end(mission_id, old_stage, iteration, cycle)

                # Start new stage
                if new_stage != "COMPLETE":
                    track_stage_start(mission_id, new_stage, iteration, cycle)

                # End mission when reaching COMPLETE
                if new_stage == "COMPLETE":
                    analytics = get_analytics()
                    analytics.end_mission(mission_id, "complete")
                    logger.info(f"Analytics: Mission {mission_id} completed")
            except Exception as e:
                logger.warning(f"Analytics: Stage tracking failed: {e}")

        # Real-time token watcher: Update stage for attribution
        if TOKEN_WATCHER_AVAILABLE:
            try:
                if new_stage == "COMPLETE":
                    # Stop watching when mission completes
                    stop_watching_mission()
                    logger.info("Token watcher: Stopped (mission complete)")
                else:
                    # Update stage for token attribution
                    update_mission_stage(new_stage)
                    logger.debug(f"Token watcher: Stage updated to {new_stage}")
            except Exception as e:
                logger.debug(f"Token watcher: Stage update failed: {e}")

        # Git Integration: Create checkpoint on stage transition
        if GIT_INTEGRATION_AVAILABLE:
            try:
                git_result = git_handle_stage_transition(old_stage, new_stage, self.mission)
                if git_result.get("checkpoint_created"):
                    self.log_history(
                        f"Git checkpoint: {git_result.get('checkpoint_message', 'created')}",
                        {"git_checkpoint": True, "commit_hash": git_result.get("checkpoint_message")}
                    )
                    logger.info(f"Git: Created checkpoint for {old_stage} -> {new_stage}")
                if git_result.get("push_executed"):
                    push_result = git_result.get("push_result", {})
                    self.log_history(
                        f"Git push: {push_result.get('commits_pushed', 0)} commit(s)" +
                        (" (squashed)" if push_result.get('squashed') else ""),
                        {"git_push": True, "push_result": push_result}
                    )
                    logger.info(f"Git: Push executed - {push_result.get('message', 'success')}")
                if git_result.get("errors"):
                    for error in git_result["errors"]:
                        logger.warning(f"Git integration error: {error}")
            except Exception as e:
                logger.warning(f"Git integration: Stage transition handling failed: {e}")

        # Save checkpoint for crash recovery
        if RECOVERY_AVAILABLE and new_stage not in ("COMPLETE",):
            try:
                mission_id = self.mission.get("mission_id", "unknown")
                iteration = self.mission.get("iteration", 0)
                cycle = self.mission.get("cycle_current", 1)

                # Get list of files created in workspace
                files_created = []
                workspace = self.mission.get("workspace")
                if workspace and Path(workspace).exists():
                    for f in Path(workspace).rglob("*"):
                        if f.is_file():
                            files_created.append(str(f.relative_to(workspace)))

                save_stage_progress(
                    progress={
                        "mission_id": mission_id,
                        "stage": new_stage,
                        "iteration": iteration,
                        "cycle": cycle,
                    },
                    files_created=files_created[:50],  # Limit to 50 files
                    recovery_hint=f"Mission was in {new_stage} stage, iteration {iteration}"
                )
                logger.debug(f"Saved checkpoint for stage {new_stage}")
            except Exception as e:
                logger.warning(f"Failed to save checkpoint: {e}")

        # Create snapshot on stage transition for backup/recovery
        if SNAPSHOT_AVAILABLE and new_stage not in ("COMPLETE",):
            try:
                snapshot = create_mission_snapshot(
                    stage_hint=f"Stage transition: {old_stage} -> {new_stage}"
                )
                if snapshot:
                    self.log_history(
                        f"Snapshot created: {snapshot.snapshot_id}",
                        {"snapshot": snapshot.snapshot_id, "hash": snapshot.sha256_hash[:16]}
                    )
                    logger.debug(f"Created snapshot {snapshot.snapshot_id}")
            except Exception as e:
                logger.warning(f"Failed to create snapshot: {e}")

        # Clear checkpoint when mission completes
        if new_stage == "COMPLETE":
            if RECOVERY_AVAILABLE:
                try:
                    clear_current_checkpoint()
                    logger.debug("Cleared recovery checkpoint")
                except Exception as e:
                    logger.warning(f"Failed to clear checkpoint: {e}")

        # Archive transcripts when mission completes
        if new_stage == "COMPLETE":
            logger.info("Mission complete - archiving transcripts...")
            archival_result = archive_mission_transcripts(self.mission)
            if archival_result["success"]:
                self.log_history(
                    f"Archived {archival_result['transcripts_archived']} transcripts",
                    {"archive_path": archival_result["archive_path"]}
                )
            else:
                self.log_history(
                    "Transcript archival failed",
                    {"errors": archival_result["errors"]}
                )

            # Notify narrative workflow module if this was a workflow step mission
            self._notify_narrative_completion()

            # Run post-mission hooks (e.g., git auto-commit)
            if POST_MISSION_HOOKS_AVAILABLE:
                try:
                    hook_results = run_post_mission_hooks(self.mission)
                    for result in hook_results:
                        if result.get("success"):
                            self.log_history(
                                f"Post-mission hook '{result['hook']}': {result.get('message', 'completed')}",
                                {"files_committed": result.get("files_committed", []),
                                 "commit_hash": result.get("commit_hash")}
                            )
                        else:
                            logger.warning(f"Post-mission hook '{result['hook']}' failed: {result.get('error')}")
                except Exception as e:
                    logger.error(f"Post-mission hooks failed: {e}")

            # Explicitly emit recommendation event on mission completion
            # This ensures real-time push even if the initial emit failed
            self._emit_latest_recommendation_on_complete()

            # Process mission queue - start next queued mission if available
            self._process_mission_queue()

    def _process_mission_queue(self):
        """
        Check if there are queued missions and start the next one.

        This is called after a mission completes (reaches COMPLETE stage).
        Uses the extended queue scheduler if available, which handles:
        - Priority-based ordering
        - Scheduled start times
        - Mission dependencies

        Falls back to simple FIFO queue if scheduler not available.

        IMPORTANT: Queue item is only removed AFTER successful mission creation
        to prevent mission loss if creation fails.

        Uses file-based locking to prevent race conditions with
        dashboard_v2.queue_auto_start_watcher().
        """
        # Acquire queue processing lock to prevent race conditions
        try:
            from queue_processing_lock import acquire_queue_lock, release_queue_lock
            if not acquire_queue_lock(source="af_engine", timeout=2, blocking=False):
                logger.info("Queue processing locked by another process, skipping")
                return
        except ImportError:
            logger.warning("queue_processing_lock module not available, proceeding without lock")

        queue_path = STATE_DIR / "mission_queue.json"

        # Set flag to prevent log_history from saving during queue processing
        self._queue_processing = True

        try:
            # Try using the extended scheduler if available
            if QUEUE_SCHEDULER_AVAILABLE:
                scheduler = get_queue_scheduler()
                next_item_obj = scheduler.get_next_ready_item()

                if next_item_obj is None:
                    # Check if queue is empty vs just waiting
                    state = scheduler.get_queue()
                    if not state.queue:
                        logger.debug("Queue empty - no next mission")
                        # Send notification that queue is empty
                        if QUEUE_NOTIFICATIONS_AVAILABLE:
                            notify_queue_empty(self.mission.get("mission_id"))
                        return
                    else:
                        logger.debug("No ready items - all waiting on schedule/dependencies")
                        return

                # DON'T remove the item yet - wait for successful mission creation
                next_item = next_item_obj.to_dict()
                next_item_id = next_item_obj.id
                queue = scheduler.get_queue().queue
            else:
                # Fallback to simple queue processing
                queue_data = io_utils.atomic_read_json(queue_path, {"queue": [], "enabled": True})

                if not queue_data.get("enabled", True):
                    logger.debug("Queue processing disabled - skipping")
                    return

                queue = queue_data.get("queue", [])
                if not queue:
                    logger.debug("Queue empty - no next mission")
                    return

                # DON'T pop yet - just peek at the first item
                next_item = queue[0]
                next_item_id = next_item.get("id")

            logger.info(f"Processing queued mission: {next_item.get('mission_title', 'Untitled')}")

            # Send completion notification for previous mission
            if QUEUE_NOTIFICATIONS_AVAILABLE:
                prev_mission_id = self.mission.get("mission_id")
                prev_mission_title = self.mission.get("original_problem_statement", "")[:50]
                cycles_used = self.mission.get("current_cycle", 1)
                notify_mission_completed(
                    prev_mission_id,
                    prev_mission_title,
                    cycles_used,
                    len(queue)
                )

            # Create the new mission - returns True on success
            success = self._create_mission_from_queue_item(next_item)

            # Only remove from queue AFTER successful mission creation
            if success:
                if QUEUE_SCHEDULER_AVAILABLE:
                    scheduler.remove_item(next_item_id)
                    logger.info(f"Removed item {next_item_id} from queue after successful mission creation")
                else:
                    # Fallback: remove from simple queue
                    queue_data = io_utils.atomic_read_json(queue_path, {"queue": [], "enabled": True})
                    queue = queue_data.get("queue", [])
                    # Remove the first item (the one we processed)
                    if queue and queue[0].get("id") == next_item_id:
                        queue.pop(0)
                    else:
                        # Fallback: remove by matching ID
                        queue = [q for q in queue if q.get("id") != next_item_id]
                    queue_data["queue"] = queue
                    queue_data["last_processed_at"] = datetime.now().isoformat()
                    io_utils.atomic_write_json(queue_path, queue_data)
                    logger.info(f"Removed item {next_item_id} from queue after successful mission creation")

                # Emit queue update event
                try:
                    from websocket_events import emit_queue_updated
                    if QUEUE_SCHEDULER_AVAILABLE:
                        updated_queue = scheduler.get_queue()
                        # QueueState.queue is already a list of dicts
                        # Build settings dict from individual QueueState attributes
                        emit_queue_updated({
                            "missions": updated_queue.queue,
                            "settings": {
                                "enabled": updated_queue.enabled,
                                "paused": updated_queue.paused,
                                "auto_estimate_time": updated_queue.auto_estimate_time,
                                "default_priority": updated_queue.default_priority
                            }
                        }, 'mission_started')
                    else:
                        emit_queue_updated(queue_data, 'mission_started')
                except Exception as e:
                    logger.warning(f"Failed to emit queue update: {e}")
            else:
                logger.error(f"Mission creation failed - keeping item {next_item_id} in queue")

        except Exception as e:
            logger.error(f"Queue processing failed: {e}")
        finally:
            # Reset queue processing flag
            self._queue_processing = False
            # Release queue processing lock
            try:
                from queue_processing_lock import release_queue_lock
                release_queue_lock()
            except ImportError:
                pass

    def _create_mission_from_queue_item(self, queue_item: dict) -> bool:
        """
        Create a new mission from a queue item and signal for auto-start.

        Args:
            queue_item: Dict with mission_title, mission_description, cycle_budget, project_name, etc.

        Returns:
            bool: True if mission was created successfully, False otherwise
        """
        import uuid

        try:
            # Generate mission ID
            mission_id = f"mission_{uuid.uuid4().hex[:8]}"

            # Get mission details from queue item
            # Handle both dashboard format (problem_statement) and core format (mission_description)
            problem_statement = (
                queue_item.get("mission_description") or
                queue_item.get("problem_statement") or
                queue_item.get("mission_title", "")
            )
            cycle_budget = queue_item.get("cycle_budget", 3)
            user_project_name = queue_item.get("project_name")

            # Resolve project name for shared workspace
            resolved_project_name = None
            if PROJECT_NAME_RESOLVER_AVAILABLE:
                resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
                # Use shared workspace under workspace/<project_name>/
                mission_workspace = WORKSPACE_DIR / resolved_project_name
                logger.info(f"Queue mission resolved project name: {resolved_project_name}")
            else:
                # Legacy: per-mission workspace
                mission_workspace = MISSIONS_DIR / mission_id / "workspace"

            # Create mission directory (for config, analytics, drift validation)
            mission_dir = MISSIONS_DIR / mission_id
            mission_dir.mkdir(parents=True, exist_ok=True)

            # Create workspace directories (may already exist if shared project)
            (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

            # Create the mission
            new_mission = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "original_problem_statement": problem_statement,
                "preferences": {},
                "success_criteria": [],
                "current_stage": "PLANNING",
                "iteration": 0,
                "max_iterations": 10,
                "artifacts": {"plan": None, "code": [], "tests": []},
                "history": [],
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "cycle_started_at": datetime.now().isoformat(),
                "cycle_budget": max(1, cycle_budget),
                "current_cycle": 1,
                "cycle_history": [],
                "mission_workspace": str(mission_workspace),
                "mission_dir": str(mission_dir),
                "project_name": resolved_project_name,
                "source_queue_item_id": queue_item.get("id"),
                "source_recommendation_id": queue_item.get("recommendation_id"),
                "metadata": {"queued": True, "queued_at": queue_item.get("queued_at")}
            }

            # Save mission state with return value check
            success = io_utils.atomic_write_json(MISSION_PATH, new_mission)
            if not success:
                logger.error(f"Failed to write new mission {mission_id} to disk")
                return False

            # Save mission config
            mission_config_path = mission_dir / "mission_config.json"
            config_data = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "cycle_budget": max(1, cycle_budget),
                "created_at": new_mission["created_at"],
                "source_queue_item_id": queue_item.get("id")
            }
            if resolved_project_name:
                config_data["project_name"] = resolved_project_name
                config_data["project_workspace"] = str(mission_workspace)
            with open(mission_config_path, 'w') as f:
                json.dump(config_data, f, indent=2)

            # Register with analytics if available
            if ANALYTICS_AVAILABLE:
                try:
                    analytics = get_analytics()
                    analytics.start_mission(mission_id, problem_statement)
                except Exception as e:
                    logger.warning(f"Analytics: Failed to register queued mission: {e}")

            # Signal for auto-start via file-based IPC
            # The dashboard/watcher will detect this and start R&D mode
            auto_start_signal = {
                "action": "start_rd",
                "mission_id": mission_id,
                "mission_title": queue_item.get("mission_title", "Queued Mission"),
                "signaled_at": datetime.now().isoformat(),
                "source": "queue"
            }
            signal_path = STATE_DIR / "queue_auto_start_signal.json"
            io_utils.atomic_write_json(signal_path, auto_start_signal)

            # Emit WebSocket notification for dashboard
            try:
                from websocket_events import emit_mission_auto_started
                mission_title = queue_item.get("mission_title") or (problem_statement[:60] + "..." if len(problem_statement) > 60 else problem_statement)
                emit_mission_auto_started(
                    mission_id=mission_id,
                    mission_title=mission_title,
                    queue_id=queue_item.get("id"),
                    source="queue_auto"
                )
            except ImportError:
                pass  # websocket_events not available

            logger.info(f"Created mission {mission_id} from queue. Auto-start signal written.")
            # NOTE: Do NOT call self.log_history() here - it would overwrite the new mission
            # with self.mission (the old COMPLETE mission) via save_mission()
            # Instead, just log to logger and let the new mission start fresh
            logger.info(f"Queued mission started: {queue_item.get('mission_title', 'Untitled')} "
                       f"(new_mission_id={mission_id}, queue_item_id={queue_item.get('id')})")

            # Small delay to ensure filesystem sync before verification
            import time
            time.sleep(0.01)  # 10ms

            # Verify mission was created successfully
            verify_mission = io_utils.atomic_read_json(MISSION_PATH, {})
            if verify_mission.get("mission_id") == mission_id and verify_mission.get("current_stage") == "PLANNING":
                logger.info(f"Verified mission {mission_id} created with PLANNING stage")
                return True
            else:
                logger.error(f"Mission verification failed: expected {mission_id} in PLANNING stage, "
                           f"got {verify_mission.get('mission_id')} in {verify_mission.get('current_stage')}")
                return False

        except Exception as e:
            logger.error(f"Failed to create mission from queue item: {e}")
            return False

    def increment_iteration(self):
        """Increment iteration counter (for retry loops)."""
        self.mission["iteration"] = self.mission.get("iteration", 0) + 1
        self.save_mission()
        logger.info(f"R&D Iteration: {self.mission['iteration']}")

    def log_history(self, entry: str, details: dict = None):
        """Log an action to the mission history.

        Safety: Will not save to disk during queue processing to prevent
        overwriting newly created missions.
        """
        # Safety check: don't save if we're in the middle of queue processing
        if getattr(self, '_queue_processing', False):
            logger.warning(f"Skipping log_history save during queue processing: {entry}")
            return

        if "history" not in self.mission:
            self.mission["history"] = []

        history_entry = {
            "timestamp": datetime.now().isoformat(),
            "stage": self.mission.get("current_stage"),
            "iteration": self.mission.get("iteration", 0),
            "entry": entry
        }
        if details:
            history_entry["details"] = details

        self.mission["history"].append(history_entry)

        # Keep history bounded
        if len(self.mission["history"]) > 100:
            self.mission["history"] = self.mission["history"][-100:]

        self.save_mission()

    def get_recent_history(self, n: int = 10) -> list:
        """Get recent history entries."""
        return self.mission.get("history", [])[-n:]

    def build_rd_prompt(self, context: str = "") -> str:
        """
        Generate the prompt for the current stage.
        Each stage has specific instructions and expected JSON response format.
        """
        stage = self.mission.get("current_stage", "PLANNING")
        problem = self.mission.get("problem_statement", "No problem defined.")
        iteration = self.mission.get("iteration", 0)

        # Extract preferences
        prefs = self.mission.get("preferences", {})
        pref_str = ""
        if prefs:
            pref_str = "\n=== PREFERENCES & CONSTRAINTS ===\n"
            for k, v in prefs.items():
                if isinstance(v, list):
                    pref_str += f"- {k}: {', '.join(str(x) for x in v)}\n"
                else:
                    pref_str += f"- {k}: {v}\n"

        # Extract success criteria
        criteria = self.mission.get("success_criteria", [])
        criteria_str = ""
        if criteria:
            criteria_str = "\n=== SUCCESS CRITERIA ===\n"
            for i, c in enumerate(criteria, 1):
                criteria_str += f"{i}. {c}\n"

        # Recent history for context
        recent = self.get_recent_history(5)
        history_str = ""
        if recent:
            history_str = "\n=== RECENT HISTORY ===\n"
            for h in recent:
                history_str += f"[{h.get('stage')}] {h.get('entry', '')[:100]}\n"

        # Load provider-aware ground rules (base + optional overlay).
        provider = get_active_llm_provider()
        ground_rules = ""
        try:
            ground_rules, base_path, overlay_path, _ = load_ground_rules(provider=provider)
            if not ground_rules:
                logger.warning(f"No ground rules found (base path: {base_path})")
            elif overlay_path:
                logger.info(
                    f"Loaded ground rules for provider '{provider}' "
                    f"with overlay {overlay_path.name}"
                )
        except Exception as e:
            logger.warning(f"Failed to read ground rules: {e}")

        # Base prompt
        if provider == "codex":
            agent_name = "Codex"
        elif provider == "gemini":
            agent_name = "Gemini"
        else:
            agent_name = "Claude"
        prompt_content = f"""You are {agent_name}, operating as an Autonomous R&D Engineer.

=== GROUND RULES (READ CAREFULLY) ===
{ground_rules}
=== END GROUND RULES ===

CURRENT MISSION: {problem}
CURRENT STAGE: {stage}
ITERATION: {iteration}
WORKSPACE: {WORKSPACE_DIR}
{pref_str}
{criteria_str}
{history_str}

IMPORTANT: After executing your tasks, respond with a JSON object. Your response must be valid JSON.
"""

        if context:
            prompt_content += f"\nADDITIONAL CONTEXT:\n{context}\n"

        # Add crash recovery context if available
        if RECOVERY_AVAILABLE:
            try:
                recovery_context = get_recovery_context()
                if recovery_context:
                    prompt_content += f"\n=== CRASH RECOVERY CONTEXT ===\n"
                    prompt_content += recovery_context
                    prompt_content += "\n=== END RECOVERY CONTEXT ===\n"
            except Exception as e:
                logger.warning(f"Could not add recovery context: {e}")

        # AtlasForge Enhancement: Add exploration context if available
        enhancer = self._get_enhancer()
        if enhancer:
            try:
                stats = enhancer.get_exploration_stats()
                if stats.get('total_nodes', 0) > 0:
                    prompt_content += "\n=== PRIOR EXPLORATION MEMORY ===\n"
                    prompt_content += f"Files explored: {stats.get('total_nodes', 0)}\n"
                    prompt_content += f"Insights captured: {stats.get('total_insights', 0)}\n"
                    prompt_content += f"Concepts discovered: {stats.get('total_concepts', 0)}\n"
                    prompt_content += "Use `from exploration_hooks import what_do_we_know, should_explore` to query prior knowledge.\n"
                    prompt_content += "=== END PRIOR EXPLORATION ===\n"
            except Exception as e:
                logger.warning(f"Could not add exploration context: {e}")

        # Stage-specific instructions
        if stage == "PLANNING":
            prompt_content += self._prompt_planning()
        elif stage == "BUILDING":
            prompt_content += self._prompt_building()
        elif stage == "TESTING":
            prompt_content += self._prompt_testing()
        elif stage == "ANALYZING":
            prompt_content += self._prompt_analyzing()
        elif stage == "CYCLE_END":
            prompt_content += self._prompt_cycle_end()
        elif stage == "COMPLETE":
            prompt_content += self._prompt_complete()
        else:
            prompt_content += f"""
Unknown stage '{stage}'. Respond with:
{{
    "status": "error",
    "message": "Unknown stage",
    "suggested_stage": "PLANNING"
}}
"""

        return prompt_content

    def _prompt_planning(self) -> str:
        # Check for resumption file
        resumption_content = ""
        resumption_file = BASE_DIR / "resumption_state" / "next_steps.txt"
        if resumption_file.exists():
            try:
                resumption_content = resumption_file.read_text()
                resumption_content = f"""
=== RESUMPTION INSTRUCTIONS ===
{resumption_content}
=== END RESUMPTION ===
"""
            except Exception as e:
                logger.warning(f"Failed to read resumption file: {e}")

        # Get planning restrictions
        guard_prompt = InitGuard.get_planning_system_prompt()

        # KNOWLEDGE BASE CONTEXT INJECTION
        # Semantic search of past mission learnings for relevant context
        kb_context = ""
        if KB_AVAILABLE:
            try:
                kb = get_knowledge_base()
                problem_statement = self.mission.get("problem_statement", "")
                if problem_statement:
                    kb_context = kb.generate_planning_context(problem_statement)
                    if kb_context:
                        logger.info("Knowledge Base: Injected planning context from past missions")
                    else:
                        logger.debug("Knowledge Base: No relevant learnings found for this mission")
            except Exception as e:
                logger.warning(f"Knowledge Base: Context generation failed: {e}")

        # AI-AFTERIMAGE CODE MEMORY INJECTION
        # Search for similar code written in past sessions
        afterimage_context = ""
        if AFTERIMAGE_AVAILABLE:
            try:
                problem_statement = self.mission.get("problem_statement", "")
                mission_name = self.mission.get("name", "")
                query = f"{mission_name} {problem_statement}"[:200]  # Limit query length

                if query.strip():
                    search = AfterImageSearch()
                    results = search.search(query, limit=5, threshold=0.01)

                    if results:
                        lines = ["\n=== AFTERIMAGE: RELEVANT CODE FROM PAST SESSIONS ==="]
                        lines.append("You have written similar code before. Consider these patterns:\n")

                        for i, r in enumerate(results[:3], 1):
                            from pathlib import Path
                            short_path = "/".join(Path(r.file_path).parts[-3:])
                            code_preview = r.new_code[:400] if len(r.new_code) > 400 else r.new_code
                            lines.append(f"**Past Code {i}:** `{short_path}`")
                            lines.append(f"```\n{code_preview}\n```\n")

                        lines.append("=== END AFTERIMAGE ===\n")
                        afterimage_context = "\n".join(lines)
                        logger.info(f"AfterImage: Injected {len(results)} code examples from past sessions")
            except Exception as e:
                logger.warning(f"AfterImage: Code memory injection failed: {e}")

        return f"""
{guard_prompt}
{kb_context}
{afterimage_context}
=== PLANNING STAGE ===
Your goal: Understand the mission AND create a detailed implementation plan.
{resumption_content}

IMPORTANT: You are AUTONOMOUS. Do NOT ask clarifying questions. Make reasonable assumptions and proceed.

In PLANNING stage, you may ONLY write to artifacts/ or research/ directories.
Do NOT write actual code yet. Save implementation for BUILDING stage.

=== RESEARCH PHASE (BEFORE Implementation Planning) ===
Your implementation plan should be EVIDENCE-BASED, not just based on training data.

MANDATORY: Knowledge Base Consultation
The Knowledge Base context above (if present) contains SEMANTIC SEARCH RESULTS from past missions.
- These are learnings from similar problems you've solved before
- PAY ATTENTION to "Gotchas to Avoid" - these are past failures to prevent
- Apply "Relevant Techniques" if they match your current problem
- Similar Past Missions show approaches that worked (or didn't)

Research Tasks:
1. **FIRST**: Review any Knowledge Base context above and incorporate relevant learnings
2. Use WebSearch to find current best practices for the task (include year 2025)
3. Use WebFetch to get official documentation if relevant technologies are involved
4. Search for similar problems and their solutions (prior art)
5. Look for common pitfalls and "what NOT to do" guidance
6. Document research findings in {RESEARCH_DIR}/research_findings.md

Research Questions to Answer:
- What are the current best practices for this type of work?
- What tools or frameworks are commonly used?
- What are known failure modes or antipatterns?
- What recent developments (2024-2025) are relevant?
- **What did past missions teach us about similar problems?**

Cite sources for architectural decisions when available.
If research reveals better approaches than initially considered, adjust your plan.

=== IMPLEMENTATION PLANNING ===

Tasks (in order):
1. Read and understand the problem statement above
2. Conduct active research using WebSearch/WebFetch (see Research Phase above)
3. Explore the codebase to understand existing patterns
4. Identify key requirements and constraints
5. Make reasonable assumptions for any ambiguities
6. Break down the problem into concrete steps
7. Identify files to create/modify in {WORKSPACE_DIR}/
8. Define clear success criteria
9. Consider 2-3 alternative approaches (informed by research)
10. Write research findings to {RESEARCH_DIR}/research_findings.md
11. Write your plan to {ARTIFACTS_DIR}/implementation_plan.md

Respond with JSON:
{{
    "status": "plan_complete",
    "understanding": "Your summary of what needs to be built",
    "kb_learnings_applied": ["list any KB learnings you incorporated, or empty if none"],
    "research_conducted": ["topic1: key finding", "topic2: key finding"],
    "sources_consulted": ["url1", "url2"],
    "key_requirements": ["requirement1", "requirement2", ...],
    "assumptions": ["any assumptions you made"],
    "approach": "Brief description of chosen approach",
    "approach_rationale": "Why this approach (cite KB learnings and sources if available)",
    "steps": [
        {{"step": 1, "description": "...", "files": ["file1.py"]}},
        {{"step": 2, "description": "...", "files": ["file2.py"]}}
    ],
    "success_criteria": ["criterion1", "criterion2"],
    "estimated_files": ["list of files to create"],
    "message_to_human": "Planning complete. Ready to build."
}}
"""

    def _prompt_building(self) -> str:
        # AI-AFTERIMAGE CODE MEMORY INJECTION FOR BUILDING
        # Read implementation plan and search for similar past code
        afterimage_context = ""
        if AFTERIMAGE_AVAILABLE:
            try:
                # Try to read the implementation plan
                plan_path = self.mission_dir / "workspace" / "artifacts" / "implementation_plan.md"
                if plan_path.exists():
                    plan_content = plan_path.read_text()[:2000]  # First 2000 chars

                    search = AfterImageSearch()
                    results = search.search(plan_content[:500], limit=5, threshold=0.01)

                    if results:
                        from pathlib import Path
                        lines = ["\n=== AFTERIMAGE: CODE PATTERNS FROM PAST SESSIONS ==="]
                        lines.append("Based on your implementation plan, here's similar code you've written before:\n")

                        seen_paths = set()
                        for r in results:
                            short_path = "/".join(Path(r.file_path).parts[-3:])
                            if short_path in seen_paths:
                                continue
                            seen_paths.add(short_path)

                            if len(seen_paths) > 3:
                                break

                            code_preview = r.new_code[:500] if len(r.new_code) > 500 else r.new_code
                            lines.append(f"**Reference:** `{short_path}`")
                            lines.append(f"```\n{code_preview}\n```\n")

                        lines.append("Use these patterns as reference. Adapt, don't copy blindly.")
                        lines.append("=== END AFTERIMAGE ===\n")
                        afterimage_context = "\n".join(lines)
                        logger.info(f"AfterImage: Injected {len(seen_paths)} code patterns for BUILDING")
            except Exception as e:
                logger.warning(f"AfterImage: Building context injection failed: {e}")

        return f"""
{afterimage_context}
=== BUILDING STAGE ===
Your goal: Implement the solution based on your plan.

Tasks:
1. Read your plan from {ARTIFACTS_DIR}/implementation_plan.md
2. Write code to {WORKSPACE_DIR}/
3. Create all necessary files and directories
4. Ensure code is complete and runnable
5. Follow any style preferences specified

Respond with JSON:
{{
    "status": "build_complete" | "build_in_progress" | "build_blocked",
    "files_created": ["list of files created"],
    "files_modified": ["list of files modified"],
    "summary": "What was built",
    "ready_for_testing": true | false,
    "blockers": ["any blockers, or empty list"],
    "message_to_human": "Build status message"
}}
"""

    def _prompt_testing(self) -> str:
        return f"""
=== TESTING STAGE ===
Your goal: Verify the solution works correctly with EPISTEMIC RIGOR.

IMPORTANT: You design your tests based on your code - of course they'll pass.
This is the "painter who loves their own work" problem. To build TRUE confidence,
you must include ADVERSARIAL TESTING - attempts to BREAK your own code.

=== PHASE 1: SELF-TESTS (Baseline) ===
Your own tests that verify basic functionality.

Tasks:
1. Create test script(s) in {WORKSPACE_DIR}/tests/ if needed
2. Run the code and capture output
3. Verify against success criteria from your plan

=== PHASE 2: ADVERSARIAL TESTING (Epistemic Rigor) ===
The adversarial_testing module is available in the project's adversarial_testing/ directory.

Adversarial Testing Tasks:
1. **Red Team Analysis**: Use the AdversarialRunner to spawn a fresh agent that tries to BREAK your code
   - Fresh agent has no memory of how you built it
   - Looks for vulnerabilities, edge cases, logic flaws

2. **Property Testing**: Generate edge cases automatically
   - Empty inputs, null values, boundary conditions
   - Very large inputs, negative numbers, special characters

3. **Mutation Testing** (if tests exist): Check if your tests ACTUALLY catch bugs
   - Mutation score should be >= 80%
   - Low mutation score means tests are weak

4. **Blind Validation**: Compare implementation against ORIGINAL specification
   - Does the code do what was originally requested?
   - Has there been "spec drift" during implementation?

=== ADVERSARIAL TESTING CODE ===
```python
# Example usage (run this to perform adversarial testing)
from adversarial_testing import AdversarialRunner, AdversarialConfig
from experiment_framework import ModelType

config = AdversarialConfig(
    mission_id="your_mission",
    model=ModelType.CLAUDE_HAIKU,  # Use Haiku for speed
    enable_mutation=False,  # Enable if you have tests
    enable_property=True,
    enable_blind_validation=True
)

runner = AdversarialRunner(config)
results = runner.run_full_suite(
    code_path=Path("path/to/code.py"),
    specification="The original requirements..."
)

# Check epistemic score
if results.report.epistemic_score:
    print(f"Epistemic Score: {{results.report.epistemic_score.overall_score:.0%}}")
    print(f"Rigor Level: {{results.report.epistemic_score.rigor_level.value}}")
```

=== OUTPUT REQUIREMENTS ===

Document ALL test results in {ARTIFACTS_DIR}/test_results.md including:
1. Self-test results (your own tests)
2. Adversarial findings (what the red team found)
3. Edge cases discovered (from property testing)
4. Mutation score (if applicable)
5. Spec alignment (from blind validation)

Respond with JSON:
{{
    "status": "tests_passed" | "tests_failed" | "tests_error",
    "self_tests": [
        {{"name": "test1", "passed": true, "output": "..."}},
        {{"name": "test2", "passed": false, "error": "..."}}
    ],
    "adversarial_testing": {{
        "red_team_issues": ["list of issues found by adversarial agent"],
        "property_violations": ["edge cases that broke the code"],
        "mutation_score": 0.0-1.0 or null,
        "spec_alignment": 0.0-1.0 or null,
        "epistemic_score": 0.0-1.0,
        "rigor_level": "insufficient|weak|moderate|strong|rigorous"
    }},
    "summary": "Overall test summary including adversarial findings",
    "success_criteria_met": ["which criteria were met"],
    "success_criteria_failed": ["which criteria failed"],
    "issues_to_fix": ["issues that need fixing before release"],
    "message_to_human": "Test results summary with adversarial analysis"
}}
"""

    def _prompt_analyzing(self) -> str:
        # Get analyzing restrictions
        guard_prompt = InitGuard.get_analyzing_system_prompt()

        return f"""
{guard_prompt}

=== ANALYZING STAGE ===
Your goal: Evaluate results and decide next steps.

IMPORTANT: In ANALYZING stage, only write to research/ or artifacts/.
Do NOT fix bugs here. If fixes are needed, recommend BUILDING stage.

Tasks:
1. Review test results from {ARTIFACTS_DIR}/test_results.md
2. If tests passed: Prepare completion report
3. If tests failed: Diagnose issues and plan fixes
4. Document analysis in {RESEARCH_DIR}/analysis.md

Respond with JSON:
{{
    "status": "success" | "needs_revision" | "needs_replanning",
    "analysis": "Your analysis of the results",
    "issues_found": ["list of issues, or empty"],
    "proposed_fixes": ["list of fixes if needed, or empty"],
    "recommendation": "COMPLETE" | "BUILDING" | "PLANNING",
    "message_to_human": "Analysis summary"
}}

If recommending COMPLETE, also include:
{{
    ...
    "final_report": "Summary of what was accomplished",
    "deliverables": ["list of files/artifacts produced"]
}}
"""

    def _prompt_cycle_end(self) -> str:
        """Prompt for CYCLE_END stage - generates cycle report and continuation."""
        current_cycle = self.mission.get("current_cycle", 1)
        cycle_budget = self.mission.get("cycle_budget", 1)
        cycles_remaining = cycle_budget - current_cycle
        original_mission = self.mission.get("original_problem_statement") or self.mission.get("problem_statement", "")

        if cycles_remaining > 0:
            return f"""
=== CYCLE END STAGE ===
You have completed cycle {current_cycle} of {cycle_budget}.
Cycles remaining: {cycles_remaining}

ORIGINAL MISSION: {original_mission[:500]}

Your task:
1. Generate a comprehensive report of what was accomplished this cycle
2. List ALL files created or modified this cycle
3. Summarize key achievements and any issues encountered
4. Write a CONTINUATION PROMPT for the next cycle

The continuation prompt should:
- Build on what was accomplished
- Address any remaining work
- Be a complete, standalone mission statement for the next cycle
- Reference specific files/code if needed

Respond with JSON:
{{
    "status": "cycle_complete",
    "cycle_number": {current_cycle},
    "cycle_report": {{
        "summary": "What was accomplished this cycle",
        "files_created": ["list of new files"],
        "files_modified": ["list of modified files"],
        "achievements": ["key accomplishments"],
        "issues": ["any issues encountered"]
    }},
    "continuation_prompt": "The complete mission statement for the next cycle. Be specific and detailed.",
    "message_to_human": "Cycle {current_cycle}/{cycle_budget} complete. Continuing to next cycle..."
}}
"""
        else:
            return f"""
=== CYCLE END STAGE (FINAL) ===
You have completed the FINAL cycle ({current_cycle} of {cycle_budget}).

ORIGINAL MISSION: {original_mission[:500]}

Your task:
1. Generate a comprehensive FINAL report of everything accomplished across ALL cycles
2. List ALL files created or modified across the entire mission
3. Summarize the complete journey from start to finish
4. Provide lessons learned and recommendations
5. **IMPORTANT**: Suggest ONE follow-up mission that would naturally extend or build upon this work

Respond with JSON:
{{
    "status": "mission_complete",
    "total_cycles": {cycle_budget},
    "final_report": {{
        "summary": "Complete summary of what was accomplished across all cycles",
        "all_files": ["list of all files created/modified"],
        "key_achievements": ["major accomplishments"],
        "challenges_overcome": ["problems solved"],
        "lessons_learned": ["insights for future missions"]
    }},
    "deliverables": ["final list of deliverables"],
    "next_mission_recommendation": {{
        "mission_title": "A concise title for the recommended next mission",
        "mission_description": "A detailed description of what the next mission should accomplish. Be specific and actionable.",
        "suggested_cycles": 3,
        "rationale": "Why this mission would be valuable to pursue next"
    }},
    "message_to_human": "Mission complete after {cycle_budget} cycles."
}}
"""

    def _prompt_complete(self) -> str:
        return f"""
=== COMPLETE STAGE ===
The mission has been completed!

Generate a final summary:

Respond with JSON:
{{
    "status": "mission_complete",
    "summary": "What was accomplished",
    "deliverables": ["list of deliverables"],
    "lessons_learned": ["any insights for future missions"],
    "message_to_human": "Mission complete message"
}}
"""

    def process_response(self, response: dict) -> str:
        """
        Process the agent's response and determine the next stage.

        Args:
            response: Parsed JSON response from Claude

        Returns:
            The new stage name
        """
        current_stage = self.mission.get("current_stage", "PLANNING")
        status = response.get("status", "")

        # Log the response
        self.log_history(
            f"Response: {status}",
            {"status": status, "stage": current_stage}
        )

        # Stage transition logic
        if current_stage == "PLANNING":
            if status == "plan_complete":
                # Pre-build backup: Backup files mentioned in plan before BUILDING
                if PLAN_BACKUP_AVAILABLE:
                    try:
                        backup_result = backup_planned_files(self.mission)
                        if backup_result.get("files_backed_up", 0) > 0:
                            self.log_history(
                                f"Pre-build backup: {backup_result['files_backed_up']} files",
                                {
                                    "files_backed_up": backup_result["files_backed_up"],
                                    "files_skipped": backup_result.get("files_skipped", 0),
                                    "manifest": backup_result.get("manifest", [])
                                }
                            )
                            logger.info(f"Pre-build backup complete: {backup_result['files_backed_up']} files backed up")
                        else:
                            logger.info("Pre-build backup: No files to backup (none exist or none found in plan)")
                        if backup_result.get("errors"):
                            logger.warning(f"Pre-build backup had errors: {backup_result['errors']}")
                    except Exception as e:
                        logger.error(f"Pre-build backup failed: {e}")
                return "BUILDING"
            else:
                logger.warning(f"PLANNING: Unexpected status '{status}', staying in PLANNING")
                return "PLANNING"

        elif current_stage == "BUILDING":
            if status == "build_complete" and response.get("ready_for_testing", False):
                return "TESTING"
            elif status == "build_in_progress":
                # Stay in building
                return "BUILDING"
            elif status == "build_blocked":
                # May need to go back to planning
                logger.warning("Build blocked - may need replanning")
                return "BUILDING"
            else:
                return "BUILDING"

        elif current_stage == "TESTING":
            if status == "tests_passed":
                return "ANALYZING"
            elif status == "tests_failed":
                return "ANALYZING"  # Go to analyzing to decide what to do
            elif status == "tests_error":
                return "ANALYZING"  # Go to analyzing to diagnose
            else:
                return "TESTING"

        elif current_stage == "ANALYZING":
            recommendation = response.get("recommendation", "").upper()

            if status == "success" or recommendation == "COMPLETE":
                # Go to CYCLE_END instead of COMPLETE to handle cycle iteration
                return "CYCLE_END"
            elif status == "needs_revision" or recommendation == "BUILDING":
                self.increment_iteration()
                return "BUILDING"
            elif status == "needs_replanning" or recommendation == "PLANNING":
                self.increment_iteration()
                return "PLANNING"
            else:
                # Default: stay in analyzing
                return "ANALYZING"

        elif current_stage == "CYCLE_END":
            # Handle cycle completion
            cycle_budget = self.mission.get("cycle_budget", 1)
            current_cycle = self.mission.get("current_cycle", 1)

            if status == "cycle_complete":
                # Save cycle report to history
                cycle_report = response.get("cycle_report", {})
                continuation_prompt = response.get("continuation_prompt", "")

                self._save_cycle_to_history(current_cycle, cycle_report, continuation_prompt)

                if current_cycle < cycle_budget:
                    # DRIFT VALIDATION: Check if continuation stays within mission scope
                    # This is the key checkpoint that prevents scope creep
                    validated_prompt, should_halt = self._validate_continuation_drift(
                        continuation_prompt, current_cycle
                    )

                    if should_halt:
                        # Mission halted due to excessive drift
                        logger.warning(f"Mission halted at cycle {current_cycle} due to drift")
                        self.mission["halted_due_to_drift"] = True
                        self.mission["halted_at_cycle"] = current_cycle
                        self.save_mission()
                        self._generate_drift_halt_report(response)
                        return "COMPLETE"

                    # Increment cycle and continue with validated/warning-injected prompt
                    self._advance_to_next_cycle(validated_prompt)
                    return "PLANNING"
                else:
                    # Budget exhausted - complete mission
                    self._generate_final_report(response)
                    return "COMPLETE"

            elif status == "mission_complete":
                # Final cycle complete
                self._generate_final_report(response)
                return "COMPLETE"

            else:
                # Stay in CYCLE_END if unexpected status
                logger.warning(f"CYCLE_END: Unexpected status '{status}'")
                return "CYCLE_END"

        elif current_stage == "COMPLETE":
            # Mission done - stay complete
            return "COMPLETE"

        else:
            logger.error(f"Unknown stage: {current_stage}")
            return "PLANNING"

    def _notify_narrative_completion(self):
        """Notify the narrative workflow module when a workflow step mission completes.

        This checks if the mission metadata indicates it's a narrative_workflow_step,
        and if so, calls the API to complete the step and optionally auto-advance.
        """
        try:
            metadata = self.mission.get("metadata", {})
            if metadata.get("mission_type") != "narrative_workflow_step":
                return  # Not a narrative workflow mission

            workflow_id = metadata.get("workflow_id")
            current_step = metadata.get("current_step")
            folder_path = metadata.get("folder_path")

            if not workflow_id or not current_step:
                logger.warning("Narrative mission missing workflow_id or current_step")
                return

            logger.info(f"Completing narrative step: {current_step} for workflow {workflow_id}")

            # Call the complete-step API
            import requests
            complete_url = f"http://localhost:5000/api/narrative/agent1/workflow/{workflow_id}/complete-step"

            # Gather files created during this mission cycle
            cycle_history = self.mission.get("cycle_history", [])
            files_created = []
            for cycle in cycle_history:
                files_created.extend(cycle.get("files_generated", []))

            response = requests.post(complete_url, json={
                "step": current_step,
                "outputs": {
                    "mission_id": self.mission.get("mission_id"),
                    "cycle_count": len(cycle_history),
                    "folder_path": folder_path
                },
                "files_created": files_created,
                "error": None
            }, timeout=10)

            if response.ok:
                result = response.json()
                logger.info(f"Narrative step completed: {result}")

                # Check if auto-advance is needed (not requiring approval)
                if result.get("success") and not result.get("requires_approval"):
                    self._auto_advance_narrative(workflow_id, result)
            else:
                logger.error(f"Failed to complete narrative step: {response.text}")

        except Exception as e:
            logger.error(f"Error notifying narrative completion: {e}")

    def _signal_auto_advance_start(self):
        """Signal that auto-advance is starting (file-based IPC)."""
        signal = {
            "status": "in_progress",
            "started_at": datetime.now().isoformat(),
            "completed_at": None,
            "new_mission_id": None,
            "error": None
        }
        io_utils.atomic_write_json(AUTO_ADVANCE_SIGNAL_PATH, signal)
        logger.debug("Auto-advance signal: started")

    def _signal_auto_advance_complete(self, new_mission_id: str = None, error: str = None):
        """Signal that auto-advance has completed (file-based IPC)."""
        signal = {
            "status": "complete" if not error else "error",
            "started_at": None,  # Will be overwritten
            "completed_at": datetime.now().isoformat(),
            "new_mission_id": new_mission_id,
            "error": error
        }
        # Read existing to preserve started_at
        existing = io_utils.atomic_read_json(AUTO_ADVANCE_SIGNAL_PATH, {})
        if existing.get("started_at"):
            signal["started_at"] = existing["started_at"]
        io_utils.atomic_write_json(AUTO_ADVANCE_SIGNAL_PATH, signal)
        logger.debug(f"Auto-advance signal: complete (mission_id={new_mission_id}, error={error})")

    def _clear_auto_advance_signal(self):
        """Clear the auto-advance signal file."""
        if AUTO_ADVANCE_SIGNAL_PATH.exists():
            try:
                AUTO_ADVANCE_SIGNAL_PATH.unlink()
            except OSError:
                pass

    def _auto_advance_narrative(self, workflow_id: str, complete_result: dict):
        """Automatically advance to the next narrative step if workflow is still running.

        This creates a new mission for the next step without user intervention.
        Uses file-based signaling to notify the main loop when complete.
        """
        # Signal that auto-advance is starting
        self._signal_auto_advance_start()

        try:
            import requests

            # Check if workflow is complete
            workflow = complete_result.get("workflow", {})
            if workflow.get("status") == "complete":
                logger.info(f"Narrative workflow {workflow_id} is complete!")
                self._signal_auto_advance_complete(error=None)  # Signal complete, no new mission
                return

            # Get the next step mission configuration
            execute_url = f"http://localhost:5000/api/narrative/agent1/workflow/{workflow_id}/execute-step"
            response = requests.post(execute_url, json={}, timeout=30)  # Increased timeout

            if not response.ok:
                logger.warning(f"Could not get next step: {response.text}")
                self._signal_auto_advance_complete(error=f"HTTP {response.status_code}")
                return

            result = response.json()
            if not result.get("success") or not result.get("mission"):
                logger.info(f"No next step available for workflow {workflow_id}")
                self._signal_auto_advance_complete(error=None)  # No error, just no next step
                return

            mission_config = result["mission"]

            # Create the mission with proper metadata
            create_url = "http://localhost:5000/api/mission"
            create_response = requests.post(create_url, json={
                "mission": mission_config["problem_statement"],
                "cycle_budget": mission_config.get("cycle_budget", 1),
                "metadata": mission_config.get("metadata", {})
            }, timeout=30)  # Increased timeout

            if create_response.ok:
                create_result = create_response.json()
                new_mission_id = create_result.get('mission_id')
                logger.info(f"Auto-launched next narrative step: {new_mission_id}")
                self._signal_auto_advance_complete(new_mission_id=new_mission_id)
            else:
                logger.error(f"Failed to create next narrative mission: {create_response.text}")
                self._signal_auto_advance_complete(error=f"Create failed: {create_response.status_code}")

        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout during auto-advance: {e}")
            self._signal_auto_advance_complete(error=f"Timeout: {e}")
        except Exception as e:
            logger.error(f"Error auto-advancing narrative: {e}")
            self._signal_auto_advance_complete(error=str(e))

    def _save_cycle_to_history(self, cycle_num: int, cycle_report: dict, continuation_prompt: str):
        """Save the completed cycle's report to cycle_history."""
        cycle_entry = {
            "cycle": cycle_num,
            "original_mission": self.mission.get("problem_statement", ""),
            "continuation_prompt": continuation_prompt,
            "files_generated": cycle_report.get("files_created", []) + cycle_report.get("files_modified", []),
            "summary": cycle_report.get("summary", ""),
            "achievements": cycle_report.get("achievements", []),
            "issues": cycle_report.get("issues", []),
            "started_at": self.mission.get("cycle_started_at"),
            "completed_at": datetime.now().isoformat()
        }

        if "cycle_history" not in self.mission:
            self.mission["cycle_history"] = []
        self.mission["cycle_history"].append(cycle_entry)

        # AtlasForge Enhancement: Process cycle end for fingerprinting and drift detection
        enhancer = self._get_enhancer()
        if enhancer:
            try:
                rde_report = enhancer.process_cycle_end(
                    cycle_number=cycle_num,
                    cycle_output=continuation_prompt,  # Use continuation as summary
                    files_created=cycle_report.get('files_created', []),
                    files_modified=cycle_report.get('files_modified', []),
                    cycle_summary=cycle_report.get('summary', '')
                )
                # Add AtlasForge data to cycle entry
                cycle_entry['rde_enhancement'] = {
                    'drift_similarity': rde_report.get('drift', {}).get('similarity'),
                    'drift_severity': rde_report.get('drift', {}).get('severity'),
                    'healing_needed': rde_report.get('drift', {}).get('healing_needed', False),
                    'exploration_added': rde_report.get('exploration', {}).get('added', {}),
                    'bias_score': rde_report.get('bias_analysis', {}).get('score')
                }
                # Update cycle_history with AtlasForge data
                self.mission["cycle_history"][-1] = cycle_entry
                logger.info(f"AtlasForge cycle processing: drift_similarity={rde_report.get('drift', {}).get('similarity', 'N/A'):.2%}" if rde_report.get('drift', {}).get('similarity') else "AtlasForge cycle processing complete")
            except Exception as e:
                logger.warning(f"AtlasForge cycle processing failed: {e}")

        # Analytics: Ingest transcripts mid-mission for running totals
        if ANALYTICS_AVAILABLE:
            try:
                mission_id = self.mission.get("mission_id")
                if mission_id:
                    analytics = get_analytics()
                    # Use ingest_live_transcripts to get data from live Claude directories
                    ingest_result = analytics.ingest_live_transcripts(mission_id)
                    if ingest_result.get("transcripts_processed", 0) > 0:
                        # Add analytics to cycle entry
                        cycle_entry['analytics'] = {
                            'transcripts_processed': ingest_result.get('transcripts_processed', 0),
                            'input_tokens': ingest_result.get('total_input_tokens', 0),
                            'output_tokens': ingest_result.get('total_output_tokens', 0),
                            'cost_usd': ingest_result.get('total_cost_usd', 0.0),
                            'source': ingest_result.get('source', 'unknown')
                        }
                        self.mission["cycle_history"][-1] = cycle_entry
                        logger.info(f"Analytics: Cycle {cycle_num} ingested {ingest_result['transcripts_processed']} transcripts (source: {ingest_result.get('source', 'unknown')})")
            except Exception as e:
                logger.warning(f"Analytics mid-mission ingestion failed: {e}")

        # Artifact Management: Auto-categorize and index artifacts created this cycle
        if ARTIFACT_MANAGER_AVAILABLE:
            try:
                mission_id = self.mission.get("mission_id")
                files_created = cycle_report.get('files_created', [])
                files_modified = cycle_report.get('files_modified', [])

                if mission_id and (files_created or files_modified):
                    artifact_result = run_cycle_end_artifact_processing(
                        mission_id=mission_id,
                        cycle_number=cycle_num,
                        files_created=files_created,
                        files_modified=files_modified,
                        generate_health_report=False,  # Only generate on final cycle
                    )
                    # Add artifact processing results to cycle entry
                    cycle_entry['artifact_management'] = {
                        'files_processed': artifact_result.get('files_processed', 0),
                        'files_categorized': artifact_result.get('files_categorized', 0),
                        'index_updated': artifact_result.get('index_updated', False),
                        'naming_issues': len(artifact_result.get('naming_issues', [])),
                        'archive_suggestions': len(artifact_result.get('archive_suggestions', [])),
                    }
                    self.mission["cycle_history"][-1] = cycle_entry
                    logger.info(f"Artifact Management: Cycle {cycle_num} processed {artifact_result.get('files_processed', 0)} files")
            except Exception as e:
                logger.warning(f"Artifact management processing failed: {e}")

        self.save_mission()
        logger.info(f"Saved cycle {cycle_num} to history")

    def _validate_continuation_drift(
        self,
        continuation_prompt: str,
        cycle_number: int
    ) -> tuple:
        """
        Validate continuation prompt against original mission for drift.

        This is the key checkpoint that prevents scope creep in multi-cycle missions.
        Uses an LLM-as-judge evaluation with a fresh Claude instance.

        For multi-phase missions (>=2 phases detected), uses phase-aware validation
        which compares against the ACTIVE phase rather than the full mission, preventing
        false positives when agents correctly progress through sequential phases.

        Args:
            continuation_prompt: The generated continuation prompt
            cycle_number: Current cycle number

        Returns:
            Tuple of (validated_prompt, should_halt)
            - validated_prompt: Original, warning-injected, or modified prompt
            - should_halt: True if mission should be halted due to excessive drift
        """
        if not DRIFT_VALIDATION_AVAILABLE:
            logger.debug("Drift validation not available - skipping check")
            return continuation_prompt, False

        mission_id = self.mission.get("mission_id", "unknown")
        mission_dir = Path(self.mission.get("mission_dir", ""))
        original_mission = (
            self.mission.get("original_problem_statement") or
            self.mission.get("problem_statement", "")
        )

        if not original_mission:
            logger.warning("No original mission found - skipping drift validation")
            return continuation_prompt, False

        # Load or initialize phase tracking state (if available)
        phase_state = None
        use_phase_aware = False

        if PHASE_AWARE_DRIFT_AVAILABLE:
            try:
                phase_state = load_phase_state(mission_dir)
                if phase_state is None:
                    # Initialize phase tracking from mission text
                    phase_state = initialize_phase_tracking(
                        mission_id=mission_id,
                        mission_text=original_mission,
                        mission_dir=mission_dir
                    )
                # Use phase-aware validation if multiple phases detected
                use_phase_aware = len(phase_state.phases) >= 2
                logger.info(
                    f"Phase tracking: {len(phase_state.phases)} phases detected, "
                    f"active_phase={phase_state.active_phase_id}, "
                    f"phase_aware_validation={use_phase_aware}"
                )
            except Exception as e:
                logger.warning(f"Phase tracking initialization failed: {e}")
                # Fall back to standard validation

        # Load or initialize drift tracking state
        drift_dir = mission_dir / "drift_validation" if mission_dir.name else Path("./drift_validation")
        drift_dir.mkdir(parents=True, exist_ok=True)

        tracking_state = load_tracking_state(drift_dir)
        if tracking_state is None:
            tracking_state = DriftTrackingState()

        # Perform validation
        try:
            if use_phase_aware and PHASE_AWARE_DRIFT_AVAILABLE:
                # Phase-aware validation for multi-phase missions
                validator = PhaseAwareMissionDriftValidator(
                    timeout_seconds=120,
                    failure_threshold_warn=4,
                    failure_threshold_halt=5
                )

                result, updated_state, updated_phase = validator.validate_continuation_phase_aware(
                    original_mission=original_mission,
                    continuation_prompt=continuation_prompt,
                    cycle_number=cycle_number,
                    mission_dir=mission_dir,
                    tracking_state=tracking_state,
                    phase_state=phase_state
                )

                # Update mission with phase tracking info for dashboard visibility
                active_phase = updated_phase.get_active_phase()
                self.mission["phase_tracking"] = {
                    "active_phase": active_phase.name if active_phase else None,
                    "active_phase_id": updated_phase.active_phase_id,
                    "completed_phases": [p.name for p in updated_phase.phases
                                         if p.phase_id in updated_phase.completed_phase_ids],
                    "total_phases": len(updated_phase.phases),
                    "extraction_method": updated_phase.extraction_method,
                    "phase_transition": result.phase_transition_detected if hasattr(result, 'phase_transition_detected') else False,
                    "transition_from": result.transition_from if hasattr(result, 'transition_from') else None,
                    "transition_to": result.transition_to if hasattr(result, 'transition_to') else None
                }

                # Save updated phase state
                save_phase_state(updated_phase, mission_dir)

                # Log phase-aware details
                phase_info = f", phase_aware=True, active_phase={active_phase.name if active_phase else 'None'}"
                if hasattr(result, 'phase_similarity'):
                    phase_info += f", phase_similarity={result.phase_similarity:.1%}"
                if result.phase_transition_detected if hasattr(result, 'phase_transition_detected') else False:
                    phase_info += f", transition={result.transition_from}->{result.transition_to}"

            else:
                # Standard validation for single-phase missions
                validator = MissionDriftValidator(
                    timeout_seconds=120,
                    failure_threshold_warn=4,
                    failure_threshold_halt=5
                )

                result, updated_state = validator.validate_continuation(
                    original_mission=original_mission,
                    continuation_prompt=continuation_prompt,
                    cycle_number=cycle_number,
                    tracking_state=tracking_state
                )
                phase_info = ", phase_aware=False"

            # Save validation result and state
            save_validation_result(result, drift_dir)
            save_tracking_state(updated_state, drift_dir)

            # Update mission state with drift tracking info
            if "drift_validation" not in self.mission:
                self.mission["drift_validation"] = {}
            self.mission["drift_validation"]["failure_count"] = updated_state.failure_count
            self.mission["drift_validation"]["average_similarity"] = updated_state.average_similarity
            self.mission["drift_validation"]["last_validation_cycle"] = cycle_number
            self.mission["drift_validation"]["warning_issued"] = updated_state.warning_issued
            self.mission["drift_validation"]["phase_aware"] = use_phase_aware
            self.save_mission()

            # Log the result
            logger.info(
                f"Drift validation for cycle {cycle_number}: "
                f"drift={result.drift_detected}, severity={result.drift_severity.value}, "
                f"similarity={result.semantic_similarity:.1%}, decision={result.decision.value}"
                f"{phase_info}"
            )

            # Handle decision
            if result.decision == DriftDecision.HALT:
                # Generate halt report
                recap = validator.generate_drift_recap(
                    tracking_state=updated_state,
                    original_mission=original_mission,
                    mission_id=mission_id
                )
                recap_path = drift_dir / "drift_recap.json"
                with open(recap_path, 'w') as f:
                    import json
                    json.dump(recap, f, indent=2)
                logger.error(f"Mission halted due to drift. Recap saved to {recap_path}")
                return continuation_prompt, True

            elif result.decision == DriftDecision.INJECT_WARNING:
                # Prepend warning to continuation
                if use_phase_aware and PHASE_AWARE_DRIFT_AVAILABLE and hasattr(validator, 'generate_phase_aware_warning'):
                    warning = validator.generate_phase_aware_warning(
                        result=result,
                        tracking_state=updated_state,
                        phase_state=updated_phase,
                        original_mission=original_mission
                    )
                else:
                    warning = validator.generate_warning_message(
                        result=result,
                        tracking_state=updated_state,
                        original_mission=original_mission
                    )
                warned_prompt = warning + continuation_prompt
                logger.warning(f"Drift warning injected into continuation (failure {updated_state.failure_count}/5)")
                return warned_prompt, False

            elif result.decision == DriftDecision.LOG_WARNING:
                logger.warning(
                    f"Drift detected but allowing continuation "
                    f"(failure {updated_state.failure_count}/5, severity={result.drift_severity.value})"
                )
                return continuation_prompt, False

            else:  # ALLOW
                return continuation_prompt, False

        except Exception as e:
            logger.error(f"Drift validation failed with error: {e}")
            import traceback
            logger.debug(f"Traceback: {traceback.format_exc()}")
            # On error, allow continuation (fail open)
            return continuation_prompt, False

    def _build_drift_rationale(self, drift_recap: dict) -> str:
        """Build a human-readable rationale from drift recap data."""
        parts = ["Mission was halted due to repeated drift detection."]

        # Add scope expansion info
        pattern = drift_recap.get("drift_pattern_analysis") or {}
        added_scope = pattern.get("consistently_added_scope", [])
        if added_scope:
            top_items = [item["item"] if isinstance(item, dict) else str(item) for item in added_scope[:3]]
            parts.append(f"Recurring scope expansions: {', '.join(top_items)}")

        # Add lost focus info
        lost_focus = pattern.get("consistently_lost_focus", [])
        if lost_focus:
            top_items = [item["item"] if isinstance(item, dict) else str(item) for item in lost_focus[:3]]
            parts.append(f"Deprioritized objectives: {', '.join(top_items)}")

        # Add acceleration info
        if pattern.get("drift_accelerating"):
            parts.append("Drift was accelerating, suggesting the original scope may have been too narrow.")

        # Add general recommendations
        recs = drift_recap.get("recommendations") or []
        if recs:
            parts.append(f"Recommendation: {recs[0]}")

        return " ".join(parts)

    def _generate_drift_halt_report(self, response: dict):
        """Generate a report when mission is halted due to drift."""
        mission_id = self.mission.get("mission_id", "unknown")
        mission_dir = Path(self.mission.get("mission_dir", ""))

        # Load drift recap if available
        drift_dir = mission_dir / "drift_validation" if mission_dir.name else Path("./drift_validation")
        recap_path = drift_dir / "drift_recap.json"

        drift_recap = {}
        if recap_path.exists():
            try:
                with open(recap_path, 'r') as f:
                    import json
                    drift_recap = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load drift recap: {e}")

        # Create the halt report
        halt_report = {
            "mission_id": mission_id,
            "status": "HALTED_DUE_TO_DRIFT",
            "original_mission": self.mission.get("original_problem_statement", ""),
            "halted_at_cycle": self.mission.get("current_cycle", 1),
            "cycle_budget": self.mission.get("cycle_budget", 1),
            "cycles_completed": self.mission.get("cycle_history", []),
            "drift_analysis": drift_recap,
            "halted_at": datetime.now().isoformat(),
            "recommendations": drift_recap.get("recommendations", []),
            "suggested_refined_mission": drift_recap.get("suggested_refined_mission", "")
        }

        # Save to mission_logs
        log_dir = BASE_DIR / "missions" / "mission_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        report_path = log_dir / f"{mission_id}_drift_halt.json"

        try:
            with open(report_path, 'w') as f:
                import json
                json.dump(halt_report, f, indent=2)
            logger.info(f"Drift halt report saved to {report_path}")
        except Exception as e:
            logger.error(f"Failed to save drift halt report: {e}")

        # Also save in mission directory
        if mission_dir.name:
            mission_report_path = mission_dir / "drift_halt_report.json"
            try:
                with open(mission_report_path, 'w') as f:
                    import json
                    json.dump(halt_report, f, indent=2)
            except Exception as e:
                logger.warning(f"Failed to save mission-local drift report: {e}")

        # Log history entry
        self.log_history(
            f"Mission halted due to drift after {halt_report['halted_at_cycle']} cycles",
            {
                "drift_failures": drift_recap.get("total_drift_failures", 0),
                "average_similarity": drift_recap.get("average_similarity", 0),
                "recommendations": halt_report["recommendations"][:3] if halt_report["recommendations"] else []
            }
        )

        # Generate and save a follow-up mission recommendation from drift data
        suggested_mission = drift_recap.get("suggested_refined_mission", "")
        if suggested_mission:
            original_mission = self.mission.get("original_problem_statement", "")
            mission_title = f"Refined: {original_mission[:50]}..." if len(original_mission) > 50 else f"Refined: {original_mission}"

            drift_recommendation = {
                "mission_title": mission_title,
                "mission_description": suggested_mission,
                "suggested_cycles": self.mission.get("cycle_budget", 3),
                "rationale": self._build_drift_rationale(drift_recap)
            }

            drift_context = {
                "drift_failures": drift_recap.get("total_drift_failures", 0),
                "average_similarity": drift_recap.get("average_similarity", 0),
                "halted_at_cycle": halt_report["halted_at_cycle"],
                "pattern_analysis": drift_recap.get("drift_pattern_analysis", {})
            }

            self._save_recommendation(
                recommendation=drift_recommendation,
                source_mission_id=mission_id,
                source_summary=f"Mission halted at cycle {halt_report['halted_at_cycle']} due to drift.",
                source_type="drift_halt",
                drift_context=drift_context
            )
            logger.info(f"Generated follow-up recommendation from drift-halted mission {mission_id}")

            # Auto-queue the drift suggestion if queue auto-start is enabled
            self._auto_queue_drift_suggestion(drift_recommendation, mission_id)

            # Explicitly re-emit recommendation as fallback (in case initial emit failed)
            try:
                from suggestion_storage import get_storage
                from websocket_events import emit_recommendation_added
                storage = get_storage()
                all_recs = storage.get_all()
                drift_recs = [r for r in all_recs if r.get("source_mission_id") == mission_id and r.get("source_type") == "drift_halt"]
                if drift_recs:
                    latest = max(drift_recs, key=lambda x: x.get("created_at", ""))
                    emit_recommendation_added(latest, queue_if_unavailable=True)
                    logger.info(f"Emitted drift recommendation for real-time push")
            except Exception as e:
                logger.debug(f"Could not emit drift recommendation: {e}")

        # Knowledge Base: Auto-ingest drift-halted mission for learning extraction
        if KB_AVAILABLE and report_path.exists():
            try:
                kb = get_knowledge_base()
                ingest_result = kb.ingest_completed_mission(report_path)
                learnings_count = ingest_result.get('learnings_extracted', 0)
                logger.info(f"Knowledge Base: Ingested drift-halted mission - {learnings_count} learnings extracted")
            except Exception as e:
                logger.warning(f"Knowledge Base: Failed to ingest drift-halted mission: {e}")

    def _auto_queue_drift_suggestion(self, drift_recommendation: dict, source_mission_id: str):
        """Auto-queue the drift suggestion if queue auto-start is enabled.

        This ensures that when a mission is halted due to drift, the suggested
        refined mission is automatically queued for execution, providing
        continuity in the R&D workflow.
        """
        if not QUEUE_SCHEDULER_AVAILABLE:
            logger.debug("Queue scheduler not available - skipping auto-queue")
            return

        try:
            scheduler = get_queue_scheduler()
            queue_state = scheduler.get_queue()
            settings = queue_state.settings

            # Check if auto-start is enabled
            auto_start_enabled = settings.get("auto_start", False)
            if not auto_start_enabled:
                logger.debug("Queue auto-start not enabled - suggestion saved but not queued")
                return

            # Add the drift suggestion to the queue with high priority
            mission_title = drift_recommendation.get("mission_title", "Refined Mission")
            mission_description = drift_recommendation.get("mission_description", "")
            suggested_cycles = drift_recommendation.get("suggested_cycles", 3)

            item, position = scheduler.add_to_queue(
                mission_title=mission_title,
                mission_description=mission_description,
                cycle_budget=suggested_cycles,
                priority="high",  # High priority since it's a refinement of halted work
                tags=["drift_refinement", f"from_{source_mission_id}"],
                created_by="drift_halt_auto_queue"
            )

            logger.info(
                f"Auto-queued drift suggestion at position {position}: {mission_title}"
            )

            # Write auto-start signal so dashboard's queue_auto_start_watcher starts Claude
            signal_path = STATE_DIR / "queue_auto_start_signal.json"
            auto_start_signal = {
                "triggered_at": datetime.now().isoformat(),
                "queue_item_id": item.id,
                "mission_title": mission_title,
                "source": "drift_halt_auto_queue",
                "source_mission_id": source_mission_id
            }
            io_utils.atomic_write_json(signal_path, auto_start_signal)
            logger.info("Wrote auto-start signal for drift suggestion")

        except Exception as e:
            logger.warning(f"Failed to auto-queue drift suggestion: {e}")

    def _advance_to_next_cycle(self, continuation_prompt: str):
        """Advance to the next cycle with the continuation prompt."""
        current_cycle = self.mission.get("current_cycle", 1)

        # Save original mission on first cycle
        if not self.mission.get("original_problem_statement"):
            self.mission["original_problem_statement"] = self.mission.get("problem_statement", "")

        # AtlasForge Enhancement: Apply healing to continuation prompt if drift detected
        enhanced_prompt = continuation_prompt
        enhancer = self._get_enhancer()
        if enhancer:
            try:
                # Get recent cycle output for analysis
                recent_output = ""
                if self.mission.get('cycle_history'):
                    recent_output = self.mission['cycle_history'][-1].get('summary', '')

                enhanced_prompt = enhancer.heal_continuation(
                    continuation_prompt,
                    recent_output
                )
                if enhanced_prompt != continuation_prompt:
                    logger.info("AtlasForge: Applied continuation healing due to detected drift")
                    # Store both versions for transparency
                    self.mission['rde_healing_applied'] = {
                        'cycle': current_cycle,
                        'original_prompt_preview': continuation_prompt[:200],
                        'healing_applied': True
                    }
            except Exception as e:
                logger.warning(f"AtlasForge continuation healing failed: {e}")

        # Update mission for next cycle
        self.mission["current_cycle"] = current_cycle + 1
        self.mission["problem_statement"] = enhanced_prompt
        self.mission["iteration"] = 0  # Reset iteration for new cycle
        self.mission["cycle_started_at"] = datetime.now().isoformat()

        self.save_mission()
        logger.info(f"Advanced to cycle {current_cycle + 1}")

    def _generate_final_report(self, response: dict):
        """Generate and save the final mission report with full file manifest."""
        mission_id = self.mission.get("mission_id", "unknown")
        mission_workspace = self.mission.get("mission_workspace")
        mission_dir = self.mission.get("mission_dir")

        # Ingest transcripts to populate analytics BEFORE generating report
        analytics_summary = None
        if ANALYTICS_AVAILABLE:
            try:
                analytics = get_analytics()
                # Ingest transcripts to get token usage data
                # Use ingest_live_transcripts to get data from live Claude directories
                ingest_result = analytics.ingest_live_transcripts(mission_id)
                if ingest_result.get("transcripts_processed", 0) > 0:
                    logger.info(f"Analytics: Ingested {ingest_result['transcripts_processed']} transcripts "
                               f"(source: {ingest_result.get('source', 'unknown')}), "
                               f"total tokens: {ingest_result.get('total_input_tokens', 0) + ingest_result.get('total_output_tokens', 0)}")
                # Get mission summary for including in report
                mission_metrics = analytics.get_mission_summary(mission_id)
                if mission_metrics:
                    analytics_summary = {
                        "total_tokens": mission_metrics.total_tokens,
                        "input_tokens": mission_metrics.total_input_tokens,
                        "output_tokens": mission_metrics.total_output_tokens,
                        "cache_read_tokens": mission_metrics.total_cache_read_tokens,
                        "cache_write_tokens": mission_metrics.total_cache_write_tokens,
                        "estimated_cost_usd": mission_metrics.total_estimated_cost_usd,
                        "duration_seconds": mission_metrics.total_duration_seconds
                    }
            except Exception as e:
                logger.warning(f"Analytics ingestion failed: {e}")

        # Count building iterations as proxy for effort
        building_count = len([h for h in self.mission.get("history", []) if h.get("stage") == "BUILDING"])

        final_report = {
            "mission_id": mission_id,
            "original_mission": self.mission.get("original_problem_statement") or self.mission.get("problem_statement", ""),
            "total_cycles": self.mission.get("cycle_budget", 1),
            "current_cycle_completed": self.mission.get("current_cycle", 1),
            "total_iterations": building_count,
            "started_at": self.mission.get("created_at"),
            "completed_at": datetime.now().isoformat(),
            "cycles": self.mission.get("cycle_history", []),
            "final_summary": response.get("final_report", {}).get("summary", response.get("summary", "")),
            "all_files": [],
            "file_manifest": [],  # Enhanced file info
            "deliverables": response.get("deliverables", []),
            "mission_workspace": mission_workspace,
            "mission_dir": mission_dir,
            "analytics": analytics_summary  # Token usage and cost analytics
        }

        # Collect all files from all cycles
        for cycle in final_report["cycles"]:
            final_report["all_files"].extend(cycle.get("files_generated", []))
        final_report["all_files"] = list(set(final_report["all_files"]))  # Dedupe

        # Generate detailed file manifest from mission workspace
        if mission_workspace:
            workspace_path = Path(mission_workspace)
            if workspace_path.exists():
                for f in workspace_path.rglob("*"):
                    if f.is_file():
                        try:
                            stat = f.stat()
                            final_report["file_manifest"].append({
                                "path": str(f.relative_to(workspace_path)),
                                "full_path": str(f),
                                "size_bytes": stat.st_size,
                                "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                                "file_type": f.suffix or "unknown"
                            })
                        except (OSError, IOError) as e:
                            logger.warning(f"Could not stat file {f}: {e}")

        # Also scan the global workspace for any files
        for f in WORKSPACE_DIR.rglob("*"):
            if f.is_file():
                try:
                    stat = f.stat()
                    # Check if this file was modified during the mission
                    file_mtime = datetime.fromtimestamp(stat.st_mtime)
                    mission_start = datetime.fromisoformat(self.mission.get("created_at", datetime.now().isoformat()))
                    if file_mtime >= mission_start:
                        final_report["file_manifest"].append({
                            "path": str(f.relative_to(WORKSPACE_DIR)),
                            "full_path": str(f),
                            "size_bytes": stat.st_size,
                            "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                            "modified_at": file_mtime.isoformat(),
                            "file_type": f.suffix or "unknown",
                            "location": "global_workspace"
                        })
                except (OSError, IOError):
                    pass

        # Calculate summary statistics
        final_report["statistics"] = {
            "total_files": len(final_report["file_manifest"]),
            "total_size_bytes": sum(f.get("size_bytes", 0) for f in final_report["file_manifest"]),
            "file_types": {},
            "history_entries": len(self.mission.get("history", [])),
            "stages_traversed": list(set(h.get("stage") for h in self.mission.get("history", [])))
        }

        # Count file types
        for f in final_report["file_manifest"]:
            ftype = f.get("file_type", "unknown")
            final_report["statistics"]["file_types"][ftype] = final_report["statistics"]["file_types"].get(ftype, 0) + 1

        # Save to mission_logs
        report_path = MISSION_LOGS_DIR / f"{mission_id}_report.json"
        with open(report_path, 'w') as f:
            json.dump(final_report, f, indent=2)

        logger.info(f"Saved final mission report to {report_path}")

        # Also save a copy to the mission directory if it exists
        if mission_dir:
            mission_report_path = Path(mission_dir) / "final_report.json"
            try:
                with open(mission_report_path, 'w') as f:
                    json.dump(final_report, f, indent=2)
                logger.info(f"Saved mission report copy to {mission_report_path}")
            except Exception as e:
                logger.warning(f"Could not save mission report to mission dir: {e}")

        # Also log to mission history
        self.log_history(
            f"Final mission report generated",
            {
                "report_path": str(report_path),
                "total_cycles": final_report["total_cycles"],
                "total_files": final_report["statistics"]["total_files"]
            }
        )

        # Knowledge Base: Auto-ingest completed mission for learning extraction
        if KB_AVAILABLE:
            try:
                kb = get_knowledge_base()
                ingest_result = kb.ingest_completed_mission(report_path)
                learnings_count = ingest_result.get('learnings_extracted', 0)
                logger.info(f"Knowledge Base: Ingested mission - {learnings_count} learnings extracted")
                self.log_history(
                    f"Knowledge Base ingested mission",
                    {"learnings_extracted": learnings_count}
                )
            except Exception as e:
                logger.warning(f"Knowledge Base: Failed to ingest mission: {e}")

        # Save next mission recommendation if provided
        next_rec = response.get("next_mission_recommendation")
        if next_rec:
            self._save_recommendation(next_rec, mission_id, final_report.get("final_summary", ""))

    def _save_recommendation(
        self,
        recommendation: dict,
        source_mission_id: str,
        source_summary: str,
        source_type: str = "successful_completion",
        drift_context: dict = None
    ):
        """Save a mission recommendation to the storage backend (SQLite preferred, JSON fallback).

        Args:
            recommendation: Dict with mission_title, mission_description, suggested_cycles, rationale
            source_mission_id: The mission that generated this recommendation
            source_summary: Brief summary of the source mission
            source_type: "successful_completion" or "drift_halt"
            drift_context: Optional dict with drift analysis data (for drift_halt recommendations)
        """
        import uuid

        rec_entry = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": recommendation.get("mission_title", "Untitled Mission"),
            "mission_description": recommendation.get("mission_description", ""),
            "suggested_cycles": recommendation.get("suggested_cycles", 3),
            "source_mission_id": source_mission_id,
            "source_mission_summary": source_summary[:500] if source_summary else "",
            "rationale": recommendation.get("rationale", ""),
            "created_at": datetime.now().isoformat(),
            "source_type": source_type
        }

        # Add drift context if provided (for drift-halted missions)
        if drift_context:
            rec_entry["drift_context"] = drift_context

        # Save to SQLite storage (single source of truth)
        try:
            from suggestion_storage import get_storage
            storage = get_storage()
            storage.add(rec_entry)
            logger.info(f"Saved mission recommendation to SQLite ({source_type}): {rec_entry['mission_title']}")
        except Exception as e:
            logger.error(f"SQLite save failed: {e}")
            return

        # Emit WebSocket event for new recommendation
        try:
            from websocket_events import emit_recommendation_added
            emit_recommendation_added(rec_entry)
        except ImportError:
            pass

    def _emit_latest_recommendation_on_complete(self):
        """
        Emit WebSocket event for the latest recommendation on mission completion.

        This acts as a fallback to ensure recommendations are pushed in real-time
        even if the initial emit in _save_recommendation failed (e.g., socketio
        wasn't available). Called explicitly when transitioning to COMPLETE stage.
        """
        mission_id = self.mission.get("mission_id")
        if not mission_id:
            return

        try:
            from suggestion_storage import get_storage
            storage = get_storage()

            # Get all recommendations from this mission
            all_recs = storage.get_all()
            mission_recs = [r for r in all_recs if r.get("source_mission_id") == mission_id]

            if mission_recs:
                # Find the most recently created recommendation from this mission
                latest = max(mission_recs, key=lambda x: x.get("created_at", ""))

                from websocket_events import emit_recommendation_added
                emit_recommendation_added(latest, queue_if_unavailable=True)
                logger.info(f"Emitted recommendation on COMPLETE: {latest.get('mission_title')}")
        except Exception as e:
            logger.debug(f"Could not emit recommendation on complete: {e}")

    def reset_mission(self):
        """Reset mission to initial state (keeps problem statement)."""
        problem = self.mission.get("problem_statement", "No mission defined.")
        prefs = self.mission.get("preferences", {})

        self.mission = {
            "problem_statement": problem,
            "preferences": prefs,
            "current_stage": "PLANNING",
            "iteration": 0,
            "history": [],
            "created_at": datetime.now().isoformat(),
            "reset_at": datetime.now().isoformat()
        }
        self.save_mission()
        logger.info("Mission reset to PLANNING")

    def set_mission(self, problem_statement: str, preferences: dict = None,
                    success_criteria: list = None, mission_id: str = None,
                    cycle_budget: int = 1, project_name: str = None):
        """Set a new mission with optional cycle budget for multi-cycle execution.

        If PROJECT_NAME_RESOLVER_AVAILABLE, workspace is created under workspace/<project_name>/
        to enable workspace sharing across missions working on the same project.
        Otherwise falls back to missions/mission_<UUID>/workspace/ for backwards compatibility.
        """
        import uuid

        # Generate mission ID
        mid = mission_id or f"mission_{uuid.uuid4().hex[:8]}"

        # Resolve project name for shared workspace
        resolved_project_name = None
        if PROJECT_NAME_RESOLVER_AVAILABLE:
            resolved_project_name = resolve_project_name(problem_statement, mid, project_name)
            # Use shared workspace under workspace/<project_name>/
            mission_workspace = WORKSPACE_DIR / resolved_project_name
            logger.info(f"Resolved project name: {resolved_project_name}")
        else:
            # Legacy: per-mission workspace
            mission_workspace = MISSIONS_DIR / mid / "workspace"

        # Create mission directory (for config, analytics, drift validation)
        mission_dir = MISSIONS_DIR / mid
        mission_dir.mkdir(parents=True, exist_ok=True)

        # Create workspace directories (may already exist if shared project)
        (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

        logger.info(f"Mission workspace at {mission_workspace}")

        self.mission = {
            "mission_id": mid,
            "problem_statement": problem_statement,
            "original_problem_statement": problem_statement,  # Keep root mission
            "preferences": preferences or {},
            "success_criteria": success_criteria or [],
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            "cycle_started_at": datetime.now().isoformat(),
            # Cycle iteration fields
            "cycle_budget": max(1, cycle_budget),  # Minimum 1 cycle
            "current_cycle": 1,
            "cycle_history": [],
            # Mission workspace path
            "mission_workspace": str(mission_workspace),
            "mission_dir": str(mission_dir),
            # Project name for workspace deduplication
            "project_name": resolved_project_name,
            "metadata": {}
        }
        self.save_mission()

        # Also save a copy of the mission config in the mission directory
        mission_config_path = mission_dir / "mission_config.json"
        config_data = {
            "mission_id": mid,
            "problem_statement": problem_statement,
            "cycle_budget": max(1, cycle_budget),
            "created_at": self.mission["created_at"]
        }
        if resolved_project_name:
            config_data["project_name"] = resolved_project_name
            config_data["project_workspace"] = str(mission_workspace)
        with open(mission_config_path, 'w') as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"New mission set with {cycle_budget} cycles: {problem_statement[:100]}...")

        # AtlasForge Enhancement: Set baseline fingerprint for mission continuity tracking
        # Clear any existing enhancer to force re-initialization with new mission
        if hasattr(self, '_enhancer'):
            self._enhancer = None
        enhancer = self._get_enhancer()
        if enhancer:
            try:
                enhancer.set_mission_baseline(problem_statement, source="initial_mission")
                logger.info("AtlasForge baseline fingerprint set for mission continuity tracking")
            except Exception as e:
                logger.warning(f"Failed to set AtlasForge baseline fingerprint: {e}")

        # Analytics: Track mission start
        if ANALYTICS_AVAILABLE:
            try:
                analytics = get_analytics()
                analytics.start_mission(mid, problem_statement)
                # Also track the initial PLANNING stage start
                analytics.start_stage(mid, "PLANNING", iteration=0, cycle=1)
                logger.info(f"Analytics: Started tracking mission {mid}")
            except Exception as e:
                logger.warning(f"Analytics: Failed to start mission tracking: {e}")

        # Real-time token watcher: Start watching for the new mission
        if TOKEN_WATCHER_AVAILABLE:
            try:
                workspace = self.mission.get('mission_workspace')
                success = start_watching_mission(mid, workspace, stage="PLANNING")
                if success:
                    logger.info(f"Token watcher: Started real-time monitoring for {mid}")
                else:
                    logger.debug(f"Token watcher: Could not start (no transcript dir yet)")
            except Exception as e:
                logger.debug(f"Token watcher: Failed to start: {e}")

    def load_mission_from_file(self, filepath: Path):
        """Load a mission from a template file."""
        template = io_utils.atomic_read_json(filepath, {})
        if template and template.get("problem_statement"):
            # Reset to PLANNING stage
            template["current_stage"] = "PLANNING"
            template["iteration"] = 0
            template["history"] = []
            template["created_at"] = datetime.now().isoformat()
            self.mission = template
            self.save_mission()
            logger.info(f"Loaded mission from {filepath}")
            return True
        return False


# Convenience functions for external use
def get_current_stage() -> str:
    """Get current R&D stage."""
    controller = RDMissionController()
    return controller.mission.get("current_stage", "PLANNING")

def get_mission_status() -> dict:
    """Get full mission status including cycle information."""
    controller = RDMissionController()
    return {
        "stage": controller.mission.get("current_stage"),
        "iteration": controller.mission.get("iteration", 0),
        "problem": controller.mission.get("problem_statement", "")[:200],
        "last_updated": controller.mission.get("last_updated"),
        "history_count": len(controller.mission.get("history", [])),
        # Cycle information
        "current_cycle": controller.mission.get("current_cycle", 1),
        "cycle_budget": controller.mission.get("cycle_budget", 1),
        "original_mission": controller.mission.get("original_problem_statement", "")[:200]
    }
