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

from hledac.universal.utils.async_helpers import safe_create_task
import logging as _logging
import time as _time
from typing import Any

from runtime.scheduler_v2.protocol import InitResult, SprintContext


class SprintSchedulerV2:
    """SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.

    Replaces the 33 449 LOC `SprintScheduler` with a thin orchestrator that
    delegates to typed Phase implementations. All state is passed explicitly
    via SprintContext rather than stored in __slots__.

    Issue #9 fix: __slots__ = () + __dict__ workaround was anti-pattern.
    __slots__ now properly declares all instance attributes — no __dict__ needed.
    """

    # Issue #9: proper __slots__ — no __dict__ workaround needed.
    # These are transient runtime state (per-run), not shared state.
    __slots__ = (
        # Constructor params (config is owned by caller)
        "_config",
        "_result",          # owned by caller, populated by scheduler
        "_ct_log_client",
        "_ioc_graph",
        "_flags",
        # Runtime state (set in run() / _initialize_sprint_run())
        "_cancel_event",
        "_ctx",
        "_wall_clock_start",
        # Phase orchestrators (transient per run)
        "_lifecycle",
        "_runner",
        "_duckdb_store",
        "_hermes_engine",
        "_governor",
        "_evidence_log",
        "_sidecar_orchestrator",
        "_sidecar_tasks",
        "_acquisition_plan",
        # Winddown extras (transient)
        "_synth_windup_task",
        "_privacy_layer",
        "_privacy_context_id",
        "_prev_chain_hash",
        "_sprint_id",
        "_rel_discovery_engine",
        # Injectable service references (set by inject_* methods)
        "_policy_manager",
        "_prefetch_pipeline",
        "_temporal_predictor",
        "_pivot_planner",
        "_analyst_workbench",
        "_forensics_enricher",
        "_multimodal_enricher",
        "_enrichment_services",
        "_source_economics",
        "_communication_layer",
        "_stealth_layer",
        "_ghost_layer",
    )

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
        # Issue #9: caller-owned result — created here for BC, but documented as owned by caller
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
        # Winddown extras
        self._synth_windup_task: Any = None
        self._privacy_layer: Any = None
        self._privacy_context_id: Any = None
        self._prev_chain_hash: Any = None
        self._sprint_id: str = "unknown"
        self._rel_discovery_engine: Any = None

    async def run(self, query: str) -> Any:
        """Run the sprint — orchestrate prelude → acquisition → winddown phases."""
        _wall_clock_start = _time.monotonic()
        self._wall_clock_start = _wall_clock_start
        self._cancel_event = asyncio.Event()

        # Phase 1: Build SprintContext with required fields
        # All services injected via with_services() in _initialize_sprint_run
        self._ctx = SprintContext(
            config=self._config,
            query=query,
            result=self._result,
            ct_log_client=self._ct_log_client,
            graph_service=self._ioc_graph,
            cancel_event=self._cancel_event,
        )

        # Phase 2: Initialize sprint (DuckDB, governor, lifecycle)
        await self._initialize_sprint_run(query, _wall_clock_start)

        # Phase 3: Run prelude + first cycle in parallel
        await self._run_prelude_and_first_cycle(query)

        # Phase 4: Run acquisition loop until terminal
        await self._run_acquisition_loop(query)

        # Phase 5: Winddown
        await self._run_winddown(query)

        # Phase 6: Return result
        return self._result

    async def _initialize_sprint_run(self, query: str, wall_clock_start: float) -> None:
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

        # DuckDB init (lazy — fail-soft)
        self._duckdb_store = await self._init_duckdb_store(query)

        # Governor init
        self._governor = await self._init_governor()

        # Hermes engine init (lazy)
        self._hermes_engine = await self._init_hermes_engine(query)

        # Evidence log
        self._evidence_log = await self._init_evidence_log()

        # Sidecar orchestrator
        self._sidecar_orchestrator = await self._init_sidecar_orchestrator(query)

        # Build per-cycle state and update SprintContext in one call
        # Note: with_cycle takes raw values for _CycleState fields; InitResult services
        # are passed via with_services instead
        self._ctx = self._ctx.with_cycle(
            wall_clock_start=wall_clock_start,
            lifecycle=_lifecycle_mgr,
            duckdb_store=self._duckdb_store.value if self._duckdb_store else None,
            acquisition_plan=self._acquisition_plan,
            sidecar_orchestrator=self._sidecar_orchestrator.value if self._sidecar_orchestrator else None,
            sidecar_tasks=self._sidecar_tasks,
            hermes_engine=self._hermes_engine.value if self._hermes_engine else None,
            evidence_log=self._evidence_log.value if self._evidence_log else None,
        )

        # Inject services into ctx (type-safe via with_services)
        self._ctx = self._ctx.with_services(
            duckdb_store=self._duckdb_store,
            governor=self._governor,
            hermes_engine=self._hermes_engine,
            evidence_log=self._evidence_log,
            runner=_lifecycle_mgr,
            lifecycle=_lifecycle_mgr,
        )

        # Hermes prewarm (fire-and-forget)
        safe_create_task(self._prewarm_hermes())

    async def _init_duckdb_store(self, query: str) -> InitResult[Any]:
        """Initialize DuckDBShadowStore (fail-soft)."""
        _t0 = _time.monotonic()
        try:
            from hledac.universal._lazy_imports import get_DuckDBShadowStore

            DuckDBShadowStore = get_DuckDBShadowStore()
            store = DuckDBShadowStore()
            await store.async_init()
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] DuckDB init OK (%.1fms)", _elapsed)
            return InitResult.success(store, _elapsed)
        except Exception as e:
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] DuckDB init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_governor(self) -> InitResult[Any]:
        """Initialize M1ResourceGovernor (fail-soft)."""
        _t0 = _time.monotonic()
        try:
            from hledac.universal._lazy_imports import get_M1ResourceGovernor

            M1ResourceGovernor = get_M1ResourceGovernor()
            governor = M1ResourceGovernor()
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Governor init OK (%.1fms)", _elapsed)
            return InitResult.success(governor, _elapsed)
        except Exception as e:
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Governor init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_hermes_engine(self, query: str) -> InitResult[Any]:
        """Initialize Hermes3Engine (fail-soft)."""
        _t0 = _time.monotonic()
        try:
            from hledac.universal._lazy_imports import get_Hermes3Engine

            Hermes3Engine = get_Hermes3Engine()
            engine = Hermes3Engine()
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Hermes engine init OK (%.1fms)", _elapsed)
            return InitResult.success(engine, _elapsed)
        except Exception as e:
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Hermes engine init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_evidence_log(self) -> InitResult[Any]:
        """Initialize EvidenceLog (fail-soft)."""
        _t0 = _time.monotonic()
        try:
            from hledac.universal._lazy_imports import get_EvidenceLog

            EvidenceLog = get_EvidenceLog()
            elog = EvidenceLog()
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] EvidenceLog init OK (%.1fms)", _elapsed)
            return InitResult.success(elog, _elapsed)
        except Exception as e:
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] EvidenceLog init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_sidecar_orchestrator(self, query: str) -> InitResult[Any]:
        """Initialize SidecarOrchestrator (fail-soft)."""
        _t0 = _time.monotonic()
        try:
            from hledac.universal._lazy_imports import get_SidecarOrchestrator

            SidecarOrchestrator = get_SidecarOrchestrator()
            orch = SidecarOrchestrator(
                config=self._config,
                query=query,
                result=self._result,
            )
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] SidecarOrchestrator init OK (%.1fms)", _elapsed)
            return InitResult.success(orch, _elapsed)
        except Exception as e:
            _elapsed = (_time.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] SidecarOrchestrator init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _prewarm_hermes(self) -> None:
        """Prewarm Hermes model in background."""
        if self._hermes_engine is None or not self._hermes_engine.ok:
            return
        try:
            await self._hermes_engine.value.load()
        except Exception:
            pass

    async def _run_prelude_and_first_cycle(self, query: str) -> None:
        """Run prelude lanes and first cycle in parallel.

        Corresponds to v1's gather at lines ~7755-7858.
        """
        from hledac.universal._lazy_imports import get_async_helpers

        safe_create_task, safe_gather_return_exceptions = get_async_helpers()

        # Build acquisition plan first (sequential in v1, but prelude can run in parallel)
        self._acquisition_plan = await self._build_acquisition_plan(query)
        self._ctx = self._ctx.with_cycle(acquisition_plan=self._acquisition_plan)

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
        if self._hermes_engine is not None and self._hermes_engine.ok:
            self._hermes_engine.value._active_iteration_count = self._result.cycles_started

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

        # Extract raw store from InitResult (lane functions expect the actual store)
        _duckdb_raw = self._duckdb_store.value if self._duckdb_store else None

        # Build ordered sources for CT bridge
        ordered_sources = getattr(self._acquisition_plan, 'ordered_sources', []) if self._acquisition_plan else []

        _t_prelude_start = _t.time()

        # Run all prelude lanes concurrently (bounded concurrency = 5)
        _coros = [
            run_public_prelude_lane(query),
            run_ct_prelude_lane(query, self._result, seed_context=None),
            run_wayback_prelude_lane(
                query, self._result, _duckdb_raw, _t, seed_context=None
            ),
            run_pdns_prelude_lane(
                query, self._result, _duckdb_raw, _t, seed_context=None
            ),
            run_doh_prelude_lane(
                query, self._result, _duckdb_raw, _t, pivot_doh_items=None, seed_context=None
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
            self._ctx,
            ordered_sources,
            None,  # now_monotonic
            self._duckdb_store,
        )
        return _result.cycle_ok

    async def _build_acquisition_plan(self, query: str) -> Any:
        """Build acquisition plan from query + governor state."""
        try:
            from hledac.universal._lazy_imports import get_acquisition_strategy

            build_acquisition_plan, _ = get_acquisition_strategy()

            _uma_state = "ok"
            if self._governor and self._governor.ok:
                try:
                    _gov_dec = await self._governor.value.evaluate()
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

        # Extract raw store from InitResult for phase orchestrators
        _duckdb_raw = self._duckdb_store.value if self._duckdb_store else None
        _sidecar_raw = self._sidecar_orchestrator.value if self._sidecar_orchestrator else None

        # Wire per-cycle state via type-safe with_cycle() — single call replaces 12 _ctx._xxx assignments
        self._ctx = self._ctx.with_cycle(
            wall_clock_start=self._wall_clock_start,
            lifecycle=self._lifecycle,
            duckdb_store=_duckdb_raw,
            stop_requested=False,
            prewindup_barrier_delayed=False,
            barrier_retry_count=0,
            cycle_time_ema=1.0,
            last_cycle_start=None,
            effective_max_cycles=self._config.max_cycles,
            enrichment_services=None,
            sidecar_orchestrator=_sidecar_raw,
        )

        _phase_result = await _orch.run(
            ctx=self._ctx,
            ordered_sources=ordered_sources,
            duckdb_store=_duckdb_raw,
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

        # Extract raw values from InitResult for phase orchestrators
        _duckdb_raw = self._duckdb_store.value if self._duckdb_store else None
        _hermes_raw = self._hermes_engine.value if self._hermes_engine else None
        _evidence_raw = self._evidence_log.value if self._evidence_log else None

        # Wire winddown-specific per-cycle state via type-safe with_cycle()
        # Single call replaces 13 self._ctx._xxx = ... assignments
        self._ctx = self._ctx.with_cycle(
            duckdb_store=_duckdb_raw,
            sidecar_orchestrator=self._sidecar_orchestrator,
            sidecar_tasks=self._sidecar_tasks,
            synth_windup_task=getattr(self, '_synth_windup_task', None),
            hermes_engine=_hermes_raw,
            privacy_layer=getattr(self, '_privacy_layer', None),
            privacy_context_id=getattr(self, '_privacy_context_id', None),
            evidence_log=_evidence_raw,
            prev_chain_hash=getattr(self, '_prev_chain_hash', None),
            sprint_id=getattr(self, '_sprint_id', 'unknown'),
            int_counter_layout=getattr(self._result, '_int_counter_layout', None),
            rel_discovery_engine=getattr(self, '_rel_discovery_engine', None),
        )

        _orch = WinddownOrchestrator()
        await _orch.run(
            ctx=self._ctx,
            lifecycle=self._lifecycle,
            query=query,
        )

    # ── Backward-compat property ─────────────────────────────────────────────

    @property
    def sprint_id(self) -> str:
        """Read-only sprint_id from the result object."""
        return getattr(self._result, "sprint_id", "")

    # Issue #9 fix: removed __dict__ workaround — __slots__ provides direct access.
    # _result is a proper __slots__ attribute, no property needed.

    # ── Inject methods (v1 compat stubs) ──────────────────────────────────

    def inject_evidence_log(self, elog: Any) -> None:
        """Inject a pre-initialized EvidenceLog (wraps in InitResult.success)."""
        self._evidence_log = InitResult.success(elog, 0.0)
        if self._ctx:
            # Set both public (SprintContext) and private (_CycleState) fields
            self._ctx = self._ctx.with_cycle(evidence_log=elog)

    def inject_policy_manager(self, policy_manager: Any) -> None:
        self._policy_manager = policy_manager
        if self._ctx:
            self._ctx = self._ctx.with_services(governor=policy_manager)

    def inject_communication_layer(self, layer: Any) -> None:
        self._communication_layer = layer
        if self._ctx:
            self._ctx = self._ctx.with_cycle(privacy_layer=layer)

    def inject_stealth_layer(self, layer: Any) -> None:
        self._stealth_layer = layer
        if self._ctx:
            self._ctx = self._ctx.with_cycle(privacy_layer=layer)

    def inject_ghost_layer(self, layer: Any) -> None:
        self._ghost_layer = layer
        if self._ctx:
            self._ctx = self._ctx.with_cycle(privacy_layer=layer)

    def inject_security_coordinator(self, coordinator: Any) -> None:
        self._security_coordinator = coordinator
        if self._ctx:
            self._ctx = self._ctx.with_services(governor=coordinator)

    def inject_prefetch_oracle(self, oracle: Any) -> None:
        self._prefetch_oracle = oracle
        if self._ctx:
            self._ctx = self._ctx.with_cycle(enrichment_services=oracle)

    def inject_duckdb_store(self, store: Any) -> None:
        """Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success)."""
        self._duckdb_store = InitResult.success(store, 0.0)
        if self._ctx:
            # Set both public (SprintContext) and private (_CycleState) fields
            self._ctx = self._ctx.with_cycle(duckdb_store=store)

    def inject_prefetch_pipeline(self, pipeline: Any) -> None:
        self._prefetch_pipeline = pipeline
        if self._ctx:
            self._ctx = self._ctx.with_cycle(enrichment_services=pipeline)

    def inject_temporal_predictor(self, predictor: Any) -> None:
        self._temporal_predictor = predictor

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
            self._ctx = self._ctx.with_cycle(enrichment_services=services)

    def inject_source_economics(self, economics: Any) -> None:
        self._source_economics = economics

    def inject_privacy_layer(self, layer: Any) -> None:
        self._privacy_layer = layer
        if self._ctx:
            self._ctx = self._ctx.with_cycle(privacy_layer=layer)

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        self._ioc_graph = ioc_graph
        if self._ctx:
            self._ctx = self._ctx.with_services(graph_service=ioc_graph)

    async def health_check(self) -> Any:
        """Stub health check — returns None (pass)."""
        return None
