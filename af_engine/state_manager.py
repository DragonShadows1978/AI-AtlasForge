"""
af_engine.state_manager - Mission State Persistence

This module provides the StateManager class for loading, saving, and
managing mission state.
"""

import fcntl
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, List

from .mission_config import DEFAULT_CYCLE_BUDGET, DEFAULT_MAX_ITERATIONS

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
        self._mission: Optional[Dict[str, Any]] = None
        self._dirty: bool = False

    @property
    def mission(self) -> Dict[str, Any]:
        """Get the current mission state."""
        if self._mission is None:
            self.load_mission()
        return self._mission

    @mission.setter
    def mission(self, value: Dict[str, Any]) -> None:
        """Set the mission state.
        C3-7: This setter is correct — it does NOT call load_mission(). It only assigns
        the value, marks dirty, and optionally saves. The 'corruption' bug described in
        the mission prompt does not exist in current code. No functional change needed.
        """
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
                loaded = io_utils.atomic_read_json(self.mission_path, default_mission)
            except ImportError:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        loaded = json.load(f)
                else:
                    loaded = default_mission

            # Validate loaded value is a dict before merging
            if not isinstance(loaded, dict):
                logger.warning(
                    f"Mission file {self.mission_path} contains non-dict JSON "
                    f"({type(loaded).__name__}), using defaults"
                )
                loaded = {}

            # Merge with defaults to ensure required fields exist
            self._mission = {**default_mission, **loaded}

            self._dirty = False
            logger.debug(f"Loaded mission from {self.mission_path}")

        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.error(f"Failed to load mission: {e}")
            self._mission = default_mission

        return self._mission

    # Fields that can be live-edited via PATCH (e.g. /api/mission/parameters)
    # and must be preserved across save_mission() calls.
    LIVE_EDIT_FIELDS = ("cycle_budget", "max_iterations")

    def save_mission(self) -> None:
        """Save mission to disk.

        Before writing, re-reads live-editable fields from disk so that
        external PATCH updates (via the dashboard) are not clobbered by
        stale in-memory values.

        Uses an exclusive file lock to make the read-modify-write atomic
        with respect to concurrent callers (M21).
        """
        lock_path = self.mission_path.with_suffix('.lock')
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, 'w') as _lock_file:
            fcntl.flock(_lock_file, fcntl.LOCK_EX)
            try:
                self._save_mission_locked()
            finally:
                fcntl.flock(_lock_file, fcntl.LOCK_UN)

    def _save_mission_locked(self) -> None:
        """Inner save implementation — must be called with the lock held."""
        if self._mission is None:
            raise RuntimeError("save_mission() called before load_mission() — no mission state to save")
        # Validate mission_path has not been tampered with via path traversal.
        # Anchored to module root (Path(__file__).parent.parent) so the guard
        # cannot be bypassed by placing mission_path in a shallow directory
        # (e.g. /tmp) whose parent would be / and therefore contain everything.
        _resolved_mp = self.mission_path.resolve()
        _module_root = Path(__file__).resolve().parent.parent
        if not _resolved_mp.is_relative_to(_module_root):
            raise ValueError(
                f"mission_path '{self.mission_path}' resolves to '{_resolved_mp}' "
                f"which is outside the module root '{_module_root}'. "
                "Possible path traversal attempt."
            )
        try:
            # Merge live-editable fields from disk before writing, so that
            # externally PATCHed values survive the save round-trip.
            try:
                import io_utils
                live = io_utils.atomic_read_json(self.mission_path, {})
            except ImportError:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        live = json.load(f)
                else:
                    live = {}

            for field in self.LIVE_EDIT_FIELDS:
                if field in live:
                    self._mission[field] = live[field]

            # Preserve correctly-scoped mission_workspace.
            # If the on-disk mission_workspace already ends with the mission_id
            # subdirectory, keep that scoped path rather than overwriting it with
            # a stale flat path that may be held in self._mission from mission start.
            mission_id = self._mission.get("mission_id", "")
            if mission_id:
                # Reject mission_id values that contain path separators or are
                # dot-only names — these could escape the workspace via traversal.
                if "/" in mission_id or "\\" in mission_id or mission_id in (".", ".."):
                    raise ValueError(
                        f"mission_id '{mission_id}' contains path separators or is a "
                        "reserved name. Possible path injection attempt."
                    )
                on_disk_workspace = live.get("mission_workspace", "")
                in_memory_workspace = self._mission.get("mission_workspace", "")
                if on_disk_workspace and Path(on_disk_workspace).name == mission_id:
                    # On-disk path is correctly scoped — preserve it
                    self._mission["mission_workspace"] = on_disk_workspace
                elif in_memory_workspace and Path(in_memory_workspace).name != mission_id:
                    # In-memory path is flat (not scoped) — derive scoped path
                    scoped = str(in_memory_workspace).rstrip("/") + "/" + mission_id
                    if Path(scoped).exists():
                        self._mission["mission_workspace"] = scoped

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
                    f.flush()
                    os.fsync(f.fileno())

            self._dirty = False
            logger.debug(f"Saved mission to {self.mission_path}")

        except Exception as e:
            logger.error(f"Failed to save mission: {e}")
            raise

    def sync_live_params(self) -> None:
        """Sync live-editable params from disk into _mission dict.

        Call at the top of the conductor loop to pick up PATCH changes
        to cycle_budget, max_iterations, etc.
        """
        _ = self.cycle_budget       # triggers fresh disk read + sync
        _ = self.max_iterations     # triggers fresh disk read + sync

    def _get_default_mission(self) -> Dict[str, Any]:
        """Get default mission structure. Uses MissionConfig canonical defaults."""
        return {
            "mission_id": "default",
            "problem_statement": "No mission defined. Please set a mission.",
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": DEFAULT_MAX_ITERATIONS,
            "preferences": {},
            "success_criteria": [],
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            # Cycle iteration fields — canonical defaults from mission_config
            "cycle_budget": DEFAULT_CYCLE_BUDGET,
            "current_cycle": 1,
            "cycle_history": [],
            "original_problem_statement": None,
        }

    def get_mission_data(self) -> Dict[str, Any]:
        """Return a shallow copy of the mission dict.

        Use this instead of accessing the `mission` property directly when
        you do not want callers to mutate internal state.
        """
        return dict(self.mission)

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
        if not isinstance(value, str):
            raise TypeError(f"current_stage must be a str, got {type(value).__name__}: {value!r}")
        _ = self.mission  # trigger lazy-load if _mission is None
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
    def cycle_budget(self) -> Optional[int]:
        """Get cycle budget.

        Returns None when the JSON value is null, which signals unlimited cycles.
        The advance_cycle() guard ``if budget is not None`` treats None as unlimited.

        Reads fresh from disk so that live-edit PATCH updates to mission.json
        (via /api/mission/parameters) are picked up by the cycle engine without
        requiring a full engine reload or restart.
        """
        _ = self.mission  # trigger lazy-load if _mission is None
        try:
            try:
                import io_utils
                live = io_utils.atomic_read_json(self.mission_path, {})
            except ImportError:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        live = json.load(f)
                else:
                    live = {}
            if "cycle_budget" in live:
                # Sync in-memory dict so subsequent save_mission() calls don't
                # overwrite the live-edited value back to the stale cached one.
                self._mission["cycle_budget"] = live["cycle_budget"]
                cb = live["cycle_budget"]
                return int(cb) if cb is not None else None
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"cycle_budget: failed to read fresh value from disk: {e}")
        cb = self._mission.get("cycle_budget", DEFAULT_CYCLE_BUDGET)
        return int(cb) if cb is not None else None

    @property
    def max_iterations(self) -> int:
        """Get max iterations.

        Reads fresh from disk so that live-edit PATCH updates to mission.json
        (via /api/mission/parameters) are reflected in the next stage context
        without requiring a full engine reload or restart.
        """
        _ = self.mission  # trigger lazy-load if _mission is None
        try:
            try:
                import io_utils
                live = io_utils.atomic_read_json(self.mission_path, {})
            except ImportError:
                if self.mission_path.exists():
                    with open(self.mission_path, 'r') as f:
                        live = json.load(f)
                else:
                    live = {}
            if "max_iterations" in live:
                self._mission["max_iterations"] = live["max_iterations"]
                mi = live["max_iterations"]
                return int(mi) if mi is not None else DEFAULT_MAX_ITERATIONS
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(f"max_iterations: failed to read fresh value from disk: {e}")
        mi = self._mission.get("max_iterations", DEFAULT_MAX_ITERATIONS)
        return int(mi) if mi is not None else DEFAULT_MAX_ITERATIONS

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

        _ = self.mission  # trigger lazy-load if _mission is None
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
        # Use self.mission to ensure lazy loading happens first
        current = self.mission.get("iteration", 0)
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
        _ = self.mission  # trigger lazy-load if _mission is None
        budget = self.cycle_budget
        current_cycle = self._mission.get("current_cycle", 1)

        # Always record cycle summary first (including the final cycle)
        cycle_summary = {
            "cycle": current_cycle,
            "completed_at": datetime.now().isoformat(),
            "iteration_count": self._mission.get("iteration", 0),
            "continuation_prompt": continuation_prompt,
        }

        if "cycle_history" not in self._mission:
            self._mission["cycle_history"] = []
        self._mission["cycle_history"].append(cycle_summary)

        # Apply continuation prompt to problem_statement
        if continuation_prompt:
            self._mission["problem_statement"] = continuation_prompt
            logger.info(f"Updated problem_statement for cycle {current_cycle + 1}")

        # Guard: if already at budget, save the summary we just appended but don't advance
        if budget is not None and current_cycle >= budget:
            logger.warning(f"advance_cycle called but already at budget ({budget}), cycle={current_cycle}")
            self._dirty = True
            if self.auto_save:
                self.save_mission()
            return current_cycle

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
        _ = self.mission  # trigger lazy-load if _mission is None
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
        _ = self.mission  # trigger lazy-load if _mission is None
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

    @property
    def mission_dir(self) -> Path:
        """Get the mission directory path."""
        mission_dir = self.mission.get("mission_dir")
        if mission_dir:
            return Path(mission_dir)
        return Path.cwd()

    def get_workspace_dir(self) -> Path:
        """Get the mission workspace directory.

        The path is canonicalized via Path.resolve() and validated to remain
        within the expected base directory (parent of mission_path) to prevent
        path traversal attacks via a malicious mission JSON file.
        """
        workspace = self.mission.get("mission_workspace")
        if not workspace or not str(workspace).strip():
            raise KeyError(
                f"'mission_workspace' key is absent or empty in mission state "
                f"(mission_id={self.mission_id!r}). "
                "Ensure the mission JSON file contains a 'mission_workspace' field."
            )
        resolved = Path(workspace).resolve()
        # Validate the resolved path stays within the AtlasForge root.
        # The mission file lives at <root>/state/mission.json (or similar),
        # so the root is two levels up from mission_path.
        allowed_root = Path(__file__).resolve().parent.parent
        try:
            resolved.relative_to(allowed_root)
        except ValueError:
            raise ValueError(
                f"mission_workspace path '{workspace}' resolves to '{resolved}' "
                f"which is outside the allowed root '{allowed_root}'. "
                "Possible path traversal attempt in mission JSON."
            )
        return resolved

    def get_artifacts_dir(self) -> Path:
        """Get the artifacts directory."""
        return self.get_workspace_dir() / "artifacts"

    def get_research_dir(self) -> Path:
        """Get the research directory."""
        return self.get_workspace_dir() / "research"

    def get_tests_dir(self) -> Path:
        """Get the tests directory."""
        return self.get_workspace_dir() / "tests"

    def get_execution_trace(self) -> List[Dict]:
        """
        Derive execution trace from history (stage transitions).
        Returns list of {stage, event, timestamp} dicts.
        """
        trace = []
        for entry in self.history:
            event = entry.get("event", "")
            if "Stage transition:" in event:
                trace.append({
                    "stage": entry.get("stage", "UNKNOWN"),
                    "event": event,
                    "timestamp": entry.get("timestamp", ""),
                })
        return trace
