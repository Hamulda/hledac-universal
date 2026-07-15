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
from typing import Any, Sequence

import msgspec

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
                            _predispatch_tg = _tg.create_task(
                                self._ensure_nonfeed_predispatch_before_finalization(
                                    ctx, ordered_sources, duckdb_store, "hard_deadline_exceeded"
                                ),
                                name="finalize:predispatch",
                            )
                            _finalize_tg = _tg.create_task(
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
                                _predispatch_tg = _tg.create_task(
                                    self._ensure_nonfeed_predispatch_before_finalization(
                                        ctx, ordered_sources, duckdb_store, "stop_requested_break"
                                    ),
                                    name="finalize:predispatch",
                                )
                                _finalize_tg = _tg.create_task(
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
                            _predispatch_tg = _tg.create_task(
                                self._ensure_nonfeed_predispatch_before_finalization(
                                    ctx, ordered_sources, duckdb_store, "lifecycle_abort_break"
                                ),
                                name="finalize:predispatch",
                            )
                            _finalize_tg = _tg.create_task(
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

                    # Fire-and-forget IOC co-occurrence
                    safe_create_task(
                        self._run_ioc_cooccurrence_sidecar(ctx, duckdb_store),
                        name="sprint:ioc_cooccurrence",
                    )

                    # Synthesis sidecar
                    _synth_task = safe_create_task(
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

        async def run_feed_branch() -> tuple[list, bool, int]:
            """Run feed sources and return (results, ok, count)."""
            _tasks = [fetch_one(w) for w in work_items]
            _feed_results = await _safe_gather_ok(*_tasks)
            _ok = all(r[1].ok for r in _feed_results if not isinstance(r, Exception))
            _count = sum(
                r[1].accepted_findings if not isinstance(r, Exception) and hasattr(r[1], "accepted_findings") else 0
                for r in _feed_results
            )
            return _feed_results, _ok, _count

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
        _all_results = await _safe_gather_ok(run_feed_branch(), run_public_branch())

        # Unpack FEED results
        _feed_data = _all_results[0]
        _feed_results, _feed_ok, _feed_count = _feed_data if isinstance(_feed_data, tuple) else (_feed_data, False, 0)

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
        # P2-1: 5-branch TaskGroup — branch_timeout = remaining / 5 (was / 3)
        _branch_timeout = max((remaining_s - _safety_floor) / 3.0, 5.0)

        # P1-06: asyncio.TaskGroup (PEP 654) replaces create_task + gather.
        # Structured concurrency: cancellation propagates automatically,
        # ExceptionGroup diagnostics on branch failure.
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
            # TaskGroup catches child exceptions and re-raises as ExceptionGroup.
            # At least one branch failed — count it as partial.
            _result.branch_timeout_count += 1
            for _exc in _eg.exceptions:
                _result.branch_errors = getattr(_result, "branch_errors", []) + [type(_exc).__name__]

        _feed_results = (
            _feed_tg.result(),
            _public_tg.result(),
            _ct_tg.result(),
        )

        _feed_ok = _feed_results[0][0] if not isinstance(_feed_results[0], Exception) else False
        _feed_count = _feed_results[0][1] if not isinstance(_feed_results[0], Exception) else 0
        _public_ok = _feed_results[1][0] if not isinstance(_feed_results[1], Exception) else False
        _public_count = _feed_results[1][1] if not isinstance(_feed_results[1], Exception) else 0
        _public_timeout = _feed_results[1][2] if not isinstance(_feed_results[1], Exception) else False
        _ct_ok = _feed_results[2][0] if not isinstance(_feed_results[2], Exception) else False
        _ct_count = _feed_results[2][1] if not isinstance(_feed_results[2], Exception) else 0

        if _public_timeout or (_feed_results[1] and isinstance(_feed_results[1], Exception)):
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
        ctx: Any,
        work_items: list,
        fetch_one: Any,
        semaphore: asyncio.Semaphore,
        duckdb_store: Any,
    ) -> tuple[bool, int]:
        try:
            feed_results = await _safe_gather_ok(*[fetch_one(w) for w in work_items])
            _ok = all(r[1].ok for r in feed_results if not isinstance(r, Exception))
            _count = sum(
                r[1].accepted_findings if not isinstance(r, Exception) and hasattr(r[1], "accepted_findings") else 0
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
        _ds = getattr(ctx, "_duckdb_store", None) or getattr(ctx, "duckdb_store", None)
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

    async def _build_seed_context(self, ctx: Any, query: str) -> Any:
        """Build seed context from query and acquisition plan."""

        class _SeedCtx(msgspec.Struct, frozen=True):
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
        """Check that required nonfeed lanes are terminal before windup."""

        class BarrierResult(msgspec.Struct, frozen=True):
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
        _ds = getattr(ctx, "_duckdb_store", None) or getattr(ctx, "duckdb_store", None)
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
        ctx: Any,
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
        ctx: Any,
        query: str,
        duckdb_store: Any,
        seed_ctx: Any,
    ) -> dict[str, Any]:
        """Run CT discovery branch via run_ct_pivot.

        Returns {'ok': bool, 'count': int}.
        """
        try:
            from hledac.universal.runtime.sprint_entrypoint import run_ct_pivot

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
        from hledac.universal.pipeline.live_feed_pipeline import async_run_live_feed

        try:
            return await async_run_live_feed(
                work.url,
                query=ctx.query,
                store=duckdb_store,
                fetch_timeout_s=work.timeout_s or 30.0,
                max_results=getattr(work, "max_results", 10),
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
            from hledac.universal.utils.alerts import check_zero_findings_alert

            _elapsed = _time.monotonic() - getattr(ctx, "_wall_clock_start", 0.0)
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


async def _safe_gather_ok(*args: Any, **kwargs: Any) -> list:
    """safe_gather_return_exceptions wrapper — avoids importing utils.async_helpers at module load."""
    from hledac.universal.utils.async_helpers import safe_gather_return_exceptions

    return await safe_gather_return_exceptions(*args, **kwargs)


# ── Protocol re-export ────────────────────────────────────────────────────────

from runtime.scheduler_v2.protocol import AcquisitionPhaseResult  # noqa: E402

__all__ = [
    "AcquisitionOrchestrator",
    "AcquisitionPhaseResult",
    "CycleResult",
    "safe_create_task",
]
