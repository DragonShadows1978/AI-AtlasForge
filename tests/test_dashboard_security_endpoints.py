import sys
import types
from pathlib import Path

from flask import Flask


def test_decision_graph_export_sanitizes_content_disposition(monkeypatch):
    from dashboard_modules.recovery import recovery_bp

    fake_decision_graph = types.ModuleType("decision_graph")

    class _Logger:
        def get_mission_graph(self, _mission_id):
            return {"nodes": [], "edges": []}

    fake_decision_graph.get_decision_logger = lambda: _Logger()
    monkeypatch.setitem(sys.modules, "decision_graph", fake_decision_graph)

    app = Flask(__name__)
    app.register_blueprint(recovery_bp)
    client = app.test_client()

    resp = client.get("/api/decision-graph/bad%0D%0AX-Evil:%20yes/export?format=csv")

    assert resp.status_code == 200
    disposition = resp.headers["Content-Disposition"]
    assert "\r" not in disposition
    assert "\n" not in disposition
    assert resp.headers.get("X-Evil") is None
    assert disposition.endswith("_decision_graph.csv")


def test_atlasforge_file_preview_restricts_to_repo_root(monkeypatch, tmp_path):
    import interactive_graph_api

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    allowed_file = repo_root / "allowed.txt"
    allowed_file.write_text("inside repo\n")

    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("secret\n")

    monkeypatch.setattr(interactive_graph_api, "BASE_DIR", repo_root)

    app = Flask(__name__)
    interactive_graph_api.register_interactive_graph_routes(app)
    client = app.test_client()

    allowed = client.get(
        "/api/atlasforge/file-preview",
        query_string={"path": str(allowed_file), "lines": "5"},
    )
    assert allowed.status_code == 200
    allowed_body = allowed.get_json()
    assert allowed_body["content"] == "inside repo\n"

    denied = client.get(
        "/api/atlasforge/file-preview",
        query_string={"path": str(outside_file), "lines": "5"},
    )
    assert denied.status_code == 200
    denied_body = denied.get_json()
    assert denied_body["error"] == "Path not allowed"
    assert denied_body["content"] == ""
