"""WP-R3 unit tests: Reddit `.rss` rung, Medium author-feed rung, js_render budget.

All outbound HTTP is mocked — no real network, no bind on 8765/8792.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import time

import pytest
import requests

from WebProxy import js_render as web_proxy_js_render
from WebProxy import service as web_proxy_service
from WebProxy.service import (
    fetch_page,
    fetch_reddit,
    _medium_article_parts,
    _reddit_rss_url,
)


def _unsafe_url_error():
    """The LIVE UnsafeUrlError class, read through the module every time.

    `test_service.py` calls `importlib.reload(web_proxy_service)`, which
    rebinds the module's classes to NEW class objects. A name bound by
    `from WebProxy.service import UnsafeUrlError` at collection time then
    refers to a stale class that no longer matches the one production code
    raises or catches — so these tests would pass alone and fail in the full
    suite. Always resolve the class through the module at call time.
    """
    return web_proxy_service.UnsafeUrlError


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


def _reddit_atom_post_feed(comment_count: int = 3) -> bytes:
    """Atom feed shaped like reddit's `/r/<sub>/comments/<id>/<slug>/.rss`.

    entry[0] is the submission; the rest are comments.
    """
    entries = [
        """
        <entry>
          <author><name>/u/op_author</name></author>
          <title>The Original Post Title</title>
          <link href="https://www.reddit.com/r/testsub/comments/abc123/the_original_post_title/"/>
          <updated>2026-08-20T10:00:00+00:00</updated>
          <content type="html">&lt;div&gt;&lt;p&gt;This is the original post body with enough
          words in it to survive extraction.&lt;/p&gt;&lt;/div&gt;</content>
        </entry>
        """
    ]
    for i in range(comment_count):
        entries.append(
            f"""
        <entry>
          <author><name>/u/commenter_{i}</name></author>
          <title>commenter_{i} on The Original Post Title</title>
          <link href="https://www.reddit.com/r/testsub/comments/abc123/the_original_post_title/c{i}/"/>
          <updated>2026-08-20T11:0{i}:00+00:00</updated>
          <content type="html">&lt;div&gt;&lt;p&gt;Comment number {i} saying something
          substantive about the post.&lt;/p&gt;&lt;/div&gt;</content>
        </entry>
        """
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>The Original Post Title</title>" + "".join(entries) + "</feed>"
    ).encode("utf-8")


def _reddit_atom_listing_feed(n: int = 2) -> bytes:
    entries = "".join(
        f"""
        <entry>
          <author><name>/u/poster_{i}</name></author>
          <title>Listing Post {i}</title>
          <link href="https://www.reddit.com/r/testsub/comments/lst{i}/listing_post_{i}/"/>
          <updated>2026-08-2{i}T10:00:00+00:00</updated>
          <content type="html">&lt;p&gt;Listing body {i} with words.&lt;/p&gt;</content>
        </entry>
        """
        for i in range(n)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>testsub</title>" + entries + "</feed>"
    ).encode("utf-8")


def _medium_feed_body(post_id: str = "fb6bebfcd566", body_words: int = 60) -> bytes:
    paragraph = " ".join(["medium article sentence"] * body_words)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>Author Feed</title>"
        "<item>"
        "<title>Some Other Article</title>"
        "<link>https://medium.com/@shedlesky/some-other-article-000000000000</link>"
        "<guid>https://medium.com/p/000000000000</guid>"
        f"<content:encoded>&lt;p&gt;unrelated&lt;/p&gt;</content:encoded>"
        "</item>"
        "<item>"
        "<title>The Target Article</title>"
        f"<link>https://medium.com/@shedlesky/the-target-article-{post_id}</link>"
        f"<guid>https://medium.com/p/{post_id}</guid>"
        f"<content:encoded>&lt;div&gt;&lt;p&gt;{paragraph}&lt;/p&gt;&lt;/div&gt;</content:encoded>"
        "</item>"
        "</channel></rss>"
    ).encode("utf-8")


def _install_reddit_session(monkeypatch, session):
    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        yield session

    monkeypatch.setattr(web_proxy_service, "_ensure_safe_url", lambda url: None)
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: None)


def _install_page_session(monkeypatch, session):
    @contextmanager
    def _fake_pinned_session(*args, **kwargs):
        yield session

    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "medium.com", 443), None),
    )
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda url: False)
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: False)
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: None)


_POST_URL = "https://www.reddit.com/r/testsub/comments/abc123/the_original_post_title/"


# ---------------------------------------------------------------------------
# D1 — Reddit `.rss` fallback rung
# ---------------------------------------------------------------------------


def test_all_json_rungs_403_falls_back_to_rss(monkeypatch):
    """Gate 3.1: every .json rung 403s -> the .rss rung serves post + comments."""
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            assert kwargs.get("allow_redirects") is False
            if url.endswith("/.rss"):
                return _FakeResponse(
                    status_code=200,
                    body=_reddit_atom_post_feed(comment_count=3),
                    headers={"content-type": "application/atom+xml; charset=UTF-8"},
                )
            return _FakeResponse(
                status_code=403,
                body=b"blocked",
                headers={"content-type": "text/html"},
            )

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    result = fetch_reddit(_POST_URL)

    # Every json rung was tried first — .rss is a FALLBACK, not a replacement.
    json_rungs = [u for u in seen if ".json" in u]
    assert len(json_rungs) >= 1
    assert seen[-1].endswith("/.rss")

    assert result["source_format"] == "rss"
    assert result["reddit"]["source_format"] == "rss"
    assert result["reddit"]["post_count"] == 1
    assert result["reddit"]["comment_count"] == 3

    post = result["reddit"]["posts"][0]
    assert post["title"] == "The Original Post Title"
    assert post["author"] == "op_author"
    assert post["subreddit"] == "testsub"
    assert "original post body" in post["selftext"]
    assert post["created_utc"] > 0

    comment = result["reddit"]["comments"][0]
    assert comment["author"] == "commenter_0"
    assert "Comment number 0" in comment["body"]

    # Result SHAPE is unchanged from the json path so /fetch consumers and
    # mcp_server.py formatting keep working.
    for key in ("url", "title", "text", "links", "reddit", "status_code"):
        assert key in result
    assert "The Original Post Title" in result["text"]


def test_rss_rung_preserves_original_url_as_cache_key_contract(monkeypatch):
    """The returned `url` stays the ORIGINAL request URL (cache-key contract)."""

    class _Session:
        def get(self, url, **kwargs):
            if url.endswith("/.rss"):
                return _FakeResponse(
                    status_code=200,
                    body=_reddit_atom_post_feed(),
                    headers={"content-type": "application/atom+xml"},
                )
            return _FakeResponse(status_code=403, body=b"x")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    result = fetch_reddit(_POST_URL)
    assert result["url"] == _POST_URL
    assert result["resolved_url"].endswith("/.rss")


def test_rss_rung_handles_subreddit_listing(monkeypatch):
    """Listing URLs map to the listing shape: all entries are posts."""

    class _Session:
        def get(self, url, **kwargs):
            if url.endswith("/.rss"):
                return _FakeResponse(
                    status_code=200,
                    body=_reddit_atom_listing_feed(n=2),
                    headers={"content-type": "application/atom+xml"},
                )
            return _FakeResponse(status_code=403, body=b"x")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    result = fetch_reddit("https://www.reddit.com/r/testsub/")
    assert result["source_format"] == "rss"
    assert result["reddit"]["post_count"] == 2
    assert result["reddit"]["comment_count"] == 0
    assert result["reddit"]["posts"][0]["title"] == "Listing Post 0"


def test_rss_rung_also_failing_reports_last_json_status(monkeypatch):
    """Gate 3.2: rss rung ALSO fails -> honest error from the last json rung."""

    class _Session:
        def get(self, url, **kwargs):
            if url.endswith("/.rss"):
                return _FakeResponse(status_code=404, body=b"no feed")
            return _FakeResponse(status_code=403, body=b"blocked")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    with pytest.raises(requests.HTTPError) as exc_info:
        fetch_reddit(_POST_URL)

    # The surfaced status is the JSON rung's 403, not the rss rung's 404 —
    # the fallback must not mask what the real API said.
    response = getattr(exc_info.value, "response", None)
    assert response is not None
    assert response.status_code == 403


def test_rss_rung_empty_feed_reraises_json_error(monkeypatch):
    """A parseable but entry-less feed is not a success; the json error stands."""

    class _Session:
        def get(self, url, **kwargs):
            if url.endswith("/.rss"):
                return _FakeResponse(
                    status_code=200,
                    body=(
                        '<?xml version="1.0"?>'
                        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
                    ).encode("utf-8"),
                    headers={"content-type": "application/atom+xml"},
                )
            return _FakeResponse(status_code=403, body=b"blocked")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    with pytest.raises(requests.HTTPError):
        fetch_reddit(_POST_URL)


def test_derived_rss_url_goes_through_ssrf_validation(monkeypatch):
    """Gate 3.3: the derived .rss URL is validated + pinned like every fetch.

    A private-IP resolution for the .rss host is REJECTED — the derived URL
    gets no exemption from the WP-R1 SSRF laws.
    """
    resolved: list[str] = []

    def _resolve(url):
        resolved.append(url)
        if url.endswith("/.rss"):
            # Simulate DNS returning a link-local/private address for the
            # derived host: _resolve_first_safe_ip refuses it.
            raise _unsafe_url_error()("resolved to unsafe IP 169.254.169.254")
        return "127.0.0.1"

    class _Session:
        def get(self, url, **kwargs):
            assert not url.endswith("/.rss"), "unsafe .rss URL must never be fetched"
            return _FakeResponse(status_code=403, body=b"blocked")

    @contextmanager
    def _fake_pinned_session(pinned_ip, hostname, port):
        yield _Session()

    monkeypatch.setattr(web_proxy_service, "_ensure_safe_url", lambda url: None)
    monkeypatch.setattr(web_proxy_service, "_resolve_first_safe_ip", _resolve)
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _fake_pinned_session)
    monkeypatch.setattr(web_proxy_service.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    with pytest.raises(_unsafe_url_error()):
        fetch_reddit(_POST_URL)

    assert any(u.endswith("/.rss") for u in resolved), "rss URL must be resolved"


def test_derived_rss_url_rejected_by_structural_validation(monkeypatch):
    """Structural validation failure on the derived URL also refuses, not skips."""

    def _validate(url):
        if url.endswith("/.rss"):
            return None, "scheme not allowed"
        return ("https", "www.reddit.com", 443), None

    class _Session:
        def get(self, url, **kwargs):
            assert not url.endswith("/.rss")
            return _FakeResponse(status_code=403, body=b"blocked")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(web_proxy_service, "_validate_url_structure", _validate)

    with pytest.raises(_unsafe_url_error()):
        fetch_reddit(_POST_URL)


def test_rss_rung_sends_no_redirect_follow(monkeypatch):
    """The rss rung never delegates redirect handling to requests/urllib3."""
    kwargs_seen: list[dict] = []

    class _Session:
        def get(self, url, **kwargs):
            kwargs_seen.append(kwargs)
            if url.endswith("/.rss"):
                return _FakeResponse(
                    status_code=200,
                    body=_reddit_atom_post_feed(),
                    headers={"content-type": "application/atom+xml"},
                )
            return _FakeResponse(status_code=403, body=b"x")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    fetch_reddit(_POST_URL)
    assert kwargs_seen, "no request was made"
    for kw in kwargs_seen:
        assert kw.get("allow_redirects") is False
        assert kw.get("stream") is True


def test_json_success_keeps_json_source_format(monkeypatch):
    """`.json` stays PREFERRED: a working json rung never reaches .rss."""
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            return _FakeResponse(
                status_code=200,
                body=b"[]",
                headers={"content-type": "application/json"},
            )

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    result = fetch_reddit(_POST_URL)
    assert result["source_format"] == "json"
    assert not any(u.endswith("/.rss") for u in seen)


def test_rss_fallback_can_be_disabled(monkeypatch):
    """The kill switch restores the pre-WP-R3 behavior exactly."""
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            return _FakeResponse(status_code=403, body=b"blocked")

    _install_reddit_session(monkeypatch, _Session())
    monkeypatch.setattr(web_proxy_service, "REDDIT_RSS_FALLBACK", False)
    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "www.reddit.com", 443), None),
    )

    with pytest.raises(requests.HTTPError):
        fetch_reddit(_POST_URL)
    assert not any(u.endswith("/.rss") for u in seen)


def test_reddit_rss_url_derivation():
    """Query params dropped, /.rss appended, old.reddit normalized to www."""
    assert _reddit_rss_url(_POST_URL) == _POST_URL + ".rss"
    assert (
        _reddit_rss_url("https://www.reddit.com/r/py/comments/a/b/?sort=top&x=1")
        == "https://www.reddit.com/r/py/comments/a/b/.rss"
    )
    assert (
        _reddit_rss_url("https://old.reddit.com/r/py/")
        == "https://www.reddit.com/r/py/.rss"
    )
    assert (
        _reddit_rss_url("https://www.reddit.com/r/py/hot")
        == "https://www.reddit.com/r/py/hot/.rss"
    )
    # Idempotent on an already-.rss path.
    assert (
        _reddit_rss_url("https://www.reddit.com/r/py/.rss")
        == "https://www.reddit.com/r/py/.rss"
    )


# ---------------------------------------------------------------------------
# D2 — Medium author-feed fallback
# ---------------------------------------------------------------------------


_MEDIUM_URL = (
    "https://medium.com/@shedlesky/"
    "consciousness-explained-how-the-brain-creates-the-mind-and-soul-fb6bebfcd566"
)


def test_medium_403_recovers_via_author_feed(monkeypatch):
    """Gate 3.4: medium 403 -> author feed fetched, item matched by hex id."""
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            assert kwargs.get("allow_redirects") is False
            if "/feed/@" in url:
                return _FakeResponse(
                    status_code=200,
                    body=_medium_feed_body(),
                    headers={"content-type": "application/rss+xml"},
                )
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>Cloudflare</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())

    result = fetch_page(_MEDIUM_URL, max_chars=-1)

    assert any("/feed/@shedlesky" in u for u in seen)
    assert result["source_format"] == "author_feed"
    assert result["original_status"] == 403
    assert result["status_code"] == 200
    assert result["js_rendered"] is False
    assert "medium article sentence" in result["text"]
    assert result["text_length"] > 200


def test_medium_403_article_absent_from_feed_errors_with_403(monkeypatch):
    """Gate 3.5: article outside the feed window -> error with target_status 403."""

    class _Session:
        def get(self, url, **kwargs):
            if "/feed/@" in url:
                # Feed is served, but carries a DIFFERENT post id.
                return _FakeResponse(
                    status_code=200,
                    body=_medium_feed_body(post_id="aaaaaaaaaaaa"),
                    headers={"content-type": "application/rss+xml"},
                )
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>Cloudflare</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())

    with pytest.raises(web_proxy_service.TargetHTTPError) as exc_info:
        fetch_page(_MEDIUM_URL, max_chars=-1)
    assert exc_info.value.target_status == 403


def test_medium_feed_unreachable_falls_back_to_403(monkeypatch):
    """A 5xx on the feed itself must not mask the article's honest 403."""

    class _Session:
        def get(self, url, **kwargs):
            if "/feed/@" in url:
                return _FakeResponse(status_code=500, body=b"boom")
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>Cloudflare</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())

    with pytest.raises(web_proxy_service.TargetHTTPError) as exc_info:
        fetch_page(_MEDIUM_URL, max_chars=-1)
    assert exc_info.value.target_status == 403


