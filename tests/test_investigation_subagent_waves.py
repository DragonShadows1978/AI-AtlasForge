from types import SimpleNamespace
import json


def test_investigation_subagents_run_in_waves_without_timeout_cap(monkeypatch, tmp_path):
    import investigation_engine
    import subagent_pool_manager

    class FakePool:
        def __init__(self):
            self.requests = []
            self.releases = 0

        def request_slots(self, inv_id, count, priority=10, query=""):
            self.requests.append(count)
            granted = 2 if len(self.requests) == 1 else count
            return SimpleNamespace(granted=granted, quota_limit=2, reason="partial_quota")

        def release_slots(self, inv_id, count=1):
            self.releases += count

        def notify_investigation_complete(self, inv_id, elapsed_sec=0.0, success=True):
            self.completed = (inv_id, success)

    fake_pool = FakePool()
    monkeypatch.setattr(subagent_pool_manager, "get_pool_manager", lambda: fake_pool)

    config = investigation_engine.InvestigationConfig(
        query="research many angles",
        investigation_id="inv_wave_test",
        max_subagents=5,
        timeout_minutes=20,
        workspace_dir=tmp_path,
    )

    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config
    runner.ground_rules = ""
    runner.progress_callback = None
    runner._log = lambda message: None

    seen_timeouts = []

    def fake_run_single(subagent_id, focus_area, prompt, timeout):
        seen_timeouts.append(timeout)
        return investigation_engine.SubagentResult(
            subagent_id=subagent_id,
            focus_area=focus_area,
            findings=f"findings for {focus_area}",
            elapsed_seconds=0.01,
            status="completed",
        )

    runner._run_single_subagent = fake_run_single

    directions = [
        {"focus_area": f"Area {i}", "prompt": f"Prompt {i}", "research_type": "web"}
        for i in range(5)
    ]

    results = runner._run_subagents(directions)

    assert len(results) == 5
    assert all(r.status == "completed" for r in results)
    assert fake_pool.requests == [5, 3]
    assert fake_pool.releases == 5
    assert seen_timeouts == [540, 540, 540, 540, 540]


def test_investigation_synthesis_uses_opus_by_default(monkeypatch, tmp_path):
    import investigation_engine

    captured = {}

    def fake_invoke_claude(prompt, model, timeout, cwd, system_prompt=None):
        captured["model"] = model
        captured["timeout"] = timeout
        return "final synthesis", 0.01

    monkeypatch.setattr(investigation_engine, "invoke_claude", fake_invoke_claude)

    config = investigation_engine.InvestigationConfig(
        query="research synthesis model",
        investigation_id="inv_synthesis_test",
        timeout_minutes=20,
        workspace_dir=tmp_path,
    )
    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config
    runner.ground_rules = ""
    runner.url_metadata = []
    runner.progress_callback = None
    runner.subagent_results = [
        investigation_engine.SubagentResult(
            subagent_id="sub_1",
            focus_area="Area",
            findings="Detailed finding",
            elapsed_seconds=1,
            status="completed",
        )
    ]
    runner._log = lambda message: None

    assert runner._synthesize_findings() == "final synthesis"
    assert captured["model"] == investigation_engine.ModelType.CLAUDE_OPUS
    assert captured["timeout"] == 360


def test_investigation_lead_decomposition_scales_with_complexity(monkeypatch, tmp_path):
    import investigation_engine

    captured = {}

    def fake_invoke_claude(prompt, model, timeout, cwd, system_prompt=None):
        captured["timeout"] = timeout
        return """
```json
{
  "understanding": "test",
  "domain": "general",
  "key_questions": ["q"],
  "research_directions": [
    {"focus_area": "Area", "prompt": "Research area", "research_type": "web"}
  ]
}
```
""", 0.01

    monkeypatch.setattr(investigation_engine, "invoke_claude", fake_invoke_claude)

    config = investigation_engine.InvestigationConfig(
        query="large interdisciplinary query",
        investigation_id="inv_lead_timeout_test",
        max_subagents=30,
        timeout_minutes=10,
        workspace_dir=tmp_path,
    )
    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config
    runner.ground_rules = ""
    runner.url_metadata = []
    runner.progress_callback = None
    runner._last_lead_error = None
    runner._log = lambda message: None

    directions = runner._run_lead_agent()

    assert len(directions) == 1
    assert captured["timeout"] == 600


