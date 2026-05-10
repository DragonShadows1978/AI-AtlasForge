from pathlib import Path
from types import SimpleNamespace

import atlasforge_conductor as conductor


def test_get_mission_workspace_rejects_non_path_value(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    monkeypatch.setattr(conductor, "WORKSPACE_DIR", fallback)
    controller = SimpleNamespace(mission={"mission_workspace": ["not", "a", "path"]})

    assert conductor.get_mission_workspace(controller) == fallback


def test_get_mission_workspace_rejects_file_path(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    fallback.mkdir()
    not_dir = tmp_path / "file.txt"
    not_dir.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(conductor, "WORKSPACE_DIR", fallback)
    controller = SimpleNamespace(mission={"mission_workspace": str(not_dir)})

    assert conductor.get_mission_workspace(controller) == fallback


def test_get_mission_workspace_accepts_existing_directory(monkeypatch, tmp_path):
    fallback = tmp_path / "fallback"
    workspace = tmp_path / "workspace"
    fallback.mkdir()
    workspace.mkdir()
    monkeypatch.setattr(conductor, "WORKSPACE_DIR", fallback)
    controller = SimpleNamespace(mission={"mission_workspace": str(workspace)})

    assert conductor.get_mission_workspace(controller) == workspace.resolve()


def test_invoke_llm_rejects_invalid_cwd_before_spawn(tmp_path):
    missing = tmp_path / "missing"

    response, error = conductor.invoke_llm("hello", cwd=missing)

    assert response is None
    assert error == "invalid_cwd"
