from __future__ import annotations

import warnings

import pytest

from suggestion_storage import SQLiteSuggestionStorage


def test_update_rejects_null_execution_profile(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    rec_id = storage.add({"mission_title": "Profile update target"})

    with pytest.raises(ValueError, match="execution_profile"):
        storage.update(rec_id, {"execution_profile": None})

    assert storage.get_by_id(rec_id)["execution_profile"]


def test_update_all_increments_write_count_by_replaced_rows(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    storage.add({"mission_title": "Existing row"})
    before = storage._write_count

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        count = storage.update_all([
            {"id": "new_one", "mission_title": "New One"},
            {"id": "new_two", "mission_title": "New Two"},
        ])

    assert count == 2
    assert storage._write_count == before + 2


def test_schema_ensure_reaches_current_user_version(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")

    with storage._get_connection() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 8


def test_add_infers_project_name_from_suggestion_text(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")

    rec_id = storage.add({
        "mission_title": "Stick Figure Fighter sprite cleanup",
        "mission_description": "Fix renderer issues in Stick Figure Fighter.",
    })

    stored = storage.get_by_id(rec_id)
    assert stored["project_name"] == "Stick Figure Fighter"
    assert stored["project_slug"] == "stick-figure-fighter"
    assert stored["project_source"] == "inferred"


def test_get_projects_returns_project_counts(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    storage.add({"mission_title": "AtlasForge dashboard work", "project_name": "AI-AtlasForge"})
    storage.add({"mission_title": "Another AtlasForge task", "project_name": "AtlasForge"})
    storage.add({"mission_title": "Lexibank task", "project_name": "Lexibank Experiments"})

    projects = storage.get_projects(status="open")
    by_slug = {p["project_slug"]: p for p in projects}

    assert by_slug["ai-atlasforge"]["project_name"] == "AI-AtlasForge"
    assert by_slug["ai-atlasforge"]["count"] == 2
    assert by_slug["lexibank-experiments"]["count"] == 1
