# Dashboard Import Policy Document

**Version:** 1.0
**Created:** 2025-12-23
**Status:** Production
**Scope:** dashboard_v2.py, dashboard_modules/, and integrated workspace modules

---

## Executive Summary

This document establishes the import policy for the Mini-Mind RDE Dashboard codebase. It defines which paths are valid import sources, which patterns are forbidden, and provides guidelines to prevent cross-mission contamination.

---

## 1. Import Source Classification

### 1.1 Allowed Import Sources

The dashboard may import from the following sources:

| Source Type | Path | Example Imports |
|-------------|------|-----------------|
| **Standard Library** | Python stdlib | `os`, `json`, `pathlib`, `datetime` |
| **Third-Party Packages** | pip installed | `flask`, `flask_socketio`, `jinja2` |
| **Top-Level Project Modules** | `/home/vader/mini-mind-v2/*.py` | `mission_analytics`, `mission_knowledge_base`, `decision_graph` |
| **Dashboard Modules** | `/home/vader/mini-mind-v2/dashboard_modules/` | `from dashboard_modules.analytics import analytics_bp` |
| **Shared Workspace Modules** | `/home/vader/mini-mind-v2/workspace/` | `glassbox`, `bug_bounty`, `narrative` |

### 1.2 Forbidden Import Sources

The following import sources are **NEVER** permitted:

| Source Type | Pattern | Why Forbidden |
|-------------|---------|---------------|
| **Mission Workspaces** | `missions/mission_*/workspace/` | Mission code is ephemeral; creates tight coupling |
| **Mission Archives** | `missions/mission_*/` | Same as above |
| **Hardcoded Mission IDs** | `mission_865ac720`, etc. | Non-portable, breaks on mission cleanup |
| **Temporary Directories** | `/tmp/`, `/var/tmp/` | Unreliable, ephemeral |
| **User Home Subpaths** | `~/.local/`, `/home/*/` (outside project) | Not portable, security concern |

---

## 2. sys.path Usage Guidelines

### 2.1 When sys.path.insert() Is Acceptable

`sys.path.insert()` may be used when:

1. **Shared workspace access** - Adding `/home/vader/mini-mind-v2/workspace` to enable imports of glassbox, bug_bounty, or narrative modules
2. **Project root access** - Adding `/home/vader/mini-mind-v2` for top-level module imports
3. **Script self-reference** - Adding the script's own directory via `Path(__file__).parent`

### 2.2 Required Pattern for New Modules

When adding a new module that needs sys.path manipulation:

```python
# CORRECT: Use Path objects and insert at index 0
import sys
from pathlib import Path

# Shared workspace (acceptable)
sys.path.insert(0, str(Path("/home/vader/mini-mind-v2/workspace")))

# Project root (acceptable)
sys.path.insert(0, str(Path("/home/vader/mini-mind-v2")))

# Self-relative (acceptable)
sys.path.insert(0, str(Path(__file__).parent))
```

### 2.3 Forbidden sys.path Patterns

```python
# FORBIDDEN: Mission-specific workspace
sys.path.insert(0, "/home/vader/mini-mind-v2/missions/mission_abc123/workspace")

# FORBIDDEN: Hardcoded mission ID
mission_path = f"/home/vader/mini-mind-v2/missions/{mission_id}/workspace"
sys.path.insert(0, mission_path)

# FORBIDDEN: Dynamic mission path from state
sys.path.insert(0, state["mission_workspace"])
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

Workspace modules (glassbox, bug_bounty, narrative) require sys.path setup:

```python
# In dashboard_v2.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path("/home/vader/mini-mind-v2/workspace")))

# Then import
from glassbox.dashboard_routes import glassbox_bp
from bug_bounty.dashboard_routes import bug_bounty_bp
from narrative.dashboard_routes import narrative_bp

# Register blueprints
app.register_blueprint(glassbox_bp, url_prefix='/api/glassbox')
app.register_blueprint(bug_bounty_bp, url_prefix='/api/bug-bounty')
app.register_blueprint(narrative_bp, url_prefix='/api/narrative')
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
    "code_path": "/home/vader/mini-mind-v2/missions/mission_865ac720/workspace/file.py",
    "finding": "potential issue"
}
```

### 5.2 Code Imports Are Forbidden

It is **not** acceptable to **import** code from mission workspaces:

```python
# FORBIDDEN: Code import
sys.path.insert(0, "/home/vader/mini-mind-v2/missions/mission_865ac720/workspace")
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
6. [ ] Register blueprint in dashboard_v2.py
7. [ ] Add API routes with `/api/` prefix

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
python /home/vader/mini-mind-v2/workspace/tests/test_import_policy.py
```

### 7.2 Manual Review Points

When reviewing code changes:

1. Check for new `sys.path.insert()` calls
2. Verify no imports from `missions/mission_*/`
3. Ensure new blueprints follow registration pattern
4. Confirm relative imports are used appropriately

---

## 8. Current Compliance Status

As of 2025-12-23, all dashboard infrastructure is **compliant**:

| Component | Status | Notes |
|-----------|--------|-------|
| dashboard_v2.py | Compliant | sys.path only adds shared workspace |
| dashboard_modules/*.py | Compliant | All imports from allowed sources |
| workspace/glassbox/ | Compliant | Relative imports with fallback |
| workspace/bug_bounty/ | Compliant | Relative imports with fallback |
| workspace/narrative/ | Compliant | Relative imports with fallback |

---

## 9. Import Architecture Overview

```
/home/vader/mini-mind-v2/
├── dashboard_v2.py              <- Main entry point
│   ├── imports from: dashboard_modules/, top-level *.py, workspace/
│   └── sys.path adds: /workspace (allowed)
│
├── dashboard_modules/           <- Blueprint modules
│   ├── analytics.py             -> imports mission_analytics
│   ├── knowledge_base.py        -> imports mission_knowledge_base
│   ├── git.py                   -> imports git_*, git_push_manager
│   ├── rde.py                   -> imports exploration_hooks
│   ├── recovery.py              -> imports stage_checkpoint_recovery
│   ├── investigation.py         -> imports investigation_engine
│   └── ...
│
├── workspace/                   <- Shared workspace (OK to import)
│   ├── glassbox/                -> Introspection system
│   ├── bug_bounty/              -> Security scanning
│   └── narrative/               -> Narrative workflows
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

---

*Policy validation: `python workspace/tests/test_import_policy.py`*
