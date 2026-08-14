"""WP-R1 tests: safe redirect following, honest status passthrough, 403 render ladder.

All outbound HTTP is mocked — no real network, no bind on 8765/8791.

The fakes mirror the conventions already used by `test_fetch_hardening.py`:
`_pinned_session` is monkeypatched with a `@contextmanager` yielding a fake
session, and DNS is monkeypatched at `_resolve_first_safe_ip`. Unlike that
file, these tests keep `_validate_url_structure` REAL wherever the test is
about redirect-target validation, so the structural half of the SSRF pipeline
is genuinely exercised on every hop.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests

from WebProxy import service as web_proxy_service
from WebProxy.service import (
    RedirectError,
    TargetHTTPError,
    UnsafeUrlError,
    fetch_page,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"",
        headers: dict | None = None,
        encoding: str = "utf-8",
    ):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.encoding = encoding
        self.closed = False

    def raise_for_status(self):
        if not (200 <= self.status_code < 300):
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def iter_content(self, chunk_size=65536):
        if self._body:
            yield self._body

    def close(self):
        self.closed = True


def _html_body(title: str = "Ok", text: str = "hello world") -> bytes:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><main><h1>{title}</h1><p>{text}</p></main></body></html>"
    ).encode("utf-8")


class _RouteSession:
    """Fake session that answers from a {url: _FakeResponse} routing table.

    Records every URL it is asked for, so tests can assert the exact hop
    sequence the follower walked.
    """

    def __init__(self, routes: dict, seen: list):
        self._routes = routes
        self.seen = seen

    def get(self, url, **kwargs):
        # Law 2 guard: the follower must never delegate redirect handling.
        assert kwargs.get("allow_redirects") is False, (
            "allow_redirects must be False on every underlying request"
        )
        self.seen.append(url)
        try:
            return self._routes[url]
        except KeyError:
            raise AssertionError(f"unexpected request for {url}")


def _install_routes(
    monkeypatch,
    routes: dict,
    *,
    resolver=None,
    reddit=False,
    js_render=False,
):
    """Wire a routing table into fetch_page's pinned-session seam.

    `_validate_url_structure` is left REAL. `_resolve_first_safe_ip` defaults
    to a public-looking IP but can be replaced per-test to simulate a hop that
    resolves into private space.
    """
    seen: list = []

    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        yield _RouteSession(routes, seen)

    if resolver is None:
        def resolver(url):  # noqa: ANN001
            return "93.184.216.34"

    monkeypatch.setattr(web_proxy_service, "_resolve_first_safe_ip", resolver)
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda url: reddit)
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: js_render)
    return seen


# ---------------------------------------------------------------------------
# D1 — redirect following
# ---------------------------------------------------------------------------


def test_307_followed_content_returned_and_chain_recorded(monkeypatch):
    """The huggingface case: 307 to a CDN must be followed, not fatal."""
    start = "https://huggingface.co/datasets/x/resolve/main/config.json"
    cdn = "https://cdn-lfs.huggingface.co/repos/abc/config.json"
    routes = {
        start: _FakeResponse(
            status_code=307,
            headers={"content-type": "text/html", "location": cdn},
        ),
        cdn: _FakeResponse(
            status_code=200,
            body=_html_body("Config", "the real payload"),
        ),
    }
    seen = _install_routes(monkeypatch, routes)

    result = fetch_page(start)

    assert result["status_code"] == 200
    assert result["title"] == "Config"
    assert "the real payload" in result["text"]
    # Cache-key contract: `url` stays the ORIGINAL request URL.
    assert result["url"] == start
    assert result["resolved_url"] == cdn
    assert result["redirect_chain"] == [cdn]
    assert seen == [start, cdn]


def test_no_redirect_reports_empty_chain_and_self_resolved_url(monkeypatch):
    url = "https://example.com/plain"
    routes = {url: _FakeResponse(status_code=200, body=_html_body("Plain", "body"))}
    _install_routes(monkeypatch, routes)

    result = fetch_page(url)
    assert result["redirect_chain"] == []
    assert result["resolved_url"] == url


def test_relative_location_is_resolved_against_current_url(monkeypatch):
    start = "https://example.com/a/b/page"
    target = "https://example.com/a/b/final"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={"content-type": "text/html", "location": "final"},
        ),
        target: _FakeResponse(
            status_code=200, body=_html_body("Rel", "relative resolved")
        ),
    }
    seen = _install_routes(monkeypatch, routes)

    result = fetch_page(start)
    assert result["resolved_url"] == target
    assert result["redirect_chain"] == [target]
    assert seen == [start, target]


def test_root_relative_location_is_resolved(monkeypatch):
    start = "https://example.com/deep/page"
    target = "https://example.com/root"
    routes = {
        start: _FakeResponse(
            status_code=301,
            headers={"content-type": "text/html", "location": "/root"},
        ),
        target: _FakeResponse(status_code=200, body=_html_body("Root", "at root")),
    }
    _install_routes(monkeypatch, routes)
    assert fetch_page(start)["resolved_url"] == target


def test_http_to_https_canonicalization_hop(monkeypatch):
    start = "http://example.com/"
    target = "https://example.com/"
    routes = {
        start: _FakeResponse(
            status_code=301,
            headers={"content-type": "text/html", "location": target},
        ),
        target: _FakeResponse(status_code=200, body=_html_body("Secure", "tls page")),
    }
    _install_routes(monkeypatch, routes)

    result = fetch_page(start)
    assert result["status_code"] == 200
    assert result["resolved_url"] == target
    assert result["redirect_chain"] == [target]


# --- SSRF laws -------------------------------------------------------------


def test_midchain_redirect_to_private_ip_is_rejected(monkeypatch):
    """SSRF law 1: EVERY hop is re-resolved. A hop whose DNS lands on a
    private address must abort the whole fetch — the follower may not reuse
    hop 0's clean verdict for hop 1."""
    start = "https://example.com/start"
    evil = "https://internal.example.com/admin"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={"content-type": "text/html", "location": evil},
        ),
        # Deliberately routable: if the guard failed, the fetch would succeed
        # and this test would go green for the wrong reason.
        evil: _FakeResponse(status_code=200, body=_html_body("Pwned", "secrets")),
    }

    resolved: list = []

    def _resolver(url):
        resolved.append(url)
        host = url.split("/")[2]
        if host == "internal.example.com":
            # Mirrors the real guard's behavior on a private resolution.
            raise UnsafeUrlError("dns resolved to a non-public address")
        return "93.184.216.34"

    seen = _install_routes(monkeypatch, routes, resolver=_resolver)

    with pytest.raises(UnsafeUrlError, match="non-public"):
        fetch_page(start)

    # Hop 1 was re-resolved (law 1) and the request was never issued.
    assert resolved == [start, evil]
    assert seen == [start], "the private-IP hop must never be requested"


