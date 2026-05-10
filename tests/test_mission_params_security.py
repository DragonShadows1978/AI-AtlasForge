#!/usr/bin/env python3

import sys
from pathlib import Path

import pytest


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

from dashboard_modules import mission_params  # noqa: E402


def test_resolve_mission_dir_rejects_traversal(tmp_path):
    mission_params.init_mission_params_blueprint(
        tmp_path / "mission.json",
        tmp_path / "missions",
        None,
    )

    with pytest.raises(ValueError):
        mission_params._resolve_mission_dir("../outside")


def test_resolve_mission_dir_rejects_overlong_mission_id(tmp_path):
    mission_params.init_mission_params_blueprint(
        tmp_path / "mission.json",
        tmp_path / "missions",
        None,
    )

    with pytest.raises(ValueError, match="255"):
        mission_params._resolve_mission_dir("m" * 256)


def test_resolve_mission_dir_allows_child(tmp_path):
    missions_dir = tmp_path / "missions"
    mission_params.init_mission_params_blueprint(
        tmp_path / "mission.json",
        missions_dir,
        None,
    )

    assert mission_params._resolve_mission_dir("mission_abc123") == (
        missions_dir / "mission_abc123"
    ).resolve()


def test_parameter_audit_route_rejects_overlong_mission_id(tmp_path):
    app = __import__("flask").Flask(__name__)
    app.register_blueprint(mission_params.mission_params_bp)
    mission_params.init_mission_params_blueprint(
        tmp_path / "mission.json",
        tmp_path / "missions",
        None,
    )

    response = app.test_client().get(
        f"/api/mission/parameter-audit/{'m' * 256}"
    )

    assert response.status_code == 400
    assert "255" in response.get_json()["error"]
