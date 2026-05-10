#!/usr/bin/env python3
"""Lifecycle helpers for Mission Suggestions.

Mission suggestions remain in SQLite after they are selected. Their `status`
controls whether the suggestion widget renders them.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Set

from atlasforge_config import MISSION_QUEUE_PATH, STATE_DIR

logger = logging.getLogger(__name__)

VALID_SUGGESTION_STATUSES = frozenset({
    "open", "queued", "completed", "deprecated", "proposed", "rejected"
})
FAILED_STAGES = frozenset({"FAILED", "ERROR", "HALTED", "CANCELLED", "CANCELED"})


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            with open(path, "r") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception as exc:
        logger.debug("suggestion_lifecycle: failed to read %s: %s", path, exc)
    return {}


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    try:
        from io_utils import atomic_write_json
        atomic_write_json(path, data)
    except Exception:
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2)


def _get_storage(storage=None):
    if storage is not None:
        return storage
    from suggestion_storage import get_storage
    return get_storage()


def source_recommendation_id_from_mission(mission: Dict[str, Any]) -> Optional[str]:
    if not isinstance(mission, dict):
        return None
    source_id = mission.get("source_recommendation_id")
    if source_id:
        return str(source_id)

    metadata = mission.get("metadata") if isinstance(mission.get("metadata"), dict) else {}
    metadata_source_id = metadata.get("source_recommendation_id") or metadata.get("source_id")
    if metadata_source_id and metadata.get("source") in (None, "recommendation", "mission_suggestion"):
        return str(metadata_source_id)
    return None


def mission_id_from_mission(mission: Dict[str, Any]) -> Optional[str]:
    if not isinstance(mission, dict):
        return None
    mission_id = mission.get("mission_id")
    return str(mission_id) if mission_id else None


def mark_suggestion_status(
    rec_id: Any,
    status: str,
    storage=None,
    mission_id: Any = None,
    closed_reason: Optional[str] = None,
) -> bool:
    if not rec_id:
        return False
    if status not in VALID_SUGGESTION_STATUSES:
        raise ValueError(f"Invalid suggestion status: {status!r}")
    try:
        store = _get_storage(storage)
        rec_id = str(rec_id)
        current = store.get_by_id(rec_id) or {}
        previous_status = current.get("status", "open")
        now = _utc_now_iso()
        updates: Dict[str, Any] = {}

        if previous_status != status:
            updates["status"] = status
        if mission_id:
            mission_id = str(mission_id)
            if current.get("accepted_mission_id") != mission_id:
                updates["accepted_mission_id"] = mission_id

        if status == "queued":
            if previous_status != "queued" or not current.get("queued_at"):
                updates["queued_at"] = now
            if closed_reason and current.get("closed_reason") != closed_reason:
                updates["closed_reason"] = closed_reason
        elif status == "completed":
            if previous_status != "completed" or not current.get("completed_at"):
                updates["completed_at"] = now
            reason = closed_reason or "completed"
            if current.get("closed_reason") != reason:
                updates["closed_reason"] = reason
        elif status == "deprecated":
            reason = closed_reason or "deprecated"
            if current.get("closed_reason") != reason:
                updates["closed_reason"] = reason
        elif status == "rejected":
            reason = closed_reason or "rejected"
            if current.get("closed_reason") != reason:
                updates["closed_reason"] = reason
        elif status == "proposed":
            if closed_reason and current.get("closed_reason") != closed_reason:
                updates["closed_reason"] = closed_reason
        elif status == "open":
            if previous_status != "open" or closed_reason:
                updates["reopened_at"] = now
            if closed_reason and current.get("closed_reason") != closed_reason:
                updates["closed_reason"] = closed_reason

        if not updates:
            return False
        updates["last_edited_at"] = now
        return bool(store.update(rec_id, updates))
    except Exception:
        logger.warning(
            "suggestion_lifecycle: failed to mark suggestion %s as %s",
            rec_id,
            status,
            exc_info=True,
        )
        return False


def _queued_recommendation_ids(queue_path: Path) -> Set[str]:
    queue = _load_json(queue_path)
    missions = queue.get("missions")
    if not isinstance(missions, list):
        missions = queue.get("queue") if isinstance(queue.get("queue"), list) else []

    ids: Set[str] = set()
    for item in missions:
        if not isinstance(item, dict):
            continue
        if item.get("source") != "recommendation":
            continue
        source_id = item.get("source_id")
        if source_id:
            ids.add(str(source_id))
    return ids


def reconcile_suggestion_statuses(
    storage=None,
    mission_path: Optional[Path] = None,
    queue_path: Optional[Path] = None,
) -> Dict[str, int]:
    """Repair queued/open/completed suggestions from current mission and queue state."""
    store = _get_storage(storage)
    mission_path = mission_path or (STATE_DIR / "mission.json")
    queue_path = queue_path or MISSION_QUEUE_PATH

    counts = {"opened": 0, "queued": 0, "completed": 0}
    queue_ids = _queued_recommendation_ids(queue_path)
    for rec_id in queue_ids:
        if mark_suggestion_status(rec_id, "queued", store, closed_reason="queued"):
            counts["queued"] += 1

    mission = _load_json(mission_path)
    active_id = source_recommendation_id_from_mission(mission)
    active_mission_id = mission_id_from_mission(mission)
    active_queued_ids: Set[str] = set()
    if active_id:
        stage = str(mission.get("current_stage") or "").upper()
        if stage in FAILED_STAGES or mission.get("failed") is True:
            if mark_suggestion_status(
                active_id,
                "open",
                store,
                mission_id=active_mission_id,
                closed_reason="failed",
            ):
                counts["opened"] += 1
        elif stage == "COMPLETE":
            if mark_suggestion_status(
                active_id,
                "completed",
                store,
                mission_id=active_mission_id,
                closed_reason="completed",
            ):
                counts["completed"] += 1
        else:
            active_queued_ids.add(active_id)
            if mark_suggestion_status(
                active_id,
                "queued",
                store,
                mission_id=active_mission_id,
                closed_reason="mission_active",
            ):
                counts["queued"] += 1

    still_queued = queue_ids | active_queued_ids
    try:
        queued_suggestions = store.get_filtered(status="queued")
    except TypeError:
        queued_suggestions = [
            item for item in store.get_all()
            if item.get("status", "open") == "queued"
        ]

    for suggestion in queued_suggestions:
        rec_id = str(suggestion.get("id") or "")
        if rec_id and rec_id not in still_queued:
            if mark_suggestion_status(rec_id, "open", store, closed_reason="stale_queued"):
                counts["opened"] += 1

    return counts


def mark_active_suggestion_open_if_incomplete(
    storage=None,
    mission_path: Optional[Path] = None,
) -> bool:
    """Return an active recommendation-sourced mission to the open pool on stop."""
    mission_path = mission_path or (STATE_DIR / "mission.json")
    mission = _load_json(mission_path)
    rec_id = source_recommendation_id_from_mission(mission)
    if not rec_id:
        return False
    stage = str(mission.get("current_stage") or "").upper()
    if stage == "COMPLETE":
        return False
    opened = mark_suggestion_status(
        rec_id,
        "open",
        storage,
        mission_id=mission_id_from_mission(mission),
        closed_reason="stopped",
    )
    if opened:
        mission["failed"] = True
        mission["suggestion_status_returned"] = "open"
        try:
            _write_json(mission_path, mission)
        except Exception:
            logger.debug("suggestion_lifecycle: failed to persist inactive mission marker", exc_info=True)
    return opened
