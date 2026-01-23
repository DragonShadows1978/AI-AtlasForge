"""
Queue Processing Lock

File-based locking to prevent race conditions between:
1. af_engine._process_mission_queue() - called when mission stage changes to COMPLETE
2. dashboard_v2.queue_auto_start_watcher() - background thread checking for idle state

This module provides atomic file locking to ensure only one process can process
the queue at a time, preventing duplicate mission creation or queue item loss.

Lock file location: state/queue_processing.lock
Lock timeout: 60 seconds (auto-expire for crash recovery)
"""

import os
import fcntl
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import contextmanager
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Optional metrics tracking - lazy load to avoid circular imports
_metrics = None

def _get_metrics():
    """Lazy load metrics singleton."""
    global _metrics
    if _metrics is None:
        try:
            from queue_lock_metrics import get_lock_metrics
            _metrics = get_lock_metrics()
        except ImportError:
            _metrics = False  # Mark as unavailable
    return _metrics if _metrics else None

# Configuration
BASE_DIR = Path(__file__).parent.resolve()
STATE_DIR = BASE_DIR / "state"
LOCK_FILE_PATH = STATE_DIR / "queue_processing.lock"
LOCK_TIMEOUT_SECONDS = 60  # Auto-expire after 60 seconds
ACQUIRE_TIMEOUT_SECONDS = 5  # Default timeout for blocking acquire


def _get_lock_data(
    source: str,
    mission_id: Optional[str] = None,
    operation: str = "queue_processing"
) -> Dict[str, Any]:
    """Build lock metadata dictionary."""
    now = datetime.now()
    return {
        "locked_at": now.isoformat(),
        "locked_by": source,
        "mission_id": mission_id,
        "operation": operation,
        "pid": os.getpid(),
        "expires_at": (now + timedelta(seconds=LOCK_TIMEOUT_SECONDS)).isoformat()
    }


def _read_lock_info() -> Optional[Dict[str, Any]]:
    """Read current lock file contents if it exists."""
    try:
        if LOCK_FILE_PATH.exists():
            with open(LOCK_FILE_PATH, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError, IOError) as e:
        logger.debug(f"Could not read lock file: {e}")
    return None


def _is_lock_expired(lock_info: Dict[str, Any]) -> bool:
    """Check if lock has expired based on expires_at timestamp."""
    try:
        expires_at = datetime.fromisoformat(lock_info.get("expires_at", ""))
        return datetime.now() > expires_at
    except (ValueError, TypeError):
        # Can't parse timestamp - consider expired
        return True


def _is_lock_pid_alive(lock_info: Dict[str, Any]) -> bool:
    """Check if the process that holds the lock is still running."""
    pid = lock_info.get("pid")
    if not pid:
        return False
    try:
        # Check if process exists
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def is_queue_locked() -> bool:
    """
    Check if the queue is currently locked.

    Returns False if:
    - Lock file doesn't exist
    - Lock has expired
    - Holding process is dead
    """
    lock_info = _read_lock_info()
    if lock_info is None:
        return False

    # Check expiration
    if _is_lock_expired(lock_info):
        logger.debug("Lock exists but expired")
        return False

    # Check if holding process is alive
    if not _is_lock_pid_alive(lock_info):
        logger.debug(f"Lock exists but holding process (PID {lock_info.get('pid')}) is dead")
        return False

    return True


def get_queue_lock_info() -> Optional[Dict[str, Any]]:
    """
    Get information about the current lock.

    Returns lock metadata if locked (and valid), None otherwise.
    """
    lock_info = _read_lock_info()
    if lock_info is None:
        return None

    # Enrich with validity status
    expired = _is_lock_expired(lock_info)
    pid_alive = _is_lock_pid_alive(lock_info)

    lock_info["is_valid"] = not expired and pid_alive
    lock_info["is_expired"] = expired
    lock_info["holder_alive"] = pid_alive

    return lock_info


def acquire_queue_lock(
    source: str,
    mission_id: Optional[str] = None,
    timeout: int = ACQUIRE_TIMEOUT_SECONDS,
    blocking: bool = True
) -> bool:
    """
    Acquire the queue processing lock.

    Args:
        source: Identifier for the caller (e.g., "af_engine", "queue_next_api", "queue_watcher")
        mission_id: Optional mission ID being processed
        timeout: Seconds to wait for lock acquisition (default 5)
        blocking: If True, wait up to timeout. If False, fail immediately if locked.

    Returns:
        True if lock acquired, False otherwise.
    """
    # Validate source
    try:
        from queue_lock_metrics import validate_lock_source
        valid, msg = validate_lock_source(source)
        if not valid:
            logger.warning(f"Invalid lock source: {msg}")
            return False
    except ImportError:
        # Metrics module not available - do basic validation
        if not source or not source.strip():
            logger.warning("Lock source cannot be empty")
            return False

    # Ensure state directory exists
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    # Start metrics timer
    metrics = _get_metrics()
    if metrics:
        metrics.start_acquisition_timer()

    start_time = time.time()

    while True:
        try:
            # Try to acquire via exclusive file lock
            fd = os.open(
                str(LOCK_FILE_PATH),
                os.O_WRONLY | os.O_CREAT,
                0o644
            )

            try:
                # Try non-blocking lock first
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

                # Got the OS-level lock - now check for stale application-level lock
                existing_lock = _read_lock_info()

                if existing_lock and not _is_lock_expired(existing_lock):
                    # There's a valid lock - check if holder is still alive
                    if _is_lock_pid_alive(existing_lock):
                        # Lock is truly held - release OS lock and fail/retry
                        fcntl.flock(fd, fcntl.LOCK_UN)
                        os.close(fd)

                        if not blocking or (time.time() - start_time) >= timeout:
                            logger.debug(f"Queue locked by {existing_lock.get('locked_by')} (PID {existing_lock.get('pid')})")
                            # Record failed acquisition
                            metrics = _get_metrics()
                            if metrics:
                                wait_time_ms = (time.time() - start_time) * 1000
                                metrics.record_acquisition(source, acquired=False, wait_time_ms=wait_time_ms, mission_id=mission_id)
                            return False

                        time.sleep(0.1)  # Short sleep before retry
                        continue
                    else:
                        # Holder is dead - we can take over
                        logger.info(f"Releasing stale lock from dead PID {existing_lock.get('pid')}")

                # Write our lock data
                lock_data = _get_lock_data(source, mission_id)

                with os.fdopen(fd, 'w') as f:
                    json.dump(lock_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())

                logger.info(f"Queue lock acquired by {source} (PID {os.getpid()})")

                # Record metrics
                metrics = _get_metrics()
                if metrics:
                    wait_time_ms = (time.time() - start_time) * 1000
                    metrics.record_acquisition(source, acquired=True, wait_time_ms=wait_time_ms, mission_id=mission_id)
                    metrics.start_hold_timer()

                return True

            except BlockingIOError:
                # OS lock is held by another process - close fd and retry/fail
                os.close(fd)

                if not blocking or (time.time() - start_time) >= timeout:
                    logger.debug("Queue lock held by another process (OS-level)")
                    # Record failed acquisition
                    metrics = _get_metrics()
                    if metrics:
                        wait_time_ms = (time.time() - start_time) * 1000
                        metrics.record_acquisition(source, acquired=False, wait_time_ms=wait_time_ms, mission_id=mission_id)
                    return False

                time.sleep(0.1)
                continue

        except OSError as e:
            logger.error(f"Error acquiring queue lock: {e}")
            return False


