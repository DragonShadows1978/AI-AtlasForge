"""Web proxy CLI argument builders for AtlasForge subagent spawns.

Every `claude -p` subagent AtlasForge spawns should route WebSearch/WebFetch
calls through the local AtlasForge web proxy rather than Anthropic's filtered
built-ins. This module is the single source of truth for the CLI flags that
enable that routing.

Mechanism:
    `--mcp-config /path/to/WebProxy/configs/mcp.json` loads the proxy's MCP server,
    which advertises WebSearch and WebFetch tools under the same names as the
    built-ins. `--disallowedTools WebSearch,WebFetch` then blocks ONLY the
    built-in tools; the MCP-provided tools with the same names remain callable.

    This was verified empirically on 2026-04-15: a one-shot `claude -p`
    invocation with these flags issued a WebSearch that incremented the
    proxy's `cached_searches` counter, proving the MCP tool was invoked.

Usage from spawn sites:
    from WebProxy import proxy_cli_args
    cmd = ["claude", "-p", "--dangerously-skip-permissions"]
    cmd.extend(proxy_cli_args(existing_disallowed_csv))

For Codex, AtlasForge disables native `--search` by default and registers the
same MCP server through `-c mcp_servers...` overrides. Codex has no
Claude-style `--disallowedTools WebSearch,WebFetch` flag, so proxy-only search
depends on NOT enabling native `--search`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# Repo root is the directory containing WebProxy/. The MCP config lives at
# WebProxy/configs/mcp.json.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REPO_MCP_CONFIG = _REPO_ROOT / "WebProxy" / "configs" / "mcp.json"

# Built-in tools whose calls must be redirected to the MCP proxy.
_REDIRECTED_TOOLS = frozenset({"WebSearch", "WebFetch"})


def _resolve_mcp_config_path() -> str:
    """Resolve the MCP config path at call-time.

    Priority: ATLASFORGE_MCP_CONFIG env override -> repo-relative WebProxy/configs/mcp.json.
    Computing at call-time (not import-time) means a single install supports
    multiple checkouts and env-var overrides without re-importing the module.
    """
    override = os.environ.get("ATLASFORGE_MCP_CONFIG")
    if override:
        return override
    return str(_REPO_MCP_CONFIG)


def proxy_cli_args(disallowed_base: str = "") -> list[str]:
    """Return the CLI args needed to route WebSearch/WebFetch through the proxy.

    Args:
        disallowed_base: Existing comma-separated disallowed tool list from the
            stage guard. WebSearch/WebFetch are appended if missing.

    Returns:
        A list of CLI args:
            ["--mcp-config", <path>, "--disallowedTools", <combined_csv>]

        Safe to splice into a `claude -p` argv.

    Raises:
        FileNotFoundError: if the resolved MCP config does not exist. The caller
            is expected to fail loudly rather than silently fall back to the
            built-in (filtered) WebSearch/WebFetch tools.
    """
    mcp_path = _resolve_mcp_config_path()
    if not Path(mcp_path).is_file():
        raise FileNotFoundError(
            f"AtlasForge MCP config not found at {mcp_path}. "
            "Either install the proxy via ./install.sh or set "
            "ATLASFORGE_MCP_CONFIG to a valid path."
        )
    current = {t.strip() for t in disallowed_base.split(",") if t.strip()}
    combined = sorted(current | _REDIRECTED_TOOLS)
    return ["--mcp-config", mcp_path, "--disallowedTools", ",".join(combined)]


def codex_proxy_cli_args(server_name: str = "atlasforge-web-proxy") -> list[str]:
    """Return Codex CLI config overrides that load the AtlasForge MCP proxy.

    These args must appear before `exec`:
        codex -c mcp_servers.atlasforge-web-proxy.command="python3" \
              -c mcp_servers.atlasforge-web-proxy.args='[".../mcp_server.py"]' \
              exec ...

    Codex native web search is controlled separately by the top-level
    `--search` flag. AtlasForge intentionally omits that flag by default so
    web access is proxy-only.
    """
    mcp_config = Path(_resolve_mcp_config_path())
    if not mcp_config.is_file():
        raise FileNotFoundError(
            f"AtlasForge MCP config not found at {mcp_config}. "
            "Either install the proxy via ./install.sh or set "
            "ATLASFORGE_MCP_CONFIG to a valid path."
        )

    # Reuse the configured MCP script path instead of assuming repo layout.
    import json as _json
    data = _json.loads(mcp_config.read_text(encoding="utf-8"))
    server = (data.get("mcpServers") or {}).get(server_name)
    if not isinstance(server, dict):
        raise ValueError(f"MCP config {mcp_config} is missing server {server_name!r}")
    command = server.get("command")
    args = server.get("args") or []
    if not command or not isinstance(args, list):
        raise ValueError(f"MCP server {server_name!r} in {mcp_config} has invalid command/args")

    return [
        "-c", f"mcp_servers.{server_name}.command={json.dumps(command)}",
        "-c", f"mcp_servers.{server_name}.args={json.dumps(args)}",
    ]


# Lazy backwards-compat attribute — some tests/modules read MCP_CONFIG_PATH directly.
def __getattr__(name: str) -> str:
    if name == "MCP_CONFIG_PATH":
        return _resolve_mcp_config_path()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
