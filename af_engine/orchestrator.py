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
import os
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
from .mission_config import MissionConfig, MissionValidationError, save_audit_log, prune_old_audit_logs
from .integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)

import re as _re


def _sanitize_for_log(value: str) -> str:
    """Strip control characters and newlines to prevent log injection.

    Covers ASCII controls (\\x00-\\x1f, \\x7f) and Unicode line/paragraph
    separators (U+2028, U+2029) which some terminals and log parsers treat
    as line endings.
    """
    return _re.sub(r'[\x00-\x1f\x7f\u2028\u2029]', '', str(value))


def _format_research_context(findings, prior_found: bool = False) -> str:
    """Format ResearchFindings into a prompt-injectable context block."""
    cycle_note = "\n*Built on prior cycle research findings.*" if prior_found else ""

    # Guard: topics_researched may be non-list; str()-cast prevents join() crash on non-str items
    topics_list = findings.topics_researched if isinstance(findings.topics_researched, list) else []
    topics = ", ".join(str(t) for t in topics_list[:5]) if topics_list else "None identified"
    total = findings.total_sources or 0
    primary = findings.primary_sources or 0
    sources_line = f"{total} total ({primary} primary)"

    # Synthesized recommendations and gaps
    if findings.synthesis:
        # Guard: to_markdown() may return None or raise; fall back to empty string
        try:
            raw = findings.synthesis.to_markdown()
            md_content = raw if isinstance(raw, str) else ""
        except Exception:
            md_content = ""
        if not md_content:
            md_content = "No synthesis available."
        # Trim to avoid flooding prompt (cap at ~3000 chars)
        elif len(md_content) > 3000:
            md_content = md_content[:3000] + "\n...[truncated — full report in research/research_findings.md]"
    else:
        md_content = "No synthesis available."

    return f"""=== RESEARCH FINDINGS (Pre-computed) ==={cycle_note}
The following research was conducted automatically before planning began:

Topics Researched: {topics}
Sources Consulted: {sources_line}

{md_content}

Full research report: research/research_findings.md

Use these findings as the foundation for your implementation plan.
Do NOT re-research topics already covered above — focus your research
on filling the identified knowledge gaps only. Every major architectural
decision MUST cite either the pre-computed research above or a specific
source you consulted during planning.
=== END RESEARCH FINDINGS ==="""


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
        except (ImportError, AttributeError, TypeError, ValueError) as e:
            # Narrow to expected failure modes — unexpected exceptions propagate.
            # ImportError: module not found; AttributeError: missing attr on integration class;
            # TypeError: wrong argument types; ValueError: invalid configuration.
            logger.error("load_default_integrations() failed: %s", e)
        stats = self.integrations.get_stats()
        n = stats['handlers_registered']
        if n == 0:
            logger.error(
                "No integrations loaded — all handlers failed or DEFAULT_INTEGRATIONS is empty. "
                "Post-mission hooks and other integrations will not fire."
            )
        else:
            logger.info(
                "Loaded %d integrations (%d available)",
                n, stats['handlers_available']
            )

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

    def update_stage(self, new_stage: str) -> bool:
        """
        Update the mission stage.

        This method:
        1. Emits STAGE_COMPLETED event for the old stage
        2. Updates the state
        3. Emits STAGE_STARTED event for the new stage

        Args:
            new_stage: The new stage to transition to
        """
        if not isinstance(new_stage, str):
            logger.error("Invalid stage type: %s (expected str)", type(new_stage).__name__)
            return False

        new_stage = new_stage.upper()
        if new_stage not in self.STAGES:
            logger.error("Invalid stage: %s", _sanitize_for_log(new_stage))
            return False

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
        logger.info("Stage transition: %s -> %s", _sanitize_for_log(str(old)), _sanitize_for_log(new_stage))

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
            # NOTE: MISSION_COMPLETED is emitted by the CYCLE_END stage handler
            # (which has the full recommendation data). Do NOT emit it again here
            # or it will fire twice — once with data (correct) and once without (bug).
            # See: af_engine/stages/cycle_end.py and mission_report.py

            # Process mission queue - start next queued mission
            self._process_mission_queue()

        return True

    def _validate_mission_completed_data(self, data: Dict[str, Any]) -> None:
        """
        Validate required fields in MISSION_COMPLETED event data.

        Logs a WARNING for any required fields that are None or missing.
        This prevents silent failures where missing workspace data causes
        transcript archival to record zero tokens.

        Required fields: mission_workspace, mission_dir, project_name, created_at
        """
        required_fields = ["mission_workspace", "mission_dir", "project_name", "created_at"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            logger.warning(
                "[Orchestrator] MISSION_COMPLETED event missing required fields: %s "
                "for mission %s — transcript archival may record zero tokens.",
                missing,
                self.mission_id,
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
            # Run ResearchOrchestrator BEFORE the planning agent starts.
            # This ensures findings are pre-loaded context, not something the
            # agent documents as an afterthought.
            research_context = self._run_pre_planning_research(stage_context)
            if research_context:
                full_prompt = self.prompts.inject_research_context(
                    full_prompt,
                    research_context
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
        # Guard against non-dict response
        if not isinstance(response, dict):
            response = {}

        stage = self.current_stage
        handler = self.registry.get_handler(stage)
        stage_context = self._build_stage_context()

        # Log incoming response for debugging (sanitize status to prevent log injection)
        _status = _sanitize_for_log(response.get('status', ''))
        logger.info(f"Processing {stage} response with status='{_status}'")

        # Process response through handler
        result: StageResult = handler.process_response(response, stage_context)

        # Validate transition before accepting it
        # Fix: explicit check for empty string (falsy but distinct from None)
        if result.next_stage == "":
            logger.warning(
                f"Stage handler returned empty string for next_stage in {stage}. "
                "Treating as no transition (staying in current stage)."
            )
            result = StageResult(
                success=result.success,
                next_stage=stage,
                status=result.status,
                message=result.message,
                output_data=result.output_data,
                events_to_emit=result.events_to_emit,
            )
        elif result.next_stage and result.next_stage != stage:
            original_target = result.next_stage
            try:
                target_handler = self.registry.get_handler(result.next_stage)
            except (KeyError, ImportError):
                target_handler = None
            if target_handler is None:
                if result.events_to_emit:
                    logger.warning(
                        f"Discarding {len(result.events_to_emit)} event(s) from rejected "
                        f"transition to '{original_target}': {[e.type for e in result.events_to_emit]}"
                    )
                logger.warning(
                    f"Unregistered stage '{original_target}' rejected. Staying in {stage}."
                )
                result = StageResult(
                    success=False,
                    next_stage=stage,
                    status="unregistered_stage",
                    message=f"Stage '{original_target}' is not registered"
                )
            elif hasattr(target_handler, 'validate_transition'):
                try:
                    _transition_valid = target_handler.validate_transition(stage, stage_context)
                except Exception as _e:
                    logger.warning(
                        f"validate_transition() raised {type(_e).__name__}: {_e}. "
                        "Treating as validation failure."
                    )
                    _transition_valid = False
                if not _transition_valid:
                    logger.warning(
                        f"Invalid transition {stage} -> {original_target} rejected. "
                        f"Staying in {stage}."
                    )
                    # Fix: CYCLE_END->PLANNING rejection must fall back to COMPLETE to prevent deadlock
                    if stage == "CYCLE_END" and original_target == "PLANNING":
                        if result.events_to_emit:
                            logger.warning(
                                f"Discarding {len(result.events_to_emit)} event(s) from CYCLE_END "
                                f"deadlock-prevention fallback: {[e.type for e in result.events_to_emit]}"
                            )
                        logger.error(
                            "CYCLE_END->PLANNING transition rejected — falling back to COMPLETE "
                            "to prevent state machine deadlock."
                        )
                        result = StageResult(
                            success=False,
                            next_stage="COMPLETE",
                            status="cycle_deadlock_prevented",
                            message="CYCLE_END->PLANNING rejected; transitioning to COMPLETE"
                        )
                    else:
                        if result.events_to_emit:
                            logger.warning(
                                f"Discarding {len(result.events_to_emit)} event(s) from rejected "
                                f"transition to '{original_target}': {[e.type for e in result.events_to_emit]}"
                            )
                        result = StageResult(
                            success=False,
                            next_stage=stage,
                            status="invalid_transition",
                            message=f"Transition from {stage} to {original_target} not allowed"
                        )

        # Pre-logging CYCLE_END continuation check:
        # Check should_continue_cycle() BEFORE emitting events or logging so that the
        # state machine log is consistent — if we're going to COMPLETE, log it as COMPLETE,
        # not as PLANNING.
        if stage == "CYCLE_END" and result.next_stage == "PLANNING":
            if not self.should_continue_cycle():
                logger.info(
                    "Cycle budget exhausted — overriding PLANNING to COMPLETE before logging"
                )
                result = StageResult(
                    success=True,
                    next_stage="COMPLETE",
                    status="cycle_budget_exhausted",
                    message="All cycles complete",
                    output_data=result.output_data,
                    events_to_emit=result.events_to_emit,
                )

        # Emit events from result
        for event in result.events_to_emit:
            self.integrations.emit(event)

        # Log result with handler decision
        logger.info(
            f"Stage {stage} handler returned: next_stage='{result.next_stage}', "
            f"status='{result.status}', success={result.success}"
        )

        if result.message:
            logger.info(f"Handler message: {result.message}")

        # Check if stage handler requests iteration increment
        # Only increment on ANALYZING -> BUILDING or ANALYZING -> PLANNING transitions
        # (i.e., when needs_revision or needs_replanning)
        if result.output_data.get("_increment_iteration"):
            self.state.increment_iteration()
            logger.info(f"Iteration incremented to {self.state.iteration}")

        # Handle cycle advancement for CYCLE_END -> PLANNING transitions.
        # Note: by the time we reach here, result.next_stage is already corrected to
        # COMPLETE if cycle budget is exhausted (handled by the pre-logging block above),
        # so this branch only fires when continuation is actually allowed.
        if stage == "CYCLE_END" and result.next_stage == "PLANNING":
            continuation_prompt = result.output_data.get("continuation_prompt", "")
            if not continuation_prompt:
                # Generate default continuation if Claude didn't provide one
                continuation_prompt = self._generate_default_continuation()
                logger.warning(f"No continuation_prompt provided, using default for cycle {self.cycles.current_cycle + 1}")

            # === DRIFT VALIDATION ===
            # Validate continuation prompt against original mission before advancing.
            # Fails open: any error returns (prompt, False) to preserve current behavior.
            validated_prompt, should_halt = self._validate_continuation_drift(
                continuation_prompt, self.cycles.current_cycle + 1
            )
            if should_halt:
                logger.warning(
                    f"Mission halted at cycle {self.cycles.current_cycle} due to drift — "
                    "transitioning to COMPLETE"
                )
                self.update_stage("COMPLETE")
                return "COMPLETE"

            logger.info(f"Advancing cycle from {self.cycles.current_cycle} to next cycle")
            self.advance_to_next_cycle(validated_prompt)
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

    def _validate_continuation_drift(
        self,
        continuation_prompt: str,
        cycle_number: int
    ) -> tuple:
        """
        Validate continuation prompt against original mission for drift.

        Uses LLM-as-judge evaluation via MissionDriftValidator with graduated
        intervention: log → warn → inject warning → halt.

        Returns:
            (validated_prompt, should_halt): If should_halt=True, caller must
            transition to COMPLETE. If a warning was injected, validated_prompt
            has the warning prepended. Fails open on any error.
        """
        try:
            import sys as _sys
            root_str = str(self.root)
            if not _sys.path or _sys.path[0] != root_str:
                _sys.path.insert(0, root_str)
            from adversarial_testing.mission_drift_validator import (
                MissionDriftValidator,
                DriftTrackingState,
                DriftDecision,
                load_tracking_state,
                save_tracking_state,
                save_validation_result,
            )
        except ImportError as e:
            logger.debug(f"Drift validation not available (import failed: {e}) - skipping")
            return continuation_prompt, False

        original_mission = (
            self.state.get_field("original_problem_statement") or
            self.state.get_field("problem_statement", "")
        )
        if not original_mission:
            logger.warning("No original mission statement found in state - skipping drift validation")
            return continuation_prompt, False

        mission_dir = self.state.mission_dir
        if not mission_dir:
            logger.warning("No mission_dir in state - skipping drift validation")
            return continuation_prompt, False

        drift_dir = Path(mission_dir) / "drift_validation"
        drift_dir.mkdir(parents=True, exist_ok=True)

        tracking_state = load_tracking_state(drift_dir) or DriftTrackingState()

        try:
            validator = MissionDriftValidator(timeout_seconds=120)
            result, updated_state = validator.validate_continuation(
                original_mission=original_mission,
                continuation_prompt=continuation_prompt,
                cycle_number=cycle_number,
                tracking_state=tracking_state,
            )

            save_validation_result(result, drift_dir)
            save_tracking_state(updated_state, drift_dir)

            # Persist drift metadata for dashboard visibility
            self.state.set_field("drift_validation", {
                "failure_count": updated_state.failure_count,
                "average_similarity": updated_state.average_similarity,
                "last_validation_cycle": cycle_number,
                "warning_issued": updated_state.warning_issued,
                "last_decision": result.decision.value,
                "last_severity": result.drift_severity.value,
                "last_similarity": result.semantic_similarity,
            })

            logger.info(
                f"Drift validation cycle {cycle_number}: "
                f"drift={result.drift_detected}, severity={result.drift_severity.value}, "
                f"similarity={result.semantic_similarity:.1%}, decision={result.decision.value}"
            )

            if result.decision == DriftDecision.HALT:
                import json as _json
                recap = validator.generate_drift_recap(
                    tracking_state=updated_state,
                    original_mission=original_mission,
                    mission_id=self.state.mission_id,
                )
                (drift_dir / "drift_recap.json").write_text(_json.dumps(recap, indent=2))
                logger.error(
                    f"Mission HALTED due to drift at cycle {cycle_number} — "
                    f"failure_count={updated_state.failure_count}, "
                    f"similarity={result.semantic_similarity:.1%}"
                )
                self.state.set_field("halted_due_to_drift", True)
                self.state.set_field("halted_at_cycle", cycle_number)
                return continuation_prompt, True

            elif result.decision == DriftDecision.INJECT_WARNING:
                warning = validator.generate_warning_message(
                    result=result,
                    tracking_state=updated_state,
                    original_mission=original_mission,
                )
                logger.warning(
                    f"Drift warning injected at cycle {cycle_number} "
                    f"(failure {updated_state.failure_count}/{validator.failure_threshold_halt})"
                )
                return warning + continuation_prompt, False

            elif result.decision == DriftDecision.LOG_WARNING:
                logger.warning(
                    f"Drift detected at cycle {cycle_number} — logging only "
                    f"(severity={result.drift_severity.value}, "
                    f"similarity={result.semantic_similarity:.1%})"
                )
                return continuation_prompt, False

            else:  # ALLOW
                return continuation_prompt, False

        except Exception as e:
            logger.error(f"Drift validation error at cycle {cycle_number}: {e}")
            import traceback
            logger.debug(traceback.format_exc())
            return continuation_prompt, False  # Fail open

    def _run_pre_planning_research(self, stage_context) -> Optional[str]:
        """
        Run ResearchOrchestrator before the planning prompt is built.

        Returns a formatted research context string to inject into the prompt,
        or None if research fails/times out (graceful fallback).

        Multi-cycle continuity: if research_findings.md already exists from a
        prior cycle, it is passed as context so the orchestrator builds on
        existing findings rather than starting fresh.
        """
        try:
            import sys as _sys
            root_str = str(self.root)
            # Check position 0: path elsewhere still allows module shadowing
            if not _sys.path or _sys.path[0] != root_str:
                _sys.path.insert(0, root_str)
            from research_agent import ResearchOrchestrator, ResearchConfig
        except ImportError as e:
            logger.warning(f"[Planning] ResearchOrchestrator not available: {e}")
            return None

        import threading
        from pathlib import Path as _Path

        research_dir = _Path(stage_context.research_dir)
        prior_findings_path = research_dir / "research_findings.md"

        # Check for prior cycle findings (multi-cycle continuity)
        prior_findings_text = None
        prior_found = False
        if prior_findings_path.exists() and stage_context.cycle_number > 1:
            try:
                prior_findings_text = prior_findings_path.read_text(encoding="utf-8")
                prior_found = True
                logger.info(
                    "[Planning] Found prior research findings from cycle %d, will build on them",
                    stage_context.cycle_number - 1,
                )
            except Exception as exc:
                logger.warning(
                    "[Planning] Could not read prior cycle findings (%s): %s",
                    prior_findings_path, exc,
                )

        # Web search routes through the WebProxy MCP (proxy_cli_args in
        # research_agent/web_researcher.py), which is the isolation boundary that
        # prevents recursive subprocess spawning. The previous ATLASFORGE_ENABLE_WEB_SEARCH
        # env-var gate is no longer needed.
        config = ResearchConfig(
            max_topics=5,
            max_queries_per_topic=3,
            timeout_seconds=120,
            use_web_search=True,
        )
        orchestrator = ResearchOrchestrator(config=config)

        # Use original mission statement — cycle 2+ overwrites problem_statement with continuation prompt
        mission_description = (
            self.state.mission.get("original_problem_statement")
            or self.state.mission.get("problem_statement")
            or stage_context.problem_statement
        )

        # Build research context string (include prior findings for multi-cycle)
        research_context_str = "[Mission workspace context]"
        if prior_findings_text:
            prior_text = prior_findings_text[:2000]
            if len(prior_findings_text) > 2000:
                prior_text += "\n...[truncated — full report in research/research_findings.md]"
            research_context_str += f"\n\n### Prior Cycle Research:\n{prior_text}"

        result_holder: list = [None]
        error_holder: list = [None]
        # cancel_event: signals the daemon thread to skip disk writes if caller timed out
        cancel_event = threading.Event()

        # Register Researcher Agent for Mission Activity panel visibility
        import uuid as _uuid
        _research_agent_id = f"researcher_{_uuid.uuid4().hex[:8]}"
        _stream_file = None
        _complete_fn = None
        try:
            import sys as _sys2
            _root_str2 = str(self.root)
            if not _sys2.path or _sys2.path[0] != _root_str2:
                _sys2.path.insert(0, _root_str2)
            from agent_stream_manager import register_agent as _reg, complete_agent as _cmp
            _stream_file = _reg(context='mission', agent_id=_research_agent_id,
                                label='Researcher Agent', pid=0)
            _complete_fn = _cmp
        except Exception as _e:
            logger.debug("[Planning] agent_stream_manager unavailable: %s", _e)

        def _stream_write(msg: str) -> None:
            if not _stream_file:
                return
            try:
                with open(_stream_file, 'a', encoding='utf-8') as _f:
                    _f.write(msg.rstrip('\n') + '\n')
            except Exception:
                pass

        try:
            from websocket_events import emit_research_event as _emit_research_event
        except Exception:
            _emit_research_event = None

        def _progress_callback(msg: str) -> None:
            logger.info("[Research] %s", msg)
            _stream_write(msg)
            if _emit_research_event is not None:
                try:
                    _emit_research_event(agent_id=_research_agent_id,
                                        label='Researcher Agent',
                                        status='running', message=msg)
                except Exception:
                    pass

        logger.info(
            "[Planning] Research phase started: up to %d topics, mission='%.60s...'",
            config.max_topics,
            mission_description,
        )
        _research_start = time.time()

        def _run() -> None:
            try:
                findings = orchestrator.research_for_planning(
                    mission=mission_description,
                    topics=None,  # auto-extract from mission
                    context=research_context_str,
                    progress_callback=_progress_callback,
                )
                # Skip save and result assignment if caller already timed out
                if not cancel_event.is_set():
                    try:
                        research_dir.mkdir(parents=True, exist_ok=True)
                        findings.save_report(prior_findings_path)
                    except Exception as save_err:
                        logger.warning("[Planning] Failed to save research report: %s", save_err)
                    result_holder[0] = findings
            except Exception as exc:
                error_holder[0] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout=120)

        # Signal cancellation on timeout — daemon thread checks cancel_event before writing
        if thread.is_alive():
            cancel_event.set()
            if _complete_fn:
                try:
                    _complete_fn(_research_agent_id, error='timeout')
                except Exception:
                    pass
            logger.warning(
                "[Planning] Research phase timed out after 120s — "
                "planning will proceed without pre-computed research findings."
            )
            return None

        if error_holder[0]:
            if _complete_fn:
                try:
                    _complete_fn(_research_agent_id, error=str(error_holder[0])[:200])
                except Exception:
                    pass
            logger.warning(
                "[Planning] Research phase failed (%s) — continuing without research context",
                type(error_holder[0]).__name__,
            )
            return None

        findings = result_holder[0]
        if not findings:
            if _complete_fn:
                try:
                    _complete_fn(_research_agent_id, error='no_findings')
                except Exception:
                    pass
            return None

        _elapsed = time.time() - _research_start
        logger.info(
            "[Planning] Research phase completed in %.1fs: %d sources found",
            _elapsed,
            findings.total_sources or 0,
        )
        if _complete_fn:
            try:
                _complete_fn(_research_agent_id, error=None)
            except Exception:
                pass

        # Wrap: _format_research_context may raise if synthesis fields are unexpected types
        try:
            return _format_research_context(findings, prior_found=prior_found)
        except Exception as fmt_exc:
            logger.warning("[Planning] Failed to format research context: %s", fmt_exc)
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

    def sync_live_params(self) -> None:
        """Sync live-editable params from disk into in-memory state."""
        self.state.sync_live_params()

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
        cycle_budget: int = MissionConfig.DEFAULT_CYCLE_BUDGET,
        project_name: str = None
    ) -> None:
        """Set a new mission with optional cycle budget for multi-cycle execution.

        Validates all parameters via MissionConfig before creating any state.
        Raises MissionValidationError for invalid params (cycle_budget=0, etc.).

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

        # Build and validate config via canonical MissionConfig
        raw_params = {
            "problem_statement": problem_statement,
            "cycle_budget": cycle_budget,
            "preferences": preferences or {},
            "success_criteria": success_criteria or [],
            "project_name": project_name,
        }
        config, audit = MissionConfig.from_request(raw_params, mission_id=mid)
        # from_request resolves llm_provider from env automatically

        # Resolve project name for shared workspace
        resolved_project_name = None
        if resolve_project_name is not None:
            resolved_project_name = resolve_project_name(problem_statement, mid, config.project_name)
            mission_workspace = WORKSPACE_DIR / resolved_project_name / mid
            logger.info(f"Resolved project name: {resolved_project_name}")
        else:
            mission_workspace = MISSIONS_DIR / mid / "workspace"

        # Defence-in-depth: ensure resolved workspace stays within expected base dir
        mission_workspace_resolved = mission_workspace.resolve()
        if resolve_project_name is not None:
            try:
                mission_workspace_resolved.relative_to(WORKSPACE_DIR.resolve())
            except ValueError:
                raise ValueError(
                    f"Resolved workspace escapes WORKSPACE_DIR: {mission_workspace_resolved!r}"
                )
        else:
            try:
                mission_workspace_resolved.relative_to(MISSIONS_DIR.resolve())
            except ValueError:
                raise ValueError(
                    f"Resolved workspace escapes MISSIONS_DIR: {mission_workspace_resolved!r}"
                )

        # Create mission directory (for config, analytics, drift validation)
        mission_dir = MISSIONS_DIR / mid
        mission_dir.mkdir(parents=True, exist_ok=True)

        # Create workspace directories (may already exist if shared project)
        (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
        (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

        logger.info(f"Mission workspace at {mission_workspace}")

        # Build mission dict from canonical config
        new_mission = config.to_mission_dict(
            mission_id=mid,
            mission_workspace=mission_workspace,
            mission_dir=mission_dir,
            resolved_project_name=resolved_project_name,
            audit=audit,
        )
        self.state.mission = new_mission
        self.save_mission()

        # Save compact mission_config.json in mission directory
        mission_config_path = mission_dir / "mission_config.json"
        config_dict = config.to_config_dict(
            mission_id=mid,
            mission_workspace=mission_workspace,
            resolved_project_name=resolved_project_name,
            created_at=new_mission["created_at"],
        )
        with open(mission_config_path, 'w') as f:
            json.dump(config_dict, f, indent=2)

        # Write parameter audit log
        save_audit_log(audit, mission_dir)

        # Auto-prune: keep at most 200 missions' audit logs (non-fatal)
        try:
            prune_old_audit_logs(
                mission_dir.parent,
                max_missions=200,
                max_total_mb=500.0,
                dry_run=False,
            )
        except Exception as _prune_exc:
            logger.warning("prune_old_audit_logs failed (non-fatal): %s", _prune_exc)

        logger.info(
            f"New mission set with {config.cycle_budget} cycles "
            f"(submitted={cycle_budget}): {problem_statement[:100]}..."
        )

        # AtlasForge Enhancement: Set baseline fingerprint for mission continuity tracking
        try:
            from atlasforge_enhancements import AtlasForgeEnhancer
            enhancer = AtlasForgeEnhancer(
                mission_id=mid,
                storage_base=mission_workspace / "af_data",
                llm_provider=config.llm_provider,
            )
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
                "cycle_budget": config.cycle_budget,
                "llm_provider": config.llm_provider,
                "mission_workspace": str(mission_workspace),
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
            "reset_at": datetime.now().isoformat(),
            "llm_provider": self.mission.get("llm_provider", "claude"),
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

            # Build and validate config via canonical MissionConfig
            config, audit = MissionConfig.from_queue_item(queue_item, mission_id=mission_id)
            problem_statement = config.problem_statement
            user_project_name = config.project_name

            # Resolve project name for shared workspace
            resolved_project_name = None
            if PROJECT_NAME_RESOLVER_AVAILABLE and resolve_project_name:
                resolved_project_name = resolve_project_name(problem_statement, mission_id, user_project_name)
                mission_workspace = WORKSPACE_DIR / resolved_project_name / mission_id
                logger.info(f"Queue mission resolved project name: {resolved_project_name}")
            else:
                mission_workspace = MISSIONS_DIR / mission_id / "workspace"

            # Defence-in-depth: ensure resolved workspace stays within expected base dir
            mission_workspace_resolved = mission_workspace.resolve()
            if resolved_project_name is not None:
                try:
                    mission_workspace_resolved.relative_to(WORKSPACE_DIR.resolve())
                except ValueError:
                    raise ValueError(
                        f"Resolved workspace escapes WORKSPACE_DIR: {mission_workspace_resolved!r}"
                    )
            else:
                try:
                    mission_workspace_resolved.relative_to(MISSIONS_DIR.resolve())
                except ValueError:
                    raise ValueError(
                        f"Resolved workspace escapes MISSIONS_DIR: {mission_workspace_resolved!r}"
                    )

            # Create mission directory (for config, analytics, drift validation)
            mission_dir = MISSIONS_DIR / mission_id
            mission_dir.mkdir(parents=True, exist_ok=True)

            # Create workspace directories (may already exist if shared project)
            (mission_workspace / "artifacts").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "research").mkdir(parents=True, exist_ok=True)
            (mission_workspace / "tests").mkdir(parents=True, exist_ok=True)

            # Build mission dict from canonical config
            new_mission = config.to_mission_dict(
                mission_id=mission_id,
                mission_workspace=mission_workspace,
                mission_dir=mission_dir,
                resolved_project_name=resolved_project_name,
                source_queue_item_id=queue_item.get("id"),
                source_recommendation_id=queue_item.get("recommendation_id"),
                audit=audit,
            )

            # Save mission state with return value check
            success = io_utils.atomic_write_json(MISSION_PATH, new_mission)
            if not success:
                logger.error(f"Failed to write new mission {mission_id} to disk")
                return False

            # Save compact mission_config.json in mission directory
            mission_config_path = mission_dir / "mission_config.json"
            config_dict = config.to_config_dict(
                mission_id=mission_id,
                mission_workspace=mission_workspace,
                resolved_project_name=resolved_project_name,
                source_queue_item_id=queue_item.get("id"),
                created_at=new_mission["created_at"],
            )
            with open(mission_config_path, 'w') as f:
                json.dump(config_dict, f, indent=2)

            # Write parameter audit log
            save_audit_log(audit, mission_dir)

            # Auto-prune: keep at most 200 missions' audit logs (non-fatal)
            try:
                prune_old_audit_logs(
                    mission_dir.parent,
                    max_missions=200,
                    max_total_mb=500.0,
                    dry_run=False,
                )
            except Exception as _prune_exc:
                logger.warning("prune_old_audit_logs failed (non-fatal): %s", _prune_exc)

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
