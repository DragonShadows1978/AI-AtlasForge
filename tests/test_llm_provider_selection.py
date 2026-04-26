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


def test_conductor_falls_back_to_persisted_provider(monkeypatch, tmp_path):
    import atlasforge_conductor as conductor

    monkeypatch.setattr(conductor, "MISSION_PATH", tmp_path / "missing_mission.json")
    provider_path = tmp_path / "llm_provider.json"
    provider_path.write_text(json.dumps({"provider": "codex"}))
    monkeypatch.setattr(conductor, "LLM_PROVIDER_PATH", provider_path)
    monkeypatch.delenv("ATLASFORGE_LLM_PROVIDER", raising=False)

    assert conductor.get_llm_provider() == "codex"


def test_codex_model_env_reaches_command(monkeypatch):
    import atlasforge_conductor as conductor

    monkeypatch.setenv("ATLASFORGE_CODEX_MODEL", "gpt-5.4-codex")
    monkeypatch.delenv("CODEX_MODEL", raising=False)
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    command = conductor.build_llm_command("codex", model=conductor.get_llm_model("codex"))

    assert "--search" not in command
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in command
    assert any("mcp_servers.atlasforge-web-proxy.args=" in item for item in command)
    assert "--model" in command
    assert "gpt-5.4-codex" in command


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


def test_investigation_codex_uses_proxy_and_no_native_search_by_default(monkeypatch):
    import subprocess
    import investigation_engine

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(investigation_engine, "_get_active_llm_provider", lambda: "codex")
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("ATLASFORGE_CODEX_WEB_SEARCH", raising=False)

    response, _elapsed = investigation_engine.invoke_claude("search something")

    assert response == "ok"
    assert "--search" not in captured["cmd"]
    assert "mcp_servers.atlasforge-web-proxy.command=\"python3\"" in captured["cmd"]
    assert any("mcp_servers.atlasforge-web-proxy.args=" in item for item in captured["cmd"])
