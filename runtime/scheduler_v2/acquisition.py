"""STEP 4 Phase 3 — AcquisitionOrchestrator for SprintScheduler v2.

F350M-R / Issue #P2.

Extracts acquisition phase logic from runtime/sprint_scheduler.py:
    - _run_one_cycle (~110 lines, dispatcher stable/aggressive)
    - _run_one_cycle_stable (~830 lines, sequential feed → public)
    - _run_one_cycle_aggressive (~950 lines, concurrent feed/public/CT branches)
    - The while-not-terminal acquisition loop from _run_internal

Design:
    - AcquisitionOrchestrator holds the while loop
    - Each cycle function takes ctx + typed arguments, returns CycleResult
    - All `self._result.X = Y` → `ctx.result.X = Y`
    - Lazy imports avoid M1 Metal init at import time
"""

from __future__ import annotations

import asyncio
import time as _time
from dataclasses import dataclass, field
from typing import Any, Sequence

# ── Cycle Result Types ──────────────────────────────────────────────────────────


@dataclass
class CycleResult:
    """Result from one acquisition cycle."""

    cycle_ok: bool = True
    empty_work_items: bool = False
    aggressive_mode: bool = False
    feed_results: tuple = field(default_factory=tuple)  # (ok, count)
    public_results: tuple = field(default_factory=tuple)  # (ok, count, timeout)
    ct_results: tuple = field(default_factory=tuple)  # (ok, count)
    error: str | None = None


# ── AcquisitionOrchestrator ────────────────────────────────────────────────────


