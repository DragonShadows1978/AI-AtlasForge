"""
af_engine.stages.analyzing - Analyzing Stage Handler

This stage evaluates test results and decides next steps.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List

from .base import (
    BaseStageHandler,
    StageContext,
    StageResult,
    StageRestrictions,
)
from ..integrations.base import Event, StageEvent

logger = logging.getLogger(__name__)


class AnalyzingStageHandler(BaseStageHandler):
    """
    Handler for the ANALYZING stage.

    The ANALYZING stage:
    - Evaluates test results
    - Decides next steps (COMPLETE, BUILDING, or PLANNING)
    - Only allows writes to research/ and artifacts/
    - Transitions to CYCLE_END on success
    """

    stage_name = "ANALYZING"
    valid_from_stages = ["TESTING", "PLANNING"]

    def get_prompt(self, context: StageContext) -> str:
        """Generate the ANALYZING stage prompt."""
        # Get analyzing restrictions
        guard_prompt = self._get_guard_prompt()
        research_dir = context.research_dir
        artifacts_dir = context.artifacts_dir

        red_team_report_path = f"{artifacts_dir}/../tests/Red_Team/report.md"
        impl_plan_path = f"{artifacts_dir}/implementation_plan.md"

        return f"""
{guard_prompt}

=== ANALYZING STAGE ===
Your goal: Evaluate results, enforce scope gates, and generate only justified continuation missions.

IMPORTANT: In ANALYZING stage, only write to research/ or artifacts/.
Do NOT fix bugs here. If fixes are needed, recommend BUILDING stage.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP A — READ ALL ARTIFACTS (required before any decision)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Read every file listed here. If a file is absent, note the gap and continue.

1. {artifacts_dir}/test_results.md — test pass/fail summary
2. {red_team_report_path} — Red Team bug report (REQUIRED if exists)
   Also read individual agent findings: {artifacts_dir}/../tests/Red_Team/agent*_findings.md
3. {impl_plan_path} — original implementation scope (which components are IN-SCOPE)
4. {artifacts_dir}/bug_fix_plan.md — previously scheduled fixes
5. {artifacts_dir}/../research/analysis.md — prior analysis notes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP B — IN-SCOPE BUG GATE (evaluate BEFORE generating missions)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Using implementation_plan.md, identify which components are IN-SCOPE for this mission.

For each Red Team finding:
  - Is the affected file/component listed in implementation_plan.md? → IN-SCOPE
  - Is it in a different component not listed? → OUT-OF-SCOPE

Gate rules for IN-SCOPE bugs:
  - CRITICAL or HIGH → status="needs_revision", recommendation="BUILDING" immediately.
    Do NOT generate continuation missions. The mission is not done.
  - MODERATE or LIGHT → status="needs_revision", recommendation="BUILDING".
    List exact bugs in proposed_fixes. Mission must be clean before advancing.
  - NONBLOCKING → log only, does not block advancement.

If ANY in-scope CRITICAL/HIGH/MODERATE/LIGHT bugs remain unfixed → reject to BUILDING.
Only proceed to Step C if all in-scope bugs are fixed or marked NONBLOCKING.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP B2 — SPEC AUTHORITY / FALSE-PREMISE GATE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The mission statement and implementation_plan.md are authoritative.
Industry standard, conventional practice, performance convenience, or "good enough"
are NOT valid reasons to override explicit mission requirements.

Required:
  - Extract every hard requirement from the mission and plan, especially MUST,
    ONLY, NEVER, DO NOT, no/without, exact numbers, protected files, and named
    algorithms.
  - Treat implementation_plan.md as the executable contract for what BUILDING
    was supposed to implement. Plan steps and success criteria are not advisory.
  - For each hard requirement, provide concrete evidence from code, tests, or
    artifacts. Do not trust a reported metric if the implementation could have
    computed it from a proxy or counter.
  - If a requirement is only approximately satisfied, mark it unmet unless the
    mission explicitly permits approximation.
  - If implementation uses an alternative because it is an industry standard,
    classify that as a spec deviation. Return needs_revision unless the original
    mission explicitly authorized that alternative.
  - Look for false-premise success: counters, summaries, or tests that measure
    the intended behavior while code still performs forbidden work.

