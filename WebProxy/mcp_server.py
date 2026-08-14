"""
MCP server that transparently replaces Claude Code's built-in WebSearch and WebFetch.

When loaded via --mcp-config alongside --disallowedTools WebSearch,WebFetch,
the model's tool calls route through the AtlasForge local web proxy instead of
Anthropic's filtered backend. The tool names and input schemas match the built-ins
exactly so the model doesn't need to change behavior.

Requires: WebProxy/service.py running on 127.0.0.1:8765

Usage:
    claude -p --mcp-config /home/vader/AI-AtlasForge/WebProxy/configs/mcp.json \
              --disallowedTools WebSearch,WebFetch \
              "your prompt"
"""

from __future__ import annotations

import ipaddress
import hashlib
import json
import logging
import os
import re
import socket
import sys
import threading
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import requests

_logger = logging.getLogger("web_proxy_mcp_server")

PROXY_BASE = "http://127.0.0.1:8765"
TIMEOUT = 30

# Arg-validation ceilings. Match web_proxy_service defaults where applicable
# so the MCP layer rejects obviously-abusive payloads before they hit HTTP.
MAX_QUERY_LENGTH = 2048
MAX_URL_LENGTH = 2048
MAX_MAX_CHARS = 100_000
MAX_COUNT = 50
# Iter-3: bound stdin line length. A 1 MB frame is two orders of magnitude
# above any legitimate JSON-RPC request and protects against OOM on an
# adversarial peer that streams an unbounded line.
MAX_LINE_LENGTH = 1_000_000
MAX_STAGE_ARTIFACT_CHARS = 200_000
MAX_STAGE_FIELD_CHARS = 40_000

_VALID_ATLASFORGE_STAGES = frozenset({
    "PLANNING",
    "BUILDING",
    "TESTING",
    "ANALYZING",
    "CYCLE_END",
    "COMPLETE",
    "REVIEW",
})

_STAGE_GUARD_POLICIES = {
    "PLANNING": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeSubmitPlan",
            "AtlasForgeWriteStageNote",
        },
        "allowed_write_paths": [
            "workspace/*/artifacts/implementation_plan.md",
            "workspace/*/research/*.md",
        ],
        "tool_write_paths": {
            "AtlasForgeSubmitPlan": [
                "workspace/*/artifacts/implementation_plan.md",
            ],
            "AtlasForgeWriteStageNote": [
                "workspace/*/research/*.md",
            ],
        },
        "allowed_extensions": {".md"},
    },
    "BUILDING": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeWriteStageNote",
            "AtlasForgeSubmitPatchSummary",
        },
        "allowed_write_paths": [
            "missions/*/build/*.json",
            "missions/*/build/*.md",
            "state/build_reports/*.json",
            "workspace/*/artifacts/*.json",
            "workspace/*/artifacts/*.md",
        ],
        "allowed_extensions": {".json", ".md"},
    },
    "TESTING": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeWriteStageNote",
            "AtlasForgeSubmitPatchSummary",
            "AtlasForgeWriteMutationArtifact",
        },
        "allowed_write_paths": [
            "workspace/*/artifacts/test_results.md",
            "workspace/*/artifacts/testing/*.json",
            "workspace/*/artifacts/testing/*.md",
            "workspace/*/artifacts/testing/agents/*/*.json",
            "workspace/*/artifacts/testing/agents/*/*.jsonl",
            "workspace/*/artifacts/testing/agents/*/*.md",
            "workspace/*/tests/Red_Team/*.json",
            "workspace/*/tests/Red_Team/*.md",
        ],
        "tool_write_paths": {
            "AtlasForgeWriteMutationArtifact": [
                "workspace/*/artifacts/testing/mutation/*",
                "workspace/*/artifacts/testing/agents/*/mutation/*",
            ],
        },
        "allowed_extensions": {".json", ".jsonl", ".md"},
        "tool_allowed_extensions": {
            "AtlasForgeWriteMutationArtifact": {
                ".json", ".jsonl", ".md", ".txt",
                ".py", ".js", ".ts", ".tsx", ".jsx",
                ".html", ".css", ".yml", ".yaml", ".toml",
            },
        },
    },
    "ANALYZING": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeWriteStageNote",
            "AtlasForgeSubmitReview",
        },
        "allowed_write_paths": [
            "missions/*/analysis/*.json",
            "missions/*/analysis/*.md",
            "missions/*/review/*.json",
            "missions/*/review/*.md",
            "state/reviews/*.json",
            "workspace/*/research/*.json",
            "workspace/*/research/*.md",
        ],
        "allowed_extensions": {".json", ".md"},
    },
    "CYCLE_END": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeWriteStageNote",
            "AtlasForgeSubmitReview",
        },
        "allowed_write_paths": [
            "missions/*/cycle_end/*.json",
            "missions/*/cycle_end/*.md",
            "missions/*/reports/*.json",
            "missions/*/reports/*.md",
            "state/reviews/*.json",
            "workspace/*/artifacts/*.json",
            "workspace/*/artifacts/*.md",
        ],
        "allowed_extensions": {".json", ".md"},
    },
    "COMPLETE": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
        },
        "allowed_write_paths": [],
        "allowed_extensions": set(),
    },
    "REVIEW": {
        "allowed_tools": {
            "AtlasForgeGetStagePolicy",
            "AtlasForgeWriteStageNote",
            "AtlasForgeSubmitReview",
        },
        "allowed_write_paths": [
            "missions/*/review/*.json",
            "missions/*/review/*.md",
            "state/reviews/*.json",
        ],
        "allowed_extensions": {".json", ".md"},
    },
}

# Only `http` and `https` URLs are allowed through the fetch surface.
# `file://`, `javascript:`, `data:`, `gopher://`, `ftp://`, etc. are blocked
# as SSRF / local-file-read vectors.
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})

# Iter-3 H12 / iter-4 C1-C3,H5: SSRF protection. Block loopback, link-local
# (AWS IMDS at 169.254.169.254), RFC1918, CGNAT, broadcast, TEST-NETs,
# multicast, and IPv6 variants including IPv4-mapped IPv6 (::ffff:x.x.x.x).
# Hostnames are resolved via getaddrinfo and each returned address is
# checked; a single blocked match fails the whole URL. Additionally,
# `_is_blocked_ip` unwraps IPv4-mapped IPv6 addresses so v4-only bypass
# attempts via `::ffff:127.0.0.1` are re-checked against the v4 blocklist.
_BLOCKED_IP_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),        # "this network" / invalid source
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT (iter-4 H5)
    ipaddress.ip_network("127.0.0.0/8"),      # IPv4 loopback
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS IMDS / GCP metadata
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918
    ipaddress.ip_network("192.0.2.0/24"),     # TEST-NET-1 (iter-4 H5)
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918
    ipaddress.ip_network("198.51.100.0/24"),  # TEST-NET-2 (iter-4 H5)
    ipaddress.ip_network("203.0.113.0/24"),   # TEST-NET-3 (iter-4 H5)
    ipaddress.ip_network("224.0.0.0/4"),      # IPv4 multicast
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast (iter-4 H5)
    ipaddress.ip_network("::/128"),           # IPv6 unspecified (iter-4 H5)
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("::ffff:0:0/96"),    # IPv4-mapped IPv6 (iter-4 C1-C3)
    ipaddress.ip_network("fc00::/7"),         # IPv6 ULA
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
    ipaddress.ip_network("ff00::/8"),         # IPv6 multicast
]

# Hostnames that must be treated as loopback aliases regardless of DNS.
_LOOPBACK_HOSTS = frozenset({
    "localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback",
})

# Regex to strip proxy host:port from exception messages before they're
# surfaced to the LLM. Prevents internal-infrastructure leak on error paths.
_PROXY_REDACT_RE = re.compile(r"127\.0\.0\.1:\d+|localhost:\d+")

# Iter-4 H6: characters that different HTTP stacks disagree on during URL
# parsing. `urlparse` and `urllib3`/`requests` can split on these into
# different hostnames, producing a validator/fetcher TOCTOU. Reject any URL
# containing these chars. `\` is the primary vector for the backslash-ampersand
# user-info hostname-swap attack (e.g. `http://good.com\@evil.com/`).
_URL_FORBIDDEN_CHARS = frozenset("\\<>\"^`{|}")