def test_redirect_to_private_ip_literal_is_rejected_structurally(monkeypatch):
    """A Location pointing straight at an RFC1918 literal is caught by the
    structural half of the pipeline, before DNS is consulted at all."""
    start = "https://example.com/start"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={
                "content-type": "text/html",
                "location": "http://169.254.169.254/latest/meta-data/",
            },
        )
    }
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="unsafe redirect target"):
        fetch_page(start)
    assert seen == [start]


def test_redirect_with_userinfo_is_rejected(monkeypatch):
    start = "https://example.com/start"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={
                "content-type": "text/html",
                "location": "https://user:pass@example.org/x",
            },
        )
    }
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="userinfo"):
        fetch_page(start)
    assert seen == [start]


def test_redirect_to_non_http_scheme_is_rejected(monkeypatch):
    start = "https://example.com/start"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={"content-type": "text/html", "location": "file:///etc/passwd"},
        )
    }
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="unsupported scheme"):
        fetch_page(start)
    assert seen == [start]


def test_redirect_loop_aborts(monkeypatch):
    a = "https://example.com/a"
    b = "https://example.com/b"
    routes = {
        a: _FakeResponse(
            status_code=302, headers={"content-type": "text/html", "location": b}
        ),
        b: _FakeResponse(
            status_code=302, headers={"content-type": "text/html", "location": a}
        ),
    }
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="loop"):
        fetch_page(a)
    # a, b, then the repeat of `a` is caught BEFORE a third request goes out.
    assert seen == [a, b]


