"""
Local HTTP web search/fetch proxy for AtlasForge and external tool runtimes.

This service is intended to sit between an LLM tool call and external search
providers/web pages. The model does not browse directly; it calls a local tool,
the tool calls this service, and this service returns normalized JSON.

Hardened (mission_7203bd97): closes the DNS rebinding TOCTOU window between
the SSRF guard and the outbound fetch by resolving DNS exactly once via
`_resolve_first_safe_ip` and pinning the outbound connection to that IP via
`PinnedIPAdapter`. SNI, Host header, and cert verification stay bound to the
original hostname.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import logging
import math
import os
import random
import re
import socket
import stat as stat_module
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request
from requests.adapters import HTTPAdapter
from werkzeug.exceptions import RequestEntityTooLarge

try:
    import waitress
except ImportError:  # pragma: no cover - exercised when optional dependency is absent
    waitress = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


def _int_env(name: str, default: int, *, min_value: Optional[int] = None) -> int:
    """Read int env var; on invalid or too-small values, log and return default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "env %s=%r is not an integer; falling back to %d", name, raw, default
        )
        return default
    if min_value is not None and value < min_value:
        logger.warning(
            "env %s=%r must be >= %d; falling back to %d",
            name, raw, min_value, default,
        )
        return default
    return value


def _float_env(name: str, default: float, *, min_value: Optional[float] = None) -> float:
    """Read float env var; on invalid/non-finite/too-small values, log and return default."""
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "env %s=%r is not a float; falling back to %s", name, raw, default
        )
        return default
    if not math.isfinite(value):
        logger.warning(
            "env %s=%r is not finite; falling back to %s", name, raw, default
        )
        return default
    if min_value is not None and value < min_value:
        logger.warning(
            "env %s=%r must be >= %s; falling back to %s",
            name, raw, min_value, default,
        )
        return default
    return value


def _bool_env(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _header_env(name: str, default: str) -> str:
    raw = os.environ.get(name, default)
    if "\r" in raw or "\n" in raw:
        raise ValueError(f"{name} must not contain CR/LF characters")
    return raw


DEFAULT_HOST = os.environ.get("ATLASFORGE_WEB_PROXY_HOST", "127.0.0.1")
DEFAULT_PORT = _int_env("ATLASFORGE_WEB_PROXY_PORT", 8765, min_value=1)
DEFAULT_PROVIDER = os.environ.get("ATLASFORGE_WEB_PROXY_PROVIDER", "auto").strip().lower()
DEFAULT_TIMEOUT_S = _int_env("ATLASFORGE_WEB_PROXY_TIMEOUT_S", 20, min_value=1)
CACHE_DIR = Path(
    os.environ.get(
        "ATLASFORGE_WEB_PROXY_CACHE_DIR",
        str(Path(__file__).resolve().parent / "atlasforge_data" / "web_proxy_cache"),
    )
)
PAPER_ARTIFACT_DIR = Path(
    os.environ.get(
        "ATLASFORGE_WEB_PROXY_PAPER_DIR",
        str(CACHE_DIR.parent / "paper_artifacts"),
    )
)
SEARCH_TTL_S = _int_env("ATLASFORGE_WEB_PROXY_SEARCH_TTL_S", 1800, min_value=0)
FETCH_TTL_S = _int_env("ATLASFORGE_WEB_PROXY_FETCH_TTL_S", 86400, min_value=0)
PAPER_TTL_S = _int_env("ATLASFORGE_WEB_PROXY_PAPER_TTL_S", 7 * 86400, min_value=0)
CACHE_STALE_GRACE = _float_env(
    "ATLASFORGE_WEB_PROXY_CACHE_STALE_GRACE", 2.0, min_value=0.0
)
CACHE_MAX_BYTES = _int_env(
    "ATLASFORGE_WEB_PROXY_CACHE_MAX_BYTES", 2_000_000_000, min_value=0
)
CACHE_SWEEP_INTERVAL_S = _float_env(
    "ATLASFORGE_WEB_PROXY_CACHE_SWEEP_INTERVAL_S", 3600.0, min_value=0.0
)
WEB_PROXY_THREADS = _int_env("ATLASFORGE_WEB_PROXY_THREADS", 16, min_value=1)
STATS_PATH = CACHE_DIR.parent / "web_proxy_stats.json"
USER_AGENT = _header_env(
    "ATLASFORGE_WEB_PROXY_USER_AGENT",
    (
        "AI-AtlasForge-WebProxy/0.1 "
        "(local research proxy; contact admin if traffic is unexpected)"
    ),
)

BRAVE_API_KEY_ENV_NAMES = ("ATLASFORGE_BRAVE_API_KEY", "BRAVE_API_KEY")
DDGS_FALLBACK_BACKENDS = tuple(
    backend.strip()
    for backend in os.environ.get(
        "ATLASFORGE_WEB_PROXY_DDGS_BACKENDS",
        "google,bing,yahoo,mojeek,wikipedia",
    ).split(",")
    if backend.strip()
)
DDGS_IMAGE_FALLBACK_BACKENDS = tuple(
    backend.strip()
    for backend in os.environ.get(
        "ATLASFORGE_WEB_PROXY_DDGS_IMAGE_BACKENDS",
        "bing",
    ).split(",")
    if backend.strip()
)

REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "en-US,en;q=0.8",
}

# Browser-like UA for 403 UA-retry and Reddit fallback ladder. Many CDNs
# (Cloudflare, etc.) 403 the honest research-proxy UA but accept desktop Chrome.
BROWSER_USER_AGENT = _header_env(
    "ATLASFORGE_WEB_PROXY_BROWSER_USER_AGENT",
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
)
# Alternate browser UA used only on the Reddit host-fallback ladder after a 403.
REDDIT_ALT_USER_AGENT = _header_env(
    "ATLASFORGE_WEB_PROXY_REDDIT_ALT_USER_AGENT",
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) "
        "Gecko/20100101 Firefox/122.0"
    ),
)

# Bounded exponential backoff for fetch_page / fetch_reddit outbound GETs.
# Retries only transient failures (429/502/503/504, connection/timeout errors).
# Permanent statuses (401/403/404) are never retried by this path.
RETRY_MAX_ATTEMPTS = _int_env("ATLASFORGE_WEB_PROXY_RETRY_ATTEMPTS", 3, min_value=1)
RETRY_BASE_DELAY_S = _float_env("ATLASFORGE_WEB_PROXY_RETRY_BASE_S", 0.5, min_value=0.0)
RETRY_AFTER_MAX_S = _float_env("ATLASFORGE_WEB_PROXY_RETRY_AFTER_MAX_S", 60.0, min_value=0.0)
RETRY_JITTER = _bool_env("ATLASFORGE_WEB_PROXY_RETRY_JITTER", True)
UA_RETRY_ON_403 = _bool_env("ATLASFORGE_WEB_PROXY_UA_RETRY_ON_403", True)
REDDIT_OLD_FALLBACK = _bool_env("ATLASFORGE_WEB_PROXY_REDDIT_OLD_FALLBACK", True)

_TRANSIENT_HTTP_STATUS = frozenset({429, 502, 503, 504})
_RETRYABLE_REQUEST_EXCEPTIONS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)

