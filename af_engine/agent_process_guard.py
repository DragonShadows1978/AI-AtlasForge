"""Process-kill guard helpers for autonomous mission agents."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Iterable, Optional, Sequence


PROTECTED_PROCESS_TERMS = (
    "dashboard_v2.py",
    "atlasforge_conductor.py",
    "atlasforge-dashboard",
    "atlasforge-tray",
    "atlasforge.service",
    "atlasforge_conductor",
)

MUTATING_SYSTEMCTL_VERBS = frozenset(
    {
        "disable",
        "enable",
        "isolate",
        "kill",
        "mask",
        "reload",
        "reload-or-restart",
        "reset-failed",
        "restart",
        "reenable",
        "start",
        "stop",
        "try-reload-or-restart",
        "try-restart",
        "unmask",
    }
)


def _joined_args(args: Iterable[str]) -> str:
    return " ".join(str(arg) for arg in args).lower()


def _mentions_atlasforge(text: str) -> bool:
    return bool(re.search(r"(^|[^a-z0-9_])atlasforge($|[^a-z0-9_])", text))


def targets_protected_process(args: Sequence[str]) -> bool:
    """Return True when command args target AtlasForge-owned processes."""
    joined = _joined_args(args)
    if any(term in joined for term in PROTECTED_PROCESS_TERMS):
        return True
    return _mentions_atlasforge(joined)


def blocked_reason(command: str, args: Sequence[str]) -> Optional[str]:
    """Return a human-readable block reason, or None when command is allowed."""
    cmd = Path(command).name
    if cmd in {"pkill", "killall"} and targets_protected_process(args):
        return (
            f"AtlasForge process guard blocked `{cmd}` because its target matches "
            "the live AtlasForge dashboard/conductor service."
        )

    if cmd == "systemctl":
        normalized = [str(arg).lower() for arg in args]
        mutates_service = any(arg in MUTATING_SYSTEMCTL_VERBS for arg in normalized)
        if mutates_service and targets_protected_process(args):
            return (
                "AtlasForge process guard blocked a mutating systemctl command "
                "against an AtlasForge service."
            )

    return None


def _real_command_path(command: str) -> str:
    name = Path(command).name
    for parent in ("/usr/bin", "/bin", "/usr/sbin", "/sbin"):
        candidate = Path(parent) / name
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(f"Could not locate real `{name}` outside agent_safe_bin")


def main(command: Optional[str] = None, argv: Optional[Sequence[str]] = None) -> int:
    command = command or Path(sys.argv[0]).name
    args = list(sys.argv[1:] if argv is None else argv)
    reason = blocked_reason(command, args)
    if reason:
        print(reason, file=sys.stderr)
        return 64

    real_command = _real_command_path(command)
    os.execv(real_command, [real_command, *args])
    return 127
