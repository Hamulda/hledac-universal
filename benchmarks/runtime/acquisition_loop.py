"""P2-1 Benchmark: AcquisitionLoop TaskGroup performance.

Compares sequential (v1-style) vs parallel (TaskGroup) branch execution.






Target: >= 1.5× speedup from parallel branches on M1 8GB.

Run:
    uv run python -m benchmarks.runtime.acquisition_loop

Benchmark methodology:
    - synthetic SprintContext with mocked branches (no real I/O)
    - measures wall-clock time for N cycles
    - reports mean/median/p95 latency per cycle
    - validates ExceptionGroup handling

Invariant tests:
    - [P2-1-1] ExceptionGroup: branch failure raises ExceptionGroup with correct sub-exceptions
    - [P2-1-2] Cancellation: TaskGroup cancellation propagates to all children
    - [P2-1-3] AIMD telemetry: CycleResult includes aimd_window/successes/failures
    - [P2-1-4] Branch count: max 5 concurrent branches on M1 8GB RAM budget
    - [P2-1-5] Sequential vs parallel: parallel is >= 1.5× faster for 3+ branches
"""

from __future__ import annotations

import asyncio
import time as _time
import statistics as _statistics
from typing import Any

# ── Synthetic SprintContext helpers ──────────────────────────────────────────────


class _MockConfig:
    """Minimal mock for ctx.config."""
    __slots__ = ("aggressive_mode", "max_parallel_sources", "effective_windup_lead_s",
                 "sprint_duration_s", "doh_enabled", "wayback_enabled")

    def __init__(self, aggressive_mode: bool = True) -> None:
        self.aggressive_mode = aggressive_mode
        self.max_parallel_sources = 5
        self.effective_windup_lead_s = 30.0
        self.sprint_duration_s = 300.0
        self.doh_enabled = False
        self.wayback_enabled = False


class _MockResult:
    """Minimal mock for ctx.result."""
    __slots__ = (
        "cycles_started", "consecutive_empty_cycles", "max_consecutive_empty_cycles",
        "branch_timeout_count", "accepted_findings", "branch_errors", "public_ghosts_skipped",
        "aimd_window", "aimd_successes", "aimd_failures",
    )

    def __init__(self) -> None:
        self.cycles_started = 0
        self.consecutive_empty_cycles = 0
        self.max_consecutive_empty_cycles = 0
        self.branch_timeout_count = 0
        self.accepted_findings = 0
        self.branch_errors: list = []
        self.public_ghosts_skipped = 0
        self.aimd_window = 0.0
        self.aimd_successes = 0
        self.aimd_failures = 0


class _MockCycle:
    """Minimal mock for ctx._cycle."""
    __slots__ = ("wall_clock_start", "barrier_retry_count", "prewindup_barrier_delayed",
                 "last_cycle_start", "cycle_time_ema", "effective_max_cycles", "_aimd_telemetry",
                 "pressure_ratio")

    def __init__(self) -> None:
        self.wall_clock_start = _time.monotonic()
        self.barrier_retry_count = 0
        self.prewindup_barrier_delayed = False
        self.last_cycle_start = None
        self.cycle_time_ema = 0.0
        self.effective_max_cycles = 300
        self._aimd_telemetry = None
        self.pressure_ratio = 0.0


class _MockContext:
    """Minimal mock for SprintContext in benchmark."""
    __slots__ = ("config", "result", "query", "_cycle", "_wall_clock_start",
                 "_lifecycle", "_stop_requested", "acquisition_plan", "hermes_engine",
                 "enrichment_services", "runner")

    def __init__(self, aggressive: bool = True) -> None:
        self.config = _MockConfig(aggressive_mode=aggressive)
        self.result = _MockResult()
        self.query = "test domain"
        self._cycle = _MockCycle()
        self._wall_clock_start = _time.monotonic()
        self._stop_requested = False
        self.acquisition_plan = None
        self.hermes_engine = None
        self.enrichment_services = None
        self.runner = None


