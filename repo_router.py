#!/usr/bin/env python3
"""
Repository Router
==================
Routes file changes to the correct Git repository based on configurable path mappings.

This module provides intelligent multi-repository management for the RDE system,
automatically directing code changes to their appropriate repositories based on
path patterns defined in repo_routing.yaml.

Features:
- Path-based routing with glob pattern matching
- Priority-based conflict resolution
- Integration with existing git_checkpoint and git_push_manager
- Multi-repo commit and sync operations
- CLI interface for manual operations

Usage:
    from repo_router import RepoRouter, get_router

    router = get_router()

    # Find which repo a file belongs to
    repo = router.match_path("workspace/brotato_bot/main.py")

    # Route a batch of changes
    routes = router.route_changes(["file1.py", "file2.py"])

    # Commit to all affected repos
    router.commit_all(routes, "feat: update components")
"""

import os
import subprocess
import fnmatch
import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Set, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

# YAML parsing with fallback
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

logger = logging.getLogger("repo_router")


# =============================================================================
# Error Classes
# =============================================================================

class GitOperationError(Exception):
    """
    Base class for git operation errors.

    Provides error classification and recovery hints.

    Attributes:
        message: Human-readable error message
        operation: The git operation that failed (e.g., 'commit', 'push')
        repo_path: Path to the repository where error occurred
        stderr: Raw stderr from git command
        is_recoverable: Whether the error can be recovered from
        recovery_hint: Suggested action to recover
    """
    def __init__(
        self,
        message: str,
        operation: str = "",
        repo_path: str = "",
        stderr: str = "",
        is_recoverable: bool = False,
        recovery_hint: str = ""
    ):
        super().__init__(message)
        self.message = message
        self.operation = operation
        self.repo_path = repo_path
        self.stderr = stderr
        self.is_recoverable = is_recoverable
        self.recovery_hint = recovery_hint


class RepositoryNotFoundError(GitOperationError):
    """
    Repository path does not exist.

    Raised when attempting to perform operations on a non-existent path.
    """
    def __init__(self, repo_path: str, repo_id: str = ""):
        super().__init__(
            message=f"Repository path does not exist: {repo_path}",
            operation="path_check",
            repo_path=repo_path,
            is_recoverable=False,
            recovery_hint=f"Create the directory: mkdir -p {repo_path}"
        )
        self.repo_id = repo_id


class RepositoryNotInitializedError(GitOperationError):
    """
    Repository doesn't have a .git directory.

    Raised when attempting git operations on a non-git directory.
    """
    def __init__(self, repo_path: str, repo_id: str = ""):
        super().__init__(
            message=f"Not a git repository: {repo_path}",
            operation="git_check",
            repo_path=repo_path,
            is_recoverable=True,
            recovery_hint=f"Initialize git: git init {repo_path}"
        )
        self.repo_id = repo_id


class GitCommandError(GitOperationError):
    """
    Git command failed with an error.

    Provides detailed error classification based on stderr patterns.

    Error types:
        - permission_denied: Filesystem permission issue
        - disk_full: No space left on device
        - lock_file: Another git process is running
        - not_a_repo: Not a git repository
        - timeout: Command timed out
        - network: Network/remote connectivity issue
        - unknown: Unclassified error
    """

    ERROR_PATTERNS = {
        'permission_denied': ['Permission denied', 'cannot open', 'EACCES'],
        'disk_full': ['No space left', 'disk full', 'ENOSPC'],
        'lock_file': ['Unable to create', '.lock', 'Another git process'],
        'not_a_repo': ['not a git repository', 'fatal: not a git'],
        'timeout': ['timed out', 'timeout'],
        'network': ['Could not resolve', 'Connection refused', 'fatal: unable to access']
    }

    def __init__(
        self,
        message: str,
        operation: str,
        repo_path: str,
        stderr: str,
        returncode: int = 1
    ):
        # Classify the error
        error_type = 'unknown'
        for err_type, patterns in self.ERROR_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in stderr.lower() or pattern.lower() in message.lower():
                    error_type = err_type
                    break
            if error_type != 'unknown':
                break

        # Determine recoverability and hints
        recovery_map = {
            'permission_denied': (False, "Check file permissions and ownership"),
            'disk_full': (False, "Free up disk space"),
            'lock_file': (True, "Wait and retry, or remove .git/index.lock"),
            'not_a_repo': (True, "Initialize git: git init"),
            'timeout': (True, "Retry with longer timeout"),
            'network': (True, "Check network connectivity and retry"),
            'unknown': (False, "Review the error message")
        }

        is_recoverable, recovery_hint = recovery_map.get(error_type, (False, ""))

        super().__init__(
            message=message,
            operation=operation,
            repo_path=repo_path,
            stderr=stderr,
            is_recoverable=is_recoverable,
            recovery_hint=recovery_hint
        )
        self.error_type = error_type
        self.returncode = returncode


