"""
af_engine.core.archival - Transcript Archival and AfterImage Ingestion

Migrated from the legacy monolithic engine. Contains all transcript
archival logic that was previously loaded via importlib.util hack in
af_engine/__init__.py.

Public API:
    archive_mission_transcripts(mission: Dict) -> Dict
    ingest_afterimage_from_archive(archive_path, mission) -> Dict
    rearchive_mission(mission_id: str) -> Dict
    rearchive_all_missions() -> Dict
"""

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Any, Dict, List, Optional

import io_utils
from atlasforge_config import (
    BASE_DIR, STATE_DIR, ARTIFACTS_DIR, get_transcript_dir
)

logger = logging.getLogger(__name__)

# Transcript archival paths
CLAUDE_TRANSCRIPTS_BASE = Path.home() / ".claude" / "projects"
CLAUDE_TRANSCRIPTS_DIR = get_transcript_dir()
CODEX_SESSIONS_DIR = Path.home() / ".codex" / "sessions"
TRANSCRIPTS_ARCHIVE_DIR = ARTIFACTS_DIR / "transcripts"

# Mission log paths
MISSIONS_DIR = BASE_DIR / "missions"
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"
MISSIONS_DIR.mkdir(exist_ok=True)
MISSION_LOGS_DIR.mkdir(exist_ok=True)

DEFAULT_LLM_PROVIDER = "claude"
SUPPORTED_LLM_PROVIDERS = {"claude", "codex", "gemini"}

# AfterImage integration (optional)
AFTERIMAGE_AVAILABLE = False
AFTERIMAGE_INGEST_AVAILABLE = False
AfterImageTranscriptExtractor = None
AfterImageCodeFilter = None
AfterImageKnowledgeBase = None

try:
    import sys
    from atlasforge_config import BASE_DIR as _AF_BASE_DIR, WORKSPACE_DIR as _AF_WORKSPACE_DIR

    afterimage_candidates = [
        _AF_WORKSPACE_DIR / "AI-AfterImage",
        _AF_WORKSPACE_DIR / "AfterImage",
        _AF_BASE_DIR.parent / "AI-AfterImage",
        Path.home() / "AI-AfterImage",
    ]
    for candidate in afterimage_candidates:
        if candidate.exists():
            candidate_str = str(candidate)
            if candidate_str not in sys.path:
                sys.path.insert(0, candidate_str)

    from afterimage.extract import TranscriptExtractor as AfterImageTranscriptExtractor
    from afterimage.filter import CodeFilter as AfterImageCodeFilter
    from afterimage.kb import KnowledgeBase as AfterImageKnowledgeBase
    AFTERIMAGE_AVAILABLE = True
    AFTERIMAGE_INGEST_AVAILABLE = True
except ImportError:
    pass


# =============================================================================
# PROVIDER RESOLUTION
# =============================================================================

def _normalize_provider(provider: Optional[str]) -> str:
    """Normalize provider to supported values."""
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    return DEFAULT_LLM_PROVIDER


