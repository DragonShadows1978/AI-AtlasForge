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
  python3 << 'PATCH'           (python3 heredoc ANY delimiter - bypasses Write/Edit tools)
  python3 << 'EOF'             (python3 heredoc EOF delimiter)
  bash << 'EOF'                (bash heredoc stdin execution)
  python3 -c "open(...).write" (in-process file write via -c flag)
  python3 /dev/stdin           (stdin redirect to python interpreter)
"""

import json
import re
import subprocess
import sys


# Patterns that indicate a Bash command is writing file content directly.
# We care about writes to actual source/config files, not stdout piping to commands.
_FILE_WRITE_PATTERNS = [
    # heredoc into cat/tee targeting a named file (any delimiter, not just EOF)
    r'(cat|tee)\s+["\']?[\w./\-]+\.[a-zA-Z]+["\']?\s*<<',
    # heredoc with > redirect: cmd > file << WORD  or  cmd << WORD > file
    # Generalized from EOF-only to any heredoc delimiter word
    r'<<\s*["\']?\w+["\']?\s*>\s*["\']?[\w./\-]+\.[a-zA-Z]+',
    r'>\s*["\']?[\w./\-]+\.[a-zA-Z]+["\']?\s*<<\s*["\']?\w+',
    # echo/printf redirect to a named file with extension
    r'(echo|printf)\s+.{1,500}>\s*["\']?[\w./\-]+\.[a-zA-Z]+["\']?',
    # python3 -c "..." > file  or  python -c "..." > file
    r'python3?\s+-c\s+["\'].+["\'].{0,100}>\s*["\']?[\w./\-]+\.[a-zA-Z]+',
    # NEW: python/pypy interpreter invoked with heredoc stdin (ANY delimiter word).
    # Catches: python3 << 'PATCH', python2 << EOF, python << SCRIPT, pypy3 << 'END'
    # python[23]? matches python, python2, python3. (?:-\w+\s+)* handles flags like -u.
    r'(?:[/\w]*/)?(?:python[23]?|pypy3?)\s+(?:-\w+\s+)*<<\s*["\']?\w',
    # NEW: python3 -c with open() call — writes files in-process without redirect.
    r'python3?\s+-c\s+["\'][^"\']*open\s*\(',
    # NEW: python interpreter reading from /dev/stdin (equivalent to heredoc pipe).
    r'(?:[/\w]*/)?python[23]?\s+/dev/stdin',
    # NEW: bash/sh/zsh invoked with heredoc stdin — runs an arbitrary script block.
    r'(?:bash|sh|zsh)\s+<<\s*["\']?\w',
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

    # If no Conductor is running, there are no active missions and no stage
    # gates to enforce — allow everything through.
    try:
        result = subprocess.run(["pgrep", "-f", "atlasforge_conductor.py"], capture_output=True)
        conductor_running = result.returncode == 0
    except Exception:
        conductor_running = False

    if not conductor_running:
        sys.exit(0)

    if _detect_file_write(command):
        reason = (
            "============================================================\n"
            "BASH WRITE GUARD: File write via shell bypass BLOCKED\n"
            "============================================================\n"
            "\n"
            "You attempted to write a file using a Bash heredoc, output\n"
            "redirection, or interpreter stdin-piping. Examples:\n"
            "  cat > file.py << 'EOF'\n"
            "  python3 << 'PATCH'\n"
            "  echo '...' > file.py\n"
            "  python3 -c \"open('file','w').write(...)\"\n"
            "\n"
            "ALL of these techniques bypass the AfterImage PreToolUse hook\n"
            "and prevent code from being stored in the knowledge base.\n"
            "\n"
            "THIS IS AN ABSOLUTE VIOLATION. Bypassing AfterImage is\n"
            "explicitly prohibited in GROUND_RULES.md. The friction in\n"
            "the AfterImage write flow is INTENTIONAL — it forces you to\n"
            "see relevant past patterns before writing, and ensures every\n"
            "write enters the KB for future agents to recall.\n"
            "\n"
            "USE THE WRITE OR EDIT TOOLS INSTEAD:\n"
            "  - Write: create or fully replace a file\n"
            "  - Edit: replace a specific string within a file\n"
            "\n"
            "If AfterImage denies your first attempt, READ the context\n"
            "it provides, then RETRY the same Write/Edit. The retry\n"
            "will be allowed — this is the intended workflow.\n"
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
