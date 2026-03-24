"""
af_engine.integrations.post_mission_hooks - Custom Post-Mission Scripts

This integration runs custom scripts after mission completion.
"""

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import List, Optional

from .base import (
    BaseIntegrationHandler,
    Event,
    StageEvent,
    IntegrationPriority,
)

logger = logging.getLogger(__name__)

ALLOWED_SHEBANGS = frozenset([
    "#!/bin/bash",
    "#!/bin/sh",
    "#!/usr/bin/env bash",
    "#!/usr/bin/env python3",
    "#!/usr/bin/env python",
])

ALLOWED_FILENAME_RE = re.compile(r'^[a-zA-Z0-9_-]+\.(sh|py|bash)$')


class PostMissionHooksIntegration(BaseIntegrationHandler):
    """
    Executes custom scripts after mission completion.

    Looks for executable scripts in the hooks directory and
    runs them with mission context as environment variables.
    """

    name = "post_mission_hooks"
    priority = IntegrationPriority.BACKGROUND
    subscriptions = [
        StageEvent.MISSION_COMPLETED,
    ]

    def __init__(self, hooks_dir: Optional[Path] = None):
        """Initialize post-mission hooks."""
        super().__init__()
        self.hooks_dir = hooks_dir or Path(".af_hooks/post_mission")

    def on_mission_completed(self, event: Event) -> None:
        """Run post-mission hooks."""
        if not self.hooks_dir.exists():
            return

        hooks = self._find_hooks()
        if not hooks:
            return

        # Build environment with mission context
        env = self._build_env(event)

        for hook in hooks:
            self._run_hook(hook, env)

    def _validate_hook(self, hook: Path) -> tuple:
        """Validate a hook script before execution.

        Returns (True, "ok") if the hook passes all security checks,
        or (False, reason) if it should be rejected.
        """
        # 0. Reject symlinks early to close the TOCTOU window between resolve() and stat().
        # hook.lstat() returns the stat of the symlink itself, not its target.
        # If the path is a symlink, rejecting here means resolve() and stat() can never
        # disagree about which file they're examining — they both operate on the real file.
        try:
            _lstat = hook.lstat()
        except FileNotFoundError:
            return False, "file no longer exists"
        import stat as _stat_mod
        if _stat_mod.S_ISLNK(_lstat.st_mode):
            return False, "symlinks not allowed in hooks_dir"

        # 1. Resolve symlinks — containment check
        try:
            real = hook.resolve()
            hooks_real = self.hooks_dir.resolve()
            real.relative_to(hooks_real)  # raises ValueError if outside
        except (ValueError, OSError):
            return False, "path escapes hooks_dir (possible symlink attack)"

        # 2. Filename pattern
        if not ALLOWED_FILENAME_RE.match(hook.name):
            return False, f"filename '{hook.name}' does not match allowed pattern"

        # 3. Ownership — must be owned by current process UID
        # Use the already-acquired lstat result — it refers to the same inode as hook
        # because we rejected symlinks above. This eliminates the stat() TOCTOU window.
        stat = _lstat
        if stat.st_uid != os.getuid():
            return False, f"not owned by current user (owner uid={stat.st_uid})"

        # 4. World-writable check
        if stat.st_mode & 0o002:
            return False, "world-writable — rejecting to prevent tampering"

        # 5. Shebang allowlist — open with O_NOFOLLOW to close TOCTOU window between
        # stat() check above and this read; prevents symlink swap attacks.
        try:
            import io as _io
            _fd = os.open(str(hook), os.O_RDONLY | os.O_NOFOLLOW)
            _fd_owned = False
            try:
                # closefd=True transfers fd ownership to the file object.
                # Once io.open() succeeds, the file object owns _fd and closes it
                # on exit — we must NOT call os.close(_fd) after this point.
                f_obj = _io.open(_fd, 'rb', closefd=True)
                _fd_owned = True
                with f_obj:
                    first_line = f_obj.readline(256).decode('utf-8', errors='replace').rstrip()
            except BaseException:
                if not _fd_owned:
                    os.close(_fd)
                raise
        except OSError as e:
            return False, f"cannot read file: {e}"

        if first_line not in ALLOWED_SHEBANGS:
            return False, f"shebang '{first_line[:60]}' not in allowed list"

        return True, "ok"

    def _find_hooks(self) -> List[Path]:
        """Find executable hook scripts, applying security validation."""
        hooks = []

        if not self.hooks_dir.is_dir():
            return hooks

        try:
            entries = sorted(self.hooks_dir.iterdir())
        except PermissionError as e:
            logger.warning("Cannot enumerate hooks_dir %s: %s", self.hooks_dir, e)
            return hooks

        for item in entries:
            # P2: guard is_file() against FileNotFoundError for files deleted during iteration
            try:
                if not item.is_file():
                    continue
            except FileNotFoundError:
                logger.debug("Hook file disappeared during enumeration: %s", item.name)
                continue
            # Permission check is deferred to _validate_hook() — the authoritative check,
            # avoiding a TOCTOU race between stat() here and the actual validation.
            valid, reason = self._validate_hook(item)
            if valid:
                hooks.append(item)
            else:
                # Iter 3 Fix L2: use %s formatting to prevent log injection via control chars
                logger.warning("Hook %s rejected: %s", item.name, reason)

        return hooks

    def _build_env(self, event: Event) -> dict:
        """Build environment variables for hook execution."""
        # Harden PATH: exclude inherited PATH entirely to prevent CWE-426 (Untrusted Search Path).
        # An attacker-controlled directory prepended to PATH could cause a rogue binary to execute
        # instead of the real bash/python3/env interpreter.  Use a hardcoded minimal safe PATH.
        # Use exact-key allowlist (not startswith) to prevent prefix-match bypass (e.g. HOMEBASE).
        _ALLOWED_ENV_KEYS = frozenset(['HOME', 'USER', 'SHELL', 'TERM', 'LANG'])
        _ALLOWED_ENV_PREFIXES = ('LC_',)
        env = {k: v for k, v in os.environ.items()
               if k in _ALLOWED_ENV_KEYS or k.startswith(_ALLOWED_ENV_PREFIXES)}
        env['PATH'] = '/usr/local/bin:/usr/bin:/bin'

        # Iter 6 Fix: coerce mission_id/stage to str to prevent type-confusion via truthy non-str.
        # `x or ""` does not enforce str — a truthy int/object bypasses the fallback.
        env["AF_MISSION_ID"] = str(event.mission_id) if event.mission_id is not None else ""
        env["AF_STAGE"] = str(event.stage) if event.stage is not None else ""

        # Iter 3 Fix M7: guard against event.data being None
        # Iter 4 Fix B7: also guard against non-dict event.data
        data = event.data if isinstance(event.data, dict) else {}
        # Iter 6 Fix: normalize total_cycles to non-negative int; bool subclass of int handled too.
        raw_cycles = data.get("total_cycles", 0)
        try:
            cycles_int = max(0, int(raw_cycles))
        except (TypeError, ValueError, OverflowError):
            # OverflowError: int(float('inf')) raises OverflowError — treat as 0
            cycles_int = 0
        env["AF_TOTAL_CYCLES"] = str(cycles_int)
        # Cycle 2 Fix 4.3: normalize deliverables — if string, wrap in list
        # Iter 6 Fix: skip None entries; deliverable values are str-coerced safely.
        deliverables = data.get("deliverables", [])
        if isinstance(deliverables, str):
            deliverables = [deliverables]
        elif not isinstance(deliverables, list):
            deliverables = []
        env["AF_DELIVERABLES"] = "\n".join(str(d) for d in deliverables if d is not None)

        return env

    # Map validated shebangs to their canonical interpreter binary.
    # Used by _run_hook to invoke the interpreter explicitly (required because
    # Linux cannot exec shebang scripts directly via /proc/self/fd/N paths).
    _SHEBANG_TO_INTERPRETER = {
        "#!/bin/bash":              "/bin/bash",
        "#!/bin/sh":                "/bin/sh",
        "#!/usr/bin/env bash":      "/bin/bash",
        "#!/usr/bin/env python3":   "/usr/bin/python3",
        "#!/usr/bin/env python":    "/usr/bin/python3",
    }

    def _open_and_validate_hook(self, hook: Path):
        """Open hook fd with O_NOFOLLOW and validate via fstat — immune to path-swap TOCTOU.

        Returns (fd, interpreter) on success or (None, reason_str) on failure.
        The caller MUST close the fd when done.

        The interpreter is the absolute path to the shebang interpreter, resolved
        from the validated shebang line read directly from the open fd.
        """
        # Open without following symlinks; raises OSError(ELOOP) on symlink
        try:
            fd = os.open(str(hook), os.O_RDONLY | os.O_NOFOLLOW)
        except OSError as e:
            return None, f"open() failed (possible symlink): {e}"

        try:
            st = os.fstat(fd)

            # Ownership — must be owned by current process UID
            if st.st_uid != os.getuid():
                os.close(fd)
                return None, f"not owned by current user (owner uid={st.st_uid})"

            # World-writable check
            if st.st_mode & 0o002:
                os.close(fd)
                return None, "world-writable — rejecting to prevent tampering"

            # Shebang allowlist — read from already-open fd to pin the inode.
            # Explicit seek to 0 guards against any future code between os.open()
            # and here that might advance the fd cursor (defensive, not just implicit).
            import io
            os.lseek(fd, 0, os.SEEK_SET)
            with io.open(fd, 'rb', closefd=False) as f:
                first_line = f.readline(256).decode('utf-8', errors='replace').rstrip()

            if first_line not in ALLOWED_SHEBANGS:
                os.close(fd)
                return None, f"shebang '{first_line[:60]}' not in allowed list"

        except OSError as e:
            os.close(fd)
            return None, f"fstat/read failed: {e}"

        interpreter = self._SHEBANG_TO_INTERPRETER[first_line]
        return fd, interpreter

    def _run_hook(self, hook: Path, env: dict) -> bool:
        """Execute a hook script."""
        # Eliminate TOCTOU: open fd with O_NOFOLLOW, validate via fstat(), then execute
        # via [interpreter, /proc/self/fd/{fd}] with pass_fds=(fd,) so the kernel runs
        # the same inode that was validated.  No path-swap window between validation and
        # execution.  Explicit interpreter is required because Linux cannot execve()
        # shebang scripts through /proc/self/fd symlinks directly.
        fd, interpreter_or_reason = self._open_and_validate_hook(hook)
        if fd is None:
            logger.warning("Hook %s failed pre-execution validation: %s", hook.name, interpreter_or_reason)
            return False
        interpreter = interpreter_or_reason
        proc_fd_path = f"/proc/self/fd/{fd}"
        try:
            result = subprocess.run(
                [interpreter, proc_fd_path],
                env=env,
                capture_output=True,
                timeout=60,  # 1 minute timeout
                pass_fds=(fd,),
            )
        except subprocess.TimeoutExpired:
            logger.warning("Hook %s timed out", hook.name)
            return False
        except Exception as e:
            logger.warning("Hook %s error: %s", hook.name, e)
            return False
        finally:
            # Always close fd — guards against BaseException (KeyboardInterrupt, etc.)
            os.close(fd)

        if result.returncode == 0:
            # Iter 4 Fix A3: use %s lazy formatting to prevent log injection
            logger.info("Hook %s executed successfully", hook.name)
            return True
        else:
            # Iter 4 Fix B8: use errors='replace' for stderr decode
            stderr_text = result.stderr.decode(errors='replace')
            logger.warning(
                "Hook %s failed with code %s: %s",
                hook.name, result.returncode, stderr_text
            )
            return False