def test_redirect_chain_aborts_at_cap(monkeypatch):
    """A non-repeating chain longer than the cap must abort at the cap."""
    monkeypatch.setattr(web_proxy_service, "MAX_REDIRECTS", 3)
    routes = {}
    for i in range(10):
        routes[f"https://example.com/hop{i}"] = _FakeResponse(
            status_code=302,
            headers={
                "content-type": "text/html",
                "location": f"https://example.com/hop{i + 1}",
            },
        )
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="redirect limit exceeded"):
        fetch_page("https://example.com/hop0")
    # cap=3 means hop0 plus 3 followed hops are requested, then abort.
    assert seen == [
        "https://example.com/hop0",
        "https://example.com/hop1",
        "https://example.com/hop2",
        "https://example.com/hop3",
    ]


def test_max_redirects_zero_restores_old_behavior(monkeypatch):
    """min 0 = do not follow. The redirect becomes a hard failure again."""
    monkeypatch.setattr(web_proxy_service, "MAX_REDIRECTS", 0)
    start = "https://example.com/start"
    routes = {
        start: _FakeResponse(
            status_code=302,
            headers={"content-type": "text/html", "location": "https://example.org/"},
        )
    }
    seen = _install_routes(monkeypatch, routes)

    with pytest.raises(RedirectError, match="redirect limit exceeded"):
        fetch_page(start)
    assert seen == [start]


def test_empty_location_header_aborts(monkeypatch):
    start = "https://example.com/start"
    routes = {
        start: _FakeResponse(status_code=302, headers={"content-type": "text/html"})
    }
    _install_routes(monkeypatch, routes)
    with pytest.raises(RedirectError, match="empty Location"):
        fetch_page(start)


def test_every_hop_is_revalidated_and_repinned(monkeypatch):
    """SSRF law 1, positive form: N hops => N structural validations, N DNS
    resolutions, and N distinct pinned sessions."""
    urls = [f"https://h{i}.example.com/" for i in range(4)]
    routes = {}
    for i in range(3):
        routes[urls[i]] = _FakeResponse(
            status_code=307,
            headers={"content-type": "text/html", "location": urls[i + 1]},
        )
    routes[urls[3]] = _FakeResponse(status_code=200, body=_html_body("End", "done"))

    resolved: list = []
    pins: list = []

    def _resolver(url):
        resolved.append(url)
        return "93.184.216.34"

    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        pins.append((pinned_ip, hostname, port))
        yield _RouteSession(routes, [])

    monkeypatch.setattr(web_proxy_service, "_resolve_first_safe_ip", _resolver)
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda url: False)
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: False)

    result = fetch_page(urls[0])

    assert result["redirect_chain"] == urls[1:]
    assert resolved == urls, "every hop must be re-resolved"
    # A fresh pinned session per hop, each bound to that hop's own hostname.
    assert [p[1] for p in pins] == [f"h{i}.example.com" for i in range(4)]
    assert len(pins) == 4


def test_303_is_followed_as_get(monkeypatch):
    start = "https://example.com/submit"
    target = "https://example.com/result"
    routes = {
        start: _FakeResponse(
            status_code=303, headers={"content-type": "text/html", "location": target}
        ),
        target: _FakeResponse(status_code=200, body=_html_body("Result", "see other")),
    }
    _install_routes(monkeypatch, routes)
    assert fetch_page(start)["title"] == "Result"


# ---------------------------------------------------------------------------
# D2 — honest status passthrough
# ---------------------------------------------------------------------------


def test_404_raises_target_http_error_with_status(monkeypatch):
    url = "https://example.com/definitely-not-here-404"
    routes = {
        url: _FakeResponse(
            status_code=404,
            body=b"<html><body>not found</body></html>",
            headers={"content-type": "text/html"},
        )
    }
    _install_routes(monkeypatch, routes)

    with pytest.raises(TargetHTTPError) as excinfo:
        fetch_page(url)
    assert excinfo.value.target_status == 404
    assert excinfo.value.target_status_class == "client_error"
    assert excinfo.value.final_url == url
    # Backwards compatibility: existing `except requests.HTTPError` sites.
    assert isinstance(excinfo.value, requests.HTTPError)


