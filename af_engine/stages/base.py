"""
af_engine.stages.base - Stage Handler Protocol and Data Classes

This module defines the core interfaces and data structures for stage handlers.
All stage handlers must implement the StageHandler protocol.
"""

from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Protocol, runtime_checkable
from datetime import datetime


@dataclass
class StageContext:
    """
    Context passed to stage handlers for each operation.

    Contains all the information a stage handler needs to generate prompts
    and process responses.
    """
    # Mission data
    mission: Dict[str, Any]
    mission_id: str
    original_mission: str
    problem_statement: str

    # Workspace paths
    workspace_dir: str
    artifacts_dir: str
    research_dir: str
    tests_dir: str

    # Iteration tracking
    cycle_number: int
    cycle_budget: int
    iteration: int
    max_iterations: int

    # History and state
    history: List[Dict[str, Any]]
    cycle_history: List[Dict[str, Any]]

    # Preferences and criteria
    preferences: Dict[str, Any] = field(default_factory=dict)
    success_criteria: List[str] = field(default_factory=list)

    # Optional context injections (from integrations)
    kb_context: Optional[str] = None
    afterimage_context: Optional[str] = None
    recovery_context: Optional[str] = None
    resumption_file: Optional[str] = None

    # Stage-specific data
    stage_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class StageRestrictions:
    """
    Defines what operations are allowed/blocked in a stage.

    Used by InitGuard to enforce stage restrictions.
    """
    # Tool restrictions
    allowed_tools: List[str] = field(default_factory=list)
    blocked_tools: List[str] = field(default_factory=list)

    # Path restrictions for writes
    allowed_write_paths: List[str] = field(default_factory=list)
    forbidden_write_paths: List[str] = field(default_factory=list)

    # Whether the stage allows code execution
    allow_bash: bool = True

    # Whether the stage is read-only
    read_only: bool = False


@dataclass
class StageResult:
    """
    Result returned by stage handler after processing a response.

    Contains the outcome of processing Claude's response, including
    the next stage to transition to and any events to emit.
    """
    # Outcome
    success: bool
    next_stage: str
    status: str  # e.g., 'plan_complete', 'build_in_progress', 'tests_passed'

    # Output data from response processing
    output_data: Dict[str, Any] = field(default_factory=dict)

    # Events to emit to integrations
    events_to_emit: List['Event'] = field(default_factory=list)

    # Optional message for logging/display
    message: Optional[str] = None

    # Timestamp
    timestamp: datetime = field(default_factory=datetime.now)


# Forward reference for Event (defined in integrations/base.py)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from ..integrations.base import Event


@runtime_checkable
class StageHandler(Protocol):
    """
    Protocol defining the interface for stage handlers.

    All stage handlers must implement this protocol to be used by the
    StageOrchestrator. The protocol defines methods for:
    - Generating prompts for the stage
    - Processing Claude's responses
    - Validating stage transitions
    - Defining stage restrictions
    """

    # Class attribute identifying the stage
    stage_name: str

    def get_prompt(self, context: StageContext) -> str:
        """
        Generate the prompt for this stage.

        Args:
            context: Stage context with mission data and state

        Returns:
            The complete prompt string for Claude
        """
        ...

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process Claude's response and determine next stage.

        Args:
            response: Claude's response dictionary
            context: Stage context with mission data and state

        Returns:
            StageResult with next stage and any events to emit
        """
        ...

    def validate_transition(
        self,
        from_stage: str,
        context: StageContext
    ) -> bool:
        """
        Validate whether a transition to this stage is allowed.

        Args:
            from_stage: The stage transitioning from
            context: Stage context with mission data and state

        Returns:
            True if the transition is valid, False otherwise
        """
        ...

    def get_restrictions(self) -> StageRestrictions:
        """
        Get the restrictions for this stage.

        Returns:
            StageRestrictions defining allowed/blocked operations
        """
        ...


class BaseStageHandler:
    """
    Base class providing default implementations for stage handlers.

    Stage handlers can inherit from this class to get default behavior
    and only override what they need to customize.
    """

    stage_name: str = "UNKNOWN"

    # Default valid transitions (can be overridden)
    valid_from_stages: List[str] = []

    def get_prompt(self, context: StageContext) -> str:
        """Default prompt - should be overridden."""
        return f"Stage {self.stage_name} prompt not implemented"

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """Default response processing - should be overridden."""
        return StageResult(
            success=False,
            next_stage=self.stage_name,
            status="not_implemented",
            message=f"Stage {self.stage_name} response processing not implemented"
        )

    def validate_transition(
        self,
        from_stage: str,
        context: StageContext
    ) -> bool:
        """
        Default transition validation.

        Checks if from_stage is in valid_from_stages list.
        """
        if not self.valid_from_stages:
            return True  # No restrictions
        return from_stage in self.valid_from_stages

    def get_restrictions(self) -> StageRestrictions:
        """Default restrictions - full access."""
        return StageRestrictions(
            allowed_tools=[],  # Empty means no restrictions
            blocked_tools=[],
            allowed_write_paths=["*"],
            forbidden_write_paths=[],
            allow_bash=True,
            read_only=False
        )

    def _format_history(self, history: List[Dict], max_entries: int = 10) -> str:
        """
        Format history entries for inclusion in prompts.

        Args:
            history: List of history entries
            max_entries: Maximum number of recent entries to include

        Returns:
            Formatted string of recent history
        """
        if not history:
            return "No history yet."

        recent = history[-max_entries:]
        lines = []
        for entry in recent:
            timestamp = entry.get("timestamp", "unknown")
            stage = entry.get("stage", "unknown")
            event = entry.get("event", "unknown")
            lines.append(f"  [{timestamp}] {stage}: {event}")

        return "\n".join(lines)

    def _format_cycle_history(self, cycle_history: List[Dict]) -> str:
        """
        Format cycle history for inclusion in prompts.

        Args:
            cycle_history: List of cycle summaries

        Returns:
            Formatted string of cycle history
        """
        if not cycle_history:
            return "No previous cycles."

        lines = []
        for cycle in cycle_history:
            cycle_num = cycle.get("cycle", "?")
            summary = cycle.get("summary", "No summary")
            lines.append(f"  Cycle {cycle_num}: {summary}")

        return "\n".join(lines)
