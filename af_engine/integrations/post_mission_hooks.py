"""
af_engine.integrations.post_mission_hooks - Custom Post-Mission Scripts

This integration runs custom scripts after mission completion.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)

ALLOWED_SHEBANGS = frozenset([
    "#!/bin/bash",
    "#!/bin/sh",
    "#!/usr/bin/env bash",
    "#!/usr/bin/env python3",
    "#!/usr/bin/env python",
])

ALLOWED_FILENAME_RE = re.compile(r'^[a-zA-Z0-9_-]+\.(sh|py|bash)$')


class PostMissionHooksIntegration(BaseIntegrationHandler):
    """
    Executes custom scripts after mission completion.

    Looks for executable scripts in the hooks directory and
    runs them with mission context as environment variables.
    """

    name = "post_mission_hooks"
    priority = IntegrationPriority.BACKGROUND
    subscriptions = [
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, hooks_dir: Optional[Path] = None):
        """Initialize post-mission hooks."""
        super().__init__()
        self.hooks_dir = hooks_dir or Path(".af_hooks/post_mission")

    def on_mission_completed(self, event: Event) -> None:
        """Run post-mission hooks."""
        if not self.hooks_dir.exists():
            return

        hooks = self._find_hooks()
        if not hooks:
            return

        # Build environment with mission context
        env = self._build_env(event)

        for hook in hooks:
            self._run_hook(hook, env)

    def _validate_hook(self, hook: Path) -> tuple:
        """Validate a hook script before execution.

        Returns (True, "ok") if the hook passes all security checks,
        or (False, reason) if it should be rejected.
        """
        # 1. Resolve symlinks — containment check
        try:
            real = hook.resolve()
            hooks_real = self.hooks_dir.resolve()
            real.relative_to(hooks_real)  # raises ValueError if outside
        except (ValueError, OSError):
            return False, "path escapes hooks_dir (possible symlink attack)"

        # 2. Filename pattern
        if not ALLOWED_FILENAME_RE.match(hook.name):
            return False, f"filename '{hook.name}' does not match allowed pattern"

        # 3. Ownership — must be owned by current process UID
        stat = hook.stat()
        if stat.st_uid != os.getuid():
            return False, f"not owned by current user (owner uid={stat.st_uid})"

        # 4. World-writable check
        if stat.st_mode & 0o002:
            return False, "world-writable — rejecting to prevent tampering"

        # 5. Shebang allowlist
        try:
            with open(hook, 'rb') as f:
                first_line = f.readline(256).decode('utf-8', errors='replace').rstrip()
        except OSError as e:
            return False, f"cannot read file: {e}"

        if first_line not in ALLOWED_SHEBANGS:
            return False, f"shebang '{first_line[:60]}' not in allowed list"

        return True, "ok"

    def _find_hooks(self) -> List[Path]:
        """Find executable hook scripts, applying security validation."""
        hooks = []

        if not self.hooks_dir.is_dir():
            return hooks

        for item in sorted(self.hooks_dir.iterdir()):
            if not (item.is_file() and item.stat().st_mode & 0o111):
                continue
            valid, reason = self._validate_hook(item)
            if valid:
                hooks.append(item)
            else:
                logger.warning(f"Hook {item.name} rejected: {reason}")

        return hooks

    def _build_env(self, event: Event) -> dict:
        """Build environment variables for hook execution."""
        env = os.environ.copy()

        # Add mission context
        env["AF_MISSION_ID"] = event.mission_id
        env["AF_STAGE"] = event.stage

        # Add event data
        data = event.data
        env["AF_TOTAL_CYCLES"] = str(data.get("total_cycles", 0))
        env["AF_DELIVERABLES"] = ",".join(data.get("deliverables", []))

        return env

    def _run_hook(self, hook: Path, env: dict) -> bool:
        """Execute a hook script."""
        try:
            result = subprocess.run(
                [str(hook)],
                env=env,
                capture_output=True,
                timeout=60,  # 1 minute timeout
            )

            if result.returncode == 0:
                logger.info(f"Hook {hook.name} executed successfully")
                return True
            else:
                logger.warning(
                    f"Hook {hook.name} failed with code {result.returncode}: "
                    f"{result.stderr.decode()}"
                )
                return False

        except subprocess.TimeoutExpired:
            logger.warning(f"Hook {hook.name} timed out")
            return False
        except Exception as e:
            logger.warning(f"Hook {hook.name} error: {e}")
            return False
