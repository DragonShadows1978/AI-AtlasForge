# Codex Provider Overlay

These rules apply when the active provider is `codex` and are appended to the base `GROUND_RULES.md`.

## Identity Mapping
- When base rules mention "Claude", interpret that as the active Codex agent.
- Continue following all base autonomy, execution, and integration requirements.

## Execution Mode
- Run fully autonomously and non-interactively.
- Prefer Codex invocation in autonomous mode:
- Native Codex `--search` is intentionally disabled in AtlasForge by default.
- AtlasForge registers the local WebProxy MCP server for Codex instead, then runs:
  - `codex -c mcp_servers.atlasforge-web-proxy.command="python3" -c mcp_servers.atlasforge-web-proxy.args='["/home/vader/AI-AtlasForge/WebProxy/mcp_server.py"]' exec --dangerously-bypass-approvals-and-sandbox -`
- For manual interactive sessions, prefer:
  - `/home/vader/AI-AtlasForge/WebProxy/scripts/codex_proxy_interactive.sh`
- Do not block waiting for approval/confirmation prompts.

## AtlasForge Process Safety
- Do not stop, restart, kill, or `pkill` AtlasForge services, including the
  dashboard, conductor, tray, WebProxy, or Codex parent process.
- Do not use broad process cleanup patterns such as `pkill -f dashboard_v2.py`,
  `pkill -f atlasforge`, or `killall` against shared service names.
- If you start a temporary test process, record its exact PID and clean up only
  that PID after verification.

## Web Research Requirement
- Treat web research as enabled through the AtlasForge WebProxy only.
- Do not use native Codex web search unless `ATLASFORGE_CODEX_WEB_SEARCH=1` is explicitly set.
- Prefer the MCP tools `WebSearch`, `WebFetch`, `WebResearch`, and `ImageSearch`.
- If MCP tools are unavailable, use the shell wrappers:
  - `python3 /home/vader/AI-AtlasForge/WebProxy/scripts/web_search_cli.py "query"`
  - `python3 /home/vader/AI-AtlasForge/WebProxy/scripts/web_fetch_cli.py "https://example.com"`
- If the proxy is down, fail loudly rather than silently downgrading to offline assumptions.

## Prompt/Response Discipline
- If a stage requires strict JSON output, return strict JSON only.
- Keep outputs concise and machine-parseable when schema is provided.

## Transcript Awareness
- Codex transcripts are stored in `~/.codex/sessions` as JSONL events.
- Preserve mission/workspace continuity assumptions using Codex session metadata.
