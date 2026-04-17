"""AtlasForge local web proxy + thin MCP package.

Public API surface re-exported for convenience. External callers can say
`from WebProxy import proxy_cli_args` instead of digging into submodules.

Submodules:
    service     — Flask HTTP proxy (localhost:8765)
    mcp_server  — JSON-RPC thin MCP server (stdin/stdout)
    client      — programmatic HTTP client for the proxy
    cli         — builds `--mcp-config ... --disallowedTools ...` argv
    stats       — dashboard-facing /stats fetcher
    supervisor  — dashboard-side auto-start helper
"""

from __future__ import annotations

from .cli import proxy_cli_args
from .client import (
    fetch_web_via_proxy,
    proxy_health,
    research_via_proxy,
    search_web_via_proxy,
)
from .stats import (
    ProxyStatsError,
    get_proxy_stats,
    get_proxy_stats_safe,
    is_proxy_alive,
)

__all__ = [
    "proxy_cli_args",
    "search_web_via_proxy",
    "fetch_web_via_proxy",
    "research_via_proxy",
    "proxy_health",
    "get_proxy_stats",
    "get_proxy_stats_safe",
    "is_proxy_alive",
    "ProxyStatsError",
]
