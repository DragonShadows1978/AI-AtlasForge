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

import copy
import ipaddress
import os
import secrets
import subprocess
import threading
import time
from pathlib import Path
from flask import Blueprint, jsonify, request, Response, stream_with_context

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
_cache_lock = threading.Lock()
_update_lock = threading.Lock()


def _normalize_repo_dir(repo_dir) -> Path | None:
    """Normalize a configured repository path without raising on bad input."""
    if repo_dir is None:
        return None
    try:
        return repo_dir if isinstance(repo_dir, Path) else Path(repo_dir)
    except (TypeError, ValueError):
        return None


def _reset_cache_locked() -> None:
    """Reset cached version status. Caller must hold _cache_lock."""
    _cache['atlasforge'] = None
    _cache['afterimage'] = None
    _cache['last_check'] = 0


def _cache_status_copy(status: dict | None) -> dict | None:
    """Return a defensive copy of a status dictionary."""
    return copy.deepcopy(status) if status is not None else None


def _safe_cache_ttl(value) -> float:
    """Coerce cache TTL to a non-negative float, falling back to 300 seconds."""
    try:
        ttl = float(value)
    except (TypeError, ValueError):
        return 300.0
    return max(0.0, ttl)


def _branch_fallbacks(branch: str = 'main') -> list[str]:
    """Return order-preserving, deduplicated remote branch fallbacks."""
    if not isinstance(branch, str) or not branch:
        branch = 'main'
    try:
        return list(dict.fromkeys([branch, 'master', 'main']))
    except TypeError:
        return ['main', 'master']


def _to_non_negative_int(value) -> int | None:
    """Return value as a non-negative int, or None when invalid."""
    if value is None:
        return None
    try:
        count = int(value)
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _relationship_error(
    name: str,
    version: str | None,
    local_commit: str,
    remote_commit: str,
    commits_behind,
    commits_ahead,
    install_method: str,
    message: str = 'Relationship Check Failed',
    note: str = 'Remote commit was found, but ahead/behind checks failed',
) -> dict:
    """Build a consistent relationship-check error response."""
    return {
        'name': name,
        'installed': True,
        'status': 'error',
        'message': message,
        'version': version or local_commit,
        'local_commit': local_commit,
        'remote_commit': remote_commit,
        'commits_behind': commits_behind,
        'commits_ahead': commits_ahead,
        'remote_check': False,
        'install_method': install_method,
        'error': 'Could not determine local/remote commit relationship',
        'note': note,
    }


def _request_is_local() -> bool:
    """Return True when the current Flask request came from loopback."""
    remote_addr = request.remote_addr
    if not remote_addr:
        return False
    try:
        return ipaddress.ip_address(remote_addr).is_loopback
    except ValueError:
        return False


def _version_update_authorized() -> tuple[bool, str]:
    """Authorize version updates using an explicit token or local opt-in."""
    token = os.environ.get('ATLASFORGE_UPDATE_TOKEN', '')
    if token:
        supplied = request.headers.get('X-AtlasForge-Update-Token', '')
        if secrets.compare_digest(supplied, token):
            return True, ''
        return False, 'Valid update token required'

    enabled = os.environ.get('ATLASFORGE_ENABLE_VERSION_UPDATE', '').lower() in {'1', 'true', 'yes', 'on'}
    if enabled and _request_is_local():
        return True, ''
    return False, 'Version update endpoint is disabled'


def _safe_commit(value, pattern) -> str:
    """Return a commit hash only when value is a string that matches pattern."""
    return value if isinstance(value, str) and pattern.match(value) else 'unknown'


def init_version_blueprint(base_dir: Path):
    """Initialize the version blueprint with necessary paths."""
    atlasforge_dir = _normalize_repo_dir(base_dir)

    # Check common locations for AI-AfterImage
    possible_paths = [
        Path('/home/vader/Shared/AI-AfterImage'),
        Path('/home/vader/AI-AfterImage'),
        Path(os.path.expanduser('~/Shared/AI-AfterImage')),
        Path(os.path.expanduser('~/AI-AfterImage')),
    ]

    afterimage_dir = None
    for path in possible_paths:
        if path.exists() and (path / '.git').exists():
            afterimage_dir = path
            break

    with _cache_lock:
        _config['atlasforge_dir'] = atlasforge_dir
        _config['afterimage_dir'] = afterimage_dir
        _reset_cache_locked()


