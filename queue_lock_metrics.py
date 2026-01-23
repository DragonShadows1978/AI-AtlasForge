"""
Queue Lock Metrics

Provides optional timing and monitoring capabilities for queue processing locks.
Tracks:
- Lock acquisition wait times
- Lock hold durations
- Warnings for long-held locks
- Historical metrics for debugging

Usage:
    from queue_lock_metrics import LockMetrics

    metrics = LockMetrics()
    metrics.start_acquisition_timer()
    # ... try to acquire ...
    metrics.record_acquisition(acquired=True, wait_time=0.5)

    metrics.start_hold_timer()
    # ... do work ...
    metrics.record_release(hold_time=2.3)
"""

import logging
import time
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)

# Configuration
LONG_HOLD_WARNING_SECONDS = 30  # Warn if lock held > 30 seconds
MAX_HISTORY_SIZE = 100  # Keep last 100 lock events

# Valid lock sources
VALID_LOCK_SOURCES = frozenset([
    "af_engine",
    "queue_next_api",
    "queue_watcher",
    "test",  # Allow for testing
    "manual",  # For admin operations
])


@dataclass
class LockEvent:
    """Single lock acquisition/release event."""
    timestamp: str
    event_type: str  # 'acquire', 'release', 'acquire_failed', 'timeout'
    source: str
    wait_time_ms: Optional[float] = None
    hold_time_ms: Optional[float] = None
    mission_id: Optional[str] = None
    warning: Optional[str] = None


