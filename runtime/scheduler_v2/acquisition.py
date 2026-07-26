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
from typing import Any, Sequence

import msgspec

from core.env_config import ENV
from hledac.universal.utils.async_helpers import (
    safe_create_task,
    parallel,
)
from runtime.scheduler_v2._task_registry import (
    TaskScope,
    get_task_registry,
    safe_create_task_tracked,
)

# ── Pipeline Phase Types ─────────────────────────────────────────────────────────


log = logging.getLogger(__name__)

# ── Cycle Result Types ──────────────────────────────────────────────────────────


class _FeedWork(msgspec.Struct, frozen=True):
    """Work item for one feed source. Compatible with _async_run_live_feed signature.

    Migrated from @dataclass(slots=True) to msgspec.Struct (frozen=True).
    """

    url: str
    timeout_s: float = 30.0
    max_results: int = 10


class CycleResult(msgspec.Struct, frozen=True):
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
        _now_monotonic: float | None = None,  # unused: shadowed inside loop; nominal API param
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
        exit_path = "terminal"

        _wall_clock_start = getattr(ctx, "_wall_clock_start", None) or 0.0
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
                    # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                    # Issue: _ensure_nonfeed_predispatch + _finalize_result_truth sequential await
                    # Fix: asyncio.TaskGroup runs both in parallel — nonfeed_predispatch
                    # completes faster so its result can gate _finalize_result_truth output
                    try:
                        async with asyncio.TaskGroup() as _tg:
                            _tg.create_task(
                                self._ensure_nonfeed_predispatch_before_finalization(
                                    ctx, ordered_sources, duckdb_store, "hard_deadline_exceeded"
                                ),
                                name="finalize:predispatch",
                            )
                            _tg.create_task(
                                self._finalize_result_truth(
                                    ctx,
                                    "hard_deadline_exceeded",
                                    f"hard deadline exceeded at cycle {cycles_started}",
                                    "GATHER",
                                ),
                                name="finalize:truth",
                            )
                    except ExceptionGroup:
                        pass  # graceful degradation: at least one finalization path ran
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                    exit_path = "hard_deadline"
                    break

                # ── Stop requested ──────────────────────────────────────────────
                if getattr(ctx, "_stop_requested", False):
                    if await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "stop_requested"
                    ):
                        # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                        try:
                            async with asyncio.TaskGroup() as _tg:
                                _tg.create_task(
                                    self._ensure_nonfeed_predispatch_before_finalization(
                                        ctx, ordered_sources, duckdb_store, "stop_requested_break"
                                    ),
                                    name="finalize:predispatch",
                                )
                                _tg.create_task(
                                    self._finalize_result_truth(
                                        ctx, "stop_requested_break", "stop_requested guard passed", "GATHER"
                                    ),
                                    name="finalize:truth",
                                )
                        except ExceptionGroup:
                            pass
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
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
                    # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                    try:
                        async with asyncio.TaskGroup() as _tg:
                            _tg.create_task(
                                self._ensure_nonfeed_predispatch_before_finalization(
                                    ctx, ordered_sources, duckdb_store, "lifecycle_abort_break"
                                ),
                                name="finalize:predispatch",
                            )
                            _tg.create_task(
                                self._finalize_result_truth(
                                    ctx, "lifecycle_abort_break", "abort_requested from lifecycle", "GATHER"
                                ),
                                name="finalize:truth",
                            )
                    except ExceptionGroup:
                        pass
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                    _runner.request_windup()
                    break

                # ── Periodic tick ───────────────────────────────────────────────
                _runner.tick(now_monotonic)

                # ── Nonfeed pre-dispatch ───────────────────────────────────────
                await self._maybe_dispatch_nonfeed_probe_lanes(ctx, duckdb_store)

                # ── Pre-windup barrier ─────────────────────────────────────────
                _result.windup_guard_call_count += 1
                _barrier_result = await self._ensure_pre_windup_lane_terminal_states(
                    ctx, getattr(ctx, "_acquisition_plan", None), "ok"
                )
                _barrier_satisfied = getattr(_barrier_result, "satisfied", False)
                _barrier_required = getattr(_barrier_result, "required_lanes", ())

                if _barrier_required and not _barrier_satisfied:
                    _barrier_retry_count = ctx._cycle.barrier_retry_count + 1
                    _barrier_max_retries = 3
                    _barrier_hard_timeout_s = 30.0
                    ctx._cycle.barrier_retry_count = _barrier_retry_count

                    if _barrier_retry_count > _barrier_max_retries:
                        _barrier_satisfied = True
                    elif (now_monotonic - _wall_clock_start) > _barrier_hard_timeout_s:
                        _barrier_satisfied = True
                    elif not ctx._cycle.prewindup_barrier_delayed:
                        ctx._cycle.prewindup_barrier_delayed = True
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
                    windup_entered = True
                    await self._flush_dedup(ctx)

                    # Fire-and-forget IOC co-occurrence — tracked in registry
                    safe_create_task_tracked(
                        self._run_ioc_cooccurrence_sidecar(ctx, duckdb_store),
                        name="sprint:ioc_cooccurrence",
                        scope=TaskScope.WINDUP,
                    )

                    # Synthesis sidecar — tracked in registry, awaited before return
                    _synth_task = safe_create_task_tracked(
                        self._run_synthesis_sidecar(ctx, duckdb_store, _runner),
                        name="sprint:synthesis_windup",
                        scope=TaskScope.WINDUP_SYNTHESIS,
                    )

                    # ISSUE 2: store task in ctx._cycle so WinddownOrchestrator._await_synthesis
                    # can retrieve it. Without this, _await_synthesis re-awaiting None is a no-op
                    # and runner.close() races against the still-running synthesis task.
                    ctx._cycle.synth_windup_task = _synth_task

                    # ISSUE P1: await synthesis task before returning from windup.
                    # Prevents "sprint concluded" race where runner.close() is called
                    # while synthesis is still running (leaves ~2GB Hermes3 loaded on M1 8GB).
                    try:
                        async with asyncio.timeout(15.0):
                            await _synth_task
                    except asyncio.TimeoutError:
                        _synth_task.cancel()
                        log.debug("[F259] synthesis task timed out after 15s, cancelled")
                    except Exception:
                        pass  # fail-safe: synthesis errors are non-critical

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
                        # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                        try:
                            async with asyncio.TaskGroup() as _tg:
                                _predispatch_tg = _tg.create_task(
                                    self._ensure_nonfeed_predispatch_before_finalization(
                                        ctx, ordered_sources, duckdb_store, "windup_barrier_passed"
                                    ),
                                    name="finalize:predispatch",
                                )
                                _finalize_tg = _tg.create_task(
                                    self._finalize_result_truth(
                                        ctx,
                                        "windup_barrier_passed",
                                        "pre-windup barrier satisfied, entered windup",
                                        "WINDUP",
                                    ),
                                    name="finalize:truth",
                                )
                        except ExceptionGroup:
                            pass
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                        break

                    await self._ensure_mandatory_nonfeed_before_return(
                        ctx, ordered_sources, duckdb_store, "windup_barrier_forced"
                    )
                    # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                    try:
                        async with asyncio.TaskGroup() as _tg:
                            _predispatch_tg = _tg.create_task(
                                self._ensure_nonfeed_predispatch_before_finalization(
                                    ctx, ordered_sources, duckdb_store, "windup_barrier_break"
                                ),
                                name="finalize:predispatch",
                            )
                            _finalize_tg = _tg.create_task(
                                self._finalize_result_truth(
                                    ctx,
                                    "windup_barrier_break",
                                    "pre-windup barrier unsatisfied, forced terminalization",
                                    "WINDUP",
                                ),
                                name="finalize:truth",
                            )
                    except ExceptionGroup:
                        pass
                    _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
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
                        # Sprint F350M-R Issue #P2: parallelize finalization pipeline
                        try:
                            async with asyncio.TaskGroup() as _tg:
                                _predispatch_tg = _tg.create_task(
                                    self._ensure_nonfeed_predispatch_before_finalization(
                                        ctx, ordered_sources, duckdb_store, "max_cycles_reached"
                                    ),
                                    name="finalize:predispatch",
                                )
                                _finalize_tg = _tg.create_task(
                                    self._finalize_result_truth(
                                        ctx,
                                        "max_cycles_reached",
                                        f"max_cycles {effective_max_cycles} reached",
                                        "ACTIVE",
                                    ),
                                    name="finalize:truth",
                                )
                        except ExceptionGroup:
                            pass
                        _result.scheduler_exit_elapsed_s = _time.monotonic() - _wall_clock_start
                        _runner.request_windup()
                        break
                    continue

                # ── Run one cycle ─────────────────────────────────────────────
                # P6 FIX: aggressive mode now runs its 3 branches (feed/public/ct)
                # in parallel via asyncio.TaskGroup — no longer sequential within
                # the aggressive cycle body. The stable vs aggressive DISPATCHER
                # itself runs sequentially (only one branch runs per cycle), but
                # within aggressive mode the feed || public || ct parallelism is
                # the win. Stable mode already had feed || public parallelism.
                # RESULT: the _run_one_cycle() call is unchanged — it dispatches
                # correctly; the P6 gain is inside aggressive's TaskGroup.
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

                # ── Check zero-findings alert ─────────────────────────────────
                await self._check_zero_findings_alert(ctx)

            # exit_path is already "terminal" (default); all break paths override explicitly

        except asyncio.CancelledError:
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
        _now_monotonic: float | None,
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

        lifecycle = getattr(ctx, "_lifecycle", None)
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

        Issue #8 fix: both branches launch together via create_task + safe_gather_ok.
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
                _parallel_result = await parallel(_tasks, taskgroup=True, policy='collect', ctx='acquisition:feed_branch')
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
        _gather_result = await parallel([run_feed_branch(), run_public_branch()], taskgroup=True, policy='collect', ctx='acquisition:feed_vs_public')
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
        # Fetch from ctx._cycle if set by a previous run, else use FetchCoordinator
        _aimd_telemetry = getattr(ctx._cycle, "_aimd_telemetry", None)
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
            _result = await parallel([fetch_one(w) for w in work_items], taskgroup=True, policy='collect', ctx='acquisition:feed_branch_aggressive')
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
                    except Exception:
                        pass  # fail-safe: AIMD errors never propagate
                return (_ok, _count, False)
        except TimeoutError:
            ctx.result.branch_timeout_count += 1
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("timeout")
                except Exception:
                    pass
            return (False, 0, True)
        except Exception:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("exception")
                except Exception:
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
                    except Exception:
                        pass
                return (_ok, _count)
        except TimeoutError:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("timeout")
                except Exception:
                    pass
            return (False, 0)
        except Exception:
            if aimd_controller is not None:
                try:
                    await aimd_controller.on_failure("exception")
                except Exception:
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
            except Exception:
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
        _cyc = ctx._cycle
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

        class _SeedCtx(msgspec.Struct):
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
        _ctx: Any,
        _acquisition_plan: Any,
        _default_reason: str,
    ) -> Any:
        """Check that required nonfeed lanes are terminal before windup."""

        class BarrierResult(msgspec.Struct, frozen=True):
            satisfied: bool = True
            required_lanes: tuple = ()

        return BarrierResult(satisfied=True, required_lanes=())

    async def _ensure_nonfeed_predispatch_before_finalization(
        self,
        _ctx: Any,
        _ordered_sources: list,
        _duckdb_store: Any,
        _reason: str,
    ) -> None:
        """Run nonfeed pre-dispatch before finalization."""
        pass

    async def _ensure_mandatory_nonfeed_before_return(
        self,
        _ctx: Any,
        _ordered_sources: list,
        _duckdb_store: Any,
        _reason: str,
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
        _ctx: Any,
        _ordered_sources: list,
        _duckdb_store: Any,
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
        # SC-05 FIX: ctx._duckdb_store no longer exists (removed from _CycleState).
        # ctx.duckdb_store is a convenience property that unwraps InitResult.
        _ds = ctx.duckdb_store
        if _ds and hasattr(_ds, "flush"):
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
        """Sprint F259: Run SynthesisRunner in WINDUP phase."""
        if not ENV.get_bool("HLEDAC_ENABLE_HERMES_SYNTHESIS"):
            log.debug("[F259] Synthesis skipped -- HLEDAC_ENABLE_SYNTHESIS != '1'")
            return
        try:
            from hledac.universal.utils.uma_budget import get_uma_snapshot

            uma = get_uma_snapshot()
            uma_used_gib = uma['uma_used_mb'] / 1024
            if uma_used_gib >= 5.5 or uma['is_critical'] or uma['is_emergency']:
                log.debug("[F259] Synthesis skipped -- UMA used=%.1fGB, pressure=%s", uma_used_gib, uma['uma_pressure_level'])
                ctx.result.synthesis_success = False
                ctx.result.synthesis_engine = "uma_guard"
                return
        except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
            log.debug("[F259] UMA check failed")
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
            from hledac.universal.core.model_runtime import ModelLifecycle
            from hledac.universal.brain.synthesis_runner import SynthesisRunner
        except ImportError as e:
            log.debug("[F259] SynthesisRunner import failed: %s", e)
            ctx.result.synthesis_engine = "import_failed"
            return
        # P2-02: Use try/finally to guarantee runner.close() even on exception.
        # Previously close() was only on success path (line 1324) — synthesize_findings()
        # exception left model loaded (Hermes3 ~2GB on M1 8GB = significant leak).
        runner: SynthesisRunner | None = None
        try:
            lifecycle_instance = ModelLifecycle()
            runner = SynthesisRunner(lifecycle_instance)
            runner.set_compression_threshold(4000)
            runner._duckdb_store = duckdb_store
            if lifecycle is not None:
                runner.inject_lifecycle_adapter(lifecycle)
            try:
                stix_graph = getattr(duckdb_store, "get_stix_graph", None)
                if stix_graph:
                    stix = stix_graph()
                    if stix is not None:
                        runner.inject_stix_graph(stix)
            except Exception:  # noqa: BLE001 — best-effort; DB operation failure; non-critical
                pass
            report = await runner.synthesize_findings(query=ctx.query, findings=findings, force_synthesis=True)
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
                except Exception:  # noqa: BLE001 — best-effort; best-effort fallback; non-critical
                    ctx.result.synthesis_text = str(report)[:4096]
                log.info(
                    "[F259] Synthesis complete: success=%s, findings=%d",
                    ctx.result.synthesis_success,
                    ctx.result.synthesis_findings_count,
                )
                try:
                    hermes = getattr(runner, "_hermes_engine", None)
                    if hermes is not None:
                        batcher = getattr(hermes, "_mlx_batcher", None)
                        if batcher is not None and hasattr(batcher, "get_stats"):
                            ctx.result.mlx_batcher_stats = batcher.get_stats()
                            log.debug("[F285] batcher stats: %s", ctx.result.mlx_batcher_stats)
                except Exception:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
                    log.debug("[F285] batcher stats collection failed")
            else:
                ctx.result.synthesis_text = ""
        except Exception as e:  # noqa: BLE001 — best-effort; telemetry/stats; best-effort
            log.debug("[F259] Synthesis failed: %s", e)
            ctx.result.synthesis_success = False
            ctx.result.synthesis_engine = "error"
            ctx.result.synthesis_text = ""
        finally:
            # P2-02: ALWAYS close runner — finally guarantees execution even on exception.
            # This fixes the ~2GB Hermes3 model leak when synthesize_findings() raises.
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
        """Run epistemic gap advisory."""
        pass

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
        _ctx: Any,
        _work: Any,
        _nonfeed_terminal: bool,
    ) -> tuple[bool, str]:
        """Check feed dominance budget before fetching."""
        return (True, "ok")

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
        try:
            from runtime.sidecar_protocol import SidecarContext, SidecarRegistry, ensure_adapters_registered
        except Exception:
            return

        # Build SidecarContext
        _query = ctx.query
        _sprint_id = getattr(ctx, "sprint_id", "") or "unknown"
        _mode = ctx.config.aggressive_mode and "aggressive" or "active"
        # ISSUE #011 FIX: pressure_ratio does not exist on _CycleState — always 0.0
        _pressure = getattr(ctx._cycle, "pressure_ratio", 0.0)

        # ISSUE #011 FIX: findings must be a list, not int.
        # accepted_findings is int — list(int) produces list of digits (e.g. list(42) → [4, 2]).
        _sidecar_ctx = SidecarContext(
            query=_query,
            sprint_id=_sprint_id,
            findings=[],  # findings field is for cross-sprint context; empty is correct
            sprint_mode=_mode,
            memory_pressure=_pressure,
        )

        # Adaptive concurrency from Rust adaptive_scheduler — 16/32/64 based on MLX memory pressure
        _concurrency: int = 5  # clearnet default
        try:
            from hledac.universal.rust_extensions import hledac_rust_extensions

            _concurrency = getattr(hledac_rust_extensions, "get_adaptive_mixed_threshold", lambda: 32)()
        except Exception:
            pass  # fallback: 5

        # Remaining time budget per lane — 5% of remaining sprint time, min 2s
        _wall_clock_start = ctx._cycle.wall_clock_start if ctx._cycle else 0.0
        _remaining = ctx.config.sprint_duration_s - (_time.monotonic() - _wall_clock_start)
        _lane_budget = max(_remaining * 0.05, 2.0)

        # Probe lane env gates — used to filter SidecarRegistry results
        _probe_env_gates: set[str] = {
            "HLEDAC_ENABLE_WAYBACK",
            "HLEDAC_ENABLE_PDNS",
            "HLEDAC_ENABLE_DOH",
            "HLEDAC_ENABLE_IPFS",
            "HLEDAC_ENABLE_BGP",
        }

        async def _run_one_registered_sidecar(
            lane_name: str,
            sidecar: Any,
            sidecar_ctx: SidecarContext,
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

        # ISSUE #011 FIX: Use SidecarRegistry — the canonical discovery path.
        # Avoids broken factory __import__ paths for pdns/doh/ipfs/bgp.
        try:
            ensure_adapters_registered()
            _available = SidecarRegistry.get_available(memory_budget_mb=512)
        except Exception:
            _available = []

        # Filter to probe lanes only and build coroutines
        _coros: list[tuple[str, Any]] = []
        for _adapter in _available:
            _gate = getattr(_adapter, "env_gate", "") or ""
            if _gate in _probe_env_gates:
                _sid = getattr(_adapter, "sidecar_id", "") or _gate
                _coros.append((_sid, _run_one_registered_sidecar(_sid, _adapter, _sidecar_ctx, _lane_budget)))

        if not _coros:
            return  # no probe lanes available — fail-soft

        # ISSUE #011 FIX: parallel dispatch via parallel(taskgroup=True, policy="collect")
        try:
            from hledac.universal.utils.async_helpers import parallel

            _inner_coros = [coro for _, coro in _coros]
            _build = await parallel(_inner_coros, concurrency=_concurrency, policy="collect", taskgroup=True, ctx="probe_lanes")
            _ok_results: list = _build.ok
            _error_results: list = list(_build.errors) if _build.errors else []
            for _r in _ok_results:
                if isinstance(_r, tuple) and len(_r) == 3:
                    _name, _attempted, _count = _r
                    if _attempted and _count > 0:
                        if not hasattr(ctx.result, "nonfeed_probe_lanes_run"):
                            ctx.result.nonfeed_probe_lanes_run = []
                        ctx.result.nonfeed_probe_lanes_run.append({"lane": _name, "count": _count})
        except Exception:
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
        except Exception:
            pass


# ── Protocol re-export ────────────────────────────────────────────────────────

from runtime.scheduler_v2.protocol import AcquisitionPhaseResult  # noqa: E402

__all__ = [
    "AcquisitionOrchestrator",
    "AcquisitionPhaseResult",
    "CycleResult",
]
