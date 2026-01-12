"""
Resilience Module - Error handling, retries, and graceful degradation.

This module ensures adversarial testing doesn't fail due to transient issues:
1. API timeouts with exponential backoff retry
2. Rate limiting detection and waiting
3. Network failure recovery
4. Large codebase chunking
5. Progress reporting for long-running operations
"""

import time
import sys
import functools
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable, TypeVar, Optional, List, Any, Dict
from datetime import datetime
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))


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
        self.operation = operation
        self.total_items = total_items
        self.callback = callback

        self.start_time = time.time()
        self.current_stage = ""
        self.items_completed = 0
        self.current_item = ""
        self.errors: List[ErrorRecord] = []
        self.warnings: List[str] = []
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
            self.warnings.append(f"Stage '{stage_name}' completed in {duration:.1f}s")

    def item_complete(self, item_name: str):
        """Mark an item as complete."""
        self.items_completed += 1
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
            warnings=self.warnings[-5:]  # Last 5 warnings
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
            "warnings": self.warnings
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
    config = config or RetryConfig()
    error_types = error_types or [ErrorType.TIMEOUT, ErrorType.RATE_LIMIT, ErrorType.NETWORK]

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            last_error = None

            for attempt in range(config.max_retries + 1):
                try:
                    return func(*args, **kwargs)

                except TimeoutError as e:
                    last_error = e
                    error_type = ErrorType.TIMEOUT
                except ConnectionError as e:
                    last_error = e
                    error_type = ErrorType.NETWORK
                except Exception as e:
                    # Check for rate limiting
                    error_str = str(e).lower()
                    if "rate" in error_str and "limit" in error_str:
                        last_error = e
                        error_type = ErrorType.RATE_LIMIT
                    elif "timeout" in error_str:
                        last_error = e
                        error_type = ErrorType.TIMEOUT
                    elif "connection" in error_str or "network" in error_str:
                        last_error = e
                        error_type = ErrorType.NETWORK
                    else:
                        # Unknown error, don't retry
                        raise

                if error_type not in error_types:
                    raise last_error

                if attempt < config.max_retries:
                    # Calculate delay with exponential backoff and jitter
                    delay = min(
                        config.initial_delay * (config.exponential_base ** attempt),
                        config.max_delay
                    )
                    jitter_range = delay * config.jitter
                    import random
                    delay += random.uniform(-jitter_range, jitter_range)

                    print(f"Retry {attempt + 1}/{config.max_retries} after {delay:.1f}s ({error_type.value})")
                    time.sleep(delay)

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
        self.error_log: List[ErrorRecord] = []

    def _log_progress(self, message: str):
        """Log progress if callback is set."""
        if self.progress_callback:
            self.progress_callback(message)

    def run_with_resilience(
        self,
        func: Callable[[], T],
        component: str,
        timeout: Optional[float] = None
    ) -> Optional[T]:
        """
        Run a function with resilience features.

        Returns None if all retries fail (graceful degradation).
        """
        last_error = None

        for attempt in range(self.retry_config.max_retries + 1):
            try:
                if timeout:
                    import signal

                    def timeout_handler(signum, frame):
                        raise TimeoutError(f"Operation timed out after {timeout}s")

                    # Set the signal handler
                    old_handler = signal.signal(signal.SIGALRM, timeout_handler)
                    signal.alarm(int(timeout))

                    try:
                        result = func()
                    finally:
                        signal.alarm(0)
                        signal.signal(signal.SIGALRM, old_handler)
                else:
                    result = func()

                return result

            except TimeoutError as e:
                last_error = e
                error_type = ErrorType.TIMEOUT
                self._log_progress(f"Timeout in {component}: {e}")

            except Exception as e:
                error_str = str(e).lower()

                if "rate" in error_str and "limit" in error_str:
                    error_type = ErrorType.RATE_LIMIT
                    self._log_progress(f"Rate limited in {component}, waiting...")
                    # Wait longer for rate limits
                    time.sleep(60)
                elif "timeout" in error_str:
                    error_type = ErrorType.TIMEOUT
                elif "connection" in error_str or "network" in error_str:
                    error_type = ErrorType.NETWORK
                else:
                    error_type = ErrorType.API_ERROR

                last_error = e
                self._log_progress(f"Error in {component}: {error_type.value} - {e}")

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
                self._log_progress(f"Retrying {component} in {delay:.1f}s (attempt {attempt + 2})")
                time.sleep(delay)

        # All retries failed - graceful degradation
        self._log_progress(f"All retries failed for {component}, skipping...")
        if self.error_log:
            self.error_log[-1].recovered = False

        return None

    def chunk_large_code(self, code: str, chunk_size: Optional[int] = None) -> List[str]:
        """
        Split large code into manageable chunks.

        Tries to split on function/class boundaries.
        """
        chunk_size = chunk_size or self.max_code_size

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
                chunks.append('\n'.join(current_chunk))
                current_chunk = [line]
                current_size = len(line)
            else:
                current_chunk.append(line)
                current_size += len(line) + 1

                # Force split if chunk is too large
                if current_size >= chunk_size:
                    chunks.append('\n'.join(current_chunk))
                    current_chunk = []
                    current_size = 0

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
                for e in self.error_log[-10:]  # Last 10 errors
            ]
        }


def detect_error_type(exception: Exception) -> ErrorType:
    """Detect the type of error from an exception."""
    error_str = str(exception).lower()
    exc_type = type(exception).__name__.lower()

    if isinstance(exception, TimeoutError) or "timeout" in error_str:
        return ErrorType.TIMEOUT
    elif "rate" in error_str and "limit" in error_str:
        return ErrorType.RATE_LIMIT
    elif isinstance(exception, (ConnectionError, OSError)) or "connection" in error_str or "network" in error_str:
        return ErrorType.NETWORK
    elif "parse" in error_str or "json" in exc_type or "decode" in error_str:
        return ErrorType.PARSE_ERROR
    elif "too large" in error_str or "size" in error_str:
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
        func=lambda: (_ for _ in ()).throw(ConnectionError("test")),
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
