#!/usr/bin/env python3

import json
import logging
import sys
from pathlib import Path

import pytest


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

import agent_stream_manager as asm  # noqa: E402


def test_agent_context_rejects_overlong_agent_id():
    with pytest.raises(ValueError, match="at most"):
        asm.AgentContext("a" * (asm._MAX_AGENT_ID_LENGTH + 1), "mission", "BUILDING Agent 1")


def test_agent_context_rejects_negative_pid():
    with pytest.raises(ValueError, match="pid"):
        asm.AgentContext("agent_neg_pid", "mission", "BUILDING Agent 1", pid=-1)


def test_wrap_with_seq_strips_crlf():
    wrapped = asm._wrap_with_seq("hello\r\n", 1)

    assert json.loads(wrapped)["raw"] == "hello"


def test_copy_agent_fields_preserves_epoch_started_at():
    result = asm._copy_agent_fields(
        {
            "agent_id": "epoch",
            "started_at": 0,
            "spawned_at": 123,
            "completed_at": 2,
        }
    )

    assert result["started_at"] == 0
    assert result["duration_seconds"] == 2


def test_parse_stream_line_rejects_non_string_input():
    assert asm._parse_stream_line(None) == (None, None)
    assert asm._parse_stream_line(123) == (None, None)


def test_workspace_paths_match_rejects_non_string_input():
    assert asm._workspace_paths_match(123, "/tmp/workspace") is False
    assert asm._workspace_paths_match("/tmp/workspace", ["bad"]) is False


def test_is_pid_alive_rejects_non_int_pid():
    assert asm._is_pid_alive("../../proc/1") is False
    assert asm._is_pid_alive("1") is False
    assert asm._is_pid_alive(True) is False
    assert asm._is_pid_alive(0) is False


def test_update_pid_rejects_negative_pid():
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError, match="positive"):
        manager.update_pid("missing", -1)


def test_get_agent_stream_lines_rejects_non_string_agent_id():
    manager = asm.AgentStreamManager()
    assert manager.get_agent_stream_lines(123) == []
    assert manager.get_agent_stream_lines("") == []


def test_register_agent_cleans_init_lock_after_context_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError, match="context must be"):
        manager.register_agent("bad-context", "agent_bad", "BUILDING Agent 1")

    assert "agent_bad" not in manager._agent_init_locks
    assert "agent_bad" not in manager._agents


def test_complete_agent_caps_error_tombstone(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    ctx = asm.AgentContext("agent_err", "mission", "BUILDING Agent 1")
    manager._agents = {"agent_err": ctx}
    monkeypatch.setattr(manager, "_save_state_locked", lambda: None)
    monkeypatch.setattr(manager, "_write_snapshot", lambda _ctx: None)
    monkeypatch.setattr(manager, "_log_agent_error", lambda _ctx, _error: None)

    manager.complete_agent("agent_err", error="x" * (asm._MAX_AGENT_ERROR_LENGTH + 100))

    tombstone = manager._agents["agent_err"]
    assert tombstone["status"] == "error"
    assert len(tombstone["error"]) == asm._MAX_AGENT_ERROR_LENGTH


def test_load_disk_agents_rejects_non_object_root(tmp_path, caplog):
    manager = asm.AgentStreamManager()
    manager.STATE_FILE = tmp_path / "active_agents.json"
    manager.STATE_FILE.write_text("[]", encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        assert manager._load_disk_agents() == {}

    assert "active_agents.json root" in caplog.text


def test_get_agent_stream_lines_rejects_non_object_snapshot(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    snapshot = tmp_path / "mission_done.snapshot.json"
    snapshot.write_text("[]", encoding="utf-8")
    manager._agents = {
        "mission_done": {
            "_tombstone": True,
            "agent_id": "mission_done",
            "context": "mission",
            "label": "Done",
            "status": "complete",
            "snapshot_file": str(snapshot),
        }
    }

    with caplog.at_level(logging.WARNING):
        assert manager.get_agent_stream_lines("mission_done") == []

    assert "agent snapshot root" in caplog.text


def test_parse_jsonl_file_rejects_negative_max_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    path = tmp_path / "mission_agent.jsonl"
    path.write_text("", encoding="utf-8")
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError, match="max_bytes"):
        manager._parse_jsonl_file(path, max_bytes=-1)


def test_read_jsonl_lines_capped_rejects_negative_max_bytes(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    path = tmp_path / "mission_agent.jsonl"
    path.write_text("", encoding="utf-8")
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError, match="max_bytes"):
        manager._read_jsonl_lines_capped(path, max_bytes=-1)


def test_parse_jsonl_file_uses_file_timestamp_for_disk_events(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    path = tmp_path / "mission_agent.jsonl"
    path.write_text(
        json.dumps({"seq": 1, "raw": json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})}) + "\n",
        encoding="utf-8",
    )
    manager = asm.AgentStreamManager()

    lines = manager._parse_jsonl_file(path)

    assert lines[0]["timestamp"]


def test_tool_use_with_falsy_input_is_preserved():
    event = asm._parse_jsonl_line(
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "name": "zero_tool", "input": 0}
                    ]
                },
            }
        )
    )

    assert event["event_type"] == "tool_call"
    assert "0" in event["display_text"]


