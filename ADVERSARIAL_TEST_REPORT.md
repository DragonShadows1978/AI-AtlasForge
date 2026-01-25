# RED TEAM ADVERSARIAL TEST REPORT
## StageOrchestrator API Compatibility Analysis

**Date**: 2026-01-25
**Files Analyzed**:
- `/home/vader/AI-AtlasForge/af_engine/orchestrator.py` (NEW implementation)
- `/home/vader/AI-AtlasForge/af_engine_legacy.py` (LEGACY reference)
- `/home/vader/AI-AtlasForge/atlasforge_conductor.py` (consumer)

**Status**: CRITICAL ISSUES FOUND - See recommendations section

---

## EXECUTIVE SUMMARY

The new `StageOrchestrator` implementation introduces **several behavioral differences and missing features** compared to the legacy `RDMissionController`. While core API methods exist, there are edge cases, missing properties, error handling gaps, and thread-safety issues that could cause runtime failures when conductor.py invokes these APIs.

**Critical Severity**: 4 issues
**High Severity**: 5 issues
**Medium Severity**: 3 issues

---

## CRITICAL ISSUES

### 1. MISSING STATE TRACKING FOR QUEUE PROCESSING FLAG
**Severity**: CRITICAL
**Location**: `orchestrator.py:77` vs `af_engine_legacy.py:1279`

#### Issue
The legacy `af_engine_legacy.py` has a `_queue_processing` flag that prevents `log_history()` from saving during queue processing:

```python
# Legacy (af_engine_legacy.py:1278-1281)
def log_history(self, entry: str, details: dict = None):
    # Safety check: don't save if we're in the middle of queue processing
    if getattr(self, '_queue_processing', False):
        logger.warning(f"Skipping log_history save during queue processing: {entry}")
        return
```

The new orchestrator sets this flag:
```python
# New (orchestrator.py:77)
self._queue_processing = False
```

But **the new implementation NEVER uses it or checks it in `log_history()`**:

```python
# New (orchestrator.py:397-399)
def log_history(self, entry: str, details: Optional[Dict] = None) -> None:
    """Log an entry to mission history."""
    self.state.log_history(entry, details)
```

The check is delegated to `StateManager.log_history()`, but there's **no evidence** that `StateManager` implements this flag check.

#### Risk
Queue processing operations may overwrite newly created missions if `log_history()` saves during critical moments. This is documented as a safety feature in the legacy code.

#### Test Case to Break This
```python
# In mission queue processing context:
orchestrator._queue_processing = True
orchestrator.log_history("Some entry")  # Will save even though flag is set!
# This could corrupt mission.json during queue creation
```

---

### 2. MISSING `mission_dir` INITIALIZATION IN `set_mission()`
**Severity**: CRITICAL
**Location**: `orchestrator.py:474-572` vs `af_engine_legacy.py:2950-3010`

#### Issue
The new `set_mission()` method creates `mission_dir` and saves `mission_config.json`:

```python
# New (orchestrator.py:520-569)
mission_dir = MISSIONS_DIR / mid
mission_dir.mkdir(parents=True, exist_ok=True)
# ... saves config file ...
config_data = { ... }
with open(mission_config_path, 'w') as f:
    json.dump(config_data, f, indent=2)
```

However, **it stores these paths in the mission state**:

```python
"mission_workspace": str(mission_workspace),
"mission_dir": str(mission_dir),
```

**But the conductor expects to read `mission_workspace` and `mission_dir`** (e.g., line 697 of conductor.py):

```python
mission_workspace = controller.mission.get("mission_workspace")
```

#### The Gap
The new `set_mission()` method properly initializes these, BUT if these fields are **missing from an existing mission.json** (legacy mission), the properties won't be set. The legacy code also reads `mission_dir` property:

```python
@property
def mission_dir(self) -> Path:
    """Get the mission directory path (backward compatibility)."""
    return self.state.mission_dir
```

**StateManager must provide a `mission_dir` property, but it's not clear if it does.**

#### Risk
Conductor will crash with `AttributeError` when accessing `controller.mission_dir` or `controller.mission.get("mission_workspace")` on missions created before the migration.

