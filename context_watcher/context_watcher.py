#!/usr/bin/env python3
"""
ContextWatcher: Real-Time JSONL Token Monitor for AtlasForge Conductor

Monitors Claude's live transcript files (~/.claude/projects/) and detects
context exhaustion to trigger early handoffs. This prevents wasted time
from 30+ minute timeouts when Claude fills its context window.

Detection Logic:
    Context exhaustion is detected by the pattern:
    - cache_creation_input_tokens > 130K AND cache_read_input_tokens < 5K

    This pattern means Claude is building NEW context at the limit,
    not reusing cached context. It's hitting the wall.

Thresholds:
    GRACEFUL (130K): Haiku writes HANDOFF.md, Claude self-terminates
    EMERGENCY (140K): Conductor kills Claude immediately

Architecture:
    ContextWatcher (singleton)
        |
        ├── SessionMonitor 1 → workspace-A/*.jsonl
        ├── SessionMonitor 2 → workspace-B/*.jsonl
        └── ... (dynamic scaling)

Usage:
    from context_watcher import get_context_watcher, HandoffLevel

    watcher = get_context_watcher()
    session_id = watcher.start_watching(
        workspace_path="/path/to/workspace",
        callback=lambda signal: handle_handoff(signal)
    )

    # Later:
    watcher.stop_watching(session_id)
"""

import json
import logging
import math
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Set, Tuple
import uuid

# Try to import watchdog for efficient file monitoring
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = object
    FileModifiedEvent = None
    FileCreatedEvent = None

logger = logging.getLogger(__name__)

# =============================================================================
# CONFIGURATION
# =============================================================================

# Transcript source directories
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"

# Provider configuration
DEFAULT_LLM_PROVIDER = "claude"
SUPPORTED_LLM_PROVIDERS = {"claude", "codex", "gemini"}

# Token thresholds (legacy hardcoded — used as fallback when model is unknown)
GRACEFUL_THRESHOLD = 130_000  # Trigger HANDOFF.md generation
EMERGENCY_THRESHOLD = 140_000  # Kill Claude immediately
EARLY_FAILURE_THRESHOLD = 2_000  # Warning for startup issues (not exhaustion)

# Ratio-based thresholds for Claude provider (scales with context window)
CLAUDE_GRACEFUL_CONTEXT_RATIO = 0.85  # 85% of context window
CLAUDE_EMERGENCY_CONTEXT_RATIO = 0.92  # 92% of context window

# Model context window lookup (tokens)
# Used to compute dynamic thresholds when model is detected from JSONL
MODEL_CONTEXT_WINDOWS = {
    # Opus 4.6 — 1M context
    "claude-opus-4-6": 1_000_000,
    "claude-opus-4-6-20250514": 1_000_000,
    # Sonnet 4.6
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-6-20250514": 200_000,
    # Haiku 4.5
    "claude-haiku-4-5": 200_000,
    "claude-haiku-4-5-20251001": 200_000,
    # Legacy Opus 4 (200k)
    "claude-opus-4-20250514": 200_000,
    # Legacy Sonnet 4.5
    "claude-sonnet-4-5-20250514": 200_000,
    # Legacy 3.x models
    "claude-3-5-sonnet-20241022": 200_000,
    "claude-3-5-haiku-20241022": 200_000,
    "claude-3-opus-20240229": 200_000,
    "claude-3-sonnet-20240229": 200_000,
    "claude-3-haiku-20240307": 200_000,
}

CODEX_MODEL_CONTEXT_WINDOWS = {
    "gpt-5": 1_000_000,
}

# Cache pattern indicating context exhaustion
# When cache_read is low, Claude is NOT reusing context (hitting the wall)
LOW_CACHE_READ_THRESHOLD = 5_000

# Monitoring intervals
POLL_INTERVAL = 2.0  # Seconds between polls (when not using watchdog)
CHECK_INTERVAL = 1.0  # Seconds between threshold checks
STALE_SESSION_TIMEOUT = 300  # 5 minutes without writes = stale session

# Feature flag
CONTEXT_WATCHER_ENABLED = os.environ.get(
    "CONTEXT_WATCHER_ENABLED", "1"
).lower() in ("1", "true", "yes")

# Time-based handoff settings
# Triggers a proactive handoff at 55 minutes to avoid 1-hour timeout
TIME_BASED_HANDOFF_ENABLED = os.environ.get(
    "TIME_BASED_HANDOFF_ENABLED", "1"
).lower() in ("1", "true", "yes")
_MAX_SAFE_INT_ENV = 10 ** 9  # 1 billion — prevents memory exhaustion from astronomical values


def _safe_int_env(name: str, default: int) -> int:
    """Read an integer environment variable, falling back to default on ValueError or non-positive value."""
    raw = os.environ.get(name, str(default))
    try:
        val = int(raw)
        if val <= 0:
            logger.warning("Env %s=%d is non-positive, clamping to default %d", name, val, default)
            return default
        if val > _MAX_SAFE_INT_ENV:
            logger.warning(
                "Env %s=%d exceeds safe upper bound %d, clamping to default %d",
                name, val, _MAX_SAFE_INT_ENV, default,
            )
            return default
        return val
    except ValueError:
        logger.warning("Env %s=%r is not a valid integer, using default %d", name, raw, default)
        return default


def _safe_nonneg_int_env(name: str, default: int) -> int:
    """Read an integer env var that may be 0, but rejects negative values with a warning."""
    raw = os.environ.get(name, str(default))
    try:
        val = int(raw)
        if val < 0:
            logger.warning("Env %s=%d is negative, clamping to default %d", name, val, default)
            return default
        if val > _MAX_SAFE_INT_ENV:
            logger.warning(
                "Env %s=%d exceeds safe upper bound %d, clamping to default %d",
                name, val, _MAX_SAFE_INT_ENV, default,
            )
            return default
        return val
    except ValueError:
        logger.warning("Env %s=%r is not a valid integer, using default %d", name, raw, default)
        return default


_MAX_SAFE_FLOAT_ENV = 1e9  # 1 billion — prevents astronomically large timeouts/intervals

def _safe_float_env(name: str, default: float) -> float:
    """Read a float environment variable, falling back to default on ValueError, non-finite, negative, or out-of-range value."""
    raw = os.environ.get(name, str(default))
    try:
        val = float(raw)
        if not math.isfinite(val):
            logger.warning("Env %s=%r is non-finite, using default %s", name, raw, default)
            return default
        if val <= 0:
            logger.warning("Env %s=%f is non-positive, clamping to default %s", name, val, default)
            return default
        if val > _MAX_SAFE_FLOAT_ENV:
            logger.warning(
                "Env %s=%f exceeds safe upper bound %g, clamping to default %s",
                name, val, _MAX_SAFE_FLOAT_ENV, default,
            )
            return default
        return val
    except ValueError:
        logger.warning("Env %s=%r is not a valid float, using default %s", name, raw, default)
        return default

TIME_BASED_HANDOFF_MINUTES = _safe_int_env("TIME_BASED_HANDOFF_MINUTES", 55)

# ---------------------------------------------------------------------------
# WATCHER_POLICY — controls time monitor arming behaviour
#
#   token_first      (default) — suppress time-based handoff monitor;
#                                arm only MAX_ABSOLUTE_TIMEOUT_MINUTES fallback.
#                                WorkBudgetManager (conductor) drives session length.
#   legacy_time_first          — restore old behaviour exactly (time monitor always on).
#   context_only               — no time monitor at all, context pressure only.
#
# NOTE: emergency context pressure always wins regardless of policy.
# ---------------------------------------------------------------------------
_VALID_POLICIES = frozenset({"token_first", "legacy_time_first", "context_only"})
WATCHER_POLICY: str = os.environ.get("WATCHER_POLICY", "token_first").lower()
if WATCHER_POLICY not in _VALID_POLICIES:
    logger.warning(
        "Unknown WATCHER_POLICY=%r; defaulting to 'token_first'. "
        "Valid values: %s", WATCHER_POLICY, ", ".join(sorted(_VALID_POLICIES))
    )
    WATCHER_POLICY = "token_first"

# Stage-aware adaptive timeout: base timeout per stage (minutes)
STAGE_TIMEOUT_MINUTES = {
    "PLANNING": _safe_int_env("STAGE_TIMEOUT_PLANNING", 55),
    "BUILDING": _safe_int_env("STAGE_TIMEOUT_BUILDING", 120),
    "TESTING": _safe_int_env("STAGE_TIMEOUT_TESTING", 240),
    "ANALYZING": _safe_int_env("STAGE_TIMEOUT_ANALYZING", 55),
    "CYCLE_END": _safe_int_env("STAGE_TIMEOUT_CYCLE_END", 55),
    "COMPLETE": _safe_int_env("STAGE_TIMEOUT_COMPLETE", 55),
}
ACTIVITY_CHECK_INTERVAL_SECONDS = _safe_int_env("ACTIVITY_CHECK_INTERVAL_SECONDS", 30)
INACTIVITY_THRESHOLD_MINUTES = _safe_int_env("INACTIVITY_THRESHOLD_MINUTES", 15)
MAX_ABSOLUTE_TIMEOUT_MINUTES = _safe_int_env("MAX_ABSOLUTE_TIMEOUT_MINUTES", 360)
ACTIVITY_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".mypy_cache", ".pytest_cache", "venv", ".venv"}

# Subprocess activity detection (gate before handoff)
# When filesystem is quiet but child processes are burning CPU, defer the handoff.
try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False

SUBPROCESS_CHECK_ENABLED = os.environ.get(
    "SUBPROCESS_CHECK_ENABLED", "1"
).lower() in ("1", "true", "yes")

SUBPROCESS_CPU_THRESHOLD_PERCENT = _safe_float_env("SUBPROCESS_CPU_THRESHOLD_PERCENT", 1.0)

# Intelligent Subprocess Gate integration (Cycle 2)
_INTELLIGENT_GATE_AVAILABLE = False
try:
    import sys as _sys
    _gate_path = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                               'workspace', 'project_bbf2ba08')
    if os.path.isdir(_gate_path) and _gate_path not in _sys.path:
        _sys.path.insert(0, _gate_path)
    from orchestration.intelligent_gate import IntelligentSubprocessGate
    from interfaces.contracts import GateConfig as _GateConfig
    _INTELLIGENT_GATE_AVAILABLE = True
except ImportError:
    pass

# Codex scanning
CODEX_SCAN_INTERVAL_SECONDS = 10.0
CODEX_MAX_CANDIDATE_FILES = 500
CODEX_TRANSCRIPT_START_SLOP_SECONDS = _safe_nonneg_int_env("CODEX_TRANSCRIPT_START_SLOP_SECONDS", 30)
CODEX_CONTEXT_HANDOFF_ENABLED = os.environ.get(
    "CODEX_CONTEXT_HANDOFF_ENABLED", "1"
).lower() in ("1", "true", "yes", "on")
CODEX_GRACEFUL_CONTEXT_RATIO = _safe_float_env("CODEX_GRACEFUL_CONTEXT_RATIO", 0.92)
CODEX_EMERGENCY_CONTEXT_RATIO = _safe_float_env("CODEX_EMERGENCY_CONTEXT_RATIO", 0.97)
CODEX_GRACEFUL_HEADROOM_TOKENS = _safe_nonneg_int_env("CODEX_GRACEFUL_HEADROOM_TOKENS", 0)
CODEX_EMERGENCY_HEADROOM_TOKENS = _safe_nonneg_int_env("CODEX_EMERGENCY_HEADROOM_TOKENS", 0)
CODEX_STARTUP_GRACE_SECONDS = _safe_int_env("CODEX_STARTUP_GRACE_SECONDS", 90)
CODEX_MIN_OUTPUT_TOKENS_FOR_HANDOFF = _safe_int_env("CODEX_MIN_OUTPUT_TOKENS_FOR_HANDOFF", 2000)