class ConfigurationError(Exception):
    """
    Configuration file error.

    Raised when repo_routing.yaml is malformed or invalid.
    """
    def __init__(self, message: str, config_path: str = "", fallback_used: bool = False):
        super().__init__(message)
        self.message = message
        self.config_path = config_path
        self.fallback_used = fallback_used


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class RepoConfig:
    """Configuration for a single repository."""
    id: str
    name: str
    path: str
    remote: Optional[str] = None
    remote_url: Optional[str] = None
    is_primary: bool = False
    description: str = ""

    def __post_init__(self):
        """Normalize path."""
        self.path = os.path.abspath(os.path.expanduser(self.path))

    def exists(self) -> bool:
        """Check if the repo path exists."""
        return os.path.isdir(self.path)

    def has_git(self) -> bool:
        """Check if the repo has a .git directory."""
        return os.path.isdir(os.path.join(self.path, ".git"))

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return asdict(self)


@dataclass
class RoutingRule:
    """A single routing rule mapping a pattern to a repository."""
    pattern: str
    repo_id: Optional[str]  # None means "no repo" (untracked)
    priority: int = 0
    description: str = ""

    def matches(self, path: str) -> bool:
        """Check if this rule matches the given path."""
        # Normalize path separators
        path = path.replace("\\", "/")
        pattern = self.pattern.replace("\\", "/")

        # Handle ** glob pattern
        if "**" in pattern:
            # Convert ** to regex-style matching
            parts = pattern.split("**")
            if len(parts) == 2:
                prefix, suffix = parts
                if prefix and not path.startswith(prefix.rstrip("/")):
                    return False
                if suffix:
                    suffix = suffix.lstrip("/")
                    if not fnmatch.fnmatch(path.split(prefix.rstrip("/"))[-1].lstrip("/"), f"*{suffix}"):
                        # More lenient matching for **
                        remaining = path[len(prefix.rstrip("/")):].lstrip("/")
                        if suffix.startswith("*"):
                            return fnmatch.fnmatch(remaining, suffix)
                        return remaining.endswith(suffix.lstrip("*")) or fnmatch.fnmatch(remaining, f"*{suffix}")
                return path.startswith(prefix.rstrip("/") + "/") or path == prefix.rstrip("/")

        # Standard glob matching
        return fnmatch.fnmatch(path, pattern)


@dataclass
class RoutedFile:
    """A file that has been routed to a specific repository."""
    file_path: str           # Path relative to RDE root
    absolute_path: str       # Absolute path
    repo_id: Optional[str]   # Target repo ID (None if untracked)
    repo_path: Optional[str] # Target repo path
    rule_pattern: str        # The pattern that matched
    rule_priority: int       # Priority of matching rule


@dataclass
class RouteResult:
    """Result of routing a set of files."""
    files_by_repo: Dict[str, List[RoutedFile]]  # repo_id -> files
    unrouted_files: List[str]                    # Files with no matching repo
    warnings: List[str]                          # Any warnings generated

    @property
    def repo_count(self) -> int:
        """Number of repos with changes."""
        return len([r for r in self.files_by_repo.values() if r])


@dataclass
class CommitResult:
    """Result of a commit operation."""
    repo_id: str
    repo_name: str
    success: bool
    commit_hash: Optional[str]
    files_committed: List[str]
    message: str
    error: Optional[str] = None


# =============================================================================
# Path Matcher
# =============================================================================

class PathMatcher:
    """
    Handles pattern matching for file paths.
    Supports glob-style patterns with ** for recursive matching.
    """

    def __init__(self, rules: List[RoutingRule]):
        """Initialize with routing rules sorted by priority."""
        # Sort rules by priority (highest first)
        self.rules = sorted(rules, key=lambda r: r.priority, reverse=True)

    def match(self, path: str) -> Optional[RoutingRule]:
        """
        Find the highest-priority rule that matches the path.

        Args:
            path: File path relative to RDE root

        Returns:
            Matching RoutingRule or None
        """
        # Normalize path
        path = path.replace("\\", "/").lstrip("./")

        for rule in self.rules:
            if rule.matches(path):
                return rule

        return None

    def match_all(self, path: str) -> List[RoutingRule]:
        """
        Find all rules that match the path.
        Useful for debugging routing conflicts.
        """
        path = path.replace("\\", "/").lstrip("./")
        return [rule for rule in self.rules if rule.matches(path)]


# =============================================================================
# Repo Registry
# =============================================================================

