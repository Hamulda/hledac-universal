"""
runtime/protocols/pivot_protocol.py — F270: Pivot Interface
==========================================================

Protocol for IOC pivot queue and hypothesis planning.
Extracted from SprintScheduler's PIVOT group (~7 attributes).

GHOST_INVARIANTS:
- Fail-safe: enqueue_pivot is no-op on error
- Bounded: max_queue_size enforced
"""



from typing import Protocol, runtime_checkable


@runtime_checkable
class PivotProtocol(Protocol):
    """
    Pivot planning and queue protocol.

    Implementations:
        - PivotQueueAdapter: wraps pivot queue logic

    Key methods:
        - enqueue_pivot: add IOC pivot to queue
        - drain_pivot_queue: process pending pivots
    """

    def enqueue_pivot(
        self,
        ioc_value: str,
        ioc_type: str,
        confidence: float,
        source: str | None = None,
    ) -> None:
        """Enqueue IOC pivot for processing."""
        ...

    async def drain_pivot_queue(self, max_tasks: int = 5) -> int:
        """Drain pivot queue, return number processed."""
        ...

    async def record_feedback(
        self,
        pivot_type: str,
        ioc_type: str,
        succeeded: bool,
    ) -> None:
        """Record pivot execution feedback for adaptation."""
        ...
