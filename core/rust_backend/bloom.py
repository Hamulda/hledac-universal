# bloom.py — BloomFilter domain

from typing import TYPE_CHECKING, Any






from ._prober import probe

if TYPE_CHECKING:
    from hledac_rust_extensions import hledac_rust_extensions


class _RustBloomDomain:
    __slots__ = ("_ext",)

    def __init__(self, ext: hledac_rust_extensions) -> None:
        self._ext = ext

    # ------------------------------------------------------------------
    # BloomFilter
    # ------------------------------------------------------------------
    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> Any:
        return self._ext.BloomFilter(capacity, fpr)

    def MmapBloomFilter(
        self,
        path: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
        force_new: bool = False,
    ) -> Any:
        return self._ext.MmapBloomFilter(path, capacity, fp_rate, force_new)

    def RotatingMmapBloomFilter(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> Any:
        return self._ext.RotatingMmapBloomFilter(path_a, path_b, capacity, fp_rate)

    def UrlSet(self) -> Any:
        return self._ext.UrlSet()

    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        return self._ext.bloom_check_batch(items, bloom_filter)


class _PythonBloomDomain:
    """Pure-Python BloomFilter fallback — delegates to _PythonBloomFilter."""

    __slots__ = ()

    def BloomFilter(self, capacity: int = 100_000, fpr: float = 0.01) -> Any:
        return _PythonBloomFilter(capacity, fpr)

    def MmapBloomFilter(
        self,
        path: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
        force_new: bool = False,
    ) -> Any:
        return _PythonMmapBloomFilter(path, capacity, fp_rate, force_new)

    def RotatingMmapBloomFilter(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> Any:
        return _RotatingMmapBloomFilter(path_a, path_b, capacity, fp_rate)

    def UrlSet(self) -> Any:
        return _PythonUrlSet()

    def bloom_check_batch(self, items: list[str], bloom_filter: Any) -> list[bool]:
        return [item in bloom_filter for item in items]


# ------------------------------------------------------------------
# Pure-Python fallbacks (moved from top of rust_backend.py)
# ------------------------------------------------------------------


class _PythonBloomFilter:
    """Pure-Python BloomFilter fallback using a simple list."""

    __slots__ = ("_size", "_filter")

    def __init__(self, capacity: int = 100_000, fpr: float = 0.01) -> None:
        import math

        self._size = int(-capacity * math.log(fpr) / 0.4804530139182014)
        self._filter = [False] * self._size

    def add(self, item: str) -> bool:
        import hashlib

        h = int(hashlib.sha256(item.encode()).hexdigest(), 16)
        idx = h % self._size
        was_new = not self._filter[idx]
        self._filter[idx] = True
        return was_new

    def add_batch(self, items: list[str]) -> list[bool]:
        return [self.add(item) for item in items]

    def contains(self, item: str) -> bool:
        import hashlib

        h = int(hashlib.sha256(item.encode()).hexdigest(), 16)
        return self._filter[h % self._size]

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __len__(self) -> int:
        return sum(self._filter)

    def clear(self) -> None:
        self._filter = [False] * self._size

    def estimated_fill_ratio(self) -> float:
        return sum(self._filter) / len(self._filter) if self._filter else 0.0


class _PythonMmapBloomFilter:
    """Pure-Python mmap-backed BloomFilter fallback (no-op, no actual mmap)."""

    __slots__ = ("_path", "_capacity", "_fpr", "_inner")

    def __init__(
        self,
        path: str,
        capacity: int = 100_000,
        fpr: float = 0.01,
        force_new: bool = False,
    ) -> None:
        self._path = path
        self._capacity = capacity
        self._fpr = fpr
        self._inner = _PythonBloomFilter(capacity, fpr)

    def __getattr__(self, name: str) -> Any:
        # Delegate all other methods/attributes to the inner BloomFilter
        return getattr(self._inner, name)

    def __contains__(self, item: str) -> bool:
        return item in self._inner

    def __len__(self) -> int:
        return len(self._inner)

    def msync(self, _flags: int = 0) -> None:
        pass  # no-op for Python fallback


class _RotatingMmapBloomFilter:
    """Pure-Python rotating BloomFilter fallback (two filters, round-robin)."""

    __slots__ = ("_a", "_b", "_current", "_path_a", "_path_b", "_capacity", "_fpr")

    def __init__(
        self,
        path_a: str,
        path_b: str,
        capacity: int = 100_000,
        fp_rate: float = 0.01,
    ) -> None:
        self._path_a = path_a
        self._path_b = path_b
        self._capacity = capacity
        self._fpr = fp_rate
        self._a = _PythonMmapBloomFilter(path_a, capacity, fp_rate)
        self._b = _PythonMmapBloomFilter(path_b, capacity, fp_rate)
        self._current = 0

    def add(self, item: str) -> bool:
        if self._current == 0:
            return self._a.add(item)
        return self._b.add(item)

    def add_batch(self, items: list[str]) -> list[bool]:
        return [self.add(item) for item in items]

    def contains(self, item: str) -> bool:
        return self._a.contains(item) or self._b.contains(item)

    def __contains__(self, item: str) -> bool:
        return self.contains(item)

    def __len__(self) -> int:
        return len(self._a) + len(self._b)

    def msync(self, _flags: int = 0) -> None:
        self._a.msync(_flags)
        self._b.msync(_flags)


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
    """Return Rust or Python domain based on probe result."""
    if ext is not None:
        return _RustBloomDomain(ext)
    return _PythonBloomDomain()
