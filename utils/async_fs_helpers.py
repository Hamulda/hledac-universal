"""
Async FS Helpers — Non-blocking file I/O for async contexts.
===========================================================

Provides async file operations that never block the event loop:
- aiofiles as primary (non-blocking async I/O)
- asyncio.to_thread fallback (thread pool, never blocks event loop)
- Fail-safe: any error returns None/False, never raises

M1 8GB: All operations are thread-pool offloaded, zero Metal/Ridge/MLX
interaction, minimal RAM footprint (~1KB per operation).

Usage:
    from hledac.universal.utils.async_fs_helpers import async_write_file, async_read_file_text

Invariants enforced:
- Always-on: no feature flags
- Bounded: single file operation at a time (caller controls batching)
- Fail-safe: errors logged at DEBUG, returns safe zero values
"""

import asyncio
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

__all__ = [
    "async_write_file",
    "async_read_file_text",
]


# ─── aiofiles lazy import ────────────────────────────────────────────────────

def _get_aiofiles():
    """Lazy aiofiles import — fails gracefully if not installed."""
    try:
        import aiofiles
        return aiofiles
    except ImportError:
        return None


# ─── Core async file operations ─────────────────────────────────────────────


async def async_write_file(
    path: str,
    data: bytes,
    *,
    append: bool = False,
    encoding: Literal["utf-8", None] = "utf-8",
    fsync: bool = False,
) -> bool:
    """Async file write — never blocks the event loop.

    Strategy:
        1. Try aiofiles (non-blocking async I/O)
        2. Fall back to asyncio.to_thread (thread pool, still non-blocking)

    Args:
        path:      File path to write.
        data:      Bytes to write (always bytes — caller encodes).
        append:    If True, appends; otherwise truncates (default False).
        encoding:  For text operations hint (ignored since data is bytes).
        fsync:     If True, fsync after write for durability (default False).

    Returns:
        True if write succeeded, False on any error.
        Errors are logged at DEBUG level, never raised.

    Invariants:
        - [AFS-1] Never raises — all errors are logged and return False
        - [AFS-2] Non-blocking: aiofiles or to_thread, never sync open() in async ctx
        - [AFS-3] Zero-copy data path (data is passed directly, not copied)
    """
    aiofiles = _get_aiofiles()
    mode = "ab" if append else "wb"

    # Try aiofiles first (true async I/O)
    if aiofiles is not None:
        try:
            async with aiofiles.open(path, mode, encoding=encoding) as f:
                await f.write(data)
                if fsync:
                    await f.flush()
                    # Note: aiofiles doesn't expose fileno(), fsync via to_thread below
            if fsync:
                # fsync must be done in thread (requires fileno)
                def _fsync() -> None:
                    with open(path, "r+b") as sf:
                        os.fsync(sf.fileno())
                await asyncio.to_thread(_fsync)
            return True
        except Exception as _e:
            logger.debug(f"[AFS] aiofiles write failed for {path}: {_e}")
            # Fall through to thread fallback

    # Fallback: asyncio.to_thread (still non-blocking for event loop)
    try:
        def _write_sync() -> None:
            with open(path, mode) as f:
                f.write(data)
                f.flush()
                if fsync:
                    os.fsync(f.fileno())

        await asyncio.to_thread(_write_sync)
        return True
    except Exception as _e:
        logger.debug(f"[AFS] to_thread write failed for {path}: {_e}")
        return False


async def async_read_file_text(
    path: str,
    *,
    encoding: Literal["utf-8", "latin-1"] = "utf-8",
) -> str | None:
    """Async file text read — never blocks the event loop.

    Strategy:
        1. Try aiofiles (non-blocking async I/O)
        2. Fall back to asyncio.to_thread (thread pool, still non-blocking)

    Args:
        path:     File path to read.
        encoding: Text encoding (default utf-8).

    Returns:
        File contents as string, or None on any error.
        Errors are logged at DEBUG level.

    Invariants:
        - [AFS-1] Never raises — errors logged and return None
        - [AFS-2] Non-blocking: aiofiles or to_thread
    """
    aiofiles = _get_aiofiles()

    # Try aiofiles first
    if aiofiles is not None:
        try:
            async with aiofiles.open(path, "r", encoding=encoding) as f:
                return await f.read()
        except Exception as _e:
            logger.debug(f"[AFS] aiofiles read failed for {path}: {_e}")
            # Fall through to thread fallback

    # Fallback: asyncio.to_thread
    try:
        def _read_sync() -> str:
            with open(path, "r", encoding=encoding) as f:
                return f.read()

        return await asyncio.to_thread(_read_sync)
    except Exception as _e:
        logger.debug(f"[AFS] to_thread read failed for {path}: {_e}")
        return None
