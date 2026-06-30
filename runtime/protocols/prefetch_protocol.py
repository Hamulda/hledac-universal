"""
runtime/protocols/prefetch_protocol.py — F270: Prefetch Interface
=================================================================

Protocol for speculative prefetch and temporal prediction.
Extracted from SprintScheduler's PREFETCH group (~5 attributes).

GHOST_INVARIANTS:
- Fail-safe: prefetch returns [] on error
- Bounded: speculative queue size limited
"""


from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PrefetchProtocol(Protocol):
    """
    Prefetch and prediction protocol.

    Implementations:
        - PrefetchOracleAdapter: speculative prefetch
        - TemporalPredictorAdapter: time-series prediction

    Key methods:
        - get_prefetch_candidates: speculative URLs to prefetch
        - predict_temporal: predict query timing
    """

    def get_prefetch_candidates(
        self, query: str, count: int = 5
    ) -> list[str]:
        """Get speculative prefetch candidates for query."""
        ...

    async def predict_temporal(
        self, query: str
    ) -> dict[str, Any] | None:
        """Predict temporal distribution of query results."""
        ...
