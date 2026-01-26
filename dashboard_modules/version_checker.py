"""
Version Checker Module for AI-AtlasForge Dashboard

Provides version checking functionality for AtlasForge and AI-AfterImage.
Compares local git commits with remote to determine if updates are available.

Features:
- Checks AtlasForge version status (current repo)
- Checks AI-AfterImage version status (if installed)
- Caches results to avoid excessive git operations
- Provides API endpoint for dashboard
- **Developer Mode**: When a `.dev_mode` file exists in a repo root,
  version status displays "Developer Mode" instead of checking remote.
  This prevents false "update available" warnings during active development.

Usage:
    from dashboard_modules.version_checker import version_bp, init_version_blueprint
    init_version_blueprint(base_dir)
    app.register_blueprint(version_bp)
"""

import os
import subprocess
import time
from pathlib import Path
from flask import Blueprint, jsonify

version_bp = Blueprint('version', __name__, url_prefix='/api/version')

# Module-level state
_config = {
    'atlasforge_dir': None,
    'afterimage_dir': None,
    'cache_ttl': 300,  # Cache for 5 minutes
}

_cache = {
    'atlasforge': None,
    'afterimage': None,
    'last_check': 0,
}


def init_version_blueprint(base_dir: Path):
    """Initialize the version blueprint with necessary paths."""
    _config['atlasforge_dir'] = base_dir

    # Check common locations for AI-AfterImage
    possible_paths = [
        Path('/home/vader/Shared/AI-AfterImage'),
        Path('/home/vader/AI-AfterImage'),
        Path(os.path.expanduser('~/Shared/AI-AfterImage')),
        Path(os.path.expanduser('~/AI-AfterImage')),
    ]

    for path in possible_paths:
        if path.exists() and (path / '.git').exists():
            _config['afterimage_dir'] = path
            break


