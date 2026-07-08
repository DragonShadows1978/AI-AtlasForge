"""Unit tests for fetch-path hardening: retry/backoff, 403 UA-retry, Reddit ladder.

All outbound HTTP is mocked — no real network, no bind on 8765.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json

import pytest
import requests

from WebProxy import service as web_proxy_service
from WebProxy.service import (
    BROWSER_USER_AGENT,
    USER_AGENT,
    fetch_page,
    fetch_reddit,
    _compute_backoff_s,
    _reddit_fetch_candidates,
    _reddit_json_url,
    _session_get_with_retry,
)


# ---------------------------------------------------------------------------
# Shared fakes
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
            raise requests.HTTPError(
                f"{self.status_code} error", response=self
            )

    def iter_content(self, chunk_size=65536):
        if self._body:
            yield self._body

    def close(self):
        self.closed = True


def _html_body(title: str = "Ok", text: str = "hello world") -> bytes:
    return (
        f"<html><head><title>{title}</title></head>"
        f"<body><main><h1>{title}</h1><p>{text}</p>"
        f'<a href="/next">Next</a></main></body></html>'
    ).encode("utf-8")


def _reddit_listing_body() -> bytes:
    payload = [
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "ladder post",
                            "author": "alice",
                            "score": 42,
                            "num_comments": 1,
                            "link_flair_text": None,
                            "subreddit": "python",
                            "created_utc": 0,
                            "url": "https://example.com",
                            "permalink": "/r/python/comments/abc123/ladder_post/",
                            "selftext": "body",
                            "is_self": True,
                            "over_18": False,
                            "stickied": False,
                        },
                    }
                ]
            },
        },
        {"kind": "Listing", "data": {"children": []}},
    ]
    return json.dumps(payload).encode("utf-8")


def _install_page_session(monkeypatch, session):
    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield session

    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "example.com", 443), None),
    )
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda url: False)
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: False)


def _install_reddit_session(monkeypatch, session_factory):
    """session_factory(url, headers) -> session-like with .get"""

    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        # Hostname is baked in at pin time; session.get still sees full URL.
        yield session_factory(hostname)

    monkeypatch.setattr(web_proxy_service, "_ensure_safe_url", lambda url: None)
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)


# ---------------------------------------------------------------------------
# Pure helper tests
# ---------------------------------------------------------------------------


def test_compute_backoff_respects_retry_after(monkeypatch):
    monkeypatch.setattr(web_proxy_service, "RETRY_AFTER_MAX_S", 60.0)
    monkeypatch.setattr(web_proxy_service, "RETRY_BASE_DELAY_S", 0.5)
    monkeypatch.setattr(web_proxy_service, "RETRY_JITTER", False)
    resp = _FakeResponse(status_code=429, headers={"Retry-After": "7"})
    assert _compute_backoff_s(0, response=resp) == 7.0


def test_compute_backoff_exponential_without_jitter(monkeypatch):
    monkeypatch.setattr(web_proxy_service, "RETRY_BASE_DELAY_S", 0.5)
    monkeypatch.setattr(web_proxy_service, "RETRY_JITTER", False)
    assert _compute_backoff_s(0) == 0.5
    assert _compute_backoff_s(1) == 1.0
    assert _compute_backoff_s(2) == 2.0


def test_reddit_json_url_host_override():
    u = "https://www.reddit.com/r/python/comments/abc/title/"
    assert "www.reddit.com" in _reddit_json_url(u)
    old = _reddit_json_url(u, host="old.reddit.com")
    assert old.startswith("https://old.reddit.com/")
    assert old.endswith(".json?limit=500&depth=10") or ".json?" in old


def test_reddit_fetch_candidates_include_old(monkeypatch):
    monkeypatch.setattr(web_proxy_service, "REDDIT_OLD_FALLBACK", True)
    cands = _reddit_fetch_candidates(
        "https://www.reddit.com/r/python/comments/abc/x/"
    )
    hosts = [c[0] for c in cands]
    assert any("www.reddit.com" in h for h in hosts)
    assert any("old.reddit.com" in h for h in hosts)


# ---------------------------------------------------------------------------
# _session_get_with_retry
# ---------------------------------------------------------------------------


def test_session_get_retries_429_with_backoff(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(web_proxy_service, "RETRY_BASE_DELAY_S", 0.5)
    monkeypatch.setattr(web_proxy_service, "RETRY_JITTER", False)
    monkeypatch.setattr(web_proxy_service, "RETRY_AFTER_MAX_S", 60.0)

    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(
                    status_code=429,
                    headers={"Retry-After": "2", "content-type": "text/plain"},
                )
            return _FakeResponse(
                status_code=200,
                body=_html_body(),
                headers={"content-type": "text/html"},
            )

    resp = _session_get_with_retry(
        _Session(),
        "https://example.com/",
        headers={"User-Agent": USER_AGENT},
        timeout_s=5.0,
    )
    assert resp.status_code == 200
    assert calls["n"] == 2
    assert sleeps == [2.0]  # Retry-After respected


def test_session_get_does_not_retry_403(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)

    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            return _FakeResponse(status_code=403, body=b"blocked")

    resp = _session_get_with_retry(
        _Session(),
        "https://example.com/",
        headers={"User-Agent": USER_AGENT},
        timeout_s=5.0,
    )
    assert resp.status_code == 403
    assert calls["n"] == 1
    assert sleeps == []


def test_session_get_retries_connection_error(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(web_proxy_service, "RETRY_BASE_DELAY_S", 0.5)
    monkeypatch.setattr(web_proxy_service, "RETRY_JITTER", False)

    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise requests.ConnectionError("blip")
            return _FakeResponse(status_code=200, body=b"ok")

    resp = _session_get_with_retry(
        _Session(),
        "https://example.com/",
        headers={"User-Agent": USER_AGENT},
        timeout_s=5.0,
    )
    assert resp.status_code == 200
    assert calls["n"] == 3
    assert sleeps == [0.5, 1.0]


# ---------------------------------------------------------------------------
# fetch_page: 403 UA-retry + success shape
# ---------------------------------------------------------------------------


def test_fetch_page_403_triggers_ua_retry_once(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_proxy_service, "UA_RETRY_ON_403", True)
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)

    seen_uas: list[str] = []
    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            ua = (kwargs.get("headers") or {}).get("User-Agent", "")
            seen_uas.append(ua)
            if ua == USER_AGENT:
                return _FakeResponse(status_code=403, body=b"cf block")
            return _FakeResponse(
                status_code=200,
                body=_html_body("Recovered", "cloudflare recovered"),
                headers={"content-type": "text/html; charset=utf-8"},
            )

    _install_page_session(monkeypatch, _Session())

    result = fetch_page("https://example.com/news")
    assert result["status_code"] == 200
    assert result["title"] == "Recovered"
    assert "cloudflare recovered" in result["text"]
    assert calls["n"] == 2  # one default UA + one browser UA; no transient retries
    assert seen_uas[0] == USER_AGENT
    assert seen_uas[1] == BROWSER_USER_AGENT
    assert sleeps == []  # 403 is not a transient backoff path


def test_fetch_page_403_not_transient_retried_then_ua_retry_still_fails(monkeypatch):
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(web_proxy_service, "UA_RETRY_ON_403", True)
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)

    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            return _FakeResponse(status_code=403, body=b"nope")

    _install_page_session(monkeypatch, _Session())

    with pytest.raises(requests.HTTPError):
        fetch_page("https://example.com/blocked")
    # default UA once + browser UA once — not 3x transient retries
    assert calls["n"] == 2


def test_fetch_page_success_output_shape_unchanged(monkeypatch):
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: False)

    class _Session:
        def get(self, url, **kwargs):
            return _FakeResponse(
                status_code=200,
                body=_html_body("Shape", "stable contract text"),
            )

    _install_page_session(monkeypatch, _Session())
    result = fetch_page("https://example.com/story", max_chars=5000)

    # Stable contract consumed by cache + mcp_server
    for key in ("url", "title", "meta_description", "headings", "text", "links", "text_length", "status_code"):
        assert key in result
    assert result["title"] == "Shape"
    assert "stable contract text" in result["text"]
    assert result["headings"] == ["Shape"]
    assert isinstance(result["links"], list)
    assert result["links"][0]["url"] == "https://example.com/next"
    assert result["status_code"] == 200


def test_fetch_page_retries_502_then_succeeds(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(web_proxy_service, "RETRY_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(web_proxy_service, "RETRY_BASE_DELAY_S", 0.5)
    monkeypatch.setattr(web_proxy_service, "RETRY_JITTER", False)

    calls = {"n": 0}

    class _Session:
        def get(self, url, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return _FakeResponse(status_code=502, body=b"bad gateway")
            return _FakeResponse(
                status_code=200,
                body=_html_body("After502", "ok"),
            )

    _install_page_session(monkeypatch, _Session())
    result = fetch_page("https://example.com/flaky")
    assert result["title"] == "After502"
    assert calls["n"] == 2
    assert sleeps == [0.5]


# ---------------------------------------------------------------------------
# fetch_reddit: www 403 → old.reddit rewrite
# ---------------------------------------------------------------------------


def test_fetch_reddit_www_403_rewrites_to_old(monkeypatch):
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(web_proxy_service, "REDDIT_OLD_FALLBACK", True)

    seen_urls: list[str] = []

    class _Session:
        def __init__(self, hostname: str):
            self.hostname = hostname

        def get(self, url, **kwargs):
            seen_urls.append(url)
            if "www.reddit.com" in url:
                return _FakeResponse(
                    status_code=403,
                    body=b"blocked",
                    headers={"content-type": "text/html"},
                )
            if "old.reddit.com" in url:
                return _FakeResponse(
                    status_code=200,
                    body=_reddit_listing_body(),
                    headers={"content-type": "application/json"},
                )
            return _FakeResponse(status_code=404, body=b"nope")

    def _factory(hostname):
        return _Session(hostname)

    _install_reddit_session(monkeypatch, _factory)

    # Real structural validation for reddit hosts (not stubbed away entirely —
    # _reddit_get_json calls _validate_url_structure on the candidate URL).
    # Leave _validate_url_structure real; only pin/session are faked.
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (
            (
                "https",
                ("old.reddit.com" if "old.reddit.com" in url else "www.reddit.com"),
                443,
            ),
            None,
        ),
    )

    result = fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")
    assert any("www.reddit.com" in u for u in seen_urls)
    assert any("old.reddit.com" in u for u in seen_urls)
    assert result["reddit"]["post_count"] == 1
    assert "ladder post" in result["text"]
    assert result["resolved_url"].startswith("https://old.reddit.com/")
    # Output shape intact
    for key in ("url", "title", "text", "links", "reddit", "status_code"):
        assert key in result


def test_fetch_reddit_302_still_fails_immediately(monkeypatch):
    """Permanent non-403 must not walk the fallback ladder (legacy contract)."""
    monkeypatch.setattr(web_proxy_service, "REDDIT_OLD_FALLBACK", True)
    calls = {"n": 0}

    class _Session:
        def __init__(self, hostname: str):
            pass

        def get(self, url, **kwargs):
            calls["n"] += 1
            assert kwargs.get("allow_redirects") is False
            return _FakeResponse(
                status_code=302,
                body=b"",
                headers={"content-type": "text/html", "location": "https://evil.example/"},
            )

    _install_reddit_session(monkeypatch, lambda hostname: _Session(hostname))
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    with pytest.raises(requests.HTTPError):
        fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")
    assert calls["n"] == 1