# Iter-4 H11: opt-in env var to permit legacy DNS fail-open behaviour.
# Default is fail-closed: if getaddrinfo raises gaierror, we refuse the URL
# rather than let the downstream fetcher resolve to a blocked IP.
_DNS_FAIL_OPEN = os.environ.get("WEB_PROXY_ALLOW_DNS_FAIL", "0") == "1"

_CAPTURE_LOCK = threading.Lock()
_CAPTURE_COUNTER = 0


def _safe_artifact_segment(value: str, default: str = "agent") -> str:
    """Return a filesystem-safe artifact path segment."""
    if not isinstance(value, str):
        value = ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    safe = safe.strip("._-")
    return safe[:120] or default


def _investigation_workspace_from_env() -> Path | None:
    raw = (
        os.environ.get("ATLASFORGE_INVESTIGATION_WORKSPACE")
        or os.environ.get("ATLASFORGE_WORKSPACE_DIR")
        or ""
    ).strip()
    if not raw:
        return None
    try:
        workspace = Path(raw).expanduser().resolve()
    except OSError:
        return None
    if workspace.is_symlink():
        return None
    return workspace


def _capture_webproxy_json_output(
    *,
    tool_name: str,
    endpoint: str,
    request_payload: dict,
    response_payload: dict,
) -> dict | None:
    """
    Mirror the literal WebProxy JSON response into the active investigation.

    This is intentionally done in the MCP layer so the artifact exists for both
    Claude and Codex workflows, independent of their transcript/event formats.
    """
    if not isinstance(response_payload, dict):
        return None
    workspace = _investigation_workspace_from_env()
    if workspace is None:
        return None

    try:
        subagent_id = _safe_artifact_segment(
            os.environ.get("ATLASFORGE_SUBAGENT_ID")
            or os.environ.get("ATLASFORGE_ARTIFACT_LABEL")
            or os.environ.get("ATLASFORGE_AGENT_LABEL")
            or "agent"
        )
        tool_segment = _safe_artifact_segment(tool_name, "webproxy")
        endpoint_segment = _safe_artifact_segment(endpoint.strip("/").replace("/", "_"), "call")

        global _CAPTURE_COUNTER
        with _CAPTURE_LOCK:
            _CAPTURE_COUNTER += 1
            seq = _CAPTURE_COUNTER

        data = json.dumps(response_payload, ensure_ascii=False, indent=2).encode("utf-8")
        sha256 = hashlib.sha256(data).hexdigest()
        capture_dir = workspace / "artifacts" / "webproxy_json" / subagent_id
        capture_dir.mkdir(parents=True, exist_ok=True)
        dest_path = capture_dir / f"{seq:06d}_{tool_segment}_{endpoint_segment}_{sha256[:12]}.json"
        if not dest_path.exists():
            tmp_path = dest_path.with_name(f".{dest_path.name}.tmp.{os.getpid()}")
            tmp_path.write_bytes(data)
            os.replace(tmp_path, dest_path)

        now = datetime.now(timezone.utc).isoformat()
        record = {
            "timestamp": now,
            "seq": seq,
            "artifact_type": "web_proxy_json_output",
            "subagent_id": subagent_id,
            "tool_name": tool_name,
            "endpoint": endpoint,
            "request": request_payload,
            "url": response_payload.get("url") or response_payload.get("pdf_url"),
            "query": response_payload.get("query"),
            "title": response_payload.get("title") or response_payload.get("paper_title"),
            "type": response_payload.get("type") or endpoint_segment,
            "cache_json_path": response_payload.get("_cache_path"),
            "artifact_json_path": str(dest_path),
            "sha256": sha256,
            "byte_length": len(data),
        }

        line = json.dumps(record, ensure_ascii=False) + "\n"
        for manifest_path in (capture_dir / "manifest.jsonl", workspace / "artifacts" / "webproxy_json" / "manifest.jsonl"):
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with open(manifest_path, "a", encoding="utf-8") as f:
                f.write(line)
        return record
    except Exception as exc:
        _logger.debug("failed to capture WebProxy JSON output: %s", exc)
        return None


def _sanitize_error(text: str) -> str:
    """Strip proxy host:port and obvious path leaks from an error string."""
    if not isinstance(text, str):
        return ""
    return _PROXY_REDACT_RE.sub("[proxy]", text)


def _is_blocked_ip(ip_str: str) -> bool:
    """Return True iff the IP string is in any of the SSRF-blocked networks.

    Iter-4 C1-C3: IPv4-mapped IPv6 addresses (e.g. `::ffff:127.0.0.1`) parse
    as version=6 and don't match v4 networks via `ip in net`. Explicitly
    unwrap the embedded v4 address and re-check against the v4 blocklist so
    adversaries can't bypass via the IPv4-mapped prefix. The `::ffff:0:0/96`
    network entry also catches this at the v6 layer as defense-in-depth.
    """
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped
        if mapped is not None:
            for net in _BLOCKED_IP_NETWORKS:
                if isinstance(net, ipaddress.IPv4Network) and mapped in net:
                    return True
    return any(ip in net for net in _BLOCKED_IP_NETWORKS)


def _send(obj: dict) -> None:
    """Write a JSON-RPC frame to stdout and flush."""
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _make_error(msg_id: Any, code: int, message: str) -> dict:
    """Build a JSON-RPC 2.0 error envelope."""
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _host_from_url(url: str) -> str:
    """Extract lowercased, DNS-canonicalized hostname from a URL.

    Uses `urlparse().hostname`, which:
      - correctly handles IPv6 bracketed literals (`http://[::1]:8080/` → `::1`);
      - strips userinfo and port;
      - lowercases.

    The trailing dot (FQDN root label) is also stripped so `reddit.com.` and
    `reddit.com` canonicalize to the same match target.

    Iter-3 H7: percent-encoded hostnames (`reddit%2ecom`) are rejected. Some
    HTTP clients percent-decode the host before resolving DNS, so a filter
    that compares the raw netloc would silently miss the bypass.
    `urlparse` does NOT percent-decode hostname for us; we reject any host
    that still contains `%` since legitimate DNS labels never contain it.
    """
    if not isinstance(url, str):
        return ""
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return ""
    if "%" in host:
        return ""
    # Iter-6 H4: strip BOTH leading and trailing dots. urlparse on
    # `https://.reddit.com/` returns hostname `.reddit.com`; only stripping
    # trailing dots leaves the leading dot intact, and `_host_matches` then
    # treats `.reddit.com` as a subdomain of `reddit.com` because
    # `endswith(".reddit.com")` matches the literal leaked dot. Symmetric
    # strip on BOTH sides of the comparison closes this filter-bypass class.
    host = host.strip().strip(".").lower()
    if not host:
        return ""
    # Iter-4 H10: IDNA-normalize so Unicode homoglyphs (full-width period
    # U+FF0E, Cyrillic letters that share glyphs with Latin, etc.) normalize
    # to their ASCII form BEFORE filter matching. Without this, `reddit\uff0ecom`
    # passes `_host_matches("...", "reddit.com") == False` yet DNS resolves to
    # the real reddit.com after `requests` does its own IDNA pass. If the host
    # is already pure-ASCII, skip the round-trip (stdlib `idna` codec rejects
    # underscores, which are common in private/test hostnames).
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii").lower()
        except (UnicodeError, UnicodeDecodeError):
            return ""
    return host


def _host_matches(host: str, filter_entry: str) -> bool:
    """Exact-or-suffix hostname match, DNS-style. Prevents substring spoofing.

    Both `host` and `filter_entry` are trailing-dot-stripped and lowercased
    before comparison. Non-string inputs return False (no raise).

    Iter-3 H11: a bare-TLD or single-label filter (no `.` after stripping
    trailing dots) requires EXACT match. Otherwise an LLM that hallucinates
    `"com"` into a filter list would `endswith(".com")` against every .com
    domain — silent over-broad match.
    """
    if not isinstance(host, str) or not isinstance(filter_entry, str):
        return False
    # Iter-6 H4: symmetric normalization on BOTH sides — strip whitespace,
    # strip dots from BOTH ends, lowercase. Asymmetric leading-dot handling
    # was the recurring filter-bypass primitive flagged by the iter-5 Red
    # Team. Defense-in-depth even though _host_from_url now also strips.
    h = host.strip().strip(".").lower()
    fe = filter_entry.strip().strip(".").lower()
    if not h or not fe:
        return False
    if "." not in fe:
        return h == fe
    return h == fe or h.endswith("." + fe)


_HTTP_STATUS_PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    408: "Request Timeout",
    410: "Gone",
    429: "Too Many Requests",
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
}


