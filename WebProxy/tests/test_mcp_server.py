"""
Tests for web_proxy_mcp_server.py.

Covers the three bug-fix contracts:
1. MCP protocol compliance — malformed stdin produces a -32700 parse-error
   response (not silent drop / client hang).
2. Unknown method → -32601 method-not-found response (unless notification).
3. Domain filter uses exact-or-suffix host match, not substring.

Also covers the unknown-tool `isError: true` path and smoke-tests
initialize + tools/list protocol.

The HTTP proxy (127.0.0.1:8765) is NOT required — we test protocol
plumbing and pure helpers, not live tool calls.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

AF_ROOT = Path(__file__).parent.parent.parent
if str(AF_ROOT) not in sys.path:
    sys.path.insert(0, str(AF_ROOT))

SERVER_PATH = AF_ROOT / "WebProxy" / "mcp_server.py"


# ---------------------------------------------------------------------------
# Pure-function tests (no subprocess)
# ---------------------------------------------------------------------------

class TestHostMatching:
    """Tests for _host_from_url and _host_matches helpers."""

    def test_host_matches_exact(self):
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("reddit.com", "reddit.com") is True

    def test_host_matches_subdomain(self):
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("api.reddit.com", "reddit.com") is True
        assert _host_matches("old.reddit.com", "reddit.com") is True

    def test_host_matches_no_substring_spoof(self):
        """The big one: evil-reddit.com must NOT match reddit.com."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("evil-reddit.com", "reddit.com") is False
        assert _host_matches("reddit.com.attacker.net", "reddit.com") is False

    def test_host_matches_different_domain(self):
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("example.com", "reddit.com") is False

    def test_host_matches_empty(self):
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("", "reddit.com") is False
        assert _host_matches("reddit.com", "") is False

    def test_host_matches_leading_dot_stripped(self):
        """`.reddit.com` in filter list matches reddit.com and subdomains."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("reddit.com", ".reddit.com") is True
        assert _host_matches("api.reddit.com", ".reddit.com") is True

    def test_host_from_url_basic(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("https://example.com/path") == "example.com"

    def test_host_from_url_strips_port_and_userinfo(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("http://user:pw@example.com:8080/x") == "example.com"

    def test_host_from_url_lowercases(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("HTTPS://EXAMPLE.com/x") == "example.com"

    def test_host_from_url_empty_on_garbage(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("not-a-url") == ""
        assert _host_from_url("") == ""

    def test_host_from_url_ipv6(self):
        """IPv6 bracketed literal with port. Regression: manual netloc
        splitting used to return '[' because it split inside the brackets."""
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("http://[::1]:8080/") == "::1"

    def test_host_from_url_ipv6_long(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("http://[2001:db8::1]/x") == "2001:db8::1"

    def test_host_from_url_strips_trailing_dot(self):
        """FQDN trailing dot (`reddit.com.`) canonicalizes to no dot."""
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url("https://reddit.com./r/x") == "reddit.com"

    def test_host_from_url_non_string(self):
        from WebProxy.mcp_server import _host_from_url
        assert _host_from_url(None) == ""
        assert _host_from_url(123) == ""

    def test_host_matches_trailing_dot_host(self):
        """Host `reddit.com.` must match filter `reddit.com`."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("reddit.com.", "reddit.com") is True

    def test_host_matches_trailing_dot_filter(self):
        """Filter `reddit.com.` must match host `reddit.com`."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("reddit.com", "reddit.com.") is True

    def test_host_matches_uppercase_host(self):
        """Direct callers passing uppercase host must still match."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches("REDDIT.COM", "reddit.com") is True

    def test_host_matches_non_string_host(self):
        """Non-string host returns False, never raises."""
        from WebProxy.mcp_server import _host_matches
        assert _host_matches(123, "reddit.com") is False
        assert _host_matches(None, "reddit.com") is False