MAX_FETCH_BYTES = _int_env("ATLASFORGE_WEB_PROXY_MAX_FETCH_BYTES", 5 * 1024 * 1024, min_value=1024)
MAX_PAPER_BYTES = _int_env("ATLASFORGE_WEB_PROXY_MAX_PAPER_BYTES", 50 * 1024 * 1024, min_value=1024)
MAX_URL_LENGTH = _int_env("ATLASFORGE_WEB_PROXY_MAX_URL_LENGTH", 2048, min_value=32)
MAX_REQUEST_BYTES = _int_env("ATLASFORGE_WEB_PROXY_MAX_REQUEST_BYTES", 2 * 1024 * 1024, min_value=1024)
MAX_OBSERVABILITY_ENTRIES = _int_env("ATLASFORGE_WEB_PROXY_MAX_OBSERVABILITY_ENTRIES", 1000, min_value=1)
_LEGACY_NUMERIC_HOST_RE = re.compile(r"^(?:0x[0-9a-f]+|0[0-7]*|[0-9]+)$", re.IGNORECASE)


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the SSRF guard."""


def _ip_is_unsafe(ip: "ipaddress._BaseAddress") -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _validate_url_structure(url: str) -> Tuple[Optional[Tuple[str, str, int]], str]:
    """Structural URL validation — no DNS. Returns ((scheme, host, port), "")
    on success, or (None, reason) on failure."""
    if not isinstance(url, str):
        return None, "url must be a string"
    if not url:
        return None, "missing url"
    if len(url) > MAX_URL_LENGTH:
        return None, f"url exceeds {MAX_URL_LENGTH} chars"
    if "\x00" in url or "\r" in url or "\n" in url:
        return None, "url contains control characters"
    if any(ch.isspace() for ch in url):
        return None, "url contains whitespace"
    try:
        parsed = urlparse(url)
    except Exception as e:  # pragma: no cover
        return None, f"parse failed: {e}"
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return None, f"unsupported scheme: {scheme or '(none)'}"
    try:
        host = parsed.hostname
    except ValueError:
        return None, "invalid host"
    if not host:
        return None, "missing host"
    if "\x00" in host or "\r" in host or "\n" in host or any(ch.isspace() for ch in host):
        return None, "host contains unsafe characters"
    host_no_dot = host.rstrip(".")
    try:
        host_ip = ipaddress.ip_address(host_no_dot)
    except ValueError:
        if _looks_like_legacy_numeric_host(host_no_dot):
            return None, "host uses unsupported numeric ip notation"
    else:
        normalized_ip = (
            host_ip.ipv4_mapped
            if isinstance(host_ip, ipaddress.IPv6Address) and host_ip.ipv4_mapped
            else host_ip
        )
        if _ip_is_unsafe(normalized_ip):
            return None, "host resolves to a non-public address"
    try:
        explicit_port = parsed.port
    except ValueError:
        return None, "invalid port"
    if explicit_port == 0:
        return None, "invalid port: 0"
    port = explicit_port if explicit_port is not None else (443 if scheme == "https" else 80)
    return (scheme, host, port), ""


def _looks_like_legacy_numeric_host(host: str) -> bool:
    """Detect hostnames getaddrinfo may parse as non-canonical numeric IPv4."""
    if not host or ":" in host:
        return False
    parts = host.split(".")
    if len(parts) > 4:
        return False
    if not all(_LEGACY_NUMERIC_HOST_RE.fullmatch(part or "") for part in parts):
        return False
    if len(parts) == 4:
        canonical = True
        for part in parts:
            if not part.isdigit():
                canonical = False
                break
            if len(part) > 1 and part.startswith("0"):
                canonical = False
                break
            if int(part) > 255:
                canonical = False
                break
        if canonical:
            return False
    return True


def _resolve_first_safe_ip(url: str) -> str:
    """Single-DNS-lookup SSRF guard. Returns the first resolved IP as a
    string (no brackets, no scope) on success. Raises UnsafeUrlError if
    the URL is structurally invalid, DNS fails, or ANY resolved IP is
    non-public. "Any unsafe => reject" mirrors the prior guard and prevents
    mixed round-robin DNS from leaking a connection to a private address."""
    parts, reason = _validate_url_structure(url)
    if parts is None:
        raise UnsafeUrlError(reason)
    _scheme, host, port = parts
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise UnsafeUrlError(f"dns failed: {e}")
    first_ip: Optional[str] = None
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        if "%" in ip_str:
            ip_str = ip_str.split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            logger.warning("DNS returned unparsable address for host %r: %r", host, ip_str)
            raise UnsafeUrlError("dns returned an invalid address")
        if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
            normalized = ip.ipv4_mapped
        else:
            normalized = ip
        if _ip_is_unsafe(normalized):
            logger.warning("DNS resolved host %r to non-public address %s", host, normalized)
            raise UnsafeUrlError("dns resolved to a non-public address")
        if first_ip is None:
            first_ip = str(normalized)
    if first_ip is None:
        raise UnsafeUrlError("dns returned no addresses")
    return first_ip


def _is_safe_url(url: str) -> Tuple[bool, str]:
    """Return (ok, reason). Thin wrapper around `_resolve_first_safe_ip`
    that preserves the historical tuple-returning contract used by the
    `/fetch`, `/research`, and `/image_search` endpoints and any out-of-
    tree callers. This helper does its own DNS lookup; to avoid a second
    TOCTOU window at fetch sites, use `_resolve_first_safe_ip` directly
    and pin the resolved IP into the outbound request."""
    try:
        _resolve_first_safe_ip(url)
    except UnsafeUrlError as e:
        return False, str(e)
    return True, ""


def _ensure_safe_url(url: str) -> None:
    ok, reason = _is_safe_url(url)
    if not ok:
        raise UnsafeUrlError(reason)


def _rewrite_url_host(url: str, pinned_ip: str, port: int) -> str:
    """Swap the host of `url` with `pinned_ip`, preserving scheme, path,
    query, fragment, and port. IPv6 literals are bracketed."""
    parsed = urlparse(url)
    try:
        ip_obj = ipaddress.ip_address(pinned_ip)
    except ValueError:
        host_literal = pinned_ip
    else:
        if isinstance(ip_obj, ipaddress.IPv6Address):
            host_literal = f"[{pinned_ip}]"
        else:
            host_literal = pinned_ip
    # Preserve userinfo if present (rare; not a case we expect here).
    userinfo = ""
    if parsed.username is not None:
        userinfo = parsed.username
        if parsed.password is not None:
            userinfo += f":{parsed.password}"
        userinfo += "@"
    netloc = f"{userinfo}{host_literal}:{port}"
    return urlunparse(parsed._replace(netloc=netloc))


class PinnedIPAdapter(HTTPAdapter):
    """HTTPAdapter that routes every outbound request to a pre-resolved IP
    while keeping the original hostname for SNI, cert verification, and the
    HTTP Host header. This closes the DNS rebinding TOCTOU window between
    the SSRF guard's resolve and urllib3's independent resolve: there IS
    no second resolve, because the URL on the wire points at a numeric IP.

    Pinning is adapter-scoped: each `fetch_page` creates a fresh Session
    with a single PinnedIPAdapter, so there is no cross-request bleed of
    pool state.
    """

    def __init__(self, pinned_ip: str, hostname: str, port: int, *args, **kwargs):
        self._pinned_ip = pinned_ip
        self._hostname = hostname
        self._port = port
        # Serializes mutation of poolmanager.connection_pool_kw so a session
        # accidentally shared across threads cannot have one send() smear the
        # assert_hostname/server_hostname of a different host into a concurrent
        # TLS handshake. Single-use adapters (current pattern) see no contention.
        self._pool_kw_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):  # type: ignore[override]
        # Refuse to pin through an HTTP proxy — the proxy, not us, picks
        # the destination IP, so pinning would be meaningless. The service
        # does not configure outbound proxies, but we fail loudly if one
        # ever sneaks in.
        proxies = kwargs.get("proxies")
        if proxies is None:
            proxies = {}
        # Reject any non-dict proxies value up front. select_proxy silently
        # returns None for truthy non-dict values like "http://attacker" or
        # ["http://attacker"], which would let the caller sneak past the
        # proxy-bypass guard below ("selected_proxy falsy" ≠ "no proxy").
        # Fail closed on the type before we ever consult select_proxy.
        if not isinstance(proxies, dict):
            raise UnsafeUrlError(
                f"proxies must be a dict, got {type(proxies).__name__}"
            )
        # A malformed `proxies` dict (e.g. {"http": 42}) makes select_proxy
        # raise. Swallowing the error and setting selected_proxy=None would
        # SKIP the proxy-bypass guard below — silently treating a parse
        # failure as "no proxy in use". Fail closed: re-raise as UnsafeUrlError
        # so the caller cannot coax the adapter into routing bytes through an
        # attacker-controlled proxy by handing it a deliberately broken dict.
        try:
            selected_proxy = requests.utils.select_proxy(request.url, proxies)
        except Exception as exc:
            raise UnsafeUrlError(
                f"could not evaluate proxy selection: {exc!r}"
            ) from exc
        if selected_proxy:
            raise UnsafeUrlError(
                "outbound HTTP proxy detected; IP pinning is incompatible"
            )

        parsed = urlparse(request.url)
        # Paranoia: only pin if the request is actually for the hostname
        # we resolved. If something (a redirect, a misconfigured session)
        # sent us a different host, fail closed rather than leak.
        if (parsed.hostname or "").lower() != self._hostname.lower():
            raise UnsafeUrlError(
                f"host mismatch: expected {self._hostname}, got {parsed.hostname}"
            )

        # Rewrite URL to the pinned IP so urllib3 never does its own
        # getaddrinfo. Preserve the original port.
        request.url = _rewrite_url_host(request.url, self._pinned_ip, self._port)

        # Preserve the original hostname in the Host header so virtual
        # hosting still routes correctly at the destination.
        host_header_value = self._hostname
        if (parsed.scheme == "https" and self._port != 443) or (
            parsed.scheme == "http" and self._port != 80
        ):
            host_header_value = f"{self._hostname}:{self._port}"
        request.headers["Host"] = host_header_value

        # For HTTPS we need urllib3 to present the original hostname as SNI
        # and verify the cert against it, NOT the pinned IP. The official
        # urllib3 2.x way is `assert_hostname` + `server_hostname` on the
        # connection pool kwargs. These kwargs are HTTPS-only; plain HTTP
        # connections reject them, so gate on scheme.
        if parsed.scheme == "https":
            pm = self.poolmanager
            with self._pool_kw_lock:
                saved = {
                    "assert_hostname": pm.connection_pool_kw.get("assert_hostname"),
                    "server_hostname": pm.connection_pool_kw.get("server_hostname"),
                }
                pm.connection_pool_kw["assert_hostname"] = self._hostname
                pm.connection_pool_kw["server_hostname"] = self._hostname
                try:
                    return super().send(request, **kwargs)
                finally:
                    for k, v in saved.items():
                        if v is None:
                            pm.connection_pool_kw.pop(k, None)
                        else:
                            pm.connection_pool_kw[k] = v
        return super().send(request, **kwargs)


def _pinned_session(pinned_ip: str, hostname: str, port: int) -> requests.Session:
    """Build a Session whose only adapter pins to `pinned_ip`. The session
    is returned so the caller owns its lifetime (prefer `with _pinned_session(...)`).
    """
    session = requests.Session()
    # Env-var proxies (HTTP_PROXY/HTTPS_PROXY/NO_PROXY) would route bytes
    # through a third party and bypass IP pinning — the SSRF guard's
    # single-resolve-and-pin invariant dies there. PinnedIPAdapter.send
    # rejects per-request proxies; disabling trust_env closes the env-var
    # side channel.
    session.trust_env = False
    adapter = PinnedIPAdapter(pinned_ip=pinned_ip, hostname=hostname, port=port)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def _retry_after_seconds(response: Optional[requests.Response]) -> Optional[float]:
    """Parse Retry-After as delta-seconds or HTTP-date. Returns None if absent/invalid."""
    if response is None:
        return None
    raw = response.headers.get("Retry-After") or response.headers.get("retry-after")
    if raw is None:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    try:
        return max(0.0, float(int(raw)))
    except (TypeError, ValueError):
        pass
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            # HTTP-date is GMT; treat naive as UTC wall-clock for safety.
            return max(0.0, dt.timestamp() - time.time())
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _compute_backoff_s(attempt: int, *, response: Optional[requests.Response] = None) -> float:
    """Delay before the next retry. ``attempt`` is 0-based (0 = after first failure).

    On HTTP 429, prefer a capped Retry-After when the header is present;
    otherwise exponential backoff: base * 2^attempt (+ optional jitter in [0, base]).
    """
    status = getattr(response, "status_code", None) if response is not None else None
    if status == 429:
        ra = _retry_after_seconds(response)
        if ra is not None:
            return min(ra, RETRY_AFTER_MAX_S) if RETRY_AFTER_MAX_S > 0 else ra
    base = RETRY_BASE_DELAY_S
    delay = base * (2 ** max(0, attempt))
    if RETRY_JITTER and base > 0:
        delay += random.uniform(0.0, base)
    return delay


def _session_get_with_retry(
    session: requests.Session,
    url: str,
    *,
    headers: Dict[str, str],
    timeout_s: float,
    stream: bool = True,
    allow_redirects: bool = False,
    max_attempts: Optional[int] = None,
) -> requests.Response:
    """GET with bounded exponential backoff on *transient* failures only.

    Retries on connection/timeout errors and HTTP 429/502/503/504.
    Permanent statuses (incl. 401/403/404) are returned immediately — the
    caller decides whether to raise, UA-retry, or host-fallback.
    Does **not** call ``raise_for_status``; body streaming stays with the caller.
    """
    attempts = RETRY_MAX_ATTEMPTS if max_attempts is None else max(1, int(max_attempts))
    last_exc: Optional[BaseException] = None
    response: Optional[requests.Response] = None

    for attempt in range(attempts):
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=timeout_s,
                stream=stream,
                allow_redirects=allow_redirects,
            )
        except _RETRYABLE_REQUEST_EXCEPTIONS as exc:
            last_exc = exc
            if attempt + 1 >= attempts:
                raise
            delay = _compute_backoff_s(attempt)
            logger.info(
                "fetch retry after error attempt=%d/%d delay=%.3fs url=%s err=%s",
                attempt + 1,
                attempts,
                delay,
                url,
                exc,
            )
            time.sleep(delay)
            continue

        status = int(getattr(response, "status_code", 0) or 0)
        if status not in _TRANSIENT_HTTP_STATUS:
            # Success or permanent failure — hand back to caller.
            return response

        if attempt + 1 >= attempts:
            return response

        delay = _compute_backoff_s(attempt, response=response)
        logger.info(
            "fetch retry after HTTP %s attempt=%d/%d delay=%.3fs url=%s",
            status,
            attempt + 1,
            attempts,
            delay,
            url,
        )
        try:
            response.close()
        except Exception:
            pass
        time.sleep(delay)

    if last_exc is not None:
        raise last_exc
    if response is not None:
        return response
    raise RuntimeError("unreachable: _session_get_with_retry exhausted without result")


def _now_ts() -> int:
    return int(time.time())


def _js_render_enabled() -> bool:
    """Gate for the headless-browser fallback in `fetch_page`.

    Disabled when `ATLASFORGE_WEBPROXY_JS_RENDER=0`. Defaults on.
    Also disabled if Playwright isn't importable, but that check is lazy
    inside `fetch_page` to avoid startup-time import cost.
    """
    val = os.environ.get("ATLASFORGE_WEBPROXY_JS_RENDER", "1").strip().lower()
    return val not in ("0", "false", "no", "off", "")


def _first_env(names: tuple[str, ...]) -> Optional[str]:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _html_attr_text(value: Any) -> str:
    """Return a scalar HTML attribute value from BeautifulSoup's loose shapes."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _html_attr_text(item)
            if text:
                return text
        return ""
    return str(value).strip()