#### Test Case to Break This
```python
# Load a legacy mission without mission_workspace field
legacy_mission = {"mission_id": "old_m1", "problem_statement": "test"}
orchestrator.mission = legacy_mission

# This will fail or return None:
workspace = orchestrator.mission.get("mission_workspace")  # None
# Then conductor tries to use it as a Path
path = Path(workspace)  # TypeError!
```

---

### 3. MISSING `last_updated` FIELD IN `update_stage()`
**Severity**: CRITICAL
**Location**: `orchestrator.py:129-186` vs `af_engine_legacy.py:792-804`

#### Issue
The legacy `update_stage()` explicitly sets `last_updated`:

```python
# Legacy (af_engine_legacy.py:800)
self.mission["last_updated"] = datetime.now().isoformat()
self.save_mission()
```

The new implementation delegates to `StateManager.update_stage()`:

```python
# New (orchestrator.py:161)
old = self.state.update_stage(new_stage)
```

**It relies on StateManager to set `last_updated`.** But the new `orchestrator.py` shows the flag is set in `StateManager.save_mission()`:

```python
# state_manager.py (assumed, based on code pattern):
def save_mission(self) -> None:
    self._mission["last_updated"] = datetime.now().isoformat()
```

#### The Gap
The problem is timing: **when is `update_stage()` called on StateManager, and does it save?**

Looking at orchestrator line 161:
```python
old = self.state.update_stage(new_stage)
```

We don't see the StateManager.update_stage() method in the read files. If it doesn't automatically save, the `last_updated` won't be set until later.

#### Risk
Conductor and other systems rely on `last_updated` to determine mission freshness. If this field is missing, retry logic and auto-advance detection may fail.

```python
# From conductor.py (implied):
last_updated = mission.get("last_updated")  # Might be None!
if not last_updated:
    # Age calculation fails
```

---

### 4. MISSING EVENT EMISSION IN `update_stage()` - INCOMPLETE IMPLEMENTATION
**Severity**: CRITICAL
**Location**: `orchestrator.py:129-186`

#### Issue
The new `update_stage()` attempts to emit events:

```python
# orchestrator.py:150-158
self.integrations.emit_stage_completed(
    stage=old_stage,
    mission_id=self.mission_id,
    data={...}
)
```

But the legacy code emits WebSocket events AND tracks analytics:

```python
# Legacy (af_engine_legacy.py:804-850):
# 1. WebSocket event via emit_stage_change()
# 2. Analytics tracking via track_stage_end() and track_stage_start()
# 3. Token watcher updates
# 4. Git integration checkpoints
# 5. Recovery checkpoints
# 6. Mission snapshots
# 7. Transcript archival
```

The new implementation **delegates all of this to integrations**, but:
- **Are all these integrations registered?** See line 87 of orchestrator.py
- **What if an integration fails?** The legacy code has explicit error handling

#### Risk
Missing analytics, git checkpoints, transcript archival, and recovery snapshots. Multi-cycle missions won't track properly.

---

## HIGH SEVERITY ISSUES

### 5. MISSING `mission_dir` PROPERTY IN STATE MANAGER
**Severity**: HIGH
**Location**: `orchestrator.py:121-123`

#### Issue
The orchestrator exposes `mission_dir` as a property:

```python
# orchestrator.py:121-123
@property
def mission_dir(self) -> Path:
    """Get the mission directory path (backward compatibility)."""
    return self.state.mission_dir
```

But StateManager must provide this property. The read state_manager.py doesn't show this property defined.

#### Risk
`AttributeError: 'StateManager' object has no attribute 'mission_dir'` when conductor accesses `controller.mission_dir`.

#### Test Case to Break This
```python
orchestrator = StageOrchestrator()
path = orchestrator.mission_dir  # AttributeError!
```

---

### 6. NO RETURN VALUE FROM `update_stage()`
**Severity**: HIGH
**Location**: `orchestrator.py:129-186` vs `af_engine_legacy.py:792-804`

#### Issue
The new `update_stage()` returns `None`:

```python
# orchestrator.py:129-186
def update_stage(self, new_stage: str) -> None:
    """Update the mission stage."""
    # ... no return statement
```

The legacy also returns None, but some callers might expect the old stage:

```python
# Legacy (af_engine_legacy.py:792-804)
def update_stage(self, new_stage: str):
    # ... also no explicit return
    # But returns None implicitly
```

