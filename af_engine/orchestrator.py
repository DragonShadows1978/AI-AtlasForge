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

import logging
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

    @property
    def current_stage(self) -> str:
        """Get the current stage."""
        return self.state.current_stage

    @property
    def mission_id(self) -> str:
        """Get the mission ID."""
        return self.state.mission_id

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
        4. Returns the next stage

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

        # Increment iteration
        self.state.increment_iteration()

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

    def reload_mission(self) -> None:
        """Reload mission from disk."""
        self.state.load_mission()

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


# Alias for backward compatibility
RDMissionController = StageOrchestrator
