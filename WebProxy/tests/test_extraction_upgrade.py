"""Offline regression tests for WebProxy extraction-quality upgrades."""

from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import quote

from WebProxy import js_render
from WebProxy import service as web_proxy_service
from WebProxy.mcp_server import _format_fetch_results


class _Response:
    def __init__(self, body: bytes, content_type: str = "text/html"):
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self.encoding = "utf-8"
        self._body = body
        self.text = body.decode("utf-8", errors="replace")

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=65536):
        yield self._body

    def close(self):
        return None


def _install_fetch_response(monkeypatch, response: _Response) -> None:
    class _Session:
        def get(self, *args, **kwargs):
            return response

    @contextmanager
    def _pinned_session(*args, **kwargs):
        yield _Session()

    monkeypatch.setattr(
        web_proxy_service,
        "_validate_url_structure",
        lambda url: (("https", "example.com", 443), None),
    )
    monkeypatch.setattr(
        web_proxy_service, "_resolve_first_safe_ip", lambda url: "127.0.0.1"
    )
    monkeypatch.setattr(web_proxy_service, "_pinned_session", _pinned_session)
    monkeypatch.setattr(web_proxy_service, "_is_reddit_url", lambda url: False)
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: False)


def test_trafilatura_extracts_article_from_bare_div(monkeypatch):
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_EXTRACTOR", "auto")
    article = " ".join(
        f"Paragraph {i} explains extraction quality with detailed evidence and reproducible observations."
        for i in range(12)
    )
    html = (
        "<html><head><title>Bare Div Story</title></head><body>"
        "<nav>Home Products Sign in Cookie settings</nav>"
        f'<div class="content">{article}</div>'
        "<footer>Privacy Terms Contact Copyright</footer>"
        "</body></html>"
    )

    result = web_proxy_service.extract_page_content(
        "https://example.com/bare", html, max_chars=5000
    )

    assert result["extraction_method"] == "trafilatura"
    assert "Paragraph 11 explains extraction quality" in result["text"]
    assert "Cookie settings" not in result["text"]
    assert result["truncated"] is False


def test_auto_falls_back_to_bs4_when_trafilatura_returns_nothing(monkeypatch):
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_EXTRACTOR", "auto")
    monkeypatch.setattr(web_proxy_service._trafilatura, "extract", lambda *a, **k: None)

    result = web_proxy_service.extract_page_content(
        "https://example.com/story",
        "<html><body><main><h1>Fallback</h1><p>BS4 kept this.</p></main></body></html>",
        max_chars=1000,
    )

    assert result["extraction_method"] == "bs4_fallback"
    assert result["text"] == "Fallback\n\nBS4 kept this."


def test_bs4_env_forces_old_extraction_path(monkeypatch):
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_EXTRACTOR", "bs4")

    def _unexpected_extract(*args, **kwargs):
        raise AssertionError("trafilatura must not run in bs4 mode")

    monkeypatch.setattr(web_proxy_service._trafilatura, "extract", _unexpected_extract)
    result = web_proxy_service.extract_page_content(
        "https://example.com/story",
        "<html><body><main><p>Forced BS4 content.</p></main></body></html>",
        max_chars=1000,
    )

    assert result["extraction_method"] == "bs4"
    assert result["text"] == "Forced BS4 content."


def test_truncation_marks_payload_respects_limit_and_avoids_midword_cut():
    text = (
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu "
        "nu xi omicron pi rho sigma tau upsilon phi chi psi omega."
    )

    result = web_proxy_service._apply_max_chars({"text": text}, 90)
    shown, marker = result["text"].splitlines()

    assert result["truncated"] is True
    assert result["full_text_length"] == len(text)
    assert result["text_length"] == len(result["text"]) <= 90
    assert text.startswith(shown)
    assert text[len(shown)].isspace()
    assert marker == f"…[truncated: showing {len(shown)} of {len(text)} chars]"
    formatted = _format_fetch_results({"url": "https://example.com", **result})
    assert marker in formatted
    assert "Truncated: True" in formatted