def test_medium_feed_url_goes_through_ssrf_validation(monkeypatch):
    """The derived feed URL is resolved+pinned like every other fetch."""
    resolved: list[str] = []

    def _resolve(url):
        resolved.append(url)
        if "/feed/@" in url:
            raise _unsafe_url_error()("resolved to unsafe IP 127.0.0.1")
        return "127.0.0.1"

    class _Session:
        def get(self, url, **kwargs):
            assert "/feed/@" not in url, "unsafe feed URL must never be fetched"
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>Cloudflare</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())
    monkeypatch.setattr(web_proxy_service, "_resolve_first_safe_ip", _resolve)

    with pytest.raises(_unsafe_url_error()):
        fetch_page(_MEDIUM_URL, max_chars=-1)
    assert any("/feed/@" in u for u in resolved)


def test_medium_fallback_scoped_to_medium_hosts(monkeypatch):
    """No generalized "try RSS on any 403": a non-medium 403 stays a 403."""
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>nope</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())

    with pytest.raises(web_proxy_service.TargetHTTPError):
        fetch_page("https://example.com/@someone/article-fb6bebfcd566", max_chars=-1)
    assert not any("/feed/@" in u for u in seen)


def test_medium_feed_fallback_can_be_disabled(monkeypatch):
    seen: list[str] = []

    class _Session:
        def get(self, url, **kwargs):
            seen.append(url)
            return _FakeResponse(
                status_code=403,
                body=b"<html><body>Cloudflare</body></html>",
                headers={"content-type": "text/html"},
            )

    _install_page_session(monkeypatch, _Session())
    monkeypatch.setattr(web_proxy_service, "MEDIUM_FEED_FALLBACK", False)

    with pytest.raises(web_proxy_service.TargetHTTPError):
        fetch_page(_MEDIUM_URL, max_chars=-1)
    assert not any("/feed/@" in u for u in seen)


