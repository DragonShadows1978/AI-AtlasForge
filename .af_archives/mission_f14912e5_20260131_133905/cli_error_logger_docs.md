# CLI Error Logger - Operator Documentation

## Overview

The CLI Error Logger provides structured, decade-scale auditable logging of all CLI interactions
that result in errors, anomalies, or significant events (handoffs, recoveries).

**Log Format:** JSONL (one JSON object per line)
**Location:** `$ATLASFORGE_ROOT/logs/cli_errors.jsonl`

## Event Types

| Event Type | Description | Resolution |
|------------|-------------|------------|
| `empty_response` | rc=0 but empty stdout (not an error) | `continue` |
| `cli_error` | Retriable CLI error | `retry` |
| `blocking_error` | Non-retriable error, halts mission | `halt` |
| `handoff` | Graceful context handoff | `handoff` |
| `timeout` | CLI command timeout | `retry` |
| `recovery` | Successful recovery from error | `recovered` |

## Log Entry Structure

Each log entry contains:

```json
{
  "timestamp": "2026-01-31T13:30:00.123456",
  "type": "cli_error",
  "mission_id": "mission_abc123",
  "stage": "BUILDING",
  "cycle": 2,
  "return_code": 1,
  "error_category": "rate_limited",
  "error_info": "API rate limit exceeded...",
  "response_length": 0,
  "stderr_snippet": "Error: Rate limited",
  "resolution": "retry",
  "retry_count": 1,
  "extra": {}
}
```

## Query Examples with jq

### Basic Queries

```bash
# All empty responses (trend analysis - are these increasing?)
jq 'select(.type == "empty_response")' logs/cli_errors.jsonl

# All blocking errors (critical - need investigation)
jq 'select(.type == "blocking_error")' logs/cli_errors.jsonl

# Events for a specific mission
jq 'select(.mission_id == "mission_xyz")' logs/cli_errors.jsonl

# Events during BUILDING stage
jq 'select(.stage == "BUILDING")' logs/cli_errors.jsonl
```

### Trend Analysis

```bash
# Count of errors per day
jq -r '.timestamp[:10]' logs/cli_errors.jsonl | sort | uniq -c

# Error types distribution
jq -r '.type' logs/cli_errors.jsonl | sort | uniq -c | sort -rn

# Errors per mission (find problematic missions)
jq -r '.mission_id' logs/cli_errors.jsonl | sort | uniq -c | sort -rn

# Resolution outcomes
jq -r '.resolution' logs/cli_errors.jsonl | sort | uniq -c
```

### Advanced Analysis

```bash
# Missions with most retries (potential instability)
jq -r 'select(.retry_count > 0) | .mission_id' logs/cli_errors.jsonl | sort | uniq -c | sort -rn

# Handoffs per mission (context usage patterns)
jq 'select(.type == "handoff")' logs/cli_errors.jsonl | jq -r '.mission_id' | sort | uniq -c

# Blocking errors by category (what's causing halts?)
jq 'select(.type == "blocking_error") | .error_category' logs/cli_errors.jsonl | sort | uniq -c

# Time between events (detect burst patterns)
jq -r '.timestamp' logs/cli_errors.jsonl | head -20

# Events with extra context
jq 'select(.extra != null and .extra != {})' logs/cli_errors.jsonl
```

### Real-Time Monitoring

```bash
# Watch for new errors in real-time
tail -f logs/cli_errors.jsonl | jq '.'

# Alert on blocking errors
tail -f logs/cli_errors.jsonl | jq 'select(.type == "blocking_error")'

# Count events in last hour
jq --arg cutoff "$(date -d '1 hour ago' -Iseconds)" \
   'select(.timestamp > $cutoff)' logs/cli_errors.jsonl | wc -l
```

## Integration Points

The CLI Error Logger is integrated at these points in `atlasforge_conductor.py`:

1. **Empty Response Handler** (line ~1105)
   - Logs `empty_response` events when rc=0 but stdout is empty
   - Resolution: `continue` (not treated as error)

2. **Retriable Error Handler** (line ~1205)
   - Logs `cli_error` events for errors that can be retried
   - Resolution: `retry` or `halt` based on retry count

3. **Blocking Error Handler** (line ~1190)
   - Logs `blocking_error` events for non-retriable errors
   - Resolution: `halt`

4. **Handoff Handler** (line ~1170)
   - Logs `handoff` events for graceful context handoffs
   - Resolution: `handoff`

## Log Rotation

The JSONL format supports standard log rotation tools:

```bash
# Rotate with logrotate (add to /etc/logrotate.d/atlasforge)
/home/vader/AI-AtlasForge/logs/cli_errors.jsonl {
    weekly
    rotate 52
    compress
    delaycompress
    missingok
    notifempty
}

# Manual rotation
mv logs/cli_errors.jsonl logs/cli_errors.$(date +%Y%m%d).jsonl
```

## Archival and Retention

For decade-scale retention:

1. **Weekly archives**: Compress weekly rotated files
2. **Monthly summaries**: Generate aggregated statistics monthly
3. **Yearly archives**: Move to cold storage

Example monthly summary script:

```bash
#!/bin/bash
# Generate monthly summary
MONTH=$(date -d "last month" +%Y-%m)
jq "select(.timestamp | startswith(\"$MONTH\"))" logs/cli_errors.jsonl > "archives/$MONTH.jsonl"
gzip "archives/$MONTH.jsonl"

# Generate statistics
echo "=== $MONTH Summary ===" > "archives/$MONTH.summary"
echo "Total events: $(zcat archives/$MONTH.jsonl.gz | wc -l)" >> "archives/$MONTH.summary"
echo "By type:" >> "archives/$MONTH.summary"
zcat "archives/$MONTH.jsonl.gz" | jq -r '.type' | sort | uniq -c >> "archives/$MONTH.summary"
```

## Troubleshooting

### Log file not being created
- Check `$ATLASFORGE_ROOT` environment variable
- Verify `logs/` directory exists and is writable
- Check for import errors in atlasforge_conductor logs

### Events not being logged
- Verify the cli_error_logger module is importable
- Check for exceptions in the try/except blocks
- Enable debug logging to see import failures

### Log file too large
- Implement log rotation (see above)
- Consider filtering by event type before archival
- Use compression for archived logs
