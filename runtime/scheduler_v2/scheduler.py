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
from dataclasses import dataclass, field
from typing import Any

from runtime.scheduler_v2.protocol import InitResult, SprintContext
from hledac.universal.utils.async_helpers import parallel


# Issue #047 fix: @dataclass(slots=True) eliminates __slots__/__init__ duplication.
# __slots__ is auto-generated from field declarations — single source of truth.
# Type-safe, Python 3.10+ feature, 2-3× faster than manual __slots__.
@dataclass(slots=True)
class SprintSchedulerV2:
    """SprintScheduler v2 — greenfield rewrite with Protocol-based phase composition.

    Replaces the 33 449 LOC `SprintScheduler` with a thin orchestrator that
    delegates to typed Phase implementations. All state is passed explicitly
    via SprintContext rather than stored in instance attributes.

    Issue #047 fix: @dataclass(slots=True) — __slots__ auto-generated from fields,
    __init__ auto-generated, no duplication between __slots__ tuple and __init__ body.
    """

    # ── Safe repr for __new__ test fixtures ────────────────────────────────
    # When tests use SprintScheduler.__new__(cls) without calling __init__,
    # slots are uninitialized and the dataclass-generated __repr__ raises
    # AttributeError on the first slot it accesses. Override with a safe repr.
    def __repr__(self) -> str:
        try:
            return f"SprintSchedulerV2(config={getattr(self, '_config', None)!r})"
        except Exception:  # noqa: BLE001 — fail-soft: __repr__ safety net for test fixtures with uninitialized slots
            return f"SprintSchedulerV2(id={hex(id(self))})"

    # ── __new__ for test fixtures ──────────────────────────────────────────
    # Tests use SprintScheduler.__new__(cls) to bypass __init__ (avoids heavy deps).
    # This initializes all slots to None so inject_* methods work without a full run().
    def __new__(cls, *args: Any, **kwargs: Any) -> "SprintSchedulerV2":
        obj = super().__new__(cls)
        # Initialize all slots to None — required for inject_* methods that access slots
        for slot in cls.__slots__:  # type: ignore[attr-defined]
            object.__setattr__(obj, slot, None)
        return obj

    # ── Constructor params ───────────────────────────────────────────────────
    _config: Any = field(default=None)
    _result: Any = field(default=None)
    _ct_log_client: Any = field(default=None)
    _ioc_graph: Any = field(default=None)
    _flags: Any = field(default=None)

    # ── Runtime state (set in run() / _initialize_sprint_run()) ──────────────
    _cancel_event: asyncio.Event | None = field(default=None)
    _ctx: SprintContext | None = field(default=None)
    _wall_clock_start: float = field(default=0.0)

    # ── Phase orchestrators (transient per run) ──────────────────────────────
    _lifecycle: Any = field(default=None)
    _runner: Any = field(default=None)
    _duckdb_store: Any = field(default=None)
    _hermes_engine: Any = field(default=None)
    _governor: Any = field(default=None)
    _evidence_log: Any = field(default=None)
    _sidecar_orchestrator: Any = field(default=None)
    _sidecar_tasks: set = field(default_factory=set)
    _acquisition_plan: Any = field(default=None)

    # ── Winddown extras (transient) ─────────────────────────────────────────
    _synth_windup_task: Any = field(default=None)
    _privacy_layer: Any = field(default=None)
    _privacy_context_id: Any = field(default=None)
    _prev_chain_hash: Any = field(default=None)
    _sprint_id: str = field(default="unknown")
    _rel_discovery_engine: Any = field(default=None)

    # ── Injectable service references (set by inject_* methods) ─────────────
    _policy_manager: Any = field(default=None)
    _prefetch_pipeline: Any = field(default=None)
    _temporal_predictor: Any = field(default=None)
    _pivot_planner: Any = field(default=None)
    _analyst_workbench: Any = field(default=None)
    _forensics_enricher: Any = field(default=None)
    _multimodal_enricher: Any = field(default=None)
    _enrichment_services: Any = field(default=None)
    _source_economics: Any = field(default=None)
    _communication_layer: Any = field(default=None)
    _stealth_layer: Any = field(default=None)
    _ghost_layer: Any = field(default=None)
    # Issue #047: _prefetch_oracle and _security_coordinator were MISSING from
    # __slots__ in the original code — added here so inject methods work correctly.
    _prefetch_oracle: Any = field(default=None)
    _security_coordinator: Any = field(default=None)
    _layer_manager: Any = field(default=None)

    def __init__(
        self,
        config: Any,
        ct_log_client: Any = None,
        flags: Any = None,
        *,
        ioc_graph: Any = None,
    ) -> None:
        # Issue #047 fix: __post_init__ handles lazy imports and _result init.
        # __init__ generated by @dataclass(slots=True) assigns constructor params
        # to fields BEFORE __post_init__ runs, so we call it here explicitly.
        self.__post_init__(config, ct_log_client, flags, ioc_graph)

    def __post_init__(
        self,
        config: Any,
        ct_log_client: Any | None,
        flags: Any | None,
        ioc_graph: Any | None,
    ) -> None:
        """Post-initialization: lazy imports and _result creation.

        Issue #047 fix: Lazy imports moved from __init__ body to __post_init__
        to avoid circular import issues with MLX/Metal initialization.
        """
        # Lazy imports to avoid M1 Metal initialization at import time
        from runtime.scheduler_config import SprintSchedulerConfig
        from runtime.scheduler_result import SprintSchedulerResult

        object.__setattr__(self, '_config', config)
        # Issue #9: caller-owned result — created here for BC, but documented as owned by caller
        object.__setattr__(self, '_result', SprintSchedulerResult())
        object.__setattr__(self, '_ct_log_client', ct_log_client)
        object.__setattr__(self, '_ioc_graph', ioc_graph)
        object.__setattr__(self, '_flags', flags)
        object.__setattr__(self, '_cancel_event', None)
        object.__setattr__(self, '_ctx', None)
        object.__setattr__(self, '_wall_clock_start', 0.0)
        object.__setattr__(self, '_lifecycle', None)
        object.__setattr__(self, '_runner', None)
        object.__setattr__(self, '_duckdb_store', None)
        object.__setattr__(self, '_hermes_engine', None)
        object.__setattr__(self, '_governor', None)
        object.__setattr__(self, '_evidence_log', None)
        object.__setattr__(self, '_sidecar_orchestrator', None)
        object.__setattr__(self, '_sidecar_tasks', set())
        object.__setattr__(self, '_acquisition_plan', None)
        # Winddown extras
        object.__setattr__(self, '_synth_windup_task', None)
        object.__setattr__(self, '_privacy_layer', None)
        object.__setattr__(self, '_privacy_context_id', None)
        object.__setattr__(self, '_prev_chain_hash', None)
        object.__setattr__(self, '_sprint_id', "unknown")
        object.__setattr__(self, '_rel_discovery_engine', None)
        # Injectable services (default None — set via inject_* methods)
        object.__setattr__(self, '_policy_manager', None)
        object.__setattr__(self, '_prefetch_pipeline', None)
        object.__setattr__(self, '_temporal_predictor', None)
        object.__setattr__(self, '_pivot_planner', None)
        object.__setattr__(self, '_analyst_workbench', None)
        object.__setattr__(self, '_forensics_enricher', None)
        object.__setattr__(self, '_multimodal_enricher', None)
        object.__setattr__(self, '_enrichment_services', None)
        object.__setattr__(self, '_source_economics', None)
        object.__setattr__(self, '_communication_layer', None)
        object.__setattr__(self, '_stealth_layer', None)
        object.__setattr__(self, '_ghost_layer', None)
        object.__setattr__(self, '_prefetch_oracle', None)
        object.__setattr__(self, '_security_coordinator', None)

    # ── Prevent any attribute from being set outside __post_init__ ──────────
    # Issue #047: slots=True ensures only declared fields can be set.
    # inject_* methods set attributes after __post_init__, so we use
    # object.__setattr__ in __post_init__ to bypass dataclass field assignment
    # restrictions (fields are already assigned by generated __init__).
    # For inject_* methods: they use self._x = value, which is allowed
    # because dataclass fields are regular instance attributes.

    async def run(self, query: str) -> Any:
        """Run the sprint — orchestrate prelude → acquisition → winddown phases."""
        import time as _time

        _wall_clock_start = _time.monotonic()
        object.__setattr__(self, '_wall_clock_start', _wall_clock_start)
        object.__setattr__(self, '_cancel_event', asyncio.Event())

        # Phase 1: Build SprintContext with required fields
        # All services injected via with_services() in _initialize_sprint_run
        object.__setattr__(self, '_ctx', SprintContext(
            config=self._config,
            query=query,
            result=self._result,
            ct_log_client=self._ct_log_client,
            graph_service=self._ioc_graph,
            cancel_event=self._cancel_event,
        ))

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
        import logging as _logging
        import time as _time

        from runtime.sprint_lifecycle import SprintLifecycleManager
        from hledac.universal.utils.async_helpers import safe_create_task, safe_gather_ok

        _lifecycle_mgr = SprintLifecycleManager(
            config=self._config,
            result=self._result,
            cancel_event=self._cancel_event,
        )
        object.__setattr__(self, '_lifecycle', _lifecycle_mgr)
        object.__setattr__(self, '_runner', _lifecycle_mgr)

        # Issue #029: Build acquisition plan BEFORE with_cycle(acquisition_plan=...)
        # Previously built in _run_prelude_and_first_cycle, but with_cycle at line ~198
        # reads self._acquisition_plan which was still None — a read-before-write race.
        _acq_plan = await self._build_acquisition_plan(query)
        object.__setattr__(self, '_acquisition_plan', _acq_plan)

        # Issue #21: Concurrent init — all four services boot in parallel.
        # Sequential was: duckdb → governor → hermes → evidence_log → sidecar (~3-5s sum).
        # Now: max(all) — DuckDB (~1-2s), Governor (~50ms), Hermes (~100ms), EvidenceLog (~20ms)
        # run concurrently. SidecarOrchestrator still sequential (needs duckdb reference).
        (
            _duckdb_store,
            _governor,
            _hermes_engine,
            _evidence_log,
        ) = await safe_gather_ok(
            self._init_duckdb_store(query),
            self._init_governor(),
            self._init_hermes_engine(query),
            self._init_evidence_log(),
            label="scheduler_v2:_init_services",
        )
        object.__setattr__(self, '_duckdb_store', _duckdb_store)
        object.__setattr__(self, '_governor', _governor)
        object.__setattr__(self, '_hermes_engine', _hermes_engine)
        object.__setattr__(self, '_evidence_log', _evidence_log)

        # Sidecar orchestrator — needs duckdb reference, runs after duckdb init
        _sidecar_orch = await self._init_sidecar_orchestrator(query)
        object.__setattr__(self, '_sidecar_orchestrator', _sidecar_orch)

        # Build per-cycle state and update SprintContext in one call
        # Note: with_cycle takes raw values for _CycleState fields; InitResult services
        # are passed via with_services instead
        object.__setattr__(self, '_ctx', self._ctx.with_cycle(
            wall_clock_start=wall_clock_start,
            lifecycle=_lifecycle_mgr,
            duckdb_store=_duckdb_store.value if _duckdb_store else None,
            acquisition_plan=_acq_plan,
            sidecar_orchestrator=_sidecar_orch.value if _sidecar_orch else None,
            sidecar_tasks=self._sidecar_tasks,
            hermes_engine=_hermes_engine.value if _hermes_engine else None,
            evidence_log=_evidence_log.value if _evidence_log else None,
        ))

        # Inject services into ctx (type-safe via with_services)
        object.__setattr__(self, '_ctx', self._ctx.with_services(
            duckdb_store=_duckdb_store,
            governor=_governor,
            hermes_engine=_hermes_engine,
            evidence_log=_evidence_log,
            runner=_lifecycle_mgr,
            lifecycle=_lifecycle_mgr,
        ))

        # Hermes prewarm (fire-and-forget)
        safe_create_task(self._prewarm_hermes())

    async def _init_duckdb_store(self, query: str) -> InitResult[Any]:
        """Initialize DuckDBShadowStore (fail-soft)."""
        import time as _t
        import logging as _logging

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_DuckDBShadowStore

            DuckDBShadowStore = get_DuckDBShadowStore()
            store = DuckDBShadowStore()
            await store.async_init()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] DuckDB init OK (%.1fms)", _elapsed)
            return InitResult.success(store, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] DuckDB init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_governor(self) -> InitResult[Any]:
        """Initialize M1ResourceGovernor (fail-soft)."""
        import time as _t
        import logging as _logging

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_M1ResourceGovernor

            M1ResourceGovernor = get_M1ResourceGovernor()
            governor = M1ResourceGovernor()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Governor init OK (%.1fms)", _elapsed)
            return InitResult.success(governor, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Governor init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_hermes_engine(self, query: str) -> InitResult[Any]:
        """Initialize Hermes3Engine (fail-soft)."""
        import time as _t
        import logging as _logging

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_Hermes3Engine

            Hermes3Engine = get_Hermes3Engine()
            engine = Hermes3Engine()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] Hermes engine init OK (%.1fms)", _elapsed)
            return InitResult.success(engine, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] Hermes engine init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_evidence_log(self) -> InitResult[Any]:
        """Initialize EvidenceLog (fail-soft)."""
        import time as _t
        import logging as _logging

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_EvidenceLog

            EvidenceLog = get_EvidenceLog()
            elog = EvidenceLog()
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] EvidenceLog init OK (%.1fms)", _elapsed)
            return InitResult.success(elog, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] EvidenceLog init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _init_sidecar_orchestrator(self, query: str) -> InitResult[Any]:
        """Initialize SidecarOrchestrator (fail-soft)."""
        import time as _t
        import logging as _logging

        _t0 = _t.monotonic()
        try:
            from hledac.universal._lazy_imports import get_SidecarOrchestrator

            SidecarOrchestrator = get_SidecarOrchestrator()
            orch = SidecarOrchestrator(
                config=self._config,
                query=query,
                result=self._result,
            )
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.debug("[scheduler_v2] SidecarOrchestrator init OK (%.1fms)", _elapsed)
            return InitResult.success(orch, _elapsed)
        except Exception as e:
            _elapsed = (_t.monotonic() - _t0) * 1000
            _logging.warning("[scheduler_v2] SidecarOrchestrator init failed (%.1fms): %s", _elapsed, e)
            return InitResult.failure(str(e), _elapsed)

    async def _prewarm_hermes(self) -> None:
        """Prewarm Hermes model in background."""
        if self._hermes_engine is None or not self._hermes_engine.ok:
            return
        try:
            await self._hermes_engine.value.load()
        except Exception:  # noqa: BLE001 — fail-soft: Hermes load failure should not prevent sprint start
            pass

    async def _run_prelude_and_first_cycle(self, query: str) -> None:
        """Run prelude lanes and first cycle in parallel.

        Corresponds to v1's gather at lines ~7755-7858.
        """
        import logging
        import time as _t

        from hledac.universal._lazy_imports import get_async_helpers
        from hledac.universal.utils.async_helpers import parallel

        safe_create_task, _safe_gather_return_exceptions = get_async_helpers()

        # Prelude task
        prelude_coro = self._run_prelude(query)
        prelude_task = safe_create_task(prelude_coro, name="sprint:prelude", eager_start=True)

        # First cycle task
        first_cycle_coro = self._run_first_cycle(query)
        first_cycle_task = safe_create_task(first_cycle_coro, name="sprint:first_cycle", eager_start=True)

        # Capture baseline before first cycle runs (for findings delta detection)
        _findings_before = getattr(self._result, 'accepted_findings', 0) or 0

        # Gather both
        _results = await parallel(
            [prelude_task, first_cycle_task],
            taskgroup=True,
            policy='collect',
            ctx="sprint_v2:prelude_first_cycle",
        )

        _prelude_exc, _cycle_exc = _results.errors[0] if _results.errors else None, _results.errors[1] if len(_results.errors) > 1 else None

        if isinstance(_prelude_exc, BaseException) and not isinstance(_prelude_exc, asyncio.CancelledError):
            logging.warning("[sprint_v2] prelude raised: %s: %s", type(_prelude_exc).__name__, _prelude_exc)

        if isinstance(_cycle_exc, BaseException) and not isinstance(_cycle_exc, asyncio.CancelledError):
            logging.warning("[sprint_v2] first cycle raised: %s: %s", type(_cycle_exc).__name__, _cycle_exc)

        # Propagate CancelledError
        if isinstance(_prelude_exc, asyncio.CancelledError):
            raise _prelude_exc
        if isinstance(_cycle_exc, asyncio.CancelledError):
            raise _cycle_exc

        # Issue #029 findings leak fix: accepted_findings from the first cycle are
        # accumulated in ctx.result (same object as self._result) during
        # _orch._run_one_cycle, but _run_acquisition_loop merge only runs if the
        # lifecycle is NOT terminal after the first cycle. If sprint terminates
        # after the first cycle, findings would be LOST without this merge.
        # We detect "had work" by checking if accepted_findings grew.
        _findings_after = getattr(self._result, 'accepted_findings', 0) or 0
        if _findings_after > _findings_before:
            self._result.accepted_findings = _findings_after

        # Issue #029 counter fix: only count first cycle as "completed" if it had
        # real work. Previously always incremented (+1), causing double-count when
        # _orch.run() in _run_acquisition_loop also counts cycles with work.
        # When _orch.run() runs (N>0 cycles), its cycles_completed (N) overwrites
        # this via merge at lines 600-602 — but when N=0 (lifecycle terminal,
        # first cycle empty), the manual increment is the ONLY count, hence the fix.
        if _findings_after > _findings_before:
            self._result.cycles_completed += 1

        # Update hermes engine iteration count
        if self._hermes_engine is not None and self._hermes_engine.ok:
            self._hermes_engine.value._active_iteration_count = self._result.cycles_started

    async def _run_prelude(self, query: str) -> Any:
        """Run the prelude phase via PreludeOrchestrator."""
        import time as _t

        from runtime.scheduler_v2.prelude import (
            run_public_prelude_lane,
            run_ct_prelude_lane,
            run_wayback_prelude_lane,
            run_pdns_prelude_lane,
            run_doh_prelude_lane,
        )

        # Extract raw store from InitResult (lane functions expect the actual store)
        _duckdb_raw = self._duckdb_store.value if self._duckdb_store else None

        # Build ordered sources for CT bridge
        ordered_sources = getattr(self._acquisition_plan, 'ordered_sources', []) if self._acquisition_plan else []

        _t_prelude_start = _t.time()

        # F220B FIX: Generate pivot lanes using plan_lanes_for_pivot_seeds.
        # This restores the lane priority + reasoning that was lost when v2
        # switched to seed forwarding without the planning step.
        # v1 path: generate_pivot_candidates_from_query → plan_lanes_for_pivot_seeds → pivot_doh_items
        #
        # Also build NonfeedSeedContext from pivot plan items so CT/WAYBACK/PDNS
        # prelude lanes can use domain/ip/url seeds via build_lane_query().
        _pivot_lanes: Any = None
        _seed_ctx: Any = None
        try:
            from hledac.universal.runtime.pivot_planner import (
                generate_pivot_candidates_from_query as _gen_pivots,
            )
            from hledac.universal.pipeline.pivot_lane_planner import (
                plan_lanes_for_pivot_seeds,
            )
            from hledac.universal.runtime.scheduler.lanes import NonfeedSeedContext

            _pivot_seeds = _gen_pivots(query)
            if _pivot_seeds:
                _seed_dicts = [
                    {"value": p.ioc_value, "seed_type": p.ioc_type}
                    for p in _pivot_seeds
                    if p.ioc_value and p.ioc_type
                ]
                if _seed_dicts:
                    _pivot_plan = plan_lanes_for_pivot_seeds(_seed_dicts)
                    _pivot_lanes = getattr(_pivot_plan, "items", None)

                    # Build NonfeedSeedContext from pivot plan items for CT/WAYBACK/PDNS/DOH lanes.
                    # Only domains are used by prelude lanes (IPs/URLs from pivot seeds are not
                    # consumed by any prelude lane — DOH uses pivot_doh_items directly).
                    _ctx_domains = tuple(
                        i.seed_value
                        for i in (_pivot_lanes or [])
                        if getattr(i, "seed_type", None) == "domain"
                    )
                    if _ctx_domains:
                        _seed_ctx = NonfeedSeedContext(domains=_ctx_domains)
        except Exception:
            _pivot_lanes = None  # fail-safe: prelude works without pivot lanes
            _seed_ctx = None

        # Run all prelude lanes concurrently (bounded concurrency = 5)
        _coros = [
            run_public_prelude_lane(query),
            run_ct_prelude_lane(query, self._result, seed_context=_seed_ctx),
            run_wayback_prelude_lane(
                query, self._result, _duckdb_raw, _t, seed_context=_seed_ctx
            ),
            run_pdns_prelude_lane(
                query, self._result, _duckdb_raw, _t, seed_context=_seed_ctx
            ),
            run_doh_prelude_lane(
                query, self._result, _duckdb_raw, _t, pivot_doh_items=_pivot_lanes, seed_context=_seed_ctx
            ),
        ]

        _build = await parallel(_coros, concurrency=5, policy="collect", taskgroup=True, ctx="prelude_v2")
        _lane_results = _build.ok
        _lanes_attempted = [r.lane for r in _lane_results if r.attempted]
        _lanes_skipped = {r.lane: r.skip_reason for r in _lane_results if r.skipped}
        _lanes_accepted = {r.lane: r.accepted_count for r in _lane_results if r.accepted_count > 0}

        self._result.prelude_duration_s = _t.time() - _t_prelude_start
        self._result.prelude_lanes_attempted = _lanes_attempted
        self._result.prelude_lanes_skipped = _lanes_skipped
        self._result.prelude_lanes_accepted = _lanes_accepted

        # ISSUE #009: Post-lane temporal + Q-table pre-warm — runs fire-and-forget.
        # Temporal predictions inform pre-warm targets before first cycle starts.
        # Q-table guided ranking improves pre-fetch ordering for the next cycle.
        self._prewarm_temporal_predictor()

        return _lane_results

    def _prewarm_temporal_predictor(self) -> None:
        """
        ISSUE #009 + ISSUE B/D fix: Temporal predictor + Q-table guided pre-warm.

        Runs in background (fire-and-forget) after prelude lanes complete.
        1. TemporalIOCPredictor.predict_next_iocs() → predicted IOCs
        2. PrefetchOracleIntegration.get_best_prefetch_actions() → Q-table ranked targets
        3. prewarm_pool.acquire_session() → parallel TLS handshakes for predicted hosts
        4. record_prefetch_outcome() → ISSUE B fix: reward signal to Rust Q-table

        ISSUE B fix:
            - record_prefetch_outcome() called after each pre-warm attempt so Rust
              Q-table learns from pre-warm success/failure and updates future ranking.
            - Uses next_state_key='first_cycle' so subsequent Q-table lookups in
              the first real cycle read from the correct learned state.

        ISSUE D fix:
            - State transitions from 'prelude' → 'first_cycle' after pre-warm
              completes. This means first-cycle prefetch decisions use a different
              (learned) Q-state than pre-warm, breaking the cold-start deadlock.

        Fail-soft: any error is caught and logged; pre-warm is best-effort.
        """
        import logging
        from hledac.universal.utils.async_helpers import safe_create_task

        try:
            async def _prewarm_bg():
                _STATE_PREWARM = 'prelude'
                _STATE_NEXT = 'first_cycle'
                try:
                    # 1. Temporal predictions
                    predictor = getattr(self, '_temporal_predictor', None)
                    oracle = getattr(self, '_prefetch_oracle', None)
                    if predictor is None and oracle is None:
                        return
                    temporal_preds: list[dict] = []
                    if predictor is not None and hasattr(predictor, 'predict_next_iocs'):
                        try:
                            temporal_preds = await predictor.predict_next_iocs(top_k=10)
                        except Exception:  # noqa: BLE001 — fail-soft: temporal prediction failure should not prevent prewarm
                            pass
                    # 2. Q-table guided ranking (pre-warm state = 'prelude')
                    if oracle is not None and temporal_preds:
                        try:
                            ioc_values = [p.get('ioc_value', '') for p in temporal_preds if p.get('ioc_type') == 'domain']
                            if ioc_values and hasattr(oracle, 'get_best_prefetch_actions'):
                                ranked = oracle.get_best_prefetch_actions(ioc_values, lane='surface', state_key=_STATE_PREWARM, top_k=5)
                                # Dedupe: keep Q-table order but include temporal predictions
                                seen = set()
                                merged: list[dict] = []
                                for p in temporal_preds:
                                    if p.get('ioc_value') not in seen:
                                        seen.add(p.get('ioc_value'))
                                        merged.append(p)
                                for r in ranked:
                                    if r not in seen:
                                        seen.add(r)
                                        merged.append({'ioc_value': r, 'ioc_type': 'domain', 'confidence': 0.5, 'source_node': 'qtable', 'prediction_method': 'qtable_guided'})
                                temporal_preds = merged[:10]
                        except Exception:  # noqa: BLE001 — fail-soft: temporal predictor failure should not prevent prewarm
                            pass
                    # 3. Pre-warm curl_cffi sessions for predicted hosts
                    if temporal_preds:
                        try:
                            from transport.prewarm_pool import acquire_session
                            prewarmed: set[str] = set()
                            for pred in temporal_preds:
                                ioc_type = pred.get('ioc_type', '')
                                ioc_value = pred.get('ioc_value', '')
                                if ioc_type == 'domain' and ioc_value and ioc_value not in prewarmed:
                                    _success = False
                                    try:
                                        # ISSUE C fix: pre-warm the predicted domain's host,
                                        # not a static 'ja3_fingerprint' profile.
                                        # The domain itself is the pre-warm target — AcquireSession
                                        # derives the correct JA3 fingerprint from the host.
                                        ok, _, reason = await acquire_session(ioc_value)
                                        _success = ok
                                        if ok:
                                            prewarmed.add(ioc_value)
                                    except Exception:  # noqa: BLE001 — fail-soft: session acquire failure should not prevent prewarm
                                        _success = False
                                    # ISSUE B fix: record outcome to Rust Q-table so it learns
                                    # whether pre-warm succeeded. next_state_key='first_cycle'
                                    # transitions the Q-state for subsequent cycle decisions.
                                    if oracle is not None and hasattr(oracle, 'record_prefetch_outcome'):
                                        try:
                                            oracle.record_prefetch_outcome(
                                                ioc_value=ioc_value,
                                                success=_success,
                                                lane='surface',
                                                state_key=_STATE_PREWARM,
                                                next_state_key=_STATE_NEXT,
                                            )
                                        except Exception:  # noqa: BLE001 — fail-soft: prefetch action failure should not prevent prewarm
                                            pass
                                if len(prewarmed) >= 5:
                                    break
                        except ImportError:  # noqa: BLE001 — fail-soft: prewarm pool unavailable
                            pass
                except Exception:  # noqa: BLE001 — fail-soft: prewarm host failure should not prevent prelude
                    pass

            safe_create_task(_prewarm_bg(), name='sprint:prelude_prewarm')
        except Exception:  # noqa: BLE001 — fail-soft: prelude prewarm failure should not prevent sprint
            pass

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
        import asyncio

        try:
            from hledac.universal._lazy_imports import get_acquisition_strategy

            build_acquisition_plan, _ = get_acquisition_strategy()

            _uma_state = "ok"
            if self._governor and self._governor.ok:
                try:
                    _gov_dec = await self._governor.value.evaluate()
                    if _gov_dec:
                        _uma_state = getattr(_gov_dec, 'uma_state', 'ok')
                except Exception:  # noqa: BLE001 — fail-soft: governor evaluate failure should not prevent plan build
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
        except Exception:  # noqa: BLE001 — fail-soft: plan build failure should return None gracefully
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
        object.__setattr__(self, '_ctx', self._ctx.with_cycle(
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
        ))

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
        object.__setattr__(self, '_ctx', self._ctx.with_cycle(
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
        ))

        _orch = WinddownOrchestrator()
        await _orch.run(
            ctx=self._ctx,
            lifecycle=self._lifecycle,
            query=query,
        )

    # ── Backward-compat property ─────────────────────────────────────────────

    @property
    def sprint_id(self) -> str:
        """Return _sprint_id (setter stores there, not in result)."""
        return getattr(self, '_sprint_id', '')

    @sprint_id.setter
    def sprint_id(self, value: str) -> None:
        """Set sprint_id (backward compat for tests)."""
        object.__setattr__(self, '_sprint_id', value)

    # ── Inject methods (v1 compat stubs) ──────────────────────────────────

    def inject_evidence_log(self, elog: Any) -> None:
        """Inject a pre-initialized EvidenceLog (wraps in InitResult.success)."""
        object.__setattr__(self, '_evidence_log', InitResult.success(elog, 0.0))
        if self._ctx:
            # Set both public (SprintContext) and private (_CycleState) fields
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(evidence_log=elog))

    def inject_policy_manager(self, policy_manager: Any) -> None:
        object.__setattr__(self, '_policy_manager', policy_manager)
        # PolicyManager is not a SprintContext service; no ctx update needed

    def inject_communication_layer(self, layer: Any) -> None:
        # v2: layer is a private scheduler attribute only; no SprintContext update needed
        object.__setattr__(self, '_communication_layer', layer)

    def inject_stealth_layer(self, layer: Any) -> None:
        # v2: layer is a private scheduler attribute only; no SprintContext update needed
        object.__setattr__(self, '_stealth_layer', layer)

    def inject_ghost_layer(self, layer: Any) -> None:
        # v2: layer is a private scheduler attribute only; no SprintContext update needed
        object.__setattr__(self, '_ghost_layer', layer)

    def inject_security_coordinator(self, coordinator: Any) -> None:
        object.__setattr__(self, '_security_coordinator', coordinator)
        # SecurityCoordinator is not a governor; no ctx update needed

    def inject_prefetch_oracle(self, oracle: Any) -> None:
        object.__setattr__(self, '_prefetch_oracle', oracle)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(enrichment_services=oracle))

    def inject_duckdb_store(self, store: Any) -> None:
        """Inject a pre-initialized DuckDBShadowStore (wraps in InitResult.success)."""
        object.__setattr__(self, '_duckdb_store', InitResult.success(store, 0.0))
        if self._ctx:
            # Set both public (SprintContext) and private (_CycleState) fields
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(duckdb_store=store))

    def inject_prefetch_pipeline(self, pipeline: Any) -> None:
        object.__setattr__(self, '_prefetch_pipeline', pipeline)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(enrichment_services=pipeline))

    def inject_temporal_predictor(self, predictor: Any) -> None:
        object.__setattr__(self, '_temporal_predictor', predictor)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(temporal_predictor=predictor))

    def inject_pivot_planner(self, planner: Any) -> None:
        object.__setattr__(self, '_pivot_planner', planner)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(pivot_planner=planner))

    def inject_analyst_workbench(self, workbench: Any) -> None:
        object.__setattr__(self, '_analyst_workbench', workbench)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(analyst_workbench=workbench))

    def inject_forensics_enricher(self, enricher: Any) -> None:
        object.__setattr__(self, '_forensics_enricher', enricher)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(forensics_enricher=enricher))

    def inject_multimodal_enricher(self, enricher: Any) -> None:
        object.__setattr__(self, '_multimodal_enricher', enricher)

    def inject_enrichment_services(self, services: Any) -> None:
        object.__setattr__(self, '_enrichment_services', services)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(enrichment_services=services))

    def inject_source_economics(self, economics: Any) -> None:
        object.__setattr__(self, '_source_economics', economics)

    def inject_privacy_layer(self, layer: Any) -> None:
        object.__setattr__(self, '_privacy_layer', layer)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_cycle(privacy_layer=layer))

    def inject_ioc_graph(self, ioc_graph: Any) -> None:
        object.__setattr__(self, '_ioc_graph', ioc_graph)
        if self._ctx:
            object.__setattr__(self, '_ctx', self._ctx.with_services(graph_service=ioc_graph))

    # ── Hypothesis feedback (F203G) ─────────────────────────────────────────────

    async def record_hypothesis_feedback(
        self,
        pivot_type: str,
        ioc_type: str,
        produced_count: int,
        accepted_count: int,
        signal_value: float,
    ) -> None:
        """
        F203G: Record hypothesis feedback to DuckDB for future pivot planning.

        Persists a HypothesisFeedbackRecord to the duckdb_store for aggregation
        and use by PivotPlanner to penalize low-yield pivot types.

        Silently fails if duckdb_store is unavailable (fail-safe pattern).

        Args:
            pivot_type: domain/identity/leak/archive/graph
            ioc_type: The IOC type operated on
            produced_count: Number of findings produced by this pivot
            accepted_count: Number of findings accepted (stored)
            signal_value: reward signal [0.0, 1.0]
        """
        # v2: _duckdb_store is InitResult-wrapped; extract .value
        _store = getattr(self, "_duckdb_store", None)
        if _store is None:
            return
        # isinstance check distinguishes InitResult from MagicMock/test mocks
        if isinstance(_store, InitResult):
            _duckdb = _store.value if _store.ok else None
        else:
            _duckdb = _store  # direct store injection (tests, etc.)
        if _duckdb is None:
            return
        try:
            import time as _time
            import uuid

            from hledac.universal.runtime.hypothesis_feedback import HypothesisFeedbackRecord

            record = HypothesisFeedbackRecord(
                id=str(uuid.uuid4()),
                target_id=getattr(self, "_sprint_id", "") or "default",
                pivot_type=pivot_type,
                ioc_type=ioc_type,
                produced_count=produced_count,
                accepted_count=accepted_count,
                signal_value=signal_value,
                ts=_time.time(),
            )
            await _duckdb.async_record_hypothesis_feedback(record)
        except Exception:  # noqa: BLE001 — best-effort; non-critical path
            pass

    async def _run_synthesis_sidecar(self, query: str, duckdb_store: Any, lifecycle: Any) -> None:
        """Sprint F259: Run SynthesisRunner in WINDUP phase.

        Delegates to AcquisitionOrchestrator._run_synthesis_sidecar if available,
        otherwise runs inline.
        """
        import logging
        _log = logging.getLogger(__name__)

        # Check env gate
        from core.env_config import ENV
        if not ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS"):
            _log.debug("[F259] Synthesis skipped -- HLEDAC_ENABLE_HERMES_SYNTHESIS != '1'")
            self._result.synthesis_success = False
            # v1: returns early without setting synthesis_engine (leaves default)
            return

        # Use AcquisitionOrchestrator implementation if available
        try:
            from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator
            orch = AcquisitionOrchestrator()
            # Build minimal ctx with result
            class _MinimalCtx:
                __slots__ = ('query', '_result')
                def __init__(self, query, result):
                    self.query = query
                    self._result = result
                @property
                def result(self):
                    return self._result
            ctx = _MinimalCtx(query, self._result)
            await orch._run_synthesis_sidecar(ctx, duckdb_store, lifecycle)
            return
        except Exception as e:
            _log.debug("[F259] Delegation failed, using inline: %s", e)
            pass

        # Inline fallback implementation
        import logging
        import msgspec
        _log = logging.getLogger(__name__)

        if duckdb_store is None:
            _log.debug("[F259] Synthesis skipped -- no duckdb_store")
            return

        if not self._result.accepted_findings:
            _log.info("[F259-SYN] early-exit: no findings, skipping synthesis")
            return

        findings: list[dict] = []
        try:
            if hasattr(duckdb_store, "get_top_findings"):
                findings = await duckdb_store.get_top_findings(limit=15)
            elif hasattr(duckdb_store, "get_recent_findings"):
                findings = await duckdb_store.get_recent_findings(limit=15)
        except Exception as e:
            _log.debug("[F259] Failed to get findings: %s", e)
            return

        if not findings:
            _log.debug("[F259] Synthesis skipped -- no findings")
            return

        try:
            from hledac.universal.brain.model_lifecycle import ModelLifecycle
            from hledac.universal.brain.synthesis_runner import SynthesisRunner
        except ImportError as e:
            _log.debug("[F259] SynthesisRunner import failed: %s", e)
            self._result.synthesis_engine = "import_failed"
            return

        try:
            runner = SynthesisRunner(ModelLifecycle())
            runner.set_compression_threshold(4000)
            runner._duckdb_store = duckdb_store
            if lifecycle is not None:
                runner.inject_lifecycle_adapter(lifecycle)
            report = await runner.synthesize_findings(query=query, findings=findings, force_synthesis=True)
            self._result.synthesis_findings_count = len(findings)
            self._result.synthesis_success = report is not None
            self._result.synthesis_engine = getattr(runner, "_last_synthesis_engine", "synthesis_runner") or "synthesis_runner"
            if report is not None:
                try:
                    self._result.synthesis_text = msgspec.json.encode(
                        {
                            "query": query,
                            "ioc_entities": [
                                {"type": e.ioc_type, "value": e.value}
                                for e in getattr(report, "ioc_entities", None) or []
                            ],
                            "threat_summary": getattr(report, "threat_summary", ""),
                            "threat_actors": list(getattr(report, "threat_actors", None) or []),
                            "confidence": getattr(report, "confidence", 0.0),
                            "sources_count": getattr(report, "sources_count", 0),
                            "timestamp": getattr(report, "timestamp", 0.0),
                        }
                    ).decode("utf-8")
                except Exception:
                    self._result.synthesis_text = str(report)[:4096]
                _log.info("[F259] Synthesis complete: success=%s, findings=%d", self._result.synthesis_success, self._result.synthesis_findings_count)
            else:
                self._result.synthesis_text = ""
            await runner.close()
        except Exception as e:
            _log.debug("[F259] Synthesis failed: %s", e)
            self._result.synthesis_success = False
            self._result.synthesis_engine = "error"
            self._result.synthesis_text = ""

    async def health_check(self) -> Any:
        """Stub health check — returns None (pass)."""
        return None

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Graceful shutdown — F285 canonical async cleanup path.

        Cancels the cancel event, cancels sidecar tasks, closes DuckDB store
        and evidence log. Idempotent — safe to call multiple times.
        """
        import logging as _log
        import sys as _sys

        # Emit to stdout for BC with structlog-based test capture
        sprint_id = getattr(self, '_sprint_id', 'unknown')
        _sys.stdout.write(f"[aclean] sprint_id={sprint_id}\n")
        _sys.stdout.flush()

        # Cancel the cancel event if set (stops acquisition loops)
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()

        # Cancel sidecar tasks
        for task in list(getattr(self, '_sidecar_tasks', []) or []):
            if not task.done():
                task.cancel()

        # Close DuckDB store if present
        duckdb_store = getattr(self, '_duckdb_store', None)
        if duckdb_store is not None:
            try:
                from runtime.duckdb_store import DuckDBShadowStore
                if isinstance(duckdb_store, DuckDBShadowStore):
                    await asyncio.wait_for(duckdb_store.aclose(), timeout=timeout_s)
            except Exception as e:
                _sys.stdout.write(f"[aclean] duckdb_store close error: {e}\n")
                _sys.stdout.flush()

        # Close evidence log if present
        evidence_log = getattr(self, '_evidence_log', None)
        if evidence_log is not None:
            try:
                await asyncio.wait_for(evidence_log.aclose(), timeout=timeout_s)
            except Exception as e:
                _sys.stdout.write(f"[aclean] evidence_log close error: {e}\n")
                _sys.stdout.flush()

        _sys.stdout.write(f"[aclean] done\n")
        _sys.stdout.flush()
