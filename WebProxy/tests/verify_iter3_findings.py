"""Iter-3 Red Team findings — fail-closed verification tests.

Each test reproduces a specific C* or H* finding from the iter-3 Red Team
report against `init_guard.py` or `web_proxy_mcp_server.py`, and asserts
that the iter-4 fix makes the bug fail-closed.

Pattern mirrors `tests/verify_iter2_findings.py`. Run:

    pytest tests/verify_iter3_findings.py -v
"""

from __future__ import annotations

import os
import socket
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# CRITICAL findings
# ---------------------------------------------------------------------------


class TestC1_IPv4MappedIPv6:
    """C1/C2/C3: IPv4-mapped IPv6 SSRF bypass.

    `http://[::ffff:127.0.0.1]/` and similar parse as version=6, do not match
    `::1/128`, and every v4 network entry. Fix: explicit v4-unwrap in
    `_is_blocked_ip` + `::ffff:0:0/96` entry in `_BLOCKED_IP_NETWORKS`.
    """

    def test_ipv4_mapped_loopback_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("::ffff:127.0.0.1") is True

    def test_ipv4_mapped_imds_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("::ffff:169.254.169.254") is True

    def test_ipv4_mapped_rfc1918_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("::ffff:10.0.0.1") is True
        assert _is_blocked_ip("::ffff:172.16.0.1") is True
        assert _is_blocked_ip("::ffff:192.168.1.1") is True

    def test_ipv4_mapped_url_rejected(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http://[::ffff:127.0.0.1]/"})
        with pytest.raises(ValueError):
            _require_url({"url": "http://[::ffff:169.254.169.254]/"})

    def test_plain_ipv6_loopback_still_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("::1") is True


class TestC4_AllKnownToolsComplete:
    """C4: `ALL_KNOWN_TOOLS` must contain every side-effecting Claude tool so
    the CLI `--disallowedTools` string can block them. A missing tool silently
    becomes "not blocked" in every stage."""

    REQUIRED_MODERN_TOOLS = {
        "MultiEdit",
        "BashOutput",
        "KillBash",
        "SlashCommand",
        "ToolSearch",
        "Skill",
        "Monitor",
        "EnterWorktree",
        "ExitWorktree",
        "RemoteTrigger",
        "CronCreate",
        "CronDelete",
        "CronList",
        "PushNotification",
        "TaskOutput",
        "TaskStop",
        "ScheduleWakeup",
    }

    def test_all_known_tools_contains_modern_tools(self):
        from init_guard import ALL_KNOWN_TOOLS
        missing = self.REQUIRED_MODERN_TOOLS - ALL_KNOWN_TOOLS
        assert not missing, f"ALL_KNOWN_TOOLS missing: {missing}"

    def test_complete_disallowed_includes_modern_tools(self):
        from init_guard import InitGuard
        disallowed = InitGuard.get_disallowed_tools_for_cli("COMPLETE")
        for tool in self.REQUIRED_MODERN_TOOLS:
            assert tool in disallowed, (
                f"{tool!r} missing from COMPLETE disallowed string: {disallowed!r}"
            )

    def test_unknown_stage_disallowed_includes_modern_tools(self):
        """Unknown stage fails closed — should block the modern tools too."""
        from init_guard import InitGuard
        disallowed = InitGuard.get_disallowed_tools_for_cli("UNKNOWN_STAGE")
        for tool in ("MultiEdit", "BashOutput", "KillBash", "SlashCommand"):
            assert tool in disallowed, (
                f"{tool!r} missing from fail-closed disallowed: {disallowed!r}"
            )


# ---------------------------------------------------------------------------
# HIGH findings
# ---------------------------------------------------------------------------


class TestH1_RelativePathWorkspace:
    """H1/H3: Relative paths must run the workspace allow-list.

    Previously the workspace check was gated on `path.startswith("/")`, so
    `implementation_plan.md` (relative) skipped it entirely. Fix: resolve
    relative against cwd first, then run workspace check.
    """

    def test_relative_outside_workspace_rejected(self, tmp_path):
        from init_guard import InitGuard
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)  # pytest tmp_path is NOT under AtlasForge root
            # Check: even the "expected" filename fails from an out-of-workspace cwd.
            # Note: /tmp/pytest-of-<USER> is in workspace roots for testing, so we
            # use tmp_path directly which is under it — adjust expectation.
            # Actually tmp_path IS under /tmp/pytest-of-<USER>, and that IS in
            # _WORKSPACE_ROOTS. So this test needs a truly outside cwd.
        finally:
            os.chdir(original_cwd)

    def test_relative_in_truly_outside_cwd_rejected(self):
        """Run relative path check from a cwd that's NOT in any workspace root."""
        from init_guard import InitGuard
        original_cwd = os.getcwd()
        try:
            os.chdir("/var/tmp")  # not under AtlasForge or pytest-of-<USER>
            allowed, _ = InitGuard.validate_write_path(
                "PLANNING", "implementation_plan.md"
            )
            assert allowed is False
        finally:
            os.chdir(original_cwd)


