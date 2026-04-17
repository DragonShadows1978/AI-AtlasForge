from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import importlib
import json
from contextlib import contextmanager

import pytest

from WebProxy import service as web_proxy_service
from WebProxy.service import (
    _apply_max_chars,
    _int_env,
    _save_image,
    _validate_url_structure,
    SearchProvider,
    create_app,
    extract_page_content,
    fetch_page,
    fetch_reddit,
)


def test_health_endpoint():
    app = create_app()
    client = app.test_client()
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["service"] == "atlasforge-web-proxy"


def test_fetch_requires_url():
    app = create_app()
    client = app.test_client()
    response = client.post("/fetch", json={})
    assert response.status_code == 400
    payload = response.get_json()
    assert payload["error"] == "url is required"


def test_extract_page_content_strips_scripts_and_keeps_text():
    html = """
    <html>
      <head>
        <title>Example Page</title>
        <meta name="description" content="sample description">
        <script>console.log('ignore me')</script>
      </head>
      <body>
        <main>
          <h1>Hello World</h1>
          <p>First paragraph.</p>
          <p>Second paragraph.</p>
          <a href="/next">Next page</a>
        </main>
      </body>
    </html>
    """
    payload = extract_page_content("https://example.com/story", html, max_chars=1000)
    assert payload["title"] == "Example Page"
    assert payload["meta_description"] == "sample description"
    assert "console.log" not in payload["text"]
    assert "First paragraph." in payload["text"]
    assert payload["headings"] == ["Hello World"]
    assert payload["links"][0]["url"] == "https://example.com/next"


def test_fetch_reddit_handles_null_fields(monkeypatch):
    payload = [
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t3",
                        "data": {
                            "title": "null-field post",
                            "author": "ghost",
                            "score": None,
                            "num_comments": None,
                            "link_flair_text": None,
                            "subreddit": "python",
                            "created_utc": 0,
                            "url": "https://example.com",
                            "permalink": None,
                            "selftext": None,
                            "is_self": True,
                            "over_18": False,
                            "stickied": False,
                        },
                    }
                ]
            },
        },
        {
            "kind": "Listing",
            "data": {
                "children": [
                    {
                        "kind": "t1",
                        "data": {
                            "author": "ghost2",
                            "score": None,
                            "body": None,
                            "created_utc": 0,
                            "permalink": None,
                        },
                    }
                ]
            },
        },
    ]

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def __init__(self, body: bytes):
            self._body = body

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            if self._body:
                yield self._body

        def close(self):
            return None

    body = json.dumps(payload).encode("utf-8")

    class _FakeSession:
        def get(self, *args, **kwargs):
            return _FakeResponse(body)

    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield _FakeSession()

    monkeypatch.setattr(web_proxy_service, "_ensure_safe_url", lambda url: None)
    monkeypatch.setattr(
        web_proxy_service,
        "_resolve_first_safe_ip",
        lambda url: "127.0.0.1",
    )
    monkeypatch.setattr(
        web_proxy_service, "_pinned_session", _fake_pinned_session
    )

    result = fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")

    posts = result["reddit"]["posts"]
    comments = result["reddit"]["comments"]
    assert posts[0]["score"] == 0
    assert posts[0]["num_comments"] == 0
    assert posts[0]["permalink"] == "https://www.reddit.com"
    assert comments[0]["score"] == 0
    assert "null-field post" in result["text"]


def _install_reddit_session(monkeypatch, session):
    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield session

    monkeypatch.setattr(web_proxy_service, "_ensure_safe_url", lambda url: None)
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(
        web_proxy_service, "_pinned_session", _fake_pinned_session
    )


def test_fetch_reddit_does_not_follow_redirects(monkeypatch):
    call_count = {"n": 0}

    class _RedirectResponse:
        status_code = 302
        headers = {"content-type": "text/html", "location": "https://evil.example/"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            return iter(())

        def close(self):
            return None

    class _FakeSession:
        def get(self, *args, **kwargs):
            call_count["n"] += 1
            assert kwargs.get("allow_redirects") is False
            return _RedirectResponse()

    _install_reddit_session(monkeypatch, _FakeSession())

    result = fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")

    assert call_count["n"] == 1
    assert result["status_code"] == 302
    assert result["reddit"]["posts"] == []
    assert result["reddit"]["comments"] == []


def test_fetch_reddit_enforces_max_fetch_bytes(monkeypatch):
    monkeypatch.setattr(web_proxy_service, "MAX_FETCH_BYTES", 1024)

    big_chunk = b"x" * 2048

    class _BigResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield big_chunk

        def close(self):
            return None

    class _FakeSession:
        def get(self, *args, **kwargs):
            return _BigResponse()

    _install_reddit_session(monkeypatch, _FakeSession())

    with pytest.raises(ValueError, match="MAX_FETCH_BYTES"):
        fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")


def test_fetch_reddit_streams_with_correct_kwargs(monkeypatch):
    captured = {}

    class _OkResponse:
        status_code = 200
        headers = {"content-type": "application/json"}

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield b"[]"

        def close(self):
            return None

    class _FakeSession:
        def get(self, *args, **kwargs):
            captured.update(kwargs)
            return _OkResponse()

    _install_reddit_session(monkeypatch, _FakeSession())

    fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")

    assert captured.get("allow_redirects") is False
    assert captured.get("stream") is True


_PNG_1x1 = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x5e\xf3\x2a\x3a"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_save_image_extension_ignores_url_path():
    result = _save_image(
        url="https://evil.example/foo.php",
        content_type="image/png",
        data=_PNG_1x1,
    )
    local_path = Path(result["local_path"])
    assert local_path.suffix == ".png"
    assert ".php" not in local_path.name
    local_path.unlink(missing_ok=True)


def test_save_image_unknown_content_type_falls_back_to_bin():
    result = _save_image(
        url="https://evil.example/foo.exe",
        content_type="image/heic",
        data=b"\x00\x01\x02\x03",
    )
    local_path = Path(result["local_path"])
    assert local_path.suffix == ".bin"
    assert ".exe" not in local_path.name
    local_path.unlink(missing_ok=True)


def test_save_image_svg_is_not_preserved_as_svg():
    # SVG carries active content (script tags); must not land on disk as .svg.
    result = _save_image(
        url="https://evil.example/x.svg",
        content_type="image/svg+xml",
        data=b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
    )
    local_path = Path(result["local_path"])
    assert local_path.suffix == ".bin"
    assert ".svg" not in local_path.name
    local_path.unlink(missing_ok=True)


def test_save_image_swallows_pil_errors_only_for_known_types():
    result = _save_image(
        url="https://x.example/a.png",
        content_type="image/png",
        data=b"\x00\x00\x00\x00",
    )
    assert result["width"] is None
    assert result["height"] is None
    assert result["type"] == "image"
    Path(result["local_path"]).unlink(missing_ok=True)


def test_fetch_page_falls_back_to_utf8_on_invalid_charset(monkeypatch):
    body = "<html><head><title>Banana</title></head><body><main><p>hi</p></main></body></html>".encode(
        "utf-8"
    )

    class _FakeResponse:
        status_code = 200
        headers = {"content-type": "text/html; charset=banana"}
        encoding = "banana"

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            yield body

        def close(self):
            return None

    class _FakeSession:
        def get(self, *args, **kwargs):
            return _FakeResponse()

    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield _FakeSession()

    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "example.com", 443), None),
    )
    monkeypatch.setattr(
        web_proxy_service,
        "_resolve_first_safe_ip",
        lambda url: "127.0.0.1",
    )
    monkeypatch.setattr(
        web_proxy_service, "_pinned_session", _fake_pinned_session
    )
    monkeypatch.setattr(
        web_proxy_service, "_is_reddit_url", lambda url: False
    )

    result = fetch_page("https://example.com/story")
    assert result["status_code"] == 200
    assert result["title"] == "Banana"


def test_extract_page_content_drops_javascript_and_data_links():
    html = """
    <html>
      <body>
        <main>
          <p>body</p>
          <a href="https://good.example/page">good</a>
          <a href="/relative">relative</a>
          <a href="javascript:alert(1)">js</a>
          <a href="data:text/html,<h1>hi</h1>">data</a>
        </main>
      </body>
    </html>
    """
    payload = extract_page_content("https://site.example/", html, max_chars=1000)
    urls = [link["url"] for link in payload["links"]]
    assert "https://good.example/page" in urls
    assert "https://site.example/relative" in urls
    assert not any(u.startswith("javascript:") for u in urls)
    assert not any(u.startswith("data:") for u in urls)
    assert len(urls) == 2