If any hard requirement is unmet or unverifiable:
  - status="needs_revision"
  - recommendation="BUILDING"
  - issues_found must name the violated requirement and the evidence gap.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
STEP C — SUGGESTION DECISION GATE (only when gate passes)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Before creating any continuation mission, classify whether a follow-up is actually warranted.
Do not create continuation missions just because the mission ended.

Source mission execution profile: {context.mission.get("mission_type", "full_rd")}

Decision rules:
  - BUGFIX and TECH_DEBT findings that are real and not fixed must become open follow-up missions.
  - HIGH and CRITICAL findings are blockers regardless of scope. If they cannot be fixed inside the current mission, create BUGFIX follow-up missions.
  - MODERATE/LIGHT bugs and tech debt should still be recorded as BUGFIX or TECH_DEBT follow-up missions when unfixed.
  - FULL R&D missions may include forward-looking idea/expansion proposals after required bugs/debt/completion work.
  - Bug fix, build-only, test/red-team, review-existing, and research-only missions must not invent expansion ideas by default.
  - PLAN ONLY missions create exactly one BUILD follow-up from implementation_plan.md. That follow-up requires explicit user build approval.

Generate missions in priority order only after applying those rules. Each category is a SEPARATE mission.

CATEGORY 1 — BUGFIX (out-of-scope bugs, MANDATORY if any exist)
  - Group out-of-scope bugs by component. One mission per component.
  - List every bug with exact file + line number in source_bugs.
  - is_pure_bugfix: true — no new features allowed in these missions.
  - Title: "[BUGFIX] {{component}} — {{N}} bugs ({{severities}})"

CATEGORY 2 — TECH_DEBT (out-of-scope MODERATE/LIGHT bugs, lower priority)
  - Same as Category 1 but for lower-severity out-of-scope findings.
  - Title: "[TECH DEBT] {{component}} — {{N}} issues"

CATEGORY 3 — COMPLETION (if implementation_plan.md items were not finished)
  - One mission per unfinished scope item.
  - Title: "[COMPLETE] {{feature or component that was not finished}}"
  - For PLAN ONLY missions, this is the single "[BUILD] ..." follow-up.
  - For PLAN ONLY build follow-ups, set requires_user_build_approval=true and source_plan_path="{impl_plan_path}".

CATEGORY 4 — EXPANSION (new features, forward-looking proposals — FULL R&D ONLY)
  - Suggest what to build next based on what was successfully completed.
  - These are proposals, not open missions. Set proposal_status="proposed".
  - Title: "[EXPAND] {{area}}" or "[FEATURE] {{name}}"

RULE: If out-of-scope bugs exist, Category 1 missions MUST appear before Category 4.
      Never fold bug information into an expansion mission.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TERMINOLOGY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
- **Iteration**: One ANALYZING→BUILDING loop within a single cycle.
- **Cycle**: One complete PLANNING→BUILDING→TESTING→ANALYZING pass.
Do NOT call iterations "cycles" in reports.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RESPONSE FORMAT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Write analysis to {research_dir}/analysis.md.
Write bug fix plan to {artifacts_dir}/bug_fix_plan.md (if bugs found).

