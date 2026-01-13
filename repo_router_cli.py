#!/usr/bin/env python3
"""
Repository Router CLI
======================
Command-line interface for the multi-repository routing system.

Commands:
    status     - Show status of all configured repositories
    check      - Check which repo a file/path belongs to
    route      - Route pending changes and show which repo each goes to
    commit     - Commit routed changes to their respective repos
    sync       - Push all repos with pending commits to remotes
    debug      - Debug routing for a specific path

Usage:
    python repo_router_cli.py status
    python repo_router_cli.py check workspace/brotato_bot/main.py
    python repo_router_cli.py route
    python repo_router_cli.py commit -m "feat: update components"
    python repo_router_cli.py sync
"""

import argparse
import sys
import os
from typing import List, Optional

# Add parent directory to path if running as script
if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from repo_router import (
    get_router,
    RepoRouter,
    RouteResult,
    CommitResult
)


# =============================================================================
# Color Output Helpers
# =============================================================================

class Colors:
    """ANSI color codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    BG_RED = "\033[41m"
    BG_GREEN = "\033[42m"
    BG_YELLOW = "\033[43m"
    BG_BLUE = "\033[44m"


def colored(text: str, *colors: str) -> str:
    """Apply colors to text."""
    if not sys.stdout.isatty():
        return text
    return "".join(colors) + text + Colors.RESET


def success(text: str) -> str:
    return colored(text, Colors.GREEN)


def error(text: str) -> str:
    return colored(text, Colors.RED)


def warning(text: str) -> str:
    return colored(text, Colors.YELLOW)


def info(text: str) -> str:
    return colored(text, Colors.CYAN)


def bold(text: str) -> str:
    return colored(text, Colors.BOLD)


def dim(text: str) -> str:
    return colored(text, Colors.DIM)


# =============================================================================
# Command Handlers
# =============================================================================

def cmd_status(router: RepoRouter, args: argparse.Namespace) -> int:
    """Show status of all configured repositories."""
    status = router.get_status()

    print(bold("\n Repository Status"))
    print("=" * 70)

    # Summary
    total = status["total_repos"]
    with_changes = status["repos_with_changes"]
    ahead = status["repos_ahead"]

    print(f"  Total repos:      {total}")
    print(f"  With changes:     {warning(str(with_changes)) if with_changes else success('0')}")
    print(f"  Ahead of remote:  {warning(str(ahead)) if ahead else success('0')}")

    # Per-repo details
    print("\n" + bold("  Repositories:"))
    print("-" * 70)

    for repo_id, repo_status in status["repos"].items():
        name = repo_status["name"]
        valid = repo_status.get("valid", False)

        if not valid:
            print(f"  {error('[INVALID]')} {name:<25} {dim(repo_status.get('error', 'Unknown error'))}")
            continue

        changes = repo_status.get("changes_count", 0)
        repo_ahead = repo_status.get("ahead", 0)
        behind = repo_status.get("behind", 0)
        remote = repo_status.get("remote_url") or "No remote"

        # Status indicator
        if changes > 0:
            status_icon = warning("[CHANGED]")
        elif repo_ahead > 0:
            status_icon = info("[AHEAD]  ")
        else:
            status_icon = success("[OK]     ")

        # Build status line
        status_parts = []
        if changes:
            status_parts.append(f"{warning(str(changes))} changes")
        if repo_ahead:
            status_parts.append(f"{info(str(repo_ahead))} ahead")
        if behind:
            status_parts.append(f"{error(str(behind))} behind")

        status_str = ", ".join(status_parts) if status_parts else success("clean")

        print(f"  {status_icon} {bold(name):<25} {status_str}")
        if args.verbose:
            print(f"           {dim('Path:')} {repo_status['path']}")
            print(f"           {dim('Remote:')} {remote}")

    # Validation issues
    if status["validation_issues"]:
        print("\n" + bold("  Validation Issues:"))
        for issue in status["validation_issues"]:
            print(f"  {warning('WARNING:')} {issue}")

    print()
    return 0


def cmd_check(router: RepoRouter, args: argparse.Namespace) -> int:
    """Check which repository a file belongs to."""
    if not args.path:
        print(error("Error: path argument required"))
        return 1

    for path in args.path:
        print(f"\n{bold('Path:')} {path}")

        if args.debug:
            # Show detailed debug info
            debug = router.debug_path(path)

            print(f"\n  {bold('All matching rules:')}")
            if not debug["all_matching_rules"]:
                print("    (none)")
            else:
                for rule in debug["all_matching_rules"]:
                    print(f"    [{rule['priority']:3}] {rule['pattern']:<30} -> {rule['repo'] or 'UNTRACKED'}")
                    if rule["description"]:
                        print(f"          {dim(rule['description'])}")

            print(f"\n  {bold('Winning rule:')}")
            if debug["winning_rule"]:
                wr = debug["winning_rule"]
                print(f"    Pattern:  {wr['pattern']}")
                print(f"    Priority: {wr['priority']}")
                print(f"    Repo:     {wr['repo'] or 'UNTRACKED'}")
            else:
                print("    (none)")

            if debug["target_repo"]:
                tr = debug["target_repo"]
                print(f"\n  {bold('Target Repository:')}")
                print(f"    ID:   {tr['id']}")
                print(f"    Name: {tr['name']}")
                print(f"    Path: {tr['path']}")
        else:
            # Simple output
            repo = router.match_path(path)
            if repo:
                print(f"  {success('Repo:')} {repo.name}")
                print(f"  {dim('Path:')} {repo.path}")
            else:
                print(f"  {warning('Repo:')} UNTRACKED")

    print()
    return 0


def cmd_route(router: RepoRouter, args: argparse.Namespace) -> int:
    """Route pending changes and show which repo each goes to."""
    # Get all changed files from git
    import subprocess

    result = subprocess.run(
        ["git", "-C", router.atlasforge_root, "status", "--porcelain"],
        capture_output=True,
        text=True
    )

    if result.returncode != 0:
        print(error("Failed to get git status"))
        return 1

    # Parse changed files
    changed_files = []
    for line in result.stdout.strip().split("\n"):
        if line and len(line) > 3:
            changed_files.append(line[3:].strip())

    if not changed_files:
        print(success("No changes to route"))
        return 0

    # Route changes
    route_result = router.route_changes(changed_files)

    print(bold("\n Routed Changes"))
    print("=" * 70)

    # Group by repo
    for repo_id, files in route_result.files_by_repo.items():
        repo = router.registry.get(repo_id)
        if not repo:
            continue

        print(f"\n  {bold(repo.name)} ({len(files)} files)")
        print(f"  {dim(repo.path)}")
        print("-" * 50)

        for rf in files:
            pattern_hint = dim(f"({rf.rule_pattern})")
            print(f"    {rf.file_path} {pattern_hint}")

    # Unrouted files
    if route_result.unrouted_files:
        print(f"\n  {warning('UNTRACKED')} ({len(route_result.unrouted_files)} files)")
        print("-" * 50)
        for path in route_result.unrouted_files:
            print(f"    {path}")

    # Warnings
    if route_result.warnings:
        print(f"\n  {bold('Warnings:')}")
        for warn in route_result.warnings:
            print(f"    {warning('!')} {warn}")

    print()
    return 0


def cmd_commit(router: RepoRouter, args: argparse.Namespace) -> int:
    """Commit routed changes to their respective repositories."""
    if not args.message:
        print(error("Error: commit message required (-m)"))
        return 1

    # Get all changed files
    import subprocess

    result = subprocess.run(
        ["git", "-C", router.atlasforge_root, "status", "--porcelain"],
        capture_output=True,
        text=True
    )

    changed_files = []
    for line in result.stdout.strip().split("\n"):
        if line and len(line) > 3:
            changed_files.append(line[3:].strip())

    if not changed_files:
        print(success("No changes to commit"))
        return 0

    # Route changes
    route_result = router.route_changes(changed_files)

    if not route_result.files_by_repo:
        print(warning("No files matched any repository"))
        return 0

    print(bold("\n Committing Changes"))
    print("=" * 70)

    if args.dry_run:
        print(warning("DRY RUN - no actual commits will be made\n"))

    # Commit to each repo
    results = []
    for repo_id, files in route_result.files_by_repo.items():
        repo = router.registry.get(repo_id)
        if not repo:
            continue

        print(f"\n  {bold(repo.name)}")

        if args.dry_run:
            print(f"    Would commit {len(files)} files:")
            for rf in files[:5]:
                print(f"      {rf.file_path}")
            if len(files) > 5:
                print(f"      ... and {len(files) - 5} more")
            continue

        # Actually commit
        file_paths = [rf.absolute_path for rf in files]
        commit_result = router.commit_to_repo(repo_id, args.message, file_paths)
        results.append(commit_result)

        if commit_result.success:
            if commit_result.commit_hash:
                print(f"    {success('OK')} Committed {len(commit_result.files_committed)} files")
                print(f"    Hash: {commit_result.commit_hash}")
            else:
                print(f"    {info('SKIP')} {commit_result.message}")
        else:
            print(f"    {error('FAILED')} {commit_result.error}")

    # Summary
    if results and not args.dry_run:
        successful = sum(1 for r in results if r.success)
        print(f"\n  {bold('Summary:')} {successful}/{len(results)} repos committed successfully")

    print()
    return 0


def cmd_sync(router: RepoRouter, args: argparse.Namespace) -> int:
    """Push all repositories with pending commits to their remotes."""
    print(bold("\n Syncing Repositories"))
    print("=" * 70)

    if args.dry_run:
        print(warning("DRY RUN - no actual pushes will be made\n"))

    status = router.get_status()

    has_work = False
    for repo_id, repo_status in status["repos"].items():
        if not repo_status.get("valid"):
            continue

        ahead = repo_status.get("ahead", 0)
        remote = repo_status.get("remote_url")

        if ahead == 0:
            continue

        has_work = True
        repo = router.registry.get(repo_id)

        print(f"\n  {bold(repo.name)}")
        print(f"    {ahead} commits ahead")

        if not remote:
            print(f"    {warning('SKIP')} No remote configured")
            continue

        if args.dry_run:
            print(f"    Would push to: {remote}")
            continue

        # Actually push
        success_push, message = router.dispatcher.push(repo)
        if success_push:
            print(f"    {success('OK')} {message}")
        else:
            print(f"    {error('FAILED')} {message}")

    if not has_work:
        print(success("\n  All repositories are up to date"))

    print()
    return 0


def cmd_debug(router: RepoRouter, args: argparse.Namespace) -> int:
    """Debug routing configuration."""
    # Just delegate to check with debug flag
    args.debug = True
    return cmd_check(router, args)


# =============================================================================
# Main Entry Point
# =============================================================================

def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Repository Router CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    %(prog)s status                    Show all repo status
    %(prog)s status -v                 Show verbose status
    %(prog)s check path/to/file        Check which repo a file belongs to
    %(prog)s check -d path/to/file     Debug routing for a file
    %(prog)s route                     Show how changes would be routed
    %(prog)s commit -m "message"       Commit changes to respective repos
    %(prog)s commit -m "msg" --dry-run Preview commit without executing
    %(prog)s sync                      Push all repos to remotes
    %(prog)s sync --dry-run            Preview sync without executing
        """
    )

    parser.add_argument(
        "--config", "-c",
        help="Path to routing configuration file",
        default=None
    )
    parser.add_argument(
        "--root", "-r",
        help="AtlasForge root directory",
        default=None
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # Status command
    status_parser = subparsers.add_parser("status", help="Show repository status")
    status_parser.add_argument("-v", "--verbose", action="store_true",
                               help="Show verbose output")

    # Check command
    check_parser = subparsers.add_parser("check", help="Check file routing")
    check_parser.add_argument("path", nargs="+", help="File path(s) to check")
    check_parser.add_argument("-d", "--debug", action="store_true",
                              help="Show debug information")

    # Route command
    route_parser = subparsers.add_parser("route", help="Show change routing")

    # Commit command
    commit_parser = subparsers.add_parser("commit", help="Commit routed changes")
    commit_parser.add_argument("-m", "--message", required=True,
                               help="Commit message")
    commit_parser.add_argument("--dry-run", action="store_true",
                               help="Preview without committing")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Sync repos to remotes")
    sync_parser.add_argument("--dry-run", action="store_true",
                             help="Preview without pushing")

    # Debug command
    debug_parser = subparsers.add_parser("debug", help="Debug routing")
    debug_parser.add_argument("path", nargs="+", help="File path(s) to debug")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Initialize router
    try:
        router = get_router(
            config_path=args.config,
            atlasforge_root=args.root
        )
    except Exception as e:
        print(error(f"Failed to initialize router: {e}"))
        return 1

    # Dispatch command
    commands = {
        "status": cmd_status,
        "check": cmd_check,
        "route": cmd_route,
        "commit": cmd_commit,
        "sync": cmd_sync,
        "debug": cmd_debug,
    }

    handler = commands.get(args.command)
    if handler:
        return handler(router, args)
    else:
        print(error(f"Unknown command: {args.command}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
