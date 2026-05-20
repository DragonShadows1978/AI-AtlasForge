from investigation_engine import InvestigationConfig, build_synthesis_prompt_validated
from investigation_validator.filter import filter_findings
from investigation_validator.models import (
    Claim,
    FetchedSource,
    FilterMode,
    ValidationConfig,
)
from investigation_validator.validator_agent import validate_claim


def test_source_exists_mode_is_programmatic(monkeypatch):
    claim = Claim.create(
        text="This claim may be supported or refuted by the cited source.",
        subagent_id="sub_0",
        focus_area="Refuting Evidence",
        source_url="https://example.com/source",
    )
    source = FetchedSource(
        url="https://example.com/source",
        content="Fetched source text that exists and can be inspected by synthesis.",
        accessible=True,
    )

    def fail_if_llm_called(*args, **kwargs):
        raise AssertionError("source_exists mode must not call the LLM validator")

    monkeypatch.setattr(
        "investigation_validator.validator_agent._invoke_claude",
        fail_if_llm_called,
    )

    result = validate_claim(
        claim,
        source,
        ValidationConfig(filter_mode=FilterMode.SOURCE_EXISTS),
    )

    assert result.verdict.value == "supported"
    assert result.validator_model == "programmatic-source-existence"
    assert "did not adjudicate" in result.reasoning


def test_source_exists_filter_keeps_refuting_findings():
    class Result:
        status = "completed"
        subagent_id = "sub_0"
        focus_area = "Counter Evidence"
        findings = "Original finding text"

    claim = Claim.create(
        text="The cited source refutes the working theory.",
        subagent_id="sub_0",
        focus_area="Counter Evidence",
        source_url="https://example.com/refutation",
    )
    source = FetchedSource(
        url="https://example.com/refutation",
        content="This source exists and contains counter-evidence.",
        accessible=True,
    )
    validation_result = validate_claim(
        claim,
        source,
        ValidationConfig(filter_mode=FilterMode.SOURCE_EXISTS),
    )
    claim.validated = True
    claim.validation_note = validation_result.reasoning

    filtered = filter_findings(
        [Result()],
        [claim],
        {claim.id: validation_result},
        FilterMode.SOURCE_EXISTS,
    )

    assert "Source Checked" in filtered
    assert "refutes the working theory" in filtered
    assert "https://example.com/refutation" in filtered


def test_source_exists_prompt_does_not_block_refuting_data():
    prompt = build_synthesis_prompt_validated(
        query="Find evidence for and against the claim",
        validated_findings_text=(
            "### Counter Evidence\n\n"
            "**Source Checked:**\n"
            "- The cited source refutes the working theory.\n"
        ),
        validation_stats={
            "filter_mode": "source_exists",
            "total_claims": 1,
            "supported_claims": 1,
            "unsupported_claims": 0,
            "unverifiable_claims": 0,
        },
    )

    assert "not for whether each source supports or refutes the claim" in prompt
    assert "Include refuting evidence" in prompt
    assert "Do NOT include disputed/unsupported claims" not in prompt


def test_investigation_default_validation_mode_is_source_exists():
    assert InvestigationConfig(query="test").validation_filter_mode == "source_exists"
    assert ValidationConfig().filter_mode == FilterMode.SOURCE_EXISTS
