"""Regression tests for mission_config validation gaps fixed in cycle 2.

Covers continuation-priority items 1-2:
  - success_criteria=int|float|bool/scalar -> MissionValidationError (not TypeError)
  - prune_old_audit_logs negative-value -> no IndexError
  - from_queue_item(non-dict) -> MissionValidationError (not AttributeError)
  - from_request(non-dict) -> MissionValidationError
"""

from __future__ import annotations

from pathlib import Path

import pytest

from af_engine.mission_config import (
    MissionConfig,
    MissionValidationError,
    prune_old_audit_logs,
)


# ---------------------------------------------------------------------------
# success_criteria scalar handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [42, 3.14, True, b"bytes"])
def test_success_criteria_scalar_raises_validation_error(bad_value):
    """A scalar success_criteria must produce MissionValidationError, not TypeError."""
    with pytest.raises(MissionValidationError):
        MissionConfig(
            problem_statement="this is a sufficiently long problem statement",
            success_criteria=bad_value,  # type: ignore[arg-type]
        )


def test_success_criteria_string_wraps_to_single_element_list():
    """A bare string should become a one-element list, not chars."""
    cfg = MissionConfig(
        problem_statement="this is a sufficiently long problem statement",
        success_criteria="must launch",  # type: ignore[arg-type]
    )
    assert cfg.success_criteria == ["must launch"]


def test_success_criteria_none_becomes_empty_list():
    cfg = MissionConfig(
        problem_statement="this is a sufficiently long problem statement",
        success_criteria=None,  # type: ignore[arg-type]
    )
    assert cfg.success_criteria == []


def test_success_criteria_list_passes_through():
    cfg = MissionConfig(
        problem_statement="this is a sufficiently long problem statement",
        success_criteria=["a", "b"],
    )
    assert cfg.success_criteria == ["a", "b"]


# ---------------------------------------------------------------------------
# from_request / from_queue_item invalid-input handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [None, "hello", 0, 1.5, [1, 2], object()])
def test_from_request_non_dict_raises_validation_error(bad):
    with pytest.raises(MissionValidationError):
        MissionConfig.from_request(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, "hello", 0, [1, 2]])
def test_from_queue_item_non_dict_raises_validation_error(bad):
    with pytest.raises(MissionValidationError):
        MissionConfig.from_queue_item(bad)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# prune_old_audit_logs negative-value handling
# ---------------------------------------------------------------------------


def _make_mission_dir(root: Path, name: str) -> Path:
    """Create a fake mission dir with a mission_config.json so it qualifies for pruning."""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "mission_config.json").write_text("{}")
    return d


def test_prune_with_negative_max_missions_does_not_indexerror(tmp_path: Path):
    """Negative max_missions used to drive a pop-from-empty IndexError."""
    for i in range(3):
        _make_mission_dir(tmp_path, f"mission_{i}")

    # Should NOT raise. Negative max_missions clamps to 0 → all are pruned.
    pruned = prune_old_audit_logs(tmp_path, max_missions=-5, dry_run=True)
    assert isinstance(pruned, list)
    assert len(pruned) == 3


def test_prune_with_negative_max_total_mb_does_not_indexerror(tmp_path: Path):
    for i in range(2):
        _make_mission_dir(tmp_path, f"mission_{i}")

    pruned = prune_old_audit_logs(
        tmp_path, max_missions=100, max_total_mb=-1.0, dry_run=True
    )
    assert isinstance(pruned, list)


def test_prune_with_zero_max_missions_prunes_all(tmp_path: Path):
    for i in range(2):
        _make_mission_dir(tmp_path, f"mission_{i}")

    pruned = prune_old_audit_logs(tmp_path, max_missions=0, dry_run=True)
    assert len(pruned) == 2


# ---------------------------------------------------------------------------
# Cycle 2 red-team regressions: OverflowError, non-dict metadata, migrate_config
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf")])
def test_cycle_budget_inf_raises_validation_error_not_overflow(bad_value):
    """Red team agent 1 (cycle 2): float('inf') passed as cycle_budget raised
    OverflowError that escaped from_request unhandled. After the fix, it must
    surface as MissionValidationError.
    """
    with pytest.raises(MissionValidationError):
        MissionConfig.from_request(
            {"problem_statement": "x" * 20, "cycle_budget": bad_value}
        )


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf")])
def test_max_iterations_inf_raises_validation_error_not_overflow(bad_value):
    """Same OverflowError path for max_iterations."""
    with pytest.raises(MissionValidationError):
        MissionConfig.from_request(
            {"problem_statement": "x" * 20, "max_iterations": bad_value}
        )


@pytest.mark.parametrize("bad_metadata", ["bad", [1, 2, 3], 42, "x" * 100])
def test_from_queue_item_with_non_dict_metadata_does_not_crash(bad_metadata):
    """Red team agent 1: from_queue_item crashed with ValueError when
    metadata was a non-empty non-dict (string/list/int) because dict('bad')
    raises. The fix coerces non-dict metadata to {} before merging.
    """
    cfg, _ = MissionConfig.from_queue_item(
        {
            "problem_statement": "x" * 20,
            "metadata": bad_metadata,
            "id": "q1",
        },
        mission_id="m1",
    )
    assert isinstance(cfg.metadata, dict)
    # Queue origin metadata is still present.
    assert cfg.metadata.get("queued") is True
    assert cfg.metadata.get("source_queue_item_id") == "q1"


def test_migrate_config_fills_mission_type_default():
    """Red team agent 2: migrate_config did not set mission_type for
    pre-cycle-2 configs. Code reading data['mission_type'] directly would
    KeyError. After fix, mission_type defaults to 'full_rd'.
    """
    from af_engine.mission_config import migrate_config

    pre_cycle2 = {
        "mission_id": "x",
        "problem_statement": "y" * 20,
        "cycle_budget": 3,
        "created_at": "2025-01-01",
    }
    migrated = migrate_config(pre_cycle2)
    assert migrated["mission_type"] == "full_rd"
    # Other fields still backfilled
    assert "max_iterations" in migrated
    assert "llm_provider" in migrated
    assert migrated["config_version"] >= 1
