import json


def test_conductor_prefers_active_mission_provider(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    mission_path = tmp_path / "mission.json"
    provider_path = tmp_path / "llm_provider.json"
    mission_path.write_text(json.dumps({"llm_provider": "codex"}))
    provider_path.write_text(json.dumps({"provider": "claude"}))

    monkeypatch.setattr(conductor, "MISSION_PATH", mission_path)
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.setenv("ATLASFORGE_LLM_PROVIDER", "gemini")

    assert conductor.get_llm_provider() == "codex"


def test_conductor_prefers_in_memory_mission_provider(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "MISSION_PATH", tmp_path / "missing_mission.json")
    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "claude"}))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)

    assert conductor.resolve_llm_provider({"llm_provider": "codex"}) == "codex"


def test_provider_api_updates_active_mission_provider(monkeypatch, tmp_path):
    from flask import Flask
    import io_utils
    from dashboard_modules import core as core_mod

    mission_path = tmp_path / "mission.json"
    mission_path.write_text(json.dumps({
        "mission_id": "mission_provider_switch",
        "llm_provider": "claude",
    }))

    def fake_set_provider(provider, **_kwargs):
        return str(provider).strip().lower()

    monkeypatch.setattr(core_mod, "MISSION_PATH", mission_path)
    monkeypatch.setattr(core_mod, "io_utils", io_utils)
    monkeypatch.setattr(core_mod, "set_llm_provider", fake_set_provider)
    monkeypatch.setattr(
        core_mod,
        "get_llm_config",
        lambda: {"provider": "codex", "selected": {}, "options": {}},
    )

    app = Flask(__name__)
    app.register_blueprint(core_mod.core_bp)
    response = app.test_client().post(
        "/api/llm-provider",
        json={
            "provider": "codex",
            "model": "gpt-5.5",
            "thinking": "high",
            "fast": True,
        },
    )

    assert response.status_code == 200
    mission = json.loads(mission_path.read_text())
    assert mission["llm_provider"] == "codex"
    assert mission["llm_model"] == "gpt-5.5"
    assert mission["llm_thinking"] == "high"
    assert mission["llm_fast"] is True


def test_conductor_falls_back_to_persisted_provider(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "MISSION_PATH", tmp_path / "missing_mission.json")
    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "codex"}))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("ATLASFORGE_LLM_PROVIDER", raising=False)

    assert conductor.get_llm_provider() == "codex"


def test_codex_model_env_reaches_command(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", tmp_path / "missing_llm_provider.json")
    monkeypatch.setenv("ATLASFORGE_CODEX_MODEL", "gpt-5.4-codex")
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    command = conductor.build_llm_command("codex", model=conductor.get_llm_model("codex"))

    assert "--search" not in command
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in command
    assert any("mcp_servers.atlasforge-web-proxy.args=" in item for item in command)
    assert "--model" in command
    assert "gpt-5.4-codex" in command


def test_claude_persisted_custom_model_reaches_command(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({
        "provider": "claude",
        "selected": {
            "claude": {"model": "claude-opus-4-6[1m]", "thinking": "high"},
        },
    }))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)

    command = conductor.build_llm_command("claude", model=conductor.get_llm_model("claude"))

    assert command[0:2] == ["claude", "-p"]
    assert "--model" in command
    assert command[command.index("--model") + 1] == "claude-opus-4-6[1m]"


def test_conductor_mission_model_wins_over_persisted_state(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({
        "provider": "claude",
        "selected": {
            "claude": {"model": "sonnet", "thinking": "high"},
        },
    }))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)

    assert (
        conductor.get_llm_model("claude", mission={"llm_model": "claude-opus-4-6[1m]"})
        == "claude-opus-4-6[1m]"
    )


