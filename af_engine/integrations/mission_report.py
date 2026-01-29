"""
af_engine.integrations.mission_report - Final Report Generation and Recommendation Storage

This integration generates final mission reports and saves mission recommendations
to SQLite storage when a mission completes.

This replaces the functionality that was in legacy af_engine.py's
_generate_final_report() and _save_recommendation() methods.
"""

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)

# Directories for output
BASE_DIR = Path(__file__).resolve().parent.parent.parent
MISSIONS_DIR = BASE_DIR / "missions"
MISSION_LOGS_DIR = MISSIONS_DIR / "mission_logs"
WORKSPACE_DIR = BASE_DIR / "workspace"


class MissionReportIntegration(BaseIntegrationHandler):
    """
    Generates final reports and saves mission recommendations.

    This integration handles the final report generation and recommendation
    persistence that was previously handled in the legacy af_engine.py.
    It subscribes to MISSION_COMPLETED events and performs:

    1. Generate and save final mission report to missions/mission_logs/
    2. Save next_mission_recommendation to SQLite storage
    3. Emit WebSocket events for real-time dashboard updates
    4. Ingest mission into Knowledge Base for learning extraction

    Priority: HIGH - should run after analytics (which updates token counts)
    but before other integrations that might depend on the report.
    """

    name = "mission_report"
    priority = IntegrationPriority.HIGH
    subscriptions = [
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, mission_logs_dir: Optional[Path] = None):
        """Initialize the mission report integration."""
        super().__init__()
        self.mission_logs_dir = mission_logs_dir or MISSION_LOGS_DIR
        self.mission_logs_dir.mkdir(parents=True, exist_ok=True)

    def on_mission_completed(self, event: Event) -> None:
        """
        Handle mission completion by generating report and saving recommendation.

        Args:
            event: Event with data containing:
                - total_cycles: int
                - deliverables: list
                - next_mission_recommendation: dict
                - final_report: dict
        """
        mission_id = event.mission_id
        event_data = event.data or {}

        logger.info(f"[MissionReport] Processing mission completion: {mission_id}")

        # Generate and save final report
        final_report = self._generate_final_report(event)

        # Save recommendation to SQLite
        next_rec = event_data.get("next_mission_recommendation")
        if next_rec:
            summary = ""
            if final_report:
                summary = final_report.get("final_summary", "")
            self._save_recommendation(next_rec, mission_id, summary)

        # Ingest to knowledge base
        self._ingest_to_knowledge_base(mission_id)

    def _generate_final_report(self, event: Event) -> Optional[Dict[str, Any]]:
        """
        Generate and save the final mission report.

        Args:
            event: The MISSION_COMPLETED event

        Returns:
            The generated report dict, or None if generation failed
        """
        mission_id = event.mission_id
        event_data = event.data or {}

        # Extract data from event (handle explicit None values)
        total_cycles = event_data.get("total_cycles") or 1
        deliverables = event_data.get("deliverables") or []
        final_report_data = event_data.get("final_report") or {}

        # Build the report
        final_report = {
            "mission_id": mission_id,
            "total_cycles": total_cycles,
            "completed_at": datetime.now().isoformat(),
            "final_summary": final_report_data.get("summary", ""),
            "all_files": final_report_data.get("all_files", []),
            "key_achievements": final_report_data.get("key_achievements", []),
            "challenges_overcome": final_report_data.get("challenges_overcome", []),
            "lessons_learned": final_report_data.get("lessons_learned", []),
            "deliverables": deliverables,
            "file_manifest": [],
            "statistics": {}
        }

        # Generate file manifest from workspace if available
        # Look for mission-specific workspace first
        mission_workspace = WORKSPACE_DIR
        mission_dir = MISSIONS_DIR / f"mission_{mission_id}"

        if mission_dir.exists():
            workspace_path = mission_dir / "workspace"
            if workspace_path.exists():
                mission_workspace = workspace_path

        # Scan for files modified during mission
        if mission_workspace.exists():
            try:
                for f in mission_workspace.rglob("*"):
                    if f.is_file() and not f.name.startswith("."):
                        try:
                            stat = f.stat()
                            final_report["file_manifest"].append({
                                "path": str(f.relative_to(mission_workspace)),
                                "full_path": str(f),
                                "size_bytes": stat.st_size,
                                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                                "file_type": f.suffix or "unknown"
                            })
                        except (OSError, IOError):
                            pass
            except Exception as e:
                logger.warning(f"[MissionReport] Error scanning workspace: {e}")

        # Calculate statistics
        final_report["statistics"] = {
            "total_files": len(final_report["file_manifest"]),
            "total_size_bytes": sum(f.get("size_bytes", 0) for f in final_report["file_manifest"]),
            "file_types": {}
        }

        for f in final_report["file_manifest"]:
            ftype = f.get("file_type", "unknown")
            final_report["statistics"]["file_types"][ftype] = \
                final_report["statistics"]["file_types"].get(ftype, 0) + 1

        # Save to mission_logs
        report_path = self.mission_logs_dir / f"{mission_id}_report.json"
        try:
            with open(report_path, 'w') as f:
                json.dump(final_report, f, indent=2, default=str)
            logger.info(f"[MissionReport] Saved final report to {report_path}")
        except Exception as e:
            logger.error(f"[MissionReport] Failed to save report: {e}")
            return None

        # Also save copy to mission directory if it exists
        if mission_dir.exists():
            try:
                mission_report_path = mission_dir / "final_report.json"
                with open(mission_report_path, 'w') as f:
                    json.dump(final_report, f, indent=2, default=str)
            except Exception as e:
                logger.debug(f"[MissionReport] Could not save to mission dir: {e}")

        return final_report

    def _save_recommendation(
        self,
        recommendation: Dict[str, Any],
        source_mission_id: str,
        source_summary: str,
        source_type: str = "successful_completion",
        drift_context: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Save a mission recommendation to SQLite storage.

        Args:
            recommendation: Dict with mission_title, mission_description, suggested_cycles, rationale
            source_mission_id: The mission that generated this recommendation
            source_summary: Brief summary of the source mission
            source_type: "successful_completion" or "drift_halt"
            drift_context: Optional dict with drift analysis data

        Returns:
            The recommendation ID if saved successfully, None otherwise
        """
        rec_entry = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": recommendation.get("mission_title", "Untitled Mission"),
            "mission_description": recommendation.get("mission_description", ""),
            "suggested_cycles": recommendation.get("suggested_cycles", 3),
            "source_mission_id": source_mission_id,
            "source_mission_summary": source_summary[:500] if source_summary else "",
            "rationale": recommendation.get("rationale", ""),
            "created_at": datetime.now().isoformat(),
            "source_type": source_type
        }

        # Add drift context if provided
        if drift_context:
            rec_entry["drift_context"] = drift_context

        # Save to SQLite storage
        try:
            from suggestion_storage import get_storage
            storage = get_storage()
            storage.add(rec_entry)
            logger.info(f"[MissionReport] Saved recommendation to SQLite ({source_type}): {rec_entry['mission_title']}")
        except Exception as e:
            logger.error(f"[MissionReport] SQLite save failed: {e}")
            return None

        # Emit WebSocket event for new recommendation
        try:
            from websocket_events import emit_recommendation_added
            emit_recommendation_added(rec_entry, queue_if_unavailable=True)
            logger.debug(f"[MissionReport] Emitted WebSocket event for recommendation")
        except ImportError:
            logger.debug("[MissionReport] WebSocket events module not available")
        except Exception as e:
            logger.debug(f"[MissionReport] WebSocket emit failed: {e}")

        return rec_entry["id"]

    def _ingest_to_knowledge_base(self, mission_id: str) -> None:
        """
        Ingest the completed mission into the Knowledge Base.

        Args:
            mission_id: The mission ID to ingest
        """
        report_path = self.mission_logs_dir / f"{mission_id}_report.json"

        if not report_path.exists():
            logger.debug(f"[MissionReport] Report not found for KB ingestion: {report_path}")
            return

        try:
            from mission_knowledge_base import get_knowledge_base
            kb = get_knowledge_base()
            ingest_result = kb.ingest_completed_mission(report_path)
            learnings_count = ingest_result.get('learnings_extracted', 0)
            logger.info(f"[MissionReport] Knowledge Base ingested mission - {learnings_count} learnings extracted")
        except ImportError:
            logger.debug("[MissionReport] Knowledge Base module not available")
        except Exception as e:
            logger.warning(f"[MissionReport] Knowledge Base ingestion failed: {e}")

    def _check_availability(self) -> bool:
        """
        Check if required dependencies are available.

        Returns True even if SQLite or KB are unavailable - we'll handle
        those gracefully in the individual methods.
        """
        return True