def _load_provider_from_state() -> Optional[str]:
    """Load provider from state/llm_provider.json if present."""
    provider_path = STATE_DIR / "llm_provider.json"
    if not provider_path.exists():
        return None
    try:
        data = io_utils.atomic_read_json(provider_path, {})
    except Exception as e:
        logger.warning("_load_provider_from_state: failed to read provider state: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    return data.get("provider")


def _get_archive_provider(mission: Optional[Dict] = None) -> str:
    """
    Resolve the provider to use when searching transcript sources.
    Precedence: mission field -> env var -> persisted state -> default.
    """
    if isinstance(mission, dict):
        mission_provider = mission.get("llm_provider")
        if mission_provider:
            return _normalize_provider(mission_provider)

    env_provider = os.environ.get("ATLASFORGE_LLM_PROVIDER")
    if env_provider:
        return _normalize_provider(env_provider)

    state_provider = _load_provider_from_state()
    if state_provider:
        return _normalize_provider(state_provider)

    return DEFAULT_LLM_PROVIDER


# =============================================================================
# TRANSCRIPT DIRECTORY HELPERS
# =============================================================================

def _workspace_to_transcript_dir(workspace_path: str) -> Path:
    """
    Convert a workspace path to Claude transcript directory format.

    Claude stores transcripts in: ~/.claude/projects/-{path-with-dashes}
    Note: Claude converts underscores to dashes in directory names.
    """
    # rstrip BEFORE the falsy/blank check so that '/' (truthy) becomes '' (falsy) and is
    # caught rather than becoming CLAUDE_TRANSCRIPTS_BASE / '' which exposes the base dir.
    # Also strip() to catch whitespace-only paths (e.g. '   ') that would silently pass
    # the falsy check and produce a path with spaces.
    workspace_path = str(workspace_path).rstrip('/')
    # Strip all control characters (including null bytes) from the path before any checks.
    # A path like /workspace/mis\x00sion could otherwise bypass the null-byte check on
    # systems where re.sub strips \x00 before the explicit check reaches it.
    # Strip ASCII control chars AND Unicode line/paragraph separators (U+2028, U+2029).
    # These Unicode chars are treated as line endings by some log parsers and path tools,
    # matching the same set that _sanitize_for_log strips in orchestrator.py.
    workspace_path = re.sub(r'[\x00-\x1f\x7f\u2028\u2029]', '', workspace_path)
    if not workspace_path.strip():
        return CLAUDE_TRANSCRIPTS_DIR
    # Note: re.sub above already strips all control chars including \x00, so no separate
    # null-byte check is needed here.
    # Known collision: both '/' and '_' map to '-', matching Claude's actual transcript
    # directory naming behavior. This means paths like "/foo/bar" and "/foo-bar" produce
    # the same transcript dir name. This is intentional — it mirrors Claude's behavior,
    # so the mapping is correct even though distinct input paths may collide.
    escaped = workspace_path.replace('/', '-').replace('_', '-')
    return CLAUDE_TRANSCRIPTS_BASE / escaped


def _get_all_transcript_dirs_for_mission(mission: Dict) -> List[Path]:
    """Get all possible transcript directories for a mission."""
    dirs_to_check = []

    if CLAUDE_TRANSCRIPTS_DIR.exists():
        dirs_to_check.append(CLAUDE_TRANSCRIPTS_DIR)

    workspace = mission.get('mission_workspace')
    if workspace:
        workspace_dir = _workspace_to_transcript_dir(workspace)
        if workspace_dir.exists() and workspace_dir not in dirs_to_check:
            dirs_to_check.append(workspace_dir)

    mission_dir = mission.get('mission_dir')
    if mission_dir:
        workspace_under_mission = Path(mission_dir) / 'workspace'
        if workspace_under_mission.exists():
            mission_workspace_dir = _workspace_to_transcript_dir(str(workspace_under_mission))
            if mission_workspace_dir.exists() and mission_workspace_dir not in dirs_to_check:
                dirs_to_check.append(mission_workspace_dir)

    return dirs_to_check


def _workspace_paths_match(expected_workspace: str, candidate_workspace: str) -> bool:
    """Return True if candidate workspace is the same as or inside expected workspace."""
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
    """Return True only for Codex exec/headless session metadata."""
    if not isinstance(payload, dict):
        return False

    originator = str(payload.get("originator") or "").strip().lower()
    source = str(payload.get("source") or "").strip().lower()

    if source and source != "exec":
        return False
    if originator and originator != "codex_exec":
        return False

    return source == "exec" or originator == "codex_exec"


def _codex_transcript_matches_workspace(transcript_path: Path, workspace_path: str) -> bool:
    """Check whether a Codex transcript belongs to the mission workspace."""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
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
    except (OSError, IOError, UnicodeDecodeError):
        return False

    return False


def _find_codex_transcripts_in_window(
    start_dt: datetime,
    end_dt: datetime,
    mission: Optional[Dict] = None
) -> List[Path]:
    """Find Codex session JSONL files in the mission time window."""
    if not CODEX_SESSIONS_DIR.exists():
        return []

    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()

    workspace = None
    if isinstance(mission, dict):
        workspace = mission.get("mission_workspace")
        if not workspace:
            mission_dir = mission.get("mission_dir")
            if mission_dir:
                workspace = str(Path(mission_dir) / "workspace")

    matches: List[Path] = []
    try:
        for jsonl_file in CODEX_SESSIONS_DIR.rglob("*.jsonl"):
            try:
                mtime = os.path.getmtime(jsonl_file)
            except OSError:
                continue

            if not (start_ts - 60 <= mtime <= end_ts + 60):
                continue

            if workspace and not _codex_transcript_matches_workspace(jsonl_file, workspace):
                continue

            matches.append(jsonl_file)
    except OSError:
        return []

    return matches


# =============================================================================
# CORE ARCHIVAL FUNCTIONS
# =============================================================================

def _find_transcripts_in_window(
    start_dt: datetime,
    end_dt: datetime,
    transcript_dirs: List[Path] = None,
    mission: Dict = None
) -> List[Path]:
    """
    Find .jsonl files modified within the mission time window.

    Args:
        start_dt: Mission start datetime
        end_dt: Mission end datetime
        transcript_dirs: Optional list of directories to search
        mission: Optional mission dict to auto-detect directories

    Returns:
        List of Path objects for matching transcript files
    """
    provider = _get_archive_provider(mission)
    matching_files: List[Path] = []
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    start_ts = start_dt.timestamp()
    end_ts = end_dt.timestamp()
    seen_files = set()

    if provider == "claude":
        if transcript_dirs is None:
            if mission is not None:
                transcript_dirs = _get_all_transcript_dirs_for_mission(mission)
            else:
                transcript_dirs = [CLAUDE_TRANSCRIPTS_DIR] if CLAUDE_TRANSCRIPTS_DIR.exists() else []

        # Validate/coerce transcript_dirs elements to Path objects
        if transcript_dirs is not None:
            if isinstance(transcript_dirs, str):
                logger.warning(
                    "_find_transcripts_in_window: transcript_dirs received a string "
                    "instead of a list; wrapping in list to prevent character iteration"
                )
                transcript_dirs = [Path(transcript_dirs)]
            else:
                coerced = []
                for item in transcript_dirs:
                    if isinstance(item, Path):
                        coerced.append(item)
                    elif isinstance(item, str):
                        logger.warning(
                            f"_find_transcripts_in_window: transcript_dirs entry {item!r} "
                            f"is a str, not a Path — converting automatically"
                        )
                        coerced.append(Path(item))
                    else:
                        logger.warning(
                            f"_find_transcripts_in_window: transcript_dirs entry {item!r} "
                            f"has unexpected type {type(item).__name__} — skipping"
                        )
                transcript_dirs = coerced

        for transcript_dir in (transcript_dirs or []):
            if not transcript_dir.exists():
                logger.debug(f"Transcript directory not found: {transcript_dir}")
                continue

            for jsonl_file in transcript_dir.glob("*.jsonl"):
                file_key = str(jsonl_file.resolve())
                if file_key in seen_files:
                    continue
                try:
                    mtime = os.path.getmtime(jsonl_file)
                except OSError as e:
                    logger.warning(f"Could not get mtime for {jsonl_file}: {e}")
                    continue

                if start_ts - 60 <= mtime <= end_ts + 60:
                    matching_files.append(jsonl_file)
                    seen_files.add(file_key)

    elif provider in {"codex", "gemini"}:
        for jsonl_file in _find_codex_transcripts_in_window(start_dt, end_dt, mission):
            file_key = str(jsonl_file.resolve())
            if file_key in seen_files:
                continue
            matching_files.append(jsonl_file)
            seen_files.add(file_key)

    # Fallback for migrated/legacy data when provider metadata is missing.
    if provider == "claude" and not matching_files:
        for jsonl_file in _find_codex_transcripts_in_window(start_dt, end_dt, mission):
            file_key = str(jsonl_file.resolve())
            if file_key in seen_files:
                continue
            matching_files.append(jsonl_file)
            seen_files.add(file_key)
    elif provider in {"codex", "gemini"} and not matching_files:
        raw_fallback = _get_all_transcript_dirs_for_mission(mission or {}) if transcript_dirs is None else transcript_dirs
        fallback_dirs = []
        for item in raw_fallback:
            if isinstance(item, Path):
                fallback_dirs.append(item)
            elif isinstance(item, str):
                fallback_dirs.append(Path(item))
            else:
                logger.warning(f"_find_transcripts_in_window codex/gemini fallback: skipping non-path entry {item!r}")
        for transcript_dir in fallback_dirs:
            if not transcript_dir.exists():
                continue
            for jsonl_file in transcript_dir.glob("*.jsonl"):
                file_key = str(jsonl_file.resolve())
                if file_key in seen_files:
                    continue
                try:
                    mtime = os.path.getmtime(jsonl_file)
                except OSError:
                    continue
                if start_ts - 60 <= mtime <= end_ts + 60:
                    matching_files.append(jsonl_file)
                    seen_files.add(file_key)

    logger.info(f"Found {len(matching_files)} transcripts for provider={provider}")
    return matching_files


def _to_int(value: Any, default: int = 0) -> int:
    """Safely coerce a value to a non-negative int, returning default on failure.

    Clamps to 0 on success — token counts are always non-negative.
    """
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _parse_transcript_usage(transcript_path: Path) -> Dict[str, int]:
    """
    Parse token usage from a transcript file.

    Returns:
        Dict with input_tokens, output_tokens, total_tokens, and cache tokens
    """
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0
    }

    codex_total_seen = 0

    try:
        with open(transcript_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    if not isinstance(record, dict):
                        continue

                    # Claude assistant messages
                    if record.get("type") == "assistant":
                        msg = record.get("message", {})
                        if not isinstance(msg, dict):
                            continue
                        msg_usage = msg.get("usage", {})
                        if isinstance(msg_usage, dict) and msg_usage:
                            usage["input_tokens"] += _to_int(msg_usage.get("input_tokens", 0))
                            usage["output_tokens"] += _to_int(msg_usage.get("output_tokens", 0))
                            usage["cache_creation_input_tokens"] += _to_int(msg_usage.get("cache_creation_input_tokens", 0))
                            usage["cache_read_input_tokens"] += _to_int(msg_usage.get("cache_read_input_tokens", 0))
                        continue

                    # Codex token events
                    if record.get("type") == "event_msg":
                        payload = record.get("payload", {})
                        if not isinstance(payload, dict) or payload.get("type") != "token_count":
                            continue
                        info = payload.get("info", {})
                        if not isinstance(info, dict):
                            continue

                        last_usage = info.get("last_token_usage", {})
                        total_usage = info.get("total_token_usage", {})
                        if not isinstance(last_usage, dict):
                            continue
                        if not isinstance(total_usage, dict):
                            total_usage = {}

                        total_tokens = _to_int(total_usage.get("total_tokens", 0))
                        if total_tokens == 0:
                            # Zero-token codex events are semantically meaningless —
                            # skip ALL of them (first and subsequent) to prevent
                            # spurious token accumulation from empty transcript events.
                            continue
                        elif total_tokens <= codex_total_seen:
                            continue
                        else:
                            codex_total_seen = total_tokens

                        usage["input_tokens"] += _to_int(last_usage.get("input_tokens", 0))
                        usage["output_tokens"] += _to_int(last_usage.get("output_tokens", 0))
                        usage["cache_read_input_tokens"] += _to_int(last_usage.get("cached_input_tokens", 0))
                except json.JSONDecodeError:
                    continue
    except (OSError, UnicodeDecodeError) as e:
        logger.error(f"Error parsing transcript {transcript_path}: {e}", exc_info=True)

    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return usage


def _generate_manifest(mission_id: str, archive_dir: Path,
                       transcripts: List[Path], usage_data: List[Dict],
                       start_dt: datetime, end_dt: datetime) -> Dict:
    """Generate manifest.json for the archive."""
    manifest = {
        "mission_id": mission_id,
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "time_window": {
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat()
        },
        "transcripts": [],
        "totals": {
            "transcript_count": 0,
            "total_size_bytes": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_cache_creation_input_tokens": 0,
            "total_cache_read_input_tokens": 0,
            "total_tokens": 0
        }
    }

    if len(transcripts) != len(usage_data):
        logger.warning(
            "_generate_manifest: transcripts(%d) and usage_data(%d) length mismatch — "
            "some entries will have empty usage", len(transcripts), len(usage_data)
        )

    for transcript, usage in zip_longest(transcripts, usage_data, fillvalue={}):
        if not isinstance(transcript, Path):
            continue
        if not isinstance(usage, dict):
            usage = {}
        try:
            archived_path = archive_dir / transcript.name
            size = archived_path.stat().st_size
            mtime = datetime.fromtimestamp(os.path.getmtime(archived_path), tz=timezone.utc)
        except OSError:
            size = 0
            mtime = datetime.now(timezone.utc)

        manifest["transcripts"].append({
            "filename": transcript.name,
            "size_bytes": size,
            "modified_at": mtime.isoformat(),
            "token_usage": usage
        })

        manifest["totals"]["total_size_bytes"] += size
        manifest["totals"]["total_input_tokens"] += _to_int(usage.get("input_tokens", 0))
        manifest["totals"]["total_output_tokens"] += _to_int(usage.get("output_tokens", 0))
        manifest["totals"]["total_cache_creation_input_tokens"] += _to_int(usage.get("cache_creation_input_tokens", 0))
        manifest["totals"]["total_cache_read_input_tokens"] += _to_int(usage.get("cache_read_input_tokens", 0))
        manifest["totals"]["total_tokens"] += _to_int(usage.get("total_tokens", 0))

    manifest["totals"]["transcript_count"] = len(manifest["transcripts"])
    return manifest


# =============================================================================
# PUBLIC API
# =============================================================================

def archive_mission_transcripts(mission: Dict) -> Dict:
    """
    Archive all transcripts from the mission time window.

    Args:
        mission: The mission dict with created_at, last_updated, mission_id

    Returns:
        Dict with archival results (success, count, path, errors)
    """
    result = {
        "success": False,
        "transcripts_archived": 0,
        "archive_path": None,
        "errors": [],
        "warnings": []
    }

    if not isinstance(mission, dict):
        result["errors"].append(f"Invalid mission argument: expected dict, got {type(mission).__name__}")
        return result

    try:
        mission_id = mission.get("mission_id")
        if mission_id is None:
            created_at = mission.get("created_at")
            if created_at and isinstance(created_at, str):
                timestamp_clean = created_at.replace(":", "-").replace(".", "-")[:19]
            else:
                timestamp_clean = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")[:19]
            mission_id = f"mission_{timestamp_clean}"
        else:
            if not isinstance(mission_id, str):
                raise ValueError(f"mission_id must be a string, got {type(mission_id).__name__}")
            if '/' in mission_id or '\\' in mission_id:
                raise ValueError("invalid mission_id: path separators not allowed")
            if '\x00' in mission_id:
                raise ValueError("invalid mission_id: contains null bytes")
            if any(
                ord(c) < 32
                or ord(c) == 127
                or ord(c) in (0x200E, 0x200F, 0x2028, 0x2029)
                or 0x202A <= ord(c) <= 0x202E
                or 0x2066 <= ord(c) <= 0x2069
                for c in mission_id
            ):
                raise ValueError("invalid mission_id: contains control characters, Unicode line separators, or bidi controls")
            if not mission_id.strip():
                raise ValueError("invalid mission_id: whitespace-only mission_id is not a valid directory name")
            if len(mission_id) > 255:
                raise ValueError(
                    f"mission_id too long: {len(mission_id)} chars (max 255) — "
                    f"filesystem cannot create directory with this name"
                )

        raw_created = mission.get("created_at")
        if not isinstance(raw_created, str) or not raw_created.strip():
            result["warnings"].append(f"Invalid or missing created_at: {raw_created!r}, using current time")
            start_dt = datetime.now(timezone.utc)
        else:
            try:
                start_dt = datetime.fromisoformat(raw_created)
                if start_dt.tzinfo is None:
                    start_dt = start_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                result["warnings"].append(f"Unparseable created_at: {raw_created!r}, using current time")
                start_dt = datetime.now(timezone.utc)

        raw_updated = mission.get("last_updated")
        if not isinstance(raw_updated, str) or not raw_updated.strip():
            end_dt = datetime.now(timezone.utc)
        else:
            try:
                end_dt = datetime.fromisoformat(raw_updated)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                end_dt = datetime.now(timezone.utc)

        transcripts = _find_transcripts_in_window(start_dt, end_dt, mission=mission)

        archive_dir = (TRANSCRIPTS_ARCHIVE_DIR / mission_id).resolve()
        resolved_base = TRANSCRIPTS_ARCHIVE_DIR.resolve()
        try:
            archive_dir.relative_to(resolved_base)
        except ValueError:
            raise ValueError(f"Invalid mission_id rejected to prevent path traversal: {mission_id!r}")
        if archive_dir == resolved_base:
            raise ValueError(f"Invalid mission_id resolves to archive root, must be a subdirectory: {mission_id!r}")
        archive_dir.mkdir(parents=True, exist_ok=True)
        result["archive_path"] = str(archive_dir)

        usage_data = []
        copied_files = []

        for transcript in transcripts:
            try:
                dest_path = archive_dir / transcript.name
                shutil.copy2(transcript, dest_path)
                usage = _parse_transcript_usage(transcript)
                # Append both atomically after both operations succeed
                copied_files.append(transcript)
                usage_data.append(usage)
                logger.info(f"Archived transcript: {transcript.name}")
            except Exception as e:
                error_msg = f"Failed to copy {transcript.name}: {e}"
                logger.error(error_msg)
                result["errors"].append(error_msg)

        manifest = _generate_manifest(
            mission_id, archive_dir, copied_files, usage_data, start_dt, end_dt
        )
        manifest_path = archive_dir / "manifest.json"
        try:
            with open(manifest_path, 'w') as f:
                json.dump(manifest, f, indent=2, default=str)
        except (OSError, TypeError) as e:
            error_msg = f"Failed to write manifest: {e}"
            logger.error(error_msg)
            result["errors"].append(error_msg)

        result["success"] = len(result["errors"]) == 0
        result["transcripts_archived"] = len(copied_files)
        result["manifest"] = manifest

        logger.info(f"Archived {len(copied_files)} transcripts to {archive_dir}")

        try:
            from websocket_events import emit_transcript_archived
            emit_transcript_archived(
                mission_id=mission_id,
                archive_path=str(archive_dir),
                transcript_count=len(copied_files),
                stats=manifest
            )
        except ImportError:
            pass

    except Exception as e:
        error_msg = f"Transcript archival failed: {e}"
        logger.error(error_msg)
        result["errors"].append(error_msg)

    return result


def ingest_afterimage_from_archive(archive_path: Optional[str], mission: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Ingest archived transcripts into AI-AfterImage memory.

    Provider-agnostic: AfterImage extracts code-changing tool events
    from the archived JSONL files.
    """
    result: Dict[str, Any] = {
        "success": False,
        "stored_entries": 0,
        "total_changes": 0,
        "code_changes": 0,
        "files_processed": 0,
        "errors": [],
    }

    if not (AFTERIMAGE_AVAILABLE and AFTERIMAGE_INGEST_AVAILABLE):
        result["errors"].append("afterimage_unavailable")
        return result

    if not archive_path:
        result["errors"].append("missing_archive_path")
        return result

    archive_dir = Path(archive_path).resolve()
    try:
        archive_dir.relative_to(TRANSCRIPTS_ARCHIVE_DIR.resolve())
    except ValueError:
        result["errors"].append(f"path_traversal_rejected:{archive_dir}")
        return result
    if not archive_dir.exists():
        result["errors"].append(f"archive_not_found:{archive_dir}")
        return result

    transcript_files = sorted(archive_dir.glob("*.jsonl"))

    mission_data = mission if isinstance(mission, dict) else {}
    workspace_path = mission_data.get("mission_workspace")
    workspace_resolved: Optional[Path] = None
    if workspace_path:
        try:
            workspace_resolved = Path(workspace_path).expanduser().resolve()
        except OSError:
            workspace_resolved = None
    mission_started_at: Optional[datetime] = None
    if mission_data.get("created_at"):
        try:
            parsed_dt = datetime.fromisoformat(mission_data.get("created_at"))
            # Ensure timezone-aware for safe comparison with other aware datetimes
            if parsed_dt.tzinfo is None:
                parsed_dt = parsed_dt.replace(tzinfo=timezone.utc)
            mission_started_at = parsed_dt
        except (TypeError, ValueError):
            mission_started_at = None

    extractor = AfterImageTranscriptExtractor()
    code_filter = AfterImageCodeFilter()
    kb = AfterImageKnowledgeBase()

    try:
        for transcript in transcript_files:
            result["files_processed"] += 1
            try:
                changes = extractor.extract_from_file(transcript)
            except Exception as e:
                result["errors"].append(f"extract_failed:{transcript.name}:{e}")
                continue

            result["total_changes"] += len(changes)

            for change in changes:
                if workspace_resolved and change.file_path:
                    try:
                        change_path = Path(change.file_path).expanduser().resolve()
                        if change_path != workspace_resolved and workspace_resolved not in change_path.parents:
                            continue
                    except OSError:
                        continue  # Reject changes with unresolvable paths

                if not code_filter.is_code(change.file_path, change.new_code):
                    continue

                result["code_changes"] += 1
                try:
                    kb.store(
                        file_path=change.file_path,
                        new_code=change.new_code,
                        old_code=change.old_code,
                        context=change.context,
                        session_id=change.session_id or mission_data.get("mission_id"),
                        timestamp=change.timestamp,
                    )
                    result["stored_entries"] += 1
                except Exception as e:
                    result["errors"].append(f"store_failed:{change.file_path}:{e}")

        # Fallback: capture code files directly from mission workspace
        # when transcript extraction yields nothing.
        if result["stored_entries"] == 0 and workspace_resolved and workspace_resolved.exists():
            for file_path in workspace_resolved.rglob("*"):
                if file_path.is_symlink():
                    continue
                if not file_path.is_file():
                    continue
                # Containment check: resolved path must stay inside workspace
                try:
                    resolved = file_path.resolve()
                    resolved.relative_to(workspace_resolved)
                except Exception:
                    continue

                try:
                    if mission_started_at is not None:
                        file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                        if file_mtime < mission_started_at:
                            continue
                except OSError:
                    continue

                try:
                    content = file_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    continue

                rel_path = str(file_path.relative_to(workspace_resolved))
                if not code_filter.is_code(rel_path, content):
                    continue

                try:
                    kb.store(
                        file_path=rel_path,
                        new_code=content[:12000],
                        old_code=None,
                        context="mission_workspace_fallback",
                        session_id=mission_data.get("mission_id"),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    )
                    result["stored_entries"] += 1
                    result["code_changes"] += 1
                except Exception as e:
                    result["errors"].append(f"workspace_store_failed:{rel_path}:{e}")
    finally:
        try:
            kb.close()
        except Exception:
            pass

    result["success"] = len(result["errors"]) == 0
    return result


def rearchive_mission(mission_id: str) -> Dict:
    """
    Re-archive a specific mission's transcripts.

    Useful for fixing missions archived with empty/missing transcript data.

    Args:
        mission_id: The mission ID to re-archive

    Returns:
        Dict with archival results
    """
    mission_log_path = (MISSION_LOGS_DIR / f"{mission_id}_report.json").resolve()
    try:
        mission_log_path.relative_to(MISSION_LOGS_DIR.resolve())
    except ValueError:
        raise ValueError(f"Invalid mission_id rejected to prevent path traversal: {mission_id!r}")
    archive_manifest_path = (TRANSCRIPTS_ARCHIVE_DIR / mission_id / "manifest.json").resolve()
    try:
        archive_manifest_path.relative_to(TRANSCRIPTS_ARCHIVE_DIR.resolve())
    except ValueError:
        raise ValueError(f"Invalid mission_id rejected to prevent path traversal: {mission_id!r}")

    mission = None

    if mission_log_path.exists():
        try:
            with open(mission_log_path, 'r') as f:
                mission_log = json.load(f)
            mission = {
                'mission_id': mission_id,
                'created_at': mission_log.get('created_at'),
                'last_updated': mission_log.get('completed_at'),
                'mission_workspace': mission_log.get('mission_workspace'),
                'mission_dir': mission_log.get('mission_dir')
            }
        except (json.JSONDecodeError, KeyError, OSError, PermissionError) as _e:
            logger.warning("rearchive_mission: failed to load mission log %s: %s", mission_log_path, _e)

    if mission is None and archive_manifest_path.exists():
        try:
            with open(archive_manifest_path, 'r') as f:
                manifest = json.load(f)
            time_window = manifest.get('time_window', {})
            mission = {
                'mission_id': mission_id,
                'created_at': time_window.get('start'),
                'last_updated': time_window.get('end')
            }
        except (json.JSONDecodeError, KeyError, OSError, PermissionError) as _manifest_err:
            logger.warning("rearchive_mission: failed to load manifest %s: %s", archive_manifest_path, _manifest_err)

    if mission is None or not mission.get('mission_workspace'):
        inferred_workspace = str(MISSIONS_DIR / mission_id / 'workspace')
        if mission is None:
            mission = {
                'mission_id': mission_id,
                'mission_workspace': inferred_workspace,
                'mission_dir': str(MISSIONS_DIR / mission_id)
            }
        else:
            mission['mission_workspace'] = inferred_workspace
            mission['mission_dir'] = str(MISSIONS_DIR / mission_id)

    return archive_mission_transcripts(mission)


def rearchive_all_missions() -> Dict:
    """
    Re-archive all missions that have empty or missing transcript data.

    Returns:
        Dict with summary of rearchived missions
    """
    results = {
        'total_checked': 0,
        'rearchived': 0,
        'errors': [],
        'details': []
    }

    if not TRANSCRIPTS_ARCHIVE_DIR.exists():
        return results

    for archive_dir in TRANSCRIPTS_ARCHIVE_DIR.iterdir():
        if not archive_dir.is_dir():
            continue

        mission_id = archive_dir.name
        results['total_checked'] += 1

        manifest_path = archive_dir / "manifest.json"
        needs_rearchive = False

        if manifest_path.exists():
            try:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
                totals = manifest.get('totals', {})
                if totals.get('transcript_count', 0) == 0:
                    needs_rearchive = True
            except (json.JSONDecodeError, KeyError):
                needs_rearchive = True
        else:
            needs_rearchive = True

        if needs_rearchive:
            try:
                result = rearchive_mission(mission_id)
                if result['success'] and result['transcripts_archived'] > 0:
                    results['rearchived'] += 1
                    results['details'].append({
                        'mission_id': mission_id,
                        'transcripts': result['transcripts_archived'],
                        'tokens': result.get('manifest', {}).get('totals', {}).get('total_tokens', 0)
                    })
            except Exception as e:
                results['errors'].append(f"{mission_id}: {str(e)}")

    return results
