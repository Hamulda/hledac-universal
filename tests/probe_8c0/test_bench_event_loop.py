"""
Sprint 8C0 Benchmark 3: Event Loop Lag

Measures:
- event loop lag at 100 and 500 concurrent coroutine workload
- default asyncio loop baseline
- uvloop comparison if available

Reports:
- DEFAULT_LOOP_ONLY if uvloop not active
- lag_ms median/p95
- queue throughput (optional)

This is an OFFLINE benchmark — no network calls.
"""

import asyncio
import gc
import sys
import time
import unittest
from pathlib import Path
from typing import List, Tuple

PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.bench_8c0.common_stats import (
    build_result,
    check_uvloop,
    write_results,
)

from utils.async_helpers import safe_gather_fire_and_forget

# ---------------------------------------------------------------------------
# Workload generators
# ---------------------------------------------------------------------------

async def cpu_light_task(task_id: int, queue: asyncio.Queue, results: List[float]):
    """A light I/O-style coroutine that yields control."""
    start = time.perf_counter_ns()
    # Simulate a tiny bit of async work
    await asyncio.sleep(0)
    elapsed = time.perf_counter_ns() - start
    results.append(elapsed / 1_000_000)  # ms
    await queue.put(task_id)


async def run_coroutine_workload(n_tasks: int) -> Tuple[List[float], int]:
    """
    Launch `n_tasks` coroutines concurrently and measure their completion.
    Returns (list of individual task durations in ms, total_ns).
    """
    queue: asyncio.Queue = asyncio.Queue()
    results: List[float] = []

    async def worker(tid: int):
        await cpu_light_task(tid, queue, results)

    # Launch all tasks
    start = time.perf_counter_ns()
    await safe_gather_fire_and_forget(*[worker(i) for i in range(n_tasks)], label="test_bench_event_loop:62")
    total_ns = time.perf_counter_ns() - start

    return results, total_ns


def measure_loop_overhead(n_tasks: int, n_runs: int = 3) -> List[float]:
    """
    Measure event loop lag by launching concurrent tasks and measuring
    how long the total run takes vs sum of individual tasks.
    Returns list of total-durations (ms) per run.
    """
    durations: List[float] = []

    for _ in range(n_runs):
        # Reset GC
        gc.collect()

        async def _run():
            _, total_ns = await run_coroutine_workload(n_tasks)
            return total_ns

        total_ms = asyncio.run(_run()) / 1_000_000
        durations.append(total_ms)

    return durations


# ---------------------------------------------------------------------------
# Benchmark tests
# ---------------------------------------------------------------------------

class TestEventLoopBenchmark(unittest.TestCase):
    """
    Event loop lag benchmark.
    """

    def test_uvloop_availability(self):
        """Check if uvloop is available."""
        uvloop_available = check_uvloop()
        result = {
            "benchmark": "event_loop_uvloop_check",
            "status": "PASS",
            "reason": None,
            "n": 1,
            "warmup": 0,
            "min": 0.0,
            "median": 0.0,
            "p95": 0.0,
            "max": 0.0,
            "unit": "boolean",
            "fixtures": [],
            "seed": None,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "extra": {
                "uvloop_available": uvloop_available,
                "loop_type": "uvloop" if uvloop_available else "asyncio.DefaultEventLoop",
            },
        }

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "event_loop_uvloop_check.jsonl"
        write_results([result], output_path)

        # If uvloop not available, report that future comparison is possible
        if not uvloop_available:
            result_default = build_result(
                benchmark="event_loop_lag_100c",
                durations_ms=[],
                warmup=0,
                unit="ms",
                fixtures=[],
                status="UNAVAILABLE_WITH_REASON",
                reason="uvloop not installed — DEFAULT_LOOP_ONLY; hook prepared for future compare",
                extra={"loop_type": "asyncio.DefaultEventLoop"},
            )
            output_path2 = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "event_loop_lag_100c.jsonl"
            write_results([result_default], output_path2)

    def test_event_loop_lag_100_coroutines(self):
        """
        Measure event loop lag with 100 concurrent coroutines.
        """
        gc.collect()

        async def _measure_100():
            # Warmup
            await run_coroutine_workload(50)
            # Measure
            n_runs = 5
            all_results: List[float] = []
            for _ in range(n_runs):
                _, total_ns = await run_coroutine_workload(100)
                all_results.append(total_ns / 1_000_000)
            return all_results

        durations_ms = asyncio.run(_measure_100())

        result = build_result(
            benchmark="event_loop_lag_100c",
            durations_ms=durations_ms,
            warmup=1,
            unit="ms",
            fixtures=["stdlib asyncio"],
            status="PASS",
            extra={
                "n_tasks": 100,
                "n_runs": len(durations_ms),
                "loop_type": "asyncio.DefaultEventLoop (uvloop N/A)",
            },
        )

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "event_loop_lag_100c.jsonl"
        write_results([result], output_path)

        self.assertGreater(result["median"], 0)

    def test_event_loop_lag_500_coroutines(self):
        """
        Measure event loop lag with 500 concurrent coroutines.
        """
        gc.collect()

        async def _measure_500():
            # Warmup
            await run_coroutine_workload(100)
            # Measure
            n_runs = 3
            all_results: List[float] = []
            for _ in range(n_runs):
                _, total_ns = await run_coroutine_workload(500)
                all_results.append(total_ns / 1_000_000)
            return all_results

        durations_ms = asyncio.run(_measure_500())

        result = build_result(
            benchmark="event_loop_lag_500c",
            durations_ms=durations_ms,
            warmup=1,
            unit="ms",
            fixtures=["stdlib asyncio"],
            status="PASS",
            extra={
                "n_tasks": 500,
                "n_runs": len(durations_ms),
                "loop_type": "asyncio.DefaultEventLoop (uvloop N/A)",
            },
        )

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "event_loop_lag_500c.jsonl"
        write_results([result], output_path)

        self.assertGreater(result["median"], 0)

    def test_asyncio_queue_throughput(self):
        """
        Measure asyncio.Queue put/get throughput as an independent metric.
        """
        gc.collect()

        async def _measure_queue():
            q: asyncio.Queue = asyncio.Queue()
            n_ops = 10_000

            # Warmup
            for i in range(100):
                await q.put(i)
            for i in range(100):
                await q.get()

            # Measure put+get pairs
            start = time.perf_counter_ns()
            for i in range(n_ops):
                await q.put(i)
                await q.get()
            total_ns = time.perf_counter_ns() - start

            return total_ns / 1_000_000  # ms for all ops

        n_runs = 3
        durations: List[float] = []
        for _ in range(n_runs):
            durations.append(asyncio.run(_measure_queue()))

        ops_per_ms = (10_000 * 2) / durations[0] if durations[0] > 0 else 0

        result = build_result(
            benchmark="event_loop_queue_throughput",
            durations_ms=durations,
            warmup=1,
            unit="ms per 10k put+get pairs",
            fixtures=["stdlib asyncio.Queue"],
            status="PASS",
            extra={
                "ops_per_ms": round(ops_per_ms, 2),
            },
        )

        output_path = PROJECT_ROOT / "tests" / "probe_8c0" / "results" / "event_loop_queue_throughput.jsonl"
        write_results([result], output_path)

        self.assertGreater(result["median"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