def test_run_single_subagent_writes_runner_owned_artifacts(monkeypatch, tmp_path):
    import investigation_engine

    captured = {}

    def fake_invoke_claude(
        prompt,
        model,
        timeout,
        cwd,
        system_prompt=None,
        artifact_event_file=None,
        artifact_sources_file=None,
        artifact_label=None,
    ):
        captured["artifact_event_file"] = artifact_event_file
        captured["artifact_sources_file"] = artifact_sources_file
        captured["artifact_label"] = artifact_label
        artifact_event_file.write_text(
            json.dumps({
                "timestamp": "2026-05-07T00:00:00",
                "seq": 1,
                "agent_id": artifact_label,
                "event_type": "tool_call",
                "display_text": "WebFetch({\"url\":\"https://example.com\"})",
                "raw": "{}",
            }) + "\n",
            encoding="utf-8",
        )
        artifact_sources_file.write_text(
            json.dumps({
                "timestamp": "2026-05-07T00:00:00",
                "seq": 1,
                "agent_id": artifact_label,
                "source_url": "https://example.com",
                "source_event_type": "tool_call",
            }) + "\n",
            encoding="utf-8",
        )
        return "subagent findings", 0.01

    monkeypatch.setattr(investigation_engine, "invoke_claude", fake_invoke_claude)

    config = investigation_engine.InvestigationConfig(
        query="artifact test",
        investigation_id="inv_artifact_test",
        timeout_minutes=10,
        workspace_dir=tmp_path,
    )
    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config

    result = runner._run_single_subagent(
        "inv_artifact_test_sub_0",
        "Artifact Area",
        "Research artifacts",
        60,
    )

    subagent_dir = tmp_path / "artifacts" / "subagents" / "inv_artifact_test_sub_0"
    assert result.status == "completed"
    assert captured["artifact_label"] == "inv_artifact_test_sub_0"
    assert (subagent_dir / "metadata.json").exists()
    assert (subagent_dir / "events.jsonl").exists()
    assert (subagent_dir / "sources.jsonl").exists()
    assert (subagent_dir / "final.md").read_text() == "subagent findings"
    result_json = json.loads((subagent_dir / "result.json").read_text())
    assert result_json["findings"] == "subagent findings"


def test_runner_loads_pinned_webproxy_evidence_for_validation(tmp_path):
    import investigation_engine

    config = investigation_engine.InvestigationConfig(
        query="evidence test",
        investigation_id="inv_evidence_test",
        timeout_minutes=10,
        workspace_dir=tmp_path,
    )
    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config

    subagent_dir = tmp_path / "artifacts" / "subagents" / "inv_evidence_test_sub_0"
    payload_dir = subagent_dir / "source_payloads"
    payload_dir.mkdir(parents=True)
    evidence_path = payload_dir / "webproxy_1_abcdef.json"
    evidence_payload = {
        "type": "fetch",
        "url": "https://example.com/paper",
        "title": "Example Paper",
        "content_type": "text/html",
        "text": "Full captured source text for validator.",
        "text_length": 40,
        "truncated": False,
    }
    evidence_path.write_text(json.dumps(evidence_payload), encoding="utf-8")
    (subagent_dir / "sources.jsonl").write_text(
        json.dumps({
            "artifact_type": "web_proxy_cache_json",
            "cache_json_path": "/tmp/web_proxy_cache/fetch.json",
            "evidence_json_path": str(evidence_path),
            "sha256": "abc",
            "byte_length": evidence_path.stat().st_size,
        }) + "\n",
        encoding="utf-8",
    )

    sources, records = runner._load_pinned_webproxy_evidence()
    runner.pinned_evidence_records = records

    assert "https://example.com/paper" in sources
    assert sources["https://example.com/paper"].content == "Full captured source text for validator."
    assert sources["https://example.com/paper"].accessible is True
    assert len(records) == 1
    assert records[0]["evidence_json_path"] == str(evidence_path.resolve())
    assert "Example Paper" in runner._build_pinned_evidence_index()


