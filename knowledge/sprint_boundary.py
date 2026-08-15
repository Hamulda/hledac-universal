"""
SprintBoundaryCoordinator — coordinates sprint-boundary state transitions.

Handles the two concerns that must happen together when a sprint ends:

  1. Cache invalidation  (_DuckDBQueryCache.invalidate())
  2. Dedup store advance (DedupManager.advance_ioc_sprint → Rust MmapIocDedupStore.advance_sprint())

Separation of Concerns:
  - _DuckDBQueryCache is a pure cache — it knows nothing about DedupManager
  - DedupManager is pure dedup — it knows nothing about the query cache
  - SprintBoundaryCoordinator orchestrates the two at sprint boundaries

Always-on, fail-safe invariants:
  - Any error on either operation is caught and swallowed (no exception propagation)
  - Both operations are best-effort; sprint continues regardless
  - M1 8GB: +0 MB resident, +<1 µs overhead (two method calls only)
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from core import aclose

if TYPE_CHECKING:
    from ._query_cache import _DuckDBQueryCache


class SprintBoundaryCoordinator:
    """
    Coordinates cache invalidation and dedup state advance at sprint boundaries.

    Args:
        query_cache: The _DuckDBQueryCache instance to invalidate.
        dedup_manager: The DedupManager instance to advance (may be None).
    """

    __slots__ = ("_cache", "_dedup", "__weakref__")

    def __init__(
        self,
        query_cache: _DuckDBQueryCache,
        dedup_manager: object | None,
    ) -> None:
        self._cache = query_cache
        self._dedup = dedup_manager

    def advance(self, sprint_id: int) -> None:
        """
        Advance to new sprint boundary.

        Performs two operations in sequence:
          1. Invalidate the query cache (L1 + L2 cleared)
          2. Advance the IOC dedup store metadata (Rust MmapIocDedupStore.advance_sprint)

        Fail-safe: either operation may fail silently; the sprint continues.
        """
        # 1. Invalidate query cache
        try:
            self._cache.invalidate()
        except Exception:  # noqa: BLE001
            pass

        # 2. Advance dedup store (getattr for type-safe call on protocol object)
        try:
            advance_fn = getattr(self._dedup, "advance_ioc_sprint", None)
            if advance_fn is not None:
                advance_fn(sprint_id)
        except Exception:  # noqa: BLE001
            pass