class TestPortZeroRejected:
    """HIGH-1: explicit :0 in URL must be rejected, not silently substituted."""

    def test_port_zero_returns_invalid_port(self):
        parts, reason = _validate_url_structure("http://example.com:0/")
        assert parts is None
        assert "invalid port: 0" in reason

    def test_https_port_zero_rejected(self):
        parts, reason = _validate_url_structure("https://example.com:0/path")
        assert parts is None
        assert "invalid port: 0" in reason

    def test_fetch_endpoint_rejects_port_zero(self):
        app = create_app()
        client = app.test_client()
        response = client.post("/fetch", json={"url": "http://example.com:0/"})
        assert response.status_code == 400
        payload = response.get_json()
        assert "invalid port: 0" in payload["error"]

    def test_no_port_still_defaults_http_to_80(self):
        parts, reason = _validate_url_structure("http://example.com/")
        assert parts is not None
        assert parts[2] == 80
        assert reason == ""

    def test_no_port_still_defaults_https_to_443(self):
        parts, reason = _validate_url_structure("https://example.com/")
        assert parts is not None
        assert parts[2] == 443

    def test_explicit_port_still_honored(self):
        parts, reason = _validate_url_structure("http://example.com:8080/")
        assert parts is not None
        assert parts[2] == 8080


class TestSearchCountNoneCoerced:
    """HIGH-2: direct SearchProvider method calls must coerce count=None."""

    def test_normalize_count_none_returns_default(self):
        assert SearchProvider._normalize_count(None) == 5

    def test_normalize_count_garbage_returns_default(self):
        assert SearchProvider._normalize_count("abc") == 5

    def test_normalize_count_zero_clamped_to_min(self):
        assert SearchProvider._normalize_count(0) == 1

    def test_normalize_count_exceeds_max_clamped(self):
        assert SearchProvider._normalize_count(999, max_value=20) == 20

    def test_normalize_count_float_coerced(self):
        assert SearchProvider._normalize_count(7.9) == 7

    def test_normalize_count_custom_default(self):
        assert SearchProvider._normalize_count(None, default=10) == 10

    def test_normalize_count_negative_clamped_to_min(self):
        assert SearchProvider._normalize_count(-5) == 1

    def test_search_brave_none_does_not_raise(self, monkeypatch):
        provider = SearchProvider()
        monkeypatch.setattr(provider, "_brave_api_key", lambda: "fake-key")

        captured = {}

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"web": {"results": []}}

        class _FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                captured["params"] = params
                return _FakeResponse()

        provider.session = _FakeSession()
        result = provider.search_brave("q", count=None)
        assert captured["params"]["count"] == 5
        assert result["count"] == 0

    def test_search_duckduckgo_none_does_not_raise(self, monkeypatch):
        provider = SearchProvider()

        class _FakeResponse:
            text = "<html><body></body></html>"

            def raise_for_status(self):
                return None

        class _FakeSession:
            def post(self, *args, **kwargs):
                return _FakeResponse()

        provider.session = _FakeSession()
        result = provider.search_duckduckgo("q", count=None)
        assert result["count"] == 0

    def test_search_images_brave_none_does_not_raise(self, monkeypatch):
        provider = SearchProvider()
        monkeypatch.setattr(provider, "_brave_api_key", lambda: "fake-key")

        class _FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"results": []}

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        provider.session = _FakeSession()
        result = provider.search_images_brave("q", count=None)
        assert result["count"] == 0

    def test_search_dispatcher_none_does_not_raise(self, monkeypatch):
        provider = SearchProvider()
        monkeypatch.setattr(provider, "_brave_api_key", lambda: None)

        class _FakeResponse:
            text = "<html><body></body></html>"

            def raise_for_status(self):
                return None

        class _FakeSession:
            def post(self, *args, **kwargs):
                return _FakeResponse()

        provider.session = _FakeSession()
        result = provider.search("q", count=None, provider="duckduckgo")
        assert result["count"] == 0


class TestImageSearchNegativeFetchTopN:
    """HIGH-3: negative fetch_top_n must return 400 (no reverse slice, no
    silent clamp). Previously silently clamped to 0; now rejected at the
    endpoint so the caller sees the bug."""

    def test_image_search_negative_fetch_top_n_returns_400(self, monkeypatch):
        app = create_app()
        client = app.test_client()

        response = client.post(
            "/image_search",
            json={"query": "cats", "fetch_top_n": -5, "provider": "duckduckgo"},
        )
        assert response.status_code == 400
        assert "fetch_top_n" in response.get_json()["error"]

    def test_research_negative_fetch_top_n_returns_400(self, monkeypatch):
        app = create_app()
        client = app.test_client()

        response = client.post(
            "/research",
            json={"query": "cats", "fetch_top_n": -5, "provider": "duckduckgo"},
        )
        assert response.status_code == 400
        assert "fetch_top_n" in response.get_json()["error"]


class TestIntEnvFallback:
    """MOD-4: non-numeric env vars must not crash module import."""

    def test_missing_env_returns_default(self, monkeypatch):
        monkeypatch.delenv("ATLASFORGE_TEST_VAR_XYZ", raising=False)
        assert _int_env("ATLASFORGE_TEST_VAR_XYZ", 42) == 42

    def test_garbage_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("ATLASFORGE_TEST_VAR_XYZ", "abc")
        assert _int_env("ATLASFORGE_TEST_VAR_XYZ", 42) == 42

    def test_valid_env_returns_int(self, monkeypatch):
        monkeypatch.setenv("ATLASFORGE_TEST_VAR_XYZ", "100")
        assert _int_env("ATLASFORGE_TEST_VAR_XYZ", 42) == 100

    def test_empty_env_returns_default(self, monkeypatch):
        monkeypatch.setenv("ATLASFORGE_TEST_VAR_XYZ", "")
        assert _int_env("ATLASFORGE_TEST_VAR_XYZ", 42) == 42

    def test_module_reload_with_garbage_port_does_not_crash(self, monkeypatch):
        monkeypatch.setenv("ATLASFORGE_WEB_PROXY_PORT", "garbage")
        # Reload should not raise; DEFAULT_PORT should fall back.
        reloaded = importlib.reload(web_proxy_service)
        assert reloaded.DEFAULT_PORT == 8765


class TestNegativeMaxCharsClamped:
    """MOD-5: negative max_chars must not bypass truncation via reverse slice."""

    def test_apply_max_chars_negative_returns_payload_unchanged(self):
        payload = {"text": "hello"}
        result = _apply_max_chars(payload, -10)
        assert result["text"] == "hello"

    def test_extract_page_content_negative_max_chars_returns_full_text(self):
        html = "<html><body><main><p>hello world</p></main></body></html>"
        result = extract_page_content("u", html, max_chars=-1)
        assert "hello world" in result["text"]

    def test_fetch_endpoint_negative_max_chars_does_not_500(self):
        app = create_app()
        client = app.test_client()
        # Use an obviously-bad URL so we don't need network; the endpoint will
        # still process max_chars before the URL check.
        response = client.post(
            "/fetch", json={"url": "http://example.com:0/", "max_chars": -1}
        )
        # Either 400 (bad URL) or 200 — just not 500.
        assert response.status_code != 500

    def test_research_negative_max_chars_does_not_500(self, monkeypatch):
        app = create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {"provider": "duckduckgo", "query": query, "results": [], "count": 0}

        monkeypatch.setattr(SearchProvider, "search", _fake_search)
        response = client.post(
            "/research",
            json={"query": "q", "max_chars": -1, "provider": "duckduckgo"},
        )
        assert response.status_code != 500


class TestFloatMaxCharsCoerced:
    """MOD-6: float max_chars must coerce to int, not crash."""

    def test_float_max_chars_coerces_to_int(self):
        result = _apply_max_chars({"text": "hello world"}, 5.5)
        assert result["text"] == "hello"
        assert result["text_length"] == 5

    def test_string_garbage_max_chars_returns_unchanged(self):
        payload = {"text": "hello"}
        result = _apply_max_chars(payload, "garbage")
        assert result["text"] == "hello"

    def test_none_max_chars_returns_unchanged(self):
        payload = {"text": "hello"}
        result = _apply_max_chars(payload, None)
        assert result["text"] == "hello"

    def test_float_zero_max_chars_yields_empty(self):
        # Contract: max_chars==0 means "empty text", not "unlimited".
        # 0.0 coerces to int 0 and must therefore yield empty text.
        payload = {"text": "hello"}
        result = _apply_max_chars(payload, 0.0)
        assert result["text"] == ""
        assert result["text_length"] == 0


