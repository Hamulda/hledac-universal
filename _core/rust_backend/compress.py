# compress.py — Zstd compression domain for L2 cache hot paths
"""
E3: Rust zstd batch compression for L2 cache.

Zero-copy Rust zstd implementation with asyncio.to_thread bridge.
Replaces lz4.frame (GIL-bound) with Rust compress_zstd/decompress_zstd
(GIL-released, 3-5× faster on M1).

Wire format: pure zstd bytes (no marker header — caller manages framing).

Domain pattern: _RustCompressDomain / _PythonCompressDomain
"""

from __future__ import annotations

import asyncio
import logging
import struct
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

logger = logging.getLogger(__name__)

_RUST_ZSTD_AVAILABLE = False
_rust_compress_zstd: Any = None
_rust_compress_decompress: Any = None
_compress_probed = False


def _probe_rust_compress() -> None:
    """Probe for Rust compress_zstd/decompress_zstd functions."""
    global _RUST_ZSTD_AVAILABLE, _rust_compress_zstd, _rust_compress_decompress, _compress_probed
    if _compress_probed:
        return
    _compress_probed = True

    try:
        from hledac.universal._core.rust_backend import rust as _rust_backend

        if _rust_backend.is_available:
            ext = _rust_backend.raw
            _rust_compress_zstd = getattr(ext, "compress_zstd", None)
            _rust_compress_decompress = getattr(ext, "decompress_zstd", None)
            if _rust_compress_zstd is not None and _rust_compress_decompress is not None:
                _RUST_ZSTD_AVAILABLE = True
                logger.debug("[compress] Rust zstd backend available")
                return
    except Exception as e:
        logger.debug("[compress] Rust backend probe failed: %s", e)

    _RUST_ZSTD_AVAILABLE = False
    logger.debug("[compress] Using Python zstd fallback")


_python_zstd: Any = None
_python_zstd_available = False


def _ensure_python_zstd() -> Any:
    """Lazy-load Python zstd (compression.zstd stdlib or zstandard package)."""
    global _python_zstd, _python_zstd_available
    if _python_zstd is not None:
        return _python_zstd

    # Python 3.14+ stdlib
    try:
        import compression.zstd as _mod

        _python_zstd = _mod
        _python_zstd_available = True
        return _python_zstd
    except ImportError:
        pass

    # Legacy zstandard package
    try:
        import zstandard as _mod

        _python_zstd = _mod
        _python_zstd_available = True
        return _python_zstd
    except ImportError:
        _python_zstd = None
        _python_zstd_available = False
        return None


def zstd_compress_sync(data: bytes, level: int = 3) -> bytes:
    """
    Synchronous zstd compression (Rust or Python fallback).

    Args:
        data: bytes to compress
        level: compression level (1 fast — 22 max; default 3)

    Returns:
        compressed bytes
    """
    _probe_rust_compress()

    if _RUST_ZSTD_AVAILABLE and _rust_compress_zstd is not None:
        try:
            return _rust_compress_zstd(data, level)
        except Exception as e:
            logger.debug("[compress] Rust compress_zstd failed: %s, falling back to Python", e)

    # Python fallback
    zstd_mod = _ensure_python_zstd()
    if zstd_mod is None:
        raise RuntimeError(
            "zstd compression not available (compression.zstd from Python 3.14+ or zstandard package required)"
        )

    return zstd_mod.compress(data, level)


def zstd_decompress_sync(compressed: bytes) -> bytes:
    """
    Synchronous zstd decompression (Rust or Python fallback).

    Args:
        compressed: zstd compressed bytes

    Returns:
        decompressed bytes
    """
    _probe_rust_compress()

    if _RUST_ZSTD_AVAILABLE and _rust_compress_decompress is not None:
        try:
            return _rust_compress_decompress(compressed)
        except Exception as e:
            logger.debug("[compress] Rust decompress_zstd failed: %s, falling back to Python", e)

    # Python fallback
    zstd_mod = _ensure_python_zstd()
    if zstd_mod is None:
        raise RuntimeError(
            "zstd decompression not available (compression.zstd from Python 3.14+ or zstandard package required)"
        )

    return zstd_mod.decompress(compressed)


async def zstd_compress(data: bytes, level: int = 3) -> bytes:
    """
    Async zstd compression with asyncio.to_thread bridge.

    Releases GIL during compression for non-blocking hot path.
    For L2 cache: 3-5× faster than lz4.frame on M1.

    Args:
        data: bytes to compress
        level: compression level (1 fast — 22 max; default 3)

    Returns:
        compressed bytes
    """
    return await asyncio.to_thread(zstd_compress_sync, data, level)


async def zstd_decompress(compressed: bytes) -> bytes:
    """
    Async zstd decompression with asyncio.to_thread bridge.

    Releases GIL during decompression for non-blocking hot path.

    Args:
        compressed: zstd compressed bytes

    Returns:
        decompressed bytes
    """
    return await asyncio.to_thread(zstd_decompress_sync, compressed)


