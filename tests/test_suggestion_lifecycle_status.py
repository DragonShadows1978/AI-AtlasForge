import json

import pytest

from suggestion_lifecycle import mark_suggestion_status, reconcile_suggestion_statuses
from suggestion_storage import SQLiteSuggestionStorage


def test_suggestion_status_defaults_and_filters(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")

    open_id = storage.add({"mission_title": "Open Mission"})
    queued_id = storage.add({"mission_title": "Queued Mission", "status": "queued"})
    deprecated_id = storage.add({"mission_title": "Deprecated Mission", "status": "deprecated"})
    proposed_id = storage.add({"mission_title": "Proposed Mission", "status": "proposed"})
    rejected_id = storage.add({"mission_title": "Rejected Mission", "status": "rejected"})

    assert storage.get_by_id(open_id)["status"] == "open"
    assert [item["id"] for item in storage.get_filtered(status="open")] == [open_id]
    assert [item["id"] for item in storage.get_filtered(status="deprecated")] == [deprecated_id]
    assert [item["id"] for item in storage.get_filtered(status="proposed")] == [proposed_id]
    assert [item["id"] for item in storage.get_filtered(status="rejected")] == [rejected_id]

    assert storage.update(queued_id, {"status": "completed"})
    assert storage.get_by_id(queued_id)["status"] == "completed"
    assert storage.count(status="completed") == 1
    assert storage.count(status="deprecated") == 1
    assert storage.count(status="proposed") == 1
    assert storage.count(status="rejected") == 1


def test_suggestion_status_rejects_invalid_values(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")

    with pytest.raises(ValueError):
        storage.add({"mission_title": "Bad Mission", "status": "done"})

    rec_id = storage.add({"mission_title": "Good Mission"})
    with pytest.raises(ValueError):
        storage.update(rec_id, {"status": "done"})


def test_reconcile_returns_stale_queued_suggestions_to_open(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    rec_id = storage.add({"mission_title": "Queued Mission", "status": "queued"})
    mission_path = tmp_path / "mission.json"
    queue_path = tmp_path / "queue.json"
    mission_path.write_text("{}")
    queue_path.write_text(json.dumps({"missions": []}))

    result = reconcile_suggestion_statuses(
        storage=storage,
        mission_path=mission_path,
        queue_path=queue_path,
    )

    assert result["opened"] == 1
    assert storage.get_by_id(rec_id)["status"] == "open"


def test_reconcile_marks_active_suggestion_completed_or_open(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    rec_id = storage.add({"mission_title": "Active Mission", "status": "queued"})
    mission_path = tmp_path / "mission.json"
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(json.dumps({"missions": []}))

    mission_path.write_text(json.dumps({
        "mission_id": "mission_complete_1",
        "current_stage": "COMPLETE",
        "source_recommendation_id": rec_id,
    }))
    reconcile_suggestion_statuses(storage=storage, mission_path=mission_path, queue_path=queue_path)
    completed = storage.get_by_id(rec_id)
    assert completed["status"] == "completed"
    assert completed["accepted_mission_id"] == "mission_complete_1"
    assert completed["completed_at"]
    assert completed["closed_reason"] == "completed"

    storage.update(rec_id, {"status": "queued"})
    mission_path.write_text(json.dumps({
        "mission_id": "mission_failed_1",
        "current_stage": "COMPLETE",
        "source_recommendation_id": rec_id,
        "failed": True,
    }))
    reconcile_suggestion_statuses(storage=storage, mission_path=mission_path, queue_path=queue_path)
    reopened = storage.get_by_id(rec_id)
    assert reopened["status"] == "open"
    assert reopened["accepted_mission_id"] == "mission_failed_1"
    assert reopened["reopened_at"]
    assert reopened["closed_reason"] == "failed"


def test_lifecycle_metadata_tracks_unique_accepted_mission_id(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    first_id = storage.add({"mission_title": "First Mission"})
    second_id = storage.add({"mission_title": "Second Mission"})

    assert mark_suggestion_status(
        first_id,
        "queued",
        storage=storage,
        mission_id="mission_unique_1",
        closed_reason="mission_control",
    )
    queued = storage.get_by_id(first_id)
    assert queued["accepted_mission_id"] == "mission_unique_1"
    assert queued["queued_at"]
    assert queued["closed_reason"] == "mission_control"

    assert mark_suggestion_status(
        first_id,
        "completed",
        storage=storage,
        mission_id="mission_unique_1",
    )
    completed = storage.get_by_id(first_id)
    assert completed["status"] == "completed"
    assert completed["completed_at"]

    assert mark_suggestion_status(
        first_id,
        "deprecated",
        storage=storage,
        mission_id="mission_unique_1",
    )
    deprecated = storage.get_by_id(first_id)
    assert deprecated["status"] == "deprecated"
    assert deprecated["closed_reason"] == "deprecated"

    assert not mark_suggestion_status(
        second_id,
        "queued",
        storage=storage,
        mission_id="mission_unique_1",
        closed_reason="mission_control",
    )
    assert storage.get_by_id(second_id)["accepted_mission_id"] is None
