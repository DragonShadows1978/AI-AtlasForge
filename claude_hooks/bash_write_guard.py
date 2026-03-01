#!/usr/bin/env python3
"""
Bash Write Guard Hook v1.0

Prevents bypassing the AfterImage pre-write hook by using Bash heredocs
or output redirections to write files directly (e.g. cat > file << 'EOF').

This hook fires on PreToolUse:Bash and blocks any command that attempts
to write file content via shell redirection patterns. The model must use
the Write or Edit tools instead, which go through the AfterImage hook chain
and get stored in the PostgreSQL KB.

Blocked patterns:
  cat > file.py << 'EOF'       (heredoc write)
  cat >> file.py << EOF        (heredoc append)
  tee file.py << 'EOF'         (tee heredoc)
  echo "..." > file.py         (echo redirect)
  printf "..." > file.py       (printf redirect)
  python3 -c "..." > file.py   (python redirect)
"""

import json
import re
import sys


# Patterns that indicate a Bash command is writing file content directly.
# We care about writes to actual source/config files, not stdout piping to commands.
_FILE_WRITE_PATTERNS = [
    # heredoc into cat/tee targeting a named file
    r'(cat|tee)\s+["\']?[\w./\-]+\.[a-zA-Z]+["\']?\s*<<',
    # heredoc with > redirect: cmd > file << EOF  or  cmd << EOF > file
    r'<<\s*["\']?EOF["\']?\s*>\s*["\']?[\w./\-]+\.[a-zA-Z]+',
    r'>\s*["\']?[\w./\-]+\.[a-zA-Z]+["\']?\s*<<\s*["\']?EOF',
    # echo/printf redirect to a named file with extension
    r'(echo|printf)\s+.{1,500}>\s*["\']?[\w./\-]+\.[a-zA-Z]+["\']?',
    # python3 -c "..." > file  or  python -c "..." > file
    r'python3?\s+-c\s+["\'].+["\'].{0,100}>\s*["\']?[\w./\-]+\.[a-zA-Z]+',
]

_COMPILED = [re.compile(p, re.DOTALL) for p in _FILE_WRITE_PATTERNS]


def _detect_file_write(command: str) -> bool:
    """Return True if the command appears to write file content via shell."""
    for pattern in _COMPILED:
        if pattern.search(command):
            return True
    return False


def main():
    try:
        input_data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)

    hook_event = input_data.get("hook_event_name", "")
    tool_name = input_data.get("tool_name", "")

    if hook_event != "PreToolUse" or tool_name != "Bash":
        sys.exit(0)

    command = input_data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    if _detect_file_write(command):
        reason = (
            "============================================================\n"
            "BASH WRITE GUARD: File write via shell redirection blocked\n"
            "============================================================\n"
            "\n"
            "You attempted to write a file using a Bash heredoc or output\n"
            "redirection (e.g. cat > file << 'EOF' or echo ... > file).\n"
            "\n"
            "This bypasses the AfterImage pre-write hook and prevents the\n"
            "code from being stored in the knowledge base.\n"
            "\n"
            "Use the Write or Edit tools instead:\n"
            "  - Write: create or fully replace a file\n"
            "  - Edit: replace a specific string within a file\n"
            "\n"
            "These tools go through the full AfterImage hook chain and\n"
            "ensure your code is stored for future recall.\n"
            "============================================================\n"
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason
            }
        }
        print(json.dumps(output))
        sys.exit(0)

    sys.exit(0)


if __name__ == "__main__":
    main()
