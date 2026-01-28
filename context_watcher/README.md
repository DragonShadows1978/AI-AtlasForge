# ContextWatcher

Real-time JSONL token monitor for AtlasForge Conductor. Monitors Claude's live transcript files and detects context exhaustion to trigger early handoffs.

## Overview

ContextWatcher solves a critical problem in long-running Claude sessions: **context window exhaustion**. When Claude approaches its context limit, the system becomes unresponsive and eventually times out, wasting significant time. ContextWatcher detects this condition early and triggers a graceful handoff.

## Features

### Token-Based Detection

Monitors Claude's JSONL transcript files for token usage patterns that indicate context exhaustion:

- **Graceful threshold (130K tokens)**: Triggers HANDOFF.md generation via Haiku
- **Emergency threshold (140K tokens)**: Immediate session termination

The detection logic identifies context exhaustion by the pattern:
- `cache_creation_input_tokens > 130K` AND `cache_read_input_tokens < 5K`

This pattern indicates Claude is building new context at the limit rather than reusing cached context.

### Time-Based Detection (NEW)

Proactively triggers handoff at 55 minutes to avoid the 1-hour timeout:

- Fires before the hard 60-minute limit
- Uses Haiku to generate an intelligent handoff summary
- Configurable via environment variables

## Installation

ContextWatcher is included with AtlasForge. No separate installation required.

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CONTEXT_WATCHER_ENABLED` | `1` | Enable/disable all context watching |
| `TIME_BASED_HANDOFF_ENABLED` | `1` | Enable/disable time-based handoff |
| `TIME_BASED_HANDOFF_MINUTES` | `55` | Minutes before triggering time-based handoff |

### Constants

| Constant | Value | Description |
|----------|-------|-------------|
| `GRACEFUL_THRESHOLD` | 130,000 | Tokens before graceful handoff |
| `EMERGENCY_THRESHOLD` | 140,000 | Tokens before emergency termination |
| `LOW_CACHE_READ_THRESHOLD` | 5,000 | Cache read threshold for exhaustion detection |

## Usage

### Basic Usage

```python
from context_watcher import get_context_watcher, HandoffSignal, HandoffLevel

def on_handoff(signal: HandoffSignal):
    if signal.level == HandoffLevel.GRACEFUL:
        print(f"Graceful handoff at {signal.tokens_used} tokens")
    elif signal.level == HandoffLevel.TIME_BASED:
        print(f"Time-based handoff at {signal.elapsed_minutes} minutes")
    elif signal.level == HandoffLevel.EMERGENCY:
        print(f"EMERGENCY handoff at {signal.tokens_used} tokens!")

watcher = get_context_watcher()
session_id = watcher.start_watching(
    workspace_path="/path/to/workspace",
    callback=on_handoff,
    enable_time_handoff=True
)

# Later, when done:
watcher.stop_watching(session_id)
```

### With AtlasForge Conductor

ContextWatcher is automatically integrated with `atlasforge_conductor.py`. No manual setup required.

### Convenience Functions

```python
from context_watcher import (
    start_context_watching,
    stop_context_watching,
    stop_all_context_watching
)

# Start watching
session_id = start_context_watching("/path/to/workspace", callback)

# Stop specific session
stop_context_watching(session_id)

# Stop all watching
stop_all_context_watching()
```

## Architecture

```
ContextWatcher (singleton)
    |
    ├── SessionMonitor 1 → workspace-A/*.jsonl
    │   └── TimeBasedHandoffMonitor (optional)
    |
    ├── SessionMonitor 2 → workspace-B/*.jsonl
    │   └── TimeBasedHandoffMonitor (optional)
    |
    └── ... (dynamic scaling)
```

### Components

- **ContextWatcher**: Singleton manager for all session monitors
- **SessionMonitor**: Per-workspace token tracking and threshold detection
- **TimeBasedHandoffMonitor**: Timer-based handoff trigger using threading.Event
- **HandoffSignal**: Data class for handoff event information

## Handoff Levels

| Level | Trigger | Action |
|-------|---------|--------|
| `GRACEFUL` | 130K tokens + low cache read | Write HANDOFF.md, let Claude finish |
| `TIME_BASED` | 55 minutes elapsed | Write HANDOFF.md, let Claude finish |
| `EMERGENCY` | 140K tokens | Immediate termination |

## Metrics

ContextWatcher tracks detailed metrics:

```python
watcher = get_context_watcher()
metrics = watcher.get_metrics_dict()

# Returns:
{
    "sessions": {"started": 5, "completed": 4, "active": 1},
    "handoffs": {
        "total": 3,
        "graceful": 2,
        "emergency": 0,
        "time_based": 1,
        "ratio": "2:0:1"
    },
    "timing": {
        "avg_detection_latency_s": 0.015,
        "max_detection_latency_s": 0.032
    },
    "tokens": {
        "peak_seen": 135000,
        "handoff_values": [130500, 132000, 0]
    }
}
```

## HANDOFF.md Format

When a handoff is triggered, a section is appended to `HANDOFF.md`:

```markdown
## Handoff #1 - 2026-01-28 04:30:00
**Mission:** mission_abc123
**Stage:** BUILDING

**Working on:** Implementing feature X
**Completed:** Database schema, API endpoints
**In progress:** Frontend components
**Next:** Wire up React components to API
**Decisions:** Using React Query for data fetching

**Elapsed time:** 55.0 minutes
**Handoff reason:** Time-based handoff at 55 minutes (proactive, before 1-hour timeout)

---
```

## Testing

Run the test suite:

```bash
cd /home/vader/AI-AtlasForge
python -m pytest context_watcher/tests/ -v
```

Run self-test:

```bash
python context_watcher/context_watcher.py
```

## Troubleshooting

### ContextWatcher not finding transcript directory

The transcript directory is derived from the workspace path. Ensure:
1. Workspace path is absolute
2. Claude has created transcripts in `~/.claude/projects/`

### Time-based handoff not firing

Check:
1. `TIME_BASED_HANDOFF_ENABLED` is `1` or `true`
2. Session is running long enough (default 55 minutes)
3. No token-based handoff triggered first

### Watchdog not working

If `watchdog` package is not installed, ContextWatcher falls back to polling. Install watchdog for better performance:

```bash
pip install watchdog
```

## License

MIT License - see AtlasForge LICENSE for details.
