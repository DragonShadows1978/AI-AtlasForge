"""
Runner-owned TESTING pipeline for AtlasForge.

The stage agent does not launch workers itself. It produces a test plan, then
Python launches the planned lanes and aggregates their artifacts into the
strict TESTING-stage JSON consumed by the normal stage handler.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from investigation_engine import ModelType, invoke_claude
except Exception:  # pragma: no cover - import failure is surfaced at runtime
    ModelType = None  # type: ignore
    invoke_claude = None  # type: ignore

logger = logging.getLogger(__name__)

REQUIRED_RED_TEAM_LANES = ("injection", "boundary", "logic")
DEFAULT_MAX_LANES = 6


@dataclass
class TestingRunnerConfig:
    __test__ = False

    mission_id: str
    mission: Dict[str, Any]
    mission_text: str
    workspace_dir: Path
    artifacts_dir: Path
    tests_dir: Path
    max_lanes: int = DEFAULT_MAX_LANES
    timeout_minutes: int = 45
    lead_model: Any = None
    lane_model: Any = None


@dataclass
class TestingLaneResult:
    __test__ = False

    lane_id: str
    lane_type: str
    focus_area: str
    status: str
    elapsed_seconds: float
    summary: str = ""
    issues: List[Dict[str, Any]] = field(default_factory=list)
    commands_run: List[str] = field(default_factory=list)
    files_examined: List[str] = field(default_factory=list)
    raw_response: str = ""
    artifact_dir: Optional[str] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lane_id": self.lane_id,
            "lane_type": self.lane_type,
            "focus_area": self.focus_area,
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "summary": self.summary,
            "issues": self.issues,
            "commands_run": self.commands_run,
            "files_examined": self.files_examined,
            "artifact_dir": self.artifact_dir,
            "error": self.error,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n")


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        return None
    stripped = text.strip()
    match = re.search(r"```json\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    candidates = [match.group(1)] if match else []
    candidates.append(stripped)
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(stripped)):
            if stripped[i] == "{":
                depth += 1
            elif stripped[i] == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(stripped[start:i + 1])
                    break
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _as_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _clean_lane_type(value: Any) -> str:
    lane_type = re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower()).strip("_")
    return lane_type or "custom"


def _default_model(name: str) -> Any:
    if ModelType is None:
        return None
    if name == "lead":
        return getattr(ModelType, "CLAUDE_SONNET")
    return getattr(ModelType, "CLAUDE_HAIKU")


class TestingRunner:
    """Execute the lead-planned, runner-launched TESTING stage."""

    __test__ = False

    def __init__(self, config: TestingRunnerConfig):
        self.config = config
        self.artifacts_root = config.artifacts_dir / "testing"
        self.agents_root = self.artifacts_root / "agents"
        self.progress_callback: Optional[Callable[[str], None]] = None
        if self.config.lead_model is None:
            self.config.lead_model = _default_model("lead")
        if self.config.lane_model is None:
            self.config.lane_model = _default_model("lane")

    def run(self, progress_callback: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
        self.progress_callback = progress_callback
        start = time.time()
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.agents_root.mkdir(parents=True, exist_ok=True)

        self._log("TestingRunner lead is decomposing the verification method")
        lanes = self._normalize_lanes(self._run_lead_agent())
        _atomic_write_json(self.artifacts_root / "lead_plan.json", {
            "mission_id": self.config.mission_id,
            "created_at": _now(),
            "lanes": lanes,
        })

        self._log(f"TestingRunner launching {len(lanes)} test lanes")
        lane_results = self._run_lanes(lanes)
        response = self._aggregate(lanes, lane_results, time.time() - start)
        _atomic_write_json(self.artifacts_root / "result.json", response)
        _atomic_write_text(self.config.artifacts_dir / "test_results.md", self._render_report(response, lane_results))
        self._log(f"TestingRunner completed with status={response.get('status')}")
        return response

    def _log(self, message: str) -> None:
        logger.info(message)
        if self.progress_callback:
            self.progress_callback(message)

    def _run_lead_agent(self) -> List[Dict[str, Any]]:
        if invoke_claude is None:
            raise RuntimeError("investigation_engine.invoke_claude is unavailable")
        timeout = max(180, min(self.config.timeout_minutes * 60, 900))
        response, elapsed = invoke_claude(
            prompt=self._build_lead_prompt(),
            model=self.config.lead_model,
            timeout=timeout,
            cwd=self.config.workspace_dir,
            stream_context="mission",
        )
        self._log(f"Testing lead completed in {elapsed:.1f}s")
        data = _extract_json_object(response or "")
        if not isinstance(data, dict):
            self._log("Testing lead returned no JSON plan; using default lanes")
            return []
        lanes = data.get("testing_lanes") or data.get("lanes") or []
        return lanes if isinstance(lanes, list) else []

    def _build_lead_prompt(self) -> str:
        mission_json = json.dumps(self.config.mission, indent=2, default=str)[:6000]
        return f"""You are the AtlasForge TESTING lead agent.