def test_404_error_body_carries_target_status(monkeypatch, tmp_path):
    """End-to-end through /fetch: the endpoint still answers 502, but the JSON
    body now names the real upstream status."""
    monkeypatch.setattr(web_proxy_service, "CACHE_DIR", tmp_path / "cache")
    url = "https://example.com/definitely-not-here-404"
    routes = {
        url: _FakeResponse(
            status_code=404,
            body=b"<html><body>nope</body></html>",
            headers={"content-type": "text/html"},
        )
    }
    _install_routes(monkeypatch, routes)

    app = web_proxy_service.create_app()
    client = app.test_client()
    resp = client.post("/fetch", json={"url": url})

    assert resp.status_code == 502  # endpoint code unchanged (contract)
    body = resp.get_json()
    assert body["error"] == "fetch failed"
    assert body["target_status"] == 404
    assert body["target_status_class"] == "client_error"
    assert body["final_url"] == url
    assert "correlation_id" in body


def test_503_error_body_is_classed_server_error(monkeypatch, tmp_path):
    monkeypatch.setattr(web_proxy_service, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 1)
    url = "https://example.com/down"
    routes = {
        url: _FakeResponse(
            status_code=503, body=b"<html>down</html>", headers={"content-type": "text/html"}
        )
    }
    _install_routes(monkeypatch, routes)

    resp = web_proxy_service.create_app().test_client().post("/fetch", json={"url": url})
    body = resp.get_json()
    assert resp.status_code == 502
    assert body["target_status"] == 503
    assert body["target_status_class"] == "server_error"


def test_connection_error_has_no_target_status(monkeypatch, tmp_path):
    """No HTTP response at all => no invented status. The absence is the
    signal the MCP layer turns into 'no HTTP response (connection/timeout)'."""
    monkeypatch.setattr(web_proxy_service, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 1)
    url = "https://example.com/unreachable"

    class _DeadSession:
        def get(self, url, **kwargs):
            raise requests.ConnectionError("connection refused")

    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        yield _DeadSession()

    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda u: "93.184.216.34"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda u: False)

    resp = web_proxy_service.create_app().test_client().post("/fetch", json={"url": url})
    body = resp.get_json()
    assert resp.status_code == 502
    assert "target_status" not in body
    assert "correlation_id" in body


def test_status_class_helper():
    assert web_proxy_service._status_class(404) == "client_error"
    assert web_proxy_service._status_class(500) == "server_error"
    assert web_proxy_service._status_class(200) is None
    assert web_proxy_service._status_class(None) is None


# --- D2, MCP-side rendering ------------------------------------------------


def test_mcp_describes_target_status_in_tool_text():
    from WebProxy import mcp_server

    text = mcp_server._describe_target_failure(
        {
            "error": "fetch failed",
            "correlation_id": "abc123",
            "target_status": 404,
            "target_status_class": "client_error",
            "final_url": "https://example.com/gone",
        }
    )
    assert "404" in text
    assert "Not Found" in text
    assert "https://example.com/gone" in text


def test_mcp_distinguishes_no_http_response():
    from WebProxy import mcp_server

    text = mcp_server._describe_target_failure(
        {"error": "fetch failed", "correlation_id": "abc123", "url": "https://example.com/x"}
    )
    assert "no HTTP response" in text
    assert "connection/timeout" in text


