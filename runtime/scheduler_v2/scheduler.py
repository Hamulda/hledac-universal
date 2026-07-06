"""STEP 4 Phase 5 — SprintSchedulerV2: thin orchestrator for runtime/sprint_scheduler.py v2.

F350M-R / Issue #P2.

SprintSchedulerV2 is the thin orchestrator that wires Phase protocols together.
Each phase is delegated to its orchestrator (PreludeOrchestrator, AcquisitionOrchestrator,
WinddownOrchestrator) via asyncio.TaskGroup.

Target LOC: ~200 lines (vs v1's 33 449 lines).

Wiring:
    run()
      ├─ _initialize_sprint_run()     → DuckDB, lifecycle, governor, Hermes prewarm
      ├─ _run_prelude_and_first_cycle() → prelude + first cycle in parallel
      ├─ _run_acquisition_loop()      → AcquisitionOrchestrator.run()
      └─ _run_winddown()              → WinddownOrchestrator.run()
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from runtime.scheduler_v2.protocol import SprintContext


class SprintSchedulerV2:
    """SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.

    Replaces the 33 449 LOC `SprintScheduler` with a thin orchestrator that
    delegates to typed Phase implementations. All state is passed explicitly
    via SprintContext rather than stored in __slots__.
    """

    __slots__ = ()  # No instance slots — all state in SprintContext

    def __init__(
        self,
        config: Any,
        ct_log_client: Any = None,
        flags: Any = None,
        *,
        ioc_graph: Any = None,
    ) -> None:
        # Lazy imports to avoid M1 Metal initialization at import time
        from runtime.scheduler_config import SprintSchedulerConfig
        from runtime.scheduler_result import SprintSchedulerResult

        self._config: SprintSchedulerConfig = config
        self._result: SprintSchedulerResult = SprintSchedulerResult()
        self._ct_log_client = ct_log_client
        self._ioc_graph = ioc_graph
        self._flags = flags
        self._cancel_event: asyncio.Event | None = None
        self._ctx: SprintContext | None = None
        self._acquisition_plan: Any = None
        self._wall_clock_start: float = 0.0
        self._lifecycle: Any = None
        self._runner: Any = None
        self._duckdb_store: Any = None
        self._hermes_engine: Any = None
        self._governor: Any = None
        self._evidence_log: Any = None
        self._sidecar_orchestrator: Any = None
        self._sidecar_tasks: set = set()

    async def run(self, query: str) -> Any:
        """Run the sprint — orchestrate prelude → acquisition → winddown phases."""
        _t0 = _time.monotonic()
        self._wall_clock_start = _time.monotonic()

        # Phase 1: Build SprintContext
        self._cancel_event = asyncio.Event()
        self._ctx = SprintContext(
            config=self._config,
            query=query,
            result=self._result,
            duckdb_store=None,  # Initialized in _initialize_sprint_run
            graph_service=self._ioc_graph,
            hermes_engine=None,
            governor=None,
            evidence_log=None,
            ct_log_client=self._ct_log_client,
            runner=None,
            lifecycle=None,
            cancel_event=self._cancel_event,
            bg_tasks=set(),
        )

        # Phase 2: Initialize sprint (DuckDB, governor, lifecycle)
        await self._initialize_sprint_run(query)

        # Phase 3: Run prelude + first cycle in parallel
        await self._run_prelude_and_first_cycle(query)

        # Phase 4: Run acquisition loop until terminal
        await self._run_acquisition_loop(query)

        # Phase 5: Winddown
        await self._run_winddown(query)

        # Phase 6: Return result
        return self._result

    async def _initialize_sprint_run(self, query: str) -> None:
        """Initialize DuckDB, lifecycle, governor, Hermes prewarm.

        Corresponds to v1's _initialize_sprint_run (lines ~6600-7168).
        """
        from runtime.scheduler_lifecycle_manager import SprintLifecycleManager

        _lifecycle_mgr = SprintLifecycleManager(
            config=self._config,
            result=self._result,
            cancel_event=self._cancel_event,
        )
        self._lifecycle = _lifecycle_mgr
        self._runner = _lifecycle_mgr

        # Wire ctx lifecycle fields
        self._ctx.lifecycle = _lifecycle_mgr
        self._ctx.runner = _lifecycle_mgr
        self._ctx._wall_clock_start = self._wall_clock_start  # type: ignore

        # DuckDB init (lazy — fail-soft)
        self._duckdb_store = await self._init_duckdb_store(query)
        self._ctx.duckdb_store = self._duckdb_store

        # Governor init
        self._governor = await self._init_governor()
        self._ctx.governor = self._governor

        # Hermes engine init (lazy)
        self._hermes_engine = await self._init_hermes_engine(query)
        self._ctx.hermes_engine = self._hermes_engine

        # Evidence log
        self._evidence_log = await self._init_evidence_log()
        self._ctx.evidence_log = self._evidence_log

        # Sidecar orchestrator
        self._sidecar_orchestrator = await self._init_sidecar_orchestrator(query)
        self._ctx._sidecar_orchestrator = self._sidecar_orchestrator  # type: ignore
        self._ctx._sidecar_tasks = self._sidecar_tasks  # type: ignore

        # Wire DuckDB + governor into ctx
        self._ctx._duckdb_store = self._duckdb_store  # type: ignore
        self._ctx._lifecycle = self._lifecycle  # type: ignore
        self._ctx._acquisition_plan = self._acquisition_plan  # type: ignore
        self._ctx._sidecar_orchestrator = self._sidecar_orchestrator  # type: ignore

        # Hermes prewarm (fire-and-forget)
        asyncio.create_task(self._prewarm_hermes())

    async def _init_duckdb_store(self, query: str) -> Any:
        """Initialize DuckDBShadowStore."""
        try:
            from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
            store = DuckDBShadowStore()
            await store.async_init()
            return store
        except Exception:
            return None

    async def _init_governor(self) -> Any:
        """Initialize M1ResourceGovernor."""
        try:
            from hledac.universal.core.resource_governor import M1ResourceGovernor
            return M1ResourceGovernor()
        except Exception:
            return None

    async def _init_hermes_engine(self, query: str) -> Any:
        """Initialize Hermes3Engine."""
        try:
            from hledac.universal.brain.hermes_engine import Hermes3Engine
            engine = Hermes3Engine()
            return engine
        except Exception:
            return None

    async def _init_evidence_log(self) -> Any:
        """Initialize EvidenceLog."""
        try:
            from hledac.universal.utils.evidence_log import EvidenceLog
            return EvidenceLog()
        except Exception:
            return None

    async def _init_sidecar_orchestrator(self, query: str) -> Any:
        """Initialize SidecarOrchestrator."""
        try:
            from hledac.universal.runtime.sidecar_orchestrator import SidecarOrchestrator
            return SidecarOrchestrator(
                config=self._config,
                query=query,
                result=self._result,
            )
        except Exception:
            return None

    async def _prewarm_hermes(self) -> None:
        """Prewarm Hermes model in background."""
        if self._hermes_engine is None:
            return
        try:
            await self._hermes_engine.load()
        except Exception:
            pass

    async def _run_prelude_and_first_cycle(self, query: str) -> None:
        """Run prelude lanes and first cycle in parallel.

        Corresponds to v1's gather at lines ~7755-7858.
        """
        from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_return_exceptions

        # Build acquisition plan first (sequential in v1, but prelude can run in parallel)
        self._acquisition_plan = await self._build_acquisition_plan(query)
        self._ctx._acquisition_plan = self._acquisition_plan  # type: ignore

        # Prelude task
        prelude_coro = self._run_prelude(query)
        prelude_task = safe_create_task(prelude_coro, name="sprint:prelude", eager_start=True)

        # First cycle task
        first_cycle_coro = self._run_first_cycle(query)
        first_cycle_task = safe_create_task(first_cycle_coro, name="sprint:first_cycle", eager_start=True)

        # Gather both
        _results = await safe_gather_return_exceptions(
            prelude_task,
            first_cycle_task,
            label="sprint_v2:prelude_first_cycle",
        )

        _prelude_exc, _cycle_exc = _results[0], _results[1]

        if isinstance(_prelude_exc, BaseException) and not isinstance(_prelude_exc, asyncio.CancelledError):
            import logging
            logging.warning("[sprint_v2] prelude raised: %s: %s", type(_prelude_exc).__name__, _prelude_exc)

        if isinstance(_cycle_exc, BaseException) and not isinstance(_cycle_exc, asyncio.CancelledError):
            import logging
            logging.warning("[sprint_v2] first cycle raised: %s: %s", type(_cycle_exc).__name__, _cycle_exc)

        # Propagate CancelledError
        if isinstance(_prelude_exc, asyncio.CancelledError):
            raise _prelude_exc
        if isinstance(_cycle_exc, asyncio.CancelledError):
            raise _cycle_exc

        # Increment cycles_completed
        self._result.cycles_completed += 1

        # Update hermes engine iteration count
        if self._hermes_engine is not None:
            self._hermes_engine._active_iteration_count = self._result.cycles_started

    async def _run_prelude(self, query: str) -> Any:
        """Run the prelude phase via PreludeOrchestrator."""
        from runtime.scheduler_v2.prelude import (
            gather_taskgroup,
            run_public_prelude_lane,
            run_ct_prelude_lane,
            run_wayback_prelude_lane,
            run_pdns_prelude_lane,
            run_doh_prelude_lane,
        )
        import time as _t

        # Build ordered sources for CT bridge
        ordered_sources = getattr(self._acquisition_plan, 'ordered_sources', []) if self._acquisition_plan else []

        _t_prelude_start = _t.time()

        # Run all prelude lanes concurrently (bounded concurrency = 5)
        _coros = [
            run_public_prelude_lane(query),
            run_ct_prelude_lane(query, self._result, seed_context=None),
            run_wayback_prelude_lane(
                query, self._result, self._duckdb_store, _t, seed_context=None
            ),
            run_pdns_prelude_lane(
                query, self._result, self._duckdb_store, _t, seed_context=None
            ),
            run_doh_prelude_lane(
                query, self._result, self._duckdb_store, _t, pivot_doh_items=None, seed_context=None
            ),
        ]

        _lane_results = await gather_taskgroup(_coros, concurrency=5, ctx="prelude_v2")
        _lanes_attempted = [r.lane for r in _lane_results if r.attempted]
        _lanes_skipped = {r.lane: r.skip_reason for r in _lane_results if r.skipped}
        _lanes_accepted = {r.lane: r.accepted_count for r in _lane_results if r.accepted_count > 0}

        self._result.prelude_duration_s = _t.time() - _t_prelude_start
        self._result.prelude_lanes_attempted = _lanes_attempted
        self._result.prelude_lanes_skipped = _lanes_skipped
        self._result.prelude_lanes_accepted = _lanes_accepted

        return _lane_results

    async def _run_first_cycle(self, query: str) -> bool:
        """Run the first acquisition cycle (feed only, stable mode)."""
        from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator

        _orch = AcquisitionOrchestrator()
        ordered_sources = getattr(self._acquisition_plan, 'ordered_sources', []) if self._acquisition_plan else []

        # Run one stable cycle (no aggressive on first cycle)
        _result = await _orch._run_one_cycle(
            _orch,
            self._ctx,
            ordered_sources,
            None,  # now_monotonic
            self._duckdb_store,
        )
        return _result.cycle_ok

    async def _build_acquisition_plan(self, query: str) -> Any:
        """Build acquisition plan from query + governor state."""
        try:
            from hledac.universal.runtime.acquisition_strategy import build_acquisition_plan

            _uma_state = "ok"
            if self._governor:
                try:
                    _gov_dec = await self._governor.evaluate()
                    if _gov_dec:
                        _uma_state = getattr(_gov_dec, 'uma_state', 'ok')
                except Exception:
                    pass

            _plan_kwargs = {
                "query": query,
                "duration_s": self._config.sprint_duration_s,
                "aggressive_mode": self._config.aggressive_mode,
                "uma_state": _uma_state,
                "swap_detected": False,
                "accepted_findings_so_far": self._result.accepted_findings,
                "branch_timeout_count": self._result.branch_timeout_count,
                "acquisition_profile": getattr(self._config, 'acquisition_profile', '') or '',
                "source_quality_weights": None,
                "rl_lane_combo": getattr(self._result, 'rl_lane_combo', None),
                "synthetic_domains": [],
            }

            return await asyncio.to_thread(build_acquisition_plan, **_plan_kwargs)
        except Exception:
            return None

    async def _run_acquisition_loop(self, query: str) -> None:
        """Run acquisition cycles until terminal via AcquisitionOrchestrator.

        Corresponds to v1's while-not-terminal loop (lines ~7894-8300+).
        """
        from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator

        _orch = AcquisitionOrchestrator()
        ordered_sources = getattr(self._acquisition_plan, 'ordered_sources', []) if self._acquisition_plan else []

        # Wire extra ctx fields needed by acquisition orchestrator
        self._ctx._wall_clock_start = self._wall_clock_start  # type: ignore
        self._ctx._lifecycle = self._lifecycle  # type: ignore
        self._ctx._duckdb_store = self._duckdb_store  # type: ignore
        self._ctx._stop_requested = False  # type: ignore
        self._ctx._prewindup_barrier_delayed = False  # type: ignore
        self._ctx._barrier_retry_count = 0  # type: ignore
        self._ctx._cycle_time_ema = 1.0  # type: ignore
        self._ctx._last_cycle_start = None  # type: ignore
        self._ctx._effective_max_cycles = self._config.max_cycles  # type: ignore
        self._ctx.enrichment_services = None  # type: ignore
        self._ctx.sidecar_orchestrator = self._sidecar_orchestrator  # type: ignore

        _phase_result = await _orch.run(
            ctx=self._ctx,
            ordered_sources=ordered_sources,
            duckdb_store=self._duckdb_store,
            now_monotonic=None,
        )

        # Merge results back
        self._result.cycles_started = _phase_result.cycles_started
        self._result.cycles_completed = _phase_result.cycles_completed
        self._result.accepted_findings = _phase_result.accepted_findings

    async def _run_winddown(self, query: str) -> None:
        """Run winddown phase via WinddownOrchestrator.

        Corresponds to v1's _run_winddown + teardown (lines ~8976-9200+).
        """
        from runtime.scheduler_v2.winddown import WinddownOrchestrator

        # Wire extra ctx fields needed by winddown orchestrator
        self._ctx._duckdb_store = self._duckdb_store  # type: ignore
        self._ctx._sidecar_orchestrator = self._sidecar_orchestrator  # type: ignore
        self._ctx._sidecar_tasks = self._sidecar_tasks  # type: ignore
        self._ctx._synth_windup_task = getattr(self, '_synth_windup_task', None)  # type: ignore
        self._ctx._hermes_engine = self._hermes_engine  # type: ignore
        self._ctx._privacy_layer = getattr(self, '_privacy_layer', None)  # type: ignore
        self._ctx._privacy_context_id = getattr(self, '_privacy_context_id', None)  # type: ignore
        self._ctx._evidence_log = self._evidence_log  # type: ignore
        self._ctx._prev_chain_hash = getattr(self, '_prev_chain_hash', None)  # type: ignore
        self._ctx._sprint_id = getattr(self, '_sprint_id', 'unknown')  # type: ignore
        self._ctx._int_counter_layout = getattr(self._result, '_int_counter_layout', None)  # type: ignore
        self._ctx._rel_discovery_engine = getattr(self, '_rel_discovery_engine', None)  # type: ignore

        _orch = WinddownOrchestrator()
        await _orch.run(
            ctx=self._ctx,
            lifecycle=self._lifecycle,
            query=query,
        )

    # ── Backward-compat properties (delegated to _result) ──────────────────

    @property
    def sprint_id(self) -> str:
        return getattr(self._result, "sprint_id", "")

    @property
    def _result(self) -> Any:
        return self.__dict__.get("_result")

    @_result.setter
    def _result(self, value: Any) -> None:
        self.__dict__["_result"] = value

    # ── Inject methods (v1 compat stubs) ──────────────────────────────────

    def inject_evidence_log(self, elog: Any) -> None:
        self._evidence_log = elog
        if self._ctx:
            self._ctx.evidence_log = elog
            self._ctx._evidence_log = elog

    def inject_policy_manager(self, policy_manager: Any) -> None:
        self._policy_manager = policy_manager
        if self._ctx:
            self._ctx.policy_manager = policy_manager

    def inject_communication_layer(self, layer: Any) -> None:
        self._communication_layer = layer
        if self._ctx:
            self._ctx.communication_layer = layer

    def inject_stealth_layer(self, layer: Any) -> None:
        self._stealth_layer = layer
        if self._ctx:
            self._ctx.stealth_layer = layer

    def inject_ghost_layer(self, layer: Any) -> None:
        self._ghost_layer = layer
        if self._ctx:
            self._ctx.ghost_layer = layer

    def inject_security_coordinator(self, coordinator: Any) -> None:
        self._security_coordinator = coordinator
        if self._ctx:
            self._ctx.security_coordinator = coordinator

    def inject_prefetch_oracle(self, oracle: Any) -> None:
        self._prefetch_oracle = oracle
        if self._ctx:
            self._ctx.prefetch_oracle = oracle

    def inject_duckdb_store(self, store: Any) -> None:
        self._duckdb_store = store
        if self._ctx:
            self._ctx.duckdb_store = store
            self._ctx._duckdb_store = store

    def inject_prefetch_pipeline(self, pipeline: Any) -> None:
        self._prefetch_pipeline = pipeline
        if self._ctx:
            self._ctx.prefetch_pipeline = pipeline

    def inject_temporal_predictor(self, predictor: Any) -> None:
        self._temporal_predictor = predictor
        if self._ctx:
            self._ctx.temporal_predictor = predictor

    def inject_pivot_planner(self, planner: Any) -> None:
        self._pivot_planner = planner

    def inject_analyst_workbench(self, workbench: Any) -> None:
        self._analyst_workbench = workbench

    def inject_forensics_enricher(self, enricher: Any) -> None:
        self._forensics_enricher = enricher

    def inject_multimodal_enricher(self, enricher: Any) -> None:
        self._multimodal_enricher = enricher

    def inject_enrichment_services(self, services: Any) -> None:
        self._enrichment_services = services
        if self._ctx:
            self._ctx.enrichment_services = services

    def inject_source_economics(self, economics: Any) -> None:
        self._source_economics = economics

    def inject_privacy_layer(self, layer: Any) -> None:
        self._privacy_layer = layer
        if self._ctx:
            self._ctx._privacy_layer = layer

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        self._ioc_graph = ioc_graph
        if self._ctx:
            self._ctx.graph_service = ioc_graph

    async def health_check(self) -> Any:
        """Stub health check — returns None (pass)."""
        return None
