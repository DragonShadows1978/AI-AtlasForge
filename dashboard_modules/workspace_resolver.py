"""
Workspace Resolution Utility for AtlasForge Dashboard

Provides centralized functions to resolve the correct workspace path for a mission,
handling both shared workspaces (project_workspace) and legacy per-mission workspaces.

This module eliminates duplicate workspace resolution logic across dashboard routes
and ensures consistent behavior.

Usage:
    from dashboard_modules.workspace_resolver import resolve_mission_workspace, get_workspace_info

    # Simple resolution
    workspace = resolve_mission_workspace(mission_id, MISSIONS_DIR, WORKSPACE_DIR, io_utils)

    # Get detailed info
    info = get_workspace_info(mission_id, MISSIONS_DIR, WORKSPACE_DIR, io_utils)
    if info['is_shared']:
        print(f"Using shared workspace for project: {info['project_name']}")
"""

from pathlib import Path
from typing import Optional, Dict, Any, Union
import logging

logger = logging.getLogger(__name__)


def resolve_mission_workspace(
    mission_id: str,
    missions_dir: Union[Path, str],
    workspace_dir: Union[Path, str],
    io_utils,
    mission_data: Optional[Dict[str, Any]] = None
) -> Path:
    """
    Resolve the correct workspace path for a mission.

    Priority order:
    1. project_workspace from mission_config.json (shared workspace feature)
    2. mission_workspace from mission.json state (if mission_data provided)
    3. Legacy path: missions/<mission_id>/workspace/

    Args:
        mission_id: The mission ID
        missions_dir: Path to missions directory
        workspace_dir: Path to global workspace directory
        io_utils: io_utils module for atomic file operations
        mission_data: Optional mission.json data (avoids re-reading if already loaded)

    Returns:
        Path to the mission's workspace directory

    Examples:
        >>> workspace = resolve_mission_workspace("mission_abc123", MISSIONS_DIR, WORKSPACE_DIR, io_utils)
        >>> str(workspace)
        '/home/vader/AI-AtlasForge/workspace/MyProject'  # shared workspace

        >>> workspace = resolve_mission_workspace("mission_old", MISSIONS_DIR, WORKSPACE_DIR, io_utils)
        >>> str(workspace)
        '/home/vader/AI-AtlasForge/missions/mission_old/workspace'  # legacy
    """
    missions_dir = Path(missions_dir)
    workspace_dir = Path(workspace_dir)
    mission_dir = missions_dir / mission_id

    # Priority 1: Check mission_config.json for project_workspace (shared workspace)
    config_path = mission_dir / "mission_config.json"
    if config_path.exists():
        try:
            config = io_utils.atomic_read_json(str(config_path), {})
            project_workspace = config.get("project_workspace")
            if project_workspace:
                pw_path = Path(project_workspace)
                if pw_path.exists():
                    logger.debug(f"Resolved workspace for {mission_id} from project_workspace: {pw_path}")
                    return pw_path
                else:
                    # Path configured but doesn't exist - still use it, caller can handle
                    logger.debug(f"project_workspace configured but doesn't exist: {pw_path}")
                    return pw_path
        except Exception as e:
            logger.warning(f"Error reading mission_config.json for {mission_id}: {e}")

    # Priority 2: Check mission_data for mission_workspace
    if mission_data:
        mission_workspace = mission_data.get("mission_workspace")
        if mission_workspace:
            mw_path = Path(mission_workspace)
            if mw_path.exists():
                logger.debug(f"Resolved workspace for {mission_id} from mission_data: {mw_path}")
                return mw_path

    # Priority 3: Fallback to legacy path
    legacy_path = mission_dir / "workspace"
    logger.debug(f"Using legacy workspace path for {mission_id}: {legacy_path}")
    return legacy_path