def test_parse_jsonl_line_codex_agent_message():
    line = json.dumps(
        {
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "Inspecting the codebase before patching.",
                "phase": "commentary",
            },
        }
    )

    event = asm._parse_jsonl_line(line)

    assert event is not None
    assert event["event_type"] == "thinking"
    assert "Inspecting the codebase" in event["display_text"]


def test_parse_jsonl_line_codex_function_events():
    call_line = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "arguments": "{\"cmd\":\"rg -n mission\"}",
            },
        }
    )
    output_line = json.dumps(
        {
            "type": "response_item",
            "payload": {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "atlasforge_conductor.py:123",
            },
        }
    )

    call_event = asm._parse_jsonl_line(call_line)
    output_event = asm._parse_jsonl_line(output_line)

    assert call_event["event_type"] == "tool_call"
    assert "exec_command" in call_event["display_text"]
    assert output_event["event_type"] == "tool_result"
    assert "atlasforge_conductor.py" in output_event["display_text"]


def test_parse_jsonl_line_keeps_claude_system_lifecycle_events():
    init_event = asm._parse_jsonl_line(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "cwd": "/mnt/ForgeRealm/AI-AtlasForge/workspace/mission",
                "model": "claude-opus-4-8",
                "tools": ["Read", "Bash"],
            }
        )
    )
    started_event = asm._parse_jsonl_line(
        json.dumps(
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": "task_1",
                "task_type": "local_bash",
                "description": "Probe game engines",
            }
        )
    )
    notification_event = asm._parse_jsonl_line(
        json.dumps(
            {
                "type": "system",
                "subtype": "task_notification",
                "task_id": "task_1",
                "status": "completed",
                "summary": "Probe game engines",
            }
        )
    )

    assert init_event["event_type"] == "raw"
    assert "system:init" in init_event["display_text"]
    assert "claude-opus-4-8" in init_event["display_text"]
    assert started_event["event_type"] == "raw"
    assert "task_started" in started_event["display_text"]
    assert "Probe game engines" in started_event["display_text"]
    assert notification_event["event_type"] == "raw"
    assert "task_notification" in notification_event["display_text"]
    assert "completed" in notification_event["display_text"]


def test_parse_jsonl_line_preserves_long_planning_tool_result_preview():
    content = "x" * 1200
    event = asm._parse_jsonl_line(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "content": content,
                        }
                    ]
                },
            }
        )
    )

    assert event["event_type"] == "tool_result"
    assert len(event["display_text"]) == len(content)


