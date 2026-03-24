"""
Resilience Module - Error handling, retries, and graceful degradation.

This module ensures adversarial testing doesn't fail due to transient issues:
1. API timeouts with exponential backoff retry
2. Rate limiting detection and waiting
3. Network failure recovery
4. Large codebase chunking
5. Progress reporting for long-running operations
"""

import concurrent.futures
import logging
import math
import time
import sys
import functools
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, TypeVar, Optional, List, Any, Dict
from datetime import datetime
from enum import Enum

sys.path.append(str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


class ErrorType(Enum):
    """Types of errors that can occur."""
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    NETWORK = "network"
    API_ERROR = "api_error"
    PARSE_ERROR = "parse_error"
    CODE_TOO_LARGE = "code_too_large"
    UNKNOWN = "unknown"


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    initial_delay: float = 1.0  # seconds
    max_delay: float = 60.0  # seconds
    exponential_base: float = 2.0
    jitter: float = 0.1  # ±10% jitter

    def __post_init__(self):
        # RES-2: reject non-int and bool for max_retries
        if isinstance(self.max_retries, bool) or not isinstance(self.max_retries, int):
            raise TypeError(f"max_retries must be int, got {type(self.max_retries).__name__}")
        # P2: reject negative max_retries
        if self.max_retries < 0:
            raise ValueError(f"max_retries must be non-negative, got {self.max_retries}")
        # RES-6: validate numeric fields
        for field_name in ('initial_delay', 'max_delay', 'exponential_base', 'jitter'):
            val = getattr(self, field_name)
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                raise TypeError(f"{field_name} must be a number, got {type(val).__name__}")
            if not math.isfinite(val):
                raise ValueError(f"{field_name} must be finite, got {val!r}")
        if self.initial_delay < 0:
            raise ValueError(f"initial_delay must be non-negative, got {self.initial_delay}")
        if self.max_delay < 0:
            raise ValueError(f"max_delay must be non-negative, got {self.max_delay}")
        # Cycle 2 Fix 3.2: cross-field validation
        if self.initial_delay > self.max_delay:
            raise ValueError(f"initial_delay ({self.initial_delay}) must not exceed max_delay ({self.max_delay})")
        if self.exponential_base < 1:
            raise ValueError(f"exponential_base must be >= 1, got {self.exponential_base}")
        if self.jitter < 0:
            raise ValueError(f"jitter must be non-negative, got {self.jitter}")
        if self.jitter > 1.0:
            raise ValueError(f"jitter must be <= 1.0 (100%), got {self.jitter}")


@dataclass
class ErrorRecord:
    """Record of an error that occurred."""
    error_type: ErrorType
    message: str
    timestamp: str
    retry_count: int
    recovered: bool
    component: str


@dataclass
class ProgressReport:
    """Progress report for long-running operations."""
    operation: str
    stage: str
    progress_percent: float
    items_completed: int
    items_total: int
    elapsed_seconds: float
    estimated_remaining_seconds: Optional[float]
    current_item: str
    errors_count: int
    warnings: List[str] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            f"[{self.progress_percent:.1f}%] {self.operation} - {self.stage}\n"
            f"  Progress: {self.items_completed}/{self.items_total} items\n"
            f"  Elapsed: {self.elapsed_seconds:.1f}s"
            f"{f', ETA: {self.estimated_remaining_seconds:.1f}s' if self.estimated_remaining_seconds else ''}\n"
            f"  Current: {self.current_item}\n"
            f"  Errors: {self.errors_count}"
        )


