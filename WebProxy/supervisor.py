"""Dashboard-side auto-start helper for the local web proxy.

When the dashboard starts, `ensure_proxy_running()` spawns the proxy
subprocess if nothing is already listening on its port. On dashboard
shutdown, the atexit-registered `stop_proxy()` SIGTERMs the subprocess
— but ONLY if we were the one that started it. Proxies running under
systemd or started in another shell are left alone.

Opt out entirely by setting ATLASFORGE_DISABLE_PROXY_AUTOSTART=1.

Public API:
    ensure_proxy_running(host, port, wait_for_health, health_timeout_s) -> dict
    stop_proxy() -> dict
"""

from __future__ import annotations

import atexit
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

_ATLASFORGE_ROOT = Path(__file__).resolve().parent.parent

# PID of the proxy subprocess WE launched. stop_proxy() only touches this
# PID — a systemd-managed or user-launched proxy is never our concern.
_managed_pid: Optional[int] = None
_managed_proc: Optional[subprocess.Popen] = None


def _port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """Return True iff TCP connect to (host, port) succeeds quickly."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def _wait_for_health(host: str, port: int, timeout_s: float) -> bool:
    """Poll GET /health until 200 or timeout. Returns True on success."""
    if requests is None:
        # Fallback: plain socket-listen check as weak evidence of life.
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if _port_listening(host, port):
                return True
            time.sleep(0.1)
        return False
    url = f"http://{host}:{port}/health"
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            resp = requests.get(url, timeout=1.0)
            if resp.status_code == 200:
                return True
        except requests.exceptions.RequestException:
            pass
        time.sleep(0.1)
    return False


def ensure_proxy_running(
    host: str = "127.0.0.1",
    port: int = 8765,
    wait_for_health: bool = True,
    health_timeout_s: float = 5.0,
) -> dict:
    """Start the proxy subprocess if not already running.

    No-op if ATLASFORGE_DISABLE_PROXY_AUTOSTART is set (for users who
    prefer the systemd unit).

    Returns a status dict:
        {'status': 'started'|'already_running'|'disabled'|'failed',
         'pid': int|None, 'detail': str}
    """
    global _managed_pid, _managed_proc

    if os.environ.get("ATLASFORGE_DISABLE_PROXY_AUTOSTART"):
        return {
            "status": "disabled",
            "pid": None,
            "detail": "ATLASFORGE_DISABLE_PROXY_AUTOSTART is set; leaving proxy alone.",
        }

    if _port_listening(host, port):
        return {
            "status": "already_running",
            "pid": None,
            "detail": f"port {port} already accepting connections",
        }

    log_dir = _ATLASFORGE_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "web_proxy.log"

    cmd = [
        sys.executable,
        "-m",
        "WebProxy.service",
        "--host",
        host,
        "--port",
        str(port),
    ]

    try:
        log_fh = open(log_path, "ab", buffering=0)
    except OSError as exc:
        return {
            "status": "failed",
            "pid": None,
            "detail": f"cannot open {log_path}: {exc}",
        }

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_ATLASFORGE_ROOT),
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log_fh.close()
        return {
            "status": "failed",
            "pid": None,
            "detail": f"spawn failed: {exc}",
        }

    _managed_pid = proc.pid
    _managed_proc = proc
    atexit.register(stop_proxy)

    if wait_for_health and not _wait_for_health(host, port, health_timeout_s):
        return {
            "status": "failed",
            "pid": proc.pid,
            "detail": (
                f"proxy spawned (pid={proc.pid}) but /health did not respond "
                f"within {health_timeout_s}s; see {log_path}"
            ),
        }

    return {
        "status": "started",
        "pid": proc.pid,
        "detail": f"proxy pid={proc.pid} log={log_path}",
    }


def stop_proxy() -> dict:
    """SIGTERM the proxy subprocess if we own it. Idempotent."""
    global _managed_pid, _managed_proc

    if _managed_pid is None:
        return {"status": "noop", "pid": None, "detail": "no managed proxy"}

    pid = _managed_pid
    proc = _managed_proc
    _managed_pid = None
    _managed_proc = None

    try:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)
        else:
            os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as exc:
        return {
            "status": "already_gone",
            "pid": pid,
            "detail": str(exc),
        }

    return {"status": "stopped", "pid": pid, "detail": "SIGTERM delivered"}
