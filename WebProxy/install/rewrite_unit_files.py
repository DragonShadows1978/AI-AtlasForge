#!/usr/bin/env python3
"""Rewrite AtlasForge systemd unit files to point at the real install root.

The repo ships systemd/*.service with a placeholder path (/opt/ai-atlasforge)
and a %i user placeholder. On install, setup_services.sh must rewrite these
templates with the caller's $ATLASFORGE_ROOT and username.

This replaces brittle `sed -e "s|/opt/ai-atlasforge|$ATLASFORGE_ROOT|g"` calls.
Raw sed breaks on paths containing |, &, \\, and critically on ' because the
web-proxy unit's ExecStart uses `/bin/sh -c '...'` — a single-quote in
$ATLASFORGE_ROOT would un-balance the shell quoting and either be rejected by
systemd or execute unintended tokens.

Usage:
    python3 scripts/rewrite_unit_files.py <atlasforge_root> <user> <src> <dst>

Exit codes:
    0 - success (file rewritten or already up to date)
    1 - validation error (invalid path, unsafe chars, missing user)
    2 - file error (missing source, write error)
"""

from __future__ import annotations

import sys
from pathlib import Path

PATH_PLACEHOLDER = "/opt/ai-atlasforge"
USER_PLACEHOLDER = "%i"


def validate_root(root: Path) -> None:
    """Reject roots that would produce malformed unit files or are unsafe.

    Rejection criteria mirror rewrite_mcp_paths.py's validation, plus:
    - single-quote: breaks ExecStart=/bin/sh -c '...' outer quoting (finding #65)
    - shell metacharacters (&, |, ;, $, `, (, )): the web-proxy unit's
      ExecStart=/bin/sh -c '...' wraps the root in an INNER sh reparse. Even
      though the single-quoted outer layer is safe, the inner sh tokenizes the
      substituted path. An unquoted '&' would background the first exec, '|'
      would pipe, '$' would expand a variable, etc. Rejecting these is the
      simplest, most defensible fix and aligns with how `"` / `'` are already
      handled (CRITICAL C2, iter-3 red team).
    """
    s = str(root)
    if not root.is_absolute():
        raise ValueError(f"ATLASFORGE_ROOT must be an absolute path, got: {s!r}")
    if "\n" in s or "\r" in s:
        raise ValueError("ATLASFORGE_ROOT contains newline character")
    if '"' in s:
        raise ValueError("ATLASFORGE_ROOT contains double-quote character")
    if "'" in s:
        raise ValueError(
            "ATLASFORGE_ROOT contains single-quote character — would break "
            "systemd ExecStart=/bin/sh -c '...' quoting"
        )
    # Shell metacharacters re-parsed by the inner /bin/sh -c '...' wrapper.
    for bad in ("&", "|", ";", "$", "`", "(", ")", "<", ">", "\\"):
        if bad in s:
            raise ValueError(
                f"ATLASFORGE_ROOT contains shell metacharacter {bad!r} — would "
                "break inner-sh reparse inside ExecStart=/bin/sh -c '...'"
            )
    if not root.is_dir():
        raise ValueError(f"ATLASFORGE_ROOT is not an existing directory: {s!r}")


def validate_user(user: str) -> None:
    """Reject usernames with characters that would produce malformed unit files."""
    if not user:
        raise ValueError("user must be non-empty")
    if "\n" in user or "\r" in user:
        raise ValueError("user contains newline character")
    if '"' in user or "'" in user:
        raise ValueError("user contains quote character")
    # POSIX.1-2017 portable username: [A-Za-z_][A-Za-z0-9_-]*$ (practical subset)
    # Don't over-restrict; systemd accepts anything getpwnam resolves.
    for ch in user:
        if ch.isspace():
            raise ValueError(f"user contains whitespace: {user!r}")


def _rewrite_line(line: str, new_root: str, user: str) -> str:
    """Replace anchored /opt/ai-atlasforge and %i tokens in one line.

    Anchored replacement for the path: only substitute when the placeholder
    is both *left-anchored* (at SOL or preceded by a delimiter) AND
    *right-anchored* (at EOL or followed by a delimiter). This avoids
    accidentally mutating substrings like `/srv/opt/ai-atlasforge/foo`
    (left-anchor fix, CRITICAL C1 iter-3 red team) or
    `/opt/ai-atlasforge-something` (right-anchor).
    """
    out = line

    # Path substitution. We scan manually rather than using str.replace so we
    # can enforce the anchor (placeholder must start at SOL or after a
    # delimiter, AND end at EOL/delimiter). Delimiters are those that naturally
    # bound paths in systemd unit files: =, ", ', space, tab, newline, :, ;,
    # comma, closing brackets, and — for the left side — also `/` is NOT a
    # valid left-anchor (a slash before means we're mid-path).
    right_anchor_chars = set("/=\"' \t\n\r:;,])}")
    # Left-anchor characters: everything that bounds a token in a systemd unit
    # file EXCEPT `/` (a preceding slash means we're in the middle of a path
    # like /srv/opt/ai-atlasforge/ which must NOT be rewritten).
    left_anchor_chars = set("=\"' \t\n\r:;,[({")
    i = 0
    result_parts: list[str] = []
    ph_len = len(PATH_PLACEHOLDER)
    while i < len(out):
        if out.startswith(PATH_PLACEHOLDER, i):
            end = i + ph_len
            # Right anchor: placeholder ends at EOL or before a delimiter.
            at_eol = end >= len(out)
            right_anchored = at_eol or out[end] in right_anchor_chars
            # Left anchor: placeholder starts at SOL or after a delimiter.
            at_sol = i == 0
            left_anchored = at_sol or out[i - 1] in left_anchor_chars
            if right_anchored and left_anchored:
                result_parts.append(new_root)
                i = end
                continue
        result_parts.append(out[i])
        i += 1
    out = "".join(result_parts)

    # User substitution (%i → actual user).
    out = out.replace(USER_PLACEHOLDER, user)

    return out


def rewrite_unit(
    src: Path,
    dst: Path,
    new_root: str,
    user: str,
) -> str:
    """Rewrite one systemd unit file from src template to dst.

    Returns a status string: "rewrote", "unchanged", or "missing".
    """
    if not src.is_file():
        return "missing"

    src_text = src.read_text(encoding="utf-8")
    new_text = _rewrite_line(src_text, new_root, user)

    # Idempotency: if destination already exists with matching content, no-op.
    if dst.is_file():
        try:
            existing = dst.read_text(encoding="utf-8")
        except OSError:
            existing = None
        if existing == new_text:
            return "unchanged"

    # Atomic write via sibling temp file.
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(dst)
    return "rewrote"


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(
            "usage: rewrite_unit_files.py <atlasforge_root> <user> <src> <dst>",
            file=sys.stderr,
        )
        return 1

    try:
        root = Path(argv[1]).resolve()
        validate_root(root)
        user = argv[2]
        validate_user(user)
    except ValueError as e:
        print(f"[rewrite_unit_files] ERROR: {e}", file=sys.stderr)
        return 1

    src = Path(argv[3])
    dst = Path(argv[4])

    try:
        result = rewrite_unit(src, dst, str(root), user)
    except OSError as e:
        print(f"[rewrite_unit_files] ERROR: {e}", file=sys.stderr)
        return 2

    if result == "rewrote":
        print(f"[rewrite_unit_files] rewrote {dst.name} -> {root} (user={user})")
    elif result == "unchanged":
        print(f"[rewrite_unit_files] {dst.name} already up to date")
    elif result == "missing":
        print(f"[rewrite_unit_files] {src} not found, skipping")
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