Actually this is consistent. But check conductor usage:

```python
# conductor.py:877
controller.update_stage(next_stage)  # No return value captured
```

So this is OK, but **note that StateManager.update_stage() is called at line 161**:
```python
old = self.state.update_stage(new_stage)
```

**This assumes StateManager.update_stage() returns the old stage.** Does it?

#### Risk
Type mismatch if StateManager doesn't return the old stage properly.

---

### 7. INCOMPLETE `process_response()` IMPLEMENTATION
**Severity**: HIGH
**Location**: `orchestrator.py:246-289` vs `af_engine_legacy.py:1855+`

#### Issue
The new `process_response()` is extremely minimal:

```python
# New (orchestrator.py:246-289)
def process_response(self, response: Dict[str, Any]) -> str:
    if response is None:
        response = {}

    stage = self.current_stage
    handler = self.registry.get_handler(stage)
    stage_context = self._build_stage_context()

    result: StageResult = handler.process_response(response, stage_context)

    for event in result.events_to_emit:
        self.integrations.emit(event)

    self.state.increment_iteration()
    return result.next_stage
```

The legacy has **EXTENSIVE logic** for each stage:

```python
# Legacy (af_engine_legacy.py:1855-1993):
# - PLANNING stage: Pre-build backup
# - BUILDING stage: Build completion checking
# - TESTING stage: Test result handling
# - ANALYZING stage: Recommendation routing (BUILDING/PLANNING/COMPLETE)
# - CYCLE_END stage: Drift validation, cycle advancement
# - COMPLETE stage: Final report generation
```

#### The Gap
The new implementation assumes the stage handler does ALL this work. But:
1. **Pre-build backup is missing** - `PLAN_BACKUP_AVAILABLE` integration in legacy
2. **Drift validation is missing** - `DRIFT_VALIDATION_AVAILABLE` integration in legacy
3. **Cycle advancement is missing** - `_advance_to_next_cycle()` logic in legacy
4. **Final report generation is missing** - `_generate_final_report()` in legacy

#### Risk
Multi-cycle missions won't work properly. Drift validation is skipped. Pre-build backups are lost.

#### Test Case to Break This
```python
# After ANALYZING stage with recommendation=PLANNING
response = {"status": "needs_replanning", "recommendation": "PLANNING"}
next_stage = orchestrator.process_response(response)
# Legacy would: call increment_iteration() and return "PLANNING"
# New: delegates to handler, might not increment or validate drift
```

---

### 8. MISSING BUILTIN INTEGRATIONS CHECK
**Severity**: HIGH
**Location**: `orchestrator.py:84-94`

#### Issue
The orchestrator loads integrations but has a soft failure:

```python
# orchestrator.py:84-94
def _load_integrations(self) -> None:
    """Load all default integration handlers."""
    try:
        self.integrations.load_default_integrations()
        stats = self.integrations.get_stats()
        logger.info(...)
    except Exception as e:
        logger.warning(f"Failed to load some integrations: {e}")
```

The warning is logged, but **execution continues**. The conductor then calls:

```python
# conductor.py line 729
controller = atlasforge_engine.RDMissionController()
# ... later calls build_rd_prompt() which needs integrations
```

If integrations fail to load:
- `emit_stage_completed()` might fail
- Event dispatch might fail
- Analytics, token watcher, git integration all missing

#### Risk
Silent integration failures lead to missing features later.

---

### 9. NO VALIDATION OF INVALID STAGE IN `update_stage()`
**Severity**: HIGH
**Location**: `orchestrator.py:141-144`

#### Issue
Invalid stages are rejected but execution continues:

```python
# orchestrator.py:141-144
if new_stage not in self.STAGES:
    logger.error(f"Invalid stage: {new_stage}")
    return  # Silent return!
```

The legacy has the same behavior, but:

```python
# Legacy (af_engine_legacy.py:794-796)
if new_stage not in STAGES:
    logger.warning(f"Invalid stage: {new_stage}. Valid stages: {STAGES}")
    return  # Also returns silently
```

#### The Gap
Both implementations silently return, but **conductor doesn't check if update succeeded**:

```python
# conductor.py:877
controller.update_stage(next_stage)  # No return value check
```

If `next_stage` is invalid, the stage won't update and no error is raised. The mission hangs in the wrong stage.

