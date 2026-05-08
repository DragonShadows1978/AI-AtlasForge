#!/usr/bin/env python3
"""Resume an interrupted investigation from completed subagent artifacts.

This intentionally skips lead decomposition and subagent execution. It loads
existing `artifacts/subagents/*/result.json`, then runs validation and final
synthesis using the normal InvestigationRunner private methods.
"""

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from investigation_engine import (  # noqa: E402
    BASE_DIR,
    InvestigationConfig,
    InvestigationRunner,
    InvestigationStatus,
    ModelType,
    SubagentResult,
    archive_current_investigation,
    update_investigation_status,
)


def _model(value, fallback):
    if isinstance(value, ModelType):
        return value
    try:
        return ModelType(value)
    except Exception:
        return fallback


def _load_config(investigation_id: str) -> InvestigationConfig:
    workspace_dir = BASE_DIR / "investigations" / investigation_id
    config_path = workspace_dir / "config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Missing config.json: {config_path}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    return InvestigationConfig(
        query=data["query"],
        investigation_id=data.get("investigation_id") or investigation_id,
        max_subagents=int(data.get("max_subagents", 5)),
        timeout_minutes=int(data.get("timeout_minutes", 10)),
        lead_model=_model(data.get("lead_model"), ModelType.CLAUDE_SONNET),
        subagent_model=_model(data.get("subagent_model"), ModelType.CLAUDE_HAIKU),
        synthesis_model=_model(data.get("synthesis_model"), ModelType.CLAUDE_OPUS),
        workspace_dir=Path(data.get("workspace_dir") or workspace_dir),
        deliverable_format=data.get("deliverable_format"),
        source=data.get("source", "dashboard"),
        skip_global_state=False,
        enable_validation=bool(data.get("enable_validation", True)),
        validation_filter_mode=data.get("validation_filter_mode", "balanced"),
    )


def _load_subagent_results(workspace_dir: Path) -> list[SubagentResult]:
    subagent_dir = workspace_dir / "artifacts" / "subagents"
    results = []
    for result_path in sorted(subagent_dir.glob("*/result.json")):
        data = json.loads(result_path.read_text(encoding="utf-8"))
        results.append(
            SubagentResult(
                subagent_id=data["subagent_id"],
                focus_area=data.get("focus_area", result_path.parent.name),
                findings=data.get("findings", ""),
                elapsed_seconds=float(data.get("elapsed_seconds", 0) or 0),
                status=data.get("status", "completed"),
                error=data.get("error"),
            )
        )
    if not results:
        raise RuntimeError(f"No subagent result.json files found under {subagent_dir}")
    return results


def _report_path(config: InvestigationConfig) -> Path:
    artifacts_dir = config.workspace_dir / "artifacts"
    fmt = (config.deliverable_format or "").lower()
    if "html" in fmt:
        return artifacts_dir / "investigation_report.html"
    if "json" in fmt:
        return artifacts_dir / "investigation_report.json"
    return artifacts_dir / "investigation_report.md"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("investigation_id")
    args = parser.parse_args()

    started = time.time()
    config = _load_config(args.investigation_id)
    runner = InvestigationRunner(config)
    runner.started_at = datetime.now().isoformat()
    runner.subagent_results = _load_subagent_results(config.workspace_dir)
    runner.pinned_evidence_sources, runner.pinned_evidence_records = runner._load_pinned_webproxy_evidence()

    print(
        f"Loaded {len(runner.subagent_results)} subagent results and "
        f"{len(runner.pinned_evidence_records)} pinned WebProxy evidence payloads",
        flush=True,
    )

    validated_findings = None
    if config.enable_validation:
        update_investigation_status(config.investigation_id, InvestigationStatus.VALIDATING)
        print("Running validation...", flush=True)
        validated_findings = runner._validate_findings()

    update_investigation_status(config.investigation_id, InvestigationStatus.SYNTHESIZING)
    print("Running synthesis...", flush=True)
    synthesis = runner._synthesize_findings(validated_findings)

    artifacts_dir = config.workspace_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    report_path = _report_path(config)
    report_path.write_text(synthesis or "", encoding="utf-8")

    findings_data = {
        "investigation_id": config.investigation_id,
        "query": config.query,
        "subagent_results": [r.to_dict() for r in runner.subagent_results],
        "subagent_artifacts": [
            {
                "subagent_id": r.subagent_id,
                "artifact_dir": str(artifacts_dir / "subagents" / r.subagent_id),
                "events_path": str(artifacts_dir / "subagents" / r.subagent_id / "events.jsonl"),
                "sources_path": str(artifacts_dir / "subagents" / r.subagent_id / "sources.jsonl"),
                "final_path": str(artifacts_dir / "subagents" / r.subagent_id / "final.md"),
                "result_path": str(artifacts_dir / "subagents" / r.subagent_id / "result.json"),
            }
            for r in runner.subagent_results
        ],
        "validation": {
            "enabled": config.enable_validation,
            "filter_mode": config.validation_filter_mode,
        },
        "pinned_source_evidence": runner.pinned_evidence_records,
        "url_metadata": getattr(runner, "url_metadata", []) or [],
        "resumed_from_artifacts": True,
        "resumed_at": datetime.now().isoformat(),
    }
    if validated_findings:
        findings_data["validation"]["stats"] = validated_findings.to_dict()
        findings_data["validation"]["total_claims"] = validated_findings.total_claims
        findings_data["validation"]["supported"] = validated_findings.supported_claims
        findings_data["validation"]["unsupported"] = validated_findings.unsupported_claims
        findings_data["validation"]["unverifiable"] = validated_findings.unverifiable_claims
        findings_data["validation"]["flagged_claims"] = [
            {"id": c.id, "text": c.text, "reason": c.flag_reason}
            for c in validated_findings.claims if c.flagged
        ]

    (artifacts_dir / "findings.json").write_text(
        json.dumps(findings_data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    elapsed = time.time() - started
    update_investigation_status(
        config.investigation_id,
        InvestigationStatus.COMPLETED,
        {
            "completed_at": datetime.now().isoformat(),
            "elapsed_seconds": elapsed,
            "report_path": str(report_path),
        },
    )
    archive_current_investigation()
    print(f"Completed resumed investigation in {elapsed:.1f}s: {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
