"""
MissionArchiver: Enhanced archival functionality with manifest generation.

Extends the basic archival in atlasforge_engine.py with:
- Extended manifest.json with stage and agent metadata
- Archive discovery and listing
- Compression for old archives
- TTL caching for performance (added in Cycle 3)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Callable
import json
import gzip
import shutil
import os
import time
from functools import wraps


# =============================================================================
# TTL CACHE HELPER - Added in Cycle 3 for performance
# =============================================================================

def ttl_cache(maxsize: int = 100, ttl_seconds: int = 30):
    """
    Time-based cache decorator with LRU eviction.

    Args:
        maxsize: Maximum cached items before eviction
        ttl_seconds: Cache expiry time in seconds

    Usage:
        @ttl_cache(maxsize=1, ttl_seconds=60)
        def expensive_function():
            ...
    """
    def decorator(func: Callable):
        cache = {}
        timestamps = {}

        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create cache key from args
            key = str(args) + str(sorted(kwargs.items()))
            now = time.time()

            # Check if cached and not expired
            if key in cache and (now - timestamps.get(key, 0)) < ttl_seconds:
                return cache[key]

            # Call function and cache result
            result = func(*args, **kwargs)
            cache[key] = result
            timestamps[key] = now

            # Evict oldest if over maxsize
            if len(cache) > maxsize:
                oldest = min(timestamps, key=timestamps.get)
                del cache[oldest]
                del timestamps[oldest]

            return result

        def cache_clear():
            """Clear the cache."""
            cache.clear()
            timestamps.clear()

        def cache_info():
            """Get cache statistics."""
            return {
                'size': len(cache),
                'maxsize': maxsize,
                'ttl_seconds': ttl_seconds
            }

        wrapper.cache_clear = cache_clear
        wrapper.cache_info = cache_info
        return wrapper
    return decorator

from .transcript_parser import TranscriptParser
from .mission_reconstructor import MissionReconstructor, MissionTimeline


try:
    from atlasforge_config import (
        BASE_DIR,
        WORKSPACE_DIR,
        ARTIFACTS_DIR,
        STATE_DIR,
        MISSION_PATH,
    )
except ImportError:
    BASE_DIR = Path(__file__).resolve().parents[1]
    WORKSPACE_DIR = BASE_DIR / "workspace"
    ARTIFACTS_DIR = WORKSPACE_DIR / "artifacts"
    STATE_DIR = BASE_DIR / "state"
    MISSION_PATH = STATE_DIR / "mission.json"

TRANSCRIPTS_ARCHIVE_DIR = ARTIFACTS_DIR / "transcripts"


@dataclass
class ArchivedMission:
    """Summary of an archived mission."""
    mission_id: str
    archive_path: str
    created_at: Optional[datetime]
    completed_at: Optional[datetime]
    total_tokens: int
    total_cost_usd: float
    transcript_count: int
    has_manifest: bool

    def to_dict(self) -> Dict:
        return {
            'mission_id': self.mission_id,
            'archive_path': self.archive_path,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'total_tokens': self.total_tokens,
            'total_cost_usd': round(self.total_cost_usd, 4),
            'transcript_count': self.transcript_count,
            'has_manifest': self.has_manifest,
        }


class MissionArchiver:
    """Enhanced mission archival with GlassBox metadata."""

    def __init__(self, archive_dir: Optional[Path] = None):
        self.archive_dir = archive_dir or TRANSCRIPTS_ARCHIVE_DIR
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.parser = TranscriptParser()

    def enhance_manifest(self, archive_path: Path) -> Dict:
        """
        Add GlassBox-specific data to an existing manifest.

        Adds:
        - stages: List of stage timelines
        - agent_hierarchy: Tree of agents
        - decision_log: Key events

        Args:
            archive_path: Path to mission archive directory

        Returns:
            Enhanced manifest dict
        """
        archive_path = Path(archive_path)
        manifest_path = archive_path / "manifest.json"

        # Load existing manifest or create new
        if manifest_path.exists():
            with open(manifest_path, 'r') as f:
                manifest = json.load(f)
        else:
            manifest = {
                'mission_id': archive_path.name,
                'archived_at': datetime.now().isoformat(),
            }

        # Reconstruct mission to get hierarchy and stages
        reconstructor = MissionReconstructor(archive_path, manifest)
        timeline = reconstructor.reconstruct()

        # Enhance manifest
        manifest['glassbox'] = {
            'version': '1.0',
            'generated_at': datetime.now().isoformat(),
        }

        manifest['stages'] = [s.to_dict() for s in timeline.stages]

        manifest['agent_hierarchy'] = {
            'root_agents': [a.agent_id for a in timeline.root_agents],
            'agents': [
                {
                    'id': a.agent_id,
                    'parent': a.parent_agent_id,
                    'tokens': a.total_tokens,
                    'model': a.model,
                    'duration_seconds': round(a.duration_seconds, 2),
                }
                for a in timeline.all_agents
            ],
        }

        manifest['decision_log'] = [d.to_dict() for d in timeline.decision_log[:50]]

        manifest['metrics'] = {
            'total_input_tokens': timeline.total_input_tokens,
            'total_output_tokens': timeline.total_output_tokens,
            'total_tokens': timeline.total_tokens,
            'estimated_cost_usd': round(timeline.estimated_cost_usd, 4),
            'total_duration_seconds': round(timeline.total_duration_seconds, 2),
            'agent_count': len(timeline.all_agents),
        }

        # Read agent errors for this mission from state/agent_errors.jsonl
        mission_id_key = manifest.get('mission_id')
        try:
            errors_path = BASE_DIR / 'state' / 'agent_errors.jsonl'
            agent_errors = []
            if errors_path.exists():
                with open(errors_path, 'r') as _ef:
                    for _line in _ef:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _entry = json.loads(_line)
                            if mission_id_key and _entry.get('mission_id') == mission_id_key:
                                agent_errors.append(_entry)
                        except (json.JSONDecodeError, KeyError):
                            pass
            manifest['agent_errors'] = agent_errors
            manifest['agent_error_count'] = len(agent_errors)
        except Exception:
            manifest['agent_errors'] = []
            manifest['agent_error_count'] = 0

        # Save enhanced manifest
        with open(manifest_path, 'w') as f:
            json.dump(manifest, f, indent=2)

        return manifest

    def compress_old_archives(self, days_old: int = 30) -> List[str]:
        """
        Compress archives older than N days to save space.

        Creates {mission_id}.tar.gz and removes original directory.

        Args:
            days_old: Age threshold in days

        Returns:
            List of compressed archive paths
        """
        cutoff = datetime.now() - timedelta(days=days_old)
        compressed = []

        for archive_dir in self.archive_dir.iterdir():
            if not archive_dir.is_dir():
                continue

            # Check manifest for archived_at
            manifest_path = archive_dir / "manifest.json"
            if manifest_path.exists():
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)
                    archived_at = datetime.fromisoformat(manifest.get('archived_at', ''))
                    if archived_at >= cutoff:
                        continue
                except (json.JSONDecodeError, ValueError):
                    pass

            # Check directory mtime as fallback
            try:
                dir_mtime = datetime.fromtimestamp(os.path.getmtime(archive_dir))
                if dir_mtime >= cutoff:
                    continue
            except OSError:
                continue

            # Compress
            archive_name = archive_dir.name
            tar_path = self.archive_dir / f"{archive_name}.tar.gz"

            try:
                shutil.make_archive(
                    str(self.archive_dir / archive_name),
                    'gztar',
                    self.archive_dir,
                    archive_name
                )
                # Remove original directory
                shutil.rmtree(archive_dir)
                compressed.append(str(tar_path))
            except Exception as e:
                print(f"Failed to compress {archive_dir}: {e}")

        return compressed


@ttl_cache(maxsize=1, ttl_seconds=30)
def list_archived_missions() -> List[ArchivedMission]:
    """
    List all archived missions with summary stats.

    Cached for 30 seconds to improve dashboard performance with large mission counts.
    Call list_archived_missions.cache_clear() to force refresh.

    Returns:
        List of ArchivedMission objects (sorted by modification time, newest first)
    """
    missions = []
    archive_dir = TRANSCRIPTS_ARCHIVE_DIR

    if not archive_dir.exists():
        return missions

    for item in sorted(archive_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if item.is_dir():
            manifest_path = item / "manifest.json"
            has_manifest = manifest_path.exists()

            if has_manifest:
                try:
                    with open(manifest_path, 'r') as f:
                        manifest = json.load(f)

                    totals = manifest.get('totals', {})
                    metrics = manifest.get('metrics', {})
                    time_window = manifest.get('time_window', {})

                    # Parse dates
                    created_at = None
                    completed_at = None
                    if time_window.get('start'):
                        try:
                            created_at = datetime.fromisoformat(time_window['start'])
                        except ValueError:
                            pass
                    if time_window.get('end'):
                        try:
                            completed_at = datetime.fromisoformat(time_window['end'])
                        except ValueError:
                            pass

                    missions.append(ArchivedMission(
                        mission_id=manifest.get('mission_id', item.name),
                        archive_path=str(item),
                        created_at=created_at,
                        completed_at=completed_at,
                        total_tokens=totals.get('total_tokens', 0) or metrics.get('total_tokens', 0),
                        total_cost_usd=metrics.get('estimated_cost_usd', 0.0),
                        transcript_count=totals.get('transcript_count', 0) or len(list(item.glob('*.jsonl'))),
                        has_manifest=True,
                    ))
                except (json.JSONDecodeError, KeyError, TypeError):
                    # Fallback for malformed manifest
                    missions.append(ArchivedMission(
                        mission_id=item.name,
                        archive_path=str(item),
                        created_at=None,
                        completed_at=None,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        transcript_count=len(list(item.glob('*.jsonl'))),
                        has_manifest=True,
                    ))
            else:
                # No manifest, count files
                transcript_count = len(list(item.glob('*.jsonl')))
                if transcript_count > 0:
                    missions.append(ArchivedMission(
                        mission_id=item.name,
                        archive_path=str(item),
                        created_at=None,
                        completed_at=None,
                        total_tokens=0,
                        total_cost_usd=0.0,
                        transcript_count=transcript_count,
                        has_manifest=False,
                    ))

        elif item.suffix == '.gz' and item.name.endswith('.tar.gz'):
            # Compressed archive
            mission_id = item.name[:-7]  # Remove .tar.gz
            missions.append(ArchivedMission(
                mission_id=mission_id,
                archive_path=str(item),
                created_at=None,
                completed_at=None,
                total_tokens=0,
                total_cost_usd=0.0,
                transcript_count=0,
                has_manifest=False,
            ))

    return missions


def load_mission_archive(mission_id: str) -> Optional[MissionTimeline]:
    """
    Load and reconstruct a specific archived mission.

    Args:
        mission_id: Mission identifier

    Returns:
        MissionTimeline or None if not found
    """
    archive_path = TRANSCRIPTS_ARCHIVE_DIR / mission_id

    if not archive_path.exists():
        # Check for compressed archive
        tar_path = TRANSCRIPTS_ARCHIVE_DIR / f"{mission_id}.tar.gz"
        if tar_path.exists():
            # Extract temporarily
            shutil.unpack_archive(tar_path, TRANSCRIPTS_ARCHIVE_DIR)
            if not archive_path.exists():
                return None

    if not archive_path.is_dir():
        return None

    # Load manifest if available
    manifest_path = archive_path / "manifest.json"
    mission_metadata = {}
    if manifest_path.exists():
        try:
            with open(manifest_path, 'r') as f:
                mission_metadata = json.load(f)
        except json.JSONDecodeError:
            pass

    # Reconstruct
    reconstructor = MissionReconstructor(archive_path, mission_metadata)
    return reconstructor.reconstruct()


def search_missions(
    query: Optional[str] = None,
    stage: Optional[str] = None,
    min_tokens: Optional[int] = None,
    max_tokens: Optional[int] = None,
) -> List[Dict]:
    """
    Search across all missions.

    Args:
        query: Text to search in decision logs
        stage: Filter by stage (e.g., "BUILDING")
        min_tokens: Minimum total tokens
        max_tokens: Maximum total tokens

    Returns:
        List of matching missions with match details
    """
    results = []
    missions = list_archived_missions()

    for mission in missions:
        matches = []

        # Load full timeline for searching
        timeline = load_mission_archive(mission.mission_id)
        if not timeline:
            continue

        # Apply filters
        if min_tokens and timeline.total_tokens < min_tokens:
            continue
        if max_tokens and timeline.total_tokens > max_tokens:
            continue

        # Filter by stage
        if stage:
            stage_match = any(s.stage.upper() == stage.upper() for s in timeline.stages)
            if not stage_match:
                continue
            # Add matching stages to results
            for s in timeline.stages:
                if s.stage.upper() == stage.upper():
                    matches.append({
                        'type': 'stage',
                        'stage': s.stage,
                        'tokens': s.tokens_used,
                        'duration': s.duration_seconds,
                    })

        # Search query in decision log
        if query:
            query_lower = query.lower()
            for event in timeline.decision_log:
                if query_lower in event.description.lower():
                    matches.append({
                        'type': 'decision',
                        'event_type': event.event_type,
                        'description': event.description,
                        'timestamp': event.timestamp.isoformat(),
                    })

        if matches or (not query and not stage):
            results.append({
                'mission_id': mission.mission_id,
                'archive_path': mission.archive_path,
                'total_tokens': timeline.total_tokens,
                'matches': matches[:10],  # Limit matches per mission
            })

    return results