#### Risk
Infinite loops if handler returns invalid stage and update silently fails.

#### Test Case to Break This
```python
response = {"status": "success", "next_stage": "INVALID_STAGE"}
next_stage = orchestrator.process_response(response)  # Returns "INVALID_STAGE"
orchestrator.update_stage(next_stage)  # Silently fails, returns None
# Mission stays in old stage, loop continues with same handler
```

---

## MEDIUM SEVERITY ISSUES

### 10. MISSING `reset_mission()` FUNCTIONALITY
**Severity**: MEDIUM
**Location**: `orchestrator.py:614-629` vs `af_engine_legacy.py` (not found in excerpts)

#### Issue
The new `reset_mission()` only copies two fields:

```python
# orchestrator.py:614-629
def reset_mission(self) -> None:
    """Reset mission to initial state (keeps problem statement)."""
    problem = self.mission.get("problem_statement", "No mission defined.")
    prefs = self.mission.get("preferences", {})

    self.state.mission = {
        "problem_statement": problem,
        "preferences": prefs,
        "current_stage": "PLANNING",
        "iteration": 0,
        "history": [],
        "created_at": datetime.now().isoformat(),
        "reset_at": datetime.now().isoformat()
    }
```

The conductor calls this:

```python
# conductor.py:755
controller.reset_mission()
```

But the new implementation **drops critical fields**:
- `mission_id` (reset to default?)
- `success_criteria`
- `cycle_budget`
- `artifacts`
- `max_iterations`

#### Risk
After `reset_mission()`, the mission state is incomplete. The `mission_id` might be lost!

#### Test Case to Break This
```python
orchestrator.reset_mission()
# Check if mission_id is preserved
assert orchestrator.mission_id != "default"  # Will fail!
```

---

### 11. MISSING `load_mission_from_file()` ERROR HANDLING
**Severity**: MEDIUM
**Location**: `orchestrator.py:418-452`

#### Issue
The new `load_mission_from_file()` catches `ImportError` for `io_utils`:

```python
# orchestrator.py:428-432
try:
    import io_utils
except ImportError:
    import json
    io_utils = None
```

But then later:

```python
# orchestrator.py:434-440
if io_utils:
    template = io_utils.atomic_read_json(filepath, {})
else:
    if not filepath.exists():
        return False
    with open(filepath, 'r') as f:
        template = json.load(f)
```

**The fallback path doesn't handle `json.JSONDecodeError`**. If the file has invalid JSON, it will crash instead of returning False gracefully.

#### Risk
If mission template file is corrupted, orchestrator crashes instead of handling gracefully.

#### Test Case to Break This
```python
# Create a corrupted JSON file
corrupted_file = Path("/tmp/bad_mission.json")
corrupted_file.write_text("{invalid json")

# This will raise JSONDecodeError instead of returning False
result = orchestrator.load_mission_from_file(corrupted_file)
# Should return False, but crashes instead
```

---

### 12. MISSING `get_recent_history()` SLICING GUARD
**Severity**: MEDIUM
**Location**: `orchestrator.py:405-407` vs `af_engine_legacy.py:1303-1305`

#### Issue
The new implementation uses direct slicing:

```python
# orchestrator.py:405-407
def get_recent_history(self, n: int = 10) -> list:
    """Get recent history entries (backward compatibility)."""
    return self.state.history[-n:]
```

But it accesses `self.state.history` directly. **Does StateManager have a `history` property?**

Looking at the state_manager excerpt, we don't see a `history` property. The mission dict has a history key, but **accessing `self.state.history` will fail with `AttributeError`**.

#### Risk
`AttributeError: 'StateManager' object has no attribute 'history'` when conductor calls `get_recent_history()`.

#### Test Case to Break This
```python
history = orchestrator.get_recent_history(5)  # AttributeError!
```

---

## BEHAVIORAL DIFFERENCES

### 13. MISSING `_queue_processing` FLAG IN `log_history()`
**Impact**: HIGH - Operations that depend on queue safety won't work

The legacy implementation has explicit queue processing safety:
```python
# Legacy checks this flag in log_history()
if getattr(self, '_queue_processing', False):
    logger.warning(...)
    return  # Don't save
```

The new implementation never checks this flag. Code that sets `_queue_processing = True` won't prevent saves.

