import sys
import types

import investigation_engine


def test_codex_invoke_registers_mission_stream(monkeypatch, tmp_path):
    calls = {
        "registered": [],
        "pid": [],
        "streamed": [],
        "completed": [],
        "popen": [],
    }

    fake_stream_module = types.SimpleNamespace()

    def register_agent(context, agent_id, label, pid=None):
        calls["registered"].append((context, agent_id, label, pid))
        return tmp_path / "stream.jsonl"

    def update_agent_pid(agent_id, pid):
        calls["pid"].append((agent_id, pid))

    def complete_agent(agent_id, error=None):
        calls["completed"].append((agent_id, error))

    def stream_codex_session_to_file(proc, stream_file, workspace_path, started_at, agent_id=None):
        calls["streamed"].append((proc.pid, stream_file, workspace_path, agent_id))

    fake_stream_module.register_agent = register_agent
    fake_stream_module.update_agent_pid = update_agent_pid
    fake_stream_module.complete_agent = complete_agent
    fake_stream_module.stream_codex_session_to_file = stream_codex_session_to_file
    monkeypatch.setitem(sys.modules, "agent_stream_manager", fake_stream_module)

    class FakeProc:
        pid = 4242
        returncode = 0

        def communicate(self, input=None, timeout=None):
            self.input = input
            return '{"status":"passed"}\n', ""

        def poll(self):
            return self.returncode

    def fake_popen(*args, **kwargs):
        calls["popen"].append((args, kwargs))
        return FakeProc()

    monkeypatch.setattr(investigation_engine, "_get_active_llm_provider", lambda: "codex")
    monkeypatch.setattr(investigation_engine.subprocess, "Popen", fake_popen)

    response, elapsed = investigation_engine.invoke_claude(
        "run lane",
        cwd=tmp_path,
        stream_context="mission",
        artifact_label="TEST logic: invariants",
    )

    assert response == '{"status":"passed"}'
    assert elapsed >= 0
    assert calls["registered"][0][0] == "mission"
    assert calls["registered"][0][2] == "TEST logic: invariants"
    agent_id = calls["registered"][0][1]
    assert calls["pid"] == [(agent_id, 4242)]
    assert calls["streamed"][0][3] == agent_id
    assert calls["completed"] == [(agent_id, None)]
    assert calls["popen"][0][1]["cwd"] == str(tmp_path)
