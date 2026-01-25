"""
af_engine.stages - Stage Handler Implementations

This module provides stage handlers for each stage of the R&D workflow.
Each handler implements the StageHandler protocol and provides:
    - get_prompt(): Generate the prompt for the stage
    - process_response(): Process Claude's response and determine next stage
    - validate_transition(): Validate stage transitions
    - get_restrictions(): Define tool/path restrictions for the stage

Stage Flow:
    PLANNING -> BUILDING -> TESTING -> ANALYZING -> CYCLE_END -> COMPLETE
        ^                                  |              |
        |__________________________________|              |
                 (if tests fail)                          |
        |_________________________________________________|
                  (if more cycles remain)
"""

from .base import (
    StageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)

# Import all stage handlers
from .complete import CompleteStageHandler
from .planning import PlanningStageHandler
from .building import BuildingStageHandler
from .testing import TestingStageHandler
from .analyzing import AnalyzingStageHandler
from .cycle_end import CycleEndStageHandler

__all__ = [
    # Protocol and data classes
    'StageHandler',
    'StageContext',
    'StageResult',
    'StageRestrictions',
    # Stage handlers
    'CompleteStageHandler',
    'PlanningStageHandler',
    'BuildingStageHandler',
    'TestingStageHandler',
    'AnalyzingStageHandler',
    'CycleEndStageHandler',
]

# Stage handler registry for easy lookup
STAGE_HANDLERS = {
    'PLANNING': PlanningStageHandler,
    'BUILDING': BuildingStageHandler,
    'TESTING': TestingStageHandler,
    'ANALYZING': AnalyzingStageHandler,
    'CYCLE_END': CycleEndStageHandler,
    'COMPLETE': CompleteStageHandler,
}


def get_handler_for_stage(stage_name: str) -> type:
    """
    Get the handler class for a given stage name.

    Args:
        stage_name: Name of the stage (e.g., 'PLANNING', 'BUILDING')

    Returns:
        The handler class for the stage

    Raises:
        KeyError: If stage_name is not a valid stage
    """
    return STAGE_HANDLERS[stage_name]
