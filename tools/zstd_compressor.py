"""
ZstdCompressor — content-aware Zstd compression with passive dictionary learning.

Extracted from coordinators/fetch_coordinator.py (Sprint 44 refactor).
Provides compression with content-aware levels and passive dictionary building.
"""

from __future__ import annotations

from collections import deque
from typing import Optional

try:
    import zstd

    ZSTD_AVAILABLE = True
except ImportError:
    ZSTD_AVAILABLE = False


class ZstdCompressor:
    """Compressor with content-aware levels and passive dictionary."""

    def __init__(self):
        self._dctx = zstd.ZstdDecompressor() if ZSTD_AVAILABLE else None
        self._dictionary_data: Optional[bytes] = None
        self._response_counter = 0
        self._response_samples: deque[tuple[bytes, str]] = deque(maxlen=100)

    def compress(self, data: bytes, content_type: str = 'text') -> bytes:
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
            return self._dctx.decompress(data)
        except Exception:
            return data

    def add_sample(self, data: bytes, content_type: str) -> None:
        """Collect samples for dictionary building. Rebuilds dictionary every 100 samples."""
        if not ZSTD_AVAILABLE:
            return
        # P2-2 fix: always collect samples (deque maxlen=100 auto-evicts oldest)
        self._response_samples.append((data, content_type))
        self._response_counter += 1
        # Rebuild dictionary every 100 samples (not just once at counter==100)
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
        except Exception:
            pass