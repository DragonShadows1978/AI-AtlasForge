import json
from datetime import datetime, timedelta


def test_update_investigation_status_persists_completion_timing(monkeypatch, tmp_path):
    import investigation_engine

    state_path = tmp_path / "investigation_state.json"
    monkeypatch.setattr(investigation_engine, "STATE_DIR", tmp_path)
    monkeypatch.setattr(investigation_engine, "INVESTIGATION_STATE_PATH", state_path)

    investigation_engine.save_investigation_state({
        "current": {
            "investigation_id": "inv_timing",
            "query": "timing test",
            "status": "synthesizing",
            "started_at": "2026-05-07T10:00:00",
        },
        "history": [],
    })

    investigation_engine.update_investigation_status(
        "inv_timing",
        investigation_engine.InvestigationStatus.COMPLETED,
        {
            "completed_at": "2026-05-07T10:03:12",
            "elapsed_seconds": 192.4,
            "report_path": "/tmp/report.md",
        },
    )

    state = json.loads(state_path.read_text())
    assert state["current"]["completed_at"] == "2026-05-07T10:03:12"
    assert state["current"]["elapsed_seconds"] == 192.4


def test_enrich_investigation_derives_missing_legacy_timing(monkeypatch, tmp_path):
    from dashboard_modules import investigation as mod

    monkeypatch.setattr(mod, "BASE_DIR", tmp_path)
    monkeypatch.setattr(mod, "STATE_DIR", tmp_path / "state")
    (tmp_path / "state").mkdir()

    started_at = datetime(2026, 5, 7, 10, 0, 0)
    last_updated = started_at + timedelta(seconds=192)
    enriched = mod._enrich_investigation_with_metadata({
        "investigation_id": "inv_legacy",
        "query": "legacy timing",
        "status": "completed",
        "started_at": started_at.isoformat(),
        "last_updated": last_updated.isoformat(),
    })

    assert enriched["elapsed_seconds"] == 192
    assert enriched["elapsed_display"] == "3m 12s"
    assert enriched["completed_at"] == last_updated.isoformat()
