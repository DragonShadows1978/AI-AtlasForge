#!/usr/bin/env python3
"""
Exploration Memory hook for Claude Code.

Records successful tool activity into AtlasForge's exploration graph and
decision graph. This hook is intentionally best-effort: failures are logged to
stderr and never block tool execution.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


ATLASFORGE_ROOT = Path(os.environ.get("ATLASFORGE_ROOT", "/home/vader/AI-AtlasForge")).resolve()
if str(ATLASFORGE_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLASFORGE_ROOT))


def _read_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _is_success(payload: dict[str, Any]) -> bool:
    response = payload.get("tool_response")
    if isinstance(response, dict):
        if response.get("is_error") is True:
            return False
        if response.get("error"):
            return False
    return True


def _content_len(payload: dict[str, Any]) -> int:
    response = payload.get("tool_response")
    if isinstance(response, dict):
        content = response.get("content")
        if isinstance(content, str):
            return len(content)
        if isinstance(content, list):
            return sum(len(str(item)) for item in content)
    return 0


def _preview(value: Any, limit: int = 220) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text[:limit] + "..." if len(text) > limit else text


def _record_exploration(exploration_hooks: Any, path: str, summary: str, tags: list[str]) -> None:
    """
    Record to mission-local memory only during active missions.

    Outside active missions, write to global exploration memory so completed
    mission state does not capture ordinary dashboard/debug exploration.
    """
    if not path:
        return
    try:
        if exploration_hooks._current_mission_is_active():
            exploration_hooks.record_exploration(path, summary, tags=tags)
            return

        from atlasforge_enhancements import ExplorationGraph

        graph = ExplorationGraph(storage_path=exploration_hooks.EXPLORATION_DIR)
        graph.add_file_node(path=path, summary=summary, mission_id="global", tags=tags)
        graph.save()
    except Exception as exc:
        print(f"[exploration-memory-hook] record failed for {path}: {exc}", file=sys.stderr)


def main() -> int:
    payload = _read_payload()
    if payload.get("hook_event_name") != "PostToolUse":
        return 0

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    if not isinstance(tool_input, dict):
        tool_input = {}

    success = _is_success(payload)

    try:
        import exploration_hooks

        if tool_name == "Read":
            file_path = tool_input.get("file_path", "")
            lines_read = 0
            if tool_input.get("limit"):
                try:
                    lines_read = int(tool_input.get("limit"))
                except (TypeError, ValueError):
                    lines_read = 0
            exploration_hooks.log_read_tool(file_path, lines_read=lines_read, success=success)
            if success and file_path:
                summary = f"Read via Claude tool; response size {_content_len(payload)} chars"
                _record_exploration(exploration_hooks, file_path, summary, ["read", "claude-tool"])

        elif tool_name == "Write":
            file_path = tool_input.get("file_path", "")
            content = tool_input.get("content", "")
            exploration_hooks.log_write_tool(
                file_path,
                content_length=len(content) if isinstance(content, str) else 0,
                success=success,
            )
            if success and file_path:
                _record_exploration(
                    exploration_hooks,
                    file_path,
                    "Written via Claude tool",
                    ["write", "claude-tool"],
                )

        elif tool_name == "Edit":
            file_path = tool_input.get("file_path", "")
            exploration_hooks.log_edit_tool(
                file_path,
                old_string_preview=_preview(tool_input.get("old_string", "")),
                new_string_preview=_preview(tool_input.get("new_string", "")),
                replace_all=bool(tool_input.get("replace_all", False)),
                success=success,
            )
            if success and file_path:
                _record_exploration(
                    exploration_hooks,
                    file_path,
                    "Edited via Claude tool",
                    ["edit", "claude-tool"],
                )

        elif tool_name == "Bash":
            command = tool_input.get("command", "")
            exploration_hooks.log_bash_tool(command, success=success)

        elif tool_name in ("Grep", "Search"):
            pattern = tool_input.get("pattern") or tool_input.get("query") or ""
            path = tool_input.get("path")
            exploration_hooks.log_grep_tool(pattern, path=path, success=success)

        elif tool_name == "Glob":
            exploration_hooks.log_glob_tool(
                tool_input.get("pattern", ""),
                path=tool_input.get("path"),
                success=success,
            )

        elif tool_name in ("WebFetch", "WebSearch", "Task"):
            exploration_hooks.log_tool_invocation(
                tool_name=tool_name,
                input_summary={k: _preview(v) for k, v in tool_input.items()},
                output_summary={"content_length": _content_len(payload)},
                status="success" if success else "error",
            )
    except Exception as exc:
        print(f"[exploration-memory-hook] {exc}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