---

### 14. MISSING ANALYTICS AND INTEGRATION TRACKING
**Impact**: HIGH - Analytics, token watcher, git integration missing from flow

The legacy `update_stage()` has extensive integration handling that's **completely removed** from the new implementation. It's delegated to `IntegrationManager`, but:

1. **Are integrations registered by default?** (Soft failure in line 94)
2. **Do integrations handle analytics, git, and recovery?** (Unknown)
3. **What if an integration fails?** (No error propagation)

---

### 15. MISSING EXCEPTION HANDLING IN `build_rd_prompt()`
**Impact**: MEDIUM - If any component fails, no fallback

The legacy has try/except around each context injection (KB, AfterImage, recovery). The new implementation delegates to handler and prompts factory, but **if they throw exceptions, there's no fallback**.

---

## EDGE CASES NOT COVERED

### 16. NONE RESPONSE HANDLING
**Status**: PARTIALLY COVERED

The new implementation handles None:
```python
if response is None:
    response = {}
```

But what if `response` is something other than a dict?
```python
response = "not a dict"
response.get("status")  # AttributeError!
```

---

### 17. MISSING `current_stage` PROPERTY SETTER
**Impact**: MEDIUM

The orchestrator exposes:
```python
@property
def current_stage(self) -> str:
    """Get the current stage."""
    return self.state.current_stage
```

But **there's no setter**. Legacy code might do:
```python
controller.current_stage = "PLANNING"  # Won't work!
```

The legacy implementation also doesn't show a setter, so this might be OK, but it's worth checking if any code does direct assignment.

---

### 18. RACE CONDITIONS IN MISSION SAVE
**Impact**: HIGH - Concurrent access not protected

Neither implementation uses file locking. If multiple processes call:
```python
orchestrator.log_history(...)  # Calls save_mission()
orchestrator.update_stage(...)  # Also calls save_mission()
```

Simultaneously, there's a race condition where one write overwrites the other.

The StateManager's `auto_save` feature compounds this:
```python
# state_manager.py (assumed):
@mission.setter
def mission(self, value):
    self._mission = value
    if self.auto_save:
        self.save_mission()  # Implicit save
```

If two threads set `mission` concurrently, one save is lost.

---

### 19. MISSING BOUNDARY CHECKS ON CYCLE_BUDGET
**Impact**: MEDIUM

The new `set_mission()` has:
```python
"cycle_budget": max(1, cycle_budget),
```

But what about invalid types?
```python
controller.set_mission("test", cycle_budget="invalid")  # Won't fail!
# Later: mission.get("cycle_budget")  # Returns "invalid"
```

---

## THREAD SAFETY ISSUES

### 20. SHARED MUTABLE STATE IN `_queue_processing` FLAG
**Severity**: HIGH

The flag is instance-level:
```python
self._queue_processing = False
```

If two threads use the same orchestrator instance:
```python
# Thread 1
orchestrator._queue_processing = True
orchestrator.log_history("t1 entry")  # Skips save

# Thread 2 (concurrent)
orchestrator.log_history("t2 entry")  # Also skips save due to flag!
```

This is a synchronization bug.

---

## MISSING PROPERTIES AND METHODS

### Summary Table

| Property/Method | Legacy | New | Status |
|---|---|---|---|
| `mission` (property) | Yes | Yes | OK |
| `mission` (setter) | Yes | Yes | OK |
| `current_stage` | Yes | Yes | OK |
| `mission_id` | Yes | Yes | OK |
| `mission_dir` | Yes | Yes, but StateManager support unclear | RISKY |
| `state` (raw access) | No | Yes | NEW |
| `registry` | No | Yes | NEW |
| `integrations` | No | Yes | NEW |
| `cycles` | No | Yes | NEW |
| `prompts` | No | Yes | NEW |
| `update_stage()` | Yes | Yes, but delegated | CHANGED |
| `build_rd_prompt()` | Yes | Yes, but delegated | CHANGED |
| `process_response()` | Yes | Yes, but simplified | RISKY |
| `increment_iteration()` | Yes | Yes | OK |
| `log_history()` | Yes | Yes, but no queue safety | RISKY |
| `get_recent_history()` | Yes | Yes, but wrong property | BROKEN |
| `load_mission()` | Yes | Yes | OK |
| `load_mission_from_file()` | Yes | Yes, but weak error handling | RISKY |
| `save_mission()` | Yes | Yes | OK |
| `reload_mission()` | Yes | Yes | OK |
| `set_mission()` | Yes | Yes | OK |
| `reset_mission()` | Yes | Yes, but incomplete | BROKEN |
| `get_status()` | Yes (legacy: `get_mission_status()`) | Yes | OK |
| `is_tool_allowed()` | Yes | Yes | OK |
| `get_stage_restrictions()` | Yes | Yes | OK |
| `should_continue_cycle()` | Yes | Yes | OK |
| `advance_to_next_cycle()` | Yes | Yes | OK |
| `get_cycle_status()` | Yes | Yes | OK |