def test_medium_article_url_shape_parsing():
    assert _medium_article_parts(_MEDIUM_URL) == ("shedlesky", "fb6bebfcd566")
    # Publication subdomains are in scope.
    assert _medium_article_parts(
        "https://towardsdatascience.medium.com/@bob/a-post-0123456789ab"
    ) == ("bob", "0123456789ab")
    # Non-medium hosts, non-article paths, and custom domains are not.
    assert _medium_article_parts("https://example.com/@bob/a-post-0123456789ab") is None
    assert _medium_article_parts("https://medium.com/@bob") is None
    assert _medium_article_parts("https://medium.com/tag/python") is None
    # A host that merely CONTAINS "medium.com" must not match.
    assert _medium_article_parts(
        "https://notmedium.com/@bob/a-post-0123456789ab"
    ) is None


# ---------------------------------------------------------------------------
# D3 — js_render timeout budget
# ---------------------------------------------------------------------------


class _NeverIdlePage:
    """Page whose networkidle never arrives; domcontentloaded is instant.

    Sleeps model real wall-clock so the test can assert the BUDGET, not just
    the call sequence.
    """

    def __init__(self, calls: list, goto_wall_s: float = 0.05):
        self.calls = calls
        self._goto_wall_s = goto_wall_s

    def goto(self, url, wait_until=None, timeout=None):
        self.calls.append(("goto", wait_until, timeout))
        if wait_until == "networkidle":
            # The pre-WP-R3 behavior: burn the whole budget, then raise.
            time.sleep(timeout / 1000.0)
            raise TimeoutError("networkidle never reached")
        time.sleep(self._goto_wall_s)

    def wait_for_load_state(self, state, timeout=None):
        self.calls.append(("wait_for_load_state", state, timeout))
        # Never idles: consume the bounded settle window, then raise.
        time.sleep(timeout / 1000.0)
        raise TimeoutError("networkidle never reached")

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))
        time.sleep(ms / 1000.0)

    def content(self):
        return "<html><body><article><p>hydrated content</p></article></body></html>"

    def inner_text(self, selector):
        return "hydrated content"

    def title(self):
        return "Never Idle Page"