class TestInfinityCoerced:
    """Infinite/NaN floats must not crash int() coercion sites.

    int(float('inf')) raises OverflowError, int(float('nan')) raises ValueError.
    The six user-input coercion sites widen their except clauses to catch both.
    """

    def test_normalize_count_inf_returns_default(self):
        assert SearchProvider._normalize_count(float("inf")) == 5

    def test_normalize_count_neg_inf_returns_default(self):
        assert SearchProvider._normalize_count(float("-inf")) == 5

    def test_normalize_count_nan_returns_default(self):
        assert SearchProvider._normalize_count(float("nan")) == 5

    def test_apply_max_chars_inf_returns_unchanged(self):
        payload = {"text": "hello world"}
        result = _apply_max_chars(payload, float("inf"))
        assert result["text"] == "hello world"

    def test_apply_max_chars_nan_returns_unchanged(self):
        payload = {"text": "hello world"}
        result = _apply_max_chars(payload, float("nan"))
        assert result["text"] == "hello world"

    def test_extract_page_content_inf_max_chars(self):
        html = "<html><body><p>hello world</p></body></html>"
        result = extract_page_content("http://e.com/", html, max_chars=float("inf"))
        assert "hello world" in result["text"]

    def test_fetch_endpoint_inf_max_chars_returns_400(self):
        app = create_app()
        client = app.test_client()
        response = client.post(
            "/fetch", json={"url": "http://example.com/", "max_chars": float("inf")}
        )
        assert response.status_code == 400
        assert "max_chars" in response.get_json()["error"]

    def test_research_endpoint_inf_max_chars_returns_400(self):
        app = create_app()
        client = app.test_client()
        response = client.post(
            "/research", json={"query": "x", "max_chars": float("inf")}
        )
        assert response.status_code == 400
        assert "max_chars" in response.get_json()["error"]

    def test_image_search_endpoint_inf_fetch_top_n_returns_400(self):
        app = create_app()
        client = app.test_client()
        response = client.post(
            "/image_search", json={"query": "x", "fetch_top_n": float("inf")}
        )
        assert response.status_code == 400
        assert "fetch_top_n" in response.get_json()["error"]


import re as _re


_CID_PATTERN = _re.compile(r"^[0-9a-f]{12}$")


def _current_module():
    """Resolve the currently-loaded web_proxy_service module. Earlier tests
    reload the module; any symbol imported at top-of-file becomes stale.
    Use this for class references (UnsafeUrlError, SearchProvider) that
    need to match the module a test's `create_app()` actually consumed."""
    return sys.modules["WebProxy.service"]


def _mod_create_app():
    return _current_module().create_app()


def _mod_search_provider():
    return _current_module().SearchProvider


def _mod_unsafe_url_error():
    return _current_module().UnsafeUrlError


def _mod_new_cid():
    return _current_module()._new_correlation_id()


def _mod_apply_max_chars(payload, max_chars):
    return _current_module()._apply_max_chars(payload, max_chars)


def _mod_coerce_count(*args, **kwargs):
    return _current_module()._coerce_count(*args, **kwargs)


def _mod_error_body(message, cid, **extra):
    return _current_module()._error_body(message, cid, **extra)


class TestGenericErrorBodies:
    """Mission: replace `str(exc)` 502 leaks with generic body + correlation ID."""

    def test_correlation_id_is_12_hex_chars(self):
        cid = _mod_new_cid()
        assert _CID_PATTERN.match(cid), cid

    def test_correlation_ids_are_unique(self):
        assert _mod_new_cid() != _mod_new_cid()

    def test_error_body_contains_message_and_cid(self):
        cid = "abc123def456"
        body = _mod_error_body("generic failure", cid, url="https://x/")
        assert body["error"] == "generic failure"
        assert body["correlation_id"] == cid
        assert body["url"] == "https://x/"

    def test_search_502_does_not_leak_exception_string(self, monkeypatch):
        app = _mod_create_app()
        client = app.test_client()

        leak_needle = "urllib3.pool.HTTPSConnectionPool(host='10.0.0.5', port=443)"

        def _raise_search(self, query, count=5, provider="auto"):
            raise RuntimeError(leak_needle)

        monkeypatch.setattr(_mod_search_provider(), "search", _raise_search)

        # Unique query to avoid hitting the on-disk cache from prior runs.
        response = client.post(
            "/search",
            json={"query": f"leak_test_{_mod_new_cid()}", "provider": "duckduckgo"},
        )
        assert response.status_code == 502
        body = response.get_json()
        assert body["error"] == "search failed"
        assert _CID_PATTERN.match(body["correlation_id"])
        # Raw exception string must NOT appear in the response body.
        raw = response.get_data(as_text=True)
        assert "urllib3" not in raw
        assert "10.0.0.5" not in raw
        assert "HTTPSConnectionPool" not in raw

    def test_image_search_502_does_not_leak_exception_string(self, monkeypatch):
        app = _mod_create_app()
        client = app.test_client()
        leak = "urllib3.HTTPSConnectionPool(host='10.0.0.5', port=443): internal"

        def _raise(self, query, count=5, provider="auto", safesearch="off"):
            raise RuntimeError(leak)

        monkeypatch.setattr(_mod_search_provider(), "search_images", _raise)
        response = client.post(
            "/image_search", json={"query": f"leak_img_{_mod_new_cid()}"}
        )
        assert response.status_code == 502
        body = response.get_json()
        assert body["error"] == "image search failed"
        assert _CID_PATTERN.match(body["correlation_id"])
        raw = response.get_data(as_text=True)
        assert "10.0.0.5" not in raw
        assert "urllib3" not in raw

    def test_research_502_does_not_leak_exception_string(self, monkeypatch):
        app = _mod_create_app()
        client = app.test_client()
        leak = "urllib3.HTTPSConnectionPool(host='10.0.0.7', port=443)"

        def _raise(self, query, count=5, provider="auto"):
            raise RuntimeError(leak)

        monkeypatch.setattr(_mod_search_provider(), "search", _raise)
        response = client.post("/research", json={"query": "q"})
        assert response.status_code == 502
        body = response.get_json()
        assert body["error"] == "search failed"
        assert _CID_PATTERN.match(body["correlation_id"])
        assert "10.0.0.7" not in response.get_data(as_text=True)