def test_runner_loads_literal_webproxy_json_manifest_for_validation(tmp_path):
    import investigation_engine

    config = investigation_engine.InvestigationConfig(
        query="evidence test",
        investigation_id="inv_webproxy_json_test",
        timeout_minutes=10,
        workspace_dir=tmp_path,
    )
    runner = investigation_engine.InvestigationRunner.__new__(investigation_engine.InvestigationRunner)
    runner.config = config

    capture_dir = tmp_path / "artifacts" / "webproxy_json" / "inv_webproxy_json_test_sub_0"
    capture_dir.mkdir(parents=True)
    payload_path = capture_dir / "000001_WebFetch_fetch_abcdef.json"
    payload = {
        "type": "fetch",
        "url": "https://example.com/source",
        "title": "Literal Capture",
        "text": "Captured directly by the WebProxy MCP layer.",
        "text_length": 44,
    }
    payload_path.write_text(json.dumps(payload), encoding="utf-8")
    record = {
        "artifact_type": "web_proxy_json_output",
        "subagent_id": "inv_webproxy_json_test_sub_0",
        "tool_name": "WebFetch",
        "endpoint": "/fetch",
        "url": "https://example.com/source",
        "artifact_json_path": str(payload_path),
        "sha256": "abc",
        "byte_length": payload_path.stat().st_size,
    }
    (capture_dir / "manifest.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")

    sources, records = runner._load_pinned_webproxy_evidence()
    json_records = runner._load_webproxy_json_artifact_records()

    assert "https://example.com/source" in sources
    assert sources["https://example.com/source"].content == "Captured directly by the WebProxy MCP layer."
    assert len(records) == 1
    assert records[0]["artifact_json_path"] == str(payload_path)
    assert len(json_records) == 1
    assert json_records[0]["artifact_json_path"] == str(payload_path.resolve())


def test_validation_orchestrator_prefers_pinned_evidence(monkeypatch):
    from investigation_validator.models import Claim, FetchedSource, ValidationConfig
    from investigation_validator.orchestrator import ValidationOrchestrator

    class Result:
        subagent_id = "sub_0"
        focus_area = "Area"
        findings = "Claim text. https://example.com/source"

    claim = Claim.create(
        text="Claim text",
        subagent_id="sub_0",
        focus_area="Area",
        source_url="https://example.com/source/",
    )

    monkeypatch.setattr(
        "investigation_validator.orchestrator.extract_claims",
        lambda results: [claim],
    )

    captured = {}

    def fake_validate_claims_parallel(claims, sources, config):
        captured["sources"] = sources
        return {}

    monkeypatch.setattr(
        "investigation_validator.orchestrator.validate_claims_parallel",
        fake_validate_claims_parallel,
    )

    orchestrator = ValidationOrchestrator(ValidationConfig())
    monkeypatch.setattr(
        orchestrator.source_fetcher,
        "fetch_sources",
        lambda urls: (_ for _ in ()).throw(AssertionError("network fetch should not run")),
    )

    evidence = {
        "https://example.com/source": FetchedSource(
            url="https://example.com/source",
            content="Pinned source content",
            accessible=True,
            content_type="text/html",
        )
    }

    orchestrator.validate([Result()], evidence_sources=evidence)

    assert captured["sources"]["https://example.com/source/"].content == "Pinned source content"