def test_stream_stdout_pins_webproxy_cache_json_from_tool_result(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "REPO_ROOT", tmp_path)

    cache_dir = tmp_path / "WebProxy" / "atlasforge_data" / "web_proxy_cache"
    cache_dir.mkdir(parents=True)
    cache_json = cache_dir / "fetch_example.json"
    cache_payload = {
        "type": "fetch",
        "url": "https://example.com",
        "text": "full source text",
    }
    cache_json.write_text(json.dumps(cache_payload), encoding="utf-8")

    raw_line = json.dumps(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": (
                            "URL: https://example.com\n"
                            f"Cache JSON: {cache_json}\n"
                            "Text: bounded display text"
                        ),
                    }
                ]
            },
        }
    )

    class FakeProc:
        stdout = [raw_line + "\n"]
        returncode = 0

        def wait(self, timeout=None):
            return 0

    stream_file = tmp_path / "stream.jsonl"
    event_file = tmp_path / "subagent" / "events.jsonl"
    sources_file = tmp_path / "subagent" / "sources.jsonl"

    asm.stream_stdout_to_file(
        FakeProc(),
        stream_file,
        "agent_1",
        artifact_event_file=event_file,
        artifact_sources_file=sources_file,
        artifact_label="inv_1_sub_0",
    )

    source_payloads = list((tmp_path / "subagent" / "source_payloads").glob("webproxy_*.json"))
    assert len(source_payloads) == 1
    assert json.loads(source_payloads[0].read_text(encoding="utf-8")) == cache_payload

    source_records = [
        json.loads(line)
        for line in sources_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    pinned = [r for r in source_records if r.get("artifact_type") == "web_proxy_cache_json"]
    assert len(pinned) == 1
    assert pinned[0]["cache_json_path"] == str(cache_json.resolve())
    assert pinned[0]["evidence_json_path"] == str(source_payloads[0])
    assert pinned[0]["byte_length"] == cache_json.stat().st_size


def test_get_recent_agents_includes_completed_tombstones(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()

    running = asm.AgentContext("mission_live", "mission", "BUILDING Agent 1", 123)
    running.spawned_at = 100.0
    running.started_at = 100.0

    manager._agents = {
        "mission_live": running,
        "mission_done": {
            "_tombstone": True,
            "agent_id": "mission_done",
            "context": "mission",
            "label": "BUILDING Agent 2",
            "status": "complete",
            "started_at": 90.0,
            "completed_at": 110.0,
            "error": None,
            "stream_file": tmp_path / "mission_done.jsonl",
            "snapshot_file": tmp_path / "mission_done.snapshot.json",
        },
    }

    monkeypatch.setattr(manager, "_load_disk_agents", lambda: {})
    monkeypatch.setattr(asm.time, "time", lambda: 120.0)

    recent = manager.get_recent_agents(limit=6, max_age_seconds=60)
    mission_agents = recent["mission"]

    assert [a["agent_id"] for a in mission_agents] == ["mission_live", "mission_done"]
    assert mission_agents[1]["duration_seconds"] == 20.0


def test_stream_codex_session_to_file_mirrors_matching_transcript(monkeypatch, tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    stream_file = tmp_path / "mission_agent.jsonl"
    transcript_path = sessions_dir / "rollout.jsonl"
    transcript_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {
                            "cwd": "/tmp/workspace",
                            "originator": "codex_exec",
                            "source": "exec",
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": "Running targeted search.",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    class FakeProc:
        def __init__(self):
            self.calls = 0

        def poll(self):
            self.calls += 1
            return 0

    monkeypatch.setattr(asm, "CODEX_SESSIONS_DIR", sessions_dir)
    monkeypatch.setattr(asm, "CODEX_TRANSCRIPT_POLL_INTERVAL", 0.0)

    asm.stream_codex_session_to_file(FakeProc(), stream_file, "/tmp/workspace", started_at=0.0)

    content = stream_file.read_text(encoding="utf-8")
    assert "Running targeted search." in content
