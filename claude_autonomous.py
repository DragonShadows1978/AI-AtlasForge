#!/usr/bin/env python3
"""
Claude Autonomous v3.0 - Directed R&D Mode

A streamlined autonomous Claude agent focused on directed research and development.

Modes:
    --mode=rd     : Run in R&D mode (stage-based mission execution)
    --mode=free   : Run in free exploration mode (original behavior)
    (default)     : R&D mode

Usage:
    python3 claude_autonomous.py --mode=rd
    python3 claude_autonomous.py --mode=free
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
import atlasforge_engine

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
PID_PATH = BASE_DIR / "claude_autonomous.pid"

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
        logging.FileHandler(LOG_DIR / "claude_autonomous.log")
    ]
)
logger = logging.getLogger("claude_autonomous")

# Global state
running = True


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
# CLAUDE INVOCATION
# =============================================================================

def invoke_claude(prompt: str, timeout: int = 1200, cwd: Path = None) -> Optional[str]:
    """
    Invoke Claude CLI and get response.

    Args:
        prompt: The prompt to send
        timeout: Timeout in seconds (default 20 min)
        cwd: Working directory (default BASE_DIR)

    Returns:
        Response text or None on error
    """
    if cwd is None:
        cwd = BASE_DIR

    try:
        logger.info(f"Invoking Claude: {prompt[:100]}...")

        result = subprocess.run(
            ["claude", "-p", "--dangerously-skip-permissions"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd)
        )

        if result.returncode == 0:
            response = result.stdout.strip()
            logger.info(f"Claude responded: {response[:200]}...")
            return response
        else:
            logger.error(f"Claude error: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.error(f"Claude timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"Error invoking Claude: {e}")
        return None


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

    send_to_chat(f"Claude R&D Mode starting (Boot #{state['boot_count']})")

    # Initialize R&D controller
    controller = atlasforge_engine.RDMissionController()

    cycle_count = 0

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
                send_to_chat(f"R&D Mission complete after {total_cycles} cycle(s). A_Claude shutting down. Set a new mission and restart to continue.")
                break  # Exit the loop - don't waste resources polling

            # Build prompt for current stage
            prompt = controller.build_rd_prompt()

            # Get the appropriate workspace for this mission
            workspace = get_mission_workspace(controller)
            logger.info(f"Using workspace: {workspace}")

            # Invoke Claude
            response_text = invoke_claude(prompt, timeout=1800, cwd=workspace)

            if not response_text:
                logger.warning("No response from Claude, retrying...")
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
            response_text = invoke_claude(prompt, timeout=1200, cwd=WORKSPACE_DIR)

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
