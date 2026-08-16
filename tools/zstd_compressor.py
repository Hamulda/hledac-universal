"""
ZstdCompressor — content-aware Zstd compression with passive dictionary learning.

Extracted from coordinators/fetch_coordinator.py (Sprint 44 refactor).

Provides compression with content-aware levels and passive dictionary building.

HEIST-07: Dictionary-aware compression now backed by Rust `compress_page_dict()`
which uses the global DICT_REGISTRY in rust_extensions. Wire format:
  `[0x03][dict_id: 4 bytes LE][zstd_compressed_with_dict]`
Call `register_rust_dict(dict_id, dict_data)` at startup to populate the
registry from a pre-trained dictionary file (e.g., zstd_osint.dict).
"""
from collections import deque
from typing import Any
from _core import aclose
try:
    import zstandard as zstd
    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False
    zstd = None
if ZSTD_AVAILABLE:
    _DictT = zstd.ZstdCompressionDict
else:
    _DictT = Any

# HEIST-07: Lazy import of Rust dictionary functions.
_rust_compress_dict: Any = None
_rust_register_dict: Any = None
_rust_unregister_dict: Any = None

def _ensure_rust_dict_bindings() -> None:
    """Lazy-load Rust dictionary compression bindings (fail-soft)."""
    global _rust_compress_dict, _rust_register_dict, _rust_unregister_dict
    if _rust_compress_dict is not None:
        return
    try:
        from hledac.universal.rust_extensions import (
            compress_page_dict,
            register_zstd_dict,
            unregister_zstd_dict,
    )
        _rust_compress_dict = compress_page_dict
        _rust_register_dict = register_zstd_dict
        _rust_unregister_dict = unregister_zstd_dict
    except ImportError:  # noqa: BLE001
        pass


def register_rust_dict(dict_id: int, dict_data: bytes) -> bool:
    """
    Register a pre-trained zstd dictionary in the Rust DICT_REGISTRY.

    Call at startup after loading zstd_osint.dict. The dictionary is then
    available for all compress_page_dict() calls in the Rust extension.

    Args:
        dict_id: Unique u32 dictionary identifier
        dict_data: Raw zstd dictionary bytes (from zstd.train_dictionary)

    Returns:
        True if registered, False if ID already exists or Rust unavailable
    """
    _ensure_rust_dict_bindings()
    if _rust_register_dict is None:
        return False
    try:
        return _rust_register_dict(dict_id, dict_data)
    except Exception:
        return False


def unregister_rust_dict(dict_id: int) -> bool:
    """Remove a dictionary from the Rust registry, freeing memory."""
    _ensure_rust_dict_bindings()
    if _rust_unregister_dict is None:
        return False
    try:
        return _rust_unregister_dict(dict_id)
    except Exception:
        return False


def compress_with_rust_dict(data: bytes, dict_id: int) -> bytes | None:
    """
    Compress using a pre-registered Rust dictionary (HEIST-07 wire format).

    Returns None if Rust bindings unavailable — caller should fall back
    to plain zstd.

    Args:
        data: Raw bytes to compress (64 B ≤ len ≤ 1 MB)
        dict_id: Dictionary ID registered via register_rust_dict

    Returns:
        Wire-format bytes [0x03][dict_id LE][zstd_dict_compressed], or None
    """
    _ensure_rust_dict_bindings()
    if _rust_compress_dict is None:
        return None
    try:
        return _rust_compress_dict(data, dict_id)
    except Exception:
        return None

class ZstdCompressor:
    """Compressor with content-aware levels and passive dictionary."""
    __slots__ = tuple(('_dctx', '_dictionary_data', '_response_counter', '_response_samples'))

    def __init__(self):
        self._dctx = zstd.ZstdDecompressor() if ZSTD_AVAILABLE else None
        self._dictionary_data: _DictT | None = None
        self._response_counter = 0
        self._response_samples: deque[tuple[bytes, str]] = deque(maxlen=100)

    def compress(self, data: bytes, content_type: str='text') -> bytes:
        """Compress with optional dictionary and content-aware level."""
        if not ZSTD_AVAILABLE or data is None:
            return data
        level = 1 if content_type == 'json' else 3
        try:
            if self._dictionary_data and self._response_counter > 100:
                cctx = zstd.ZstdCompressor(level=level, dict_data=self._dictionary_data)
            else:
                cctx = zstd.ZstdCompressor(level=level)
            return cctx.compress(data)
        except Exception:
            return data

    def decompress(self, data: bytes) -> bytes:
        if not ZSTD_AVAILABLE or data is None:
            return data
        try:
            if self._dictionary_data:
                dctx = zstd.ZstdDecompressor(dict_data=self._dictionary_data)
                return dctx.decompress(data)
            if self._dctx is None:
                return data
            return self._dctx.decompress(data)
        except Exception:
            return data

    def add_sample(self, data: bytes, content_type: str) -> None:
        """Collect samples for dictionary building. Rebuilds dictionary every 100 samples."""
        if not ZSTD_AVAILABLE:
            return
        self._response_samples.append((data, content_type))
        self._response_counter += 1
        if self._response_counter >= 100 and self._response_counter % 100 == 0:
            self._build_dictionary()

    def _build_dictionary(self) -> None:
        """Build zstd dictionary from collected samples."""
        if not ZSTD_AVAILABLE:
            return
        try:
            samples = [s[0] for s in self._response_samples]
            if samples:
                self._dictionary_data = zstd.train_dictionary(1024 * 1024, samples)
        except Exception:  # noqa: BLE001
            pass