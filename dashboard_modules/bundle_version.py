"""
Bundle Version Module for AI-AtlasForge Dashboard

Provides automatic cache-busting for JavaScript and CSS bundles by computing
content hashes of the bundle files. The hash is appended as a query parameter
to bundle URLs (e.g., bundle.min.js?v=abc123).

Features:
- Computes MD5 hash of bundle file contents
- Falls back to git commit hash if files don't exist
- Falls back to timestamp as last resort
- Caches version to avoid repeated file reads
- Refreshes automatically when files change (via mtime check)

Usage:
    from dashboard_modules.bundle_version import get_bundle_version, init_bundle_version

    # Initialize with paths
    init_bundle_version(static_dir, base_dir)

    # Get version for template injection
    version = get_bundle_version()
    # Returns: {'js': 'abc123', 'css': 'def456', 'combined': 'abc123def456'}
"""

import hashlib
import subprocess
import time
from pathlib import Path
from typing import Optional


# Module-level state
_config = {
    'static_dir': None,
    'base_dir': None,
}

_cache = {
    'js_version': None,
    'css_version': None,
    'js_mtime': 0,
    'css_mtime': 0,
    'last_check': 0,
    'check_interval': 5,  # Check file mtime every 5 seconds
}


def init_bundle_version(static_dir: Path, base_dir: Path):
    """Initialize the bundle version module with necessary paths.

    Args:
        static_dir: Path to dashboard_static directory
        base_dir: Path to AtlasForge root (for git fallback)
    """
    _config['static_dir'] = static_dir
    _config['base_dir'] = base_dir


def _compute_file_hash(file_path: Path) -> Optional[str]:
    """Compute MD5 hash of a file's contents.

    Returns first 8 characters of the hash, or None if file doesn't exist.
    """
    if not file_path.exists():
        return None

    try:
        hasher = hashlib.md5()
        with open(file_path, 'rb') as f:
            # Read in chunks to handle large files efficiently
            for chunk in iter(lambda: f.read(65536), b''):
                hasher.update(chunk)
        return hasher.hexdigest()[:8]
    except (IOError, OSError):
        return None


def _get_git_commit_hash() -> Optional[str]:
    """Get current git commit hash as fallback version.

    Returns first 8 characters of HEAD commit, or None on failure.
    """
    if not _config['base_dir']:
        return None

    try:
        result = subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=str(_config['base_dir']),
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()[:8]
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        pass

    return None


def _get_timestamp_version() -> str:
    """Get timestamp-based version as last resort fallback."""
    return hex(int(time.time()))[-8:]


def _get_file_mtime(file_path: Path) -> float:
    """Get file modification time, or 0 if file doesn't exist."""
    try:
        return file_path.stat().st_mtime
    except (FileNotFoundError, OSError):
        return 0


def get_bundle_version(force_refresh: bool = False) -> dict:
    """Get version strings for bundle files.

    Returns a dict with:
        - js: Version hash for JavaScript bundle
        - css: Version hash for CSS bundle
        - combined: Combined version string for both files
        - source: How the version was computed ('content_hash', 'git', 'timestamp')

    The version is cached and only recomputed when:
        - force_refresh is True
        - File modification times have changed
        - check_interval (5 seconds) has passed since last mtime check
    """
    now = time.time()

    # Skip mtime check if within interval (unless forced)
    if not force_refresh and (now - _cache['last_check']) < _cache['check_interval']:
        if _cache['js_version'] and _cache['css_version']:
            return {
                'js': _cache['js_version'],
                'css': _cache['css_version'],
                'combined': _cache['js_version'] + _cache['css_version'],
                'source': 'cache',
            }

    _cache['last_check'] = now

    # Check if files have changed by comparing mtime
    static_dir = _config.get('static_dir')
    if not static_dir:
        # Fallback to git or timestamp
        fallback = _get_git_commit_hash() or _get_timestamp_version()
        return {
            'js': fallback,
            'css': fallback,
            'combined': fallback,
            'source': 'git' if _get_git_commit_hash() else 'timestamp',
        }

    js_path = static_dir / 'dist' / 'bundle.min.js'
    css_path = static_dir / 'dist' / 'bundle.min.css'

    js_mtime = _get_file_mtime(js_path)
    css_mtime = _get_file_mtime(css_path)

    # Check if we need to recompute
    js_changed = js_mtime != _cache['js_mtime'] or not _cache['js_version']
    css_changed = css_mtime != _cache['css_mtime'] or not _cache['css_version']

    source = 'cache'

    if js_changed or force_refresh:
        _cache['js_mtime'] = js_mtime
        js_hash = _compute_file_hash(js_path)
        if js_hash:
            _cache['js_version'] = js_hash
            source = 'content_hash'
        else:
            # Fallback to git commit
            git_hash = _get_git_commit_hash()
            if git_hash:
                _cache['js_version'] = git_hash
                source = 'git'
            else:
                _cache['js_version'] = _get_timestamp_version()
                source = 'timestamp'

    if css_changed or force_refresh:
        _cache['css_mtime'] = css_mtime
        css_hash = _compute_file_hash(css_path)
        if css_hash:
            _cache['css_version'] = css_hash
            if source == 'cache':
                source = 'content_hash'
        else:
            # Use same fallback as JS
            if source == 'git' or _get_git_commit_hash():
                _cache['css_version'] = _cache['js_version']
            else:
                _cache['css_version'] = _get_timestamp_version()

    return {
        'js': _cache['js_version'],
        'css': _cache['css_version'],
        'combined': _cache['js_version'] + _cache['css_version'],
        'source': source,
    }


def get_versioned_url(base_url: str, file_type: str = 'js') -> str:
    """Get a versioned URL for a bundle file.

    Args:
        base_url: The base URL (e.g., '/static/dist/bundle.min.js')
        file_type: 'js' or 'css' to determine which version to use

    Returns:
        URL with version query parameter (e.g., '/static/dist/bundle.min.js?v=abc123')
    """
    version = get_bundle_version()
    v = version.get(file_type, version['combined'])
    return f"{base_url}?v={v}"
