#!/usr/bin/env python3
"""
Real-Time Token Ingestion Watcher

Monitors Claude's live transcript files (~/.claude/projects/) during mission
execution and immediately records token events to the database. This provides
true real-time cost visibility in the dashboard instead of showing 0 tokens
until the mission completes.

Architecture:
  - Uses watchdog library with inotify backend (Linux native, efficient)
  - Falls back to polling if watchdog unavailable
  - Tracks file offsets for incremental JSONL reading (no re-parsing)
  - Records via mission_analytics.record_token_usage()
  - Pushes updates via existing SocketIO broadcast

Usage:
    from realtime_token_watcher import get_token_watcher

    # Start watching for a mission
    watcher = get_token_watcher()
    watcher.start(
        mission_id="mission_abc123",
        workspace_path="/path/to/atlasforge/missions/mission_abc123/workspace"
    )

    # Stop when mission completes
    watcher.stop()
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Set, Callable

from atlasforge_config import ANALYTICS_DIR

logger = logging.getLogger(__name__)

# Try to import watchdog, fall back to polling if not available
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
    HAS_WATCHDOG = True
except ImportError:
    HAS_WATCHDOG = False
    Observer = None
    FileSystemEventHandler = object
    FileModifiedEvent = None

# Claude projects directory
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Feature flag for easy disable
REALTIME_TOKEN_WATCHER_ENABLED = os.environ.get(
    "REALTIME_TOKEN_WATCHER_ENABLED", "1"
).lower() in ("1", "true", "yes")


class TranscriptFileTracker:
    """
    Tracks file offsets for incremental JSONL reading.

    Maintains the last read position for each file so we only
    process new entries, not re-parse the entire file.
    """

    def __init__(self, max_seen_ids: int = 10000):
        """
        Initialize the tracker.

        Args:
            max_seen_ids: Maximum number of request IDs to remember
                          for deduplication (older ones are dropped)
        """
        self._file_offsets: Dict[str, int] = {}
        self._seen_request_ids: Set[str] = set()
        self._max_seen_ids = max_seen_ids
        self._lock = threading.Lock()

    def reset(self):
        """Reset all tracking state (call when mission changes)."""
        with self._lock:
            self._file_offsets.clear()
            self._seen_request_ids.clear()

    def preload_seen_ids_from_db(self, mission_id: str):
        """
        Pre-populate seen request IDs from database on watcher startup.

        This avoids unnecessary INSERT attempts for events already recorded
        in a previous watcher session.

        Args:
            mission_id: Mission identifier to query
        """
        try:
            import sqlite3
            from pathlib import Path

            db_path = ANALYTICS_DIR / "mission_analytics.db"
            if not db_path.exists():
                return

            conn = sqlite3.connect(db_path)
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT request_id FROM token_events
                    WHERE mission_id = ? AND request_id IS NOT NULL AND request_id != ''
                """, (mission_id,))

                with self._lock:
                    for row in cursor.fetchall():
                        self._seen_request_ids.add(row[0])

                    loaded_count = len(self._seen_request_ids)
                    if loaded_count > 0:
                        logger.info(f"Preloaded {loaded_count} seen request IDs for mission {mission_id}")
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"Error preloading seen IDs: {e}")

    def get_new_entries(self, file_path: str):
        """
        Read and yield new JSONL entries from a file.

        Args:
            file_path: Path to the JSONL transcript file

        Yields:
            Parsed JSON records that haven't been seen before
        """
        path = str(file_path)

        with self._lock:
            offset = self._file_offsets.get(path, 0)

        try:
            with open(path, 'r', encoding='utf-8') as f:
                # Seek to last known position
                f.seek(offset)

                new_entries = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                        new_entries.append(record)
                    except json.JSONDecodeError as e:
                        logger.debug(f"Skipping malformed JSON line: {e}")
                        continue

                # Update offset
                new_offset = f.tell()
                with self._lock:
                    self._file_offsets[path] = new_offset

                for entry in new_entries:
                    yield entry

        except UnicodeDecodeError as e:
            # Handle binary files or files with invalid UTF-8
            logger.debug(f"Skipping file with encoding error {path}: {e}")
        except (IOError, OSError) as e:
            logger.debug(f"Error reading transcript file {path}: {e}")

    def is_seen(self, request_id: str) -> bool:
        """Check if a request ID has been seen (for deduplication)."""
        if not request_id:
            return True  # Treat missing ID as duplicate (skip it)

        with self._lock:
            if request_id in self._seen_request_ids:
                return True

            # Add to seen set
            self._seen_request_ids.add(request_id)

            # Prune if too large (drop oldest half)
            if len(self._seen_request_ids) > self._max_seen_ids:
                # Sets don't maintain order, so just clear half randomly
                to_keep = list(self._seen_request_ids)[-self._max_seen_ids // 2:]
                self._seen_request_ids = set(to_keep)

            return False


class TranscriptEventHandler(FileSystemEventHandler):
    """
    Watchdog event handler for transcript file modifications.

    Only processes .jsonl files in the Claude projects directory.
    """

    def __init__(self, callback: Callable[[str], None]):
        """
        Initialize the handler.

        Args:
            callback: Function to call with file path when modified
        """
        super().__init__()
        self._callback = callback

    def on_modified(self, event):
        """Handle file modification events."""
        if event.is_directory:
            return

        src_path = event.src_path
        if src_path.endswith('.jsonl'):
            self._callback(src_path)


class RealTimeTokenWatcher:
    """
    Watches Claude transcript files and ingests tokens in real-time.

    Provides real-time cost visibility during active missions by:
    - Monitoring ~/.claude/projects/ for transcript updates
    - Parsing new JSONL entries as they're written
    - Recording token usage to the analytics database
    - Pushing updates to the dashboard via SocketIO
    """

    def __init__(self):
        """Initialize the watcher (not yet watching)."""
        self._mission_id: Optional[str] = None
        self._workspace_path: Optional[str] = None
        self._transcript_dir: Optional[Path] = None
        self._socketio = None
        self._tracker = TranscriptFileTracker()

        # Watchdog observer (if available)
        self._observer: Optional[Observer] = None

        # Polling fallback
        self._polling_thread: Optional[threading.Thread] = None
        self._stop_polling = threading.Event()

        self._running = False
        self._lock = threading.Lock()

        # Track current stage
        self._current_stage = "unknown"

        # Stats
        self._events_recorded = 0
        self._last_update: Optional[datetime] = None

    def _find_transcript_dir(self, workspace_path: str) -> Optional[Path]:
        """
        Find the Claude transcript directory for a workspace.

        Claude stores transcripts in ~/.claude/projects/-{path-with-dashes}
        where slashes and underscores become dashes.

        Args:
            workspace_path: Path to the mission workspace

        Returns:
            Path to transcript directory, or None if not found
        """
        # Convert workspace path to Claude's format
        # /path/to/atlasforge/missions/mission_xxx/workspace
        # -> -path-to-atlasforge-missions-mission-xxx-workspace
        escaped = workspace_path.replace('/', '-').replace('_', '-')

        transcript_dir = CLAUDE_PROJECTS_DIR / escaped

        if transcript_dir.exists():
            logger.info(f"Found transcript dir: {transcript_dir}")
            return transcript_dir

        # Fallback: search for partial match (in case of path variations)
        if CLAUDE_PROJECTS_DIR.exists():
            # Extract mission ID pattern
            parts = workspace_path.split('/')
            mission_part = None
            for part in parts:
                if part.startswith('mission'):
                    mission_part = part.replace('_', '-')
                    break

            if mission_part:
                for d in CLAUDE_PROJECTS_DIR.iterdir():
                    if d.is_dir() and mission_part in d.name and 'workspace' in d.name:
                        logger.info(f"Found transcript dir via search: {d}")
                        return d

        logger.warning(f"No transcript directory found for workspace: {workspace_path}")
        return None

    def start(self, mission_id: str, workspace_path: str,
              socketio=None, stage: str = "unknown") -> bool:
        """
        Start watching for the specified mission.

        Args:
            mission_id: Mission identifier
            workspace_path: Path to mission workspace
            socketio: Optional Flask-SocketIO instance for push updates
            stage: Current mission stage

        Returns:
            True if watching started successfully
        """
        if not REALTIME_TOKEN_WATCHER_ENABLED:
            logger.info("Real-time token watcher disabled via env var")
            return False

        with self._lock:
            # Stop any existing watch
            self._stop_internal()

            self._mission_id = mission_id
            self._workspace_path = workspace_path
            self._socketio = socketio
            self._current_stage = stage
            self._tracker.reset()
            self._events_recorded = 0

            # Pre-populate seen IDs from database to avoid re-recording
            # events from previous watcher sessions
            self._tracker.preload_seen_ids_from_db(mission_id)

            # Find transcript directory
            self._transcript_dir = self._find_transcript_dir(workspace_path)

            if not self._transcript_dir:
                logger.warning(f"Cannot watch: no transcript dir for {mission_id}")
                return False

            # Start watching
            if HAS_WATCHDOG:
                success = self._start_watchdog()
            else:
                success = self._start_polling()

            if success:
                self._running = True
                logger.info(f"Started real-time token watching for {mission_id}")

                # Do initial scan of existing files
                self._scan_existing_files()

            return success

    def _start_watchdog(self) -> bool:
        """Start watchdog-based file monitoring."""
        try:
            handler = TranscriptEventHandler(self._on_file_modified)
            self._observer = Observer()
            self._observer.schedule(handler, str(self._transcript_dir), recursive=False)
            self._observer.start()
            logger.info("Using watchdog (inotify) for file monitoring")
            return True
        except Exception as e:
            logger.error(f"Failed to start watchdog: {e}")
            return self._start_polling()  # Fallback

    def _start_polling(self) -> bool:
        """Start polling-based file monitoring (fallback)."""
        try:
            self._stop_polling.clear()
            self._polling_thread = threading.Thread(
                target=self._polling_loop,
                daemon=True,
                name="TokenWatcherPolling"
            )
            self._polling_thread.start()
            logger.info("Using polling for file monitoring (watchdog not available)")
            return True
        except Exception as e:
            logger.error(f"Failed to start polling: {e}")
            return False

    def _polling_loop(self):
        """Polling loop for file monitoring."""
        poll_interval = 2.0  # seconds

        # Track file sizes for change detection
        file_sizes: Dict[str, int] = {}

        while not self._stop_polling.is_set():
            try:
                if self._transcript_dir and self._transcript_dir.exists():
                    for jsonl_file in self._transcript_dir.glob("*.jsonl"):
                        path = str(jsonl_file)
                        current_size = jsonl_file.stat().st_size
                        prev_size = file_sizes.get(path, 0)

                        if current_size > prev_size:
                            # File grew, process new content
                            self._on_file_modified(path)
                            file_sizes[path] = current_size
                        elif path not in file_sizes:
                            file_sizes[path] = current_size

            except Exception as e:
                logger.debug(f"Polling error: {e}")

            self._stop_polling.wait(poll_interval)

    def _scan_existing_files(self):
        """Scan existing transcript files for any unrecorded entries."""
        if not self._transcript_dir:
            return

        try:
            for jsonl_file in self._transcript_dir.glob("*.jsonl"):
                self._on_file_modified(str(jsonl_file))
        except Exception as e:
            logger.debug(f"Error scanning existing files: {e}")

    def _on_file_modified(self, file_path: str):
        """
        Handle file modification event.

        Args:
            file_path: Path to the modified file
        """
        try:
            for record in self._tracker.get_new_entries(file_path):
                self._process_record(record)
        except Exception as e:
            logger.debug(f"Error processing file {file_path}: {e}")

    def _process_record(self, record: Dict[str, Any]):
        """
        Process a single JSONL record.

        Only processes 'assistant' type records with usage data.

        Args:
            record: Parsed JSON record from transcript
        """
        # Only process assistant responses
        if record.get('type') != 'assistant':
            return

        message = record.get('message', {})
        usage = message.get('usage', {})
        request_id = record.get('requestId')

        if not usage:
            return

        # Deduplication check
        if self._tracker.is_seen(request_id):
            return

        # Extract token counts
        input_tokens = usage.get('input_tokens', 0)
        output_tokens = usage.get('output_tokens', 0)
        cache_read = usage.get('cache_read_input_tokens', 0)
        cache_write = usage.get('cache_creation_input_tokens', 0)

        # Skip if no meaningful token data
        if input_tokens == 0 and output_tokens == 0 and cache_read == 0 and cache_write == 0:
            return

        model = message.get('model', 'unknown')

        # Record to database
        try:
            from mission_analytics import get_analytics
            analytics = get_analytics()
            analytics.record_token_usage(
                mission_id=self._mission_id,
                stage=self._current_stage,
                usage={
                    'input_tokens': input_tokens,
                    'output_tokens': output_tokens,
                    'cache_read_input_tokens': cache_read,
                    'cache_creation_input_tokens': cache_write
                },
                model=model,
                request_id=request_id
            )

            self._events_recorded += 1
            self._last_update = datetime.now()

            logger.debug(f"Recorded tokens: in={input_tokens}, out={output_tokens}, "
                        f"cache_read={cache_read}, cache_write={cache_write}")

            # Push update to dashboard
            self._push_update()

        except Exception as e:
            logger.error(f"Error recording token usage: {e}")

    def _push_update(self):
        """Push token update to connected dashboard clients via SocketIO."""
        if not self._socketio:
            return

        try:
            from mission_analytics import get_current_mission_analytics
            data = get_current_mission_analytics()

            self._socketio.emit('update', {
                'room': 'analytics',
                'data': data,
                'timestamp': datetime.now().isoformat(),
                'source': 'realtime_watcher'
            }, room='analytics', namespace='/widgets')

        except Exception as e:
            logger.debug(f"Error pushing SocketIO update: {e}")

    def update_stage(self, stage: str):
        """
        Update the current stage (for attributing tokens to correct stage).

        Args:
            stage: New stage name (e.g., 'BUILDING', 'TESTING')
        """
        self._current_stage = stage

    def stop(self):
        """Stop watching."""
        with self._lock:
            self._stop_internal()

    def _stop_internal(self):
        """Internal stop (assumes lock is held)."""
        self._running = False

        # Stop watchdog observer
        if self._observer:
            try:
                self._observer.stop()
                self._observer.join(timeout=2.0)
            except Exception:
                pass
            self._observer = None

        # Stop polling thread
        if self._polling_thread:
            self._stop_polling.set()
            try:
                self._polling_thread.join(timeout=2.0)
            except Exception:
                pass
            self._polling_thread = None

        if self._mission_id:
            logger.info(f"Stopped real-time token watching for {self._mission_id} "
                       f"({self._events_recorded} events recorded)")

        self._mission_id = None
        self._workspace_path = None
        self._transcript_dir = None

    def is_running(self) -> bool:
        """Check if watcher is currently running."""
        return self._running

    def get_stats(self) -> Dict[str, Any]:
        """Get watcher statistics."""
        return {
            'running': self._running,
            'mission_id': self._mission_id,
            'events_recorded': self._events_recorded,
            'last_update': self._last_update.isoformat() if self._last_update else None,
            'transcript_dir': str(self._transcript_dir) if self._transcript_dir else None,
            'using_watchdog': HAS_WATCHDOG and self._observer is not None,
            'enabled': REALTIME_TOKEN_WATCHER_ENABLED
        }


# Global singleton instance
_watcher_instance: Optional[RealTimeTokenWatcher] = None
_watcher_lock = threading.Lock()


def get_token_watcher() -> RealTimeTokenWatcher:
    """
    Get the global token watcher instance.

    Returns:
        The singleton RealTimeTokenWatcher instance
    """
    global _watcher_instance

    with _watcher_lock:
        if _watcher_instance is None:
            _watcher_instance = RealTimeTokenWatcher()
        return _watcher_instance


def start_watching_mission(mission_id: str, workspace_path: str,
                           socketio=None, stage: str = "unknown") -> bool:
    """
    Convenience function to start watching a mission.

    Args:
        mission_id: Mission identifier
        workspace_path: Path to mission workspace
        socketio: Optional Flask-SocketIO instance
        stage: Current mission stage

    Returns:
        True if watching started successfully
    """
    return get_token_watcher().start(
        mission_id=mission_id,
        workspace_path=workspace_path,
        socketio=socketio,
        stage=stage
    )


def stop_watching_mission():
    """Convenience function to stop watching."""
    get_token_watcher().stop()


def update_mission_stage(stage: str):
    """
    Convenience function to update the current stage.

    Args:
        stage: New stage name
    """
    get_token_watcher().update_stage(stage)


# =============================================================================
# MAIN (Self-Test)
# =============================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print("=" * 60)
    print("Real-Time Token Watcher - Self Test")
    print("=" * 60)

    print(f"\nWatchdog available: {HAS_WATCHDOG}")
    print(f"Watcher enabled: {REALTIME_TOKEN_WATCHER_ENABLED}")
    print(f"Claude projects dir: {CLAUDE_PROJECTS_DIR}")
    print(f"Projects dir exists: {CLAUDE_PROJECTS_DIR.exists()}")

    # Test 1: Create watcher
    print("\n[TEST 1] Creating watcher instance...")
    watcher = get_token_watcher()
    print(f"  Stats: {watcher.get_stats()}")

    # Test 2: Find transcript directory for current mission
    print("\n[TEST 2] Testing transcript directory lookup...")
    test_paths = [
        "/path/to/atlasforge/missions/mission_1b2fe87d/workspace",
        "/path/to/atlasforge/workspace",
    ]

    for path in test_paths:
        result = watcher._find_transcript_dir(path)
        print(f"  {path}")
        print(f"    -> {result}")

    # Test 3: Start watching (if we have a transcript dir)
    print("\n[TEST 3] Testing file tracker...")
    tracker = TranscriptFileTracker()

    # Find a real transcript file to test with
    # Test with any available transcript directory
    test_dir = next(CLAUDE_PROJECTS_DIR.iterdir(), None) if CLAUDE_PROJECTS_DIR.exists() else None
    if test_dir.exists():
        jsonl_files = list(test_dir.glob("*.jsonl"))
        if jsonl_files:
            test_file = jsonl_files[0]
            print(f"  Testing with: {test_file}")

            # Read entries
            entries = list(tracker.get_new_entries(str(test_file)))
            print(f"  First scan: {len(entries)} entries")

            # Second read should return 0 (no new content)
            entries2 = list(tracker.get_new_entries(str(test_file)))
            print(f"  Second scan: {len(entries2)} entries (should be 0)")

            # Check an entry
            if entries:
                sample = entries[-1]
                print(f"  Sample entry type: {sample.get('type')}")
                if sample.get('type') == 'assistant':
                    usage = sample.get('message', {}).get('usage', {})
                    print(f"  Sample usage: {usage}")

    # Test 4: Test deduplication
    print("\n[TEST 4] Testing deduplication...")
    tracker.reset()
    test_id = "req_test123"
    print(f"  First check: is_seen={tracker.is_seen(test_id)} (should be False)")
    print(f"  Second check: is_seen={tracker.is_seen(test_id)} (should be True)")
    print(f"  None check: is_seen={tracker.is_seen(None)} (should be True)")

    # Test 5: Full integration test
    if len(sys.argv) > 1 and sys.argv[1] == "--watch":
        print("\n[TEST 5] Full watch test (Ctrl+C to stop)...")

        mission_id = "mission_1b2fe87d"
        workspace = "/path/to/atlasforge/missions/mission_1b2fe87d/workspace"

        success = watcher.start(mission_id, workspace, stage="TESTING")
        print(f"  Started: {success}")
        print(f"  Stats: {watcher.get_stats()}")

        if success:
            try:
                print("  Watching for changes (Ctrl+C to stop)...")
                while True:
                    time.sleep(5)
                    stats = watcher.get_stats()
                    print(f"  Events recorded: {stats['events_recorded']}, "
                          f"Last update: {stats['last_update']}")
            except KeyboardInterrupt:
                print("\n  Stopping...")

        watcher.stop()
        print(f"  Final stats: {watcher.get_stats()}")

    print("\n" + "=" * 60)
    print("Self-test complete!")
    print("=" * 60)
