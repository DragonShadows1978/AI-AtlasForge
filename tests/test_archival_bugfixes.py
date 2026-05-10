from datetime import datetime, timezone

from af_engine.core import archival


def test_archive_missing_created_at_is_warning_not_error(tmp_path, monkeypatch):
    monkeypatch.setattr(archival, "TRANSCRIPTS_ARCHIVE_DIR", tmp_path)
    monkeypatch.setattr(archival, "_find_transcripts_in_window", lambda *args, **kwargs: [])

    result = archival.archive_mission_transcripts({
        "mission_id": "mission_ok",
        "created_at": None,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })

    assert result["success"] is True
    assert result["errors"] == []
    assert result["warnings"]
    assert "created_at" in result["warnings"][0]


def test_archive_rejects_non_string_mission_id():
    result = archival.archive_mission_transcripts({
        "mission_id": 123,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    assert result["success"] is False
    assert "mission_id must be a string" in result["errors"][0]


def test_archive_rejects_bidi_mission_id():
    result = archival.archive_mission_transcripts({
        "mission_id": "mission_\u202etest",
        "created_at": datetime.now(timezone.utc).isoformat(),
    })

    assert result["success"] is False
    assert "bidi controls" in result["errors"][0]
