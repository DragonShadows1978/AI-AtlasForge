#!/usr/bin/env python3
"""
AtlasForge Conductor v3.0 - Mission Orchestration Engine

The main orchestrator for AtlasForge missions, conducting the execution flow
across stages and cycles with model-agnostic LLM integration.

Modes:
    --mode=rd     : Run in R&D mode (stage-based mission execution)
    --mode=free   : Run in free exploration mode (original behavior)
    (default)     : R&D mode

Usage:
    python3 atlasforge_conductor.py --mode=rd
    python3 atlasforge_conductor.py --mode=free
"""

import json
import subprocess
import sys
import re
import time
import signal
import logging
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List

# Import local modules
import io_utils
import af_engine as atlasforge_engine

# Anthropic SDK for Haiku-powered handoff summaries
try:
    import anthropic
    HAS_ANTHROPIC_SDK = True
except ImportError:
    HAS_ANTHROPIC_SDK = False
    anthropic = None

# ContextWatcher for early handoff on context exhaustion
try:
    from context_watcher import (
        get_context_watcher,
        HandoffSignal,
        HandoffLevel,
        write_handoff_state,
        TIME_BASED_HANDOFF_ENABLED,
    )
    HAS_CONTEXT_WATCHER = True
except ImportError:
    HAS_CONTEXT_WATCHER = False
    get_context_watcher = None
    HandoffSignal = None
    HandoffLevel = None
    write_handoff_state = None
    TIME_BASED_HANDOFF_ENABLED = False

# =============================================================================
# CONFIGURATION
# =============================================================================

# Determine BASE_DIR from script location or environment variable
_SCRIPT_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("ATLASFORGE_ROOT", str(_SCRIPT_DIR)))
STATE_DIR = BASE_DIR / "state"
WORKSPACE_DIR = BASE_DIR / "workspace"
LOG_DIR = BASE_DIR / "logs"

# State files
CLAUDE_STATE_PATH = STATE_DIR / "claude_state.json"
CLAUDE_MEMORY_PATH = STATE_DIR / "claude_memory.json"
CLAUDE_JOURNAL_PATH = STATE_DIR / "claude_journal.jsonl"
CLAUDE_PROMPT_PATH = STATE_DIR / "claude_prompt.json"
CHAT_HISTORY_PATH = STATE_DIR / "chat_history.json"
PID_PATH = BASE_DIR / "atlasforge_conductor.pid"

# Ensure directories exist
STATE_DIR.mkdir(exist_ok=True)
WORKSPACE_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
(WORKSPACE_DIR / "artifacts").mkdir(exist_ok=True)
(WORKSPACE_DIR / "research").mkdir(exist_ok=True)
(WORKSPACE_DIR / "tests").mkdir(exist_ok=True)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_DIR / "atlasforge_conductor.log")
    ],
    force=True  # Prevent duplicate handlers on reimport
)
logger = logging.getLogger("atlasforge_conductor")

# Global state
running = True

# Maximum retries when Claude times out or fails to respond
MAX_CLAUDE_RETRIES = 3


def signal_handler(signum, frame):
    global running
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    running = False


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)


# =============================================================================
# STATE MANAGEMENT
# =============================================================================

def load_state() -> dict:
    """Load Claude's persistent state (operational)."""
    return io_utils.atomic_read_json(CLAUDE_STATE_PATH, {
        "mode": "rd",
        "boot_count": 0,
        "total_cycles": 0,
        "current_project": None,
        "current_task": None,
        "task_queue": [],
        "completed_tasks": [],
        "last_boot": None,
        "last_thought": None
    })


def save_state(state: dict):
    """Save Claude's persistent state."""
    io_utils.atomic_write_json(CLAUDE_STATE_PATH, state)


def load_memory() -> dict:
    """Load Claude's long-term memory/knowledge."""
    return io_utils.atomic_read_json(CLAUDE_MEMORY_PATH, {
        "facts_learned": [],
        "projects_completed": [],
        "files_created": [],
        "insights": [],
        "mission_history": []
    })


def save_memory(memory: dict):
    """Save Claude's long-term memory."""
    io_utils.atomic_write_json(CLAUDE_MEMORY_PATH, memory)


def add_to_memory(key: str, value: str, max_items: int = 100):
    """Add an item to a memory list, keeping it bounded."""
    def update_fn(memory):
        if key not in memory:
            memory[key] = []
        memory[key].append({
            "content": value,
            "timestamp": datetime.now().isoformat()
        })
        # Keep bounded
        if len(memory[key]) > max_items:
            memory[key] = memory[key][-max_items:]
        return memory

    io_utils.atomic_update_json(CLAUDE_MEMORY_PATH, update_fn, {})


def append_journal(entry: dict):
    """Append to Claude's thought journal."""
    entry["timestamp"] = datetime.now().isoformat()
    with open(CLAUDE_JOURNAL_PATH, 'a') as f:
        f.write(json.dumps(entry) + "\n")


def send_to_chat(message: str):
    """Send a message to the chat history for UI display."""
    def update_history(history):
        if not isinstance(history, list):
            history = []
        history.append({
            "role": "claude",
            "content": message,
            "timestamp": datetime.now().isoformat()
        })
        if len(history) > 500:
            history = history[-500:]
        return history

    io_utils.atomic_update_json(CHAT_HISTORY_PATH, update_history, [])
    logger.info(f"Chat: {message[:100]}...")


def check_human_message() -> Optional[dict]:
    """Check if human sent a message."""
    prompt_data = io_utils.atomic_read_json(CLAUDE_PROMPT_PATH, {})
    if prompt_data.get("pending"):
        return prompt_data
    return None


def clear_human_message():
    """Clear the pending human message."""
    io_utils.atomic_write_json(CLAUDE_PROMPT_PATH, {
        "pending": False, "prompt": "", "from": "", "timestamp": ""
    })


