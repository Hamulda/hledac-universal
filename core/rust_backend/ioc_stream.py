"""
HEIST-01: Python seam for StreamingIocScanner — zero-copy mmap/bytes IOC sweep.

Provides a convenience wrapper around the Rust `StreamingIocScanner` PyClass

with Python 3.14+ best practices: type hints, async support via
`asyncio.to_thread()`, and graceful fallback when the Rust extension
is not available.

Usage:
    from hledac.universal.core.rust_backend.ioc_stream import IocStreamScanner

    scanner = IocStreamScanner(patterns=["malware", "CVE-\\d{4}-\\d+"])
    hits = scanner.scan_file("/data/dump.bin")  # mmap zero-copy, 3-4 GB/s on M1
    hits = scanner.scan_bytes(b"raw data with malware signatures")
    
    # Async (non-blocking for large files):
    hits = await scanner.scan_file_async("/data/5gb.dump")

M1 8GB safety:
    - Mmap: kernel page cache, ~0 bytes resident
    - Automaton: ~2-5 MB for 10k patterns
    - Async: uses asyncio.to_thread(), no event loop blocking
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Rust import guard
# ---------------------------------------------------------------------------

_RUST_SCANNER_AVAILABLE = False
_RUST_IMPORT_ERROR: str | None = None

try:
    from hledac_rust_extensions import StreamingIocScanner as _RustStreamingIocScanner
    from hledac_rust_extensions import StreamPatternHit as _RustStreamPatternHit
    _RUST_SCANNER_AVAILABLE = True
except ImportError as _exc:
    _RUST_IMPORT_ERROR = str(_exc)
    _RustStreamingIocScanner = None  # type: ignore[assignment]
    _RustStreamPatternHit = None  # type: ignore[assignment]

if not _RUST_SCANNER_AVAILABLE:
    logger.warning(
        "[HEIST-01] Rust StreamingIocScanner not available. "
        "mmap/bytes streaming scan disabled. "
        "Install: rebuild Rust extensions with `uv run maturin develop --release`. "
        f"Import error: {_RUST_IMPORT_ERROR}"
    )


# ---------------------------------------------------------------------------
# IocStreamScanner — Python convenience wrapper
# ---------------------------------------------------------------------------

class IocStreamScanner:
    """Streaming IOC scanner for mmap'd files and raw byte buffers.

    Wraps the Rust `StreamingIocScanner` PyClass with Pythonic ergonomics:
    - Accepts `Path` objects for file paths
    - Provides async variants via `asyncio.to_thread()`
    - Graceful fallback when Rust extension is not available (returns empty)

    Performance (M1, NEON Teddy):
        - 3-4 GB/s for mmap'd files
        - Zero-copy: no String allocation for haystack
        - Bounded memory: automaton ~2-5 MB, mmap ~0 bytes resident
    """

    def __init__(
        self,
        patterns: Sequence[str],
        labels: Sequence[str] | None = None,
    ) -> None:
        """Create a streaming IOC scanner.

        Args:
            patterns: List of literal patterns to match.
            labels: Optional parallel list of labels (same length as patterns).
        """
        if not _RUST_SCANNER_AVAILABLE:
            self._rust_scanner = None
            logger.debug("IocStreamScanner: Rust unavailable, all scans return empty")
            return

        self._rust_scanner = _RustStreamingIocScanner(
            list(patterns),
            list(labels) if labels is not None else [],
        )

    # -- Sync API -----------------------------------------------------------

    def scan_bytes(self, buffer: bytes | bytearray | memoryview) -> list[dict]:
        """Scan raw bytes buffer — zero-copy.

        Args:
            buffer: Raw bytes, bytearray, or memoryview to scan.

        Returns:
            List of dicts with keys: start, end, pattern, label, value.
            Returns empty list if Rust is unavailable.
        """
        if self._rust_scanner is None:
            return []
        if isinstance(buffer, bytearray):
            hits = self._rust_scanner.scan_bytearray(buffer)
        elif isinstance(buffer, memoryview):
            hits = self._rust_scanner.scan_memoryview(buffer)
        else:
            hits = self._rust_scanner.scan_bytes(buffer)
        return [_hit_to_dict(h) for h in hits]

    def scan_file(self, path: str | Path) -> list[dict]:
        """Scan a file via mmap — zero-copy, 3-4 GB/s on M1.

        The file is memory-mapped read-only. The kernel manages the page
        cache; resident memory cost is near-zero.

        Args:
            path: Filesystem path to the file.

        Returns:
            List of dicts with keys: start, end, pattern, label, value.
            Returns empty list if Rust is unavailable or file cannot be opened.

        Raises:
            FileNotFoundError: If the file does not exist.
            OSError: If the file cannot be mmap'd.
        """
        if self._rust_scanner is None:
            return []
        path_str = str(path)
        hits = self._rust_scanner.scan_mmap(path_str)
        return [_hit_to_dict(h) for h in hits]

    def scan_file_range(
        self,
        path: str | Path,
        offset: int,
        length: int,
    ) -> list[dict]:
        """Scan a byte range of an mmap'd file.

        Useful for incremental processing from Python.

        Args:
            path: Filesystem path to the file.
            offset: Start byte offset (0-based).
            length: Number of bytes to scan from offset.

        Returns:
            List of dicts with absolute byte offsets.
        """
        if self._rust_scanner is None:
            return []
        hits = self._rust_scanner.scan_mmap_range(str(path), offset, length)
        return [_hit_to_dict(h) for h in hits]

    def contains_any(self, buffer: bytes) -> bool:
        """Fast check: does ANY pattern match in the buffer?

        Short-circuits on first match. Much faster than collecting all hits.
        """
        if self._rust_scanner is None:
            return False
        return self._rust_scanner.contains_any(buffer)

    def count_matches(self, buffer: bytes) -> int:
        """Count total matches in a buffer (no value extraction)."""
        if self._rust_scanner is None:
            return 0
        return self._rust_scanner.count_matches(buffer)

    def __len__(self) -> int:
        """Number of patterns in the scanner."""
        if self._rust_scanner is None:
            return 0
        return len(self._rust_scanner)

    @property
    def is_available(self) -> bool:
        """Whether the Rust backend is available."""
        return self._rust_scanner is not None

    # -- Async API (Python 3.14+ asyncio.to_thread) -------------------------

    async def scan_bytes_async(
        self,
        buffer: bytes | bytearray | memoryview,
    ) -> list[dict]:
        """Async variant of scan_bytes — non-blocking for large buffers."""
        return await asyncio.to_thread(self.scan_bytes, buffer)

    async def scan_file_async(self, path: str | Path) -> list[dict]:
        """Async variant of scan_file — non-blocking for large files."""
        return await asyncio.to_thread(self.scan_file, path)

    async def scan_file_range_async(
        self,
        path: str | Path,
        offset: int,
        length: int,
    ) -> list[dict]:
        """Async variant of scan_file_range."""
        return await asyncio.to_thread(self.scan_file_range, path, offset, length)

    async def contains_any_async(self, buffer: bytes) -> bool:
        """Async variant of contains_any."""
        return await asyncio.to_thread(self.contains_any, buffer)

    async def count_matches_async(self, buffer: bytes) -> int:
        """Async variant of count_matches."""
        return await asyncio.to_thread(self.count_matches, buffer)

    def close(self) -> None:
        """Release the automaton and free memory."""
        if self._rust_scanner is not None:
            self._rust_scanner.close()
            self._rust_scanner = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hit_to_dict(hit) -> dict:
    """Convert a Rust StreamPatternHit to a plain dict.

    Dict keys: start, end, pattern, label, value.
    Compatible with PatternHit NamedTuple consumers.
    """
    return {
        "start": hit.start,
        "end": hit.end,
        "pattern": hit.pattern,
        "label": hit.label,
        "value": hit.value,
    }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Check if the Rust streaming scanner is importable."""
    return _RUST_SCANNER_AVAILABLE


def create_scanner(
    patterns: Sequence[str],
    labels: Sequence[str] | None = None,
) -> IocStreamScanner:
    """Create a new IocStreamScanner.

    Convenience function that always succeeds — returns a scanner that
    produces empty results when Rust is unavailable.
    """
    return IocStreamScanner(patterns, labels)