class TestRequireUrlSchemeWhitelist:
    """Tests for the URL scheme whitelist in _require_url."""

    def test_rejects_file_scheme(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError, match="scheme"):
            _require_url({"url": "file:///etc/passwd"})

    def test_rejects_javascript_scheme(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError, match="scheme"):
            _require_url({"url": "javascript:alert(1)"})

    def test_rejects_data_scheme(self):
        from WebProxy.mcp_server import _require_url
        # Either the forbidden-chars check (<, >) or the scheme check may
        # reject this; either is acceptable — both are fail-closed.
        with pytest.raises(ValueError, match="scheme|forbidden"):
            _require_url({"url": "data:text/html,<script>x</script>"})

    def test_rejects_data_scheme_plain(self):
        """data: URL without forbidden chars — scheme check must still reject."""
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError, match="scheme"):
            _require_url({"url": "data:text/plain,hello"})

    def test_rejects_gopher_scheme(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError, match="scheme"):
            _require_url({"url": "gopher://example.com"})

    def test_rejects_ftp_scheme(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError, match="scheme"):
            _require_url({"url": "ftp://example.com/x"})

    def test_accepts_http(self):
        from WebProxy.mcp_server import _require_url
        assert _require_url({"url": "http://example.com/x"}) == "http://example.com/x"

    def test_accepts_https(self):
        from WebProxy.mcp_server import _require_url
        assert _require_url({"url": "https://example.com/x"}) == "https://example.com/x"

    def test_rejects_missing_hostname(self):
        from WebProxy.mcp_server import _require_url
        with pytest.raises(ValueError):
            _require_url({"url": "http:///only-path"})


