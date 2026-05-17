# AtlasForge — Agent Context

This file is loaded automatically by Claude Code when working in the AtlasForge repo.
It documents conventions and wiring that are not obvious from the directory layout.

## Web Research Routing

**All investigation and R&D subagents in this project route `WebSearch` and
`WebFetch` through the local AtlasForge web proxy (MCP).** The tool-call
interface is identical to Anthropic's built-ins; the content returned is
unfiltered and verbatim.

How it works:
- `WebProxy/cli.py` — `proxy_cli_args()` returns the CLI flags
  (`--mcp-config <path> --disallowedTools WebSearch,WebFetch`) that every
  `claude -p` spawn site threads into its argv. Public entry point:
  `from WebProxy import proxy_cli_args`.
- `WebProxy/configs/mcp.json` — MCP config that loads `WebProxy/mcp_server.py`.
  The MCP server advertises `WebSearch` and `WebFetch` tools under the same
  names as the built-ins. With the built-ins in `--disallowedTools`, calls
  to these names hit the MCP tools instead.
- `WebProxy/service.py` — the actual proxy (localhost:8765). Starts
  automatically alongside the dashboard (via `WebProxy.supervisor`), and
  can also run under the `atlasforge-web-proxy` systemd user unit. Exposes
  `/search /fetch /research /image_search /cache /stats /health`. Search
  auto-routing is Brave API -> DuckDuckGo HTML -> DDGS fallback backends.

Spawn sites that route through the proxy:
- `atlasforge_conductor.build_llm_command()` — R&D stage subagents
- `investigation_engine.py` — parallel research subagents
- `adversarial_testing/blind_agent_runner.py` — blind validators

Why:
- 22× more content per query vs. Anthropic's filtered backend
- Survives domain blocks (Reddit, adult domains, niche forums)
- Returns raw HTML, enabling verbatim source verification
- 24h fetch cache and 30-min search cache for repeatability

If the proxy is down, subagents fail loudly with a connection error rather
than silently falling back. The dashboard auto-starts the proxy on launch
(see `WebProxy/supervisor.py`); opt out with
`ATLASFORGE_DISABLE_PROXY_AUTOSTART=1` when running the systemd unit instead.
Check `systemctl --user status atlasforge-web-proxy` if investigations are
stalling on web calls.

## Validator Source Fetching

The investigation validator at `investigation_validator/source_fetcher.py`
tries the local web proxy first (via `POST /fetch`) before falling back to
httpx/requests/curl. This is how the validator reaches Reddit and other
filtered domains at the source-verification layer.

Override with `WEB_PROXY_URL` env var if the proxy is bound to a non-default
host/port.

## Dashboard Visibility

- `GET /api/web-proxy/stats` on `localhost:5000` returns live proxy counters
  (cached searches, cached fetches, provider breakdown). The widget that
  visualizes this is a follow-up; the data pipe is live.