def _cache_key(prefix: str, payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()}"


def _validate_timeout_s(timeout_s: Any) -> float:
    if isinstance(timeout_s, bool):
        raise ValueError(f"timeout_s must be a positive number, got {timeout_s!r}")
    try:
        value = float(timeout_s)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"timeout_s must be a positive number, got {timeout_s!r}")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"timeout_s must be a positive finite number, got {timeout_s!r}")
    return value


def _apply_max_chars(payload: Dict[str, Any], max_chars: Any) -> Dict[str, Any]:
    """Truncate payload["text"] to max_chars and keep text_length consistent.

    Contract:
      max_chars < 0  -> unlimited (text preserved, text_length recomputed)
      max_chars == 0 -> empty text, text_length == 0
      max_chars > 0  -> truncate to that many chars, text_length recomputed

    Negative values are an internal-use sentinel for populating cached
    full-text payloads. HTTP endpoints reject `max_chars < 0` at
    `_coerce_count(min_value=0)`, so only internal call sites reach that
    branch. Non-coercible values (NaN, strings) return the payload
    unchanged so callers that never set max_chars get full text.

    `text_length` is ALWAYS recomputed from the final text string so a
    tampered cache entry with a mismatched `text_length` cannot propagate.
    """
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError, OverflowError):
        return payload
    text = payload.get("text") or ""
    out = dict(payload)
    if max_chars < 0:
        out["text"] = text
        out["text_length"] = len(text)
        return out
    if max_chars == 0:
        out["text"] = ""
        out["text_length"] = 0
        return out
    if max_chars >= len(text):
        out["text"] = text
        out["text_length"] = len(text)
        return out
    out["text"] = text[:max_chars]
    out["text_length"] = len(out["text"])
    return out


@dataclass
class FetchResponse:
    """Unified internal return shape for fetch_page / fetch_reddit.

    Serializes to the same dict shape the HTTP layer and cache consume today;
    every non-core field is ``Optional`` and ``to_dict`` drops ``None`` values.
    The image-path dict (no text/headings/links) and the html-path dict (no
    local_path/byte_length) retain their existing asymmetry because the caller
    leaves the unused fields as ``None``.
    """

    url: str
    status_code: int
    fetched_at: int
    content_type: Optional[str] = None
    title: Optional[str] = None
    meta_description: Optional[str] = None
    headings: Optional[List[str]] = None
    text: Optional[str] = None
    links: Optional[List[Dict[str, str]]] = None
    text_length: Optional[int] = None
    resolved_url: Optional[str] = None
    reddit: Optional[Dict[str, Any]] = None
    type: Optional[str] = None
    local_path: Optional[str] = None
    byte_length: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    rendered: Optional[bool] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Preserve the image-path dict's explicit ``width: None`` / ``height: None``
        # when PIL can't decode; dropping them would silently change the cached
        # JSON shape that consumers read by key.
        preserve_null_for_image = {"width", "height"}
        is_image = d.get("type") == "image"
        return {
            k: v
            for k, v in d.items()
            if v is not None or (is_image and k in preserve_null_for_image)
        }


def _stream_capped_body(response, *, chunk_size: int = 65536) -> Tuple[int, str, bytes]:
    """Stream ``response`` into memory with MAX_FETCH_BYTES cap, then close.

    Returns ``(status_code, content_type_header, body_bytes)``. The finally
    block always closes the response so HTTPError, cap overflow, and clean
    success share identical cleanup — matching the regression tests that
    assert the old inlined shape.
    """
    try:
        response.raise_for_status()
        status_code = response.status_code
        if not 200 <= status_code < 300:
            raise requests.HTTPError(f"unexpected HTTP status {status_code}", response=response)
        content_type = response.headers.get("content-type", "")
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            total += len(chunk)
            if total > MAX_FETCH_BYTES:
                raise ValueError(
                    f"response body exceeds MAX_FETCH_BYTES ({MAX_FETCH_BYTES})"
                )
            chunks.append(chunk)
    finally:
        response.close()
    return status_code, content_type, b"".join(chunks)


def _stream_capped_body_with_limit(
    response,
    max_bytes: int,
    *,
    chunk_size: int = 65536,
) -> Tuple[int, str, bytes]:
    """Stream ``response`` into memory with a caller-supplied byte cap."""
    try:
        response.raise_for_status()
        status_code = response.status_code
        if not 200 <= status_code < 300:
            raise requests.HTTPError(f"unexpected HTTP status {status_code}", response=response)
        content_type = response.headers.get("content-type", "")
        chunks: List[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=chunk_size):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"response body exceeds max_bytes ({max_bytes})")
            chunks.append(chunk)
    finally:
        response.close()
    return status_code, content_type, b"".join(chunks)


def _paper_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _normalize_arxiv_pdf_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if host.endswith("arxiv.org") and path.startswith("/abs/"):
        arxiv_id = path[len("/abs/"):].strip("/")
        if arxiv_id:
            return urlunparse(parsed._replace(path=f"/pdf/{arxiv_id}", query="", fragment=""))
    return url


def _extract_pdf_text_bytes(content: bytes) -> Tuple[str, Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "extractor": None,
        "page_count": None,
        "pages_extracted": 0,
        "extraction_error": None,
    }
    try:
        try:
            from pypdf import PdfReader  # type: ignore
            meta["extractor"] = "pypdf"
        except ImportError:
            from PyPDF2 import PdfReader  # type: ignore
            meta["extractor"] = "PyPDF2"
    except ImportError:
        meta["extraction_error"] = "PDF extraction unavailable: install pypdf or PyPDF2"
        return "", meta

    try:
        import io
        reader = PdfReader(io.BytesIO(content))
        meta["page_count"] = len(reader.pages)
        text_parts: List[str] = []
        for idx, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text() or ""
            except Exception as exc:
                logger.debug("paper fetch PDF page %d extraction failed: %s", idx + 1, exc)
                page_text = ""
            if page_text.strip():
                text_parts.append(f"--- Page {idx + 1} ---\n{page_text.strip()}")
            meta["pages_extracted"] = idx + 1
        return "\n\n".join(text_parts), meta
    except Exception as exc:
        meta["extraction_error"] = str(exc)
        return "", meta