def _run_git_command(repo_dir: Path, *args) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    repo_dir = _normalize_repo_dir(repo_dir)
    if not repo_dir:
        return False, "invalid repository path"
    try:
        result = subprocess.run(
            ['git'] + list(args),
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired as _te:
        try:
            _te.process.kill()
            _te.process.wait()
        except Exception:
            pass
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def _is_dev_mode(repo_dir: Path) -> bool:
    """Check if developer mode is enabled for this repository.

    Developer mode is signaled by the presence of a `.dev_mode` file
    in the repository root. This file should be gitignored.
    """
    repo_dir = _normalize_repo_dir(repo_dir)
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
    fetch_success, _ = _run_git_command(repo_dir, 'fetch', '--quiet', 'origin')
    if not fetch_success:
        return None

    # Try requested branch, then fallbacks — deduplicated, order preserved
    for ref_branch in _branch_fallbacks(branch):
        success, output = _run_git_command(repo_dir, 'rev-parse', f'origin/{ref_branch}')
        if success:
            return output[:12]

    return None


def _get_commits_behind(repo_dir: Path, branch: str = 'main') -> int | None:
    """Get how many commits behind the remote we are."""
    for ref_branch in _branch_fallbacks(branch):
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
    for ref_branch in _branch_fallbacks(branch):
        success, output = _run_git_command(
            repo_dir, 'rev-list', '--count', f'origin/{ref_branch}..HEAD'
        )
        if success:
            try:
                return int(output)
            except ValueError:
                pass
    return None


def detect_install_method(repo_dir: Path) -> str:
    """Detect how AtlasForge was installed: 'git', 'pip', or 'unknown'."""
    repo_dir = _normalize_repo_dir(repo_dir)
    if repo_dir and (repo_dir / '.git').exists():
        return 'git'
    import sys as _sys
    try:
        result = subprocess.run(
            [_sys.executable, '-m', 'pip', 'show', 'ai-atlasforge'],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return 'pip'
    except Exception:
        pass
    return 'unknown'


def _detect_remote_branch(repo_dir: Path) -> str:
    """Return the name of the tracking remote branch ('main' or 'master')."""
    for branch in ('main', 'master'):
        ok, _ = _run_git_command(repo_dir, 'rev-parse', '--verify', f'origin/{branch}')
        if ok:
            return branch
    return 'main'


def get_recent_commits(repo_dir: Path, base_ref: str, head_ref: str, max_count: int = 10) -> list[dict]:
    """Return up to max_count commits between base_ref and head_ref as list of {hash, subject, date}."""
    try:
        import re as _re
        _safe = _re.compile(r'^[a-zA-Z0-9_./@~^:\-]{1,200}$')
        if not _safe.match(base_ref) or not _safe.match(head_ref):
            return []
        max_count = max(1, max_count)
        result = subprocess.run(
            ['git', 'log', '--format=%H\t%s\t%ai', f'-{max_count}',
             f'{base_ref}..{head_ref}'],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15
        )
        if result.returncode != 0:
            return []
        commits = []
        for line in result.stdout.strip().splitlines():
            parts = line.split('\t', 2)
            if len(parts) == 3:
                commits.append({'hash': parts[0][:12], 'subject': parts[1], 'date': parts[2][:10]})
        return commits
    except Exception:
        return []


def _check_repo_version(repo_dir: Path, name: str) -> dict:
    """Check version status for a git repository."""
    repo_dir = _normalize_repo_dir(repo_dir)
    if not repo_dir or not repo_dir.exists():
        return {
            'name': name,
            'installed': False,
            'status': 'not_installed',
            'message': 'Not Installed',
        }

    install_method = detect_install_method(repo_dir)

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
            'install_method': install_method,
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
            'install_method': install_method,
        }

    # Get local version tag
    version_tag = _get_local_version_tag(repo_dir)

    # Get remote commit
    remote_commit = _get_remote_commit(repo_dir)

    if not remote_commit:
        return {
            'name': name,
            'installed': True,
            'status': 'offline',
            'message': 'Offline (remote unreachable)',
            'version': version_tag or local_commit,
            'local_commit': local_commit,
            'remote_check': False,
            'install_method': install_method,
            'note': 'Could not reach remote to check for updates',
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
            'install_method': install_method,
        }

    # Commits differ - check ahead/behind relationship
    commits_behind = _get_commits_behind(repo_dir)
    commits_ahead = _get_commits_ahead(repo_dir)

    normalized_behind = _to_non_negative_int(commits_behind)
    normalized_ahead = _to_non_negative_int(commits_ahead)
    if normalized_behind is None or normalized_ahead is None:
        return _relationship_error(
            name,
            version_tag,
            local_commit,
            remote_commit,
            commits_behind,
            commits_ahead,
            install_method,
        )

    commits_behind = normalized_behind
    commits_ahead = normalized_ahead

    remote_branch = _detect_remote_branch(repo_dir)
    if commits_behind > 0 and commits_ahead > 0:
        recent = get_recent_commits(repo_dir, 'HEAD', f'origin/{remote_branch}', max_count=10)
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
            'install_method': install_method,
            'recent_commits': recent,
        }
    elif commits_ahead > 0:
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
            'install_method': install_method,
        }
    elif commits_behind > 0:
        recent = get_recent_commits(repo_dir, 'HEAD', f'origin/{remote_branch}', max_count=10)
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
            'install_method': install_method,
            'recent_commits': recent,
        }
    else:
        return _relationship_error(
            name,
            version_tag,
            local_commit,
            remote_commit,
            commits_behind,
            commits_ahead,
            install_method,
            message='Relationship Check Inconclusive',
            note='Commits differ but no ahead/behind relationship was detected',
        )