def test_fetch_page_binary_zip_returns_structured_shape_without_decode(monkeypatch):
    body = b"PK\x03\x04\xff\xfe\x00binary archive"
    _install_fetch_response(monkeypatch, _Response(body, "application/zip"))

    result = web_proxy_service.fetch_page("https://example.com/archive.zip")

    assert result["content_kind"] == "binary"
    assert result["content_type"] == "application/zip"
    assert result["content_length"] == len(body)
    assert result["text"] == (
        f"Binary content (application/zip, {len(body)} bytes) — not fetchable as text."
    )
    assert "�" not in result["text"]
    for key in ("title", "meta_description", "headings", "text", "links", "text_length"):
        assert key in result


def test_fetch_page_pdf_returns_paperfetch_hint_shape(monkeypatch):
    body = b"%PDF-1.7 fake fixture"
    _install_fetch_response(monkeypatch, _Response(body, "application/pdf"))

    result = web_proxy_service.fetch_page("https://example.com/paper.pdf")

    assert result["content_kind"] == "binary"
    assert result["content_type"] == "application/pdf"
    assert result["content_length"] == len(body)
    assert "PaperFetch tool" in result["text"]
    assert "/paper/fetch" in result["text"]


def test_duckduckgo_parser_unwraps_redirect_href():
    destination = "https://example.com/article?q=quality"
    wrapped = f"//duckduckgo.com/l/?uddg={quote(destination, safe='')}"
    html = (
        '<div class="result"><a class="result__a" href="'
        + wrapped
        + '">Result</a><div class="result__snippet">Snippet</div></div>'
    )
    provider = web_proxy_service.SearchProvider(
        session=type("Session", (), {"post": lambda self, *a, **k: _Response(html.encode())})()
    )

    result = provider.search_duckduckgo("quality", count=1)

    assert result["results"][0]["url"] == destination


def test_duckduckgo_parser_keeps_malformed_redirect_href():
    malformed = "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%ZZbad"
    html = f'<div class="result"><a class="result__a" href="{malformed}">Result</a></div>'
    provider = web_proxy_service.SearchProvider(
        session=type("Session", (), {"post": lambda self, *a, **k: _Response(html.encode())})()
    )

    result = provider.search_duckduckgo("quality", count=1)

    assert result["results"][0]["url"] == malformed


def test_should_render_large_spa_shell_with_low_visible_text_ratio():
    visible = "Rendered boilerplate " * 25
    scripts = "".join(
        f"<script>window.chunk{i}='{('x' * 34_000)}';</script>" for i in range(3)
    )
    html = f'<html><body><div id="__next">{visible}</div>{scripts}</body></html>'

    assert len(html) > 100_000
    assert len(visible) > 200
    assert js_render.should_render(html, 200, "text/html") is True


def test_should_render_normal_article_is_false():
    paragraphs = "".join(
        f"<p>Article paragraph {i} contains useful prose and supporting detail.</p>"
        for i in range(40)
    )
    html = f"<html><body><article><h1>Normal article</h1>{paragraphs}</article></body></html>"

    assert js_render.should_render(html, 200, "text/html") is False


def test_fetch_page_and_formatter_surface_js_rendered(monkeypatch):
    html = b'<html><body><div id="root"></div><script></script><script></script></body></html>'
    _install_fetch_response(monkeypatch, _Response(html))
    monkeypatch.setenv("ATLASFORGE_WEB_PROXY_EXTRACTOR", "bs4")
    monkeypatch.setattr(web_proxy_service, "_js_render_enabled", lambda: True)
    monkeypatch.setattr(js_render, "host_wants_render", lambda url: False)
    monkeypatch.setattr(js_render, "should_render", lambda *args: True)
    monkeypatch.setattr(
        js_render,
        "render",
        lambda url: {
            "rendered_html": "<html><body><main><p>Hydrated content.</p></main></body></html>",
            "rendered_text": "Hydrated content.",
            "rendered_title": "Hydrated",
        },
    )

    result = web_proxy_service.fetch_page("https://example.com/spa")
    formatted = _format_fetch_results(result)

    assert result["rendered"] is True
    assert result["js_rendered"] is True
    assert "JS-rendered: yes" in formatted
    assert "Extraction method: bs4" in formatted
