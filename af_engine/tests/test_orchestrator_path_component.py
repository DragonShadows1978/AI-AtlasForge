import pytest

from af_engine.orchestrator import _sanitize_for_log, _validate_path_component


@pytest.mark.parametrize("value", ["project", "project_123", "Project-Name"])
def test_validate_path_component_allows_single_component(value):
    assert _validate_path_component(value, "resolved_project_name") == value


@pytest.mark.parametrize("value", ["../evil", "nested/project", "nested\\project", "..", ".", "", "  ", "bad\x00name", "bad\nname"])
def test_validate_path_component_rejects_unsafe_values(value):
    with pytest.raises(ValueError):
        _validate_path_component(value, "resolved_project_name")


def test_validate_path_component_rejects_non_string():
    with pytest.raises(ValueError):
        _validate_path_component(123, "resolved_project_name")


def test_sanitize_for_log_strips_bidi_controls():
    assert _sanitize_for_log("abc\u202edef\u2066ghi") == "abcdefghi"
