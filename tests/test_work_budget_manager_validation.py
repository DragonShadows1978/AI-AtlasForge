from __future__ import annotations

import logging

import pytest

from context_watcher.work_budget_manager import (
    WorkBudgetManager,
    _DEFAULT_BUDGET,
    _MAX_BUDGET,
    _resolve_budget_for_model,
)


def test_explicit_budget_tokens_has_upper_bound():
    with pytest.raises(ValueError, match=str(_MAX_BUDGET)):
        WorkBudgetManager("claude-sonnet-4-6", budget_tokens=_MAX_BUDGET + 1)


def test_env_budget_tokens_has_upper_bound(monkeypatch, caplog):
    monkeypatch.setenv("WORK_BUDGET_TOKENS", str(_MAX_BUDGET + 1))

    with caplog.at_level(logging.WARNING):
        assert _resolve_budget_for_model("unknown-model") == _DEFAULT_BUDGET

    assert "WORK_BUDGET_TOKENS" in caplog.text


def test_invalid_env_budget_tokens_logs_warning(monkeypatch, caplog):
    monkeypatch.setenv("WORK_BUDGET_TOKENS", "not-an-int")

    with caplog.at_level(logging.WARNING):
        assert _resolve_budget_for_model("unknown-model") == _DEFAULT_BUDGET

    assert "invalid WORK_BUDGET_TOKENS" in caplog.text
