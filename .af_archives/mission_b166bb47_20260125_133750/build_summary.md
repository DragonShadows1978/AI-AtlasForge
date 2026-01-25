# Build Summary: Mission Queue Auto-Start Fix

## Problem
The mission queue auto-start functionality was broken in the modular `af_engine`. When a mission completed, the next queued mission was not being processed because the `_process_mission_queue()` method was not ported from the legacy engine to the new modular architecture.

## Root Cause
The modular engine (`af_engine/orchestrator.py`) did not call `_process_mission_queue()` when a mission transitioned to the COMPLETE stage. This method existed in `af_engine_legacy.py` but was not included in the modular refactoring.

## Changes Made

### 1. Added Imports to `af_engine/orchestrator.py`
Added the following imports required for queue processing:
- `json` - For JSON serialization
- `uuid` - For generating mission IDs
- `time` - For filesystem sync delays

### 2. Added Queue Processing Methods to `StageOrchestrator`

**`_process_mission_queue()`** (~120 lines):
- Acquires queue processing lock to prevent race conditions
- Uses extended queue scheduler if available (mission_queue_scheduler)
- Falls back to simple FIFO queue processing if scheduler unavailable
- Retrieves next ready item from queue
- Creates mission from queue item
- Removes item from queue only after successful mission creation
- Emits WebSocket queue update events

**`_create_mission_from_queue_item()`** (~130 lines):
- Generates new mission ID
- Resolves project name for shared workspace
- Creates mission directories
- Writes mission to state/mission.json
- Writes auto-start signal file for dashboard watcher
- Registers with analytics if available
- Emits WebSocket notification

### 3. Modified `update_stage()` Method
Added call to `_process_mission_queue()` when stage transitions to COMPLETE:
```python
if new_stage == "COMPLETE":
    self.integrations.emit_mission_completed(...)
    # Process mission queue - start next queued mission
    self._process_mission_queue()
```

### 4. Updated Queue Scheduler Integration
Modified `af_engine/integrations/queue_scheduler.py` to:
- Remove Flask context dependency (was using `dashboard_modules.queue_scheduler.queue_status()` which called `jsonify()`)
- Use direct file access via `io_utils.atomic_read_json()`
- Clarify that actual queue processing is handled by StageOrchestrator

## Files Modified

| File | Changes |
|------|---------|
| `af_engine/orchestrator.py` | Added imports, `_process_mission_queue()`, `_create_mission_from_queue_item()`, modified `update_stage()` |
| `af_engine/integrations/queue_scheduler.py` | Removed Flask dependency, updated to use direct file access |

## Testing Verification

All tests passed:
- `StageOrchestrator` instantiation: SUCCESS
- `_process_mission_queue` method exists: TRUE
- `_create_mission_from_queue_item` method exists: TRUE
- `update_stage` calls `_process_mission_queue`: TRUE
- Queue scheduler integration works without Flask context: TRUE
- Queue status reading works: TRUE (shows 1 pending mission)

## How It Works Now

1. Mission completes (reaches COMPLETE stage)
2. `update_stage("COMPLETE")` is called
3. MISSION_COMPLETED event is emitted
4. `_process_mission_queue()` is called
5. Queue lock is acquired
6. Next ready item is retrieved from queue
7. `_create_mission_from_queue_item()` creates new mission
8. Auto-start signal file is written
9. Dashboard watcher picks up signal and starts Claude
10. Queue item is removed after successful creation

## Rollback Plan

If issues occur, set `USE_MODULAR_ENGINE=false` environment variable and restart the dashboard/conductor. This reverts to the legacy engine which has working queue processing.
