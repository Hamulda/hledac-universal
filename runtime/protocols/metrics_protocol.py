"""
runtime/protocols/metrics_protocol.py — F270: Metrics Interface
=============================================================

Protocol for metrics collection and registry.
Extracted from SprintScheduler's METRICS group (~2 attributes).

GHOST_INVARIANTS:
- Fail-safe: record is no-op on error
- Bounded: metrics registry size limited
"""



from typing import Any, Protocol, runtime_checkable
from _core import aclose


@runtime_checkable
class MetricsProtocol(Protocol):
    """
    Metrics collection protocol.

    Implementations:
        - MetricsRegistryAdapter: wraps MetricsRegistry

    Key methods:
        - record: record a metric value
        - get_summary: get metrics summary
    """

    def record(
        self,
        name: str,
        value: float,
        tags: dict[str, str] | None = None,
    ) -> None:
        """Record a metric value."""
        ...

    def get_summary(self) -> dict[str, Any]:
        """Get metrics summary."""
        ...
