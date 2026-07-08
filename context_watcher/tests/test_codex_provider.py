#!/usr/bin/env python3
"""
Targeted regression tests for Codex provider compatibility.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest


AF_ROOT = Path(__file__).resolve().parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

import context_watcher.context_watcher as cw_mod  # noqa: E402
from context_watcher import HandoffLevel, SessionMonitor  # noqa: E402
from context_watcher.context_watcher import _is_codex_file_for_workspace  # noqa: E402
from af_engine.core.archival import _codex_transcript_matches_workspace  # noqa: E402


@pytest.fixture(autouse=True)
def enable_codex_context_handoff_for_legacy_tests(monkeypatch):
    monkeypatch.setattr(cw_mod, "CODEX_CONTEXT_HANDOFF_ENABLED", True)


def _write_codex_session(
    path: Path,
    *,
    cwd: str,
    total_tokens: int,
    model_context_window: int,
    model: str = "",
    input_tokens: int = 0,
    cached_input_tokens: int = 0,
    output_tokens: int = 0,
    last_total_tokens: int | None = None,
    cumulative_input_tokens: int | None = None,
    cumulative_cached_input_tokens: int | None = None,
    cumulative_output_tokens: int | None = None,
    originator: str = "codex_exec",
    source: str = "exec",
) -> None:
    records = [
        {
            "timestamp": "2026-03-10T19:11:04.260Z",
            "type": "session_meta",
            "payload": {
                "id": "session-1",
                "timestamp": "2026-03-10T19:10:49.872Z",
                "cwd": cwd,
                "originator": originator,
                "cli_version": "0.113.0",
                "source": source,
            },
        },
    ]
    if model:
        records.append({
            "timestamp": "2026-03-10T19:11:04.261Z",
            "type": "turn_context",
            "payload": {
                "cwd": cwd,
                "model": model,
            },
        })
    records.append(
        {
            "timestamp": "2026-03-10T19:11:05.884Z",
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {
                        "input_tokens": cumulative_input_tokens if cumulative_input_tokens is not None else input_tokens,
                        "cached_input_tokens": (
                            cumulative_cached_input_tokens
                            if cumulative_cached_input_tokens is not None
                            else cached_input_tokens
                        ),
                        "output_tokens": cumulative_output_tokens if cumulative_output_tokens is not None else output_tokens,
                        "reasoning_output_tokens": 0,
                        "total_tokens": total_tokens,
                    },
                    "last_token_usage": {
                        "input_tokens": input_tokens,
                        "cached_input_tokens": cached_input_tokens,
                        "output_tokens": output_tokens,
                        "reasoning_output_tokens": 0,
                        "total_tokens": last_total_tokens if last_total_tokens is not None else total_tokens,
                    },
                    "model_context_window": model_context_window,
                },
                "rate_limits": None,
            },
        }
    )
    path.write_text(
        "\n".join(
            json.dumps(record)
            for record in records
        )
        + "\n",
        encoding="utf-8",
    )


def test_codex_workspace_match_rejects_parent_directories(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_file = tmp_path / "session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(tmp_path),
        total_tokens=100,
        model_context_window=1000,
    )

    assert not _is_codex_file_for_workspace(session_file, str(workspace))
    assert not _codex_transcript_matches_workspace(session_file, str(workspace))


def test_codex_workspace_match_accepts_same_or_child_directory(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    child_dir = workspace / "src"
    child_dir.mkdir(parents=True)
    session_file = tmp_path / "session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(child_dir),
        total_tokens=100,
        model_context_window=1000,
    )

    assert _is_codex_file_for_workspace(session_file, str(workspace))
    assert _codex_transcript_matches_workspace(session_file, str(workspace))


def test_codex_workspace_match_rejects_interactive_cli_session_in_same_workspace(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_file = tmp_path / "session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=100,
        model_context_window=1000,
        originator="codex_cli_rs",
        source="cli",
    )

    assert not _is_codex_file_for_workspace(session_file, str(workspace))
    assert not _codex_transcript_matches_workspace(session_file, str(workspace))


def test_start_watching_honors_explicit_provider_over_stale_state(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    monkeypatch.setattr(cw_mod, "CODEX_SESSIONS_DIR", session_dir)
    monkeypatch.setattr(cw_mod, "_load_provider_from_state", lambda: "claude")

    watcher = cw_mod.ContextWatcher()
    session_id = watcher.start_watching(
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )

    try:
        assert session_id is not None
        stats = watcher.get_session_stats(session_id)
        assert stats["provider"] == "codex"
        assert stats["transcript_dir"] == str(session_dir)
    finally:
        watcher.stop_all()


def test_codex_graceful_handoff_uses_context_window(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=930,
        model_context_window=1000,
        input_tokens=700,
        cached_input_tokens=200,
        output_tokens=30,
    )

    received = []
    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        received.append,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    signal = monitor.process_updates()

    assert signal is not None
    assert signal.level == HandoffLevel.GRACEFUL
    assert signal.tokens_used == 930
    assert received and received[0].level == HandoffLevel.GRACEFUL


def test_codex_emergency_handoff_uses_context_window(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=740,
        cached_input_tokens=220,
        output_tokens=20,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    signal = monitor.process_updates()

    assert signal is not None
    assert signal.level == HandoffLevel.EMERGENCY
    assert signal.tokens_used == 980


def test_codex_large_window_does_not_count_cached_input_as_full_exhaustion(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=229_000,
        model_context_window=258_400,
        input_tokens=180_000,
        cached_input_tokens=40_000,
        output_tokens=9_000,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir

    assert monitor.process_updates() is None


def test_codex_gpt5_uses_one_million_window_when_cli_reports_smaller_window(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        model="gpt-5.5",
        total_tokens=256_328,
        model_context_window=258_400,
        input_tokens=253_646,
        cached_input_tokens=198_272,
        output_tokens=2_682,
    )

    received = []
    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        received.append,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    assert monitor.process_updates() is None
    assert monitor.last_tokens.model_name == "gpt-5.5"
    assert monitor.last_tokens.model_context_window == 1_000_000
    assert monitor.peak_tokens == 256_328
    assert received == []


def test_codex_cumulative_total_does_not_drive_context_handoff(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        model="gpt-5.5",
        total_tokens=1_130_000,
        model_context_window=258_400,
        input_tokens=180_000,
        cached_input_tokens=150_000,
        output_tokens=500,
        last_total_tokens=180_500,
        cumulative_input_tokens=1_100_000,
        cumulative_cached_input_tokens=800_000,
        cumulative_output_tokens=30_000,
    )

    received = []
    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        received.append,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    assert monitor.process_updates() is None
    assert monitor.last_tokens.total_tokens_seen == 180_500
    assert monitor.last_tokens.model_context_window == 1_000_000
    assert monitor.peak_tokens == 180_500
    assert received == []


def test_codex_startup_grace_suppresses_early_high_reported_total(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=970,
        cached_input_tokens=400,
        output_tokens=10,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir

    assert monitor.process_updates() is None


def test_codex_monitor_ignores_matching_transcript_from_before_start(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    old_session = session_dir / "old-session.jsonl"
    _write_codex_session(
        old_session,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=970,
        cached_input_tokens=400,
        output_tokens=10,
    )

    new_session = session_dir / "new-session.jsonl"
    _write_codex_session(
        new_session,
        cwd=str(workspace),
        total_tokens=100,
        model_context_window=1000,
        input_tokens=100,
    )

    now = time.time()
    os.utime(old_session, (now - 120, now - 120))
    os.utime(new_session, (now, now))

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at = datetime.fromtimestamp(now - 1)

    assert monitor.process_updates() is None
    assert monitor.current_jsonl == new_session
    assert monitor.peak_tokens == 100


def test_codex_monitor_tracks_multiple_matching_transcripts_without_rereading(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()

    first_session = session_dir / "first-session.jsonl"
    _write_codex_session(
        first_session,
        cwd=str(workspace),
        total_tokens=100,
        model_context_window=1000,
        input_tokens=100,
    )

    second_session = session_dir / "second-session.jsonl"
    _write_codex_session(
        second_session,
        cwd=str(workspace),
        total_tokens=200,
        model_context_window=1000,
        input_tokens=200,
    )

    now = time.time()
    os.utime(first_session, (now - 2, now - 2))
    os.utime(second_session, (now - 1, now - 1))

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at = datetime.fromtimestamp(now - 3)

    assert monitor.process_updates() is None
    assert monitor.peak_tokens == 200
    assert monitor.current_jsonl == second_session

    with first_session.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "timestamp": "2026-03-10T19:11:06.884Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 300,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 300,
                            },
                            "last_token_usage": {
                                "input_tokens": 300,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 300,
                            },
                            "model_context_window": 1000,
                        },
                    },
                }
            )
            + "\n"
        )
    os.utime(first_session, (now + 1, now + 1))

    assert monitor.process_updates() is None
    assert monitor.peak_tokens == 300
    assert monitor.current_jsonl == first_session

    # A later mtime on the other file must not reset its offset or reread 200.
    os.utime(second_session, (now + 2, now + 2))
    assert monitor.process_updates() is None
    assert monitor.peak_tokens == 300
    assert monitor.last_tokens.total_tokens_seen == 300
    assert monitor.current_jsonl == second_session


def test_codex_monitor_tails_preexisting_matching_transcripts_from_eof(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=970,
        cached_input_tokens=400,
        output_tokens=10,
    )

    now = time.time()
    os.utime(session_file, (now - 10, now - 10))

    received = []
    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        received.append,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at = datetime.fromtimestamp(now)

    assert monitor.process_updates() is None
    assert monitor.peak_tokens == 0
    assert received == []

    with session_file.open("a", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {
                    "timestamp": "2026-03-10T19:11:06.884Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "total_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 100,
                            },
                            "last_token_usage": {
                                "input_tokens": 100,
                                "cached_input_tokens": 0,
                                "output_tokens": 0,
                                "reasoning_output_tokens": 0,
                                "total_tokens": 100,
                            },
                            "model_context_window": 1000,
                        },
                    },
                }
            )
            + "\n"
        )
    os.utime(session_file, (now + 1, now + 1))

    assert monitor.process_updates() is None
    assert monitor.peak_tokens == 100
    assert received == []


def test_codex_handoff_after_startup_grace_window(tmp_path):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=970,
        cached_input_tokens=400,
        output_tokens=10,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    signal = monitor.process_updates()

    assert signal is not None
    assert signal.level == HandoffLevel.EMERGENCY
    assert signal.tokens_used == 980


def test_codex_context_handoff_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(cw_mod, "CODEX_CONTEXT_HANDOFF_ENABLED", False)

    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=980,
        model_context_window=1000,
        input_tokens=970,
        cached_input_tokens=400,
        output_tokens=10,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir
    monitor.started_at -= timedelta(seconds=120)

    assert monitor.process_updates() is None
    assert monitor.handoff_triggered is False
    assert monitor.peak_tokens == 980


@pytest.mark.parametrize("total_tokens", [100, 500, 910])
def test_codex_below_threshold_does_not_trigger(tmp_path, total_tokens):
    workspace = tmp_path / "workspace" / "project" / "mission_123"
    workspace.mkdir(parents=True)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    session_file = session_dir / "codex-session.jsonl"
    _write_codex_session(
        session_file,
        cwd=str(workspace),
        total_tokens=total_tokens,
        model_context_window=1000,
        input_tokens=total_tokens,
    )

    monitor = SessionMonitor(
        "codex-session",
        str(workspace),
        lambda signal: None,
        enable_time_handoff=False,
        provider="codex",
    )
    monitor.transcript_dir = session_dir

    assert monitor.process_updates() is None
