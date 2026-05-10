from __future__ import annotations

import sys
from pathlib import Path

import pytest


AF_ROOT = Path(__file__).resolve().parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

from dashboard_modules import mission_params  # noqa: E402


@pytest.mark.parametrize("value", [2.5, "2.5", object()])
def test_parse_live_int_param_rejects_fractional_or_non_integer_values(value):
    with pytest.raises(ValueError, match="cycle_budget must be an integer"):
        mission_params._parse_live_int_param(value, "cycle_budget")


def test_parse_live_int_param_rejects_bool():
    with pytest.raises(ValueError, match="not bool"):
        mission_params._parse_live_int_param(True, "cycle_budget")


@pytest.mark.parametrize("value", [2, 2.0, "2"])
def test_parse_live_int_param_accepts_integral_values(value):
    assert mission_params._parse_live_int_param(value, "cycle_budget") == 2


def test_clear_history_cache_uses_locked_helper():
    mission_params._HISTORY_CACHE["data"] = [{"mission_id": "x"}]
    mission_params._HISTORY_CACHE["ts"] = 123.0

    mission_params._clear_history_cache()

    assert mission_params._HISTORY_CACHE == {"data": None, "ts": 0.0}