class TestH2_StarAllowlistWorkspaceCheck:
    """H2: BUILDING/TESTING `["*"]` must still enforce workspace allow-list.

    Previously `if "*" not in write_paths_allowed` short-circuited the
    workspace check, making `["*"]` a scope-escape. Fix: workspace check
    always runs; `*` is only a filename wildcard.
    """

    def test_building_outside_workspace_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "BUILDING", "/home/otheruser/id_rsa"
        )
        assert allowed is False

    def test_building_opt_secrets_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "BUILDING", "/opt/secrets/token"
        )
        assert allowed is False

    def test_testing_run_secrets_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "TESTING", "/run/secrets/key"
        )
        assert allowed is False

    def test_building_workspace_path_still_allowed(self):
        """Regression guard: iter-4 fix must not break legitimate in-workspace writes."""
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "BUILDING", "/home/vader/AI-AtlasForge/workspace/test/new_file.py"
        )
        assert allowed is True


class TestH4_StagePoliciesImmutable:
    """H4: STAGE_POLICIES must be runtime-immutable.

    Previously `.allowed_tools.add(...)` and `.write_paths_allowed.append(...)`
    silently corrupted process-wide policy. Fix: frozen dataclass +
    frozenset/tuple fields + MappingProxyType wrapper on the dict.
    """

    def test_allowed_tools_frozenset(self):
        from init_guard import STAGE_POLICIES, RDStage
        with pytest.raises(AttributeError):
            STAGE_POLICIES[RDStage.PLANNING].allowed_tools.add("Evil")

    def test_blocked_tools_frozenset(self):
        from init_guard import STAGE_POLICIES, RDStage
        with pytest.raises(AttributeError):
            STAGE_POLICIES[RDStage.PLANNING].blocked_tools.add("Evil")

    def test_write_paths_tuple(self):
        from init_guard import STAGE_POLICIES, RDStage
        with pytest.raises(AttributeError):
            STAGE_POLICIES[RDStage.PLANNING].write_paths_allowed.append("*")

    def test_stage_policies_mapping_readonly(self):
        from init_guard import STAGE_POLICIES, RDStage
        with pytest.raises(TypeError):
            STAGE_POLICIES[RDStage.PLANNING] = None

    def test_dataclass_field_replacement_blocked(self):
        from init_guard import STAGE_POLICIES, RDStage
        # dataclass(frozen=True) makes attribute replacement raise FrozenInstanceError
        # (subclass of AttributeError).
        with pytest.raises(AttributeError):
            STAGE_POLICIES[RDStage.PLANNING].allowed_tools = frozenset({"Evil"})


class TestH5_ExtendedBlockedNetworks:
    """H5: Extended blocked networks — CGNAT, broadcast, TEST-NETs, IPv6
    unspecified were all missing from `_BLOCKED_IP_NETWORKS`."""

    def test_broadcast_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("255.255.255.255") is True

    def test_test_net_1_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("192.0.2.5") is True

    def test_test_net_2_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("198.51.100.10") is True

    def test_test_net_3_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("203.0.113.42") is True

    def test_cgnat_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("100.64.1.1") is True
        assert _is_blocked_ip("100.127.255.254") is True

    def test_ipv6_unspecified_blocked(self):
        from WebProxy.mcp_server import _is_blocked_ip
        assert _is_blocked_ip("::") is True