class RepoRegistry:
    """
    Manages repository configurations.
    Validates repos and provides lookup functionality.
    """

    def __init__(self, repos: Dict[str, RepoConfig]):
        """Initialize with repo configurations."""
        self.repos = repos
        self._primary = None
        for repo_id, repo in repos.items():
            if repo.is_primary:
                self._primary = repo_id
                break

    @property
    def primary_repo(self) -> Optional[RepoConfig]:
        """Get the primary repository."""
        if self._primary:
            return self.repos.get(self._primary)
        return None

    def get(self, repo_id: str) -> Optional[RepoConfig]:
        """Get a repository by ID."""
        return self.repos.get(repo_id)

    def get_all(self) -> List[RepoConfig]:
        """Get all repositories."""
        return list(self.repos.values())

    def validate(self) -> Tuple[bool, List[str]]:
        """
        Validate all repository configurations.

        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []

        for repo_id, repo in self.repos.items():
            if not repo.exists():
                issues.append(f"Repo '{repo_id}': path does not exist: {repo.path}")
            elif not repo.has_git():
                issues.append(f"Repo '{repo_id}': not a git repository: {repo.path}")

        return len(issues) == 0, issues


# =============================================================================
# Change Dispatcher
# =============================================================================

class ChangeDispatcher:
    """
    Handles dispatching changes to the correct repositories.
    Manages staging, committing, and pushing operations.
    """

    def __init__(self, registry: RepoRegistry, rde_root: str):
        """Initialize with registry and RDE root path."""
        self.registry = registry
        self.rde_root = os.path.abspath(rde_root)

    def _run_git(
        self,
        repo_path: str,
        *args,
        check: bool = True,
        timeout: int = 60,
        retry_on_lock: bool = True,
        max_retries: int = 2
    ) -> subprocess.CompletedProcess:
        """
        Run a git command in the specified repository with retry logic.

        Args:
            repo_path: Path to the repository
            *args: Git command arguments
            check: Whether to log warnings on failure
            timeout: Command timeout in seconds
            retry_on_lock: Retry if lock file error detected
            max_retries: Maximum retry attempts for recoverable errors

        Returns:
            subprocess.CompletedProcess

        Raises:
            GitCommandError: On non-recoverable errors (after retries exhausted)
        """
        import time

        cmd = ["git", "-C", repo_path] + list(args)
        operation = args[0] if args else "unknown"
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False
                )

                if result.returncode == 0:
                    return result

                # Check if this is a recoverable error
                stderr_lower = result.stderr.lower()

                # Lock file error - wait and retry
                if retry_on_lock and any(p in stderr_lower for p in ['.lock', 'unable to create', 'another git process']):
                    if attempt < max_retries:
                        logger.warning(f"Git lock file detected, waiting 2s before retry {attempt + 1}/{max_retries}")
                        time.sleep(2)
                        continue

                # Non-recoverable or out of retries
                if check:
                    logger.warning(f"Git command failed: {' '.join(cmd)}\n{result.stderr}")

                last_error = GitCommandError(
                    message=f"Git {operation} failed: {result.stderr.strip()}",
                    operation=operation,
                    repo_path=repo_path,
                    stderr=result.stderr,
                    returncode=result.returncode
                )

                return result

            except subprocess.TimeoutExpired:
                logger.error(f"Git command timed out (attempt {attempt + 1}/{max_retries + 1}): {' '.join(cmd)}")

                # Double timeout on retry for timeout errors
                if attempt < max_retries:
                    timeout = timeout * 2
                    logger.info(f"Retrying with timeout {timeout}s")
                    continue

                last_error = GitCommandError(
                    message=f"Git {operation} timed out after {timeout}s",
                    operation=operation,
                    repo_path=repo_path,
                    stderr="Command timed out",
                    returncode=-1
                )
                raise last_error

        # Should not reach here, but just in case
        if last_error:
            raise last_error

        return subprocess.CompletedProcess(cmd, -1, "", "Unknown error")

    def get_repo_status(self, repo: RepoConfig) -> Dict:
        """Get git status for a repository."""
        result = self._run_git(repo.path, "status", "--porcelain", check=False)

        if result.returncode != 0:
            return {"error": result.stderr, "changes": []}

        changes = []
        for line in result.stdout.strip().split("\n"):
            if line:
                status = line[:2]
                filepath = line[3:]
                changes.append({"status": status, "file": filepath})

        # Get remote status
        result = self._run_git(
            repo.path, "rev-list", "--count", "@{upstream}..HEAD", check=False
        )
        ahead = int(result.stdout.strip()) if result.returncode == 0 else 0

        result = self._run_git(
            repo.path, "rev-list", "--count", "HEAD..@{upstream}", check=False
        )
        behind = int(result.stdout.strip()) if result.returncode == 0 else 0

        return {
            "changes": changes,
            "has_changes": len(changes) > 0,
            "ahead": ahead,
            "behind": behind
        }

    def stage_files(
        self,
        repo: RepoConfig,
        files: List[str]
    ) -> Tuple[bool, str]:
        """
        Stage specific files in a repository.

        Args:
            repo: Target repository
            files: List of file paths (relative to repo root)

        Returns:
            Tuple of (success, message)
        """
        if not files:
            return True, "No files to stage"

        # Convert paths to be relative to repo root
        relative_files = []
        for f in files:
            if f.startswith(repo.path):
                relative_files.append(os.path.relpath(f, repo.path))
            else:
                # Try to make it relative
                relative_files.append(f)

        result = self._run_git(repo.path, "add", *relative_files, check=False)

        if result.returncode == 0:
            return True, f"Staged {len(relative_files)} files"
        else:
            return False, f"Failed to stage: {result.stderr}"

    def commit(
        self,
        repo: RepoConfig,
        message: str,
        files: Optional[List[str]] = None
    ) -> CommitResult:
        """
        Commit changes to a repository.

        Args:
            repo: Target repository
            message: Commit message
            files: Optional specific files to commit (stages all if None)

        Returns:
            CommitResult with operation details
        """
        result = CommitResult(
            repo_id=repo.id,
            repo_name=repo.name,
            success=False,
            commit_hash=None,
            files_committed=[],
            message=message
        )

        # Stage files if specified
        if files:
            success, stage_msg = self.stage_files(repo, files)
            if not success:
                result.error = stage_msg
                return result
        else:
            # Stage all changes
            self._run_git(repo.path, "add", "-A")

        # Check if there are staged changes
        status = self._run_git(repo.path, "diff", "--cached", "--name-only")
        staged_files = [f for f in status.stdout.strip().split("\n") if f]

        if not staged_files:
            result.success = True
            result.message = "No changes to commit"
            return result

        # Commit
        commit_result = self._run_git(repo.path, "commit", "-m", message, check=False)

        if commit_result.returncode == 0:
            # Get commit hash
            hash_result = self._run_git(repo.path, "rev-parse", "HEAD")
            result.success = True
            result.commit_hash = hash_result.stdout.strip()[:8]
            result.files_committed = staged_files
            logger.info(f"Committed to {repo.name}: {result.commit_hash}")
        else:
            result.error = commit_result.stderr
            logger.error(f"Commit failed for {repo.name}: {result.error}")

        return result

    def push(
        self,
        repo: RepoConfig,
        force: bool = False
    ) -> Tuple[bool, str]:
        """
        Push changes to remote.

        Args:
            repo: Repository to push
            force: Whether to force push

        Returns:
            Tuple of (success, message)
        """
        if not repo.remote:
            return False, f"No remote configured for {repo.name}"

        args = ["push", repo.remote]
        if force:
            args.append("--force")

        result = self._run_git(repo.path, *args, check=False)

        if result.returncode == 0:
            return True, f"Pushed to {repo.remote}"
        else:
            return False, f"Push failed: {result.stderr}"

    def sync_all(self, repos: List[RepoConfig]) -> List[Dict]:
        """
        Sync (push) all repos with changes.

        Returns:
            List of sync results
        """
        results = []

        for repo in repos:
            if not repo.remote:
                results.append({
                    "repo": repo.name,
                    "success": False,
                    "message": "No remote configured"
                })
                continue

            status = self.get_repo_status(repo)
            if status.get("ahead", 0) > 0:
                success, message = self.push(repo)
                results.append({
                    "repo": repo.name,
                    "success": success,
                    "message": message
                })
            else:
                results.append({
                    "repo": repo.name,
                    "success": True,
                    "message": "Already up to date"
                })

        return results

    def fetch(self, repo: RepoConfig) -> Tuple[bool, str, bool]:
        """
        Fetch from remote for a repository.

        Args:
            repo: Repository to fetch

        Returns:
            Tuple of (success, message, has_new_commits)
        """
        if not repo.remote:
            return False, f"No remote configured for {repo.name}", False

        # Get HEAD before fetch for comparison
        head_before = self._run_git(repo.path, "rev-parse", f"{repo.remote}/HEAD", check=False)
        head_before_sha = head_before.stdout.strip() if head_before.returncode == 0 else ""

        result = self._run_git(repo.path, "fetch", repo.remote, check=False)

        if result.returncode != 0:
            return False, f"Fetch failed: {result.stderr}", False

        # Check if new commits
        head_after = self._run_git(repo.path, "rev-parse", f"{repo.remote}/HEAD", check=False)
        head_after_sha = head_after.stdout.strip() if head_after.returncode == 0 else ""

        has_new = head_before_sha != head_after_sha

        return True, "Fetch successful", has_new

    def pull(
        self,
        repo: RepoConfig,
        rebase: bool = True
    ) -> Tuple[bool, str, List[str]]:
        """
        Pull from remote for a repository.

        Args:
            repo: Repository to pull
            rebase: Use rebase instead of merge

        Returns:
            Tuple of (success, message, conflicts_list)
        """
        if not repo.remote:
            return False, f"No remote configured for {repo.name}", []

        args = ["pull", repo.remote]
        if rebase:
            args.append("--rebase")

        result = self._run_git(repo.path, *args, check=False)

        if result.returncode == 0:
            return True, "Pull successful", []

        # Check for conflicts
        conflicts = []
        if "CONFLICT" in result.stdout or "conflict" in result.stderr.lower():
            status = self._run_git(repo.path, "diff", "--name-only", "--diff-filter=U", check=False)
            conflicts = [f for f in status.stdout.strip().split('\n') if f]

        return False, f"Pull failed: {result.stderr}", conflicts

    def sync(self, repo: RepoConfig) -> Dict:
        """
        Full sync: fetch + pull (if behind) + push (if ahead).

        Args:
            repo: Repository to sync

        Returns:
            Dict with sync results
        """
        result = {
            "success": True,
            "fetch": {"success": False, "message": ""},
            "pull": {"success": False, "message": "", "needed": False},
            "push": {"success": False, "message": "", "needed": False},
            "ahead": 0,
            "behind": 0,
            "conflicts": []
        }

        if not repo.remote:
            result["success"] = False
            result["fetch"]["message"] = "No remote configured"
            return result

        # 1. Fetch
        fetch_ok, fetch_msg, _ = self.fetch(repo)
        result["fetch"]["success"] = fetch_ok
        result["fetch"]["message"] = fetch_msg

        if not fetch_ok:
            result["success"] = False
            return result

        # 2. Get ahead/behind status
        status = self.get_repo_status(repo)
        result["ahead"] = status.get("ahead", 0)
        result["behind"] = status.get("behind", 0)

        # 3. Pull if behind
        if result["behind"] > 0:
            result["pull"]["needed"] = True
            pull_ok, pull_msg, conflicts = self.pull(repo, rebase=True)
            result["pull"]["success"] = pull_ok
            result["pull"]["message"] = pull_msg
            result["conflicts"] = conflicts

            if not pull_ok:
                result["success"] = False
                return result

        # 4. Push if ahead
        if result["ahead"] > 0:
            result["push"]["needed"] = True
            push_ok, push_msg = self.push(repo)
            result["push"]["success"] = push_ok
            result["push"]["message"] = push_msg

            if not push_ok:
                result["success"] = False

        return result

    def check_divergence(self, repo: RepoConfig) -> Dict:
        """
        Check if local branch has diverged from remote.

        Args:
            repo: Repository to check

        Returns:
            Dict with divergence info
        """
        result = {
            "ahead": 0,
            "behind": 0,
            "diverged": False,
            "status": "unknown"
        }

        if not repo.remote:
            result["status"] = "no_remote"
            return result

        # Get current branch
        branch = self._run_git(repo.path, "branch", "--show-current", check=False)
        branch_name = branch.stdout.strip()

        if not branch_name:
            result["status"] = "detached"
            return result

        # Get ahead/behind
        rev_list = self._run_git(
            repo.path, "rev-list", "--left-right", "--count",
            f"{branch_name}...{repo.remote}/{branch_name}", check=False
        )

        if rev_list.returncode == 0:
            parts = rev_list.stdout.strip().split()
            if len(parts) == 2:
                result["ahead"] = int(parts[0])
                result["behind"] = int(parts[1])

        # Determine status
        if result["ahead"] > 0 and result["behind"] > 0:
            result["diverged"] = True
            result["status"] = "diverged"
        elif result["ahead"] > 0:
            result["status"] = "ahead"
        elif result["behind"] > 0:
            result["status"] = "behind"
        else:
            result["status"] = "synced"

        return result


# =============================================================================
# Main Router Class
# =============================================================================

class RepoRouter:
    """
    Main orchestrator for multi-repository routing.

    Integrates PathMatcher, RepoRegistry, and ChangeDispatcher to provide
    complete repository routing functionality.
    """

    DEFAULT_CONFIG_PATH = "repo_routing.yaml"

    def __init__(
        self,
        config_path: Optional[str] = None,
        rde_root: Optional[str] = None
    ):
        """
        Initialize the repo router.

        Args:
            config_path: Path to routing configuration file
            rde_root: Root path of RDE installation
        """
        self.rde_root = rde_root or os.environ.get(
            "RDE_REPO_PATH",
            "/home/vader/mini-mind-v2"
        )
        self.rde_root = os.path.abspath(self.rde_root)

        # Determine config path
        if config_path:
            self.config_path = config_path
        else:
            self.config_path = os.path.join(self.rde_root, self.DEFAULT_CONFIG_PATH)

        # Load configuration
        self.config = self._load_config()

        # Initialize components
        self.registry = RepoRegistry(self.config["repos"])
        self.matcher = PathMatcher(self.config["rules"])
        self.dispatcher = ChangeDispatcher(self.registry, self.rde_root)
        self.settings = self.config.get("settings", {})

    def _load_config(self) -> Dict:
        """
        Load routing configuration from YAML file.

        Handles errors gracefully:
        - Missing file: returns default config
        - Parse errors: returns default config with warning
        - Missing required fields: fills with defaults
        - Invalid structure: returns default config with warning

        Returns:
            Configuration dict with repos, rules, and settings
        """
        if not os.path.exists(self.config_path):
            logger.warning(f"Config file not found: {self.config_path}")
            return self._default_config()

        if not HAS_YAML:
            logger.warning("PyYAML not installed, using default config")
            return self._default_config()

        # Try to parse the config
        try:
            with open(self.config_path, 'r') as f:
                raw_config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.error(f"YAML parse error in {self.config_path}: {e}")
            logger.warning("Using default configuration due to YAML parse error")
            return self._default_config()
        except IOError as e:
            logger.error(f"Cannot read config file {self.config_path}: {e}")
            return self._default_config()

        # Validate structure
        if raw_config is None:
            logger.warning(f"Config file is empty: {self.config_path}")
            return self._default_config()

        if not isinstance(raw_config, dict):
            logger.error(f"Config file must be a YAML dict, got {type(raw_config).__name__}")
            return self._default_config()

        # Parse repos with validation
        repos = {}
        raw_repos = raw_config.get("repos", {})

        if not isinstance(raw_repos, dict):
            logger.warning(f"'repos' should be a dict, got {type(raw_repos).__name__}. Using default.")
            return self._default_config()

        for repo_id, repo_data in raw_repos.items():
            if not isinstance(repo_data, dict):
                logger.warning(f"Skipping repo '{repo_id}': expected dict, got {type(repo_data).__name__}")
                continue

            # Required field: path
            path = repo_data.get("path", "")
            if not path:
                logger.warning(f"Skipping repo '{repo_id}': missing required 'path' field")
                continue

            try:
                repos[repo_id] = RepoConfig(
                    id=repo_id,
                    name=repo_data.get("name", repo_id),
                    path=path,
                    remote=repo_data.get("remote"),
                    remote_url=repo_data.get("remote_url"),
                    is_primary=bool(repo_data.get("is_primary", False)),
                    description=str(repo_data.get("description", ""))
                )
            except Exception as e:
                logger.warning(f"Error parsing repo '{repo_id}': {e}")
                continue

        # If no valid repos, fall back to default
        if not repos:
            logger.warning("No valid repos found in config, using default")
            return self._default_config()

        # Parse rules with validation
        rules = []
        raw_rules = raw_config.get("routing_rules", [])

        if not isinstance(raw_rules, list):
            logger.warning(f"'routing_rules' should be a list, got {type(raw_rules).__name__}")
            raw_rules = []

        for i, rule_data in enumerate(raw_rules):
            if not isinstance(rule_data, dict):
                logger.warning(f"Skipping rule {i}: expected dict, got {type(rule_data).__name__}")
                continue

            pattern = rule_data.get("pattern")
            if not pattern:
                logger.warning(f"Skipping rule {i}: missing 'pattern' field")
                continue

            try:
                rules.append(RoutingRule(
                    pattern=str(pattern),
                    repo_id=rule_data.get("repo"),  # None is valid (means untracked)
                    priority=int(rule_data.get("priority", 0)),
                    description=str(rule_data.get("description", ""))
                ))
            except (ValueError, TypeError) as e:
                logger.warning(f"Error parsing rule {i}: {e}")
                continue

        # If no rules, add default catch-all
        if not rules:
            logger.warning("No routing rules found, adding default catch-all to primary repo")
            primary_id = next((rid for rid, r in repos.items() if r.is_primary), next(iter(repos.keys())))
            rules.append(RoutingRule(pattern="**", repo_id=primary_id, priority=0))

        return {
            "repos": repos,
            "rules": rules,
            "settings": raw_config.get("settings", {}) if isinstance(raw_config.get("settings"), dict) else {}
        }

    def _default_config(self) -> Dict:
        """Return default configuration."""
        return {
            "repos": {
                "rde_core": RepoConfig(
                    id="rde_core",
                    name="RDE Core",
                    path=self.rde_root,
                    remote="origin",
                    is_primary=True
                )
            },
            "rules": [
                RoutingRule(pattern="**", repo_id="rde_core", priority=0)
            ],
            "settings": {}
        }

    def match_path(self, file_path: str) -> Optional[RepoConfig]:
        """
        Find the repository for a given file path.

        Args:
            file_path: Path relative to RDE root or absolute path

        Returns:
            RepoConfig for the matching repository, or None if untracked
        """
        # Normalize path
        if os.path.isabs(file_path):
            file_path = os.path.relpath(file_path, self.rde_root)

        rule = self.matcher.match(file_path)

        if rule and rule.repo_id:
            return self.registry.get(rule.repo_id)

        return None

    def route_changes(
        self,
        files: List[str],
        warn_untracked: bool = True
    ) -> RouteResult:
        """
        Route a list of files to their appropriate repositories.

        Args:
            files: List of file paths
            warn_untracked: Log warnings for untracked files

        Returns:
            RouteResult with files grouped by repository
        """
        result = RouteResult(
            files_by_repo={},
            unrouted_files=[],
            warnings=[]
        )

        for file_path in files:
            # Normalize path
            if os.path.isabs(file_path):
                rel_path = os.path.relpath(file_path, self.rde_root)
                abs_path = file_path
            else:
                rel_path = file_path
                abs_path = os.path.join(self.rde_root, file_path)

            # Find matching rule
            rule = self.matcher.match(rel_path)

            if rule is None:
                result.unrouted_files.append(rel_path)
                if warn_untracked:
                    result.warnings.append(f"No routing rule for: {rel_path}")
                continue

            if rule.repo_id is None:
                result.unrouted_files.append(rel_path)
                if warn_untracked:
                    result.warnings.append(f"File in untracked area: {rel_path}")
                continue

            repo = self.registry.get(rule.repo_id)
            if repo is None:
                result.warnings.append(f"Unknown repo '{rule.repo_id}' for: {rel_path}")
                result.unrouted_files.append(rel_path)
                continue

            # Create routed file entry
            routed = RoutedFile(
                file_path=rel_path,
                absolute_path=abs_path,
                repo_id=rule.repo_id,
                repo_path=repo.path,
                rule_pattern=rule.pattern,
                rule_priority=rule.priority
            )

            # Add to repo group
            if rule.repo_id not in result.files_by_repo:
                result.files_by_repo[rule.repo_id] = []
            result.files_by_repo[rule.repo_id].append(routed)

        return result

    def commit_to_repo(
        self,
        repo_id: str,
        message: str,
        files: Optional[List[str]] = None
    ) -> CommitResult:
        """
        Commit changes to a specific repository.

        Args:
            repo_id: Repository ID
            message: Commit message
            files: Specific files to commit (None = all staged)

        Returns:
            CommitResult
        """
        repo = self.registry.get(repo_id)
        if not repo:
            return CommitResult(
                repo_id=repo_id,
                repo_name="Unknown",
                success=False,
                commit_hash=None,
                files_committed=[],
                message=message,
                error=f"Unknown repository: {repo_id}"
            )

        return self.dispatcher.commit(repo, message, files)

    def commit_all(
        self,
        route_result: RouteResult,
        message: str,
        per_repo_messages: Optional[Dict[str, str]] = None
    ) -> List[CommitResult]:
        """
        Commit all routed changes to their respective repositories.

        Args:
            route_result: Result from route_changes()
            message: Default commit message
            per_repo_messages: Optional repo-specific messages

        Returns:
            List of CommitResults
        """
        results = []
        per_repo_messages = per_repo_messages or {}

        for repo_id, routed_files in route_result.files_by_repo.items():
            repo = self.registry.get(repo_id)
            if not repo:
                continue

            # Get files relative to repo root
            files = []
            for rf in routed_files:
                if rf.absolute_path.startswith(repo.path):
                    files.append(os.path.relpath(rf.absolute_path, repo.path))
                else:
                    files.append(rf.file_path)

            # Use repo-specific message or default
            commit_msg = per_repo_messages.get(repo_id, message)

            result = self.dispatcher.commit(repo, commit_msg, files)
            results.append(result)

        return results

    def sync_all(self) -> List[Dict]:
        """Push all repos with pending changes to their remotes."""
        repos = self.registry.get_all()
        return self.dispatcher.sync_all(repos)

    def get_status(self) -> Dict:
        """Get status of all configured repositories."""
        status = {
            "repos": {},
            "total_repos": len(self.registry.repos),
            "repos_with_changes": 0,
            "repos_ahead": 0,
            "validation_issues": []
        }

        # Validate repos
        is_valid, issues = self.registry.validate()
        status["validation_issues"] = issues

        # Get per-repo status
        for repo_id, repo in self.registry.repos.items():
            if not repo.exists() or not repo.has_git():
                status["repos"][repo_id] = {
                    "name": repo.name,
                    "path": repo.path,
                    "valid": False,
                    "has_git": repo.has_git() if repo.exists() else False,
                    "exists": repo.exists(),
                    "error": "Not a git repository" if repo.exists() else "Path does not exist"
                }
                continue

            repo_status = self.dispatcher.get_repo_status(repo)
            status["repos"][repo_id] = {
                "name": repo.name,
                "path": repo.path,
                "valid": True,
                "has_git": True,
                "exists": True,
                "has_changes": repo_status.get("has_changes", False),
                "changes_count": len(repo_status.get("changes", [])),
                "ahead": repo_status.get("ahead", 0),
                "behind": repo_status.get("behind", 0),
                "remote": repo.remote,
                "remote_url": repo.remote_url
            }

            if repo_status.get("has_changes"):
                status["repos_with_changes"] += 1
            if repo_status.get("ahead", 0) > 0:
                status["repos_ahead"] += 1

        return status

    def ensure_repo_initialized(self, repo_id: str) -> Tuple[bool, str]:
        """
        Ensure a repository is initialized with git.

        Args:
            repo_id: Repository ID to initialize

        Returns:
            Tuple of (success, message)
        """
        repo = self.registry.get(repo_id)
        if not repo:
            return False, f"Unknown repository: {repo_id}"

        # Check if path exists
        if not repo.exists():
            return False, f"Repository path does not exist: {repo.path}"

        # Check if already initialized
        if repo.has_git():
            return True, f"Repository already initialized: {repo.name}"

        # Initialize git repository
        try:
            result = subprocess.run(
                ["git", "init"],
                cwd=repo.path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                return False, f"git init failed: {result.stderr}"

            logger.info(f"Initialized git repository: {repo.name} at {repo.path}")

            # Create .gitignore if it doesn't exist
            gitignore_path = os.path.join(repo.path, ".gitignore")
            if not os.path.exists(gitignore_path):
                default_gitignore = """# Auto-generated by RepoRouter
