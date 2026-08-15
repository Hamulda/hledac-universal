"""
runtime/protocols/score_protocol.py — F270: Score Interface
==========================================================

Protocol for IOC scoring and source weight management.
Extracted from SprintScheduler's SCORING group (~7 attributes).

GHOST_INVARIANTS:
- Fail-safe: score returns 0.0 on error
- Bounded: weight tables are immutable after init
"""



from typing import Any, Protocol, runtime_checkable
from core import aclose


@runtime_checkable
class ScoreProtocol(Protocol):
    """
    IOC scoring and source weighting protocol.

    Implementations:
        - SourceWeightAdapter: manages source weights

    Key methods:
        - compute_score: IOC novelty/quality score
        - get_source_weight: source reliability weight
    """

    def compute_score(
        self,
        ioc_value: str,
        ioc_type: str,
        source: str,
        context: dict[str, Any] | None = None,
    ) -> float:
        """Compute IOC score (0.0-1.0)."""
        ...

    def get_source_weight(self, source: str) -> float:
        """Get source reliability weight (0.0-1.0)."""
        ...

    def get_november_bonus(self, ioc_type: str) -> float:
        """Get novelty bonus for IOC type."""
        ...