def fetch_paper(url: str, max_chars: int = -1, timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    """Download a paper-like PDF as bytes and extract full text when possible."""
    pdf_url = _normalize_arxiv_pdf_url(url)
    parts, reason = _validate_url_structure(pdf_url)
    if parts is None:
        raise UnsafeUrlError(reason)
    _scheme, hostname, port = parts
    pinned_ip = _resolve_first_safe_ip(pdf_url)

    with _pinned_session(pinned_ip=pinned_ip, hostname=hostname, port=port) as session:
        response = session.get(
            pdf_url,
            headers={**REQUEST_HEADERS, "Accept": "application/pdf,*/*;q=0.8"},
            timeout=timeout_s,
            stream=True,
            allow_redirects=False,
        )
        status_code, content_type, body_bytes = _stream_capped_body_with_limit(
            response,
            MAX_PAPER_BYTES,
        )

    sha256 = hashlib.sha256(body_bytes).hexdigest()
    artifact_id = _paper_key(f"{pdf_url}:{sha256}")
    artifact_dir = PAPER_ARTIFACT_DIR / artifact_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = artifact_dir / "paper.pdf"
    text_path = artifact_dir / "paper.txt"
    meta_path = artifact_dir / "metadata.json"
    pdf_path.write_bytes(body_bytes)

    text, extraction_meta = _extract_pdf_text_bytes(body_bytes)
    original_text_length = len(text)
    truncated = False
    if max_chars >= 0 and original_text_length > max_chars:
        text = text[:max_chars]
        truncated = True
    text_path.write_text(text, encoding="utf-8")

    payload = {
        "type": "paper",
        "url": url,
        "pdf_url": pdf_url,
        "status_code": status_code,
        "content_type": content_type,
        "fetched_at": _now_ts(),
        "artifact_id": artifact_id,
        "local_pdf_path": str(pdf_path),
        "local_text_path": str(text_path),
        "metadata_path": str(meta_path),
        "sha256": sha256,
        "byte_length": len(body_bytes),
        "max_bytes": MAX_PAPER_BYTES,
        "text": text,
        "text_length": len(text),
        "original_text_length": original_text_length,
        "truncated": truncated,
        **extraction_meta,
    }
    meta_path.write_text(json.dumps({k: v for k, v in payload.items() if k != "text"}, indent=2), encoding="utf-8")
    return payload


_STATS_COUNTER_KEYS = (
    "searches_total",
    "fetches_total",
    "paper_fetches_total",
    "image_searches_total",
    "research_total",
    "search_cache_hits",
    "fetch_cache_hits",
    "paper_fetch_cache_hits",
    "image_search_cache_hits",
)


class WebProxyStats:
    """Persistent, process-safe lifetime counters for the web proxy."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._data = self._load()

    @staticmethod
    def _fresh() -> Dict[str, Any]:
        return {
            **{key: 0 for key in _STATS_COUNTER_KEYS},
            "provider_breakdown": {},
        }

    def _load(self) -> Dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("root is not an object")
            data = self._fresh()
            for key in _STATS_COUNTER_KEYS:
                value = raw.get(key, 0)
                if type(value) is not int or value < 0:
                    raise ValueError(f"invalid {key}")
                data[key] = value
            providers = raw.get("provider_breakdown", {})
            if not isinstance(providers, dict):
                raise ValueError("provider_breakdown is not an object")
            for name, value in providers.items():
                if not isinstance(name, str) or type(value) is not int or value < 0:
                    raise ValueError("invalid provider_breakdown entry")
            data["provider_breakdown"] = dict(providers)
            return data
        except FileNotFoundError:
            return self._fresh()
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
            logger.warning("stats file %s is corrupt; starting fresh: %s", self.path, exc)
            return self._fresh()

    def _flush_locked(self) -> None:
        tmp_path = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".web_proxy_stats_tmp_", suffix=".json"
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self.path)
            tmp_path = None
        except OSError as exc:
            logger.warning("failed to persist stats file %s: %s", self.path, exc)
        finally:
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def increment(self, key: str, amount: int = 1) -> None:
        if key not in _STATS_COUNTER_KEYS:
            raise KeyError(key)
        with self._lock:
            self._data[key] += amount
            self._flush_locked()

    def increment_provider(self, provider: str, amount: int = 1) -> None:
        with self._lock:
            breakdown = self._data["provider_breakdown"]
            breakdown[provider] = breakdown.get(provider, 0) + amount
            self._flush_locked()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return {
                **{key: self._data[key] for key in _STATS_COUNTER_KEYS},
                "provider_breakdown": dict(self._data["provider_breakdown"]),
            }


class FileCache:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._sweep_stop = threading.Event()
        self._sweep_thread: Optional[threading.Thread] = None

    def _path_for(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str, ttl_s: int) -> Optional[Dict[str, Any]]:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("cache read failed for %s: %s", path.name, exc)
            return None
        if not isinstance(data, dict):
            logger.warning(
                "cache entry %s root is %s, expected object",
                path.name,
                type(data).__name__,
            )
            return None
        try:
            fetched_at = int(data.get("_cached_at", 0))
        except (TypeError, ValueError, OverflowError):
            logger.warning("cache entry %s has invalid _cached_at=%r", path.name, data.get("_cached_at"))
            return None
        now = _now_ts()
        if fetched_at <= 0:
            logger.warning("cache entry %s has non-positive _cached_at=%r", path.name, fetched_at)
            return None
        if fetched_at > now:
            logger.warning("cache entry %s has future _cached_at=%r", path.name, fetched_at)
            return None
        if (now - fetched_at) >= ttl_s:
            return None
        data["_cache_hit"] = True
        data["_cache_key"] = key
        data["_cache_path"] = str(path)
        return data

    def put(self, key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        path = self._path_for(key)
        to_write = dict(payload)
        to_write["_cached_at"] = _now_ts()
        to_write["_cache_key"] = key
        to_write["_cache_path"] = str(path)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(self.root), prefix=".cache_tmp_", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(to_write, f, ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError as exc:
                logger.warning("failed to remove cache temp file %s: %s", tmp_path, exc)
            raise
        return to_write

    def _iter_files(self) -> List[Path]:
        files: List[Path] = []

        def _walk_error(exc: OSError) -> None:
            logger.warning("cache sweep scan failed: %s", exc)

        for dirpath, _dirnames, filenames in os.walk(self.root, onerror=_walk_error):
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    mode = path.lstat().st_mode
                except OSError as exc:
                    logger.warning("cache sweep stat failed for %s: %s", path, exc)
                    continue
                if stat_module.S_ISREG(mode):
                    files.append(path)
        return files

    @staticmethod
    def _ttl_for_name(name: str) -> Optional[int]:
        if name.startswith("paper_fetch_"):
            return PAPER_TTL_S
        if name.startswith("search_") or name.startswith("image_search_"):
            return SEARCH_TTL_S
        if name.startswith("fetch_"):
            return FETCH_TTL_S
        return None

    @staticmethod
    def _unlink(path: Path) -> bool:
        try:
            path.unlink()
        except FileNotFoundError:
            logger.debug("cache sweep skipped vanished file %s", path)
            return False
        except PermissionError as exc:
            logger.warning("cache sweep could not delete %s: %s", path, exc)
            return False
        except OSError as exc:
            logger.warning("cache sweep could not delete %s: %s", path, exc)
            return False
        return True

    def sweep(self) -> Dict[str, int]:
        """Evict stale JSON entries, then oldest files until under the byte cap."""
        now = time.time()
        deleted_files = 0
        bytes_freed = 0
        files = self._iter_files()

        for path in files:
            if path.parent != self.root or path.suffix != ".json":
                continue
            ttl_s = self._ttl_for_name(path.name)
            if ttl_s is None:
                continue
            try:
                stat_result = path.lstat()
            except OSError as exc:
                logger.warning("cache sweep stat failed for %s: %s", path, exc)
                continue
            age_s = now - stat_result.st_mtime
            if age_s > ttl_s * CACHE_STALE_GRACE:
                if self._unlink(path):
                    deleted_files += 1
                    bytes_freed += stat_result.st_size

        file_info: List[Tuple[float, int, Path]] = []
        bytes_remaining = 0
        for path in self._iter_files():
            try:
                stat_result = path.lstat()
            except OSError as exc:
                logger.warning("cache sweep stat failed for %s: %s", path, exc)
                continue
            if not stat_module.S_ISREG(stat_result.st_mode):
                continue
            info = (stat_result.st_mtime, stat_result.st_size, path)
            file_info.append(info)
            bytes_remaining += stat_result.st_size

        if bytes_remaining > CACHE_MAX_BYTES:
            for mtime, size_bytes, path in sorted(file_info, key=lambda item: item[0]):
                if bytes_remaining <= CACHE_MAX_BYTES:
                    break
                if self._unlink(path):
                    deleted_files += 1
                    bytes_freed += size_bytes
                    bytes_remaining -= size_bytes

        logger.info(
            "cache sweep: files deleted=%d, bytes freed=%d, bytes remaining=%d",
            deleted_files,
            bytes_freed,
            bytes_remaining,
        )
        return {
            "files_deleted": deleted_files,
            "bytes_freed": bytes_freed,
            "bytes_remaining": bytes_remaining,
        }

    def start_sweeper(self) -> None:
        if CACHE_SWEEP_INTERVAL_S <= 0:
            return
        self.sweep()

        def _run() -> None:
            while not self._sweep_stop.wait(CACHE_SWEEP_INTERVAL_S):
                self.sweep()

        self._sweep_thread = threading.Thread(
            target=_run,
            name="atlasforge-web-proxy-cache-sweeper",
            daemon=True,
        )
        self._sweep_thread.start()


class SearchProvider:
    def __init__(
        self,
        session: Optional[requests.Session] = None,
        stats: Optional[WebProxyStats] = None,
    ):
        self.session = session or requests.Session()
        self.stats = stats

    def _record_provider_call(self, provider: str) -> None:
        if self.stats is not None:
            self.stats.increment_provider(provider)

    def _brave_api_key(self) -> Optional[str]:
        return _first_env(BRAVE_API_KEY_ENV_NAMES)

    @staticmethod
    def _brave_fallback_reason(exc: BaseException) -> str:
        if not isinstance(exc, requests.HTTPError):
            return "brave_error"
        response = getattr(exc, "response", None)
        status_code = getattr(response, "status_code", None)
        if status_code in {402, 429}:
            return "brave_quota_or_rate_limit"
        body = getattr(response, "text", "") or ""
        if re.search(r"\b(credit|quota|rate.?limit|subscription|exhaust)", body, re.I):
            return "brave_quota_or_rate_limit"
        return "brave_error"

    @staticmethod
    def _with_fallback_metadata(
        result: Dict[str, Any],
        *,
        from_provider: str,
        reason: str,
    ) -> Dict[str, Any]:
        out = dict(result)
        out["fallback_from"] = from_provider
        out["fallback_reason"] = reason
        return out

    @staticmethod
    def _results_are_empty(result: Dict[str, Any]) -> bool:
        results = result.get("results")
        return isinstance(results, list) and len(results) == 0

    @staticmethod
    def _normalize_count(count: Any, default: int = 5, max_value: int = 20) -> int:
        """Coerce count to a clamped int. None/garbage -> default; float -> int().

        Direct callers of search_* methods (MCP server, ad-hoc scripts) bypass
        the endpoint-level _coerce_count, so methods must self-defend.
        """
        if count is None:
            count = default
        try:
            count = int(count)
        except (TypeError, ValueError, OverflowError):
            count = default
        return max(1, min(count, max_value))

    def search(self, query: str, count: Any = 5, provider: str = "auto") -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=20)
        provider = (provider or "auto").strip().lower()
        if provider == "auto":
            if self._brave_api_key():
                try:
                    return self.search_brave(query, count=count)
                except Exception as exc:
                    fallback_reason = self._brave_fallback_reason(exc)
                    logger.warning(
                        "Brave search failed; falling back to DuckDuckGo: %s",
                        exc,
                    )
                    return self._search_duckduckgo_or_ddgs_fallback(
                        query=query,
                        count=count,
                        from_provider="brave",
                        reason=fallback_reason,
                    )
            provider = "duckduckgo"
            return self._search_duckduckgo_or_ddgs_fallback(
                query=query,
                count=count,
                from_provider="duckduckgo",
                reason="duckduckgo_unavailable_or_empty",
            )

        if provider == "brave":
            return self.search_brave(query, count=count)
        if provider in {"duckduckgo", "ddg"}:
            return self.search_duckduckgo(query, count=count)
        if provider in {"ddgs", "fallback"}:
            return self.search_ddgs_fallback(query, count=count)
        raise ValueError(f"Unsupported search provider: {provider}")

    def _search_duckduckgo_or_ddgs_fallback(
        self,
        *,
        query: str,
        count: int,
        from_provider: str,
        reason: str,
    ) -> Dict[str, Any]:
        try:
            result = self.search_duckduckgo(query, count=count)
        except Exception as exc:
            logger.warning("DuckDuckGo search failed; falling back to DDGS: %s", exc)
        else:
            if not self._results_are_empty(result):
                if from_provider == "duckduckgo":
                    return result
                return self._with_fallback_metadata(
                    result,
                    from_provider=from_provider,
                    reason=reason,
                )
            logger.warning("DuckDuckGo returned no results; falling back to DDGS")

        return self._with_fallback_metadata(
            self.search_ddgs_fallback(query, count=count),
            from_provider=from_provider,
            reason=reason,
        )

    def search_brave(self, query: str, count: Any = 5) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=20)
        api_key = self._brave_api_key()
        if not api_key:
            raise RuntimeError("Brave search selected but no API key is configured")

        self._record_provider_call("brave")
        response = self.session.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": count},
            headers={**REQUEST_HEADERS, "X-Subscription-Token": api_key},
            timeout=DEFAULT_TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()

        results: List[Dict[str, Any]] = []
        for item in payload.get("web", {}).get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "snippet": item.get("description", ""),
                    "display_url": item.get("meta_url", {}).get("display"),
                    "source": "brave",
                }
            )

        return {
            "provider": "brave",
            "query": query,
            "results": results,
            "count": len(results),
        }

    def search_images(
        self,
        query: str,
        count: Any = 5,
        provider: str = "auto",
        safesearch: str = "off",
    ) -> Dict[str, Any]:
        provider = (provider or "auto").strip().lower()
        if provider == "auto":
            if self._brave_api_key():
                count = self._normalize_count(count, max_value=20)
                try:
                    return self.search_images_brave(query, count=count)
                except Exception as exc:
                    fallback_reason = self._brave_fallback_reason(exc)
                    logger.warning(
                        "Brave image search failed; falling back to DuckDuckGo: %s",
                        exc,
                    )
                    return self._image_duckduckgo_or_ddgs_fallback(
                        query=query,
                        count=count,
                        safesearch=safesearch,
                        from_provider="brave_images",
                        reason=fallback_reason,
                    )
            provider = "duckduckgo"
            count = self._normalize_count(count, max_value=50)
            return self._image_duckduckgo_or_ddgs_fallback(
                query=query,
                count=count,
                safesearch=safesearch,
                from_provider="duckduckgo_images",
                reason="duckduckgo_unavailable_or_empty",
            )

        if provider == "brave":
            count = self._normalize_count(count, max_value=20)
            return self.search_images_brave(query, count=count)
        if provider in {"duckduckgo", "ddg"}:
            count = self._normalize_count(count, max_value=50)
            return self.search_images_duckduckgo(
                query,
                count=count,
                safesearch=safesearch,
            )
        if provider in {"ddgs", "fallback"}:
            count = self._normalize_count(count, max_value=50)
            return self.search_images_ddgs_fallback(
                query,
                count=count,
                safesearch=safesearch,
            )
        return {
            "provider": provider,
            "query": query,
            "results": [],
            "count": 0,
            "error": f"Unsupported image search provider: {provider}",
        }

    def _image_duckduckgo_or_ddgs_fallback(
        self,
        *,
        query: str,
        count: int,
        safesearch: str,
        from_provider: str,
        reason: str,
    ) -> Dict[str, Any]:
        try:
            result = self.search_images_duckduckgo(
                query,
                count=count,
                safesearch=safesearch,
            )
        except Exception as exc:
            logger.warning(
                "DuckDuckGo image search failed; falling back to DDGS: %s", exc
            )
        else:
            if not self._results_are_empty(result):
                if from_provider == "duckduckgo_images":
                    return result
                return self._with_fallback_metadata(
                    result,
                    from_provider=from_provider,
                    reason=reason,
                )
            logger.warning(
                "DuckDuckGo image search returned no results; falling back to DDGS"
            )

        return self._with_fallback_metadata(
            self.search_images_ddgs_fallback(
                query,
                count=count,
                safesearch=safesearch,
            ),
            from_provider=from_provider,
            reason=reason,
        )

    def search_images_duckduckgo(
        self,
        query: str,
        count: Any = 5,
        safesearch: str = "off",
    ) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=50)
        try:
            from ddgs import DDGS
        except ImportError:
            return {
                "provider": "duckduckgo",
                "query": query,
                "results": [],
                "count": 0,
                "error": "ddgs package not installed (pip install ddgs)",
            }

        self._record_provider_call("duckduckgo_images")
        raw = list(
            DDGS().images(
                query,
                max_results=count,
                safesearch=safesearch,
            )
        )
        results: List[Dict[str, Any]] = []
        for item in raw:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("image", ""),
                    "source_url": item.get("url", ""),
                    "thumbnail": item.get("thumbnail", ""),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "source": "duckduckgo_images",
                }
            )
        return {
            "provider": "duckduckgo_images",
            "query": query,
            "results": results,
            "count": len(results),
        }

    def search_images_brave(self, query: str, count: Any = 5) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=20)
        api_key = self._brave_api_key()
        if not api_key:
            raise RuntimeError("Brave image search requires an API key")

        self._record_provider_call("brave_images")
        response = self.session.get(
            "https://api.search.brave.com/res/v1/images/search",
            params={"q": query, "count": count},
            headers={**REQUEST_HEADERS, "X-Subscription-Token": api_key},
            timeout=DEFAULT_TIMEOUT_S,
        )
        response.raise_for_status()
        payload = response.json()

        results: List[Dict[str, Any]] = []
        for item in payload.get("results", []):
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "source_url": item.get("source", ""),
                    "thumbnail": item.get("thumbnail", {}).get("src", ""),
                    "width": item.get("properties", {}).get("width"),
                    "height": item.get("properties", {}).get("height"),
                    "source": "brave_images",
                }
            )

        return {
            "provider": "brave_images",
            "query": query,
            "results": results,
            "count": len(results),
        }

    def search_images_ddgs_fallback(
        self,
        query: str,
        count: Any = 5,
        safesearch: str = "off",
    ) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=50)
        if not DDGS_IMAGE_FALLBACK_BACKENDS:
            raise RuntimeError(
                "DDGS image fallback selected but no backends are configured"
            )
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError("ddgs package not installed (pip install ddgs)") from exc

        self._record_provider_call("ddgs_images")
        backend = ",".join(DDGS_IMAGE_FALLBACK_BACKENDS)
        raw = DDGS(timeout=DEFAULT_TIMEOUT_S).images(
            query,
            max_results=count,
            backend=backend,
            safesearch=safesearch,
        )
        results: List[Dict[str, Any]] = []
        for item in raw:
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("image", ""),
                    "source_url": item.get("url", ""),
                    "thumbnail": item.get("thumbnail", ""),
                    "width": item.get("width"),
                    "height": item.get("height"),
                    "source": item.get("source") or "ddgs_images",
                }
            )

        return {
            "provider": "ddgs_images",
            "query": query,
            "results": results,
            "count": len(results),
            "backends": list(DDGS_IMAGE_FALLBACK_BACKENDS),
        }

    def search_ddgs_fallback(self, query: str, count: Any = 5) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=20)
        if not DDGS_FALLBACK_BACKENDS:
            raise RuntimeError("DDGS fallback selected but no backends are configured")
        try:
            from ddgs import DDGS
        except ImportError as exc:
            raise RuntimeError("ddgs package not installed (pip install ddgs)") from exc

        self._record_provider_call("ddgs")
        backend = ",".join(DDGS_FALLBACK_BACKENDS)
        raw = DDGS(timeout=DEFAULT_TIMEOUT_S).text(
            query,
            max_results=count,
            backend=backend,
        )
        results: List[Dict[str, Any]] = []
        for item in raw:
            url = item.get("href") or item.get("url") or ""
            results.append(
                {
                    "title": item.get("title", ""),
                    "url": url,
                    "snippet": item.get("body") or item.get("snippet", ""),
                    "source": item.get("source") or "ddgs",
                }
            )

        return {
            "provider": "ddgs",
            "query": query,
            "results": results,
            "count": len(results),
            "backends": list(DDGS_FALLBACK_BACKENDS),
        }

    def search_duckduckgo(self, query: str, count: Any = 5) -> Dict[str, Any]:
        count = self._normalize_count(count, max_value=20)
        self._record_provider_call("duckduckgo")
        response = self.session.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers=REQUEST_HEADERS,
            timeout=DEFAULT_TIMEOUT_S,
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "lxml")

        results: List[Dict[str, Any]] = []
        for block in soup.select(".result")[:count]:
            link = block.select_one("a.result__a")
            snippet = block.select_one(".result__snippet")
            if not link:
                continue
            href = link.get("href", "").strip()
            title = _normalize_whitespace(link.get_text(" ", strip=True))
            snippet_text = _normalize_whitespace(
                snippet.get_text(" ", strip=True) if snippet else ""
            )
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippet_text,
                    "source": "duckduckgo",
                }
            )

        return {
            "provider": "duckduckgo",
            "query": query,
            "results": results,
            "count": len(results),
        }


def extract_page_content(url: str, html: str, max_chars: int = 12000) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "lxml")

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "canvas"]):
        tag.decompose()

    title = ""
    if soup.title:
        title = _normalize_whitespace(soup.title.get_text(" ", strip=True))
        if re.search(r"</?[A-Za-z][^>]*>", title):
            title = _normalize_whitespace(re.sub(r"</?[A-Za-z][^>]*>", " ", title))

    meta_description = ""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta:
        meta_description = _html_attr_text(meta.get("content"))

    main = soup.find("main") or soup.find("article") or soup.body or soup

    headings: List[str] = []
    for tag in main.find_all(["h1", "h2", "h3"]):
        text = _normalize_whitespace(tag.get_text(" ", strip=True))
        if text:
            headings.append(text)

    text_parts: List[str] = []
    for tag in main.find_all(["h1", "h2", "h3", "p", "li", "blockquote"]):
        text = _normalize_whitespace(tag.get_text(" ", strip=True))
        if text:
            text_parts.append(text)

    if not text_parts:
        text_parts.append(_normalize_whitespace(main.get_text("\n", strip=True)))

    text = "\n\n".join(part for part in text_parts if part)
    text = _normalize_whitespace(text)
    try:
        max_chars = int(max_chars)
    except (TypeError, ValueError, OverflowError):
        max_chars = 0
    if max_chars > 0:
        text = text[:max_chars]

    links: List[Dict[str, str]] = []
    for anchor in main.find_all("a", href=True):
        href = _html_attr_text(anchor.get("href"))
        if not href:
            continue
        absolute = urljoin(url, href)
        scheme = (urlparse(absolute).scheme or "").lower()
        if scheme not in ("http", "https"):
            continue
        links.append(
            {
                "text": _normalize_whitespace(anchor.get_text(" ", strip=True))[:160],
                "url": absolute,
            }
        )

    return {
        "url": url,
        "title": title,
        "meta_description": meta_description,
        "headings": headings,
        "text": text,
        "links": links,
        "text_length": len(text),
    }


def _is_reddit_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    try:
        host_ascii = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False
    return host_ascii == "reddit.com" or host_ascii.endswith(".reddit.com")


def _reddit_json_url(url: str, host: str = "www.reddit.com") -> str:
    """Build the Reddit ``.json`` API URL. Default host is www (legacy contract).

    ``host`` is used by the 403 fallback ladder (``old.reddit.com``).
    """
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path.lower().endswith(".json"):
        json_path = path
    else:
        json_path = path.rstrip("/") + "/.json"
    pairs = parse_qsl(parsed.query, keep_blank_values=True)
    has_limit = any(k == "limit" for k, _ in pairs)
    has_depth = any(k == "depth" for k, _ in pairs)
    if not has_limit:
        pairs.append(("limit", "500"))
    if not has_depth:
        pairs.append(("depth", "10"))
    query = urlencode(pairs, doseq=True)
    return f"https://{host}{json_path}?{query}" if query else f"https://{host}{json_path}"


def _reddit_primary_user_agent() -> str:
    return _header_env(
        "ATLASFORGE_REDDIT_USER_AGENT",
        BROWSER_USER_AGENT,
    )


def _reddit_fetch_candidates(url: str) -> List[Tuple[str, str]]:
    """Ordered (json_url, user_agent) ladder for Reddit fetches.

    1. www.reddit.com + primary UA (historical default)
    2. old.reddit.com + primary UA (on www 403)
    3. old.reddit.com + alternate browser UA
    """
    primary_ua = _reddit_primary_user_agent()
    candidates: List[Tuple[str, str]] = [(_reddit_json_url(url, "www.reddit.com"), primary_ua)]
    if REDDIT_OLD_FALLBACK:
        old_url = _reddit_json_url(url, "old.reddit.com")
        candidates.append((old_url, primary_ua))
        if REDDIT_ALT_USER_AGENT and REDDIT_ALT_USER_AGENT != primary_ua:
            candidates.append((old_url, REDDIT_ALT_USER_AGENT))
    # Dedupe while preserving order.
    seen: set = set()
    out: List[Tuple[str, str]] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _reddit_get_json(
    json_url: str,
    headers: Dict[str, str],
    timeout_s: float,
) -> Tuple[int, str, bytes]:
    """One Reddit GET with transient retry. Raises HTTPError on non-2xx after retries."""
    parts, reason = _validate_url_structure(json_url)
    if parts is None:
        raise UnsafeUrlError(reason)
    _scheme, hostname, port = parts
    pinned_ip = _resolve_first_safe_ip(json_url)
    with _pinned_session(pinned_ip=pinned_ip, hostname=hostname, port=port) as session:
        response = _session_get_with_retry(
            session,
            json_url,
            headers=headers,
            timeout_s=timeout_s,
            stream=True,
            allow_redirects=False,
        )
        return _stream_capped_body(response)


def fetch_reddit(url: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    timeout_s = _validate_timeout_s(timeout_s)
    # Structural-only validation of the caller's URL. The URL we actually
    # fetch is `json_url` (on www.reddit.com / old.reddit.com); that's the one
    # we DNS-resolve and pin. Resolving the original URL's DNS here would be a
    # redundant lookup (plus a small oracle: different DNS failures for the
    # two URLs).
    parts_in, reason_in = _validate_url_structure(url)
    if parts_in is None:
        raise UnsafeUrlError(reason_in)

    status_code = 0
    content_type = ""
    body_bytes = b""
    json_url = _reddit_json_url(url)
    last_http_error: Optional[requests.HTTPError] = None

    for candidate_url, user_agent in _reddit_fetch_candidates(url):
        reddit_headers = {
            "User-Agent": user_agent,
            "Accept": "*/*",
        }
        try:
            status_code, content_type, body_bytes = _reddit_get_json(
                candidate_url, reddit_headers, timeout_s
            )
            json_url = candidate_url
            last_http_error = None
            break
        except requests.HTTPError as exc:
            resp = getattr(exc, "response", None)
            status = getattr(resp, "status_code", None) if resp is not None else None
            # Only the 403 ladder continues; 401/404/302/etc. fail immediately
            # (preserves historical contract e.g. no-redirect tests).
            if status == 403:
                logger.info(
                    "reddit 403 on %s (ua=%s...); trying next fallback",
                    candidate_url,
                    user_agent[:40],
                )
                last_http_error = exc
                json_url = candidate_url
                continue
            raise

    if last_http_error is not None:
        raise last_http_error

    # Headers used for optional morechildren expansion (best-effort).
    reddit_headers = {
        "User-Agent": _reddit_primary_user_agent(),
        "Accept": "*/*",
    }

    # Content-type gate: reject 200 + text/html (rate-limit interstitial or
    # login wall) BEFORE reaching json.loads. Previously this surfaced as a
    # JSONDecodeError bubbling out as a generic 502; now we return the normal
    # reddit payload shape with empty posts/comments so callers can diagnose
    # via status_code + content_type instead of "fetch failed".
    ct_main = content_type.split(";")[0].strip().lower()
    is_json_ct = (
        ct_main in ("application/json", "text/json") or ct_main.endswith("+json")
    )
    data: Any
    if 200 <= status_code < 300 and body_bytes and is_json_ct:
        try:
            data = json.loads(body_bytes.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            # Servers can lie about content-type; degrade gracefully.
            logger.warning("reddit JSON decode failed for %s: %s", json_url, exc)
            data = []
    else:
        data = []

    posts: List[Dict[str, Any]] = []
    comments: List[Dict[str, Any]] = []

    def _as_int(x: Any, default: int = 0) -> int:
        """Coerce to int with a default, rejecting bools (which are int
        subclasses in Python). Reddit JSON schema drift has historically
        surfaced stringified scores and float timestamps; `or 0` lets bools
        and wrong-type values through, then the f-string renderers crash."""
        if isinstance(x, bool):
            return default
        try:
            return int(x)
        except (TypeError, ValueError, OverflowError):
            return default

    def _as_str(x: Any) -> str:
        """Coerce to str, defaulting to "". None/False/0 collapse to ""
        via the `or` short-circuit; anything else is str()'d."""
        return str(x or "")

    def _reddit_permalink(x: Any) -> str:
        value = _as_str(x)
        if (
            not value.startswith("/")
            or value.startswith("//")
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        ):
            return "https://www.reddit.com"
        return "https://www.reddit.com" + value

    more_ids: List[str] = []  # accumulates "more" node child IDs for morechildren fetch

    def _collect_listing(listing: Any, target: List[Dict[str, Any]], kind: str, depth: int = 0) -> None:
        if not isinstance(listing, dict):
            return
        # B3: the inner "data" layer can be None (short-circuit handled by `or {}`)
        # or a truthy non-dict (e.g. "foo", [1,2,3], 42) in tampered/trimmed JSON.
        # `.get("children")` on the latter raises AttributeError, so gate on type.
        data_layer = listing.get("data")
        if not isinstance(data_layer, dict):
            return
        children = data_layer.get("children")
        if not isinstance(children, list):
            return
        for child in children:
            if not isinstance(child, dict):
                continue
            cd = child.get("data") or {}
            if not isinstance(cd, dict):
                continue
            if kind == "post" and child.get("kind") == "t3":
                target.append(
                    {
                        "title": _as_str(cd.get("title")),
                        "author": _as_str(cd.get("author")),
                        "score": _as_int(cd.get("score")),
                        "num_comments": _as_int(cd.get("num_comments")),
                        "flair": _as_str(cd.get("link_flair_text")),
                        "subreddit": _as_str(cd.get("subreddit")),
                        "created_utc": _as_int(cd.get("created_utc")),
                        "url": _as_str(cd.get("url")),
                        "permalink": _reddit_permalink(cd.get("permalink")),
                        "selftext": _as_str(cd.get("selftext"))[:2000],
                        "is_self": bool(cd.get("is_self", False)),
                        "over_18": bool(cd.get("over_18", False)),
                        "stickied": bool(cd.get("stickied", False)),
                    }
                )
            elif kind == "comment" and child.get("kind") == "t1":
                target.append(
                    {
                        "author": _as_str(cd.get("author")),
                        "score": _as_int(cd.get("score")),
                        "body": _as_str(cd.get("body"))[:2000],
                        "created_utc": _as_int(cd.get("created_utc")),
                        "permalink": _reddit_permalink(cd.get("permalink")),
                        "depth": depth,
                    }
                )
                # Recurse into replies
                replies = cd.get("replies")
                if isinstance(replies, dict):
                    _collect_listing(replies, target, kind, depth + 1)
            elif kind == "comment" and child.get("kind") == "more":
                # Collect IDs for morechildren expansion (skip empty sentinel nodes)
                ids = cd.get("children") or []
                if isinstance(ids, list):
                    more_ids.extend(ids)

    if isinstance(data, list):
        if len(data) >= 1:
            _collect_listing(data[0], posts, "post")
        if len(data) >= 2:
            _collect_listing(data[1], comments, "comment")
    else:
        _collect_listing(data, posts, "post")

    # Expand "more" nodes via morechildren API (up to 100 IDs per call)
    if more_ids and posts:
        link_id = f"t3_{posts[0].get('id', '')}" if posts and posts[0].get("id") else None
        # Derive link_id from json_url if not on post object
        if not link_id or link_id == "t3_":
            import re as _re
            m = _re.search(r"/comments/([a-z0-9]+)", json_url)
            link_id = f"t3_{m.group(1)}" if m else None
        if link_id:
            # Batch into chunks of 100
            def _chunks(lst: List[str], n: int):
                for i in range(0, len(lst), n):
                    yield lst[i:i + n]
            mc_headers = reddit_headers.copy()
            mc_pinned_ip = _resolve_first_safe_ip("https://www.reddit.com/api/morechildren")
            _, mc_hostname, mc_port = _validate_url_structure("https://www.reddit.com/api/morechildren")[0]
            for chunk in _chunks(more_ids[:200], 100):  # cap at 200 IDs total
                mc_url = (
                    f"https://www.reddit.com/api/morechildren.json"
                    f"?link_id={link_id}&children={','.join(chunk)}&api_type=json"
                )
                try:
                    with _pinned_session(pinned_ip=mc_pinned_ip, hostname=mc_hostname, port=mc_port) as mc_session:
                        mc_resp = mc_session.get(mc_url, headers=mc_headers, timeout=timeout_s, stream=True, allow_redirects=False)
                        _, mc_ct, mc_body = _stream_capped_body(mc_resp)
                    mc_ct_main = mc_ct.split(";")[0].strip().lower()
                    if mc_body and (mc_ct_main in ("application/json", "text/json") or mc_ct_main.endswith("+json")):
                        mc_data = json.loads(mc_body.decode("utf-8", errors="replace"))
                        mc_things = mc_data.get("json", {}).get("data", {}).get("things", [])
                        for thing in mc_things:
                            if not isinstance(thing, dict):
                                continue
                            td = thing.get("data") or {}
                            if thing.get("kind") == "t1" and isinstance(td, dict):
                                comments.append({
                                    "author": _as_str(td.get("author")),
                                    "score": _as_int(td.get("score")),
                                    "body": _as_str(td.get("body"))[:2000],
                                    "created_utc": _as_int(td.get("created_utc")),
                                    "permalink": _reddit_permalink(td.get("permalink")),
                                    "depth": _as_int(td.get("depth")),
                                })
                except Exception:
                    pass  # morechildren expansion is best-effort

    text_lines: List[str] = []
    for p in posts:
        # Defense-in-depth: _as_int already guarantees int, but a future edit
        # could regress. Wrap the render sites so :5d/:4d cannot trigger a
        # TypeError even if post dicts ever get handed a non-int field.
        text_lines.append(
            f"[{int(p['score']):5d} | {int(p['num_comments']):4d}c] {p['title']}"
        )
        text_lines.append(
            f"        by u/{p['author']} in r/{p['subreddit']} - {p['flair'] or 'no flair'}"
        )
        if p["selftext"]:
            text_lines.append(f"        {p['selftext'][:500]}")
        text_lines.append("")
    for c in comments:
        text_lines.append(f"[{int(c['score']):5d}] u/{c['author']}: {c['body'][:500]}")
        text_lines.append("")

    text = "\n".join(text_lines).strip()

    return FetchResponse(
        url=url,
        status_code=status_code,
        content_type=content_type,
        fetched_at=_now_ts(),
        title=f"Reddit: {urlparse(url).path}",
        meta_description="",
        headings=[],
        text=text,
        links=[{"text": p["title"], "url": p["permalink"]} for p in posts],
        text_length=len(text),
        resolved_url=json_url,
        reddit={
            "posts": posts,
            "comments": comments,
            "post_count": len(posts),
            "comment_count": len(comments),
        },
    ).to_dict()


IMAGE_CACHE_DIR = CACHE_DIR / "images"
IMAGE_EXT_BY_CT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _image_magic_matches(content_type: str, data: bytes) -> bool:
    if content_type in {"image/jpeg", "image/jpg"}:
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if content_type == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if content_type == "image/bmp":
        return data.startswith(b"BM")
    if content_type == "image/tiff":
        return data.startswith((b"II*\x00", b"MM\x00*"))
    return False


def _save_image(url: str, content_type: str, data: bytes) -> Dict[str, Any]:
    IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = IMAGE_EXT_BY_CT.get(ct)
    if ext is None:
        raise ValueError(f"unsupported image content type: {ct or 'missing'}")
    if not _image_magic_matches(ct, data):
        raise ValueError(f"image bytes do not match declared content type: {ct}")
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
    local_path = IMAGE_CACHE_DIR / f"{digest}{ext}"
    fd = os.open(str(local_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(local_path, 0o600)

    width: Optional[int] = None
    height: Optional[int] = None
    try:
        from PIL import Image, UnidentifiedImageError  # type: ignore
    except ImportError:
        pass
    else:
        image_decode_errors = (UnidentifiedImageError, OSError, ValueError)
        decompression_error = getattr(Image, "DecompressionBombError", None)
        if decompression_error is not None:
            image_decode_errors = image_decode_errors + (decompression_error,)
        try:
            with Image.open(local_path) as img:
                width, height = img.size
        except image_decode_errors as exc:
            logger.warning(
                "image dimension decode failed path=%s content_type=%s error=%s",
                local_path,
                ct,
                exc,
            )

    return {
        "type": "image",
        "url": url,
        "local_path": str(local_path),
        "content_type": ct,
        "byte_length": len(data),
        "width": width,
        "height": height,
    }


def fetch_page(url: str, max_chars: int = 12000, timeout_s: int = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
    timeout_s = _validate_timeout_s(timeout_s)
    # Structural validation first — cheap, no DNS. Then delegate reddit URLs
    # BEFORE resolving, so the reddit path DNS-resolves exactly once (on
    # its json_url). Previously this function resolved the caller URL, then
    # handed off to fetch_reddit which resolved json_url — two DNS lookups
    # per reddit fetch, re-introducing the double-resolve B5 tried to kill.
    parts, reason = _validate_url_structure(url)
    if parts is None:
        raise UnsafeUrlError(reason)

    if _is_reddit_url(url):
        return fetch_reddit(url, timeout_s=timeout_s)

    _scheme, hostname, port = parts
    # Single DNS lookup via the SSRF guard for non-reddit URLs. The IP
    # returned here is the ONLY IP this fetch will ever talk to — urllib3
    # never gets to resolve the hostname on its own.
    pinned_ip = _resolve_first_safe_ip(url)

    # allow_redirects=False keeps us from chasing a 302 to an internal host.
    # If redirects are ever needed, re-validate the target via
    # _resolve_first_safe_ip before following.
    request_headers = dict(REQUEST_HEADERS)
    with _pinned_session(pinned_ip=pinned_ip, hostname=hostname, port=port) as session:
        response = _session_get_with_retry(
            session,
            url,
            headers=request_headers,
            timeout_s=timeout_s,
            stream=True,
            allow_redirects=False,
        )
        # Capture response.encoding BEFORE _stream_capped_body closes the
        # response. `encoding` is attribute-level (parsed from Content-Type),
        # not stream-level, but reading it only from the live object keeps
        # us future-proof against requests internals.
        response_encoding = response.encoding or "utf-8"

        # Generic 403 UA-retry: many Cloudflare/WAF frontends 403 only the
        # honest research-proxy UA. One additional attempt with a browser-like
        # UA recovers those pages without a full JS render. Permanent; not
        # part of the transient retry loop.
        if (
            UA_RETRY_ON_403
            and int(getattr(response, "status_code", 0) or 0) == 403
            and request_headers.get("User-Agent") != BROWSER_USER_AGENT
        ):
            try:
                response.close()
            except Exception:
                pass
            browser_headers = {
                **request_headers,
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": request_headers.get(
                    "Accept",
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                ),
            }
            logger.info("fetch 403 UA-retry once url=%s", url)
            response = _session_get_with_retry(
                session,
                url,
                headers=browser_headers,
                timeout_s=timeout_s,
                stream=True,
                allow_redirects=False,
            )
            response_encoding = response.encoding or "utf-8"

        status_code, content_type, body_bytes = _stream_capped_body(response)
        ct_main = content_type.split(";")[0].strip().lower()

        if ct_main.startswith("image/"):
            image_payload = _save_image(url=url, content_type=content_type, data=body_bytes)
            return FetchResponse(
                url=url,
                status_code=status_code,
                content_type=image_payload["content_type"],
                fetched_at=_now_ts(),
                type=image_payload["type"],
                local_path=image_payload["local_path"],
                byte_length=image_payload["byte_length"],
                width=image_payload["width"],
                height=image_payload["height"],
            ).to_dict()

        try:
            html = body_bytes.decode(response_encoding)
        except UnicodeDecodeError as exc:
            logger.warning(
                "html decode replacement used url=%r encoding=%r error=%s",
                url,
                response_encoding,
                exc,
            )
            html = body_bytes.decode(response_encoding, errors="replace")
        except LookupError:
            try:
                html = body_bytes.decode("utf-8")
            except UnicodeDecodeError as exc:
                logger.warning(
                    "html decode replacement used url=%r encoding=%r error=%s",
                    url,
                    "utf-8",
                    exc,
                )
                html = body_bytes.decode("utf-8", errors="replace")
        extracted = extract_page_content(url=url, html=html, max_chars=max_chars)

        # JS-rendering fallback. Plain requests can't execute JavaScript, so
        # SPAs (ChatGPT shares, Claude shares, Twitter/X, etc.) and
        # Cloudflare-challenged endpoints come back with near-empty bodies.
        # Two triggers: either the host is on a known-needs-render list, OR
        # the extracted text looks suspiciously thin.
        used_js_render = False
        _jsr = None
        try:
            # Package-context import (python -m WebProxy.service or imported)
            from . import js_render as _jsr  # type: ignore
        except ImportError:
            try:
                # Script-context import (systemd invokes service.py directly,
                # so __name__ == "__main__" and there is no parent package).
                import js_render as _jsr  # type: ignore
            except ImportError:
                _jsr = None

        needs_render = False
        if _js_render_enabled() and _jsr is not None:
            if _jsr.host_wants_render(url):
                needs_render = True
            elif extracted["text_length"] < 200 or status_code == 403:
                needs_render = _jsr.should_render(html, status_code, content_type)

        if needs_render and _jsr is not None:
            if True:  # kept as nested to minimize diff to existing body
                try:
                    rendered = _jsr.render(url)
                except Exception as e:
                    logger.warning("js_render: rendering %s failed: %s", url, e)
                    rendered = None
                if rendered:
                    rendered_html = rendered.get("rendered_html") or html
                    extracted = extract_page_content(
                        url=url, html=rendered_html, max_chars=max_chars
                    )
                    rtext = rendered.get("rendered_text") or ""
                    if rtext and len(rtext) > extracted["text_length"]:
                        if max_chars > 0:
                            rtext = rtext[:max_chars]
                        extracted["text"] = rtext
                        extracted["text_length"] = len(rtext)
                    if not extracted.get("title"):
                        extracted["title"] = rendered.get("rendered_title", "")
                    used_js_render = True

        return FetchResponse(
            url=url,
            status_code=status_code,
            content_type=content_type,
            fetched_at=_now_ts(),
            title=extracted["title"],
            meta_description=extracted["meta_description"],
            headings=extracted["headings"],
            text=extracted["text"],
            links=extracted["links"],
            text_length=extracted["text_length"],
            rendered=used_js_render or None,
        ).to_dict()


def _new_correlation_id() -> str:
    return uuid.uuid4().hex[:12]


def _error_body(message: str, correlation_id: str, **extra: Any) -> Dict[str, Any]:
    body: Dict[str, Any] = {"error": message, "correlation_id": correlation_id}
    body.update(extra)
    return body


def _item_error(message: str, correlation_id: str, **extra: Any) -> Dict[str, Any]:
    """Per-item error for /research, /image_search fetched[] entries.
    Includes correlation_id so operators can cross-reference logs even for
    partial per-item failures."""
    return _error_body(message, correlation_id, **extra)


def _require_json_body() -> Tuple[Optional[Dict[str, Any]], Optional[Tuple[Any, int]]]:
    """Return (body, None) on success or (None, (response, status)) on failure.

    `force=True` is deliberately NOT set: callers must send a proper JSON
    content-type. Previous behavior (`get_json(force=True, silent=True) or {}`)
    silently swallowed malformed bodies, masking client bugs."""
    body = request.get_json(silent=True)
    if body is None:
        return None, (
            jsonify({"error": "request body must be valid JSON"}),
            400,
        )
    if not isinstance(body, dict):
        return None, (
            jsonify({"error": "request body must be a JSON object"}),
            400,
        )
    return body, None


def _string_payload_field(
    payload: Dict[str, Any], field: str, default: Optional[str] = ""
) -> Tuple[Optional[str], Optional[str]]:
    """Return stripped string field value, or an error for non-string input."""
    if field not in payload:
        return default, None
    value = payload.get(field)
    if not isinstance(value, str):
        return None, f"{field} must be a string"
    return value.strip(), None


def _coerce_count(
    raw: Any, default: int = 5, min_value: int = 1
) -> Optional[int]:
    """Coerce to int >= min_value. Returns default when raw is None, None
    on coercion failure or if coerced value is below min_value."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if value < min_value:
        return None
    return value


def _iter_dir_limited(root: Path, *, label: str, limit: Optional[int] = None) -> List[Path]:
    """Return up to limit directory entries, logging races and truncation."""
    if limit is None:
        limit = MAX_OBSERVABILITY_ENTRIES
    if not root.exists():
        return []
    entries: List[Path] = []
    try:
        for idx, path in enumerate(root.iterdir()):
            if idx >= limit:
                logger.warning("%s scan capped at %d entries", label, limit)
                break
            entries.append(path)
    except OSError as exc:
        logger.warning("%s scan failed: %s", label, exc)
    return entries


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_REQUEST_BYTES
    cache = FileCache(CACHE_DIR)
    stats = WebProxyStats(STATS_PATH)
    app.extensions["web_proxy_stats"] = stats
    cache.start_sweeper()
    provider = SearchProvider(stats=stats)

    @app.errorhandler(RequestEntityTooLarge)
    def request_entity_too_large(_exc):  # type: ignore[no-untyped-def]
        return jsonify({"error": "request body too large"}), 413

    @app.get("/health")
    def health() -> Any:
        return jsonify(
            {
                "ok": True,
                "service": "atlasforge-web-proxy",
                "default_provider": DEFAULT_PROVIDER,
                "cache_dir": str(CACHE_DIR),
            }
        )

    @app.post("/search")
    def search_endpoint() -> Any:
        payload, err = _require_json_body()
        if err is not None:
            return err
        query, field_error = _string_payload_field(payload, "query", "")
        if field_error:
            return jsonify({"error": field_error}), 400
        count = _coerce_count(payload.get("count", 5))
        if count is None:
            return jsonify({"error": "count must be a positive integer"}), 400
        provider_name, field_error = _string_payload_field(
            payload, "provider", DEFAULT_PROVIDER
        )
        if field_error:
            return jsonify({"error": field_error}), 400
        provider_name = provider_name.lower()
        if not query:
            return jsonify({"error": "query is required"}), 400

        stats.increment("searches_total")
        cache_key = _cache_key(
            "search", {"query": query, "count": count, "provider": provider_name}
        )
        cached = cache.get(cache_key, SEARCH_TTL_S)
        if cached:
            stats.increment("search_cache_hits")
            return jsonify(cached)

        try:
            result = provider.search(query=query, count=count, provider=provider_name)
        except Exception:  # pragma: no cover
            cid = _new_correlation_id()
            logger.exception("search failed cid=%s query=%r", cid, query)
            return jsonify(_error_body("search failed", cid, query=query)), 502

        result["fetched_at"] = _now_ts()
        result["_cache_hit"] = False
        stored = cache.put(cache_key, result)
        stored["_cache_hit"] = False
        return jsonify(stored)

    @app.post("/fetch")
    def fetch_endpoint() -> Any:
        payload, err = _require_json_body()
        if err is not None:
            return err
        url, field_error = _string_payload_field(payload, "url", "")
        if field_error:
            return jsonify({"error": field_error}), 400
        full_text = payload.get("full_text") is True
        if full_text:
            max_chars = -1
        else:
            max_chars = _coerce_count(
                payload.get("max_chars", 12000), default=12000, min_value=0
            )
            if max_chars is None:
                return (
                    jsonify({"error": "max_chars must be a non-negative integer"}),
                    400,
                )
        if not url:
            return jsonify({"error": "url is required"}), 400

        # Cheap structural pre-flight so obviously-bad URLs get a 400 without
        # the cost of DNS. Structural reasons describe the CALLER'S INPUT and
        # are safe to surface; DNS/IP reasons from the runtime check below
        # are NOT safe (they leak internal topology) and must be generalized.
        parts, reason = _validate_url_structure(url)
        if parts is None:
            return jsonify({"error": f"unsafe url: {reason}", "url": url}), 400

        stats.increment("fetches_total")
        cache_key = _cache_key("fetch", {"url": url})
        cached = cache.get(cache_key, FETCH_TTL_S)
        if cached:
            stats.increment("fetch_cache_hits")
            return jsonify(_apply_max_chars(cached, max_chars))

        try:
            # max_chars=-1 caches full text; we re-truncate per caller below.
            result = fetch_page(url=url, max_chars=-1)
        except UnsafeUrlError:
            cid = _new_correlation_id()
            logger.exception("fetch rejected unsafe url cid=%s url=%r", cid, url)
            return (
                jsonify(_error_body("unsafe url", cid, url=url)),
                400,
            )
        except Exception:  # pragma: no cover
            cid = _new_correlation_id()
            logger.exception("fetch failed cid=%s url=%r", cid, url)
            return (
                jsonify(_error_body("fetch failed", cid, url=url)),
                502,
            )

        stored = cache.put(cache_key, result)
        stored["_cache_hit"] = False
        return jsonify(_apply_max_chars(stored, max_chars))

    @app.post("/paper/fetch")
    def paper_fetch_endpoint() -> Any:
        payload, err = _require_json_body()
        if err is not None:
            return err
        url, field_error = _string_payload_field(payload, "url", None)
        if field_error:
            return jsonify({"error": field_error}), 400
        if not url:
            url, field_error = _string_payload_field(payload, "pdf_url", "")
            if field_error:
                return jsonify({"error": field_error}), 400
        max_chars = _coerce_count(
            payload.get("max_chars", -1), default=-1, min_value=-1
        )
        if max_chars is None:
            return (
                jsonify({"error": "max_chars must be an integer >= -1"}),
                400,
            )
        if not url:
            return jsonify({"error": "url is required"}), 400

        parts, reason = _validate_url_structure(_normalize_arxiv_pdf_url(url))
        if parts is None:
            return jsonify({"error": f"unsafe url: {reason}", "url": url}), 400

        stats.increment("paper_fetches_total")
        cache_key = _cache_key("paper_fetch", {"url": url})
        cached = cache.get(cache_key, PAPER_TTL_S)
        if cached:
            stats.increment("paper_fetch_cache_hits")
            return jsonify(_apply_max_chars(cached, max_chars))

        try:
            result = fetch_paper(url=url, max_chars=max_chars)
        except UnsafeUrlError:
            cid = _new_correlation_id()
            logger.exception("paper fetch rejected unsafe url cid=%s url=%r", cid, url)
            return jsonify(_error_body("unsafe url", cid, url=url)), 400
        except Exception:
            cid = _new_correlation_id()
            logger.exception("paper fetch failed cid=%s url=%r", cid, url)
            return jsonify(_error_body("paper fetch failed", cid, url=url)), 502

        stored = cache.put(cache_key, result)
        stored["_cache_hit"] = False
        return jsonify(_apply_max_chars(stored, max_chars))

    @app.post("/research")
    def research_endpoint() -> Any:
        payload, err = _require_json_body()
        if err is not None:
            return err
        query, field_error = _string_payload_field(payload, "query", "")
        if field_error:
            return jsonify({"error": field_error}), 400
        count = _coerce_count(payload.get("count", 5))
        if count is None:
            return jsonify({"error": "count must be a positive integer"}), 400
        fetch_top_n = _coerce_count(
            payload.get("fetch_top_n", min(3, count)), default=min(3, count), min_value=0
        )
        if fetch_top_n is None:
            return (
                jsonify({"error": "fetch_top_n must be a non-negative integer"}),
                400,
            )
        provider_name, field_error = _string_payload_field(
            payload, "provider", DEFAULT_PROVIDER
        )
        if field_error:
            return jsonify({"error": field_error}), 400
        provider_name = provider_name.lower()
        max_chars = _coerce_count(
            payload.get("max_chars", 12000), default=12000, min_value=0
        )
        if max_chars is None:
            return (
                jsonify({"error": "max_chars must be a non-negative integer"}),
                400,
            )

        if not query:
            return jsonify({"error": "query is required"}), 400

        stats.increment("research_total")
        stats.increment("searches_total")
        try:
            search_result = provider.search(query=query, count=count, provider=provider_name)
        except Exception:  # pragma: no cover
            cid = _new_correlation_id()
            logger.exception("research search failed cid=%s query=%r", cid, query)
            return (
                jsonify(_error_body("search failed", cid, query=query)),
                502,
            )

        fetched: List[Dict[str, Any]] = []
        for item in search_result.get("results", [])[:fetch_top_n]:
            url = item.get("url", "").strip()
            if not url:
                continue
            parts, reason = _validate_url_structure(url)
            if parts is None:
                # Unified per-item shape: structural and DNS rejections return
                # the same body (bare "unsafe url" + cid). Surfacing the
                # structural reason here re-opened an SSRF oracle via the
                # presence/absence of `: <reason>` in the error string.
                cid = _new_correlation_id()
                logger.warning(
                    "research per-item structural reject cid=%s url=%r reason=%s",
                    cid, url, reason,
                )
                fetched.append(_item_error("unsafe url", cid, url=url))
                continue
            try:
                # max_chars=-1 populates full text; we re-truncate per caller
                # so `max_chars=0` yields empty text per contract, matching
                # /fetch's behavior.
                page = fetch_page(url=url, max_chars=-1)
                fetched.append(_apply_max_chars(page, max_chars))
            except UnsafeUrlError:
                cid = _new_correlation_id()
                logger.exception(
                    "research per-item unsafe url cid=%s url=%r", cid, url
                )
                fetched.append(_item_error("unsafe url", cid, url=url))
            except Exception:
                cid = _new_correlation_id()
                logger.exception(
                    "research per-item fetch failed cid=%s url=%r", cid, url
                )
                fetched.append(_item_error("fetch failed", cid, url=url))

        return jsonify(
            {
                "query": query,
                "provider": search_result.get("provider", provider_name),
                "results": search_result.get("results", []),
                "fetched": fetched,
                "fetched_at": _now_ts(),
            }
        )

    @app.post("/image_search")
    def image_search_endpoint() -> Any:
        payload, err = _require_json_body()
        if err is not None:
            return err
        query, field_error = _string_payload_field(payload, "query", "")
        if field_error:
            return jsonify({"error": field_error}), 400
        count = _coerce_count(payload.get("count", 5))
        if count is None:
            return jsonify({"error": "count must be a positive integer"}), 400
        fetch_top_n = _coerce_count(
            payload.get("fetch_top_n", 0), default=0, min_value=0
        )
        if fetch_top_n is None:
            return (
                jsonify({"error": "fetch_top_n must be a non-negative integer"}),
                400,
            )
        provider_name, field_error = _string_payload_field(
            payload, "provider", DEFAULT_PROVIDER
        )
        if field_error:
            return jsonify({"error": field_error}), 400
        provider_name = provider_name.lower()
        safesearch, field_error = _string_payload_field(payload, "safesearch", "off")
        if field_error:
            return jsonify({"error": field_error}), 400
        safesearch = safesearch.lower()
        if not query:
            return jsonify({"error": "query is required"}), 400

        stats.increment("image_searches_total")
        cache_key = _cache_key(
            "image_search",
            {
                "query": query,
                "count": count,
                "provider": provider_name,
                "safesearch": safesearch,
                "fetch_top_n": fetch_top_n,
            },
        )
        cached = cache.get(cache_key, SEARCH_TTL_S)
        if cached:
            stats.increment("image_search_cache_hits")
            return jsonify(cached)

        try:
            result = provider.search_images(
                query=query, count=count, provider=provider_name, safesearch=safesearch
            )
        except Exception:
            cid = _new_correlation_id()
            logger.exception("image search failed cid=%s query=%r", cid, query)
            return (
                jsonify(_error_body("image search failed", cid, query=query)),
                502,
            )

        if fetch_top_n > 0:
            fetched: List[Dict[str, Any]] = []
            for item in result.get("results", [])[:fetch_top_n]:
                img_url = item.get("url", "").strip()
                if not img_url:
                    continue
                parts, reason = _validate_url_structure(img_url)
                if parts is None:
                    # Unified per-item shape: structural and DNS rejections
                    # return the same body to avoid an SSRF oracle via error
                    # string differences.
                    cid = _new_correlation_id()
                    logger.warning(
                        "image_search per-item structural reject cid=%s url=%r reason=%s",
                        cid, img_url, reason,
                    )
                    fetched.append(_item_error("unsafe url", cid, url=img_url))
                    continue
                try:
                    img_result = fetch_page(url=img_url, max_chars=-1)
                    fetched.append(img_result)
                except UnsafeUrlError:
                    cid = _new_correlation_id()
                    logger.exception(
                        "image_search per-item unsafe url cid=%s url=%r", cid, img_url
                    )
                    fetched.append(_item_error("unsafe url", cid, url=img_url))
                except Exception:
                    cid = _new_correlation_id()
                    logger.exception(
                        "image_search per-item fetch failed cid=%s url=%r", cid, img_url
                    )
                    fetched.append(_item_error("fetch failed", cid, url=img_url))
            result["fetched_images"] = fetched

        result["fetched_at"] = _now_ts()
        result["_cache_hit"] = False
        stored = cache.put(cache_key, result)
        stored["_cache_hit"] = False
        return jsonify(stored)

    @app.get("/cache")
    def cache_list_endpoint() -> Any:
        entries: List[Dict[str, Any]] = []
        cache_paths = [
            p for p in _iter_dir_limited(CACHE_DIR, label="cache")
            if p.suffix == ".json"
        ]
        for path in sorted(cache_paths):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("skipping unreadable cache entry %s: %s", path.name, exc)
                continue
            if not isinstance(data, dict):
                logger.warning(
                    "skipping cache entry %s: root is %s, expected object",
                    path.name,
                    type(data).__name__,
                )
                continue
            try:
                size_bytes = path.stat().st_size
            except OSError as exc:
                logger.warning("skipping cache entry %s: stat failed: %s", path.name, exc)
                continue
            entry: Dict[str, Any] = {
                "key": path.stem,
                "cached_at": data.get("_cached_at", 0),
                "size_bytes": size_bytes,
            }
            if "query" in data:
                entry["query"] = data["query"]
            if "url" in data:
                entry["url"] = data["url"]
            if "provider" in data:
                entry["provider"] = data["provider"]
            entries.append(entry)
        image_entries: List[Dict[str, Any]] = []
        img_iter = sorted(_iter_dir_limited(IMAGE_CACHE_DIR, label="image cache"))
        for img_path in img_iter:
            try:
                size_bytes = img_path.stat().st_size
            except OSError as exc:
                logger.warning("skipping image cache entry %s: stat failed: %s", img_path.name, exc)
                continue
            image_entries.append({
                "filename": img_path.name,
                "path": str(img_path),
                "size_bytes": size_bytes,
            })
        return jsonify({
            "entries": entries,
            "images": image_entries,
            "total_entries": len(entries),
            "total_images": len(image_entries),
            "total_bytes": sum(e["size_bytes"] for e in entries) + sum(e["size_bytes"] for e in image_entries),
        })

    @app.get("/stats")
    def stats_endpoint() -> Any:
        search_count = 0
        fetch_count = 0
        image_count = 0
        providers: Dict[str, int] = {}

        def _count_provider(p: Path) -> None:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("skipping unreadable cache entry %s: %s", p.name, exc)
                return
            if not isinstance(data, dict):
                logger.warning(
                    "skipping cache entry %s: root is %s, expected object",
                    p.name,
                    type(data).__name__,
                )
                return
            name = data.get("provider", "unknown")
            providers[name] = providers.get(name, 0) + 1

        for path in _iter_dir_limited(CACHE_DIR, label="cache stats"):
            if path.suffix != ".json":
                continue
            stem = path.stem
            if stem.startswith("image_search_"):
                image_count += 1
                _count_provider(path)
            elif stem.startswith("search_"):
                search_count += 1
                _count_provider(path)
            elif stem.startswith("fetch_"):
                fetch_count += 1
        img_files = [
            p for p in _iter_dir_limited(IMAGE_CACHE_DIR, label="image stats")
            if p.is_file()
        ]
        return jsonify({
            "cached_searches": search_count,
            "cached_fetches": fetch_count,
            "cached_image_searches": image_count,
            "cached_images": len(img_files),
            "providers": providers,
            "cache_dir": str(CACHE_DIR),
            **stats.snapshot(),
        })

    return app


def _is_loopback_bind_host(host: str) -> bool:
    value = (host or "").strip().strip("[]")
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(value, None)
    except socket.gaierror:
        return False
    return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_loopback for info in infos)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AtlasForge local web proxy service")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.debug and not _is_loopback_bind_host(args.host):
        parser.error("--debug may only be used with a loopback host")

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    app = create_app()
    if waitress is not None and not args.debug:
        logger.info(
            "serving with waitress host=%s port=%d threads=%d",
            args.host,
            args.port,
            WEB_PROXY_THREADS,
        )
        waitress.serve(
            app,
            host=args.host,
            port=args.port,
            threads=WEB_PROXY_THREADS,
        )
    else:
        logger.info(
            "serving with Flask/Werkzeug host=%s port=%d debug=%s",
            args.host,
            args.port,
            args.debug,
        )
        app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
