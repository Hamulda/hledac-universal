"""STEP 3 — Sprint phase helpers extracted from runtime/sprint_scheduler.py.

F350M-R / Issue #P2.

COMPLETED EXTRACTS:
    runtime/scheduler_phases.prelude  — run_public_prelude_lane (standalone async fn)

NOT EXTRACTED (architectural coupling — PMB lesson [941]):
    The SprintScheduler class (L5042-32832) is a 27k-line monolith where run()
    depends on all 93 __slots__ attributes and methods. Phase 2-4 (method
    extraction) is NOT feasible without a greenfield rewrite.

    Specifically blocked:
    - _run_mandatory_acquisition_prelude: 1380 lines, touches ~60 self.* attrs
    - _run_ct_prelude_lane: self._result.* telemetry updates, _get_ct_adapter()
    - _run_wayback_prelude_lane: self._result.*, self._enqueue_duckdb_write(),
      self._gate_then_ingest_and_accumulate(), self.sprint_id
    - _run_pdns_prelude_lane: same _result/_enqueue/_gate coupling
    - _run_doh_prelude_lane: self._doh_adapter, self._result.* mutations,
      self._gate_then_ingest_and_accumulate(), self._enqueue_duckdb_write()
    - _run_one_cycle / _run_feed_branch / _run_public_branch: 800+ lines,
      deep _result accumulation, _accumulate_findings_to_graph()

ARCHITECTURAL PATH FORWARD (documented in PMB lesson [941]):
    Option A: Greenfield rewrite — SprintScheduler v2 with Protocol-based
      composition, each phase as a first-class async module with explicit
      dependency injection.
    Option B: Keep as-is — the monolith is testable (106/106 pass) and
      stable; focus on adding new sidecars via SidecarRegistry instead.

DELIVERED IN STEP 1-3:
    runtime/scheduler_config.py    (~250 LOC) — SprintSchedulerConfig, SourceTier,
                              FeedDominanceGuard, LaneBudgetPool
    runtime/scheduler_result.py   (~540 LOC) — SprintSchedulerResult (~395 fields),
                              SprintResultBuilder
    runtime/lifecycle_registry.py (~120 LOC) — ResourceLifecycleRegistry + OwnedResource
    runtime/scheduler_phases/
        __init__.py              — PhaseRunner Protocol
        prelude.py               (~100 LOC) — run_public_prelude_lane helper
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from runtime.sprint_scheduler import SprintScheduler


class PhaseRunner(Protocol):
    """Protocol for SprintScheduler phase methods.

    Allows future greenfield SprintScheduler v2 to delegate to extracted
    phase modules while maintaining explicit self.* attribute access.
    """
    async def run(self, sched: "SprintScheduler", **kwargs: Any) -> Any: ...


# Re-export for convenience
from runtime.scheduler_phases.prelude import run_public_prelude_lane

__all__ = ["PhaseRunner", "run_public_prelude_lane"]