class ProxyTargetError(RuntimeError):
    """WP-R1 D2: the proxy answered, but the TARGET site failed.

    Carries the honest upstream status so the tool result the model reads can
    say "target returned HTTP 404 (Not Found)" instead of an opaque
    "fetch failed". A RuntimeError subclass so the existing
    `except RuntimeError` branch in `_handle_tools_call` already catches it.
    """

    def __init__(self, message: str):
        super().__init__(message)


def _describe_target_failure(body: dict) -> str:
    """Render a D2 proxy error body into one honest line for the model.

    `target_status` present  -> name the status, phrase, and final URL.
    `target_status` absent   -> the fetch never got an HTTP response at all
                                (connection error / timeout / DNS), which is a
                                materially different retry decision.
    """
    base = body.get("error") or "proxy request failed"
    status = body.get("target_status")
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None

    if status is None:
        detail = "no HTTP response (connection/timeout)"
    else:
        phrase = _HTTP_STATUS_PHRASES.get(status)
        detail = f"target returned HTTP {status}"
        if phrase:
            detail += f" ({phrase})"
    final_url = body.get("final_url") or body.get("url")
    if final_url:
        detail += f" for {final_url}"
    cid = body.get("correlation_id")
    if cid:
        detail += f" [cid {cid}]"
    return f"{base.capitalize()}: {detail}"


def _proxy_post(endpoint: str, payload: dict) -> dict:
    """POST to the proxy and return parsed JSON.

    Iter-3 H4: if the proxy returns non-JSON, raise a tagged RuntimeError
    rather than letting `json.JSONDecodeError` (a ValueError subclass)
    surface to the LLM via the `except ValueError as exc` branch in
    `_handle_tools_call`. JSONDecodeError messages contain raw parser
    detail like "Expecting value: line 1 column 1 (char 0)" which is
    confusing-noise at best and information-disclosure at worst.

    WP-R1 D2: on an upstream failure the proxy answers 502 with a JSON body
    naming the real target status. `raise_for_status` alone would throw that
    body away and leave the model with "Proxy returned HTTP 502", so parse the
    error body first and re-raise as a `ProxyTargetError` carrying the honest
    status. Non-JSON error bodies fall through to `raise_for_status`.
    """
    resp = requests.post(f"{PROXY_BASE}{endpoint}", json=payload, timeout=TIMEOUT)
    if resp.status_code >= 400:
        body = None
        try:
            body = resp.json()
        except (json.JSONDecodeError, ValueError):
            body = None
        if isinstance(body, dict) and "error" in body:
            raise ProxyTargetError(_describe_target_failure(body))
    resp.raise_for_status()
    try:
        return resp.json()
    except json.JSONDecodeError:
        raise RuntimeError("proxy returned non-JSON body")


def _require_query(arguments: dict) -> str:
    raw = arguments.get("query", "")
    if not isinstance(raw, str):
        raise ValueError("query must be a string")
    q = raw.strip()
    if not q:
        raise ValueError("query is required")
    if len(q) > MAX_QUERY_LENGTH:
        raise ValueError(f"query exceeds {MAX_QUERY_LENGTH} chars")
    return q


def _require_url(arguments: dict) -> str:
    """Validate a URL argument and return it.

    Iter-3 hardening (H12):
      - Reject control chars (NUL, CR, LF, TAB) embedded in the URL.
      - Reject pure-decimal hostnames (decimal-encoded IPv4 literals).
      - Reject `localhost` and well-known loopback aliases regardless of DNS.
      - If the host is an IP literal (v4 or v6), check directly against
        `_BLOCKED_IP_NETWORKS`.
      - If the host is a name, resolve via getaddrinfo and reject if ANY
        returned address is in a blocked network. DNS rebinding is
        addressed by the proxy's outbound socket configuration, not here.
    """
    raw = arguments.get("url", "")
    if not isinstance(raw, str):
        raise ValueError("url must be a string")
    u = raw.strip()
    if not u:
        raise ValueError("url is required")
    if len(u) > MAX_URL_LENGTH:
        raise ValueError(f"url exceeds {MAX_URL_LENGTH} chars")
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in u):
        raise ValueError("url contains control characters")
    # Iter-4 H6: forbidden characters cause urlparse vs urllib3 disagreement
    # (backslash is the primary `good.com\@evil.com` TOCTOU vector). Reject
    # BEFORE parsing, since urlparse silently accepts these and yields a
    # hostname the fetcher will not actually connect to.
    if any(c in _URL_FORBIDDEN_CHARS for c in u):
        raise ValueError("url contains forbidden characters")
    try:
        parsed = urlparse(u)
    except Exception:
        raise ValueError("url is not parseable")
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        raise ValueError("url scheme must be http or https")
    host = parsed.hostname
    if not host:
        raise ValueError("url must include a hostname")
    host_lower = host.lower()
    # Iter-3: percent-encoded host bypass (e.g., reddit%2ecom that decodes
    # to reddit.com after `requests` normalizes). Reject outright.
    if "%" in host:
        raise ValueError("url hostname must not contain percent-encoding")
    # Decimal-encoded IPv4 literals (e.g., http://2130706433/ → 127.0.0.1)
    if host.isdigit():
        raise ValueError("decimal-encoded IP literals not allowed")
    # Well-known loopback aliases (case-insensitive).
    if host_lower in _LOOPBACK_HOSTS:
        raise ValueError(f"host {host_lower!r} not allowed (loopback)")
    # IP literal? Check directly. Non-IP-literal? Resolve.
    try:
        ipaddress.ip_address(host)
        is_ip_literal = True
    except ValueError:
        is_ip_literal = False
    if is_ip_literal:
        if _is_blocked_ip(host):
            raise ValueError(f"host {host} is in a blocked network")
    else:
        try:
            addrs = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            # Iter-4 H11: fail-CLOSED by default. If an attacker controls DNS
            # for the target hostname, a SERVFAIL at the validator with
            # fail-open lets the proxy's own resolver (which may have
            # different cache / retry behaviour) resolve to a blocked IP.
            # Legacy behaviour is available via env var for callers that
            # accept the risk (e.g. intermittent-DNS dev loops).
            if _DNS_FAIL_OPEN:
                _logger.warning(
                    "DNS resolution failed for %s; allowing (legacy mode): %s",
                    host,
                    exc,
                )
                return u
            raise ValueError(
                f"DNS resolution failed for {host}; refusing to fetch "
                "(set WEB_PROXY_ALLOW_DNS_FAIL=1 to permit)"
            ) from None
        for fam, _, _, _, sockaddr in addrs:
            ip_str = sockaddr[0]
            if _is_blocked_ip(ip_str):
                raise ValueError(
                    f"host {host} resolves to blocked address {ip_str}"
                )
    return u


def _clamp_int(value: Any, lo: int, hi: int, default: int) -> int:
    if value is None:
        return default
    # Iter-6 H8: `int(float('inf'))` and `int(float('nan'))` raise
    # OverflowError / ValueError respectively. Without OverflowError in the
    # tuple, Infinity values escaped to the bare `except Exception` in main()
    # and produced an opaque `-32603 Internal error`. This handler promised
    # `-32602 Invalid params`; honor that by catching OverflowError too.
    try:
        iv = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"expected integer, got {value!r}")
    if iv < lo:
        return lo
    if iv > hi:
        return hi
    return iv


