#!/usr/bin/env python3
"""
scripts/install_hooks.py - Install git hooks for AI-AtlasForge.

Installs the pre-push hook that blocks [AF] artifact commits from reaching main.

Usage:
    python3 scripts/install_hooks.py          # Install hooks
    python3 scripts/install_hooks.py --check  # Check hook status only
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def get_repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode != 0:
        print("ERROR: Not in a git repository")
        sys.exit(1)
    return Path(result.stdout.strip())


def install_pre_push_hook(repo_root: Path, check_only: bool = False) -> bool:
    src = repo_root / "scripts" / "pre_push_hook.sh"
    dest = repo_root / ".git" / "hooks" / "pre-push"

    if not src.exists():
        print(f"ERROR: Source hook not found: {src}")
        return False

    print(f"Source:      {src}")
    print(f"Destination: {dest}")

    if dest.exists():
        # Read both to compare
        src_content = src.read_text()
        dest_content = dest.read_text()
        if src_content == dest_content:
            print("✓ pre-push hook is already installed and up to date.")
            return True
        else:
            print("! pre-push hook exists but differs from scripts/pre_push_hook.sh")
            if check_only:
                print("  Run without --check to update.")
                return False

    if check_only:
        if dest.exists():
            print("  Hook exists but differs — run without --check to update.")
        else:
            print("  Hook NOT installed — run without --check to install.")
        return dest.exists()

    shutil.copy2(src, dest)
    dest.chmod(0o755)
    print("✓ pre-push hook installed successfully.")
    print("  Future pushes to main will block [AF] artifact commits.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Install git hooks for AI-AtlasForge")
    parser.add_argument("--check", action="store_true", help="Check status only, don't install")
    args = parser.parse_args()

    repo_root = get_repo_root()
    print(f"Repository: {repo_root}\n")

    success = install_pre_push_hook(repo_root, check_only=args.check)
    if success:
        print("\nAll hooks OK.")
    else:
        print("\nHook installation incomplete.")
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