def test_mcp_proxy_post_raises_target_error_on_502(monkeypatch):
    from WebProxy import mcp_server

    class _Resp:
        status_code = 502

        def json(self):
            return {
                "error": "fetch failed",
                "correlation_id": "cid1",
                "target_status": 403,
                "final_url": "https://example.com/blocked",
            }

        def raise_for_status(self):
            raise requests.HTTPError("502", response=self)

    monkeypatch.setattr(mcp_server.requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(mcp_server.ProxyTargetError) as excinfo:
        mcp_server._proxy_post("/fetch", {"url": "https://example.com/blocked"})
    msg = str(excinfo.value)
    assert "403" in msg and "Forbidden" in msg
    # RuntimeError subclass keeps the existing handler branch working.
    assert isinstance(excinfo.value, RuntimeError)


# ---------------------------------------------------------------------------
# D3 — 403 render ladder
# ---------------------------------------------------------------------------


_CHALLENGE_HTML = (
    b"<html><head><title>Just a moment...</title></head>"
    b"<body><div class='cf-mitigated'>checking your browser</div>"
    b"<script>challenge()</script></body></html>"
)


def test_403_html_challenge_routes_into_render_and_succeeds(monkeypatch):
    """D3: the 403 body must survive to should_render(), and a render that
    yields real content (>= 200 chars) is returned as a success."""
    from WebProxy import js_render

    url = "https://example.com/protected"
    # Both the plain UA and the browser-UA retry get the same 403 challenge.
    routes = {
        url: _FakeResponse(
            status_code=403,
            body=_CHALLENGE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    }
    _install_routes(monkeypatch, routes, js_render=True)

    rendered_text = "R" * 500
    render_calls: list = []

    def _fake_render(target, *args, **kwargs):
        render_calls.append(target)
        return {
            "rendered_html": f"<html><body><p>{rendered_text}</p></body></html>",
            "rendered_text": rendered_text,
            "rendered_title": "Protected Page",
        }

    monkeypatch.setattr(js_render, "render", _fake_render)

    result = fetch_page(url)

    assert render_calls == [url], "the 403 body must reach the render ladder"
    assert result["js_rendered"] is True
    assert result["original_status"] == 403
    assert result["status_code"] == 200
    assert result["text_length"] >= 200


def test_403_render_producing_thin_content_still_fails_with_status(monkeypatch):
    """A render that yields nothing real is NOT a success — it fails per D2."""
    from WebProxy import js_render

    url = "https://example.com/protected-thin"
    routes = {
        url: _FakeResponse(
            status_code=403,
            body=_CHALLENGE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    }
    _install_routes(monkeypatch, routes, js_render=True)

    monkeypatch.setattr(
        js_render,
        "render",
        lambda target, *a, **k: {
            "rendered_html": "<html><body><p>tiny</p></body></html>",
            "rendered_text": "tiny",
            "rendered_title": "",
        },
    )

    with pytest.raises(TargetHTTPError) as excinfo:
        fetch_page(url)
    assert excinfo.value.target_status == 403
    assert excinfo.value.target_status_class == "client_error"


def test_403_render_unavailable_fails_with_status_403(monkeypatch):
    """Playwright missing / render disabled: honest 403, not a silent 502."""
    url = "https://example.com/protected-norender"
    routes = {
        url: _FakeResponse(
            status_code=403,
            body=_CHALLENGE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    }
    _install_routes(monkeypatch, routes, js_render=False)

    with pytest.raises(TargetHTTPError) as excinfo:
        fetch_page(url)
    assert excinfo.value.target_status == 403


def test_403_body_is_captured_not_discarded(monkeypatch):
    """The named residual from the prior hardening pass: _stream_capped_body
    raised on non-2xx, so the render path never saw the body. The
    any-status variant must return the bytes."""
    resp = _FakeResponse(
        status_code=403,
        body=_CHALLENGE_HTML,
        headers={"content-type": "text/html"},
    )
    status, ctype, body = web_proxy_service._stream_capped_body_any_status(resp)
    assert status == 403
    assert body == _CHALLENGE_HTML
    assert resp.closed is True


def test_403_render_ladder_after_redirect_uses_final_url(monkeypatch):
    """A 403 reached THROUGH a redirect renders the final URL, not the original."""
    from WebProxy import js_render

    start = "https://example.com/go"
    final = "https://cdn.example.com/protected"
    routes = {
        start: _FakeResponse(
            status_code=302, headers={"content-type": "text/html", "location": final}
        ),
        final: _FakeResponse(
            status_code=403,
            body=_CHALLENGE_HTML,
            headers={"content-type": "text/html; charset=utf-8"},
        ),
    }
    _install_routes(monkeypatch, routes, js_render=True)

    render_calls: list = []

    def _fake_render(target, *args, **kwargs):
        render_calls.append(target)
        text = "S" * 400
        return {
            "rendered_html": f"<html><body><p>{text}</p></body></html>",
            "rendered_text": text,
            "rendered_title": "CDN",
        }

    monkeypatch.setattr(js_render, "render", _fake_render)

    result = fetch_page(start)
    assert render_calls == [final]
    assert result["resolved_url"] == final
    assert result["redirect_chain"] == [final]
    assert result["original_status"] == 403
