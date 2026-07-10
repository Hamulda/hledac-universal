"""
runtime/protocols/lifecycle_protocol.py — F270: Lifecycle Interface
===================================================================

Protocol for sprint lifecycle management.
Extracted from SprintScheduler's LIFECYCLE group (~2 attributes).

GHOST_INVARIANTS:
- Fail-safe: lifecycle calls are no-op on error
- Bounded: lifecycle state machine is strict
"""



from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LifecycleProtocol(Protocol):
    """
    Sprint lifecycle management protocol.

    Implementations:
        - LifecycleAdapter: wraps LifecycleManager

    Key methods:
        - transition: advance lifecycle state
        - get_state: current lifecycle state
    """

    def transition(self, event: str, data: dict[str, Any] | None = None) -> None:
        """Transition lifecycle to next state."""
        ...

    def get_state(self) -> str:
        """Get current lifecycle state."""
        ...

    async def winddown(self) -> None:
        """Execute winddown procedures."""
        ...