def _run_git_command(repo_dir: Path, *args) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    try:
        result = subprocess.run(
            ['git'] + list(args),
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _is_dev_mode(repo_dir: Path) -> bool:
    """Check if developer mode is enabled for this repository.

    Developer mode is signaled by the presence of a `.dev_mode` file
    in the repository root. This file should be gitignored.
    """
    if not repo_dir:
        return False
    dev_mode_file = repo_dir / '.dev_mode'
    return dev_mode_file.exists()


def _get_local_commit(repo_dir: Path) -> str | None:
    """Get the current local commit hash."""
    success, output = _run_git_command(repo_dir, 'rev-parse', 'HEAD')
    return output[:12] if success else None


def _get_local_version_tag(repo_dir: Path) -> str | None:
    """Get version from the most recent tag, if any."""
    success, output = _run_git_command(repo_dir, 'describe', '--tags', '--abbrev=0')
    return output if success else None


def _get_remote_commit(repo_dir: Path, branch: str = 'main') -> str | None:
    """Get the latest remote commit hash (after fetch)."""
    # First, do a fetch to get latest remote refs
    _run_git_command(repo_dir, 'fetch', '--quiet', 'origin')

    # Try main, then master
    for ref_branch in [branch, 'master', 'main']:
        success, output = _run_git_command(repo_dir, 'rev-parse', f'origin/{ref_branch}')
        if success:
            return output[:12]

    return None


def _get_commits_behind(repo_dir: Path, branch: str = 'main') -> int | None:
    """Get how many commits behind the remote we are."""
    for ref_branch in [branch, 'master', 'main']:
        success, output = _run_git_command(
            repo_dir, 'rev-list', '--count', f'HEAD..origin/{ref_branch}'
        )
        if success:
            try:
                return int(output)
            except ValueError:
                pass
    return None


def _get_commits_ahead(repo_dir: Path, branch: str = 'main') -> int | None:
    """Get how many commits ahead of the remote we are.

    This detects local commits that haven't been pushed to remote,
    indicating local customizations or unpushed work.
    """
    for ref_branch in [branch, 'master', 'main']:
        success, output = _run_git_command(
            repo_dir, 'rev-list', '--count', f'origin/{ref_branch}..HEAD'
        )
        if success:
            try:
                return int(output)
            except ValueError:
                pass
    return None


def _check_repo_version(repo_dir: Path, name: str) -> dict:
    """Check version status for a git repository."""
    if not repo_dir or not repo_dir.exists():
        return {
            'name': name,
            'installed': False,
            'status': 'not_installed',
            'message': 'Not Installed',
        }

    # Check for Developer Mode first - skip remote checks if active
    if _is_dev_mode(repo_dir):
        local_commit = _get_local_commit(repo_dir)
        version_tag = _get_local_version_tag(repo_dir)
        return {
            'name': name,
            'installed': True,
            'status': 'dev_mode',
            'message': 'Developer Mode',
            'version': version_tag or local_commit or 'dev',
            'local_commit': local_commit,
            'remote_check': False,
            'dev_mode': True,
            'note': 'Developer mode active - remote check skipped',
        }

    # Get local commit
    local_commit = _get_local_commit(repo_dir)
    if not local_commit:
        return {
            'name': name,
            'installed': True,
            'status': 'error',
            'message': 'Git Error',
            'error': 'Cannot read local commit',
        }

    # Get local version tag
    version_tag = _get_local_version_tag(repo_dir)

    # Get remote commit
    remote_commit = _get_remote_commit(repo_dir)

    if not remote_commit:
        # Offline or can't reach remote - assume up to date
        return {
            'name': name,
            'installed': True,
            'status': 'up_to_date',
            'message': 'Up To Date',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_check': False,
            'note': 'Could not check remote',
        }

    # Compare commits
    if local_commit == remote_commit:
        return {
            'name': name,
            'installed': True,
            'status': 'up_to_date',
            'message': 'Up To Date',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'remote_check': True,
        }

    # Commits differ - check ahead/behind relationship
    commits_behind = _get_commits_behind(repo_dir)
    commits_ahead = _get_commits_ahead(repo_dir)

    # Default to safe values if git commands fail
    commits_behind = commits_behind or 0
    commits_ahead = commits_ahead or 0

    # Determine status based on relationship
    if commits_behind > 0 and commits_ahead > 0:
        # Diverged: local has commits AND is missing remote commits
        return {
            'name': name,
            'installed': True,
            'status': 'diverged',
            'message': f'Diverged ({commits_ahead} ahead, {commits_behind} behind)',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'commits_ahead': commits_ahead,
            'commits_behind': commits_behind,
            'remote_check': True,
        }
    elif commits_ahead > 0:
        # Ahead: local has commits not on remote (custom changes)
        return {
            'name': name,
            'installed': True,
            'status': 'ahead',
            'message': f'Local Ahead ({commits_ahead} commits)',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'commits_ahead': commits_ahead,
            'remote_check': True,
        }
    elif commits_behind > 0:
        # Behind: remote has commits we don't have (update available)
        return {
            'name': name,
            'installed': True,
            'status': 'update_available',
            'message': f'Update Available ({commits_behind} commits)',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'commits_behind': commits_behind,
            'remote_check': True,
        }
    else:
        # Edge case: commits differ but both counts are 0
        # This shouldn't happen, but treat as up_to_date
        return {
            'name': name,
            'installed': True,
            'status': 'up_to_date',
            'message': 'Up To Date',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_commit': remote_commit,
            'remote_check': True,
            'note': 'Commits differ but no ahead/behind detected',
        }


def get_version_status(force_refresh: bool = False) -> dict:
    """Get version status for AtlasForge and AfterImage.

    Returns cached results unless force_refresh is True or cache has expired.
    """
    now = time.time()

    # Check if cache is still valid
    if not force_refresh and _cache['last_check'] > 0:
        if now - _cache['last_check'] < _config['cache_ttl']:
            return {
                'atlasforge': _cache['atlasforge'],
                'afterimage': _cache['afterimage'],
                'cached': True,
                'cache_age': int(now - _cache['last_check']),
            }

    # Check AtlasForge
    atlasforge_status = _check_repo_version(
        _config['atlasforge_dir'],
        'AtlasForge'
    )
    _cache['atlasforge'] = atlasforge_status

    # Check AI-AfterImage
    afterimage_status = _check_repo_version(
        _config['afterimage_dir'],
        'AI-AfterImage'
    )
    _cache['afterimage'] = afterimage_status

    _cache['last_check'] = now

    return {
        'atlasforge': atlasforge_status,
        'afterimage': afterimage_status,
        'cached': False,
        'checked_at': now,
    }


# =============================================================================
# API ROUTES
# =============================================================================

@version_bp.route('/status')
def api_version_status():
    """Get version status for all tracked projects.

    Returns:
        JSON with version status for AtlasForge and AI-AfterImage

    Query params:
        refresh: If 'true', force refresh the cache
    """
    from flask import request
    force_refresh = request.args.get('refresh', 'false').lower() == 'true'

    status = get_version_status(force_refresh=force_refresh)
    return jsonify(status)


@version_bp.route('/atlasforge')
def api_atlasforge_version():
    """Get version status for AtlasForge only."""
    status = get_version_status()
    return jsonify(status['atlasforge'])


@version_bp.route('/afterimage')
def api_afterimage_version():
    """Get version status for AI-AfterImage only."""
    status = get_version_status()
    return jsonify(status['afterimage'])


@version_bp.route('/refresh', methods=['POST'])
def api_refresh_versions():
    """Force refresh version status cache."""
    status = get_version_status(force_refresh=True)
    return jsonify({
        'success': True,
        'message': 'Version cache refreshed',
        **status
    })
