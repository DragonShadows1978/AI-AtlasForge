"""
af_engine.integrations.post_mission_hooks - Custom Post-Mission Scripts

This integration runs custom scripts after mission completion.
"""

import logging
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

    def _find_hooks(self) -> List[Path]:
        """Find executable hook scripts."""
        hooks = []

        if not self.hooks_dir.is_dir():
            return hooks

        for item in sorted(self.hooks_dir.iterdir()):
            if item.is_file() and item.stat().st_mode & 0o111:
                hooks.append(item)

        return hooks

    def _build_env(self, event: Event) -> dict:
        """Build environment variables for hook execution."""
        import os
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
