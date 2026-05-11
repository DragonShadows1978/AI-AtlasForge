"""
af_engine.stages.planning - Planning Stage Handler

This stage handles mission understanding and implementation planning.
It injects KB context and AfterImage code memory for informed planning.
"""

import logging
import json
import re as _re
import unicodedata as _unicodedata
from pathlib import Path
from typing import Dict, Any, List, Optional

from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)

_INJECTION_PREFIXES = (
    "IGNORE", "<system>", "[INST]", "[/INST]", "###OVERRIDE", "SYSTEM:", "</s>",
    "<|im_start|>", "<|im_end|>", "<!--", "ASSISTANT:", "USER:", "HUMAN:",
)


def _sanitize_resumption_content(text: str, max_len: int = 8000) -> str:
    """Strip prompt-injection attempts from resumption file content before LLM embedding."""
    if not text:
        return ""
    if not isinstance(text, str):
        text = repr(text)
    # NFKC normalization converts fullwidth/halfwidth variants to canonical ASCII.
    text = _unicodedata.normalize('NFKC', text)
    # Delete ZWNJ (U+200C) and ZWJ (U+200D) — they survive NFKC and can be
    # inserted between prefix letters to bypass prefix detection.
    text = text.replace('‌', '').replace('‍', '')
    # Strip combining diacritics, variation selectors (U+FE00–FE1F), AND Unicode
    # tag characters (U+E0000–U+E01EF) — all survive NFKC and can camouflage tokens.
    text = _re.sub(r'[̀-ͯ᷀-᷿⃐-⃿︀-︟︠-︯\U000E0000-\U000E01EF]', '', text)
    # Replace control chars with spaces to prevent token concatenation.
    text = _re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', ' ', text)
    clean_lines = []
    _prefixes_upper = tuple(p.upper() for p in _INJECTION_PREFIXES)
    for line in text.splitlines():
        # Remove Unicode invisible/format chars and collapse whitespace
        stripped = _re.sub(r'[­͏؜ᅟᅠ឴឵'
                           r'᠋-᠎​-‏‪-‮'
                           r'⁠-⁯ㅤ﻿ﾠ]', ' ', line.lstrip())
        stripped = _re.sub(r'  +', ' ', stripped).strip()
        upper = stripped.upper()
        # Drop lines where an injection prefix appears at start-of-line
        # (case-insensitive) OR anywhere mid-line (case-insensitive, word boundary).
        if any(upper.startswith(p) for p in _prefixes_upper):
            continue
        if any(
            bool(_re.search(r'(?<![a-zA-Z0-9])' + _re.escape(p), stripped, _re.IGNORECASE))
            for p in _INJECTION_PREFIXES
        ):
            continue
        clean_lines.append(stripped)
    try:
        limit = max(0, int(max_len))
    except (TypeError, ValueError, OverflowError):
        limit = 8000
    return "\n".join(clean_lines)[:limit]


def _as_list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _render_bullets(values: Any) -> str:
    items = [str(item).strip() for item in _as_list(values) if str(item).strip()]
    return "\n".join(f"- {item}" for item in items) if items else "- None recorded."


