#!/usr/bin/env python3
"""
scripts/release_workflow.py - Create clean Release vX.X.X commits for AI-AtlasForge.

This script bundles all real code changes since the last release into a single
clean commit on main, suitable for pushing to origin/main.

Usage:
    python3 scripts/release_workflow.py --version 1.9.2 --description "Clean git strategy"
    python3 scripts/release_workflow.py --version 1.9.2 --description "..." --no-tag
    python3 scripts/release_workflow.py --clean-push          # Dry-run squash audit
    python3 scripts/release_workflow.py --clean-push --execute # Actually squash [AF] commits

What it does:
1. Audits git log origin/main..HEAD for non-[AF] commits
2. Reports [AF] vs real commit counts
3. Creates "Release vX.X.X - description" commit if real changes exist
4. Optionally creates a git tag
5. (--clean-push) Squashes all [AF] artifact commits into a single clean commit

What it does NOT do:
- Push to remote (you must run 'git push origin main' separately)
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
        print("Tip: Run scripts/release_workflow.py --clean-push to squash [AF] commits before pushing.")
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


def squash_af_commits(repo_root: Path, execute: bool = False) -> bool:
    """Squash all [AF] artifact commits ahead of origin/main into a single commit.

    Does NOT push to remote — caller runs 'git push origin main' separately.

    Args:
        repo_root: Path to the repository root.
        execute: If False, perform a dry-run audit only. If True, actually squash.

    Returns:
        True on success (or clean state), False on error.
    """
    log = subprocess.run(
        ["git", "log", "--oneline", "origin/main..HEAD"],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if log.returncode != 0:
        print(f"ERROR: git log failed: {log.stderr}")
        return False

    commits = []
    for line in log.stdout.strip().splitlines():
        if not line:
            continue
        parts = line.split(" ", 1)
        sha = parts[0]
        msg = parts[1] if len(parts) > 1 else ""
        commits.append({"sha": sha, "message": msg, "is_af": "[AF]" in msg})

    af_commits = [c for c in commits if c["is_af"]]
    real_commits = [c for c in commits if not c["is_af"]]
    total = len(commits)

    print(f"\n=== Clean Push Audit (--clean-push) ===")
    print(f"Commits ahead of origin/main: {total}")
    print(f"  [AF] artifact commits: {len(af_commits)}")
    print(f"  Real code commits:     {len(real_commits)}")

    if real_commits:
        print("\nERROR: Real code commits detected in ahead range:")
        for c in real_commits:
            print(f"  {c['sha']} {c['message']}")
        print("\nCannot safely squash — real commits would be collapsed.")
        print("  1. Run: python3 scripts/release_workflow.py --version X.X.X --description '...'")
        print("  2. Then re-run: python3 scripts/release_workflow.py --clean-push [--execute]")
        return False

    if total == 0:
        print("Nothing to do: already up to date with origin/main.")
        return True

    print(f"\n{'DRY RUN: ' if not execute else ''}Will squash {total} [AF] commits into a single commit.")

    if not execute:
        print("\nDry run complete. Re-run with --execute to apply.")
        return True

    # Create backup tag before modifying history
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_tag = f"af-backup/pre-squash-{timestamp}"
    tag_result = subprocess.run(
        ["git", "tag", backup_tag],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    if tag_result.returncode == 0:
        print(f"✓ Backup tag created: {backup_tag}")
    else:
        print(f"WARNING: Could not create backup tag: {tag_result.stderr.strip()}")

    # Soft-reset to origin/main — preserves all working-tree/index changes
    print("Resetting to origin/main (soft)...")
    reset_result = subprocess.run(
        ["git", "reset", "--soft", "origin/main"],
        cwd=repo_root, capture_output=True, text=True, timeout=30,
    )
    if reset_result.returncode != 0:
        print(f"ERROR: git reset failed: {reset_result.stderr}")
        return False

    # Stage all non-gitignored changes
    subprocess.run(["git", "add", "-A"], cwd=repo_root, capture_output=True, timeout=30)

    # Check if anything survives gitignore filtering
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )

    today = datetime.now().strftime("%Y-%m-%d")
    squash_msg = f"Squash {total} [AF] mission artifact commits (batch cleanup {today})"

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
        print("  (All [AF] artifact files were gitignored)")

    # Report final state
    final_log = subprocess.run(
        ["git", "log", "--oneline", "origin/main..HEAD"],
        cwd=repo_root, capture_output=True, text=True, timeout=10,
    )
    remaining = [line for line in final_log.stdout.strip().splitlines() if line]
    print(f"\n=== Result ===")
    print(f"Commits ahead of origin/main: {len(remaining)}")
    for line in remaining:
        print(f"  {line}")
    print(f"\nTo push: git push origin main")
    if tag_result.returncode == 0:
        print(f"Backup preserved at: {backup_tag}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Create a clean Release vX.X.X commit for AI-AtlasForge"
    )
    # Release commit mode
    parser.add_argument("--version", help="Version e.g. 1.9.2 (required unless --clean-push)")
    parser.add_argument("--description", help="Short release description (required unless --clean-push)")
    parser.add_argument("--no-tag", action="store_true", help="Skip creating git tag")
    # Clean-push / squash mode
    parser.add_argument(
        "--clean-push", action="store_true",
        help="Squash all [AF] mission artifact commits into a single clean commit"
    )
    parser.add_argument(
        "--execute", action="store_true",
        help="With --clean-push: actually apply the squash (default: dry-run)"
    )
    args = parser.parse_args()

    repo_root = get_repo_root()

    if args.clean_push:
        success = squash_af_commits(repo_root, execute=args.execute)
        sys.exit(0 if success else 1)

    # Release commit mode — both --version and --description are required
    if not args.version or not args.description:
        parser.error("--version and --description are required unless --clean-push is specified")

    success = create_release_commit(
        repo_root, args.version, args.description, tag=not args.no_tag
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
