import json


def test_pool_ignores_stale_restored_trackers(monkeypatch, tmp_path):
    import subagent_pool_manager

    state_path = tmp_path / "pool_state.json"
    state_path.write_text(json.dumps({
        "total_slots": 50,
        "active_slots": 2,
        "investigations": {
            f"stale_{i}": {
                "investigation_id": f"stale_{i}",
                "priority": 10,
                "requested_slots": 1,
                "active_slots": 0,
                "queued_slots": 0,
                "allocated_quota": 6,
                "started_at": "2026-05-07T16:46:53",
                "query_preview": "old red team validation",
            }
            for i in range(7)
        } | {
            "stale_active": {
                "investigation_id": "stale_active",
                "priority": 10,
                "requested_slots": 20,
                "active_slots": 2,
                "queued_slots": 0,
                "allocated_quota": 6,
                "started_at": "2026-05-07T16:49:25",
                "query_preview": "previous process",
            },
            "stale_queued": {
                "investigation_id": "stale_queued",
                "priority": 10,
                "requested_slots": 20,
                "active_slots": 0,
                "queued_slots": 2,
                "allocated_quota": 6,
                "started_at": "2026-05-07T16:49:25",
                "query_preview": "previous queued process",
            }
        },
        "last_updated": "2026-05-07T16:55:18",
    }))

    monkeypatch.setattr(subagent_pool_manager, "POOL_STATE_FILE", state_path)

    pool = subagent_pool_manager.SubagentPoolManager()
    request = pool.request_slots("inv_new", 20, query="new investigation")

    assert request.granted == 20
    assert request.queued == 0
    assert request.quota_limit == 50
    assert pool.get_status().active_investigations == 1

    pool.notify_investigation_complete("inv_new", elapsed_sec=1, success=True)


def test_single_investigation_can_use_full_pool(monkeypatch, tmp_path):
    import subagent_pool_manager

    monkeypatch.setattr(subagent_pool_manager, "POOL_STATE_FILE", tmp_path / "pool_state.json")

    pool = subagent_pool_manager.SubagentPoolManager()
    request = pool.request_slots("inv_full", 50, query="wide research")

    assert request.granted == 50
    assert request.queued == 0
    assert request.quota_limit == 50

    pool.notify_investigation_complete("inv_full", elapsed_sec=1, success=True)


def test_pool_fair_shares_when_multiple_investigations(monkeypatch, tmp_path):
    import subagent_pool_manager

    monkeypatch.setattr(subagent_pool_manager, "POOL_STATE_FILE", tmp_path / "pool_state.json")

    pool = subagent_pool_manager.SubagentPoolManager()
    first = pool.request_slots("inv_a", 20, query="first")
    second = pool.request_slots("inv_b", 50, query="second")

    assert first.granted == 20
    assert second.granted == 25
    assert second.queued == 25
    assert second.quota_limit == 25

    pool.notify_investigation_complete("inv_a", elapsed_sec=1, success=True)
    pool.notify_investigation_complete("inv_b", elapsed_sec=1, success=True)
