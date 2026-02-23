"""
af_engine.integrations.git - Checkpoint-Based Git Commits

This integration creates git commits at checkpoints during mission execution.

GIT STRATEGY (AF_GIT_STRATEGY env var):
  "branch"   - Route all [AF] checkpoint commits to 'af-missions/checkpoints' orphan branch.
                (DEFAULT) Main branch is never touched by mission artifacts.
  "disabled" - No git commits at all. Mission state lives on filesystem only.

Real code changes should be committed via scripts/release_workflow.py which creates
clean "Release vX.X.X" commits on main.
"""

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)

# Git strategy: "branch" routes [AF] commits to orphan branch; "disabled" skips all commits
AF_GIT_STRATEGY = os.environ.get("AF_GIT_STRATEGY", "branch")
AF_CHECKPOINT_BRANCH = "af-missions/checkpoints"


class GitIntegration(BaseIntegrationHandler):
    """
    Creates git commits at checkpoints during mission execution.

    strategy="branch" (default): Checkpoint commits go to 'af-missions/checkpoints'
    orphan branch. Main branch is never touched by [AF] commits.

    strategy="disabled": No git commits. Mission state lives on filesystem only.
    """

    name = "git"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.STAGE_COMPLETED,
        StageEvent.MISSION_COMPLETED,
        StageEvent.CYCLE_COMPLETED,
    ]

    def __init__(self, workspace_dir: Optional[Path] = None):
        super().__init__()
        self.workspace_dir = workspace_dir or Path.cwd()
        self.strategy = AF_GIT_STRATEGY
        self.checkpoint_branch = AF_CHECKPOINT_BRANCH

    def _check_availability(self) -> bool:
        try:
            result = subprocess.run(["git", "--version"], capture_output=True, timeout=5)
            return result.returncode == 0
        except Exception:
            return False

    def on_stage_completed(self, event: Event) -> None:
        if event.stage == "BUILDING":
            self._create_checkpoint_commit(
                f"[AF] Build checkpoint - {event.mission_id}",
                event.data.get("files_created", []) + event.data.get("files_modified", [])
            )

    def on_cycle_completed(self, event: Event) -> None:
        cycle_number = event.data.get("cycle_number", 0)
        self._create_checkpoint_commit(
            f"[AF] Cycle {cycle_number} complete - {event.mission_id}", []
        )

    def on_mission_completed(self, event: Event) -> None:
        self._create_checkpoint_commit(
            f"[AF] Mission complete - {event.mission_id}", []
        )

    def _get_repo_root(self) -> Optional[Path]:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=self.workspace_dir, capture_output=True, timeout=10, text=True,
            )
            if result.returncode == 0:
                return Path(result.stdout.strip())
        except Exception:
            pass
        return None

    def _ensure_checkpoint_branch_exists(self, repo_root: Path) -> bool:
        """Create af-missions/checkpoints orphan branch if it doesn't exist."""
        try:
            ref_check = subprocess.run(
                ["git", "show-ref", "--verify", f"refs/heads/{self.checkpoint_branch}"],
                cwd=repo_root, capture_output=True, timeout=10,
            )
            if ref_check.returncode == 0:
                return True

            # Create orphan branch via temporary worktree
            worktree_path = Path(tempfile.mkdtemp(prefix="af_init_"))
            try:
                init_result = subprocess.run(
                    ["git", "worktree", "add", "--orphan", "-b",
                     self.checkpoint_branch, str(worktree_path)],
                    cwd=repo_root, capture_output=True, timeout=30, text=True,
                )
                if init_result.returncode != 0:
                    logger.warning(f"Failed to create orphan branch: {init_result.stderr}")
                    return False
                subprocess.run(
                    ["git", "commit", "--allow-empty", "-m",
                     f"[AF] Initialize {self.checkpoint_branch} branch"],
                    cwd=worktree_path, capture_output=True, timeout=30,
                )
                return True
            finally:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=repo_root, capture_output=True, timeout=30,
                )
                if worktree_path.exists():
                    shutil.rmtree(worktree_path, ignore_errors=True)
        except Exception as e:
            logger.warning(f"Failed to ensure checkpoint branch: {e}")
            return False

    def _create_checkpoint_commit_on_branch(
        self, message: str, files_to_include: List[str], repo_root: Path
    ) -> bool:
        """Commit to af-missions/checkpoints via worktree without touching main."""
        worktree_path = Path(tempfile.mkdtemp(prefix="af_ckpt_"))
        try:
            add_result = subprocess.run(
                ["git", "worktree", "add", str(worktree_path), self.checkpoint_branch],
                cwd=repo_root, capture_output=True, timeout=30, text=True,
            )
            if add_result.returncode != 0:
                logger.warning(f"Failed to add worktree: {add_result.stderr}")
                return False

            # Determine which files to stage in the checkpoint branch
            if files_to_include:
                files_to_copy = [Path(f) for f in files_to_include if Path(f).exists()]
            else:
                status_result = subprocess.run(
                    ["git", "status", "--porcelain", "--untracked-files=all"],
                    cwd=repo_root, capture_output=True, timeout=30, text=True,
                )
                files_to_copy = []
                for line in status_result.stdout.splitlines():
                    if len(line) > 3:
                        filepath = line[3:].strip().strip('"')
                        p = repo_root / filepath
                        if p.exists() and p.is_file():
                            files_to_copy.append(p)

            if not files_to_copy:
                logger.debug("No files to commit to checkpoint branch")
                return False

            copied = 0
            for src in files_to_copy:
                try:
                    rel = src.relative_to(repo_root)
                    dest = worktree_path / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dest)
                    copied += 1
                except (ValueError, OSError) as e:
                    logger.debug(f"Skipping {src}: {e}")

            if copied == 0:
                logger.debug("No files copied to checkpoint worktree")
                return False

            subprocess.run(["git", "add", "-A"], cwd=worktree_path,
                           capture_output=True, timeout=30)

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_path, capture_output=True, timeout=10, text=True,
            )
            if not status.stdout.strip():
                logger.debug("No changes to commit to checkpoint branch")
                return False

            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=worktree_path, capture_output=True, timeout=30, text=True,
            )
            if result.returncode == 0:
                logger.info(f"Checkpoint commit on {self.checkpoint_branch}: {message}")
                return True
            else:
                logger.warning(f"Checkpoint commit failed: {result.stderr}")
                return False

        except Exception as e:
            logger.warning(f"Checkpoint branch commit failed: {e}")
            return False
        finally:
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", str(worktree_path)],
                    cwd=repo_root, capture_output=True, timeout=30,
                )
            except Exception:
                pass
            if worktree_path.exists():
                shutil.rmtree(worktree_path, ignore_errors=True)

    def _create_checkpoint_commit(self, message: str, files: List[str]) -> bool:
        """Route checkpoint commit based on configured strategy."""
        if self.strategy == "disabled":
            logger.debug(f"Git strategy disabled, skipping: {message}")
            return False

        if self.strategy == "branch":
            repo_root = self._get_repo_root()
            if repo_root is None:
                logger.warning("Could not find repo root, skipping checkpoint commit")
                return False
            if not self._ensure_checkpoint_branch_exists(repo_root):
                logger.warning("Could not create checkpoint branch, falling back to main")
                return self._create_checkpoint_commit_on_main(message, files)
            return self._create_checkpoint_commit_on_branch(message, files, repo_root)

        logger.warning(f"Unknown AF_GIT_STRATEGY '{self.strategy}', committing on main")
        return self._create_checkpoint_commit_on_main(message, files)

    def _create_checkpoint_commit_on_main(self, message: str, files: List[str]) -> bool:
        """Legacy fallback: commit on current branch. Avoid using this."""
        try:
            if files:
                for f in files:
                    subprocess.run(["git", "add", f], cwd=self.workspace_dir,
                                   capture_output=True, timeout=30)
            else:
                subprocess.run(["git", "add", "-A"], cwd=self.workspace_dir,
                               capture_output=True, timeout=30)

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.workspace_dir, capture_output=True, timeout=10,
            )
            if not status.stdout.strip():
                logger.debug("No changes to commit")
                return False

            result = subprocess.run(
                ["git", "commit", "-m", message],
                cwd=self.workspace_dir, capture_output=True, timeout=30,
            )
            if result.returncode == 0:
                logger.info(f"Git commit on main (legacy fallback): {message}")
                return True
            else:
                logger.warning(f"Git commit failed: {result.stderr.decode()}")
                return False
        except Exception as e:
            logger.warning(f"Git operation failed: {e}")
            return False


