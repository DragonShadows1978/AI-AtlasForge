"""
af_engine.orchestrator - Core Stage Orchestrator

This module provides the StageOrchestrator class, the central coordinator
for the modular R&D Engine. It replaces the monolithic update_stage() method
with a clean, event-driven architecture.

The StageOrchestrator:
- Loads and manages stage handlers via StageRegistry
- Coordinates event dispatch via IntegrationManager
- Manages mission state via StateManager
- Handles multi-cycle iteration via CycleManager
- Generates prompts via PromptFactory
"""

import json
import logging
import uuid
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .state_manager import StateManager
from .stage_registry import StageRegistry
from .integration_manager import IntegrationManager
from .cycle_manager import CycleManager
from .prompt_factory import PromptFactory
from .stages.base import StageContext, StageResult
from .integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class StageOrchestrator:
    """
    Core orchestrator for the modular R&D Engine.

    The StageOrchestrator coordinates all components of the modular engine:
    - Stage handlers for each workflow stage
    - Integration handlers for cross-cutting concerns
    - State persistence for mission data
    - Cycle management for multi-cycle missions
    - Prompt generation with context injection

    This class provides the same public API as the legacy RDMissionController
    for backward compatibility.
    """

    # Valid stages
    STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]

    def __init__(
        self,
        mission_path: Optional[Path] = None,
        config_path: Optional[Path] = None,
        atlasforge_root: Optional[Path] = None,
    ):
        """
        Initialize the stage orchestrator.

        Args:
            mission_path: Path to mission.json (defaults to state/mission.json)
            config_path: Path to stage_definitions.yaml (optional)
            atlasforge_root: Path to AtlasForge root directory
        """
        # Determine paths
        self.root = atlasforge_root or Path(__file__).parent.parent
        self._mission_path = mission_path or self.root / "state" / "mission.json"
        self._config_path = config_path

        # Initialize components
        self.state = StateManager(self._mission_path)
        self.registry = StageRegistry(config_path)
        self.integrations = IntegrationManager()
        self.cycles = CycleManager(self.state)
        self.prompts = PromptFactory(self.root)

        # Flag to prevent log_history from saving during queue processing (backward compat)
        self._queue_processing = False

        # Load default integrations
        self._load_integrations()

        logger.info("StageOrchestrator initialized")

    def _load_integrations(self) -> None:
        """Load all default integration handlers."""
        try:
            self.integrations.load_default_integrations()
            stats = self.integrations.get_stats()
            logger.info(
                f"Loaded {stats['handlers_registered']} integrations "
                f"({stats['handlers_available']} available)"
            )
        except Exception as e:
            logger.warning(f"Failed to load some integrations: {e}")

    # =========================================================================
    # Backward-compatible properties (matching legacy RDMissionController)
    # =========================================================================

    @property
    def mission(self) -> Dict[str, Any]:
        """Get the current mission state."""
        return self.state.mission

    @mission.setter
    def mission(self, value: Dict[str, Any]) -> None:
        """Set the mission state (backward compatibility)."""
        self.state.mission = value

    @property
    def current_stage(self) -> str:
        """Get the current stage."""
        return self.state.current_stage

    @property
    def mission_id(self) -> str:
        """Get the mission ID."""
        return self.state.mission_id

    @property
    def mission_dir(self) -> Path:
        """Get the mission directory path (backward compatibility)."""
        return self.state.mission_dir

    # =========================================================================
    # Core workflow methods
    # =========================================================================

    def update_stage(self, new_stage: str) -> None:
        """
        Update the mission stage.

        This method:
        1. Emits STAGE_COMPLETED event for the old stage
        2. Updates the state
        3. Emits STAGE_STARTED event for the new stage

        Args:
            new_stage: The new stage to transition to
        """
        new_stage = new_stage.upper()
        if new_stage not in self.STAGES:
            logger.error(f"Invalid stage: {new_stage}")
            return

        old_stage = self.current_stage

        # Emit STAGE_COMPLETED for old stage
        if old_stage and old_stage != "COMPLETE":
            self.integrations.emit_stage_completed(
                stage=old_stage,
                mission_id=self.mission_id,
                data={
                    "old_stage": old_stage,
                    "new_stage": new_stage,
                    "iteration": self.state.iteration,
                }
            )

        # Update state
        old = self.state.update_stage(new_stage)
        logger.info(f"Stage transition: {old} -> {new_stage}")

        # Emit STAGE_STARTED for new stage
        if new_stage != "COMPLETE":
            self.integrations.emit_stage_started(
                stage=new_stage,
                mission_id=self.mission_id,
                data={
                    "old_stage": old_stage,
                    "new_stage": new_stage,
                    "iteration": self.state.iteration,
                }
            )

        # Handle special stage transitions
        if new_stage == "COMPLETE":
            self.integrations.emit_mission_completed(
                mission_id=self.mission_id,
                data={
                    "final_stage": old_stage,
                    "total_iterations": self.state.iteration,
                    "cycle_count": self.cycles.current_cycle,
                }
            )

            # Process mission queue - start next queued mission
            self._process_mission_queue()

    def build_rd_prompt(self, context: str = "") -> str:
        """
        Build the R&D prompt for the current stage.

        This method:
        1. Gets the handler for the current stage
        2. Builds the stage context
        3. Generates the stage-specific prompt
        4. Injects additional context (KB, AfterImage, Recovery)

        Args:
            context: Additional context to include

        Returns:
            Complete prompt string for Claude
        """
        stage = self.current_stage
        handler = self.registry.get_handler(stage)
        stage_context = self._build_stage_context()

        # Get stage-specific prompt
        stage_prompt = handler.get_prompt(stage_context)

        # Assemble with ground rules and headers
        full_prompt = self.prompts.assemble_prompt(
            stage_prompt=stage_prompt,
            context=stage_context,
            include_ground_rules=True,
            include_mission_header=True,
        )

        # Inject KB context for PLANNING stage
        if stage == "PLANNING":
            full_prompt = self.prompts.inject_kb_context(
                full_prompt,
                stage_context.problem_statement
            )

        # Inject AfterImage context for BUILDING stage
        if stage == "BUILDING":
            full_prompt = self.prompts.inject_afterimage_context(
                full_prompt,
                stage_context.problem_statement
            )

        # Inject recovery context if available
        recovery_info = self._get_recovery_info()
        if recovery_info:
            full_prompt = self.prompts.inject_recovery_context(
                full_prompt,
                recovery_info
            )

        # Append any additional context
        if context:
            full_prompt = f"{full_prompt}\n\n{context}"

        return full_prompt

    def process_response(self, response: Dict[str, Any]) -> str:
        """
        Process Claude's response and determine next stage.

        This method:
        1. Gets the handler for the current stage
        2. Processes the response through the handler
        3. Emits events from the result
        4. Handles cycle advancement for CYCLE_END -> PLANNING transitions
        5. Returns the next stage

        Args:
            response: Claude's response dictionary

        Returns:
            The next stage to transition to
        """
        # Guard against None response
        if response is None:
            response = {}

        stage = self.current_stage
        handler = self.registry.get_handler(stage)
        stage_context = self._build_stage_context()

        # Process response through handler
        result: StageResult = handler.process_response(response, stage_context)

        # Emit events from result
        for event in result.events_to_emit:
            self.integrations.emit(event)

        # Log result
        logger.info(
            f"Stage {stage} response: status={result.status}, "
            f"next_stage={result.next_stage}, success={result.success}"
        )

        if result.message:
            logger.info(f"Handler message: {result.message}")

        # Check if stage handler requests iteration increment
        # Only increment on ANALYZING -> BUILDING or ANALYZING -> PLANNING transitions
        # (i.e., when needs_revision or needs_replanning)
        if result.output_data.get("_increment_iteration"):
            self.state.increment_iteration()
            logger.info(f"Iteration incremented to {self.state.iteration}")

        # Handle cycle advancement for CYCLE_END -> PLANNING transitions
        # This is the key fix: when transitioning from CYCLE_END to PLANNING,
        # we need to advance the cycle counter before returning the next stage
        if stage == "CYCLE_END" and result.next_stage == "PLANNING":
            if self.should_continue_cycle():
                continuation_prompt = result.output_data.get("continuation_prompt", "")
                if not continuation_prompt:
                    # Generate default continuation if Claude didn't provide one
                    continuation_prompt = self._generate_default_continuation()
                    logger.warning(f"No continuation_prompt provided, using default for cycle {self.cycles.current_cycle + 1}")

                logger.info(f"Advancing cycle from {self.cycles.current_cycle} to next cycle")
                self.advance_to_next_cycle(continuation_prompt)
                # Note: advance_to_next_cycle already calls update_stage("PLANNING")
                # so we return the current stage to prevent double-transition
                return self.current_stage

        return result.next_stage

    def _build_stage_context(self) -> StageContext:
        """Build StageContext from current state."""
        return self.prompts.build_context(self.state)

    def _get_recovery_info(self) -> Optional[Dict]:
        """Get crash recovery information if available."""
        recovery_handler = self.integrations.get_handler("recovery")
        if recovery_handler and hasattr(recovery_handler, 'get_recovery_info'):
            return recovery_handler.get_recovery_info()
        return None

    # =========================================================================
    # Cycle management methods
    # =========================================================================

    def should_continue_cycle(self) -> bool:
        """Check if another cycle should be started."""
        return self.cycles.should_continue()

    def advance_to_next_cycle(self, continuation_prompt: str) -> Dict[str, Any]:
        """
        Advance to the next cycle.

        Args:
            continuation_prompt: Prompt for the next cycle

        Returns:
            Cycle advancement details
        """
        # Emit cycle completed event
        cycle_event = self.cycles.create_cycle_completed_event(
            summary=continuation_prompt[:200],
            next_stage="PLANNING"
        )
        self.integrations.emit(cycle_event)

        # Advance cycle
        result = self.cycles.advance_cycle(continuation_prompt)

        # Reset to PLANNING for new cycle
        self.update_stage("PLANNING")

        # Emit cycle started event
        start_event = self.cycles.create_cycle_started_event()
        self.integrations.emit(start_event)

        return result

    def get_cycle_status(self) -> Dict[str, Any]:
        """Get current cycle status."""
        return self.cycles.get_cycle_context()

    def _generate_default_continuation(self) -> str:
        """Generate a default continuation prompt when Claude doesn't provide one.

        This ensures multi-cycle missions continue even if the CYCLE_END response
        doesn't include a continuation_prompt field.
        """
        original_mission = self.state.get_field("original_problem_statement") or self.state.get_field("problem_statement", "Continue the mission")
        current_cycle = self.cycles.current_cycle
        cycle_budget = self.cycles.cycle_budget

        return f"""=== CONTINUATION: Cycle {current_cycle + 1} of {cycle_budget} ===

ORIGINAL MISSION:
{original_mission}

PREVIOUS CYCLE NOTE:
The previous cycle completed but did not provide a specific continuation prompt.

OBJECTIVES FOR THIS CYCLE:
- Continue work from the previous cycle
- Address any remaining tasks from the original mission
- Build upon completed work

Continue the mission from where the previous cycle left off.
"""

    # =========================================================================
    # Stage restriction methods
    # =========================================================================

    def get_stage_restrictions(self, stage: Optional[str] = None) -> Dict[str, Any]:
        """
        Get restrictions for a stage.

        Args:
            stage: Stage name (defaults to current stage)

        Returns:
            Dictionary of restrictions
        """
        stage = stage or self.current_stage
        restrictions = self.registry.get_restrictions(stage)

        return {
            "allowed_tools": restrictions.allowed_tools,
            "blocked_tools": restrictions.blocked_tools,
            "allowed_write_paths": restrictions.allowed_write_paths,
            "forbidden_write_paths": restrictions.forbidden_write_paths,
            "allow_bash": restrictions.allow_bash,
            "read_only": restrictions.read_only,
        }

    def is_tool_allowed(self, tool_name: str, stage: Optional[str] = None) -> bool:
        """
        Check if a tool is allowed in a stage.

        Args:
            tool_name: Name of the tool
            stage: Stage name (defaults to current stage)

        Returns:
            True if allowed, False otherwise
        """
        restrictions = self.get_stage_restrictions(stage)

        # Check blocked tools first
        if tool_name in restrictions["blocked_tools"]:
            return False

        # If allowed_tools is non-empty, check if tool is in list
        if restrictions["allowed_tools"]:
            return tool_name in restrictions["allowed_tools"]

        # If no restrictions, allow
        return True

    # =========================================================================
    # Utility methods
    # =========================================================================

    def log_history(self, entry: str, details: Optional[Dict] = None) -> None:
        """Log an entry to mission history."""
        self.state.log_history(entry, details)

    def increment_iteration(self) -> int:
        """Increment the iteration counter (backward compatibility)."""
        return self.state.increment_iteration()

    def get_recent_history(self, n: int = 10) -> list:
        """Get recent history entries (backward compatibility)."""
        return self.state.history[-n:]

    def reload_mission(self) -> None:
        """Reload mission from disk."""
        self.state.load_mission()

    def load_mission(self) -> Dict[str, Any]:
        """Load and return mission from disk (backward compatibility)."""
        self.state.load_mission()
        return self.state.mission

    def load_mission_from_file(self, filepath: Path) -> bool:
        """Load a mission from a template file (backward compatibility).

        Args:
            filepath: Path to the mission template JSON file

        Returns:
            True if successfully loaded, False otherwise
        """
        from datetime import datetime
        try:
            import io_utils
        except ImportError:
            import json
            io_utils = None

        if io_utils:
            template = io_utils.atomic_read_json(filepath, {})
        else:
            if not filepath.exists():
                return False
            with open(filepath, 'r') as f:
                template = json.load(f)

        if template and template.get("problem_statement"):
            # Reset to PLANNING stage
            template["current_stage"] = "PLANNING"
            template["iteration"] = 0
            template["history"] = []
            template["created_at"] = datetime.now().isoformat()
            self.state.mission = template
            self.save_mission()
            logger.info(f"Loaded mission from {filepath}")
            return True
        return False

    def save_mission(self) -> None:
        """Save mission to disk."""
        self.state.save_mission()

    def get_status(self) -> Dict[str, Any]:
        """Get orchestrator status."""
        return {
            "mission_id": self.mission_id,
            "current_stage": self.current_stage,
            "iteration": self.state.iteration,
            "cycle": self.cycles.current_cycle,
            "cycle_budget": self.cycles.cycle_budget,
            "cycles_remaining": self.cycles.cycles_remaining,
            "integrations": self.integrations.get_stats(),
        }

    # =========================================================================
    # Mission setup methods (backward compatibility with legacy controller)
    # =========================================================================

    def set_mission(
        self,
        problem_statement: str,
        preferences: dict = None,
        success_criteria: list = None,
        mission_id: str = None,
        cycle_budget: int = 1,
        project_name: str = None
    ) -> None:
        """Set a new mission with optional cycle budget for multi-cycle execution.

        If PROJECT_NAME_RESOLVER_AVAILABLE, workspace is created under workspace/<project_name>/
        to enable workspace sharing across missions working on the same project.
        Otherwise falls back to missions/mission_<UUID>/workspace/ for backwards compatibility.
        """
        import uuid
        import json

        # Import paths from atlasforge_config
        try:
            from atlasforge_config import MISSIONS_DIR, WORKSPACE_DIR
        except ImportError:
            MISSIONS_DIR = self.root / "missions"
            WORKSPACE_DIR = self.root / "workspace"

        # Try to import project name resolver
        resolve_project_name = None
        try:
            from project_name_resolver import resolve_project_name
        except ImportError:
            pass

        # Generate mission ID
        mid = mission_id or f"mission_{uuid.uuid4().hex[:8]}"

        # Resolve project name for shared workspace
        resolved_project_name = None
        if resolve_project_name is not None:
            resolved_project_name = resolve_project_name(problem_statement, mid, project_name)
            # Use shared workspace under workspace/<project_name>/
            mission_workspace = WORKSPACE_DIR / resolved_project_name
            logger.info(f"Resolved project name: {resolved_project_name}")
        else:
            # Legacy: per-mission workspace
            mission_workspace = MISSIONS_DIR / mid / "workspace"

        # Create mission directory (for config, analytics, drift validation)
        mission_dir = MISSIONS_DIR / mid
        mission_dir.mkdir(parents=True, exist_ok=True)

        # Create workspace directories (may already exist if shared project)
        (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

        logger.info(f"Mission workspace at {mission_workspace}")

        self.state.mission = {
            "mission_id": mid,
            "problem_statement": problem_statement,
            "original_problem_statement": problem_statement,  # Keep root mission
            "preferences": preferences or {},
            "success_criteria": success_criteria or [],
            "current_stage": "PLANNING",
            "iteration": 0,
            "max_iterations": 10,
            "artifacts": {"plan": None, "code": [], "tests": []},
            "history": [],
            "created_at": datetime.now().isoformat(),
            "cycle_started_at": datetime.now().isoformat(),
            # Cycle iteration fields
            "cycle_budget": max(1, cycle_budget),  # Minimum 1 cycle
            "current_cycle": 1,
            "cycle_history": [],
            # Mission workspace path
            "mission_workspace": str(mission_workspace),
            "mission_dir": str(mission_dir),
            # Project name for workspace deduplication
            "project_name": resolved_project_name,
            "metadata": {}
        }
        self.save_mission()

        # Also save a copy of the mission config in the mission directory
        mission_config_path = mission_dir / "mission_config.json"
        config_data = {
            "mission_id": mid,
            "problem_statement": problem_statement,
            "cycle_budget": max(1, cycle_budget),
            "created_at": self.mission["created_at"]
        }
        if resolved_project_name:
            config_data["project_name"] = resolved_project_name
            config_data["project_workspace"] = str(mission_workspace)
        with open(mission_config_path, 'w') as f:
            json.dump(config_data, f, indent=2)

        logger.info(f"New mission set with {cycle_budget} cycles: {problem_statement[:100]}...")

        # AtlasForge Enhancement: Set baseline fingerprint for mission continuity tracking
        try:
            from atlasforge_enhancements import AtlasForgeEnhancer
            enhancer = AtlasForgeEnhancer()
            enhancer.set_mission_baseline(problem_statement, source="initial_mission")
            logger.info("AtlasForge baseline fingerprint set for mission continuity tracking")
        except Exception as e:
            logger.debug(f"AtlasForge enhancement not available: {e}")

        # Analytics: Track mission start
        try:
            from mission_analytics import get_analytics
            analytics = get_analytics()
            analytics.start_mission(mid, problem_statement)
            # Also track the initial PLANNING stage start
            analytics.start_stage(mid, "PLANNING", iteration=0, cycle=1)
            logger.info(f"Analytics: Started tracking mission {mid}")
        except Exception as e:
            logger.debug(f"Analytics not available: {e}")

        # Real-time token watcher: Start watching for the new mission
        try:
            from realtime_token_watcher import start_watching_mission
            workspace = self.mission.get('mission_workspace')
            success = start_watching_mission(mid, workspace, stage="PLANNING")
            if success:
                logger.info(f"Token watcher: Started real-time monitoring for {mid}")
            else:
                logger.debug(f"Token watcher: Could not start (no transcript dir yet)")
        except Exception as e:
            logger.debug(f"Token watcher not available: {e}")

        # Emit mission started event
        self.integrations.emit_mission_started(
            mission_id=mid,
            data={
                "problem_statement": problem_statement[:200],
                "cycle_budget": cycle_budget,
            }
        )

    def reset_mission(self) -> None:
        """Reset mission to initial state (keeps problem statement)."""
        problem = self.mission.get("problem_statement", "No mission defined.")
        prefs = self.mission.get("preferences", {})

        self.state.mission = {
            "problem_statement": problem,
            "preferences": prefs,
            "current_stage": "PLANNING",
            "iteration": 0,
            "history": [],
            "created_at": datetime.now().isoformat(),
            "reset_at": datetime.now().isoformat()
        }
        self.save_mission()
        logger.info("Mission reset to PLANNING")

    # =========================================================================
    # Mission Queue Processing (ported from legacy af_engine)
    # =========================================================================

    def _process_mission_queue(self) -> None:
        """
        Check if there are queued missions and start the next one.

        This is called after a mission completes (reaches COMPLETE stage).
        Uses the extended queue scheduler if available, which handles:
        - Priority-based ordering
        - Scheduled start times
        - Mission dependencies

        Falls back to simple FIFO queue if scheduler not available.

        IMPORTANT: Queue item is only removed AFTER successful mission creation
        to prevent mission loss if creation fails.

        Uses file-based locking to prevent race conditions with
        dashboard_v2.queue_auto_start_watcher().
        """
        # Import paths from atlasforge_config
        try:
            from atlasforge_config import STATE_DIR, MISSIONS_DIR, WORKSPACE_DIR
        except ImportError:
            STATE_DIR = self.root / "state"
            MISSIONS_DIR = self.root / "missions"
            WORKSPACE_DIR = self.root / "workspace"

        # Import io_utils for atomic file operations
        try:
            import io_utils
        except ImportError:
            logger.warning("io_utils not available, queue processing disabled")
            return

        # Try to import queue scheduler
        QUEUE_SCHEDULER_AVAILABLE = False
        get_queue_scheduler = None
        try:
            from mission_queue_scheduler import get_scheduler as get_queue_scheduler
            QUEUE_SCHEDULER_AVAILABLE = True
        except ImportError:
            pass

        # Try to import queue notifications
        QUEUE_NOTIFICATIONS_AVAILABLE = False
        notify_queue_empty = None
        notify_mission_completed = None
        try:
            from queue_notifications import (
                notify_queue_empty,
                notify_mission_completed
            )
            QUEUE_NOTIFICATIONS_AVAILABLE = True
        except ImportError:
            pass

        # Acquire queue processing lock to prevent race conditions
        release_queue_lock = None
        try:
            from queue_processing_lock import acquire_queue_lock, release_queue_lock
            if not acquire_queue_lock(source="af_engine_modular", timeout=2, blocking=False):
                logger.info("Queue processing locked by another process, skipping")
                return
        except ImportError:
            logger.warning("queue_processing_lock module not available, proceeding without lock")

        queue_path = STATE_DIR / "mission_queue.json"

        # Set flag to prevent log_history from saving during queue processing
        self._queue_processing = True

        try:
            # Try using the extended scheduler if available
            if QUEUE_SCHEDULER_AVAILABLE and get_queue_scheduler:
                scheduler = get_queue_scheduler()
                next_item_obj = scheduler.get_next_ready_item()

                if next_item_obj is None:
                    # Check if queue is empty vs just waiting
                    state = scheduler.get_queue()
                    if not state.queue:
                        logger.debug("Queue empty - no next mission")
                        # Send notification that queue is empty
                        if QUEUE_NOTIFICATIONS_AVAILABLE and notify_queue_empty:
                            notify_queue_empty(self.mission.get("mission_id"))
                        return
                    else:
                        logger.debug("No ready items - all waiting on schedule/dependencies")
                        return

                # DON'T remove the item yet - wait for successful mission creation
                next_item = next_item_obj.to_dict()
                next_item_id = next_item_obj.id
                queue = scheduler.get_queue().queue
            else:
                # Fallback to simple queue processing
                queue_data = io_utils.atomic_read_json(queue_path, {"queue": [], "enabled": True})

                if not queue_data.get("enabled", True):
                    logger.debug("Queue processing disabled - skipping")
                    return

                queue = queue_data.get("queue", [])
                if not queue:
                    logger.debug("Queue empty - no next mission")
                    return

                # DON'T pop yet - just peek at the first item
                next_item = queue[0]
                next_item_id = next_item.get("id")

            logger.info(f"Processing queued mission: {next_item.get('mission_title', 'Untitled')}")

            # Send completion notification for previous mission
            if QUEUE_NOTIFICATIONS_AVAILABLE and notify_mission_completed:
                prev_mission_id = self.mission.get("mission_id")
                # Use 'or ""' to handle None values (get() returns None if key exists with None value)
                prev_mission_title = (self.mission.get("original_problem_statement") or "")[:50]
                cycles_used = self.mission.get("current_cycle", 1)
                notify_mission_completed(
                    prev_mission_id,
                    prev_mission_title,
                    cycles_used,
                    len(queue)
                )

            # Create the new mission - returns True on success
            success = self._create_mission_from_queue_item(next_item)

            # Only remove from queue AFTER successful mission creation
            if success:
                if QUEUE_SCHEDULER_AVAILABLE and get_queue_scheduler:
                    scheduler = get_queue_scheduler()
                    scheduler.remove_item(next_item_id)
                    logger.info(f"Removed item {next_item_id} from queue after successful mission creation")
                else:
                    # Fallback: remove from simple queue
                    queue_data = io_utils.atomic_read_json(queue_path, {"queue": [], "enabled": True})
                    queue = queue_data.get("queue", [])
                    # Remove the first item (the one we processed)
                    if queue and queue[0].get("id") == next_item_id:
                        queue.pop(0)
                    else:
                        # Fallback: remove by matching ID
                        queue = [q for q in queue if q.get("id") != next_item_id]
                    queue_data["queue"] = queue
                    queue_data["last_processed_at"] = datetime.now().isoformat()
                    io_utils.atomic_write_json(queue_path, queue_data)
                    logger.info(f"Removed item {next_item_id} from queue after successful mission creation")

                # Emit queue update event
                try:
                    from websocket_events import emit_queue_updated
                    if QUEUE_SCHEDULER_AVAILABLE and get_queue_scheduler:
                        updated_queue = get_queue_scheduler().get_queue()
                        emit_queue_updated({
                            "missions": updated_queue.queue,
                            "settings": {
                                "enabled": updated_queue.enabled,
                                "paused": updated_queue.paused,
                                "auto_estimate_time": updated_queue.auto_estimate_time,
                                "default_priority": updated_queue.default_priority
                            }
                        }, 'mission_started')
                    else:
                        emit_queue_updated(queue_data, 'mission_started')
                except Exception as e:
                    logger.warning(f"Failed to emit queue update: {e}")
            else:
                logger.error(f"Mission creation failed - keeping item {next_item_id} in queue")

        except Exception as e:
            logger.error(f"Queue processing failed: {e}")
        finally:
            # Reset queue processing flag
            self._queue_processing = False
            # Release queue processing lock
            if release_queue_lock:
                try:
                    release_queue_lock()
                except Exception:
                    pass

    def _create_mission_from_queue_item(self, queue_item: dict) -> bool:
        """
        Create a new mission from a queue item and signal for auto-start.

        Args:
            queue_item: Dict with mission_title, mission_description, cycle_budget, project_name, etc.

        Returns:
            bool: True if mission was created successfully, False otherwise
        """
        # Import paths from atlasforge_config
        try:
            from atlasforge_config import STATE_DIR, MISSIONS_DIR, WORKSPACE_DIR, MISSION_PATH
        except ImportError:
            STATE_DIR = self.root / "state"
            MISSIONS_DIR = self.root / "missions"
            WORKSPACE_DIR = self.root / "workspace"
            MISSION_PATH = STATE_DIR / "mission.json"

        # Import io_utils for atomic file operations
        try:
            import io_utils
        except ImportError:
            logger.error("io_utils not available, cannot create mission")
            return False

        # Try to import project name resolver
        PROJECT_NAME_RESOLVER_AVAILABLE = False
        resolve_project_name = None
        try:
            from project_name_resolver import resolve_project_name
            PROJECT_NAME_RESOLVER_AVAILABLE = True
        except ImportError:
            pass

        # Try to import analytics
        ANALYTICS_AVAILABLE = False
        get_analytics = None
        try:
            from mission_analytics import get_analytics
            ANALYTICS_AVAILABLE = True
        except ImportError:
            pass

        try:
            # Generate mission ID
            mission_id = f"mission_{uuid.uuid4().hex[:8]}"

            # Get mission details from queue item
            # Handle both dashboard format (problem_statement) and core format (mission_description)
            problem_statement = (
                queue_item.get("mission_description") or
                queue_item.get("problem_statement") or
                queue_item.get("mission_title", "")
            )
            cycle_budget = queue_item.get("cycle_budget") or 3  # Handle None values
            user_project_name = queue_item.get("project_name")

            # Resolve project name for shared workspace
            resolved_project_name = None
            if PROJECT_NAME_RESOLVER_AVAILABLE and resolve_project_name:
                resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
                # Use shared workspace under workspace/<project_name>/
                mission_workspace = WORKSPACE_DIR / resolved_project_name
                logger.info(f"Queue mission resolved project name: {resolved_project_name}")
            else:
                # Legacy: per-mission workspace
                mission_workspace = MISSIONS_DIR / mission_id / "workspace"

            # Create mission directory (for config, analytics, drift validation)
            mission_dir = MISSIONS_DIR / mission_id
            mission_dir.mkdir(parents=True, exist_ok=True)

            # Create workspace directories (may already exist if shared project)
            (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

            # Create the mission
            new_mission = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "original_problem_statement": problem_statement,
                "preferences": {},
                "success_criteria": [],
                "current_stage": "PLANNING",
                "iteration": 0,
                "max_iterations": 10,
                "artifacts": {"plan": None, "code": [], "tests": []},
                "history": [],
                "created_at": datetime.now().isoformat(),
                "last_updated": datetime.now().isoformat(),
                "cycle_started_at": datetime.now().isoformat(),
                "cycle_budget": max(1, cycle_budget),
                "current_cycle": 1,
                "cycle_history": [],
                "mission_workspace": str(mission_workspace),
                "mission_dir": str(mission_dir),
                "project_name": resolved_project_name,
                "source_queue_item_id": queue_item.get("id"),
                "source_recommendation_id": queue_item.get("recommendation_id"),
                "metadata": {"queued": True, "queued_at": queue_item.get("queued_at")}
            }

            # Save mission state with return value check
            success = io_utils.atomic_write_json(MISSION_PATH, new_mission)
            if not success:
                logger.error(f"Failed to write new mission {mission_id} to disk")
                return False

            # Save mission config
            mission_config_path = mission_dir / "mission_config.json"
            config_data = {
                "mission_id": mission_id,
                "problem_statement": problem_statement,
                "cycle_budget": max(1, cycle_budget),
                "created_at": new_mission["created_at"],
                "source_queue_item_id": queue_item.get("id")
            }
            if resolved_project_name:
                config_data["project_name"] = resolved_project_name
                config_data["project_workspace"] = str(mission_workspace)
            with open(mission_config_path, 'w') as f:
                json.dump(config_data, f, indent=2)

            # Register with analytics if available
            if ANALYTICS_AVAILABLE and get_analytics:
                try:
                    analytics = get_analytics()
                    analytics.start_mission(mission_id, problem_statement)
                except Exception as e:
                    logger.warning(f"Analytics: Failed to register queued mission: {e}")

            # Signal for auto-start via file-based IPC
            # The dashboard/watcher will detect this and start R&D mode
            auto_start_signal = {
                "action": "start_rd",
                "mission_id": mission_id,
                "mission_title": queue_item.get("mission_title", "Queued Mission"),
                "signaled_at": datetime.now().isoformat(),
                "source": "queue"
            }
            signal_path = STATE_DIR / "queue_auto_start_signal.json"
            io_utils.atomic_write_json(signal_path, auto_start_signal)

            # Emit WebSocket notification for dashboard
            try:
                from websocket_events import emit_mission_auto_started
                mission_title = queue_item.get("mission_title") or (problem_statement[:60] + "..." if len(problem_statement) > 60 else problem_statement)
                emit_mission_auto_started(
                    mission_id=mission_id,
                    mission_title=mission_title,
                    queue_id=queue_item.get("id"),
                    source="queue_auto"
                )
            except ImportError:
                pass  # websocket_events not available

            logger.info(f"Created mission {mission_id} from queue. Auto-start signal written.")
            logger.info(f"Queued mission started: {queue_item.get('mission_title', 'Untitled')} "
                       f"(new_mission_id={mission_id}, queue_item_id={queue_item.get('id')})")

            # Small delay to ensure filesystem sync before verification
            time.sleep(0.01)  # 10ms

            # Verify mission was created successfully
            verify_mission = io_utils.atomic_read_json(MISSION_PATH, {})
            if verify_mission.get("mission_id") == mission_id and verify_mission.get("current_stage") == "PLANNING":
                logger.info(f"Verified mission {mission_id} created with PLANNING stage")
                return True
            else:
                logger.error(f"Mission verification failed: expected {mission_id} in PLANNING stage, "
                           f"got {verify_mission.get('mission_id')} in {verify_mission.get('current_stage')}")
                return False

        except Exception as e:
            logger.error(f"Failed to create mission from queue item: {e}")
            return False


# Alias for backward compatibility
RDMissionController = StageOrchestrator
