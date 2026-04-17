#!/usr/bin/env python3
"""Rewrite AtlasForge MCP JSON configs to point at the real install root.

The repo ships .mcp.json and WebProxy/configs/mcp.json with a placeholder
path (/home/vader/AI-AtlasForge). On install we must rewrite those paths to
the caller's $ATLASFORGE_ROOT.

This replaces a brittle `sed -i` one-liner. sed-based rewriting breaks when
the target path contains special characters (|, &, \\) or appears as a
prefix of the placeholder (so re-runs double-rewrite). Operating on parsed
JSON is immune to both issues and keeps the files valid regardless of the
path content.

Usage:
    python3 scripts/rewrite_mcp_paths.py <atlasforge_root>

Exit codes:
    0 - success (all files rewritten or already up to date)
    1 - validation error (invalid path, unsafe characters)
    2 - file error (malformed JSON, permission denied)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PLACEHOLDER = "/home/vader/AI-AtlasForge"
TARGET_SCRIPT = "WebProxy/mcp_server.py"
MCP_FILES = (".mcp.json", "WebProxy/configs/mcp.json")


def validate_root(root: Path) -> None:
    """Reject roots that would produce malformed JSON or are unsafe."""
    s = str(root)
    if not root.is_absolute():
        raise ValueError(f"ATLASFORGE_ROOT must be an absolute path, got: {s!r}")
    if "\n" in s or "\r" in s:
        raise ValueError("ATLASFORGE_ROOT contains newline character")
    if '"' in s:
        raise ValueError("ATLASFORGE_ROOT contains double-quote character")
    if not root.is_dir():
        raise ValueError(f"ATLASFORGE_ROOT is not an existing directory: {s!r}")


def _rewrite_args(args: list[str], new_root: str) -> tuple[list[str], bool]:
    """Replace any placeholder-rooted path in args with new_root.

    Returns (new_args, changed). Only anchored replacements are performed:
    the value must either equal the placeholder, or start with placeholder + '/'.
    This prevents prefix-substring corruption (placeholder is a prefix of
    an unrelated path).

    Idempotency note (CRITICAL C3, iter-3 red team): the "already points at
    new_root" check MUST run BEFORE the placeholder-prefix check. When
    new_root is a subdirectory of the placeholder (e.g.
    placeholder=/home/vader/AI-AtlasForge, new_root=/home/vader/AI-AtlasForge/nested),
    a previously-rewritten arg like /home/vader/AI-AtlasForge/nested/foo.py
    still matches PLACEHOLDER+"/" and would get the /nested segment appended
    again on every re-run. Checking new_root first short-circuits the no-op.
    """
    changed = False
    out: list[str] = []
    for arg in args:
        new_arg = arg
        if isinstance(arg, str):
            # No-op check FIRST: arg already rooted at new_root.
            if arg == new_root or arg.startswith(new_root + "/"):
                pass
            elif arg == PLACEHOLDER:
                new_arg = new_root
                changed = True
            elif arg.startswith(PLACEHOLDER + "/"):
                new_arg = new_root + arg[len(PLACEHOLDER):]
                changed = True
        out.append(new_arg)
    return out, changed


def rewrite_mcp_file(path: Path, new_root: str) -> str:
    """Rewrite one MCP JSON file in place.

    Returns a status string: "rewrote", "unchanged", or "missing".
    Raises on malformed JSON or unexpected structure.
    """
    if not path.is_file():
        return "missing"

    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level JSON is not an object")

    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: missing or invalid 'mcpServers' object")

    overall_changed = False
    for name, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        args = spec.get("args")
        if not isinstance(args, list):
            continue
        new_args, changed = _rewrite_args(args, new_root)
        if changed:
            spec["args"] = new_args
            overall_changed = True

    if not overall_changed:
        return "unchanged"

    # Write atomically via a sibling temp file so a crash mid-write can't
    # leave a truncated config.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(data, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp_path.replace(path)
    return "rewrote"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: rewrite_mcp_paths.py <atlasforge_root>", file=sys.stderr)
        return 1

    try:
        root = Path(argv[1]).resolve()
        validate_root(root)
    except ValueError as e:
        print(f"[rewrite_mcp_paths] ERROR: {e}", file=sys.stderr)
        return 1

    new_root = str(root)
    exit_code = 0
    for name in MCP_FILES:
        path = root / name
        try:
            result = rewrite_mcp_file(path, new_root)
        except json.JSONDecodeError as e:
            print(f"[rewrite_mcp_paths] ERROR: {path}: invalid JSON ({e})", file=sys.stderr)
            exit_code = 2
            continue
        except (OSError, ValueError) as e:
            print(f"[rewrite_mcp_paths] ERROR: {path}: {e}", file=sys.stderr)
            exit_code = 2
            continue

        if result == "rewrote":
            print(f"[rewrite_mcp_paths] rewrote {path.name} -> {new_root}")
        elif result == "unchanged":
            print(f"[rewrite_mcp_paths] {path.name} already points at {new_root}")
        elif result == "missing":
            print(f"[rewrite_mcp_paths] {path.name} not found, skipping")

    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv))