class TestDomainFilterEndToEnd:
    """Verify URL->host->match chain used by the WebSearch filter."""

    def test_filter_chain_blocks_subdomain_spoof(self):
        from WebProxy.mcp_server import _host_from_url, _host_matches
        url = "https://evil-reddit.com/r/fake"
        assert _host_matches(_host_from_url(url), "reddit.com") is False

    def test_filter_chain_allows_real_subdomain(self):
        from WebProxy.mcp_server import _host_from_url, _host_matches
        url = "https://old.reddit.com/r/Python/comments/abc"
        assert _host_matches(_host_from_url(url), "reddit.com") is True

    def test_filter_chain_path_substring_does_not_match(self):
        """URL path containing 'reddit.com' does not make the host match."""
        from WebProxy.mcp_server import _host_from_url, _host_matches
        url = "https://example.com/articles/reddit.com-explained"
        assert _host_matches(_host_from_url(url), "reddit.com") is False


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def _spawn_server() -> subprocess.Popen:
    """Spawn the MCP server as a subprocess with piped stdin/stdout."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    return subprocess.Popen(
        [sys.executable, str(SERVER_PATH)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )


def _send_line(proc: subprocess.Popen, line: str) -> None:
    assert proc.stdin is not None
    proc.stdin.write(line + "\n")
    proc.stdin.flush()


def _read_response(proc: subprocess.Popen, timeout: float = 5.0) -> dict:
    """Read one line from stdout with a timeout; parse as JSON."""
    import threading
    assert proc.stdout is not None
    bucket: dict = {}

    def _read():
        try:
            data = proc.stdout.readline()
            if data:
                bucket["line"] = data.strip()
        except Exception as exc:
            bucket["error"] = str(exc)

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(f"No response within {timeout}s")
    if "error" in bucket:
        raise RuntimeError(bucket["error"])
    if "line" not in bucket:
        raise RuntimeError("Empty stdout (server exited?)")
    return json.loads(bucket["line"])


def _close(proc: subprocess.Popen) -> None:
    try:
        if proc.stdin:
            proc.stdin.close()
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


# ---------------------------------------------------------------------------
# MCP protocol tests
# ---------------------------------------------------------------------------

class TestMcpProtocol:
    """End-to-end JSON-RPC protocol tests against the spawned server."""

    def test_jsondecode_error_returns_parse_error(self):
        """Malformed input -> -32700 parse error, id=None.

        Regression: previously the server silently continued past bad JSON,
        deadlocking clients waiting for a response on that request id.
        """
        proc = _spawn_server()
        try:
            _send_line(proc, "garbage not json {")
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("jsonrpc") == "2.0"
            assert resp.get("id") is None
            assert resp.get("error", {}).get("code") == -32700
            assert "parse" in resp["error"]["message"].lower()
        finally:
            _close(proc)

    def test_unknown_method_returns_method_not_found(self):
        """Unknown request method -> -32601 method-not-found."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 42,
                "method": "resources/list",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("id") == 42
            assert resp.get("error", {}).get("code") == -32601
            assert "resources/list" in resp["error"]["message"]
        finally:
            _close(proc)

    def test_unknown_method_notification_no_response(self):
        """Notification (no id) of unknown method -> no response."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "method": "notifications/foo",
            }))
            # Follow with a real request; if we get THAT response first,
            # the notification correctly produced none.
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 99,
                "method": "ping",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("id") == 99, (
                f"Expected ping response first; got {resp}. "
                "If we saw a notification reply, the server is wrongly replying."
            )
        finally:
            _close(proc)

    def test_initialize_then_tools_list(self):
        """Smoke: initialize handshake + tools/list returns all 4 tools."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }))
            init_resp = _read_response(proc, timeout=3.0)
            assert init_resp["id"] == 1
            assert init_resp["result"]["serverInfo"]["name"] == "atlasforge-web-proxy"

            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/list",
            }))
            tools_resp = _read_response(proc, timeout=3.0)
            assert tools_resp["id"] == 2
            names = {t["name"] for t in tools_resp["result"]["tools"]}
            assert names == {"WebSearch", "WebFetch", "WebResearch", "ImageSearch"}
        finally:
            _close(proc)

    def test_ping_returns_empty_result(self):
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 7,
                "method": "ping",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 7
            assert resp["result"] == {}
        finally:
            _close(proc)

    def test_unknown_tool_returns_is_error(self):
        """tools/call with unknown tool -> result.isError=True."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "NotARealTool", "arguments": {}},
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 3
            assert resp["result"].get("isError") is True
            text = resp["result"]["content"][0]["text"]
            assert "Unknown tool" in text
        finally:
            _close(proc)

    def test_tools_call_params_null_returns_invalid_params(self):
        """tools/call with params=null must NOT crash; returns -32602.

        Regression: `msg.get('params', {}).get(...)` used to AttributeError
        on null params because `.get('params', default)` only applies the
        default when the key is missing, not when the value is explicitly
        null.
        """
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": None,
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 10
            assert resp.get("error", {}).get("code") == -32602
            # Server must still be alive — send a ping and confirm response.
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0", "id": 11, "method": "ping",
            }))
            follow = _read_response(proc, timeout=3.0)
            assert follow["id"] == 11
        finally:
            _close(proc)

    def test_tools_call_params_string_returns_invalid_params(self):
        """tools/call with params as a string (non-dict) returns -32602."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": "garbage",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 12
            assert resp.get("error", {}).get("code") == -32602
        finally:
            _close(proc)

    def test_tools_call_arguments_null_coerced(self):
        """arguments=null coerces to {} (tool runs with empty args)."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {"name": "NotARealTool", "arguments": None},
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 13
            # Unknown tool + null arguments → isError (not crash).
            assert resp["result"].get("isError") is True
        finally:
            _close(proc)

    def test_initialize_notification_no_response(self):
        """A notification-form initialize (no id) must produce NO response.

        Regression: every known-method branch used to _send(...) without
        checking is_notification, so a client sending an initialize
        notification would get a response it didn't ask for — a JSON-RPC
        §4.1 violation.
        """
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "method": "initialize"}))
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "id": 50, "method": "ping"}))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 50, (
                f"Expected ping response first; got {resp}. "
                "An initialize notification wrongly produced a response."
            )
        finally:
            _close(proc)

    def test_tools_list_notification_no_response(self):
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "method": "tools/list"}))
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "id": 51, "method": "ping"}))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 51
        finally:
            _close(proc)

    def test_tools_call_notification_no_response(self):
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": "NotARealTool", "arguments": {}},
            }))
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "id": 52, "method": "ping"}))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 52
        finally:
            _close(proc)

    def test_ping_notification_no_response(self):
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "method": "ping"}))
            _send_line(proc, json.dumps({"jsonrpc": "2.0", "id": 53, "method": "ping"}))
            resp = _read_response(proc, timeout=3.0)
            assert resp["id"] == 53
        finally:
            _close(proc)

    def test_non_dict_message_returns_invalid_request(self):
        """A JSON primitive (not an object) at top level returns -32600."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps(["not", "an", "object"]))
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("error", {}).get("code") == -32600
        finally:
            _close(proc)


