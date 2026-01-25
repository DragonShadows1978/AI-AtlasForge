# Feature Flag Rollout Strategy

This document describes the rollout strategy for transitioning from the legacy `af_engine.py` to the modular `af_engine/` architecture.

## Current State

| Component | Status |
|-----------|--------|
| Legacy Engine | `af_engine_legacy.py` - Preserved as backup (3098 lines) |
| Modular Engine | `af_engine/` - Complete (5514 lines across 33 modules) |
| Feature Flag | `USE_MODULAR_ENGINE` - Defaults to `False` |
| Tests | 140+ tests passing |

## Feature Flag Configuration

### Environment Variable

```bash
# Enable modular engine
export USE_MODULAR_ENGINE=true

# Or disable (default)
export USE_MODULAR_ENGINE=false
```

### In Code

```python
import os

USE_MODULAR_ENGINE = os.getenv("USE_MODULAR_ENGINE", "false").lower() == "true"

if USE_MODULAR_ENGINE:
    from af_engine.orchestrator import StageOrchestrator as RDMissionController
else:
    from af_engine_legacy import RDMissionController
```

## Rollout Phases

### Phase 1: Internal Testing (Current)

**Duration:** 1-2 days

**Actions:**
1. Run test missions with `USE_MODULAR_ENGINE=true`
2. Monitor for any issues
3. Compare outputs between legacy and modular
4. Fix any bugs discovered

**Success Criteria:**
- All test missions complete successfully
- No regression in functionality
- All integrations fire correctly

### Phase 2: Beta Rollout

**Duration:** 3-5 days

**Actions:**
1. Enable for new missions only
2. Keep legacy for existing/in-progress missions
3. Monitor dashboard for anomalies
4. Collect metrics on performance

**Configuration:**
```python
# Enable for new missions only
def should_use_modular(mission_id: str) -> bool:
    # New missions use modular
    if mission_created_after(mission_id, BETA_START_DATE):
        return True
    # Existing missions use legacy
    return False
```

**Success Criteria:**
- No mission failures due to engine
- Performance within 10% of legacy
- All events visible in dashboard

### Phase 3: Gradual Rollout

**Duration:** 1 week

**Actions:**
1. Enable for 50% of new missions (random)
2. Monitor for issues
3. Increase to 100% if stable

**Configuration:**
```python
import random

def should_use_modular(mission_id: str) -> bool:
    # Hash-based consistent assignment
    return hash(mission_id) % 100 < 50  # 50% rollout
```

**Success Criteria:**
- Both engines produce equivalent results
- No increase in failure rate
- Dashboard shows correct events

### Phase 4: Full Rollout

**Duration:** Permanent

**Actions:**
1. Set `USE_MODULAR_ENGINE=true` as default
2. Remove legacy fallback code (optional)
3. Archive `af_engine_legacy.py`
4. Update all documentation

**Success Criteria:**
- All missions use modular engine
- No rollbacks needed for 1 week
- Performance and stability verified

## Rollback Procedure

If issues are discovered:

### Immediate Rollback

```bash
# Disable modular engine
export USE_MODULAR_ENGINE=false

# Restart any running services
systemctl restart atlasforge-dashboard
```

### Per-Mission Rollback

```python
# In mission config or state
{
    "mission_id": "problem_mission",
    "force_legacy_engine": true
}
```

### Code-Level Rollback

```python
# Temporarily force legacy
USE_MODULAR_ENGINE = False  # Override environment
```

## Monitoring

### Key Metrics

| Metric | Source | Alert Threshold |
|--------|--------|-----------------|
| Mission success rate | Mission logs | < 95% |
| Prompt generation time | Analytics | > 500ms |
| Event emission | WebSocket | Any stage missing events |
| Error rate | Logs | Any ERROR logs |

### Dashboard Widgets

The following dashboard widgets monitor modular engine health:

- **Cost Analytics** - Token usage per stage
- **Decision Graph** - Execution flow visualization
- **Lessons Learned** - KB integration status

### Log Monitoring

```bash
# Watch for modular engine logs
tail -f logs/atlasforge.log | grep -E "(modular|StageOrchestrator)"

# Watch for errors
tail -f logs/atlasforge.log | grep ERROR
```

## Compatibility Notes

### API Compatibility

The modular engine maintains full API compatibility:

```python
# Same interface
controller.update_stage(new_stage)
controller.build_rd_prompt()
controller.process_response(response)
```

### Breaking Changes

None. The modular engine is a drop-in replacement.

### Deprecated Features

None at this time.

## Timeline

| Date | Phase | Action |
|------|-------|--------|
| Day 1-2 | Internal Testing | Run test missions, fix issues |
| Day 3-5 | Beta | Enable for new missions |
| Week 2 | Gradual | 50% -> 100% rollout |
| Week 3+ | Full | Remove legacy code path |

## Contact

For issues with the modular engine rollout:
- Check logs first
- Review this document for rollback procedures
- File issues in the AtlasForge repository
