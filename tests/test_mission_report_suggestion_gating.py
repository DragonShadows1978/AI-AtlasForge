import suggestion_storage
from af_engine.integrations.mission_report import MissionReportIntegration
from suggestion_storage import SQLiteSuggestionStorage


def _storage(tmp_path):
    storage = SQLiteSuggestionStorage(db_path=tmp_path / "suggestions.sqlite")
    suggestion_storage._storage_instance = storage
    return storage


def teardown_function():
    suggestion_storage._storage_instance = None


def test_non_full_mission_suppresses_expansion_but_keeps_bug_and_debt(tmp_path):
    storage = _storage(tmp_path)
    integration = MissionReportIntegration(mission_logs_dir=tmp_path / "logs")

    integration._save_continuation_manifest(
        [
            {"category": "EXPANSION", "title": "[EXPAND] Add more modes", "description": "new idea"},
            {"category": "BUGFIX", "title": "[BUGFIX] Renderer", "description": "fix bug", "severity": "HIGH"},
            {"category": "TECH_DEBT", "title": "[TECH DEBT] Cleanup", "description": "cleanup"},
        ],
        "mission_source",
        "summary",
        source_mission_profile="bug_hunt",
    )

    items = storage.get_all()
    assert {item["classification"] for item in items} == {"BUGFIX", "TECH_DEBT"}
    assert {item["status"] for item in items} == {"open"}


def test_full_rd_expansion_is_saved_as_proposal(tmp_path):
    storage = _storage(tmp_path)
    integration = MissionReportIntegration(mission_logs_dir=tmp_path / "logs")

    integration._save_continuation_manifest(
        [{"category": "EXPANSION", "title": "[EXPAND] New workflow", "description": "proposal"}],
        "mission_source",
        "summary",
        source_mission_profile="full_rd",
    )

    item = storage.get_all()[0]
    assert item["classification"] == "EXPANSION"
    assert item["status"] == "proposed"


def test_plan_only_completion_requires_build_approval(tmp_path):
    storage = _storage(tmp_path)
    integration = MissionReportIntegration(mission_logs_dir=tmp_path / "logs")
    plan_path = tmp_path / "implementation_plan.md"
    plan_path.write_text("Build the thing in phases.")

    integration._save_continuation_manifest(
        [{"category": "COMPLETION", "title": "[BUILD] Approved plan", "description": "build it"}],
        "mission_source",
        "summary",
        source_mission_profile="plan_only",
        source_plan_path=str(plan_path),
    )

    item = storage.get_all()[0]
    assert item["classification"] == "COMPLETION"
    assert item["execution_profile"] == "build_only"
    assert item["status"] == "open"
    assert item["requires_user_build_approval"] is True
    assert item["build_approval_status"] == "pending"
    assert item["source_plan_path"] == str(plan_path)
