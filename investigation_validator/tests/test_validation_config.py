from __future__ import annotations

import pytest

from investigation_validator.models import ValidationConfig


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("parallel_validators", 0),
        ("parallel_validators", True),
        ("parallel_validators", 2.5),
        ("fetch_timeout_seconds", 0),
        ("fetch_timeout_seconds", False),
        ("fetch_timeout_seconds", "30"),
        ("cache_ttl_hours", -1),
        ("cache_ttl_hours", True),
        ("cache_ttl_hours", 1.5),
        ("max_claims_per_finding", 0),
        ("max_claims_per_finding", False),
        ("max_claims_per_finding", "20"),
        ("max_source_chars", 0),
        ("max_source_chars", True),
        ("max_source_chars", 50000.0),
    ],
)
def test_validation_config_rejects_invalid_integer_fields(field: str, value: object):
    with pytest.raises(ValueError, match=field):
        ValidationConfig(**{field: value})


def test_validation_config_accepts_documented_integer_boundaries():
    config = ValidationConfig(
        parallel_validators=1,
        fetch_timeout_seconds=1,
        cache_ttl_hours=0,
        max_claims_per_finding=1,
        max_source_chars=1,
    )

    assert config.to_dict()["cache_ttl_hours"] == 0