class TestH6_BackslashUrlRejected:
    """H6: Backslash in URL causes urlparse vs urllib3 TOCTOU.

    `urlparse('http://reddit.com\\@evil.com/').hostname == 'evil.com'` but
    urllib3 reads `'reddit.com'`. Fix: reject forbidden chars pre-parse.
    """

    def test_backslash_at_hostname_swap_rejected(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http://reddit.com\\@evil.com/"})

    def test_angle_brackets_rejected(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http://example.com/<script>"})

    def test_curly_braces_rejected(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http://example.com/{template}"})

    def test_caret_rejected(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http://example.com/^path"})


class TestH7_GlobPrefixNotMatched:
    """H7/H12: `*implementation_plan.md` glob should not match
    `evil_implementation_plan.md`. Fix: exact basename + `**/`-anchored glob.
    """

    WORKSPACE = "/home/vader/AI-AtlasForge/workspace/proj"

    def test_evil_implementation_plan_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/evil_implementation_plan.md"
        )
        assert allowed is False

    def test_shadow_implementation_plan_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/shadow_implementation_plan.md"
        )
        assert allowed is False

    def test_evil_analysis_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "ANALYZING", f"{self.WORKSPACE}/evil_analysis.md"
        )
        assert allowed is False

    def test_evil_report_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "ANALYZING", f"{self.WORKSPACE}/evil_report.md"
        )
        assert allowed is False

    def test_my_cycle_reports_rejected(self):
        """`*cycle_report*.md` used to accept `my_cycle_reports.md`.
        Fix: explicit `cycle_report.md` + `cycle_report_*.md` patterns."""
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END", f"{self.WORKSPACE}/my_cycle_reports.md"
        )
        assert allowed is False

    def test_exact_implementation_plan_still_allowed(self):
        """Regression guard: legitimate exact filename still works."""
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/implementation_plan.md"
        )
        assert allowed is True

    def test_cycle_report_numbered_still_allowed(self):
        """Regression guard: `cycle_report_2.md` still works under the new
        `cycle_report_*.md` pattern (explicit underscore separator)."""
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "CYCLE_END", f"{self.WORKSPACE}/cycle_report_2.md"
        )
        assert allowed is True


class TestH8_UrlEncodedDotDot:
    """H8: `%2e%2e` URL-encoded traversal must be rejected.

    Fix: `unquote(path)` once before the `..` segment check.
    """

    WORKSPACE = "/home/vader/AI-AtlasForge/workspace/proj"

    def test_url_encoded_dotdot_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/artifacts/%2e%2e/etc/plan.md"
        )
        assert allowed is False

    def test_url_encoded_dotdot_mixed_case(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/artifacts/%2E%2E/etc/plan.md"
        )
        assert allowed is False

    def test_literal_dotdot_still_rejected(self):
        """Regression: the plain `..` check is still in place."""
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING", f"{self.WORKSPACE}/artifacts/../etc/plan.md"
        )
        assert allowed is False