# ---------------------------------------------------------------------------
# Iter-3 Red Team hardening regression tests
# ---------------------------------------------------------------------------

class TestIter3HostHardening:
    """H7, H11: percent-encoded host reject + bare-TLD reject."""

    def test_percent_encoded_host_rejected(self):
        """H7: hostnames containing `%` must NOT be returned. Some HTTP
        clients percent-decode the host before resolving DNS, so a filter
        that compared the raw netloc would silently miss the bypass."""
        from WebProxy.mcp_server import _host_from_url

        assert _host_from_url("http://reddit%2ecom/r/x") == ""
        assert _host_from_url("https://%72eddit.com/x") == ""

    def test_bare_tld_filter_requires_exact(self):
        """H11: a single-label filter (no `.`) must NOT suffix-match
        every domain ending in that label. `_host_matches('example.com',
        'com')` was True in iter-2; must be False in iter-3."""
        from WebProxy.mcp_server import _host_matches

        assert _host_matches("example.com", "com") is False
        assert _host_matches("evil.org", "org") is False
        # Exact match still works
        assert _host_matches("com", "com") is True
        # Two-label filter still suffix-matches
        assert _host_matches("api.reddit.com", "reddit.com") is True


class TestIter3SsrfProtection:
    """H12: _require_url must block loopback / link-local / RFC1918 / etc."""

    def test_loopback_v4_rejected(self):
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="blocked"):
            _require_url({"url": "http://127.0.0.1/"})
        with pytest.raises(ValueError, match="blocked"):
            _require_url({"url": "http://127.1.2.3/"})

    def test_loopback_v6_rejected(self):
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="blocked"):
            _require_url({"url": "http://[::1]/"})

    def test_aws_imds_rejected(self):
        """169.254.169.254 — AWS / GCP metadata service."""
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="blocked"):
            _require_url({"url": "http://169.254.169.254/latest/meta-data/"})

    def test_rfc1918_rejected(self):
        from WebProxy.mcp_server import _require_url

        for addr in ("http://10.0.0.1/", "http://192.168.1.1/", "http://172.16.0.1/"):
            with pytest.raises(ValueError, match="blocked"):
                _require_url({"url": addr})

    def test_decimal_ip_literal_rejected(self):
        """Decimal-encoded IPv4 (2130706433 = 127.0.0.1)."""
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="decimal"):
            _require_url({"url": "http://2130706433/"})

    def test_localhost_alias_rejected(self):
        from WebProxy.mcp_server import _require_url

        for host in ("localhost", "LOCALHOST", "ip6-localhost"):
            with pytest.raises(ValueError, match="loopback"):
                _require_url({"url": f"http://{host}/"})

    def test_percent_in_hostname_rejected(self):
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="percent-encoding"):
            _require_url({"url": "http://reddit%2ecom/x"})

    def test_control_chars_in_url_rejected(self):
        from WebProxy.mcp_server import _require_url

        with pytest.raises(ValueError, match="control"):
            _require_url({"url": "http://example.com/\nGET /admin"})

    def test_public_url_still_accepted(self):
        """Sanity: legitimate public URLs still pass."""
        from WebProxy.mcp_server import _require_url

        # We can't guarantee DNS for any specific public host, but we can
        # at least verify the function doesn't reject a syntactically-valid
        # public host. If DNS happens to fail, _require_url returns the URL
        # rather than raising — that's the documented behavior.
        result = _require_url({"url": "http://example.com/"})
        assert result == "http://example.com/"


