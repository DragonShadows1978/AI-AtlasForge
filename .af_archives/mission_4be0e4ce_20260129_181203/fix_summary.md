# ContextWatcher Timeout/Restart Bug Fix Summary

## Problem Statement

The AtlasForge conductor had a critical bug where the 3-strike timeout system incorrectly counted graceful handoffs (context exhaustion, time-based) as errors, causing missions to halt prematurely during long-running processes like model training.

### Root Cause

The conductor's `run_rd_mode()` function would increment `timeout_retries` whenever `invoke_llm()` returned `None`, without checking whether the empty response was due to:
1. **Graceful handoff** (context exhaustion or 55-minute time limit) - Expected behavior, NOT an error
2. **Real error** (CLI crash, network issue, unresponsive) - Actual error requiring retry/halt

## Changes Made

### 1. Modified `invoke_llm()` Return Type (lines 228-274)

**Before:**
```python
def invoke_llm(...) -> Optional[str]:
    # Returns response text or None
```

**After:**
```python
def invoke_llm(...) -> tuple[Optional[str], Optional[str]]:
    """
    Returns:
        Tuple of (response_text, error_info):
        - On success: (response_text, None)
        - On timeout: (None, "timeout:<seconds>")
        - On CLI error: (None, "cli_error:<stderr_snippet>")
        - On exception: (None, "exception:<error_message>")
    """
```

This change enables the conductor to capture and log detailed error information for debugging.

### 2. Core Bug Fix - Handoff Detection (lines 1080-1143)

Added logic to check `handoff_triggered.is_set()` **BEFORE** incrementing `timeout_retries`:

```python
if not response_text:
    # Check if this was a graceful handoff (NOT an error)
    if handoff_triggered.is_set():
        # Log [RESTART] message, record graceful_handoff_restart journal entry
        # Do NOT increment timeout_retries
        handoff_triggered.clear()
        continue
    else:
        # Real error - increment counter
        timeout_retries += 1
        # Log [ERROR] message with detailed error info
```

### 3. Activity Log Differentiation

New message prefixes clearly distinguish restart types:

| Scenario | Log Message |
|----------|-------------|
| Context exhaustion | `[RESTART] Context exhaustion handoff complete. Fresh instance starting...` |
| Time-based (55 min) | `[RESTART] Time-based handoff (55.0 min) complete. Fresh instance starting...` |
| Emergency handoff | `[RESTART] Emergency context handoff. Fresh instance starting...` |
| Real error (retry) | `[ERROR] No response from Claude (attempt N/3). Error: <details>` |
| Real error (halt) | `[ERROR] Claude unresponsive after 3 retries. Mission halted.` |

### 4. Journal Entry Types

Two distinct journal entry types now exist:

**`graceful_handoff_restart`** - For expected handoffs (NOT errors):
```json
{
    "type": "graceful_handoff_restart",
    "stage": "BUILDING",
    "handoff_level": "graceful|time_based|emergency",
    "mission_id": "mission_xyz",
    "error_info": null
}
```

**`claude_timeout_failure`** - For real errors:
```json
{
    "type": "claude_timeout_failure",
    "stage": "BUILDING",
    "retries": 3,
    "error_details": "cli_error:...",
    "mission_id": "mission_xyz"
}
```

## Files Modified

| File | Lines Changed | Description |
|------|---------------|-------------|
| `atlasforge_conductor.py` | ~80 | Core bug fix: invoke_llm return type, handoff detection logic |

## Files Created

| File | Description |
|------|-------------|
| `workspace/ContextWatcher_Fixes/tests/test_timeout_tracking_fix.py` | Test suite validating all 7 requirements |

## Test Results

All 7 tests pass (100%):

1. ✓ invoke_llm returns (response, error_info) tuple
2. ✓ Graceful handoffs are properly handled
3. ✓ Error details are captured
4. ✓ Activity log differentiates restart types
5. ✓ Journal entries distinguish restart types
6. ✓ Real errors still count towards limit
7. ✓ Handoff event is reset after handling

## Success Criteria Verification

1. **Context restarts don't count as errors** ✓
   - Code checks `handoff_triggered.is_set()` before incrementing counter

2. **Time-based handoffs don't count as errors** ✓
   - `handoff_level == "time_based"` is explicitly handled

3. **Real errors still count and halt** ✓
   - `else` branch increments counter and checks MAX_CLAUDE_RETRIES

4. **Activity log shows correct restart type** ✓
   - `[RESTART]` vs `[ERROR]` prefix matches actual scenario

5. **Journal entries distinguish restart types** ✓
   - `graceful_handoff_restart` vs `claude_timeout_failure` entries

## Known Error Patterns to Watch

From journal analysis, these error patterns indicate REAL failures:
- Empty stderr on CLI return (investigate further)
- Claude CLI timeout after subprocess.TimeoutExpired
- Non-zero return code with stderr content

These should NOT count as failures:
- Context handoff at 130K+ tokens
- Time-based handoff at 55 minutes
- Emergency handoff at 140K tokens