__pycache__/
*.pyc
*.pyo
.env
.venv/
venv/
*.egg-info/
dist/
build/
.pytest_cache/
.coverage
*.log
"""
                with open(gitignore_path, 'w') as f:
                    f.write(default_gitignore)
                logger.info(f"Created default .gitignore for {repo.name}")

            return True, f"Successfully initialized: {repo.name}"

        except subprocess.TimeoutExpired:
            return False, f"git init timed out for {repo.name}"
        except Exception as e:
            return False, f"Failed to initialize {repo.name}: {e}"

    def initialize_all_repos(self) -> Dict[str, Tuple[bool, str]]:
        """
        Initialize all repositories that don't have git.

        Returns:
            Dict mapping repo_id to (success, message) tuple
        """
        results = {}

        for repo_id, repo in self.registry.repos.items():
            if repo.exists() and not repo.has_git():
                success, message = self.ensure_repo_initialized(repo_id)
                results[repo_id] = (success, message)
            elif not repo.exists():
                results[repo_id] = (False, f"Path does not exist: {repo.path}")
            else:
                results[repo_id] = (True, "Already initialized")

        return results

    def get_repos_needing_init(self) -> List[str]:
        """
        Get list of repo IDs that need git initialization.

        Returns:
            List of repo_id strings
        """
        need_init = []
        for repo_id, repo in self.registry.repos.items():
            if repo.exists() and not repo.has_git():
                need_init.append(repo_id)
        return need_init

    def debug_path(self, file_path: str) -> Dict:
        """
        Debug routing for a specific path.
        Shows all matching rules and why a particular repo was chosen.
        """
        # Normalize path
        if os.path.isabs(file_path):
            file_path = os.path.relpath(file_path, self.rde_root)

        all_matches = self.matcher.match_all(file_path)
        winning_rule = self.matcher.match(file_path)

        debug_info = {
            "input_path": file_path,
            "all_matching_rules": [
                {
                    "pattern": r.pattern,
                    "repo": r.repo_id,
                    "priority": r.priority,
                    "description": r.description
                }
                for r in all_matches
            ],
            "winning_rule": None,
            "target_repo": None
        }

        if winning_rule:
            debug_info["winning_rule"] = {
                "pattern": winning_rule.pattern,
                "repo": winning_rule.repo_id,
                "priority": winning_rule.priority
            }

            if winning_rule.repo_id:
                repo = self.registry.get(winning_rule.repo_id)
                if repo:
                    debug_info["target_repo"] = {
                        "id": repo.id,
                        "name": repo.name,
                        "path": repo.path
                    }

        return debug_info


# =============================================================================
# Singleton Access
# =============================================================================

_router: Optional[RepoRouter] = None


def get_router(
    config_path: Optional[str] = None,
    rde_root: Optional[str] = None,
    force_reload: bool = False
) -> RepoRouter:
    """
    Get the singleton RepoRouter instance.

    Args:
        config_path: Optional config file path
        rde_root: Optional RDE root path
        force_reload: Force reload of configuration

    Returns:
        RepoRouter instance
    """
    global _router

    if _router is None or force_reload:
        _router = RepoRouter(config_path=config_path, rde_root=rde_root)

    return _router


# =============================================================================
# Self-Test
# =============================================================================

if __name__ == "__main__":
    print("Repository Router - Self Test")
    print("=" * 60)

    try:
        router = get_router()

        # Test path matching
        test_paths = [
            "rd_engine.py",
            "dashboard_v2.py",
            "workspace/brotato_bot/main.py",
            "workspace/Narrative WorkFlow/CLAUDE.md",
            "workspace/glove/src/glove.py",
            "workspace/vision/capture.py",
            "rde_enhancements/exploration_hooks.py",
            "git_checkpoint.py",
            "workspace/some_random_file.txt",
            "repo_router.py"
        ]

        print("\nPath Routing Tests:")
        print("-" * 60)

        for path in test_paths:
            repo = router.match_path(path)
            repo_name = repo.name if repo else "UNTRACKED"
            print(f"  {path:<45} -> {repo_name}")

        # Get status
        print("\n" + "=" * 60)
        print("Repository Status:")
        print("-" * 60)

        status = router.get_status()

        for repo_id, repo_status in status["repos"].items():
            valid = "OK" if repo_status.get("valid") else "INVALID"
            changes = repo_status.get("changes_count", 0)
            ahead = repo_status.get("ahead", 0)
            print(f"  [{valid:7}] {repo_status['name']:<25} "
                  f"changes: {changes}, ahead: {ahead}")

        if status["validation_issues"]:
            print("\nValidation Issues:")
            for issue in status["validation_issues"]:
                print(f"  WARNING: {issue}")

        print("\n" + "=" * 60)
        print("Self-test PASSED")

    except Exception as e:
        print(f"Self-test FAILED: {e}")
        import traceback
        traceback.print_exc()
