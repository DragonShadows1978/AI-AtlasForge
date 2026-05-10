import os
import time

import pytest

import agent_stream_manager as asm


def test_subscribe_accepts_integer_float_cursor_without_full_replay(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    ctx = asm.AgentContext("agent_seq", "mission", "Agent")
    manager._agents = {"agent_seq": ctx}
    ctx.ring.append({"seq": 1, "text": "old"})
    ctx.ring.append({"seq": 2, "text": "new"})

    snapshot, q = manager.subscribe("agent_seq", 1.0)
    try:
        assert [event["seq"] for event in snapshot] == [2]
    finally:
        manager.unsubscribe("agent_seq", q)


def test_subscribe_rejects_fractional_float_cursor(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError):
        manager.subscribe("agent_seq", 1.5)


def test_negative_event_seq_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    ctx = asm.AgentContext("agent_seq", "mission", "Agent")

    with pytest.raises(ValueError):
        manager.broadcast_stream_line(ctx, {"event_type": "raw", "display_text": "bad"}, -1)


def test_get_recent_agents_zero_age_window_does_not_bypass_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    old = asm.AgentContext("old_agent", "mission", "Old")
    old.spawned_at = 90.0
    old.started_at = 90.0
    recent = asm.AgentContext("recent_agent", "mission", "Recent")
    recent.spawned_at = 100.0
    recent.started_at = 100.0
    manager._agents = {"old_agent": old, "recent_agent": recent}
    monkeypatch.setattr(manager, "_load_disk_agents", lambda: {})
    monkeypatch.setattr(asm.time, "time", lambda: 100.0)

    result = manager.get_recent_agents(limit=10, max_age_seconds=0)

    assert [agent["agent_id"] for agent in result["mission"]] == ["recent_agent"]


def test_get_recent_agents_epoch_timestamp_ages_out(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    epoch = asm.AgentContext("epoch_agent", "mission", "Epoch")
    epoch.spawned_at = 0
    epoch.started_at = 0
    manager._agents = {"epoch_agent": epoch}
    monkeypatch.setattr(manager, "_load_disk_agents", lambda: {})
    monkeypatch.setattr(asm.time, "time", lambda: 100.0)

    assert manager.get_recent_agents(limit=10, max_age_seconds=60)["mission"] == []


def test_get_recent_agents_rejects_negative_age_window(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()

    with pytest.raises(ValueError, match="max_age_seconds"):
        manager.get_recent_agents(max_age_seconds=-1)


def test_get_stream_history_applies_limit_after_mission_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    manager = asm.AgentStreamManager()
    monkeypatch.setattr(manager, "_get_current_mission_id", lambda: "target")

    manager._agents = {
        "keep1": {"_tombstone": True, "completed_at": 10.0},
        "keep2": {"_tombstone": True, "completed_at": 9.0},
    }

    files = [
        ("mission_skip1.jsonl", 50),
        ("mission_skip2.jsonl", 40),
        ("mission_skip3.jsonl", 30),
        ("mission_keep1.jsonl", 20),
        ("mission_keep2.jsonl", 10),
    ]
    for name, mtime in files:
        path = tmp_path / name
        path.write_text("", encoding="utf-8")
        os.utime(path, (mtime, mtime))

    history = manager.get_stream_history(mission_id="target", limit=2)

    assert [item["agent_id"] for item in history] == ["keep1", "keep2"]


def test_get_stream_history_limit_zero_returns_without_scanning(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    monkeypatch.setattr(
        asm,
        "_ensure_stream_dir",
        lambda: (_ for _ in ()).throw(AssertionError("should not scan stream dir")),
    )

    assert manager.get_stream_history(limit=0) == []


def test_cleanup_old_stream_files_skips_bare_snapshot_filename(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    snapshot = tmp_path / ".snapshot.json"
    snapshot.write_text("{}", encoding="utf-8")
    old = time.time() - 7200
    os.utime(snapshot, (old, old))

    assert manager.cleanup_old_stream_files(max_age_hours=1) == 0
    assert snapshot.exists()


def test_reap_dead_agents_broadcasts_lifecycle_after_tombstone(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    ctx = asm.AgentContext("dead_agent", "mission", "Dead <Agent>", pid=12345)
    ctx.started_at = 90.0
    ctx._parsed_lines.append({"event_type": "raw", "display_text": "line"})
    manager._agents = {"dead_agent": ctx}
    monkeypatch.setattr(manager, "_load_disk_agents", lambda: {})
    monkeypatch.setattr(asm, "_is_pid_alive", lambda pid, spawned_at: False)
    monkeypatch.setattr(manager, "_save_state_locked", lambda: None)
    monkeypatch.setattr(manager, "_log_agent_error", lambda ctx, error: None)
    monkeypatch.setattr(manager, "_write_snapshot", lambda ctx: None)

    assert manager.reap_dead_agents() == ["dead_agent"]

    tombstone = manager._agents["dead_agent"]
    assert tombstone["_tombstone"] is True
    assert tombstone["status"] == "error"
    assert ctx._completion_broadcasted is True
    event = list(tombstone["ring_tail"])[-1]
    assert event["event"] == "agent_error"
    assert event["error"] == "process_died"
    assert event["line_count"] == 1


def test_prewarm_active_agents_skips_invalid_status(monkeypatch, tmp_path):
    monkeypatch.setattr(asm, "STREAM_DIR", tmp_path)
    manager = asm.AgentStreamManager()
    monkeypatch.setattr(
        manager,
        "_load_disk_agents",
        lambda: {"bad": {"context": "mission", "label": "Bad", "status": "zombie"}},
    )

    assert manager.prewarm_active_agents() == 0
    assert "bad" not in manager._agents