def create_release_commit(version: str, description: str, tag: bool = True) -> bool:
    """
    Create a clean release commit bundling all real code changes since last release.

    Checks origin/main..HEAD for non-[AF] commits. If real changes exist, creates
    a "Release vX.X.X - description" commit and optionally tags it.

    Usage:
        from af_engine.integrations.git import create_release_commit
        create_release_commit("1.9.2", "Clean git commit strategy implementation")
    """
    repo_root = Path.cwd()
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo_root, capture_output=True, timeout=10, text=True,
        )
        if r.returncode == 0:
            repo_root = Path(r.stdout.strip())
    except Exception:
        pass

    log_result = subprocess.run(
        ["git", "log", "--oneline", "origin/main..HEAD"],
        cwd=repo_root, capture_output=True, timeout=30, text=True,
    )
    if log_result.returncode != 0:
        print(f"ERROR: Could not check git log: {log_result.stderr}")
        return False

    commits = log_result.stdout.strip().splitlines()
    af_commits = [c for c in commits if "[AF]" in c]
    real_commits = [c for c in commits if "[AF]" not in c]
    print(f"Commits ahead of origin/main: {len(commits)} total")
    print(f"  [AF] artifact commits: {len(af_commits)}")
    print(f"  Real code commits: {len(real_commits)}")

    status_result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_root, capture_output=True, timeout=10, text=True,
    )
    has_uncommitted = bool(status_result.stdout.strip())

    if not real_commits and not has_uncommitted:
        print("WARNING: No real code changes found. Nothing to release.")
        print("Run scripts/clean_push.py to squash/push the [AF] commits if needed.")
        return False

    subprocess.run(["git", "add", "-A"], cwd=repo_root, capture_output=True, timeout=30)

    commit_msg = f"Release v{version} - {description}"
    commit_result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        cwd=repo_root, capture_output=True, timeout=30, text=True,
    )
    if commit_result.returncode != 0:
        if "nothing to commit" in (commit_result.stdout + commit_result.stderr):
            print("No staged changes to commit.")
            return False
        print(f"ERROR: Release commit failed: {commit_result.stderr}")
        return False

    print(f"Release commit created: {commit_msg}")

    if tag:
        tag_result = subprocess.run(
            ["git", "tag", f"v{version}", "-m", commit_msg],
            cwd=repo_root, capture_output=True, timeout=10, text=True,
        )
        if tag_result.returncode == 0:
            print(f"Git tag created: v{version}")
        else:
            print(f"WARNING: Tag creation failed: {tag_result.stderr}")

    return True
