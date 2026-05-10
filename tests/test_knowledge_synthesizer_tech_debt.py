from research_agent.knowledge_synthesizer import (
    ConfidenceLevel,
    KnowledgeSynthesizer,
    Recommendation,
    RecommendationType,
    SynthesisResult,
    _extract_json_object,
)


def _recommendation(title: str) -> Recommendation:
    return Recommendation(
        title=title,
        description=f"{title} description",
        recommendation_type=RecommendationType.IMPLEMENTATION,
        confidence=ConfidenceLevel.MEDIUM,
        rationale=f"{title} rationale",
    )


def test_merge_syntheses_ignores_failed_results():
    synthesizer = KnowledgeSynthesizer()
    good = SynthesisResult(
        topic="topic",
        summary="usable summary",
        recommendations=[_recommendation("keep")],
        sources_used=["https://example.test/good"],
        total_sources=1,
        primary_sources=1,
        success=True,
    )
    failed = SynthesisResult(
        topic="topic",
        summary="error summary should not leak",
        recommendations=[_recommendation("drop")],
        sources_used=["https://example.test/failed"],
        total_sources=99,
        primary_sources=99,
        success=False,
        error="failed",
    )

    merged = synthesizer.merge_syntheses([failed, good])

    assert merged.success is True
    assert merged.summary == "usable summary"
    assert [rec.title for rec in merged.recommendations] == ["keep"]
    assert merged.sources_used == ["https://example.test/good"]
    assert merged.total_sources == 1
    assert merged.primary_sources == 1


def test_merge_syntheses_reports_all_failed_inputs():
    synthesizer = KnowledgeSynthesizer()
    failed = SynthesisResult(topic="topic", success=False, error="failed")

    merged = synthesizer.merge_syntheses([failed])

    assert merged.topic == "topic"
    assert merged.success is False
    assert merged.error == "All synthesis inputs failed"


def test_extract_json_object_linear_scanner_handles_malformed_prefix():
    text = (
        "prefix { never closed "
        '{"summary": "valid", "recommendations": []}'
        " suffix"
    )

    assert _extract_json_object(text) == '{"summary": "valid", "recommendations": []}'


def test_extract_json_object_ignores_summary_inside_string_value():
    text = '{"outer": "\\"summary\\" is just text"} {"summary": "real"}'

    assert _extract_json_object(text) == '{"summary": "real"}'