TOOLS = [
    {
        "name": "WebSearch",
        "description": (
            "Search the web for information. Returns unfiltered search results with "
            "title, URL, and snippet for each result. No domain blocks, no content "
            "filtering. Uses Brave API if configured, otherwise DuckDuckGo."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to use",
                    "minLength": 2,
                },
                "allowed_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Only include search results from these domains",
                },
                "blocked_domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Never include search results from these domains",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "WebFetch",
        "description": (
            "Fetch content from a URL. Returns raw extracted content: title, headings, "
            "full text, and links. No summarization — returns the actual page content. "
            "Reddit URLs auto-route to JSON API. Image URLs auto-detect and save locally. "
            "Cached for 24h per URL."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "The URL to fetch content from",
                },
                "prompt": {
                    "type": "string",
                    "description": "Ignored — raw content is returned instead of a summary",
                },
            },
            "required": ["url", "prompt"],
        },
    },
    {
        "name": "FetchURL",
        "description": (
            "Fetch content from a URL. Returns raw extracted content: title, headings, "
            "full text, and links. No summarization — returns the actual page content. "
            "Reddit URLs auto-route to JSON API. Image URLs auto-detect and save locally. "
            "Cached for 24h per URL."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "The URL to fetch content from",
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "WebResearch",
        "description": (
            "Combined search + fetch: searches the web, then fetches the top N result "
            "pages. Returns search results plus extracted content from each page. "
            "Single call for research queries."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of search results",
                    "default": 5,
                },
                "fetch_top_n": {
                    "type": "integer",
                    "description": "How many top results to fetch",
                    "default": 3,
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max text chars per page",
                    "default": 12000,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "PaperFetch",
        "description": (
            "Download an open-access paper PDF directly and extract full paper text "
            "when possible. Use this for arXiv/PDF paper sources before quoting a "
            "paper. Returns local artifact paths, SHA-256, page extraction metadata, "
            "and extracted text. This is separate from webpage WebFetch."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "url": {
                    "type": "string",
                    "format": "uri",
                    "description": "Paper landing URL or direct PDF URL",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Max extracted text chars to return; -1 for all extracted text",
                    "default": -1,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "ImageSearch",
        "description": (
            "Search for images. Uses Brave API if configured, otherwise DuckDuckGo. "
            "Returns image URLs, source page URLs, thumbnails, dimensions. Optionally "
            "downloads top N images locally for vision tool access. The safesearch "
            "parameter controls content filtering: 'off' disables all filtering, "
            "'moderate' is the default DDG behavior, 'on' is strict SFW."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Image search query",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results",
                    "default": 5,
                },
                "fetch_top_n": {
                    "type": "integer",
                    "description": "Download top N images locally",
                    "default": 0,
                },
                "safesearch": {
                    "type": "string",
                    "description": "Content filter: 'off', 'moderate', or 'on'",
                    "enum": ["off", "moderate", "on"],
                    "default": "off",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "AtlasForgeGetStagePolicy",
        "description": (
            "Return the AtlasForge MCP stage-guard policy for the active or requested "
            "stage. Use this before submitting stage artifacts."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "stage": {
                    "type": "string",
                    "description": "Optional AtlasForge stage such as PLANNING, BUILDING, ANALYZING.",
                },
            },
        },
    },
    {
        "name": "AtlasForgeSubmitPlan",
        "description": (
            "Submit a structured AtlasForge planning artifact through the stage guard. "
            "The MCP server validates the active stage, target path, and extension before writing."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mission_id": {"type": "string"},
                "stage": {"type": "string"},
                "plan": {
                    "type": "object",
                    "description": "Structured plan data to render into artifacts/implementation_plan.md.",
                },
                "target_path": {
                    "type": "string",
                    "description": "Optional repo-relative path. During PLANNING, defaults to workspace/<mission>/artifacts/implementation_plan.md.",
                },
            },
            "required": ["plan"],
        },
    },
    {
        "name": "AtlasForgeWriteStageNote",
        "description": (
            "Write a Markdown stage note through the AtlasForge stage guard. "
            "Allowed paths depend on the active stage."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mission_id": {"type": "string"},
                "stage": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "target_path": {
                    "type": "string",
                    "description": "Optional repo-relative .md path.",
                },
            },
            "required": ["content"],
        },
    },
    {
        "name": "AtlasForgeSubmitReview",
        "description": (
            "Submit a structured review or analysis artifact through the AtlasForge stage guard."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mission_id": {"type": "string"},
                "stage": {"type": "string"},
                "review": {
                    "type": "object",
                    "description": "Structured review JSON to persist.",
                },
                "target_path": {
                    "type": "string",
                    "description": "Optional repo-relative path. Defaults to state/reviews/<mission_id>.json.",
                },
            },
            "required": ["review"],
        },
    },
    {
        "name": "AtlasForgeSubmitPatchSummary",
        "description": (
            "Submit a structured build/test patch summary through the AtlasForge stage guard."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mission_id": {"type": "string"},
                "stage": {"type": "string"},
                "summary": {
                    "type": "object",
                    "description": "Structured build/test summary JSON to persist.",
                },
                "target_path": {
                    "type": "string",
                    "description": "Optional repo-relative path. Defaults by stage.",
                },
            },
            "required": ["summary"],
        },
    },
    {
        "name": "AtlasForgeWriteMutationArtifact",
        "description": (
            "Write a controlled TESTING-stage mutation artifact. This is only for "
            "mutant copies, mutation scripts, and mutation evidence under "
            "artifacts/testing/mutation/; it must not modify production files."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mission_id": {"type": "string"},
                "stage": {"type": "string"},
                "content": {"type": "string"},
                "target_path": {
                    "type": "string",
                    "description": "Repo-relative path under workspace/<mission>/artifacts/testing/mutation/.",
                },
            },
            "required": ["content", "target_path"],
        },
    },
]


def _format_search_results(data: dict) -> str:
    lines = []
    lines.append(f"Web search results for query: \"{data.get('query', '')}\"")
    lines.append(f"Provider: {data.get('provider', 'unknown')}")
    lines.append(f"Cache hit: {data.get('_cache_hit', False)}")
    if data.get("_investigation_json_path"):
        lines.append(f"Investigation JSON: {data.get('_investigation_json_path')}")
    lines.append("")
    for r in data.get("results", []):
        lines.append(f"**{r.get('title', '')}**")
        lines.append(f"URL: {r.get('url', '')}")
        lines.append(f"{r.get('snippet', '')}")
        lines.append("")
    return "\n".join(lines)


def _format_fetch_results(data: dict) -> str:
    if data.get("type") == "paper":
        lines = [
            f"Paper fetched: {data.get('pdf_url') or data.get('url', '')}",
            f"Saved PDF: {data.get('local_pdf_path', '')}",
            f"Saved text: {data.get('local_text_path', '')}",
            f"Cache JSON: {data.get('_cache_path', '')}",
            f"Investigation JSON: {data.get('_investigation_json_path', '')}",
            f"SHA-256: {data.get('sha256', '')}",
            f"Size: {data.get('byte_length', 0)} bytes",
            f"Pages extracted: {data.get('pages_extracted', 0)}/{data.get('page_count', '?')}",
            f"Extractor: {data.get('extractor') or 'unavailable'}",
            f"Truncated: {data.get('truncated', False)}",
            "",
        ]
        if data.get("extraction_error"):
            lines.append(f"Extraction error: {data.get('extraction_error')}")
            lines.append("")
        lines.append(data.get("text", ""))
        return "\n".join(lines)

    if data.get("type") == "image":
        lines = [
            f"Image fetched: {data.get('url', '')}",
            f"Saved to: {data.get('local_path', '')}",
            f"Dimensions: {data.get('width', '?')}x{data.get('height', '?')}",
            f"Size: {data.get('byte_length', 0)} bytes",
            f"Content-Type: {data.get('content_type', '')}",
        ]
        return "\n".join(lines)

    reddit = data.get("reddit")
    if reddit:
        lines = [f"Reddit: {data.get('url', '')}"]
        for p in reddit.get("posts", []):
            lines.append(f"\n**{p.get('title', '')}**")
            lines.append(f"by u/{p.get('author', '')} | {p.get('score', 0)} points | {p.get('num_comments', 0)} comments")
            if p.get("selftext"):
                lines.append(p["selftext"][:2000])
            if p.get("url") and p["url"] != p.get("permalink"):
                lines.append(f"Link: {p['url']}")
        for c in reddit.get("comments", []):
            lines.append(f"\n[{c.get('score', 0)}] u/{c.get('author', '')}: {c.get('body', '')[:1000]}")
        return "\n".join(lines)

    lines = [
        f"URL: {data.get('url', '')}",
        f"Title: {data.get('title', '')}",
        f"Cache hit: {data.get('_cache_hit', False)}",
        f"Cache JSON: {data.get('_cache_path', '')}",
        f"Investigation JSON: {data.get('_investigation_json_path', '')}",
        "",
    ]
    if data.get("headings"):
        lines.append("Headings:")
        for h in data["headings"]:
            lines.append(f"  - {h}")
        lines.append("")
    lines.append(data.get("text", ""))
    if data.get("links"):
        lines.append("\nLinks:")
        for lnk in data["links"][:30]:
            if lnk.get("text"):
                lines.append(f"  [{lnk['text']}]({lnk['url']})")
    return "\n".join(lines)