def _normalize_provider(provider: Optional[str]) -> str:
    """Normalize provider name and fall back to default on unknown values."""
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    return DEFAULT_LLM_PROVIDER


def _load_provider_from_state() -> Optional[str]:
    """Load persisted provider from state/llm_provider.json if available."""
    state_path = Path(__file__).resolve().parent.parent / "state" / "llm_provider.json"
    if not state_path.exists():
        return None

    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    return data.get("provider")


def get_active_provider(provider: Optional[str] = None) -> str:
    """Resolve runtime provider with precedence: explicit -> env -> state -> default."""
    if provider:
        return _normalize_provider(provider)

    env_provider = os.environ.get("ATLASFORGE_LLM_PROVIDER")
    if env_provider:
        return _normalize_provider(env_provider)

    state_provider = _load_provider_from_state()
    if state_provider:
        return _normalize_provider(state_provider)

    return DEFAULT_LLM_PROVIDER


def _codex_context_window_for_model(model_name: str, reported_window: int = 0) -> int:
    """Return the effective Codex context window for threshold decisions."""
    normalized = str(model_name or "").strip().lower()
    for key in sorted(CODEX_MODEL_CONTEXT_WINDOWS, key=len, reverse=True):
        if normalized == key or normalized.startswith(f"{key}.") or normalized.startswith(f"{key}-"):
            return CODEX_MODEL_CONTEXT_WINDOWS[key]
    return reported_window


def _safe_nonnegative_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(default, parsed)


# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class WatcherMetrics:
    """Metrics for ContextWatcher performance analysis."""
    # Session tracking
    sessions_started: int = 0
    sessions_completed: int = 0
    sessions_active: int = 0

    # Handoff tracking
    total_handoffs: int = 0
    graceful_handoffs: int = 0
    emergency_handoffs: int = 0
    time_based_handoffs: int = 0

    # Timing metrics (in seconds)
    detection_latencies: List[float] = field(default_factory=list)
    avg_detection_latency: float = 0.0
    max_detection_latency: float = 0.0

    # Token metrics
    peak_tokens_seen: int = 0
    handoff_token_values: List[int] = field(default_factory=list)

    # Work budget fields (populated by WorkBudgetManager via conductor)
    work_budget_target: int = 0
    output_tokens_spent: int = 0
    continuation_count: int = 0
    diminishing_returns_signal: bool = False
    final_stop_reason: str = ""  # StopReason value: context_graceful | work_budget_complete | ...

    # Timestamps
    started_at: Optional[datetime] = None
    last_handoff_at: Optional[datetime] = None

    def record_detection_latency(self, latency_ms: float):
        """Record a detection latency measurement."""
        latency_s = latency_ms / 1000.0
        self.detection_latencies.append(latency_s)
        # Keep bounded
        if len(self.detection_latencies) > 100:
            self.detection_latencies = self.detection_latencies[-100:]
        # Update averages
        self.avg_detection_latency = sum(self.detection_latencies) / len(self.detection_latencies)
        self.max_detection_latency = max(self.max_detection_latency, latency_s)

    def record_handoff(self, level: "HandoffLevel", tokens: int):
        """Record a handoff event."""
        self.total_handoffs += 1
        if level.value == "graceful":
            self.graceful_handoffs += 1
        elif level.value == "time_based":
            self.time_based_handoffs += 1
        else:
            self.emergency_handoffs += 1
        self.handoff_token_values.append(tokens)
        self.last_handoff_at = datetime.now()
        if tokens > self.peak_tokens_seen:
            self.peak_tokens_seen = tokens

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary for JSON serialization."""
        return {
            "sessions": {
                "started": self.sessions_started,
                "completed": self.sessions_completed,
                "active": self.sessions_active
            },
            "handoffs": {
                "total": self.total_handoffs,
                "graceful": self.graceful_handoffs,
                "emergency": self.emergency_handoffs,
                "time_based": self.time_based_handoffs,
                "ratio": f"{self.graceful_handoffs}:{self.emergency_handoffs}:{self.time_based_handoffs}"
            },
            "timing": {
                "avg_detection_latency_s": round(self.avg_detection_latency, 3),
                "max_detection_latency_s": round(self.max_detection_latency, 3),
                "detection_samples": len(self.detection_latencies)
            },
            "tokens": {
                "peak_seen": self.peak_tokens_seen,
                "handoff_values": self.handoff_token_values[-10:] if self.handoff_token_values else []
            },
            "timestamps": {
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "last_handoff_at": self.last_handoff_at.isoformat() if self.last_handoff_at else None
            }
        }


class HandoffLevel(Enum):
    """Handoff urgency levels."""
    GRACEFUL = "graceful"  # Write HANDOFF.md, let Claude finish
    EMERGENCY = "emergency"  # Kill immediately
    TIME_BASED = "time_based"  # Proactive time-based handoff (55 min default)


@dataclass
class TokenState:
    """Token usage state from a single JSONL entry."""
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens_seen: int = 0
    model_context_window: int = 0
    model_name: str = ""
    timestamp: Optional[datetime] = None
    request_id: Optional[str] = None

    @property
    def total_context(self) -> int:
        """Total context tokens = cache_read + cache_creation + input."""
        return (
            self.cache_read_input_tokens +
            self.cache_creation_input_tokens +
            self.input_tokens
        )

    @classmethod
    def from_usage(cls, usage: Dict[str, Any], request_id: Optional[str] = None, model_name: str = "") -> "TokenState":
        """Create TokenState from JSONL usage dict."""
        def safe_int(value, default=0):
            """Safely convert value to int, returning default on failure."""
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            try:
                return int(value)
            except (ValueError, TypeError):
                return default

        # If model_context_window not in usage data, look it up from model name
        context_window = safe_int(usage.get("model_context_window", 0))
        if context_window <= 0 and model_name:
            context_window = MODEL_CONTEXT_WINDOWS.get(model_name, 0)

        return cls(
            cache_read_input_tokens=safe_int(usage.get("cache_read_input_tokens", 0)),
            cache_creation_input_tokens=safe_int(usage.get("cache_creation_input_tokens", 0)),
            input_tokens=safe_int(usage.get("input_tokens", 0)),
            output_tokens=safe_int(usage.get("output_tokens", 0)),
            total_tokens_seen=safe_int(usage.get("total_tokens", 0)),
            model_context_window=context_window,
            model_name=model_name,
            timestamp=datetime.now(),
            request_id=request_id
        )


@dataclass
class HandoffSignal:
    """Signal emitted when handoff threshold is reached."""
    level: HandoffLevel
    session_id: str
    workspace_path: str
    tokens_used: int
    cache_read: int
    cache_creation: int
    timestamp: datetime = field(default_factory=datetime.now)
    elapsed_minutes: Optional[float] = None  # For time-based handoffs

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "level": self.level.value,
            "session_id": self.session_id,
            "workspace_path": self.workspace_path,
            "tokens_used": self.tokens_used,
            "cache_read": self.cache_read,
            "cache_creation": self.cache_creation,
            "timestamp": self.timestamp.isoformat()
        }
        if self.elapsed_minutes is not None:
            result["elapsed_minutes"] = self.elapsed_minutes
        return result


# =============================================================================
# TIME-BASED HANDOFF MONITOR
# =============================================================================

class TimeBasedHandoffMonitor:
    """
    Monitors session duration and triggers handoff at configured time limit.

    Uses threading.Event for efficient waiting with clean cancellation.
    Fires callback once when time limit is reached.
    """

    def __init__(
        self,
        session_id: str,
        workspace_path: str,
        callback: Callable[[HandoffSignal], None],
        timeout_minutes: int = TIME_BASED_HANDOFF_MINUTES
    ):
        """
        Initialize time-based handoff monitor.

        Args:
            session_id: Unique session identifier
            workspace_path: Path to the workspace being monitored
            callback: Function to call when time limit is reached
            timeout_minutes: Minutes before triggering handoff (default 55)
        """
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.callback = callback
        self.timeout_minutes = timeout_minutes
        self.timeout_seconds = timeout_minutes * 60

        self._stop_event = threading.Event()
        self._fired = False
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[datetime] = None
        self._lock = threading.Lock()

    def start(self):
        """Start the time-based handoff monitor."""
        with self._lock:
            if self._thread is not None:
                return  # Already running

            self._started_at = datetime.now()
            self._stop_event.clear()
            self._fired = False
            self._cancelled = False

            self._thread = threading.Thread(
                target=self._timer_loop,
                daemon=True,
                name=f"TimeBasedHandoff-{self.session_id}"
            )
            self._thread.start()
            logger.info(
                f"Time-based handoff monitor started for session {self.session_id} "
                f"(timeout: {self.timeout_minutes} minutes)"
            )

    def _timer_loop(self):
        """Wait for timeout and fire callback if not cancelled.

        DEFENSE-IN-DEPTH: Before firing the callback, this method validates
        that the session is still active in the ContextWatcher. This catches
        edge cases where _cleanup_session() was called but the timer thread
        didn't get cancelled properly (e.g., timing race conditions).
        """
        # Wait for timeout or cancellation
        triggered = self._stop_event.wait(timeout=self.timeout_seconds)

        with self._lock:
            if triggered:
                # Was cancelled before timeout
                self._cancelled = True
                logger.debug(f"Time-based handoff cancelled for session {self.session_id}")
                return

            if self._fired:
                return  # Already fired (shouldn't happen)

            # DEFENSE-IN-DEPTH: Validate session is still active before firing
            # This catches zombie timers that weren't properly cancelled
            try:
                # Import here to avoid circular dependency at module load time
                global _watcher_instance
                if _watcher_instance is not None:
                    # Check without acquiring watcher lock to avoid deadlock
                    # (we already hold self._lock)
                    if self.session_id not in _watcher_instance._sessions:
                        logger.warning(
                            f"Time-based handoff for session {self.session_id} skipped: "
                            f"session no longer active (zombie timer detected and prevented)"
                        )
                        self._cancelled = True
                        return
            except Exception as e:
                # Fallback: proceed with handoff if validation fails
                # Better to fire a potentially stale handoff than miss a real one
                logger.debug(f"Could not validate session activity for {self.session_id}: {e}")

            self._fired = True

        # Timeout reached - fire callback
        elapsed_minutes = self.timeout_minutes
        if self._started_at:
            elapsed_seconds = (datetime.now() - self._started_at).total_seconds()
            elapsed_minutes = elapsed_seconds / 60

        logger.info(
            f"Time-based handoff triggered for session {self.session_id} "
            f"after {elapsed_minutes:.1f} minutes"
        )

        signal = HandoffSignal(
            level=HandoffLevel.TIME_BASED,
            session_id=self.session_id,
            workspace_path=self.workspace_path,
            tokens_used=0,  # Unknown at time-based trigger
            cache_read=0,
            cache_creation=0,
            elapsed_minutes=elapsed_minutes
        )

        try:
            self.callback(signal)
        except Exception as e:
            logger.error(f"Time-based handoff callback error: {e}")

    def cancel(self):
        """Cancel the time-based handoff monitor."""
        with self._lock:
            if self._fired:
                return  # Too late, already fired
            self._stop_event.set()

        if self._thread:
            self._thread.join(timeout=1.0)
            self._thread = None

        logger.debug(f"Time-based handoff monitor cancelled for session {self.session_id}")

    def stop(self):
        """Alias for cancel()."""
        self.cancel()

    @property
    def has_fired(self) -> bool:
        """Check if the handoff has already fired."""
        with self._lock:
            return self._fired

    @property
    def is_cancelled(self) -> bool:
        """Check if the monitor was cancelled."""
        with self._lock:
            return self._cancelled

    @property
    def elapsed_seconds(self) -> float:
        """Get elapsed time since start in seconds."""
        if self._started_at is None:
            return 0.0
        return (datetime.now() - self._started_at).total_seconds()

    @property
    def remaining_seconds(self) -> float:
        """Get remaining time before timeout in seconds."""
        return max(0.0, self.timeout_seconds - self.elapsed_seconds)

    def get_stats(self) -> Dict[str, Any]:
        """Get monitor statistics."""
        with self._lock:
            return {
                "session_id": self.session_id,
                "timeout_minutes": self.timeout_minutes,
                "elapsed_seconds": self.elapsed_seconds,
                "remaining_seconds": self.remaining_seconds,
                "fired": self._fired,
                "cancelled": self._cancelled,
                "started_at": self._started_at.isoformat() if self._started_at else None
            }


# =============================================================================
# ACTIVITY-AWARE HANDOFF MONITOR (ADAPTIVE TIMEOUT)
# =============================================================================

class ActivityAwareHandoffMonitor:
    """
    Monitors workspace file activity to decide when to trigger handoff.

    Instead of a fixed wall-clock timer, this monitor:
    1. Polls the workspace for recently modified files every N seconds
    2. Resets an inactivity counter whenever new file writes are detected
    3. Only fires the handoff when inactivity exceeds a configurable threshold
       AND the minimum stage-based runtime has elapsed
    4. Has an absolute maximum timeout as a hard safety ceiling

    This prevents long-running TESTING stages from being killed while the
    agent is still actively producing output (writing files, running tests).

    The monitor is model-agnostic: it watches filesystem activity, not
    LLM-specific signals, so it works with Claude, Codex, Gemini, or any
    other provider.
    """

    def __init__(
        self,
        session_id: str,
        workspace_path: str,
        callback: Callable[[HandoffSignal], None],
        stage: str = "TESTING",
        timeout_minutes: Optional[int] = None,
        inactivity_minutes: Optional[int] = None,
        max_absolute_minutes: Optional[int] = None,
    ):
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.callback = callback
        self.stage = stage.upper() if stage else "TESTING"

        # Resolve timeouts from stage config, explicit args, or defaults
        # Use "is None" check (not truthiness) so explicit 0 values are respected
        self.timeout_minutes = timeout_minutes if timeout_minutes is not None else STAGE_TIMEOUT_MINUTES.get(self.stage, TIME_BASED_HANDOFF_MINUTES)
        self.inactivity_minutes = inactivity_minutes if inactivity_minutes is not None else INACTIVITY_THRESHOLD_MINUTES
        self.max_absolute_minutes = max_absolute_minutes if max_absolute_minutes is not None else MAX_ABSOLUTE_TIMEOUT_MINUTES

        self._stop_event = threading.Event()
        self._fired = False
        self._cancelled = False
        self._thread: Optional[threading.Thread] = None
        self._started_at: Optional[datetime] = None
        self._last_activity_time: Optional[float] = None
        self._last_known_mtimes: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._monitored_pid: Optional[int] = None
        self._intelligent_gate = None  # Lazy-initialized IntelligentSubprocessGate
        self._workspace_path = Path(workspace_path) if workspace_path else None

        logger.info(
            f"ActivityAwareHandoffMonitor created for session {session_id}: "
            f"stage={self.stage}, base_timeout={self.timeout_minutes}min, "
            f"inactivity={self.inactivity_minutes}min, "
            f"max_absolute={self.max_absolute_minutes}min"
        )

    def start(self):
        """Start the activity-aware handoff monitor."""
        with self._lock:
            if self._thread is not None:
                return
            self._started_at = datetime.now()
            self._last_activity_time = time.time()
            self._stop_event.clear()
            self._fired = False
            self._cancelled = False

            self._thread = threading.Thread(
                target=self._activity_loop,
                daemon=True,
                name=f"ActivityAwareHandoff-{self.session_id}"
            )
            self._thread.start()
            logger.info(
                f"Activity-aware handoff monitor started for session {self.session_id} "
                f"(stage: {self.stage}, base timeout: {self.timeout_minutes}min, "
                f"inactivity threshold: {self.inactivity_minutes}min)"
            )

    def _scan_workspace_activity(self) -> float:
        """Scan workspace files for the most recent modification time.

        Returns the max mtime found across workspace files (up to 2 levels deep),
        ignoring directories in ACTIVITY_IGNORE_DIRS.
        """
        max_mtime = 0.0
        workspace = Path(self.workspace_path)
        if not workspace.exists():
            return max_mtime

        try:
            for depth0 in workspace.iterdir():
                if depth0.name in ACTIVITY_IGNORE_DIRS:
                    continue
                try:
                    st = depth0.stat()
                    if st.st_mtime > max_mtime:
                        max_mtime = st.st_mtime
                    if depth0.is_dir():
                        for depth1 in depth0.iterdir():
                            if depth1.name in ACTIVITY_IGNORE_DIRS:
                                continue
                            try:
                                st1 = depth1.stat()
                                if st1.st_mtime > max_mtime:
                                    max_mtime = st1.st_mtime
                                if depth1.is_dir():
                                    for depth2 in depth1.iterdir():
                                        if depth2.name in ACTIVITY_IGNORE_DIRS:
                                            continue
                                        try:
                                            st2 = depth2.stat()
                                            if st2.st_mtime > max_mtime:
                                                max_mtime = st2.st_mtime
                                        except OSError:
                                            continue
                            except OSError:
                                continue
                except OSError:
                    continue
        except OSError:
            pass

        return max_mtime

    def set_monitored_pid(self, pid: int):
        """Set the PID of the monitored LLM subprocess (thread-safe).

        When set, _has_active_children() inspects this process tree.
        When unset, falls back to os.getpid() (conductor's own children).
        """
        self._monitored_pid = pid
        logger.debug(
            f"Session {self.session_id}: monitored PID set to {pid}"
        )

    def _has_active_children(self) -> bool:
        """Check whether the monitored process has children consuming CPU.

        Uses the IntelligentSubprocessGate when available for rich classification
        (process type, confidence, adaptive timeout, stall detection). Falls back
        to the original CPU-threshold check when the gate is unavailable.

        Fail-open: returns False on any error so the handoff proceeds.
        """
        if not _PSUTIL_AVAILABLE or not SUBPROCESS_CHECK_ENABLED:
            return False

        # Use intelligent gate if available
        if _INTELLIGENT_GATE_AVAILABLE:
            try:
                if self._intelligent_gate is None:
                    root_pid = self._monitored_pid or os.getpid()
                    workspace = str(self._workspace_path) if self._workspace_path else "."
                    self._intelligent_gate = IntelligentSubprocessGate(
                        monitored_pid=root_pid, workspace_path=workspace
                    )
                elif self._monitored_pid:
                    self._intelligent_gate._monitored_pid = self._monitored_pid

                decision = self._intelligent_gate.should_defer_handoff()
                if decision.should_defer:
                    logger.info(
                        f"Session {self.session_id}: intelligent gate defers handoff - "
                        f"type={decision.process_type.value}, confidence={decision.confidence:.2f}, "
                        f"timeout={decision.timeout_seconds:.0f}s, reason={decision.reason}"
                        + (", STALLED" if decision.is_stalled else "")
                    )
                return decision.should_defer and not decision.is_stalled
            except Exception as exc:
                logger.debug(
                    f"Session {self.session_id}: intelligent gate failed ({exc!r}), "
                    f"falling back to CPU threshold check"
                )

        # Fallback to original CPU threshold check
        try:
            root_pid = self._monitored_pid or os.getpid()
            try:
                root_proc = psutil.Process(root_pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return False

            children = root_proc.children(recursive=True)
            alive = []
            for child in children:
                try:
                    if child.status() != psutil.STATUS_ZOMBIE:
                        alive.append(child)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            if not alive:
                return False

            for child in alive:
                try:
                    child.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            time.sleep(0.3)

            total_cpu = 0.0
            live_count = 0
            for child in alive:
                try:
                    total_cpu += child.cpu_percent(interval=None)
                    live_count += 1
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            active = total_cpu > SUBPROCESS_CPU_THRESHOLD_PERCENT
            if active:
                logger.info(
                    f"Session {self.session_id}: active child processes detected "
                    f"(root_pid={root_pid}, children={live_count}, "
                    f"total_cpu={total_cpu:.1f}%)"
                )
            else:
                logger.debug(
                    f"Session {self.session_id}: child processes idle "
                    f"(root_pid={root_pid}, children={live_count}, "
                    f"total_cpu={total_cpu:.1f}%)"
                )
            return active

        except Exception as exc:
            logger.debug(
                f"Session {self.session_id}: subprocess check failed "
                f"({exc!r}), allowing handoff"
            )
            return False

    def _activity_loop(self):
        """Core polling loop that checks workspace for file activity."""
        baseline_mtime = self._scan_workspace_activity()
        if baseline_mtime > 0:
            self._last_activity_time = max(self._last_activity_time or 0, baseline_mtime)

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=ACTIVITY_CHECK_INTERVAL_SECONDS)
            if self._stop_event.is_set():
                break

            now = time.time()
            elapsed_minutes = (now - (self._started_at.timestamp() if self._started_at else now)) / 60.0

            # Scan for new file activity
            current_max_mtime = self._scan_workspace_activity()
            if current_max_mtime > (self._last_activity_time or 0):
                logger.debug(
                    f"Session {self.session_id}: workspace activity detected "
                    f"(mtime delta: {current_max_mtime - (self._last_activity_time or 0):.1f}s)"
                )
                self._last_activity_time = current_max_mtime

            # Calculate inactivity duration
            inactivity_seconds = now - (self._last_activity_time or now)
            inactivity_minutes = inactivity_seconds / 60.0

            # DECISION: Should we fire the handoff?
            should_fire = False
            is_absolute_max = False
            fire_reason = ""

            # Check 1: Absolute maximum timeout (hard ceiling, always enforced)
            if elapsed_minutes >= self.max_absolute_minutes:
                should_fire = True
                is_absolute_max = True
                fire_reason = f"absolute max timeout ({self.max_absolute_minutes}min)"

            # Check 2: Inactivity threshold exceeded AND minimum runtime passed
            elif (inactivity_minutes >= self.inactivity_minutes
                  and elapsed_minutes >= self.timeout_minutes):
                should_fire = True
                fire_reason = (
                    f"inactivity ({inactivity_minutes:.1f}min) after "
                    f"base timeout ({self.timeout_minutes}min)"
                )

            # Check 3: Extended inactivity even before base timeout
            # If the agent has been completely silent for 2x the inactivity threshold,
            # it's probably hung regardless of elapsed time (but only after 30 min minimum)
            elif (inactivity_minutes >= self.inactivity_minutes * 2
                  and elapsed_minutes >= 30):
                should_fire = True
                fire_reason = (
                    f"extended inactivity ({inactivity_minutes:.1f}min, "
                    f"2x threshold) after {elapsed_minutes:.1f}min"
                )

            # Subprocess activity gate: defer handoff if children are consuming CPU.
            # Absolute max timeout is NEVER gated (safety ceiling).
            if should_fire and not is_absolute_max:
                if self._has_active_children():
                    logger.info(
                        f"Session {self.session_id}: handoff deferred — "
                        f"active child processes detected ({fire_reason})"
                    )
                    should_fire = False

            if not should_fire:
                if int(elapsed_minutes) % 10 == 0 and int(elapsed_minutes) > 0:
                    logger.debug(
                        f"Session {self.session_id}: alive at {elapsed_minutes:.0f}min, "
                        f"last activity {inactivity_minutes:.1f}min ago"
                    )
                continue

            # Fire the handoff
            with self._lock:
                if self._fired or self._cancelled:
                    return

                # Defense-in-depth: validate session is still active
                try:
                    global _watcher_instance
                    if _watcher_instance is not None:
                        if self.session_id not in _watcher_instance._sessions:
                            logger.warning(
                                f"Activity-aware handoff for {self.session_id} skipped: "
                                f"session no longer active (zombie detected)"
                            )
                            self._cancelled = True
                            return
                except Exception:
                    pass

                self._fired = True

            logger.info(
                f"Activity-aware handoff triggered for session {self.session_id}: "
                f"{fire_reason} (elapsed: {elapsed_minutes:.1f}min)"
            )

            signal = HandoffSignal(
                level=HandoffLevel.TIME_BASED,
                session_id=self.session_id,
                workspace_path=self.workspace_path,
                tokens_used=0,
                cache_read=0,
                cache_creation=0,
                elapsed_minutes=elapsed_minutes
            )

            try:
                self.callback(signal)
            except Exception as e:
                logger.error(f"Activity-aware handoff callback error: {e}")
            return

        # Loop exited via stop event
        with self._lock:
            if not self._fired:
                self._cancelled = True
                logger.debug(f"Activity-aware handoff cancelled for session {self.session_id}")

    def cancel(self):
        """Cancel the activity-aware handoff monitor."""
        with self._lock:
            if self._fired:
                return
            self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.debug(f"Activity-aware handoff monitor cancelled for session {self.session_id}")

    def stop(self):
        """Alias for cancel()."""
        self.cancel()

    @property
    def has_fired(self) -> bool:
        with self._lock:
            return self._fired

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    @property
    def elapsed_seconds(self) -> float:
        if self._started_at is None:
            return 0.0
        return (datetime.now() - self._started_at).total_seconds()

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_absolute_minutes * 60 - self.elapsed_seconds)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            inactivity_sec = time.time() - (self._last_activity_time or time.time())
            return {
                "session_id": self.session_id,
                "monitor_type": "activity_aware",
                "stage": self.stage,
                "timeout_minutes": self.timeout_minutes,
                "inactivity_threshold_minutes": self.inactivity_minutes,
                "max_absolute_minutes": self.max_absolute_minutes,
                "elapsed_seconds": self.elapsed_seconds,
                "remaining_seconds": self.remaining_seconds,
                "current_inactivity_seconds": inactivity_sec,
                "fired": self._fired,
                "cancelled": self._cancelled,
                "started_at": self._started_at.isoformat() if self._started_at else None,
                "subprocess_check_enabled": SUBPROCESS_CHECK_ENABLED and _PSUTIL_AVAILABLE,
                "monitored_pid": self._monitored_pid,
            }


# =============================================================================
# SESSION CLASSIFICATION
# =============================================================================

def is_p_mode_session(jsonl_path: Path) -> bool:
    """
    Return True if this is a -p mode session (no progress events).

    -p mode jsonl is clean: user, assistant, tool_use, tool_result
    Interactive mode has: progress, hook_progress, bash_progress, etc.

    Args:
        jsonl_path: Path to the JSONL transcript file

    Returns:
        True if -p mode (should be watched), False if interactive (skip)
    """
    try:
        with open(jsonl_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                if i > 50:
                    break
                # Check for interactive mode markers
                if '"type":"progress"' in line or '"type":"hook_progress"' in line:
                    return False  # Interactive mode - skip
                if '"type":"bash_progress"' in line:
                    return False
                if '"type":"file-history-snapshot"' in line:
                    return False
        return True  # -p mode - watch it
    except (IOError, OSError, UnicodeDecodeError):
        return False


def _workspace_paths_match(expected_workspace: str, candidate_workspace: str) -> bool:
    """Return True when candidate workspace is the same as or inside expected workspace."""
    expected = (expected_workspace or "").strip()
    candidate = (candidate_workspace or "").strip()
    if not expected or not candidate:
        return False

    try:
        expected_path = Path(expected).expanduser().resolve()
        candidate_path = Path(candidate).expanduser().resolve()
        return (
            candidate_path == expected_path
            or expected_path in candidate_path.parents
        )
    except OSError:
        return (
            candidate == expected
            or candidate.startswith(expected + os.sep)
        )


def _is_codex_exec_session_payload(payload: Dict[str, Any]) -> bool:
    """Return True only for Codex exec/headless sessions."""
    if not isinstance(payload, dict):
        return False

    originator = str(payload.get("originator") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()

    if source and source != "exec":
        return False
    if originator and originator != "codex_exec":
        return False

    return source == "exec" or originator == "codex_exec"


def _is_codex_file_for_workspace(jsonl_path: Path, workspace_path: str) -> bool:
    """
    Return True if a Codex session file belongs to a workspace.

    Codex stores workspace metadata in a session_meta event:
      {"type":"session_meta","payload":{"cwd":"..."}}
    """
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f):
                if idx > 40:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if not isinstance(record, dict):
                    continue

                if record.get("type") != "session_meta":
                    continue

                payload = record.get("payload", {})
                if not isinstance(payload, dict):
                    continue

                if not _is_codex_exec_session_payload(payload):
                    return False

                cwd = payload.get("cwd")
                return _workspace_paths_match(workspace_path, cwd)
    except (IOError, OSError, UnicodeDecodeError):
        return False

    return False


def find_transcript_dir(workspace_path: str, provider: Optional[str] = None) -> Optional[Path]:
    """
    Find transcript storage for the active provider and workspace.

    For Claude, this resolves to a workspace-specific directory under
    ~/.claude/projects/.
    For Codex, this resolves to ~/.codex/sessions (file-level filtering
    is done by SessionMonitor based on session_meta.cwd).
    For Gemini, transcript token monitoring is optional via
    ATLASFORGE_GEMINI_SESSIONS_DIR; otherwise time-based handoff is used.
    """
    active_provider = get_active_provider(provider)

    if active_provider == "codex":
        if CODEX_SESSIONS_DIR.exists():
            logger.debug(f"Using Codex sessions dir: {CODEX_SESSIONS_DIR}")
            return CODEX_SESSIONS_DIR
        logger.warning(f"Codex sessions directory not found: {CODEX_SESSIONS_DIR}")
        return None

    if active_provider == "gemini":
        gemini_sessions = os.environ.get("ATLASFORGE_GEMINI_SESSIONS_DIR", "").strip()
        if not gemini_sessions:
            logger.info("Gemini transcript directory not configured; using time-based handoff only")
            return None

        gemini_dir = Path(gemini_sessions).expanduser()
        if gemini_dir.exists():
            logger.debug(f"Using Gemini sessions dir: {gemini_dir}")
            return gemini_dir

        logger.warning(f"Gemini sessions directory not found: {gemini_dir}")
        return None

    # Claude transcript directory resolution
    escaped = workspace_path.replace("/", "-")
    if escaped.startswith("-"):
        escaped = escaped[1:]

    transcript_dir = CLAUDE_PROJECTS_DIR / f"-{escaped}"
    if transcript_dir.exists():
        logger.debug(f"Found Claude transcript dir: {transcript_dir}")
        return transcript_dir

    if CLAUDE_PROJECTS_DIR.exists():
        workspace_name = Path(workspace_path).name
        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if d.is_dir() and workspace_name in d.name:
                logger.debug(f"Found Claude transcript dir via partial match: {d}")
                return d

    logger.warning(f"No Claude transcript directory found for: {workspace_path}")
    return None


# =============================================================================
# SESSION MONITOR
# =============================================================================

class SessionMonitor:
    """
    Per-session context tracking.

    Monitors a single workspace's JSONL files for token usage
    and detects context exhaustion patterns.
    """

    def __init__(
        self,
        session_id: str,
        workspace_path: str,
        callback: Callable[[HandoffSignal], None],
        enable_time_handoff: bool = True,
        provider: Optional[str] = None,
        stage: Optional[str] = None
    ):
        """
        Initialize session monitor.

        Args:
            session_id: Unique session identifier
            workspace_path: Path to the workspace being monitored
            callback: Function to call when handoff is triggered
            enable_time_handoff: Whether to enable time-based handoff (default True)
            stage: Current R&D stage (PLANNING, BUILDING, TESTING, etc.)
                   When provided, uses ActivityAwareHandoffMonitor instead of
                   the fixed TimeBasedHandoffMonitor.
        """
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.callback = callback
        self.provider = get_active_provider(provider)
        self.stage = stage

        # Find transcript directory
        self.transcript_dir = find_transcript_dir(workspace_path, provider=self.provider)

        # File tracking
        self.current_jsonl: Optional[Path] = None
        self.file_offset: int = 0
        self.file_mtime: float = 0
        self._file_offsets: Dict[str, int] = {}
        self._file_mtimes: Dict[str, float] = {}
        self._logged_jsonl_files: Set[str] = set()

        # Token state
        self.last_tokens: Optional[TokenState] = None
        self.peak_tokens = 0
        self.seen_request_ids: Set[str] = set()
        self._codex_file_match_cache: Dict[str, Tuple[float, int, bool]] = {}
        self._codex_candidates: List[Path] = []
        self._codex_last_scan: float = 0.0
        self._detected_model: str = ""  # Model name from JSONL (for dynamic thresholds)
        self._codex_context_handoff_disabled_logged = False

        # Handoff state
        self.handoff_triggered = False
        self.handoff_level: Optional[HandoffLevel] = None

        # Timing
        self.last_activity = datetime.now()
        self.started_at = datetime.now()
        self.last_check_time: Optional[float] = None  # For latency measurement

        # Time-based handoff monitor
        self._time_handoff_monitor: Optional[TimeBasedHandoffMonitor] = None
        self._enable_time_handoff = enable_time_handoff and TIME_BASED_HANDOFF_ENABLED

        self._lock = threading.Lock()

    def _is_codex_candidate(self, jsonl_path: Path) -> bool:
        """Return True if a Codex session file belongs to this workspace."""
        try:
            stat = jsonl_path.stat()
        except OSError:
            return False

        cache_key = str(jsonl_path)
        cache_value = self._codex_file_match_cache.get(cache_key)
        if cache_value and cache_value[0] == stat.st_mtime and cache_value[1] == stat.st_size:
            return cache_value[2]

        matches = _is_codex_file_for_workspace(jsonl_path, self.workspace_path)
        self._codex_file_match_cache[cache_key] = (stat.st_mtime, stat.st_size, matches)
        return matches

    def _refresh_codex_candidates(self):
        """Refresh matching Codex session file candidates for the workspace."""
        if not self.transcript_dir or not self.transcript_dir.exists():
            self._codex_candidates = []
            return

        now = time.time()
        if (now - self._codex_last_scan) < CODEX_SCAN_INTERVAL_SECONDS and self._codex_candidates:
            return

        self._codex_last_scan = now

        try:
            all_files = sorted(
                self.transcript_dir.rglob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
        except OSError:
            all_files = []

        candidates: List[Path] = []
        lower_bound = self.started_at.timestamp() - CODEX_TRANSCRIPT_START_SLOP_SECONDS
        for jsonl_path in all_files[:CODEX_MAX_CANDIDATE_FILES]:
            try:
                if jsonl_path.stat().st_mtime < lower_bound:
                    break
            except OSError:
                continue
            if self._is_codex_candidate(jsonl_path):
                candidates.append(jsonl_path)
                if len(candidates) >= 20:
                    break

        self._codex_candidates = candidates

    def _find_active_jsonl(self) -> Optional[Path]:
        """Find the most recently modified JSONL file."""
        if not self.transcript_dir or not self.transcript_dir.exists():
            return None

        if self.provider == "codex":
            self._refresh_codex_candidates()
            if not self._codex_candidates:
                return None
            try:
                return max(self._codex_candidates, key=lambda p: p.stat().st_mtime)
            except OSError:
                return None

        jsonl_files = list(self.transcript_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None

        valid_files = [f for f in jsonl_files if is_p_mode_session(f)]
        if not valid_files:
            return None

        return max(valid_files, key=lambda p: p.stat().st_mtime)

    def _read_new_entries(self) -> List[Dict[str, Any]]:
        """
        Read new JSONL entries since last read.

        Returns:
            List of new parsed JSON records
        """
        if self.provider == "codex":
            return self._read_codex_new_entries()

        active_jsonl = self._find_active_jsonl()

        if not active_jsonl:
            return []

        # Handle file rotation (new session file)
        if self.current_jsonl != active_jsonl:
            logger.info(f"Session {self.session_id}: New JSONL file: {active_jsonl.name}")
            self.current_jsonl = active_jsonl
            self.file_offset = 0
            self.file_mtime = 0

        # Check if file has been modified
        try:
            current_mtime = active_jsonl.stat().st_mtime
            current_size = active_jsonl.stat().st_size
        except OSError:
            return []

        if current_mtime <= self.file_mtime and current_size <= self.file_offset:
            return []  # No changes

        self.file_mtime = current_mtime

        # Read new content
        entries = []
        try:
            with open(active_jsonl, 'r', encoding='utf-8') as f:
                f.seek(self.file_offset)
                content = f.read()

            # Process lines from content
            lines = content.split('\n')
            processed_bytes = 0
            is_last_line = lambda i: i == len(lines) - 1

            for i, line in enumerate(lines):
                line_bytes = len(line.encode('utf-8')) + (1 if i < len(lines) - 1 else 0)  # +1 for newline
                stripped = line.strip()

                if not stripped:
                    processed_bytes += line_bytes
                    continue

                try:
                    record = json.loads(stripped)
                    entries.append(record)
                    processed_bytes += line_bytes
                except json.JSONDecodeError:
                    # Only consider as partial line if it's the LAST line AND
                    # doesn't look like it ends properly AND the content doesn't end with newline
                    if is_last_line(i) and not content.endswith('\n'):
                        # Likely a partial line at EOF - don't advance offset past this line
                        logger.debug(f"Partial JSON line at EOF, will retry on next read")
                        break
                    else:
                        # Malformed JSON - skip it but log for debugging
                        logger.debug(f"Malformed JSON line skipped: {stripped[:100]}...")
                        processed_bytes += line_bytes
                        continue

            # Update offset based on successfully processed bytes
            self.file_offset += processed_bytes

        except (IOError, OSError, UnicodeDecodeError) as e:
            logger.debug(f"Error reading JSONL: {e}")

        return entries

    def _read_entries_from_jsonl(
        self,
        jsonl_path: Path,
        *,
        offset: int,
        last_mtime: float,
    ) -> Tuple[List[Dict[str, Any]], int, float]:
        """Read new JSONL records from one file without mutating monitor state."""
        try:
            current_stat = jsonl_path.stat()
        except OSError:
            return [], offset, last_mtime

        current_mtime = current_stat.st_mtime
        current_size = current_stat.st_size
        if current_size < offset:
            offset = 0
            last_mtime = 0

        if current_mtime <= last_mtime and current_size <= offset:
            return [], offset, last_mtime

        entries: List[Dict[str, Any]] = []
        processed_bytes = 0
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                f.seek(offset)
                content = f.read()

            lines = content.split("\n")
            for i, line in enumerate(lines):
                line_bytes = len(line.encode("utf-8")) + (1 if i < len(lines) - 1 else 0)
                stripped = line.strip()

                if not stripped:
                    processed_bytes += line_bytes
                    continue

                try:
                    record = json.loads(stripped)
                    entries.append(record)
                    processed_bytes += line_bytes
                except json.JSONDecodeError:
                    if i == len(lines) - 1 and not content.endswith("\n"):
                        logger.debug("Partial JSON line at EOF, will retry on next read")
                        break
                    logger.debug("Malformed JSON line skipped: %s...", stripped[:100])
                    processed_bytes += line_bytes

        except (IOError, OSError, UnicodeDecodeError) as e:
            logger.debug("Error reading JSONL %s: %s", jsonl_path, e)
            return [], offset, last_mtime

        return entries, offset + processed_bytes, current_mtime

    def _read_codex_new_entries(self) -> List[Dict[str, Any]]:
        """Read new records from all matching Codex session transcripts.

        Codex can create multiple exec transcripts for one workspace during a
        single AtlasForge stage. Tracking only the newest file causes the
        monitor to bounce between files and reset offsets, so each matching
        transcript gets its own offset.
        """
        self._refresh_codex_candidates()
        if not self._codex_candidates:
            return []

        def _mtime(path: Path) -> float:
            try:
                return path.stat().st_mtime
            except OSError:
                return 0.0

        ordered_candidates = sorted(self._codex_candidates, key=_mtime)
        newest = max(ordered_candidates, key=_mtime)
        self.current_jsonl = newest

        entries: List[Dict[str, Any]] = []
        active_keys = set()
        for jsonl_path in ordered_candidates:
            key = str(jsonl_path)
            active_keys.add(key)
            try:
                stat = jsonl_path.stat()
            except OSError:
                continue

            if key not in self._file_offsets and stat.st_mtime < self.started_at.timestamp():
                self._file_offsets[key] = stat.st_size
                self._file_mtimes[key] = stat.st_mtime
                if key not in self._logged_jsonl_files:
                    logger.info(
                        "Session %s: Ignoring pre-existing Codex JSONL file: %s",
                        self.session_id,
                        jsonl_path.name,
                    )
                    self._logged_jsonl_files.add(key)
                continue

            if key not in self._logged_jsonl_files:
                logger.info("Session %s: Tracking Codex JSONL file: %s", self.session_id, jsonl_path.name)
                self._logged_jsonl_files.add(key)

            file_entries, new_offset, new_mtime = self._read_entries_from_jsonl(
                jsonl_path,
                offset=self._file_offsets.get(key, 0),
                last_mtime=self._file_mtimes.get(key, 0),
            )
            self._file_offsets[key] = new_offset
            self._file_mtimes[key] = new_mtime
            entries.extend(file_entries)

        # Keep tracking maps bounded to current candidates.
        for stale_key in list(self._file_offsets):
            if stale_key not in active_keys:
                self._file_offsets.pop(stale_key, None)
                self._file_mtimes.pop(stale_key, None)

        self.file_offset = sum(self._file_offsets.values())
        self.file_mtime = max(self._file_mtimes.values(), default=0)
        return entries

    def _extract_token_state(self, record: Dict[str, Any]) -> Optional[TokenState]:
        """Extract TokenState from JSONL record if it has usage data."""
        # Ensure record is a dict (malformed entries might be arrays or other types)
        if not isinstance(record, dict):
            return None

        usage: Optional[Dict[str, Any]] = None
        request_id: Optional[str] = None

        if self.provider == "codex":
            if record.get("type") == "turn_context":
                payload = record.get("payload", {})
                if isinstance(payload, dict):
                    model_name = str(payload.get("model") or "").strip()
                    if model_name and not self._detected_model:
                        self._detected_model = model_name
                        context_window = _codex_context_window_for_model(model_name)
                        if context_window > 0:
                            logger.info(
                                "Session %s: Detected Codex model %r with %d token context window",
                                self.session_id,
                                model_name,
                                context_window,
                            )
                return None

            if record.get("type") != "event_msg":
                return None

            payload = record.get("payload", {})
            if not isinstance(payload, dict) or payload.get("type") != "token_count":
                return None

            info = payload.get("info", {})
            if not isinstance(info, dict):
                return None

            last_usage = info.get("last_token_usage", {})
            total_usage = info.get("total_token_usage", {})
            if not isinstance(last_usage, dict):
                return None
            if not isinstance(total_usage, dict):
                total_usage = {}

            usage = {
                # Codex `total_token_usage` is cumulative for the whole rollout,
                # not the active context window. Context pressure must use the
                # current request/window usage from `last_token_usage`.
                "input_tokens": last_usage.get("input_tokens", 0),
                "output_tokens": last_usage.get("output_tokens", 0),
                "cache_read_input_tokens": last_usage.get("cached_input_tokens", 0),
                "cache_creation_input_tokens": 0,
                "total_tokens": last_usage.get("total_tokens", 0),
                "model_context_window": _codex_context_window_for_model(
                    self._detected_model,
                    _safe_nonnegative_int(info.get("model_context_window", 0)),
                ),
            }
            request_id = (
                f"token_count:{total_usage.get('total_tokens', 0)}:"
                f"{last_usage.get('input_tokens', 0)}:{last_usage.get('output_tokens', 0)}"
            )
        elif self.provider == "claude":
            if record.get('type') != 'assistant':
                return None

            message = record.get('message', {})
            if not isinstance(message, dict):
                return None

            usage = message.get('usage', {})
            if not isinstance(usage, dict):
                return None

            # Extract model name for dynamic context window lookup
            model_name = message.get('model', '')
            if model_name and not self._detected_model:
                self._detected_model = model_name
                context_window = MODEL_CONTEXT_WINDOWS.get(model_name, 0)
                if context_window > 0:
                    logger.info(
                        f"Session {self.session_id}: Detected model '{model_name}' "
                        f"with {context_window:,} token context window"
                    )
                else:
                    logger.warning(
                        f"Session {self.session_id}: Unknown model '{model_name}', "
                        f"falling back to legacy thresholds"
                    )

            request_id = record.get('requestId')
        else:
            # Gemini token metadata schema is not yet standardized in watcher feeds.
            return None

        if not usage:
            return None

        # Deduplication
        if request_id:
            if request_id in self.seen_request_ids:
                return None
            self.seen_request_ids.add(request_id)

            # Keep set bounded
            if len(self.seen_request_ids) > 5000:
                # Drop oldest half
                self.seen_request_ids = set(list(self.seen_request_ids)[-2500:])

        return TokenState.from_usage(usage, request_id, model_name=getattr(self, '_detected_model', '') or '')

    def _check_thresholds(self, tokens: TokenState) -> Optional[HandoffSignal]:
        """
        Check if token state crosses handoff thresholds.

        Detection logic:
        - Context exhaustion = high cache_creation + low cache_read
        - This means Claude is building NEW context at the limit

        Returns:
            HandoffSignal if threshold crossed, None otherwise
        """
        if self.handoff_triggered:
            return None  # Already triggered

        total = tokens.total_context
        tracked_total = (
            (tokens.total_tokens_seen or (tokens.input_tokens + tokens.output_tokens))
            if self.provider == "codex"
            else max(tokens.total_tokens_seen, total)
        )
        cache_read = tokens.cache_read_input_tokens
        cache_creation = tokens.cache_creation_input_tokens

        # Update peak
        if tracked_total > self.peak_tokens:
            self.peak_tokens = tracked_total

        if self.provider == "codex":
            if not CODEX_CONTEXT_HANDOFF_ENABLED:
                if not self._codex_context_handoff_disabled_logged:
                    logger.info(
                        "Session %s: Codex context handoff disabled; "
                        "WorkBudgetManager controls Codex token budget "
                        "(set CODEX_CONTEXT_HANDOFF_ENABLED=1 to re-enable)",
                        self.session_id,
                    )
                    self._codex_context_handoff_disabled_logged = True
                return None

            context_window = tokens.model_context_window
            if context_window <= 0:
                return None

            # Codex reports cumulative `total_tokens` separately from cached input.
            # Use the reported total for exhaustion thresholds; treating cached
            # replayed input as fully "consumed" context causes premature
            # handoffs on large startup prompts and restored sessions.
            graceful_threshold = int(context_window * CODEX_GRACEFUL_CONTEXT_RATIO)
            emergency_threshold = int(context_window * CODEX_EMERGENCY_CONTEXT_RATIO)

            # Large Codex windows can still fail abruptly when only a small amount
            # of headroom remains. Reserve explicit tail room so handoff begins
            # before the final high-burn stretch of the session.
            if CODEX_GRACEFUL_HEADROOM_TOKENS > 0 and context_window > CODEX_GRACEFUL_HEADROOM_TOKENS:
                graceful_threshold = min(
                    graceful_threshold,
                    context_window - CODEX_GRACEFUL_HEADROOM_TOKENS,
                )
            if CODEX_EMERGENCY_HEADROOM_TOKENS > 0 and context_window > CODEX_EMERGENCY_HEADROOM_TOKENS:
                emergency_threshold = min(
                    emergency_threshold,
                    context_window - CODEX_EMERGENCY_HEADROOM_TOKENS,
                )

            elapsed_seconds = (datetime.now() - self.started_at).total_seconds()
            startup_suppressed = (
                tracked_total >= graceful_threshold
                and elapsed_seconds < CODEX_STARTUP_GRACE_SECONDS
                and tokens.output_tokens < CODEX_MIN_OUTPUT_TOKENS_FOR_HANDOFF
            )
            if startup_suppressed:
                logger.info(
                    f"Session {self.session_id}: Suppressing early Codex handoff "
                    f"({tracked_total}/{context_window} tokens, output={tokens.output_tokens}, "
                    f"elapsed={elapsed_seconds:.1f}s, effective_context={total})"
                )
                return None
            level = None

            if tracked_total >= emergency_threshold:
                level = HandoffLevel.EMERGENCY
                logger.warning(
                    f"Session {self.session_id}: Codex emergency threshold reached "
                    f"({tracked_total}/{context_window} tokens, threshold={emergency_threshold}, "
                    f"reported_total={tokens.total_tokens_seen}, effective_context={total})"
                )
            elif tracked_total >= graceful_threshold:
                level = HandoffLevel.GRACEFUL
                logger.info(
                    f"Session {self.session_id}: Codex graceful threshold reached "
                    f"({tracked_total}/{context_window} tokens, threshold={graceful_threshold}, "
                    f"reported_total={tokens.total_tokens_seen}, effective_context={total})"
                )

            if level:
                self.handoff_triggered = True
                self.handoff_level = level
                return HandoffSignal(
                    level=level,
                    session_id=self.session_id,
                    workspace_path=self.workspace_path,
                    tokens_used=tracked_total,
                    cache_read=cache_read,
                    cache_creation=cache_creation,
                )
            return None

        # Non-Claude providers without standardized token-pressure signals
        # fall back to time-based handoff only.
        if self.provider != "claude":
            return None

        # Early failure detection (startup issue, not exhaustion)
        if total < EARLY_FAILURE_THRESHOLD and cache_read == 0 and cache_creation == 0:
            logger.debug(f"Session {self.session_id}: Low tokens ({total}), likely startup")
            return None

        # Context exhaustion pattern:
        # High cache_creation + low cache_read = hitting the wall
        # Claude can't reuse cache because it's at the limit

        if cache_read < LOW_CACHE_READ_THRESHOLD:
            level = None

            # Compute thresholds dynamically from model context window
            context_window = tokens.model_context_window
            if context_window > 0:
                graceful_thresh = int(context_window * CLAUDE_GRACEFUL_CONTEXT_RATIO)
                emergency_thresh = int(context_window * CLAUDE_EMERGENCY_CONTEXT_RATIO)
            else:
                # Fallback to legacy hardcoded thresholds (unknown model)
                graceful_thresh = GRACEFUL_THRESHOLD
                emergency_thresh = EMERGENCY_THRESHOLD

            if cache_creation >= emergency_thresh:
                level = HandoffLevel.EMERGENCY
                logger.warning(
                    f"Session {self.session_id}: EMERGENCY threshold reached! "
                    f"cache_creation={cache_creation}, cache_read={cache_read}, "
                    f"threshold={emergency_thresh}, context_window={context_window}, "
                    f"model={tokens.model_name or 'unknown'}"
                )
            elif cache_creation >= graceful_thresh:
                level = HandoffLevel.GRACEFUL
                logger.info(
                    f"Session {self.session_id}: Graceful threshold reached. "
                    f"cache_creation={cache_creation}, cache_read={cache_read}, "
                    f"threshold={graceful_thresh}, context_window={context_window}, "
                    f"model={tokens.model_name or 'unknown'}"
                )

            if level:
                self.handoff_triggered = True
                self.handoff_level = level

                return HandoffSignal(
                    level=level,
                    session_id=self.session_id,
                    workspace_path=self.workspace_path,
                    tokens_used=tracked_total,
                    cache_read=cache_read,
                    cache_creation=cache_creation
                )

        return None

    def process_updates(self) -> Optional[HandoffSignal]:
        """
        Process new JSONL entries and check thresholds.

        Returns:
            HandoffSignal if threshold crossed, None otherwise
        """
        with self._lock:
            if self.handoff_triggered:
                return None

            entries = self._read_new_entries()

            if entries:
                self.last_activity = datetime.now()

            for entry in entries:
                tokens = self._extract_token_state(entry)
                if tokens:
                    self.last_tokens = tokens

                    signal = self._check_thresholds(tokens)
                    if signal:
                        # Invoke callback
                        try:
                            self.callback(signal)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")
                        return signal

            return None

    def is_stale(self) -> bool:
        """Check if session has been inactive for too long.

        Sessions with an active (not-yet-fired, not-cancelled) time-based
        handoff monitor are NEVER considered stale — the handoff timer
        itself serves as the session lifetime manager.
        """
        # If time-based handoff monitor is active, the session is not stale.
        # The handoff timer is the authoritative timeout mechanism.
        if (self._time_handoff_monitor is not None
                and not self._time_handoff_monitor.has_fired
                and not self._time_handoff_monitor.is_cancelled):
            return False

        inactive_seconds = (datetime.now() - self.last_activity).total_seconds()
        return inactive_seconds > STALE_SESSION_TIMEOUT

    def get_stats(self) -> Dict[str, Any]:
        """Get session statistics."""
        stats = {
            "session_id": self.session_id,
            "workspace_path": self.workspace_path,
            "provider": self.provider,
            "transcript_dir": str(self.transcript_dir) if self.transcript_dir else None,
            "current_jsonl": str(self.current_jsonl) if self.current_jsonl else None,
            "peak_tokens": self.peak_tokens,
            "last_tokens": {
                "total": (
                    self.last_tokens.total_tokens_seen or self.last_tokens.total_context
                ) if self.last_tokens else 0,
                "cache_read": self.last_tokens.cache_read_input_tokens if self.last_tokens else 0,
                "cache_creation": self.last_tokens.cache_creation_input_tokens if self.last_tokens else 0,
            } if self.last_tokens else None,
            "handoff_triggered": self.handoff_triggered,
            "handoff_level": self.handoff_level.value if self.handoff_level else None,
            "last_activity": self.last_activity.isoformat(),
            "started_at": self.started_at.isoformat(),
            "is_stale": self.is_stale()
        }
        # Add time-based handoff stats if enabled
        if self._time_handoff_monitor:
            stats["time_handoff"] = self._time_handoff_monitor.get_stats()
        return stats

    def start_time_handoff_monitor(self):
        """Start the time-based or activity-aware handoff monitor for this session.

        Behaviour depends on WATCHER_POLICY:
          token_first      — skip time monitor entirely; only MAX_ABSOLUTE_TIMEOUT
                             fires if set externally (circuit-breaker via conductor).
          legacy_time_first — existing behaviour: ActivityAware (stage known) or
                              TimeBasedHandoffMonitor (no stage).
          context_only     — no time monitor at all.

        When a stage is provided and recognized (legacy_time_first), uses
        ActivityAwareHandoffMonitor instead of the fixed TimeBasedHandoffMonitor.
        """
        if not self._enable_time_handoff:
            # Covers token_first (enable_time_handoff=False passed from conductor)
            # and context_only explicitly
            logger.debug(
                "Session %s: time handoff monitor suppressed "
                "(enable_time_handoff=False, policy=%s)",
                self.session_id, WATCHER_POLICY,
            )
            return

        if WATCHER_POLICY == "context_only":
            logger.debug("Session %s: time monitor disabled by WATCHER_POLICY=context_only", self.session_id)
            return

        if self._time_handoff_monitor is not None:
            return  # Already running

        if self.stage and self.stage.upper() in STAGE_TIMEOUT_MINUTES:
            # Use activity-aware monitor for stage-aware adaptive timeout
            self._time_handoff_monitor = ActivityAwareHandoffMonitor(
                session_id=self.session_id,
                workspace_path=self.workspace_path,
                callback=self._on_time_handoff,
                stage=self.stage,
            )
            logger.info(
                f"Session {self.session_id}: using ActivityAwareHandoffMonitor "
                f"for stage {self.stage} (base timeout: "
                f"{STAGE_TIMEOUT_MINUTES.get(self.stage.upper(), 55)}min, policy={WATCHER_POLICY})"
            )
        else:
            # No stage or unrecognized stage: use fixed timer (backward compat)
            self._time_handoff_monitor = TimeBasedHandoffMonitor(
                session_id=self.session_id,
                workspace_path=self.workspace_path,
                callback=self._on_time_handoff,
                timeout_minutes=TIME_BASED_HANDOFF_MINUTES
            )
        self._time_handoff_monitor.start()

    def _on_time_handoff(self, signal: HandoffSignal):
        """Handle time-based handoff signal."""
        with self._lock:
            if self.handoff_triggered:
                # Token-based handoff already happened, ignore time-based
                logger.debug(f"Session {self.session_id}: Ignoring time-based handoff (token handoff already triggered)")
                return

            self.handoff_triggered = True
            self.handoff_level = HandoffLevel.TIME_BASED

        # Invoke the main callback
        try:
            self.callback(signal)
        except Exception as e:
            logger.error(f"Time handoff callback error for session {self.session_id}: {e}")

    def stop_time_handoff_monitor(self):
        """Stop the time-based handoff monitor for this session."""
        if self._time_handoff_monitor:
            logger.debug(f"Stopping time handoff monitor for session {self.session_id}")
            self._time_handoff_monitor.cancel()
            self._time_handoff_monitor = None

    def set_monitored_pid(self, pid: int):
        """Propagate monitored PID to the activity-aware handoff monitor.

        Only effective when the monitor is an ActivityAwareHandoffMonitor.
        Silently ignored for TimeBasedHandoffMonitor (backward compat).
        """
        monitor = self._time_handoff_monitor
        if isinstance(monitor, ActivityAwareHandoffMonitor):
            monitor.set_monitored_pid(pid)


# =============================================================================
# WATCHDOG EVENT HANDLER
# =============================================================================

class TranscriptEventHandler(FileSystemEventHandler if HAS_WATCHDOG else object):
    """Watchdog handler for JSONL file changes."""

    def __init__(self, on_change: Callable[[str], None]):
        if HAS_WATCHDOG:
            super().__init__()
        self._on_change = on_change
        self._last_event_times: Dict[str, float] = {}
        self._debounce_ms = 100  # Debounce rapid events

    def _should_process(self, path: str) -> bool:
        """Check if event should be processed (debounce)."""
        now = time.time() * 1000
        last = self._last_event_times.get(path, 0)
        if now - last < self._debounce_ms:
            return False
        self._last_event_times[path] = now
        return True

    def on_modified(self, event):
        """Handle file modification."""
        if event.is_directory:
            return
        if event.src_path.endswith('.jsonl') and self._should_process(event.src_path):
            self._on_change(event.src_path)

    def on_created(self, event):
        """Handle new file creation."""
        if event.is_directory:
            return
        if event.src_path.endswith('.jsonl') and self._should_process(event.src_path):
            self._on_change(event.src_path)


# =============================================================================
# CONTEXT WATCHER (MAIN CLASS)
# =============================================================================

class ContextWatcher:
    """
    Singleton context monitor for all active Claude -p mode sessions.

    Dynamically scales to monitor multiple workspaces in parallel.
    Uses watchdog (inotify) when available, falls back to polling.
    """

    def __init__(self):
        """Initialize the watcher (not yet watching)."""
        self._sessions: Dict[str, SessionMonitor] = {}
        self._observers: Dict[str, Observer] = {}  # Per-directory observers

        # Background monitoring thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._lock = threading.Lock()
        self._running = False

        # Global stats (legacy)
        self._total_handoffs = 0
        self._graceful_handoffs = 0
        self._emergency_handoffs = 0

        # Metrics tracking (new in Cycle 1)
        self._metrics = WatcherMetrics(started_at=datetime.now())

        # Watchdog restart tracking
        self._observer_failures: Dict[str, int] = {}
        self._max_observer_restarts = 3

    def start_watching(
        self,
        workspace_path: str,
        callback: Callable[[HandoffSignal], None],
        enable_time_handoff: bool = True,
        stage: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> Optional[str]:
        """
        Start monitoring a workspace for context exhaustion.

        Args:
            workspace_path: Path to the workspace to monitor
            callback: Function to call when handoff is triggered
            enable_time_handoff: Whether to enable time-based handoff (default True)
            stage: Current R&D stage (e.g. "TESTING"). When provided, uses
                   activity-aware adaptive timeout instead of fixed 55-min timer.
            provider: Provider used by the active LLM subprocess. When omitted,
                      falls back to env/state/default provider resolution.

        Returns:
            Session ID if started successfully, None on failure
        """
        if not CONTEXT_WATCHER_ENABLED:
            logger.info("ContextWatcher disabled via env var")
            return None

        with self._lock:
            # Generate session ID
            session_id = str(uuid.uuid4())[:8]
            active_provider = get_active_provider(provider)

            # Create session monitor with time-based handoff option
            monitor = SessionMonitor(
                session_id, workspace_path, callback,
                enable_time_handoff=enable_time_handoff,
                provider=active_provider,
                stage=stage
            )

            if not monitor.transcript_dir and monitor.provider != "gemini":
                logger.warning(f"Cannot watch {workspace_path}: no transcript dir found")
                return None
            if not monitor.transcript_dir and monitor.provider == "gemini":
                logger.info(
                    f"Starting watcher for {workspace_path} with provider=gemini "
                    "(time-based handoff only; transcript tokens unavailable)"
                )

            self._sessions[session_id] = monitor

            # Update metrics
            self._metrics.sessions_started += 1
            self._metrics.sessions_active = len(self._sessions)

            # Start time-based handoff monitor if enabled
            monitor.start_time_handoff_monitor()

            # Start watchdog for this directory if available
            if HAS_WATCHDOG and monitor.transcript_dir:
                self._start_watchdog_for_session(session_id, monitor)

            # Ensure background monitor thread is running
            if not self._running:
                self._start_monitor_thread()

            logger.info(f"Started watching session {session_id} for {workspace_path}")
            return session_id

    def _start_watchdog_for_session(self, session_id: str, monitor: SessionMonitor):
        """Start watchdog observer for a session's transcript directory."""
        if not monitor.transcript_dir:
            return

        dir_path = str(monitor.transcript_dir)

        # Check if we already have an observer for this directory
        if dir_path in self._observers:
            # Verify observer is alive, restart if needed
            observer = self._observers[dir_path]
            if not observer.is_alive():
                logger.warning(f"Watchdog for {dir_path} died, attempting restart")
                self._restart_watchdog(dir_path)
            return

        self._create_watchdog_observer(dir_path)

    def _create_watchdog_observer(self, dir_path: str) -> bool:
        """Create and start a watchdog observer for a directory."""
        try:
            def on_file_change(file_path: str):
                # Process updates for all sessions watching this directory
                with self._lock:
                    for sid, mon in self._sessions.items():
                        if mon.transcript_dir and str(mon.transcript_dir) == dir_path:
                            mon.process_updates()

            handler = TranscriptEventHandler(on_file_change)
            observer = Observer()
            observer.schedule(handler, dir_path, recursive=False)
            observer.start()

            self._observers[dir_path] = observer
            self._observer_failures[dir_path] = 0  # Reset failure count
            logger.debug(f"Started watchdog for {dir_path}")
            return True

        except Exception as e:
            logger.warning(f"Failed to start watchdog: {e}, falling back to polling")
            return False

    def _restart_watchdog(self, dir_path: str):
        """Attempt to restart a failed watchdog observer."""
        # Check failure count
        failures = self._observer_failures.get(dir_path, 0)
        if failures >= self._max_observer_restarts:
            logger.error(f"Watchdog for {dir_path} exceeded max restarts ({self._max_observer_restarts}), using polling only")
            if dir_path in self._observers:
                del self._observers[dir_path]
            return

        # Stop old observer if exists
        if dir_path in self._observers:
            try:
                old_observer = self._observers[dir_path]
                old_observer.stop()
                old_observer.join(timeout=1.0)
            except Exception:
                pass
            del self._observers[dir_path]

        # Increment failure count before attempting restart
        self._observer_failures[dir_path] = failures + 1

        # Attempt restart
        if self._create_watchdog_observer(dir_path):
            logger.info(f"Successfully restarted watchdog for {dir_path} (attempt {failures + 1})")
        else:
            logger.warning(f"Failed to restart watchdog for {dir_path} (attempt {failures + 1})")

    def _start_monitor_thread(self):
        """Start background monitoring thread."""
        self._stop_event.clear()
        self._running = True

        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="ContextWatcherMonitor"
        )
        self._monitor_thread.start()
        logger.info("Started ContextWatcher monitor thread")

    def _monitor_loop(self):
        """Background loop that periodically checks all sessions."""
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    sessions = list(self._sessions.items())

                for session_id, monitor in sessions:
                    if self._stop_event.is_set():
                        break

                    try:
                        # Track timing for detection latency
                        check_start = time.time() * 1000  # ms

                        # Process any new entries
                        signal = monitor.process_updates()

                        if signal:
                            # Calculate detection latency
                            detection_latency = time.time() * 1000 - check_start
                            self._metrics.record_detection_latency(detection_latency)

                            # Update legacy stats
                            self._total_handoffs += 1
                            if signal.level == HandoffLevel.GRACEFUL:
                                self._graceful_handoffs += 1
                            else:
                                self._emergency_handoffs += 1

                            # Update new metrics
                            self._metrics.record_handoff(signal.level, signal.tokens_used)

                        # Track peak tokens across all sessions
                        if monitor.peak_tokens > self._metrics.peak_tokens_seen:
                            self._metrics.peak_tokens_seen = monitor.peak_tokens

                        # Clean up stale sessions
                        if monitor.is_stale():
                            logger.info(f"Session {session_id} is stale, cleaning up")
                            self._cleanup_session(session_id)

                    except Exception as e:
                        logger.error(f"Error monitoring session {session_id}: {e}")

            except Exception as e:
                logger.error(f"Monitor loop error: {e}")

            # Sleep between checks
            self._stop_event.wait(CHECK_INTERVAL)

    def _cleanup_session(self, session_id: str):
        """Clean up a session (internal, assumes lock NOT held).

        CRITICAL: This method must stop the time-based handoff monitor before
        deleting the session. Failure to do so results in zombie timer threads
        that fire callbacks for sessions that no longer exist.

        Bug fixed: Previously this method did not call stop_time_handoff_monitor(),
        while stop_watching() did. This caused zombie timers from stale sessions
        to fire handoff callbacks on the wrong session context.
        """
        with self._lock:
            if session_id in self._sessions:
                monitor = self._sessions[session_id]

                # CRITICAL: Stop the time-based handoff monitor to prevent zombie timers
                # This was missing before, causing timers to fire after session cleanup
                monitor.stop_time_handoff_monitor()
                logger.info(f"Stopped time handoff monitor for stale session {session_id}")

                # Update metrics before deletion
                self._metrics.sessions_completed += 1
                self._metrics.sessions_active = len(self._sessions) - 1

                del self._sessions[session_id]

            # Clean up unused observers
            self._cleanup_unused_observers()

    def _cleanup_unused_observers(self):
        """Stop observers for directories no longer being watched."""
        # Get all directories currently being watched
        active_dirs = set()
        for monitor in self._sessions.values():
            if monitor.transcript_dir:
                active_dirs.add(str(monitor.transcript_dir))

        # Stop observers for inactive directories
        for dir_path in list(self._observers.keys()):
            if dir_path not in active_dirs:
                try:
                    observer = self._observers.pop(dir_path)
                    observer.stop()
                    observer.join(timeout=1.0)
                    logger.debug(f"Stopped watchdog for {dir_path}")
                except Exception:
                    pass

    def stop_watching(self, session_id: str):
        """
        Stop monitoring a specific session.

        Args:
            session_id: Session ID returned from start_watching()
        """
        with self._lock:
            if session_id in self._sessions:
                monitor = self._sessions[session_id]
                stats = monitor.get_stats()

                # Stop time-based handoff monitor if running
                monitor.stop_time_handoff_monitor()

                del self._sessions[session_id]

                # Update metrics
                self._metrics.sessions_completed += 1
                self._metrics.sessions_active = len(self._sessions)

                logger.info(
                    f"Stopped watching session {session_id}. "
                    f"Peak tokens: {stats['peak_tokens']}, "
                    f"Handoff triggered: {stats['handoff_triggered']}"
                )

                self._cleanup_unused_observers()

                # Stop monitor thread if no more sessions
                if not self._sessions and self._running:
                    self._stop_monitor_thread()

    def _stop_monitor_thread(self):
        """Stop the background monitoring thread."""
        self._running = False
        self._stop_event.set()

        if self._monitor_thread:
            try:
                self._monitor_thread.join(timeout=2.0)
            except Exception:
                pass
            self._monitor_thread = None

        logger.info("Stopped ContextWatcher monitor thread")

    def stop_all(self):
        """Stop all monitoring."""
        with self._lock:
            # Stop all observers
            for dir_path, observer in self._observers.items():
                try:
                    observer.stop()
                    observer.join(timeout=1.0)
                except Exception:
                    pass
            self._observers.clear()

            # Clear sessions
            self._sessions.clear()

            # Stop monitor thread
            self._stop_monitor_thread()

        logger.info("Stopped all ContextWatcher monitoring")

    def get_session_stats(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get statistics for a specific session."""
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id].get_stats()
        return None

    def get_all_stats(self) -> Dict[str, Any]:
        """Get overall watcher statistics."""
        with self._lock:
            session_stats = {
                sid: mon.get_stats()
                for sid, mon in self._sessions.items()
            }

            return {
                "enabled": CONTEXT_WATCHER_ENABLED,
                "active_provider": get_active_provider(),
                "running": self._running,
                "using_watchdog": HAS_WATCHDOG,
                "active_sessions": len(self._sessions),
                "active_observers": len(self._observers),
                "total_handoffs": self._total_handoffs,
                "graceful_handoffs": self._graceful_handoffs,
                "emergency_handoffs": self._emergency_handoffs,
                "sessions": session_stats,
                "thresholds": {
                    "graceful_legacy": GRACEFUL_THRESHOLD,
                    "emergency_legacy": EMERGENCY_THRESHOLD,
                    "claude_graceful_ratio": CLAUDE_GRACEFUL_CONTEXT_RATIO,
                    "claude_emergency_ratio": CLAUDE_EMERGENCY_CONTEXT_RATIO,
                    "codex_context_handoff_enabled": CODEX_CONTEXT_HANDOFF_ENABLED,
                    "low_cache_read": LOW_CACHE_READ_THRESHOLD,
                    "model_context_windows": MODEL_CONTEXT_WINDOWS,
                },
                "metrics": self._metrics.to_dict()
            }

    def is_running(self) -> bool:
        """Check if watcher is currently running."""
        return self._running

    def get_metrics(self) -> WatcherMetrics:
        """Get the metrics object for this watcher."""
        return self._metrics

    def get_metrics_dict(self) -> Dict[str, Any]:
        """Get metrics as a dictionary for JSON serialization."""
        return self._metrics.to_dict()

    def set_monitored_pid(self, session_id: str, pid: int):
        """Set the monitored subprocess PID for a specific session.

        Propagates through SessionMonitor -> ActivityAwareHandoffMonitor.
        """
        with self._lock:
            monitor = self._sessions.get(session_id)
            if monitor:
                monitor.set_monitored_pid(pid)
                logger.debug(
                    f"ContextWatcher: set monitored PID {pid} for session {session_id}"
                )

    def set_monitored_pid_for_active_session(self, pid: int):
        """Set monitored PID on the most recently started session.

        Convenience method when the caller doesn't track session IDs
        (e.g. invoke_llm in the conductor).
        """
        with self._lock:
            if not self._sessions:
                return
            # Pick the session with the latest start time
            latest_sid = max(
                self._sessions,
                key=lambda sid: self._sessions[sid].started_at
            )
            monitor = self._sessions[latest_sid]
            monitor.set_monitored_pid(pid)
            logger.debug(
                f"ContextWatcher: set monitored PID {pid} for active session {latest_sid}"
            )


# =============================================================================
# GLOBAL SINGLETON
# =============================================================================

_watcher_instance: Optional[ContextWatcher] = None
_watcher_lock = threading.Lock()


def get_context_watcher() -> ContextWatcher:
    """
    Get the global ContextWatcher instance.

    Returns:
        The singleton ContextWatcher instance
    """
    global _watcher_instance

    with _watcher_lock:
        if _watcher_instance is None:
            _watcher_instance = ContextWatcher()
        return _watcher_instance


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def start_context_watching(
    workspace_path: str,
    callback: Callable[[HandoffSignal], None],
    stage: Optional[str] = None,
    provider: Optional[str] = None,
) -> Optional[str]:
    """
    Convenience function to start watching a workspace.

    Args:
        workspace_path: Path to workspace
        callback: Handoff callback function
        stage: Current R&D stage for adaptive timeout (optional)
        provider: Provider used by the active LLM subprocess (optional)

    Returns:
        Session ID or None
    """
    return get_context_watcher().start_watching(
        workspace_path,
        callback,
        stage=stage,
        provider=provider,
    )


def stop_context_watching(session_id: str):
    """Convenience function to stop watching a session."""
    get_context_watcher().stop_watching(session_id)


def stop_all_context_watching():
    """Convenience function to stop all watching."""
    get_context_watcher().stop_all()


# =============================================================================
# HANDOFF.MD WRITER
# =============================================================================

def write_handoff_state(
    workspace_path: str,
    mission_id: str,
    stage: str,
    summary: str
) -> bool:
    """
    Write or append to HANDOFF.md state file.

    HANDOFF.md is APPEND-ONLY. Each handoff adds a new timestamped section.

    Args:
        workspace_path: Path to workspace
        mission_id: Current mission ID
        stage: Current stage (BUILDING, TESTING, etc.)
        summary: Summary text from Haiku

    Returns:
        True if written successfully
    """
    try:
        handoff_path = Path(workspace_path) / "HANDOFF.md"

        # Count existing handoffs
        handoff_num = 1
        if handoff_path.exists():
            content = handoff_path.read_text()
            handoff_num = content.count("## Handoff #") + 1

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        section = f"""
## Handoff #{handoff_num} - {timestamp}
**Mission:** {mission_id}
**Stage:** {stage}

{summary}

---
"""

        # Append to file
        with open(handoff_path, 'a') as f:
            f.write(section)

        logger.info(f"Wrote HANDOFF.md section #{handoff_num} for {mission_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to write HANDOFF.md: {e}")
        return False


def count_handoffs(workspace_path: str) -> int:
    """Count the number of handoffs in HANDOFF.md."""
    try:
        handoff_path = Path(workspace_path) / "HANDOFF.md"
        if not handoff_path.exists():
            return 0
        content = handoff_path.read_text()
        return content.count("## Handoff #")
    except Exception:
        return 0


# =============================================================================
# SELF-TEST
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("ContextWatcher - Self Test")
    print("=" * 60)

    print(f"\nConfiguration:")
    print(f"  Enabled: {CONTEXT_WATCHER_ENABLED}")
    print(f"  Active provider: {get_active_provider()}")
    print(f"  Watchdog available: {HAS_WATCHDOG}")
    print(f"  Claude projects dir: {CLAUDE_PROJECTS_DIR}")
    print(f"  Projects dir exists: {CLAUDE_PROJECTS_DIR.exists()}")
    print(f"  Codex sessions dir: {CODEX_SESSIONS_DIR}")
    print(f"  Codex dir exists: {CODEX_SESSIONS_DIR.exists()}")
    print(f"  Graceful threshold (legacy fallback): {GRACEFUL_THRESHOLD:,}")
    print(f"  Emergency threshold (legacy fallback): {EMERGENCY_THRESHOLD:,}")
    print(f"  Claude graceful ratio: {CLAUDE_GRACEFUL_CONTEXT_RATIO}")
    print(f"  Claude emergency ratio: {CLAUDE_EMERGENCY_CONTEXT_RATIO}")
    print(f"  Known models: {', '.join(sorted(MODEL_CONTEXT_WINDOWS.keys()))}")

    # Test 1: Session classification
    print("\n[TEST 1] Session Classification")
    if CLAUDE_PROJECTS_DIR.exists():
        for project_dir in list(CLAUDE_PROJECTS_DIR.iterdir())[:3]:
            if project_dir.is_dir():
                jsonl_files = list(project_dir.glob("*.jsonl"))[:1]
                for jsonl in jsonl_files:
                    is_p = is_p_mode_session(jsonl)
                    print(f"  {jsonl.parent.name}/{jsonl.name}: -p mode = {is_p}")

    # Test 2: Find transcript dir
    print("\n[TEST 2] Find Transcript Directory")
    test_paths = [
        "/home/vader/AI-AtlasForge/workspace/ContextWatcher",
        "/home/vader/AI-AtlasForge/workspace/StenoAI",
    ]
    for path in test_paths:
        result = find_transcript_dir(path)
        print(f"  {path}")
        print(f"    -> {result}")

    # Test 3: TokenState
    print("\n[TEST 3] TokenState")
    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 50000,
        "cache_creation_input_tokens": 80000,
        "output_tokens": 500
    }
    tokens = TokenState.from_usage(usage, "req_123")
    print(f"  Usage: {usage}")
    print(f"  Total context: {tokens.total_context:,}")
    print(f"  Would trigger graceful (legacy 130k): {tokens.cache_creation_input_tokens >= GRACEFUL_THRESHOLD}")
    print(f"  Model context window: {tokens.model_context_window:,}")

    # Test 4: ContextWatcher
    print("\n[TEST 4] ContextWatcher Instance")
    watcher = get_context_watcher()
    print(f"  Stats: {json.dumps(watcher.get_all_stats(), indent=2)}")

    # Test 5: Full watch test (optional)
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        print("\n[TEST 5] Full Watch Test (Ctrl+C to stop)")

        def on_handoff(signal: HandoffSignal):
            print(f"  HANDOFF SIGNAL: {signal.to_dict()}")

        test_workspace = "/home/vader/AI-AtlasForge/workspace/ContextWatcher"
        session_id = watcher.start_watching(test_workspace, on_handoff)

        if session_id:
            print(f"  Started session: {session_id}")
            try:
                while True:
                    time.sleep(5)
                    stats = watcher.get_session_stats(session_id)
                    if stats:
                        print(f"  Tokens: {stats['last_tokens']}")
            except KeyboardInterrupt:
                print("\n  Stopping...")

            watcher.stop_watching(session_id)
        else:
            print("  Failed to start watching")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