def _render_planning_artifact(response: Dict[str, Any]) -> str:
    steps = []
    for idx, step in enumerate(_as_list(response.get("steps")), start=1):
        if isinstance(step, dict):
            desc = str(step.get("description") or step.get("step") or "Planned work").strip()
            files = _as_list(step.get("files"))
            file_note = f" Files: {', '.join(str(f) for f in files)}." if files else ""
            steps.append(f"{idx}. {desc}.{file_note}")
        else:
            steps.append(f"{idx}. {str(step).strip()}")

    return "\n".join([
        "# Implementation Plan",
        "",
        "## Understanding",
        str(response.get("understanding") or "Mission requirements understood from planning response.").strip(),
        "",
        "## Approach",
        str(response.get("approach") or "Use the planned incremental implementation approach.").strip(),
        "",
        "## Rationale",
        str(response.get("approach_rationale") or "Generated from the PLANNING response after direct artifact writes were unavailable.").strip(),
        "",
        "## Requirements",
        _render_bullets(response.get("key_requirements")),
        "",
        "## Steps",
        "\n".join(steps) if steps else "1. Implement the scoped mission work described in the planning response.",
        "",
        "## Estimated Files",
        _render_bullets(response.get("estimated_files")),
        "",
        "## Success Criteria",
        _render_bullets(response.get("success_criteria")),
        "",
        "## Assumptions",
        _render_bullets(response.get("assumptions")),
        "",
        "## Structured Planning Response",
        "",
        "```json",
        json.dumps(response, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ])


def _render_research_artifact(response: Dict[str, Any]) -> str:
    research_summary = response.get("research_summary")
    if not isinstance(research_summary, dict):
        research_summary = {}

    return "\n".join([
        "# Research Findings",
        "",
        "This artifact was materialized by AtlasForge from the PLANNING response because the planning agent completed but did not leave the required research file.",
        "",
        "## Research Conducted",
        _render_bullets(response.get("research_conducted")),
        "",
        "## Sources Consulted",
        _render_bullets(response.get("sources_consulted")),
        "",
        "## Topics Researched",
        _render_bullets(research_summary.get("topics_researched")),
        "",
        "## Key Findings",
        _render_bullets(research_summary.get("key_findings")),
        "",
        "## Knowledge Gaps",
        _render_bullets(research_summary.get("knowledge_gaps_identified")),
        "",
        "## KB Learnings Applied",
        _render_bullets(response.get("kb_learnings_applied")),
        "",
        "## Confidence",
        str(research_summary.get("confidence_level") or "not specified"),
        "",
        "## Structured Research Summary",
        "",
        "```json",
        json.dumps(research_summary or response, indent=2, sort_keys=True, default=str),
        "```",
        "",
    ])


def _ensure_planning_artifacts_from_response(response: Dict[str, Any], context: StageContext) -> None:
    """Materialize required planning artifacts when the LLM returned valid plan data.

    Codex stages may run in a read-only filesystem sandbox and submit artifacts
    through MCP. If that MCP path is cancelled or unavailable, the conductor can
    still persist the structured response it received instead of retrying
    PLANNING forever.
    """
    if context.research_dir:
        findings_path = Path(context.research_dir) / "research_findings.md"
        if not findings_path.exists() or findings_path.stat().st_size < 500:
            findings_path.parent.mkdir(parents=True, exist_ok=True)
            findings_path.write_text(_render_research_artifact(response), encoding="utf-8")
            logger.info("PLANNING materialized missing research artifact: %s", findings_path)

    if context.artifacts_dir:
        plan_path = Path(context.artifacts_dir) / "implementation_plan.md"
        if not plan_path.exists() or plan_path.stat().st_size < 200:
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_text(_render_planning_artifact(response), encoding="utf-8")
            logger.info("PLANNING materialized missing implementation plan artifact: %s", plan_path)


class PlanningStageHandler(BaseStageHandler):
    """
    Handler for the PLANNING stage.

    The PLANNING stage:
    - Combines mission understanding with plan creation
    - Injects Knowledge Base context for past learnings
    - Injects AfterImage code memory for similar past code
    - Only allows writes to artifacts/ and research/
    - Transitions to BUILDING when plan is complete
    """

    stage_name = "PLANNING"
    valid_from_stages = ["CYCLE_END", "ANALYZING", "PLANNING", None]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the PLANNING stage prompt."""
        # Build prompt components
        guard_prompt = self._get_guard_prompt()
        # Sanitize KB/AfterImage context before embedding — these fields originate from
        # external systems and may contain adversarial prompt-injection tokens.
        kb_context = _sanitize_resumption_content(context.kb_context or "", max_len=16000)
        afterimage_context = _sanitize_resumption_content(context.afterimage_context or "", max_len=8000)
        resumption_content = self._get_resumption_content(context)

        workspace_dir = _sanitize_resumption_content(context.workspace_dir or "", max_len=500)
        artifacts_dir = _sanitize_resumption_content(context.artifacts_dir or "", max_len=500)
        research_dir = _sanitize_resumption_content(context.research_dir or "", max_len=500)

        return f"""
{guard_prompt}
{kb_context}
{afterimage_context}
=== PLANNING STAGE ===
Your goal: Understand the mission AND create a detailed implementation plan.
{resumption_content}

IMPORTANT: You are AUTONOMOUS. Do NOT ask clarifying questions. Make reasonable assumptions and proceed.

In PLANNING stage, you may ONLY write to artifacts/ or research/ directories.
Do NOT write actual code yet. Save implementation for BUILDING stage.

=== RESEARCH PHASE ===
MANDATORY: Knowledge Base Consultation
The Knowledge Base context above (if present) contains SEMANTIC SEARCH RESULTS from past missions.
- PAY ATTENTION to "Gotchas to Avoid" — these are past failures to prevent
- Apply "Relevant Techniques" if they match your current problem

If RESEARCH FINDINGS are shown above (pre-computed automatically), your research task is
focused and specific — do NOT re-research what is already covered:

1. **Fill knowledge gaps** listed in the RESEARCH FINDINGS section above
2. **Explore the specific codebase** for implementation details:
   - Read relevant source files before designing changes
   - Verify assumptions against actual code, not training data
   - Append codebase-specific findings to {research_dir}/research_findings.md
3. If NO pre-computed research is shown above, conduct full evidence-based research:
   - Use WebSearch to find current best practices (include year in query)
   - Use WebFetch to get official documentation for relevant technologies
   - Document ALL findings in {research_dir}/research_findings.md

Every major architectural decision MUST cite either:
  (a) The pre-computed RESEARCH FINDINGS above, or
  (b) A specific source you consulted during this planning session

Decisions not backed by evidence MUST be explicitly flagged as assumptions.

=== IMPLEMENTATION PLANNING ===

Tasks (in order):
1. Read and understand the problem statement above
2. Conduct active research using WebSearch/WebFetch (see Research Phase above)
3. Explore the codebase to understand existing patterns
4. Identify key requirements and constraints
5. Make reasonable assumptions for any ambiguities
6. Break down the problem into concrete steps
7. Identify files to create/modify in {workspace_dir}/
8. Define clear success criteria
9. Consider 2-3 alternative approaches (informed by research)
10. Write research findings to {research_dir}/research_findings.md
11. Write your plan to {artifacts_dir}/implementation_plan.md

Respond with JSON:
{{
    "status": "plan_complete",
    "understanding": "Your summary of what needs to be built",
    "kb_learnings_applied": ["list any KB learnings you incorporated, or empty if none"],
    "research_conducted": ["topic1: key finding", "topic2: key finding"],
    "sources_consulted": ["url1", "url2"],
    "research_summary": {{
        "topics_researched": ["topic1", "topic2"],
        "sources_consulted": 0,
        "primary_sources": 0,
        "knowledge_gaps_identified": ["gap1", "gap2"],
        "key_findings": ["finding1", "finding2"],
        "confidence_level": "high"
    }},
    "key_requirements": ["requirement1", "requirement2"],
    "assumptions": ["any assumptions you made"],
    "approach": "Brief description of chosen approach",
    "approach_rationale": "Why this approach (cite KB learnings and sources if available)",
    "steps": [
        {{"step": 1, "description": "...", "files": ["file1.py"]}},
        {{"step": 2, "description": "...", "files": ["file2.py"]}}
    ],
    "success_criteria": ["criterion1", "criterion2"],
    "estimated_files": ["list of files to create"],
    "message_to_human": "Planning complete. Ready to build."
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the PLANNING stage response.

        Transitions to BUILDING when plan is complete.
        """
        if not isinstance(response, dict):
            response = {}
        status = str(response.get("status") or "")

        if status.lower().strip() == "plan_complete":
            _ensure_planning_artifacts_from_response(response, context)

            # Enforce two-phase completion: both artifacts must exist before BUILDING
            missing = []
            if not context.research_dir:
                missing.append("research/research_findings.md (research_dir not set in context)")
            else:
                findings_path = Path(context.research_dir) / "research_findings.md"
                if not findings_path.exists():
                    missing.append("research/research_findings.md (research phase not complete)")
                elif findings_path.stat().st_size < 500:
                    missing.append(f"research/research_findings.md too small ({findings_path.stat().st_size} bytes, need ≥500)")
            if not context.artifacts_dir:
                missing.append("artifacts/implementation_plan.md (artifacts_dir not set in context)")
            else:
                plan_path = Path(context.artifacts_dir) / "implementation_plan.md"
                if not plan_path.exists():
                    missing.append("artifacts/implementation_plan.md (implementation plan not written)")
                elif plan_path.stat().st_size < 200:
                    missing.append(f"artifacts/implementation_plan.md too small ({plan_path.stat().st_size} bytes, need ≥200)")

            if missing:
                msg = "Cannot advance to BUILDING — missing required artifacts:\n" + "\n".join(f"  - {m}" for m in missing)
                logger.warning("PLANNING two-phase gate: %s", msg)
                return StageResult(
                    success=False,
                    next_stage="PLANNING",
                    status="artifacts_missing",
                    output_data=response,
                    message=msg,
                )

            # Create events for stage completion
            events = [
                Event(
                    type=StageEvent.STAGE_COMPLETED,
                    stage=self.stage_name,
                    mission_id=context.mission_id,
                    data={
                        "status": status,
                        "kb_learnings": response.get("kb_learnings_applied", []),
                        "steps_planned": len(response.get("steps") or []) if isinstance(response.get("steps") or [], (list, tuple)) else 0,
                        "research_summary": response.get("research_summary", {}),
                    }
                )
            ]

            mission_type = (context.mission or {}).get("mission_type")
            next_stage = "ANALYZING" if mission_type == "plan_only" else "BUILDING"
            default_message = (
                "Plan complete, moving to analysis for build follow-up"
                if next_stage == "ANALYZING"
                else "Plan complete, moving to building"
            )

            return StageResult(
                success=True,
                next_stage=next_stage,
                status=status,
                output_data=response,
                events_to_emit=events,
                message=response.get("message_to_human", default_message)
            )
        else:
            logger.warning(f"PLANNING: Unexpected status '{status}', staying in PLANNING")
            return StageResult(
                success=False,
                next_stage="PLANNING",
                status=status,
                output_data=response,
                message=f"Unexpected status: {status}"
            )

    def get_restrictions(self) -> StageRestrictions:
        """
        Get PLANNING stage restrictions.

        Only allows writes to artifacts/ and research/ directories.
        Matches legacy init_guard.py STAGE_POLICIES["PLANNING"].
        """
        return StageRestrictions(
            allowed_tools=[
                "Read", "Glob", "Grep", "Write", "Edit",
                "Bash", "WebFetch", "WebSearch", "Task"
            ],
            blocked_tools=["NotebookEdit"],
            allowed_write_paths=[
                "*/artifacts/*",
                "*/research/*",
                "*implementation_plan.md"
            ],
            forbidden_write_paths=["*.py", "*.js", "*.ts"],
            allow_bash=True,
            read_only=False
        )

    def _get_guard_prompt(self) -> str:
        """Get the InitGuard prompt for planning restrictions."""
        try:
            from init_guard import InitGuard
            return InitGuard.get_planning_system_prompt()
        except ImportError:
            return ""

    def _get_resumption_content(self, context: StageContext) -> str:
        """Get resumption instructions if available."""
        if context.resumption_file:
            try:
                resumption_path = Path(context.resumption_file).resolve()
                # BUG-SEC-4: enforce workspace boundary to prevent path traversal.
                # If workspace_dir is None/empty we MUST reject — skipping the check
                # when workspace is falsy would allow arbitrary filesystem reads.
                workspace = getattr(context, 'workspace_dir', None)
                if not workspace:
                    logger.warning(
                        "resumption_file rejected: workspace_dir is unset, cannot validate boundary"
                    )
                    return ""
                resolved_workspace = Path(workspace).resolve()
                # Reject workspace_dir='/' — every path resolves inside /, making the
                # boundary check meaningless and allowing arbitrary file reads.
                if str(resolved_workspace) == '/':
                    logger.warning(
                        "resumption_file rejected: workspace_dir resolves to filesystem root"
                    )
                    return ""
                # Guard against symlink-based workspace_dir pointing outside the
                # AtlasForge workspace root.  Path.resolve() follows symlinks, so a
                # symlink workspace_dir → /etc would make any /etc/… file appear
                # inside the boundary.  Anchor to this module's parent tree instead.
                _module_root = Path(__file__).resolve().parent.parent.parent  # af_engine/../.. = repo root
                if not resolved_workspace.is_relative_to(_module_root):
                    logger.warning(
                        "resumption_file rejected: workspace_dir '%s' resolves outside module root '%s'",
                        workspace, _module_root,
                    )
                    return ""
                try:
                    resumption_path.relative_to(resolved_workspace)
                except ValueError:
                    logger.warning(
                        "resumption_file outside mission workspace rejected: %s",
                        context.resumption_file,
                    )
                    return ""
                if resumption_path.exists() and resumption_path.is_file():
                    content = resumption_path.read_text()
                    # BUG-SEC-4: sanitize content before embedding in LLM prompt
                    content = _sanitize_resumption_content(content, max_len=8000)
                    return f"""
=== RESUMPTION INSTRUCTIONS ===
{content}
=== END RESUMPTION ===
"""
            except Exception as e:
                logger.warning(f"Failed to read resumption file: {e}")
        return ""
