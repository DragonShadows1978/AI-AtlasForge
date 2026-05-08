"""
Dashboard Module: Conductor Status

Provides the /api/conductor/* routes used by the Conductor Status widget.

This replaces the legacy workspace.ConductorTakeover dashboard API, which lived
under the ignored workspace tree and can disappear from installs/checkouts.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify

conductor_status_bp = Blueprint("conductor_status", __name__)

_BASE_DIR: Path | None = None
_STATE_DIR: Path | None = None
_LOG_DIR: Path | None = None


def init_conductor_status_blueprint(base_dir: Path, state_dir: Path, log_dir: Path) -> None:
    """Initialize filesystem locations used by the conductor status routes."""
    global _BASE_DIR, _STATE_DIR, _LOG_DIR
    _BASE_DIR = Path(base_dir)
    _STATE_DIR = Path(state_dir)
    _LOG_DIR = Path(log_dir)


def _base_dir() -> Path:
    return _BASE_DIR or Path(__file__).resolve().parents[1]


def _state_dir() -> Path:
    return _STATE_DIR or (_base_dir() / "state")


def _log_dir() -> Path:
    return _LOG_DIR or (_base_dir() / "logs")


def _read_json(path: Path, default: dict) -> dict:
    try:
        if path.exists():
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return default


def _find_conductor_pid() -> int | None:
    """Return the first live atlasforge_conductor.py PID, if any."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "atlasforge_conductor.py"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        for raw_pid in (result.stdout or "").splitlines():
            try:
                pid = int(raw_pid.strip())
            except ValueError:
                continue
            if pid == os.getpid():
                continue
            try:
                raw_cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
                cmd_parts = [part.decode("utf-8", errors="replace") for part in raw_cmdline.split(b"\0") if part]
                if not any(Path(part).name == "atlasforge_conductor.py" for part in cmd_parts):
                    continue
            except Exception:
                continue
            try:
                os.kill(pid, 0)
            except OSError:
                continue
            return pid
    except Exception:
        pass
    return None


def _process_uptime_seconds(pid: int | None) -> int | None:
    if not pid:
        return None
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "etimes="],
            capture_output=True,
            text=True,
            timeout=2,
        )
        raw = result.stdout.strip()
        return int(raw) if raw else None
    except Exception:
        return None


def _path_age_seconds(path: Path) -> float | None:
    try:
        if not path.exists():
            return None
        return max(0.0, datetime.now(timezone.utc).timestamp() - path.stat().st_mtime)
    except Exception:
        return None


def _count_log_occurrences(path: Path, needles: tuple[str, ...]) -> int:
    """Count occurrences in the tail of a text log without loading huge logs."""
    try:
        if not path.exists():
            return 0
        max_bytes = 512 * 1024
        with open(path, "rb") as f:
            try:
                f.seek(-max_bytes, os.SEEK_END)
            except OSError:
                f.seek(0)
            text = f.read().decode("utf-8", errors="replace").lower()
        return sum(text.count(needle) for needle in needles)
    except Exception:
        return 0


def _load_last_jsonl(path: Path) -> dict:
    try:
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            try:
                f.seek(-64 * 1024, os.SEEK_END)
            except OSError:
                f.seek(0)
            lines = f.read().splitlines()
        for raw in reversed(lines):
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except Exception:
                continue
            if isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return {}


@conductor_status_bp.route("/api/conductor/status")
def api_conductor_status():
    """Return the live conductor status consumed by the dashboard widget."""
    pid = _find_conductor_pid()
    mission = _read_json(_state_dir() / "mission.json", {})
    state = _read_json(_state_dir() / "claude_state.json", {})
    lock_path = _base_dir() / "atlasforge_conductor.lock"
    lock_file_exists = lock_path.exists()
    lock_age = _path_age_seconds(lock_path)

    mission_text = mission.get("problem_statement") or mission.get("mission") or ""
    mission_id = mission.get("mission_id") or state.get("mission_id")
    current_stage = mission.get("current_stage") or state.get("current_stage")

    return jsonify({
        "running": pid is not None,
        "pid": pid,
        "hostname": socket.gethostname(),
        "uptime_seconds": _process_uptime_seconds(pid),
        "mission_id": mission_id,
        "current_stage": current_stage,
        "mission_preview": mission_text[:120] if isinstance(mission_text, str) else "",
        "lock_file_exists": lock_file_exists,
        "lock_age_seconds": lock_age,
        "is_stale": bool(lock_file_exists and pid is None),
        "mode": state.get("mode", "unknown"),
        "boot_count": state.get("boot_count", 0),
        "total_cycles": state.get("total_cycles", 0),
        "last_boot": state.get("last_boot"),
    })


@conductor_status_bp.route("/api/conductor/metrics")
def api_conductor_metrics():
    """Return lightweight conductor metrics for the dashboard widget."""
    conductor_log = _log_dir() / "atlasforge_conductor.log"
    auto_advance_log = _log_dir() / "auto_advance_metrics.jsonl"
    last_auto_advance = _load_last_jsonl(auto_advance_log)

    return jsonify({
        "collision_count": _count_log_occurrences(
            conductor_log,
            (
                "another conductor instance is already running",
                "failed to acquire conductor lock",
            ),
        ),
        "log_size_bytes": conductor_log.stat().st_size if conductor_log.exists() else 0,
        "last_auto_advance": last_auto_advance,
        "last_auto_advance_reason": last_auto_advance.get("reason"),
        "last_auto_advance_at": last_auto_advance.get("timestamp"),
    })


@conductor_status_bp.route("/api/conductor/takeover", methods=["POST"])
def api_conductor_takeover():
    """Ask the current conductor process to stop so a new one can be started."""
    pid = _find_conductor_pid()
    if pid is None:
        return jsonify({
            "success": False,
            "message": "Conductor is not running",
        }), 409

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return jsonify({
            "success": True,
            "message": "Conductor was already stopped",
            "pid": pid,
        })
    except Exception as exc:
        return jsonify({
            "success": False,
            "message": f"Failed to signal conductor: {exc}",
            "pid": pid,
        }), 500

    return jsonify({
        "success": True,
        "message": "Shutdown signal sent to conductor",
        "pid": pid,
    })
