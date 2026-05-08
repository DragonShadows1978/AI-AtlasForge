#!/usr/bin/env python3

import json
import sys
from pathlib import Path


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

import agent_stream_manager as asm  # noqa: E402


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
