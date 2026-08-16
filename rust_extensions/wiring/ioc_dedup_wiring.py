"""
IOC Deduplication Wiring - ISSUE-007
====================================

Wires the zombie ioc_dedup.rs Rust module to its proper Python integration points.

Rust Module: rust_extensions/src/ioc_dedup.rs
Feature: advanced
Purpose: mmap-backed persistent IOC deduplication store

Integration Points:
--------------------
1. knowledge/ioc_graph.py - IOC deduplication
2. brain/jtms.py - JTMS fact deduplication
3. core/ - Global IOC dedup store

API (from Rust):
-----------------
- IocDedupStore: mmap-backed persistent store
  - add(ioc_type: str, value: str, timestamp: i64) -> bool (returns true if new)
  - contains(ioc_type: str, value: str) -> bool
  - get_count(ioc_type: str) -> usize
  - total_count() -> usize
  - clear() -> ()
  - sync() -> bool

M1 8GB Safety:
---------------
- Demand-paged via mmap(2)
- Entries rebuilt into HashMap on load
- madvise(MADV_WILLNEED) on hot pages

Usage:
-------
from rust_extensions.wiring import IocDedupStore

store = IocDedupStore("/path/to/store.bin")
store.add("ip", "1.2.3.4", timestamp=1234567890)
if store.contains("ip", "1.2.3.4"):
    print("Already seen this IP")
count = store.get_count("ip")
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

from hledac.universal._core.rust_backend import rust as _rust_backend

# Check availability
_ioc_dedup_available = (
    _rust_backend.is_available
    and hasattr(_rust_backend, "ioc_dedup")
    and getattr(_rust_backend, "ioc_dedup", None) is not None
)

# Get module reference
_ioc_dedup = getattr(_rust_backend, "ioc_dedup", None) if _ioc_dedup_available else None


# =============================================================================
# IOC Deduplication Store
# =============================================================================


class IocDedupStore:
    """
    IOC deduplication store with mmap-backed persistence.

    Thread-safe via parking_lot::RwLock.
    Falls back to Python dict if Rust module unavailable.

    Args:
        mmap_path: Path to mmap file for persistence
        max_entries: Maximum entries (default 1,000,000)

    Example:
        >>> store = IocDedupStore("/tmp/ioc_dedup.bin")
        >>> store.add("domain", "evil.com", 1234567890)
        True
        >>> store.contains("domain", "evil.com")
        True
        >>> store.add("domain", "evil.com", 1234567890)
        False
    """

    def __init__(
        self,
        mmap_path: str | None = None,
        max_entries: int = 1_000_000,
    ) -> None:
        self._store = None
        self._python_store: dict[tuple[str, str], int] = {}

        if _ioc_dedup is not None:
            try:
                self._store = _ioc_dedup.IocDedupStore(mmap_path, max_entries)
                logger.debug("Using Rust IOC dedup store (mmap-backed)")
                return
            except Exception as e:
                logger.warning(f"Failed to create Rust IOC dedup store: {e}")

        # Python fallback
        logger.debug("Using Python IOC dedup store")
        self._store = None

    def add(self, ioc_type: str, value: str, timestamp: int) -> bool:
        """
        Add IOC to deduplication store.

        Args:
            ioc_type: Type of IOC (ip, domain, url, md5, sha256, etc.)
            value: IOC value
            timestamp: Unix timestamp

        Returns:
            True if IOC is new (not seen before), False if duplicate
        """
        if self._store is not None:
            try:
                return self._store.add(ioc_type, value, timestamp)
            except Exception:
                pass

        # Python fallback
        key = (ioc_type.lower(), value.lower())
        if key in self._python_store:
            return False
        self._python_store[key] = timestamp
        return True

    def contains(self, ioc_type: str, value: str) -> bool:
        """
        Check if IOC is in deduplication store.

        Args:
            ioc_type: Type of IOC
            value: IOC value

        Returns:
            True if IOC exists in store
        """
        if self._store is not None:
            try:
                return self._store.contains(ioc_type, value)
            except Exception:
                pass

        # Python fallback
        key = (ioc_type.lower(), value.lower())
        return key in self._python_store

    def get_count(self, ioc_type: str) -> int:
        """
        Get count of IOCs of a specific type.

        Args:
            ioc_type: Type of IOC

        Returns:
            Count of IOCs of this type
        """
        if self._store is not None:
            try:
                return self._store.get_count(ioc_type)
            except Exception:
                pass

        # Python fallback
        return sum(1 for k in self._python_store if k[0] == ioc_type.lower())

    def total_count(self) -> int:
        """
        Get total count of all IOCs.

        Returns:
            Total IOC count
        """
        if self._store is not None:
            try:
                return self._store.total_count()
            except Exception:
                pass

        # Python fallback
        return len(self._python_store)

    def clear(self) -> None:
        """Clear all IOCs from store."""
        if self._store is not None:
            try:
                self._store.clear()
                return
            except Exception:
                pass

        # Python fallback
        self._python_store.clear()

    def sync(self) -> bool:
        """
        Sync store to disk.

        Returns:
            True if sync successful
        """
        if self._store is not None:
            try:
                return self._store.sync()
            except Exception:
                pass

        # Python fallback - no sync needed for dict
        return True

    def stats(self) -> dict:
        """
        Get store statistics.

        Returns:
            Dict with store stats
        """
        if self._store is not None:
            try:
                return self._store.stats()
            except Exception:
                pass

        # Python fallback
        counts: dict[str, int] = {}
        for ioc_type, _ in self._python_store.keys():
            counts[ioc_type] = counts.get(ioc_type, 0) + 1
        return {
            "total": len(self._python_store),
            "by_type": counts,
            "backend": "python",
        }


def ioc_dedup_available() -> bool:
    """Check if Rust IOC dedup store is available."""
    return _ioc_dedup_available


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "IocDedupStore",
    "ioc_dedup_available",
]