def save_pid():
    with open(PID_PATH, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid():
    if PID_PATH.exists():
        PID_PATH.unlink()


# =============================================================================
# LLM INVOCATION (Model-Agnostic)
# =============================================================================

def invoke_llm(prompt: str, timeout: int = 1200, cwd: Path = None) -> tuple[Optional[str], Optional[str]]:
    """
    Invoke configured LLM and get response.

    Currently uses Claude CLI, but designed to support multiple providers
    (Claude, DeepSeek, etc.) via configuration.

    Args:
        prompt: The prompt to send
        timeout: Timeout in seconds (default 20 min)
        cwd: Working directory (default BASE_DIR)

    Returns:
        Tuple of (response_text, error_info):
        - On success: (response_text, None)
        - On timeout: (None, "timeout:<seconds>")
        - On CLI error: (None, "cli_error:<stderr_snippet>")
        - On exception: (None, "exception:<error_message>")
    """
    if cwd is None:
        cwd = BASE_DIR

    try:
        logger.info(f"Invoking Claude: {prompt[:100]}...")

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions",
             "--disallowedTools", "EnterPlanMode,ExitPlanMode"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd),
            start_new_session=True  # Prevent FD inheritance blocking from background processes
        )

        if result.returncode == 0:
            response = result.stdout.strip()
            logger.info(f"Claude responded: {response[:200]}...")
            return response, None
        else:
            stderr_snippet = result.stderr[:500] if result.stderr else "No stderr"
            logger.error(f"Claude error: {result.stderr}")
            return None, f"cli_error:{stderr_snippet}"

    except subprocess.TimeoutExpired:
        logger.error(f"Claude timed out after {timeout}s")
        return None, f"timeout:{timeout}s"
    except Exception as e:
        logger.error(f"Error invoking Claude: {e}")
        return None, f"exception:{str(e)}"