def _install_fake_playwright(monkeypatch, page):
    class _Context:
        def new_page(self):
            return page

    class _Browser:
        def new_context(self, **kwargs):
            return _Context()

        def close(self):
            pass

    class _Chromium:
        def launch(self, **kwargs):
            return _Browser()

    class _PW:
        chromium = _Chromium()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(
        web_proxy_js_render, "sync_playwright", lambda: _PW(), raising=False
    )
    import types

    fake_module = types.ModuleType("playwright.sync_api")
    fake_module.sync_playwright = lambda: _PW()
    fake_pkg = types.ModuleType("playwright")
    fake_pkg.sync_api = fake_module
    monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)


def test_js_render_never_idle_page_captures_under_ceiling(monkeypatch):
    """Gate 3.6: a never-idle page captures after the bounded settle wait.

    The pre-WP-R3 code passed wait_until="networkidle" with the FULL budget to
    goto, so a never-idle page spent all 30s there and the caller's own 30s
    ceiling fired first. Now goto waits domcontentloaded, the settle window is
    bounded, and capture always happens.
    """
    calls: list = []
    page = _NeverIdlePage(calls)
    _install_fake_playwright(monkeypatch, page)

    start = time.monotonic()
    result = web_proxy_js_render.render(
        "https://never-idle.example/article",
        timeout_s=30,
        hydrate_ms=200,
        settle_ms=300,
    )
    elapsed = time.monotonic() - start

    assert result is not None
    assert "hydrated content" in result["rendered_text"]
    assert result["rendered_title"] == "Never Idle Page"

    # goto asked for domcontentloaded, NOT networkidle, and was capped well
    # below the 30s budget.
    goto_calls = [c for c in calls if c[0] == "goto"]
    assert len(goto_calls) == 1
    assert goto_calls[0][1] == "domcontentloaded"
    assert goto_calls[0][2] <= web_proxy_js_render.JS_RENDER_GOTO_TIMEOUT_S * 1000

    # networkidle was attempted, but only inside the bounded settle window.
    settle_calls = [c for c in calls if c[0] == "wait_for_load_state"]
    assert len(settle_calls) == 1
    assert settle_calls[0][1] == "networkidle"
    assert settle_calls[0][2] == 300

    # And the whole render finished far under the caller's ceiling.
    assert elapsed < 25.0
    assert elapsed < 5.0, f"never-idle render took {elapsed:.2f}s"


