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
import fcntl
import shlex
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

# ---------------------------------------------------------------------------
# Subprocess environment allowlist — explicit safe variables only
# ---------------------------------------------------------------------------
_SAFE_ENV_EXACT = frozenset({
    'PATH', 'HOME', 'USER', 'LANG', 'TERM', 'SHELL', 'DISPLAY',
    'TMPDIR', 'TEMP', 'TMP',
    'ANTHROPIC_API_KEY',
    'OPENAI_API_KEY',
    'GEMINI_API_KEY', 'GOOGLE_API_KEY',
    'CLAUDE_MODEL', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC',
    'ATLASFORGE_CODEX_MODEL', 'CODEX_MODEL',
    'ATLASFORGE_GEMINI_MODEL', 'ATLASFORGE_GEMINI_MODEL_BALANCED',
    'OLLAMA_URL', 'OLLAMA_MODEL',
    'ATLASFORGE_PORT', 'ATLASFORGE_ROOT', 'ATLASFORGE_DATA_DIR',
    'ATLASFORGE_LLM_PROVIDER',
})
_SAFE_ENV_PREFIXES = ('LC_', 'XDG_')

# Import local modules
import io_utils
import af_engine as atlasforge_engine

# Import error classification module for categorized error handling
from atlasforge_conductor_errors import (
    RestartReason,
    classify_error,
    is_graceful,
    is_blocking,
    format_error_message,
    format_fatal_message,
    format_restart_message,
)

# Response adapter for deterministic fallback when JSON parsing fails
try:
    import sys as _sys
    _adapter_path = str(Path(__file__).resolve().parent / "workspace" / "AtlasLab")
    if _adapter_path not in _sys.path:
        _sys.path.insert(0, _adapter_path)
    from core.response_adapter import (
        construct_fallback_response,
        build_format_correction_prompt,
        adapt_response,
    )
    HAS_RESPONSE_ADAPTER = True
except ImportError:
    HAS_RESPONSE_ADAPTER = False

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
        MAX_ABSOLUTE_TIMEOUT_MINUTES,
    )
    HAS_CONTEXT_WATCHER = True
except ImportError:
    HAS_CONTEXT_WATCHER = False
    get_context_watcher = None
    HandoffSignal = None
    HandoffLevel = None
    write_handoff_state = None
    TIME_BASED_HANDOFF_ENABLED = False
    MAX_ABSOLUTE_TIMEOUT_MINUTES = 360

# WorkBudgetManager — token-primary session control
try:
    from context_watcher.work_budget_manager import WorkBudgetManager, WorkBudgetDecision
    HAS_WORK_BUDGET_MANAGER = True
except ImportError:
    HAS_WORK_BUDGET_MANAGER = False
    WorkBudgetManager = None  # type: ignore
    WorkBudgetDecision = None  # type: ignore

# Enhanced conductor singleton with takeover support
try:
    # Add ConductorTakeover to path so its internal imports resolve
    import sys as _sys
    _conductor_path = str(Path(__file__).resolve().parent / "workspace" / "ConductorTakeover")
    if _conductor_path not in _sys.path:
        _sys.path.insert(0, _conductor_path)

    from workspace.ConductorTakeover.conductor_integration import (
        acquire_conductor_lock_enhanced,
        release_conductor_lock_enhanced,
        setup_enhanced_signal_handlers,
        update_conductor_state,
        is_shutdown_requested,
        show_conductor_status,
        parse_conductor_args,
        ConductorMode,
    )
    HAS_ENHANCED_CONDUCTOR = True
except ImportError:
    HAS_ENHANCED_CONDUCTOR = False

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
MISSION_PATH = STATE_DIR / "mission.json"
LLM_PROVIDER_PATH = STATE_DIR / "llm_provider.json"
CODEX_STAGE_GUARD_CONTEXT_PATH = STATE_DIR / "codex_stage_guard_context.json"
PID_PATH = BASE_DIR / "atlasforge_conductor.pid"
CONDUCTOR_LOCK_PATH = BASE_DIR / "atlasforge_conductor.lock"

# Global file descriptor for conductor lock
_conductor_lock_fd = None

# =============================================================================
# GIT COMMIT STRATEGY
# =============================================================================
# Real code changes: Use scripts/release_workflow.py to create clean
#                    "Release vX.X.X - description" commits on main.
#
# Mission artifacts (.af_snapshots/, .af_archives/, atlasforge_conductor.lock,
#                    .mutmut-cache, coverage*.json, mutants/):
#                    Gitignored — never staged or committed to main.
#
# Checkpoint commits: Routed to 'af-missions/checkpoints' orphan branch by
#                     GitIntegration (af_engine/integrations/git.py).
#                     Set AF_GIT_STRATEGY=disabled to suppress all git commits.
#
# To clean current state (squash 244 [AF] commits ahead of origin/main):
#     python3 scripts/clean_push.py --execute
# =============================================================================

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

# Global state — threading.Event for signal-safe flag
_running = threading.Event()
_running.set()  # Initially running
# NOTE: Do NOT use `running` or `bool(_running)` as a loop guard — threading.Event
# objects are always truthy regardless of their internal state. Always use
# `_running.is_set()` to check whether the conductor should continue running.

# =============================================================================
# SESSION STOP REASON ENUM
# Priority order (highest to lowest):
#   1. context_emergency    — ContextWatcher: window nearly full
#   2. context_graceful     — ContextWatcher: graceful handoff
#   3. work_budget_complete — WorkBudgetManager: token target reached
#   4. work_budget_diminishing_returns — WorkBudgetManager: low delta
#   5. time_fallback        — TimeBasedHandoff: circuit-breaker
#   6. hard_timeout         — process kill for broken sessions
#   7. manual_stop          — user/signal requested stop
# =============================================================================
from enum import Enum as _Enum

class StopReason(_Enum):
    CONTEXT_EMERGENCY = "context_emergency"
    CONTEXT_GRACEFUL = "context_graceful"
    WORK_BUDGET_COMPLETE = "work_budget_complete"
    WORK_BUDGET_DIMINISHING = "work_budget_diminishing_returns"
    TIME_FALLBACK = "time_fallback"
    HARD_TIMEOUT = "hard_timeout"
    MANUAL_STOP = "manual_stop"

# Maximum retries when Claude times out or fails to respond
MAX_CLAUDE_RETRIES = 3

# Maximum consecutive JSON parse failures before warning (separate from transport retries).
# Unlike transport failures, parse failures use a deterministic fallback adapter so they
# never kill the mission - this threshold only controls warning frequency.
MAX_PARSE_FAILURES = 5

# Supported LLM providers for CLI invocation
SUPPORTED_LLM_PROVIDERS = {"claude", "codex", "gemini"}
DEFAULT_LLM_PROVIDER = "claude"


def _env_flag_enabled(name: str, default: bool = False) -> bool:
    """Parse a boolean env flag with tolerant true/false values."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _codex_web_search_enabled() -> bool:
    """Enable Codex native web_search only when explicitly requested.

    Default is False because proxy-only search requires omitting Codex's
    `--search` flag. AtlasForge loads the local WebProxy MCP server instead.
    """
    return _env_flag_enabled("ATLASFORGE_CODEX_WEB_SEARCH", default=False)


def _codex_autonomous_enabled() -> bool:
    """Run Codex with no approval/sandbox prompts by default."""
    return _env_flag_enabled("ATLASFORGE_CODEX_AUTONOMOUS", default=True)


def _codex_stage_guard_enabled() -> bool:
    """Use stage-aware Codex sandboxing for non-implementation stages."""
    return _env_flag_enabled("ATLASFORGE_CODEX_STAGE_GUARD", default=True)


_CODEX_READ_ONLY_STAGES = frozenset({
    "PLANNING",
    "ANALYZING",
    "CYCLE_END",
    "COMPLETE",
    "REVIEW",
})
_CODEX_FULL_SEND_STAGES = frozenset({"BUILDING", "TESTING"})
CODEX_TESTING_RED_TEAM_AGENTS = 3
CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS = 2700
ATLASFORGE_TESTING_RUNNER_DEFAULT_MAX_LANES = 6


def get_codex_stage_sandbox(stage: Optional[str]) -> Optional[str]:
    """Return Codex sandbox mode for an AtlasForge stage, or None for full-send."""
    if not _codex_stage_guard_enabled() or not stage:
        return None
    normalized = str(stage).strip().upper()
    if normalized in _CODEX_FULL_SEND_STAGES:
        return None
    if normalized in _CODEX_READ_ONLY_STAGES:
        return "read-only"
    # Unknown stage names fail conservative. Disable with
    # ATLASFORGE_CODEX_STAGE_GUARD=0 for intentional custom stages.
    return "read-only"


def _gemini_autonomous_enabled() -> bool:
    """Run Gemini with auto-approvals by default."""
    return _env_flag_enabled("ATLASFORGE_GEMINI_AUTONOMOUS", default=True)


def _normalize_llm_provider(value: Any) -> Optional[str]:
    """Normalize a provider string, returning None for unsupported values."""
    provider = str(value or "").strip().lower()
    if provider in SUPPORTED_LLM_PROVIDERS:
        return provider
    return None


def _read_provider_from_json(path: Path) -> Optional[str]:
    """Read provider from a small state JSON file without failing startup."""
    try:
        data = io_utils.atomic_read_json(path, {}) or {}
    except Exception as exc:
        logger.debug("Unable to read provider from %s: %s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return _normalize_llm_provider(data.get("llm_provider") or data.get("provider"))


def _read_llm_state() -> dict:
    """Read persisted dashboard LLM provider/model state."""
    try:
        with open(LLM_PROVIDER_PATH, 'r') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_model_name(value: Any) -> Optional[str]:
    model = str(value or "").strip()
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_./:@\[\]-]{0,79}$", model):
        return model
    return None


def _safe_thinking_effort(value: Any, provider: Optional[str] = None) -> Optional[str]:
    effort = str(value or "").strip().lower()
    allowed = {"low", "medium", "high", "xhigh", "max"}
    if effort in allowed:
        return effort
    return None


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def get_llm_provider() -> str:
    """Resolve the active LLM provider from mission, env, persisted state, then default."""
    mission_provider = _read_provider_from_json(MISSION_PATH)
    if mission_provider:
        return mission_provider

    env_raw = os.environ.get("ATLASFORGE_LLM_PROVIDER")
    env_provider = _normalize_llm_provider(env_raw)
    if env_provider:
        return env_provider
    if env_raw:
        logger.warning(
            "Unknown ATLASFORGE_LLM_PROVIDER='%s', checking persisted provider state",
            str(env_raw).strip().lower(),
        )

    state_provider = _read_provider_from_json(LLM_PROVIDER_PATH)
    if state_provider:
        return state_provider

    return DEFAULT_LLM_PROVIDER


def resolve_llm_provider(mission: Optional[dict] = None) -> str:
    """Resolve the LLM provider, preferring the in-memory active mission.

    The dashboard can persist provider state separately from the mission file.
    During a running mission, the mission object is the authority; otherwise a
    mission-selected Codex run can accidentally fall back to the dashboard's
    previous Claude selection and miss Codex-only stage-guard wiring.
    """
    if isinstance(mission, dict):
        mission_provider = _normalize_llm_provider(
            mission.get("llm_provider") or mission.get("provider")
        )
        if mission_provider:
            return mission_provider
    return get_llm_provider()


def _write_codex_stage_guard_context(stage: Optional[str], mission: Optional[dict]) -> None:
    """Persist Codex stage context for MCP stage-guard enforcement.

    Codex also receives this context in its subprocess environment, but a small
    AtlasForge-owned state file keeps the MCP guard independent of client env
    inheritance. This is written only for Codex launches.
    """
    mission = mission if isinstance(mission, dict) else {}
    payload = {
        "provider": "codex",
        "stage": str(stage or mission.get("current_stage") or "PLANNING").strip().upper(),
        "mission_id": str(mission.get("mission_id") or ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        if not io_utils.atomic_write_json(CODEX_STAGE_GUARD_CONTEXT_PATH, payload):
            logger.warning("Failed to write Codex stage-guard context")
    except Exception as exc:
        logger.warning("Failed to write Codex stage-guard context: %s", exc)


def get_llm_model(provider: str, mission: Optional[dict] = None) -> Optional[str]:
    """Resolve provider-specific model override for CLI invocation."""
    if isinstance(mission, dict):
        mission_model = _safe_model_name(mission.get("llm_model") or mission.get("model"))
        if mission_model:
            return mission_model

    state = _read_llm_state()
    selected = state.get("selected") if isinstance(state.get("selected"), dict) else {}
    provider_selected = selected.get(provider) if isinstance(selected.get(provider), dict) else {}
    persisted_model = _safe_model_name(provider_selected.get("model"))
    if persisted_model:
        return persisted_model
    if provider == "codex":
        model = os.environ.get("ATLASFORGE_CODEX_MODEL") or os.environ.get("CODEX_MODEL")
        return model.strip() if model and model.strip() else None
    if provider == "gemini":
        model = os.environ.get("ATLASFORGE_GEMINI_MODEL") or os.environ.get("ATLASFORGE_GEMINI_MODEL_BALANCED")
        return model.strip() if model and model.strip() else None
    model = os.environ.get("CLAUDE_MODEL")
    return model.strip() if model and model.strip() else None


def get_llm_thinking_effort(provider: str, mission: Optional[dict] = None) -> Optional[str]:
    """Resolve provider-specific thinking/reasoning effort."""
    if isinstance(mission, dict):
        mission_effort = _safe_thinking_effort(mission.get("llm_thinking") or mission.get("thinking"), provider)
        if mission_effort:
            return mission_effort

    state = _read_llm_state()
    selected = state.get("selected") if isinstance(state.get("selected"), dict) else {}
    provider_selected = selected.get(provider) if isinstance(selected.get(provider), dict) else {}
    persisted = _safe_thinking_effort(provider_selected.get("thinking"), provider)
    if persisted:
        return persisted
    env_key = {
        "claude": "ATLASFORGE_CLAUDE_THINKING",
        "codex": "ATLASFORGE_CODEX_THINKING",
        "gemini": "ATLASFORGE_GEMINI_THINKING",
    }.get(provider)
    return _safe_thinking_effort(os.environ.get(env_key), provider) if env_key else None


def get_llm_fast_enabled(provider: str, mission: Optional[dict] = None) -> bool:
    """Resolve Codex fast service tier separately from reasoning effort."""
    if provider != "codex":
        return False
    if isinstance(mission, dict) and "llm_fast" in mission:
        return _safe_bool(mission.get("llm_fast"))

    state = _read_llm_state()
    selected = state.get("selected") if isinstance(state.get("selected"), dict) else {}
    provider_selected = selected.get(provider) if isinstance(selected.get(provider), dict) else {}
    return (
        _safe_bool(provider_selected.get("fast"))
        or str(provider_selected.get("thinking") or "").strip().lower() == "fast"
        or _env_flag_enabled("ATLASFORGE_CODEX_FAST", default=False)
    )


def codex_stage_guard_prompt(stage: str, mission: Optional[dict] = None) -> str:
    """Provider-specific prompt addendum for AtlasForge MCP stage-guard tools."""
    stage_name = str(stage or "PLANNING").strip().upper()
    mission = mission if isinstance(mission, dict) else {}
    mission_id = mission.get("mission_id", "current mission")
    if stage_name == "PLANNING":
        return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
AtlasForge is the conductor. Use the AtlasForge MCP stage-guard tools for
stage artifacts:

- Call `AtlasForgeGetStagePolicy` if you need the current MCP write policy.
- During PLANNING, submit structured planning artifacts with
  `AtlasForgeSubmitPlan`; it writes the only permitted planning plan file:
  `artifacts/implementation_plan.md`.
- Do not implement source changes during PLANNING.
- If planning research notes are needed, use `AtlasForgeWriteStageNote`; during
  PLANNING it may write only `research/*.md`.

AtlasForge launches Codex PLANNING with a read-only workspace sandbox, so MCP
stage-guard tools are the intended write path for planning artifacts.
These MCP tools enforce AtlasForge's stage/path/extension policy. If a write is
rejected, report the rejection and stop expanding scope.
"""
    if stage_name in {"ANALYZING", "CYCLE_END"}:
        return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