Respond with JSON:
{{
    "status": "success" | "needs_revision" | "needs_replanning",
    "analysis": "Your analysis of the results",
    "issues_found": ["list of issues, or empty"],
    "proposed_fixes": ["list of fixes if needed, or empty"],
    "recommendation": "COMPLETE" | "BUILDING" | "PLANNING",
    "red_team_findings_count": 0,
    "red_team_blocking_bugs": [],
    "spec_compliance": {{
        "hard_requirements": [
            {{
                "source": "mission|implementation_plan|test_results|artifact",
                "source_excerpt": "Exact mission/plan text being evaluated",
                "requirement": "Hard requirement in your own words",
                "status": "met|unmet|unverifiable",
                "evidence": ["file.py:123 proves X", "artifact.json field Y proves Z"],
                "deviation": null,
                "mission_authorized_deviation": false
            }}
        ],
        "all_hard_requirements_met": true,
        "unmet_requirements": [],
        "unverifiable_requirements": [],
        "unauthorized_deviations": []
    }},
    "suggestion_decision": {{
        "generate_ideas": false,
        "generate_bugfixes": false,
        "generate_tech_debt": false,
        "reason": "Why continuation missions are or are not warranted",
        "critical_findings": [],
        "high_findings": []
    }},
    "continuation_missions": [
        {{
            "priority": 1,
            "category": "BUGFIX",
            "severity": "HIGH",
            "is_pure_bugfix": true,
            "title": "[BUGFIX] component — N bugs (HIGH/MODERATE)",
            "description": "Detailed description of what to fix and why",
            "rationale": "Out-of-scope bugs found by Red Team in component X",
            "source_bugs": ["path/to/file.py:240", "path/to/file.py:265"],
            "proposal_status": "open",
            "requires_user_build_approval": false,
            "blocks": []
        }}
    ],
    "message_to_human": "Analysis summary"
}}

Notes on continuation_missions:
- Include this field ONLY when status=success (gate passed).
- If no bugs, no tech debt, and no unfinished scope, use [] unless this is a FULL R&D mission with useful idea proposals.
- If no follow-up is needed at all, use an empty list [].
- BUGFIX/TECH_DEBT missions must have is_pure_bugfix=true.
- EXPANSION missions have is_pure_bugfix=false and proposal_status="proposed".

