from pathlib import Path

import pytest

import investigation_engine
from investigation_validator import validator_agent


def _write_executable(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    path.chmod(0o755)
    return path


def test_resolver_prefers_valid_user_cli_over_broken_path_entry(monkeypatch, tmp_path):
    user_cli = _write_executable(
        tmp_path / "user-bin" / "claude",
        b"#!/bin/sh\nexit 0\n",
    )
    broken_global = _write_executable(
        tmp_path / "system-bin" / "claude",
        b'echo "native binary not installed" >&2\nexit 1\n',
    )

    monkeypatch.delenv("ATLASFORGE_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        investigation_engine,
        "_user_local_cli_path",
        lambda provider: user_cli,
    )
    monkeypatch.setattr(
        investigation_engine.shutil,
        "which",
        lambda name: str(broken_global) if name == "claude" else None,
    )

    assert investigation_engine._resolve_cli_executable("claude") == str(user_cli.resolve())


def test_resolver_rejects_executable_ascii_fallback_without_shebang(monkeypatch, tmp_path):
    broken_global = _write_executable(
        tmp_path / "system-bin" / "claude",
        b'echo "native binary not installed" >&2\nexit 1\n',
    )

    monkeypatch.delenv("ATLASFORGE_CLAUDE_BIN", raising=False)
    monkeypatch.setattr(
        investigation_engine,
        "_user_local_cli_path",
        lambda provider: tmp_path / "missing-user-bin" / provider,
    )
    monkeypatch.setattr(
        investigation_engine.shutil,
        "which",
        lambda name: str(broken_global) if name == "claude" else None,
    )

    with pytest.raises(FileNotFoundError, match="No usable claude CLI executable"):
        investigation_engine._resolve_cli_executable("claude")


def test_explicit_provider_binary_override_wins(monkeypatch, tmp_path):
    override = _write_executable(
        tmp_path / "custom" / "claude",
        b"#!/bin/sh\nexit 0\n",
    )

    monkeypatch.setenv("ATLASFORGE_CLAUDE_BIN", str(override))
    monkeypatch.setattr(
        investigation_engine,
        "_user_local_cli_path",
        lambda provider: tmp_path / "missing-user-bin" / provider,
    )
    monkeypatch.setattr(investigation_engine.shutil, "which", lambda _name: None)

    assert investigation_engine._resolve_cli_executable("claude") == str(override.resolve())


def test_validator_uses_resolved_claude_executable(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return investigation_engine.subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(
        investigation_engine,
        "_resolve_cli_executable",
        lambda provider: "/validated/bin/claude",
    )
    monkeypatch.setattr(validator_agent.subprocess, "run", fake_run)

    response, _elapsed = validator_agent._invoke_claude("validate this")

    assert response == "ok"
    assert captured["cmd"][0] == "/validated/bin/claude"