Use `AtlasForgeGetStagePolicy` to inspect allowed stage artifact paths.
Submit analysis/review artifacts with `AtlasForgeSubmitReview`, and use
`AtlasForgeWriteStageNote` for Markdown notes. Do not make implementation
changes from this stage; recommend BUILDING-stage work instead.
AtlasForge launches this stage with a read-only workspace sandbox for Codex.
"""
    if stage_name in {"BUILDING", "TESTING"}:
        if stage_name == "BUILDING":
            return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
AtlasForge owns the stage lifecycle. BUILDING includes implementation and
builder self-validation: run focused tests, lint, type checks, or build commands
needed to prove the patch is ready.

Do not collapse AtlasForge TESTING into BUILDING. Any tests you run here are
self-validation only. The official TESTING stage must still run the configured
blind red-team gate before the mission can be considered verified.

At the end of BUILDING, include `self_validation` in your JSON response with
commands run, result, and notes. Submit a concise structured patch summary
through `AtlasForgeSubmitPatchSummary` when useful.
"""
        if stage_name == "TESTING":
            return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
AtlasForge owns the TESTING pipeline for Codex. Before this tester pass,
AtlasForge automatically launches {CODEX_TESTING_RED_TEAM_AGENTS} blind
red-team agents with a {CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS} second
(45 minute) timeout and collects their artifacts.

Your role in this Codex TESTING pass:
- Read the appended Codex red-team preflight summary and the referenced files.
- Reproduce or refute the red-team findings.
- Run your own targeted verification after reviewing red-team output.
- Run a quick mutation check where practical, or explicitly report why it is
  unavailable for this workspace/language/test command.
- Return strict TESTING JSON. Include `adversarial_testing.red_team_completion`
  and mutation-test evidence.

BUILDING self-validation does not satisfy TESTING. TESTING can pass early only
when all required red-team agents completed and their reports were collected.
Submit a concise structured test summary through `AtlasForgeSubmitPatchSummary`
when useful.
"""
        return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
AtlasForge owns the stage lifecycle. Keep BUILDING/TESTING work scoped to the
approved mission task. TESTING is the official verification gate: run the
configured blind red-team agents and report their results. Builder self-tests
from BUILDING do not satisfy TESTING. At the end of the stage, submit a concise
structured summary through `AtlasForgeSubmitPatchSummary` when useful.
"""
    return f"""

## CODEX + ATLASFORGE MCP STAGE GUARD

You are running under AtlasForge as Codex for mission `{mission_id}`.
Use `AtlasForgeGetStagePolicy` before writing any stage artifact through MCP.
"""


def build_llm_command(
    provider: str,
    model: Optional[str] = None,
    stage: Optional[str] = None,
    mission: Optional[dict] = None,
) -> List[str]:
    """Build subprocess CLI command for the selected provider.

    Args:
        provider: LLM provider name (claude, codex, gemini)
        model: Optional model override
        stage: Current R&D stage - used to compute --disallowedTools for Claude CLI
        mission: Optional mission dict — when set, profile flags
                 (allow_code_writes, allow_implementation) overlay extra tool
                 restrictions on top of stage policy. Backwards-compat: omit to
                 retain pre-cycle-2 behavior.
    """
    logger.info(f"Building command for provider: {provider}, model: {model}, stage: {stage}")
    thinking = get_llm_thinking_effort(provider, mission=mission)
    if provider == "codex":
        from WebProxy import codex_proxy_cli_args
        cmd = ["codex"]
        cmd.extend(codex_proxy_cli_args())
        codex_sandbox = get_codex_stage_sandbox(stage)
        if get_llm_fast_enabled(provider, mission=mission):
            cmd.extend(["--enable", "fast_mode"])
            cmd.extend(["-c", 'service_tier="fast"'])
        if thinking:
            cmd.extend(["-c", f'model_reasoning_effort="{thinking}"'])
        if _codex_web_search_enabled():
            # Native Responses web_search. Off by default so proxy MCP is authoritative.
            cmd.append("--search")
        cmd.extend([
            "exec",
            "--color", "never",
        ])
        if codex_sandbox:
            cmd.extend(["--sandbox", codex_sandbox])
        elif _codex_autonomous_enabled():
            cmd.append("--dangerously-bypass-approvals-and-sandbox")
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")
        return cmd

    if provider == "gemini":
        # Force non-interactive prompt mode; prompt content is provided via stdin.
        cmd = ["gemini"]
        if _gemini_autonomous_enabled():
            cmd.append("--yolo")
        cmd.extend(["--output-format", "json"])

        selected_model = (model or os.environ.get("ATLASFORGE_GEMINI_MODEL", "")).strip()
        if not selected_model:
            # Fallback to balanced tier when no explicit override is provided.
            selected_model = os.environ.get("ATLASFORGE_GEMINI_MODEL_BALANCED", "").strip()
        if selected_model:
            cmd.extend(["-m", selected_model])
            logger.info(f"Using Gemini model: {selected_model}")
        return cmd

    # Default provider: Claude CLI
    from init_guard import InitGuard
    from WebProxy import proxy_cli_args
    disallowed = InitGuard.get_disallowed_tools_for_cli(stage or "BUILDING", mission=mission)
    # Sanitize disallowed tools string — only allow safe characters to prevent CLI injection
    # if a stage handler somehow returns shell metacharacters.
    import re as _re
    disallowed = _re.sub(r'[^a-zA-Z0-9_,\s]', '', disallowed)
    logger.info(f"Stage '{stage or 'BUILDING'}' -> disallowedTools: {disallowed}")
    cmd = [
        "claude", "-p",
        "--dangerously-skip-permissions",
    ]
    cmd.extend(proxy_cli_args(disallowed))
    if model:
        cmd[2:2] = ["--model", model]
    if thinking:
        cmd.extend(["--effort", thinking])
    return cmd


def _llm_command_preview(provider: str) -> str:
    """Build a safe, user-visible command preview for activity feed."""
    try:
        cmd = build_llm_command(provider, model=get_llm_model(provider))
        return shlex.join(cmd)
    except Exception as e:
        return f"<command unavailable: {e}>"


