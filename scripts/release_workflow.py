#!/usr/bin/env python3
"""
scripts/release_workflow.py - Create clean Release vX.X.X commits for AI-AtlasForge.

This script bundles all real code changes since the last release into a single
clean commit on main, suitable for pushing to origin/main.

Usage:
    python3 scripts/release_workflow.py --version 1.9.2 --description "Clean git strategy"
    python3 scripts/release_workflow.py --version 1.9.2 --description "..." --no-tag

What it does:
1. Audits git log origin/main..HEAD for non-[AF] commits
2. Reports [AF] vs real commit counts
3. Creates "Release vX.X.X - description" commit if real changes exist
4. Optionally creates a git tag

What it does NOT do:
- Push to remote (you must run 'git push origin main' separately)
- Squash or remove [AF] commits (use clean_push.py for that)
"""

import argparse
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


def audit_commits(repo_root: Path) -> tuple[list, list]:
    """Return (af_commits, real_commits) ahead of origin/main."""
    log = subprocess.run(
        ["git", "log", "--oneline", "origin/main..HEAD"],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if log.returncode != 0:
        print(f"ERROR: git log failed: {log.stderr}")
        sys.exit(1)

    commits = [c for c in log.stdout.strip().splitlines() if c]
    af = [c for c in commits if "[AF]" in c]
    real = [c for c in commits if "[AF]" not in c]
    return af, real


def create_release_commit(repo_root: Path, version: str, description: str, tag: bool) -> bool:
    af_commits, real_commits = audit_commits(repo_root)

    print(f"\n=== Release v{version} Audit ===")
    print(f"Commits ahead of origin/main: {len(af_commits) + len(real_commits)}")
    print(f"  [AF] artifact commits (ignored for release): {len(af_commits)}")
    print(f"  Real code commits: {len(real_commits)}")

    if real_commits:
        print("\nReal commits to be bundled:")
        for c in real_commits[:20]:
            print(f"  {c}")
        if len(real_commits) > 20:
            print(f"  ... and {len(real_commits) - 20} more")

    # Check for unstaged real changes too
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    has_uncommitted = bool(status.stdout.strip())

    if not real_commits and not has_uncommitted:
        print("\nWARNING: No real code changes found. Nothing meaningful to release.")
        print("Tip: Run scripts/clean_push.py to squash [AF] commits before pushing.")
        return False

    # Stage all non-gitignored changes
    subprocess.run(["git", "add", "-A"], cwd=repo_root, capture_output=True, timeout=30)

    commit_msg = f"Release v{version} - {description}"
    print(f"\nCreating release commit: {commit_msg}")

    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        combined = result.stdout + result.stderr
        if "nothing to commit" in combined or "no changes added to commit" in combined:
            print("No uncommitted changes to include in release commit.")
            print("The real commits listed above are already committed.")
            # Still create a synthetic release tag/marker if requested
        else:
            print(f"ERROR: Commit failed: {result.stderr or result.stdout}")
            return False
    else:
        print(f"✓ Release commit created: {commit_msg}")

    if tag:
        tag_result = subprocess.run(
            ["git", "tag", "-a", f"v{version}", "-m", commit_msg],
            cwd=repo_root, capture_output=True, text=True, timeout=10,
        )
        if tag_result.returncode == 0:
            print(f"✓ Git tag created: v{version}")
        else:
            print(f"WARNING: Tag creation failed (may already exist): {tag_result.stderr.strip()}")

    print(f"\nRelease v{version} ready. To push:")
    print(f"  git push origin main")
    if tag:
        print(f"  git push origin v{version}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Create a clean Release vX.X.X commit for AI-AtlasForge"
    )
    parser.add_argument("--version", required=True, help="Version e.g. 1.9.2")
    parser.add_argument("--description", required=True, help="Short release description")
    parser.add_argument("--no-tag", action="store_true", help="Skip creating git tag")
    args = parser.parse_args()

    repo_root = get_repo_root()
    success = create_release_commit(
        repo_root, args.version, args.description, tag=not args.no_tag
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
