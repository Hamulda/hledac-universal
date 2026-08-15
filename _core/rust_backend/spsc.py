# spsc.py — SPSC (Single Producer Single Consumer) Queue domain
"""
Lock-free SPSC queue for high-performance inter-thread communication.
Used for evidence log mpsc channel implementation.


"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from _core._util import aclose

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


# =============================================================================
# SPSC Queue Domain
# =============================================================================


class _RustSPSCDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    def SPSCQueuePair(self) -> tuple[Any, Any]:
        """Create SPSC queue pair (sender, receiver)."""
        return self._ext.spsc_queue_pair()

    def recv_blocking(self, receiver_ptr: int) -> int:
        """Receive item from queue (blocking)."""
        return self._ext.spsc_recv_blocking(receiver_ptr)

    def try_recv(self, receiver_ptr: int) -> int:
        """Try to receive item from queue (non-blocking)."""
        return self._ext.spsc_try_recv(receiver_ptr)

    def item_data(self, item_ptr: int) -> bytes:
        """Get item data as bytes."""
        return self._ext.spsc_item_data(item_ptr)

    def item_free(self, item_ptr: int) -> None:
        """Free item back to pool."""
        self._ext.spsc_item_free(item_ptr)


class _PythonSPSCDomain:
    __slots__ = ()

    def SPSCQueuePair(self) -> tuple[_PythonSPSCSender, Any]:
        """Python fallback: return dummy sender and receiver."""
        return (_PythonSPSCSender(None), None)

    def recv_blocking(self, receiver_ptr: int) -> int:
        """Python fallback: not supported."""
        return 0

    def try_recv(self, receiver_ptr: int) -> int:
        """Python fallback: not supported."""
        return 0

    def item_data(self, item_ptr: int) -> bytes:
        """Python fallback: return empty bytes."""
        return b""

    def item_free(self, item_ptr: int) -> None:
        """Python fallback: no-op."""
        pass


class _PythonSPSCSender:
    """Python fallback SPSC sender."""

    __slots__ = ("_queue",)

    def __init__(self, queue: Any) -> None:
        self._queue = queue

    def send(self, payload: bytes) -> bool:
        """Python fallback: always returns False (not supported)."""
        return False


def get_spsc_domain(ext: object | None) -> _RustSPSCDomain | _PythonSPSCDomain:
    """Factory: return Rust or Python SPSCDomain based on ext availability."""
    if ext is not None:
        try:
            return _RustSPSCDomain(ext)
        except Exception:  # noqa: BLE001
            pass
    return _PythonSPSCDomain()