def _atlasforge_root() -> Path:
    raw = os.environ.get("ATLASFORGE_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def _read_json_file(path: Path, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _current_mission() -> dict:
    data = _read_json_file(_atlasforge_root() / "state" / "mission.json", {})
    return data if isinstance(data, dict) else {}


def _stage_guard_context() -> dict:
    data = _read_json_file(
        _atlasforge_root() / "state" / "codex_stage_guard_context.json",
        {},
    )
    if not isinstance(data, dict):
        return {}
    provider = str(data.get("provider") or "").strip().lower()
    stage = str(data.get("stage") or "").strip().upper()
    if provider not in {"claude", "codex", "gemini"} or stage not in _VALID_ATLASFORGE_STAGES:
        return {}
    current = _current_mission()
    current_provider = str(
        current.get("llm_provider") or current.get("provider") or ""
    ).strip().lower()
    if not current_provider:
        provider_state = _read_json_file(_atlasforge_root() / "state" / "llm_provider.json", {})
        current_provider = str(
            (provider_state or {}).get("provider")
            or (provider_state or {}).get("llm_provider")
            or ""
        ).strip().lower()
    if current_provider and current_provider != provider:
        return {}
    current_mission_id = str(current.get("mission_id") or "").strip()
    context_mission_id = str(data.get("mission_id") or "").strip()
    if current_mission_id and context_mission_id and current_mission_id != context_mission_id:
        return {}
    return data


def _env_provider() -> str:
    provider = str(os.environ.get("ATLASFORGE_ACTIVE_PROVIDER") or "").strip().lower()
    if provider in {"claude", "codex", "gemini"}:
        return provider
    return ""


def _active_provider() -> str:
    env_provider = _env_provider()
    if env_provider:
        return env_provider
    mission = _current_mission()
    mission_provider = str(
        mission.get("llm_provider") or mission.get("provider") or ""
    ).strip().lower()
    if mission_provider in {"claude", "codex", "gemini"}:
        return mission_provider
    context_provider = str(_stage_guard_context().get("provider") or "").strip().lower()
    if context_provider in {"claude", "codex", "gemini"}:
        return context_provider
    data = _read_json_file(_atlasforge_root() / "state" / "llm_provider.json", {})
    provider = str((data or {}).get("provider") or "").strip().lower()
    if provider in {"claude", "codex", "gemini"}:
        return provider
    return "unknown"


def _clean_identifier(value: Any, fallback: str, max_len: int = 80) -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = fallback
    cleaned = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw).strip("._-")
    return (cleaned or fallback)[:max_len]


def _active_stage() -> str:
    env_stage = str(os.environ.get("ATLASFORGE_ACTIVE_STAGE") or "").strip().upper()
    if env_stage in _VALID_ATLASFORGE_STAGES:
        return env_stage
    context = _stage_guard_context()
    context_stage = str(context.get("stage") or "").strip().upper()
    if context_stage in _VALID_ATLASFORGE_STAGES and _active_provider() == "codex":
        return context_stage
    raw = str(_current_mission().get("current_stage") or "PLANNING").strip().upper()
    if raw not in _VALID_ATLASFORGE_STAGES:
        return "PLANNING"
    return raw


def _resolve_stage(arguments: dict, allow_override: bool = False) -> str:
    active = _active_stage()
    requested = str(arguments.get("stage") or "").strip().upper()
    if not requested:
        return active
    if requested not in _VALID_ATLASFORGE_STAGES:
        raise ValueError(f"unsupported AtlasForge stage: {requested or '<empty>'}")
    if not allow_override and requested != active:
        raise ValueError(
            f"requested stage {requested} does not match active mission stage {active}"
        )
    return requested


def _resolve_mission_id(arguments: dict) -> str:
    mission_id = (
        arguments.get("mission_id")
        or os.environ.get("ATLASFORGE_ACTIVE_MISSION_ID")
        or (_stage_guard_context().get("mission_id") if _active_provider() == "codex" else None)
        or _current_mission().get("mission_id")
    )
    return _clean_identifier(mission_id, "manual")


def _stage_policy(stage: str) -> dict:
    return _STAGE_GUARD_POLICIES.get(stage) or _STAGE_GUARD_POLICIES["COMPLETE"]


def _policy_public_view(stage: str) -> dict:
    policy = _stage_policy(stage)
    return {
        "provider": _active_provider(),
        "stage": stage,
        "allowed_tools": sorted(policy["allowed_tools"]),
        "allowed_write_paths": list(policy["allowed_write_paths"]),
        "tool_write_paths": {
            tool: list(paths)
            for tool, paths in policy.get("tool_write_paths", {}).items()
        },
        "allowed_extensions": sorted(policy["allowed_extensions"]),
        "tool_allowed_extensions": {
            tool: sorted(extensions)
            for tool, extensions in policy.get("tool_allowed_extensions", {}).items()
        },
        "atlasforge_root": str(_atlasforge_root()),
    }


def _repo_relative_path(path: Path) -> str:
    root = _atlasforge_root()
    resolved = path.expanduser().resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        raise ValueError("mission workspace escapes ATLASFORGE_ROOT")


def _mission_workspace_relative(mission_id: str) -> str:
    mission = _current_mission()
    current_id = _clean_identifier(mission.get("mission_id"), "", max_len=80)
    if current_id == mission_id:
        for key in ("mission_workspace", "project_workspace", "workspace_dir"):
            raw = mission.get(key)
            if isinstance(raw, str) and raw.strip():
                candidate = Path(raw.strip()).expanduser()
                if not candidate.is_absolute():
                    candidate = _atlasforge_root() / candidate
                return _repo_relative_path(candidate)
    return f"workspace/{mission_id}"


def _normalize_repo_relative_path(raw_path: Any) -> str:
    if not isinstance(raw_path, str):
        raise ValueError("target_path must be a string")
    if not raw_path:
        raise ValueError("target_path is required")
    if raw_path != raw_path.strip():
        raise ValueError("target_path must not contain leading or trailing whitespace")
    if "://" in raw_path:
        raise ValueError("target_path must not be URL-schemed")
    if unquote(raw_path) != raw_path:
        raise ValueError("target_path must not contain percent-encoded characters")
    path = raw_path
    if len(path) > 260:
        raise ValueError("target_path is too long")
    if "\x00" in path or any(ord(ch) < 0x20 for ch in path):
        raise ValueError("target_path contains control characters")
    p = Path(path)
    if p.is_absolute():
        raise ValueError("target_path must be repo-relative")
    if ".." in p.parts or any(".." in part for part in p.parts):
        raise ValueError("target_path must not contain '..'")
    normalized = Path(*[part for part in p.parts if part not in ("", ".")]).as_posix()
    if not normalized:
        raise ValueError("target_path is empty after normalization")
    return normalized


def _path_under_root(rel_path: str) -> Path:
    root = _atlasforge_root()
    target = (root / rel_path).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError("target_path escapes ATLASFORGE_ROOT")
    return target


def _enforce_stage_artifact_policy(tool_name: str, stage: str, target_path: str) -> tuple[str, Path]:
    policy = _stage_policy(stage)
    if tool_name not in policy["allowed_tools"]:
        raise ValueError(f"{tool_name} is not allowed during {stage}")

    rel_path = _normalize_repo_relative_path(target_path)
    suffix = Path(rel_path).suffix.lower()
    allowed_extensions = policy.get("tool_allowed_extensions", {}).get(
        tool_name,
        policy["allowed_extensions"],
    )
    if suffix not in allowed_extensions:
        raise ValueError(f"{stage} cannot write files with extension {suffix or '<none>'}")
    tool_paths = policy.get("tool_write_paths", {}).get(tool_name)
    allowed_paths = tool_paths or policy["allowed_write_paths"]
    if not any(fnmatch(rel_path, pattern) for pattern in allowed_paths):
        raise ValueError(f"{stage} cannot write target_path {rel_path}")
    return rel_path, _path_under_root(rel_path)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _atomic_write_json(path: Path, payload: dict) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True)
    if len(text) > MAX_STAGE_ARTIFACT_CHARS:
        raise ValueError(f"artifact exceeds {MAX_STAGE_ARTIFACT_CHARS} chars")
    _atomic_write_text(path, text + "\n")


def _bounded_string(value: Any, field_name: str, max_len: int = MAX_STAGE_FIELD_CHARS) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    if len(text) > max_len:
        raise ValueError(f"{field_name} exceeds {max_len} chars")
    return text