async def zstd_compress_framed(obj: Any, level: int = 3) -> bytes:
    """
    Encode object to JSON, then compress with 4-byte length prefix.

    Wire format: struct.pack('<I', raw_len) + zstd_compressed(raw)
    This lets decoder detect truncation before invoking zstd.

    Args:
        obj: JSON-serializable object
        level: compression level (default 3)

    Returns:
        length-prefixed zstd compressed bytes
    """
    from hledac.universal.utils.codec import encode

    raw = encode(obj)
    compressed = await zstd_compress(raw, level)
    return struct.pack("<I", len(raw)) + compressed


async def zstd_decompress_framed(data: bytes | memoryview | bytearray) -> Any:
    """
    Decode length-prefixed zstd compressed bytes to Python object.

    Args:
        data: length-prefixed payload from zstd_compress_framed

    Returns:
        decoded Python object

    Raises:
        ValueError: On length-prefix mismatch
    """
    from hledac.universal.utils.codec import decode

    if isinstance(data, (memoryview, bytearray)):
        data = bytes(data)
    if len(data) < 4:
        raise ValueError("zstd_decompress_framed: payload too short for length prefix")

    raw_len = struct.unpack("<I", data[:4])[0]
    raw = await zstd_decompress(data[4:])
    if len(raw) != raw_len:
        raise ValueError(f"zstd_decompress_framed: length mismatch (prefix={raw_len}, actual={len(raw)})")

    return decode(raw)


class _RustCompressDomain:
    """
    Rust-accelerated zstd compression domain.

    Wraps compress_zstd/decompress_zstd with asyncio.to_thread for
    non-blocking hot path on L2 cache.
    """

    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions | None) -> None:
        self._ext = ext

    def compress_zstd(self, data: bytes, level: int = 3) -> bytes:
        """Synchronous zstd compression (Rust)."""
        if self._ext is None:
            return _python_fallback_compress(data, level)
        return self._ext.compress_zstd(data, level)

    def decompress_zstd(self, compressed: bytes) -> bytes:
        """Synchronous zstd decompression (Rust)."""
        if self._ext is None:
            return _python_fallback_decompress(compressed)
        return self._ext.decompress_zstd(compressed)

    async def compress(self, data: bytes, level: int = 3) -> bytes:
        """Async zstd compression with GIL release."""
        return await zstd_compress(data, level)

    async def decompress(self, compressed: bytes) -> bytes:
        """Async zstd decompression with GIL release."""
        return await zstd_decompress(compressed)


class _PythonCompressDomain:
    """
    Python fallback zstd compression domain.

    Uses compression.zstd (Python 3.14+) or zstandard package.
    """

    __slots__ = ()

    def compress_zstd(self, data: bytes, level: int = 3) -> bytes:
        """Synchronous zstd compression (Python)."""
        return _python_fallback_compress(data, level)

    def decompress_zstd(self, compressed: bytes) -> bytes:
        """Synchronous zstd decompression (Python)."""
        return _python_fallback_decompress(compressed)

    async def compress(self, data: bytes, level: int = 3) -> bytes:
        """Async zstd compression (Python)."""
        return await asyncio.to_thread(_python_fallback_compress, data, level)

    async def decompress(self, compressed: bytes) -> bytes:
        """Async zstd decompression (Python)."""
        return await asyncio.to_thread(_python_fallback_decompress, compressed)


def _python_fallback_compress(data: bytes, level: int = 3) -> bytes:
    """Python fallback zstd compression."""
    zstd_mod = _ensure_python_zstd()
    if zstd_mod is None:
        raise RuntimeError("zstd not available")
    return zstd_mod.compress(data, level)


def _python_fallback_decompress(compressed: bytes) -> bytes:
    """Python fallback zstd decompression."""
    zstd_mod = _ensure_python_zstd()
    if zstd_mod is None:
        raise RuntimeError("zstd not available")
    return zstd_mod.decompress(compressed)


def get_domain(ext: hledac_rust_extensions | None) -> _RustCompressDomain | _PythonCompressDomain:
    """
    Domain factory for rust.compress accessor.

    Returns _RustCompressDomain if Rust backend available,
    otherwise _PythonCompressDomain with Python zstd fallback.
    """
    _probe_rust_compress()
    if _RUST_ZSTD_AVAILABLE:
        return _RustCompressDomain(ext)
    return _PythonCompressDomain()


__all__ = [
    "zstd_compress",
    "zstd_decompress",
    "zstd_compress_sync",
    "zstd_decompress_sync",
    "zstd_compress_framed",
    "zstd_decompress_framed",
    "get_domain",
    "_RustCompressDomain",
    "_PythonCompressDomain",
]
