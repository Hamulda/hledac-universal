"""
runtime/adapters/fetch_adapter.py — F270: Fetch Coordinator Adapter
===================================================================


Adapter implementing FetchProtocol for FetchCoordinator.
Non-breaking: wraps existing FetchCoordinator without changes.

GHOST_INVARIANTS:
- Fail-safe: fetch returns None on error
- Bounded: semaphore limits concurrency
"""



import asyncio
from typing import Any

from hledac.universal.runtime.protocols.fetch_protocol import FetchProtocol
from _core import aclose


class FetchCoordinatorAdapter(FetchProtocol):
    """
    Adapter wrapping FetchCoordinator to implement FetchProtocol.

    Non-breaking: wraps existing FetchCoordinator and delegates
    to it without changing behavior.

    Usage:
        fc = FetchCoordinator(...)
        adapter = FetchCoordinatorAdapter(fc)
        # Use as FetchProtocol
        result = await adapter.fetch(work)
    """

    __slots__ = ('_coordinator',)

    def __init__(self, coordinator: Any) -> None:
        """
        Initialize adapter with existing FetchCoordinator.

        Args:
            coordinator: FetchCoordinator instance to wrap
        """
        self._coordinator = coordinator

    async def fetch(self, work: Any) -> tuple[str, Any] | None:
        """Delegate fetch to coordinator."""
        try:
            return await self._coordinator.fetch(work)
        except Exception:
            return None

    def get_semaphore(self) -> asyncio.Semaphore:
        """Return coordinator's semaphore."""
        try:
            return self._coordinator.get_semaphore()
        except Exception:
            from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore
            return get_semaphore(ConcurrencyCategory.HTTP_LANE)

    def get_backpressure(self) -> float | None:
        """Return coordinator's backpressure ratio."""
        try:
            return self._coordinator.get_backpressure()
        except Exception:
            return None