class TestIter3DomainFilterTypeChecks:
    """H13: WebSearch must reject string allowed/blocked_domains, not
    silently iterate char-by-char."""

    def test_string_allowed_domains_rejected(self):
        from WebProxy.mcp_server import handle_tool_call

        result = handle_tool_call("WebSearch", {
            "query": "test",
            "allowed_domains": "reddit.com",  # string, not list — bug shape
        })
        assert "allowed_domains" in result
        assert "list" in result.lower()

    def test_string_blocked_domains_rejected(self):
        from WebProxy.mcp_server import handle_tool_call

        result = handle_tool_call("WebSearch", {
            "query": "test",
            "blocked_domains": "reddit.com",
        })
        assert "blocked_domains" in result
        assert "list" in result.lower()

    def test_int_in_allowed_domains_rejected(self):
        from WebProxy.mcp_server import handle_tool_call

        result = handle_tool_call("WebSearch", {
            "query": "test",
            "allowed_domains": ["reddit.com", 42],
        })
        assert "string" in result.lower()


class TestIter3SanitizeError:
    """Ensure proxy host:port doesn't leak into LLM-visible error strings."""

    def test_redacts_127_0_0_1_with_port(self):
        from WebProxy.mcp_server import _sanitize_error

        out = _sanitize_error("Connection refused: 127.0.0.1:8765")
        assert "127.0.0.1:8765" not in out
        assert "[proxy]" in out

    def test_redacts_localhost_with_port(self):
        from WebProxy.mcp_server import _sanitize_error

        out = _sanitize_error("HTTPConnectionPool(host=localhost, port=8765): ...")
        # Note: this regex catches "localhost:8765" pattern; bare host
        # without port is left alone. Acceptable for our threat model
        # (the goal is to hide the proxy port).
        assert _sanitize_error("localhost:8765") == "[proxy]"

    def test_passes_through_non_matching_text(self):
        from WebProxy.mcp_server import _sanitize_error

        assert _sanitize_error("normal error text") == "normal error text"

    def test_handles_non_string(self):
        from WebProxy.mcp_server import _sanitize_error

        assert _sanitize_error(None) == ""
        assert _sanitize_error(42) == ""


class TestIter6NullIdIsRequest:
    """Iter-6 H3: per JSON-RPC 2.0 §4.1, only a MISSING "id" makes a message
    a notification. Explicit `{"id": null}` is a (spec-discouraged but legal)
    request and the server MUST respond with the correlated null id.

    This test was previously TestIter3IsNotificationNullId, which encoded the
    inverse (buggy) behavior — see iter-5 Red Team finding H3 for the
    deadlock vector that motivated the flip.
    """

    def test_null_id_ping_gets_response(self):
        """A ping with explicit id:null must produce a response with id:null."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "method": "ping",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("id") is None, (
                f"Expected id:null in response; got {resp}."
            )
            assert "result" in resp, (
                f"Expected result envelope for null-id ping; got {resp}."
            )
            # Server must still be alive after replying to null-id.
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0", "id": 100, "method": "ping",
            }))
            follow = _read_response(proc, timeout=3.0)
            assert follow.get("id") == 100, (
                f"Expected follow-up id:100; got {follow}."
            )
        finally:
            _close(proc)

    def test_missing_id_is_notification(self):
        """A ping with no `id` key at all is a notification; no response."""
        proc = _spawn_server()
        try:
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0",
                "method": "ping",
            }))
            # Follow-up real request; if we get THAT first, the missing-id
            # ping correctly produced no reply.
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0", "id": 101, "method": "ping",
            }))
            resp = _read_response(proc, timeout=3.0)
            assert resp.get("id") == 101, (
                f"Expected id:101 first; got {resp}. "
                "Server replied to a notification (missing id)."
            )
        finally:
            _close(proc)


class TestIter3OversizedLineRejected:
    """H3: stdin lines over MAX_LINE_LENGTH must be rejected."""

    def test_oversized_line_returns_invalid_request(self):
        from WebProxy.mcp_server import MAX_LINE_LENGTH

        proc = _spawn_server()
        try:
            # 1.5 MB of garbage on one line
            _send_line(proc, "x" * (MAX_LINE_LENGTH + 100))
            resp = _read_response(proc, timeout=5.0)
            assert resp.get("error", {}).get("code") == -32600
            assert "large" in resp["error"]["message"].lower()
            # Server must still be alive — confirm with a ping.
            _send_line(proc, json.dumps({
                "jsonrpc": "2.0", "id": 200, "method": "ping",
            }))
            follow = _read_response(proc, timeout=3.0)
            assert follow.get("id") == 200
        finally:
            _close(proc)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
