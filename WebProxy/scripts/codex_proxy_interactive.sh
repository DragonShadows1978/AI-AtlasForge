#!/usr/bin/env bash
# Launch interactive Codex with AtlasForge WebProxy MCP enabled.
#
# Native Codex web search is enabled by the top-level --search flag. This
# launcher rejects that flag unless ATLASFORGE_CODEX_WEB_SEARCH=1 is explicit,
# keeping interactive sessions on the proxy path by default.
set -euo pipefail

for arg in "$@"; do
    if [[ "$arg" == "--search" && "${ATLASFORGE_CODEX_WEB_SEARCH:-0}" != "1" ]]; then
        echo "Refusing native Codex --search. Use WebProxy MCP tools, or set ATLASFORGE_CODEX_WEB_SEARCH=1 to override." >&2
        exit 2
    fi
done

atlasforge_root="${ATLASFORGE_ROOT:-/home/vader/AI-AtlasForge}"
mcp_server="${atlasforge_root}/WebProxy/mcp_server.py"

if [[ ! -f "$mcp_server" ]]; then
    echo "AtlasForge WebProxy MCP server not found: $mcp_server" >&2
    exit 1
fi

exec codex \
    -c 'mcp_servers.atlasforge-web-proxy.command="python3"' \
    -c "mcp_servers.atlasforge-web-proxy.args=[\"${mcp_server}\"]" \
    "$@"