def test_js_render_worst_case_budget_stays_under_ceiling():
    """The DEFAULT knobs must leave headroom under the MCP client's 30s."""
    worst_case_s = (
        web_proxy_js_render.JS_RENDER_GOTO_TIMEOUT_S
        + web_proxy_js_render.JS_RENDER_SETTLE_MS / 1000.0
        + web_proxy_js_render.JS_RENDER_HYDRATE_MS / 1000.0
    )
    assert worst_case_s < 25.0, f"worst-case render budget is {worst_case_s}s"


def test_js_render_goto_cap_respects_smaller_caller_timeout(monkeypatch):
    """A caller passing a SMALLER timeout still caps the goto at its value."""
    calls: list = []
    page = _NeverIdlePage(calls)
    _install_fake_playwright(monkeypatch, page)

    web_proxy_js_render.render(
        "https://never-idle.example/x", timeout_s=3, hydrate_ms=10, settle_ms=10
    )
    goto_calls = [c for c in calls if c[0] == "goto"]
    assert goto_calls[0][2] == 3000


def test_js_render_normal_page_behavior_unchanged(monkeypatch):
    """A page that loads normally is captured exactly as before."""
    calls: list = []

    class _NormalPage(_NeverIdlePage):
        def wait_for_load_state(self, state, timeout=None):
            calls.append(("wait_for_load_state", state, timeout))
            # Idles immediately — no exception, no waiting.

        def content(self):
            return "<html><body><p>server rendered</p></body></html>"

        def inner_text(self, selector):
            return "server rendered"

        def title(self):
            return "Normal Page"

    page = _NormalPage(calls, goto_wall_s=0.0)
    _install_fake_playwright(monkeypatch, page)

    start = time.monotonic()
    result = web_proxy_js_render.render(
        "https://normal.example/", timeout_s=30, hydrate_ms=10, settle_ms=500
    )
    elapsed = time.monotonic() - start

    assert result["rendered_text"] == "server rendered"
    assert result["rendered_title"] == "Normal Page"
    # No settle penalty for a page that idles immediately.
    assert elapsed < 1.0
