#!/usr/bin/env python3
"""
pre_tool_use.py - AtlasForge PreToolUse hook for stage-gate enforcement.

Reads current stage from atlasforge_conductor.lock, normalizes via .upper(),
and enforces STAGE_WRITE_RULES. Writes structured JSON to stderr for every
decision (blocked and allowed). Bypasses enforcement when conductor is not running.

Hook protocol:
- Reads JSON from stdin with tool_name and tool_input
- For Write/Edit: checks file_path against stage rules
- For Bash: checks command against destructive patterns during restricted stages
- Outputs JSON with permissionDecision: "deny" to stdout if violation detected
- Exits silently (no output) if operation is allowed

Structured stderr log format (one JSON object per line):
  {"ts": "...", "hook": "pre_tool_use", "stage": "ANALYZING", "raw_stage": "analyzing",
   "tool": "Write", "path": "...", "decision": "deny"|"allow", "reason": "..."}
"""
import json
import sys
import os
import errno
import fnmatch
import datetime
from pathlib import Path


# Stage -> write path rules.
# Stages absent from this dict (BUILDING, TESTING) have no restrictions.
STAGE_WRITE_RULES = {
    "PLANNING": {
        "allowed_patterns": [
            "*/artifacts/*",
            "*/research/*",
            "*implementation_plan.md",
        ],
        "forbidden_extensions": [
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".java", ".c", ".cpp", ".h", ".hpp", ".cs", ".rb",
            ".sh", ".bash", ".zsh", ".fish",
        ],
    },
    "ANALYZING": {
        "allowed_patterns": [
            "*/artifacts/*",
            "*/research/*",
            "*analysis.md",
            "*report.md",
            "*test_results.md",
        ],
        "forbidden_extensions": [
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".java", ".c", ".cpp", ".h", ".hpp",
        ],
    },
    "CYCLE_END": {
        "allowed_patterns": [
            "*/artifacts/*",
            "*/research/*",
            "*report.md",
            "*report.json",
            "*cycle_report*",
            "*mission_logs/*",
        ],
        "forbidden_extensions": [
            ".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs",
            ".java", ".c", ".cpp", ".h", ".hpp",
        ],
    },
    "COMPLETE": {
        "allowed_patterns": [],  # No writes allowed at all
        "forbidden_extensions": ["*"],  # Block everything
    },
}

# Bash commands that modify state (blocked during PLANNING)
PLANNING_BASH_FORBIDDEN_PATTERNS = [
    "pip install", "pip3 install",
    "npm install", "yarn add",
    "apt install", "apt-get install",
    "rm ", "rm -", "rmdir",
    "mv ", "cp ",
    "mkdir ",
    "touch ",
    "chmod ", "chown ",
    "> ", ">> ", "tee ",
    "python3 -c", "python -c",
]