def release_queue_lock() -> bool:
    """
    Release the queue processing lock.

    Only releases if the current process holds the lock.

    Returns:
        True if lock released, False otherwise.
    """
    try:
        # Verify we hold the lock
        lock_info = _read_lock_info()
        if lock_info is None:
            logger.debug("No lock to release")
            return True  # Consider success - no lock exists

        if lock_info.get("pid") != os.getpid():
            logger.warning(f"Refusing to release lock held by PID {lock_info.get('pid')} (we are {os.getpid()})")
            return False

        source = lock_info.get("locked_by", "unknown")
        mission_id = lock_info.get("mission_id")

        # Remove the lock file
        LOCK_FILE_PATH.unlink(missing_ok=True)
        logger.info(f"Queue lock released by {source} (PID {os.getpid()})")

        # Record metrics
        metrics = _get_metrics()
        if metrics:
            metrics.record_release(source, mission_id=mission_id)

        return True

    except Exception as e:
        logger.error(f"Error releasing queue lock: {e}")
        return False


def force_release_stale_lock() -> bool:
    """
    Force release a stale lock (expired or dead holder).

    Use with caution - this is meant for administrative recovery.

    Returns:
        True if lock was stale and released, False otherwise.
    """
    lock_info = _read_lock_info()
    if lock_info is None:
        return True  # No lock

    expired = _is_lock_expired(lock_info)
    pid_alive = _is_lock_pid_alive(lock_info)

    if not expired and pid_alive:
        logger.warning("Lock is valid and holder is alive - refusing to force release")
        return False

    # Lock is stale - remove it
    try:
        LOCK_FILE_PATH.unlink(missing_ok=True)
        reason = "expired" if expired else f"dead holder (PID {lock_info.get('pid')})"
        logger.info(f"Force-released stale lock ({reason})")
        return True
    except Exception as e:
        logger.error(f"Error force-releasing lock: {e}")
        return False


@contextmanager
def queue_lock(
    source: str,
    mission_id: Optional[str] = None,
    timeout: int = ACQUIRE_TIMEOUT_SECONDS
):
    """
    Context manager for queue lock acquisition/release.

    Usage:
        with queue_lock("af_engine", mission_id="mission_abc") as locked:
            if locked:
                # Process queue
                ...
            else:
                # Lock not acquired
                ...

    The lock is automatically released when exiting the context.
    """
    acquired = acquire_queue_lock(source, mission_id, timeout)
    try:
        yield acquired
    finally:
        if acquired:
            release_queue_lock()


class QueueLock:
    """
    Class-based queue lock with context manager support.

    Usage:
        lock = QueueLock(source="af_engine", mission_id="mission_abc")

        # Method 1: Explicit acquire/release
        if lock.acquire():
            try:
                # Process queue
            finally:
                lock.release()

        # Method 2: Context manager
        with lock:
            if lock.acquired:
                # Process queue
    """

    def __init__(
        self,
        source: str,
        mission_id: Optional[str] = None,
        timeout: int = ACQUIRE_TIMEOUT_SECONDS
    ):
        self.source = source
        self.mission_id = mission_id
        self.timeout = timeout
        self.acquired = False

    def acquire(self, blocking: bool = True) -> bool:
        """Acquire the lock."""
        self.acquired = acquire_queue_lock(
            self.source,
            self.mission_id,
            self.timeout,
            blocking
        )
        return self.acquired

    def release(self) -> bool:
        """Release the lock if held."""
        if self.acquired:
            result = release_queue_lock()
            if result:
                self.acquired = False
            return result
        return True

    def __enter__(self):
        """Enter context - acquire lock."""
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context - release lock."""
        self.release()
        return False  # Don't suppress exceptions

    @property
    def is_locked(self) -> bool:
        """Check if queue is currently locked (by anyone)."""
        return is_queue_locked()

    @staticmethod
    def get_info() -> Optional[Dict[str, Any]]:
        """Get current lock information."""
        return get_queue_lock_info()
