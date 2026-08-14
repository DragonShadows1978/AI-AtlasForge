"""
JavaScript rendering fallback for WebProxy.

Certain pages (ChatGPT shares, Claude shares, Twitter/X, many modern SPAs)
return near-empty HTML from a plain HTTP GET because their content is
rendered client-side by JavaScript. A BeautifulSoup extract of that raw HTML
yields nothing useful. This module provides a headless-Chromium fallback
via Playwright that actually executes the page's JavaScript and returns the
post-hydration DOM.

The markup-density SPA signal requires a framework marker, at least 3 script
tags, at least 10,000 HTML characters, no more than 1,500 visible-text
characters, and a visible-text/HTML ratio below 2%. These conservative bounds
catch large hydration shells while leaving ordinary server-rendered articles
on the plain HTTP path.

Activation is lazy — Playwright and its Chromium binary are only imported
and launched when a fetch site needs them. The plain requests path stays
fast and cheap for the 95% of sites that don't need a browser.

SSRF safety note:
  The plain-path `fetch_page` resolves a URL to a single pinned IP and
  refuses to follow cross-host redirects. A real browser cannot be pinned
  to a single IP the same way — the JS inside the page may fetch dozens
  of third-party resources (CDNs, analytics, images). We rely on the
  CALLER having validated the top-level URL via _resolve_first_safe_ip
  before dispatching here. Sub-resource fetches are trusted to the
  browser's normal DNS resolution; this is the same trust model a human
  browsing the URL would have.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


JS_RENDER_TIMEOUT_S = int(os.environ.get("ATLASFORGE_WEBPROXY_JS_TIMEOUT_S", "30"))
JS_RENDER_HYDRATE_MS = int(os.environ.get("ATLASFORGE_WEBPROXY_JS_HYDRATE_MS", "2500"))
JS_RENDER_UA = os.environ.get(
    "ATLASFORGE_WEBPROXY_JS_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
)

# Hosts where we always prefer the headless-Chromium path. These sites
# server-render with markup the plain BeautifulSoup extractor can't parse
# (ChatGPT share pages nest messages in <div>s that escape the <main>/<p>
# rule set; same pattern for Claude shares and a few others). Listing them
# explicitly avoids fragile length-heuristic detection.
_JS_RENDER_HOSTS = {
    "chatgpt.com", "chat.openai.com",
    "claude.ai",
    "poe.com",
    "x.com", "twitter.com",
    "linkedin.com",
}


def host_wants_render(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    # Match exact host OR any subdomain
    for h in _JS_RENDER_HOSTS:
        if host == h or host.endswith("." + h):
            return True
    return False


def is_available() -> bool:
    """Return True if Playwright (and its Chromium) is installed."""
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except ImportError:
        return False


def should_render(html: str, status_code: int, content_type: str) -> bool:
    """Heuristic: does this response look like it needs JS to have content?

    Signals we check:
      - HTTP 403 with a Cloudflare challenge marker
      - Very short body with an SPA framework marker
      - A framework-marked, script-heavy body with under 2% visible text

    Returns False for anything that clearly doesn't need rendering so we
    don't spend a headless Chrome launch on a page that already rendered
    server-side.
    """
    if not html:
        return False
    low = html[:16384].lower()
    # Cloudflare interstitial/challenge
    if "cf-mitigated" in low or "just a moment" in low or (
        "cloudflare" in low and "challenge" in low
    ):
        return True
    # SPA framework signatures. We can't rely on the root div being literally
    # empty — Next/React ship a hydration shell with metadata, script tags,
    # and loading skeletons. Instead, we look for the framework identifier
    # AND a script-heavy body with very little real text content.
    spa_signals = (
        'id="__next"',      # Next.js
        'id="__nuxt"',      # Nuxt
        'id="root"',        # CRA / generic React
        '__next_data__',    # Next payload marker
        'window.__nuxt__',  # Nuxt state
        '_app-',            # Next asset path
    )
    has_spa_signal = any(m in low for m in spa_signals)
    if has_spa_signal:
        # Count visible-content tags versus script tags. SPAs are script-heavy
        # with almost no server-rendered <p>/<article>/<section> content.
        script_count = low.count("<script")
        para_count = low.count("<p>") + low.count("<article") + low.count("<section")
        if script_count >= 3 and para_count <= 2:
            return True
        without_code = re.sub(
            r"<(?:script|style|noscript)\b[^>]*>.*?</(?:script|style|noscript)\s*>",
            " ",
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        visible_text = re.sub(r"<[^>]+>", " ", without_code)
        visible_length = len(re.sub(r"\s+", " ", visible_text).strip())
        markup_length = len(html)
        script_count = len(re.findall(r"<script\b", html, flags=re.IGNORECASE))
        if (
            markup_length >= 10_000
            and script_count >= 3
            and visible_length <= 1_500
            and visible_length / markup_length < 0.02
        ):
            return True
    # Very short body with script-heavy shell is almost certainly SPA
    if len(html) < 4000 and html.count("<script") >= 2 and "<p>" not in html and "<article" not in html:
        return True
    return False


def render(
    url: str,
    timeout_s: int = JS_RENDER_TIMEOUT_S,
    hydrate_ms: int = JS_RENDER_HYDRATE_MS,
) -> Optional[Dict[str, Any]]:
    """Load `url` in headless Chromium and return the rendered HTML + text.

    Returns None if Playwright isn't installed. Raises RuntimeError on
    rendering failure (caller can fall back to whatever it had).
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.warning("js_render: Playwright not installed; skipping JS fallback")
        return None

    logger.info("js_render: launching headless chromium for %s", url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent=JS_RENDER_UA,
                viewport={"width": 1280, "height": 720},
                java_script_enabled=True,
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_s * 1000)
            except Exception as e:
                # Many SPAs never hit "networkidle" — fall through and grab
                # whatever DOM has hydrated so far.
                logger.info("js_render: goto('%s') did not reach networkidle: %s", url, e)
            page.wait_for_timeout(hydrate_ms)
            rendered_html = page.content()
            try:
                body_text = page.inner_text("body")
            except Exception:
                body_text = ""
            title = page.title() or ""
            return {
                "rendered_html": rendered_html,
                "rendered_text": body_text,
                "rendered_title": title,
            }
        finally:
            browser.close()