def acquire_conductor_lock() -> bool:
    """Acquire exclusive lock to prevent multiple conductor instances.

    Uses fcntl file locking for atomic cross-process coordination.
    The lock is automatically released when the process exits.

    Returns:
        True if lock acquired, False if another instance is running.
    """
    global _conductor_lock_fd
    try:
        _conductor_lock_fd = open(CONDUCTOR_LOCK_PATH, 'w')
        fcntl.flock(_conductor_lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _conductor_lock_fd.write(str(os.getpid()))
        _conductor_lock_fd.flush()
        logger.info(f"Acquired conductor lock (PID {os.getpid()})")
        return True
    except (IOError, BlockingIOError):
        logger.error("Another conductor instance is already running")
        if _conductor_lock_fd:
            _conductor_lock_fd.close()
            _conductor_lock_fd = None
        return False


def release_conductor_lock():
    """Release conductor lock."""
    global _conductor_lock_fd
    if _conductor_lock_fd:
        try:
            fcntl.flock(_conductor_lock_fd.fileno(), fcntl.LOCK_UN)
            _conductor_lock_fd.close()
            CONDUCTOR_LOCK_PATH.unlink(missing_ok=True)
            logger.info("Released conductor lock")
        except Exception as e:
            logger.debug(f"Error releasing conductor lock: {e}")
        finally:
            _conductor_lock_fd = None


def signal_handler(signum, frame):
    logger.info(f"Received signal {signum}, shutting down gracefully...")
    _running.clear()


if threading.current_thread() is threading.main_thread():
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
    if isinstance(max_items, bool):
        raise TypeError(f"max_items must be an int or None, not bool (got {max_items!r})")
    if max_items is not None and (not isinstance(max_items, int) or max_items < 0):
        raise ValueError(f"max_items must be None (unlimited), 0 (unlimited), or a positive integer, got {max_items!r}")
    def update_fn(memory):
        if key not in memory:
            memory[key] = []
        memory[key].append({
            "content": value,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        # Keep bounded (0 means no limit; None also means no limit)
        if max_items is not None and max_items > 0 and len(memory[key]) > max_items:
            memory[key] = memory[key][-max_items:]
        return memory

    io_utils.atomic_update_json(CLAUDE_MEMORY_PATH, update_fn, {})


def append_journal(entry: dict):
    """Append to Claude's thought journal."""
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(CLAUDE_JOURNAL_PATH, 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            f.write(json.dumps(entry) + "\n")
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def send_to_chat(message: str):
    """Send a message to the chat history for UI display."""
    provider = get_llm_provider()
    message = str(message)

    def update_history(history):
        if not isinstance(history, list):
            history = []
        history.append({
            "role": "claude",
            "provider": provider,
            "content": message,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        if len(history) > 500:
            history = history[-500:]
        return history

    io_utils.atomic_update_json(CHAT_HISTORY_PATH, update_history, [])
    logger.info(f"Chat: {_sanitize_for_log(message)[:100]}...")


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

# Module-level process reference for handoff termination
_active_claude_process = None
_active_claude_lock = threading.Lock()

# Agent streaming counter for mission agents
import uuid as _uuid
_mission_agent_counter = 0
_mission_agent_counter_lock = threading.Lock()

def _next_mission_agent_id(stage: str) -> tuple:
    """Return (agent_id, label) for a new mission agent."""
    global _mission_agent_counter
    with _mission_agent_counter_lock:
        _mission_agent_counter += 1
        n = _mission_agent_counter
    agent_id = f"mission_{_uuid.uuid4().hex[:8]}"
    stage_short = (stage or 'AGENT').upper()[:8]
    label = f"{stage_short} Agent {n}"
    return agent_id, label


def get_active_claude_process():
    """Get the currently active Claude subprocess, if any."""
    with _active_claude_lock:
        return _active_claude_process


def _restore_active_claude(proc):
    """Restore process ref if termination failed."""
    global _active_claude_process
    with _active_claude_lock:
        if _active_claude_process is None:
            _active_claude_process = proc


def terminate_active_claude():
    """Terminate the active Claude subprocess for graceful handoff.
    Returns True if a process was terminated."""
    global _active_claude_process
    with _active_claude_lock:
        proc = _active_claude_process
        if proc is None:
            return False
        _active_claude_process = None  # Atomically claim ownership
    try:
        if proc.pid is None or not isinstance(proc.pid, int) or proc.pid <= 2:
            _restore_active_claude(proc)
            return False
    except (TypeError, AttributeError):
        _restore_active_claude(proc)
        return False
    # Guard against PID reuse: verify the process is actually a Claude/LLM process
    # Open /proc directly without exists() pre-check to avoid TOCTOU race
    try:
        with open(f"/proc/{proc.pid}/cmdline", 'rb') as f:
            cmdline = f.read().replace(b'\x00', b' ').decode('utf-8', errors='replace')
        if 'claude' not in cmdline and 'codex' not in cmdline and 'gemini' not in cmdline:
            logger.warning(f"terminate_active_claude: PID {proc.pid} is not a Claude/LLM process (cmdline: {cmdline[:100]}), skipping kill")
            _restore_active_claude(proc)
            return False
    except (OSError, IOError) as e:
        logger.warning(f"terminate_active_claude: Cannot verify PID {proc.pid} via /proc: {e}, skipping kill for safety")
        _restore_active_claude(proc)
        return False
    try:
        pgid = os.getpgid(proc.pid)
        # Guard: refuse to kill our own process group (subprocess shares conductor's pgid)
        if pgid == os.getpgrp():
            logger.error(f"terminate_active_claude: pgid {pgid} matches conductor's own process group, refusing to kill")
            _restore_active_claude(proc)
            return False
        os.killpg(pgid, signal.SIGTERM)
        logger.info(f"terminate_active_claude: SIGTERM sent to pgid={pgid} (pid={proc.pid})")
        proc.wait(timeout=10)
        logger.info(f"terminate_active_claude: Process {proc.pid} terminated successfully")
        return True
    except ProcessLookupError:
        logger.warning(f"terminate_active_claude: Process {proc.pid} already dead")
        return False
    except OSError as e:
        logger.warning(f"terminate_active_claude: OSError killing pid={proc.pid}: {e}")
        _restore_active_claude(proc)
        return False
    except subprocess.TimeoutExpired:
        sigkill_succeeded = False
        try:
            # Reuse pgid from SIGTERM — do not re-query getpgid to avoid PID reuse race
            os.killpg(pgid, signal.SIGKILL)
            logger.info(f"terminate_active_claude: SIGKILL sent to pgid={pgid} (pid={proc.pid})")
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                logger.warning(f"terminate_active_claude: Process {proc.pid} survived SIGKILL (D-state?)")
            logger.info(f"terminate_active_claude: Process {proc.pid} killed after SIGKILL")
            sigkill_succeeded = True
        except ProcessLookupError:
            logger.warning(f"terminate_active_claude: Process {proc.pid} already dead during SIGKILL")
            sigkill_succeeded = True
        except OSError as e:
            logger.warning(f"terminate_active_claude: OSError during SIGKILL pid={proc.pid}: {e}")
            _restore_active_claude(proc)
            sigkill_succeeded = False
        return sigkill_succeeded



# =============================================================================
# HIERARCHICAL PARALLEL EXECUTION — dashboard-integrated multi-agent BUILDING
# =============================================================================

def _should_use_parallel(stage: str, mission: dict) -> bool:
    """
    Determine if parallel execution should be used for this stage.

    Conditions (any one triggers parallel mode):
    - stage == BUILDING AND cycle_budget >= 2
    - stage == BUILDING AND problem_statement is very long (> 500 chars)
    """
    if stage != "BUILDING":
        return False
    if not isinstance(mission, dict):
        return False
    raw_cb = mission.get("cycle_budget", 1)
    if isinstance(raw_cb, bool):
        raise TypeError(f"cycle_budget must be an integer, got bool {raw_cb!r}")
    try:
        cycle_budget = int(raw_cb)
    except (TypeError, ValueError, OverflowError):
        cycle_budget = 1
    if cycle_budget >= 2:
        return True
    problem_statement = mission.get("problem_statement") or ""
    if not isinstance(problem_statement, str):
        problem_statement = ""
    if len(problem_statement) > 500:
        return True
    return False


def _run_hierarchical_building(
    prompt: str,
    mission: dict,
    workspace: Path,
    stage: str = "BUILDING",
    timeout: int = 7200
) -> tuple:
    """
    Run BUILDING stage using HierarchicalExperiment (parallel Claude agents).

    Each agent is registered with agent_stream_manager and appears as a tab
    in the Mission Activity Panel on the dashboard.

    Falls back to single-agent path if:
    - hierarchical_framework cannot be imported
    - MissionSplitter produces < 2 work units

    Returns: (aggregated_response_text | None, error_info | None)
    """
    try:
        from hierarchical_framework import HierarchicalExperiment, HierarchicalConfig
        from mission_splitter import MissionSplitter, SplitStrategy

        mission_id = mission.get("mission_id", f"hf_{__import__('uuid').uuid4().hex[:8]}")

        splitter = MissionSplitter()
        # Use recommend_agent_count() to derive correct agent count from mission complexity
        n_agents = splitter.recommend_agent_count(prompt, max_agents=3)
        work_units = splitter.split(prompt, strategy=SplitStrategy.AUTO, max_units=n_agents)

        if len(work_units) < 2:
            logger.info("[Parallel] MissionSplitter returned < 2 units — falling back to single agent")
            return None, "insufficient_work_units"

        # If an implementation plan exists, use MasterBuilder for wave-based orchestration
        plan_path = workspace / "artifacts" / "implementation_plan.md"
        if plan_path.exists():
            from hierarchical_framework import MasterBuilder
            config = HierarchicalConfig(
                mission_id=mission_id,
                description=(mission.get("problem_statement", "") or prompt)[:100],
                total_timeout=timeout,
                max_agents=n_agents,
                stage=stage,
                enable_streaming=True,
            )
            send_to_chat(f"[PARALLEL] MasterBuilder: wave-based execution from {plan_path.name}")
            builder = MasterBuilder(config, plan_path, project_context=prompt[:300])
            results = builder.run(progress_callback=send_to_chat)
        else:
            config = HierarchicalConfig(
                mission_id=mission_id,
                description=(mission.get("problem_statement", "") or prompt)[:100],
                total_timeout=timeout,
                max_agents=len(work_units),
                stage=stage,
                enable_streaming=True,
            )
            send_to_chat(f"[PARALLEL] Splitting {stage} stage into {len(work_units)} parallel agents")
            exp = HierarchicalExperiment(config)
            results = exp.run(work_units, progress_callback=send_to_chat)

        summary = results.get_summary()
        logger.info(f"[Parallel] Completed: {summary['completed']}/{summary['total_agents']} agents succeeded")

        # Build aggregated response that the conductor's response handler can process
        response_parts = []
        all_files_created = []
        all_files_modified = []

        for ar in results.agent_results:
            if ar.status == "completed":
                if ar.parsed_result and "summary" in ar.parsed_result:
                    response_parts.append(ar.parsed_result["summary"])
                all_files_created.extend(ar.files_created)
                all_files_modified.extend(ar.files_modified)

        if not response_parts and summary["completed"] == 0:
            return None, "all_agents_failed"

        aggregated = {
            "status": "build_complete",
            "parallel_execution": True,
            "agents_run": summary["total_agents"],
            "agents_completed": summary["completed"],
            "files_created": list(set(all_files_created)),
            "files_modified": list(set(all_files_modified)),
            "summary": f"Parallel BUILDING complete: {summary['completed']}/{summary['total_agents']} agents succeeded. " + " | ".join(response_parts[:3]),
            "ready_for_testing": summary["completed"] > 0,
            "blockers": [],
            "message_to_human": f"Parallel execution: {summary['completed']}/{summary['total_agents']} agents completed in {summary['total_elapsed_seconds']:.0f}s",
        }

        return json.dumps(aggregated, indent=2), None

    except ImportError as e:
        logger.warning(f"[Parallel] Hierarchical framework unavailable: {e}")
        return None, f"import_error:{e}"
    except Exception as e:
        logger.error(f"[Parallel] Hierarchical building failed: {e}")
        return None, f"exception:{e}"


def invoke_llm(
    prompt: str,
    timeout: int = 1200,
    cwd: Path = None,
    stage: str = None,
    mission: Optional[dict] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Invoke configured LLM and get response.

    Uses subprocess.Popen instead of subprocess.run so the process can be
    terminated externally by the handoff callback (terminate_active_claude).

    Args:
        prompt: The prompt to send
        timeout: Timeout in seconds (default 20 min, must be > 0)
        cwd: Working directory (default BASE_DIR)
        stage: Current R&D stage (passed to build_llm_command for tool restrictions)
        mission: Optional mission dict (forwarded to build_llm_command for
                 profile-driven tool restrictions). Backwards-compat: omit to
                 retain pre-cycle-2 behavior.

    Returns:
        Tuple of (response_text, error_info):
        - On success: (response_text, None)
        - On timeout: (None, "timeout:<seconds>")
        - On CLI error: (None, "cli_error:<stderr_snippet>")
        - On exception: (None, "exception:<error_message>")
    """
    global _active_claude_process
    import math
    if not isinstance(prompt, str):
        raise TypeError(
            f"invoke_llm requires prompt to be a str, got {type(prompt).__name__!r}"
        )
    if not prompt:
        raise ValueError("invoke_llm requires a non-empty prompt string")
    if not isinstance(timeout, (int, float)) or timeout <= 0 or math.isnan(timeout) or math.isinf(timeout):
        raise ValueError(f"invoke_llm timeout must be a finite number > 0, got {timeout}")
    if cwd is None:
        cwd = BASE_DIR
    try:
        cwd = Path(cwd).expanduser().resolve(strict=True)
    except (OSError, RuntimeError, TypeError) as exc:
        logger.warning("invoke_llm received invalid cwd %r: %s", cwd, exc)
        return None, "invalid_cwd"
    if not cwd.is_dir():
        logger.warning("invoke_llm cwd is not a directory: %s", cwd)
        return None, "invalid_cwd"

    try:
        provider = resolve_llm_provider(mission)
        model = get_llm_model(provider, mission=mission)
        command = build_llm_command(provider, model=model, stage=stage, mission=mission)
        env = {k: v for k, v in os.environ.items()
               if k in _SAFE_ENV_EXACT or k.startswith(_SAFE_ENV_PREFIXES)}
        env.pop("CLAUDECODE", None)  # Prevent "nested session" error when spawning Claude CLI
        if provider == "codex":
            # Give the MCP stage-guard server explicit runtime context. This is
            # Codex-only and leaves Claude's native InitGuard/tool path alone.
            _write_codex_stage_guard_context(stage, mission)
            env["ATLASFORGE_ROOT"] = str(BASE_DIR)
            env["ATLASFORGE_ACTIVE_PROVIDER"] = "codex"
            if stage:
                env["ATLASFORGE_ACTIVE_STAGE"] = str(stage).strip().upper()
            if isinstance(mission, dict) and mission.get("mission_id"):
                env["ATLASFORGE_ACTIVE_MISSION_ID"] = str(mission.get("mission_id"))
        if provider == "gemini":
            # Gemini CLI is more reliable in headless mode when both HOME and
            # GEMINI_API_KEY are explicit in the spawned environment.
            env.setdefault("HOME", str(Path.home()))
            if not env.get("GEMINI_API_KEY"):
                google_api_key = env.get("GOOGLE_API_KEY", "").strip()
                if google_api_key:
                    env["GEMINI_API_KEY"] = google_api_key
        logger.info(f"Invoking {provider} model={model or '(default)'}: {prompt[:100]}...")

        # For Claude provider: register agent for streaming before spawn
        _agent_id = None
        _comp = None
        _stream_file = None
        _stream_thread = None
        _process_started_at = time.time()
        if provider in {"claude", "codex"}:
            _agent_id, _agent_label = _next_mission_agent_id(stage)
            try:
                from agent_stream_manager import (
                    register_agent as _reg,
                    update_agent_pid as _upd_pid,
                    complete_agent as _comp,
                    stream_stdout_to_file as _stream_fn,
                    stream_codex_session_to_file as _stream_codex_fn,
                )
                _stream_file = _reg('mission', _agent_id, _agent_label, pid=None)
            except Exception as e:
                logger.warning('stream_register failed: %s', e)
                _agent_id = None
                _stream_file = None

        # Add stream-json format flag for Claude so agent-activity widget can parse output
        if provider == "claude":
            command = list(command)  # copy
            if '--output-format' not in command:
                command.extend(['--output-format', 'stream-json', '--verbose'])

        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(cwd),
            env=env,
            start_new_session=True  # Prevent FD inheritance blocking from background processes
        )

        with _active_claude_lock:
            _active_claude_process = proc

        # Update PID and start streaming thread (Claude only)
        if _stream_file and _agent_id:
            try:
                _upd_pid(_agent_id, proc.pid)
                if provider == "claude":
                    _stream_thread = threading.Thread(
                        target=_stream_fn,
                        args=(proc, _stream_file, _agent_id),
                        daemon=True,
                        name=f"stream-{_agent_id}"
                    )
                elif provider == "codex":
                    _stream_thread = threading.Thread(
                        target=_stream_codex_fn,
                        args=(proc, _stream_file, str(cwd), _process_started_at),
                        daemon=True,
                        name=f"stream-{_agent_id}"
                    )
                if _stream_thread is not None:
                    _stream_thread.start()
            except Exception as e:
                logger.warning('stream_thread_setup failed: %s', e)
                _stream_file = None
                _agent_id = None
                _stream_thread = None

        # Propagate LLM subprocess PID to activity monitor for subprocess detection
        try:
            from context_watcher import get_context_watcher as _get_cw
            _cw = _get_cw()
            if hasattr(proc, 'pid') and proc.pid:
                _cw.set_monitored_pid_for_active_session(proc.pid)
        except Exception:
            pass  # Non-critical enhancement

        stdout = None  # initialized here so error-path code can safely reference them
        stderr = ""
        _stderr_thread = None
        try:
            if provider == "claude" and _stream_file:
                # Streaming path: feed stdin manually because we do not use communicate().
                try:
                    proc.stdin.write(prompt)
                    proc.stdin.close()
                except BrokenPipeError as e:
                    logger.error('proc.stdin.write broken pipe: %s', e)
                    proc.wait(timeout=5)
                    return None, f"broken_pipe:{e}"
                except Exception as e:
                    logger.debug('proc.stdin.write failed: %s', e)
                # Streaming thread handles stdout; drain stderr in a background
                # thread to prevent pipe-buffer exhaustion deadlock, then wait.
                _stderr_buf = []

                def _drain_stderr():
                    try:
                        chunks = []
                        if proc.stderr:
                            while True:
                                chunk = proc.stderr.read(8192)
                                if not chunk:
                                    break
                                chunks.append(chunk)
                        _stderr_buf.append("".join(chunks))
                    except Exception:
                        _stderr_buf.append("")

                _stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
                _stderr_thread.start()
                proc.wait(timeout=timeout)
                _stderr_thread.join(timeout=5)
                stderr = _stderr_buf[0] if _stderr_buf else ""
                stdout = None  # consumed by stream thread
            else:
                # Non-streaming path: let communicate() own stdin end-to-end.
                stdout_data, stderr = proc.communicate(input=prompt, timeout=timeout)
                stdout = stdout_data
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                logger.info(f"SIGTERM sent to process group {pgid} (pid={proc.pid})")
                proc.wait(timeout=10)
                logger.info(f"Process {proc.pid} terminated successfully after SIGTERM")
            except ProcessLookupError:
                logger.warning(f"Process {proc.pid} already dead when SIGTERM attempted")
            except OSError as e:
                logger.warning(f"OSError sending SIGTERM to process {proc.pid}: {e}")
            except subprocess.TimeoutExpired:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGKILL)
                    logger.info(f"SIGKILL sent to process group {pgid} (pid={proc.pid})")
                    try:
                        proc.wait(timeout=5)
                        logger.info(f"Process {proc.pid} terminated after SIGKILL")
                    except subprocess.TimeoutExpired:
                        logger.warning(f"Process {proc.pid} survived SIGKILL (D-state?)")
                except ProcessLookupError:
                    logger.warning(f"Process {proc.pid} already dead when SIGKILL attempted")
                except OSError as e:
                    logger.warning(f"OSError sending SIGKILL to process {proc.pid}: {e}")
            logger.error(f"{provider} timed out after {timeout}s")
            if _stderr_thread is not None:
                _stderr_thread.join(timeout=3)
            if _stream_thread:
                _stream_thread.join(timeout=3)
            if _agent_id and _comp:
                try: _comp(_agent_id, error=f"timeout:{timeout}s")
                except Exception as e: logger.debug("complete_agent failed after timeout: %s", e)
            return None, f"timeout:{timeout}s"
        finally:
            with _active_claude_lock:
                _active_claude_process = None

        if proc.returncode == 0:
            response = ''  # default; overwritten by each branch below
            if provider == "claude" and _stream_file:
                # Reconstruct response from JSONL stream file
                try:
                    from agent_stream_manager import reconstruct_text_from_stream_file as _recon
                    response = _recon(_stream_file, provider='claude')
                except Exception as e:
                    logger.error(f"Stream reconstruction failed for {_stream_file}: {e}", exc_info=True)
                    response = ""
                    # Return with error indicator so caller can distinguish from success
                    return response, "stream_reconstruction_failed"
                if _agent_id and _comp:
                    try: _comp(_agent_id)
                    except Exception as e: logger.debug("complete_agent failed: %s", e)
            elif provider == "codex":
                if _stream_thread:
                    _stream_thread.join(timeout=3)
                response = (stdout or "").strip()
                if _agent_id and _comp:
                    try: _comp(_agent_id)
                    except Exception as e: logger.debug("complete_agent failed: %s", e)
            elif provider == "claude":
                # Claude without streaming (e.g. stream registration failed)
                response = (stdout or "").strip()
            else:
                response = (stdout or "").strip()
                # Handle Gemini CLI JSON wrapper
                if provider == "gemini":
                    try:
                        data = json.loads(response)
                        if isinstance(data, dict) and "response" in data:
                            response = data["response"]
                    except json.JSONDecodeError:
                        pass
            logger.info(f"{provider} responded: {response[:200]}...")
            if not response or not response.strip():
                logger.warning(f"{provider} returned empty response despite exit code 0")
                return '', 'empty_response'
            return response, None
        else:
            stderr_text = (stderr or "").strip()
            stderr_snippet = stderr_text[:500] if stderr_text else "No stderr"

            if provider == "gemini":
                lines = [ln.strip() for ln in stderr_text.splitlines() if ln.strip()]
                noise_prefixes = (
                    "YOLO mode is enabled.",
                    "Hook registry initialized",
                    "Both GOOGLE_API_KEY and GEMINI_API_KEY are set.",
                )
                filtered = [ln for ln in lines if not ln.startswith(noise_prefixes)]
                candidate_lines = filtered or lines
                best_line = next(
                    (ln for ln in candidate_lines if "error" in ln.lower() or "failed" in ln.lower()),
                    candidate_lines[0] if candidate_lines else "Gemini CLI failed",
                )
                lower = stderr_text.lower()
                if "error authenticating" in lower or "listen eperm" in lower:
                    stderr_snippet = (
                        "Gemini authentication failed in headless mode. "
                        "Set GEMINI_API_KEY for the dashboard runtime."
                    )
                elif "fetch failed" in lower or "error generating content via api" in lower:
                    stderr_snippet = (
                        "Gemini API request failed (network/API). "
                        "Verify outbound internet access and API key validity."
                    )
                elif "no input provided via stdin" in lower:
                    stderr_snippet = "Gemini CLI did not receive prompt input via stdin."
                else:
                    stderr_snippet = best_line[:500]

            logger.error(f"{provider} error: {stderr_text}")
            if _stream_thread:
                _stream_thread.join(timeout=3)
            if _agent_id and _comp:
                try: _comp(_agent_id, error=f"cli_error:{stderr_snippet[:80]}")
                except Exception as e: logger.debug("complete_agent failed: %s", e)
            return None, f"cli_error:{stderr_snippet}"

    except Exception as e:
        with _active_claude_lock:
            _active_claude_process = None
        logger.error(f"Error invoking LLM provider: {e}")
        return None, f"exception:{str(e)}"


def _find_balanced_json(text: str) -> Optional[str]:
    """
    Find a balanced JSON object by counting braces, handling strings correctly.

    This function correctly handles nested JSON structures by tracking brace
    depth rather than using regex patterns that fail on nested braces.

    NOTE: This uses a "first-brace-wins" strategy -- it starts scanning from the
    first '{' found in the text. If the text contains non-JSON content before the
    actual JSON (e.g., markdown fences with a '{' in commentary), this may extract
    the wrong object. Callers should strip obvious non-JSON prefixes before calling,
    or validate the returned JSON and retry with a different start position if needed.

    Args:
        text: Input text that may contain JSON

    Returns:
        Extracted JSON string if found and balanced, None otherwise
    """
    if not isinstance(text, str):
        return None

    # O(N) linear pass: collect all top-level braced candidates in one scan,
    # then try json.loads on each (typically ≤ 5 candidates).
    # Replaces the previous O(N²) approach where _max_attempts scaled with len(text)
    # and each attempt re-scanned the text from the beginning.
    candidates: list[tuple[int, int]] = []  # (start, end+1) of each top-level {} block
    depth = 0
    in_string = False
    escape_next = False
    current_start = -1

    for i, char in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if char == '\\' and in_string:
            escape_next = True
            continue
        # Special case: '{' at depth==0 resets string-tracking state.
        # Pre-brace text is not JSON, so unmatched quotes in preamble
        # (e.g. 'use the "key" {...}') must not corrupt in_string tracking
        # for the actual JSON object. The reset must happen BEFORE the
        # in_string guard so that '{' is not skipped when in_string=True.
        if char == '{' and depth == 0:
            in_string = False
            escape_next = False
            current_start = i
            depth += 1
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and current_start != -1:
                    candidates.append((current_start, i + 1))
                    current_start = -1

    for start, end in candidates:
        candidate = text[start:end]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Try with trailing comma cleanup before skipping this candidate
            cleaned = _cleanup_trailing_commas(candidate)
            try:
                json.loads(cleaned)
                return cleaned  # Return the cleaned version that actually parsed
            except json.JSONDecodeError:
                continue  # Try the next top-level candidate

    return None  # No valid JSON found


def _cleanup_trailing_commas(json_str: str) -> str:
    """
    Remove trailing commas outside of JSON string values.

    Claude sometimes produces JSON with trailing commas which are valid in
    JavaScript but not in strict JSON. Uses a character-level tokenizer that
    tracks whether the current position is inside a string, so commas inside
    string values (e.g. {"pattern": ",}"}) are never removed.

    Args:
        json_str: JSON string that may have trailing commas

    Returns:
        Cleaned JSON string with structural trailing commas removed
    """
    if not isinstance(json_str, str):
        return ""
    result = []
    in_string = False
    i = 0
    n = len(json_str)
    while i < n:
        ch = json_str[i]
        if in_string:
            result.append(ch)
            if ch == '\\' and i + 1 < n:
                # Consume escape sequence without toggling string state
                i += 1
                result.append(json_str[i])
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
                result.append(ch)
            elif ch == ',':
                # Look ahead past whitespace and other commas; if next non-ws/non-comma
                # char is } or ], skip this comma (handles double trailing commas too).
                j = i + 1
                while j < n and json_str[j] in ' \t\r\n,':
                    j += 1
                if j < n and json_str[j] in ']}':
                    pass  # Drop the trailing comma (and any redundant commas before it)
                else:
                    result.append(ch)
            else:
                result.append(ch)
        i += 1
    return ''.join(result)


def extract_json_from_response(text: str) -> Optional[dict]:
    """
    Extract JSON from Claude's response.

    Handles:
    - Clean JSON strings
    - JSON in markdown code blocks (```json ... ``` or ``` ... ```)
    - JSON embedded in prose text
    - Trailing commas in JSON
    - Nested JSON objects and arrays

    The extraction uses a multi-strategy approach:
    1. Direct parse (for clean JSON)
    2. Code block extraction (grabs content between fences)
    3. Balanced brace matching (for prose-embedded JSON)
    4. Trailing comma cleanup at each stage

    Args:
        text: Raw response text from Claude

    Returns:
        Parsed JSON dict if extraction successful, None otherwise
    """
    if not text or not isinstance(text, str):
        return None

    # Strategy 1: Try direct parse (clean JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Extract from markdown code block
    # Match ```json ... ``` or ``` ... ``` (with or without language label)
    code_block_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', text)
    if code_block_match:
        block_content = code_block_match.group(1).strip()
        try:
            return json.loads(block_content)
        except json.JSONDecodeError:
            # Try with trailing comma cleanup
            cleaned = _cleanup_trailing_commas(block_content)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    # Strategy 3: Find balanced JSON object in prose using brace-counting
    # This correctly handles nested JSON structures
    json_str = _find_balanced_json(text)
    if json_str:
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            # Try with trailing comma cleanup
            cleaned = _cleanup_trailing_commas(json_str)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass

    logger.warning(f"Could not extract JSON from response: {text[:200]}...")
    return None


# =============================================================================
# HAIKU-POWERED HANDOFF SUMMARIES
# =============================================================================

HANDOFF_MODEL = "claude-sonnet-4-5-20250929"
HAIKU_TIMEOUT = 10  # seconds
HAIKU_MAX_TOKENS = 500

HAIKU_HANDOFF_PROMPT = """You are generating a handoff summary for a Claude session that is ending due to context limits. Summarize what was being worked on concisely.

Mission: $mission_id
Mission Objective: $mission_objective
Stage: $stage
Recent activity context:
$recent_context

Format your response EXACTLY as:
**Working on:** [what was being built/fixed - one line]
**Completed:** [what was finished - one line]
**In progress:** [what was partially done - one line]
**Next:** [immediate next steps - one line]
**Decisions:** [key decisions made - one line]

Be concise. Each line should be under 100 characters. Stay focused on the Mission Objective above."""


def invoke_haiku_summary(
    mission_id: str,
    stage: str,
    recent_context: str,
    mission_objective: str = "",
    timeout: int = HAIKU_TIMEOUT
) -> Optional[str]:
    """
    Invoke active LLM provider to generate an intelligent handoff summary.

    Args:
        mission_id: Current mission ID
        stage: Current stage (BUILDING, TESTING, etc.)
        recent_context: Recent activity context (last messages, files modified, etc.)
        mission_objective: The actual mission description/problem statement
        timeout: API call timeout in seconds

    Returns:
        Formatted summary string, or None on failure
    """
    try:
        provider = get_llm_provider()
        import string as _string
        _tmpl = _string.Template(HAIKU_HANDOFF_PROMPT)
        prompt = _tmpl.safe_substitute(
            mission_id=mission_id,
            mission_objective=mission_objective or "No mission objective available.",
            stage=stage,
            recent_context=recent_context or "No recent activity context available."
        )

        logger.info(f"Invoking Haiku summary with provider: {provider}")
        command = build_llm_command(
            provider,
            model=HANDOFF_MODEL if provider == "claude" else get_llm_model(provider)
        )

        haiku_env = {k: v for k, v in os.environ.items()
                     if k in _SAFE_ENV_EXACT or k.startswith(_SAFE_ENV_PREFIXES)}
        haiku_env.pop("CLAUDECODE", None)
        result = subprocess.run(
            command,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
            env=haiku_env,
            start_new_session=True
        )

        if result.returncode == 0:
            summary = result.stdout.strip()
            # Handle Gemini CLI JSON wrapper
            if provider == "gemini":
                try:
                    data = json.loads(summary)
                    if isinstance(data, dict) and "response" in data:
                        summary = data["response"]
                except json.JSONDecodeError:
                    pass

            if summary:
                logger.info(f"{provider} generated handoff summary ({len(summary)} chars)")
                return summary
            else:
                logger.warning(f"{provider} returned empty handoff summary")
                return None
        else:
            logger.error(f"{provider} handoff summary error: {result.stderr}")
            return None

    except subprocess.TimeoutExpired:
        logger.warning(f"Handoff summary call timed out after {timeout}s")
        return None
    except Exception as e:
        logger.error(f"Error generating handoff summary: {e}")
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
        if n_messages <= 0:
            return "No recent messages."
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
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "completed_mission_id": completed_mission_id,
            **metrics
        }
        with open(RETRY_METRICS_LOG_PATH, 'a') as f:
            f.write(json.dumps(log_entry) + "\n")
    except Exception as _exc:
        # Silent failure - metrics logging should never disrupt mission flow
        logger.debug("_log_retry_metrics failed: %s", _exc)


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
    # Rule 1: Reject non-dict types, None, empty dict, or falsy values
    if not isinstance(mission, dict) or not mission:
        return False

    # Rule 2: Check for required mission_id field (also reject whitespace-only)
    mission_id = mission.get("mission_id")
    if not mission_id or not isinstance(mission_id, str) or not mission_id.strip():
        return False

    # Rule 3 & 4: Check for meaningful problem statement (not just placeholder)
    # Use .strip() to handle whitespace-only strings like "   \n\t  "
    problem = mission.get("problem_statement", "")
    if not isinstance(problem, str):
        return False
    if not problem or not problem.strip() or problem.strip().lower() == "no mission defined. please set a mission.":
        return False

    return True


def _calculate_backoff_interval(attempt: int, base_interval: float = 1.0, max_interval: float = 300.0) -> float:
    """Calculate exponential backoff interval for retry attempts.

    Uses exponential backoff (1s, 2s, 4s, ...) to handle intermittent network
    issues gracefully. This reduces hammering on slow endpoints while still
    detecting fast auto-advances quickly.

    Args:
        attempt: Zero-indexed attempt number (0 = first attempt)
        base_interval: Base interval in seconds (default 1.0)
        max_interval: Upper bound on returned interval (default 300.0s / 5 min)

    Returns:
        Sleep duration in seconds for this attempt, capped at max_interval

    Formula:
        interval = min(base_interval * (2 ^ attempt), max_interval)

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
    try:
        attempt = max(0, int(attempt))
    except (OverflowError, ValueError):
        attempt = 0
    base_interval = max(0.0, float(base_interval))
    max_interval = max(0.0, float(max_interval))
    # Cap attempt to prevent 2**attempt overflow (2**30 * any base > any sane max_interval)
    attempt = min(attempt, 30)
    return min(base_interval * (2 ** attempt), max_interval)


def _mission_content_hash(mission: dict) -> str:
    """Return a short hash of mission content fields for re-submission detection."""
    import hashlib as _hashlib
    if not isinstance(mission, dict):
        return _hashlib.md5(b"").hexdigest()[:16]
    problem_statement = str(mission.get("problem_statement") or "")
    mission_id = str(mission.get("mission_id") or "")
    key = problem_statement + "|" + mission_id
    return _hashlib.md5(key.encode("utf-8", errors="replace")).hexdigest()[:16]


def _wait_for_new_mission_with_retry(
    controller,
    completed_mission_id: str,
    max_retries: int = 3,
    base_interval: float = 1.0,
    max_total_wait: float = None,
    use_exponential_backoff: bool = True,
    completed_mission_content_hash: str = None
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
       - Written by af_engine/orchestrator.py's StageOrchestrator._create_mission_from_queue_item()

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

        # Validate the mission is not empty or invalid
        if not _is_valid_mission(controller.mission):
            logger.debug(f"Invalid/empty mission detected, skipping (attempt {attempt + 1})")
        elif new_mission_id != completed_mission_id or (
            # Same mission_id: detect re-submission by comparing content hash
            new_mission_id == completed_mission_id
            and completed_mission_content_hash is not None
            and _mission_content_hash(controller.mission) != completed_mission_content_hash
        ):
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
            time_module.sleep(interval)

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


def _mark_source_suggestion_status(controller, status: str) -> None:
    """Best-effort lifecycle update for missions created from suggestions."""
    try:
        from suggestion_lifecycle import (
            mission_id_from_mission,
            mark_suggestion_status,
            source_recommendation_id_from_mission,
        )
        mission = getattr(controller, "mission", {}) or {}
        rec_id = source_recommendation_id_from_mission(mission)
        if rec_id:
            if status == "open":
                try:
                    if hasattr(controller, "state") and hasattr(controller.state, "set_field"):
                        controller.state.set_field("failed", True)
                        controller.state.set_field("suggestion_status_returned", "open")
                    elif hasattr(controller, "set_field"):
                        controller.set_field("failed", True)
                        controller.set_field("suggestion_status_returned", "open")
                    else:
                        controller.mission["failed"] = True
                        controller.mission["suggestion_status_returned"] = "open"
                except Exception:
                    logger.debug("Failed to persist source suggestion failure marker", exc_info=True)
            mark_suggestion_status(
                rec_id,
                status,
                mission_id=mission_id_from_mission(mission),
                closed_reason="completed" if status == "completed" else "failed",
            )
    except Exception:
        logger.debug("Source suggestion lifecycle update failed", exc_info=True)


def get_mission_workspace(controller) -> Path:
    """Get the working directory for the current mission.

    Returns the mission-specific workspace if available, otherwise falls back to global workspace.
    """
    mission_workspace = controller.mission.get("mission_workspace")
    if mission_workspace:
        if not isinstance(mission_workspace, (str, os.PathLike)):
            logger.warning(
                "Ignoring mission_workspace with invalid type: %s",
                type(mission_workspace).__name__,
            )
            return WORKSPACE_DIR
        try:
            workspace_path = Path(mission_workspace).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            logger.warning("Ignoring invalid mission_workspace %r: %s", mission_workspace, exc)
            return WORKSPACE_DIR
        if workspace_path.is_dir():
            return workspace_path
        logger.warning("Ignoring mission_workspace that is not a directory: %s", workspace_path)
    return WORKSPACE_DIR


def _log_parse_failure(stage: str, raw_text: str, attempt: int, controller) -> None:
    """Log a JSON parse failure to both journal and chat for diagnostics.

    Called whenever the response adapter produces a synthetic fallback response.
    Provides visibility into parse failures via the dashboard chat and the
    persistent journal file.
    """
    mission_id = controller.mission.get("mission_id", "unknown") if controller else "unknown"
    preview = (raw_text or "")[:200]

    append_journal({
        "type": "json_parse_failure",
        "stage": stage,
        "attempt": attempt,
        "max_attempts": MAX_PARSE_FAILURES,
        "mission_id": mission_id,
        "raw_response_preview": preview,
        "resolution": "fallback_adapter",
    })

    send_to_chat(
        f"[WARN:JSON_PARSE] Stage {stage}: Response was not valid JSON "
        f"(attempt {attempt}/{MAX_PARSE_FAILURES}). Using adapter fallback. "
        f"Preview: {preview[:100]}..."
    )


def _looks_like_testing_wait_response(text: str) -> bool:
    """Return True when TESTING exited with a monitor/wakeup wait message."""
    if not isinstance(text, str) or not text.strip():
        return False
    lower = text.lower()
    has_wait = any(term in lower for term in ("wait", "waiting", "i'll wait", "i will wait"))
    has_async_marker = any(
        term in lower
        for term in ("monitor", "wakeup", "schedulewakeup", "notification")
    )
    return has_wait and has_async_marker


def _testing_artifact_preview(controller, max_chars: int = 1000) -> str:
    """Read a short preview of artifacts/test_results.md for fallback context."""
    try:
        workspace = get_mission_workspace(controller)
        results_path = workspace / "artifacts" / "test_results.md"
        if not results_path.exists():
            return ""
        preview = results_path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        return f"Latest test_results.md exists at {results_path}. Preview:\n{preview}"
    except Exception as e:
        logger.debug("Could not read TESTING artifact preview: %s", e)
        return ""


def construct_testing_wait_fallback_response(response_text: str, controller) -> Dict[str, Any]:
    """Convert TESTING wait/prose output into a structured stage response.

    The conductor runs each stage as a one-shot process. If an agent exits after
    saying it will wait for Monitor/ScheduleWakeup, retrying usually repeats the
    same prose and exhausts JSON parse retries. This fallback keeps the mission
    alive by moving to ANALYZING with a clear tests_error payload.
    """
    artifact_preview = _testing_artifact_preview(controller)
    raw_preview = (response_text or "")[:500]
    summary = (
        "TESTING returned a monitor/wakeup wait message instead of the required "
        "JSON response. AtlasForge converted it to tests_error so ANALYZING can "
        "decide whether to accept existing artifacts or request another testing pass."
    )
    if artifact_preview:
        summary = f"{summary}\n\n{artifact_preview}"

    return {
        "status": "tests_error",
        "self_tests": [],
        "adversarial_testing": {
            "red_team_issues": [],
            "red_team_agent_count": 0,
            "red_team_duration_seconds": 0,
            "property_violations": [],
            "mutation_score": None,
            "spec_alignment": None,
            "epistemic_score": 0.0,
            "rigor_level": "insufficient",
        },
        "summary": summary,
        "success_criteria_met": [],
        "success_criteria_failed": [
            "TESTING stage did not return strict JSON to the conductor"
        ],
        "issues_to_fix": [
            "Do not finish TESTING with Monitor/ScheduleWakeup waiting prose; "
            "wait synchronously and return the required JSON object."
        ],
        "message_to_human": (
            "TESTING produced a wait/monitor message instead of JSON; "
            "converted to tests_error for analysis. Preview: "
            f"{raw_preview}"
        ),
    }


def _red_team_report_count(workspace: Path) -> int:
    """Count non-empty red-team report artifacts in a mission workspace."""
    try:
        tests_dir = Path(workspace) / "tests"
        json_reports = [
            p for p in tests_dir.glob("red_team_agent_*.json")
            if p.is_file() and p.stat().st_size > 0
        ]
        md_reports = [
            p for p in (tests_dir / "Red_Team").glob("agent*_findings.md")
            if p.is_file() and p.stat().st_size > 0
        ]
        # Use the larger count because the runner may produce either format
        # depending on streaming/timeout path.
        return max(len(json_reports), len(md_reports))
    except Exception as exc:
        logger.debug("Could not count red-team reports: %s", exc)
        return 0


def _requires_existing_plan_missing(mission: Optional[dict], current_stage: str) -> bool:
    """Return True when the active profile requires a plan but none exists."""
    if current_stage != "BUILDING" or not isinstance(mission, dict):
        return False
    try:
        from af_engine.mission_profiles import requires_existing_plan as _req_plan
    except ImportError:
        return False
    if not _req_plan(mission):
        return False
    workspace = mission.get("mission_workspace") or mission.get("project_workspace")
    if not workspace:
        return True
    return not (Path(workspace) / "artifacts" / "implementation_plan.md").exists()


def _redirect_disabled_profile_stage(
    mission: Optional[dict],
    current_stage: str,
    next_stage: Optional[str],
) -> Optional[str]:
    """Redirect disabled profile stages to the next enabled stage or COMPLETE."""
    if not next_stage or next_stage == "COMPLETE" or not isinstance(mission, dict):
        return next_stage
    try:
        from af_engine.mission_profiles import (
            stage_allowed_for_mission as _stage_allowed,
            next_enabled_stage as _next_enabled,
        )
    except ImportError:
        return next_stage

    if _stage_allowed(mission, next_stage):
        return next_stage

    redirected = _next_enabled(mission, next_stage)
    if redirected is None and not _stage_allowed(mission, current_stage):
        redirected = _next_enabled(mission, current_stage)
    return redirected or "COMPLETE"


def _codex_red_team_payload(result: Any, workspace: Path, error: Optional[str] = None) -> Dict[str, Any]:
    """Normalize BlindAgentRedTeam output into TESTING JSON metadata."""
    stop_reasons = getattr(result, "stop_reasons", {}) if result is not None else {}
    if not isinstance(stop_reasons, dict):
        stop_reasons = {}
    timed_out_agents = [
        agent for agent, reason in stop_reasons.items()
        if "timeout" in str(reason).lower() or "error" in str(reason).lower()
    ]
    report_count = _red_team_report_count(workspace)
    agent_count = max(len(stop_reasons), report_count)
    duration_seconds = 0.0
    if result is not None:
        try:
            duration_seconds = round(float(getattr(result, "duration_ms", 0) or 0) / 1000, 1)
        except (TypeError, ValueError):
            duration_seconds = 0.0
    issues = []
    if result is not None:
        for finding in getattr(result, "findings", []) or []:
            title = getattr(finding, "title", None) or str(finding)
            severity = getattr(finding, "severity", "")
            affected = getattr(finding, "affected_code", "")
            issues.append(
                " ".join(part for part in [f"[{severity}]" if severity else "", title, affected] if part)
            )
    all_completed = (
        agent_count >= CODEX_TESTING_RED_TEAM_AGENTS
        and report_count >= CODEX_TESTING_RED_TEAM_AGENTS
        and len(timed_out_agents) == 0
        and not error
    )
    completion = {
        "agent_reports_collected": report_count,
        "all_agents_completed": all_completed,
        "agents_reached_report_phase": report_count,
        "timed_out_agents": timed_out_agents,
        "stop_reasons": stop_reasons,
    }
    return {
        "red_team_issues": issues,
        "red_team_agent_count": agent_count,
        "red_team_duration_seconds": duration_seconds,
        "red_team_completion": completion,
        "red_team_error": error,
    }


def _write_codex_red_team_artifact(workspace: Path, payload: Dict[str, Any]) -> Optional[Path]:
    try:
        artifacts_dir = Path(workspace) / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = artifacts_dir / "codex_red_team_preflight.json"
        path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
        return path
    except Exception as exc:
        logger.warning("Failed to write Codex red-team preflight artifact: %s", exc)
        return None


def run_codex_testing_red_team_preflight(controller, workspace: Path) -> Dict[str, Any]:
    """Run the Codex-only official red-team preflight before the tester pass."""
    mission = controller.mission if hasattr(controller, "mission") else {}
    mission_desc = str(
        mission.get("problem_statement")
        or mission.get("original_problem_statement")
        or "AtlasForge mission"
    )
    send_to_chat(
        "[CODEX TESTING] Launching 3-agent red-team preflight "
        f"({CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS // 60}m timeout, early close on completion)"
    )
    try:
        from adversarial_testing.blind_agent_runner import BlindAgentRedTeam
        team = BlindAgentRedTeam(
            n_agents=CODEX_TESTING_RED_TEAM_AGENTS,
            timeout=CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS,
            safety_timeout=CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS,
        )
        result = team.launch_parallel_team(
            workspace_dir=Path(workspace),
            mission_desc=mission_desc,
            n_agents=CODEX_TESTING_RED_TEAM_AGENTS,
            timeout=CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS,
            safety_timeout=CODEX_TESTING_RED_TEAM_TIMEOUT_SECONDS,
        )
        payload = _codex_red_team_payload(result, workspace)
    except Exception as exc:
        logger.warning("Codex red-team preflight failed: %s", exc, exc_info=True)
        payload = _codex_red_team_payload(None, workspace, error=str(exc))

    artifact_path = _write_codex_red_team_artifact(workspace, payload)
    if artifact_path:
        payload["artifact_path"] = str(artifact_path)
    send_to_chat(
        "[CODEX TESTING] Red-team preflight complete: "
        f"agents={payload.get('red_team_agent_count', 0)} "
        f"reports={payload.get('red_team_completion', {}).get('agent_reports_collected', 0)} "
        f"issues={len(payload.get('red_team_issues', []))}"
    )
    return payload


def _testing_runner_enabled(mission: Optional[dict]) -> bool:
    """Return whether the runner-owned TESTING pipeline should replace tester pass."""
    if not _env_flag_enabled("ATLASFORGE_TESTING_RUNNER", default=True):
        return False
    if isinstance(mission, dict) and mission.get("disable_testing_runner") is True:
        return False
    return True


def run_atlasforge_testing_runner(controller, workspace: Path) -> Dict[str, Any]:
    """Run the lead-planned TestingRunner and return strict TESTING JSON."""
    try:
        from af_engine.stages.testing_runner import (
            TestingRunner,
            TestingRunnerConfig,
        )

        stage_context = controller._build_stage_context()
        timeout_minutes = 45
        try:
            timeout_minutes = int(os.environ.get("ATLASFORGE_TESTING_RUNNER_TIMEOUT_MINUTES", "45"))
        except (TypeError, ValueError):
            timeout_minutes = 45
        max_lanes = ATLASFORGE_TESTING_RUNNER_DEFAULT_MAX_LANES
        try:
            max_lanes = int(os.environ.get("ATLASFORGE_TESTING_RUNNER_MAX_LANES", str(max_lanes)))
        except (TypeError, ValueError):
            max_lanes = ATLASFORGE_TESTING_RUNNER_DEFAULT_MAX_LANES

        config = TestingRunnerConfig(
            mission_id=stage_context.mission_id,
            mission=stage_context.mission,
            mission_text=stage_context.problem_statement or stage_context.original_mission,
            workspace_dir=Path(workspace),
            artifacts_dir=Path(stage_context.artifacts_dir),
            tests_dir=Path(stage_context.tests_dir),
            max_lanes=max(ATLASFORGE_TESTING_RUNNER_DEFAULT_MAX_LANES, max_lanes),
            timeout_minutes=max(5, timeout_minutes),
        )
        send_to_chat(
            "[TESTING] Launching TestingRunner "
            f"({config.max_lanes} lanes, {config.timeout_minutes}m budget)"
        )
        runner = TestingRunner(config)
        result = runner.run(progress_callback=lambda msg: send_to_chat(f"[TESTING] {msg}"))
        send_to_chat(
            "[TESTING] TestingRunner complete: "
            f"{result.get('status')} ({result.get('summary', '')})"
        )
        return result
    except Exception as exc:
        logger.error("TestingRunner failed", exc_info=True)
        send_to_chat(f"[TESTING] TestingRunner failed: {exc}")
        artifacts_dir = Path(workspace) / "artifacts" / "testing"
        try:
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            error_path = artifacts_dir / "result.json"
            fallback = {
                "status": "tests_error",
                "self_tests": [],
                "adversarial_testing": {
                    "red_team_issues": [],
                    "red_team_agent_count": 0,
                    "red_team_duration_seconds": 0,
                    "red_team_completion": {
                        "agent_reports_collected": 0,
                        "agents_reached_report_phase": 0,
                        "all_agents_completed": False,
                        "timed_out_agents": [],
                    },
                    "mutation_score": None,
                    "mutation_testing": {
                        "status": "error",
                        "summary": str(exc),
                        "issues": [],
                    },
                    "epistemic_score": 0.0,
                    "rigor_level": "insufficient",
                },
                "mutation_testing": {
                    "status": "error",
                    "summary": str(exc),
                    "issues": [],
                },
                "summary": f"TestingRunner failed before completing lanes: {exc}",
                "success_criteria_met": [],
                "success_criteria_failed": ["TestingRunner execution failed"],
                "issues_to_fix": [f"Restore TESTING runner execution: {exc}"],
                "message_to_human": f"TestingRunner failed before completing lanes: {exc}",
                "testing_runner": {
                    "schema": "testing-runner-v1",
                    "artifact_dir": str(artifacts_dir),
                    "result_path": str(error_path),
                    "error": str(exc),
                },
            }
            error_path.write_text(json.dumps(fallback, indent=2, default=str) + "\n", encoding="utf-8")
            return fallback
        except Exception:
            return {
                "status": "tests_error",
                "summary": f"TestingRunner failed before completing lanes: {exc}",
                "message_to_human": f"TestingRunner failed before completing lanes: {exc}",
                "issues_to_fix": [f"Restore TESTING runner execution: {exc}"],
                "adversarial_testing": {
                    "red_team_agent_count": 0,
                    "red_team_completion": {
                        "agent_reports_collected": 0,
                        "agents_reached_report_phase": 0,
                        "all_agents_completed": False,
                        "timed_out_agents": [],
                    },
                },
            }


def codex_testing_preflight_prompt(payload: Dict[str, Any]) -> str:
    """Prompt addendum for Codex tester after AtlasForge red-team preflight."""
    return f"""

## CODEX TESTING PREFLIGHT RESULTS

AtlasForge already ran the official Codex red-team preflight before launching
this tester pass. Use these results as mandatory input before running your own
verification.

```json
{json.dumps(payload, indent=2, default=str)}
```

Required next actions:
- Read the referenced red-team artifacts.
- Reproduce/refute the findings.
- Run your own targeted tests after reviewing the red-team output.
- Run a quick mutation check where practical. If mutation testing is not
  practical, include a concrete `mutation_unavailable_reason`.
- Include `adversarial_testing.red_team_completion` in your final JSON.
"""


def merge_codex_red_team_payload(response: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure Codex TESTING response carries AtlasForge red-team metadata."""
    if not isinstance(response, dict) or not payload:
        return response
    merged = dict(response)
    adversarial = merged.get("adversarial_testing")
    if not isinstance(adversarial, dict):
        adversarial = {}
    for key, value in payload.items():
        if key in {"artifact_path"}:
            continue
        if key not in adversarial or adversarial.get(key) in (None, "", [], {}):
            adversarial[key] = value
    if payload.get("artifact_path"):
        adversarial.setdefault("red_team_artifact_path", payload["artifact_path"])
    merged["adversarial_testing"] = adversarial
    return merged


def run_rd_mode(takeover: bool = False, force: bool = False):
    """
    Run Claude in directed R&D mode.

    Uses the RDMissionController to guide Claude through:
    PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE

    Args:
        takeover: If True, attempt graceful takeover of existing conductor
        force: If True, force takeover with SIGKILL if needed
    """
    logger.info("=" * 60)
    logger.info("CLAUDE AUTONOMOUS - R&D MODE")
    logger.info("=" * 60)

    # Acquire exclusive lock to prevent multiple conductors
    if HAS_ENHANCED_CONDUCTOR:
        # Load mission info for lock metadata
        mission = io_utils.atomic_read_json(STATE_DIR / "mission.json", {})
        state = load_state()
        if not acquire_conductor_lock_enhanced(
            takeover=takeover,
            force=force,
            mission_id=mission.get('mission_id'),
            current_stage=mission.get('current_stage'),
            boot_count=state.get('boot_count', 0)
        ):
            logger.error("Failed to acquire conductor lock - another instance may be running")
            send_to_chat("[ERROR] Another conductor instance is already running. Use --takeover to restart.")
            return
    else:
        # Fallback to basic locking
        if not acquire_conductor_lock():
            logger.error("Failed to acquire conductor lock - another instance may be running")
            send_to_chat("[ERROR] Another conductor instance is already running. Exiting.")
            return

    save_pid()

    state = load_state()
    state["mode"] = "rd"
    state["boot_count"] = state.get("boot_count", 0) + 1
    state["last_boot"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    # Install enhanced signal handlers for graceful takeover support
    if HAS_ENHANCED_CONDUCTOR:
        def shutdown_callback():
            """Save mission state on shutdown signal."""
            try:
                save_state(state)
                send_to_chat("[SHUTDOWN] Mission state saved.")
            except Exception as e:
                logger.error(f"Shutdown callback error: {e}")

        setup_enhanced_signal_handlers(shutdown_callback)

    send_to_chat(f"AtlasForge starting Mission Launch #{state['boot_count']}")
    provider = get_llm_provider()
    send_to_chat(f"[LLM] Launch command ({provider}): {_llm_command_preview(provider)}")

    # Initialize R&D controller
    controller = atlasforge_engine.RDMissionController()

    # Preload KB for faster first query (~1.6s savings)
    try:
        from af_engine.kb_cache import preload_kb
        preload_kb()
        logger.info("KB preloaded for faster first query")
    except Exception as e:
        logger.debug("KB preload failed (non-critical, lazy-load works): %s", e)

    cycle_count = 0
    timeout_retries = 0  # Track consecutive timeout/transport failures
    parse_retries = 0  # Track consecutive parse-only retries (no-adapter path), separate from timeout_retries
    parse_failure_count = 0  # Track consecutive JSON parse failures (separate from transport)
    empty_response_count = 0  # Track consecutive empty responses to prevent infinite loops
    total_empty_response_count = 0  # Cumulative empty responses — prevents alternating bypass of halt condition
    _announced_mission_id = None  # Track which mission we've announced to avoid duplicate announcements

    try:
        while _running.is_set():
            cycle_count += 1
            state["total_cycles"] = state.get("total_cycles", 0) + 1

            current_stage = (controller.mission.get("current_stage", "PLANNING") or "PLANNING").upper()

            # Mission-type profile stage gate: if the current stage is not enabled
            # for this mission, advance to the next enabled stage or stop.
            try:
                from af_engine.mission_profiles import (
                    stage_allowed_for_mission, next_enabled_stage
                )
                if (current_stage != "COMPLETE"
                        and not stage_allowed_for_mission(controller.mission, current_stage)):
                    nxt = next_enabled_stage(controller.mission, current_stage)
                    if nxt:
                        logger.info(
                            "[mission_profile] Stage %s not enabled — advancing to %s",
                            current_stage, nxt,
                        )
                        controller.update_stage(nxt)
                        continue
                    # No later enabled stage: complete the mission rather than
                    # silently running a disabled stage. (Reach here even if
                    # stop_after_profile_complete is False — running a stage
                    # the profile excluded would violate the profile contract.)
                    logger.warning(
                        "[mission_profile] Stage %s not enabled and no later enabled stage exists — marking COMPLETE",
                        current_stage,
                    )
                    controller.update_stage("COMPLETE")
                    continue
            except ImportError:
                pass

            # Sync live-editable params from disk (picks up dashboard PATCH changes)
            try:
                controller.sync_live_params()
            except Exception:
                pass

            # Announce mission start parameters once per mission (when mission_id changes)
            _current_mission_id = controller.mission.get("mission_id")
            if _current_mission_id and _current_mission_id != _announced_mission_id:
                _cb = controller.mission.get("cycle_budget", 1)
                _mi = controller.mission.get("max_iterations", 10)
                _mt_label = controller.mission.get("mission_type_label") or "Full R&D"
                _enabled = controller.mission.get("enabled_stages") or [
                    "PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END",
                ]
                _stages_str = ",".join(_enabled) if isinstance(_enabled, list) else str(_enabled)
                send_to_chat(
                    f"[MISSION] {_current_mission_id} | type={_mt_label} | "
                    f"enabled_stages={_stages_str} | cycle_budget={_cb} cycles | "
                    f"max_iterations={_mi} per cycle"
                )
                _announced_mission_id = _current_mission_id

            # build_only / requires_existing_plan pre-flight: if the active
            # profile demands an existing plan and we're entering BUILDING,
            # abort cleanly when the plan is missing.
            try:
                if _requires_existing_plan_missing(controller.mission, current_stage):
                    send_to_chat(
                        "[PROFILE] build_only requires existing artifacts/implementation_plan.md; "
                        "not found — completing mission."
                    )
                    controller.update_stage("COMPLETE")
                    continue
            except Exception as _e_pre:
                logger.warning("requires_existing_plan preflight error: %s", _e_pre)

            logger.info(f"=== R&D Cycle {cycle_count} | Stage: {current_stage} ===")

            # Check for graceful shutdown request (takeover in progress)
            if HAS_ENHANCED_CONDUCTOR and is_shutdown_requested():
                logger.info("Shutdown requested, completing current operation...")
                send_to_chat("[SHUTDOWN] Graceful shutdown requested. Completing current operation.")
                break

            # Check for human interrupt
            human_msg = check_human_message()
            if human_msg:
                human_prompt = human_msg.get("prompt") or ""
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
                if controller.mission.get("halted_due_to_drift") or controller.mission.get("failed"):
                    _mark_source_suggestion_status(controller, "open")
                else:
                    _mark_source_suggestion_status(controller, "completed")

                # Record completed mission to memory
                mission_summary = {
                    "mission_id": completed_mission_id,
                    "problem": controller.mission.get("original_problem_statement") or controller.mission.get("problem_statement", "")[:200],
                    "iterations": controller.mission.get("iteration", 0),
                    "total_cycles": total_cycles,
                    "completed_at": datetime.now(timezone.utc).isoformat()
                }
                add_to_memory("mission_history", json.dumps(mission_summary))

                # ROBUST: Use retry loop with file-based signaling to detect new missions
                # This handles the case where auto-advance HTTP calls take longer than expected
                #
                # Uses exponential backoff (1s -> 2s -> 4s) to reduce load on slow networks
                # while still detecting fast auto-advances quickly on the first poll.
                #
                # Graceful degradation: If signal file fails, falls back to mission.json polling
                _completed_content_hash = _mission_content_hash(controller.mission)
                new_mission_detected, retry_metrics = _wait_for_new_mission_with_retry(
                    controller,
                    completed_mission_id,
                    max_retries=4,           # More retries with backoff
                    base_interval=1.0,       # Start at 1s, then 2s, 4s, 8s
                    max_total_wait=15.0,     # Cap total wait at 15 seconds
                    completed_mission_content_hash=_completed_content_hash,
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

            # Inject mission-type profile prompt modifier under a clear header.
            # Done before any provider-specific append so all providers see it.
            mission_profile = controller.mission.get("mission_profile")
            modifier = (
                mission_profile.get("prompt_modifier")
                if isinstance(mission_profile, dict)
                else None
            )
            if modifier:
                prompt = f"{prompt.rstrip()}\n\n## MISSION TYPE INSTRUCTIONS\n{modifier}\n"

            if resolve_llm_provider(controller.mission) == "codex":
                prompt = f"{prompt.rstrip()}\n{codex_stage_guard_prompt(current_stage, controller.mission)}"

            # Get the appropriate workspace for this mission
            workspace = get_mission_workspace(controller)
            logger.info(f"Using workspace: {workspace}")

            stage_provider = resolve_llm_provider(controller.mission)
            codex_red_team_payload: Optional[Dict[str, Any]] = None
            testing_runner_response: Optional[Dict[str, Any]] = None
            if (
                stage_provider == "codex"
                and current_stage == "TESTING"
                and not _testing_runner_enabled(controller.mission)
            ):
                codex_red_team_payload = run_codex_testing_red_team_preflight(
                    controller,
                    workspace,
                )
                prompt = (
                    f"{prompt.rstrip()}\n"
                    f"{codex_testing_preflight_prompt(codex_red_team_payload)}"
                )

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
                    send_to_chat(f"[HAIKU] Context limit detected ({signal.cache_creation:,} tokens). Invoking Haiku for intelligent handoff summary...")
                    recent_context = get_recent_chat_context(n_messages=5)
                    mission_objective = controller.mission.get("problem_statement", "")
                    haiku_summary = invoke_haiku_summary(mission_id, current_stage, recent_context, mission_objective)

                    if haiku_summary:
                        summary = f"""{haiku_summary}

**Handoff reason:** {StopReason.CONTEXT_GRACEFUL.value}
**Token stats:** {signal.tokens_used:,} total (cache_creation: {signal.cache_creation:,}, cache_read: {signal.cache_read:,})"""
                        send_to_chat(f"[CONTEXT] Graceful handoff at {signal.tokens_used:,} tokens. Haiku wrote HANDOFF.md.")
                    else:
                        summary = f"""**Working on:** Stage {current_stage}
**Handoff reason:** {StopReason.CONTEXT_GRACEFUL.value}
**Tokens used:** {signal.tokens_used:,} (cache_creation: {signal.cache_creation:,}, cache_read: {signal.cache_read:,})
**Next:** Continue from current stage with fresh context"""
                        send_to_chat(f"[CONTEXT] Graceful handoff at {signal.tokens_used:,} tokens. HANDOFF.md written.")

                    write_handoff_state(str(workspace), mission_id, current_stage, summary)

                elif signal.level == HandoffLevel.TIME_BASED:
                    # Time-based handoff: this is the FALLBACK circuit-breaker.
                    # It should only fire when work budget telemetry is unavailable
                    # or when a session is genuinely stalled. It is NOT the primary
                    # stop reason for healthy sessions (token_first policy suppresses it).
                    elapsed_min = signal.elapsed_minutes if signal.elapsed_minutes else 55.0
                    send_to_chat(f"[HAIKU] Time fallback at {elapsed_min:.1f} min. Invoking Haiku for intelligent handoff summary...")
                    recent_context = get_recent_chat_context(n_messages=5)
                    mission_objective = controller.mission.get("problem_statement", "")
                    haiku_summary = invoke_haiku_summary(mission_id, current_stage, recent_context, mission_objective)

                    if haiku_summary:
                        summary = f"""{haiku_summary}

**Handoff reason:** {StopReason.TIME_FALLBACK.value} (time-based circuit-breaker)
**Elapsed time (secondary):** {elapsed_min:.1f} minutes"""
                        send_to_chat(f"[CONTEXT] Time fallback at {elapsed_min:.1f} minutes. Haiku wrote HANDOFF.md.")
                    else:
                        summary = f"""**Working on:** Stage {current_stage}
**Handoff reason:** {StopReason.TIME_FALLBACK.value} (time-based circuit-breaker)
**Elapsed time (secondary):** {elapsed_min:.1f} minutes
**Next:** Continue from current stage with fresh context"""
                        send_to_chat(f"[CONTEXT] Time fallback at {elapsed_min:.1f} minutes. HANDOFF.md written.")

                    write_handoff_state(str(workspace), mission_id, current_stage, summary)

                elif signal.level == HandoffLevel.EMERGENCY:
                    send_to_chat(f"[CONTEXT] EMERGENCY handoff at {signal.tokens_used:,} tokens!")

                # Terminate the active Claude subprocess so invoke_llm() returns immediately
                # This is critical: without this, the handoff writes HANDOFF.md but the
                # Claude subprocess keeps running until the 3600s timeout hits.
                terminated = terminate_active_claude()
                if terminated:
                    logger.info(f"Terminated Claude subprocess for {signal.level.value} handoff")

            # Start ContextWatcher if available
            # Pass current_stage so ActivityAwareHandoffMonitor is used for
            # long-running stages (TESTING, BUILDING) instead of fixed 55min timer
            #
            # WATCHER_POLICY=token_first (default): time monitor is demoted to fallback.
            # The conductor's work budget (WorkBudgetManager) drives session length.
            # Time monitor only fires for genuinely hung/stalled processes.
            _watcher_policy = os.environ.get("WATCHER_POLICY", "token_first").lower()
            _enable_time_handoff = (
                TIME_BASED_HANDOFF_ENABLED
                if _watcher_policy != "token_first"
                else False   # token_first: suppress time monitor, use budget instead
            )
            if HAS_CONTEXT_WATCHER:
                try:
                    watcher = get_context_watcher()
                    context_session_id = watcher.start_watching(
                        str(workspace),
                        on_context_handoff,
                        enable_time_handoff=_enable_time_handoff,
                        stage=current_stage
                    )
                    if context_session_id:
                        logger.info(
                            f"ContextWatcher started for session {context_session_id} "
                            f"(stage={current_stage}, policy={_watcher_policy}, "
                            f"time_handoff={_enable_time_handoff})"
                        )
                except Exception as e:
                    logger.warning(f"Failed to start ContextWatcher: {e}")

            # WorkBudgetManager: token-primary session control (policy=token_first)
            _work_budget_mgr: Optional["WorkBudgetManager"] = None
            if HAS_WORK_BUDGET_MANAGER and _watcher_policy == "token_first":
                try:
                    _detected_provider = resolve_llm_provider(controller.mission)
                    _detected_model = get_llm_model(_detected_provider, mission=controller.mission) or ""
                    _work_budget_mgr = WorkBudgetManager(model=_detected_model)
                    logger.info(
                        "WorkBudgetManager active: provider=%s model=%s budget=%d tokens",
                        _detected_provider,
                        _detected_model or "(unset)", _work_budget_mgr.budget_tokens,
                    )
                except Exception as _wbm_err:
                    logger.warning("Failed to create WorkBudgetManager: %s", _wbm_err)

            # Adaptive subprocess timeout: TESTING/BUILDING get longer timeouts
            # to match the activity-aware handoff monitor's extended window
            llm_timeout = 3600  # Default 1 hour
            if current_stage == "TESTING":
                llm_timeout = MAX_ABSOLUTE_TIMEOUT_MINUTES * 60 if HAS_CONTEXT_WATCHER else 3600
            elif current_stage == "BUILDING":
                llm_timeout = 7200  # 2 hours for builds
            if current_stage == "TESTING" and _testing_runner_enabled(controller.mission):
                testing_runner_response = run_atlasforge_testing_runner(controller, workspace)
                response_text = json.dumps(testing_runner_response, indent=2, default=str)
                error_info = None
            # Use hierarchical parallel execution for complex BUILDING stages
            elif _should_use_parallel(current_stage, controller.mission):
                parallel_response, parallel_error = _run_hierarchical_building(
                    prompt, controller.mission, workspace, stage=current_stage, timeout=llm_timeout
                )
                if parallel_response:
                    response_text = parallel_response
                    error_info = None
                    logger.info("[Parallel] Hierarchical building succeeded — using aggregated response")
                else:
                    logger.info(f"[Parallel] Falling back to single agent ({parallel_error})")
                    response_text, error_info = invoke_llm(prompt, timeout=llm_timeout, cwd=workspace, stage=current_stage, mission=controller.mission)
            else:
                response_text, error_info = invoke_llm(prompt, timeout=llm_timeout, cwd=workspace, stage=current_stage, mission=controller.mission)

            # WorkBudgetManager check: determine if session should continue or stop.
            # Context pressure (ContextWatcher) takes precedence and has already
            # set handoff_triggered if it fired. Only check work budget when no
            # context handoff occurred.
            _session_stop_reason: Optional[str] = None
            if _work_budget_mgr is not None and not handoff_triggered.is_set():
                try:
                    # Estimate output tokens from response length (//4 chars-to-tokens)
                    _resp_tokens = len(response_text) // 4 if response_text else 0
                    _wbd = _work_budget_mgr.check(
                        _work_budget_mgr.output_tokens + _resp_tokens
                    )
                    logger.info(
                        "WorkBudgetManager check: action=%s reason=%s pct=%.1f%% "
                        "output=%d budget=%d",
                        _wbd.action, _wbd.reason or "none", _wbd.pct,
                        _wbd.output_tokens, _wbd.budget_tokens,
                    )
                    if _wbd.action == "stop":
                        _session_stop_reason = _wbd.reason
                        # Write HANDOFF.md with token-centric stop reason
                        if write_handoff_state is not None:
                            try:
                                _mission_id = controller.mission.get("mission_id", "unknown")
                                _token_summary = (
                                    f"Handoff reason: {_wbd.reason} "
                                    f"(output={_wbd.output_tokens:,}, budget={_wbd.budget_tokens:,}, "
                                    f"pct={_wbd.pct:.0f}%)"
                                )
                                write_handoff_state(
                                    str(workspace), _mission_id, current_stage, _token_summary
                                )
                                logger.info(
                                    "[CONDUCTOR] WorkBudget stop: %s — HANDOFF.md written",
                                    _wbd.reason,
                                )
                            except Exception as _hw_err:
                                logger.debug("WorkBudget HANDOFF.md write failed: %s", _hw_err)
                        send_to_chat(
                            f"[BUDGET] Work budget {_wbd.reason}: "
                            f"{_wbd.output_tokens:,}/{_wbd.budget_tokens:,} tokens "
                            f"({_wbd.pct:.0f}%). Starting fresh session."
                        )
                except Exception as _wbm_check_err:
                    logger.debug("WorkBudgetManager check failed: %s", _wbm_check_err)

            # Act on work budget stop decision — break loop so fresh session starts.
            if _session_stop_reason is not None:
                logger.info("[CONDUCTOR] Breaking session loop: stop_reason=%s", _session_stop_reason)
                break

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

            # =========================================================
            # BUG FIX: Handle empty response with rc=0 (not an error)
            # =========================================================
            # Empty stdout with rc=0 is valid (rare but possible). This should
            # NOT increment timeout_retries or trigger 3-strike halt.
            if not response_text and not error_info:
                empty_response_count += 1
                total_empty_response_count += 1
                logger.warning(f"Empty Claude response #{empty_response_count} with rc=0 (not an error) [total={total_empty_response_count}]")
                # Log to CLI error tracker for trend analysis
                try:
                    from workspace.contextWatcher_Error_Tracking.cli_error_logger import (
                        get_cli_error_logger, CLIEventType
                    )
                    cli_logger = get_cli_error_logger()
                    cli_logger.log_empty_response(
                        mission_id=controller.mission.get("mission_id"),
                        stage=current_stage,
                        cycle=cycle_count
                    )
                except ImportError:
                    pass  # CLI error logger not available

                append_journal({
                    "type": "empty_response_warning",
                    "stage": current_stage,
                    "mission_id": controller.mission.get("mission_id"),
                    "note": f"rc=0 with empty stdout #{empty_response_count} - not counting as error"
                })

                if empty_response_count >= 3 or total_empty_response_count >= MAX_CLAUDE_RETRIES * 2:
                    logger.error(
                        f"Empty response loop: consecutive={empty_response_count}, "
                        f"total={total_empty_response_count} — treating as retriable error"
                    )
                    send_to_chat(f"[WARN] {empty_response_count} consecutive / {total_empty_response_count} total empty responses in {current_stage}")
                    timeout_retries += 1
                    empty_response_count = 0
                    if timeout_retries >= MAX_CLAUDE_RETRIES:
                        logger.error(f"Empty response loop exhausted {MAX_CLAUDE_RETRIES} retries")
                        try:
                            controller.update_stage("COMPLETE")
                            _mark_source_suggestion_status(controller, "open")
                            send_to_chat(f"[FATAL] Mission halted — empty response loop in {current_stage}")
                        except Exception:
                            pass
                        break

                time.sleep(5)
                continue

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

                    # Map handoff level to RestartReason for consistent formatting
                    if handoff_level == "graceful":
                        restart_reason = RestartReason.CONTEXT_EXHAUSTION
                        extra_info = f"{handoff_signal.tokens_used:,} tokens" if handoff_signal else ""
                        logger.info("Graceful context handoff - NOT counting as error")
                    elif handoff_level == "time_based":
                        restart_reason = RestartReason.TIME_BASED_HANDOFF
                        elapsed = handoff_signal.elapsed_minutes if handoff_signal and handoff_signal.elapsed_minutes else 55.0
                        extra_info = f"{elapsed:.1f} min"
                        logger.info(f"Time-based handoff at {elapsed:.1f} min - NOT counting as error")
                    else:
                        restart_reason = RestartReason.CONTEXT_OVERFLOW
                        extra_info = "emergency"
                        logger.warning("Emergency handoff - NOT counting as error but flagging for review")

                    # Use consistent formatted restart message
                    restart_msg = format_restart_message(restart_reason, extra_info)
                    send_to_chat(restart_msg)

                    # Record in journal with handoff type and reason (distinguishable from errors)
                    append_journal({
                        "type": "graceful_handoff_restart",
                        "stage": current_stage,
                        "handoff_level": handoff_level,
                        "restart_reason": restart_reason.value,
                        "mission_id": controller.mission.get("mission_id"),
                        "error_info": error_info  # Include for diagnostics
                    })

                    # Log handoff to CLI error tracker for trend analysis
                    try:
                        from workspace.contextWatcher_Error_Tracking.cli_error_logger import (
                            get_cli_error_logger
                        )
                        cli_logger = get_cli_error_logger()
                        cli_logger.log_handoff(
                            mission_id=controller.mission.get("mission_id"),
                            stage=current_stage,
                            handoff_level=handoff_level,
                            cycle=cycle_count,
                            extra={
                                "restart_reason": restart_reason.value,
                                "extra_info": extra_info
                            }
                        )
                    except ImportError:
                        pass  # CLI error logger not available

                    # Reset handoff state for next iteration
                    handoff_triggered.clear()
                    handoff_signal_ref[0] = None

                    # Do NOT increment timeout_retries - this is expected behavior
                    time.sleep(5)  # Brief pause before restart
                    continue
                else:
                    # Real error - classify it for proper handling
                    error_reason, error_explanation = classify_error(error_info, response_text)

                    # Check for blocking errors (don't retry, halt immediately)
                    if is_blocking(error_reason):
                        error_msg = format_error_message(error_reason, error_explanation)
                        fatal_msg = format_fatal_message(error_reason, error_explanation)
                        send_to_chat(error_msg)
                        send_to_chat(fatal_msg)
                        send_to_chat(f"[ERROR] Stage: {current_stage}, Mission: {controller.mission.get('mission_id')}")
                        if error_info:
                            logger.error(f"Blocking error details: {error_info[:500]}")
                        logger.error(f"Blocking error: {error_reason.value} - {error_explanation}")

                        # Log blocking error to CLI error tracker
                        try:
                            from workspace.contextWatcher_Error_Tracking.cli_error_logger import (
                                get_cli_error_logger, CLIEventType
                            )
                            cli_logger = get_cli_error_logger()
                            cli_logger.log_blocking_error(
                                mission_id=controller.mission.get("mission_id"),
                                stage=current_stage,
                                error_category=error_reason.value,
                                error_info=error_info or "",
                                cycle=cycle_count
                            )
                        except ImportError:
                            pass  # CLI error logger not available

                        append_journal({
                            "type": "claude_blocking_error",
                            "stage": current_stage,
                            "error_category": error_reason.value,
                            "error_explanation": error_explanation,
                            "error_info": error_info,
                            "mission_id": controller.mission.get("mission_id")
                        })
                        try:
                            controller.update_stage("COMPLETE")
                            _mark_source_suggestion_status(controller, "open")
                            send_to_chat(f"[FATAL] Mission halted in {current_stage} — marking COMPLETE")
                        except Exception as e:
                            logger.error(f"Failed to update stage to COMPLETE during break cleanup: {e}")
                        break  # Exit loop - blocking errors don't retry

                    # Retriable error - increment counter
                    timeout_retries += 1

                    # Log to CLI error tracker for trend analysis
                    try:
                        from workspace.contextWatcher_Error_Tracking.cli_error_logger import (
                            get_cli_error_logger, CLIEventType
                        )
                        cli_logger = get_cli_error_logger()
                        cli_logger.log_cli_error(
                            mission_id=controller.mission.get("mission_id"),
                            stage=current_stage,
                            error_category=error_reason.value,
                            error_info=error_info or "",
                            cycle=cycle_count,
                            retry_count=timeout_retries,
                            resolution="retry" if timeout_retries < MAX_CLAUDE_RETRIES else "halt"
                        )
                    except ImportError:
                        pass  # CLI error logger not available

                    if timeout_retries >= MAX_CLAUDE_RETRIES:
                        logger.error(f"Claude failed {MAX_CLAUDE_RETRIES} times consecutively: {error_reason.value}")
                        fatal_msg = format_fatal_message(error_reason, error_explanation, MAX_CLAUDE_RETRIES)
                        send_to_chat(fatal_msg)
                        send_to_chat(f"[ERROR] Stage: {current_stage}, Mission: {controller.mission.get('mission_id')}")
                        if error_info:
                            logger.error(f"Retry-exhaustion error details: {error_info[:500]}")
                        append_journal({
                            "type": "claude_timeout_failure",
                            "stage": current_stage,
                            "retries": timeout_retries,
                            "error_category": error_reason.value,
                            "error_explanation": error_explanation,
                            "error_info": error_info,
                            "mission_id": controller.mission.get("mission_id")
                        })
                        try:
                            controller.update_stage("COMPLETE")
                            _mark_source_suggestion_status(controller, "open")
                            send_to_chat(f"[FATAL] Mission halted in {current_stage} after {timeout_retries} retries — marking COMPLETE")
                        except Exception:
                            pass
                        break  # Exit loop - mission needs intervention

                    # Log retriable error with attempt count
                    error_msg = format_error_message(error_reason, error_explanation, timeout_retries - 1, MAX_CLAUDE_RETRIES)
                    send_to_chat(error_msg)
                    logger.warning(f"Error ({error_reason.value}): {error_explanation}, retrying ({timeout_retries}/{MAX_CLAUDE_RETRIES})")
                    # Exponential backoff: 10s, 20s, 40s...
                    backoff_time = min(60, 10 * (2 ** (timeout_retries - 1)))
                    time.sleep(backoff_time)
                    continue

            # Parse response - hardened with fallback adapter
            response = extract_json_from_response(response_text)
            if (
                response
                and current_stage == "TESTING"
                and codex_red_team_payload
                and stage_provider == "codex"
            ):
                response = merge_codex_red_team_payload(response, codex_red_team_payload)

            if (not response
                    and current_stage == "TESTING"
                    and _looks_like_testing_wait_response(response_text)):
                response = construct_testing_wait_fallback_response(response_text, controller)
                append_journal({
                    "type": "testing_wait_fallback",
                    "stage": current_stage,
                    "mission_id": controller.mission.get("mission_id"),
                    "raw_response_preview": (response_text or "")[:500],
                })
                logger.warning(
                    "TESTING returned monitor/wakeup wait prose; using structured tests_error fallback"
                )

            if not response and HAS_RESPONSE_ADAPTER:
                # Layer 1 failed - try format correction re-prompt (one shot)
                logger.warning("JSON extraction failed, attempting format correction re-prompt")
                correction_prompt = build_format_correction_prompt(current_stage, response_text)
                try:
                    corrected_text, _corr_err = invoke_llm(
                        correction_prompt,
                        timeout=120,
                        cwd=WORKSPACE_DIR,
                        stage=current_stage,
                        mission=controller.mission,
                    )
                    if corrected_text:
                        response = extract_json_from_response(corrected_text)
                        if response:
                            logger.info("Format correction re-prompt succeeded")
                except Exception as e:
                    logger.warning(f"Format correction re-prompt failed: {e}")

            if not response:
                # Layer 2 failed (or adapter not available) - use deterministic fallback
                parse_failure_count += 1

                if HAS_RESPONSE_ADAPTER:
                    response = construct_fallback_response(current_stage, response_text)
                    if response is None:
                        logger.error(
                            f"construct_fallback_response returned None for stage {current_stage}"
                        )
                        response = {}
                    logger.warning(
                        f"Using fallback adapter for stage {current_stage} "
                        f"(parse failure {parse_failure_count}/{MAX_PARSE_FAILURES})"
                    )
                else:
                    # No adapter available - fall back to old behavior
                    # Use parse_retries (separate from timeout_retries) to avoid conflating
                    # parse failures with transport-level timeout/error retries.
                    parse_retries += 1
                    if parse_retries >= MAX_CLAUDE_RETRIES:
                        logger.error(f"Claude failed {MAX_CLAUDE_RETRIES} times to produce valid JSON (no adapter)")
                        try:
                            controller.update_stage("COMPLETE")
                            _mark_source_suggestion_status(controller, "open")
                            send_to_chat(f"[FATAL] Mission halted in {current_stage} — JSON parse failures exhausted retries")
                        except Exception as e:
                            logger.error(f"Failed to update stage to COMPLETE after parse retries: {e}")
                        break
                    append_journal({
                        "type": "rd_raw_response",
                        "stage": current_stage,
                        "response": response_text[:1000]
                    })
                    backoff_time = min(60, 10 * (2 ** (parse_retries - 1)))
                    time.sleep(backoff_time)
                    continue

                # Rich diagnostics - journal + chat (always, even with fallback)
                _log_parse_failure(current_stage, response_text, parse_failure_count, controller)

                if parse_failure_count >= MAX_PARSE_FAILURES:
                    send_to_chat(
                        f"[WARN:PARSE] {parse_failure_count} consecutive non-JSON responses. "
                        f"Using fallback adapter to keep mission alive."
                    )
                    # Halt if fallback adapter is also failing — prevents infinite loop
                    if parse_failure_count >= MAX_PARSE_FAILURES * 2:
                        logger.error(f"Halting: {parse_failure_count} consecutive parse failures with fallback adapter")
                        try:
                            controller.update_stage("COMPLETE")
                            _mark_source_suggestion_status(controller, "open")
                            send_to_chat(f"[FATAL] Mission halted — fallback adapter exhausted after {parse_failure_count} failures")
                        except Exception as e:
                            logger.error(f"Failed to halt after fallback exhaustion: {e}")
                        break
            else:
                # Successful JSON parse - reset parse failure counter and parse retries
                parse_failure_count = 0
                parse_retries = 0

                # Reset timeout counter only on successful parse (not on fallback responses)
                # to prevent infinite loops when LLM consistently produces unparseable output
                timeout_retries = 0
                empty_response_count = 0

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

            # Mission-type profile: if the handler picked a stage outside the
            # profile's enabled_stages, short-circuit to the next enabled stage
            # (or COMPLETE) BEFORE update_stage fires events for the disabled stage.
            redirected_stage = _redirect_disabled_profile_stage(
                controller.mission,
                current_stage,
                next_stage,
            )
            if redirected_stage != next_stage:
                logger.info(
                    "[mission_profile] Handler picked disabled stage %s — redirecting to %s",
                    next_stage,
                    redirected_stage,
                )
                next_stage = redirected_stage

            # Update stage if changed
            if next_stage != current_stage:
                controller.update_stage(next_stage)
                cycle_info = f" (Cycle {controller.mission.get('current_cycle', 1)}/{controller.mission.get('cycle_budget', 1)})"
                send_to_chat(f"Stage transition: {current_stage} -> {next_stage}{cycle_info}")

                # Update lock metadata with current stage
                if HAS_ENHANCED_CONDUCTOR:
                    update_conductor_state(
                        mission_id=controller.mission.get('mission_id'),
                        current_stage=next_stage
                    )

            # Save state
            save_state(state)

            # Brief pause between cycles
            time.sleep(5)

    except Exception as e:
        logger.error(f"R&D Mode error: {e}", exc_info=True)
        _mark_source_suggestion_status(controller, "open")
        send_to_chat("R&D Error: an unexpected error occurred. Check logs for details.")
    finally:
        save_state(state)
        remove_pid()
        if HAS_ENHANCED_CONDUCTOR:
            release_conductor_lock_enhanced()
        else:
            release_conductor_lock()
        logger.info("Claude Autonomous R&D Mode stopped")


# =============================================================================
# FREE MODE (Original autonomous behavior)
# =============================================================================

def build_free_mode_prompt(state: dict) -> str:
    """Build prompt for free exploration mode."""
    return f"""You are Claude, running AUTONOMOUSLY on a home server.
You are not responding to a human - you are THINKING and WORKING on your own.

CURRENT TIME: {datetime.now(timezone.utc).isoformat()}
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
    provider = get_llm_provider()
    provider_label = provider.capitalize()

    logger.info("=" * 60)
    logger.info(f"{provider_label} AUTONOMOUS - FREE MODE")
    logger.info("=" * 60)

    save_pid()

    state = load_state()
    state["mode"] = "free"
    state["boot_count"] = state.get("boot_count", 0) + 1
    state["last_boot"] = datetime.now(timezone.utc).isoformat()
    save_state(state)

    send_to_chat(f"{provider_label} Free Mode starting (Boot #{state['boot_count']})")

    cycle_count = 0

    try:
        while _running.is_set():
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
            response_text, error_info = invoke_llm(prompt, timeout=1200, cwd=WORKSPACE_DIR)

            if not response_text:
                logger.warning(f"No response, retrying... ({error_info or 'no error info'})")
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
        send_to_chat("Error: an unexpected error occurred. Check logs for details.")
    finally:
        save_state(state)
        remove_pid()
        logger.info("Claude Autonomous Free Mode stopped")


# =============================================================================
# MAIN
# =============================================================================

def _run_release_pipeline() -> None:
    """
    Execute the release pipeline and exit.

    Reads --push-tags, --dry-run, --bump, --publish-pypi from sys.argv.
    Wraps scripts/release_pipeline.ReleasePipeline and exits with 0/1.
    """
    try:
        _scripts_path = str(Path(__file__).resolve().parent / "scripts")
        if _scripts_path not in sys.path:
            sys.path.insert(0, _scripts_path)
        from release_pipeline import ReleasePipeline, get_repo_root
    except ImportError as e:
        print(f"ERROR: Could not import release_pipeline: {e}")
        sys.exit(1)

    push_tags = "--push-tags" in sys.argv
    dry_run = "--dry-run" in sys.argv
    publish_pypi = "--publish-pypi" in sys.argv
    bump_override: Optional[str] = None
    for arg in sys.argv[1:]:
        if arg.startswith("--bump="):
            bump_override = arg.split("=", 1)[1].strip()
            break

    repo_root = get_repo_root()
    pipeline = ReleasePipeline(
        repo_root=repo_root,
        push_tags=push_tags,
        dry_run=dry_run,
        bump_override=bump_override,
        publish_pypi=publish_pypi,
    )
    success = pipeline.run()
    sys.exit(0 if success else 1)


def main():
    """Main entry point."""
    # --release is an early-exit convenience wrapper around scripts/release_pipeline.py.
    # It must be checked before mode dispatch so it does not start the mission engine.
    if "--release" in sys.argv:
        _run_release_pipeline()
        return  # _run_release_pipeline always calls sys.exit; this is unreachable

    if HAS_ENHANCED_CONDUCTOR:
        # Use enhanced argument parsing with takeover support
        args = parse_conductor_args()

        if args.mode == ConductorMode.CHECK_STATUS:
            show_conductor_status()
            sys.exit(0)

        if args.mode == ConductorMode.RD:
            run_rd_mode(takeover=args.takeover, force=args.force_takeover)
        elif args.mode == ConductorMode.FREE:
            run_free_mode()
        else:
            print(f"Unknown mode: {args.mode}")
            sys.exit(1)
    else:
        # Fallback to basic arg parsing
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
            print("Usage: python3 atlasforge_conductor.py --mode=rd|free [--release [--push-tags] [--dry-run] [--bump=minor|patch]]")
            sys.exit(1)


if __name__ == "__main__":
    logger.info("Conductor main entry point reached.")
    main()