def _stage_artifact_envelope(kind: str, stage: str, mission_id: str, payload: dict) -> dict:
    return {
        "kind": kind,
        "mission_id": mission_id,
        "stage": stage,
        "provider": _active_provider(),
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
    }


def _render_plan_markdown(plan: dict, mission_id: str) -> str:
    lines = [
        "# Implementation Plan",
        "",
        f"Mission: {mission_id}",
        f"Provider: {_active_provider()}",
        f"Submitted: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    scalar_fields = [
        ("understanding", "Understanding"),
        ("approach", "Approach"),
        ("approach_rationale", "Approach Rationale"),
        ("message_to_human", "Message"),
    ]
    for key, heading in scalar_fields:
        value = plan.get(key)
        if isinstance(value, str) and value.strip():
            lines.extend([f"## {heading}", "", value.strip(), ""])

    list_fields = [
        ("key_requirements", "Key Requirements"),
        ("assumptions", "Assumptions"),
        ("research_conducted", "Research Conducted"),
        ("sources_consulted", "Sources Consulted"),
        ("success_criteria", "Success Criteria"),
        ("estimated_files", "Estimated Files"),
    ]
    for key, heading in list_fields:
        values = plan.get(key)
        if isinstance(values, list) and values:
            lines.extend([f"## {heading}", ""])
            for item in values:
                lines.append(f"- {str(item).strip()}")
            lines.append("")

    steps = plan.get("steps")
    if isinstance(steps, list) and steps:
        lines.extend(["## Steps", ""])
        for idx, step in enumerate(steps, start=1):
            if isinstance(step, dict):
                desc = str(step.get("description") or step.get("step") or "").strip()
                files = step.get("files")
                suffix = ""
                if isinstance(files, list) and files:
                    suffix = " (" + ", ".join(str(f).strip() for f in files if str(f).strip()) + ")"
                lines.append(f"{idx}. {desc or 'Planned work'}{suffix}")
            else:
                lines.append(f"{idx}. {str(step).strip()}")
        lines.append("")

    lines.extend(["## Structured Plan", "", "```json", json.dumps(plan, indent=2, sort_keys=True), "```", ""])
    return "\n".join(lines)


def _default_stage_artifact_path(tool_name: str, stage: str, mission_id: str) -> str:
    if tool_name == "AtlasForgeSubmitPlan":
        return f"{_mission_workspace_relative(mission_id)}/artifacts/implementation_plan.md"
    if tool_name == "AtlasForgeSubmitReview":
        if stage in {"ANALYZING", "CYCLE_END", "REVIEW"}:
            return f"state/reviews/{mission_id}.json"
        return f"missions/{mission_id}/review/review.json"
    if tool_name == "AtlasForgeSubmitPatchSummary":
        if stage == "TESTING":
            return f"{_mission_workspace_relative(mission_id)}/artifacts/testing/summary.json"
        return f"state/build_reports/{mission_id}.json"
    directory = stage.lower()
    if tool_name == "AtlasForgeWriteStageNote" and stage == "PLANNING":
        return f"{_mission_workspace_relative(mission_id)}/research/research_findings.md"
    return f"missions/{mission_id}/{directory}/note.md"


def _handle_atlasforge_get_stage_policy(arguments: dict) -> str:
    stage = _resolve_stage(arguments, allow_override=True)
    return json.dumps(_policy_public_view(stage), indent=2, sort_keys=True)


def _handle_atlasforge_submit_json_artifact(
    tool_name: str,
    arguments: dict,
    payload_key: str,
    kind: str,
) -> str:
    payload = arguments.get(payload_key)
    if not isinstance(payload, dict):
        raise ValueError(f"{payload_key} must be an object")
    mission_id = _resolve_mission_id(arguments)
    stage = _resolve_stage(arguments)
    target = arguments.get("target_path") or _default_stage_artifact_path(tool_name, stage, mission_id)
    rel_path, abs_path = _enforce_stage_artifact_policy(tool_name, stage, target)
    if tool_name == "AtlasForgeSubmitPlan":
        _atomic_write_text(abs_path, _render_plan_markdown(payload, mission_id))
        return json.dumps({
            "ok": True,
            "path": rel_path,
            "stage": stage,
            "provider": _active_provider(),
            "message": "implementation plan accepted by AtlasForge MCP stage guard.",
        }, indent=2, sort_keys=True)
    envelope = _stage_artifact_envelope(kind, stage, mission_id, payload)
    _atomic_write_json(abs_path, envelope)
    return json.dumps({
        "ok": True,
        "path": rel_path,
        "stage": stage,
        "provider": _active_provider(),
        "message": f"{kind} accepted by AtlasForge MCP stage guard.",
    }, indent=2, sort_keys=True)


def _handle_atlasforge_write_stage_note(arguments: dict) -> str:
    content = _bounded_string(arguments.get("content"), "content")
    title = str(arguments.get("title") or "").strip()[:160]
    mission_id = _resolve_mission_id(arguments)
    stage = _resolve_stage(arguments)
    target = arguments.get("target_path") or _default_stage_artifact_path(
        "AtlasForgeWriteStageNote", stage, mission_id
    )
    rel_path, abs_path = _enforce_stage_artifact_policy("AtlasForgeWriteStageNote", stage, target)
    heading = f"# {title}\n\n" if title else ""
    metadata = (
        f"Stage: {stage}\n"
        f"Provider: {_active_provider()}\n"
        f"Submitted: {datetime.now(timezone.utc).isoformat()}\n\n"
    )
    text = heading + metadata + content.rstrip() + "\n"
    if len(text) > MAX_STAGE_ARTIFACT_CHARS:
        raise ValueError(f"artifact exceeds {MAX_STAGE_ARTIFACT_CHARS} chars")
    _atomic_write_text(abs_path, text)
    return json.dumps({
        "ok": True,
        "path": rel_path,
        "stage": stage,
        "provider": _active_provider(),
        "message": "stage note accepted by AtlasForge MCP stage guard.",
    }, indent=2, sort_keys=True)


def _handle_atlasforge_write_mutation_artifact(arguments: dict) -> str:
    content = _bounded_string(arguments.get("content"), "content", max_len=MAX_STAGE_ARTIFACT_CHARS)
    mission_id = _resolve_mission_id(arguments)
    stage = _resolve_stage(arguments)
    target = arguments.get("target_path")
    rel_path, abs_path = _enforce_stage_artifact_policy(
        "AtlasForgeWriteMutationArtifact",
        stage,
        target,
    )
    _atomic_write_text(abs_path, content.rstrip() + "\n")
    return json.dumps({
        "ok": True,
        "path": rel_path,
        "stage": stage,
        "provider": _active_provider(),
        "message": "mutation artifact accepted by AtlasForge MCP stage guard.",
        "mission_id": mission_id,
    }, indent=2, sort_keys=True)


