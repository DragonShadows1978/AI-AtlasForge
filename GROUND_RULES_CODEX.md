# Codex Provider Overlay

These rules apply when the active provider is `codex` and are appended to the base `GROUND_RULES.md`.

## Identity Mapping
- When base rules mention "Claude", interpret that as the active Codex agent.
- Continue following all base autonomy, execution, and integration requirements.

## Execution Mode
- Run fully autonomously and non-interactively.
- Prefer Codex invocation in autonomous mode:
  - `codex --search exec --dangerously-bypass-approvals-and-sandbox -`
- Do not block waiting for approval/confirmation prompts.

## Web Research Requirement
- Treat web research as enabled capability when using Codex.
- For research stages, use live search-capable flows; do not silently downgrade to offline assumptions.

## Prompt/Response Discipline
- If a stage requires strict JSON output, return strict JSON only.
- Keep outputs concise and machine-parseable when schema is provided.

## Transcript Awareness
- Codex transcripts are stored in `~/.codex/sessions` as JSONL events.
- Preserve mission/workspace continuity assumptions using Codex session metadata.
