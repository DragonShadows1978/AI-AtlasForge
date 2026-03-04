"""
af_engine.integrations.mission_report - Final Report Generation and Recommendation Storage

This integration generates final mission reports and saves mission recommendations
to SQLite storage when a mission completes.

This replaces the functionality that was in the legacy monolithic engine's
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
    persistence that was previously handled in the legacy monolithic engine.
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

    # Placeholder/generic titles that indicate a low-quality LLM response
    _PLACEHOLDER_TITLES = frozenset({
        "follow-up mission",
        "follow up mission",
        "untitled mission",
        "continue work",
        "next mission",
        "follow up",
        "continuation",
        "next steps",
        "a concise title for the recommended next mission",
        "recommended next mission",
        "new mission",
    })

    def __init__(self, mission_logs_dir: Optional[Path] = None):
        """Initialize the mission report integration."""
        super().__init__()
        self.mission_logs_dir = mission_logs_dir or MISSION_LOGS_DIR
        self.mission_logs_dir.mkdir(parents=True, exist_ok=True)
        self._processed_missions: set = set()  # deduplication guard against double-fire

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

        # Deduplication guard: MISSION_COMPLETED can fire more than once due to
        # orchestrator bugs. Skip if we have already processed this mission.
        if mission_id in self._processed_missions:
            logger.warning(f"[MissionReport] Duplicate MISSION_COMPLETED for {mission_id}, skipping")
            return
        self._processed_missions.add(mission_id)

        logger.info(f"[MissionReport] Processing mission completion: {mission_id}")

        # Generate and save final report
        final_report = self._generate_final_report(event)
        source_summary = final_report.get("final_summary", "") if final_report else ""

        # Save all continuation missions from the manifest (primary path)
        continuation_missions = event_data.get("continuation_missions")
        if continuation_missions and isinstance(continuation_missions, list):
            self._save_continuation_manifest(continuation_missions, mission_id, source_summary)
        else:
            # Backward-compat: fall back to single next_mission_recommendation
            next_rec = event_data.get("next_mission_recommendation")
            if next_rec:
                self._save_recommendation(next_rec, mission_id, source_summary)

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

        # Build the report with all required fields for GlassBox
        final_report = {
            "mission_id": mission_id,
            "total_cycles": total_cycles,
            "completed_at": datetime.now().isoformat(),
            "started_at": event_data.get("started_at"),
            "mission_workspace": event_data.get("mission_workspace"),
            "mission_dir": event_data.get("mission_dir"),
            "original_mission": event_data.get("problem_statement"),
            "current_cycle_completed": event_data.get("cycle_count", 1),
            "cycle_history": event_data.get("cycle_history", []),
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
        # Look for mission-specific workspace first - use event data if available
        mission_workspace = Path(event_data.get("mission_workspace")) if event_data.get("mission_workspace") else WORKSPACE_DIR
        # mission_id already has "mission_" prefix, don't add it again
        mission_dir = Path(event_data.get("mission_dir")) if event_data.get("mission_dir") else MISSIONS_DIR / mission_id

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

        # Collect agent errors for this mission from claude_journal.jsonl
        try:
            af_root = Path(__file__).resolve().parent.parent.parent
            journal_path = af_root / 'state' / 'claude_journal.jsonl'
            agent_errors = []
            if journal_path.exists():
                with open(journal_path, 'r') as _jf:
                    for _line in _jf:
                        _line = _line.strip()
                        if not _line:
                            continue
                        try:
                            _entry = json.loads(_line)
                            if (_entry.get('type') == 'agent_error' and
                                    _entry.get('mission_id') == mission_id):
                                agent_errors.append(_entry)
                        except (json.JSONDecodeError, KeyError):
                            pass
            final_report['agent_errors'] = agent_errors
            final_report['agent_error_count'] = len(agent_errors)
        except Exception:
            final_report['agent_errors'] = []
            final_report['agent_error_count'] = 0

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
        # Reject placeholder/generic titles produced by low-quality LLM responses
        title = (recommendation.get("mission_title") or "").strip()
        if not title or len(title) < 5 or title.lower() in self._PLACEHOLDER_TITLES:
            logger.warning(f"[MissionReport] Skipping generic/placeholder recommendation title: '{title}'")
            return None

        rec_entry = {
            "id": f"rec_{uuid.uuid4().hex[:8]}",
            "mission_title": recommendation.get("mission_title", "Untitled Mission"),
            "mission_description": recommendation.get("mission_description", ""),
            "suggested_cycles": recommendation.get("suggested_cycles", 3),
            "source_mission_id": source_mission_id,
            "source_mission_summary": source_summary[:500] if source_summary else "",
            "rationale": recommendation.get("rationale", ""),
            "created_at": datetime.now().isoformat(),
            "source_type": source_type,
            "priority_score": recommendation.get("priority_score", 50.0),
            # v2 fields — passed through from continuation manifest or defaulted
            "mission_type": recommendation.get("mission_type", "EXPANSION"),
            "bug_references": recommendation.get("bug_references", []),
            "scope_context": recommendation.get("scope_context"),
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

        # Notify activity log that a suggestion was generated
        try:
            from atlasforge_config import STATE_DIR
            import io_utils
            chat_history_path = STATE_DIR / "chat_history.json"
            source_labels = {
                "successful_completion": "mission completion",
                "drift_halt": "drift halt",
                "manual": "manual"
            }
            source_label = source_labels.get(source_type, source_type)
            notification = f'[SUGGESTION] Mission suggestion generated: "{rec_entry["mission_title"]}" (via {source_label})'

            def _add_suggestion_to_chat(history):
                if not isinstance(history, list):
                    history = []
                history.append({
                    "role": "claude",
                    "provider": "system",
                    "content": notification,
                    "timestamp": rec_entry["created_at"]
                })
                if len(history) > 500:
                    history = history[-500:]
                return history

            io_utils.atomic_update_json(chat_history_path, _add_suggestion_to_chat, [])
            logger.info(f'[MissionReport] Logged suggestion to activity log: {rec_entry["mission_title"]}')
        except Exception as e:
            logger.debug(f"[MissionReport] Could not write suggestion to activity log: {e}")

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

    def _save_continuation_manifest(
        self,
        missions: list,
        source_mission_id: str,
        source_summary: str,
    ) -> None:
        """
        Save all missions from the continuation manifest to SQLite.

        Each manifest mission is saved as a separate suggestion row with the
        appropriate mission_type, bug_references, and scope_context set.
        Priority 1 (BUGFIX) missions get a boosted priority_score so they
        sort to the top of the queue panel.
        """
        # Map category → (mission_type, scope_context, base_priority_score)
        category_map = {
            "BUGFIX":      ("BUGFIX",      "out_of_scope", 90.0),
            "TECH_DEBT":   ("TECH_DEBT",   "out_of_scope", 70.0),
            "COMPLETION":  ("COMPLETION",  None,           65.0),
            "EXPANSION":   ("EXPANSION",   None,           50.0),
        }

        saved = 0
        for mission in missions:
            try:
                category = (mission.get("category") or "EXPANSION").upper()
                mission_type, scope_context, base_score = category_map.get(
                    category, ("EXPANSION", None, 50.0)
                )
                # Higher priority number → lower in queue priority (invert manifest priority).
                # Default to 1 (highest priority) when field is missing; clamp to [1, 10].
                raw_priority = mission.get("priority", 1)
                try:
                    manifest_priority = max(1, min(10, int(raw_priority)))
                except (TypeError, ValueError):
                    manifest_priority = 1
                priority_score = max(10.0, base_score - (manifest_priority - 1) * 5)

                title = mission.get("title", "").strip()
                if not title or len(title) < 5:
                    logger.warning(f"[MissionReport] Skipping continuation mission with short/empty title: '{title}'")
                    continue

                rec_entry = {
                    "mission_title": title,
                    "mission_description": mission.get("description", ""),
                    "suggested_cycles": 3,
                    "source_mission_id": source_mission_id,
                    "source_mission_summary": source_summary[:500] if source_summary else "",
                    "rationale": mission.get("rationale", ""),
                    "source_type": "successful_completion",
                    "priority_score": priority_score,
                    "mission_type": mission_type,
                    "bug_references": mission.get("source_bugs", []),
                    "scope_context": scope_context,
                }
                self._save_recommendation(rec_entry, source_mission_id, source_summary,
                                          source_type="successful_completion")
                saved += 1
            except Exception as e:
                title_safe = mission.get('title', '?') if isinstance(mission, dict) else repr(mission)
                logger.warning(f"[MissionReport] Failed to save continuation mission '{title_safe}': {e}")

        logger.info(f"[MissionReport] Saved {saved}/{len(missions)} continuation missions from manifest")

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