def test_codex_planning_uses_read_only_stage_sandbox(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.delenv("ATLASFORGE_CODEX_STAGE_GUARD", raising=False)
    monkeypatch.setenv("ATLASFORGE_CODEX_AUTONOMOUS", "1")

    command = conductor.build_llm_command("codex", stage="PLANNING")

    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_codex_mcp_tools_are_auto_approved(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.delenv("ATLASFORGE_CODEX_STAGE_GUARD", raising=False)

    command = conductor.build_llm_command("codex", stage="PLANNING")
    joined = " ".join(command)

    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    for tool_name in (
        "WebSearch",
        "WebFetch",
        "WebResearch",
        "AtlasForgeGetStagePolicy",
        "AtlasForgeSubmitPlan",
        "AtlasForgeWriteStageNote",
        "AtlasForgeSubmitReview",
        "AtlasForgeSubmitPatchSummary",
    ):
        assert (
            f"mcp_servers.atlasforge-web-proxy.tools.{tool_name}.approval_mode=\"approve\""
            in joined
        )


def test_codex_building_keeps_full_send_by_default(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.delenv("ATLASFORGE_CODEX_STAGE_GUARD", raising=False)
    monkeypatch.setenv("ATLASFORGE_CODEX_AUTONOMOUS", "1")

    command = conductor.build_llm_command("codex", stage="BUILDING")

    assert "--sandbox" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command


def test_codex_testing_uses_read_only_stage_sandbox(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.delenv("ATLASFORGE_CODEX_STAGE_GUARD", raising=False)
    monkeypatch.setenv("ATLASFORGE_CODEX_AUTONOMOUS", "1")

    command = conductor.build_llm_command("codex", stage="TESTING")

    assert "--sandbox" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_codex_stage_guard_can_be_disabled(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.setenv("ATLASFORGE_CODEX_STAGE_GUARD", "0")
    monkeypatch.setenv("ATLASFORGE_CODEX_AUTONOMOUS", "1")

    command = conductor.build_llm_command("codex", stage="PLANNING")

    assert "--sandbox" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" in command


def test_invoke_llm_codex_mission_sets_stage_guard_context(monkeypatch, tmp_path):
    import subprocess
    from unittest.mock import MagicMock, patch
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "BASE_DIR", tmp_path)
    monkeypatch.setattr(conductor, "CODEX_STAGE_GUARD_CONTEXT_PATH", tmp_path / "state" / "codex_stage_guard_context.json")
    monkeypatch.setattr(conductor, "MISSION_PATH", tmp_path / "missing_mission.json")
    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "claude"}))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("ATLASFORGE_CODEX_STAGE_GUARD", raising=False)

    captured = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs.get("env", {})
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = 0
        proc.communicate.return_value = ('{"status":"ok"}', "")
        return proc

    with patch.object(subprocess, "Popen", side_effect=fake_popen):
        result, error = conductor.invoke_llm(
            "test prompt",
            timeout=5,
            cwd=tmp_path,
            stage="PLANNING",
            mission={"mission_id": "mission_codex", "llm_provider": "codex"},
        )

    assert result == '{"status":"ok"}'
    assert error is None
    assert captured["command"][0] == "codex"
    assert "--sandbox" in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert captured["env"]["ATLASFORGE_ACTIVE_PROVIDER"] == "codex"
    assert captured["env"]["ATLASFORGE_ACTIVE_STAGE"] == "PLANNING"
    assert captured["env"]["ATLASFORGE_ACTIVE_MISSION_ID"] == "mission_codex"
    context = json.loads((tmp_path / "state" / "codex_stage_guard_context.json").read_text())
    assert context["provider"] == "codex"
    assert context["stage"] == "PLANNING"
    assert context["mission_id"] == "mission_codex"


def test_queue_provider_explicit_value_wins(monkeypatch, tmp_path):
    from dashboard_modules import queue_scheduler

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "claude"}))
    monkeypatch.setattr(queue_scheduler, "LLM_PROVIDER_PATH", provider_path)

    assert queue_scheduler._resolve_queue_llm_provider({"llm_provider": "codex"}) == "codex"


def test_queue_provider_falls_back_to_active_state(monkeypatch, tmp_path):
    from dashboard_modules import queue_scheduler

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "gemini"}))
    monkeypatch.setattr(queue_scheduler, "LLM_PROVIDER_PATH", provider_path)

    assert queue_scheduler._resolve_queue_llm_provider({}) == "gemini"


def test_blind_red_team_uses_mission_provider_and_codex_model(monkeypatch, tmp_path):
    from adversarial_testing import blind_agent_runner

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "mission.json").write_text(json.dumps({"llm_provider": "codex"}))
    monkeypatch.setattr(blind_agent_runner, "_ATLASFORGE_ROOT", tmp_path)
    monkeypatch.setenv("ATLASFORGE_CODEX_MODEL", "gpt-5.4-codex")
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    provider = blind_agent_runner._resolve_red_team_llm_provider()
    command = blind_agent_runner._build_red_team_llm_command(provider)

    assert provider == "codex"
    assert "codex" in command[0]
    assert "--search" not in command
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in command
    assert "--model" in command
    assert "gpt-5.4-codex" in command


