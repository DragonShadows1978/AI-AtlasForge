"""
af_engine - Modular R&D Engine

This package is the sole implementation of the AtlasForge R&D Engine.
The legacy monolithic engine file has been archived to .af_archived/.

Architecture:
    - StageOrchestrator: Core workflow orchestrator
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

Archival functions (af_engine/core/archival):
    - archive_mission_transcripts
    - ingest_afterimage_from_archive
    - rearchive_mission
    - rearchive_all_missions
"""

import logging

logger = logging.getLogger(__name__)

# Valid stages (6-stage workflow with CYCLE_END)
STAGES = ["PLANNING", "BUILDING", "TESTING", "ANALYZING", "CYCLE_END", "COMPLETE"]

# Modular engine components
from .orchestrator import StageOrchestrator as RDMissionController
from .state_manager import StateManager
from .stage_registry import StageRegistry
from .integration_manager import IntegrationManager
from .cycle_manager import CycleManager
from .prompt_factory import PromptFactory

# Archival functions (migrated from legacy monolithic engine)
from .core.archival import (
    archive_mission_transcripts,
    ingest_afterimage_from_archive,
    rearchive_mission,
    rearchive_all_missions,
)


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


def get_execution_trace() -> list:
    """Get execution trace (stage transitions) from the active mission."""
    import os
    from pathlib import Path
    mission_path = Path(os.environ.get("AF_MISSION_PATH", "/home/vader/AI-AtlasForge/state/mission.json"))
    sm = StateManager(mission_path)
    return sm.get_execution_trace()


__all__ = [
    'RDMissionController',
    'StateManager',
    'StageRegistry',
    'IntegrationManager',
    'CycleManager',
    'PromptFactory',
    'get_current_stage',
    'get_mission_status',
    'get_execution_trace',
    'STAGES',
    'archive_mission_transcripts',
    'ingest_afterimage_from_archive',
    'rearchive_mission',
    'rearchive_all_missions',
]
