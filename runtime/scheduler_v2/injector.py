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
    from hledac.universal.runtime.scheduler_v2.injector import Injector
    Injector.apply(scheduler)  # attaches all inject_* methods
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from _core import aclose

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
        from hledac.universal.runtime.scheduler_v2.protocol import InitResult, SprintContext

        # Primary: store in SprintContext (canonical path)
        if scheduler._ctx:
            scheduler._ctx = scheduler._ctx.with_services(duckdb_store=InitResult.success(store, 0.0))
        else:
            # Fallback for tests that construct scheduler without run():
            # Build a minimal SprintContext so duckdb_store is accessible
            from hledac.universal.runtime.scheduler_config import SprintSchedulerConfig
            from hledac.universal.runtime.scheduler_result import SprintSchedulerResult

            minimal_ctx = SprintContext(
                config=SprintSchedulerConfig(sprint_duration_s=60.0, cycle_sleep_s=10.0),
                query="test",
                result=SprintSchedulerResult(),
                duckdb_store_result=InitResult.success(store, 0.0),
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

    @staticmethod
    def inject_meta_reasoning_coordinator(scheduler: Any, coordinator: Any) -> None:
        """UNIFIED-006: Wire UniversalMetaReasoningCoordinator into scheduler.
        
        The coordinator provides CoT/ToT/Graph reasoning strategies with
        deterministic crash recovery via DuckDB checkpointing.
        Fail-soft: stored as instance attribute, used by advisory runners.
        """
        scheduler._meta_reasoning_coordinator = coordinator

    @staticmethod
    def inject_gravity_field(scheduler: Any, gravity_field: Any) -> None:
        """SILICON-05: Wire SemanticGravityField into the scheduler.

        The gravity field tracks IOC embedding density for void detection.
        The scheduler pipeline pushes embeddings via add_embedding() as
        findings are accumulated.
        Fail-soft: stored as instance attribute.
        """
        scheduler._gravity_field = gravity_field

    # ── Bulk apply ──────────────────────────────────────────────────────────

    @classmethod
    def apply(cls, scheduler: Any) -> None:
        """Attach all inject_* methods to a scheduler instance.

        After calling this, the scheduler has all inject_* methods available
        as bound methods for v1 API compatibility.

        Each inject method is a staticmethod that takes (scheduler, value) as arguments.
        We create a closure that binds the scheduler for convenience.

        NOTE: Simpler pattern than functools.partial - directly store the method
        with scheduler bound via closure. This is clearer and has minimal overhead.
        """
        for name in dir(cls):
            if not name.startswith("inject_"):
                continue
            if name in ("inject_evidence_log", "inject_cancel_event"):
                continue  # keep these in the scheduler
            method = getattr(cls, name)
            if not callable(method):
                continue

            # Create a bound method that passes scheduler as first argument
            # This is cleaner than functools.partial for static methods
            #
            # CLOSURE CAPTURE PATTERN (SC-06):
            # The closure captures (name, method, scheduler) at DEFINITION time
            # (when make_bound is called), not at call time.
            # This is intentional — each loop iteration creates a new
            # make_bound frame with its own captured values, avoiding the
            # classic "lambda in loop" late-binding bug where all closures
            # would share the same (final) loop variable values.
            def make_bound(name: str, method: Any) -> Any:
                def bound(*args: Any, **kwargs: Any) -> Any:
                    return method(scheduler, *args, **kwargs)
                bound.__name__ = name
                bound.__doc__ = getattr(method, '__doc__', None)
                return bound

            setattr(scheduler, name, make_bound(name, method))
