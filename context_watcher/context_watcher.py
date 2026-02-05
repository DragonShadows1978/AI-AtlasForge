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
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Any, Set
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

# Claude projects directory
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Token thresholds
GRACEFUL_THRESHOLD = 130_000  # Trigger HANDOFF.md generation
EMERGENCY_THRESHOLD = 140_000  # Kill Claude immediately
EARLY_FAILURE_THRESHOLD = 2_000  # Warning for startup issues (not exhaustion)

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
TIME_BASED_HANDOFF_MINUTES = int(os.environ.get(
    "TIME_BASED_HANDOFF_MINUTES", "55"
))


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
    def from_usage(cls, usage: Dict[str, Any], request_id: Optional[str] = None) -> "TokenState":
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

        return cls(
            cache_read_input_tokens=safe_int(usage.get("cache_read_input_tokens", 0)),
            cache_creation_input_tokens=safe_int(usage.get("cache_creation_input_tokens", 0)),
            input_tokens=safe_int(usage.get("input_tokens", 0)),
            output_tokens=safe_int(usage.get("output_tokens", 0)),
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


def find_transcript_dir(workspace_path: str) -> Optional[Path]:
    """
    Find the Claude transcript directory for a workspace.

    Claude stores transcripts in ~/.claude/projects/-{path-with-dashes}
    where slashes become dashes.

    Args:
        workspace_path: Absolute path to the mission workspace

    Returns:
        Path to transcript directory, or None if not found
    """
    # Convert workspace path to Claude's format
    # /home/vader/AI-AtlasForge/workspace/StenoAI
    # -> -home-vader-AI-AtlasForge-workspace-StenoAI
    escaped = workspace_path.replace('/', '-')
    if escaped.startswith('-'):
        escaped = escaped[1:]  # Remove leading dash

    transcript_dir = CLAUDE_PROJECTS_DIR / f"-{escaped}"

    if transcript_dir.exists():
        logger.debug(f"Found transcript dir: {transcript_dir}")
        return transcript_dir

    # Fallback: search for partial match
    if CLAUDE_PROJECTS_DIR.exists():
        # Extract workspace name for partial match
        workspace_name = Path(workspace_path).name

        for d in CLAUDE_PROJECTS_DIR.iterdir():
            if d.is_dir() and workspace_name in d.name:
                logger.debug(f"Found transcript dir via partial match: {d}")
                return d

    logger.warning(f"No transcript directory found for: {workspace_path}")
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
        enable_time_handoff: bool = True
    ):
        """
        Initialize session monitor.

        Args:
            session_id: Unique session identifier
            workspace_path: Path to the workspace being monitored
            callback: Function to call when handoff is triggered
            enable_time_handoff: Whether to enable time-based handoff (default True)
        """
        self.session_id = session_id
        self.workspace_path = workspace_path
        self.callback = callback

        # Find transcript directory
        self.transcript_dir = find_transcript_dir(workspace_path)

        # File tracking
        self.current_jsonl: Optional[Path] = None
        self.file_offset: int = 0
        self.file_mtime: float = 0

        # Token state
        self.last_tokens: Optional[TokenState] = None
        self.peak_tokens = 0
        self.seen_request_ids: Set[str] = set()

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

    def _find_active_jsonl(self) -> Optional[Path]:
        """Find the most recently modified JSONL file."""
        if not self.transcript_dir or not self.transcript_dir.exists():
            return None

        jsonl_files = list(self.transcript_dir.glob("*.jsonl"))
        if not jsonl_files:
            return None

        # Filter to -p mode sessions only
        valid_files = []
        for f in jsonl_files:
            if is_p_mode_session(f):
                valid_files.append(f)

        if not valid_files:
            return None

        # Return most recently modified
        return max(valid_files, key=lambda p: p.stat().st_mtime)

    def _read_new_entries(self) -> List[Dict[str, Any]]:
        """
        Read new JSONL entries since last read.

        Returns:
            List of new parsed JSON records
        """
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

    def _extract_token_state(self, record: Dict[str, Any]) -> Optional[TokenState]:
        """Extract TokenState from JSONL record if it has usage data."""
        # Ensure record is a dict (malformed entries might be arrays or other types)
        if not isinstance(record, dict):
            return None

        # Only process assistant responses
        if record.get('type') != 'assistant':
            return None

        message = record.get('message', {})

        # Handle case where message is not a dict (malformed entry)
        if not isinstance(message, dict):
            return None

        usage = message.get('usage', {})

        # Handle case where usage is not a dict
        if not isinstance(usage, dict):
            return None

        request_id = record.get('requestId')

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

        return TokenState.from_usage(usage, request_id)

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
        cache_read = tokens.cache_read_input_tokens
        cache_creation = tokens.cache_creation_input_tokens

        # Update peak
        if total > self.peak_tokens:
            self.peak_tokens = total

        # Early failure detection (startup issue, not exhaustion)
        if total < EARLY_FAILURE_THRESHOLD and cache_read == 0 and cache_creation == 0:
            logger.debug(f"Session {self.session_id}: Low tokens ({total}), likely startup")
            return None

        # Context exhaustion pattern:
        # High cache_creation + low cache_read = hitting the wall
        # Claude can't reuse cache because it's at the limit

        if cache_read < LOW_CACHE_READ_THRESHOLD:
            level = None

            if cache_creation >= EMERGENCY_THRESHOLD:
                level = HandoffLevel.EMERGENCY
                logger.warning(
                    f"Session {self.session_id}: EMERGENCY threshold reached! "
                    f"cache_creation={cache_creation}, cache_read={cache_read}"
                )
            elif cache_creation >= GRACEFUL_THRESHOLD:
                level = HandoffLevel.GRACEFUL
                logger.info(
                    f"Session {self.session_id}: Graceful threshold reached. "
                    f"cache_creation={cache_creation}, cache_read={cache_read}"
                )

            if level:
                self.handoff_triggered = True
                self.handoff_level = level

                return HandoffSignal(
                    level=level,
                    session_id=self.session_id,
                    workspace_path=self.workspace_path,
                    tokens_used=total,
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
            "transcript_dir": str(self.transcript_dir) if self.transcript_dir else None,
            "current_jsonl": str(self.current_jsonl) if self.current_jsonl else None,
            "peak_tokens": self.peak_tokens,
            "last_tokens": {
                "total": self.last_tokens.total_context if self.last_tokens else 0,
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
        """Start the time-based handoff monitor for this session."""
        if not self._enable_time_handoff:
            return

        if self._time_handoff_monitor is not None:
            return  # Already running

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
        enable_time_handoff: bool = True
    ) -> Optional[str]:
        """
        Start monitoring a workspace for context exhaustion.

        Args:
            workspace_path: Path to the workspace to monitor
            callback: Function to call when handoff is triggered
            enable_time_handoff: Whether to enable time-based handoff (default True)

        Returns:
            Session ID if started successfully, None on failure
        """
        if not CONTEXT_WATCHER_ENABLED:
            logger.info("ContextWatcher disabled via env var")
            return None

        with self._lock:
            # Generate session ID
            session_id = str(uuid.uuid4())[:8]

            # Create session monitor with time-based handoff option
            monitor = SessionMonitor(
                session_id, workspace_path, callback,
                enable_time_handoff=enable_time_handoff
            )

            if not monitor.transcript_dir:
                logger.warning(f"Cannot watch {workspace_path}: no transcript dir found")
                return None

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
                "running": self._running,
                "using_watchdog": HAS_WATCHDOG,
                "active_sessions": len(self._sessions),
                "active_observers": len(self._observers),
                "total_handoffs": self._total_handoffs,
                "graceful_handoffs": self._graceful_handoffs,
                "emergency_handoffs": self._emergency_handoffs,
                "sessions": session_stats,
                "thresholds": {
                    "graceful": GRACEFUL_THRESHOLD,
                    "emergency": EMERGENCY_THRESHOLD,
                    "low_cache_read": LOW_CACHE_READ_THRESHOLD
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
    callback: Callable[[HandoffSignal], None]
) -> Optional[str]:
    """
    Convenience function to start watching a workspace.

    Args:
        workspace_path: Path to workspace
        callback: Handoff callback function

    Returns:
        Session ID or None
    """
    return get_context_watcher().start_watching(workspace_path, callback)


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
    print(f"  Watchdog available: {HAS_WATCHDOG}")
    print(f"  Claude projects dir: {CLAUDE_PROJECTS_DIR}")
    print(f"  Projects dir exists: {CLAUDE_PROJECTS_DIR.exists()}")
    print(f"  Graceful threshold: {GRACEFUL_THRESHOLD:,}")
    print(f"  Emergency threshold: {EMERGENCY_THRESHOLD:,}")

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
    print(f"  Would trigger graceful: {tokens.cache_creation_input_tokens >= GRACEFUL_THRESHOLD}")

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