Your job is to decompose verification into independent lanes. Do not run tests
and do not launch agents. Python will launch the agents you specify.

Workspace: {self.config.workspace_dir}
Tests dir: {self.config.tests_dir}
Mission:
{self.config.mission_text}

Mission metadata:
```json
{mission_json}
```

Return strict JSON only:
{{
  "testing_lanes": [
    {{
      "lane_type": "self_tests|injection|boundary|logic|mutation|spec_alignment|custom",
      "focus_area": "short label",
      "prompt": "concrete instructions for one autonomous testing agent",
      "expected_evidence": ["commands, files, or observations this lane should report"]
    }}
  ]
}}

Required behavior:
- Include lanes that cover baseline self-tests, blind red-team injection,
  boundary/type behavior, logic/state behavior, mutation testing, and spec alignment.
- Keep each lane independent. Each launched agent must report back to the runner.
- Mutation testing means test the tests: introduce small controlled mutants or
  equivalent reasoning and report whether existing tests catch the change.
"""

    def _normalize_lanes(self, lanes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for lane in lanes:
            if not isinstance(lane, dict):
                continue
            lane_type = _clean_lane_type(lane.get("lane_type") or lane.get("type"))
            prompt = str(lane.get("prompt") or "").strip()
            focus = str(lane.get("focus_area") or lane.get("focus") or lane_type).strip()
            if not prompt:
                prompt = self._default_lane_prompt(lane_type, focus)
            normalized.append({
                "lane_type": lane_type,
                "focus_area": focus or lane_type,
                "prompt": prompt,
                "expected_evidence": _as_list(lane.get("expected_evidence")),
            })

        required = [
            ("self_tests", "Baseline self-tests"),
            ("injection", "Blind red team: injection and path/config boundaries"),
            ("boundary", "Blind red team: boundary values and type confusion"),
            ("logic", "Blind red team: logic, state, and invariants"),
            ("mutation", "Mutation testing: test the tests"),
            ("spec_alignment", "Blind validation against the mission specification"),
        ]
        existing = {_clean_lane_type(lane.get("lane_type")) for lane in normalized}
        for lane_type, focus in required:
            if lane_type not in existing:
                normalized.append({
                    "lane_type": lane_type,
                    "focus_area": focus,
                    "prompt": self._default_lane_prompt(lane_type, focus),
                    "expected_evidence": [],
                })

        max_lanes = max(DEFAULT_MAX_LANES, int(self.config.max_lanes or DEFAULT_MAX_LANES))
        required_order = ["self_tests", "injection", "boundary", "logic", "mutation", "spec_alignment"]
        selected: List[Dict[str, Any]] = []
        selected_types = set()
        for lane_type in required_order:
            lane = next((item for item in normalized if item["lane_type"] == lane_type), None)
            if lane is not None:
                selected.append(lane)
                selected_types.add(lane_type)
        for lane in normalized:
            if len(selected) >= max_lanes:
                break
            lane_type = lane["lane_type"]
            if lane_type in selected_types:
                continue
            selected.append(lane)
            selected_types.add(lane_type)
        for idx, lane in enumerate(selected):
            lane["lane_id"] = f"{self.config.mission_id}_test_{idx}"
        return selected

    def _default_lane_prompt(self, lane_type: str, focus: str) -> str:
        if lane_type == "self_tests":
            return "Run the relevant existing tests and smoke checks. Report commands, failures, and confidence gaps."
        if lane_type == "injection":
            return "Explore the workspace blind for command injection, path traversal, dynamic import/exec, template/config injection, and unsafe subprocess usage. Do not fix bugs."
        if lane_type == "boundary":
            return "Explore blind for boundary and type failures: None, empty data, negative values, large inputs, malformed payloads, and shape mismatches. Do not fix bugs."
        if lane_type == "logic":
            return "Explore blind for logic/state errors, invariant violations, skipped transitions, off-by-one behavior, stale state, and concurrency/resource hazards. Do not fix bugs."
        if lane_type == "mutation":
            return "Test the tests. Identify a small safe mutation or equivalent controlled fault and determine whether the existing tests would catch it. Revert any mutation before reporting."
        if lane_type == "spec_alignment":
            return "Compare implementation behavior against the mission specification and report spec drift or missing acceptance criteria evidence."
        return f"Run an independent TESTING lane for {focus}. Report evidence and do not fix bugs."

    def _run_lanes(self, lanes: List[Dict[str, Any]]) -> List[TestingLaneResult]:
        timeout_per_lane = max(180, int(self.config.timeout_minutes * 60 * 0.55))
        first_phase = [lane for lane in lanes if _clean_lane_type(lane.get("lane_type")) != "mutation"]
        mutation_phase = [lane for lane in lanes if _clean_lane_type(lane.get("lane_type")) == "mutation"]

        results = self._run_lane_group(first_phase, timeout_per_lane)
        if not mutation_phase:
            return results

        self._log("TestingRunner starting mutation sub-stage after baseline verification")
        if not self._baseline_ready_for_mutation(results):
            for lane in mutation_phase:
                results.append(self._blocked_mutation_result(lane, results))
            return results

        prior_context = self._format_prior_results_for_mutation(results)
        mutation_lanes = []
        for lane in mutation_phase:
            enriched = dict(lane)
            enriched["prior_results_context"] = prior_context
            mutation_lanes.append(enriched)
        results.extend(self._run_lane_group(mutation_lanes, timeout_per_lane))
        return results

    def _run_lane_group(self, lanes: List[Dict[str, Any]], timeout_per_lane: int) -> List[TestingLaneResult]:
        if not lanes:
            return []
        results: List[TestingLaneResult] = []
        try:
            configured_workers = int(os.environ.get("ATLASFORGE_TESTING_MAX_PARALLEL", "3"))
        except (TypeError, ValueError):
            configured_workers = 3
        max_workers = min(len(lanes), max(1, configured_workers))

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._run_single_lane, lane, timeout_per_lane): lane
                for lane in lanes
            }
            for future in concurrent.futures.as_completed(futures):
                lane = futures[future]
                try:
                    results.append(future.result())
                except Exception as exc:
                    logger.exception("Testing lane failed: %s", lane.get("lane_id"))
                    results.append(TestingLaneResult(
                        lane_id=str(lane.get("lane_id") or "unknown"),
                        lane_type=_clean_lane_type(lane.get("lane_type")),
                        focus_area=str(lane.get("focus_area") or ""),
                        status="error",
                        elapsed_seconds=0.0,
                        error=str(exc),
                    ))
        return results

    def _baseline_ready_for_mutation(self, results: List[TestingLaneResult]) -> bool:
        baseline_results = [result for result in results if result.lane_type == "self_tests"]
        return any(result.status == "passed" for result in baseline_results)

    def _blocked_mutation_result(
        self,
        lane: Dict[str, Any],
        prior_results: List[TestingLaneResult],
    ) -> TestingLaneResult:
        lane_id = str(lane.get("lane_id") or f"{self.config.mission_id}_mutation")
        lane_type = _clean_lane_type(lane.get("lane_type"))
        focus = str(lane.get("focus_area") or "Mutation testing")
        lane_dir = self.agents_root / lane_id
        lane_dir.mkdir(parents=True, exist_ok=True)
        summary = (
            "Mutation testing blocked because the baseline self-test sub-stage "
            "did not produce a passing baseline."
        )
        result = TestingLaneResult(
            lane_id=lane_id,
            lane_type=lane_type,
            focus_area=focus,
            status="blocked",
            elapsed_seconds=0.0,
            summary=summary,
            artifact_dir=str(lane_dir),
            error=summary,
        )
        _atomic_write_json(lane_dir / "metadata.json", {
            "mission_id": self.config.mission_id,
            "lane_id": lane_id,
            "lane_type": lane_type,
            "focus_area": focus,
            "started_at": _now(),
            "artifact_schema": "testing-runner-lane-v1",
            "blocked_by": "baseline_self_tests",
            "prior_results": [item.to_dict() for item in prior_results],
        })
        _atomic_write_json(lane_dir / "result.json", result.to_dict())
        _atomic_write_text(lane_dir / "final.md", summary + "\n")
        return result

    def _format_prior_results_for_mutation(self, results: List[TestingLaneResult]) -> str:
        payload = [
            {
                "lane_id": result.lane_id,
                "lane_type": result.lane_type,
                "status": result.status,
                "summary": result.summary,
                "issues": result.issues,
                "commands_run": result.commands_run,
                "artifact_dir": result.artifact_dir,
            }
            for result in sorted(results, key=lambda item: item.lane_id)
        ]
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)

    def _run_single_lane(self, lane: Dict[str, Any], timeout: int) -> TestingLaneResult:
        if invoke_claude is None:
            raise RuntimeError("investigation_engine.invoke_claude is unavailable")
        start = time.time()
        lane_id = str(lane.get("lane_id") or f"{self.config.mission_id}_test")
        lane_type = _clean_lane_type(lane.get("lane_type"))
        focus = str(lane.get("focus_area") or lane_type)
        lane_dir = self.agents_root / lane_id
        events_path = lane_dir / "events.jsonl"
        sources_path = lane_dir / "sources.jsonl"
        final_path = lane_dir / "final.md"
        result_path = lane_dir / "result.json"
        metadata_path = lane_dir / "metadata.json"
        lane_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(metadata_path, {
            "mission_id": self.config.mission_id,
            "lane_id": lane_id,
            "lane_type": lane_type,
            "focus_area": focus,
            "timeout_seconds": timeout,
            "started_at": _now(),
            "artifact_schema": "testing-runner-lane-v1",
        })

        response, _elapsed = invoke_claude(
            prompt=self._build_lane_prompt(lane),
            model=self.config.lane_model,
            timeout=timeout,
            cwd=self.config.workspace_dir,
            artifact_event_file=events_path,
            artifact_sources_file=sources_path,
            artifact_label=lane_id,
            stream_context="mission",
        )
        elapsed = time.time() - start
        _atomic_write_text(final_path, response or "")
        parsed = _extract_json_object(response or "") or {}
        if (response or "").strip().startswith("ERROR:"):
            parsed = {"status": "error", "summary": response.strip(), "issues": []}
        result = TestingLaneResult(
            lane_id=lane_id,
            lane_type=lane_type,
            focus_area=focus,
            status=str(parsed.get("status") or "error").strip().lower(),
            elapsed_seconds=elapsed,
            summary=str(parsed.get("summary") or ""),
            issues=[i for i in _as_list(parsed.get("issues")) if isinstance(i, dict)],
            commands_run=[str(x) for x in _as_list(parsed.get("commands_run"))],
            files_examined=[str(x) for x in _as_list(parsed.get("files_examined"))],
            raw_response=response or "",
            artifact_dir=str(lane_dir),
            error=str(parsed.get("error") or "") or None,
        )
        if result.status not in {"passed", "failed", "error", "blocked"}:
            result.status = "error"
            result.error = result.error or f"Unrecognized lane status in response: {parsed.get('status')!r}"
        _atomic_write_json(result_path, result.to_dict())
        return result

    def _build_lane_prompt(self, lane: Dict[str, Any]) -> str:
        lane_type = _clean_lane_type(lane.get("lane_type"))
        red_team_note = ""
        if lane_type in REQUIRED_RED_TEAM_LANES:
            red_team_note = "This is a blind red-team lane. Try to break behavior. Do not fix bugs."
        elif lane_type == "mutation":
            red_team_note = "This is mutation testing. Test whether the tests fail when behavior is intentionally perturbed. Revert any edits before final report."
        mission_json = json.dumps(self.config.mission, indent=2, default=str)[:5000]
        return f"""You are an AtlasForge TESTING lane agent.

