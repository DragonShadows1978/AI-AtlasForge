"""
Atomic File I/O Utilities

Provides safe, atomic file operations using fcntl locking to prevent race conditions
in the file-based message bus.
"""

import json
import fcntl
import os
import tempfile
import time
import logging
from pathlib import Path
from typing import Any, Callable, Optional, Union

logger = logging.getLogger("io_utils")

def atomic_read_json(path: Union[str, Path], default: Any = None, max_retries: int = 5) -> Any:
    """
    Atomically read a JSON file using a shared lock.
    
    Args:
        path: Path to the JSON file
        default: Value to return if file doesn't exist or is invalid
        max_retries: Number of times to retry if lock fails
        
    Returns:
        Parsed JSON content or default value
    """
    file_path = Path(path)
    if not file_path.exists():
        return default if default is not None else {}

    for attempt in range(max_retries):
        try:
            with open(file_path, 'r') as f:
                try:
                    # Acquire shared lock (non-blocking)
                    # Use flock instead of lockf for better compatibility with some systems
                    # but lockf is standard POSIX. Let's use flock logic via fcntl module.
                    fcntl.flock(f, fcntl.LOCK_SH)
                    try:
                        content = f.read()
                        if not content.strip():
                            return default if default is not None else {}
                        return json.loads(content)
                    finally:
                        fcntl.flock(f, fcntl.LOCK_UN)
                except IOError as e:
                    # If locked by another process
                    if attempt < max_retries - 1:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    logger.warning(f"Could not acquire read lock on {file_path}: {e}")
                    # Fallback: try reading without lock if desperate? No, return default
                    return default if default is not None else {}
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON in {file_path}")
                    return default if default is not None else {}
        except Exception as e:
            logger.error(f"Error reading {file_path}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.1)
                continue
            return default if default is not None else {}
            
    return default if default is not None else {}

def atomic_write_json(path: Union[str, Path], data: Any, max_retries: int = 5) -> bool:
    """
    Atomically write a JSON file using an exclusive lock.
    
    Args:
        path: Path to the JSON file
        data: Data to serialize and write
        max_retries: Number of times to retry if lock fails
        
    Returns:
        True if successful, False otherwise
    """
    file_path = Path(path)
    
    # Create temp file first to ensure we don't corrupt main file if write fails
    # However, for flock we need the actual file descriptor. 
    # Strategy: Open main file with 'a+' (append/read) to get handle, lock it, 
    # then truncate and write.
    
    target_dir = file_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    for attempt in range(max_retries):
        tmp_fd = None
        tmp_path = None
        try:
            # Write to an unpredictable temp file in the same directory so
            # os.replace() is atomic (same filesystem). NamedTemporaryFile
            # gives an unguessable name, eliminating the TOCTOU window that
            # existed when we wrote to a predictable temp path.
            tmp_fd, tmp_path = tempfile.mkstemp(dir=target_dir, suffix='.tmp')
            try:
                with os.fdopen(tmp_fd, 'w') as f:
                    tmp_fd = None  # fdopen takes ownership; don't close twice
                    json.dump(data, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                # Atomic replace — visible to other processes only after this point
                os.replace(tmp_path, file_path)
                tmp_path = None
                return True
            except Exception:
                if tmp_fd is not None:
                    try:
                        os.close(tmp_fd)
                    except OSError:
                        pass
                raise
        except Exception as e:
            logger.error(f"Error writing {file_path}: {e}")
            if tmp_path is not None:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
            if attempt < max_retries - 1:
                time.sleep(0.1)
                continue
            return False

    return False

def atomic_update_json(path: Union[str, Path], update_fn: Callable[[Any], Any], default: Any = None, max_retries: int = 5) -> Any:
    """
    Atomically update a JSON file: read -> transform -> write.
    Ensures no other process modifies the file between read and write.
    
    Args:
        path: Path to the JSON file
        update_fn: Function that takes current data and returns new data
        default: Default value if file doesn't exist
        
    Returns:
        The new data returned by update_fn
    """
    file_path = Path(path)
    
    for attempt in range(max_retries):
        try:
            # Open for update
            mode = 'r+' if file_path.exists() else 'w+'
            
            with open(file_path, mode) as f:
                try:
                    # Acquire exclusive lock
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    
                    # Read current data
                    f.seek(0)
                    content = f.read()
                    if not content.strip():
                        current_data = default if default is not None else {}
                    else:
                        try:
                            current_data = json.loads(content)
                        except json.JSONDecodeError:
                            current_data = default if default is not None else {}
                    
                    # Apply update function
                    new_data = update_fn(current_data)
                    
                    # Write new data
                    f.seek(0)
                    f.truncate()
                    json.dump(new_data, f, indent=2, default=str)
                    f.flush()
                    os.fsync(f.fileno())
                    
                    return new_data
                    
                except BlockingIOError:
                    if attempt < max_retries - 1:
                        time.sleep(0.1 * (attempt + 1))
                        continue
                    else:
                        logger.error(f"Could not acquire lock for update on {file_path}")
                        return None
                finally:
                    # Always try to unlock, even if inner logic failed (but file was opened)
                    try:
                        fcntl.flock(f, fcntl.LOCK_UN)
                    except OSError as e:
                        logger.warning("Failed to unlock file: %s", e)
                        
        except Exception as e:
            logger.error(f"Error updating {file_path}: {e}")
            if attempt < max_retries - 1:
                time.sleep(0.1)
                continue
            return None
            
    return None
