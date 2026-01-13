# AI-AtlasForge Usage Guide

This guide explains how to use AI-AtlasForge for autonomous AI research and development.

## Quick Start

```bash
# 1. Start the dashboard
python3 dashboard_v2.py

# 2. Open http://localhost:5050

# 3. Create a mission via the dashboard

# 4. Start the autonomous agent
python3 claude_autonomous.py --mode=rd
```

## Core Concepts

### Missions

A **mission** is a self-contained task for the autonomous agent to complete. Missions have:

- **Problem Statement**: What you want accomplished
- **Cycle Budget**: How many iterations the agent can use (1-10)
- **Workspace**: Isolated directory for mission files

### Stages

Each mission progresses through stages:

| Stage | Purpose | Agent Actions |
|-------|---------|---------------|
| **PLANNING** | Understand the task | Read files, research, create plan |
| **BUILDING** | Implement solution | Write code, create files |
| **TESTING** | Verify implementation | Run tests, adversarial testing |
| **ANALYZING** | Evaluate results | Identify issues, assess quality |
| **CYCLE_END** | Document progress | Generate reports |
| **COMPLETE** | Mission finished | Read-only |

### Cycles

Missions can iterate through multiple **cycles**. Each cycle:
1. Plans improvements based on previous results
2. Implements changes
3. Tests the implementation
4. Analyzes outcomes

The cycle budget determines how many attempts the agent gets.

## Dashboard

### Mission Panel

Shows current mission status:
- Current stage
- Cycle progress (e.g., "Cycle 2 of 3")
- Elapsed time

### Activity Feed

Real-time log of agent actions:
- File reads/writes
- Tool invocations
- Stage transitions

### Knowledge Base Widget

Browse accumulated knowledge from past missions:
- Techniques discovered
- Insights gained
- Gotchas to avoid

### Analytics Widget

Track resource usage:
- Token consumption
- Estimated costs
- Stage timing

## Creating Missions

### Via Dashboard (Recommended)

1. Click **"Create Mission"**
2. Enter your problem statement
3. Set the cycle budget
4. Click **"Create"**

### Via JSON

Create `state/mission.json`:

```json
{
  "mission_id": "my_mission_001",
  "problem_statement": "Create a Python script that...",
  "cycle_budget": 3,
  "success_criteria": [
    "Script runs without errors",
    "Output matches expected format"
  ]
}
```

## Running Missions

### Basic Execution

```bash
python3 claude_autonomous.py --mode=rd
```

This runs until the mission completes or errors.

### With Dashboard Monitoring

Run in separate terminals:

```bash
# Terminal 1: Dashboard
python3 dashboard_v2.py

# Terminal 2: Agent
python3 claude_autonomous.py --mode=rd
```

### Background Execution

For long-running missions:

```bash
nohup python3 claude_autonomous.py --mode=rd > mission.log 2>&1 &
```

## Mission Queue

Queue multiple missions to run sequentially:

### Via Dashboard

1. Create missions normally
2. Use "Queue" section to order them
3. Missions run automatically in order

### Via JSON

Create `state/mission_queue.json`:

```json
[
  {"mission_id": "mission_001", "priority": 1},
  {"mission_id": "mission_002", "priority": 2}
]
```

## Knowledge Base

The knowledge base accumulates learnings across all missions.

### Viewing Learnings

Dashboard → Knowledge Base widget

Or via API:
```bash
curl http://localhost:5050/api/knowledge-base/learnings
```

### Searching

```bash
curl "http://localhost:5050/api/knowledge-base/search?q=python+async"
```

### How It Works

After each mission:
1. Techniques used are extracted
2. Insights are identified
3. Gotchas are recorded

During PLANNING stage:
1. KB is queried for relevant learnings
2. Similar past missions are retrieved
3. Context is injected into the prompt

## Adversarial Testing

AI-AtlasForge includes adversarial testing to validate implementations:

### Red Team Analysis

Fresh Claude instances (no implementation knowledge) try to break the code.

### Property Testing

Edge cases are generated automatically:
- Empty inputs
- Boundary values
- Large inputs

### Mutation Testing

Code is mutated to verify tests catch bugs.

### Blind Validation

Implementation is compared against original specification.

## Recovery

### Crash Recovery

If the agent crashes mid-mission:
1. State is preserved in `state/mission.json`
2. Checkpoints exist for each stage
3. Agent can resume from last checkpoint

### Manual Recovery

Via dashboard:
1. Open Recovery modal
2. Select checkpoint to restore
3. Click "Restore"

Via JSON:
- Edit `state/mission.json` to set desired stage
- Restart agent

## Directory Structure

```
workspace/
├── artifacts/          # Plans, reports
│   └── implementation_plan.md
├── research/           # Notes, findings
└── tests/              # Test scripts

missions/
├── mission_001/        # Per-mission workspace
│   ├── workspace/
│   └── artifacts/
└── mission_logs/       # Completed mission reports
```

## Best Practices

### Writing Good Problem Statements

**Good:**
```
Create a Python script that:
1. Reads a CSV file of user data
2. Validates email format
3. Outputs invalid rows to a separate file

Success criteria:
- Handles files up to 1GB
- Reports progress every 10%
- Runs in under 30 seconds
```

**Bad:**
```
Make a CSV thing that checks emails
```

### Cycle Budget Guidelines

| Task Complexity | Recommended Cycles |
|----------------|-------------------|
| Simple script | 1-2 |
| Feature addition | 2-3 |
| Bug fix | 1-2 |
| Complex feature | 3-5 |
| Architecture change | 5-10 |

### Monitoring Long Missions

1. Use the dashboard for real-time updates
2. Check `logs/` for detailed logs
3. Set up notifications via `queue_notifications.py`

## CLI Reference

### claude_autonomous.py

```bash
python3 claude_autonomous.py [options]

Options:
  --mode=rd          Run in R&D mode (default)
  --mission=ID       Run specific mission
  --dry-run          Parse mission without executing
  --verbose          Enable verbose logging
```

### dashboard_v2.py

```bash
python3 dashboard_v2.py [options]

Environment:
  ATLASFORGE_PORT    Dashboard port (default: 5050)
  ATLASFORGE_DEBUG   Enable debug mode
```

## API Reference

### Mission Status

```bash
GET /api/status
```

### Create Mission

```bash
POST /api/mission/create
Content-Type: application/json

{
  "problem_statement": "...",
  "cycle_budget": 3
}
```

### Mission Control

```bash
POST /api/mission/start
POST /api/mission/stop
POST /api/mission/advance   # Manual stage advance
```

### Knowledge Base

```bash
GET /api/knowledge-base/learnings
GET /api/knowledge-base/search?q=query
```

### Analytics

```bash
GET /api/analytics/summary
GET /api/analytics/current
```

## Troubleshooting

### Agent Stuck in Stage

Check logs for errors:
```bash
tail -f logs/claude_autonomous.log
```

Manual advance via dashboard or API.

### Memory Issues

For large missions:
- Increase system RAM
- Reduce cycle budget
- Split into smaller missions

### Rate Limiting

If hitting API limits:
- Add delays between requests
- Use mission queue with gaps
- Consider API tier upgrade

## Next Steps

- Read ARCHITECTURE.md for system internals
- Browse missions/mission_logs/ for examples
- Experiment with small missions first