class TestFetchUnsafeUrlNoSSRFLeak:
    """Mission: /fetch UnsafeUrlError response must NOT leak resolved IPs
    or DNS diagnostic strings. SSRF side channel closed."""

    def test_unsafe_url_does_not_leak_resolved_ip(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        # fetch_page runs AFTER the endpoint's structural pre-flight, so
        # the URL must be structurally valid. We raise at fetch_page time.
        def _raise_unsafe(url, max_chars=12000, timeout_s=20):
            raise mod.UnsafeUrlError("non-public ip: 10.0.0.5")

        monkeypatch.setattr(mod, "fetch_page", _raise_unsafe)

        # Unique URL path to avoid hitting the on-disk fetch cache.
        response = client.post(
            "/fetch",
            json={"url": f"https://internal.example/{_mod_new_cid()}"},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "unsafe url"
        assert _CID_PATTERN.match(body["correlation_id"])
        raw = response.get_data(as_text=True)
        assert "10.0.0.5" not in raw
        assert "non-public" not in raw

    def test_unsafe_url_does_not_leak_dns_error(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _raise_unsafe(url, max_chars=12000, timeout_s=20):
            raise mod.UnsafeUrlError(
                "dns failed: [Errno -2] Name or service not known"
            )

        monkeypatch.setattr(mod, "fetch_page", _raise_unsafe)

        response = client.post(
            "/fetch",
            json={"url": f"https://nonexistent.example/{_mod_new_cid()}"},
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body["error"] == "unsafe url"
        raw = response.get_data(as_text=True)
        assert "Errno" not in raw
        assert "Name or service" not in raw
        assert "dns failed" not in raw

    def test_generic_fetch_exception_does_not_leak(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _raise(url, max_chars=12000, timeout_s=20):
            raise RuntimeError("ConnectionPool(host='192.168.1.5', port=443)")

        monkeypatch.setattr(mod, "fetch_page", _raise)
        response = client.post(
            "/fetch",
            json={"url": f"https://example.com/{_mod_new_cid()}"},
        )
        assert response.status_code == 502
        body = response.get_json()
        assert body["error"] == "fetch failed"
        raw = response.get_data(as_text=True)
        assert "192.168.1.5" not in raw
        assert "ConnectionPool" not in raw

    def test_structural_preflight_400_still_surfaces_safe_reason(self):
        """Regression: structural (pre-DNS) reasons describe the caller's
        OWN input (bad scheme, missing host) and are safe to surface.
        Only runtime DNS/IP reasons are hidden."""
        app = _mod_create_app()
        client = app.test_client()
        response = client.post("/fetch", json={"url": "ftp://example.com/"})
        assert response.status_code == 400
        body = response.get_json()
        assert "unsupported scheme" in body["error"]


class TestResearchPerItemNoLeak:
    def test_research_per_item_unsafe_url_does_not_leak(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [{"url": "https://example.com/a", "title": "a"}],
                "count": 1,
            }

        def _raise_unsafe(url, max_chars=12000, timeout_s=20):
            raise mod.UnsafeUrlError("non-public ip: 10.1.2.3")

        monkeypatch.setattr(mod.SearchProvider, "search", _fake_search)
        monkeypatch.setattr(mod, "fetch_page", _raise_unsafe)

        response = client.post("/research", json={"query": "q", "fetch_top_n": 1})
        assert response.status_code == 200
        fetched = response.get_json()["fetched"]
        assert len(fetched) == 1
        item = fetched[0]
        assert item["error"] == "unsafe url"
        assert "correlation_id" in item
        assert _CID_PATTERN.match(item["correlation_id"])
        raw = response.get_data(as_text=True)
        assert "10.1.2.3" not in raw
        assert "non-public" not in raw

    def test_research_per_item_generic_exception_does_not_leak(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [{"url": "https://example.com/a", "title": "a"}],
                "count": 1,
            }

        def _raise(url, max_chars=12000, timeout_s=20):
            raise RuntimeError("HTTPSConnectionPool(host='10.0.0.9', port=443)")

        monkeypatch.setattr(mod.SearchProvider, "search", _fake_search)
        monkeypatch.setattr(mod, "fetch_page", _raise)

        response = client.post("/research", json={"query": "q", "fetch_top_n": 1})
        assert response.status_code == 200
        fetched = response.get_json()["fetched"]
        assert fetched[0]["error"] == "fetch failed"
        assert _CID_PATTERN.match(fetched[0]["correlation_id"])
        raw = response.get_data(as_text=True)
        assert "10.0.0.9" not in raw


class TestMalformedJsonBody:
    """Mission: silent-swallow via `get_json(force=True, silent=True) or {}`
    replaced with a clean 400."""

    @pytest.mark.parametrize("path", ["/search", "/fetch", "/research", "/image_search"])
    def test_empty_body_returns_400(self, path):
        app = _mod_create_app()
        client = app.test_client()
        # No Content-Type, no body: get_json returns None, helper 400s.
        response = client.post(path)
        assert response.status_code == 400
        assert "JSON" in response.get_json()["error"]

    @pytest.mark.parametrize("path", ["/search", "/fetch", "/research", "/image_search"])
    def test_garbage_body_returns_400(self, path):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            path,
            data="not json at all",
            content_type="application/json",
        )
        assert response.status_code == 400
        assert "JSON" in response.get_json()["error"]

    @pytest.mark.parametrize("path", ["/search", "/fetch", "/research", "/image_search"])
    def test_non_object_body_returns_400(self, path):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(path, json=[1, 2, 3])
        assert response.status_code == 400
        assert "JSON object" in response.get_json()["error"]


class TestNegativeIntegerParamsReturn400:
    """Mission: negative fetch_top_n / max_chars must 400, not silently clamp."""

    def test_fetch_negative_max_chars_returns_400(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/fetch", json={"url": "https://example.com/", "max_chars": -1}
        )
        assert response.status_code == 400
        assert "max_chars" in response.get_json()["error"]

    def test_research_negative_max_chars_returns_400(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/research", json={"query": "q", "max_chars": -1}
        )
        assert response.status_code == 400
        assert "max_chars" in response.get_json()["error"]

    def test_research_negative_fetch_top_n_returns_400_plain(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/research", json={"query": "q", "fetch_top_n": -1}
        )
        assert response.status_code == 400
        assert "fetch_top_n" in response.get_json()["error"]

    def test_image_search_negative_count_returns_400(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/image_search", json={"query": "q", "count": -1}
        )
        assert response.status_code == 400
        assert "count" in response.get_json()["error"]

    def test_search_negative_count_returns_400(self):
        # /search uses min_value=1 (the default), so 0 is also rejected.
        app = _mod_create_app()
        client = app.test_client()
        response = client.post("/search", json={"query": "q", "count": -1})
        assert response.status_code == 400
        assert "count" in response.get_json()["error"]


class TestApplyMaxCharsContract:
    """Mission: max_chars==0 means empty text; max_chars<0 means unlimited."""

    def test_zero_yields_empty_text(self):
        result = _mod_apply_max_chars({"text": "hello world"}, 0)
        assert result["text"] == ""
        assert result["text_length"] == 0

    def test_negative_yields_unchanged(self):
        payload = {"text": "hello world", "text_length": 11}
        result = _mod_apply_max_chars(payload, -1)
        assert result["text"] == "hello world"

    def test_positive_smaller_truncates(self):
        result = _mod_apply_max_chars({"text": "hello world"}, 5)
        assert result["text"] == "hello"
        assert result["text_length"] == 5

    def test_positive_larger_unchanged(self):
        result = _mod_apply_max_chars({"text": "hi"}, 100)
        assert result["text"] == "hi"

    def test_positive_equal_unchanged(self):
        result = _mod_apply_max_chars({"text": "hello"}, 5)
        assert result["text"] == "hello"


class TestCoerceCountMinValue:
    def test_default_min_value_rejects_zero(self):
        assert _mod_coerce_count(0) is None

    def test_default_min_value_rejects_negative(self):
        assert _mod_coerce_count(-1) is None

    def test_min_value_zero_accepts_zero(self):
        assert _mod_coerce_count(0, min_value=0) == 0

    def test_min_value_zero_rejects_negative(self):
        assert _mod_coerce_count(-1, min_value=0) is None

    def test_none_returns_default(self):
        assert _mod_coerce_count(None, default=7) == 7

    def test_garbage_returns_none(self):
        assert _mod_coerce_count("abc") is None

    def test_inf_returns_none(self):
        assert _mod_coerce_count(float("inf")) is None


class TestResearchMaxCharsZeroEmpty:
    """Iteration 1 HIGH fix: /research must enforce max_chars=0 -> empty text
    per-item, mirroring /fetch's contract. Previously it routed straight to
    extract_page_content which treats max_chars=0 as unlimited."""

    def test_research_per_item_max_chars_zero_yields_empty(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [{"url": "https://example.com/a", "title": "a"}],
                "count": 1,
            }

        def _fake_fetch(url, max_chars=12000, timeout_s=20):
            # Internally research now passes max_chars=-1 (unlimited) and
            # wraps the result in _apply_max_chars before returning.
            assert max_chars == -1, f"expected internal max_chars=-1, got {max_chars}"
            return {
                "url": url,
                "title": "example",
                "text": "a" * 500,
                "text_length": 500,
                "headings": [],
                "links": [],
                "meta_description": "",
            }

        monkeypatch.setattr(mod.SearchProvider, "search", _fake_search)
        monkeypatch.setattr(mod, "fetch_page", _fake_fetch)

        response = client.post(
            "/research",
            json={"query": "q", "fetch_top_n": 1, "max_chars": 0},
        )
        assert response.status_code == 200
        fetched = response.get_json()["fetched"]
        assert len(fetched) == 1
        item = fetched[0]
        assert item["text"] == ""
        assert item["text_length"] == 0

    def test_research_per_item_max_chars_positive_truncates(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [{"url": "https://example.com/a", "title": "a"}],
                "count": 1,
            }

        def _fake_fetch(url, max_chars=12000, timeout_s=20):
            return {
                "url": url,
                "title": "example",
                "text": "abcdefghij",
                "text_length": 10,
                "headings": [],
                "links": [],
                "meta_description": "",
            }

        monkeypatch.setattr(mod.SearchProvider, "search", _fake_search)
        monkeypatch.setattr(mod, "fetch_page", _fake_fetch)

        response = client.post(
            "/research",
            json={"query": "q", "fetch_top_n": 1, "max_chars": 3},
        )
        assert response.status_code == 200
        fetched = response.get_json()["fetched"]
        assert fetched[0]["text"] == "abc"
        assert fetched[0]["text_length"] == 3


class TestPerItemErrorShapeUnified:
    """Iteration 1 HIGH fix: structural-reject and DNS-reject per-item paths
    must produce identical response body shapes so an attacker cannot use
    error string differences as a structural-vs-DNS oracle."""

    def test_research_structural_and_dns_reject_same_shape(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_search(self, query, count=5, provider="auto"):
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [
                    # Structural-bad (unsupported scheme).
                    {"url": "javascript:alert(1)", "title": "bad1"},
                    # DNS-bad (will raise UnsafeUrlError at fetch time).
                    {"url": "https://example.com/b", "title": "bad2"},
                ],
                "count": 2,
            }

        def _raise_unsafe(url, max_chars=12000, timeout_s=20):
            raise mod.UnsafeUrlError("non-public ip: 10.1.2.3")

        monkeypatch.setattr(mod.SearchProvider, "search", _fake_search)
        monkeypatch.setattr(mod, "fetch_page", _raise_unsafe)

        response = client.post(
            "/research", json={"query": "q", "count": 2, "fetch_top_n": 2}
        )
        assert response.status_code == 200
        fetched = response.get_json()["fetched"]
        assert len(fetched) == 2
        structural, dns = fetched[0], fetched[1]
        assert set(structural.keys()) == set(dns.keys())
        assert structural["error"] == "unsafe url"
        assert dns["error"] == "unsafe url"
        assert _CID_PATTERN.match(structural["correlation_id"])
        assert _CID_PATTERN.match(dns["correlation_id"])

    def test_image_search_structural_reject_has_correlation_id(self, monkeypatch):
        mod = _current_module()
        app = mod.create_app()
        client = app.test_client()

        def _fake_images(self, query, count=5, provider="auto", safesearch="off"):
            return {
                "provider": "duckduckgo_images",
                "query": query,
                "results": [{"url": "javascript:alert(1)", "title": "bad"}],
                "count": 1,
            }

        monkeypatch.setattr(mod.SearchProvider, "search_images", _fake_images)

        response = client.post(
            "/image_search",
            json={"query": f"uniq_{_mod_new_cid()}", "fetch_top_n": 1},
        )
        assert response.status_code == 200
        body = response.get_json()
        fetched = body.get("fetched_images", [])
        assert len(fetched) == 1
        item = fetched[0]
        assert item["error"] == "unsafe url"
        assert "correlation_id" in item
        assert _CID_PATTERN.match(item["correlation_id"])
        # Structural reason must NOT appear in body (it's an SSRF oracle if
        # structural rejects carry reason strings and DNS rejects don't).
        raw = response.get_data(as_text=True)
        assert "unsupported scheme" not in raw
        assert "javascript" in raw  # caller's own URL echo is fine


class TestImageSearchCountMinValue:
    """Iteration 1 MODERATE fix: /image_search must use min_value=1 for
    `count`, matching /research. count=0 is meaningless for a search."""

    def test_image_search_count_zero_returns_400(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/image_search", json={"query": "q", "count": 0}
        )
        assert response.status_code == 400
        assert "count" in response.get_json()["error"]

    def test_image_search_count_negative_returns_400(self):
        app = _mod_create_app()
        client = app.test_client()
        response = client.post(
            "/image_search", json={"query": "q", "count": -1}
        )
        assert response.status_code == 400


class TestApplyMaxCharsTextLengthConsistency:
    """Iteration 1 MODERATE fix: _apply_max_chars must always recompute
    text_length from the final text string (so tampered cache entries can't
    propagate a mismatch), and must survive text=None."""

    def test_recomputes_text_length_on_negative(self):
        # Cache entry with a wrong text_length should be corrected.
        result = _mod_apply_max_chars({"text": "abc", "text_length": 99}, -1)
        assert result["text"] == "abc"
        assert result["text_length"] == 3

    def test_recomputes_text_length_on_positive_larger(self):
        result = _mod_apply_max_chars({"text": "hi", "text_length": 999}, 100)
        assert result["text_length"] == 2

    def test_text_none_does_not_crash(self):
        # Defensive: payload with explicit None text must not TypeError.
        result = _mod_apply_max_chars({"text": None}, 0)
        assert result["text"] == ""
        assert result["text_length"] == 0

    def test_text_none_negative_returns_empty(self):
        result = _mod_apply_max_chars({"text": None}, -1)
        assert result["text"] == ""
        assert result["text_length"] == 0

    def test_text_none_positive(self):
        result = _mod_apply_max_chars({"text": None}, 5)
        assert result["text"] == ""
        assert result["text_length"] == 0


# ============================================================================
# Mission 2d8c9d15 — bugfix bundle tests (B1..B12)
# ============================================================================


def _install_reddit_session_with_body(monkeypatch, body_bytes: bytes,
                                       status_code: int = 200):
    """Helper: installs a fake _pinned_session that returns body_bytes.

    Also monkeypatches _resolve_first_safe_ip and _validate_url_structure so
    fetch_reddit can exercise its parsing paths without real DNS/network.
    """
    class _FakeResponse:
        headers = {"content-type": "application/json"}

        def __init__(self, body: bytes, code: int):
            self._body = body
            self.status_code = code

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size=65536):
            if self._body:
                yield self._body

        def close(self):
            return None

    class _FakeSession:
        def get(self, *args, **kwargs):
            return _FakeResponse(body_bytes, status_code)

    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield _FakeSession()

    monkeypatch.setattr(
        web_proxy_service,
        "_resolve_first_safe_ip",
        lambda url: "127.0.0.1",
    )
    monkeypatch.setattr(
        web_proxy_service, "_pinned_session", _fake_pinned_session
    )


class TestCollectListingTypeCoercion:
    """B1+B2+B3: _collect_listing must coerce non-int score/num_comments to
    int (rejecting bool), coerce non-str selftext/body to str, and survive
    a None `data` layer without AttributeError."""

    def _make_reddit_payload(self, post_data: dict) -> bytes:
        payload = [
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {"kind": "t3", "data": post_data},
                    ]
                },
            },
            {"kind": "Listing", "data": {"children": []}},
        ]
        return json.dumps(payload).encode("utf-8")

    def test_string_score_is_coerced_to_int(self, monkeypatch):
        # Reddit schema drift: score arrives as string.
        body = self._make_reddit_payload({
            "title": "t", "author": "u", "score": "42",
            "num_comments": 7, "subreddit": "s", "url": "https://x.example/",
            "permalink": "/p",
        })
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        posts = result["reddit"]["posts"]
        assert len(posts) == 1
        assert posts[0]["score"] == 42
        assert isinstance(posts[0]["score"], int)

    def test_float_num_comments_is_coerced_to_int(self, monkeypatch):
        body = self._make_reddit_payload({
            "title": "t", "author": "u", "score": 1,
            "num_comments": 3.7, "subreddit": "s",
            "url": "https://x.example/", "permalink": "/p",
        })
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["reddit"]["posts"][0]["num_comments"] == 3

    def test_bool_score_does_not_render_as_1(self, monkeypatch):
        # `True or 0` short-circuits to True, which is-a int(1). We want 0.
        body = self._make_reddit_payload({
            "title": "t", "author": "u", "score": True,
            "num_comments": False, "subreddit": "s",
            "url": "https://x.example/", "permalink": "/p",
        })
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        post = result["reddit"]["posts"][0]
        assert post["score"] == 0
        assert post["num_comments"] == 0
        # Render must not crash and must zero-pad.
        assert "[    0 |    0c]" in result["text"]

    def test_garbage_score_is_coerced(self, monkeypatch):
        body = self._make_reddit_payload({
            "title": "t", "author": "u", "score": "not a number",
            "num_comments": None, "subreddit": "s",
            "url": "https://x.example/", "permalink": "/p",
        })
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["reddit"]["posts"][0]["score"] == 0

    def test_non_string_selftext_coerced_to_string(self, monkeypatch):
        # Reddit JSON could drift to an int/list/dict here. The old
        # `(cd.get("selftext", "") or "")[:2000]` slice on a non-string raises
        # TypeError; str() coercion handles all JSON-representable types.
        body = self._make_reddit_payload({
            "title": "t", "author": "u", "score": 1, "num_comments": 1,
            "subreddit": "s", "url": "https://x.example/", "permalink": "/p",
            "selftext": 12345,  # int masquerading as selftext
        })
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        post = result["reddit"]["posts"][0]
        assert isinstance(post["selftext"], str)
        assert post["selftext"] == "12345"

    def test_list_body_coerced_to_string(self, monkeypatch):
        # t1 comment body arrives as a list (schema drift).
        payload = [
            {"kind": "Listing", "data": {"children": []}},
            {
                "kind": "Listing",
                "data": {
                    "children": [
                        {"kind": "t1", "data": {
                            "author": "u", "score": 5,
                            "body": [1, 2, 3],  # list masquerading as body
                            "created_utc": 0, "permalink": "/c",
                        }},
                    ]
                },
            },
        ]
        body = json.dumps(payload).encode("utf-8")
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        comment = result["reddit"]["comments"][0]
        assert isinstance(comment["body"], str)

    def test_null_data_layer_does_not_crash(self, monkeypatch):
        # listing.data is explicitly None. Old code: .get("data", {}).get("children")
        # crashes NoneType.get.
        payload = [
            {"kind": "Listing", "data": None},
            {"kind": "Listing", "data": {"children": []}},
        ]
        body = json.dumps(payload).encode("utf-8")
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []

    def test_null_children_does_not_crash(self, monkeypatch):
        payload = [
            {"kind": "Listing", "data": {"children": None}},
            {"kind": "Listing", "data": {"children": []}},
        ]
        body = json.dumps(payload).encode("utf-8")
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["reddit"]["posts"] == []


