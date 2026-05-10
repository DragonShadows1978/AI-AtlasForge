from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from flask import Flask


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

from dashboard_modules.atlasforge import (  # noqa: E402
    _validate_route_mission_id,
    register_archival_routes,
)


@pytest.mark.parametrize(
    "mission_id",
    [
        "../outside",
        "nested/path",
        "nested\\path",
        "",
        "   ",
        "mission_\x00bad",
        "mission_\n_bad",
        "mission_\u202ebad",
        "x" * 256,
        "mission bad",
    ],
)
def test_validate_route_mission_id_rejects_unsafe_values(mission_id):
    ok, reason = _validate_route_mission_id(mission_id)

    assert ok is False
    assert reason


def test_validate_route_mission_id_accepts_safe_values():
    assert _validate_route_mission_id("mission_abc-123") == (True, "")


def test_rearchive_route_rejects_invalid_mission_id():
    app = Flask(__name__)
    register_archival_routes(app)
    client = app.test_client()

    response = client.post("/api/rearchive/mission/mission_%E2%80%AEbad")

    assert response.status_code == 400
    assert "mission_id" in response.get_json()["error"]


def test_rearchive_route_sanitizes_internal_exception(monkeypatch):
    app = Flask(__name__)
    register_archival_routes(app)
    client = app.test_client()

    fake_af_engine = types.SimpleNamespace(
        rearchive_mission=lambda mission_id: (_ for _ in ()).throw(
            RuntimeError("/secret/path/token")
        )
    )
    monkeypatch.setitem(sys.modules, "af_engine", fake_af_engine)

    response = client.post("/api/rearchive/mission/mission_ok")

    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "rearchive failed"
    assert "/secret" not in response.get_data(as_text=True)


def test_populate_decision_graph_route_sanitizes_internal_exception(monkeypatch):
    app = Flask(__name__)
    register_archival_routes(app)
    client = app.test_client()

    fake_exploration_hooks = types.SimpleNamespace(
        populate_from_mission_archive=lambda mission_id: (_ for _ in ()).throw(
            RuntimeError("/secret/graph.db")
        )
    )
    monkeypatch.setitem(sys.modules, "exploration_hooks", fake_exploration_hooks)

    response = client.post("/api/populate-decision-graph/mission_ok")

    assert response.status_code == 500
    body = response.get_json()
    assert body["error"] == "populate decision graph failed"
    assert body["mission_id"] == "mission_ok"
    assert "/secret" not in response.get_data(as_text=True)