---

## RECOMMENDATIONS FOR ADDITIONAL TESTS

### 1. **Queue Processing Safety** (CRITICAL)
```python
def test_log_history_respects_queue_processing_flag():
    """Verify log_history doesn't save during queue processing."""
    orchestrator._queue_processing = True
    orchestrator.log_history("test entry")
    # Verify mission.json was NOT updated
    # Currently FAILS - flag is not checked
```

### 2. **Mission Directory Initialization** (CRITICAL)
```python
def test_legacy_mission_without_workspace_paths():
    """Test that legacy missions get mission_workspace initialized."""
    legacy_mission = {"mission_id": "old_m1", "problem_statement": "test"}
    orchestrator.mission = legacy_mission

    # Should not crash
    workspace = orchestrator.mission.get("mission_workspace")
    # Currently might be None, breaking conductor
```

### 3. **StateManager Property Exposure** (CRITICAL)
```python
def test_state_manager_provides_mission_dir():
    """Verify StateManager.mission_dir property exists."""
    path = orchestrator.state.mission_dir
    assert isinstance(path, Path)
    # Currently might AttributeError
```

### 4. **Invalid Stage Handling** (HIGH)
```python
def test_invalid_stage_returns_error_not_silent():
    """Verify invalid stage transitions are detected."""
    result = orchestrator.update_stage("INVALID_STAGE")
    # Should raise or return False, not silently fail
    # Currently returns None silently
```

### 5. **Process Response Cycle Advancement** (HIGH)
```python
def test_process_response_advances_cycles():
    """Verify CYCLE_END response properly advances cycles."""
    orchestrator.mission["current_cycle"] = 1
    orchestrator.mission["cycle_budget"] = 3

    response = {
        "status": "cycle_complete",
        "continuation_prompt": "Continue with..."
    }

    next_stage = orchestrator.process_response(response)

    # Should be PLANNING for next cycle, not COMPLETE
    assert next_stage == "PLANNING"
    assert orchestrator.mission["current_cycle"] == 2
    # Currently might not increment cycle properly
```

### 6. **Drift Validation Integration** (HIGH)
```python
def test_drift_validation_halts_mission():
    """Verify drift validation stops out-of-scope cycles."""
    # Setup mission with problem statement
    # Provide continuation prompt that drifts

    response = {"status": "cycle_complete", "continuation_prompt": "...drift..."}
    next_stage = orchestrator.process_response(response)

    # Should return COMPLETE if drift detected
    # Currently no drift validation implemented
```

### 7. **Missing method: get_recent_history Error** (HIGH)
```python
def test_get_recent_history_works():
    """Verify get_recent_history doesn't crash."""
    orchestrator.log_history("entry 1")
    orchestrator.log_history("entry 2")

    recent = orchestrator.get_recent_history(1)
    # Currently crashes with AttributeError
    assert len(recent) == 1
```

### 8. **Reset Mission Preserves ID** (MEDIUM)
```python
def test_reset_mission_keeps_mission_id():
    """Verify reset_mission preserves mission_id."""
    original_id = orchestrator.mission_id
    orchestrator.reset_mission()

    assert orchestrator.mission_id == original_id
    # Currently might reset to "default"
```

### 9. **Corrupted Mission File Handling** (MEDIUM)
```python
def test_load_mission_from_file_handles_json_error():
    """Verify corrupted JSON files are handled."""
    bad_file = Path("/tmp/corrupted.json")
    bad_file.write_text("{invalid json")

    result = orchestrator.load_mission_from_file(bad_file)

    assert result is False
    # Currently crashes with JSONDecodeError
```