If recommending COMPLETE, also include:
{{
    ...
    "final_report": "Summary of what was accomplished",
    "deliverables": ["list of files/artifacts produced"]
}}
"""

    def process_response(
        self,
        response: Dict[str, Any],
        context: StageContext
    ) -> StageResult:
        """
        Process the ANALYZING stage response.

        Determines next stage based on recommendation.
        """
        # Normalize status for case-insensitive matching
        raw_status = response.get("status", "")
        status = raw_status.lower().strip() if isinstance(raw_status, str) else ""
        raw_rec = response.get("recommendation", "")
        recommendation = raw_rec.upper().strip() if isinstance(raw_rec, str) else ""

        # Log unrecognized status values for debugging
        valid_statuses = ["success", "needs_revision", "needs_replanning", "complete", "mission_complete", "done", "finished"]
        if status and status not in valid_statuses:
            logger.warning(f"ANALYZING: Unrecognized status '{raw_status}' (normalized: '{status}')")

        integrity_blockers = self._spec_integrity_blockers(response, context)
        if integrity_blockers and (status in {"success", "complete", "mission_complete", "done", "finished"} or recommendation == "COMPLETE"):
            issues = list(response.get("issues_found", []) or [])
            fixes = list(response.get("proposed_fixes", []) or [])
            for blocker in integrity_blockers:
                if blocker not in issues:
                    issues.append(blocker)
            if "Align implementation with explicit mission requirements; do not justify deviations as industry standard." not in fixes:
                fixes.append("Align implementation with explicit mission requirements; do not justify deviations as industry standard.")
            response = {
                **response,
                "status": "needs_revision",
                "recommendation": "BUILDING",
                "issues_found": issues,
                "proposed_fixes": fixes,
                "message_to_human": (
                    "Spec integrity gate rejected completion: "
                    + "; ".join(integrity_blockers[:2])
                ),
            }
            status = "needs_revision"
            recommendation = "BUILDING"

        events = [
            Event(
                type=StageEvent.STAGE_COMPLETED,
                stage=self.stage_name,
                mission_id=context.mission_id,
                data={
                    "status": status,
                    "recommendation": recommendation,
                    "issues_found": response.get("issues_found", []),
                }
            )
        ]

        failure_signals = ["needs_revision"]
        replanning_signals = ["needs_replanning"]
        success_signals = ["success", "complete", "mission_complete", "done", "finished"]

        # IMPORTANT: failure/replanning checks MUST come before success/recommendation checks.
        # A response with status='needs_revision' + recommendation='COMPLETE' must route to
        # BUILDING — the status field takes priority over recommendation.
        if recommendation == "BUILDING" or status in failure_signals:
            # Need to make fixes - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="BUILDING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs revision, returning to building")
            )

        elif status in replanning_signals or (recommendation == "PLANNING" and status not in success_signals):
            # Need to revise the plan - signal orchestrator to increment iteration
            return StageResult(
                success=True,
                next_stage="PLANNING",
                status=status,
                output_data={**response, "_increment_iteration": True},
                events_to_emit=events,
                message=response.get("message_to_human", "Needs replanning")
            )

        elif status in success_signals or recommendation == "COMPLETE":
            # Write continuation_missions.json manifest to artifacts/
            continuation_missions = response.get("continuation_missions", [])
            if isinstance(continuation_missions, list):
                continuation_missions = self._filter_continuation_missions(continuation_missions, response, context)
                self._write_continuation_manifest(continuation_missions, context)
            else:
                logger.warning("ANALYZING: continuation_missions is not a list, skipping manifest write")

            # Go to CYCLE_END instead of COMPLETE to handle cycle iteration
            return StageResult(
                success=True,
                next_stage="CYCLE_END",
                status=status,
                output_data={**response, "continuation_missions": continuation_missions},
                events_to_emit=events,
                message=response.get("message_to_human", "Analysis complete, moving to cycle end")
            )

        else:
            # Default to CYCLE_END
            logger.warning(f"ANALYZING: Unexpected status '{status}', defaulting to CYCLE_END")
            return StageResult(
                success=True,
                next_stage="CYCLE_END",
                status=status,
                output_data=response,
                events_to_emit=events,
                message="Analysis complete"
            )

    def _write_continuation_manifest(self, missions: list, context: StageContext) -> None:
        """Write continuation_missions.json to artifacts/ for CYCLE_END to consume."""
        try:
            artifacts_dir = Path(context.artifacts_dir)
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            manifest = {
                "generated_at": datetime.now().isoformat(),
                "source_mission": context.mission_id,
                "missions": missions,
            }
            manifest_path = artifacts_dir / "continuation_missions.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)
            logger.info(f"ANALYZING: Wrote continuation manifest with {len(missions)} missions to {manifest_path}")
        except Exception as e:
            logger.error(f"ANALYZING: Failed to write continuation_missions.json: {e}")

    def _source_profile(self, context: StageContext) -> str:
        raw = context.mission.get("mission_type") or context.mission.get("execution_profile")
        valid = {
            "full_rd", "plan_only", "build_only", "test_red_team",
            "bug_hunt", "research_only", "review_existing",
        }
        return raw if isinstance(raw, str) and raw in valid else "full_rd"

    def _filter_continuation_missions(
        self,
        missions: List[Dict[str, Any]],
        response: Dict[str, Any],
        context: StageContext,
    ) -> List[Dict[str, Any]]:
        """Hard gate continuation missions so prompt drift cannot flood suggestions."""
        profile = self._source_profile(context)
        filtered: List[Dict[str, Any]] = []
        seen_build_followup = False
        for item in missions:
            if not isinstance(item, dict):
                continue
            category = str(item.get("category") or item.get("classification") or "EXPANSION").upper().strip()
            if category in {"BUG", "BUGS"}:
                category = "BUGFIX"
            elif category in {"DEBT", "TECHDEBT", "TECH-DEBT"}:
                category = "TECH_DEBT"
            elif category in {"FEATURE", "IDEA"}:
                category = "EXPANSION"
            if category not in {"BUGFIX", "TECH_DEBT"} and item.get("source_bugs"):
                category = "BUGFIX"
            item["category"] = category

            if category in {"BUGFIX", "TECH_DEBT"}:
                item["proposal_status"] = "open"
                item["is_pure_bugfix"] = True
                filtered.append(item)
            elif category == "COMPLETION":
                if profile == "plan_only":
                    if seen_build_followup:
                        continue
                    seen_build_followup = True
                    item["title"] = item.get("title") or f"[BUILD] {context.problem_statement[:80]}"
                    item["requires_user_build_approval"] = True
                    item["proposal_status"] = "open"
                    item["source_plan_path"] = str(Path(context.artifacts_dir) / "implementation_plan.md")
                filtered.append(item)
            elif category == "EXPANSION" and profile == "full_rd":
                item["proposal_status"] = "proposed"
                item["is_pure_bugfix"] = False
                filtered.append(item)

        if profile == "plan_only" and not seen_build_followup:
            filtered.append({
                "priority": 1,
                "category": "COMPLETION",
                "title": f"[BUILD] {context.problem_statement[:80]}",
                "description": (
                    "Build from the implementation plan produced by this plan-only mission. "
                    "The user must explicitly approve the build or send it back for plan revision."
                ),
                "rationale": "Plan-only mission completed and requires a gated build follow-up.",
                "proposal_status": "open",
                "requires_user_build_approval": True,
                "source_plan_path": str(Path(context.artifacts_dir) / "implementation_plan.md"),
                "blocks": [],
            })

        suppressed = len(missions) - len(filtered)
        if suppressed > 0:
            logger.info("ANALYZING: suppressed %s continuation mission(s) for profile=%s", suppressed, profile)
        response["continuation_missions"] = filtered
        return filtered

    def _spec_integrity_blockers(
        self,
        response: Dict[str, Any],
        context: StageContext,
    ) -> List[str]:
        """
        Deterministic backstop for false-premise success in ANALYZING.

        The LLM still performs the broad review, but a few high-risk spec
        contracts are cheap enough to enforce here. This specifically catches
        "selective-only" missions whose code reports a selective counter while
        still computing dense full-precision scores and masking afterward.
        """
        blockers: List[str] = []
        response_text = self._stringify_response(response).lower()
        if (
            "industry standard" in response_text
            and ("spec" in response_text or "requirement" in response_text or "deviation" in response_text)
        ):
            blockers.append(
                "Spec deviation justified by industry standard; explicit mission requirements remain authoritative."
            )

        hard_requirements = self._extract_hard_requirements(context)
        if hard_requirements:
            blockers.extend(self._spec_ledger_blockers(response, hard_requirements))

        mission_text = "\n".join([
            str(context.problem_statement or ""),
            str(context.original_mission or ""),
            str(context.mission.get("problem_statement") or ""),
            self._read_text(Path(context.artifacts_dir) / "implementation_plan.md", max_chars=20000),
        ]).lower()

        selective_contract = (
            "full precision only" in mission_text
            or "full-precision dots only" in mission_text
            or "compute full-precision dots only" in mission_text
            or "no redundant computation" in mission_text
            or "no computing full precision and throwing it away" in mission_text
            or "only for the tail" in mission_text
            or "only for the top-k" in mission_text
        )
        if selective_contract:
            dense_hits = self._dense_full_precision_mask_hits(context)
            if dense_hits:
                sample = "; ".join(dense_hits[:3])
                blockers.append(
                    "Selective/full-precision-only requirement is not proven: code appears to compute dense "
                    f"full-precision score matrices before masking ({sample})."
                )

        return blockers

    def _spec_ledger_blockers(
        self,
        response: Dict[str, Any],
        hard_requirements: List[Dict[str, str]],
    ) -> List[str]:
        spec = response.get("spec_compliance")
        if not isinstance(spec, dict):
            return [
                "Missing spec_compliance hard-requirement ledger for mission/implementation plan requirements."
            ]

        ledger = spec.get("hard_requirements")
        if not isinstance(ledger, list) or not ledger:
            return [
                "Empty spec_compliance.hard_requirements ledger; completion requires evidence for every hard requirement."
            ]

        blockers: List[str] = []
        bad_statuses = []
        unauthorized = []
        covered = set()
        for index, item in enumerate(ledger):
            if not isinstance(item, dict):
                bad_statuses.append(f"ledger[{index}] is not an object")
                continue
            status = str(item.get("status") or "").lower().strip()
            if status not in {"met", "pass", "passed", "satisfied"}:
                bad_statuses.append(
                    f"{item.get('source_excerpt') or item.get('requirement') or f'ledger[{index}]'} => {status or 'missing status'}"
                )
            deviation = item.get("deviation")
            if deviation and not item.get("mission_authorized_deviation"):
                unauthorized.append(str(item.get("source_excerpt") or item.get("requirement") or deviation))
            evidence = item.get("evidence")
            if not evidence:
                bad_statuses.append(
                    f"{item.get('source_excerpt') or item.get('requirement') or f'ledger[{index}]'} => missing evidence"
                )

            ledger_text = " ".join([
                str(item.get("source_excerpt") or ""),
                str(item.get("requirement") or ""),
            ])
            for req_index, req in enumerate(hard_requirements):
                if req_index not in covered and self._requirement_matches(req["text"], ledger_text):
                    covered.add(req_index)

        if spec.get("all_hard_requirements_met") is not True:
            blockers.append("spec_compliance.all_hard_requirements_met is not true.")
        for field in ("unmet_requirements", "unverifiable_requirements", "unauthorized_deviations"):
            values = spec.get(field)
            if isinstance(values, list) and values:
                blockers.append(f"spec_compliance.{field} is non-empty: {values[:3]}")

        if bad_statuses:
            blockers.append(f"Hard requirement ledger has unmet/unverifiable/unevidenced entries: {bad_statuses[:5]}")
        if unauthorized:
            blockers.append(f"Unauthorized spec deviations present: {unauthorized[:5]}")

        missing = [
            req for index, req in enumerate(hard_requirements)
            if index not in covered
        ]
        if missing:
            samples = [f"{req['source']}: {req['text']}" for req in missing[:5]]
            blockers.append(f"Spec ledger does not cover extracted hard requirements: {samples}")

        return blockers

    def _extract_hard_requirements(self, context: StageContext) -> List[Dict[str, str]]:
        sources = [
            ("mission", str(context.problem_statement or "")),
            ("original_mission", str(context.original_mission or "")),
            ("implementation_plan", self._read_text(Path(context.artifacts_dir) / "implementation_plan.md", max_chars=50000)),
        ]
        requirements: List[Dict[str, str]] = []
        seen = set()
        for source, text in sources:
            section = ""
            for raw_line in text.splitlines():
                line = self._clean_requirement_line(raw_line)
                if not line:
                    continue
                lower = line.lower()
                if lower.startswith("#"):
                    section = lower.strip("# ").strip()
                    continue
                if self._is_hard_requirement_line(line, section):
                    key = self._normalize_requirement(line)
                    if key and key not in seen:
                        requirements.append({"source": source, "text": line})
                        seen.add(key)
        return requirements[:60]

    @staticmethod
    def _clean_requirement_line(line: str) -> str:
        cleaned = line.strip()
        cleaned = re.sub(r"^[-*]\s+", "", cleaned)
        cleaned = re.sub(r"^\d+[.)]\s+", "", cleaned)
        cleaned = cleaned.strip()
        if len(cleaned) < 8:
            return ""
        if cleaned in {"---", "```", "```json"}:
            return ""
        return cleaned

    @classmethod
    def _is_hard_requirement_line(cls, line: str, section: str) -> bool:
        lower = line.lower()
        hard_markers = (
            "must", "only", "never", "do not", "don't", "without",
            "no ", "exact", "all ", "every ", "required", "success criteria",
            "output:", "produce", "write ", "create ", "build ", "implement ",
            "protected", "forbidden", "in-scope", "out-of-scope",
        )
        hard_sections = (
            "success criteria", "requirements", "what to build", "steps",
            "deliverables", "scope", "implementation requirements",
        )
        return any(marker in lower for marker in hard_markers) or any(name in section for name in hard_sections)

    @staticmethod
    def _normalize_requirement(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9_]+", text.lower()))

    @classmethod
    def _requirement_matches(cls, requirement: str, ledger_text: str) -> bool:
        req_norm = cls._normalize_requirement(requirement)
        led_norm = cls._normalize_requirement(ledger_text)
        if not req_norm or not led_norm:
            return False
        if req_norm in led_norm or led_norm in req_norm:
            return True
        req_tokens = {t for t in req_norm.split() if len(t) > 2}
        led_tokens = {t for t in led_norm.split() if len(t) > 2}
        if not req_tokens or not led_tokens:
            return False
        overlap = len(req_tokens & led_tokens)
        return overlap / max(len(req_tokens), 1) >= 0.55

    def _dense_full_precision_mask_hits(self, context: StageContext) -> List[str]:
        workspace = Path(context.workspace_dir).resolve()
        if not workspace.exists():
            return []

        hits: List[str] = []
        dense_score_re = re.compile(
            r"\b(full_scores|full_sc|ranking_scores|scores_full)\s*="
            r".*(einsum|matmul|@).*(key_data|key\b|K\b)",
            re.IGNORECASE,
        )
        mask_blend_re = re.compile(
            r"where\s*\(\s*(refine_mask|tail_mask|r_mask)\s*,\s*"
            r"(full_scores|full_sc|ranking_scores|scores_full)",
            re.IGNORECASE,
        )

        for path in self._candidate_code_files(context):
            try:
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            if not mask_blend_re.search(text):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if dense_score_re.search(line):
                    hits.append(f"{path.relative_to(workspace)}:{lineno}")
                    break

        return hits

    def _candidate_code_files(self, context: StageContext) -> List[Path]:
        workspace = Path(context.workspace_dir).resolve()
        plan_text = self._read_text(Path(context.artifacts_dir) / "implementation_plan.md", max_chars=50000)
        candidates: List[Path] = []
        seen = set()
        for match in re.finditer(r"[\w./-]+\.py\b", plan_text):
            raw = match.group(0).strip("./")
            path = (workspace / raw).resolve()
            try:
                path.relative_to(workspace)
            except ValueError:
                continue
            if path.exists() and path.is_file() and path not in seen:
                candidates.append(path)
                seen.add(path)

        if candidates:
            return candidates

        return [
            path for path in workspace.rglob("*.py")
            if "__pycache__" not in path.parts and path.is_file()
        ]

    @staticmethod
    def _read_text(path: Path, max_chars: int = 10000) -> str:
        try:
            return path.read_text(errors="ignore")[:max_chars]
        except OSError:
            return ""

    @staticmethod
    def _stringify_response(response: Dict[str, Any]) -> str:
        try:
            return json.dumps(response, default=str)
        except (TypeError, ValueError):
            return str(response)

    def get_restrictions(self) -> StageRestrictions:
        """
        Get ANALYZING stage restrictions.

        Only allows writes to research/ and artifacts/.
        Matches legacy init_guard.py STAGE_POLICIES["ANALYZING"].
        """
        return StageRestrictions(
            allowed_tools=[
                "Read", "Glob", "Grep", "Write", "Edit",
                "WebFetch", "WebSearch", "Task"
            ],
            blocked_tools=[],
            allowed_write_paths=[
                "*/artifacts/*",
                "*/research/*",
                "*analysis.md",
                "*report.md",
                "*test_results.md"
            ],
            forbidden_write_paths=["*.py", "*.js", "*.ts"],
            allow_bash=False,
            read_only=False
        )

    def _get_guard_prompt(self) -> str:
        """Get the InitGuard prompt for analyzing restrictions."""
        try:
            from init_guard import InitGuard
            return InitGuard.get_analyzing_system_prompt()
        except ImportError:
            return ""