@dataclass
class LockMetrics:
    """
    Tracks timing metrics for queue processing locks.

    Thread-safe singleton that accumulates metrics across lock operations.
    """
    _instance: Optional['LockMetrics'] = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    # Active timers (per-thread)
    _acquisition_starts: Dict[int, float] = field(default_factory=dict, init=False, repr=False)
    _hold_starts: Dict[int, float] = field(default_factory=dict, init=False, repr=False)

    # Historical events
    history: deque = field(default_factory=lambda: deque(maxlen=MAX_HISTORY_SIZE), init=False)

    # Aggregated stats
    total_acquisitions: int = 0
    total_failures: int = 0
    total_wait_time_ms: float = 0.0
    total_hold_time_ms: float = 0.0
    max_wait_time_ms: float = 0.0
    max_hold_time_ms: float = 0.0
    warnings_issued: int = 0

    def __new__(cls) -> 'LockMetrics':
        """Ensure singleton instance."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def start_acquisition_timer(self) -> None:
        """Start timing a lock acquisition attempt."""
        thread_id = threading.get_ident()
        with self._lock:
            self._acquisition_starts[thread_id] = time.monotonic()

    def stop_acquisition_timer(self) -> float:
        """Stop acquisition timer and return elapsed time in ms."""
        thread_id = threading.get_ident()
        elapsed = 0.0
        with self._lock:
            start = self._acquisition_starts.pop(thread_id, None)
            if start is not None:
                elapsed = (time.monotonic() - start) * 1000
        return elapsed

    def start_hold_timer(self) -> None:
        """Start timing how long a lock is held."""
        thread_id = threading.get_ident()
        with self._lock:
            self._hold_starts[thread_id] = time.monotonic()

    def stop_hold_timer(self) -> float:
        """Stop hold timer and return elapsed time in ms."""
        thread_id = threading.get_ident()
        elapsed = 0.0
        with self._lock:
            start = self._hold_starts.pop(thread_id, None)
            if start is not None:
                elapsed = (time.monotonic() - start) * 1000
        return elapsed

    def get_hold_duration(self) -> float:
        """Get current hold duration without stopping timer."""
        thread_id = threading.get_ident()
        with self._lock:
            start = self._hold_starts.get(thread_id)
            if start is not None:
                return (time.monotonic() - start) * 1000
        return 0.0

    def record_acquisition(
        self,
        source: str,
        acquired: bool,
        wait_time_ms: Optional[float] = None,
        mission_id: Optional[str] = None
    ) -> None:
        """Record a lock acquisition attempt."""
        if wait_time_ms is None:
            wait_time_ms = self.stop_acquisition_timer()

        event_type = "acquire" if acquired else "acquire_failed"
        warning = None

        with self._lock:
            if acquired:
                self.total_acquisitions += 1
                self.total_wait_time_ms += wait_time_ms
                self.max_wait_time_ms = max(self.max_wait_time_ms, wait_time_ms)
            else:
                self.total_failures += 1

            event = LockEvent(
                timestamp=datetime.now().isoformat(),
                event_type=event_type,
                source=source,
                wait_time_ms=wait_time_ms,
                mission_id=mission_id,
                warning=warning
            )
            self.history.append(event)

        # Log metrics
        if acquired:
            logger.debug(f"Lock acquired by {source} (wait: {wait_time_ms:.1f}ms)")
        else:
            logger.debug(f"Lock acquisition failed for {source} (wait: {wait_time_ms:.1f}ms)")

    def record_release(
        self,
        source: str,
        hold_time_ms: Optional[float] = None,
        mission_id: Optional[str] = None
    ) -> None:
        """Record a lock release."""
        if hold_time_ms is None:
            hold_time_ms = self.stop_hold_timer()

        warning = None

        with self._lock:
            self.total_hold_time_ms += hold_time_ms
            self.max_hold_time_ms = max(self.max_hold_time_ms, hold_time_ms)

            # Check for long hold warning
            if hold_time_ms > LONG_HOLD_WARNING_SECONDS * 1000:
                warning = f"Lock held for {hold_time_ms/1000:.1f}s (>{LONG_HOLD_WARNING_SECONDS}s)"
                self.warnings_issued += 1
                logger.warning(warning)

            event = LockEvent(
                timestamp=datetime.now().isoformat(),
                event_type="release",
                source=source,
                hold_time_ms=hold_time_ms,
                mission_id=mission_id,
                warning=warning
            )
            self.history.append(event)

        logger.debug(f"Lock released by {source} (held: {hold_time_ms:.1f}ms)")

    def record_timeout(
        self,
        source: str,
        wait_time_ms: float,
        mission_id: Optional[str] = None
    ) -> None:
        """Record a timeout while waiting for lock."""
        with self._lock:
            self.total_failures += 1
            warning = f"Lock acquisition timed out after {wait_time_ms/1000:.1f}s"
            self.warnings_issued += 1

            event = LockEvent(
                timestamp=datetime.now().isoformat(),
                event_type="timeout",
                source=source,
                wait_time_ms=wait_time_ms,
                mission_id=mission_id,
                warning=warning
            )
            self.history.append(event)

        logger.warning(f"Lock timeout for {source} after {wait_time_ms:.1f}ms")

    def get_summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        with self._lock:
            total = self.total_acquisitions + self.total_failures
            success_rate = (self.total_acquisitions / total * 100) if total > 0 else 0
            avg_wait = (self.total_wait_time_ms / self.total_acquisitions) if self.total_acquisitions > 0 else 0
            avg_hold = (self.total_hold_time_ms / self.total_acquisitions) if self.total_acquisitions > 0 else 0

            return {
                "total_acquisitions": self.total_acquisitions,
                "total_failures": self.total_failures,
                "success_rate": round(success_rate, 1),
                "avg_wait_time_ms": round(avg_wait, 1),
                "max_wait_time_ms": round(self.max_wait_time_ms, 1),
                "avg_hold_time_ms": round(avg_hold, 1),
                "max_hold_time_ms": round(self.max_hold_time_ms, 1),
                "warnings_issued": self.warnings_issued,
                "history_size": len(self.history)
            }

    def get_recent_events(self, n: int = 20) -> List[Dict[str, Any]]:
        """Get recent lock events."""
        with self._lock:
            events = list(self.history)[-n:]
            return [
                {
                    "timestamp": e.timestamp,
                    "event_type": e.event_type,
                    "source": e.source,
                    "wait_time_ms": e.wait_time_ms,
                    "hold_time_ms": e.hold_time_ms,
                    "mission_id": e.mission_id,
                    "warning": e.warning
                }
                for e in events
            ]

    def reset(self) -> None:
        """Reset all metrics (for testing)."""
        with self._lock:
            self.total_acquisitions = 0
            self.total_failures = 0
            self.total_wait_time_ms = 0.0
            self.total_hold_time_ms = 0.0
            self.max_wait_time_ms = 0.0
            self.max_hold_time_ms = 0.0
            self.warnings_issued = 0
            self.history.clear()
            self._acquisition_starts.clear()
            self._hold_starts.clear()


def validate_lock_source(source: str) -> tuple[bool, str]:
    """
    Validate that a lock source is recognized.

    Args:
        source: The source string to validate

    Returns:
        Tuple of (is_valid, message)
    """
    if not source:
        return False, "Lock source cannot be empty"

    if not source.strip():
        return False, "Lock source cannot be whitespace only"

    source_clean = source.strip().lower()

    # Check against known valid sources
    if source_clean not in VALID_LOCK_SOURCES:
        # Log warning but allow - this enables future sources
        logger.warning(
            f"Unknown lock source '{source}'. "
            f"Known sources: {', '.join(sorted(VALID_LOCK_SOURCES))}"
        )
        return True, f"Warning: Unknown source '{source}', but proceeding"

    return True, "Valid source"


# Global singleton instance
_metrics_instance: Optional[LockMetrics] = None


def get_lock_metrics() -> LockMetrics:
    """Get the global lock metrics singleton."""
    global _metrics_instance
    if _metrics_instance is None:
        _metrics_instance = LockMetrics()
    return _metrics_instance