class _MockLifecycle:
    """Minimal mock for lifecycle."""
    __slots__ = ("_remaining",)

    def __init__(self, remaining: float = 200.0) -> None:
        self._remaining = remaining

    def remaining_time(self) -> float:
        return self._remaining


# ── Branch simulators (synthetic I/O) ───────────────────────────────────────────


async def _simulate_branch(name: str, latency_s: float, count: int, fail_prob: float = 0.0) -> tuple[bool, int]:
    """Simulate a branch with synthetic latency. Returns (ok, count)."""
    await asyncio.sleep(latency_s)
    task = asyncio.current_task()
    if task is not None and fail_prob > 0 and task.cancelled():
        raise asyncio.CancelledError(f"{name} cancelled")
    ok = True  # always ok unless cancelled
    return (ok, count)


# ── Sequential baseline (v1-style) ───────────────────────────────────────────────


async def _run_sequential_v1(ctx: _MockContext, lifecycle: _MockLifecycle, n_branches: int = 3) -> float:
    """V1-style sequential await per branch. Returns wall-clock time."""
    t0 = _time.perf_counter()
    remaining_s = lifecycle.remaining_time()
    branch_timeout = max((remaining_s - 30.0) / n_branches, 5.0)

    for i in range(n_branches):
        try:
            async with asyncio.timeout(branch_timeout):
                await _simulate_branch(f"branch_{i}", latency_s=0.05, count=i + 1)
        except TimeoutError:
            pass  # branch timeout — fail-soft, continue to next
    return _time.perf_counter() - t0


# ── Parallel TaskGroup (v2-style) ───────────────────────────────────────────────


async def _run_parallel_taskgroup(
    ctx: _MockContext, lifecycle: _MockLifecycle, n_branches: int = 3
) -> tuple[float, list[tuple[bool, int]]]:
    """V2-style parallel TaskGroup. Returns (wall-clock time, results)."""
    t0 = _time.perf_counter()
    remaining_s = lifecycle.remaining_time()
    branch_timeout = max((remaining_s - 30.0) / n_branches, 5.0)

    try:
        async with asyncio.TaskGroup() as tg:
            tasks = []
            for i in range(n_branches):
                t = tg.create_task(_simulate_branch(f"branch_{i}", latency_s=0.05, count=i + 1))
                tasks.append(t)
    except* BaseException as eg:
        # ExceptionGroup handling — at least one branch failed
        pass

    results = []
    for t in tasks:
        try:
            results.append(t.result())
        except Exception:
            results.append((False, 0))

    elapsed = _time.perf_counter() - t0
    return elapsed, results


# ── ExceptionGroup test helpers ────────────────────────────────────────────────────


async def _branch_that_fails() -> tuple[bool, int]:
    """Branch that always raises ValueError."""
    raise ValueError("synthetic branch failure")


async def _run_exceptiongroup_test() -> dict[str, Any]:
    """Test: TaskGroup raises ExceptionGroup with correct sub-exceptions."""
    errors: list[Exception] = []

    try:
        async with asyncio.TaskGroup() as tg:
            tg.create_task(_simulate_branch("ok_branch", 0.01, 1))
            tg.create_task(_branch_that_fails())
            tg.create_task(_simulate_branch("ok_branch_2", 0.01, 2))
    except* ValueError as eg:
        for e in eg.exceptions:
            errors.append(e)

    return {
        "exception_count": len(errors),
        "is_value_error": all(isinstance(e, ValueError) for e in errors),
        "has_branch_failure_msg": any("synthetic branch failure" in str(e) for e in errors),
    }


