"""
af_engine.state_manager - Mission State Persistence

This module provides the StateManager class for loading, saving, and
managing mission state.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages mission state persistence.

    The StateManager handles:
    - Loading/saving mission state from/to disk
    - Cycle history tracking
    - Iteration management
    - Mission history logging
    """

    def __init__(self, mission_path: Path, auto_save: bool = True):
        """
        Initialize the state manager.

        Args:
            mission_path: Path to the mission JSON file
            auto_save: Whether to auto-save after modifications
        """
        self.mission_path = mission_path
        self.auto_save = auto_save
        self._mission: Dict[str, Any] = {}
        self._dirty: bool = False

    @property
    def mission(self) -> Dict[str, Any]:
        """Get the current mission state."""
        if not self._mission:
            self.load_mission()
        return self._mission

    @mission.setter
    def mission(self, value: Dict[str, Any]) -> None:
        """Set the mission state."""
        self._mission = value
        self._dirty = True
        if self.auto_save:
            self.save_mission()

    def load_mission(self) -> Dict[str, Any]:
        """
        Load mission from disk.

        Returns:
            The mission dictionary
        """
        default_mission = self._get_default_mission()

        try:
            # Use io_utils for atomic reads if available
            try:
                import io_utils
                self._mission = io_utils.atomic_read_json(self.mission_path, default_mission)
            except ImportError:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        self._mission = json.load(f)
                else:
                    self._mission = default_mission

            self._dirty = False
            logger.debug(f"Loaded mission from {self.mission_path}")

        except Exception as e:
            logger.error(f"Failed to load mission: {e}")
            self._mission = default_mission

        return self._mission

    def save_mission(self) -> None:
        """Save mission to disk."""
        try:
            # Update last_updated timestamp
            self._mission["last_updated"] = datetime.now().isoformat()

            # Use io_utils for atomic writes if available
            try:
                import io_utils
                io_utils.atomic_write_json(self.mission_path, self._mission)
            except ImportError:
                self.mission_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.mission_path, 'w') as f:
                    json.dump(self._mission, f, indent=2)

            self._dirty = False
            logger.debug(f"Saved mission to {self.mission_path}")

        except Exception as e:
            logger.error(f"Failed to save mission: {e}")
            raise

    def _get_default_mission(self) -> Dict[str, Any]:
        """Get default mission structure."""
        return {
            "mission_id": "default",
            "problem_statement": "No mission defined. Please set a mission.",
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "preferences": {},
            "success_criteria": [],
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            # Cycle iteration fields
            "cycle_budget": 1,
            "current_cycle": 1,
            "cycle_history": [],
            "original_problem_statement": None,
        }

    # =========================================================================
    # Property accessors
    # =========================================================================

    @property
    def mission_id(self) -> str:
        """Get mission ID."""
        return self.mission.get("mission_id", "default")

    @property
    def current_stage(self) -> str:
        """Get current stage."""
        return self.mission.get("current_stage", "PLANNING")

    @current_stage.setter
    def current_stage(self, value: str) -> None:
        """Set current stage."""
        self._mission["current_stage"] = value
        self._dirty = True
        if self.auto_save:
            self.save_mission()

    @property
    def iteration(self) -> int:
        """Get current iteration."""
        return self.mission.get("iteration", 0)

    @property
    def cycle_number(self) -> int:
        """Get current cycle number."""
        return self.mission.get("current_cycle", 1)

    @property
    def cycle_budget(self) -> int:
        """Get cycle budget."""
        return self.mission.get("cycle_budget", 1)

    @property
    def history(self) -> List[Dict]:
        """Get mission history."""
        return self.mission.get("history", [])

    @property
    def cycle_history(self) -> List[Dict]:
        """Get cycle history."""
        return self.mission.get("cycle_history", [])

    # =========================================================================
    # State modification methods
    # =========================================================================

    def log_history(self, entry: str, details: Optional[Dict] = None) -> None:
        """
        Add an entry to mission history.

        Args:
            entry: The history entry text
            details: Optional additional details
        """
        history_entry = {
            "timestamp": datetime.now().isoformat(),
            "stage": self.current_stage,
            "event": entry,
        }
        if details:
            history_entry["details"] = details

        if "history" not in self._mission:
            self._mission["history"] = []
        self._mission["history"].append(history_entry)

        self._dirty = True
        if self.auto_save:
            self.save_mission()

    def increment_iteration(self) -> int:
        """
        Increment the iteration counter.

        Returns:
            The new iteration number
        """
        current = self._mission.get("iteration", 0)
        self._mission["iteration"] = current + 1
        self._dirty = True
        if self.auto_save:
            self.save_mission()
        return self._mission["iteration"]

    def advance_cycle(self, continuation_prompt: str) -> int:
        """
        Advance to the next cycle.

        Args:
            continuation_prompt: The prompt for the next cycle

        Returns:
            The new cycle number
        """
        current_cycle = self._mission.get("current_cycle", 1)

        # Store cycle summary
        cycle_summary = {
            "cycle": current_cycle,
            "completed_at": datetime.now().isoformat(),
            "iteration_count": self._mission.get("iteration", 0),
            "continuation_prompt": continuation_prompt,
        }

        if "cycle_history" not in self._mission:
            self._mission["cycle_history"] = []
        self._mission["cycle_history"].append(cycle_summary)

        # Advance to next cycle
        self._mission["current_cycle"] = current_cycle + 1
        self._mission["iteration"] = 0  # Reset iteration for new cycle

        self._dirty = True
        if self.auto_save:
            self.save_mission()

        logger.info(f"Advanced to cycle {self._mission['current_cycle']}")
        return self._mission["current_cycle"]

    def update_stage(self, new_stage: str) -> str:
        """
        Update the current stage.

        Args:
            new_stage: The new stage name

        Returns:
            The old stage name
        """
        old_stage = self._mission.get("current_stage", "UNKNOWN")
        self._mission["current_stage"] = new_stage
        self._mission["last_updated"] = datetime.now().isoformat()

        self.log_history(f"Stage transition: {old_stage} -> {new_stage}")

        self._dirty = True
        if self.auto_save:
            self.save_mission()

        return old_stage

    def set_field(self, key: str, value: Any) -> None:
        """
        Set a field in the mission.

        Args:
            key: Field name
            value: Field value
        """
        self._mission[key] = value
        self._dirty = True
        if self.auto_save:
            self.save_mission()

    def get_field(self, key: str, default: Any = None) -> Any:
        """
        Get a field from the mission.

        Args:
            key: Field name
            default: Default value if not found

        Returns:
            The field value or default
        """
        return self.mission.get(key, default)

    # =========================================================================
    # Workspace path helpers
    # =========================================================================

    def get_workspace_dir(self) -> Path:
        """Get the mission workspace directory."""
        workspace = self.mission.get("mission_workspace")
        if workspace:
            return Path(workspace)
        return Path.cwd() / "workspace"

    def get_artifacts_dir(self) -> Path:
        """Get the artifacts directory."""
        return self.get_workspace_dir() / "artifacts"

    def get_research_dir(self) -> Path:
        """Get the research directory."""
        return self.get_workspace_dir() / "research"

    def get_tests_dir(self) -> Path:
        """Get the tests directory."""
        return self.get_workspace_dir() / "tests"
