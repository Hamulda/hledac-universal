"""
Rolling Hash Engine for URL deduplication.

Rabin-Karp rolling hash for fast sliding-window computation on URL strings.
Provides O(1) hash roll when sliding window advances — critical for URL dedup.

Sprint F214Q: Rust extension candidate — Python fallback for M1 environments
without Rust toolchain.
"""

from __future__ import annotations

# -----------------------------------------------------------------------------
# Rust extension import guard
# -----------------------------------------------------------------------------
_RUST_RH_AVAILABLE = False
try:
    import hledac_rust_extensions
    # Expose Rust RollingHashEngine for API compatibility
    _RustRhEngine = hledac_rust_extensions.RollingHashEngine
    _RUST_RH_AVAILABLE = True
except ImportError:
    _RustRhEngine = None

# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------
# Default: 64-bit polynomial rolling hash with large prime modulus
DEFAULT_BASE = 256
DEFAULT_MODULUS = 2**61 - 1  # Mersenne prime — fast modular arithmetic

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

    __slots__ = ("_impl", "_is_rust")

    def __init__(
        self,
        base: int = DEFAULT_BASE,
        modulus: int = DEFAULT_MODULUS,
    ) -> None:
        if _RUST_RH_AVAILABLE and _RustRhEngine is not None:
            self._impl = _RustRhEngine(base=base, modulus=modulus)
            self._is_rust = True
        else:
            self._impl = RollingHashPython(base=base, modulus=modulus)
            self._is_rust = False

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
        """Compute hashes for all windows."""
        return self._impl.hashes(data, window_size)


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