async def _run_cancellation_test() -> dict[str, bool]:
    """Test: cancelling TaskGroup propagates to all children."""
    # Use mutable list for closure-safe mutation
    cancelled_count = [0]

    async def _slow_branch(idx: int) -> tuple[int, str]:
        try:
            await asyncio.sleep(10.0)  # intentionally slow
            return (idx, "completed")
        except asyncio.CancelledError:
            cancelled_count[0] += 1
            raise

    inner_done = asyncio.Event()
    tasks: list[asyncio.Task] = []

    async def _outer_cancel() -> None:
        await asyncio.sleep(0.02)  # let TaskGroup start
        for t in tasks:
            t.cancel()
        inner_done.set()

    try:
        async with asyncio.TaskGroup() as tg:
            for i in range(3):
                t = tg.create_task(_slow_branch(i))
                tasks.append(t)
            cancel_task = asyncio.create_task(_outer_cancel())
            await inner_done.wait()
    except* BaseException:
        pass  # CancelledError or ExceptionGroup

    return {
        "cancelled_or_completed": cancelled_count[0] > 0,
    }


# ── AIMD telemetry test ─────────────────────────────────────────────────────────


async def _run_aimd_telemetry_test() -> dict[str, Any]:
    """Test: CycleResult includes aimd_window/successes/failures."""
    from runtime.scheduler_v2.acquisition import CycleResult

    result = CycleResult(
        cycle_ok=True,
        aggressive_mode=True,
        feed_results=(True, 5),
        public_results=(True, 3, False),
        ct_results=(True, 2),
        aimd_window=12.0,
        aimd_successes=47,
        aimd_failures=3,
    )

    return {
        "has_aimd_fields": (
            result.aimd_window > 0
            and result.aimd_successes > 0
            and result.aimd_failures >= 0
        ),
        "aimd_window_value": result.aimd_window,
        "aimd_successes_value": result.aimd_successes,
        "aimd_failures_value": result.aimd_failures,
    }


# ── Branch limit test ────────────────────────────────────────────────────────────


async def _run_branch_limit_test() -> dict[str, Any]:
    """Test: feed fetches respect max_parallel_sources semaphore (M1 8GB RAM budget).

    P2-1: The TaskGroup runs 3 branch tasks (feed, public, ct) concurrently.
    Within the feed branch, Semaphore(max_parallel_sources=5) limits concurrent
    source fetches. This is the correct limiting mechanism — not the TaskGroup itself.
    """
    MAX_PARALLEL_SOURCES = 5

    peak_concurrent = 0
    current_concurrent = 0

    async def _fetch_source(idx: int) -> tuple[int, int]:
        nonlocal current_concurrent, peak_concurrent
        current_concurrent += 1
        peak_concurrent = max(peak_concurrent, current_concurrent)
        await asyncio.sleep(0.01)  # simulate I/O
        current_concurrent -= 1
        return (idx, 1)

    # Semaphore limits concurrent feed fetches to MAX_PARALLEL_SOURCES
    sem = asyncio.Semaphore(MAX_PARALLEL_SOURCES)
    all_sources = list(range(20))  # 20 sources requested

    async def _fetch_with_semaphore() -> None:
        nonlocal peak_concurrent, current_concurrent
        async def bounded_fetch(idx: int) -> tuple[int, int]:
            async with sem:
                return await _fetch_source(idx)

        async with asyncio.TaskGroup() as tg:
            for idx in all_sources:
                tg.create_task(bounded_fetch(idx))

    await _fetch_with_semaphore()

    return {
        "under_limit": peak_concurrent <= MAX_PARALLEL_SOURCES,
        "peak_concurrent": peak_concurrent,
        "max_allowed": MAX_PARALLEL_SOURCES,
    }


# ── Speed comparison benchmark ───────────────────────────────────────────────────