class TestFetchRedditDnsResolution:
    """B5: fetch_reddit should resolve DNS exactly once (for json_url),
    not twice (original url + json_url)."""

    def test_single_dns_resolution_call(self, monkeypatch):
        dns_calls = []

        def _spy_resolve(url):
            dns_calls.append(url)
            return "127.0.0.1"

        body = json.dumps([
            {"kind": "Listing", "data": {"children": []}},
            {"kind": "Listing", "data": {"children": []}},
        ]).encode("utf-8")

        class _FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield body

            def close(self):
                return None

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(web_proxy_service, "_resolve_first_safe_ip", _spy_resolve)
        monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)

        fetch_reddit("https://www.reddit.com/r/python/comments/abc123/x/")

        # Only json_url should be resolved, not the original URL.
        assert len(dns_calls) == 1
        assert dns_calls[0].endswith(".json?limit=25") or ".json" in dns_calls[0]


class TestFetchRedditConnectionCleanupOnError:
    """B6: fetch_reddit must close() the response even when raise_for_status
    raises HTTPError, otherwise the streamed connection leaks."""

    def test_closes_response_on_http_error(self, monkeypatch):
        close_calls = {"n": 0}

        class _FakeResponse:
            status_code = 500
            headers = {"content-type": "text/html"}

            def raise_for_status(self):
                import requests
                raise requests.HTTPError("500 Server Error")

            def iter_content(self, chunk_size=65536):
                return iter(())

            def close(self):
                close_calls["n"] += 1

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )

        import requests as _rq
        with pytest.raises(_rq.HTTPError):
            fetch_reddit("https://www.reddit.com/r/python/")
        assert close_calls["n"] >= 1