def test_hierarchical_agents_use_codex_provider_command(monkeypatch, tmp_path):
    import hierarchical_framework

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "mission.json").write_text(json.dumps({"llm_provider": "codex"}))
    monkeypatch.setattr(hierarchical_framework, "BASE_DIR", tmp_path)
    monkeypatch.setenv("ATLASFORGE_CODEX_MODEL", "gpt-5.4-codex")
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    provider = hierarchical_framework._resolve_llm_provider()
    command = hierarchical_framework._build_provider_command(provider, stage="TESTING")

    assert provider == "codex"
    assert command[0] == "codex"
    assert "--search" not in command
    assert "exec" in command
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in command
    assert "--model" in command
    assert "gpt-5.4-codex" in command


def test_codex_native_search_is_explicit_opt_in(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.setenv("ATLASFORGE_CODEX_WEB_SEARCH", "1")

    command = conductor.build_llm_command("codex")

    assert "--search" in command


def test_codex_fast_mode_uses_cli_service_tier(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({
        "provider": "codex",
        "selected": {
            "codex": {"model": "gpt-5.5", "thinking": "high", "fast": True},
        },
    }))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("ATLASFORGE_CODEX_THINKING", raising=False)
    monkeypatch.delenv("ATLASFORGE_CODEX_FAST", raising=False)

    command = conductor.build_llm_command("codex")

    assert command[0] == "codex"
    assert "--enable" in command
    assert command[command.index("--enable") + 1] == "fast_mode"
    assert 'service_tier="fast"' in command
    assert 'model_reasoning_effort="high"' in command


def test_codex_legacy_fast_thinking_enables_fast_without_reasoning(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({
        "provider": "codex",
        "selected": {
            "codex": {"model": "gpt-5.5", "thinking": "fast"},
        },
    }))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("ATLASFORGE_CODEX_THINKING", raising=False)
    monkeypatch.delenv("ATLASFORGE_CODEX_FAST", raising=False)

    command = conductor.build_llm_command("codex")

    assert "--enable" in command
    assert command[command.index("--enable") + 1] == "fast_mode"
    assert 'service_tier="fast"' in command
    assert not any("model_reasoning_effort" in item for item in command)


def test_fast_thinking_is_codex_only(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", tmp_path / "missing_llm_provider.json")
    monkeypatch.setenv("ATLASFORGE_CLAUDE_THINKING", "fast")

    command = conductor.build_llm_command("claude")

    assert "--effort" not in command


def test_blind_red_team_claude_combines_stage_bans_with_proxy(monkeypatch):
    from adversarial_testing import blind_agent_runner

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}" if name == "claude" else None)

    command = blind_agent_runner._build_red_team_llm_command("claude", stage="TESTING")

    assert "--mcp-config" in command
    assert command.count("--disallowedTools") == 1
    disallowed = command[command.index("--disallowedTools") + 1]
    assert "WebSearch" in disallowed
    assert "WebFetch" in disallowed


def test_hierarchical_claude_routes_web_tools_through_proxy():
    import hierarchical_framework

    command = hierarchical_framework._build_provider_command("claude", stage="TESTING")

    assert "--mcp-config" in command
    assert command.count("--disallowedTools") == 1
    disallowed = command[command.index("--disallowedTools") + 1]
    assert "WebSearch" in disallowed
    assert "WebFetch" in disallowed


def test_investigation_codex_uses_proxy_and_no_native_search_by_default(monkeypatch, tmp_path):
    import agent_stream_manager
    import investigation_engine

    captured = {}

    class FakeProc:
        pid = 4242
        returncode = 0

        def communicate(self, input=None, timeout=None):
            return "ok", ""

        def poll(self):
            return self.returncode

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProc()

    monkeypatch.setattr(investigation_engine, "_get_active_llm_provider", lambda: "codex")
    monkeypatch.setattr(investigation_engine.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        agent_stream_manager,
        "register_agent",
        lambda *_args, **_kwargs: tmp_path / "codex-stream.jsonl",
    )
    monkeypatch.setattr(agent_stream_manager, "update_agent_pid", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent_stream_manager, "complete_agent", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_stream_manager,
        "stream_codex_session_to_file",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    response, _elapsed = investigation_engine.invoke_claude("search something")

    assert response == "ok"
    assert "--search" not in captured["cmd"]
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in captured["cmd"]
    assert any("mcp_servers.atlasforge-web-proxy.args=" in item for item in captured["cmd"])
