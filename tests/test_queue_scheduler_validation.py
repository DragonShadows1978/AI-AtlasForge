import builtins
import json
import sys
import types
from datetime import timezone

from flask import Flask


def _build_queue_app(monkeypatch, tmp_path):
    from dashboard_modules import queue_scheduler

    queue_path = tmp_path / "mission_queue.json"
    monkeypatch.setattr(queue_scheduler, "MISSION_QUEUE_PATH", queue_path)
    monkeypatch.setattr(queue_scheduler, "LLM_PROVIDER_PATH", tmp_path / "llm_provider.json")
    monkeypatch.setattr(queue_scheduler, "_emit_queue_update", lambda *_args, **_kwargs: None)

    app = Flask(__name__)
    app.register_blueprint(queue_scheduler.queue_scheduler_bp)
    return app, queue_scheduler, queue_path


def test_queue_add_defaults_null_mission_type_to_full_rd(monkeypatch, tmp_path):
    app, _queue_scheduler, queue_path = _build_queue_app(monkeypatch, tmp_path)

    resp = app.test_client().post(
        "/api/queue/add",
        data=json.dumps({"problem_statement": "Queue me", "mission_type": None}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["entry"]["mission_type"] == "full_rd"
    stored = json.loads(queue_path.read_text())
    assert stored["missions"][0]["mission_type"] == "full_rd"


def test_queue_add_rejects_invalid_mission_type(monkeypatch, tmp_path):
    app, _queue_scheduler, _queue_path = _build_queue_app(monkeypatch, tmp_path)

    resp = app.test_client().post(
        "/api/queue/add",
        data=json.dumps({"problem_statement": "Queue me", "mission_type": "bad_profile"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert "Invalid mission_type" in resp.get_json()["error"]


def test_queue_mission_type_fallback_validates_when_profiles_import_fails(monkeypatch):
    from dashboard_modules import queue_scheduler

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "af_engine.mission_profiles":
            raise ImportError("simulated missing profile module")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert queue_scheduler._resolve_queue_mission_type({"mission_type": "bug_hunt"}) == "bug_hunt"

    try:
        queue_scheduler._resolve_queue_mission_type({"mission_type": "bad_profile"})
    except ValueError as exc:
        assert "Invalid mission_type" in str(exc)
    else:
        raise AssertionError("bad_profile should be rejected")


def test_queue_datetime_parser_normalizes_to_utc():
    from dashboard_modules import queue_scheduler

    zulu = queue_scheduler._parse_queue_datetime("2026-05-10T07:00:00Z")
    naive = queue_scheduler._parse_queue_datetime("2026-05-10T07:00:00")

    assert zulu.tzinfo is not None
    assert zulu.astimezone(timezone.utc).isoformat() == "2026-05-10T07:00:00+00:00"
    assert naive.tzinfo is timezone.utc


def test_queue_health_score_degrades_without_early_saturation():
    from dashboard_modules import queue_scheduler

    assert queue_scheduler._queue_health_score(0) == 100
    seven_issue_score = queue_scheduler._queue_health_score(7)
    thirty_issue_score = queue_scheduler._queue_health_score(30)

    assert 0 < thirty_issue_score < seven_issue_score < 100
    assert queue_scheduler._queue_health_score(-1) == 100
    assert queue_scheduler._queue_health_score("bad") == 100


def test_kb_recommendation_queue_ids_do_not_collide(monkeypatch, tmp_path):
    app, _queue_scheduler, queue_path = _build_queue_app(monkeypatch, tmp_path)

    fake_kb_module = types.ModuleType("mission_knowledge_base")

    class _KB:
        _recommendations = {
            "kbrec": {
                "title": "KB Recommendation",
                "problem_statement": "Investigate the KB recommendation",
                "complexity_budget": 3,
            }
        }

    fake_kb_module.get_knowledge_base = lambda: _KB()
    monkeypatch.setitem(sys.modules, "mission_knowledge_base", fake_kb_module)

    client = app.test_client()
    payload = {"recommendation_id": "kbrec"}

    first = client.post(
        "/api/queue/from-kb-recommendation",
        data=json.dumps(payload),
        content_type="application/json",
    )
    second = client.post(
        "/api/queue/from-kb-recommendation",
        data=json.dumps(payload),
        content_type="application/json",
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json()["entry"]["id"] != second.get_json()["entry"]["id"]
    stored = json.loads(queue_path.read_text())
    ids = [item["id"] for item in stored["missions"]]
    assert len(ids) == len(set(ids))