def get_workspace_info(
    mission_id: str,
    missions_dir: Union[Path, str],
    workspace_dir: Union[Path, str],
    io_utils
) -> Dict[str, Any]:
    """
    Get detailed workspace information for a mission.

    Returns:
        Dict containing:
        - workspace_path: Resolved workspace path (string)
        - is_shared: Whether this is a shared workspace
        - project_name: Project name if shared workspace, else None
        - exists: Whether the workspace directory exists
        - source: Where the path was resolved from ('config', 'state', 'legacy')

    Examples:
        >>> info = get_workspace_info("mission_abc123", MISSIONS_DIR, WORKSPACE_DIR, io_utils)
        >>> info
        {
            'workspace_path': '/home/vader/AI-AtlasForge/workspace/MyProject',
            'is_shared': True,
            'project_name': 'MyProject',
            'exists': True,
            'source': 'config'
        }
    """
    missions_dir = Path(missions_dir)
    workspace_dir = Path(workspace_dir)
    mission_dir = missions_dir / mission_id
    config_path = mission_dir / "mission_config.json"

    result = {
        "workspace_path": None,
        "is_shared": False,
        "project_name": None,
        "exists": False,
        "source": "legacy"
    }

    # Check for shared workspace in config
    if config_path.exists():
        try:
            config = io_utils.atomic_read_json(str(config_path), {})
            project_workspace = config.get("project_workspace")
            project_name = config.get("project_name")

            if project_workspace:
                pw_path = Path(project_workspace)
                result["workspace_path"] = str(pw_path)
                result["is_shared"] = True
                result["project_name"] = project_name
                result["exists"] = pw_path.exists()
                result["source"] = "config"
                return result
        except Exception as e:
            logger.warning(f"Error reading config for {mission_id}: {e}")

    # Legacy workspace
    legacy_path = mission_dir / "workspace"
    result["workspace_path"] = str(legacy_path)
    result["exists"] = legacy_path.exists()
    result["source"] = "legacy"

    return result


def resolve_workspace_for_file_listing(
    mission_data: Dict[str, Any],
    missions_dir: Union[Path, str],
    workspace_dir: Union[Path, str],
    io_utils
) -> Path:
    """
    Resolve workspace path optimized for file listing operations.

    This is a convenience wrapper that uses mission_data if available,
    falling back to config/legacy resolution.

    Args:
        mission_data: Mission data from mission.json
        missions_dir: Path to missions directory
        workspace_dir: Path to global workspace directory
        io_utils: io_utils module

    Returns:
        Path to the workspace directory
    """
    mission_id = mission_data.get("mission_id")

    if not mission_id:
        # No active mission, use global workspace
        return Path(workspace_dir)

    # First check if mission_data has mission_workspace directly
    mission_workspace = mission_data.get("mission_workspace")
    if mission_workspace:
        mw_path = Path(mission_workspace)
        if mw_path.exists():
            return mw_path

    # Fall back to standard resolution
    return resolve_mission_workspace(
        mission_id,
        missions_dir,
        workspace_dir,
        io_utils,
        mission_data
    )


def get_artifacts_path(
    mission_id: str,
    missions_dir: Union[Path, str],
    workspace_dir: Union[Path, str],
    io_utils,
    mission_data: Optional[Dict[str, Any]] = None
) -> Path:
    """
    Get the artifacts directory path for a mission.

    Args:
        mission_id: The mission ID
        missions_dir: Path to missions directory
        workspace_dir: Path to global workspace directory
        io_utils: io_utils module
        mission_data: Optional mission.json data

    Returns:
        Path to the mission's artifacts directory
    """
    workspace = resolve_mission_workspace(
        mission_id, missions_dir, workspace_dir, io_utils, mission_data
    )
    return workspace / "artifacts"


def validate_workspace_access(
    mission_id: str,
    missions_dir: Union[Path, str],
    workspace_dir: Union[Path, str],
    io_utils,
    requested_path: Union[Path, str]
) -> bool:
    """
    Validate that a requested path is within the mission's workspace.

    This is a security helper to prevent directory traversal attacks.

    Args:
        mission_id: The mission ID
        missions_dir: Path to missions directory
        workspace_dir: Path to global workspace directory
        io_utils: io_utils module
        requested_path: The path being requested

    Returns:
        True if the requested path is within the workspace, False otherwise
    """
    workspace = resolve_mission_workspace(
        mission_id, missions_dir, workspace_dir, io_utils
    )

    try:
        requested = Path(requested_path).resolve()
        workspace_resolved = workspace.resolve()
        requested.relative_to(workspace_resolved)
        return True
    except ValueError:
        return False