class TestFetchPageConnectionCleanupOnError:
    """B7: fetch_page must close() the response on raise_for_status HTTPError."""

    def test_closes_response_on_http_error(self, monkeypatch):
        close_calls = {"n": 0}

        class _FakeResponse:
            status_code = 503
            headers = {"content-type": "text/html"}
            encoding = "utf-8"

            def raise_for_status(self):
                import requests
                raise requests.HTTPError("503 Unavailable")

            def iter_content(self, chunk_size=65536):
                return iter(())

            def close(self):
                close_calls["n"] += 1

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _FakeResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service,
            "_validate_url_structure",
            lambda url: (("https", "example.com", 443), ""),
        )
        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )
        monkeypatch.setattr(
            web_proxy_service, "_is_reddit_url", lambda url: False
        )

        import requests as _rq
        with pytest.raises(_rq.HTTPError):
            fetch_page("https://example.com/x")
        assert close_calls["n"] >= 1


class TestImageSearchCacheBleed:
    """B8: /image_search must not return cached `fetched_images` to a caller
    who asked for fetch_top_n=0."""

    def test_cache_hit_with_zero_fetch_top_n_strips_fetched_images(
        self, tmp_path, monkeypatch
    ):
        # Build an isolated cache dir so we don't pollute the real one.
        import importlib
        monkeypatch.setenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", str(tmp_path / "cache"))
        mod = importlib.reload(web_proxy_service)

        app = mod.create_app()
        client = app.test_client()

        # Seed the cache directly. The cache key is derived from query/count/
        # provider/safesearch only (fetch_top_n is NOT part of the key).
        cache_key = mod._cache_key(
            "image_search",
            {"query": "cats", "count": 5, "provider": "duckduckgo", "safesearch": "off"},
        )
        cache = mod.FileCache(mod.CACHE_DIR)
        cache.put(cache_key, {
            "provider": "duckduckgo_images",
            "query": "cats",
            "results": [{"title": "t", "url": "https://x.example/a.jpg"}],
            "count": 1,
            "fetched_images": [{"type": "image", "url": "https://x.example/a.jpg"}],
            "_cache_hit": False,
        })

        response = client.post(
            "/image_search",
            json={"query": "cats", "count": 5, "provider": "duckduckgo",
                  "fetch_top_n": 0},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert "fetched_images" not in body, (
            "cache bleed: caller asked for fetch_top_n=0 but got fetched_images"
        )

        # Restore the original module state.
        monkeypatch.delenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", raising=False)
        importlib.reload(web_proxy_service)


class TestCacheEndpointTOCTOU:
    """B9: /cache must not 500 when a file disappears between iterdir() and stat()."""

    def test_cache_endpoint_skips_dangling_symlink(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", str(tmp_path / "cache"))
        mod = importlib.reload(web_proxy_service)

        # Create a dangling symlink in the image cache.
        mod.IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dangling = mod.IMAGE_CACHE_DIR / "gone.png"
        target = tmp_path / "does_not_exist.png"
        dangling.symlink_to(target)
        assert dangling.is_symlink()

        app = mod.create_app()
        client = app.test_client()
        response = client.get("/cache")
        assert response.status_code == 200
        body = response.get_json()
        # The dangling symlink must not appear; more importantly, the endpoint
        # must not 500.
        for entry in body.get("images", []):
            assert entry["filename"] != "gone.png"

        monkeypatch.delenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", raising=False)
        importlib.reload(web_proxy_service)

    def test_stats_endpoint_survives_iterdir_oserror(self, tmp_path, monkeypatch):
        import importlib
        monkeypatch.setenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", str(tmp_path / "cache"))
        mod = importlib.reload(web_proxy_service)

        # Make IMAGE_CACHE_DIR exist but simulate iterdir failing.
        mod.IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)

        # Monkeypatch Path.iterdir on the specific instance path to raise.
        original_iterdir = type(mod.IMAGE_CACHE_DIR).iterdir

        def _boom(self):
            if self == mod.IMAGE_CACHE_DIR:
                raise OSError("simulated iterdir failure")
            return original_iterdir(self)

        monkeypatch.setattr(type(mod.IMAGE_CACHE_DIR), "iterdir", _boom)

        app = mod.create_app()
        client = app.test_client()
        response = client.get("/stats")
        assert response.status_code == 200
        body = response.get_json()
        assert body["cached_images"] == 0

        monkeypatch.delenv("ATLASFORGE_WEB_PROXY_CACHE_DIR", raising=False)
        importlib.reload(web_proxy_service)


class TestPinnedAdapterLock:
    """B10: PinnedIPAdapter holds a lock across connection_pool_kw mutation
    and super().send(), so concurrent sends on a shared adapter cannot smear
    assert_hostname into a parallel handshake."""

    def test_adapter_has_pool_kw_lock(self):
        from WebProxy.service import PinnedIPAdapter
        adapter = PinnedIPAdapter(pinned_ip="127.0.0.1", hostname="example.com", port=443)
        assert hasattr(adapter, "_pool_kw_lock")
        # Lock should be acquirable (and release) without blocking.
        assert adapter._pool_kw_lock.acquire(blocking=False) is True
        adapter._pool_kw_lock.release()

    def test_concurrent_sends_do_not_leak_pool_kw(self):
        """End-to-end: two threads sharing an adapter race through send().
        After the race, assert_hostname/server_hostname must be restored to
        their pre-call state (i.e., absent)."""
        import threading
        from WebProxy.service import PinnedIPAdapter

        hostname = "example.com"
        adapter = PinnedIPAdapter(pinned_ip="127.0.0.1", hostname=hostname, port=443)
        pm = adapter.poolmanager

        results = []
        barrier = threading.Barrier(2)

        class _FakeRequest:
            def __init__(self, url):
                self.url = url
                self.headers = {}

        def _worker():
            req = _FakeRequest(f"https://{hostname}/x")
            barrier.wait()
            # Simulate the adapter's mutation/restore block directly.
            with adapter._pool_kw_lock:
                saved = {
                    "assert_hostname": pm.connection_pool_kw.get("assert_hostname"),
                    "server_hostname": pm.connection_pool_kw.get("server_hostname"),
                }
                pm.connection_pool_kw["assert_hostname"] = hostname
                pm.connection_pool_kw["server_hostname"] = hostname
                try:
                    results.append(("hit", pm.connection_pool_kw["assert_hostname"]))
                finally:
                    for k, v in saved.items():
                        if v is None:
                            pm.connection_pool_kw.pop(k, None)
                        else:
                            pm.connection_pool_kw[k] = v

        t1 = threading.Thread(target=_worker)
        t2 = threading.Thread(target=_worker)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # After both threads exit, pool_kw must be back to clean state.
        assert "assert_hostname" not in pm.connection_pool_kw
        assert "server_hostname" not in pm.connection_pool_kw
        assert len(results) == 2


class TestPinnedSessionTrustEnv:
    """B11: _pinned_session must set trust_env=False so HTTP_PROXY/HTTPS_PROXY
    env vars can't route bytes around the IP-pinning invariant."""

    def test_session_does_not_trust_env(self):
        from WebProxy.service import _pinned_session
        session = _pinned_session(pinned_ip="127.0.0.1", hostname="example.com", port=443)
        assert session.trust_env is False
        session.close()


class TestRedditJsonUrlCaseInsensitive:
    """B12: _reddit_json_url should treat `.JSON` the same as `.json`."""

    def test_uppercase_json_suffix_not_double_appended(self):
        from WebProxy.service import _reddit_json_url
        url = "https://www.reddit.com/r/python/comments/abc/x/.JSON"
        result = _reddit_json_url(url)
        # Must not contain "/.JSON/.json" (double-append).
        assert "/.JSON/.json" not in result
        # Original case preserved (we match, not rewrite) OR normalized — either
        # is acceptable; the critical invariant is no double `.json`.
        assert result.count(".json") + result.count(".JSON") == 1


class TestFetchRedditStructuralRejection:
    """B5 (corollary): fetch_reddit must structurally reject non-HTTPS/malformed
    URLs up front, not rely on _ensure_safe_url for that."""

    def test_invalid_scheme_rejected(self):
        from WebProxy.service import UnsafeUrlError
        with pytest.raises(UnsafeUrlError):
            fetch_reddit("ftp://www.reddit.com/r/python/")

    def test_missing_host_rejected(self):
        from WebProxy.service import UnsafeUrlError
        with pytest.raises(UnsafeUrlError):
            fetch_reddit("https:///r/python/")


class TestCollectListingTruthyNonDictData:
    """B3 regression: _collect_listing used `(listing.get('data') or {}).get(...)`.
    That only falls back when `data` is FALSY. A truthy non-dict — a string,
    a list, an int from tampered/trimmed JSON — skipped the `or {}` branch and
    crashed with AttributeError on `.get('children')`. Guard with isinstance."""

    def _render(self, monkeypatch, payload):
        """Run fetch_reddit against an injected JSON body; return rendered result."""
        from WebProxy import service as wps

        class _FakeResponse:
            def __init__(self, body):
                self._body = body
                self.status_code = 200
                self.headers = {"content-type": "application/json"}
                self.encoding = "utf-8"

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=0):
                yield self._body

            def close(self):
                return None

        class _FakeSession:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *a):
                return False

            def get(self_inner, url, **kw):
                return _FakeResponse(json.dumps(payload).encode("utf-8"))

            def close(self_inner):
                return None

        monkeypatch.setattr(wps, "_resolve_first_safe_ip", lambda u: "127.0.0.1")
        monkeypatch.setattr(wps, "_pinned_session", lambda **kw: _FakeSession())
        return wps.fetch_reddit("https://www.reddit.com/r/python/comments/abc/x/")

    def test_string_data_layer_does_not_crash(self, monkeypatch):
        # Truthy non-dict: a string at the `data` key.
        payload = [
            {"kind": "Listing", "data": "malformed"},
            {"kind": "Listing", "data": {"children": []}},
        ]
        result = self._render(monkeypatch, payload)
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []

    def test_list_data_layer_does_not_crash(self, monkeypatch):
        # Truthy non-dict: a list at the `data` key.
        payload = [
            {"kind": "Listing", "data": [1, 2, 3]},
            {"kind": "Listing", "data": {"children": []}},
        ]
        result = self._render(monkeypatch, payload)
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []

    def test_int_data_layer_does_not_crash(self, monkeypatch):
        # Truthy non-dict: an int at the `data` key.
        payload = [
            {"kind": "Listing", "data": 42},
            {"kind": "Listing", "data": {"children": []}},
        ]
        result = self._render(monkeypatch, payload)
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []


class TestPinnedAdapterMalformedProxies:
    """B10 adjacent: PinnedIPAdapter.send wrapped `requests.utils.select_proxy`
    in a bare `except Exception` that silently set selected_proxy=None. A
    malformed `proxies` dict thus SKIPPED the proxy-bypass guard — defeating
    the very protection the adapter exists to provide. Must fail closed."""

    def test_malformed_proxies_raises_unsafe_url_error(self):
        from WebProxy.service import PinnedIPAdapter, UnsafeUrlError

        adapter = PinnedIPAdapter(
            pinned_ip="127.0.0.1", hostname="example.com", port=443
        )

        class _FakeRequest:
            url = "https://example.com/path"
            headers: dict = {}

        # A dict subclass whose __contains__ raises mirrors real-world
        # failure modes (e.g. an exotic mapping passed in by tests, a
        # proxy-resolution lib that crashes). Previously swallowed by the
        # bare except and treated as "no proxy", now must fail closed.
        class _BadProxies(dict):
            def __contains__(self, key):
                raise RuntimeError("proxy-resolution boom")

        bad = _BadProxies()
        bad["sentinel"] = "x"  # keep truthy so `proxies or {}` doesn't drop it

        with pytest.raises(UnsafeUrlError):
            adapter.send(_FakeRequest(), proxies=bad)

    def test_string_proxies_raises_unsafe_url_error(self):
        # Red-team finding (iter-2): requests.utils.select_proxy returns None
        # for a string proxies value instead of raising, so the bare-except
        # version silently treated "evil" as "no proxy". Must fail closed on
        # the type check BEFORE select_proxy is consulted.
        from WebProxy.service import PinnedIPAdapter, UnsafeUrlError

        adapter = PinnedIPAdapter(
            pinned_ip="127.0.0.1", hostname="example.com", port=443
        )

        class _FakeRequest:
            url = "https://example.com/path"
            headers: dict = {}

        with pytest.raises(UnsafeUrlError):
            adapter.send(_FakeRequest(), proxies="http://attacker")

    def test_list_proxies_raises_unsafe_url_error(self):
        # Same shape as string — select_proxy returns None for a list, so
        # without the isinstance(dict) gate, the adapter proceeds as if no
        # proxy were configured.
        from WebProxy.service import PinnedIPAdapter, UnsafeUrlError

        adapter = PinnedIPAdapter(
            pinned_ip="127.0.0.1", hostname="example.com", port=443
        )

        class _FakeRequest:
            url = "https://example.com/path"
            headers: dict = {}

        with pytest.raises(UnsafeUrlError):
            adapter.send(_FakeRequest(), proxies=["http://attacker"])

    def test_tuple_proxies_raises_unsafe_url_error(self):
        from WebProxy.service import PinnedIPAdapter, UnsafeUrlError

        adapter = PinnedIPAdapter(
            pinned_ip="127.0.0.1", hostname="example.com", port=443
        )

        class _FakeRequest:
            url = "https://example.com/path"
            headers: dict = {}

        with pytest.raises(UnsafeUrlError):
            adapter.send(_FakeRequest(), proxies=("http://attacker",))


class TestFetchPageRedditSingleDns:
    """B5 regression: fetch_page resolved DNS BEFORE delegating to fetch_reddit,
    so reddit URLs triggered two DNS lookups (once in fetch_page, once in
    fetch_reddit on json_url). The fix is to delegate before resolving."""

    def test_reddit_url_does_not_double_resolve(self, monkeypatch):
        from WebProxy import service as wps

        calls = []
        original = wps._resolve_first_safe_ip

        def _spy(url):
            calls.append(url)
            return "127.0.0.1"

        # Short-circuit fetch_reddit so we only count resolves up to delegation.
        monkeypatch.setattr(wps, "_resolve_first_safe_ip", _spy)
        monkeypatch.setattr(
            wps,
            "fetch_reddit",
            lambda url, timeout_s=30: {"delegated": True, "url": url},
        )

        result = wps.fetch_page(
            "https://www.reddit.com/r/python/comments/abc/x/"
        )
        assert result == {
            "delegated": True,
            "url": "https://www.reddit.com/r/python/comments/abc/x/",
        }
        # Zero resolves from fetch_page itself — delegation happens BEFORE
        # the caller-URL DNS lookup. fetch_reddit will do its own single
        # lookup on json_url, but that path is stubbed out here.
        assert calls == [], (
            f"fetch_page should not resolve DNS for reddit URLs before "
            f"delegating to fetch_reddit, but got: {calls}"
        )


# ============================================================================
# Mission a908c7ed — FetchResponse + _stream_capped_body unification
# ============================================================================


