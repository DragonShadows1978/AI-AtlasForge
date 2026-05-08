# Local Web Proxy

A local HTTP service + thin MCP server that transparently replaces Claude
Code's built-in `WebSearch` / `WebFetch` for every subagent spawned by
AtlasForge. Returns unfiltered, verbatim content with caching.

## Why

- one local search/fetch service with **provider swapping** (Brave ↔ DuckDuckGo)
- **~22× more content per query** vs Claude's filtered built-ins
- **survives domain blocks** (Reddit, niche forums, etc.)
- caching, logging, and sanitization in one place
- returns raw HTML for source verification
- 24h fetch cache / 30m search cache

Flow:

`Claude Code subagent -> WebSearch/WebFetch MCP tool -> local HTTP proxy -> search provider / webpage`

## Files (paths relative to `$ATLASFORGE_ROOT`)

- `WebProxy/service.py` — the HTTP service
- `WebProxy/mcp_server.py` — the thin MCP server (advertises `WebSearch`/`WebFetch`)
- `WebProxy/client.py` — Python client for programmatic use
- `WebProxy/supervisor.py` — dashboard-side auto-start helper
- `.mcp.json` — project-level MCP config at repo root (auto-loaded by Claude Code)
- `WebProxy/configs/mcp.json` — explicit MCP config threaded through `--mcp-config`
- `WebProxy/scripts/web_search_cli.py` / `WebProxy/scripts/web_fetch_cli.py` — shell tool bridges
- `WebProxy/cli.py` — produces `--mcp-config … --disallowedTools …` CLI flags
- `WebProxy/stats.py` — dashboard-facing stats adapter

## Run

### As a systemd user service (recommended)

```bash
make proxy-start     # systemctl --user start atlasforge-web-proxy
make proxy-status    # unit status
make proxy-health    # curl /health
make proxy-logs      # follow journal
```

The user-level unit is installed by `./install.sh` when you accept the
systemd prompt. It runs under your user account so it inherits env
variables like `BRAVE_API_KEY`.

### Manually

```bash
cd "$ATLASFORGE_ROOT"
python3 -m WebProxy.service --host 127.0.0.1 --port 8765
```

Health:

```bash
curl http://127.0.0.1:8765/health
```

## Endpoints

### `POST /search`

```json
{
  "query": "state machine orchestration",
  "count": 5,
  "provider": "auto"
}
```

### `POST /fetch`

```json
{
  "url": "https://example.com/article",
  "max_chars": 12000
}
```

### `POST /research`

Combined search + fetch-top-N. Useful for "give me a research brief on X".

```json
{
  "query": "token budget orchestration",
  "count": 5,
  "fetch_top_n": 3,
  "provider": "auto",
  "max_chars": 12000
}
```

### `GET /stats`

Returns live counters consumed by the dashboard widget:

```json
{
  "searches_total": 142,
  "fetches_total": 831,
  "cached_searches": 37,
  "cached_fetches": 206,
  "provider_breakdown": {"brave": 105, "duckduckgo": 37}
}
```

### `GET /health`

```json
{"ok": true, "service": "atlasforge-web-proxy"}
```

## Search providers

`provider=auto`:

- uses **Brave** if `ATLASFORGE_BRAVE_API_KEY` or `BRAVE_API_KEY` is set
- otherwise falls back to **DuckDuckGo** HTML scraping

Set the key via `.env`, shell export, or systemd drop-in
(`systemctl --user edit atlasforge-web-proxy`).

## Thin MCP integration

The proxy ships with a **thin MCP server** (`WebProxy/mcp_server.py`) that
advertises tools named `WebSearch` and `WebFetch` — the same names as Claude
Code's built-ins. Two MCP configs register it:

- `.mcp.json` at repo root — Claude Code's **project-level** MCP file.
  Claude Code auto-loads it when launched from the repo root. No flags needed.
- `WebProxy/configs/mcp.json` — explicit config threaded through
  `--mcp-config` when AtlasForge spawns subagents.

Pairing either config with `--disallowedTools WebSearch,WebFetch` causes
Claude Code to reject the built-ins and fall through to the MCP tools with
the same names. The subagent's prompt and tool schemas are unchanged — the
redirection is entirely at the tool-registration layer.

`WebProxy.proxy_cli_args()` (re-exported from `WebProxy.cli`) returns the
exact CLI flags and is threaded into every `claude -p` spawn site:

- `atlasforge_conductor.build_llm_command()` — R&D stage subagents
- `investigation_engine.py` — parallel research subagents
- `adversarial_testing/blind_agent_runner.py` — blind validators

### Path portability

The MCP JSON files ship with the placeholder path
`/home/vader/AI-AtlasForge`. `install.sh` rewrites this to the actual
`$ATLASFORGE_ROOT` on every install (idempotent). If you relocate the
checkout, re-run `./install.sh` — or manually:

```bash
python3 "$ATLASFORGE_ROOT/WebProxy/install/rewrite_mcp_paths.py" "$ATLASFORGE_ROOT"
```

## Python client usage

```python
from WebProxy import search_web_via_proxy, fetch_web_via_proxy

results = search_web_via_proxy("AtlasForge token budget", count=5)
page = fetch_web_via_proxy(results["results"][0]["url"])
```

## Shell tool usage

Use the scripts as stdin/stdout tool entry points when you need a
non-MCP bridge (e.g. for Codex-style tools):

```bash
python3 "$ATLASFORGE_ROOT/WebProxy/scripts/web_search_cli.py" "state machine"
python3 "$ATLASFORGE_ROOT/WebProxy/scripts/web_fetch_cli.py" "https://example.com"
```

## Interactive Codex

Normal AtlasForge subprocesses inject the Codex MCP config automatically. For
manual interactive sessions, use the launcher:

```bash
/home/vader/AI-AtlasForge/WebProxy/scripts/codex_proxy_interactive.sh
```

It starts `codex` with:

```bash
-c 'mcp_servers.atlasforge-web-proxy.command="python3"'
-c 'mcp_servers.atlasforge-web-proxy.args=["/home/vader/AI-AtlasForge/WebProxy/mcp_server.py"]'
```

The launcher rejects native Codex `--search` unless
`ATLASFORGE_CODEX_WEB_SEARCH=1` is explicitly set. That keeps interactive
sessions proxy-first instead of falling back to the built-in Responses
`web_search` tool.

## Dashboard visibility

`GET http://localhost:5050/api/web-proxy/stats` on the dashboard mirrors
`GET /stats` on the proxy. The dashboard "Web Proxy" widget polls this
endpoint and displays cached-search / cached-fetch counters and provider
breakdown live.

## Override with `WEB_PROXY_URL`

Downstream consumers (e.g. the investigation validator at
`investigation_validator/source_fetcher.py`) check
`WEB_PROXY_URL` first and fall back to direct HTTP. Override if you're
running the proxy on a non-default host/port.
