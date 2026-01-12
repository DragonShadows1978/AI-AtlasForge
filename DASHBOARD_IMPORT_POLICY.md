# Dashboard Import Policy Document

**Version:** 2.0
**Created:** 2025-12-23
**Updated:** 2026-01-12
**Status:** Production
**Scope:** dashboard_v2.py, dashboard_modules/, and integrated workspace modules

---

## Executive Summary

This document establishes the import policy for the AI-AtlasForge Dashboard codebase. It defines which paths are valid import sources, which patterns are forbidden, and provides guidelines to prevent cross-mission contamination.

---

## 1. Import Source Classification

### 1.1 Allowed Import Sources

The dashboard may import from the following sources:

| Source Type | Path | Example Imports |
|-------------|------|-----------------|
| **Standard Library** | Python stdlib | `os`, `json`, `pathlib`, `datetime` |
| **Third-Party Packages** | pip installed | `flask`, `flask_socketio`, `jinja2` |
| **Top-Level Project Modules** | `<ATLASFORGE_ROOT>/*.py` | `mission_analytics`, `mission_knowledge_base`, `decision_graph` |
| **Dashboard Modules** | `<ATLASFORGE_ROOT>/dashboard_modules/` | `from dashboard_modules.analytics import analytics_bp` |
| **Shared Workspace Modules** | `<ATLASFORGE_ROOT>/workspace/` | `glassbox`, `investigation_validator` |
| **Config Module** | `atlasforge_config.py` | `from atlasforge_config import BASE_DIR, WORKSPACE_DIR` |

### 1.2 Forbidden Import Sources

The following import sources are **NEVER** permitted:

| Source Type | Pattern | Why Forbidden |
|-------------|---------|---------------|
| **Mission Workspaces** | `missions/mission_*/workspace/` | Mission code is ephemeral; creates tight coupling |
| **Mission Archives** | `missions/mission_*/` | Same as above |
| **Hardcoded Mission IDs** | `mission_865ac720`, etc. | Non-portable, breaks on mission cleanup |
| **Temporary Directories** | `/tmp/`, `/var/tmp/` | Unreliable, ephemeral |
| **User Home Subpaths** | `~/.local/`, `/home/*/` (outside project) | Not portable, security concern |
| **Hardcoded Absolute Paths** | `/home/vader/...` | Not portable across installations |

---

## 2. sys.path Usage Guidelines

### 2.1 When sys.path.insert() Is Acceptable

`sys.path.insert()` may be used when:

1. **Shared workspace access** - Adding workspace directory to enable imports of glassbox, investigation_validator modules
2. **Project root access** - Adding project root for top-level module imports
3. **Script self-reference** - Adding the script's own directory via `Path(__file__).parent`

### 2.2 Required Pattern for New Modules

When adding a new module that needs sys.path manipulation:

```python
# CORRECT: Use atlasforge_config for paths
from atlasforge_config import BASE_DIR, WORKSPACE_DIR
import sys

# Shared workspace (acceptable)
sys.path.insert(0, str(WORKSPACE_DIR))

# Project root (acceptable)
sys.path.insert(0, str(BASE_DIR))

# Self-relative (acceptable)
sys.path.insert(0, str(Path(__file__).parent))
```

### 2.3 Forbidden sys.path Patterns

```python
# FORBIDDEN: Mission-specific workspace
sys.path.insert(0, "/path/to/missions/mission_abc123/workspace")

# FORBIDDEN: Hardcoded mission ID
mission_path = f"/path/to/missions/{mission_id}/workspace"
sys.path.insert(0, mission_path)

# FORBIDDEN: Dynamic mission path from state
sys.path.insert(0, state["mission_workspace"])

# FORBIDDEN: Hardcoded user paths
sys.path.insert(0, "/home/vader/some/path")
```

---

## 3. Blueprint Registration Pattern

Dashboard modules follow a Flask Blueprint pattern. All blueprints must be registered in `dashboard_v2.py`:

### 3.1 Standard Blueprint Registration

```python
# In dashboard_modules/<module>.py
from flask import Blueprint

module_bp = Blueprint('module_name', __name__)

@module_bp.route('/api/module/endpoint')
def endpoint():
    return {"status": "ok"}
```

```python
# In dashboard_v2.py
from dashboard_modules.module import module_bp
app.register_blueprint(module_bp)
```

### 3.2 Workspace Module Registration

Workspace modules (glassbox, etc.) require sys.path setup:

```python
# In dashboard_v2.py
import sys
from pathlib import Path

# Use relative path from script location
sys.path.insert(0, str(Path(__file__).parent / "workspace"))

# Then import
from glassbox.dashboard_routes import glassbox_bp

# Register blueprints
app.register_blueprint(glassbox_bp, url_prefix='/api/glassbox')
```