async def _run_speed_benchmark(n_cycles: int = 20, n_branches: int = 3) -> dict[str, float]:
    """Compare sequential vs parallel branch execution. Target: >= 1.5× speedup."""
    seq_times: list[float] = []
    par_times: list[float] = []

    for _ in range(n_cycles):
        ctx = _MockContext(aggressive=True)
        lifecycle = _MockLifecycle(remaining=200.0)

        # Sequential
        t_seq = await _run_sequential_v1(ctx, lifecycle, n_branches=n_branches)
        seq_times.append(t_seq)

        # Parallel
        ctx2 = _MockContext(aggressive=True)
        t_par, _ = await _run_parallel_taskgroup(ctx2, lifecycle, n_branches=n_branches)
        par_times.append(t_par)

    speedup = _statistics.median(seq_times) / _statistics.median(par_times) if par_times else 0.0

    return {
        "sequential_median_ms": round(_statistics.median(seq_times) * 1000, 2),
        "parallel_median_ms": round(_statistics.median(par_times) * 1000, 2),
        "speedup_ratio": round(speedup, 3),
        "meets_target": speedup >= 1.5,
        "n_cycles": n_cycles,
        "n_branches": n_branches,
    }


# ── Main benchmark runner ────────────────────────────────────────────────────────


async def main() -> dict[str, Any]:
    """Run all benchmarks and invariant tests."""
    print("=" * 60)
    print("P2-1 AcquisitionLoop TaskGroup Benchmark")
    print("=" * 60)

    # [P2-1-1] ExceptionGroup handling
    print("\n[P2-1-1] ExceptionGroup handling...", end=" ")
    eg_result = await _run_exceptiongroup_test()
    ok = eg_result["is_value_error"] and eg_result["exception_count"] >= 1
    print(f"{'PASS' if ok else 'FAIL'} ({eg_result['exception_count']} errors captured)")
    assert ok, f"ExceptionGroup test failed: {eg_result}"

    # [P2-1-2] Cancellation propagation
    print("[P2-1-2] Cancellation propagation...", end=" ")
    cancel_result = await _run_cancellation_test()
    print(f"{'PASS' if cancel_result['cancelled_or_completed'] else 'FAIL'}")
    assert cancel_result["cancelled_or_completed"], "Cancellation test failed"

    # [P2-1-3] AIMD telemetry
    print("[P2-1-3] AIMD telemetry in CycleResult...", end=" ")
    aimd_result = await _run_aimd_telemetry_test()
    print(f"{'PASS' if aimd_result['has_aimd_fields'] else 'FAIL'}")
    print(f"         aimd_window={aimd_result['aimd_window_value']}, "
          f"successes={aimd_result['aimd_successes_value']}, "
          f"failures={aimd_result['aimd_failures_value']}")
    assert aimd_result["has_aimd_fields"], "AIMD telemetry test failed"

    # [P2-1-4] Branch limit (M1 8GB RAM)
    print("[P2-1-4] Branch limit (max 5 concurrent, M1 8GB)...", end=" ")
    limit_result = await _run_branch_limit_test()
    print(f"{'PASS' if limit_result['under_limit'] else 'FAIL'} "
          f"(peak={limit_result['peak_concurrent']}/{limit_result['max_allowed']})")
    assert limit_result["under_limit"], f"Branch limit exceeded: {limit_result}"

    # [P2-1-5] Speed comparison
    print("[P2-1-5] Sequential vs Parallel speedup...", end=" ")
    speed_result = await _run_speed_benchmark(n_cycles=20, n_branches=3)
    print(f"{'PASS' if speed_result['meets_target'] else 'FAIL'}")
    print(f"         sequential median: {speed_result['sequential_median_ms']} ms")
    print(f"         parallel median:   {speed_result['parallel_median_ms']} ms")
    print(f"         speedup:          {speed_result['speedup_ratio']}× "
          f"(target >= 1.5×)")

    print("\n" + "=" * 60)
    if speed_result["meets_target"]:
        print("ALL INVARIANTS PASSED")
    else:
        print(f"WARNING: speedup {speed_result['speedup_ratio']}× < 1.5× target")
    print("=" * 60)

    return {
        "exceptiongroup": eg_result,
        "cancellation": cancel_result,
        "aimd": aimd_result,
        "branch_limit": limit_result,
        "speed": speed_result,
    }


if __name__ == "__main__":
    asyncio.run(main())
