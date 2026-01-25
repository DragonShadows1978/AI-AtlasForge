"""
af_engine - Modular Architecture for the R&D Engine

This package provides a modular, extensible architecture for the AtlasForge R&D Engine.
It uses protocol-based interfaces, event-driven integrations, and YAML configuration
for maximum flexibility and maintainability.

Feature Flag:
    USE_MODULAR_ENGINE controls which implementation is used:
    - False (default): Uses legacy af_engine_legacy.py
    - True: Uses new modular StageOrchestrator

The modular architecture includes:
    - StageOrchestrator: Core workflow orchestrator (~300 lines)
    - StateManager: Mission state persistence
    - StageRegistry: Plugin discovery and registration
    - IntegrationManager: Event-based integration coordination
    - CycleManager: Multi-cycle iteration logic
    - PromptFactory: Template-based prompt generation

Stage Handlers (af_engine/stages/):
    - PlanningStageHandler
    - BuildingStageHandler
    - TestingStageHandler
    - AnalyzingStageHandler
    - CycleEndStageHandler
    - CompleteStageHandler

Integration Handlers (af_engine/integrations/):
    17+ event-driven integrations for analytics, recovery, git, etc.
"""

import os
import logging

logger = logging.getLogger(__name__)

# Feature flag: Controls whether to use the new modular engine or legacy
# Default to False for safe rollout - can be enabled via environment variable
USE_MODULAR_ENGINE = os.environ.get('USE_MODULAR_ENGINE', 'false').lower() in ('true', '1', 'yes')

# Valid stages (same as legacy)
STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]

if USE_MODULAR_ENGINE:
    logger.info("Using MODULAR af_engine implementation")
    try:
        from .orchestrator import StageOrchestrator as RDMissionController
        from .state_manager import StateManager
        from .stage_registry import StageRegistry
        from .integration_manager import IntegrationManager
        from .cycle_manager import CycleManager
        from .prompt_factory import PromptFactory
    except ImportError as e:
        logger.error(f"Failed to import modular engine components: {e}")
        logger.warning("Falling back to legacy engine")
        USE_MODULAR_ENGINE = False
        # Fall through to legacy import below

if not USE_MODULAR_ENGINE:
    logger.info("Using LEGACY af_engine implementation")
    # Import from legacy during transition
    import sys
    from pathlib import Path

    # Add parent directory to path for legacy import
    parent_dir = Path(__file__).parent.parent
    if str(parent_dir) not in sys.path:
        sys.path.insert(0, str(parent_dir))

    from af_engine_legacy import (
        RDMissionController,
        archive_mission_transcripts,
        rearchive_mission,
        rearchive_all_missions,
    )


# Re-export common functionality regardless of which engine is used
def get_current_stage() -> str:
    """Get the current stage of the active mission."""
    controller = RDMissionController()
    return controller.mission.get("current_stage", "PLANNING")


def get_mission_status() -> dict:
    """Get the current mission status."""
    controller = RDMissionController()
    return {
        "mission_id": controller.mission.get("mission_id"),
        "stage": controller.mission.get("current_stage"),
        "iteration": controller.mission.get("iteration", 0),
        "cycle": controller.mission.get("current_cycle", 1),
        "cycle_budget": controller.mission.get("cycle_budget", 1),
    }


__all__ = [
    'RDMissionController',
    'get_current_stage',
    'get_mission_status',
    'USE_MODULAR_ENGINE',
    'STAGES',
]

# Conditionally export modular components when using new engine
if USE_MODULAR_ENGINE:
    __all__.extend([
        'StateManager',
        'StageRegistry',
        'IntegrationManager',
        'CycleManager',
        'PromptFactory',
    ])
else:
    __all__.extend([
        'archive_mission_transcripts',
        'rearchive_mission',
        'rearchive_all_missions',
    ])
