"""
Client helpers for routing web-search/fetch tool calls through the local proxy.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional

import requests

try:
    from exploration_hooks import log_web_fetch_tool, log_web_search_tool
except Exception:  # pragma: no cover - optional logging integration
    log_web_fetch_tool = None
    log_web_search_tool = None


DEFAULT_PROXY_URL = os.environ.get("ATLASFORGE_WEB_PROXY_URL", "http://127.0.0.1:8765").rstrip("/")
DEFAULT_TIMEOUT_S = int(os.environ.get("ATLASFORGE_WEB_PROXY_CLIENT_TIMEOUT_S", "30"))


def _post(path: str, payload: Dict[str, Any], timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    response = requests.post(
        f"{DEFAULT_PROXY_URL}{path}",
        json=payload,
        timeout=timeout_s,
    )
    response.raise_for_status()
    return response.json()


def search_web_via_proxy(
    query: str,
    count: int = 5,
    provider: str = "auto",
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    started = time.time()
    try:
        payload = _post(
            "/search",
            {"query": query, "count": count, "provider": provider},
            timeout_s=timeout_s,
        )
    except Exception as exc:
        if log_web_search_tool:
            log_web_search_tool(
                query=query,
                success=False,
                error=str(exc),
                duration_ms=int((time.time() - started) * 1000),
            )
        raise

    if log_web_search_tool:
        log_web_search_tool(
            query=query,
            results_count=len(payload.get("results", [])),
            success=True,
            duration_ms=int((time.time() - started) * 1000),
        )
    return payload


def fetch_web_via_proxy(
    url: str,
    max_chars: int = 12000,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    started = time.time()
    try:
        payload = _post(
            "/fetch",
            {"url": url, "max_chars": max_chars},
            timeout_s=timeout_s,
        )
    except Exception as exc:
        if log_web_fetch_tool:
            log_web_fetch_tool(
                url=url,
                content_length=0,
                success=False,
                error=str(exc),
                duration_ms=int((time.time() - started) * 1000),
            )
        raise

    if log_web_fetch_tool:
        log_web_fetch_tool(
            url=url,
            content_length=len(payload.get("text", "")),
            success=True,
            duration_ms=int((time.time() - started) * 1000),
        )
    return payload


def research_via_proxy(
    query: str,
    count: int = 5,
    fetch_top_n: int = 3,
    provider: str = "auto",
    max_chars: int = 12000,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Dict[str, Any]:
    return _post(
        "/research",
        {
            "query": query,
            "count": count,
            "fetch_top_n": fetch_top_n,
            "provider": provider,
            "max_chars": max_chars,
        },
        timeout_s=timeout_s,
    )


def proxy_health(timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    response = requests.get(f"{DEFAULT_PROXY_URL}/health", timeout=timeout_s)
    response.raise_for_status()
    return response.json()