---

## 4. Relative Import Best Practices

### 4.1 Within Module Packages

Use relative imports within the same package:

```python
# In dashboard_modules/analytics.py
from .core import some_utility  # Relative within package

# NOT
from dashboard_modules.core import some_utility  # Avoid absolute when relative works
```

### 4.2 Fallback Pattern for Standalone Testing

Workspace modules should support both import modes:

```python
# Support both package and standalone execution
try:
    from .helpers import some_function  # When imported as package
except ImportError:
    from helpers import some_function  # When run directly
```

---

## 5. Data File References vs. Code Imports

### 5.1 Data References Are Acceptable

It is acceptable to **reference** mission workspace files in data:

```python
# ACCEPTABLE: Data reference (reading files, storing paths in JSON)
scan_result = {
    "code_path": f"{MISSIONS_DIR}/mission_865ac720/workspace/file.py",
    "finding": "potential issue"
}
```

### 5.2 Code Imports Are Forbidden

It is **not** acceptable to **import** code from mission workspaces:

```python
# FORBIDDEN: Code import
sys.path.insert(0, f"{MISSIONS_DIR}/mission_865ac720/workspace")
from performance import web_vitals  # NEVER DO THIS
```

---

## 6. Adding New Dashboard Features

When adding new features to the dashboard:

### 6.1 Checklist

1. [ ] Create module in `dashboard_modules/` (not in workspace/)
2. [ ] Use Flask Blueprint pattern
3. [ ] Import only from allowed sources (Section 1.1)
4. [ ] No hardcoded mission IDs
5. [ ] No sys.path manipulation pointing to missions/
6. [ ] No hardcoded absolute paths - use atlasforge_config
7. [ ] Register blueprint in dashboard_v2.py
8. [ ] Add API routes with `/api/` prefix

### 6.2 Example: Adding a New Widget

```python
# dashboard_modules/new_widget.py
from flask import Blueprint, jsonify

new_widget_bp = Blueprint('new_widget', __name__)

@new_widget_bp.route('/api/new-widget/data')
def get_data():
    # Import from allowed sources only
    from mission_analytics import get_stats
    return jsonify(get_stats())
```

---

## 7. Validation and Enforcement

### 7.1 Automated Validation

A validation script should be run to ensure compliance:

```bash
python workspace/tests/test_import_policy.py
```

### 7.2 Manual Review Points

When reviewing code changes:

1. Check for new `sys.path.insert()` calls
2. Verify no imports from `missions/mission_*/`
3. Ensure new blueprints follow registration pattern
4. Confirm relative imports are used appropriately
5. Check for hardcoded absolute paths

---

## 8. Current Compliance Status

As of 2026-01-12, all dashboard infrastructure is **compliant**:

| Component | Status | Notes |
|-----------|--------|-------|
| dashboard_v2.py | Compliant | sys.path only adds shared workspace |
| dashboard_modules/*.py | Compliant | All imports from allowed sources |
| workspace/glassbox/ | Compliant | Relative imports with fallback |
| atlasforge_config.py | Compliant | Centralized path configuration |

---

## 9. Import Architecture Overview

```
<ATLASFORGE_ROOT>/
├── atlasforge_config.py         <- Centralized path configuration
│   ├── BASE_DIR, WORKSPACE_DIR, STATE_DIR, etc.
│   └── All other modules import paths from here
│
├── dashboard_v2.py              <- Main entry point
│   ├── imports from: dashboard_modules/, top-level *.py, workspace/
│   └── sys.path adds: /workspace (allowed)
│
├── dashboard_modules/           <- Blueprint modules
│   ├── analytics.py             -> imports mission_analytics
│   ├── knowledge_base.py        -> imports mission_knowledge_base
│   ├── rde.py                   -> imports exploration_hooks
│   ├── recovery.py              -> imports stage_checkpoint_recovery
│   ├── investigation.py         -> imports investigation_engine
│   └── ...
│
├── workspace/                   <- Shared workspace (OK to import)
│   ├── glassbox/                -> Introspection system
│   └── investigation_validator/ -> Fact-checking system
│
├── missions/                    <- NEVER import from here
│   └── mission_*/workspace/     FORBIDDEN
│
└── *.py (top-level)             <- Shared infrastructure (OK to import)
    ├── mission_analytics.py
    ├── mission_knowledge_base.py
    ├── decision_graph.py
    └── ...
```

---

## 10. Version History

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2025-12-23 | RDE Mission | Initial policy document |
| 2.0 | 2026-01-12 | AI-AtlasForge | Updated for public release, removed hardcoded paths |

---

*Policy validation: `python workspace/tests/test_import_policy.py`*
