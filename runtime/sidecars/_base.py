"""
runtime/sidecars/_base.py — F-ISSUE-005: Shared Base for Scheduler-Backed Sidecars
=================================================================================


SchedulerBackedSidecarAdapter and bind_scheduler() are shared by all
discovery / enrichment / forensics adapter categories.

This module is imported at module load time by each category package.
It deliberately does NOT import SidecarRegistry — the decorator call
is in each adapter file so the import graph is explicit.

ISSUE-003 FIX: _scheduler_ref replaced with ContextVar.
- Each async context (sprint) gets its own scheduler reference.
- Enables parallel sprint execution in tests without monkey-patching.
- bind_scheduler() sets the ContextVar for the current async context.
"""

from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.runtime.sidecar_protocol import BaseSidecarAdapter, SidecarContext

if TYPE_CHECKING:
    from hledac.universal.runtime.sprint_scheduler import SprintScheduler

logger = logging.getLogger(__name__)

# ISSUE-003 fix: ContextVar for per-sprint scheduler isolation.
# Each sprint (async context) gets its own scheduler reference.
# This enables parallel sprint execution in tests and eliminates
# the module-level mutable _scheduler_ref global.
_scheduler_ref_var: contextvars.ContextVar[SprintScheduler | None] = contextvars.ContextVar("_scheduler_ref_var")


def bind_scheduler(scheduler: SprintScheduler | None) -> None:
    """Bind the live SprintScheduler instance for the current async context.

    Called by `SidecarOrchestrator.__init__`. Idempotent. Pass `None` to
    clear (used in tests + teardown).
    """
    _scheduler_ref_var.set(scheduler)


class SchedulerBackedSidecarAdapter(BaseSidecarAdapter):
    """
    Adapter that delegates to an existing `SprintScheduler` private method.

    Subclasses set `scheduler_method_name` to the method to invoke.
    `run_async` is a thin `getattr` + `await` — all error handling lives in
    `BaseSidecarAdapter.run` (fail-soft at registry level).

    Invariant: scheduler_method_name must be a coroutine function on
    SprintScheduler. If the method is missing (e.g. `commoncrawl` /
    `ti_feed` not yet implemented), the adapter logs once and returns
    an empty finding list. This makes the previous silent no-op behavior
    observable and documentable.
    """

    sidecar_id: str = "base"
    env_gate: str = ""
    ram_budget_mb: int = 100
    priority: int = 5
    scheduler_method_name: str = ""
    missing_method_expected: bool = False

    __slots__ = ("_missing_logged",)

    def __init__(self) -> None:
        super().__init__()
        self._missing_logged: bool = False

    async def run_async(self, ctx: SidecarContext) -> list[Any]:
        # ISSUE-003 fix: read from ContextVar instead of module-level global
        try:
            scheduler = _scheduler_ref_var.get()
        except LookupError:
            scheduler = None
        if scheduler is None:
            return []
        logger.debug(
            "%s: delegating to scheduler.%s (sprint=%s, mode=%s)",
            self.sidecar_id,
            self.scheduler_method_name,
            ctx.sprint_id,
            ctx.sprint_mode,
        )
        method = getattr(scheduler, self.scheduler_method_name, None)
        if method is None:
            if not self._missing_logged:
                level = logging.INFO if self.missing_method_expected else logging.WARNING
                logger.log(
                    level,
                    "%s: scheduler method %r not implemented (returning empty findings)",
                    self.sidecar_id,
                    self.scheduler_method_name,
                )
                self._missing_logged = True
            return []
        result = method()
        if hasattr(result, "__await__"):
            result = await result
        if result is None:
            return []
        if isinstance(result, list):
            return result
        return [result]