def handle_tool_call(name: str, arguments: dict) -> str:
    if not isinstance(arguments, dict):
        return "Error: arguments must be an object"

    if name == "WebSearch":
        try:
            query = _require_query(arguments)
        except ValueError as exc:
            return f"Error: {exc}"
        allowed = arguments.get("allowed_domains") or []
        blocked = arguments.get("blocked_domains") or []

        # H13: type-check domain lists. A string passed as allowed_domains
        # (common LLM shape confusion with a single-element list) iterates
        # char-by-char — `"reddit.com"` becomes `['r','e','d',...]` — which
        # silently drops all real results. Raise explicitly.
        if not isinstance(allowed, list):
            return "Error: allowed_domains must be a list of strings"
        if not isinstance(blocked, list):
            return "Error: blocked_domains must be a list of strings"
        if not all(isinstance(d, str) for d in allowed):
            return "Error: allowed_domains must contain only strings"
        if not all(isinstance(d, str) for d in blocked):
            return "Error: blocked_domains must contain only strings"

        request_payload = {"query": query, "count": 10}
        data = _proxy_post("/search", request_payload)
        capture = _capture_webproxy_json_output(
            tool_name=name,
            endpoint="/search",
            request_payload=request_payload,
            response_payload=data,
        )
        if capture:
            data["_investigation_json_path"] = capture["artifact_json_path"]

        if allowed or blocked:
            filtered = []
            for r in data.get("results", []):
                host = _host_from_url(r.get("url", ""))
                # Drop results with empty/unparseable URLs regardless of
                # filter direction. An unparseable host can't be matched
                # against either an allow- or block-list safely.
                if not host:
                    continue
                if allowed and not any(_host_matches(host, d) for d in allowed):
                    continue
                if blocked and any(_host_matches(host, d) for d in blocked):
                    continue
                filtered.append(r)
            data["results"] = filtered

        return _format_search_results(data)

    elif name in ("WebFetch", "FetchURL"):
        try:
            url = _require_url(arguments)
        except ValueError as exc:
            return f"Error: {exc}"
        request_payload = {"url": url, "max_chars": MAX_MAX_CHARS}
        data = _proxy_post("/fetch", request_payload)
        capture = _capture_webproxy_json_output(
            tool_name=name,
            endpoint="/fetch",
            request_payload=request_payload,
            response_payload=data,
        )
        if capture:
            data["_investigation_json_path"] = capture["artifact_json_path"]
        return _format_fetch_results(data)

    elif name == "WebResearch":
        try:
            query = _require_query(arguments)
            count = _clamp_int(arguments.get("count"), 1, MAX_COUNT, 5)
            fetch_top_n = _clamp_int(arguments.get("fetch_top_n"), 0, MAX_COUNT, 3)
            max_chars = _clamp_int(arguments.get("max_chars"), 0, MAX_MAX_CHARS, 12000)
        except ValueError as exc:
            return f"Error: {exc}"
        request_payload = {
            "query": query,
            "count": count,
            "fetch_top_n": fetch_top_n,
            "max_chars": max_chars,
        }
        data = _proxy_post("/research", request_payload)
        capture = _capture_webproxy_json_output(
            tool_name=name,
            endpoint="/research",
            request_payload=request_payload,
            response_payload=data,
        )
        if capture:
            data["_investigation_json_path"] = capture["artifact_json_path"]
        lines = [_format_search_results(data)]
        for page in data.get("fetched", []):
            lines.append("---")
            if "error" in page:
                # WP-R1 D2: per-item failures name the real upstream status.
                lines.append(f"FETCH ERROR: {_describe_target_failure(page)}")
            else:
                lines.append(_format_fetch_results(page))
        return "\n".join(lines)

    elif name == "PaperFetch":
        try:
            url = _require_url(arguments)
            max_chars = _clamp_int(arguments.get("max_chars"), -1, MAX_MAX_CHARS, -1)
        except ValueError as exc:
            return f"Error: {exc}"
        request_payload = {"url": url, "max_chars": max_chars}
        data = _proxy_post("/paper/fetch", request_payload)
        capture = _capture_webproxy_json_output(
            tool_name=name,
            endpoint="/paper/fetch",
            request_payload=request_payload,
            response_payload=data,
        )
        if capture:
            data["_investigation_json_path"] = capture["artifact_json_path"]
        return _format_fetch_results(data)

    elif name == "ImageSearch":
        try:
            query = _require_query(arguments)
            count = _clamp_int(arguments.get("count"), 1, MAX_COUNT, 5)
            fetch_top_n = _clamp_int(arguments.get("fetch_top_n"), 0, MAX_COUNT, 0)
        except ValueError as exc:
            return f"Error: {exc}"
        request_payload = {
            "query": query,
            "count": count,
            "fetch_top_n": fetch_top_n,
            "safesearch": arguments.get("safesearch", "off"),
        }
        data = _proxy_post("/image_search", request_payload)
        capture = _capture_webproxy_json_output(
            tool_name=name,
            endpoint="/image_search",
            request_payload=request_payload,
            response_payload=data,
        )
        if capture:
            data["_investigation_json_path"] = capture["artifact_json_path"]
        lines = [f"Image search for: \"{query}\"", ""]
        if data.get("_investigation_json_path"):
            lines.append(f"Investigation JSON: {data.get('_investigation_json_path')}")
            lines.append("")
        for r in data.get("results", []):
            lines.append(f"**{r.get('title', '')}**")
            lines.append(f"Image: {r.get('url', '')}")
            lines.append(f"Source: {r.get('source_url', '')}")
            lines.append(f"Dimensions: {r.get('width', '?')}x{r.get('height', '?')}")
            lines.append("")
        if data.get("fetched_images"):
            lines.append("Downloaded images:")
            for img in data["fetched_images"]:
                if "error" in img:
                    # WP-R1 D2: name the real upstream status per image too.
                    lines.append(f"  FAILED: {_describe_target_failure(img)}")
                else:
                    lines.append(f"  Saved: {img.get('local_path', '')} ({img.get('byte_length', 0)} bytes)")
        return "\n".join(lines)

    elif name == "AtlasForgeGetStagePolicy":
        return _handle_atlasforge_get_stage_policy(arguments)

    elif name == "AtlasForgeSubmitPlan":
        return _handle_atlasforge_submit_json_artifact(
            "AtlasForgeSubmitPlan",
            arguments,
            payload_key="plan",
            kind="plan",
        )

    elif name == "AtlasForgeWriteStageNote":
        return _handle_atlasforge_write_stage_note(arguments)

    elif name == "AtlasForgeSubmitReview":
        return _handle_atlasforge_submit_json_artifact(
            "AtlasForgeSubmitReview",
            arguments,
            payload_key="review",
            kind="review",
        )

    elif name == "AtlasForgeSubmitPatchSummary":
        return _handle_atlasforge_submit_json_artifact(
            "AtlasForgeSubmitPatchSummary",
            arguments,
            payload_key="summary",
            kind="patch_summary",
        )

    elif name == "AtlasForgeWriteMutationArtifact":
        return _handle_atlasforge_write_mutation_artifact(arguments)

    raise ValueError(f"Unknown tool: {name}")


def _handle_tools_call(msg_id: Any, raw_params: Any, is_notification: bool) -> None:
    """Dispatch a tools/call message. Respond unless is_notification.

    `raw_params` is the parsed value of `msg["params"]` BEFORE the
    null-coercion applied in _handle_one_message. tools/call requires a
    params object (with `name` and optionally `arguments`), so a null or
    non-dict params is a protocol violation, not a silent zero.
    """
    if not isinstance(raw_params, dict):
        if not is_notification:
            _send(_make_error(msg_id, -32602, "Invalid params: must be an object"))
        return
    params = raw_params

    tool_name = params.get("name", "")
    tool_args = params.get("arguments")
    if tool_args is None:
        tool_args = {}
    if not isinstance(tool_args, dict):
        if not is_notification:
            _send(_make_error(msg_id, -32602, "Invalid params: arguments must be an object"))
        return

    def _err_result(text: str) -> dict:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [{"type": "text", "text": text}],
                "isError": True,
            },
        }

    try:
        text = handle_tool_call(tool_name, tool_args)
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    except json.JSONDecodeError:
        # H4: JSONDecodeError is a ValueError subclass, so without this
        # explicit catch it would fall through to `except ValueError` and
        # leak parser internals ("Expecting value: line 1 column 1") to
        # the LLM. Never useful, sometimes discloses intermediate state.
        _logger.exception("tools/call proxy JSON decode failed")
        resp = _err_result("Proxy returned non-JSON body")
    except ProxyTargetError as exc:
        # WP-R1 D2: the proxy worked; the TARGET failed. The message already
        # names the upstream status (or says there was no HTTP response at
        # all), so do not bury it under a generic "Proxy error" prefix.
        _logger.warning("tools/call target failure: %s", exc)
        resp = _err_result(_sanitize_error(str(exc)))
    except RuntimeError as exc:
        # H4: our own tagged runtime error from `_proxy_post` for the
        # non-JSON-body case. Safe to surface the short message.
        resp = _err_result(_sanitize_error(f"Proxy error: {exc}"))
    except requests.exceptions.ConnectionError as exc:
        # H1: CLAUDE.md contract — "If the proxy is down, subagents fail
        # loudly with a connection error rather than silently falling
        # back." An opaque "Tool error" violates that. Surface a specific,
        # actionable message while redacting the proxy host:port.
        _logger.warning("tools/call proxy connection error: %s", exc)
        resp = _err_result(
            _sanitize_error(
                "Proxy unreachable. Check "
                "`systemctl --user status atlasforge-web-proxy`."
            )
        )
    except requests.exceptions.Timeout:
        _logger.warning("tools/call proxy timeout")
        resp = _err_result(f"Proxy timed out after {TIMEOUT}s")
    except requests.exceptions.HTTPError as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "?")
        _logger.warning("tools/call proxy HTTP %s", status)
        resp = _err_result(f"Proxy returned HTTP {status}")
    except ValueError as exc:
        # ValueError is our controlled "bad input from client" signal
        # (unknown tool, missing url, scheme whitelist). Its message is safe
        # to surface because it's derived from validated paths, not from
        # upstream library internals. Still sanitize for safety.
        resp = _err_result(_sanitize_error(f"Error: {exc}"))
    except Exception:
        # Uncontrolled exceptions may embed internal details (e.g., the
        # proxy's internal host:port, library tracebacks). Do NOT surface
        # them into LLM-visible tool results; log to stderr and return a
        # generic message.
        _logger.exception("tools/call failed")
        resp = _err_result("Tool error (see web proxy server logs)")
    if not is_notification:
        _send(resp)