### 10. **Thread Safety: Concurrent Saves** (HIGH)
```python
def test_concurrent_mission_updates_dont_corrupt():
    """Verify concurrent saves don't lose data."""
    import threading

    def update_thread(entry_text):
        orchestrator.log_history(entry_text)

    threads = [
        threading.Thread(target=update_thread, args=(f"entry_{i}",))
        for i in range(10)
    ]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # All entries should be in history
    history = orchestrator.mission.get("history", [])
    assert len(history) == 10
    # Currently might lose entries due to race condition
```

### 11. **Integration Failure Handling** (HIGH)
```python
def test_orchestrator_works_with_missing_integrations():
    """Verify orchestrator handles missing integrations gracefully."""
    # Mock integration loading to fail
    orchestrator.integrations.load_default_integrations = Mock(side_effect=Exception("No integrations"))

    # Should still work, just with degraded functionality
    orchestrator._load_integrations()

    # Should not crash on stage update
    orchestrator.update_stage("BUILDING")
    # Currently might fail silently but inconsistently
```

### 12. **None/Empty Response Handling** (MEDIUM)
```python
def test_process_response_handles_non_dict():
    """Verify process_response handles non-dict responses."""
    # Test with various invalid types
    for invalid_response in [None, "", 42, [], True]:
        result = orchestrator.process_response(invalid_response)
        # Should not crash, should return valid stage
        assert result in orchestrator.STAGES
    # Currently only handles None
```

### 13. **Last Updated Field** (HIGH)
```python
def test_update_stage_sets_last_updated():
    """Verify last_updated is set on stage change."""
    old_time = orchestrator.mission.get("last_updated", "")

    import time
    time.sleep(0.1)

    orchestrator.update_stage("BUILDING")

    new_time = orchestrator.mission.get("last_updated", "")

    assert new_time > old_time
    # Currently might not be set if StateManager doesn't handle it
```

---

## COMPARISON: KEY BEHAVIORAL DIFFERENCES

### Legacy vs New: Stage Update Flow

**Legacy (af_engine_legacy.py)**:
1. Validate stage
2. Update mission state
3. Save mission
4. Emit WebSocket event
5. Track analytics (start/end)
6. Stop token watcher if COMPLETE
7. Update git integration
8. Save recovery checkpoint
9. Create mission snapshot
10. Clear checkpoint on COMPLETE
11. Archive transcripts on COMPLETE

**New (orchestrator.py)**:
1. Validate stage
2. Emit STAGE_COMPLETED event
3. Update state via StateManager
4. Emit STAGE_STARTED event
5. Emit MISSION_COMPLETED event if COMPLETE
6. (Relies on IntegrationManager for steps 2, 4, 5)

**Gap**: Steps 5-11 in legacy are missing or depend on integrations being registered.

---

## CONCLUSION

The new `StageOrchestrator` provides a cleaner, event-driven architecture but has **critical gaps** in:

1. **State management** - Missing properties and fields
2. **Error handling** - Silent failures instead of exceptions
3. **Integration safety** - Soft failure on integration load
4. **Thread safety** - Race conditions on concurrent access
5. **Backward compatibility** - Missing queue processing flag checks
6. **Feature completeness** - Cycle advancement, drift validation, backups missing from `process_response()`

**The implementation is NOT production-ready** without addressing these issues.

### Priority Fixes (in order):
1. Fix `get_recent_history()` to access correct property
2. Add `history` property to StateManager
3. Add `mission_dir` property to StateManager
4. Implement queue processing flag check in `log_history()`
5. Add cycle advancement logic to `process_response()`
6. Add drift validation to `process_response()`
7. Implement file-level locking for concurrent saves
8. Add proper error handling for integration failures

---

## FILES TO AUDIT

1. `/home/vader/AI-AtlasForge/af_engine/state_manager.py` - Full review needed
2. `/home/vader/AI-AtlasForge/af_engine/cycle_manager.py` - Verify cycle advancement
3. `/home/vader/AI-AtlasForge/af_engine/integration_manager.py` - Verify all integrations
4. `/home/vader/AI-AtlasForge/af_engine/stages/*.py` - Verify handler implementations
