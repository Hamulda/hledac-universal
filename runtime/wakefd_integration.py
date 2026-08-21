"""
WakeFd asyncio integration — ISSUE-010
=====================================


Propojuje Rust WakeFd (pipe-based cross-thread notification)
s Python asyncio event loop pres loop.add_reader().

Architecture:
    Rust: WakeFd.wake_fd() → read_fd (int)
    Python: loop.add_reader(read_fd, callback)
    Rust: recv_batch() → Vec<Vec<u8>> items

Usage:
    pool = MPSCPool(capacity=2048)
    handle = pool.add_sender()
    notifier = WakeFdNotifier(pool, loop)
    await notifier.start()
    # Now Rust can wake Python via pipe write
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class WakeFdNotifier:
    """
    Propojuje Rust WakeFd pipe s asyncio event loop.

    Rust side: MPSCPool.wake_fd() vrací read_fd
    Python side: loop.add_reader(read_fd, self._on_wake)

    Když Rust zapíše do pipe (wake), Python callback
    zavolá recv_batch() a notifikuje waitery.
    """

    __slots__ = ("_pool", "_loop", "_reader_handle", "_callbacks", "_running")

    def __init__(self, pool: Any, loop: asyncio.AbstractEventLoop) -> None:
        self._pool = pool
        self._loop = loop
        self._reader_handle: int | None = None  # wake_fd value for remove_reader
        self._callbacks: list[Callable[[list[bytes]], None]] = []
        self._running = False

    def add_callback(self, cb: Callable[[list[bytes]], None]) -> None:
        """Registruje callback pro wake události."""
        self._callbacks.append(cb)

    def _on_wake(self) -> None:
        """
        Voláno když wake_fd.fire() zapíše do pipe.

        Drainuje batch z Rust recv_batch() a distribuuje
        všem registrovaným callbackům.
        """
        try:
            # Drain the batch from Rust
            batch = self._pool.recv_batch(max_items=None)
            if batch:
                items = [bytes(item) for item in batch]
                for cb in self._callbacks:
                    try:
                        cb(items)
                    except Exception as e:
                        logger.error(f"[WakeFdNotifier] callback error: {e}")
        except Exception as e:
            logger.error(f"[WakeFdNotifier] wake handler error: {e}")

    async def start(self) -> None:
        """Registruje wake_fd s event loop."""
        if self._running:
            return

        try:
            wake_fd = self._pool.wake_fd()
            if wake_fd < 0:
                logger.warning("[WakeFdNotifier] Invalid wake_fd")
                return

            # Register read_fd with event loop
            self._reader_handle = self._loop.add_reader(
                wake_fd,
                self._on_wake,
            )
            self._running = True
            logger.info(f"[WakeFdNotifier] Registered wake_fd={wake_fd}")

        except Exception as e:
            logger.error(f"[WakeFdNotifier] Failed to start: {e}")

    async def stop(self) -> None:
        """Odebere wake_fd z event loop."""
        if not self._running:
            return

        try:
            if self._reader_handle is not None:
                self._loop.remove_reader(self._reader_handle)
                self._reader_handle = None
            self._running = False
            logger.info("[WakeFdNotifier] Stopped")
        except Exception as e:
            logger.error(f"[WakeFdNotifier] stop error: {e}")


async def create_mpsc_notifier(
    capacity: int = 2048,
    loop: asyncio.AbstractEventLoop | None = None,
) -> tuple[Any, WakeFdNotifier]:
    """
    Factory: vytvoří MPSCPool + WakeFdNotifier.

    Returns:
        (pool, notifier) tuple

    Usage:
        pool, notifier = await create_mpsc_notifier()
        await notifier.start()
        # Send from Rust threads:
        pool.send(handle, payload_bytes)
        # Python receives via callbacks
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    # Lazy import - MPSCPool je v rust_extensions
    try:
        # R6: Centralized Rust access via core.rust_backend
        from hledac.universal._core.rust_backend import rust

        MPSCPool = rust.raw.MPSCPool
    except ImportError:
        logger.error("[create_mpsc_notifier] hledac_rust_extensions not available")
        raise RuntimeError("MPSCPool requires hledac_rust_extensions")

    pool = MPSCPool(capacity=capacity)
    notifier = WakeFdNotifier(pool, loop)
    return pool, notifier