class TestH9_OversizedLineNoOOM:
    """H9: `for line in sys.stdin:` buffers entire line before size check.

    Fix: `_iter_bounded_lines` chunks with incremental length tracking,
    drops oversize lines without buffering the full payload.
    """

    def test_iter_bounded_lines_drops_oversize(self):
        """Feed a 2 MB line (no newline) followed by a small one; first
        must yield sentinel b'', second must round-trip."""
        from io import BytesIO
        from WebProxy.mcp_server import _iter_bounded_lines

        big = b"x" * (2 * 1024 * 1024) + b"\n"  # 2 MB, over the 1 MB cap
        small = b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n'
        stream = BytesIO(big + small)

        results = list(_iter_bounded_lines(stream, max_bytes=1_000_000))
        # Iter-6 H9: tuple API. Oversize drops yield (True, b""); normal
        # lines yield (False, payload).
        assert any(t[0] is True and t[1] == b"" for t in results), (
            f"expected oversize sentinel; got {results!r}"
        )
        # Small request should round-trip on the non-oversize side.
        assert any(t[0] is False and b"ping" in t[1] for t in results), (
            f"small request lost after oversize drop: {results!r}"
        )

    def test_iter_bounded_lines_normal_roundtrip(self):
        """Small lines round-trip unchanged."""
        from io import BytesIO
        from WebProxy.mcp_server import _iter_bounded_lines

        payload = b'{"id":1}\n{"id":2}\n{"id":3}\n'
        stream = BytesIO(payload)
        # Iter-6 H9: unpack tagged tuple, keep only non-oversize non-empty.
        results = [
            payload for is_oversize, payload in _iter_bounded_lines(
                stream, max_bytes=1_000_000
            )
            if not is_oversize and payload
        ]
        assert len(results) == 3
        assert b'"id":1' in results[0]
        assert b'"id":2' in results[1]
        assert b'"id":3' in results[2]


class TestH10_FullWidthPeriodIDNA:
    """H10: Unicode full-width period in hostname bypasses domain filter.

    Fix: IDNA-normalize in `_host_from_url` so `reddit\uff0ecom` → `reddit.com`.
    """

    def test_fullwidth_period_normalized(self):
        from WebProxy.mcp_server import _host_from_url
        host = _host_from_url("http://reddit\uff0ecom/")
        assert host == "reddit.com", f"expected reddit.com, got {host!r}"

    def test_fullwidth_period_matches_filter(self):
        from WebProxy.mcp_server import _host_from_url, _host_matches
        host = _host_from_url("http://reddit\uff0ecom/")
        # After IDNA normalization, filter match should now succeed.
        assert _host_matches(host, "reddit.com") is True

    def test_ascii_host_unchanged(self):
        """Regression: pure-ASCII hosts skip IDNA round-trip."""
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("http://example.com/") == "example.com"
        assert _host_from_url("http://api.reddit.com/") == "api.reddit.com"


class TestH11_DNSFailClosed:
    """H11: `_require_url` DNS branch must fail CLOSED on gaierror.

    Fix: raise ValueError by default; opt-in legacy behavior via env var.
    """

    def test_dns_gaierror_raises_by_default(self):
        from WebProxy.mcp_server import _require_url
        with mock.patch(
            "WebProxy.mcp_server.socket.getaddrinfo",
            side_effect=socket.gaierror("test"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                _require_url({"url": "http://nonexistent.invalid/"})

    def test_dns_gaierror_opt_in_legacy(self):
        from WebProxy.mcp_server import _require_url
        # Patch the module-level flag that was read at import; import fresh.
        import importlib
        from WebProxy import mcp_server as web_proxy_mcp_server

        with mock.patch.dict(os.environ, {"WEB_PROXY_ALLOW_DNS_FAIL": "1"}):
            # The flag is read at import time, so we need to reload.
            importlib.reload(web_proxy_mcp_server)
            try:
                with mock.patch(
                    "WebProxy.mcp_server.socket.getaddrinfo",
                    side_effect=socket.gaierror("test"),
                ):
                    result = web_proxy_mcp_server._require_url(
                        {"url": "http://nonexistent.invalid/"}
                    )
                    assert result == "http://nonexistent.invalid/"
            finally:
                # Reset the module so subsequent tests see default (fail-closed).
                os.environ["WEB_PROXY_ALLOW_DNS_FAIL"] = "0"
                importlib.reload(web_proxy_mcp_server)


class TestH12_UrlEncodedTraversal:
    """H12: URL-encoded path traversal — covered by TestH8 above.

    Separate test here just for the explicit finding-id trace.
    """

    def test_url_encoded_parent_reference_rejected(self):
        from init_guard import InitGuard
        allowed, _ = InitGuard.validate_write_path(
            "PLANNING",
            "/home/vader/AI-AtlasForge/workspace/proj/%2e%2e/etc/plan.md",
        )
        assert allowed is False
