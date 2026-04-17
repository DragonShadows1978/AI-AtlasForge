"""Thin HTTP client for the AtlasForge web proxy's /stats endpoint.

Used by dashboard_v2 to surface proxy activity counters (cached searches,
cached fetches, provider breakdown) without needing the full web_proxy_client
dependency chain.

The proxy lives at http://127.0.0.1:8765 by default and exposes a lightweight
/stats JSON endpoint. This module wraps that call with sane timeouts and
graceful error handling so the dashboard never blocks or crashes when the
proxy is down.
"""

from __future__ import annotations

import os
from typing import Any

import requests

# Canonical env var: ATLASFORGE_WEB_PROXY_URL. WEB_PROXY_URL is a backcompat
# fallback — both web_proxy_client and web_proxy_stats used to disagree on
# which one to read; now they agree on ATLASFORGE_WEB_PROXY_URL first.
PROXY_BASE = os.environ.get(
    "ATLASFORGE_WEB_PROXY_URL",
    os.environ.get("WEB_PROXY_URL", "http://127.0.0.1:8765"),
).rstrip("/")
DEFAULT_TIMEOUT = 2.0


class ProxyStatsError(RuntimeError):
    """Raised when the proxy is unreachable or returns invalid data."""


def get_proxy_stats(timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Fetch live stats from the proxy.

    Args:
        timeout: Network timeout in seconds. Keep small — this is called from
            a live dashboard poll loop.

    Returns:
        Dict with keys like `cached_fetches`, `cached_searches`,
        `cached_images`, `cached_image_searches`, `providers`, `cache_dir`.

    Raises:
        ProxyStatsError: if the proxy is unreachable, times out, or returns
            non-JSON.
    """
    url = f"{PROXY_BASE}/stats"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.ConnectionError as e:
        raise ProxyStatsError(f"proxy unreachable at {url}: {e}") from e
    except requests.exceptions.Timeout as e:
        raise ProxyStatsError(f"proxy timeout at {url}: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ProxyStatsError(f"proxy error at {url}: {e}") from e
    except ValueError as e:
        raise ProxyStatsError(f"proxy returned non-JSON: {e}") from e

    if not isinstance(data, dict):
        raise ProxyStatsError(f"proxy returned non-object: {type(data).__name__}")

    return data


def get_proxy_stats_safe(timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    """Non-raising variant for use in dashboard polls.

    Returns either the stats dict (with `status: "ok"` merged in) or an error
    envelope `{"status": "error", "error": "<message>"}`. Never raises.
    """
    try:
        stats = get_proxy_stats(timeout=timeout)
        stats["status"] = "ok"
        return stats
    except ProxyStatsError as e:
        return {"status": "error", "error": str(e)}


def is_proxy_alive(timeout: float = 1.0) -> bool:
    """Fast health check. Returns True iff /health responds with 200 quickly."""
    try:
        resp = requests.get(f"{PROXY_BASE}/health", timeout=timeout)
        return resp.status_code == 200
    except requests.exceptions.RequestException:
        return False
