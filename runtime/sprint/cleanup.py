"""
runtime/sprint/cleanup.py — Sprint cleanup utilities and context manager

F350M-R: Standardized exception handling and cleanup utilities.

Exports:
- _fail_safe(): Sync contextmanager for exception handling
- _fail_safe_async(): Async decorator for exception handling
- _cleanup_stale_locks(): Remove dead lock files

Usage:
    with _fail_safe("warning", "DuckDB init"):
        await store.async_initialize()
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar('T')


@contextlib.contextmanager
def _fail_safe(level: str = 'debug', label: str = ''):
    """
    PHASE REFACTORING F350M-R: Standardized exception handling decorator.
    
    Replaces 39+ identical try/except/pass patterns with a consistent handler.
    - CancelledError is always re-raised (I6 invariant)
    - All other exceptions are logged at configured level and swallowed
    - Optional label for debugging which operation failed
    
    Usage:
        with _fail_safe("warning", "DuckDB init"):
            await store.async_initialize()
    
    Args:
        level: Log level - "debug", "info", "warning", "error"
        label: Descriptive label for the operation (for log messages)
    """
    try:
        yield
    except asyncio.CancelledError:
        raise
    except Exception as e:
        _log_level = {
            'debug': logger.debug,
            'info': logger.info,
            'warning': logger.warning,
            'error': logger.error,
        }.get(level, logger.debug)
        if label:
            _log_level(f'[fail_safe:{label}] {type(e).__name__}: {e}')
        else:
            _log_level(f'[fail_safe] {type(e).__name__}: {e}')


def _fail_safe_async(level: str = 'debug', label: str = ''):
    """
    Async-compatible fail_safe wrapper for coroutines.

    Args:
        level: Log level - "debug", "info", "warning", "error"
        label: Descriptive label for the operation (for log messages)
    """
    def decorator(coro: Callable[..., T]) -> Callable[..., T | None]:
        async def wrapper(*args, **kwargs):
            try:
                return await coro(*args, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                _log_level = {
                    'debug': logger.debug,
                    'info': logger.info,
                    'warning': logger.warning,
                    'error': logger.error,
                }.get(level, logger.debug)
                prefix = f'[fail_safe:{label}] ' if label else '[fail_safe] '
                _log_level(f'{prefix}{type(e).__name__}: {e}')
                return None
        return wrapper
    return decorator


def _cleanup_stale_locks(lock_dir: Path, logger: logging.Logger) -> int:
    """
    Sprint F320: Stale-lock janitor.

    Scans lock_dir for *.lock files whose owning PID is dead.
    Removes stale locks and returns count of removed entries.

    Args:
        lock_dir: Directory containing lock files
        logger: Logger for debug messages
        
    Returns:
        Number of stale locks removed
    """
    removed_count = 0
    try:
        from hledac.universal._core.psutil_shim import psutil_module
        _ps = psutil_module()
        if _ps is None:
            return 0
        if not lock_dir.exists():
            return 0
        for lock_file in lock_dir.iterdir():
            if not lock_file.name.endswith('.lock'):
                continue
            try:
                pid_bytes = lock_file.read_bytes()
                if len(pid_bytes) >= 4:
                    lock_pid = int.from_bytes(pid_bytes[:4], byteorder='little')
                    if not _ps.pid_exists(lock_pid):
                        lock_file.unlink()
                        removed_count += 1
                        logger.info(f'[F320-JANITOR] Removed stale lock: {lock_file.name} (PID={lock_pid} dead)')
            except Exception:
                pass
    except Exception:
        pass
    return removed_count
