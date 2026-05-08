"""
Bundle Builder Module for AI-AtlasForge Dashboard

Detects stale JavaScript/CSS bundles at dashboard startup and rebuilds them
synchronously by invoking ``npm run build`` before the first request is served.

Why this exists:
The dashboard ships compiled assets in ``dashboard_static/dist/`` that are
generated from sources in ``dashboard_static/src/`` and ``dashboard_static/css/``.
Agents (and humans) routinely edit the sources but forget to run ``npm run build``,
which silently leaves the browser serving the previous mission's UI. This
module fails that gap closed by checking mtimes on every dashboard start and
running the build script when sources are newer than the dist bundle.

Behavior:
- Scans ``dashboard_static/src/**/*.{js,mjs}`` and ``dashboard_static/css/**/*.css``
  for the newest source mtime.
- Compares against the mtime of ``dashboard_static/dist/bundle.min.js``.
- If src is newer (or dist is missing), runs ``npm run build`` from ``base_dir``.
- Skips entirely when ``ATLASFORGE_SKIP_BUNDLE_BUILD=1`` is set (CI / packaged
  installs that pre-build the bundle).
- Logs a warning and returns ``status='no_npm'`` when ``npm`` isn't on PATH;
  never crashes the dashboard over a build failure.

Usage:
    from dashboard_modules.bundle_builder import ensure_bundle_fresh
    result = ensure_bundle_fresh(STATIC_DIR, BASE_DIR)
    # result == {'status': 'fresh' | 'rebuilt' | 'skipped' | 'no_npm' |
    #                      'build_failed' | 'no_dist',
    #            'duration_ms': 71.4, 'reason': '...'}
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

_SOURCE_GLOBS = ('src/**/*.js', 'src/**/*.mjs', 'css/**/*.css')
_BUNDLE_RELATIVE = Path('dist') / 'bundle.min.js'
_BUILD_TIMEOUT_SECONDS = 120
_SKIP_ENV_VAR = 'ATLASFORGE_SKIP_BUNDLE_BUILD'


def _newest_mtime(static_dir: Path, globs: Iterable[str]) -> float:
    """Return the largest mtime across all files matching the given globs.

    Returns 0.0 if nothing matches.
    """
    newest = 0.0
    for pattern in globs:
        for path in static_dir.glob(pattern):
            try:
                m = path.stat().st_mtime
            except OSError:
                continue
            if m > newest:
                newest = m
    return newest


def _bundle_mtime(static_dir: Path) -> float:
    bundle = static_dir / _BUNDLE_RELATIVE
    try:
        return bundle.stat().st_mtime
    except OSError:
        return 0.0


def ensure_bundle_fresh(static_dir: Path, base_dir: Path, force: bool = False) -> dict:
    """Verify the dashboard bundle is up to date, rebuilding if not.

    Args:
        static_dir: Path to ``dashboard_static`` (contains ``src/``, ``css/``, ``dist/``).
        base_dir: Repo root that owns ``package.json`` (where ``npm run build`` runs).
        force: When True, rebuild unconditionally (used by tests).

    Returns:
        dict with ``status`` and supporting fields. Never raises — build
        failures are logged and reported via the returned status so the
        dashboard can still serve the (possibly stale) bundle.
    """
    started = time.monotonic()

    if os.environ.get(_SKIP_ENV_VAR) == '1' and not force:
        logger.info("Bundle build skipped: %s=1", _SKIP_ENV_VAR)
        return {
            'status': 'skipped',
            'reason': f'{_SKIP_ENV_VAR}=1',
            'duration_ms': 0.0,
        }

    static_dir = Path(static_dir)
    base_dir = Path(base_dir)

    src_mtime = _newest_mtime(static_dir, _SOURCE_GLOBS)
    dist_mtime = _bundle_mtime(static_dir)

    if not force and dist_mtime > 0 and src_mtime <= dist_mtime:
        return {
            'status': 'fresh',
            'src_mtime': src_mtime,
            'dist_mtime': dist_mtime,
            'duration_ms': (time.monotonic() - started) * 1000,
        }

    if dist_mtime == 0:
        reason = 'dist bundle missing'
    elif force:
        reason = 'forced rebuild'
    else:
        reason = f'src newer than dist (src={src_mtime:.0f} dist={dist_mtime:.0f})'

    npm = shutil.which('npm')
    if not npm:
        logger.warning(
            "Bundle is stale (%s) but npm is not on PATH; serving existing bundle. "
            "Install Node.js or set %s=1 to silence this warning.",
            reason, _SKIP_ENV_VAR,
        )
        return {
            'status': 'no_npm',
            'reason': reason,
            'duration_ms': (time.monotonic() - started) * 1000,
        }

    if not (base_dir / 'package.json').exists():
        logger.warning(
            "Bundle is stale (%s) but %s/package.json is missing; cannot rebuild.",
            reason, base_dir,
        )
        return {
            'status': 'no_package_json',
            'reason': reason,
            'duration_ms': (time.monotonic() - started) * 1000,
        }

    logger.info("Rebuilding dashboard bundle (%s)...", reason)
    try:
        proc = subprocess.run(
            [npm, 'run', 'build'],
            cwd=str(base_dir),
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.error("Bundle rebuild timed out after %ss", _BUILD_TIMEOUT_SECONDS)
        return {
            'status': 'build_failed',
            'reason': 'timeout',
            'duration_ms': (time.monotonic() - started) * 1000,
        }
    except OSError as exc:
        logger.error("Bundle rebuild failed to start: %s", exc)
        return {
            'status': 'build_failed',
            'reason': f'spawn error: {exc}',
            'duration_ms': (time.monotonic() - started) * 1000,
        }

    duration_ms = (time.monotonic() - started) * 1000

    if proc.returncode != 0:
        logger.error(
            "Bundle rebuild exited %s in %.0fms. stderr:\n%s",
            proc.returncode, duration_ms, (proc.stderr or '').strip(),
        )
        return {
            'status': 'build_failed',
            'reason': f'exit {proc.returncode}',
            'returncode': proc.returncode,
            'stderr': proc.stderr,
            'duration_ms': duration_ms,
        }

    new_dist_mtime = _bundle_mtime(static_dir)
    if new_dist_mtime == 0:
        logger.warning(
            "npm build exited 0 but %s does not exist.",
            _BUNDLE_RELATIVE,
        )
        return {
            'status': 'no_dist',
            'reason': 'build succeeded but bundle.min.js was not produced',
            'duration_ms': duration_ms,
        }
    if new_dist_mtime <= dist_mtime and dist_mtime > 0:
        logger.warning(
            "npm build reported success but %s did not update (mtime unchanged).",
            _BUNDLE_RELATIVE,
        )
        return {
            'status': 'no_dist',
            'reason': 'build succeeded but bundle.min.js unchanged',
            'duration_ms': duration_ms,
        }

    logger.info("Bundle rebuilt in %.0fms (%s)", duration_ms, reason)
    return {
        'status': 'rebuilt',
        'reason': reason,
        'duration_ms': duration_ms,
        'src_mtime': src_mtime,
        'dist_mtime': new_dist_mtime,
        'stdout_tail': (proc.stdout or '').splitlines()[-3:],
    }