def get_version_status(force_refresh: bool = False) -> dict:
    """Get version status for AtlasForge and AfterImage.

    Returns cached results unless force_refresh is True or cache has expired.
    """
    with _cache_lock:
        now = time.time()
        cache_ttl = _safe_cache_ttl(_config.get('cache_ttl', 300))

        # Check if cache is still valid
        if not force_refresh and _cache['last_check'] > 0:
            if now - _cache['last_check'] < cache_ttl:
                return {
                    'atlasforge': _cache_status_copy(_cache['atlasforge']),
                    'afterimage': _cache_status_copy(_cache['afterimage']),
                    'cached': True,
                    'cache_age': int(now - _cache['last_check']),
                }

        # Check AtlasForge
        atlasforge_status = _check_repo_version(
            _config['atlasforge_dir'],
            'AtlasForge'
        )

        # Check AI-AfterImage
        afterimage_status = _check_repo_version(
            _config['afterimage_dir'],
            'AI-AfterImage'
        )
        checked_at = time.time()

        _cache['atlasforge'] = _cache_status_copy(atlasforge_status)
        _cache['afterimage'] = _cache_status_copy(afterimage_status)
        _cache['last_check'] = checked_at

        return {
            'atlasforge': _cache_status_copy(atlasforge_status),
            'afterimage': _cache_status_copy(afterimage_status),
            'cached': False,
            'checked_at': checked_at,
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


@version_bp.route('/changelog')
def api_version_changelog():
    """Return recent commits between local HEAD and remote for AtlasForge."""
    repo_dir = _config.get('atlasforge_dir')
    if not repo_dir:
        return jsonify({'success': False, 'commits': [], 'error': 'No repo configured'})
    try:
        max_count = max(1, min(int(request.args.get('max', 15)), 100))
    except (ValueError, TypeError):
        return jsonify({'success': False, 'commits': [], 'error': 'max must be an integer'}), 400
    remote_branch = _detect_remote_branch(repo_dir)
    commits = get_recent_commits(repo_dir, 'HEAD', f'origin/{remote_branch}', max_count=max_count)
    return jsonify({'success': True, 'commits': commits})


@version_bp.route('/update', methods=['POST'])
def api_version_update():
    """Stream the update command output.

    Body: {"method": "git" | "pip", "force": false}
    Streams line-by-line output of the update command via chunked response.
    Only allowed when status is update_available or diverged (or force=true).
    """
    authorized, auth_error = _version_update_authorized()
    if not authorized:
        return jsonify({'success': False, 'error': auth_error}), 403

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({'success': False, 'error': 'Request body must be a JSON object'}), 400

    method = data.get('method', 'git')
    if not isinstance(method, str):
        return jsonify({'success': False, 'error': 'method must be a string'}), 400

    force = data.get('force', False)
    if not isinstance(force, bool):
        return jsonify({'success': False, 'error': 'force must be a JSON boolean'}), 400

    repo_dir = _normalize_repo_dir(_config.get('atlasforge_dir'))
    if not repo_dir:
        return jsonify({'success': False, 'error': 'No repo configured'}), 400

    # Safety check: only allow when update is actually available, unless forced
    if not force:
        status = get_version_status()
        af = status.get('atlasforge', {})
        allowed_states = {'update_available', 'diverged'}
        if af.get('status') not in allowed_states:
            return jsonify({
                'success': False,
                'error': f"Update not available (status: {af.get('status')}). Pass force=true to override."
            }), 400

    if method == 'git':
        _remote_branch = _detect_remote_branch(repo_dir)
        cmd = ['git', 'pull', '--ff-only', 'origin', _remote_branch]
        cwd = str(repo_dir)
    elif method == 'pip':
        import sys as _sys
        # --break-system-packages handles PEP 668 on modern Linux distros (Ubuntu 23+, Debian 12+)
        # where pip refuses to modify system-managed packages without this flag.
        cmd = [_sys.executable, '-m', 'pip', 'install', '--upgrade',
               '--break-system-packages', 'ai-atlasforge']
        cwd = None
    else:
        return jsonify({'success': False, 'error': f'Unknown method: {method}'}), 400

    if not _update_lock.acquire(blocking=False):
        return jsonify({'success': False, 'error': 'Version update already in progress'}), 409

    def generate():
        yield f'[AtlasForge Update] Running: {" ".join(cmd)}\n'
        proc = None
        try:
            proc = subprocess.Popen(
                cmd, cwd=cwd,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1
            )
            for line in proc.stdout:
                yield line
            proc.wait()
            if proc.returncode == 0:
                yield '\n[AtlasForge Update] SUCCESS\n'
                with _cache_lock:
                    _cache['last_check'] = 0
            else:
                yield f'\n[AtlasForge Update] FAILED (exit code {proc.returncode})\n'
        except Exception as e:
            yield f'\n[AtlasForge Update] ERROR: {e}\n'
        finally:
            if proc is not None and proc.poll() is None:
                proc.kill()
                proc.wait()
            _update_lock.release()

    return Response(stream_with_context(generate()), mimetype='text/plain',
                    headers={'X-Accel-Buffering': 'no', 'Cache-Control': 'no-cache'})


@version_bp.route('/compatibility-check', methods=['POST'])
def api_version_compatibility_check():
    """Launch a headless compatibility investigation for incoming commits.

    Returns: {"success": true, "investigation_id": "inv_xxx"}
    """
    repo_dir = _normalize_repo_dir(_config.get('atlasforge_dir'))
    if not repo_dir:
        return jsonify({'success': False, 'error': 'No repo configured'}), 400

    import re as _re
    _hex_re = _re.compile(r'^[0-9a-f]{7,40}$')

    status = get_version_status()
    af = status.get('atlasforge', {})
    local_commit = _safe_commit(af.get('local_commit', ''), _hex_re)
    remote_commit = _safe_commit(af.get('remote_commit', ''), _hex_re)
    commits_behind = _to_non_negative_int(af.get('commits_behind', 0)) or 0
    commits_ahead = _to_non_negative_int(af.get('commits_ahead', 0)) or 0

    query = (
        f"AtlasForge compatibility check: assess the incoming {commits_behind} commits "
        f"from local {local_commit} to remote {remote_commit}. "
        f"Identify: breaking changes, config file changes, new dependencies, "
        f"migration steps needed, and whether a git pull is safe to run. "
        f"Local repo is {commits_ahead} commits ahead (custom changes). "
        f"Summarize risk level (low/medium/high) and recommended action."
    )

    try:
        import requests as req_lib
        try:
            port = int(os.environ.get('PORT', '5010'))
        except ValueError:
            port = 5010
        from pathlib import Path as _Path
        _ssl_cert = _Path(__file__).parent.parent / 'certs' / 'cert.pem'
        _use_https = os.environ.get('DASHBOARD_SSL', 'true').lower() == 'true' and _ssl_cert.exists()
        _proto = 'https' if _use_https else 'http'
        resp = req_lib.post(
            f'{_proto}://127.0.0.1:{port}/api/investigation/start',
            json={'query': query, 'max_subagents': 3, 'timeout_minutes': 8},
            timeout=10,
            verify=False,
        )
        result = resp.json()
        if result.get('success'):
            return jsonify({'success': True, 'investigation_id': result.get('investigation_id')})
        return jsonify({'success': False, 'error': result.get('message', 'Investigation start failed')}), 500
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