def extract_json_from_response(text: str) -> Optional[dict]:
    """
    Extract JSON from Claude's response.
    Handles both clean JSON and JSON embedded in markdown.
    """
    if not text:
        return None

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON in markdown code block
    json_match = re.search(r'```json\s*(\{[\s\S]*?\})\s*```', text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find any JSON object
    json_match = re.search(r'(\{[\s\S]*\})', text, re.DOTALL)
    if json_match:
        json_str = json_match.group(1)
        # Clean up common issues
        json_str = re.sub(r',\s*\}', '}', json_str)  # trailing comma
        json_str = re.sub(r',\s*\]', ']', json_str)  # trailing comma in array
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass

    logger.warning(f"Could not extract JSON from response: {text[:200]}...")
    return None


# =============================================================================
# HAIKU-POWERED HANDOFF SUMMARIES
# =============================================================================

HAIKU_MODEL = "claude-3-haiku-20240307"
HAIKU_TIMEOUT = 10  # seconds
HAIKU_MAX_TOKENS = 500

HAIKU_HANDOFF_PROMPT = """You are generating a handoff summary for a Claude session that is ending due to context limits. Summarize what was being worked on concisely.

Mission: {mission_id}
Stage: {stage}
Recent activity context:
{recent_context}

Format your response EXACTLY as:
**Working on:** [what was being built/fixed - one line]
**Completed:** [what was finished - one line]
**In progress:** [what was partially done - one line]
**Next:** [immediate next steps - one line]
**Decisions:** [key decisions made - one line]

Be concise. Each line should be under 100 characters."""


def invoke_haiku_summary(
    mission_id: str,
    stage: str,
    recent_context: str,
    timeout: int = HAIKU_TIMEOUT
) -> Optional[str]:
    """
    Invoke Claude Haiku to generate an intelligent handoff summary.

    Uses the Claude CLI with --model flag to leverage subscription instead of API credits.

    Args:
        mission_id: Current mission ID
        stage: Current stage (BUILDING, TESTING, etc.)
        recent_context: Recent activity context (last messages, files modified, etc.)
        timeout: API call timeout in seconds

    Returns:
        Formatted summary string, or None on failure
    """
    try:
        prompt = HAIKU_HANDOFF_PROMPT.format(
            mission_id=mission_id,
            stage=stage,
            recent_context=recent_context or "No recent activity context available."
        )

        result = subprocess.run(
            ["claude", "-p", "--model", HAIKU_MODEL, "--dangerously-skip-permissions",
             "--disallowedTools", "EnterPlanMode,ExitPlanMode"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            start_new_session=True
        )

        if result.returncode == 0:
            summary = result.stdout.strip()
            if summary:
                logger.info(f"Haiku generated handoff summary ({len(summary)} chars)")
                return summary
            else:
                logger.warning("Haiku returned empty response")
                return None
        else:
            logger.error(f"Haiku CLI error: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.warning(f"Haiku CLI call timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"Error invoking Haiku: {e}")
        return None


def get_recent_chat_context(n_messages: int = 5) -> str:
    """
    Get recent chat history as context for Haiku.

    Args:
        n_messages: Number of recent messages to include

    Returns:
        Formatted string of recent messages
    """
    try:
        history = io_utils.atomic_read_json(CHAT_HISTORY_PATH, [])
        if not history:
            return "No recent messages."

        # Get last n messages
        recent = history[-n_messages:]
        lines = []
        for msg in recent:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:200]  # Truncate long messages
            lines.append(f"[{role}] {content}")

        return "\n".join(lines)
    except Exception as e:
        logger.debug(f"Error getting chat context: {e}")
        return "No recent messages available."


# =============================================================================
# R&D MODE
# =============================================================================

# =============================================================================
# AUTO-ADVANCE SIGNALING PROTOCOL
# =============================================================================
#
# The auto-advance mechanism allows missions to automatically chain together.
# When a mission completes, the dashboard's _auto_advance_narrative() function
# creates the next mission and signals completion via a file-based IPC protocol.
#
# SIGNALING FLOW:
# ┌─────────────────────┐      ┌─────────────────────┐      ┌─────────────────────┐
# │  claude_autonomous  │      │   Signal File       │      │   Dashboard         │
# │  (main loop)        │      │ (auto_advance_      │      │ (_auto_advance_     │
# │                     │      │  signal.json)       │      │  narrative)         │
# └─────────┬───────────┘      └──────────┬──────────┘      └──────────┬──────────┘
#           │                             │                            │
#           │ Mission completes           │                            │
#           │─────────────────────────────│───────────────────────────>│
#           │                             │                            │
#           │                             │   Write {status:           │
#           │                             │<──"in_progress"}───────────│
#           │                             │                            │
#           │ Poll signal file            │                            │
#           │────────────────────────────>│                            │
#           │<────{status:"in_progress"}──│                            │
#           │                             │                            │
#           │ Sleep (exponential backoff) │   (HTTP call to            │
#           │                             │    create mission)         │
#           │                             │                            │
#           │                             │   Write {status:           │
#           │                             │<──"complete",              │
#           │                             │    new_mission_id}─────────│
#           │                             │                            │
#           │ Poll signal file            │                            │
#           │────────────────────────────>│                            │
#           │<────{status:"complete"}─────│                            │
#           │                             │                            │
#           │ Reload mission.json         │                            │
#           │ Clear signal file           │                            │
#           │ Continue with new mission   │                            │
#           │                             │                            │
#
# SIGNAL FILE STATES:
#   - {status: "in_progress"}: Dashboard is creating the next mission
#   - {status: "complete", new_mission_id: "..."}: Mission created successfully
#   - {status: "error", error: "..."}: Mission creation failed
#   - {} or missing: No auto-advance in progress
#
# GRACEFUL DEGRADATION:
#   If the signal file mechanism fails (corrupted, permissions, etc.),
#   the retry loop falls back to directly polling mission.json with
#   longer intervals. This ensures missions continue even if IPC breaks.
#
# =============================================================================

# Path to auto-advance signal file (file-based IPC)
AUTO_ADVANCE_SIGNAL_PATH = STATE_DIR / "auto_advance_signal.json"

# Path to queue auto-start signal file (from queue processing)
QUEUE_AUTO_START_SIGNAL_PATH = STATE_DIR / "queue_auto_start_signal.json"

# Path to retry metrics log for long-term analysis
RETRY_METRICS_LOG_PATH = LOG_DIR / "auto_advance_metrics.jsonl"


def _log_retry_metrics(metrics: dict, completed_mission_id: str) -> None:
    """Log retry metrics to a JSONL file for long-term analysis.

    This enables monitoring patterns like:
    - Average retry counts (>2 indicates slow network)
    - Signal detection rate (low rate indicates IPC issues)
    - Common failure reasons

    Args:
        metrics: The metrics dict from _wait_for_new_mission_with_retry()
        completed_mission_id: The ID of the mission that just completed

    Note:
        Failures to write are silently ignored to avoid disrupting mission flow.
    """
    try:
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "completed_mission_id": completed_mission_id,
            **metrics
        }
        with open(RETRY_METRICS_LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception:
        # Silent failure - metrics logging should never disrupt mission flow
        pass


def _is_valid_mission(mission: dict) -> bool:
    """Validate that a mission dict is not empty and has required fields.

    This addresses the edge case where {} results in should_continue=True,
    which would cause the main loop to spin indefinitely on an invalid mission.

    Validation Rules:
    1. Mission dict must not be None, empty, or falsy
    2. Must have a non-empty mission_id field
    3. Must have a meaningful problem_statement (not placeholder text)
    4. Whitespace-only problem statements are rejected via .strip()

    Args:
        mission: The mission dict to validate, typically loaded from mission.json

    Returns:
        True if mission is valid and can be executed, False otherwise

    Examples:
        >>> _is_valid_mission({})
        False
        >>> _is_valid_mission({"mission_id": "m1"})
        False  # Missing problem_statement
        >>> _is_valid_mission({"mission_id": "m1", "problem_statement": "  "})
        False  # Whitespace-only problem
        >>> _is_valid_mission({"mission_id": "m1", "problem_statement": "Fix bug"})
        True
    """
    # Rule 1: Reject None, empty dict, or falsy values
    if not mission:
        return False

    # Rule 2: Check for required mission_id field
    if not mission.get("mission_id"):
        return False

    # Rule 3 & 4: Check for meaningful problem statement (not just placeholder)
    # Use .strip() to handle whitespace-only strings like "   \n\t  "
    problem = mission.get("problem_statement", "")
    if not problem or not problem.strip() or problem.strip() == "No mission defined. Please set a mission.":
        return False

    # Redundant but explicit check for empty dict
    if mission == {}:
        return False

    return True


def _calculate_backoff_interval(attempt: int, base_interval: float = 1.0) -> float:
    """Calculate exponential backoff interval for retry attempts.

    Uses exponential backoff (1s, 2s, 4s, ...) to handle intermittent network
    issues gracefully. This reduces hammering on slow endpoints while still
    detecting fast auto-advances quickly.

    Args:
        attempt: Zero-indexed attempt number (0 = first attempt)
        base_interval: Base interval in seconds (default 1.0)

    Returns:
        Sleep duration in seconds for this attempt

    Formula:
        interval = base_interval * (2 ^ attempt)

    Examples:
        >>> _calculate_backoff_interval(0)  # First attempt
        1.0
        >>> _calculate_backoff_interval(1)  # Second attempt
        2.0
        >>> _calculate_backoff_interval(2)  # Third attempt
        4.0
        >>> _calculate_backoff_interval(3)  # Fourth attempt
        8.0
    """
    return base_interval * (2 ** attempt)


def _wait_for_new_mission_with_retry(
    controller,
    completed_mission_id: str,
    max_retries: int = 3,
    base_interval: float = 1.0,
    max_total_wait: float = None,
    use_exponential_backoff: bool = True
) -> tuple:
    """Wait for a new mission using retry loop with file-based signaling.

    This robustly handles the case where auto-advance HTTP calls take longer
    than expected by implementing a multi-layered detection strategy:

    DETECTION LAYERS:
    1. **Signal File (Primary)**: Check auto_advance_signal.json for completion
       - Fastest detection when dashboard signals correctly
       - Handles in_progress/complete/error states

    1B. **Queue Signal File**: Check queue_auto_start_signal.json for queue missions
       - Detects missions started from the mission queue
       - Written by af_engine._create_mission_from_queue_item()

    2. **Mission File (Fallback)**: Poll mission.json for changes
       - Works even if signal file mechanism fails
       - Detects direct mission changes without signaling

    3. **Exponential Backoff**: 1s -> 2s -> 4s intervals
       - Reduces load on slow networks
       - Still detects fast auto-advances on first poll

    4. **Max Total Wait**: Hard timeout regardless of retry count
       - Ensures time-bounded behavior
       - Prevents infinite waiting on broken setups

    GRACEFUL DEGRADATION:
    If the signal file cannot be read (corrupted, permissions), the function
    falls back to pure mission.json polling without failing. This ensures
    mission continuity even when the IPC mechanism breaks.

    Args:
        controller: The RDMissionController instance with load_mission() method
        completed_mission_id: The mission ID that just completed
        max_retries: Maximum number of retry attempts (default 3)
        base_interval: Base seconds between retries for exponential backoff (default 1.0)
        max_total_wait: Maximum total seconds to wait regardless of retries.
                       If None, calculated from backoff: sum(2^i for i in range(max_retries))
        use_exponential_backoff: Use exponential backoff (True) or fixed intervals (False)

    Returns:
        Tuple of (success: bool, metrics: dict) where metrics contains:
        - attempts: number of retry attempts made
        - total_wait_time: actual time spent waiting in seconds
        - signal_detected: whether auto-advance signal file was detected
        - reason: why the function returned:
            - "success": New valid mission detected
            - "timeout": max_total_wait exceeded
            - "max_retries": All retry attempts exhausted
            - "error": Auto-advance signaled an error
            - "signal_fallback": Signal mechanism failed, using fallback
        - backoff_intervals: list of intervals used (for debugging)
        - fallback_used: True if signal file read failed

    Example:
        >>> success, metrics = _wait_for_new_mission_with_retry(
        ...     controller, "mission_123",
        ...     max_retries=3, base_interval=1.0
        ... )
        >>> if success:
        ...     print(f"New mission after {metrics['attempts']} attempts")
        ... else:
        ...     print(f"Failed: {metrics['reason']}")
    """
    import time as time_module
    start_time = time_module.time()

    # Calculate effective max_total_wait from exponential backoff sum
    # For 3 retries with base 1.0: 1 + 2 + 4 = 7s + 1s buffer = 8s
    if max_total_wait is None:
        if use_exponential_backoff:
            max_total_wait = sum(_calculate_backoff_interval(i, base_interval)
                                 for i in range(max_retries)) + 1.0
        else:
            max_total_wait = max_retries * base_interval + 0.5

    # Initialize metrics tracking
    metrics = {
        "attempts": 0,
        "total_wait_time": 0.0,
        "signal_detected": False,
        "reason": "unknown",
        "backoff_intervals": [],
        "fallback_used": False
    }

    logger.info(f"Checking for new mission (completed: {completed_mission_id})...")

    for attempt in range(max_retries):
        metrics["attempts"] = attempt + 1
        elapsed = time_module.time() - start_time
        metrics["total_wait_time"] = elapsed

        # === HARD TIMEOUT CHECK ===
        # Ensures time-bounded behavior regardless of retry logic
        if elapsed >= max_total_wait:
            metrics["reason"] = "timeout"
            logger.info(f"Max total wait time ({max_total_wait}s) exceeded after {attempt} attempts")
            return False, metrics

        # === LAYER 1: Signal File Detection ===
        # Check auto_advance_signal.json for IPC from dashboard
        try:
            signal = io_utils.atomic_read_json(AUTO_ADVANCE_SIGNAL_PATH, {})
            signal_status = signal.get("status")
        except Exception as e:
            # Graceful degradation: signal file read failed
            logger.debug(f"Signal file read failed: {e}, using fallback")
            metrics["fallback_used"] = True
            signal_status = None

        if signal_status == "in_progress":
            # Dashboard is still creating the next mission
            metrics["signal_detected"] = True
            logger.info(f"Auto-advance in progress, waiting... (attempt {attempt + 1}/{max_retries})")

        elif signal_status == "complete":
            # Dashboard finished creating the next mission
            metrics["signal_detected"] = True
            new_mission_id_from_signal = signal.get("new_mission_id")
            if new_mission_id_from_signal:
                logger.info(f"Signal indicates new mission: {new_mission_id_from_signal}")
                # Clear the signal file to prevent stale reads
                _clear_signal_file()
                # Reload and validate the new mission
                controller.mission = controller.load_mission()
                if _is_valid_mission(controller.mission):
                    metrics["reason"] = "success"
                    metrics["total_wait_time"] = time_module.time() - start_time
                    return True, metrics

        elif signal_status == "error":
            # Dashboard reported an error during auto-advance
            metrics["signal_detected"] = True
            error = signal.get("error", "Unknown error")
            logger.warning(f"Auto-advance error: {error}")
            metrics["reason"] = "error"
            _clear_signal_file()
            # Fall through to check mission.json directly as fallback

        # === LAYER 1B: Queue Auto-Start Signal Detection ===
        # Check queue_auto_start_signal.json for missions started from queue
        try:
            queue_signal = io_utils.atomic_read_json(QUEUE_AUTO_START_SIGNAL_PATH, {})
            if queue_signal.get("action") == "start_rd":
                queue_mission_id = queue_signal.get("mission_id")
                if queue_mission_id and queue_mission_id != completed_mission_id:
                    logger.info(f"Queue auto-start signal detected for: {queue_mission_id}")
                    metrics["signal_detected"] = True
                    # Reload mission.json to verify the queue-created mission exists
                    controller.mission = controller.load_mission()
                    loaded_mission_id = controller.mission.get("mission_id")
                    if loaded_mission_id == queue_mission_id and _is_valid_mission(controller.mission):
                        logger.info(f"Queue mission verified: {queue_mission_id}")
                        # Clear the queue signal file
                        _clear_queue_signal_file()
                        metrics["reason"] = "queue_auto_start"
                        metrics["total_wait_time"] = time_module.time() - start_time
                        return True, metrics
                    else:
                        logger.warning(f"Queue signal for {queue_mission_id} but mission.json has {loaded_mission_id}")
        except Exception as e:
            logger.debug(f"Queue signal file read failed: {e}")

        # === LAYER 2: Mission File Polling (Fallback) ===
        # Directly check mission.json for changes, works even if signaling breaks
        controller.mission = controller.load_mission()
        new_mission_id = controller.mission.get("mission_id")
        new_stage = controller.mission.get("current_stage", "COMPLETE")

        # Validate the mission is not empty or invalid
        if not _is_valid_mission(controller.mission):
            logger.debug(f"Invalid/empty mission detected, skipping (attempt {attempt + 1})")
        elif new_mission_id != completed_mission_id and new_stage != "COMPLETE":
            # New valid mission detected via polling!
            logger.info(f"New mission detected on attempt {attempt + 1}: {new_mission_id}")
            _clear_signal_file()  # Clear any lingering signal
            metrics["reason"] = "success"
            if metrics["fallback_used"]:
                metrics["reason"] = "signal_fallback"
            metrics["total_wait_time"] = time_module.time() - start_time
            return True, metrics

        # === LAYER 3: Exponential Backoff ===
        # Wait before next retry with increasing intervals
        if attempt < max_retries - 1:
            if use_exponential_backoff:
                interval = _calculate_backoff_interval(attempt, base_interval)
            else:
                interval = base_interval
            metrics["backoff_intervals"].append(interval)
            logger.debug(f"No new mission yet, retrying in {interval}s (attempt {attempt + 1}/{max_retries})")
            time.sleep(interval)

    # All retries exhausted without finding a new mission
    metrics["reason"] = "max_retries"
    metrics["total_wait_time"] = time_module.time() - start_time
    logger.info(f"No new mission detected after {max_retries} attempts")
    return False, metrics


def _clear_signal_file() -> None:
    """Clear the auto-advance signal file.

    Safely removes the signal file to prevent stale reads. Failures are
    silently ignored since a lingering signal file is not critical.
    """
    if AUTO_ADVANCE_SIGNAL_PATH.exists():
        try:
            AUTO_ADVANCE_SIGNAL_PATH.unlink()
        except OSError:
            pass


def _clear_queue_signal_file() -> None:
    """Clear the queue auto-start signal file.

    Safely removes the queue signal file to prevent stale reads. Failures are
    silently ignored since a lingering signal file is not critical.
    """
    if QUEUE_AUTO_START_SIGNAL_PATH.exists():
        try:
            QUEUE_AUTO_START_SIGNAL_PATH.unlink()
        except OSError:
            pass


def get_mission_workspace(controller) -> Path:
    """Get the working directory for the current mission.

    Returns the mission-specific workspace if available, otherwise falls back to global workspace.
    """
    mission_workspace = controller.mission.get("mission_workspace")
    if mission_workspace:
        workspace_path = Path(mission_workspace)
        if workspace_path.exists():
            return workspace_path
    return WORKSPACE_DIR


def run_rd_mode():
    """
    Run Claude in directed R&D mode.

    Uses the RDMissionController to guide Claude through:
    PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE
    """
    global running

    logger.info("=" * 60)
    logger.info("CLAUDE AUTONOMOUS - R&D MODE")
    logger.info("=" * 60)

    save_pid()

    state = load_state()
    state["mode"] = "rd"
    state["boot_count"] = state.get("boot_count", 0) + 1
    state["last_boot"] = datetime.now().isoformat()
    save_state(state)

    send_to_chat(f"AtlasForge starting Mission Launch #{state['boot_count']}")

    # Initialize R&D controller
    controller = atlasforge_engine.RDMissionController()

    cycle_count = 0
    timeout_retries = 0  # Track consecutive timeout failures

    try:
        while running:
            cycle_count += 1
            state["total_cycles"] = state.get("total_cycles", 0) + 1

            current_stage = controller.mission.get("current_stage", "PLANNING")
            logger.info(f"=== R&D Cycle {cycle_count} | Stage: {current_stage} ===")

            # Check for human interrupt
            human_msg = check_human_message()
            if human_msg:
                human_prompt = human_msg.get("prompt", "")
                logger.info(f"Human message: {human_prompt[:100]}...")
                clear_human_message()

                # Handle human message - could be mission update or question
                if human_prompt.lower().startswith("set mission:"):
                    new_mission = human_prompt[12:].strip()
                    controller.set_mission(new_mission)
                    send_to_chat(f"New mission set: {new_mission[:100]}...")
                    continue
                elif human_prompt.lower() == "reset":
                    controller.reset_mission()
                    send_to_chat("Mission reset to PLANNING stage.")
                    continue
                elif human_prompt.lower() == "status":
                    status = atlasforge_engine.get_mission_status()
                    send_to_chat(f"Status: Stage={status['stage']}, Iteration={status['iteration']}")
                    continue
                else:
                    # Include human message as context for next cycle
                    send_to_chat(f"Noted: {human_prompt[:100]}...")
                    # Continue with R&D cycle, incorporating message as context

            # Check if mission is complete
            if current_stage == "COMPLETE":
                completed_mission_id = controller.mission.get("mission_id")
                total_cycles = controller.mission.get("cycle_budget", 1)
                logger.info(f"Mission COMPLETE after {total_cycles} cycle(s).")

                # Record completed mission to memory
                mission_summary = {
                    "mission_id": completed_mission_id,
                    "problem": controller.mission.get("original_problem_statement") or controller.mission.get("problem_statement", "")[:200],
                    "iterations": controller.mission.get("iteration", 0),
                    "total_cycles": total_cycles,
                    "completed_at": datetime.now().isoformat()
                }
                add_to_memory("mission_history", json.dumps(mission_summary))

                # ROBUST: Use retry loop with file-based signaling to detect new missions
                # This handles the case where auto-advance HTTP calls take longer than expected
                #
                # Uses exponential backoff (1s -> 2s -> 4s) to reduce load on slow networks
                # while still detecting fast auto-advances quickly on the first poll.
                #
                # Graceful degradation: If signal file fails, falls back to mission.json polling
                new_mission_detected, retry_metrics = _wait_for_new_mission_with_retry(
                    controller,
                    completed_mission_id,
                    max_retries=4,           # More retries with backoff
                    base_interval=1.0,       # Start at 1s, then 2s, 4s, 8s
                    max_total_wait=15.0,     # Cap total wait at 15 seconds
                    use_exponential_backoff=True
                )

                # Log metrics to file for long-term analysis of auto-advance timing patterns
                _log_retry_metrics(retry_metrics, completed_mission_id)

                # Log metrics for immediate monitoring
                logger.info(f"Mission detection metrics: {retry_metrics}")

                # Alert on potentially slow network conditions
                if retry_metrics["attempts"] > 2:
                    logger.warning(f"High retry count ({retry_metrics['attempts']}) - possible slow network or dashboard latency")

                # Alert if fallback mechanism was used (indicates IPC issues)
                if retry_metrics.get("fallback_used"):
                    logger.warning("Signal file fallback was used - check IPC mechanism health")

                if new_mission_detected:
                    new_mission_id = controller.mission.get("mission_id")
                    new_stage = controller.mission.get("current_stage", "COMPLETE")
                    logger.info(f"New mission detected: {new_mission_id} (stage: {new_stage}). Continuing...")
                    send_to_chat(f"Mission {completed_mission_id} complete. Auto-advancing to new mission: {new_mission_id}")
                    continue  # Don't break - continue with the new mission

                # No new mission, truly shut down
                # Check if mission was halted due to drift
                halted_due_to_drift = controller.mission.get("halted_due_to_drift", False)
                halted_at_cycle = controller.mission.get("halted_at_cycle", total_cycles)

                if halted_due_to_drift:
                    send_to_chat(f"R&D Mission complete due to drift after {halted_at_cycle} cycle(s). AtlasForge shutting down. Set a new mission and restart to continue.")
                else:
                    send_to_chat(f"R&D Mission complete after {total_cycles} cycle(s). AtlasForge shutting down. Set a new mission and restart to continue.")
                break  # Exit the loop - don't waste resources polling

            # Build prompt for current stage
            prompt = controller.build_rd_prompt()

            # Get the appropriate workspace for this mission
            workspace = get_mission_workspace(controller)
            logger.info(f"Using workspace: {workspace}")

            # Invoke Claude with ContextWatcher monitoring
            # Timeout is 3600s (1 hour) safety net - ContextWatcher handles normal handoffs
            context_session_id = None
            handoff_triggered = threading.Event()
            handoff_signal_ref = [None]  # Mutable container for signal

            def on_context_handoff(signal):
                """Handle context exhaustion signal from ContextWatcher."""
                handoff_signal_ref[0] = signal
                handoff_triggered.set()
                logger.warning(f"Context handoff triggered: {signal.level.value} at {signal.tokens_used} tokens")

                mission_id = controller.mission.get("mission_id", "unknown")

                if signal.level == HandoffLevel.GRACEFUL:
                    # Write HANDOFF.md for graceful handoff with Haiku-generated summary
                    # Notify user that Haiku is being invoked
                    send_to_chat(f"[HAIKU] Context limit detected ({signal.cache_creation:,} tokens). Invoking Haiku for intelligent handoff summary...")
                    # Try to get intelligent summary from Haiku
                    recent_context = get_recent_chat_context(n_messages=5)
                    haiku_summary = invoke_haiku_summary(mission_id, current_stage, recent_context)

                    if haiku_summary:
                        # Use Haiku-generated summary
                        summary = f"""{haiku_summary}

**Token stats:** {signal.tokens_used:,} total (cache_creation: {signal.cache_creation:,}, cache_read: {signal.cache_read:,})
**Handoff reason:** Context approaching limit, graceful handoff initiated"""
                        send_to_chat(f"[CONTEXT] Graceful handoff at {signal.tokens_used:,} tokens. Haiku wrote HANDOFF.md.")
                    else:
                        # Fallback to basic summary if Haiku unavailable
                        summary = f"""**Working on:** Stage {current_stage}
**Tokens used:** {signal.tokens_used:,} (cache_creation: {signal.cache_creation:,}, cache_read: {signal.cache_read:,})
**Handoff reason:** Context approaching limit, graceful handoff initiated
**Next:** Continue from current stage with fresh context"""
                        send_to_chat(f"[CONTEXT] Graceful handoff at {signal.tokens_used:,} tokens. HANDOFF.md written.")

                    write_handoff_state(str(workspace), mission_id, current_stage, summary)

                elif signal.level == HandoffLevel.TIME_BASED:
                    # Time-based handoff at 55 minutes - use Haiku to write intelligent summary
                    elapsed_min = signal.elapsed_minutes if signal.elapsed_minutes else 55.0
                    # Notify user that Haiku is being invoked
                    send_to_chat(f"[HAIKU] Time limit reached ({elapsed_min:.1f} min). Invoking Haiku for intelligent handoff summary...")
                    recent_context = get_recent_chat_context(n_messages=5)
                    haiku_summary = invoke_haiku_summary(mission_id, current_stage, recent_context)

                    if haiku_summary:
                        summary = f"""{haiku_summary}

**Elapsed time:** {elapsed_min:.1f} minutes
**Handoff reason:** Time-based handoff at 55 minutes (proactive, before 1-hour timeout)"""
                        send_to_chat(f"[CONTEXT] Time-based handoff at {elapsed_min:.1f} minutes. Haiku wrote HANDOFF.md.")
                    else:
                        summary = f"""**Working on:** Stage {current_stage}
**Elapsed time:** {elapsed_min:.1f} minutes
**Handoff reason:** Time-based handoff at 55 minutes (proactive, before 1-hour timeout)
**Next:** Continue from current stage with fresh context"""
                        send_to_chat(f"[CONTEXT] Time-based handoff at {elapsed_min:.1f} minutes. HANDOFF.md written.")

                    write_handoff_state(str(workspace), mission_id, current_stage, summary)

                elif signal.level == HandoffLevel.EMERGENCY:
                    send_to_chat(f"[CONTEXT] EMERGENCY handoff at {signal.tokens_used:,} tokens!")

            # Start ContextWatcher if available
            if HAS_CONTEXT_WATCHER:
                try:
                    watcher = get_context_watcher()
                    context_session_id = watcher.start_watching(
                        str(workspace),
                        on_context_handoff,
                        enable_time_handoff=TIME_BASED_HANDOFF_ENABLED
                    )
                    if context_session_id:
                        logger.info(f"ContextWatcher started for session {context_session_id}")
                except Exception as e:
                    logger.warning(f"Failed to start ContextWatcher: {e}")

            response_text, error_info = invoke_llm(prompt, timeout=3600, cwd=workspace)

            # Stop ContextWatcher
            if context_session_id and HAS_CONTEXT_WATCHER:
                try:
                    watcher = get_context_watcher()
                    stats = watcher.get_session_stats(context_session_id)
                    watcher.stop_watching(context_session_id)
                    if stats:
                        logger.info(f"ContextWatcher session {context_session_id}: peak={stats.get('peak_tokens', 0):,} tokens")
                except Exception as e:
                    logger.debug(f"Error stopping ContextWatcher: {e}")

            if not response_text:
                # =========================================================
                # CRITICAL BUG FIX: Distinguish graceful handoffs from errors
                # =========================================================
                # Check if this was a graceful handoff (NOT an error)
                # Graceful handoffs (context exhaustion, time-based) should NOT
                # count towards the 3-strike timeout limit.
                if handoff_triggered.is_set():
                    handoff_signal = handoff_signal_ref[0]
                    handoff_level = handoff_signal.level.value if handoff_signal else "unknown"

                    # Log appropriate restart message based on handoff type
                    if handoff_level == "graceful":
                        send_to_chat(f"[RESTART] Context exhaustion handoff complete. Fresh instance starting...")
                        logger.info("Graceful context handoff - NOT counting as error")
                    elif handoff_level == "time_based":
                        elapsed = handoff_signal.elapsed_minutes if handoff_signal and handoff_signal.elapsed_minutes else 55.0
                        send_to_chat(f"[RESTART] Time-based handoff ({elapsed:.1f} min) complete. Fresh instance starting...")
                        logger.info(f"Time-based handoff at {elapsed:.1f} min - NOT counting as error")
                    else:
                        send_to_chat(f"[RESTART] Emergency context handoff. Fresh instance starting...")
                        logger.warning("Emergency handoff - NOT counting as error but flagging for review")

                    # Record in journal with handoff type (distinguishable from errors)
                    append_journal({
                        "type": "graceful_handoff_restart",
                        "stage": current_stage,
                        "handoff_level": handoff_level,
                        "mission_id": controller.mission.get("mission_id"),
                        "error_info": error_info  # Include for diagnostics
                    })

                    # Reset handoff state for next iteration
                    handoff_triggered.clear()
                    handoff_signal_ref[0] = None

                    # Do NOT increment timeout_retries - this is expected behavior
                    time.sleep(5)  # Brief pause before restart
                    continue
                else:
                    # Real error - increment counter
                    timeout_retries += 1

                    # Capture detailed error info for verbose logging
                    error_details = error_info or "No response from Claude CLI (unknown reason)"

                    if timeout_retries >= MAX_CLAUDE_RETRIES:
                        logger.error(f"Claude timed out {MAX_CLAUDE_RETRIES} times consecutively")
                        send_to_chat(f"[ERROR] Claude unresponsive after {MAX_CLAUDE_RETRIES} retries. Mission halted.")
                        send_to_chat(f"[ERROR] Last error: {error_details}")
                        send_to_chat(f"[ERROR] Stage: {current_stage}, Mission: {controller.mission.get('mission_id')}")
                        append_journal({
                            "type": "claude_timeout_failure",
                            "stage": current_stage,
                            "retries": timeout_retries,
                            "error_details": error_details,
                            "mission_id": controller.mission.get("mission_id")
                        })
                        break  # Exit loop - mission needs intervention

                    send_to_chat(f"[ERROR] No response from Claude (attempt {timeout_retries}/{MAX_CLAUDE_RETRIES}). Error: {error_details}")
                    logger.warning(f"No response from Claude, retrying ({timeout_retries}/{MAX_CLAUDE_RETRIES}). Error: {error_details}")
                    time.sleep(10)
                    continue

            # Parse response
            response = extract_json_from_response(response_text)

            if not response:
                # If we can't parse JSON, log the raw response and retry
                logger.warning("Could not parse response as JSON")
                append_journal({
                    "type": "rd_raw_response",
                    "stage": current_stage,
                    "response": response_text[:1000]
                })
                time.sleep(5)
                continue

            # Reset timeout counter on successful response
            timeout_retries = 0

            # Log the response
            append_journal({
                "type": "rd_cycle",
                "stage": current_stage,
                "status": response.get("status"),
                "message": response.get("message_to_human", "")[:200]
            })

            # Send status to chat if present
            if response.get("message_to_human"):
                send_to_chat(f"[{current_stage}] {response['message_to_human']}")

            # Process response and get next stage
            next_stage = controller.process_response(response)

            # Update stage if changed
            if next_stage != current_stage:
                controller.update_stage(next_stage)
                cycle_info = f" (Cycle {controller.mission.get('current_cycle', 1)}/{controller.mission.get('cycle_budget', 1)})"
                send_to_chat(f"Stage transition: {current_stage} -> {next_stage}{cycle_info}")

            # Save state
            save_state(state)

            # Brief pause between cycles
            time.sleep(5)

    except Exception as e:
        logger.error(f"R&D Mode error: {e}", exc_info=True)
        send_to_chat(f"R&D Error: {e}")
    finally:
        save_state(state)
        remove_pid()
        logger.info("Claude Autonomous R&D Mode stopped")


# =============================================================================
# FREE MODE (Original autonomous behavior)
# =============================================================================

def build_free_mode_prompt(state: dict) -> str:
    """Build prompt for free exploration mode."""
    return f"""You are Claude, running AUTONOMOUSLY on a home server.
You are not responding to a human - you are THINKING and WORKING on your own.

CURRENT TIME: {datetime.now().isoformat()}
BOOT COUNT: {state.get('boot_count', 0)}
TOTAL CYCLES: {state.get('total_cycles', 0)}

YOUR ENVIRONMENT:
- Base directory: {BASE_DIR}
- Workspace: {WORKSPACE_DIR}
- You have full access to bash, files, and the internet

CURRENT TASK: {state.get('current_task', 'None - decide what to do')}

YOUR JOB: Decide what to work on and do it.

Options:
1. Explore and understand this codebase
2. Build something useful
3. Research a topic
4. Improve the system
5. Write documentation

Respond with JSON:
{{
    "action": "work|explore|research|create|improve",
    "task": "what you're going to do",
    "status": "starting|in_progress|completed",
    "work_done": "description of what you accomplished (if any)",
    "next_step": "what should happen next",
    "message_to_human": "optional status message"
}}
"""


def run_free_mode():
    """
    Run Claude in free exploration mode.
    Original autonomous behavior without directed missions.
    """
    global running

    logger.info("=" * 60)
    logger.info("CLAUDE AUTONOMOUS - FREE MODE")
    logger.info("=" * 60)

    save_pid()

    state = load_state()
    state["mode"] = "free"
    state["boot_count"] = state.get("boot_count", 0) + 1
    state["last_boot"] = datetime.now().isoformat()
    save_state(state)

    send_to_chat(f"Claude Free Mode starting (Boot #{state['boot_count']})")

    cycle_count = 0

    try:
        while running:
            cycle_count += 1
            state["total_cycles"] = state.get("total_cycles", 0) + 1

            logger.info(f"=== Free Cycle {cycle_count} ===")

            # Check for human message
            human_msg = check_human_message()
            if human_msg:
                human_prompt = human_msg.get("prompt", "")
                clear_human_message()
                send_to_chat(f"Noted: {human_prompt[:100]}...")
                # Could integrate into next prompt as context

            # Build and send prompt
            prompt = build_free_mode_prompt(state)
            response_text = invoke_llm(prompt, timeout=1200, cwd=WORKSPACE_DIR)

            if not response_text:
                logger.warning("No response, retrying...")
                time.sleep(10)
                continue

            # Parse response
            response = extract_json_from_response(response_text)

            if response:
                state["current_task"] = response.get("next_step") or response.get("task")

                append_journal({
                    "type": "free_cycle",
                    "action": response.get("action"),
                    "task": response.get("task"),
                    "status": response.get("status"),
                    "work_done": response.get("work_done", "")[:500]
                })

                if response.get("message_to_human"):
                    send_to_chat(response["message_to_human"])

            save_state(state)
            time.sleep(5)

    except Exception as e:
        logger.error(f"Free Mode error: {e}", exc_info=True)
        send_to_chat(f"Error: {e}")
    finally:
        save_state(state)
        remove_pid()
        logger.info("Claude Autonomous Free Mode stopped")


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Main entry point."""
    # Parse command line arguments
    mode = "rd"  # Default to R&D mode

    for arg in sys.argv[1:]:
        if arg.startswith("--mode="):
            mode = arg.split("=")[1].lower()

    if mode == "rd":
        run_rd_mode()
    elif mode == "free":
        run_free_mode()
    else:
        print(f"Unknown mode: {mode}")
        print("Usage: python3 claude_autonomous.py --mode=rd|free")
        sys.exit(1)


if __name__ == "__main__":
    main()
