"""
runtime/protocols/lane_protocol.py — F270: Lane Interface
======================================================

Protocol for feed/pipeline lane management.
Extracted from SprintScheduler's LANE group (~8 attributes).

GHOST_INVARIANTS:
- Fail-safe: run_lane returns empty result on error
- Bounded: lane budgets enforced per sprint
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LaneProtocol(Protocol):
    """
    Feed/pipeline lane execution protocol.

    Implementations:
        - LaneExecutorAdapter: runs acquisition lanes

    Key methods:
        - run_lane: execute single acquisition lane
        - get_lane_budget: remaining budget for lane
    """

    async def run_lane(
        self,
        lane_name: str,
        query: str,
        budget_seconds: float,
    ) -> dict[str, Any]:
        """Execute named acquisition lane."""
        ...

    def get_lane_budget(self, lane_name: str) -> float:
        """Get remaining time budget for lane."""
        ...

    def record_lane_outcome(self, lane_name: str, outcome: dict[str, Any]) -> None:
        """Record lane execution outcome."""
        ...