def _handle_one_message(line: str) -> None:
    """Process a single JSON-RPC frame. All outputs go through _send()."""
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        # MCP requires a response for every request; silently dropping
        # malformed input deadlocks clients on the request id. id is
        # unknown here, so null.
        _send(_make_error(None, -32700, "Parse error"))
        return

    if not isinstance(msg, dict):
        _send(_make_error(None, -32600, "Invalid Request"))
        return

    # Iter-6 H2: strict JSON-RPC 2.0 §4 — every request MUST include
    # `"jsonrpc": "2.0"`. Missing field, wrong type, or wrong value is an
    # Invalid Request. We compute a safe-to-echo id BEFORE rejecting (callers
    # benefit from id correlation when they use one). If id is not a legal
    # JSON-RPC id type (str|int|None), echo null per JSON-RPC convention.
    raw_id = msg.get("id") if "id" in msg else None
    safe_id = raw_id if isinstance(raw_id, (str, int, type(None))) else None
    if msg.get("jsonrpc") != "2.0":
        _send(_make_error(safe_id, -32600, "Invalid Request: jsonrpc must be '2.0'"))
        return

    msg_id = msg.get("id")
    method = msg.get("method", "")
    # Iter-6 H3: per JSON-RPC 2.0 §4.1, ONLY a missing "id" makes a message a
    # notification. Explicit `{"id": null}` is a (spec-discouraged but legal)
    # request, and the server MUST respond with the matching null id. The
    # earlier conflation deadlocked clients that sent `id: null`. The id-null
    # vs. parse-error-id-null collision concern is moot: clients correlate by
    # field presence on the response, and parse-error envelopes are emitted
    # only when the request itself is unparseable (no id to collide with).
    is_notification = "id" not in msg
    # Do NOT null-coerce params here; each method decides whether a null /
    # non-dict params is tolerable (e.g., ping) or a protocol violation
    # (e.g., tools/call, which requires a params object).
    raw_params = msg.get("params")

    if method == "initialize":
        if not is_notification:
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "atlasforge-web-proxy",
                        "version": "0.2.0",
                    },
                },
            })
        return

    if method == "notifications/initialized":
        # Standard MCP notification; no response.
        return

    if method == "tools/list":
        if not is_notification:
            _send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS},
            })
        return

    if method == "tools/call":
        _handle_tools_call(msg_id, raw_params, is_notification)
        return

    if method == "ping":
        if not is_notification:
            _send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
        return

    # Unknown method. Per JSON-RPC 2.0: respond with -32601 if it was a
    # request; silently ignore if it was a notification (no id).
    if not is_notification:
        _send(_make_error(msg_id, -32601, f"Method not found: {method}"))


def _iter_bounded_lines(stream, max_bytes: int):
    """Yield (is_oversize, payload) tuples per newline-terminated chunk.

    Iter-6 H9: previous API returned bare bytes and used `b""` for both an
    oversize-drop sentinel AND a legitimately empty line (`\\n`). main()
    treated every `b""` as oversize — so a blank keep-alive line spuriously
    produced `-32600 Request too large`. Switching to a tagged tuple kills
    the ambiguity permanently:

        - `(False, payload)` for a normal line (payload may be `b""` for an
          empty line, which the caller may safely ignore).
        - `(True, b"")` for an oversize-drop sentinel; payload is always
          empty since the actual oversize bytes are dropped without
          buffering.

    Iter-4 H9 (preserved): `for line in sys.stdin:` reads until `\\n` before
    yielding, which means a 2 GB payload without a newline is fully buffered
    before the MAX_LINE_LENGTH check runs. This iterator reads in 64 KiB
    chunks, tracks the running byte count, and on overflow yields the
    oversize tuple, then DROPS the remainder of the oversize line without
    buffering it.

    Backwards-compat: `_iter_bounded_lines` is private. The only external
    consumers are main() (updated below) and the iter-3 verification tests
    (updated to unpack the tuple).
    """
    buf = bytearray()
    oversize = False
    while True:
        chunk = stream.read1(65536) if hasattr(stream, "read1") else stream.read(65536)
        if not chunk:
            # EOF. If we had partial data, yield what we have (non-oversize
            # path). If we were in oversize-drain mode without hitting a
            # newline, just exit — caller will see stream end.
            if buf and not oversize:
                yield (False, bytes(buf))
            return
        nl = chunk.find(b"\n")
        if nl < 0:
            if oversize:
                # Still draining — keep consuming but don't buffer.
                continue
            buf.extend(chunk)
            if len(buf) > max_bytes:
                buf.clear()
                oversize = True
            continue
        # Newline found. Either this completes an in-progress line (normal
        # or oversize) and the remainder of `chunk` starts a new one.
        if oversize:
            # Oversize line ends here; emit oversize sentinel. Start a fresh
            # normal line from the tail.
            yield (True, b"")
            oversize = False
            tail = chunk[nl + 1:]
            if tail:
                # Feed the tail back through the normal accumulator.
                # Iter-5 CRIT-2: re-check size cap on each emitted piece.
                sub_nl = tail.find(b"\n")
                while sub_nl >= 0:
                    piece = bytes(buf) + bytes(tail[:sub_nl])
                    buf.clear()
                    if len(piece) > max_bytes:
                        yield (True, b"")
                    else:
                        yield (False, piece)
                    tail = tail[sub_nl + 1:]
                    sub_nl = tail.find(b"\n")
                buf.extend(tail)
                if len(buf) > max_bytes:
                    buf.clear()
                    oversize = True
            continue
        buf.extend(chunk[:nl])
        if len(buf) > max_bytes:
            buf.clear()
            # We've already consumed the newline — just drop this frame.
            yield (True, b"")
        else:
            yield (False, bytes(buf))
            buf.clear()
        # Handle any additional full lines in the same chunk.
        tail = chunk[nl + 1:]
        while True:
            sub_nl = tail.find(b"\n")
            if sub_nl < 0:
                buf.extend(tail)
                if len(buf) > max_bytes:
                    buf.clear()
                    oversize = True
                break
            piece = bytes(buf) + bytes(tail[:sub_nl])
            buf.clear()
            if len(piece) > max_bytes:
                yield (True, b"")
            else:
                yield (False, piece)
            tail = tail[sub_nl + 1:]


def main():
    stream = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
    for is_oversize, raw in _iter_bounded_lines(stream, MAX_LINE_LENGTH):
        # Iter-6 H9: tagged-tuple API disambiguates oversize-drop sentinel
        # from a legitimate blank line. Oversize → -32600. Blank line →
        # ignore silently (common in keep-alive / line-buffered protocols).
        if is_oversize:
            _logger.warning("stdin line exceeds %d bytes; dropped", MAX_LINE_LENGTH)
            try:
                _send(_make_error(None, -32600, "Request too large"))
            except Exception:
                pass
            continue
        try:
            line = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            try:
                _send(_make_error(None, -32700, "Parse error (invalid UTF-8)"))
            except Exception:
                pass
            continue
        if not line:
            # Blank or whitespace-only line — silently ignore (no spurious
            # -32600 since this is no longer ambiguous with the oversize
            # sentinel).
            continue
        try:
            _handle_one_message(line)
        except (KeyboardInterrupt, SystemExit, MemoryError, RecursionError):
            # Iter-3 H3: let fatal errors propagate so systemd can restart
            # the unit and reclaim memory. Catching these turns a one-shot
            # resource exhaustion into an infinite error loop, much worse.
            raise
        except Exception:
            # Last-resort guard: a handler bug must not terminate the server
            # subprocess, which would deadlock every subsequent client request.
            _logger.exception("fatal handler error; continuing")
            try:
                _send(_make_error(None, -32603, "Internal error"))
            except Exception:
                # stdout is broken — nothing we can do.
                pass


if __name__ == "__main__":
    main()
