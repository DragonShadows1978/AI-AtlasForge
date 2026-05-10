"""
Tests for AtlasForge stage-guard tools exposed by WebProxy/mcp_server.py.
"""

import json
import pytest


def _prepare_root(tmp_path, stage="PLANNING", provider="codex"):
    state = tmp_path / "state"
    state.mkdir()
    (state / "mission.json").write_text(json.dumps({
        "mission_id": "mission_test",
        "current_stage": stage,
        "mission_workspace": str(tmp_path / "workspace" / "mission_test"),
    }))
    (state / "llm_provider.json").write_text(json.dumps({
        "provider": provider,
    }))


def test_submit_plan_writes_default_planning_artifact(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    result = handle_tool_call("AtlasForgeSubmitPlan", {
        "plan": {
            "summary": "Do the thing",
            "target_files": ["app.py"],
        },
    })

    parsed = json.loads(result)
    assert parsed["ok"] is True
    assert parsed["path"] == "workspace/mission_test/artifacts/implementation_plan.md"

    artifact = (tmp_path / "workspace" / "mission_test" / "artifacts" / "implementation_plan.md").read_text()
    assert "# Implementation Plan" in artifact
    assert "Do the thing" in artifact
    assert '"summary": "Do the thing"' in artifact


def test_stage_guard_prefers_codex_runtime_context(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    monkeypatch.setenv("ATLASFORGE_ACTIVE_PROVIDER", "codex")
    monkeypatch.setenv("ATLASFORGE_ACTIVE_STAGE", "PLANNING")
    monkeypatch.setenv("ATLASFORGE_ACTIVE_MISSION_ID", "mission_env")
    _prepare_root(tmp_path, stage="COMPLETE", provider="claude")

    from WebProxy.mcp_server import handle_tool_call

    result = handle_tool_call("AtlasForgeSubmitPlan", {
        "plan": {
            "summary": "runtime context wins",
        },
    })

    parsed = json.loads(result)
    assert parsed["ok"] is True
    assert parsed["stage"] == "PLANNING"
    assert parsed["provider"] == "codex"
    assert parsed["path"] == "workspace/mission_env/artifacts/implementation_plan.md"


def test_stage_guard_uses_codex_context_file_when_env_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="COMPLETE", provider="codex")
    context_path = tmp_path / "state" / "codex_stage_guard_context.json"
    context_path.write_text(json.dumps({
        "provider": "codex",
        "stage": "PLANNING",
        "mission_id": "mission_test",
    }))

    from WebProxy.mcp_server import handle_tool_call

    result = handle_tool_call("AtlasForgeSubmitPlan", {
        "plan": {
            "summary": "context file wins",
        },
    })

    parsed = json.loads(result)
    assert parsed["ok"] is True
    assert parsed["stage"] == "PLANNING"
    assert parsed["provider"] == "codex"


def test_stage_guard_ignores_stale_codex_context_for_claude_mission(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="COMPLETE", provider="claude")
    context_path = tmp_path / "state" / "codex_stage_guard_context.json"
    context_path.write_text(json.dumps({
        "provider": "codex",
        "stage": "PLANNING",
        "mission_id": "mission_test",
    }))

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="AtlasForgeSubmitPlan is not allowed during COMPLETE"):
        handle_tool_call("AtlasForgeSubmitPlan", {
            "plan": {
                "summary": "must not use stale codex context",
            },
        })


def test_planning_rejects_source_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="PLANNING cannot write"):
        handle_tool_call("AtlasForgeSubmitPlan", {
            "plan": {"summary": "nope"},
            "target_path": "src/plan.json",
        })
    assert not (tmp_path / "src" / "plan.json").exists()


def test_stage_guard_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="must not contain"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "escape attempt",
            "target_path": "missions/mission_test/planning/../../escape.md",
        })
    assert not (tmp_path / "missions" / "escape.md").exists()


def test_complete_stage_allows_policy_read_but_not_note_write(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="COMPLETE")

    from WebProxy.mcp_server import handle_tool_call

    policy = json.loads(handle_tool_call("AtlasForgeGetStagePolicy", {}))
    assert policy["stage"] == "COMPLETE"
    assert "AtlasForgeGetStagePolicy" in policy["allowed_tools"]

    with pytest.raises(ValueError, match="AtlasForgeWriteStageNote is not allowed during COMPLETE"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "should not write",
            "target_path": "missions/mission_test/complete/note.md",
        })


def test_write_tools_reject_stage_override(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="COMPLETE")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="does not match active mission stage"):
        handle_tool_call("AtlasForgeSubmitPlan", {
            "stage": "PLANNING",
            "plan": {"summary": "bypass attempt"},
            "target_path": "workspace/mission_test/artifacts/implementation_plan.md",
        })
    assert not (tmp_path / "workspace" / "mission_test" / "artifacts" / "implementation_plan.md").exists()


def test_planning_submit_plan_rejects_research_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="PLANNING cannot write target_path"):
        handle_tool_call("AtlasForgeSubmitPlan", {
            "plan": {"summary": "wrong file"},
            "target_path": "workspace/mission_test/research/research_findings.md",
        })
    assert not (tmp_path / "workspace" / "mission_test" / "research" / "research_findings.md").exists()


def test_planning_stage_note_defaults_to_research_findings(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    result = json.loads(handle_tool_call("AtlasForgeWriteStageNote", {
        "content": "Research notes for the plan.",
    }))

    assert result["ok"] is True
    assert result["path"] == "workspace/mission_test/research/research_findings.md"
    text = (tmp_path / "workspace" / "mission_test" / "research" / "research_findings.md").read_text()
    assert "Research notes for the plan." in text


def test_planning_stage_note_rejects_artifacts_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="PLANNING cannot write target_path"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "wrong file",
            "target_path": "workspace/mission_test/artifacts/implementation_plan.md",
        })
    assert not (tmp_path / "workspace" / "mission_test" / "artifacts" / "implementation_plan.md").exists()


def test_stage_guard_rejects_whitespace_padded_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="leading or trailing whitespace"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "padded path",
            "target_path": " missions/mission_test/planning/note.md",
        })
    assert not (tmp_path / "missions" / "mission_test" / "planning" / "note.md").exists()


def test_stage_guard_rejects_url_schemed_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="URL-schemed"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "url path",
            "target_path": "file://missions/mission_test/planning/note.md",
        })
    assert not (tmp_path / "file:" / "missions" / "mission_test" / "planning" / "note.md").exists()


def test_stage_guard_rejects_percent_encoded_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="percent-encoded"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "encoded path",
            "target_path": "missions/mission_test/planning/%2e%2e/escape.md",
        })
    assert not (tmp_path / "missions" / "mission_test" / "escape.md").exists()


def test_stage_guard_rejects_embedded_dotdot_segment(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLASFORGE_ROOT", str(tmp_path))
    _prepare_root(tmp_path, stage="PLANNING")

    from WebProxy.mcp_server import handle_tool_call

    with pytest.raises(ValueError, match="must not contain"):
        handle_tool_call("AtlasForgeWriteStageNote", {
            "content": "embedded dotdot path",
            "target_path": "missions/mission_test/planning/safe..evil.md",
        })
    assert not (tmp_path / "missions" / "mission_test" / "planning" / "safe..evil.md").exists()