def _structured_log(entry: dict) -> None:
    """Write a single-line JSON record to stderr. Always active (not debug-gated).

    Stderr is safe for diagnostics - hook protocol uses stdout only.
    """
    entry["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry["hook"] = "pre_tool_use"
    print(json.dumps(entry), file=sys.stderr)


def is_conductor_running(lock_path: Path) -> bool:
    """Check if AtlasForge Conductor is actively running via PID liveness check.

    Returns False (bypass enforcement) if conductor is not running.
    """
    if not lock_path.exists():
        return False

    try:
        data = json.loads(lock_path.read_text())
        pid = data.get("pid")
        if not pid:
            return False
        pid_int = int(pid)
        # PIDs must be positive integers; kill(-1, 0) broadcasts to all processes
        if pid_int <= 0:
            return False
        # os.kill(pid, 0) checks if process is alive without sending a signal
        os.kill(pid_int, 0)
        return True  # Process exists
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False  # No such process - stale lock
        if e.errno == errno.EPERM:
            return True   # Process exists but we lack permission to signal it
        return False
    except (json.JSONDecodeError, IOError, ValueError, TypeError):
        return False  # Unreadable lock - don't enforce


def get_stage_from_lock(lock_path: Path) -> tuple:
    """Read and normalize stage from lock file.

    Returns (normalized_stage, raw_stage) tuple.
    normalized_stage is always uppercase.
    Returns ('BUILDING', '') on any error (least restrictive default).

    Cross-checks lock mission_id against mission.json: if they differ, the lock
    is stale (from a prior mission) and we fall back to mission.json's stage.
    """
    af_root = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    mission_path = Path(af_root) / "state" / "mission.json"
    try:
        lock_data = json.loads(lock_path.read_text())
        lock_stage = lock_data.get("current_stage", "")
        lock_mission_id = lock_data.get("mission_id", "")

        # Cross-check: if lock belongs to a different (completed) mission, use mission.json
        if mission_path.exists() and lock_mission_id:
            try:
                mission_data = json.loads(mission_path.read_text())
                current_mission_id = mission_data.get("mission_id", "")
                if current_mission_id and current_mission_id != lock_mission_id:
                    # Lock is stale - belongs to prior mission, use mission.json stage
                    raw_stage = mission_data.get("current_stage", "BUILDING")
                    return raw_stage.strip().upper(), raw_stage
            except (json.JSONDecodeError, IOError):
                pass

        if lock_stage:
            return lock_stage.strip().upper(), lock_stage
    except (json.JSONDecodeError, IOError):
        pass
    # Fall back to mission.json
    try:
        if mission_path.exists():
            mission_data = json.loads(mission_path.read_text())
            raw_stage = mission_data.get("current_stage", "BUILDING")
            return raw_stage.strip().upper(), raw_stage
    except (json.JSONDecodeError, IOError):
        pass
    return "BUILDING", ""


def is_path_allowed(file_path: str, rules: dict) -> bool:
    """Check if file_path is allowed by the stage rules."""
    file_path_lower = file_path.lower()

    # Check forbidden extensions first (case-insensitive to prevent .PY bypass)
    for ext in rules.get("forbidden_extensions", []):
        if ext == "*":
            return False
        if file_path_lower.endswith(ext.lower()):
            return False

    # Check allowed patterns
    allowed = rules.get("allowed_patterns", [])
    if not allowed:
        return False  # No patterns = no writes allowed
    return any(fnmatch.fnmatch(file_path, p) for p in allowed)


def check_write_edit(tool_name, tool_input, stage, raw_stage):
    """Check Write/Edit operations against stage rules.

    Returns (deny_response_or_None, reason_string).
    """
    rules = STAGE_WRITE_RULES.get(stage)
    if rules is None:
        return None, "no_restrictions"

    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None, "no_path"

    if not is_path_allowed(file_path, rules):
        reason = (
            f"STAGE GATE VIOLATION: Cannot {tool_name} to '{file_path}' "
            f"during {stage} stage (raw: '{raw_stage}'). Only writes to "
            f"{rules.get('allowed_patterns', [])} are allowed. "
            f"Code files ({rules.get('forbidden_extensions', [])}) are forbidden."
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }, reason

    return None, "allowed"


def check_bash(tool_input, stage, raw_stage):
    """Check Bash commands for destructive operations during restricted stages.

    Returns (deny_response_or_None, reason_string).
    """
    if stage != "PLANNING":
        return None, "no_restrictions"

    command = tool_input.get("command", "")
    if not command:
        return None, "no_command"

    # Normalize whitespace to prevent double-space bypass (e.g. "pip  install")
    cmd_lower = " ".join(command.lower().split())
    for pattern in PLANNING_BASH_FORBIDDEN_PATTERNS:
        if pattern.lower() in cmd_lower:
            reason = (
                f"STAGE GATE VIOLATION: Bash command '{command[:100]}' "
                f"appears to modify state during PLANNING stage (raw: '{raw_stage}'). "
                f"Only read-only commands are allowed. "
                f"Matched forbidden pattern: '{pattern}'"
            )
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }, reason

    return None, "allowed"


def main():
    af_root = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    lock_path = Path(af_root) / "atlasforge_conductor.lock"

    # Parse stdin
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, IOError):
        _structured_log({"event": "error", "msg": "stdin parse failed"})
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    # Conductor liveness check - bypass if not running
    if not is_conductor_running(lock_path):
        _structured_log({
            "stage": "BYPASS", "raw_stage": "",
            "tool": tool_name, "path": tool_input.get("file_path", ""),
            "decision": "allow", "reason": "conductor_not_running",
        })
        sys.exit(0)

    # Get and normalize stage
    stage, raw_stage = get_stage_from_lock(lock_path)
    file_path = tool_input.get("file_path", tool_input.get("command", "")[:80])

    # Check Write/Edit path restrictions
    if tool_name in ("Write", "Edit"):
        deny_response, reason = check_write_edit(tool_name, tool_input, stage, raw_stage)
        decision = "deny" if deny_response else "allow"
        _structured_log({
            "stage": stage, "raw_stage": raw_stage,
            "tool": tool_name, "path": file_path,
            "decision": decision, "reason": reason[:200],
        })
        if deny_response:
            print(json.dumps(deny_response))
            sys.exit(0)

    # Check Bash command restrictions
    elif tool_name == "Bash":
        deny_response, reason = check_bash(tool_input, stage, raw_stage)
        decision = "deny" if deny_response else "allow"
        _structured_log({
            "stage": stage, "raw_stage": raw_stage,
            "tool": tool_name, "path": file_path,
            "decision": decision, "reason": reason[:200],
        })
        if deny_response:
            print(json.dumps(deny_response))
            sys.exit(0)

    else:
        # Log non-restricted tool calls too
        _structured_log({
            "stage": stage, "raw_stage": raw_stage,
            "tool": tool_name, "path": "",
            "decision": "allow", "reason": "tool_not_restricted",
        })

    # Allowed - exit silently
    sys.exit(0)


if __name__ == "__main__":
    main()
