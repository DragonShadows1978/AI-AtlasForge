#!/usr/bin/env python3
"""
scripts/clean_push.py - Squash [AF] artifact commits and push cleanly to origin/main.

This script handles the "244 commits ahead of origin/main" problem by:
1. Auditing that ALL ahead commits are [AF] artifacts (no real code mixed in)
2. If safe: squashes them into a single "Squash N [AF] artifact commits" commit
3. Pushes the squashed result to origin/main

If any real code commits are detected in the ahead range, the script STOPS
and asks you to create a release commit first via release_workflow.py.

Usage:
    python3 scripts/clean_push.py              # Audit only (dry run)
    python3 scripts/clean_push.py --execute    # Actually squash and push

Safety:
    - NEVER touches working tree files
    - Validates all ahead commits are [AF] before squashing
    - Creates a backup tag before squashing (af-backup/pre-squash-YYYYMMDD)
"""

import argparse
import subprocess
import sys
from datetime import datetime
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


def get_ahead_commits(repo_root: Path) -> list[dict]:
    """Get all commits ahead of origin/main with their details."""
    log = subprocess.run(
        ["git", "log", "--oneline", "origin/main..HEAD"],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if log.returncode != 0:
        print(f"ERROR: git log failed: {log.stderr}")
        sys.exit(1)

    commits = []
    for line in log.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        commits.append({"sha": sha, "message": msg, "is_af": "[AF]" in msg})
    return commits


def audit(commits: list[dict]) -> tuple[list, list]:
    af = [c for c in commits if c["is_af"]]
    real = [c for c in commits if not c["is_af"]]
    return af, real


def squash_and_push(repo_root: Path, commits: list[dict], dry_run: bool) -> bool:
    af_commits, real_commits = audit(commits)
    total = len(commits)

    print(f"\n=== Clean Push Audit ===")
    print(f"Commits ahead of origin/main: {total}")
    print(f"  [AF] artifact commits: {len(af_commits)}")
    print(f"  Real code commits: {len(real_commits)}")

    if real_commits:
        print("\nERROR: Real code commits detected in ahead range:")
        for c in real_commits:
            print(f"  {c['sha']} {c['message']}")
        print("\nCannot safely squash. Please:")
        print("  1. Run: python3 scripts/release_workflow.py --version X.X.X --description '...'")
        print("  2. Then re-run this script")
        return False

    if total == 0:
        print("Nothing to do: already up to date with origin/main")
        return True

    print(f"\n{'DRY RUN: ' if dry_run else ''}Will squash {total} [AF] commits into single commit.")

    if dry_run:
        print("\nDry run complete. Re-run with --execute to apply.")
        return True

    # Create backup tag before modifying history
    today = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_tag = f"af-backup/pre-squash-{today}"
    tag_result = subprocess.run(
        ["git", "tag", backup_tag],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    if tag_result.returncode == 0:
        print(f"✓ Backup tag created: {backup_tag}")
    else:
        print(f"WARNING: Could not create backup tag: {tag_result.stderr.strip()}")

    # Squash: reset soft to origin/main, then commit all staged changes
    print(f"Resetting to origin/main (soft)...")
    reset_result = subprocess.run(
        ["git", "reset", "--soft", "origin/main"],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if reset_result.returncode != 0:
        print(f"ERROR: git reset failed: {reset_result.stderr}")
        return False

    # Stage remaining non-gitignored changes
    subprocess.run(["git", "add", "-A"], cwd=repo_root, capture_output=True, timeout=30)

    # Check if there's anything to commit after gitignore filtering
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )

    squash_msg = f"Squash {total} [AF] mission artifact commits (pre-v1.9.2 cleanup)"

    if status.stdout.strip():
        commit_result = subprocess.run(
            ["git", "commit", "-m", squash_msg],
            cwd=repo_root, capture_output=True, text=True, timeout=30,
        )
        if commit_result.returncode != 0:
            print(f"ERROR: Squash commit failed: {commit_result.stderr}")
            return False
        print(f"✓ Squash commit created: {squash_msg}")
    else:
        print("✓ No staged changes after gitignore filtering — history is clean as-is.")
        print("  (All [AF] artifact files are now gitignored)")

    # Push to origin/main
    print("Pushing to origin/main...")
    push_result = subprocess.run(
        ["git", "push", "origin", "main"],
        cwd=repo_root, capture_output=True, text=True, timeout=120,
    )
    if push_result.returncode == 0:
        print("✓ Successfully pushed to origin/main")
        print(f"\ngit log --oneline origin/main..HEAD should now be empty.")
        return True
    else:
        print(f"ERROR: Push failed: {push_result.stderr}")
        print(f"Backup tag {backup_tag} preserves your work.")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Squash [AF] commits and push cleanly to origin/main"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="Actually squash and push (default: dry run / audit only)"
    )
    args = parser.parse_args()

    repo_root = get_repo_root()
    commits = get_ahead_commits(repo_root)
    success = squash_and_push(repo_root, commits, dry_run=not args.execute)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
