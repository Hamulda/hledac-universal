"""
Rolling Hash Engine for URL deduplication.

Rabin-Karp rolling hash for fast sliding-window computation on URL strings.


Provides O(1) hash roll when sliding window advances — critical for URL dedup.

Sprint F214Q: Rust extension candidate — Python fallback for M1 environments
without Rust toolchain.
"""


from typing import Any

# -----------------------------------------------------------------------------
# Rust extension import guard
# -----------------------------------------------------------------------------
_RUST_RH_AVAILABLE = False
# R6: Centralized Rust access via core.rust_backend
from hledac.universal._core.rust_backend import rust
from _core import aclose
if rust.is_available:
    _RustRhEngine = rust.raw.RollingHashEngine
    _RUST_RH_AVAILABLE = _RustRhEngine is not None
else:
    _RustRhEngine = None

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# Default: 64-bit polynomial rolling hash with large prime modulus
DEFAULT_BASE = 256
DEFAULT_MODULUS = 2**61 - 1  # Mersenne prime — fast modular arithmetic

# Bounded cache of per-window Rust engine instances (see hashes() override).
MAX_RH_ENGINES = 16

# -----------------------------------------------------------------------------
# Python fallback implementation
# -----------------------------------------------------------------------------

class RollingHashPython:
    """
    Rabin-Karp rolling hash — Python fallback.

    Uses polynomial rolling hash with Mersenne prime modulus.
    O(1) hash roll when sliding window advances by one byte.
    """

    __slots__ = ("_base", "_modulus", "_base_pow")

    def __init__(self, base: int = DEFAULT_BASE, modulus: int = DEFAULT_MODULUS) -> None:
        self._base = base
        self._modulus = modulus
        # Precompute base^window_size mod modulus for window_size up to 2048
        self._base_pow: dict[int, int] = {}

    def _compute_power(self, window_size: int) -> int:
        """Compute base^window_size mod modulus, cached."""
        if window_size not in self._base_pow:
            result = 1
            for _ in range(window_size):
                result = (result * self._base) % self._modulus
            self._base_pow[window_size] = result
        return self._base_pow[window_size]

    def hash(self, data: bytes) -> int:
        """Compute hash of initial window (all bytes)."""
        result = 0
        for byte in data:
            result = (result * self._base + byte) % self._modulus
        return result

    def roll(self, old_hash: int, old_char: int, new_char: int, window_size: int) -> int:
        """
        Roll hash forward by one byte.

        Args:
            old_hash: Hash of previous window
            old_char: Byte being removed (0-255)
            new_char: Byte being added (0-255)
            window_size: Size of sliding window

        Returns:
            New hash value
        """
        power = self._compute_power(window_size)
        # Remove contribution of old_char (shifted to position window_size)
        new_hash = (old_hash - (old_char * power) % self._modulus) % self._modulus
        if new_hash < 0:
            new_hash += self._modulus
        # Add new character at least significant position
        new_hash = (new_hash * self._base + new_char) % self._modulus
        return new_hash

    def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
        """
        Compute hashes for all windows in data.

        Args:
            data: Input bytes
            window_size: Sliding window size (default 8 bytes)

        Returns:
            List of hash values, one per window position
        """
        if len(data) < window_size:
            return []
        results = []
        current = self.hash(data[:window_size])
        results.append(current)
        for i in range(window_size, len(data)):
            current = self.roll(current, data[i - window_size], data[i], window_size)
            results.append(current)
        return results


# -----------------------------------------------------------------------------
# Public API — uses Rust if available, Python fallback otherwise
# -----------------------------------------------------------------------------

