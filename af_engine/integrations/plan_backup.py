"""
af_engine.integrations.plan_backup - Plan File Backup

This integration backs up the implementation plan before the building
stage begins, enabling recovery if the plan is accidentally modified.
"""

import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)


class PlanBackupIntegration(BaseIntegrationHandler):
    """
    Backs up implementation plan before building stage.

    Creates timestamped backups of the plan file to enable
    recovery if it's accidentally modified during building.
    """

    name = "plan_backup"
    priority = IntegrationPriority.NORMAL
    subscriptions = [
        StageEvent.STAGE_STARTED,
    ]

    def __init__(self, backup_dir: Optional[Path] = None):
        """Initialize plan backup integration."""
        super().__init__()
        self.backup_dir = backup_dir
        self.last_backup = None

    def on_stage_started(self, event: Event) -> None:
        """Backup plan when entering BUILDING stage."""
        if event.stage != "BUILDING":
            return

        # Get artifacts directory from event data
        artifacts_dir = event.data.get("artifacts_dir")
        if not artifacts_dir:
            logger.debug("No artifacts directory in event data")
            return

        plan_file = Path(artifacts_dir) / "implementation_plan.md"
        if not plan_file.exists():
            logger.warning(f"Plan file not found: {plan_file}")
            return

        self._backup_plan(plan_file)

    def _backup_plan(self, plan_file: Path) -> Optional[Path]:
        """Create backup of plan file."""
        try:
            # Determine backup directory
            if self.backup_dir is None:
                self.backup_dir = plan_file.parent / ".plan_backups"

            self.backup_dir.mkdir(parents=True, exist_ok=True)

            # Create timestamped backup
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"implementation_plan_{timestamp}.md"
            backup_path = self.backup_dir / backup_name

            shutil.copy2(plan_file, backup_path)
            self.last_backup = backup_path

            logger.info(f"Plan backed up to: {backup_path}")
            return backup_path

        except Exception as e:
            logger.warning(f"Plan backup failed: {e}")
            return None

    def get_last_backup(self) -> Optional[Path]:
        """Get path to most recent plan backup."""
        return self.last_backup

    def restore_from_backup(self, backup_path: Optional[Path] = None) -> bool:
        """Restore plan from backup."""
        backup = backup_path or self.last_backup
        if not backup or not backup.exists():
            logger.error("No backup available to restore")
            return False

        try:
            # Find original plan location
            original = backup.parent.parent / "implementation_plan.md"
            shutil.copy2(backup, original)
            logger.info(f"Plan restored from: {backup}")
            return True
        except Exception as e:
            logger.error(f"Plan restore failed: {e}")
            return False
