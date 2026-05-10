import json

from af_engine.stages import testing_runner as module
from af_engine.stages.testing_runner import TestingRunner, TestingRunnerConfig, TestingLaneResult


def _config(tmp_path):
    workspace = tmp_path / "workspace"
    artifacts = workspace / "artifacts"
    tests = workspace / "tests"
    tests.mkdir(parents=True)
    return TestingRunnerConfig(
        mission_id="mission-test",
        mission={"mission_id": "mission-test", "llm_provider": "codex"},
        mission_text="Implement and verify the feature.",
        workspace_dir=workspace,
        artifacts_dir=artifacts,
        tests_dir=tests,
        max_lanes=6,
        timeout_minutes=5,
    )


def test_testing_runner_injects_required_lanes_and_reports_completion(tmp_path, monkeypatch):
    calls = []

    def fake_invoke(prompt, **kwargs):
        calls.append(prompt)
        if "TESTING lead agent" in prompt:
            return json.dumps({
                "testing_lanes": [
                    {
                        "lane_type": "self_tests",
                        "focus_area": "Baseline",
                        "prompt": "Run baseline tests.",
                    }
                ]
            }), 0.1
        lane_type = "custom"
        for marker in ("self_tests", "injection", "boundary", "logic", "mutation", "spec_alignment"):
            if f"Lane type: {marker}" in prompt:
                lane_type = marker
                break
        return json.dumps({
            "lane_type": lane_type,
            "status": "passed",
            "summary": f"{lane_type} passed",
            "issues": [],
            "commands_run": ["pytest -q"],
            "files_examined": ["tests/test_example.py"],
            "confidence": 0.8,
        }), 0.1

    monkeypatch.setattr(module, "invoke_claude", fake_invoke)
    monkeypatch.setattr(module, "ModelType", None)

    result = TestingRunner(_config(tmp_path)).run()

    assert result["status"] == "tests_passed"
    assert result["adversarial_testing"]["red_team_agent_count"] == 3
    assert result["adversarial_testing"]["red_team_completion"]["agent_reports_collected"] == 3
    assert result["adversarial_testing"]["red_team_completion"]["all_agents_completed"] is True
    assert result["mutation_testing"]["status"] == "passed"
    assert (tmp_path / "workspace" / "artifacts" / "testing" / "lead_plan.json").exists()
    assert (tmp_path / "workspace" / "artifacts" / "test_results.md").exists()
    assert any("TESTING lead agent" in call for call in calls)
    mutation_call_index = next(i for i, call in enumerate(calls) if "Lane type: mutation" in call)
    assert "Prior TESTING sub-stage results:" in calls[mutation_call_index]
    assert "self_tests passed" in calls[mutation_call_index]
    assert all("Lane type: mutation" not in call for call in calls[1:mutation_call_index])


def test_testing_runner_blocks_mutation_when_baseline_does_not_pass(tmp_path, monkeypatch):
    lane_calls = []

    def fake_invoke(prompt, **kwargs):
        if "TESTING lead agent" in prompt:
            return json.dumps({"testing_lanes": []}), 0.1
        lane_calls.append(prompt)
        lane_type = "custom"
        for marker in ("self_tests", "injection", "boundary", "logic", "mutation", "spec_alignment"):
            if f"Lane type: {marker}" in prompt:
                lane_type = marker
                break
        status = "failed" if lane_type == "self_tests" else "passed"
        return json.dumps({
            "lane_type": lane_type,
            "status": status,
            "summary": f"{lane_type} {status}",
            "issues": [],
            "commands_run": ["pytest -q"],
            "files_examined": [],
            "confidence": 0.5,
        }), 0.1

    monkeypatch.setattr(module, "invoke_claude", fake_invoke)
    monkeypatch.setattr(module, "ModelType", None)

    result = TestingRunner(_config(tmp_path)).run()

    assert result["status"] == "tests_error"
    assert result["mutation_testing"]["status"] == "blocked"
    assert "baseline self-test" in result["mutation_testing"]["summary"]
    assert all("Lane type: mutation" not in call for call in lane_calls)


def test_testing_runner_aggregates_findings_as_failed(tmp_path):
    runner = TestingRunner(_config(tmp_path))
    lanes = [
        {"lane_id": "mission-test_test_0", "lane_type": "injection", "focus_area": "Injection"},
        {"lane_id": "mission-test_test_1", "lane_type": "boundary", "focus_area": "Boundary"},
        {"lane_id": "mission-test_test_2", "lane_type": "logic", "focus_area": "Logic"},
        {"lane_id": "mission-test_test_3", "lane_type": "mutation", "focus_area": "Mutation"},
    ]
    results = [
        TestingLaneResult("mission-test_test_0", "injection", "Injection", "passed", 1.0),
        TestingLaneResult(
            "mission-test_test_1",
            "boundary",
            "Boundary",
            "failed",
            1.0,
            issues=[{
                "severity": "high",
                "title": "Boundary value crashes",
                "evidence": "pytest reproduced crash",
                "recommendation": "Add input validation",
            }],
        ),
        TestingLaneResult("mission-test_test_2", "logic", "Logic", "passed", 1.0),
        TestingLaneResult("mission-test_test_3", "mutation", "Mutation", "passed", 1.0),
    ]

    result = runner._aggregate(lanes, results, 4.0)

    assert result["status"] == "tests_failed"
    assert "Boundary value crashes" in result["success_criteria_failed"]
    assert result["adversarial_testing"]["red_team_completion"]["agent_reports_collected"] == 3
    assert result["mutation_testing"]["status"] == "passed"