class RollingHashEngine:
    """
    Unified rolling hash engine.

    Uses Rust implementation if available (10x faster on M1),
    falls back to pure Python.
    """

    __slots__ = ("_impl", "_is_rust", "_window_size", "_rust_by_window")

    def __init__(
        self,
        base: int = DEFAULT_BASE,
        modulus: int = DEFAULT_MODULUS,
        window_size: int = 8,
    ) -> None:
        if _RUST_RH_AVAILABLE and _RustRhEngine is not None:
            self._impl = _RustRhEngine(base=base, modulus=modulus, window_size=window_size)
            self._is_rust = True
        else:
            self._impl = RollingHashPython(base=base, modulus=modulus)
            self._is_rust = False
        self._window_size = window_size

    @property
    def is_rust(self) -> bool:
        """True if Rust backend is active."""
        return self._is_rust

    def hash(self, data: bytes) -> int:
        """Compute hash of initial window."""
        return self._impl.hash(data)

    def roll(self, old_hash: int, old_char: int, new_char: int, window_size: int) -> int:
        """Roll hash forward by one byte."""
        return self._impl.roll(old_hash, old_char, new_char, window_size)

    def hashes(self, data: bytes, window_size: int = 8) -> list[int]:
        """
        Compute hashes for all windows.

        The Rust backend bakes `window_size` at construction time, so when a
        caller requests a different window we transparently reuse a cached
        engine instance for that size. The Python backend honours the
        `window_size` argument directly on every call.
        """
        if self._is_rust:
            if window_size == self._window_size:
                return self._impl.hashes(data)
            # Per-window Rust engine cache — bounded (MAX_RH_ENGINES entries)
            # to keep the hot path allocation-free for the common case.
            return self._get_rust_for_window(window_size).hashes(data)
        return self._impl.hashes(data, window_size)

    def _get_rust_for_window(self, window_size: int) -> Any:
        """Return (and cache) a Rust engine configured for `window_size`."""
        cache_attr = getattr(self, "_rust_by_window", None)
        if cache_attr is None:
            cache = dict[int, Any]()
            self._rust_by_window = cache
        else:
            cache = cache_attr
        engine = cache.get(window_size)
        if engine is None:
            engine = _RustRhEngine(  # type: ignore[misc]
                base=DEFAULT_BASE,
                modulus=DEFAULT_MODULUS,
                window_size=window_size,
    )
            cache[window_size] = engine
            if len(cache) > MAX_RH_ENGINES:
                # FIFO eviction to keep cache bounded.
                oldest = next(iter(cache))
                cache.pop(oldest, None)
        return engine

    def chunk_bytes(self, data: bytes, chunk_size: int = 64) -> list[bytes]:
        """
        Split data into fixed-size chunks.

        For repetitive text, content-defined chunking produces identical
        boundaries (same hash = 2^65), defeating MinHash. Fixed-size
        chunking ensures meaningful variation in chunk content.

        Args:
            data: Input bytes (e.g. UTF-8 text)
            chunk_size: Fixed chunk size in bytes (default 64)

        Returns:
            List of byte chunks, non-empty
        """
        if len(data) <= chunk_size:
            return [data]

        chunks = []
        for i in range(0, len(data), chunk_size):
            chunks.append(data[i:i + chunk_size])

        return chunks

    def chunk_signatures(self, chunks: list[bytes]) -> list[int]:
        """
        Hash signature for each chunk.

        Args:
            chunks: List of byte chunks from chunk_bytes()

        Returns:
            List of int hash signatures (one per chunk)
        """
        return [self.hash(chunk) for chunk in chunks]

    def superfeatures(
        self,
        signatures: list[int],
        num_features: int = 6,
    ) -> frozenset[int]:
        """
        MinHash bottom-k sketch — picks num_features smallest hash values.

        Jaccard similarity on superfeatures approximates full document similarity.

        Args:
            signatures: List of chunk signatures from chunk_signatures()
            num_features: Number of superfeatures to select (default 6)

        Returns:
            frozenset of selected hash values (MinHash bottom-k sketch)
        """
        if not signatures:
            return frozenset()
        k = min(num_features, len(signatures))
        return frozenset(sorted(signatures)[:k])


def rolling_hash_bytes(data: bytes, base: int = DEFAULT_BASE, modulus: int = DEFAULT_MODULUS) -> int:
    """
    Compute rolling hash of bytes data.

    Convenience function for single-shot hashing.
    """
    engine = RollingHashEngine(base=base, modulus=modulus)
    return engine.hash(data)


# -----------------------------------------------------------------------------
# Exported symbols
# -----------------------------------------------------------------------------
__all__ = [
    "RollingHashEngine",
    "RollingHashPython",
    "rolling_hash_bytes",
    "DEFAULT_BASE",
    "DEFAULT_MODULUS",
    "_RUST_RH_AVAILABLE",
]
