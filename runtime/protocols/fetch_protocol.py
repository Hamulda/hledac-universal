"""
runtime/protocols/fetch_protocol.py — F270: Fetch Interface
=========================================================

Protocol for HTTP fetching via FetchCoordinator.
Extracted from SprintScheduler's FETCH group (~5 attributes).

GHOST_INVARIANTS:
- Fail-safe: all methods return None on error
- Bounded: semaphore limits concurrency
"""


import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class FetchProtocol(Protocol):
    """
    HTTP fetch coordination protocol.

    Implementations:
        - FetchCoordinatorAdapter: wraps FetchCoordinator

    Key methods:
        - fetch: execute HTTP fetch with backpressure
        - get_semaphore: concurrency control
    """

    async def fetch(self, work: Any) -> tuple[str, Any] | None:
        """Execute fetch work item, return (url, result) or None."""
        ...

    def get_semaphore(self) -> asyncio.Semaphore:
        """Return concurrency semaphore."""
        ...

    def get_backpressure(self) -> float | None:
        """Return current backpressure ratio (0.0-1.0)."""
        ...
