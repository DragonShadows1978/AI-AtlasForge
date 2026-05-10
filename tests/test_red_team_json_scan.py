from __future__ import annotations

import json

from adversarial_testing.red_team_agent import _find_all_balanced_json


def test_find_all_balanced_json_skips_nested_objects():
    text = 'prefix {"findings": [{"id": 1, "meta": {"nested": true}}]} suffix {"ok": true}'

    candidates = _find_all_balanced_json(text)
    parsed = [json.loads(candidate) for _, _, candidate in candidates]

    assert parsed == [
        {"findings": [{"id": 1, "meta": {"nested": True}}]},
        {"ok": True},
    ]


def test_find_all_balanced_json_continues_after_unclosed_object():
    text = 'bad {"missing": true later {"ok": true}'

    candidates = _find_all_balanced_json(text)

    assert [json.loads(candidate) for _, _, candidate in candidates] == [{"ok": True}]
