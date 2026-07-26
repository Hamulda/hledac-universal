"""STEP 4 Phase 5.3 — SprintInjector: legacy compatibility shims for SprintSchedulerV2.

F350M-R / Issue SC-06.

Extracts all 26 inject_* methods from SprintSchedulerV2.
These are v1 API compatibility stubs that allow tests and external callers to
inject pre-initialized services into the scheduler without going through the
normal boot sequence.

Two inject methods are kept in SprintSchedulerV2 (needed for aclose / critical paths):
- inject_evidence_log()
- inject_cancel_event()

All other inject_* methods live here and are applied via Injector.apply().

Usage:
    from runtime.scheduler_v2.injector import Injector
    Injector.apply(scheduler)  # attaches all inject_* methods
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


class Injector:
    """Legacy injection shims for SprintSchedulerV2.

    Provides all v1-style inject_* methods as a stateless utility class.
    Each method mutates the scheduler in-place (msgspec.Struct with frozen=False).
    """

    # ── Core service injectors ──────────────────────────────────────────────

    @staticmethod
    def inject_duckdb_store(scheduler: Any, store: Any) -> None:
        """Inject a pre-initialized DuckDBShadowStore.

        SC-05 FIX: Single source of truth is ctx.duckdb_store (SprintContext field).
        Writes to SprintContext via with_services().

        Also sets scheduler._duckdb_store for backward-compat with legacy tests
        and external code that accesses it directly.
        """
        from runtime.scheduler_v2.protocol import InitResult, SprintContext

        # Primary: store in SprintContext (canonical path)
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_services(duckdb_store=InitResult.success(store, 0.0))
        else:
            # Fallback for tests that construct scheduler without run():
            # Build a minimal SprintContext so duckdb_store is accessible
            from runtime.scheduler_config import SprintSchedulerConfig
            from runtime.scheduler_result import SprintSchedulerResult

            minimal_ctx = SprintContext(
                config=SprintSchedulerConfig(sprint_duration_s=60.0, cycle_sleep_s=10.0),
                query="test",
                result=SprintSchedulerResult(),
                duckdb_store=InitResult.success(store, 0.0),
            )
            object.__setattr__(scheduler, "_ctx", minimal_ctx)

        # Backward-compat: also set _duckdb_store directly on scheduler for
        # legacy code and tests that access it without going through ctx
        object.__setattr__(scheduler, "_duckdb_store", store)

    @staticmethod
    def inject_prefetch_oracle(scheduler: Any, oracle: Any) -> None:
        scheduler._prefetch_oracle = oracle
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(enrichment_services=oracle)

    @staticmethod
    def inject_prefetch_pipeline(scheduler: Any, pipeline: Any) -> None:
        scheduler._prefetch_pipeline = pipeline
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(enrichment_services=pipeline)

    @staticmethod
    def inject_temporal_predictor(scheduler: Any, predictor: Any) -> None:
        scheduler._temporal_predictor = predictor
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(temporal_predictor=predictor)

    @staticmethod
    def inject_pivot_planner(scheduler: Any, planner: Any) -> None:
        scheduler._pivot_planner = planner
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(pivot_planner=planner)

    @staticmethod
    def inject_analyst_workbench(scheduler: Any, workbench: Any) -> None:
        scheduler._analyst_workbench = workbench
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(analyst_workbench=workbench)

    @staticmethod
    def inject_forensics_enricher(scheduler: Any, enricher: Any) -> None:
        scheduler._forensics_enricher = enricher
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(forensics_enricher=enricher)

    @staticmethod
    def inject_multimodal_enricher(scheduler: Any, enricher: Any) -> None:
        scheduler._multimodal_enricher = enricher

    @staticmethod
    def inject_enrichment_services(scheduler: Any, services: Any) -> None:
        scheduler._enrichment_services = services
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(enrichment_services=services)

    @staticmethod
    def inject_source_economics(scheduler: Any, economics: Any) -> None:
        scheduler._source_economics = economics

    @staticmethod
    def inject_privacy_layer(scheduler: Any, layer: Any) -> None:
        scheduler._privacy_layer = layer
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_cycle(privacy_layer=layer)

    @staticmethod
    def inject_ioc_graph(scheduler: Any, ioc_graph: Any) -> None:
        scheduler._ioc_graph = ioc_graph
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_services(graph_service=ioc_graph)

    # ── Policy / layer injectors (no ctx update) ────────────────────────────

    @staticmethod
    def inject_policy_manager(scheduler: Any, policy_manager: Any) -> None:
        """PolicyManager is not a SprintContext service; no ctx update needed."""
        scheduler._policy_manager = policy_manager

    @staticmethod
    def inject_communication_layer(scheduler: Any, layer: Any) -> None:
        """v2: layer is a private scheduler attribute only; no SprintContext update."""
        scheduler._communication_layer = layer

    @staticmethod
    def inject_stealth_layer(scheduler: Any, layer: Any) -> None:
        """v2: layer is a private scheduler attribute only; no SprintContext update."""
        scheduler._stealth_layer = layer

    @staticmethod
    def inject_ghost_layer(scheduler: Any, layer: Any) -> None:
        """v2: layer is a private scheduler attribute only; no SprintContext update."""
        scheduler._ghost_layer = layer

    @staticmethod
    def inject_security_coordinator(scheduler: Any, coordinator: Any) -> None:
        """SecurityCoordinator is not a governor; no ctx update needed."""
        scheduler._security_coordinator = coordinator

    # ── Bulk apply ──────────────────────────────────────────────────────────

    @classmethod
    def apply(cls, scheduler: Any) -> None:
        """Attach all inject_* methods to a scheduler instance.

        After calling this, the scheduler has all inject_* methods available
        as bound methods for v1 API compatibility.
        """
        _methods = {
            name: getattr(cls, name)
            for name in dir(cls)
            if name.startswith("inject_") and callable(getattr(cls, name))
        }
        for name, method in _methods.items():
            if name in ("inject_evidence_log", "inject_cancel_event"):
                continue  # keep these in the scheduler
            setattr(scheduler, name, lambda m=method: lambda val: m(scheduler, val))