Workspace: {self.config.workspace_dir}
Tests dir: {self.config.tests_dir}
Lane type: {lane_type}
Focus: {lane.get("focus_area")}
{red_team_note}

Mission:
{self.config.mission_text}

Mission metadata:
```json
{mission_json}
```

Lane instructions:
{lane.get("prompt")}

Prior TESTING sub-stage results:
{lane.get("prior_results_context") or "None. This lane is part of the first TESTING sub-stage."}

Rules:
- Work independently from the builder. Inspect files and run commands as needed.
- Report evidence. Do not claim a test passed unless you actually ran or directly inspected the evidence.
- You may create temporary testing artifacts, but do not make production fixes.
- For mutation testing, restore any intentional mutation before finishing.

Return strict JSON only:
{{
  "lane_type": "{lane_type}",
  "status": "passed|failed|error|blocked",
  "summary": "short evidence-based summary",
  "issues": [
    {{
      "severity": "critical|high|medium|low",
      "title": "finding title",
      "evidence": "specific command, file, or observed behavior",
      "affected_code": "file/function if known",
      "recommendation": "what ANALYZING should decide next"
    }}
  ],
  "commands_run": ["commands actually run"],
  "files_examined": ["important files inspected"],
  "confidence": 0.0
}}
"""

    def _aggregate(
        self,
        lanes: List[Dict[str, Any]],
        results: List[TestingLaneResult],
        elapsed_seconds: float,
    ) -> Dict[str, Any]:
        issues: List[Dict[str, Any]] = []
        for result in results:
            for issue in result.issues:
                item = dict(issue)
                item.setdefault("lane_id", result.lane_id)
                item.setdefault("lane_type", result.lane_type)
                issues.append(item)

        red_team_results = [r for r in results if r.lane_type in REQUIRED_RED_TEAM_LANES]
        completed_red_team = [r for r in red_team_results if r.status in {"passed", "failed"}]
        failed_lanes = [r for r in results if r.status == "failed" or r.issues]
        error_lanes = [r for r in results if r.status == "error"]
        blocked_lanes = [r for r in results if r.status == "blocked"]
        mutation = next((r for r in results if r.lane_type == "mutation"), None)

        if error_lanes:
            status = "tests_error"
        elif blocked_lanes:
            status = "tests_error"
        elif failed_lanes:
            status = "tests_failed"
        elif len(completed_red_team) < len(REQUIRED_RED_TEAM_LANES):
            status = "tests_error"
        else:
            status = "tests_passed"

        lane_dicts = [r.to_dict() for r in sorted(results, key=lambda item: item.lane_id)]
        red_team_issues = [
            f"[{issue.get('severity', 'unknown')}] {issue.get('title', 'finding')} ({issue.get('lane_type')})"
            for issue in issues
            if issue.get("lane_type") in REQUIRED_RED_TEAM_LANES
        ]
        completion = {
            "agent_reports_collected": len(completed_red_team),
            "agents_reached_report_phase": len(completed_red_team),
            "all_agents_completed": len(completed_red_team) >= len(REQUIRED_RED_TEAM_LANES),
            "timed_out_agents": [r.lane_id for r in error_lanes if "timeout" in str(r.error or r.summary).lower()],
            "lane_statuses": {r.lane_id: r.status for r in results},
        }
        mutation_payload = {
            "status": mutation.status if mutation else "missing",
            "summary": mutation.summary if mutation else "Mutation lane did not run",
            "issues": mutation.issues if mutation else [],
            "artifact_dir": mutation.artifact_dir if mutation else None,
        }

        return {
            "status": status,
            "self_tests": [
                {
                    "name": r.focus_area,
                    "passed": r.status == "passed",
                    "output": r.summary,
                    "artifact_dir": r.artifact_dir,
                }
                for r in results
                if r.lane_type == "self_tests"
            ],
            "adversarial_testing": {
                "red_team_issues": red_team_issues,
                "red_team_agent_count": len(red_team_results),
                "red_team_duration_seconds": round(elapsed_seconds, 1),
                "red_team_completion": completion,
                "property_violations": [
                    issue.get("title", "property violation")
                    for issue in issues
                    if issue.get("lane_type") in {"boundary", "logic"}
                ],
                "mutation_score": None,
                "mutation_testing": mutation_payload,
                "spec_alignment": None,
                "epistemic_score": self._epistemic_score(results, issues),
                "rigor_level": self._rigor_level(results, issues),
                "lane_results": lane_dicts,
            },
            "mutation_testing": mutation_payload,
            "summary": self._summary(status, results, issues, blocked_lanes),
            "success_criteria_met": [] if issues or error_lanes else ["TestingRunner found no blocking issues"],
            "success_criteria_failed": [i.get("title", "Untitled issue") for i in issues],
            "issues_to_fix": [
                f"[{i.get('severity', 'unknown')}] {i.get('title', 'Untitled issue')}: {i.get('recommendation', '')}".strip()
                for i in issues
            ],
            "message_to_human": self._summary(status, results, issues, blocked_lanes),
            "testing_runner": {
                "schema": "testing-runner-v1",
                "mission_id": self.config.mission_id,
                "started_lanes": len(lanes),
                "completed_lanes": len(results),
                "artifact_dir": str(self.artifacts_root),
                "lead_plan_path": str(self.artifacts_root / "lead_plan.json"),
                "result_path": str(self.artifacts_root / "result.json"),
                "report_path": str(self.config.artifacts_dir / "test_results.md"),
            },
        }

    def _epistemic_score(self, results: List[TestingLaneResult], issues: List[Dict[str, Any]]) -> float:
        completed = sum(1 for r in results if r.status in {"passed", "failed"})
        total = max(1, len(results))
        score = completed / total
        if any(r.lane_type == "mutation" and r.status in {"passed", "failed"} for r in results):
            score += 0.1
        if issues:
            score -= 0.1
        return round(max(0.0, min(1.0, score)), 2)

    def _rigor_level(self, results: List[TestingLaneResult], issues: List[Dict[str, Any]]) -> str:
        red_completed = sum(1 for r in results if r.lane_type in REQUIRED_RED_TEAM_LANES and r.status in {"passed", "failed"})
        has_mutation = any(r.lane_type == "mutation" and r.status in {"passed", "failed"} for r in results)
        if red_completed >= 3 and has_mutation and not issues:
            return "rigorous"
        if red_completed >= 3 and has_mutation:
            return "strong"
        if red_completed >= 3:
            return "moderate"
        if red_completed > 0:
            return "weak"
        return "insufficient"

    def _summary(
        self,
        status: str,
        results: List[TestingLaneResult],
        issues: List[Dict[str, Any]],
        blocked_lanes: List[TestingLaneResult],
    ) -> str:
        return (
            f"TestingRunner {status}: {len(results)} lanes reported, "
            f"{len(issues)} issues found, {len(blocked_lanes)} lanes blocked."
        )

    def _render_report(self, response: Dict[str, Any], results: List[TestingLaneResult]) -> str:
        lines = [
            "# TESTING Results",
            "",
            f"- Status: {response.get('status')}",
            f"- Summary: {response.get('summary')}",
            f"- Artifact dir: {response.get('testing_runner', {}).get('artifact_dir')}",
            "",
            "## Lane Results",
            "",
        ]
        for result in sorted(results, key=lambda item: item.lane_id):
            lines.extend([
                f"### {result.focus_area}",
                "",
                f"- Lane: {result.lane_type}",
                f"- Status: {result.status}",
                f"- Artifact: {result.artifact_dir}",
                f"- Summary: {result.summary or '(none)'}",
                "",
            ])
            if result.issues:
                lines.append("Findings:")
                for issue in result.issues:
                    lines.append(f"- [{issue.get('severity', 'unknown')}] {issue.get('title', 'Untitled')}: {issue.get('evidence', '')}")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
