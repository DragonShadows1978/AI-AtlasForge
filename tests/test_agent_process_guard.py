from af_engine.agent_process_guard import blocked_reason, targets_protected_process


def test_blocks_pkill_pattern_that_targets_dashboard():
    reason = blocked_reason("pkill", ["-f", "PORT=5055|dashboard_v2.py"])

    assert reason is not None
    assert "AtlasForge process guard blocked" in reason


def test_blocks_mutating_systemctl_for_atlasforge_service():
    reason = blocked_reason("systemctl", ["--user", "restart", "atlasforge"])

    assert reason is not None
    assert "systemctl" in reason


def test_allows_unrelated_pkill_pattern():
    assert blocked_reason("pkill", ["-f", "temporary-test-server-5055"]) is None


def test_detects_atlasforge_service_names():
    assert targets_protected_process(["--user", "restart", "atlasforge-dashboard"])
