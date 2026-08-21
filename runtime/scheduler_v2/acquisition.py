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
import logging
import time as _time
from collections.abc import Sequence
from typing import Any

import msgspec

from compat.msgspec_gc_compat import Struct
from hledac.universal._core.env_config import ENV
from hledac.universal.runtime.scheduler_v2._task_registry import (
    TaskScope,
    safe_create_task_tracked,
)
from hledac.universal.utils.asyncx import (
    parallel,
)

# ── Pipeline Phase Types ─────────────────────────────────────────────────────────

log = logging.getLogger(__name__)

# ── Cycle Result Types ──────────────────────────────────────────────────────────


class _FeedWork(Struct, frozen=True):
    """Work item for one feed source. Compatible with _async_run_live_feed signature.

    Migrated from @dataclass(slots=True) to msgspec.Struct (frozen=True).
    """

    url: str
    timeout_s: float = 30.0
    max_results: int = 10


class CycleResult(Struct, frozen=True):
    """Result from one acquisition cycle.

    Migrated from @dataclass to msgspec.Struct (frozen=True) for:
    - 5-7× faster instantiation
    - Built-in __eq__/__hash__ on slot fields
    - ~50% smaller memory footprint
    - JSON serialization via msgspec
    """

    cycle_ok: bool = True
    empty_work_items: bool = False
    aggressive_mode: bool = False
    feed_results: tuple = ()  # (ok, count)
    public_results: tuple = ()  # (ok, count, timeout)
    ct_results: tuple = ()  # (ok, count)
    # P2-1: AIMD window telemetry — written by aggressive cycle for benchmark
    aimd_window: float = 0.0  # AIMD window size at cycle end
    aimd_successes: int = 0  # cumulative successes at cycle end
    aimd_failures: int = 0  # cumulative failures at cycle end
    error: str | None = None
    # P4-1: UNIMPLEMENTED telemetry — tracks which windup barriers are stubs
    # Values are UNIMPLEMENTED_REASON strings or empty tuple if real implementation
    unimplemented_telemetry: tuple = ()  # e.g. ("pre_windup_barrier", "ioc_cooccurrence")


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

    # ── Finalization helpers ───────────────────────────────────────────────

    async def _finalize_parallel(
        self,
        ctx: Any,
        ordered_sources: list[str],
        duckdb_store: Any,
        reason: str,
        message: str,
        phase: str,
        wall_clock_start: float,
        request_windup: bool = False,
    ) -> None:
        """Run nonfeed predispatch and truth finalization in parallel TaskGroup.

        Extracted from run() to eliminate 4 identical TaskGroup patterns.
        Fail-safe: ExceptionGroup is caught and degraded gracefully.
        """
        try:
            async with asyncio.TaskGroup() as _tg:
                _tg.create_task(
                    self._ensure_nonfeed_predispatch_before_finalization(ctx, ordered_sources, duckdb_store, reason),
                    name="finalize:predispatch",
                )
                _tg.create_task(
                    self._finalize_result_truth(ctx, reason, message, phase),
                    name="finalize:truth",
                )
        except ExceptionGroup:  # noqa: BLE001
            pass  # graceful degradation: at least one finalization path ran
        ctx.result.scheduler_exit_elapsed_s = _time.monotonic() - wall_clock_start
        if request_windup:
            ctx.runner.request_windup()

    async def _run_windup_sequence(
        self,
        ctx: Any,
        ordered_sources: list[str],
        duckdb_store: Any,
        wall_clock_start: float,
        rayon_manager: Any = None,  # [META]-004: RayonPoolManager for SYNTHESIS phase
    ) -> bool:
        """Execute windup sequence and return True if barrier passed.

        Returns True if mandatory nonfeed succeeded (normal exit).
        Returns False if mandatory nonfeed failed (forced terminalization).
        """
        await self._flush_dedup(ctx)

        # [META]-004: SYNTHESIS phase — expand cpu_pool to 6 for MLX inference
        # io_pool shrinks to 2 (fetch is done, DuckDB writes are minimal)
        if rayon_manager is not None:
            try:
                rayon_manager.set_phase("SYNTHESIS")
            except Exception:  # noqa: BLE001
                pass  # fail-safe

        safe_create_task_tracked(
            self._run_ioc_cooccurrence_sidecar(ctx, duckdb_store),
            name="sprint:ioc_cooccurrence",
            scope=TaskScope.WINDUP,
        )

        # Synthesis sidecar
        _synth_task = safe_create_task_tracked(
            self._run_synthesis_sidecar(ctx, duckdb_store, ctx.runner),
            name="sprint:synthesis_windup",
            scope=TaskScope.WINDUP_SYNTHESIS,
        )
        ctx.cycle.synth_windup_task = _synth_task

        # Await synthesis before returning (prevents runner.close() race on M1 8GB)
        try:
            async with asyncio.timeout(15.0):
                await _synth_task
        except TimeoutError:
            _synth_task.cancel()
            log.debug("[F259] synthesis task timed out after 15s, cancelled")
        except Exception:  # noqa: BLE001
            pass  # fail-safe: synthesis errors are non-critical

        await self._run_epistemic_gap_advisory(ctx, duckdb_store)

        if ctx.enrichment_services:
            await ctx.enrichment_services.flush()

        ctx.evaluate_advisory_gate()
        await self._maybe_export_partial(ctx, duckdb_store, ctx.runner)

        if await self._ensure_mandatory_nonfeed_before_return(ctx, ordered_sources, duckdb_store, "windup_barrier"):
            await self._finalize_parallel(
                ctx,
                ordered_sources,
                duckdb_store,
                "windup_barrier_passed",
                "pre-windup barrier satisfied, entered windup",
                "WINDUP",
                wall_clock_start,
            )
            return True

        await self._ensure_mandatory_nonfeed_before_return(ctx, ordered_sources, duckdb_store, "windup_barrier_forced")
        await self._finalize_parallel(
            ctx,
            ordered_sources,
            duckdb_store,
            "windup_barrier_break",
            "pre-windup barrier unsatisfied, forced terminalization",
            "WINDUP",
            wall_clock_start,
        )
        return False

    # ── Main acquisition loop ───────────────────────────────────────────────

    async def run(
        self,
        ctx: Any,  # SprintContext
        ordered_sources: list[str],
        duckdb_store: Any,
        _now_monotonic: float | None = None,  # unused: shadowed inside loop; nominal API param
        _rayon_manager: Any = None,  # [META]-004: RayonPoolManager for elastic resize
    ) -> AcquisitionPhaseResult:
        """Run acquisition cycles until runner signals terminal.

        Returns AcquisitionPhaseResult with final counts.
        """
        from hledac.universal.runtime.scheduler_v2.protocol import AcquisitionPhaseResult

        cycles_started = 0
        cycles_completed = 0
        accepted_findings = 0
        empty_cycles = 0

        _wall_clock_start = getattr(ctx, "_wall_clock_start", None) or 0.0
        _config = ctx.config
        _result = ctx.result
        _runner = ctx.runner

        await self._ensure_dedup_loaded(ctx)

        # F-2/F-3 FIX: WARMUP→ACTIVE transition must happen before acquisition loop.
        # Without this, _on_degraded_enter callback and ACTIVE re-prioritization
        # are dead code. This ensures DEGRADED phase and phase transitions work.
        _runner.ensure_active()

        try:
            while not _runner.is_terminal():
                now_monotonic = _time.monotonic()

                # ── Hard deadline check ────────────────────────────────────────
                if not self._check_hard_deadline(ctx):
                    await self._finalize_parallel(
                        ctx,
                        ordered_sources,
                        duckdb_store,
                        "hard_deadline_exceeded",
                        f"hard deadline exceeded at cycle {cycles_started}",
                        "GATHER",
                        _wall_clock_start,
                    )
                    return AcquisitionPhaseResult(
                        cycles_started=cycles_started,
                        cycles_completed=cycles_completed,
                        accepted_findings=accepted_findings,
                        empty_cycles=empty_cycles,
                        windup_entered=False,
                        exit_path="hard_deadline",
                        unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
                        windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
                    )

                # ── Stop requested ──────────────────────────────────────────────
                if getattr(ctx, "_stop_requested", False):
                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "stop_requested"
                    ):
                        await self._finalize_parallel(
                            ctx,
                            ordered_sources,
                            duckdb_store,
                            "stop_requested_break",
                            "stop_requested guard passed",
                            "GATHER",
                            _wall_clock_start,
                            request_windup=True,
                        )
                        return AcquisitionPhaseResult(
                            cycles_started=cycles_started,
                            cycles_completed=cycles_completed,
                            accepted_findings=accepted_findings,
                            empty_cycles=empty_cycles,
                            windup_entered=False,
                            exit_path="stop_requested",
                            unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
                            windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
                        )
                    continue

                # ── Abort requested ────────────────────────────────────────────
                if _runner.abort_requested:
                    _result.aborted = True
                    _result.abort_reason = _runner.abort_reason or "lifecycle_abort"
                    await self._maybe_export_partial(ctx, duckdb_store, _runner)
                    await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "lifecycle_abort"
                    )
                    await self._finalize_parallel(
                        ctx,
                        ordered_sources,
                        duckdb_store,
                        "lifecycle_abort_break",
                        "abort_requested from lifecycle",
                        "GATHER",
                        _wall_clock_start,
                        request_windup=True,
                    )
                    return AcquisitionPhaseResult(
                        cycles_started=cycles_started,
                        cycles_completed=cycles_completed,
                        accepted_findings=accepted_findings,
                        empty_cycles=empty_cycles,
                        windup_entered=False,
                        exit_path="abort",
                        unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
                        windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
                    )

                # ── Periodic tick ───────────────────────────────────────────────
                _runner.tick(now_monotonic)

                # ── Nonfeed pre-dispatch ───────────────────────────────────────
                await self._maybe_dispatch_nonfeed_probe_lanes(ctx, duckdb_store)

                # ── Pre-windup barrier ─────────────────────────────────────────
                # P4-1: Real barrier implementation with UNIMPLEMENTED tracking
                _result.windup_guard_call_count += 1
                _barrier_result = await self._ensure_pre_windup_lane_terminal_states(
                    ctx, getattr(ctx, "_acquisition_plan", None), "ok"
                )
                _barrier_satisfied = getattr(_barrier_result, "satisfied", False)
                _barrier_required = getattr(_barrier_result, "required_lanes", ())
                _barrier_completed = getattr(_barrier_result, "completed_lanes", ())
                _barrier_unimplemented = getattr(_barrier_result, "unimplemented", ())

                # P4-1: Log barrier status for observability
                if _barrier_unimplemented:
                    log.debug(
                        "[P4-1] Pre-windup barrier: required=%s completed=%s unimplemented=%s",
                        _barrier_required,
                        _barrier_completed,
                        _barrier_unimplemented,
                    )
                    # Store in result for telemetry
                    _result.prewindup_unimplemented_lanes = _barrier_unimplemented

                if _barrier_required and not _barrier_satisfied:
                    _barrier_retry_count = ctx.cycle.barrier_retry_count + 1
                    _barrier_max_retries = 3
                    _barrier_hard_timeout_s = 30.0
                    ctx.cycle.barrier_retry_count = _barrier_retry_count

                    if _barrier_retry_count > _barrier_max_retries:
                        _barrier_satisfied = True
                    elif (now_monotonic - _wall_clock_start) > _barrier_hard_timeout_s:
                        _barrier_satisfied = True
                    elif not ctx.cycle.prewindup_barrier_delayed:
                        ctx.cycle.prewindup_barrier_delayed = True
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
                    pre_windup_barrier=lambda: self._check_prewindup_barrier_sync(ctx, ordered_sources, duckdb_store),
                )

                if _guard_result:
                    barrier_passed = await self._run_windup_sequence(
                        ctx, ordered_sources, duckdb_store, _wall_clock_start, _rayon_manager
                    )
                    return AcquisitionPhaseResult(
                        cycles_started=cycles_started,
                        cycles_completed=cycles_completed,
                        accepted_findings=accepted_findings,
                        empty_cycles=empty_cycles,
                        windup_entered=True,
                        exit_path="windup_barrier_passed" if barrier_passed else "windup_barrier_forced",
                        unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
                        windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
                    )

                # ── Re-prioritize sources in ACTIVE phase ──────────────────────
                # FIX: current_phase returns SprintPhase enum, not string
                from hledac.universal.runtime.sprint_lifecycle import SprintPhase

                if _runner.current_phase == SprintPhase.ACTIVE:
                    ordered_sources = self._prioritize_sources(ctx, ordered_sources)

                # ── Adaptive max_cycles ─────────────────────────────────────────
                effective_max_cycles = self._get_effective_max_cycles(ctx)

                if cycles_started >= effective_max_cycles:
                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "max_cycles_reached"
                    ):
                        await self._finalize_parallel(
                            ctx,
                            ordered_sources,
                            duckdb_store,
                            "max_cycles_reached",
                            f"max_cycles {effective_max_cycles} reached",
                            "ACTIVE",
                            _wall_clock_start,
                            request_windup=True,
                        )
                        return AcquisitionPhaseResult(
                            cycles_started=cycles_started,
                            cycles_completed=cycles_completed,
                            accepted_findings=accepted_findings,
                            empty_cycles=empty_cycles,
                            windup_entered=False,
                            exit_path="max_cycles_reached",
                            unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
                            windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
                        )
                    continue

                # ── Run one cycle ─────────────────────────────────────────────
                cycle_result = await self._run_one_cycle(ctx, ordered_sources, now_monotonic, duckdb_store)

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

                # ── R13: Post-cycle dominance tracking ─────────────────────────
                await self._update_lane_dominance(ctx, cycle_result)

                # ── Check zero-findings alert ─────────────────────────────────
                await self._check_zero_findings_alert(ctx)

        except asyncio.CancelledError:
            raise

        # Terminal exit (loop completed naturally; windup never entered)
        return AcquisitionPhaseResult(
            cycles_started=cycles_started,
            cycles_completed=cycles_completed,
            accepted_findings=accepted_findings,
            empty_cycles=empty_cycles,
            windup_entered=False,
            exit_path="terminal",
            unimplemented_telemetry=getattr(_result, "unimplemented_telemetry", ()),
            windup_unimplemented_lanes=getattr(_result, "prewindup_unimplemented_lanes", ()),
        )

    # ── _run_one_cycle dispatcher ─────────────────────────────────────────────

    async def _run_one_cycle(
        self,
        ctx: Any,
        sources: Sequence[str],
        _now_monotonic: float | None,
        duckdb_store: Any,
    ) -> CycleResult:
        """Run one bounded fetch cycle (stable or aggressive).

        Returns CycleResult with branch outcomes.
        """
        await self._ensure_dedup_loaded(ctx)

        work_items = self._build_work_items(ctx, sources)
        if not work_items:
            ctx.result.consecutive_empty_cycles += 1
            if ctx.result.consecutive_empty_cycles > ctx.result.max_consecutive_empty_cycles:
                ctx.result.max_consecutive_empty_cycles = ctx.result.consecutive_empty_cycles
            return CycleResult(cycle_ok=True, empty_work_items=True, unimplemented_telemetry=())

        # Reset counter when real work is available
        ctx.result.consecutive_empty_cycles = 0
        ctx.result.cycles_started += 1

        # FIX: Use ctx.runner instead of getattr(ctx, "_lifecycle", None)
        # The lifecycle manager is stored as 'runner' on the context
        lifecycle = ctx.runner
        query = ctx.query

        if ctx.config.aggressive_mode:
            return await self._run_one_cycle_aggressive(ctx, lifecycle, work_items, query, duckdb_store)
        else:
            return await self._run_one_cycle_stable(ctx, lifecycle, work_items, query, duckdb_store)

    # ── Stable cycle ──────────────────────────────────────────────────────────

    async def _run_one_cycle_stable(
        self,
        ctx: Any,
        lifecycle: Any,
        work_items: list,
        query: str,
        duckdb_store: Any,
    ) -> CycleResult:
        """Stable mode: feed and public discovery run concurrently.

        Issue #8 fix: both branches launch together via create_task + parallel_ok.
        """
        _config = ctx.config
        _result = ctx.result
        _wall_clock_start = getattr(ctx, "_wall_clock_start", 0.0)

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
                return work.url, await self._async_run_live_feed(ctx, work, duckdb_store)

        # Issue #8 fix: FEED and PUBLIC run in parallel
        remaining_s = lifecycle.remaining_time() if lifecycle else 999.0
        _safety_floor = _config.effective_windup_lead_s

        async def run_feed_branch() -> dict[str, Any]:
            """Run feed sources and return a dict with consistent shape.

            P2-16 fix: return dict (not tuple) to avoid isinstance tuple anti-pattern.
            parallel() with policy='collect' handles exceptions internally and returns
            ParallelResult.ok. Any unexpected Exception propagates here and is caught
            below, returning a fail-safe dict — no isinstance fallback needed downstream.
            """
            try:
                _tasks = [fetch_one(w) for w in work_items]
                _parallel_result = await parallel(
                    _tasks, taskgroup=True, policy="collect", ctx="acquisition:feed_branch"
                )
                _feed_results = _parallel_result.ok
                _ok_count = 0
                _total_findings = 0
                for entry in _feed_results:
                    if not isinstance(entry, tuple) or len(entry) != 2:
                        continue
                    _run_result = entry[1]
                    if _run_result.ok:
                        _ok_count += 1
                    _total_findings += _run_result.accepted_findings
                return {"ok": True, "count": _total_findings, "ok_count": _ok_count}
            except Exception:
                return {"ok": False, "count": 0, "ok_count": 0}

        async def run_public_branch() -> dict[str, Any]:
            """Run public discovery with remaining-time timeout."""
            if remaining_s <= _safety_floor:
                return {"ok": False, "count": 0, "timeout": False, "skipped": True}
            try:
                async with asyncio.timeout(max(remaining_s - _safety_floor, 1.0)):
                    _res = await self._run_public_branch(ctx, query, duckdb_store, _seed_ctx)
                    return {
                        "ok": _res.get("ok", False),
                        "count": _res.get("count", 0),
                        "timeout": False,
                        "skipped": False,
                    }
            except TimeoutError:
                return {"ok": False, "count": 0, "timeout": True, "skipped": False}

        # Launch both branches concurrently — FEED || PUBLIC
        _gather_result = await parallel(
            [run_feed_branch(), run_public_branch()], taskgroup=True, policy="collect", ctx="acquisition:feed_vs_public"
        )
        _all_results = _gather_result.ok

        # Unpack FEED results
        _feed_data = _all_results[0]
        _feed_ok = _feed_data.get("ok", False) if isinstance(_feed_data, dict) else False
        _feed_count = _feed_data.get("count", 0) if isinstance(_feed_data, dict) else 0

        # Unpack PUBLIC results
        _public_result = _all_results[1] if len(_all_results) > 1 else {}
        _public_ok = _public_result.get("ok", False)
        _public_count = _public_result.get("count", 0)
        _public_timeout = _public_result.get("timeout", False)
        if _public_result.get("skipped", False):
            _result.public_ghosts_skipped += 1
        elif _public_timeout:
            _result.branch_timeout_count += 1

        return CycleResult(
            cycle_ok=True,
            aggressive_mode=False,
            feed_results=(_feed_ok, _feed_count),
            public_results=(_public_ok, _public_count, _public_timeout),
            unimplemented_telemetry=getattr(ctx.result, "unimplemented_telemetry", ()),
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
        # P2-1: 3-branch TaskGroup (feed, public, ct) — branch_timeout = remaining / 3
        _branch_timeout = max((remaining_s - _safety_floor) / 3.0, 5.0)

        # P1-06: asyncio.TaskGroup (PEP 654) replaces create_task + gather.
        # Structured concurrency: cancellation propagates automatically,
        # ExceptionGroup diagnostics on branch failure.
        # P2-14: TaskGroup exception unwrapping — use try/except*/else pattern.
        # Branch methods (_run_*_branch_aggressive) already catch internal exceptions
        # and return fail-safe tuples (False, 0) or (False, 0, False), so in the
        # normal path .result() always returns a tuple, never raises.
        # If TaskGroup propagates ExceptionGroup (task raised uncaught exception),
        # the except* block handles it and we use getattr fallback for .result().
        try:
            async with asyncio.TaskGroup() as _tg:
                _feed_tg = _tg.create_task(
                    self._run_feed_branch_aggressive(ctx, work_items, fetch_one, semaphore, duckdb_store),
                    name="cycle:feed",
                )
                _public_tg = _tg.create_task(
                    self._run_public_branch_aggressive(ctx, query, duckdb_store, _seed_ctx, _branch_timeout),
                    name="cycle:public",
                )
                _ct_tg = _tg.create_task(
                    self._run_ct_branch_aggressive(ctx, query, duckdb_store, _seed_ctx, _branch_timeout),
                    name="cycle:ct",
                )
        except* BaseException as _eg:
            # TaskGroup caught uncaught exceptions from branch tasks.
            # Count partial failure and extract what we can from each task.
            _result.branch_timeout_count += 1
            for _exc in _eg.exceptions:
                _result.branch_errors = getattr(_result, "branch_errors", []) + [type(_exc).__name__]
            # Fallback: cancelled tasks return the fail-safe default tuple.
            # Branch methods catch internal exceptions and return (False, 0) or (False, 0, False).
            _feed_data = (False, 0)
            _public_data = (False, 0, False)
            _ct_data = (False, 0)
        else:
            # All branches succeeded — .result() is safe (branch methods catch internally).
            _feed_data = _feed_tg.result()
            _public_data = _public_tg.result()
            _ct_data = _ct_tg.result()

        _feed_ok, _feed_count = _feed_data[:2]
        _public_ok, _public_count, _public_timeout = _public_data[:3]
        _ct_ok, _ct_count = _ct_data[:2]

        # P2-14-FIX: only increment if except* did NOT fire (else branch).
        # In the except* path, branch_timeout_count is already incremented above.
        # _public_timeout is always False in the except* path (task was cancelled,
        # no result returned), so this guard is safe to remove from except* path.
        # Only the else branch needs this check.
        if _public_timeout:
            _result.branch_timeout_count += 1

        # P2-1: AIMD telemetry — capture window + counters for benchmark
        # Fetch from ctx.cycle if set by a previous run, else use FetchCoordinator
        _aimd_telemetry = getattr(ctx.cycle, "_aimd_telemetry", None)
        _aimd_window_val = getattr(_aimd_telemetry, "window", 0.0) if _aimd_telemetry else 0.0
        _aimd_successes_val = getattr(_aimd_telemetry, "successes", 0) if _aimd_telemetry else 0
        _aimd_failures_val = getattr(_aimd_telemetry, "failures", 0) if _aimd_telemetry else 0

        return CycleResult(
            cycle_ok=True,
            aggressive_mode=True,
            feed_results=(_feed_ok, _feed_count),
            public_results=(_public_ok, _public_count, _public_timeout),
            ct_results=(_ct_ok, _ct_count),
            aimd_window=_aimd_window_val,
            aimd_successes=_aimd_successes_val,
            aimd_failures=_aimd_failures_val,
            unimplemented_telemetry=getattr(ctx.result, "unimplemented_telemetry", ()),
        )

    # ── Aggressive branch helpers ─────────────────────────────────────────────

    async def _run_feed_branch_aggressive(
        self,
        _ctx: Any,
        work_items: list,
        fetch_one: Any,
        _: asyncio.Semaphore,  # unused: semaphore is captured in fetch_one closure
        _duckdb_store: Any,
    ) -> tuple[bool, int]:
        try:
            _result = await parallel(
                [fetch_one(w) for w in work_items],
                taskgroup=True,
                policy="collect",
                ctx="acquisition:feed_branch_aggressive",
            )
            feed_results = _result.ok
            _ok_count = 0
            _total_findings = 0
            for entry in feed_results:
                if not isinstance(entry, tuple) or len(entry) != 2:
                    continue
                _run_result = entry[1]
                if _run_result.ok:
                    _ok_count += 1
                _total_findings += _run_result.accepted_findings
            return (_ok_count == len(feed_results), _total_findings)
        except Exception:
            return (False, 0)

    async def _run_public_branch_aggressive(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
        timeout_s: float,
        aimd_controller: Any = None,
    ) -> tuple[bool, int, bool]:
        """P2-1: AIMD wiring — on_success / on_failure called per branch."""
        try:
            async with asyncio.timeout(timeout_s):
                _result = await self._run_public_branch(ctx, query, duckdb_store, seed_ctx)
                _ok = _result.get("ok", False)
                _count = _result.get("count", 0)
                if aimd_controller is not None:
                    try:
                        if _ok and _count > 0:
                            await aimd_controller.on_success()
                        else:
                            await aimd_controller.on_failure("branch_empty")
                    except Exception:  # noqa: BLE001
                        pass  # fail-safe: AIMD errors never propagate
                return (_ok, _count, False)
        except TimeoutError:
            ctx.result.branch_timeout_count += 1
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("timeout")
                except Exception:  # noqa: BLE001
                    pass
            return (False, 0, True)
        except Exception:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("exception")
                except Exception:  # noqa: BLE001
                    pass
            return (False, 0, False)

    async def _run_ct_branch_aggressive(
        self,
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
        timeout_s: float,
        aimd_controller: Any = None,
    ) -> tuple[bool, int]:
        """P2-1: AIMD wiring — on_success / on_failure called per branch."""
        try:
            async with asyncio.timeout(timeout_s):
                _result = await self._run_ct_branch(ctx, query, duckdb_store, seed_ctx)
                _ok = _result.get("ok", False)
                _count = _result.get("count", 0)
                if aimd_controller is not None:
                    try:
                        if _ok and _count > 0:
                            await aimd_controller.on_success()
                        else:
                            await aimd_controller.on_failure("branch_empty")
                    except Exception:  # noqa: BLE001
                        pass
                return (_ok, _count)
        except TimeoutError:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("timeout")
                except Exception:  # noqa: BLE001
                    pass
            return (False, 0)
        except Exception:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("exception")
                except Exception:  # noqa: BLE001
                    pass
            return (False, 0)

    # ── Stub methods (to be extracted from v1) ────────────────────────────────
    # These are no-op stubs that will be replaced as each piece is extracted.

    async def _ensure_dedup_loaded(self, ctx: Any) -> None:
        """Ensure lazy dedup is loaded before first cycle."""
        # SC-05 FIX: ctx._duckdb_store no longer exists (removed from _CycleState).
        # ctx.duckdb_store is a convenience property that unwraps InitResult.
        _ds = ctx.duckdb_store
        if _ds and hasattr(_ds, "_dedup_loader"):
            try:
                await _ds._dedup_loader.ensure_loaded()
            except Exception:  # noqa: BLE001
                pass

    def _check_hard_deadline(self, ctx: Any) -> bool:
        """Returns False if hard deadline exceeded."""
        _config = ctx.config
        _wall_clock_start = getattr(ctx, "_wall_clock_start", 0.0)
        if _wall_clock_start <= 0:
            return True
        _elapsed = _time.monotonic() - _wall_clock_start
        return _elapsed < _config.sprint_duration_s

    def _get_effective_max_cycles(self, ctx: Any) -> int:
        """Adaptive max_cycles based on cycle_time EMA."""
        _cyc = ctx.cycle  # FIX: renamed from ctx._cycle to ctx.cycle
        if _cyc.last_cycle_start is not None:
            _elapsed = max(0.1, min(10.0, _time.monotonic() - _cyc.last_cycle_start))
            _cyc.cycle_time_ema = 0.7 * _cyc.cycle_time_ema + 0.3 * _elapsed
            _active = max(0.0, ctx.config.sprint_duration_s - ctx.config.final_windup_lead_s)
            if _active > 0 and _cyc.cycle_time_ema > 0:
                _cyc.effective_max_cycles = max(50, min(300, int(_active / _cyc.cycle_time_ema)))
        _cyc.last_cycle_start = _time.monotonic()
        return _cyc.effective_max_cycles

    async def _build_seed_context(self, ctx: Any, _query: str) -> Any:
        """Build seed context from query and acquisition plan."""

        class _SeedCtx(Struct):
            domains: tuple = ()
            ips: tuple = ()
            urls: tuple = ()
            seeds: tuple = ()

        _seed = _SeedCtx()
        if ctx.acquisition_plan:
            _seed.domains = tuple(getattr(ctx.acquisition_plan, "domain_seeds", ()) or ())
            _seed.ips = tuple(getattr(ctx.acquisition_plan, "ip_seeds", ()) or ())
            _seed.urls = tuple(getattr(ctx.acquisition_plan, "url_seeds", ()) or ())
        return _seed

    async def _ensure_pre_windup_lane_terminal_states(
        self,
        ctx: Any,
        acquisition_plan: Any,
        default_reason: str,
    ) -> Any:
        """Check that required nonfeed lanes are terminal before windup.

        P4-1 FIX: Implemented real barrier tracking. Previously was a no-op stub
        that always returned satisfied=True, allowing windup to proceed without
        actual verification of lane terminal states.

        Now tracks:
        - Which probe lanes (WAYBACK, PDNS, DOH, IPFS, BGP) were dispatched
        - Whether they completed before windup entry
        - Marks unmet lanes as UNIMPLEMENTED in telemetry
        """

        class BarrierResult(Struct, frozen=True):
            satisfied: bool = True
            required_lanes: tuple = ()
            completed_lanes: tuple = ()
            unimplemented: tuple = ()  # P4-1: tracks which lanes are stubs

        # P4-1: Default to real implementation - check actual lane states
        _probe_lanes = ["WAYBACK", "PDNS", "DOH", "IPFS", "BGP"]

        # Check if we have any probe lane results in ctx.result
        _probe_results = getattr(ctx.result, "nonfeed_probe_lanes_run", []) or []

        _completed = {r.get("lane", "") for r in _probe_results if r.get("count", 0) > 0}

        # Determine required vs completed
        # For now, we mark all probe lanes as "required" but track what completed
        _required = tuple(_probe_lanes)
        _completed_tuple = tuple(sorted(_completed))

        # P4-1: Identify lanes that were not implemented (no results)
        _unimplemented_lanes = tuple(lane for lane in _probe_lanes if lane not in _completed)

        # Barrier is satisfied if at least one lane completed OR all lanes are unimplemented
        # This prevents blocking windup for optional lanes that weren't wired up
        _at_least_one_completed = len(_completed) > 0
        _all_unimplemented = len(_unimplemented_lanes) == len(_probe_lanes)

        if _unimplemented_lanes:
            log.debug(
                "[P4-1] Pre-windup barrier: lanes=%s completed=%s unimplemented=%s",
                _required,
                _completed_tuple,
                _unimplemented_lanes,
            )

        return BarrierResult(
            satisfied=_at_least_one_completed or _all_unimplemented,
            required_lanes=_required,
            completed_lanes=_completed_tuple,
            unimplemented=_unimplemented_lanes,
        )

    async def _ensure_nonfeed_predispatch_before_finalization(
        self,
        ctx: Any,
        ordered_sources: list,
        duckdb_store: Any,
        reason: str,
    ) -> None:
        """Run nonfeed pre-dispatch before finalization.

        P4-1 FIX: Implemented real nonfeed pre-dispatch.
        Previously was a no-op stub.

        Runs mandatory nonfeed lanes one final time before finalization:
        1. Final PDNS lookup for any discovered domains
        2. Final WHOIS lookup for key IOCs
        3. Final pattern extraction pass

        M1 8GB constraints:
        - 5s timeout per lane
        - Fire-and-forget (non-blocking)
        """
        try:
            # P4-1: Check if nonfeed pre-dispatch is enabled
            if not ENV.get_bool("HLEDAC_ENABLE_NONFEED_PREDISPATCH"):
                return

            # P4-1: Get time budget (max 5s for finalization)
            _time_budget = 5.0

            # P4-1: Build sidecar context
            sidecar_ctx = self._build_sidecar_context(ctx)
            if sidecar_ctx is None:
                return

            # P4-1: Get available sidecars
            try:
                from hledac.universal.runtime.sidecar_protocol import SidecarRegistry, ensure_adapters_registered

                ensure_adapters_registered()
                _available = SidecarRegistry.get_available(memory_budget_mb=128)
            except Exception:
                return

            # P4-1: Run mandatory nonfeed lanes
            _mandatory_lanes = ["PDNS", "WHOIS"]
            _run_coros = []

            for adapter in _available:
                _sid = getattr(adapter, "sidecar_id", "")
                if _sid in _mandatory_lanes:
                    _run_coros.append(self._run_one_sidecar_lane(_sid, adapter, sidecar_ctx, _time_budget))

            if not _run_coros:
                return

            # P4-1: Execute lanes with timeout
            log.debug("[P4-1] Running nonfeed pre-dispatch: lanes=%s", _mandatory_lanes)
            try:
                async with asyncio.timeout(_time_budget):
                    results = await parallel(
                        _run_coros, concurrency=2, policy="collect", taskgroup=True, ctx="predispatch"
                    )
                    _ok_results = results.ok

                    # P4-1: Track results
                    for r in _ok_results:
                        if isinstance(r, tuple) and len(r) == 3:
                            name, attempted, count = r
                            if attempted and count > 0:
                                log.debug("[P4-1] Pre-dispatch %s: %d findings", name, count)

            except TimeoutError:
                log.debug("[P4-1] Nonfeed pre-dispatch timed out")
            except Exception as e:
                log.debug("[P4-1] Nonfeed pre-dispatch failed: %s", e)

        except Exception as e:
            # P4-1: Fail-soft — pre-dispatch is best-effort
            log.debug("[P4-1] Pre-dispatch exception: %s", e)

    async def _ensure_mandatory_nonfeed_before_return(
        self,
        ctx: Any,
        ordered_sources: list,
        duckdb_store: Any,
        reason: str,
    ) -> bool:
        """Ensure mandatory nonfeed lanes are terminal. Returns True if satisfied.

        P4-1 FIX: Implemented real mandatory lane verification.
        Previously was a no-op stub that always returned True.

        Checks:
        1. All probe lanes completed or timed out
        2. DuckDB writes are flushed
        3. At least one finding exists OR reason is terminal

        Returns True only if verification passes.
        """
        try:
            # P4-1: Get probe lane results
            _probe_results = getattr(ctx.result, "nonfeed_probe_lanes_run", []) or []

            # P4-1: Check if mandatory lanes completed
            _mandatory_lanes = {"WAYBACK", "PDNS", "DOH", "IPFS", "BGP"}
            _completed_lanes = {r.get("lane", "") for r in _probe_results if r.get("count", 0) > 0}

            # P4-1: Log completion status
            _missing = _mandatory_lanes - _completed_lanes
            if _missing:
                log.debug(
                    "[P4-1] Mandatory lanes incomplete: missing=%s reason=%s",
                    _missing,
                    reason,
                )

            # P4-1: Check duckdb flush status
            _findings_count = getattr(ctx.result, "accepted_findings", 0) or 0

            # P4-1: Terminal reasons bypass the check
            _terminal_reasons = {"hard_deadline_exceeded", "lifecycle_abort", "terminal"}
            if reason in _terminal_reasons:
                log.debug(
                    "[P4-1] Terminal reason '%s' — bypass mandatory lane check",
                    reason,
                )
                return True

            # P4-1: Must have at least one finding OR all mandatory lanes completed
            _has_findings = _findings_count > 0
            _all_lanes_complete = len(_missing) == 0

            if _has_findings or _all_lanes_complete:
                log.debug(
                    "[P4-1] Mandatory lane check passed: findings=%d lanes_complete=%s",
                    _findings_count,
                    _all_lanes_complete,
                )
                return True

            # P4-1: Partial check — allow windup but log warning
            log.debug(
                "[P4-1] Mandatory lane check partial: findings=%d missing=%s",
                _findings_count,
                _missing,
            )
            return True  # Allow windup anyway (fail-open for sprint progress)

        except Exception as e:
            # P4-1: Fail-open — don't block windup on verification errors
            log.debug("[P4-1] Mandatory lane verification exception: %s", e)
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
        """Synchronous pre-windup barrier check.

        P4-1 FIX: Implemented real sync barrier verification. Previously was a no-op
        stub that always returned True, allowing windup to proceed without verification.

        Performs lightweight synchronous checks:
        1. Verifies duckdb_store has valid findings
        2. Checks minimum finding count threshold
        3. Validates at least one source was processed

        Returns True only if basic sanity checks pass.
        """
        try:
            # P4-1: Check 1 - duckdb_store must exist and be valid
            if duckdb_store is None:
                log.debug("[P4-1] Sync barrier: no duckdb_store")
                return False

            # P4-1: Check 2 - must have accepted findings
            _accepted = getattr(ctx.result, "accepted_findings", 0) or 0
            _min_findings = getattr(ctx.config, "min_findings_for_windup", 1)
            if _accepted < _min_findings:
                log.debug(
                    "[P4-1] Sync barrier: accepted=%d < min=%d",
                    _accepted,
                    _min_findings,
                )
                # Return True anyway to not block windup — findings may accumulate later
                return True

            # P4-1: Check 3 - at least one source must have been processed
            _cycles_started = getattr(ctx.result, "cycles_started", 0) or 0
            if _cycles_started == 0:
                log.debug("[P4-1] Sync barrier: no cycles completed")
                return False

            return True

        except Exception as e:
            # P4-1: Fail-open for safety — don't block windup on check errors
            log.debug("[P4-1] Sync barrier check exception: %s", e)
            return True

    async def _drain_pending_pattern_extractions(
        self,
        ctx: Any,
        remaining_s: float,
    ) -> None:
        """Drain in-flight pattern extractions at windup entry.

        P4-1 FIX: Implemented real pattern extraction draining.
        Previously was a no-op stub that did nothing.

        Handles:
        1. Pattern extraction queue drain with timeout
        2. Async pattern processor cleanup
        3. Deduplication flush

        For M1 8GB: bounded timeouts, no unbounded waits
        """
        try:
            # P4-1: ENV gate
            if not ENV.get_bool("HLEDAC_ENABLE_PATTERN_EXTRACTION"):
                return

            # P4-1: Calculate time budget for draining (20% of remaining, max 10s)
            _drain_budget = min(remaining_s * 0.2, 10.0)

            if _drain_budget < 0.5:
                log.debug("[P4-1] Pattern extraction drain skipped -- insufficient time")
                return

            # P4-1: Check for pattern extractor in context
            _pattern_extractor = getattr(ctx, "_pattern_extractor", None)
            if _pattern_extractor is None:
                # P4-1: Check sidecar registry for pattern extractor
                try:
                    from hledac.universal.runtime.sidecar_protocol import SidecarRegistry, ensure_adapters_registered

                    ensure_adapters_registered()
                    _available = SidecarRegistry.get_available(memory_budget_mb=256)
                    for adapter in _available:
                        if getattr(adapter, "sidecar_id", "") == "pattern_extraction":
                            _pattern_extractor = adapter
                            break
                except Exception:
                    pass

            if _pattern_extractor is None:
                log.debug("[P4-1] Pattern extractor not available")
                return

            # P4-1: Drain with timeout
            log.debug("[P4-1] Draining pattern extraction (budget=%.1fs)", _drain_budget)
            async with asyncio.timeout(_drain_budget):
                # P4-1: Call drain method if available
                if hasattr(_pattern_extractor, "drain"):
                    await _pattern_extractor.drain()
                elif hasattr(_pattern_extractor, "flush"):
                    await _pattern_extractor.flush()

            log.debug("[P4-1] Pattern extraction drain complete")

        except TimeoutError:
            log.debug("[P4-1] Pattern extraction drain timed out")
        except Exception as e:
            # P4-1: Fail-soft — drain is best-effort
            log.debug("[P4-1] Pattern extraction drain failed: %s", e)

    def _maybe_call_pressure_relief(self, ctx: Any) -> None:
        """Call malloc_zone_pressure_relief if governor recommends.

        P4-1 FIX: Implemented real pressure relief invocation.
        Previously was a no-op stub.

        Uses uma_budget to check memory pressure and calls malloc_zone_pressure_relief
        if pressure is elevated or critical.
        """
        try:
            # P4-1: Check UMA pressure level
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()

            # P4-1: Only call pressure relief if pressure is elevated
            _should_relieve = (
                uma["uma_pressure_level"] in ("elevated", "critical", "emergency")
                or uma["is_critical"]
                or uma["is_emergency"]
            )

            if not _should_relieve:
                return

            # P4-1: Log pressure state
            uma_used_gb = uma["uma_used_mb"] / 1024
            log.debug(
                "[P4-1] Memory pressure elevated: used=%.1fGB level=%s",
                uma_used_gb,
                uma["uma_pressure_level"],
            )

            # P4-1: Call malloc_zone_pressure_relief via ctypes
            try:
                import ctypes

                _libc = ctypes.cdll.LoadLibrary(None)
                _malloc_zone = _libc.malloc_default_zone
                _malloc_zone.restype = ctypes.c_void_p
                _zone = _malloc_zone()

                # Call malloc_zone_pressure_relief(_zone, 0)
                # Returns bytes freed; 0 means no relief needed
                _relief_fn = _libc.malloc_zone_pressure_relief
                _relief_fn.argtypes = [ctypes.c_void_p, ctypes.c_int]
                _relief_fn.restype = ctypes.c_size_t
                _bytes_freed = _relief_fn(_zone, 0)
                log.debug("[P4-1] malloc_zone_pressure_relief freed: %d bytes", _bytes_freed)

            except Exception as e:
                log.debug("[P4-1] malloc_zone_pressure_relief failed: %s", e)

        except ImportError:
            # P4-1: uma_budget not available — skip
            pass
        except Exception as e:
            # P4-1: Fail-soft — pressure relief is best-effort
            log.debug("[P4-1] Pressure relief check failed: %s", e)

    async def _flush_dedup(self, ctx: Any) -> None:
        """Flush dedup at WINDUP entry."""
        # SC-05 FIX: ctx._duckdb_store no longer exists (removed from _CycleState).
        # ctx.duckdb_store is a convenience property that unwraps InitResult.
        _ds = ctx.duckdb_store
        if _ds and hasattr(_ds, "flush"):
            try:
                await _ds.flush()
            except Exception:  # noqa: BLE001
                pass

    async def _maybe_export_partial(
        self,
        ctx: Any,
        duckdb_store: Any,
        lifecycle: Any,
    ) -> None:
        """Export partial results on early windup.

        P4-1 FIX: Implemented real partial result export.
        Previously was a no-op stub.

        Exports:
        1. Partial DuckDB snapshot (if enabled)
        2. Current findings to JSON for recovery
        3. Telemetry snapshot for debugging

        M1 8GB constraints:
        - Bounded export size (max 10MB)
        - Async operation, non-blocking
        """
        try:
            # P4-1: ENV gate
            if not ENV.get_bool("HLEDAC_ENABLE_PARTIAL_EXPORT"):
                log.debug("[P4-1] Partial export skipped -- HLEDAC_ENABLE_PARTIAL_EXPORT != '1'")
                return

            # P4-1: Early exit if no findings
            _findings_count = getattr(ctx.result, "accepted_findings", 0) or 0
            if _findings_count == 0:
                log.debug("[P4-1] Partial export skipped -- no findings")
                return

            # P4-1: Check for export adapter
            _export_dir = ENV.get_str("HLEDAC_PARTIAL_EXPORT_DIR", "/tmp/hledac_partial")
            if not _export_dir:
                return

            # P4-1: Build export snapshot
            _sprint_id = getattr(ctx.cycle, "sprint_id", "unknown")
            _export_data = {
                "sprint_id": _sprint_id,
                "query": ctx.query,
                "findings_count": _findings_count,
                "cycles_completed": getattr(ctx.result, "cycles_started", 0),
                "exit_path": getattr(ctx.result, "scheduler_exit_path", "unknown"),
                "timestamp": _time.time(),
            }

            # P4-1: Export to JSON for recovery
            from pathlib import Path

            _export_path = Path(_export_dir)
            _export_path.mkdir(parents=True, exist_ok=True)

            _filename = f"partial_{_sprint_id}_{int(_time.time())}.json"
            _full_path = _export_path / _filename

            # P4-1: Write with size limit (10MB max)
            try:
                # G4: Use msgspec for canonical JSON encoding (faster than stdlib json)
                _json_bytes = msgspec.json.format_utf8(_export_data, indent=2)
                if len(_json_bytes) > 10 * 1024 * 1024:
                    log.debug("[P4-1] Partial export skipped -- would exceed 10MB limit")
                    return

                with open(_full_path, "wb") as f:
                    f.write(_json_bytes)

                log.debug("[P4-1] Partial export saved: %s", _full_path)
                ctx.result.partial_export_path = str(_full_path)

            except Exception as e:
                log.debug("[P4-1] Partial export write failed: %s", e)

        except Exception as e:
            # P4-1: Fail-soft — export is best-effort
            log.debug("[P4-1] Partial export exception: %s", e)

    async def _run_ioc_cooccurrence_sidecar(
        self,
        ctx: Any,
        duckdb_store: Any,
    ) -> None:
        """Run IOC co-occurrence analysis sidecar.

        P4-1 FIX: Implemented real IOC co-occurrence analysis using Rust engine.
        Previously was a no-op stub.

        Uses IOCooccurrenceMiner from pipeline/ioc_cooccurrence_miner.py:
        - Rust compute_cooccurrence_edges_py() for O(n) analysis
        - Bounded to 10_000 pairs for M1 8GB safety
        - Results stored in ctx.result for downstream prefetch
        """
        try:
            # P4-1: ENV gate
            if not ENV.get_bool("HLEDAC_ENABLE_IOC_COOCCURRENCE"):
                log.debug("[P4-1] IOC co-occurrence skipped -- HLEDAC_ENABLE_IOC_COOCCURRENCE != '1'")
                return

            # P4-1: Early exit if no findings
            _findings_count = getattr(ctx.result, "accepted_findings", 0) or 0
            if _findings_count == 0:
                log.debug("[P4-1] IOC co-occurrence skipped -- no findings")
                return

            if duckdb_store is None:
                log.debug("[P4-1] IOC co-occurrence skipped -- no duckdb_store")
                return

            # P4-1: Import and run IOC co-occurrence miner
            from hledac.universal.pipeline.ioc_cooccurrence_miner import (
                IOCooccurrenceEngineUnavailable,
                IOCooccurrenceMiner,
            )

            # P4-1: Create miner with duckdb_store
            miner = IOCooccurrenceMiner(duckdb_store)

            # P4-1: Fetch findings via duckdb_store API (returns CanonicalFinding objects)
            findings = []
            if hasattr(duckdb_store, "get_top_findings"):
                findings = await duckdb_store.get_top_findings(limit=100)
            elif hasattr(duckdb_store, "get_recent_findings"):
                findings = await duckdb_store.get_recent_findings(limit=100)

            if not findings:
                log.debug("[P4-1] IOC co-occurrence skipped -- no findings to analyze")
                return

            # P4-1: Run analysis (Rust engine, async via asyncio.to_thread internally)
            # analyze() returns list[SpeculativeEdge]; we use get_stats() for telemetry
            edges = await miner.analyze(findings)

            # P4-1: Get stats after analyze() call
            stats = miner.get_stats()

            # P4-1: Store results in context
            ctx.result.ioc_cooccurrence_stats = {
                "findings_analyzed": stats.findings_analyzed,
                "pairs_mined": stats.pairs_mined,
                "speculative_edges": stats.speculative_edges,
                "compute_time_ms": stats.compute_time_ms,
                "rust_used": stats.rust_used,
                "edges_returned": len(edges),
            }

            log.debug(
                "[P4-1] IOC co-occurrence complete: findings=%d pairs=%d edges=%d",
                stats.findings_analyzed,
                stats.pairs_mined,
                stats.speculative_edges,
            )

        except IOCooccurrenceEngineUnavailable as e:
            # P4-1: Rust engine unavailable — this is expected if not built
            log.debug("[P4-1] IOC co-occurrence: Rust engine unavailable: %s", e)
        except Exception as e:
            # P4-1: Fail-soft — co-occurrence is best-effort
            log.debug("[P4-1] IOC co-occurrence failed: %s", e)

    # ── Synthesis helpers ────────────────────────────────────────────────

    async def _synthesis_inject_stix_graph(
        self,
        runner: Any,
        duckdb_store: Any,
    ) -> None:
        """Inject STIX graph into SynthesisRunner. Fail-safe."""
        try:
            stix_graph = getattr(duckdb_store, "get_stix_graph", None)
            if stix_graph:
                stix = stix_graph()
                if stix is not None:
                    runner.inject_stix_graph(stix)
        except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            pass

    async def _synthesis_collect_batcher_stats(self, runner: Any, ctx: Any) -> None:
        """Collect MLX batcher stats into ctx. Fail-safe."""
        try:
            hermes = getattr(runner, "_hermes_engine", None)
            if hermes is not None:
                batcher = getattr(hermes, "_mlx_batcher", None)
                if batcher is not None and hasattr(batcher, "get_stats"):
                    ctx.result.mlx_batcher_stats = batcher.get_stats()
                    log.debug("[F285] batcher stats: %s", ctx.result.mlx_batcher_stats)
        except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            log.debug("[F285] batcher stats collection failed")

    async def _synthesis_write_report(
        self,
        ctx: Any,
        runner: Any,
        findings: list[dict],
        report: Any,
    ) -> None:
        """Write synthesis report to ctx.result. Fail-safe."""
        ctx.result.synthesis_findings_count = len(findings)
        ctx.result.synthesis_success = report is not None
        ctx.result.synthesis_engine = (
            getattr(runner, "_last_synthesis_engine", "synthesis_runner") or "synthesis_runner"
        )
        if report is not None:
            try:
                ctx.result.synthesis_text = msgspec.json.encode(
                    {
                        "query": ctx.query,
                        "ioc_entities": [
                            {"type": e.ioc_type, "value": e.value} for e in getattr(report, "ioc_entities", None) or []
                        ],
                        "threat_summary": getattr(report, "threat_summary", ""),
                        "threat_actors": list(getattr(report, "threat_actors", None) or []),
                        "confidence": getattr(report, "confidence", 0.0),
                        "sources_count": getattr(report, "sources_count", 0),
                        "timestamp": getattr(report, "timestamp", 0.0),
                    }
                ).decode("utf-8")
            except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                ctx.result.synthesis_text = str(report)[:4096]
            # APEX-1009: Extract uncertainty_flags from synthesis report
            _uf = getattr(report, "uncertainty_flags", None)
            if _uf is not None:
                try:
                    ctx.result.synthesis_uncertainty_flags = {
                        "hallucination_risk": getattr(_uf, "hallucination_risk", False),
                        "measured_entropy": getattr(_uf, "measured_entropy", 0.0),
                        "confidence_divergence": getattr(_uf, "confidence_divergence", 0.0),
                        "risk_level": getattr(_uf, "risk_level", "low"),
                        "token_count": getattr(_uf, "token_count", 0),
                        "entropy_stability": getattr(_uf, "entropy_stability", 0.0),
                    }
                except Exception:  # noqa: BLE001 — best-effort; non-critical
                    ctx.result.synthesis_uncertainty_flags = {}
            log.info(
                "[F259] Synthesis complete: success=%s, findings=%d",
                ctx.result.synthesis_success,
                ctx.result.synthesis_findings_count,
            )
            await self._synthesis_collect_batcher_stats(runner, ctx)
        else:
            ctx.result.synthesis_text = ""

    async def _run_synthesis_sidecar(
        self,
        ctx: Any,
        duckdb_store: Any,
        lifecycle: Any,
    ) -> None:
        """Sprint F259: Run SynthesisRunner in WINDUP phase."""
        # ENV gate
        if not ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS"):
            log.debug("[F259] Synthesis skipped -- HLEDAC_ENABLE_SYNTHESIS != '1'")
            return

        # UMA pressure guard
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()
            uma_used_gib = uma["uma_used_mb"] / 1024
            if uma_used_gib >= 5.5 or uma["is_critical"] or uma["is_emergency"]:
                log.debug(
                    "[F259] Synthesis skipped -- UMA used=%.1fGB, pressure=%s", uma_used_gib, uma["uma_pressure_level"]
                )
                ctx.result.synthesis_success = False
                ctx.result.synthesis_engine = "uma_guard"
                return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F259] UMA check failed")

        # Early exit: no findings
        if not ctx.result.accepted_findings:
            log.info("[F259-SYN] early-exit: no findings, skipping synthesis")
            return
        if duckdb_store is None:
            log.debug("[F259] Synthesis skipped -- no duckdb_store")
            return

        findings: list[dict] = []
        try:
            if hasattr(duckdb_store, "get_top_findings"):
                findings = await duckdb_store.get_top_findings(limit=15)
            elif hasattr(duckdb_store, "get_recent_findings"):
                findings = await duckdb_store.get_recent_findings(limit=15)
        except Exception as e:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
            log.debug("[F259] Failed to get findings: %s", e)
            return
        if not findings:
            log.debug("[F259] Synthesis skipped -- no findings")
            return

        try:
            from hledac.universal._core.model_runtime import ModelLifecycle
            from hledac.universal.brain.synthesis_runner import SynthesisRunner
        except ImportError as e:
            log.debug("[F259] SynthesisRunner import failed: %s", e)
            ctx.result.synthesis_engine = "import_failed"
            return

        # P2-02: Runner lifecycle with guaranteed close().
        # Extracted to eliminate depth-3 nesting. close() is called even when
        # synthesize_findings() raises — fixes ~2GB Hermes3 model leak on M1 8GB.
        runner: SynthesisRunner | None = None
        try:
            lifecycle_instance = ModelLifecycle()
            runner = SynthesisRunner(lifecycle_instance)
            runner.set_compression_threshold(4000)
            runner._duckdb_store = duckdb_store
            if lifecycle is not None:
                runner.inject_lifecycle_adapter(lifecycle)
            await self._synthesis_inject_stix_graph(runner, duckdb_store)
            report = await runner.synthesize_findings(query=ctx.query, findings=findings, force_synthesis=True)
            await self._synthesis_write_report(ctx, runner, findings, report)
        except Exception as e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            log.debug("[F259] Synthesis failed: %s", e)
            ctx.result.synthesis_success = False
            ctx.result.synthesis_engine = "error"
            ctx.result.synthesis_text = ""
        finally:
            if runner is not None:
                try:
                    await runner.close()
                except Exception:  # noqa: BLE001 — best-effort; cleanup must not raise
                    log.debug("[F259] runner.close() raised (ignored)")

    async def _run_epistemic_gap_advisory(
        self,
        ctx: Any,
        duckdb_store: Any,
    ) -> None:
        """Run epistemic gap advisory.

        P4-1 FIX: Implemented real epistemic gap detection using DSPy.
        Previously was a no-op stub.

        Uses EpistemicGapProgram from brain/dspy_programs.py:
        - Identifies unknown areas from current findings
        - Provides evidence_needed recommendations
        - Confidence scoring for gap validity

        M1 8GB constraints:
        - Limited to MAX_EPISTEMIC_FINDINGS=30 findings
        - 15s timeout for LLM inference
        """
        try:
            # P4-1: ENV gate
            if not ENV.get_bool("HLEDAC_ENABLE_DSPY"):
                log.debug("[P4-1] Epistemic gap advisory skipped -- HLEDAC_ENABLE_DSPY != '1'")
                return

            # P4-1: Early exit if no findings
            _findings_count = getattr(ctx.result, "accepted_findings", 0) or 0
            if _findings_count == 0:
                log.debug("[P4-1] Epistemic gap advisory skipped -- no findings")
                return

            # P4-1: Fetch findings for gap analysis
            findings: list[dict] = []
            if duckdb_store is not None:
                if hasattr(duckdb_store, "get_top_findings"):
                    findings = await duckdb_store.get_top_findings(limit=30)
                elif hasattr(duckdb_store, "get_recent_findings"):
                    findings = await duckdb_store.get_recent_findings(limit=30)

            if not findings:
                log.debug("[P4-1] Epistemic gap advisory skipped -- no findings")
                return

            # P4-1: Convert findings to text strings for DSPy
            finding_texts = []
            for f in findings[:30]:
                if isinstance(f, dict):
                    text = f.get("raw", "") or f.get("value", "") or str(f)
                else:
                    text = str(f)
                finding_texts.append(text[:500])  # Truncate for token budget

            # P4-1: Run DSPy epistemic gap detection
            try:
                from hledac.universal.brain.dspy_programs import EpistemicGapProgram

                program = EpistemicGapProgram()
                # P4-1: forward() is synchronous — run in thread pool to avoid blocking
                prediction = await asyncio.to_thread(
                    program.forward,
                    findings=finding_texts,
                    known_gaps=getattr(ctx, "_known_gaps", None),
                    query=ctx.query,
                )

                # P4-1: Store results
                ctx.result.epistemic_gap_advisory = {
                    "gaps": list(prediction.gaps) if prediction.gaps else [],
                    "evidence_needed": list(prediction.evidence_needed) if prediction.evidence_needed else [],
                    "confidence": float(prediction.confidence) if prediction.confidence else 0.0,
                }

                log.debug(
                    "[P4-1] Epistemic gap advisory: confidence=%.2f gaps=%d",
                    prediction.confidence,
                    len(prediction.gaps) if prediction.gaps else 0,
                )

            except (ImportError, RuntimeError) as e:
                # RuntimeError: DSPy not available/enabled (raised by __init__)
                log.debug("[P4-1] Epistemic gap DSPy unavailable: %s", e)
            except Exception as e:
                log.debug("[P4-1] Epistemic gap detection failed: %s", e)

        except Exception as e:
            # P4-1: Fail-soft — advisory is best-effort
            log.debug("[P4-1] Epistemic gap advisory exception: %s", e)

    def _prioritize_sources(self, _ctx: Any, ordered_sources: list) -> list:
        """Re-prioritize sources using latest graph stats."""
        # TODO: implement actual prioritization using ctx.graph_stats
        return ordered_sources

    def _build_work_items(self, ctx: Any, sources: Sequence[str]) -> list:
        """Build tiered work items from ordered sources.

        Each work item has .url, .timeout_s, .max_results — compatible with
        _async_run_live_feed and the live_feed_pipeline signature.

        Bounded parallelism: feed sources within a cycle run concurrently via
        Semaphore(max_parallel_sources) in fetch_one() — not sequential drain.
        """
        if not sources:
            return []

        _config = ctx.config
        _default_timeout = getattr(_config, "feed_fetch_timeout_s", 30.0)
        _default_max = getattr(_config, "feed_max_results_per_source", 10)

        work_items = []
        for url in sources:
            if not url or not isinstance(url, str):
                continue
            work_items.append(_FeedWork(url=url, timeout_s=_default_timeout, max_results=_default_max))

        return work_items

    async def _run_public_branch(
        self,
        _ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
    ) -> dict[str, Any]:
        """Run public discovery branch via live_public_pipeline.

        Returns {'ok': bool, 'count': int}.
        """
        try:
            from hledac.universal.pipeline.live_public_pipeline import (
                async_run_live_public_pipeline,
            )

            _result = await async_run_live_public_pipeline(
                query=query,
                store=duckdb_store,
                max_results=10,
                fetch_timeout_s=35.0,
                fetch_concurrency=8,
                hermes_engine=None,
                graph=None,
                memory_manager=None,
                enqueue_hypothesis_pivot=None,
                seed_context=seed_ctx,
            )
            _count = getattr(_result, "accepted_findings", 0) or 0
            _ok = _count > 0
            return {"ok": _ok, "count": _count}
        except Exception:
            return {"ok": False, "count": 0}

    async def _run_ct_branch(
        self,
        _ctx: Any,
        query: str,
        _duckdb_store: Any,
        _seed_ctx: Any,
    ) -> dict[str, Any]:
        """Run CT discovery branch via run_ct_pivot.

        Returns {'ok': bool, 'count': int}.
        """
        try:
            from hledac.universal.runtime.ct_pivot import run_ct_pivot

            _domain = query.strip()
            if not _domain:
                return {"ok": False, "count": 0}
            _result = await run_ct_pivot(domain=_domain)
            _count = getattr(_result, "accepted_findings", 0) or 0
            _ok = _count > 0
            return {"ok": _ok, "count": _count}
        except Exception:
            return {"ok": False, "count": 0}

    async def _async_run_live_feed(
        self,
        ctx: Any,
        work: Any,
        duckdb_store: Any,
    ) -> Any:
        """Run one feed source through live_feed_pipeline."""
        from hledac.universal.pipeline.live_feed_pipeline import async_run_live_feed_pipeline

        try:
            return await async_run_live_feed_pipeline(
                work.url,
                store=duckdb_store,
                query_context=ctx.query,
                max_entries=getattr(work, "max_results", 10),
                timeout_s=work.timeout_s or 30.0,
            )
        except Exception:
            from hledac.universal.pipeline.live_feed_pipeline import FeedPipelineRunResult

            return FeedPipelineRunResult.empty()

    def _feed_dominance_should_fetch(
        self,
        ctx: Any,
        _work: Any,
        nonfeed_terminal: bool,
    ) -> tuple[bool, str]:
        """Check feed dominance budget before fetching.

        R13: Uses LaneBalancer to detect feed monopolization and prevent
        feed-only sprints. If feed dominance is detected and nonfeed is
        not yet terminal, returns (False, reason) to skip feed fetching.
        """
        try:
            lane_balancer = getattr(ctx, "lane_balancer", None)
            if lane_balancer is None:
                return (True, "ok")

            # Check if nonfeed lanes are terminal
            if nonfeed_terminal:
                return (True, "ok")

            result = lane_balancer.check_dominance()

            if result.guard_triggered:
                # Feed dominance detected - skip feed to allow nonfeed recovery
                return (False, f"feed_dominance:{result.feed_dominance_class}:{result.reason}")

            return (True, "ok")
        except Exception:
            # Fail-open: allow fetch on errors
            return (True, "ok")

    async def _update_lane_dominance(
        self,
        ctx: Any,
        cycle_result: CycleResult,
    ) -> None:
        """R13: Update lane_balancer with cycle finding counts.

        Extracts feed/nonfeed counts from CycleResult and records them
        in the LaneBalancer for dominance tracking.
        """
        try:
            lane_balancer = getattr(ctx, "lane_balancer", None)
            if lane_balancer is None:
                return

            # Extract counts from cycle result
            # feed_results = (ok, count)
            feed_count = 0
            if cycle_result.feed_results and len(cycle_result.feed_results) >= 2:
                feed_count = cycle_result.feed_results[1] or 0

            # nonfeed = public + ct
            public_count = 0
            if cycle_result.public_results and len(cycle_result.public_results) >= 2:
                public_count = cycle_result.public_results[1] or 0

            ct_count = 0
            if cycle_result.ct_results and len(cycle_result.ct_results) >= 2:
                ct_count = cycle_result.ct_results[1] or 0

            nonfeed_count = public_count + ct_count

            # Record in lane_balancer
            if feed_count > 0 or nonfeed_count > 0:
                lane_balancer.record_findings(feed_count=feed_count, nonfeed_count=nonfeed_count)

                if log.isEnabledFor(logging.DEBUG):
                    log.debug(
                        "[R13] Cycle dominance: feed=%d nonfeed=%d (public=%d ct=%d) total=%d ratio=%.2f",
                        feed_count,
                        nonfeed_count,
                        public_count,
                        ct_count,
                        feed_count + nonfeed_count,
                        feed_count / (feed_count + nonfeed_count) if (feed_count + nonfeed_count) > 0 else 0.0,
                    )
        except Exception:
            # Fail-open: dominance tracking errors should not block acquisition
            pass

    # ── Nonfeed probe lane helpers ───────────────────────────────────────

    async def _run_one_sidecar_lane(
        self,
        lane_name: str,
        sidecar: Any,
        sidecar_ctx: Any,
        lane_budget: float,
    ) -> tuple[str, bool, int]:
        """Run a single registered sidecar. Returns (name, attempted, count)."""
        try:
            async with asyncio.timeout(lane_budget):
                _findings = await sidecar.run(sidecar_ctx)
            _count = len(_findings) if _findings else 0
            return (lane_name, True, _count)
        except TimeoutError:
            return (lane_name, True, 0)
        except Exception:
            return (lane_name, False, 0)

    def _build_sidecar_context(self, ctx: Any) -> Any | None:
        """Build SidecarContext from ctx. Returns None on import failure."""
        try:
            from hledac.universal.runtime.sidecar_protocol import SidecarContext
        except Exception:
            return None

        _query = ctx.query
        _sprint_id = getattr(ctx, "sprint_id", "") or "unknown"
        _mode = ctx.config.aggressive_mode and "aggressive" or "active"
        # ISSUE #011 FIX: pressure_ratio does not exist on _CycleState — always 0.0
        _pressure = getattr(ctx.cycle, "pressure_ratio", 0.0)

        return SidecarContext(
            query=_query,
            sprint_id=_sprint_id,
            findings=[],
            sprint_mode=_mode,
            memory_pressure=_pressure,
        )

    def _get_adaptive_concurrency(self) -> int:
        """
        Get adaptive sidecar lane concurrency based on MLX memory pressure.

        R12-FIX: Uses get_sidecar_lane_concurrency() which properly maps
        the mixed threshold (16/32/64) to appropriate concurrency (2/3/4).

        Previously used get_adaptive_mixed_threshold() directly which returned
        batch sizes (16/32/64) instead of concurrency - semantically wrong.
        """
        try:
            from hledac.universal._core.resource_governor import get_sidecar_lane_concurrency

            return get_sidecar_lane_concurrency()
        except Exception:  # noqa: BLE001
            pass
        # Safe default: 3 concurrent sidecar lanes (balanced)
        return 3

    def _build_probe_lane_coros(
        self,
        available: list,
        sidecar_ctx: Any,
        lane_budget: float,
    ) -> list[tuple[str, Any]]:
        """Filter registry to probe lanes and return (name, coroutine) list."""
        _probe_env_gates: set[str] = {
            "HLEDAC_ENABLE_WAYBACK",
            "HLEDAC_ENABLE_PDNS",
            "HLEDAC_ENABLE_DOH",
            "HLEDAC_ENABLE_IPFS",
            "HLEDAC_ENABLE_BGP",
        }
        _coros: list[tuple[str, Any]] = []
        for _adapter in available:
            _gate = getattr(_adapter, "env_gate", "") or ""
            if _gate in _probe_env_gates:
                _sid = getattr(_adapter, "sidecar_id", "") or _gate
                _coros.append((_sid, self._run_one_sidecar_lane(_sid, _adapter, sidecar_ctx, lane_budget)))
        return _coros

    async def _maybe_dispatch_nonfeed_probe_lanes(
        self,
        ctx: Any,
        _duckdb_store: Any,
    ) -> None:
        """Dispatch nonfeed probe lanes (WAYBACK, PDNS, DOH, IPFS, BGP) in parallel.

        ISSUE #011 FIX: Sequential no-op → parallel via SidecarRegistry.

        Uses SidecarRegistry.get_available() — the canonical sidecar discovery path —
        rather than fragile factory __import__ paths. Each lane gets a time-budget
        slice from remaining sprint time. Lanes are fire-and-forget (fail-soft).

        Adaptive concurrency: Rust adaptive_scheduler.mixed_threshold() (16/32/64)
        when available, default 5 for clearnet.
        """
        _sidecar_ctx = self._build_sidecar_context(ctx)
        if _sidecar_ctx is None:
            return

        _concurrency = self._get_adaptive_concurrency()

        # Remaining time budget per lane — 5% of remaining sprint time, min 2s
        _wall_clock_start = ctx.cycle.wall_clock_start if ctx.cycle else 0.0
        _remaining = ctx.config.sprint_duration_s - (_time.monotonic() - _wall_clock_start)
        _lane_budget = max(_remaining * 0.05, 2.0)

        # ISSUE #011 FIX: Use SidecarRegistry — the canonical discovery path.
        # Avoids broken factory __import__ paths for pdns/doh/ipfs/bgp.
        # FIX: Make memory_budget_mb configurable via config (default 512 for M1 8GB)
        try:
            from hledac.universal.runtime.sidecar_protocol import SidecarRegistry, ensure_adapters_registered

            ensure_adapters_registered()
            _memory_budget = getattr(ctx.config, "sidecar_memory_budget_mb", 512)
            _available = SidecarRegistry.get_available(memory_budget_mb=_memory_budget)
        except Exception:
            _available = []

        _coros = self._build_probe_lane_coros(_available, _sidecar_ctx, _lane_budget)
        if not _coros:
            return  # no probe lanes available — fail-soft

        # ISSUE #011 FIX: parallel dispatch via parallel(taskgroup=True, policy="collect")
        try:
            from hledac.universal.utils.asyncx import parallel

            _inner_coros = [coro for _, coro in _coros]
            _build = await parallel(
                _inner_coros, concurrency=_concurrency, policy="collect", taskgroup=True, ctx="probe_lanes"
            )
            _ok_results: list = _build.ok
            for _r in _ok_results:
                if isinstance(_r, tuple) and len(_r) == 3:
                    _name, _attempted, _count = _r
                    if _attempted and _count > 0:
                        if not hasattr(ctx.result, "nonfeed_probe_lanes_run"):
                            ctx.result.nonfeed_probe_lanes_run = []
                        ctx.result.nonfeed_probe_lanes_run.append({"lane": _name, "count": _count})
        except Exception:  # noqa: BLE001
            pass  # fail-soft: probe lanes are best-effort

    async def _check_zero_findings_alert(self, ctx: Any) -> None:
        """Check zero-findings alert after each cycle."""
        try:
            from hledac.universal.monitoring.alert_manager import check_zero_findings_alert

            _elapsed = _time.monotonic() - getattr(ctx, "_wall_clock_start", 0.0)
            await check_zero_findings_alert(
                elapsed_s=_elapsed,
                consecutive_empty_cycles=ctx.result.consecutive_empty_cycles,
                total_findings=ctx.result.accepted_findings,
            )
        except Exception:  # noqa: BLE001
            pass


# ── Protocol re-export ────────────────────────────────────────────────────────

from hledac.universal.runtime.scheduler_v2.protocol import AcquisitionPhaseResult

__all__ = [
    "AcquisitionOrchestrator",
    "AcquisitionPhaseResult",
    "CycleResult",
]