class ProgressTracker:
    """
    Tracks progress of long-running operations.

    Usage:
        tracker = ProgressTracker("Adversarial Testing", total_items=5)
        tracker.set_callback(print)

        with tracker.stage("Red Team Analysis"):
            # do work
            tracker.item_complete("analyzed file.py")

        tracker.report()
    """

    def __init__(
        self,
        operation: str,
        total_items: int = 0,
        callback: Optional[Callable[[ProgressReport], None]] = None
    ):
        # Reject non-int types (bool, float, str) — bool must be checked before int (bool is subclass of int)
        if isinstance(total_items, bool) or not isinstance(total_items, int):
            raise TypeError(f"total_items must be int, got {type(total_items).__name__}")
        # Cycle 3 Fix P4a: reject negative total_items
        if total_items < 0:
            raise ValueError(f"total_items must be non-negative, got {total_items}")
        self.operation = operation
        self.total_items = total_items
        self.callback = callback

        self.start_time = time.time()
        self.current_stage = ""
        self.items_completed = 0
        self.current_item = ""
        self.errors: List[ErrorRecord] = []
        # RES-C5-4: bound warnings to prevent unbounded memory growth
        from collections import deque
        self.warnings: deque = deque(maxlen=100)
        self._stage_times: Dict[str, float] = {}

    def set_callback(self, callback: Callable[[ProgressReport], None]):
        """Set progress callback."""
        self.callback = callback

    def stage(self, stage_name: str) -> 'ProgressStageContext':
        """Enter a new stage (use as context manager)."""
        return ProgressStageContext(self, stage_name)

    def _enter_stage(self, stage_name: str):
        """Internal: enter a stage."""
        self.current_stage = stage_name
        self._stage_times[stage_name] = time.time()
        self._report()

    def _exit_stage(self, stage_name: str):
        """Internal: exit a stage."""
        if stage_name in self._stage_times:
            duration = time.time() - self._stage_times[stage_name]
            import logging as _logging
            _logging.getLogger(__name__).debug("Stage '%s' completed in %.1fs", stage_name, duration)

    def item_complete(self, item_name: str):
        """Mark an item as complete."""
        if self.total_items == 0:
            return  # Nothing to track — guard against unbounded increment when total=0
        self.items_completed = min(self.items_completed + 1, self.total_items)
        self.current_item = item_name
        self._report()

    def record_error(self, error: ErrorRecord):
        """Record an error."""
        self.errors.append(error)
        self._report()

    def add_warning(self, warning: str):
        """Add a warning."""
        self.warnings.append(warning)

    def _report(self):
        """Generate and send progress report."""
        if not self.callback:
            return

        elapsed = time.time() - self.start_time
        progress = (self.items_completed / self.total_items * 100) if self.total_items > 0 else 0

        # Estimate remaining time
        eta = None
        if self.items_completed > 0 and self.total_items > 0:
            rate = elapsed / self.items_completed
            remaining_items = self.total_items - self.items_completed
            eta = rate * remaining_items

        report = ProgressReport(
            operation=self.operation,
            stage=self.current_stage,
            progress_percent=progress,
            items_completed=self.items_completed,
            items_total=self.total_items,
            elapsed_seconds=elapsed,
            estimated_remaining_seconds=eta,
            current_item=self.current_item,
            errors_count=len(self.errors),
            warnings=list(self.warnings)[-5:]  # Last 5 warnings (deque doesn't support slicing)
        )

        self.callback(report)

    def get_summary(self) -> Dict[str, Any]:
        """Get final summary."""
        return {
            "operation": self.operation,
            "total_time_seconds": time.time() - self.start_time,
            "items_completed": self.items_completed,
            "items_total": self.total_items,
            "errors": [
                {
                    "type": e.error_type.value,
                    "message": e.message,
                    "recovered": e.recovered
                }
                for e in self.errors
            ],
            "warnings": list(self.warnings)
        }


