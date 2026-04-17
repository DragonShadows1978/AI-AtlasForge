"""Tests for WebProxy.supervisor — dashboard-side auto-start helper."""

from __future__ import annotations

import sys
from pathlib import Path

AF_ROOT = Path(__file__).resolve().parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

import importlib

import pytest


@pytest.fixture()
def supervisor(monkeypatch):
    """Fresh supervisor module per test with global state reset."""
    import WebProxy.supervisor as sv
    importlib.reload(sv)
    # Reset the module-level managed-PID state so tests don't bleed.
    sv._managed_pid = None
    sv._managed_proc = None
    return sv


def test_disabled_via_env(supervisor, monkeypatch):
    """When ATLASFORGE_DISABLE_PROXY_AUTOSTART is set, ensure_proxy_running
    must be a no-op returning status=disabled."""
    monkeypatch.setenv("ATLASFORGE_DISABLE_PROXY_AUTOSTART", "1")
    result = supervisor.ensure_proxy_running()
    assert result["status"] == "disabled"
    assert result["pid"] is None


def test_already_running_short_circuits(supervisor, monkeypatch):
    """If port is already listening, don't spawn; return already_running."""
    monkeypatch.delenv("ATLASFORGE_DISABLE_PROXY_AUTOSTART", raising=False)
    monkeypatch.setattr(supervisor, "_port_listening", lambda h, p, timeout=0.5: True)

    spawn_called = {"n": 0}

    def _fake_popen(*a, **kw):
        spawn_called["n"] += 1
        raise AssertionError("Popen must not be called when port is up")

    monkeypatch.setattr(supervisor.subprocess, "Popen", _fake_popen)

    result = supervisor.ensure_proxy_running(port=9999)
    assert result["status"] == "already_running"
    assert spawn_called["n"] == 0


def test_spawn_failure_returns_failed(supervisor, monkeypatch):
    """If Popen raises OSError, return status=failed without leaving stale PID state."""
    monkeypatch.delenv("ATLASFORGE_DISABLE_PROXY_AUTOSTART", raising=False)
    monkeypatch.setattr(supervisor, "_port_listening", lambda h, p, timeout=0.5: False)

    def _raise(*a, **kw):
        raise OSError("no fork for you")

    monkeypatch.setattr(supervisor.subprocess, "Popen", _raise)

    result = supervisor.ensure_proxy_running(wait_for_health=False)
    assert result["status"] == "failed"
    assert supervisor._managed_pid is None


def test_stop_proxy_noop_when_not_managed(supervisor):
    """stop_proxy must not crash when we never launched anything."""
    result = supervisor.stop_proxy()
    assert result["status"] == "noop"
    assert result["pid"] is None


def test_stop_proxy_terminates_managed_subprocess(supervisor, monkeypatch):
    """When we DO own a subprocess, stop_proxy must call terminate() on it."""

    class _FakeProc:
        def __init__(self):
            self.pid = 12345
            self.terminated = False
            self.killed = False
            self._alive = True

        def poll(self):
            return None if self._alive else 0

        def terminate(self):
            self.terminated = True
            self._alive = False

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.killed = True

    fake = _FakeProc()
    supervisor._managed_pid = fake.pid
    supervisor._managed_proc = fake

    result = supervisor.stop_proxy()
    assert result["status"] == "stopped"
    assert fake.terminated is True
    # Idempotency: second call is a noop.
    assert supervisor.stop_proxy()["status"] == "noop"
