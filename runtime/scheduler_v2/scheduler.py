"""STEP 4 Phase 5 — SprintSchedulerV2: thin orchestrator for runtime/sprint_scheduler.py v2.

F350M-R / Issue SC-06.

SC-06 refactor: scheduler.py slimmed to ~320 LOC.
Bootstrap  → runtime/scheduler_v2/bootstrap.py (SprintBootstrap.run())
Inject shims → runtime/scheduler_v2/injector.py (Injector.apply() called at end of run())
Synthesis    → runtime/scheduler_v2/synthesis.py (run_synthesis_sidecar())

Wiring:
    run()
      ├─ SprintBootstrap.run()   → service init
      ├─ _run_prelude_and_first_cycle()
      ├─ _run_acquisition_loop()
      └─ _run_winddown()
"""

from __future__ import annotations

import asyncio
import logging
import sys
import time as _time
from typing import Any

import msgspec

from runtime.scheduler_v2.protocol import InitResult, SprintContext
from hledac.universal.utils.async_helpers import parallel, safe_create_task


class SprintSchedulerV2(msgspec.Struct, frozen=False, gc=True):
    """SprintScheduler v2 — Protocol-based phase orchestrator.

    All phase logic delegated to: SprintBootstrap, PreludeOrchestrator,
    AcquisitionOrchestrator, WinddownOrchestrator.
    """
    DEFAULT_TIMEOUT_S = 10.0

    # ── Constructor params ─────────────────────────────────────────────────
    _config: Any = None
    _result: Any = None
    _ct_log_client: Any = None
    _flags: Any = None
    _ioc_graph: Any = None

    # ── Runtime state ───────────────────────────────────────────────────────
    _cancel_event: asyncio.Event | None = None
    _ctx: SprintContext | None = None
    _wall_clock_start: float = 0.0

    # ── Phase orchestrators ─────────────────────────────────────────────────
    _lifecycle: Any = None
    _runner: Any = None
    _hermes_engine: Any = None
    _governor: Any = None
    _evidence_log: Any = None
    _sidecar_orchestrator: Any = None
    _acquisition_plan: Any = None

    # ── Winddown extras ─────────────────────────────────────────────────────
    _synth_windup_task: Any = None
    _privacy_layer: Any = None
    _privacy_context_id: Any = None
    _prev_chain_hash: Any = None
    _sprint_id: str = "unknown"
    _rel_discovery_engine: Any = None

    # ── Injectable services ─────────────────────────────────────────────────
    _policy_manager: Any = None
    _prefetch_pipeline: Any = None
    _temporal_predictor: Any = None
    _pivot_planner: Any = None
    _analyst_workbench: Any = None
    _forensics_enricher: Any = None
    _multimodal_enricher: Any = None
    _enrichment_services: Any = None
    _source_economics: Any = None
    _communication_layer: Any = None
    _stealth_layer: Any = None
    _ghost_layer: Any = None
    _prefetch_oracle: Any = None
    _security_coordinator: Any = None
    _layer_manager: Any = None

    # ── Backward-compat property ───────────────────────────────────────────

    @property
    def sprint_id(self) -> str:
        return getattr(self, "_sprint_id", "")

    @sprint_id.setter
    def sprint_id(self, value: str) -> None:
        self._sprint_id = value

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def __post_init__(self) -> None:
        if self._result is None:
            from runtime.scheduler_result import SprintSchedulerResult
            object.__setattr__(self, "_result", SprintSchedulerResult())

    # ── Run ────────────────────────────────────────────────────────────────

    async def run(self, query: str) -> Any:
        """Run the sprint — orchestrate prelude → acquisition → winddown phases."""
        from runtime.scheduler_v2.bootstrap import SprintBootstrap
        from runtime.scheduler_v2.injector import Injector

        _wall_clock_start = _time.monotonic()
        self._wall_clock_start = _wall_clock_start
        self._cancel_event = asyncio.Event()

        object.__setattr__(
            self,
            "_ctx",
            SprintContext(
                config=self._config,
                query=query,
                result=self._result,
                ct_log_client=self._ct_log_client,
                graph_service=self._ioc_graph,
                cancel_event=self._cancel_event,
            ),
        )

        # Phase init: bootstrap services
        bootstrap = SprintBootstrap(scheduler=self)
        self._ctx = await bootstrap.run(query, _wall_clock_start, self._ctx)

        # Backward-compat: delegate prewarm to bootstrap (tests patch this method)
        self._hermes_prewarm_delegate = bootstrap._prewarm_hermes

        # Phase prelude: run prelude lanes
        await self._run_prelude_and_first_cycle(query)

        # Phase acquisition: run until terminal
        await self._run_acquisition_loop(query)

        # Phase winddown: export + synthesis
        await self._run_winddown(query)

        # Wire v1 inject API
        Injector.apply(self)

        return self._result

    # ── Prelude ────────────────────────────────────────────────────────────

    async def _run_prelude_and_first_cycle(self, query: str) -> None:
        """Run all prelude lanes + prewarm concurrently."""
        from runtime.scheduler_v2.prelude import (
            run_public_prelude_lane,
            run_ct_prelude_lane,
            run_wayback_prelude_lane,
            run_pdns_prelude_lane,
            run_doh_prelude_lane,
        )

        _t0 = _time.time()
        _duckdb_raw = self._ctx.duckdb_store if self._ctx else None

        # Pivot lane planning (fail-safe)
        _pivot_lanes: Any = None
        _seed_ctx: Any = None
        try:
            from hledac.universal.runtime.pivot_planner import (
                generate_pivot_candidates_from_query as _gen_pivots,
            )
            from hledac.universal.pipeline.pivot_lane_planner import plan_lanes_for_pivot_seeds
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
                    _ctx_domains = tuple(
                        i.seed_value
                        for i in (_pivot_lanes or [])
                        if getattr(i, "seed_type", None) == "domain"
                    )
                    if _ctx_domains:
                        _seed_ctx = NonfeedSeedContext(domains=_ctx_domains)
        except Exception:
            _pivot_lanes = None
            _seed_ctx = None

        # Run all prelude lanes concurrently
        _coros = [
            run_public_prelude_lane(query),
            run_ct_prelude_lane(query, self._result, seed_context=_seed_ctx),
            run_wayback_prelude_lane(
                query, self._result, _duckdb_raw, _time, seed_context=_seed_ctx
            ),
            run_pdns_prelude_lane(
                query, self._result, _duckdb_raw, _time, seed_context=_seed_ctx
            ),
            run_doh_prelude_lane(
                query,
                self._result,
                _duckdb_raw,
                _time,
                pivot_doh_items=_pivot_lanes,
                seed_context=_seed_ctx,
            ),
        ]

        _build = await parallel(
            _coros, concurrency=5, policy="collect", taskgroup=True, ctx="prelude_v2"
        )
        _lane_results = _build.ok
        self._result.prelude_duration_s = _time.time() - _t0
        self._result.prelude_lanes_attempted = [r.lane for r in _lane_results if r.attempted]
        self._result.prelude_lanes_skipped = {r.lane: r.skip_reason for r in _lane_results if r.skipped}
        self._result.prelude_lanes_accepted = {r.lane: r.accepted_count for r in _lane_results if r.accepted_count > 0}

        # Temporal prewarm (fire-and-forget)
        self._prewarm_temporal_predictor()

    def _prewarm_temporal_predictor(self) -> None:
        safe_create_task(self._async_prewarm_temporal())

    # Backward-compat: _prewarm_hermes lives in SprintBootstrap but test patches this class.
    # Delegates to bootstrap after run() sets _hermes_prewarm_delegate.
    async def _prewarm_hermes(self) -> None:
        _delegate = getattr(self, "_hermes_prewarm_delegate", None)
        if _delegate is not None:
            await _delegate()
        else:
            # Before run() or if bootstrap wasn't used — no-op (fail-safe)
            pass

    async def _async_prewarm_temporal(self) -> None:
        try:
            await asyncio.sleep(0.5)
        except Exception:
            pass

    # ── Acquisition ────────────────────────────────────────────────────────

    async def _run_acquisition_loop(self, query: str) -> None:
        """Run acquisition cycles until terminal."""
        from runtime.scheduler_v2.acquisition import AcquisitionOrchestrator

        _orch = AcquisitionOrchestrator()
        ordered_sources = (
            getattr(self._acquisition_plan, "ordered_sources", [])
            if self._acquisition_plan
            else []
        )
        _duckdb_raw = self._ctx.duckdb_store if self._ctx else None
        _sidecar_raw = self._sidecar_orchestrator.value if self._sidecar_orchestrator else None

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
        )

        self._result.cycles_started = _phase_result.cycles_started
        self._result.cycles_completed = _phase_result.cycles_completed
        self._result.accepted_findings = _phase_result.accepted_findings

    # ── Winddown ───────────────────────────────────────────────────────────

    async def _run_winddown(self, query: str) -> None:
        """Run winddown: export, synthesis, teardown."""
        from runtime.scheduler_v2.winddown import WinddownOrchestrator

        _duckdb_raw = self._ctx.duckdb_store if self._ctx else None
        _hermes_raw = self._hermes_engine.value if self._hermes_engine else None
        _evidence_raw = self._evidence_log.value if self._evidence_log else None
        _sidecar_raw = self._sidecar_orchestrator.value if self._sidecar_orchestrator else None

        self._ctx = self._ctx.with_cycle(
            duckdb_store=_duckdb_raw,
            sidecar_orchestrator=_sidecar_raw,
            synth_windup_task=getattr(self, "_synth_windup_task", None),
            hermes_engine=_hermes_raw,
            privacy_layer=getattr(self, "_privacy_layer", None),
            privacy_context_id=getattr(self, "_privacy_context_id", None),
            evidence_log=_evidence_raw,
            prev_chain_hash=getattr(self, "_prev_chain_hash", None),
            sprint_id=getattr(self, "_sprint_id", "unknown"),
            int_counter_layout=getattr(self._result, "_int_counter_layout", None),
            rel_discovery_engine=getattr(self, "_rel_discovery_engine", None),
        )

        _orch = WinddownOrchestrator(scheduler=self)
        await _orch.run(ctx=self._ctx, lifecycle=self._lifecycle, query=query)

    # ── Critical inject methods (needed for aclose / tests) ─────────────────

    def inject_evidence_log(self, elog: Any) -> None:
        """Inject pre-initialized EvidenceLog."""
        self._evidence_log = InitResult.success(elog, 0.0)
        if self._ctx:
            self._ctx = self._ctx.with_cycle(evidence_log=elog)

    def inject_cancel_event(self, cancel_event: asyncio.Event) -> None:
        """Wire cancel_event into EvidenceLog."""
        _elog_raw = self._evidence_log.value if self._evidence_log else None
        if _elog_raw is not None and hasattr(_elog_raw, "inject_cancel_event"):
            _elog_raw.inject_cancel_event(cancel_event)

    # ── Hypothesis feedback ──────────────────────────────────────────────────

    async def record_hypothesis_feedback(
        self,
        pivot_type: str,
        ioc_type: str,
        produced_count: int,
        accepted_count: int,
        signal_value: float,
    ) -> None:
        """F203G: Record hypothesis feedback to DuckDB."""
        _duckdb = self._ctx.duckdb_store if self._ctx else None
        if _duckdb is None:
            return
        try:
            import time as _t
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
                ts=_t.time(),
            )
            await _duckdb.async_record_hypothesis_feedback(record)
        except Exception:  # noqa: BLE001
            pass

    # ── Synthesis ───────────────────────────────────────────────────────────

    async def _run_synthesis_sidecar(
        self, query: str, duckdb_store: Any, lifecycle: Any
    ) -> None:
        """F259: Run synthesis in windup phase."""
        from runtime.scheduler_v2.synthesis import run_synthesis_sidecar

        await run_synthesis_sidecar(self, query, duckdb_store, lifecycle)

    # ── Health / shutdown ─────────────────────────────────────────────────

    async def health_check(self) -> Any:
        return None

    async def aclose(self, timeout_s: float = 10.0) -> None:
        """Graceful shutdown — F285 + P1-9 canonical async cleanup."""
        sprint_id = getattr(self, "_sprint_id", "unknown")
        sys.stdout.write(f"[aclean] sprint_id={sprint_id}\n")
        sys.stdout.flush()

        async def _do_aclose() -> None:
            if self._cancel_event is not None and not self._cancel_event.is_set():
                self._cancel_event.set()

            _duckdb_raw = self._ctx.duckdb_store if self._ctx else None
            if _duckdb_raw is not None and hasattr(_duckdb_raw, "aclose"):
                try:
                    async with asyncio.timeout(timeout_s):
                        await _duckdb_raw.aclose(timeout_s=timeout_s)
                except Exception:
                    pass

            _elog = getattr(self, "_evidence_log", None)
            if _elog is not None:
                try:
                    _elog_raw = _elog.value if isinstance(_elog, InitResult) else _elog
                    if _elog_raw is not None and hasattr(_elog_raw, "aclose"):
                        async with asyncio.timeout(timeout_s):
                            await _elog_raw.aclose(timeout_s=timeout_s)
                except Exception:
                    pass

            try:
                from hledac.universal.knowledge.graph_service import shutdown_graph
                shutdown_graph()
            except Exception:
                pass

            sys.stdout.write("[aclean] done\n")
            sys.stdout.flush()

        start = _time.monotonic()
        reason = "normal"
        try:
            await asyncio.wait_for(_do_aclose(), timeout=timeout_s)
        except asyncio.TimeoutError:
            reason = "timeout"
            sys.stdout.write(f"[aclean:{sprint_id}] force shutdown after {timeout_s}s\n")
            sys.stdout.flush()
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            reason = "force"
            raise
        finally:
            elapsed_ms = (_time.monotonic() - start) * 1000
            sys.stdout.write(
                f"[aclean:{sprint_id}] reason={reason} duration_ms={elapsed_ms:.1f}\n"
            )
            sys.stdout.flush()
