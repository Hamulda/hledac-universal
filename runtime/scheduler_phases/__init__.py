"""STEP 3 — Phase protocol and helpers."""
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from runtime.sprint_scheduler import SprintScheduler


class PhaseRunner(Protocol):
    """Protocol for SprintScheduler phase methods.

    Allows SprintScheduler to delegate to extracted phase modules
    while maintaining the existing self.* attribute access pattern.
    """
    async def run(self, sched: SprintScheduler, **kwargs: Any) -> Any: ...