class ProgressStageContext:
    """Context manager for progress stages."""

    def __init__(self, tracker: ProgressTracker, stage_name: str):
        self.tracker = tracker
        self.stage_name = stage_name

    def __enter__(self):
        self.tracker._enter_stage(self.stage_name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.tracker._exit_stage(self.stage_name)
        return False


T = TypeVar('T')


def with_retry(
    config: Optional[RetryConfig] = None,
    error_types: Optional[List[ErrorType]] = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for retrying functions with exponential backoff.

    Usage:
        @with_retry(RetryConfig(max_retries=3))
        def make_api_call():
            ...
    """
    # C3-1a: validate config is a RetryConfig before using it
    # Allow callables as config (legacy usage: with_retry(func, ...)) — treat as config=None
    if config is not None and callable(config) and not isinstance(config, RetryConfig):
        config = None
    elif config is not None and not isinstance(config, RetryConfig):
        raise TypeError(f"config must be a RetryConfig instance, got {type(config).__name__}: {config!r}")
    # C3-1b: reject explicitly-empty error_types list
    if error_types is not None and not isinstance(error_types, list):
        raise TypeError(f"error_types must be a list, got {type(error_types).__name__}")
    if isinstance(error_types, list) and len(error_types) == 0:
        raise ValueError("error_types must not be empty; pass None to use defaults")
    config = config or RetryConfig()
    error_types = error_types or [ErrorType.TIMEOUT, ErrorType.RATE_LIMIT, ErrorType.NETWORK]

    # Validate error_types elements are ErrorType enum members (not strings like "timeout")
    if error_types is not None:
        for et in error_types:
            if not isinstance(et, ErrorType):
                raise TypeError(f"error_types elements must be ErrorType, got {type(et).__name__}: {et!r}")

    # R1: reject negative max_retries to prevent 'raise None' TypeError
    if config.max_retries < 0:
        raise ValueError(f"max_retries must be non-negative, got {config.max_retries}")
    # Cycle 2 4c: reject negative/NaN/inf initial_delay
    if not isinstance(config.initial_delay, (int, float)) or not math.isfinite(config.initial_delay) or config.initial_delay < 0:
        raise ValueError(f"initial_delay must be a non-negative finite number, got {config.initial_delay!r}")
    # Cycle 2 4b: snapshot config to prevent post-decoration mutation bypass
    from dataclasses import replace as _dc_replace
    config = _dc_replace(config)

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None
            error_type = ErrorType.UNKNOWN

            for attempt in range(config.max_retries + 1):
                # Cycle 2 Fix (stale last_error): reset per-iteration so a
                # prior attempt's exception cannot leak into a later iteration's
                # re-raise if str(e) raises on the current exception.
                # Invariant: every except branch below MUST assign last_error
                # before any `raise` that exits the loop body, so last_error
                # is always non-None when execution falls through to line 371.
                # The guard at line 371 is defense-in-depth for future changes.
                last_error = None
                error_type = ErrorType.UNKNOWN
                try:
                    return func(*args, **kwargs)

                except TimeoutError as e:
                    last_error = e
                    error_type = ErrorType.TIMEOUT
                except ConnectionError as e:
                    last_error = e
                    error_type = ErrorType.NETWORK
                except Exception as e:
                    # RES-C5-1: programming errors should fail fast, never retry
                    if isinstance(e, (AttributeError, TypeError, NameError, KeyError, IndexError)):
                        raise
                    # Set last_error BEFORE str(e) — if __str__ raises, last_error is still assigned
                    last_error = e
                    # R2: check for rate limiting with contiguous phrase match
                    # to avoid false positives on normal messages containing both words
                    import re as _re
                    try:
                        error_str = str(e).lower()
                    except Exception:
                        # Exception.__str__ raised — cannot classify, re-raise original
                        raise e
                    if _re.search(r'\brate[_\s-]?limit', error_str):
                        error_type = ErrorType.RATE_LIMIT
                    elif "timeout" in error_str:
                        error_type = ErrorType.TIMEOUT
                    elif _re.search(r'connection\s*(reset|refused|timed?\s*out|closed|error|abort)', error_str) or "network" in error_str:
                        error_type = ErrorType.NETWORK
                    else:
                        # Unknown error, don't retry
                        raise

                if error_type not in error_types:
                    # Cycle 3 Fix (#4): guard against last_error being None,
                    # which would cause "TypeError: exceptions must derive from BaseException".
                    if last_error is None:
                        raise RuntimeError(
                            "with_retry: error_type is set but last_error is None (internal error)"
                        )
                    raise last_error

                if attempt < config.max_retries:
                    # Calculate delay with exponential backoff and jitter
                    delay = min(
                        config.initial_delay * (config.exponential_base ** attempt),
                        config.max_delay
                    )
                    effective_jitter = min(config.jitter, 0.9)  # cap at 90% to preserve minimum delay
                    jitter_range = delay * effective_jitter
                    import random
                    delay += random.uniform(-jitter_range, jitter_range)
                    # RES-1: clamp to non-negative; also re-apply max_delay cap AFTER jitter
                    # so the jitter upside never exceeds the configured maximum.
                    delay = max(0, min(config.max_delay, delay))

                    logger.warning(
                        "Retry %d/%d after %.1fs (%s)",
                        attempt + 1, config.max_retries, delay, error_type.value,
                    )
                    time.sleep(delay)

            if last_error is None:
                raise RuntimeError("with_retry: last_error is None at end of retry loop (internal error)")
            raise last_error

        return wrapper
    return decorator


class ResilientRunner:
    """
    Wrapper for running adversarial tests with resilience features.

    Features:
    - Automatic retry on transient failures
    - Rate limit detection and waiting
    - Large codebase chunking
    - Progress reporting
    - Graceful degradation

    Usage:
        resilient = ResilientRunner(progress_callback=print)

        result = resilient.run_with_resilience(
            func=lambda: runner.run_full_suite(...),
            component="red_team"
        )
    """

    def __init__(
        self,
        retry_config: Optional[RetryConfig] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
        max_code_size: int = 50000  # Max chars before chunking
    ):
        self.retry_config = retry_config or RetryConfig()
        self.progress_callback = progress_callback
        self.max_code_size = max_code_size
        # RES-10: bound error_log to prevent unbounded memory growth
        from collections import deque
        self.error_log: deque = deque(maxlen=1000)

    def _log_progress(self, message: str):
        """Log progress if callback is set."""
        if self.progress_callback:
            self.progress_callback(message)

    def run_with_resilience(
        self,
        func: Callable[[], T],
        component: str,
        timeout: Optional[float] = None,
        error_types: Optional[List[ErrorType]] = None
    ) -> Optional[T]:
        """
        Run a function with resilience features.

        Returns None if all retries fail (graceful degradation).
        """
        # C3-1c: validate func is callable before any other checks
        if not callable(func):
            raise TypeError(f"operation must be callable, got {type(func).__name__}: {func!r}")
        # RES-3: reject bool timeout (bool is subclass of int, passes isinstance check)
        # Cycle 2 4d: reject NaN/inf/negative timeout (math.ceil(NaN) crashes, negative is nonsensical)
        if timeout is not None and isinstance(timeout, bool):
            raise ValueError(f"timeout must be a non-negative finite number, got {timeout!r}")
        # Iter 3 Fix H6: also reject timeout=0 (zero timeout is useless and causes issues)
        if timeout is not None and (not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError(f"timeout must be a positive finite number, got {timeout!r}")

        # Red-team fix: validate error_types parity with with_retry()
        if error_types is not None:
            if not isinstance(error_types, list):
                raise TypeError(f"error_types must be a list, got {type(error_types).__name__}")
            if len(error_types) == 0:
                raise ValueError("error_types must not be empty; pass None to use defaults")
            for et in error_types:
                if not isinstance(et, ErrorType):
                    raise TypeError(f"error_types elements must be ErrorType, got {type(et).__name__}: {et!r}")
        _error_types = error_types if error_types is not None else [ErrorType.TIMEOUT, ErrorType.RATE_LIMIT, ErrorType.NETWORK]

        last_error = None
        # Cycle 5 Fix: create executor once outside the retry loop to prevent thread
        # accumulation. Each retry previously created a new executor and called
        # shutdown(wait=False), leaving up to max_retries orphaned threads alive.
        import threading as _threading
        _non_main_executor = None
        try:
            for attempt in range(self.retry_config.max_retries + 1):
                try:
                    if timeout is not None and timeout > 0:
                        import signal

                        # Bug 16: SIGALRM only works from main thread; use thread-based
                        # timeout as fallback when called from non-main threads
                        if _threading.current_thread() is _threading.main_thread():
                            def timeout_handler(signum, frame):
                                raise TimeoutError(f"Operation timed out after {timeout}s")

                            # Set the signal handler
                            old_handler = signal.signal(signal.SIGALRM, timeout_handler)
                            signal.alarm(min(max(1, int(math.ceil(timeout))), 2**31 - 1))

                            try:
                                result = func()
                            finally:
                                signal.alarm(0)
                                signal.signal(signal.SIGALRM, old_handler)
                        else:
                            # Thread-safe fallback: reuse single executor across retries
                            if _non_main_executor is None:
                                _non_main_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
                            try:
                                _fut = _non_main_executor.submit(func)
                                result = _fut.result(timeout=timeout)
                            except concurrent.futures.TimeoutError:
                                raise TimeoutError(f"Operation timed out after {timeout}s")
                    else:
                        result = func()

                    return result

                except TimeoutError as e:
                    last_error = e
                    error_type = ErrorType.TIMEOUT
                    self._log_progress(f"Timeout in {component}: {e}")
                    if ErrorType.TIMEOUT not in _error_types:
                        raise

                except (MemoryError, SystemExit, KeyboardInterrupt):
                    # RES-C3-2: never retry fatal exceptions — propagate immediately
                    raise

                except (AttributeError, TypeError, ValueError, NameError, KeyError, IndexError):
                    # R3: programming errors — fail fast, don't retry
                    raise

                except Exception as e:
                    import re as _re
                    last_error = e  # Set before str(e) — guards against str(e) raising
                    try:
                        error_str = str(e).lower()
                    except Exception:
                        raise e  # str(e) raised — cannot classify, re-raise original

                    # R2: use contiguous phrase match for rate limit detection
                    if _re.search(r'\brate[_\s-]?limit', error_str):
                        error_type = ErrorType.RATE_LIMIT
                        if ErrorType.RATE_LIMIT not in _error_types:
                            raise e
                        self._log_progress(f"Rate limited in {component}, waiting...")
                        last_error = e
                        self.error_log.append(ErrorRecord(
                            error_type=error_type,
                            message=str(e),
                            timestamp=datetime.now().isoformat(),
                            retry_count=attempt,
                            recovered=False,
                            component=component
                        ))
                        if attempt < self.retry_config.max_retries:
                            # Cycle 2 Fix (rate-limit flat sleep): use exponential backoff
                            # instead of flat min(60, max_delay) to match normal retry path.
                            _rl_delay = min(
                                self.retry_config.initial_delay * (self.retry_config.exponential_base ** attempt),
                                self.retry_config.max_delay
                            )
                            import random as _random
                            # Cap jitter at 0.9 (same guard as with_retry) to prevent zero delay.
                            _rl_effective_jitter = min(self.retry_config.jitter, 0.9)
                            _rl_jitter = 1.0 + _random.uniform(
                                -_rl_effective_jitter, _rl_effective_jitter
                            )
                            # C2-4: apply max_delay cap AFTER jitter — without this the
                            # jitter multiplier can push the delay above max_delay.
                            _rl_delay = max(0.0, min(self.retry_config.max_delay, _rl_delay * _rl_jitter))
                            time.sleep(_rl_delay)
                        continue
                    elif "timeout" in error_str:
                        error_type = ErrorType.TIMEOUT
                    elif _re.search(r'connection\s*(reset|refused|timed?\s*out|closed|error|abort)', error_str) or "network" in error_str:
                        error_type = ErrorType.NETWORK
                    else:
                        # RES-9: unknown/unexpected exceptions — fail fast, don't retry
                        raise

                    last_error = e
                    self._log_progress(f"Error in {component}: {error_type.value} - {e}")
                    if error_type not in _error_types:
                        raise last_error

                # Record error
                self.error_log.append(ErrorRecord(
                    error_type=error_type,
                    message=str(last_error),
                    timestamp=datetime.now().isoformat(),
                    retry_count=attempt,
                    recovered=False,
                    component=component
                ))

                if attempt < self.retry_config.max_retries:
                    delay = min(
                        self.retry_config.initial_delay * (self.retry_config.exponential_base ** attempt),
                        self.retry_config.max_delay
                    )
                    # Use configured jitter to prevent thundering herd on retry storms
                    import random as _random
                    # Cap jitter at 0.9 (same as with_retry) so delay can never reach 0;
                    # also apply max_delay cap AFTER jitter multiplication.
                    _effective_jitter = min(self.retry_config.jitter, 0.9)
                    jitter_factor = 1.0 + _random.uniform(-_effective_jitter, _effective_jitter)
                    delay = max(0, min(self.retry_config.max_delay, delay * jitter_factor))
                    self._log_progress(f"Retrying {component} in {delay:.1f}s (attempt {attempt + 2})")
                    time.sleep(delay)

            # All retries failed - graceful degradation
            self._log_progress(f"All retries failed for {component}, skipping...")
            if self.error_log:
                self.error_log[-1].recovered = False

            return None
        finally:
            if _non_main_executor is not None:
                _non_main_executor.shutdown(wait=False, cancel_futures=True)

    def chunk_large_code(self, code: str, chunk_size: Optional[int] = None) -> List[str]:
        """
        Split large code into manageable chunks.

        Tries to split on function/class boundaries.

        Note: When using SIGALRM-based timeouts elsewhere, the OS enforces a
        minimum granularity of 1 second. Sub-second values are rounded up via
        max(1, int(math.ceil(timeout))).
        """
        # Cycle 2 Fix 3.3: guard against None code
        # Iter 4 Fix B6: reject non-string types (bytes, int, etc.)
        if not isinstance(code, str):
            raise TypeError(f"code must be a string, got {type(code).__name__}")
        # Cycle 2 Fix 3.1: reject bool (bool is subclass of int) and float
        chunk_size = self.max_code_size if chunk_size is None else chunk_size
        if isinstance(chunk_size, bool):
            raise TypeError(f"chunk_size must be int, got bool: {chunk_size!r}")
        if isinstance(chunk_size, float):
            raise TypeError(f"chunk_size must be int, got float: {chunk_size!r}")
        if not isinstance(chunk_size, int):
            raise TypeError(f"chunk_size must be int, got {type(chunk_size).__name__}")
        if chunk_size <= 0:
            raise ValueError(f"chunk_size must be positive, got {chunk_size}")

        if len(code) <= chunk_size:
            return [code]

        chunks = []
        lines = code.split('\n')
        current_chunk = []
        current_size = 0

        for line in lines:
            # Try to split on function/class definitions
            is_boundary = (
                line.startswith('def ') or
                line.startswith('class ') or
                line.startswith('async def ')
            )

            if is_boundary and current_size > chunk_size // 2:
                # Start a new chunk at this boundary
                # Iter 3 Fix L1: don't append empty chunks
                if current_chunk:
                    chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_size = len(line) + 1  # +1 for trailing newline
            else:
                # Pre-flush: if adding this line would exceed chunk_size and we
                # already have content, flush first so multi-line chunks never
                # exceed chunk_size.  Single lines larger than chunk_size are
                # unavoidable and land as solo oversized chunks.
                if current_chunk and current_size + len(line) + 1 > chunk_size:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = []
                    current_size = 0
                # Single lines longer than chunk_size cannot be split without
                # breaking syntax, so we accept the invariant violation. Long lines
                # are unusual (generated code, minified output) and callers must
                # handle oversized chunks gracefully.
                current_chunk.append(line)
                current_size += len(line) + 1

        if current_chunk:
            chunks.append('\n'.join(current_chunk))

        return chunks

    def get_error_summary(self) -> Dict[str, Any]:
        """Get summary of all errors encountered."""
        by_type = {}
        for error in self.error_log:
            type_name = error.error_type.value
            if type_name not in by_type:
                by_type[type_name] = 0
            by_type[type_name] += 1

        recovered = sum(1 for e in self.error_log if e.recovered)

        return {
            "total_errors": len(self.error_log),
            "recovered": recovered,
            "unrecovered": len(self.error_log) - recovered,
            "by_type": by_type,
            "errors": [
                {
                    "type": e.error_type.value,
                    "message": e.message[:100],
                    "component": e.component,
                    "recovered": e.recovered
                }
                for e in list(self.error_log)[-10:]  # Last 10 errors (deque doesn't support slicing)
            ]
        }


def detect_error_type(exception: Exception) -> ErrorType:
    """Detect the type of error from an exception."""
    try:
        error_str = str(exception).lower()
    except Exception:
        error_str = ""  # str() raised — treat as unknown
    exc_type = type(exception).__name__.lower()

    if isinstance(exception, TimeoutError) or "timeout" in error_str:
        return ErrorType.TIMEOUT
    # Cycle 2 R2-sibling: use contiguous phrase match (same regex as with_retry/run_with_resilience)
    elif __import__('re').search(r'\brate[_\s-]?limit', error_str):
        return ErrorType.RATE_LIMIT
    # RES-C5-3: filesystem OSError subclasses are SYSTEM errors, not NETWORK
    elif isinstance(exception, (FileNotFoundError, FileExistsError, IsADirectoryError,
                                NotADirectoryError, PermissionError)):
        return ErrorType.UNKNOWN
    # Iter 3 Fix M8: check ConnectionError separately from OSError to avoid
    # misclassifying non-network OSError subclasses (BlockingIOError, etc.) as NETWORK
    elif isinstance(exception, ConnectionError) or __import__('re').search(r'connection\s*(reset|refused|timed?\s*out|closed|error|abort)', error_str) or "network" in error_str:
        return ErrorType.NETWORK
    elif "parse" in error_str or "json" in exc_type or "decode" in error_str:
        return ErrorType.PARSE_ERROR
    # Cycle 3 Fix P4b: narrow 'size' match to avoid false positives on 'resize', 'font_size', etc.
    elif "too large" in error_str or __import__('re').search(r'\b(file|message|payload|content|request|response|data|body|buffer|object|maximum)\s+size\b|size\s+(limit|exceed|too)', error_str):
        return ErrorType.CODE_TOO_LARGE
    elif "api" in error_str or "http" in error_str:
        return ErrorType.API_ERROR
    else:
        return ErrorType.UNKNOWN


if __name__ == "__main__":
    # Self-test
    print("Resilience Module - Self Test")
    print("=" * 50)

    # Test retry decorator
    print("\n1. Testing retry decorator...")
    call_count = 0

    @with_retry(RetryConfig(max_retries=2, initial_delay=0.1))
    def flaky_function():
        global call_count
        call_count += 1
        if call_count < 3:
            raise TimeoutError("Simulated timeout")
        return "success"

    result = flaky_function()
    print(f"   Result: {result} (took {call_count} attempts)")
    assert result == "success"
    assert call_count == 3

    # Test progress tracker
    print("\n2. Testing progress tracker...")
    progress_messages = []

    def progress_callback(report: ProgressReport):
        progress_messages.append(report.stage)

    tracker = ProgressTracker(
        "Test Operation",
        total_items=3,
        callback=progress_callback
    )

    with tracker.stage("Stage 1"):
        tracker.item_complete("item 1")
    with tracker.stage("Stage 2"):
        tracker.item_complete("item 2")
        tracker.item_complete("item 3")

    summary = tracker.get_summary()
    print(f"   Items completed: {summary['items_completed']}")
    print(f"   Stages tracked: {set(progress_messages)}")

    # Test resilient runner
    print("\n3. Testing resilient runner...")
    resilient = ResilientRunner(
        retry_config=RetryConfig(max_retries=1, initial_delay=0.1),
        progress_callback=lambda msg: print(f"      {msg}")
    )

    # Test with failing function
    fail_result = resilient.run_with_resilience(
        func=lambda: (_ for _ in ()).throw(ConnectionError("connection reset")),
        component="test_component"
    )
    print(f"   Graceful degradation: {fail_result is None}")

    error_summary = resilient.get_error_summary()
    print(f"   Errors logged: {error_summary['total_errors']}")

    # Test code chunking
    print("\n4. Testing code chunking...")
    large_code = "\n".join([
        "def function_1():\n    pass\n",
        "def function_2():\n    pass\n",
        "class MyClass:\n    pass\n",
    ] * 100)

    chunks = resilient.chunk_large_code(large_code, chunk_size=500)
    print(f"   Large code ({len(large_code)} chars) split into {len(chunks)} chunks")

    print("\nResilience module self-test complete!")
