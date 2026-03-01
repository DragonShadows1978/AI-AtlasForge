# AtlasForge Claude Code Hooks

Claude Code hooks are Python scripts that intercept tool calls before and after
they execute. AtlasForge uses them to enforce mission discipline, protect files,
and give Claude awareness of what it's doing.

These hooks are installed to `~/.claude/hooks/` and registered in
`~/.claude/settings.json` by `install.sh`. They are **not** active during normal
Claude Code sessions unless the AtlasForge conductor is running — most hooks
check for the conductor lock file and bypass enforcement when it's absent.

---

## Hooks in this directory

### `pre_tool_use.py` — Stage gate (primary layer)

**Fires on:** `PreToolUse` for `Write`, `Edit`, and `Bash`

Enforces AtlasForge's stage-gate system. During a mission, the conductor writes
the current stage to `atlasforge.lock`. This hook reads that stage and applies
write-path restrictions:

- `ANALYZING` stage: no file writes outside the workspace scratch area
- `PLANNING` stage: can write plans/artifacts, not source code
- `IMPLEMENTING` stage: full write access
- `TESTING`, `REVIEWING` stages: restricted again

If no conductor lock file exists (i.e., AtlasForge is not running), all
enforcement is bypassed — normal Claude Code sessions are unaffected.

### `stage_gate_hook.py` — Stage gate (secondary layer / defense in depth)

**Fires on:** `PreToolUse` for `Write`, `Edit`, and `Bash`

A second layer of stage-gate enforcement focused on write-path validation. Where
`pre_tool_use.py` enforces stage rules broadly, this hook validates that specific
file paths comply with per-stage restrictions. The two hooks complement each other:
`pre_tool_use.py` handles tool-level blocking, `stage_gate_hook.py` handles
path-level validation.

Also bypasses when conductor is not running.

### `backup-core-files.py` — Pre-edit backup of core files

**Fires on:** `PreToolUse` for `Write` and `Edit`

Automatically creates a timestamped backup of critical AtlasForge system files
before they are modified. This gives a recovery point before any edit — useful
when missions modify core infrastructure files.

Protected files are configured in `CORE_FILES` at the top of the script. After
installation, edit `~/.claude/hooks/backup-core-files.py` to add paths relevant
to your setup.

### `bash_write_guard.py` — Blocks heredoc/redirect file writes via Bash

**Fires on:** `PreToolUse` for `Bash`

Prevents bypassing the AfterImage pre-write hook (and churn tracking) by writing
files via Bash heredocs or output redirections, e.g.:

```bash
cat > file.py << 'EOF'    # blocked
echo "..." > file.py      # blocked
python3 -c "..." > file   # blocked
```

Claude must use the `Write` or `Edit` tools instead, which go through the full
hook chain and get stored in the knowledge base. Allows the command through on the
second attempt so intentional shell operations aren't permanently blocked.

### `bash_delete_guard.py` — Deny-then-allow with auto-backup for destructive deletes

**Fires on:** `PreToolUse` for `Bash`

Intercepts `rm` and `unlink` commands targeting consequential files (source code,
configs, docs). On the first attempt it:

1. Backs up the target(s) to `~/.afterimage/deleted_backups/<timestamp>/`
2. Denies the command with a message showing what was backed up

On the second attempt with the same command, it allows through — deletion is
then considered intentional. Recovery is always possible from the backup directory.

Automatically skips: `/tmp/`, `__pycache__`, `.pyc`, `node_modules`, `dist/`, etc.

---

## Third-party hook: AfterImage

`afterimage_hook.py` is **not** included here — it belongs to the
[AI-AfterImage](https://github.com/DragonShadows1978/AI-AfterImage) repository.

AfterImage provides episodic code memory: before Claude writes a file, it
surfaces similar past code from a PostgreSQL/SQLite knowledge base. After the
write, it stores the new code for future recall. It also tracks churn tiers
(Gold/Silver/Bronze/Red) and warns when stable files are being modified too
frequently.

To install AfterImage hooks:
```bash
git clone https://github.com/DragonShadows1978/AI-AfterImage.git
cd AI-AfterImage
./install.sh
```

AfterImage's installer registers its hook in `~/.claude/settings.json`
alongside the AtlasForge hooks.

---

## settings.json reference

After running `install.sh`, your `~/.claude/settings.json` hook section should
look like this:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {"type": "command", "command": "~/.claude/hooks/pre_tool_use.py"},
          {"type": "command", "command": "~/.claude/hooks/stage_gate_hook.py"},
          {"type": "command", "command": "~/.claude/hooks/backup-core-files.py"},
          {"type": "command", "command": "~/.claude/hooks/afterimage_hook.py"}
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "~/.claude/hooks/pre_tool_use.py"},
          {"type": "command", "command": "~/.claude/hooks/stage_gate_hook.py"},
          {"type": "command", "command": "~/.claude/hooks/bash_write_guard.py"},
          {"type": "command", "command": "~/.claude/hooks/bash_delete_guard.py"}
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {"type": "command", "command": "~/.claude/hooks/afterimage_hook.py"}
        ]
      }
    ]
  }
}
```

`afterimage_hook.py` entries are added by AfterImage's installer — they won't
appear until AfterImage is installed.

---

## Hook execution order matters

Hooks run in the order listed in `settings.json`. The intended order for
`Write|Edit` is:

1. `pre_tool_use.py` — stage gate (fast deny if wrong stage)
2. `stage_gate_hook.py` — path validation (defense in depth)
3. `backup-core-files.py` — create backup before write
4. `afterimage_hook.py` — surface past code, churn warnings, deny-then-allow

This means stage enforcement happens before AfterImage runs, so a denied write
in the wrong stage never touches the knowledge base.

---

## Hook protocol

All hooks read a JSON object from stdin:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "Write",
  "tool_input": {
    "file_path": "/path/to/file",
    "content": "..."
  }
}
```

To deny a tool call, a hook prints a JSON object to stdout:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "Human-readable reason shown to Claude"
  }
}
```

To allow, the hook exits silently (no stdout output, exit code 0).