class AcquisitionOrchestrator:
    """Orchestrates the main acquisition cycle loop.

    Replaces the 33 449 LOC SprintScheduler's while-not-terminal loop
    with a thin class that delegates to typed cycle functions.

    Lifecycle:
        run() → while not ctx.runner.is_terminal():
            → _check_hard_deadline()
            → _ensure_pre_windup_lane_terminal_states()
            → _drain_pending_pattern_extractions()
            → _maybe_call_pressure_relief()
            → _runner.windup_guard()
            → _run_one_cycle()  (stable or aggressive)
    """

    __slots__ = ()

    def __init__(self) -> None:
        pass

    async def run(
        self,
        ctx: Any,  # SprintContext
        ordered_sources: list[str],
        duckdb_store: Any,
        now_monotonic: float | None = None,
    ) -> AcquisitionPhaseResult:
        """Run acquisition cycles until runner signals terminal.

        Returns AcquisitionPhaseResult with final counts.
        """
        from runtime.scheduler_v2.protocol import AcquisitionPhaseResult

        cycles_started = 0
        cycles_completed = 0
        accepted_findings = 0
        empty_cycles = 0
        windup_entered = False
        exit_path: str | None = None

        _wall_clock_start = getattr(ctx, '_wall_clock_start', None) or 0.0
        _config = ctx.config
        _result = ctx.result
        _runner = ctx.runner

        # Sprint F278B-2: Ensure lazy dedup loaded before first cycle
        await self._ensure_dedup_loaded(ctx)

        try:
            while not _runner.is_terminal():
                now_monotonic = _time.monotonic()

                # ── Hard deadline check ────────────────────────────────────────
                if not self._check_hard_deadline(ctx):
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        ctx, ordered_sources, duckdb_store, "hard_deadline_exceeded"
                    )
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                    await self._finalize_result_truth(
                        ctx, "hard_deadline_exceeded",
                        f"hard deadline exceeded at cycle {cycles_started}",
                        "GATHER",
                    )
                    exit_path = "hard_deadline"
                    break

                # ── Stop requested ──────────────────────────────────────────────
                if getattr(ctx, '_stop_requested', False):
                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "stop_requested"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            ctx, ordered_sources, duckdb_store, "stop_requested_break"
                        )
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                        await self._finalize_result_truth(
                            ctx, "stop_requested_break", "stop_requested guard passed", "GATHER"
                        )
                        _runner.request_windup()
                        break
                    continue

                # ── Abort requested ────────────────────────────────────────────
                if _runner.abort_requested:
                    _result.aborted = True
                    _result.abort_reason = _runner.abort_reason or "lifecycle_abort"
                    await self._maybe_export_partial(ctx, duckdb_store, _runner)
                    await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "lifecycle_abort"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        ctx, ordered_sources, duckdb_store, "lifecycle_abort_break"
                    )
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                    await self._finalize_result_truth(
                        ctx, "lifecycle_abort_break", "abort_requested from lifecycle", "GATHER"
                    )
                    _runner.request_windup()
                    break

                # ── Periodic tick ───────────────────────────────────────────────
                _runner.tick(now_monotonic)

                # ── Nonfeed pre-dispatch ───────────────────────────────────────
                await self._maybe_dispatch_nonfeed_probe_lanes(ctx, duckdb_store)

                # ── Pre-windup barrier ─────────────────────────────────────────
                _result.windup_guard_call_count += 1
                _barrier_result = await self._ensure_pre_windup_lane_terminal_states(
                    ctx, getattr(ctx, '_acquisition_plan', None), "ok"
                )
                _barrier_satisfied = getattr(_barrier_result, "satisfied", False)
                _barrier_required = getattr(_barrier_result, "required_lanes", ())

                if _barrier_required and not _barrier_satisfied:
                    _barrier_retry_count = getattr(ctx, '_barrier_retry_count', 0) + 1
                    _barrier_max_retries = 3
                    _barrier_hard_timeout_s = 30.0
                    ctx._barrier_retry_count = _barrier_retry_count  # type: ignore

                    if _barrier_retry_count > _barrier_max_retries:
                        _barrier_satisfied = True
                    elif (now_monotonic - _wall_clock_start) > _barrier_hard_timeout_s:
                        _barrier_satisfied = True
                    elif not getattr(ctx, '_prewindup_barrier_delayed', False):
                        ctx._prewindup_barrier_delayed = True
                        _result.prewindup_barrier_delayed_cycle = True
                        continue

                # ── Drain pending pattern extractions ────────────────────────────
                _sprint_elapsed = now_monotonic - _wall_clock_start
                _remaining_s = max(0.0, _config.sprint_duration_s - _sprint_elapsed)
                await self._drain_pending_pattern_extractions(ctx, _remaining_s)

                # ── Pressure relief ────────────────────────────────────────────
                self._maybe_call_pressure_relief(ctx)

                # ── Windup guard ───────────────────────────────────────────────
                _guard_result = _runner.windup_guard(
                    now_monotonic,
                    pre_windup_barrier=lambda: self._check_prewindup_barrier_sync(
                        ctx, ordered_sources, duckdb_store
                    ),
                )

                if _guard_result:
                    windup_entered = True
                    await self._flush_dedup(ctx)

                    # Fire-and-forget IOC co-occurrence
                    safe_create_task(
                        self._run_ioc_cooccurrence_sidecar(ctx, duckdb_store),
                        name="sprint:ioc_cooccurrence",
                    )

                    # Synthesis sidecar
                    _synth_task = asyncio.create_task(
                        self._run_synthesis_sidecar(ctx, duckdb_store, _runner),
                        name="sprint:synthesis_windup",
                    )

                    # Epistemic gap advisory
                    await self._run_epistemic_gap_advisory(ctx, duckdb_store)

                    # Flush forensics
                    if ctx.enrichment_services:
                        await ctx.enrichment_services.flush()

                    ctx.evaluate_advisory_gate()

                    await self._maybe_export_partial(ctx, duckdb_store, _runner)

                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "windup_barrier"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            ctx, ordered_sources, duckdb_store, "windup_barrier_passed"
                        )
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                        await self._finalize_result_truth(
                            ctx, "windup_barrier_passed",
                            "pre-windup barrier satisfied, entered windup", "WINDUP"
                        )
                        break

                    await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "windup_barrier_forced"
                    )
                    await self._ensure_nonfeed_predispatch_before_finalization(
                        ctx, ordered_sources, duckdb_store, "windup_barrier_break"
                    )
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                    await self._finalize_result_truth(
                        ctx, "windup_barrier_break",
                        "pre-windup barrier unsatisfied, forced terminalization", "WINDUP"
                    )
                    break

                # ── Re-prioritize sources in ACTIVE phase ──────────────────────
                if _runner.current_phase == "ACTIVE":
                    ordered_sources = self._prioritize_sources(ctx, ordered_sources)

                # ── Adaptive max_cycles ─────────────────────────────────────────
                effective_max_cycles = self._get_effective_max_cycles(ctx)

                if cycles_started >= effective_max_cycles:
                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "max_cycles_reached"
                    ):
                        await self._ensure_nonfeed_predispatch_before_finalization(
                            ctx, ordered_sources, duckdb_store, "max_cycles_reached"
                        )
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                        await self._finalize_result_truth(
                            ctx, "max_cycles_reached",
                            f"max_cycles {effective_max_cycles} reached", "ACTIVE"
                        )
                        _runner.request_windup()
                        break
                    continue

                # ── Run one cycle ─────────────────────────────────────────────
                cycle_result = await self._run_one_cycle(
                    ctx, ordered_sources, now_monotonic, duckdb_store
                )

                if cycle_result.empty_work_items:
                    empty_cycles += 1
                else:
                    empty_cycles = 0
                    cycles_completed += 1
                    accepted_findings = _result.accepted_findings

                cycles_started += 1

                # ── Post-cycle hook: MLX batch sizing ─────────────────────────
                if ctx.hermes_engine is not None:
                    ctx.hermes_engine._active_iteration_count = cycles_started

                # ── Check zero-findings alert ─────────────────────────────────
                await self._check_zero_findings_alert(ctx)

            # Normal loop exit
            if exit_path is None and _runner.is_terminal():
                exit_path = "terminal"

        except asyncio.CancelledError:
            exit_path = "cancelled"
            raise

        return AcquisitionPhaseResult(
            cycles_started=cycles_started,
            cycles_completed=cycles_completed,
            accepted_findings=accepted_findings,
            empty_cycles=empty_cycles,
            windup_entered=windup_entered,
            exit_path=exit_path,
        )

    # ── _run_one_cycle dispatcher ─────────────────────────────────────────────

    async def _run_one_cycle(
        self,
        ctx: Any,
        sources: Sequence[str],
        now_monotonic: float | None,
        duckdb_store: Any,
    ) -> CycleResult:
        """Run one bounded fetch cycle (stable or aggressive).

        Returns CycleResult with branch outcomes.
        """
        await self._ensure_dedup_loaded(ctx)

        # Build tiered work list
        work_items = self._build_work_items(ctx, sources)
        if not work_items:
            ctx.result.consecutive_empty_cycles += 1
            if ctx.result.consecutive_empty_cycles > ctx.result.max_consecutive_empty_cycles:
                ctx.result.max_consecutive_empty_cycles = ctx.result.consecutive_empty_cycles
            return CycleResult(cycle_ok=True, empty_work_items=True)

        # Reset counter when real work is available
        ctx.result.consecutive_empty_cycles = 0
        ctx.result.cycles_started += 1

        lifecycle = getattr(ctx, '_lifecycle', None)
        query = ctx.query

        if ctx.config.aggressive_mode:
            return await self._run_one_cycle_aggressive(
                ctx, lifecycle, work_items, query, duckdb_store
            )
        else:
            return await self._run_one_cycle_stable(
                ctx, lifecycle, work_items, query, duckdb_store
            )

    # ── Stable cycle ──────────────────────────────────────────────────────────

    async def _run_one_cycle_stable(
        self,
        ctx: Any,
        lifecycle: Any,
        work_items: list,
        query: str,
        duckdb_store: Any,
    ) -> CycleResult:
        """Stable mode: feed sources first, then public discovery.

        Sequential execution — feed completes before public starts.
        """
        _config = ctx.config
        _result = ctx.result
        _wall_clock_start = getattr(ctx, '_wall_clock_start', 0.0)

        # Bootstrap _seed_ctx
        _seed_ctx = await self._build_seed_context(ctx, query)

        # Run sources under TaskGroup (bounded concurrency)
        semaphore = asyncio.Semaphore(_config.max_parallel_sources)

        async def fetch_one(work: Any) -> tuple[str, Any]:
            async with semaphore:
                should_fetch, _ = self._feed_dominance_should_fetch(ctx, work, False)
                if not should_fetch:
                    from hledac.universal.pipeline.live_feed_pipeline import FeedPipelineRunResult
                    return work.url, FeedPipelineRunResult.empty()
                return work.url, await self._async_run_live_feed(
                    ctx, work, duckdb_store
                )

        feed_tasks = [fetch_one(w) for w in work_items]
        feed_results = await asyncio.gather(*feed_tasks, return_exceptions=True)

        _feed_ok = all(r[1].ok for r in feed_results if not isinstance(r, Exception))
        _feed_count = sum(
            r[1].accepted_findings if not isinstance(r, Exception) and hasattr(r[1], 'accepted_findings') else 0
            for r in feed_results
        )

        # Public discovery under remaining-time-aware timeout
        remaining_s = lifecycle.remaining_time() if lifecycle else 999.0
        _safety_floor = _config.effective_windup_lead_s

        _public_ok = False
        _public_count = 0
        _public_timeout = False

        if remaining_s > _safety_floor:
            try:
                async with asyncio.timeout(max(remaining_s - _safety_floor, 1.0)):
                    _public_result = await self._run_public_branch(
                        ctx, query, duckdb_store, _seed_ctx
                    )
                    _public_ok = _public_result.get('ok', False)
                    _public_count = _public_result.get('count', 0)
            except TimeoutError:
                _public_timeout = True
                _result.branch_timeout_count += 1
        else:
            _result.public_ghosts_skipped += 1

        return CycleResult(
            cycle_ok=True,
            aggressive_mode=False,
            feed_results=(_feed_ok, _feed_count),
            public_results=(_public_ok, _public_count, _public_timeout),
        )

    # ── Aggressive cycle ──────────────────────────────────────────────────────

    async def _run_one_cycle_aggressive(
        self,
        ctx: Any,
        lifecycle: Any,
        work_items: list,
        query: str,
        duckdb_store: Any,
    ) -> CycleResult:
        """Aggressive mode: feed, public, and CT branches run concurrently.

        Per-branch timeouts based on remaining time.
        """
        _config = ctx.config
        _result = ctx.result

        _seed_ctx = await self._build_seed_context(ctx, query)

        semaphore = asyncio.Semaphore(_config.max_parallel_sources)

        async def fetch_one(work: Any) -> tuple[str, Any]:
            async with semaphore:
                should_fetch, _ = self._feed_dominance_should_fetch(ctx, work, False)
                if not should_fetch:
                    from hledac.universal.pipeline.live_feed_pipeline import FeedPipelineRunResult
                    return work.url, FeedPipelineRunResult.empty()
                return work.url, await self._async_run_live_feed(ctx, work, duckdb_store)

        remaining_s = lifecycle.remaining_time() if lifecycle else 999.0
        _safety_floor = _config.effective_windup_lead_s
        _branch_timeout = max((remaining_s - _safety_floor) / 3.0, 5.0)

        feed_task = asyncio.create_task(self._run_feed_branch_aggressive(
            ctx, work_items, fetch_one, semaphore, duckdb_store
        ))
        public_task = asyncio.create_task(self._run_public_branch_aggressive(
            ctx, query, duckdb_store, _seed_ctx, _branch_timeout
        ))
        ct_task = asyncio.create_task(self._run_ct_branch_aggressive(
            ctx, query, duckdb_store, _seed_ctx, _branch_timeout
        ))

        _feed_results = await asyncio.gather(feed_task, public_task, ct_task, return_exceptions=True)

        _feed_ok = _feed_results[0][0] if not isinstance(_feed_results[0], Exception) else False
        _feed_count = _feed_results[0][1] if not isinstance(_feed_results[0], Exception) else 0
        _public_ok = _feed_results[1][0] if not isinstance(_feed_results[1], Exception) else False
        _public_count = _feed_results[1][1] if not isinstance(_feed_results[1], Exception) else 0
        _public_timeout = _feed_results[1][2] if not isinstance(_feed_results[1], Exception) else False
        _ct_ok = _feed_results[2][0] if not isinstance(_feed_results[2], Exception) else False
        _ct_count = _feed_results[2][1] if not isinstance(_feed_results[2], Exception) else 0

        if _public_timeout or _feed_results[1] and isinstance(_feed_results[1], Exception):
            _result.branch_timeout_count += 1

        return CycleResult(
            cycle_ok=True,
            aggressive_mode=True,
            feed_results=(_feed_ok, _feed_count),
            public_results=(_public_ok, _public_count, _public_timeout),
            ct_results=(_ct_ok, _ct_count),
        )

    # ── Aggressive branch helpers ─────────────────────────────────────────────

    async def _run_feed_branch_aggressive(
        self,
        ctx: Any,
        work_items: list,
        fetch_one: Any,
        semaphore: asyncio.Semaphore,
        duckdb_store: Any,
    ) -> tuple[bool, int]:
        try:
            feed_results = await asyncio.gather(
                *[fetch_one(w) for w in work_items],
                return_exceptions=True
            )
            _ok = all(r[1].ok for r in feed_results if not isinstance(r, Exception))
            _count = sum(
                r[1].accepted_findings if not isinstance(r, Exception) and hasattr(r[1], 'accepted_findings') else 0
                for r in feed_results
            )
            return (_ok, _count)
        except Exception:
            return (False, 0)

    async def _run_public_branch_aggressive(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
        timeout_s: float,
    ) -> tuple[bool, int, bool]:
        try:
            async with asyncio.timeout(timeout_s):
                _result = await self._run_public_branch(ctx, query, duckdb_store, seed_ctx)
                return (_result.get('ok', False), _result.get('count', 0), False)
        except TimeoutError:
            ctx.result.branch_timeout_count += 1
            return (False, 0, True)
        except Exception:
            return (False, 0, False)

    async def _run_ct_branch_aggressive(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
        timeout_s: float,
    ) -> tuple[bool, int]:
        try:
            async with asyncio.timeout(timeout_s):
                _result = await self._run_ct_branch(ctx, query, duckdb_store, seed_ctx)
                return (_result.get('ok', False), _result.get('count', 0))
        except TimeoutError:
            return (False, 0)
        except Exception:
            return (False, 0)

    # ── Stub methods (to be extracted from v1) ────────────────────────────────
    # These are no-op stubs that will be replaced as each piece is extracted.

    async def _ensure_dedup_loaded(self, ctx: Any) -> None:
        """Ensure lazy dedup is loaded before first cycle."""
        _ds = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
        if _ds and hasattr(_ds, '_dedup_loader'):
            try:
                await _ds._dedup_loader.ensure_loaded()
            except Exception:
                pass

    def _check_hard_deadline(self, ctx: Any) -> bool:
        """Returns False if hard deadline exceeded."""
        _config = ctx.config
        _wall_clock_start = getattr(ctx, '_wall_clock_start', 0.0)
        if _wall_clock_start <= 0:
            return True
        _elapsed = _time.monotonic() - _wall_clock_start
        return _elapsed < _config.sprint_duration_s

    def _get_effective_max_cycles(self, ctx: Any) -> int:
        """Adaptive max_cycles based on cycle_time EMA."""
        if not hasattr(ctx, '_cycle_time_ema'):
            ctx._cycle_time_ema = 1.0
            ctx._last_cycle_start = None
            ctx._effective_max_cycles = ctx.config.max_cycles
        if ctx._last_cycle_start is not None:
            _elapsed = max(0.1, min(10.0, _time.monotonic() - ctx._last_cycle_start))
            ctx._cycle_time_ema = 0.7 * ctx._cycle_time_ema + 0.3 * _elapsed
            _active = max(0.0, ctx.config.sprint_duration_s - ctx.config.final_windup_lead_s)
            if _active > 0 and ctx._cycle_time_ema > 0:
                ctx._effective_max_cycles = max(50, min(300, int(_active / ctx._cycle_time_ema)))
        ctx._last_cycle_start = _time.monotonic()
        return ctx._effective_max_cycles

    async def _build_seed_context(self, ctx: Any, query: str) -> Any:
        """Build seed context from query and acquisition plan."""
        from dataclasses import dataclass

        @dataclass
        class _SeedCtx:
            domains: tuple = ()
            ips: tuple = ()
            urls: tuple = ()
            seeds: tuple = ()

        _seed = _SeedCtx()
        if ctx.acquisition_plan:
            _seed.domains = tuple(getattr(ctx.acquisition_plan, 'domain_seeds', ()) or ())
            _seed.ips = tuple(getattr(ctx.acquisition_plan, 'ip_seeds', ()) or ())
            _seed.urls = tuple(getattr(ctx.acquisition_plan, 'url_seeds', ()) or ())
        return _seed

    async def _ensure_pre_windup_lane_terminal_states(
        self,
        ctx: Any,
        acquisition_plan: Any,
        default_reason: str,
    ) -> Any:
        """Check that required nonfeed lanes are terminal before windup."""
        from dataclasses import dataclass

        @dataclass
        class BarrierResult:
            satisfied: bool = True
            required_lanes: tuple = ()

        return BarrierResult(satisfied=True, required_lanes=())

    async def _ensure_nonfeed_predispatch_before_finalization(
        self,
        ctx: Any,
        ordered_sources: list,
        duckdb_store: Any,
        reason: str,
    ) -> None:
        """Run nonfeed pre-dispatch before finalization."""
        pass

    async def _ensure_mandatory_nonfeed_before_return(
        self,
        ctx: Any,
        ordered_sources: list,
        duckdb_store: Any,
        reason: str,
    ) -> bool:
        """Ensure mandatory nonfeed lanes are terminal. Returns True if satisfied."""
        return True

    async def _finalize_result_truth(
        self,
        ctx: Any,
        exit_path: str,
        reason: str,
        phase: str,
    ) -> None:
        """Finalize result truth with exit path and telemetry."""
        ctx.result.scheduler_exit_path = exit_path
        ctx.result.scheduler_exit_reason = reason
        ctx.result.scheduler_exit_phase = phase

    def _check_prewindup_barrier_sync(
        self,
        ctx: Any,
        ordered_sources: list,
        duckdb_store: Any,
    ) -> bool:
        """Synchronous pre-windup barrier check."""
        return True

    async def _drain_pending_pattern_extractions(
        self,
        ctx: Any,
        remaining_s: float,
    ) -> None:
        """Drain in-flight pattern extractions at windup entry."""
        pass

    def _maybe_call_pressure_relief(self, ctx: Any) -> None:
        """Call malloc_zone_pressure_relief if governor recommends."""
        pass

    async def _flush_dedup(self, ctx: Any) -> None:
        """Flush dedup at WINDUP entry."""
        _ds = getattr(ctx, '_duckdb_store', None) or getattr(ctx, 'duckdb_store', None)
        if _ds and hasattr(_ds, 'flush'):
            try:
                await _ds.flush()
            except Exception:
                pass

    async def _maybe_export_partial(
        self,
        ctx: Any,
        duckdb_store: Any,
        lifecycle: Any,
    ) -> None:
        """Export partial results on early windup."""
        pass

    async def _run_ioc_cooccurrence_sidecar(
        self,
        ctx: Any,
        duckdb_store: Any,
    ) -> None:
        """Run IOC co-occurrence analysis sidecar."""
        pass

    async def _run_synthesis_sidecar(
        self,
        ctx: Any,
        duckdb_store: Any,
        lifecycle: Any,
    ) -> None:
        """Run Hermes synthesis sidecar in windup."""
        pass

    async def _run_epistemic_gap_advisory(
        self,
        ctx: Any,
        duckdb_store: Any,
    ) -> None:
        """Run epistemic gap advisory."""
        pass

    def _prioritize_sources(self, ctx: Any, ordered_sources: list) -> list:
        """Re-prioritize sources using latest graph stats."""
        return ordered_sources

    def _build_work_items(self, ctx: Any, sources: Sequence[str]) -> list:
        """Build tiered work items from ordered sources."""
        return []

    async def _run_public_branch(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
    ) -> dict[str, Any]:
        """Run public discovery branch. Returns {'ok': bool, 'count': int}."""
        return {'ok': False, 'count': 0}

    async def _run_ct_branch(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
    ) -> dict[str, Any]:
        """Run CT discovery branch. Returns {'ok': bool, 'count': int}."""
        return {'ok': False, 'count': 0}

    async def _async_run_live_feed(
        self,
        ctx: Any,
        work: Any,
        duckdb_store: Any,
    ) -> Any:
        """Run one feed source through live_feed_pipeline."""
        from hledac.universal.pipeline.live_feed_pipeline import async_run_live_feed
        try:
            return await async_run_live_feed(
                work.url,
                query=ctx.query,
                store=duckdb_store,
                fetch_timeout_s=work.timeout_s or 30.0,
                max_results=getattr(work, 'max_results', 10),
            )
        except Exception:
            from hledac.universal.pipeline.live_feed_pipeline import FeedPipelineRunResult
            return FeedPipelineRunResult.empty()

    def _feed_dominance_should_fetch(
        self,
        ctx: Any,
        work: Any,
        nonfeed_terminal: bool,
    ) -> tuple[bool, str]:
        """Check feed dominance budget before fetching."""
        return (True, "ok")

    async def _maybe_dispatch_nonfeed_probe_lanes(
        self,
        ctx: Any,
        duckdb_store: Any,
    ) -> None:
        """Dispatch nonfeed probe lanes (WAYBACK, PDNS, DOH, IPFS, BGP)."""
        pass

    async def _check_zero_findings_alert(self, ctx: Any) -> None:
        """Check zero-findings alert after each cycle."""
        try:
            from hledac.universal.utils.alerts import check_zero_findings_alert
            _elapsed = _time.monotonic() - getattr(ctx, '_wall_clock_start', 0.0)
            await check_zero_findings_alert(
                elapsed_s=_elapsed,
                consecutive_empty_cycles=ctx.result.consecutive_empty_cycles,
                total_findings=ctx.result.accepted_findings,
            )
        except Exception:
            pass


# ── Lazy imports helper ────────────────────────────────────────────────────────


def safe_create_task(coro: Any, name: str | None = None) -> asyncio.Task:
    """safe_create_task wrapper — avoids importing utils.async_helpers at module load."""
    from hledac.universal.utils.async_helpers import safe_create_task as _sct
    return _sct(coro, name=name)


# ── Protocol re-export ────────────────────────────────────────────────────────

from runtime.scheduler_v2.protocol import AcquisitionPhaseResult

__all__ = [
    "AcquisitionOrchestrator",
    "AcquisitionPhaseResult",
    "CycleResult",
    "safe_create_task",
]
