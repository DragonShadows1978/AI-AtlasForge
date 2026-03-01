#!/usr/bin/env python3
"""
Stage Gate Hook: Enforces per-stage write-path restrictions for AtlasForge R&D.

This is a PreToolUse hook that provides defense-in-depth for stage enforcement.
Layer 1 (primary): --disallowedTools CLI flag blocks tools entirely per stage.
Layer 2 (this hook): Validates write PATHS for tools that are allowed but restricted.

For example, during PLANNING stage, Write/Edit are allowed (to create plans in
artifacts/) but should NOT be able to create .py files or write outside artifacts/.

CONDUCTOR AWARENESS: If no Conductor process is running (lock file absent or stale PID),
all enforcement is bypassed. This prevents blocking normal Claude Code terminal usage.

Hook protocol:
- Reads JSON from stdin with tool_name and tool_input
- For Write/Edit: checks file_path against stage rules
- For Bash: checks command against destructive patterns during restricted stages
- Outputs JSON with permissionDecision: "deny" if violation detected
- Exits silently (no output) if operation is allowed
"""
import json
import sys
import os
import fnmatch
import datetime
from pathlib import Path

# Stage -> write path rules
# Only stages with restrictions are listed; BUILDING/TESTING have no restrictions.
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


def _debug_log(entry: dict) -> None:
    """Write a single-line JSON debug record to stderr.

    Only active when ATLASFORGE_HOOK_DEBUG env var is set.
    Stderr is safe for diagnostics - hook protocol uses stdout only.
    """
    if not os.environ.get("ATLASFORGE_HOOK_DEBUG"):
        return
    entry["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    entry["hook"] = "stage_gate"
    print(json.dumps(entry), file=sys.stderr)


def is_conductor_running() -> bool:
    """Check if AtlasForge Conductor is actively running.

    Uses the conductor lock file + PID liveness check.
    Returns False (bypass enforcement) if conductor is not running.
    """
    import errno
    af_root = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    lock_path = Path(af_root) / "atlasforge_conductor.lock"

    if not lock_path.exists():
        return False

    try:
        data = json.loads(lock_path.read_text())
        pid = data.get("pid")
        if not pid:
            return False
        pid_int = int(pid)
        # PIDs must be positive integers; kill(-1,0) broadcasts to all processes on Linux
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


def get_current_stage():
    """Read current stage. Returns 'BUILDING' (permissive) if conductor not running."""

    # CONDUCTOR AWARENESS: If conductor is not running, never enforce restrictions
    if not is_conductor_running():
        return "BUILDING"

    # Conductor is running - get stage from lock file (most accurate, updated on transitions)
    af_root = os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")
    lock_path = Path(af_root) / "atlasforge_conductor.lock"
    mission_path = Path(af_root) / "state" / "mission.json"

    try:
        lock_data = json.loads(lock_path.read_text())
        lock_stage = lock_data.get("current_stage")
        lock_mission_id = lock_data.get("mission_id", "")

        # Cross-check: if lock belongs to a different (completed) mission, use mission.json
        if mission_path.exists() and lock_mission_id:
            try:
                mission_data = json.loads(mission_path.read_text())
                current_mission_id = mission_data.get("mission_id", "")
                if current_mission_id and current_mission_id != lock_mission_id:
                    # Lock is stale (from prior completed mission) - use mission.json stage
                    raw_stage = mission_data.get("current_stage", "BUILDING")
                    normalized = raw_stage.strip().upper()
                    _debug_log({"event": "stage_normalized", "raw": raw_stage, "normalized": normalized,
                                "source": "mission_json_stale_lock", "lock_mission": lock_mission_id,
                                "current_mission": current_mission_id})
                    return normalized
            except (json.JSONDecodeError, IOError):
                pass

        if lock_stage:
            normalized = lock_stage.strip().upper()
            _debug_log({"event": "stage_normalized", "raw": lock_stage, "normalized": normalized, "source": "lock_file"})
            return normalized
    except (json.JSONDecodeError, IOError):
        pass

    # Fall back to mission.json if lock doesn't have stage
    if mission_path.exists():
        try:
            data = json.loads(mission_path.read_text())
            raw_stage = data.get("current_stage", "BUILDING")
            normalized = raw_stage.strip().upper()
            _debug_log({"event": "stage_normalized", "raw": raw_stage, "normalized": normalized, "source": "mission_json"})
            return normalized
        except (json.JSONDecodeError, IOError):
            pass

    return "BUILDING"  # Default to least restrictive


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


def check_write_edit(tool_name: str, tool_input: dict, stage: str) -> dict:
    """Check Write/Edit operations against stage rules."""
    rules = STAGE_WRITE_RULES.get(stage)
    if rules is None:
        return None  # No restrictions for this stage

    file_path = tool_input.get("file_path", "")
    if not file_path:
        return None

    if not is_path_allowed(file_path, rules):
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"STAGE GATE VIOLATION: Cannot {tool_name} to '{file_path}' "
                    f"during {stage} stage. Only writes to "
                    f"{rules.get('allowed_patterns', [])} are allowed. "
                    f"Code files ({rules.get('forbidden_extensions', [])}) are forbidden."
                )
            }
        }
    return None


def check_bash(tool_input: dict, stage: str) -> dict:
    """Check Bash commands for destructive operations during restricted stages."""
    if stage != "PLANNING":
        return None  # Only restrict Bash during PLANNING

    command = tool_input.get("command", "")
    if not command:
        return None

    # Normalize whitespace to prevent double-space bypass (e.g. "pip  install")
    cmd_lower = ' '.join(command.lower().split())
    for pattern in PLANNING_BASH_FORBIDDEN_PATTERNS:
        if pattern.lower() in cmd_lower:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        f"STAGE GATE VIOLATION: Bash command '{command[:100]}' "
                        f"appears to modify state during PLANNING stage. "
                        f"Only read-only commands (ls, git status, cat, etc.) are allowed. "
                        f"Matched forbidden pattern: '{pattern}'"
                    )
                }
            }
    return None


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, IOError):
        _debug_log({"event": "error", "msg": "stdin parse failed"})
        sys.exit(0)

    tool_name = input_data.get("tool_name", "")
    tool_input = input_data.get("tool_input", {})

    stage = get_current_stage()
    _debug_log({"event": "stage_read", "stage": stage, "tool": tool_name})

    # Check Write/Edit path restrictions
    if tool_name in ("Write", "Edit"):
        result = check_write_edit(tool_name, tool_input, stage)
        if result:
            _debug_log({"event": "deny", "tool": tool_name, "stage": stage,
                        "reason": result["hookSpecificOutput"]["permissionDecisionReason"][:120]})
            print(json.dumps(result))
            sys.exit(0)

    # Check Bash command restrictions
    if tool_name == "Bash":
        result = check_bash(tool_input, stage)
        if result:
            _debug_log({"event": "deny", "tool": tool_name, "stage": stage,
                        "reason": result["hookSpecificOutput"]["permissionDecisionReason"][:120]})
            print(json.dumps(result))
            sys.exit(0)

    # Allowed - exit silently
    _debug_log({"event": "allow", "tool": tool_name, "stage": stage})
    sys.exit(0)


if __name__ == "__main__":
    main()
