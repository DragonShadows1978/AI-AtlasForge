# Email Dead Letter Queue (DLQ) Operator Guide

## Overview

The Email Dead Letter Queue (DLQ) system ensures that failed email deliveries are not silently lost. When an email fails to send (SMTP timeout, connection refused, invalid recipient, etc.), it is automatically queued for later retry with exponential backoff.

## Accessing the DLQ Dashboard

1. Open the AtlasForge Dashboard at `http://localhost:5000`
2. Click the **Email Monitor** tab
3. Scroll down to the **Dead Letter Queue** section

## Understanding the DLQ Status

### Statistics Display

| Metric | Description |
|--------|-------------|
| **Pending** | Emails waiting for retry |
| **Failed** | Emails that exceeded max retry attempts |
| **Completed** | Successfully sent after retry |
| **Active Total** | Sum of Pending + Retrying |
| **Next Retry** | When the next pending email will be retried |
| **Events (24h)** | Number of DLQ events in last 24 hours |

### Email Status Values

| Status | Meaning |
|--------|---------|
| `pending` | Queued, waiting for next retry attempt |
| `retrying` | Currently being retried |
| `completed` | Successfully sent after retry |
| `failed` | Max retries exceeded, requires manual action |
| `deleted` | Removed from queue by operator |

## Common Operations

### Viewing Queued Emails

The DLQ email list shows all queued emails with:
- Status badge (color-coded)
- Email type (acknowledgement, rejection, completion)
- Recipient address
- Subject line
- Failure reason
- Retry count (e.g., "Retry 3/10")
- Timestamps (first failed, next retry)

### Retrying a Single Email

1. Find the email in the list
2. Click the **Retry Now** button
3. The email will be queued for immediate retry

### Retrying All Pending Emails

1. Click **Retry All Pending** button in the DLQ section
2. Confirm the action
3. All pending emails will be queued for immediate retry

### Deleting an Email

1. Find the email in the list
2. Click the **Delete** button
3. Confirm the deletion
4. The email will be marked as deleted and removed from the active queue

### Clearing All Failed Emails

1. Click **Clear Failed** button
2. Confirm the action
3. All permanently failed emails will be deleted

### Viewing Email Details

1. Click the **Details** button on any email
2. A modal will show:
   - Full email metadata
   - Complete failure reason
   - Event history (all retry attempts)
   - Attachment information (if any)

## DLQ Worker Management

### Worker Status

- **Running**: Worker is actively processing the queue
- **Stopped**: Worker is not running (emails won't be retried automatically)
- **Unknown**: Status couldn't be determined

### Starting/Stopping the Worker

- **Start**: Click the green **Start** button to begin automatic retries
- **Stop**: Click the red **Stop** button to pause automatic retries

The worker automatically starts when the dashboard boots.

### Worker Statistics

| Stat | Description |
|------|-------------|
| **Restarts** | How many times the supervisor restarted the worker |
| **Last Check** | When the worker last checked for pending emails |
| **Retries Attempted** | Total retry attempts since worker started |
| **Retries Succeeded** | Successful retries since worker started |

## Configuration

### Viewing/Editing Configuration

1. Expand the **DLQ Configuration** section
2. Modify settings:
   - **Enabled**: Toggle automatic retries on/off
   - **Max Retries**: Maximum attempts before marking as failed (default: 10)
   - **Check Interval**: How often the worker checks for pending emails (seconds)
   - **Base Retry Delay**: Initial delay before first retry (seconds)
3. Click **Save Configuration**

### Default Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| enabled | true | Enable automatic retries |
| max_retries | 10 | Max retry attempts |
| check_interval_seconds | 60 | Worker check interval |
| base_retry_delay_seconds | 60 | Initial retry delay |
| max_retry_delay_seconds | 3600 | Maximum delay (1 hour) |
| backoff_factor | 2.0 | Exponential backoff multiplier |
| jitter_factor | 0.1 | Random jitter (+/- 10%) |

### Retry Delay Calculation

The delay between retries follows exponential backoff:

```
delay = base_delay * (backoff_factor ^ retry_count)
delay = min(delay, max_delay)
delay = delay + random(-jitter, +jitter)
```

Example progression (base=60s, backoff=2x):
- Retry 1: ~60 seconds
- Retry 2: ~120 seconds (2 minutes)
- Retry 3: ~240 seconds (4 minutes)
- Retry 4: ~480 seconds (8 minutes)
- Retry 5: ~960 seconds (16 minutes)
- Retry 6+: Caps at 3600 seconds (1 hour)

## REST API Reference

All endpoints are prefixed with `/api/email/dlq/`

### Status & List

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/status` | Get queue statistics |
| GET | `/list` | List all queued emails |
| GET | `/list?status=pending` | Filter by status |
| GET | `/<queue_id>` | Get specific email details |
| GET | `/<queue_id>/events` | Get email event history |

### Actions

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/retry/<queue_id>` | Retry specific email immediately |
| POST | `/retry-all` | Retry all pending emails |
| DELETE | `/<queue_id>` | Delete specific email |
| DELETE | `/clear` | Clear all failed emails |

### Configuration

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/config` | Get current configuration |
| POST | `/config` | Update configuration |

### Worker

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/worker/status` | Get worker status |
| POST | `/worker/start` | Start the worker |
| POST | `/worker/stop` | Stop the worker |

## Troubleshooting

### Emails Not Being Retried

1. Check if the worker is running (should show "Running" in green)
2. Check the "Last Check" timestamp - should be recent
3. Verify DLQ is enabled in configuration
4. Check the logs: `tail -f $ATLASFORGE_ROOT/logs/dashboard.log`

### High Failure Rate

1. Check the failure reason for patterns (e.g., all "Connection refused")
2. Verify SMTP credentials in `state/email_config.json`
3. Test email connection via the Email Monitor "Test" button
4. Check if the mail server is blacklisting our IP

### Worker Keeps Restarting

1. Check restart count - high numbers indicate issues
2. Review logs for exceptions
3. Check if the email_dlq.db database is healthy
4. Try stopping and restarting the worker manually

### Database Issues

The DLQ uses SQLite stored at `$ATLASFORGE_ROOT/state/email_dlq.db`

To inspect directly:
```bash
sqlite3 $ATLASFORGE_ROOT/state/email_dlq.db
> SELECT * FROM dead_letter_queue WHERE status='pending';
> SELECT * FROM dlq_events ORDER BY timestamp DESC LIMIT 20;
```

## Best Practices

1. **Monitor regularly**: Check the DLQ at least daily for failed emails
2. **Don't ignore failures**: Permanently failed emails may indicate configuration issues
3. **Review before clearing**: Always review failed emails before bulk deletion
4. **Adjust retries appropriately**: Some failures are permanent (invalid address) - don't retry forever
5. **Check timestamps**: Old pending emails might indicate worker issues

## Related Files

| File | Purpose |
|------|---------|
| `$ATLASFORGE_ROOT/email_dlq.py` | Core DLQ module |
| `$ATLASFORGE_ROOT/email_dlq_worker.py` | Background retry worker |
| `$ATLASFORGE_ROOT/dashboard_modules/dlq.py` | API endpoints |
| `$ATLASFORGE_ROOT/dashboard_static/src/modules/dlq-monitor.js` | UI module |
| `$ATLASFORGE_ROOT/state/email_dlq.db` | SQLite database |
| `$ATLASFORGE_ROOT/state/email_config.json` | Email & DLQ configuration |
