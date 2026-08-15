# bloom.py — BloomFilter domain (thin delegate to utils/bloom_filter.py)
"""
Thin domain wrapper for BloomFilter functionality.

This module provides _RustBloomDomain and _PythonBloomDomain classes
that wrap the canonical implementations from utils.bloom_filter.

For MultiTierRotatingBloomFilter (per-host tiers + global bloom),
use utils.bloom_filter.MultiTierRotatingBloomFilter directly.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions

from hledac.universal.utils.bloom_filter import (
    BloomFilter as _CanonicalBloomFilter,
    RotatingBloomFilter as _CanonicalRotatingBloomFilter,
    MultiTierRotatingBloomFilter,
    MmapBloomFilter as _CanonicalMmapBloomFilter,
)

# Import the Rust extension accessor
from ._prober import probe
from core._util import aclose


class _RustBloomDomain:
    """
    Rust-accelerated BloomFilter domain.
    
    Delegates to hledac_rust_extensions for:
    - BloomFilter (FNV-1a hashing, 10x faster on M1)
    - MmapBloomFilter (memory-mapped persistence)
    - RotatingMmapBloomFilter (dual-filter rotation)
    - UrlSet (FNV-1a URL deduplication)
    - bloom_check_batch (rayon parallel batch checking)
    """
    
    __slots__ = ("_ext",)
    
    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext
    
    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> Any:
        """Create a Rust BloomFilter with FNV-1a hashing."""
        return self._ext.BloomFilter(capacity, fpr)
    
    def MmapBloomFilter(
        self,
        path: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
        force_new: bool = False,
    ) -> Any:
        """Create a memory-mapped BloomFilter (Rust)."""
        return self._ext.MmapBloomFilter(path, capacity, fp_rate, force_new)
    
    def RotatingMmapBloomFilter(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> Any:
        """Create a rotating dual-mmap BloomFilter (Rust)."""
        return self._ext.RotatingMmapBloomFilter(path_a, path_b, capacity, fp_rate)
    
    def UrlSet(self) -> Any:
        """Create a Rust UrlSet for URL deduplication."""
        return self._ext.UrlSet()
    
    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        """Batch check items against a BloomFilter (rayon parallel)."""
        return self._ext.bloom_check_batch(items, bloom_filter)
    
    def MultiTierRotatingBloomFilter(
        self,
        per_host_capacity: int = 10000,
        global_capacity: int = 100000,
        max_tiers: int = 100,
        max_fill_ratio: float = 0.7,
        mmap_path: str | None = None,
    ) -> MultiTierRotatingBloomFilter:
        """
        Create a multi-tier rotating BloomFilter (per-host tiers + global bloom).
        
        This uses the canonical Python implementation from utils.bloom_filter
        since the Rust extension doesn't yet support multi-tier.
        """
        return MultiTierRotatingBloomFilter(
            per_host_capacity=per_host_capacity,
            global_capacity=global_capacity,
            max_tiers=max_tiers,
            max_fill_ratio=max_fill_ratio,
            mmap_path=mmap_path,
        )


class _PythonBloomDomain:
    """
    Pure-Python BloomFilter domain fallback.
    
    Delegates to utils.bloom_filter for canonical implementations:
    - BloomFilter: byte array with xxHash/MD5 hashing
    - RotatingBloomFilter: single-tier rotating filter
    - MultiTierRotatingBloomFilter: per-host tiers + global bloom
    """
    
    __slots__ = ()
    
    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> _CanonicalBloomFilter:
        """Create a pure-Python BloomFilter."""
        return _CanonicalBloomFilter(max_elements=capacity, error_rate=fpr)
    
    def MmapBloomFilter(
        self,
        path: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
        force_new: bool = False,
    ) -> _CanonicalMmapBloomFilter:
        """Create a memory-mapped BloomFilter (with mmap fallback)."""
        return _CanonicalMmapBloomFilter(path=path, capacity=capacity, fp_rate=fp_rate)
    
    def RotatingMmapBloomFilter(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> Any:
        """Create a rotating dual-mmap BloomFilter (Python fallback)."""
        # Simple rotating implementation
        filter_a = _CanonicalMmapBloomFilter(path=path_a, capacity=capacity, fp_rate=fp_rate)
        filter_b = _CanonicalMmapBloomFilter(path=path_b, capacity=capacity, fp_rate=fp_rate)
        return _RotatingMmapFilterWrapper(filter_a, filter_b)
    
    def UrlSet(self) -> "_PythonUrlSet":
        """Create a pure-Python URL set."""
        return _PythonUrlSet()
    
    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        """Batch check items (pure Python)."""
        return [item in bloom_filter for item in items]
    
    def MultiTierRotatingBloomFilter(
        self,
        per_host_capacity: int = 10000,
        global_capacity: int = 100000,
        max_tiers: int = 100,
        max_fill_ratio: float = 0.7,
        mmap_path: str | None = None,
    ) -> MultiTierRotatingBloomFilter:
        """Create a multi-tier rotating BloomFilter."""
        return MultiTierRotatingBloomFilter(
            per_host_capacity=per_host_capacity,
            global_capacity=global_capacity,
            max_tiers=max_tiers,
            max_fill_ratio=max_fill_ratio,
            mmap_path=mmap_path,
        )


class _RotatingMmapFilterWrapper:
    """Simple rotating wrapper for two mmap filters."""
    
    __slots__ = ("_a", "_b", "_current")
    
    def __init__(self, filter_a: Any, filter_b: Any) -> None:
        self._a = filter_a
        self._b = filter_b
        self._current = 0
    
    def add(self, item: str) -> bool:
        if self._current == 0:
            return self._a.add(item)
        return self._b.add(item)
    
    def add_batch(self, items: list[str]) -> list[bool]:
        return [self.add(item) for item in items]
    
    def contains(self, item: str) -> bool:
        return item in self._a or item in self._b
    
    def __contains__(self, item: str) -> bool:
        return self.contains(item)
    
    def __len__(self) -> int:
        return len(self._a) + len(self._b)
    
    def msync(self, flags: int = 0) -> None:
        self._a.msync(flags)
        self._b.msync(flags)


class _PythonUrlSet:
    """Pure-Python URL set fallback."""
    
    __slots__ = ("_items",)
    
    def __init__(self) -> None:
        self._items: list[str] = []
    
    def add(self, item: str) -> None:
        if item not in self._items:
            self._items.append(item)
    
    def add_batch(self, items: list[str]) -> list[bool]:
        """Bulk add — returns True per new item, False per duplicate."""
        if not items:
            return []
        results = []
        for item in items:
            if item in self._items:
                results.append(False)
            else:
                self._items.append(item)
                results.append(True)
        return results
    
    def contains(self, item: str) -> bool:
        return item in self._items
    
    def __contains__(self, item: str) -> bool:
        return self.contains(item)
    
    def __len__(self) -> int:
        return len(self._items)
    
    def len(self) -> int:
        """Return the number of items in the set."""
        return len(self._items)
    
    def clear(self) -> None:
        self._items.clear()


def get_domain(ext: object | None) -> _RustBloomDomain | _PythonBloomDomain:
    """Return Rust or Python domain based on extension availability."""
    if ext is not None:
        return _RustBloomDomain(ext)
    return _PythonBloomDomain()


# Re-export MultiTierRotatingBloomFilter for convenience
__all__ = [
    '_RustBloomDomain',
    '_PythonBloomDomain',
    '_RotatingMmapFilterWrapper',
    '_PythonUrlSet',
    'get_domain',
    'MultiTierRotatingBloomFilter',
]