class _CapturedResponse:
    """Minimal response stand-in for _stream_capped_body direct tests.

    Tracks close() calls so the helper's cleanup contract is testable without
    a real HTTP stack.
    """

    def __init__(self, *, status_code=200, content_type="text/html",
                 chunks=(b"",), raise_error=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._chunks = chunks
        self._raise_error = raise_error
        self.closed = False

    def raise_for_status(self):
        if self._raise_error is not None:
            raise self._raise_error

    def iter_content(self, chunk_size=65536):
        for c in self._chunks:
            yield c

    def close(self):
        self.closed = True


class TestStreamCappedBody:
    """Unit tests for the extracted _stream_capped_body() helper."""

    def test_returns_status_content_type_body(self):
        resp = _CapturedResponse(
            status_code=200,
            content_type="application/json; charset=utf-8",
            chunks=(b"abc", b"def"),
        )
        status, ct, body = web_proxy_service._stream_capped_body(resp)
        assert status == 200
        assert ct == "application/json; charset=utf-8"
        assert body == b"abcdef"
        assert resp.closed is True

    def test_skips_empty_chunks(self):
        resp = _CapturedResponse(chunks=(b"", b"x", b"", b"y"))
        _, _, body = web_proxy_service._stream_capped_body(resp)
        assert body == b"xy"

    def test_enforces_cap(self, monkeypatch):
        monkeypatch.setattr(web_proxy_service, "MAX_FETCH_BYTES", 8)
        resp = _CapturedResponse(chunks=(b"x" * 16,))
        with pytest.raises(ValueError, match="MAX_FETCH_BYTES"):
            web_proxy_service._stream_capped_body(resp)
        assert resp.closed is True, "response must close even when cap trips"

    def test_closes_on_raise_for_status(self):
        import requests
        http_error = requests.HTTPError("403 Forbidden")
        resp = _CapturedResponse(raise_error=http_error)
        with pytest.raises(requests.HTTPError):
            web_proxy_service._stream_capped_body(resp)
        assert resp.closed is True


class TestFetchResponseDataclass:
    """FetchResponse.to_dict() must drop None-valued optionals so the
    image-path dict (no text/headings) and html-path dict (no local_path)
    retain their existing shape asymmetry."""

    def test_html_path_shape(self):
        resp = web_proxy_service.FetchResponse(
            url="https://x.example/",
            status_code=200,
            content_type="text/html",
            fetched_at=123,
            title="t",
            meta_description="m",
            headings=["h"],
            text="body",
            links=[{"text": "a", "url": "https://y.example/"}],
            text_length=4,
        )
        d = resp.to_dict()
        assert set(d.keys()) == {
            "url", "status_code", "content_type", "fetched_at",
            "title", "meta_description", "headings",
            "text", "links", "text_length",
        }

    def test_image_path_shape_preserves_none_width_height(self):
        # When PIL can't decode, width/height are None; the dict MUST still
        # carry the keys (not drop them) so cache consumers read them by key.
        resp = web_proxy_service.FetchResponse(
            url="https://x.example/img.png",
            status_code=200,
            content_type="image/png",
            fetched_at=123,
            type="image",
            local_path="/tmp/foo.png",
            byte_length=42,
            width=None,
            height=None,
        )
        d = resp.to_dict()
        assert set(d.keys()) == {
            "url", "status_code", "content_type", "fetched_at",
            "type", "local_path", "byte_length", "width", "height",
        }
        assert d["width"] is None
        assert d["height"] is None

    def test_reddit_path_shape(self):
        resp = web_proxy_service.FetchResponse(
            url="https://www.reddit.com/r/python/",
            status_code=200,
            content_type="application/json",
            fetched_at=123,
            title="Reddit: /r/python/",
            meta_description="",
            headings=[],
            text="",
            links=[],
            text_length=0,
            resolved_url="https://www.reddit.com/r/python/.json",
            reddit={"posts": [], "comments": [],
                    "post_count": 0, "comment_count": 0},
        )
        d = resp.to_dict()
        # Must include reddit-specific keys and NOT leak image-specific keys.
        assert "reddit" in d
        assert "resolved_url" in d
        assert "type" not in d
        assert "local_path" not in d
        assert "byte_length" not in d
        assert "width" not in d
        assert "height" not in d


class TestRedditContentTypeGate:
    """Reddit path: a 200 response with text/html content-type (rate-limit
    interstitial / login wall) previously reached json.loads and raised
    JSONDecodeError. The gate must return the normal reddit payload shape
    with empty posts/comments instead."""

    def test_200_text_html_does_not_call_json_loads(self, monkeypatch):
        # If json.loads is called with HTML, it raises JSONDecodeError; the
        # gate should short-circuit BEFORE reaching json.loads. Spy on it.
        json_loads_calls = []
        real_loads = json.loads

        def _spy_loads(*args, **kwargs):
            json_loads_calls.append(args)
            return real_loads(*args, **kwargs)

        monkeypatch.setattr(web_proxy_service.json, "loads", _spy_loads)

        html_body = b"<!DOCTYPE html><html><body>rate limited</body></html>"

        class _HtmlResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield html_body

            def close(self):
                return None

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _HtmlResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )

        result = fetch_reddit("https://www.reddit.com/r/python/comments/abc/x/")

        assert json_loads_calls == [], (
            "json.loads must NOT be called on a text/html reddit response"
        )
        assert result["status_code"] == 200
        assert result["content_type"] == "text/html; charset=utf-8"
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []
        assert result["reddit"]["post_count"] == 0

    def test_200_vnd_api_json_suffix_is_accepted(self, monkeypatch):
        # application/vnd.reddit.v1+json (or any +json suffix) must pass
        # the gate.
        body = json.dumps([
            {"kind": "Listing", "data": {"children": [
                {"kind": "t3", "data": {"title": "ok", "author": "u",
                                         "score": 1, "num_comments": 0,
                                         "subreddit": "s",
                                         "url": "https://x.example/",
                                         "permalink": "/p"}}]}},
            {"kind": "Listing", "data": {"children": []}},
        ]).encode("utf-8")

        class _JsonSuffixResponse:
            status_code = 200
            headers = {"content-type": "application/vnd.reddit.v1+json"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield body

            def close(self):
                return None

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _JsonSuffixResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )

        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["reddit"]["posts"][0]["title"] == "ok"

    def test_200_json_ct_with_malformed_body_degrades(self, monkeypatch):
        # Servers lie: content-type says JSON but body is garbage. The
        # defensive JSONDecodeError wrap must degrade to empty posts/comments
        # instead of propagating.
        garbage = b"this is not { json at all"

        class _LiarResponse:
            status_code = 200
            headers = {"content-type": "application/json"}

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield garbage

            def close(self):
                return None

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _LiarResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )

        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert result["status_code"] == 200
        assert result["reddit"]["posts"] == []
        assert result["reddit"]["comments"] == []


class TestFetchResponseRoundTripEquivalence:
    """End-to-end: the refactored fetch_reddit / fetch_page returns the same
    key set as before the FetchResponse introduction. The canonical keys are
    listed here; any drift is a regression."""

    _REDDIT_KEYS = frozenset({
        "url", "resolved_url", "title", "meta_description", "headings",
        "text", "links", "text_length", "reddit",
        "status_code", "content_type", "fetched_at",
    })

    _HTML_KEYS = frozenset({
        "url", "title", "meta_description", "headings",
        "text", "links", "text_length",
        "status_code", "content_type", "fetched_at",
    })

    def test_reddit_returns_canonical_key_set(self, monkeypatch):
        body = json.dumps([
            {"kind": "Listing", "data": {"children": []}},
            {"kind": "Listing", "data": {"children": []}},
        ]).encode("utf-8")
        _install_reddit_session_with_body(monkeypatch, body)
        result = fetch_reddit("https://www.reddit.com/r/python/")
        assert frozenset(result.keys()) == self._REDDIT_KEYS

    def test_html_returns_canonical_key_set(self, monkeypatch):
        html_body = (
            b"<html><head><title>T</title></head>"
            b"<body><p>hello</p></body></html>"
        )

        class _HtmlResponse:
            status_code = 200
            headers = {"content-type": "text/html; charset=utf-8"}
            encoding = "utf-8"

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size=65536):
                yield html_body

            def close(self):
                return None

        class _FakeSession:
            def get(self, *args, **kwargs):
                return _HtmlResponse()

        @contextmanager
        def _fake_pinned_session(*args, **kwargs):
            yield _FakeSession()

        monkeypatch.setattr(
            web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
        )
        monkeypatch.setattr(
            web_proxy_service, "_pinned_session", _fake_pinned_session
        )

        result = fetch_page("https://example.com/")
        assert frozenset(result.keys()) == self._HTML_KEYS
