"""
Mission Snapshot Manager

Provides automated backup and recovery for mission state:
- Creates snapshots of mission.json to .git/mission_snapshots/
- SHA256 integrity verification
- Rotation policy (24 hourly + 7 daily)
- Hourly background scheduler during active missions
- Stale backup monitoring and alerts

Created by: mission_4474ab18
Date: 2026-01-01
"""

import hashlib
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable
import fcntl

logger = logging.getLogger("mission_snapshot")

# Base paths
BASE_DIR = Path(__file__).parent
STATE_DIR = BASE_DIR / "state"
MISSION_PATH = STATE_DIR / "mission.json"
SNAPSHOTS_DIR = BASE_DIR / ".git" / "mission_snapshots"


@dataclass
class MissionSnapshot:
    """Represents a mission state snapshot."""
    snapshot_id: str
    mission_id: str
    timestamp: str
    stage: str
    sha256_hash: str
    file_path: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'MissionSnapshot':
        """Create from dictionary, filtering unknown keys to prevent TypeError."""
        from dataclasses import fields as _dc_fields
        known = {f.name for f in _dc_fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class SnapshotManager:
    """
    Manages mission state snapshots with integrity verification.

    Stores snapshots in .git/mission_snapshots/ with format:
    snapshot_<mission_id>_<timestamp>_<short_hash>.json

    Each snapshot file contains:
    - Original mission.json content
    - Snapshot metadata (hash, timestamp, stage, etc.)
    """

    # Rotation policy
    MAX_HOURLY_SNAPSHOTS = 24  # Keep 24 hourly snapshots
    MAX_DAILY_SNAPSHOTS = 7    # Keep 7 daily snapshots

    def __init__(self, snapshots_dir: Path = None, mission_path: Path = None):
        self.snapshots_dir = snapshots_dir or SNAPSHOTS_DIR
        self.mission_path = mission_path or MISSION_PATH
        self._lock = threading.RLock()  # RLock prevents deadlock when rotate_snapshots calls list_snapshots

        # Ensure snapshots directory exists
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()

    def _generate_snapshot_id(self, mission_id: str, timestamp: str, content_hash: str) -> str:
        """Generate unique snapshot ID."""
        import re as _re
        # Strip path traversal chars — only allow alphanumeric, underscore, hyphen
        safe_mission_id = _re.sub(r'[^a-zA-Z0-9_-]', '_', str(mission_id))
        short_hash = content_hash[:8]
        ts_clean = timestamp.replace(':', '-').replace('.', '-').replace('+', '-')
        return f"snapshot_{safe_mission_id}_{ts_clean}_{short_hash}"

    def create_snapshot(self, extra_metadata: Dict[str, Any] = None, stage_hint: str = None) -> Optional[MissionSnapshot]:
        """
        Create a snapshot of current mission.json.

        Args:
            extra_metadata: Additional metadata to include
            stage_hint: Optional hint about what triggered snapshot

        Returns:
            MissionSnapshot if successful, None otherwise
        """
        with self._lock:
            try:
                if not self.mission_path.exists():
                    logger.debug("No mission.json to snapshot")
                    return None

                # Read mission.json
                with open(self.mission_path, 'r') as f:
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        content = f.read()
                        mission_data = json.loads(content)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)

                # Extract key fields
                mission_id = mission_data.get('mission_id', 'unknown')
                stage = mission_data.get('current_stage', 'UNKNOWN')

                # Compute hash
                content_hash = self._compute_hash(content)

                # Generate snapshot ID and timestamp
                timestamp = datetime.now().isoformat()
                snapshot_id = self._generate_snapshot_id(mission_id, timestamp, content_hash)

                # Create snapshot file path
                snapshot_filename = f"{snapshot_id}.json"
                snapshot_path = self.snapshots_dir / snapshot_filename

                # Compute a normalized hash of mission_data for tamper detection.
                # sha256_hash is the raw-content hash (used for snapshot_id generation).
                # normalized_sha256 is the canonical hash of mission_state JSON —
                # this is what verify_snapshot() compares against.
                normalized_hash = hashlib.sha256(
                    json.dumps(mission_data, sort_keys=True, separators=(',', ':')).encode('utf-8')
                ).hexdigest()

                # Build snapshot data
                snapshot_data = {
                    'snapshot_metadata': {
                        'snapshot_id': snapshot_id,
                        'mission_id': mission_id,
                        'timestamp': timestamp,
                        'stage': stage,
                        'sha256_hash': content_hash,
                        'normalized_sha256': normalized_hash,
                        'file_path': str(snapshot_path),
                        'stage_hint': stage_hint,
                        'extra': extra_metadata or {}
                    },
                    'mission_state': mission_data
                }

                # Atomic write: temp file + rename
                temp_path = snapshot_path.with_suffix('.tmp')
                with open(temp_path, 'w') as f:
                    json.dump(snapshot_data, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic rename
                temp_path.rename(snapshot_path)

                # Create snapshot object
                snapshot = MissionSnapshot(
                    snapshot_id=snapshot_id,
                    mission_id=mission_id,
                    timestamp=timestamp,
                    stage=stage,
                    sha256_hash=content_hash,
                    file_path=str(snapshot_path),
                    metadata=snapshot_data['snapshot_metadata'].get('extra', {})
                )

                logger.info(f"Created snapshot: {snapshot_id}")

                # Run rotation in background
                threading.Thread(target=self.rotate_snapshots, daemon=True).start()

                return snapshot

            except Exception as e:
                logger.error(f"Failed to create snapshot: {e}")
                return None

    def verify_snapshot(self, snapshot_id: str) -> bool:
        """
        Verify snapshot integrity using stored hash.

        Args:
            snapshot_id: ID of snapshot to verify

        Returns:
            True if snapshot is valid, False otherwise
        """
        try:
            snapshot = self.get_snapshot_by_id(snapshot_id)
            if not snapshot:
                return False

            snapshot_path = Path(snapshot.file_path)
            if not snapshot_path.exists():
                return False

            # Read snapshot file
            with open(snapshot_path, 'r') as f:
                snapshot_data = json.load(f)

            meta = snapshot_data.get('snapshot_metadata', {})
            mission_state = snapshot_data.get('mission_state', {})

            # Prefer normalized_sha256 (stored since the integrity fix).
            # Fall back to sha256_hash for legacy snapshots: recompute normalized
            # hash and compare — these will only match if the stored hash happened
            # to be a normalized-JSON hash, which is unlikely for old snapshots,
            # so legacy snapshots without normalized_sha256 return False.
            normalized_stored = meta.get('normalized_sha256')
            if not normalized_stored:
                # Legacy snapshot without normalized hash: cannot verify integrity
                logger.warning(
                    f"Snapshot {snapshot_id} has no normalized_sha256 — verification skipped"
                )
                return False

            if len(normalized_stored) != 64:
                return False

            recomputed = hashlib.sha256(
                json.dumps(mission_state, sort_keys=True, separators=(',', ':')).encode('utf-8')
            ).hexdigest()
            return recomputed == normalized_stored

        except Exception as e:
            logger.error(f"Snapshot verification failed for {snapshot_id}: {e}")
            return False

    def restore_snapshot(self, snapshot_id: str, verify_first: bool = True) -> bool:
        """
        Restore mission.json from a snapshot.

        Args:
            snapshot_id: ID of snapshot to restore
            verify_first: Whether to verify integrity before restore

        Returns:
            True if restoration successful, False otherwise
        """
        with self._lock:
            try:
                snapshot = self.get_snapshot_by_id(snapshot_id)
                if not snapshot:
                    logger.error(f"Snapshot not found: {snapshot_id}")
                    return False

                snapshot_path = Path(snapshot.file_path)
                if not snapshot_path.exists():
                    logger.error(f"Snapshot file not found: {snapshot_path}")
                    return False

                if verify_first and not self.verify_snapshot(snapshot_id):
                    logger.error(f"Snapshot integrity verification failed: {snapshot_id}")
                    return False

                # Read snapshot
                with open(snapshot_path, 'r') as f:
                    snapshot_data = json.load(f)

                mission_state = snapshot_data.get('mission_state', {})

                # Create timestamped backup of current mission.json before restoring
                # (avoids overwriting previous backup on each call)
                if self.mission_path.exists():
                    ts = datetime.now().strftime('%Y%m%dT%H%M%S')
                    backup_path = self.mission_path.parent / f"mission.json.pre_restore_{ts}.backup"
                    shutil.copy2(self.mission_path, backup_path)

                # Ensure state directory exists
                self.mission_path.parent.mkdir(parents=True, exist_ok=True)

                # Atomic write to mission.json
                temp_path = self.mission_path.with_suffix('.tmp')
                with open(temp_path, 'w') as f:
                    json.dump(mission_state, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())

                # Atomic rename
                temp_path.rename(self.mission_path)

                logger.info(f"Restored from snapshot: {snapshot_id}")
                return True

            except Exception as e:
                logger.error(f"Failed to restore snapshot {snapshot_id}: {e}")
                return False

    def rotate_snapshots(self, max_hourly: int = None, max_daily: int = None):
        """
        Apply rotation policy to snapshots.

        Keeps:
        - max_hourly most recent snapshots from last 24 hours
        - max_daily snapshots (one per day) for older snapshots

        Args:
            max_hourly: Maximum hourly snapshots to keep
            max_daily: Maximum daily snapshots to keep
        """
        if max_hourly is None:
            max_hourly = self.MAX_HOURLY_SNAPSHOTS
        if max_daily is None:
            max_daily = self.MAX_DAILY_SNAPSHOTS

        with self._lock:
            try:
                snapshots = self.list_snapshots()
                if not snapshots:
                    return

                now = datetime.now()
                cutoff_24h = now - timedelta(hours=24)

                # Separate into recent (last 24h) and older
                recent = []
                older = []

                def _parse_ts(x):
                    try:
                        return datetime.fromisoformat(x.timestamp).replace(tzinfo=None)
                    except (ValueError, AttributeError):
                        return datetime.min

                for s in snapshots:
                    try:
                        ts = datetime.fromisoformat(s.timestamp).replace(tzinfo=None)
                        if ts > cutoff_24h:
                            recent.append(s)
                        else:
                            older.append(s)
                    except Exception as e:
                        logger.warning(f"Could not parse snapshot timestamp {s.timestamp!r}: {e}")
                        older.append(s)

                # Sort by timestamp (newest first)
                recent.sort(key=_parse_ts, reverse=True)
                older.sort(key=_parse_ts, reverse=True)

                # Keep only max_hourly recent snapshots
                to_delete = recent[max_hourly:]

                # For older snapshots, keep one per day (most recent per day)
                daily_buckets = {}
                for s in older:
                    try:
                        ts = datetime.fromisoformat(s.timestamp).replace(tzinfo=None)
                        day_key = ts.strftime('%Y-%m-%d')
                        if day_key not in daily_buckets:
                            daily_buckets[day_key] = s  # Keep newest for this day
                        else:
                            to_delete.append(s)  # Delete older snapshot from same day
                    except (ValueError, AttributeError) as e:
                        logger.warning(f"Could not bucket snapshot {getattr(s, 'timestamp', '?')!r}: {e}")
                        to_delete.append(s)

                # From daily buckets, keep only max_daily
                daily_snapshots = sorted(daily_buckets.values(), key=_parse_ts, reverse=True)
                to_delete.extend(daily_snapshots[max_daily:])

                # Delete expired snapshots
                for s in to_delete:
                    try:
                        snapshot_path = Path(s.file_path)
                        if snapshot_path.exists():
                            snapshot_path.unlink()
                            logger.debug(f"Rotated out snapshot: {s.snapshot_id}")
                    except Exception as e:
                        logger.warning(f"Failed to delete snapshot {s.snapshot_id}: {e}")

                if to_delete:
                    logger.info(f"Rotated {len(to_delete)} old snapshots")

            except Exception as e:
                logger.error(f"Snapshot rotation failed: {e}")

    def list_snapshots(self) -> List[MissionSnapshot]:
        """
        List all available snapshots.

        Returns:
            List of MissionSnapshot objects sorted by timestamp (newest first)
        """
        snapshots = []

        try:
            if not self.snapshots_dir.exists():
                return snapshots

            for snapshot_file in self.snapshots_dir.glob('snapshot_*.json'):
                try:
                    with open(snapshot_file, 'r') as f:
                        data = json.load(f)

                    meta = data.get('snapshot_metadata', {})
                    snapshot = MissionSnapshot(
                        snapshot_id=meta.get('snapshot_id', snapshot_file.stem),
                        mission_id=meta.get('mission_id', 'unknown'),
                        timestamp=meta.get('timestamp', ''),
                        stage=meta.get('stage', ''),
                        sha256_hash=meta.get('sha256_hash', ''),
                        file_path=str(snapshot_file),
                        metadata=meta.get('extra', {})
                    )
                    snapshots.append(snapshot)
                except Exception as e:
                    logger.warning(f"Failed to read snapshot {snapshot_file}: {e}")

            # Sort by timestamp (newest first)
            def _parse_ts_simple(x):
                try:
                    return datetime.fromisoformat(x.timestamp).replace(tzinfo=None)
                except (ValueError, AttributeError):
                    return datetime.min
            snapshots.sort(key=_parse_ts_simple, reverse=True)

        except Exception as e:
            logger.error(f"Failed to list snapshots: {e}")

        return snapshots

    def get_latest_snapshot(self) -> Optional[MissionSnapshot]:
        """
        Get the most recent snapshot.

        Returns:
            Most recent MissionSnapshot or None
        """
        snapshots = self.list_snapshots()
        return snapshots[0] if snapshots else None

    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[MissionSnapshot]:
        """
        Get a specific snapshot by ID.

        Args:
            snapshot_id: ID of snapshot to retrieve

        Returns:
            MissionSnapshot if found, None otherwise
        """
        snapshots = self.list_snapshots()
        for s in snapshots:
            if s.snapshot_id == snapshot_id:
                return s
        return None

    def get_snapshot_content(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the mission.json content stored in a snapshot.

        Args:
            snapshot_id: ID of snapshot to read

        Returns:
            Dict containing the mission.json content from the snapshot
        """
        snapshot = self.get_snapshot_by_id(snapshot_id)
        if not snapshot:
            return None

        try:
            with open(snapshot.file_path, 'r') as f:
                data = json.load(f)
            return data.get('mission_state', data)
        except Exception as e:
            logger.error(f"Failed to read snapshot content: {e}")
            return None

    def create_coordinated_snapshot(self, sub_repos: List[str] = None) -> Optional[MissionSnapshot]:
        """
        Create snapshot that captures main repo and sub-repo states.

        Args:
            sub_repos: List of sub-repo paths to include

        Returns:
            MissionSnapshot with coordinated state
        """
        try:
            # Get list of sub-repos from mission.json if not specified
            if sub_repos is None:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        mission_data = json.load(f)
                    sub_repos = mission_data.get('sub_repos', [])
                else:
                    sub_repos = []

            # Build coordination data
            coordination_data = {
                'main_repo': self._capture_repo_state(BASE_DIR),
                'sub_repos': {}
            }

            for repo_path_str in sub_repos:
                repo_path = Path(repo_path_str)
                if not repo_path.is_absolute():
                    repo_path = BASE_DIR / repo_path
                # Reject paths that escape BASE_DIR to prevent path traversal
                try:
                    repo_path.resolve().relative_to(BASE_DIR.resolve())
                except ValueError:
                    logger.warning("Skipping sub_repo outside BASE_DIR: %s", repo_path_str)
                    continue
                if repo_path.exists():
                    coordination_data['sub_repos'][str(repo_path)] = self._capture_repo_state(repo_path)

            # Create snapshot with coordination metadata
            return self.create_snapshot(
                extra_metadata={'coordination': coordination_data},
                stage_hint='Coordinated multi-repo snapshot'
            )

        except Exception as e:
            logger.error(f"Failed to create coordinated snapshot: {e}")
            return None

    def _capture_repo_state(self, repo_path: Path) -> dict:
        """Capture git state of a repository."""
        try:
            # Get current commit
            result = subprocess.run(
                ['git', 'rev-parse', 'HEAD'],
                cwd=repo_path, capture_output=True, text=True
            )
            commit = result.stdout.strip() if result.returncode == 0 else None

            # Check for uncommitted changes
            result = subprocess.run(
                ['git', 'status', '--porcelain'],
                cwd=repo_path, capture_output=True, text=True
            )
            dirty = bool(result.stdout.strip()) if result.returncode == 0 else None

            # Get current branch
            result = subprocess.run(
                ['git', 'branch', '--show-current'],
                cwd=repo_path, capture_output=True, text=True
            )
            branch = result.stdout.strip() if result.returncode == 0 else None

            return {
                'commit': commit,
                'branch': branch,
                'dirty': dirty,
                'path': str(repo_path)
            }
        except Exception as e:
            return {'error': str(e), 'path': str(repo_path)}


class SnapshotScheduler:
    """
    Background scheduler for hourly snapshots during active missions.
    """

    SNAPSHOT_INTERVAL = 3600  # 1 hour in seconds
    CHECK_INTERVAL = 60       # Check every minute if mission is active

    def __init__(self, manager: SnapshotManager = None):
        self.manager = manager
        self._running = False
        self._thread = None
        self._last_snapshot_time = 0
        self._lock = threading.Lock()

    def start(self):
        """Start the scheduler."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._scheduler_loop, daemon=True)
            self._thread.start()
            logger.info("Snapshot scheduler started")

    def stop(self):
        """Stop the scheduler."""
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Snapshot scheduler stopped")

    def is_active_mission(self) -> bool:
        """Check if there's an active mission."""
        try:
            if not MISSION_PATH.exists():
                return False

            with open(MISSION_PATH, 'r') as f:
                mission = json.load(f)

            stage = mission.get('current_stage', '')
            return stage not in (None, '', 'COMPLETE')
        except (FileNotFoundError, AttributeError, json.JSONDecodeError) as e:
            logger.debug(f"is_active_mission check failed: {e}")
            return False

    def _scheduler_loop(self):
        """Main scheduler loop."""
        while self._running:
            try:
                if self.is_active_mission():
                    now = time.time()
                    if now - self._last_snapshot_time >= self.SNAPSHOT_INTERVAL:
                        # Time for a snapshot
                        if self.manager is None:
                            self.manager = get_snapshot_manager()

                        snapshot = self.manager.create_snapshot(
                            stage_hint='Scheduled hourly snapshot'
                        )
                        if snapshot:
                            self._last_snapshot_time = now
                            logger.info(f"Scheduled snapshot created: {snapshot.snapshot_id}")

                # Wait before next check
                time.sleep(self.CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                time.sleep(60)  # Back off on errors


class StaleBackupMonitor:
    """
    Monitor for stale backups during active missions.
    Emits alerts via WebSocket when no snapshot in >2 hours.
    """

    STALE_THRESHOLD = 7200     # 2 hours in seconds
    CHECK_INTERVAL = 300       # Check every 5 minutes
    ALERT_COOLDOWN = 1800      # Don't spam alerts (30 min cooldown)

    def __init__(self, manager: SnapshotManager = None):
        self.manager = manager
        self._running = False
        self._thread = None
        self._socketio = None
        self._last_alert_time = 0
        self._lock = threading.Lock()

    def set_socketio(self, socketio):
        """Set SocketIO instance for emitting alerts."""
        self._socketio = socketio

    def start(self):
        """Start the monitor."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._thread.start()
            logger.info("Stale backup monitor started")

    def stop(self):
        """Stop the monitor."""
        with self._lock:
            self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Stale backup monitor stopped")

    def check_staleness(self) -> bool:
        """
        Check if backup is stale.

        Returns:
            True if backup is stale (>2 hours old during active mission), False otherwise
        """
        try:
            if self.manager is None:
                self.manager = get_snapshot_manager()

            latest = self.manager.get_latest_snapshot()

            if not latest:
                # No backups at all - check if mission is active
                if MISSION_PATH.exists():
                    with open(MISSION_PATH, 'r') as f:
                        mission = json.load(f)
                    stage = mission.get('current_stage', '')
                    return stage not in (None, '', 'COMPLETE')
                return False

            # Check if mission is active
            if MISSION_PATH.exists():
                with open(MISSION_PATH, 'r') as f:
                    mission = json.load(f)
                stage = mission.get('current_stage', '')
                if stage in (None, '', 'COMPLETE'):
                    return False  # Not stale if no active mission
            else:
                return False  # No mission file means not stale

            # Check age of latest snapshot
            try:
                snapshot_time = datetime.fromisoformat(latest.timestamp).replace(tzinfo=None)
                age = (datetime.now() - snapshot_time).total_seconds()
                return age > self.STALE_THRESHOLD
            except (OSError, ValueError) as e:
                logger.debug(f"StaleBackupMonitor timestamp parse failed: {e}")
                return False  # not stale — don't false-alarm

        except Exception as e:
            logger.error(f"Staleness check failed: {e}")
            return False

    def _monitor_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                if self.check_staleness():
                    self._emit_stale_alert()
                time.sleep(self.CHECK_INTERVAL)
            except Exception as e:
                logger.warning(f"Stale backup monitor error: {e}")
                time.sleep(60)

    def _emit_stale_alert(self):
        """Emit stale backup alert via WebSocket."""
        now = time.time()
        if now - self._last_alert_time < self.ALERT_COOLDOWN:
            return  # Still in cooldown

        self._last_alert_time = now

        alert_data = {
            'type': 'stale_backup',
            'message': 'No backup in over 2 hours during active mission',
            'timestamp': datetime.now().isoformat(),
            'threshold_seconds': self.STALE_THRESHOLD
        }

        if self._socketio:
            try:
                self._socketio.emit(
                    'backup_stale_alert',
                    alert_data,
                    room='backup_status',
                    namespace='/widgets'
                )
            except Exception as e:
                logger.warning(f"Failed to emit stale alert via WebSocket: {e}")

        logger.warning(f"STALE BACKUP ALERT: {alert_data['message']}")


# Singletons
_snapshot_manager: Optional[SnapshotManager] = None
_snapshot_scheduler: Optional[SnapshotScheduler] = None
_stale_backup_monitor: Optional[StaleBackupMonitor] = None


def get_snapshot_manager() -> SnapshotManager:
    """Get or create singleton SnapshotManager."""
    global _snapshot_manager
    if _snapshot_manager is None:
        _snapshot_manager = SnapshotManager()
    return _snapshot_manager


def get_snapshot_scheduler() -> SnapshotScheduler:
    """Get or create singleton SnapshotScheduler."""
    global _snapshot_scheduler
    if _snapshot_scheduler is None:
        _snapshot_scheduler = SnapshotScheduler(get_snapshot_manager())
    return _snapshot_scheduler


def get_stale_backup_monitor() -> StaleBackupMonitor:
    """Get or create singleton StaleBackupMonitor."""
    global _stale_backup_monitor
    if _stale_backup_monitor is None:
        _stale_backup_monitor = StaleBackupMonitor(get_snapshot_manager())
    return _stale_backup_monitor


# Convenience functions
def create_mission_snapshot(stage_hint: str = None) -> Optional[MissionSnapshot]:
    """
    Convenience function to create a mission snapshot.

    Args:
        stage_hint: Optional hint about what triggered snapshot

    Returns:
        MissionSnapshot if successful, None otherwise
    """
    return get_snapshot_manager().create_snapshot(stage_hint=stage_hint)


def restore_mission_snapshot(snapshot_id: str) -> bool:
    """
    Convenience function to restore from a snapshot.

    Args:
        snapshot_id: ID of snapshot to restore

    Returns:
        True if successful, False otherwise
    """
    return get_snapshot_manager().restore_snapshot(snapshot_id)


def check_recovery_needed() -> Optional[Dict[str, Any]]:
    """
    Check if mission recovery is needed.
    Called on dashboard startup to detect crashed missions.

    Returns:
        Recovery info dict if recovery needed, None otherwise
    """
    try:
        if not MISSION_PATH.exists():
            return None

        with open(MISSION_PATH, 'r') as f:
            mission = json.load(f)

        stage = mission.get('current_stage', '')
        mission_id = mission.get('mission_id', '')

        # Check if mission was in progress
        if stage in (None, '', 'COMPLETE'):
            return None

        # Check for recovery checkpoint
        recovery_checkpoint = STATE_DIR / "recovery_checkpoint.json"
        recovery_data = None
        if recovery_checkpoint.exists():
            with open(recovery_checkpoint, 'r') as f:
                recovery_data = json.load(f)

        # Get available snapshots for this mission
        manager = get_snapshot_manager()
        snapshots = manager.list_snapshots()
        mission_snapshots = [s for s in snapshots if s.mission_id == mission_id]

        return {
            'recovery_needed': True,
            'mission_id': mission_id,
            'current_stage': stage,
            'iteration': mission.get('iteration', 0),
            'cycle': mission.get('cycle', 1),
            'recovery_checkpoint': recovery_data,
            'available_snapshots': [s.to_dict() for s in mission_snapshots[:10]],  # Last 10
            'latest_snapshot': mission_snapshots[0].to_dict() if mission_snapshots else None
        }

    except Exception as e:
        logger.error(f"Recovery check failed: {e}")
        return None


# Module-level backup status data for WebSocket
def get_backup_status_data() -> Dict[str, Any]:
    """
    Get current backup status for WebSocket clients.
    Used as initial data when joining backup_status room.
    """
    try:
        manager = get_snapshot_manager()
        monitor = get_stale_backup_monitor()

        latest = manager.get_latest_snapshot()
        is_stale = monitor.check_staleness()

        # Check if mission is active
        is_active = False
        if MISSION_PATH.exists():
            with open(MISSION_PATH, 'r') as f:
                mission = json.load(f)
            stage = mission.get('current_stage', '')
            is_active = stage not in (None, '', 'COMPLETE')

        return {
            'type': 'backup_status',
            'latest_snapshot': latest.to_dict() if latest else None,
            'is_stale': is_stale,
            'stale_threshold_seconds': StaleBackupMonitor.STALE_THRESHOLD,
            'is_mission_active': is_active,
            'snapshot_count': len(manager.list_snapshots()),
            'timestamp': datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Failed to get backup status: {e}")
        return {
            'type': 'backup_status',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }


if __name__ == '__main__':
    # CLI for testing
    import sys

    logging.basicConfig(level=logging.INFO)

    if len(sys.argv) < 2:
        print("Usage: python mission_snapshot_manager.py <command>")
        print("Commands:")
        print("  create     - Create a snapshot")
        print("  list       - List all snapshots")
        print("  verify <id> - Verify snapshot integrity")
        print("  restore <id> - Restore from snapshot")
        print("  status     - Show backup status")
        sys.exit(1)

    cmd = sys.argv[1]
    manager = get_snapshot_manager()

    if cmd == 'create':
        snapshot = manager.create_snapshot(stage_hint='CLI test')
        if snapshot:
            print(f"Created: {snapshot.snapshot_id}")
            print(f"  Hash: {snapshot.sha256_hash}")
            print(f"  Stage: {snapshot.stage}")
        else:
            print("Failed to create snapshot")

    elif cmd == 'list':
        snapshots = manager.list_snapshots()
        print(f"Found {len(snapshots)} snapshots:")
        for s in snapshots:
            print(f"  {s.snapshot_id}")
            print(f"    Stage: {s.stage}, Time: {s.timestamp}")

    elif cmd == 'verify' and len(sys.argv) > 2:
        snapshot_id = sys.argv[2]
        valid = manager.verify_snapshot(snapshot_id)
        print(f"Snapshot {snapshot_id}: {'VALID' if valid else 'INVALID'}")

    elif cmd == 'restore' and len(sys.argv) > 2:
        snapshot_id = sys.argv[2]
        success = manager.restore_snapshot(snapshot_id)
        print(f"Restore {'successful' if success else 'failed'}")

    elif cmd == 'status':
        status = get_backup_status_data()
        print(json.dumps(status, indent=2))

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
