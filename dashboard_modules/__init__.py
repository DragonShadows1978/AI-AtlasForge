"""
Dashboard Modules - Modular Flask Blueprints for the AI-AtlasForge Dashboard

This package contains refactored route handlers extracted from dashboard_v2.py
to make the codebase more maintainable and modular.

Structure:
- core.py: Core routes (status, start/stop, journal, mission, proposals)
- knowledge_base.py: Knowledge base API routes
- analytics.py: Analytics and cost tracking routes
- atlasforge.py: AtlasForge exploration and enhancement routes
- recovery.py: Recovery and decision graph routes
- investigation.py: Investigation mode routes
- services.py: Service health monitoring
- cache.py: Caching utilities
- url_handlers.py: URL routing helpers
- queue_scheduler.py: Mission queue scheduling

Each module exports a Flask Blueprint that is registered in dashboard_v2.py.

Usage in dashboard_v2.py:
    from dashboard_modules import (
        core_bp, init_core_blueprint,
        knowledge_base_bp,
        analytics_bp, init_analytics_blueprint,
        atlasforge_bp,
        recovery_bp, init_recovery_blueprint,
        investigation_bp, init_investigation_blueprint,
        services_bp,
        cache_bp,
        url_handlers_bp,
        queue_scheduler_bp, init_queue_scheduler_blueprint,
        register_archival_routes
    )

    # Initialize blueprints with dependencies
    init_core_blueprint(BASE_DIR, STATE_DIR, WORKSPACE_DIR, ...)
    init_analytics_blueprint(MISSION_PATH, io_utils)
    init_recovery_blueprint(MISSION_PATH, io_utils)
    init_investigation_blueprint(...)
    init_queue_scheduler_blueprint(...)

    # Register blueprints
    app.register_blueprint(core_bp)
    app.register_blueprint(knowledge_base_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(atlasforge_bp)
    app.register_blueprint(recovery_bp)
    app.register_blueprint(investigation_bp)
    app.register_blueprint(services_bp)
    app.register_blueprint(cache_bp)
    app.register_blueprint(url_handlers_bp)
    app.register_blueprint(queue_scheduler_bp)

    # Register non-prefixed routes
    register_archival_routes(app)
"""

from .core import core_bp, init_core_blueprint
from .knowledge_base import knowledge_base_bp
from .analytics import analytics_bp, init_analytics_blueprint
from .atlasforge import atlasforge_bp, register_archival_routes
from .recovery import recovery_bp, init_recovery_blueprint
from .investigation import investigation_bp, init_investigation_blueprint
from .services import services_bp
from .cache import cache_bp
from .url_handlers import url_handlers_bp
from .queue_scheduler import queue_scheduler_bp, init_queue_scheduler_blueprint
from .semantic import semantic_bp, init_semantic_blueprint
from .version_checker import version_bp, init_version_blueprint
from .bundle_version import get_bundle_version, init_bundle_version
from .artifact_health import artifact_health_bp, init_artifact_health_blueprint

__all__ = [
    # Blueprints
    'core_bp',
    'knowledge_base_bp',
    'analytics_bp',
    'atlasforge_bp',
    'recovery_bp',
    'investigation_bp',
    'services_bp',
    'cache_bp',
    'url_handlers_bp',
    'queue_scheduler_bp',
    'semantic_bp',
    # Initialization functions
    'init_core_blueprint',
    'init_analytics_blueprint',
    'init_recovery_blueprint',
    'init_investigation_blueprint',
    'init_queue_scheduler_blueprint',
    'init_semantic_blueprint',
    'version_bp',
    'init_version_blueprint',
    # Bundle version (cache-busting)
    'get_bundle_version',
    'init_bundle_version',
    # Artifact health
    'artifact_health_bp',
    'init_artifact_health_blueprint',
    # Non-blueprint route registrations
    'register_archival_routes',
]
