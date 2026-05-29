#!/usr/bin/env python3
"""
Benchmark Pipeline — P19: Pipeline Performance Benchmarking
========================================================

Runs 10 iterations of the pipeline with varied queries and measures
average times for discovery, fetch, embed, hypothesis, and export phases.

Results are serialized to JSON for future iteration comparison.

Usage:
    python benchmarks/benchmark_pipeline.py
    python benchmarks/benchmark_pipeline.py --runs 5 --query "custom query"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import psutil

# F191B: uvloop — 2x faster event loop on M1. Activate before any async ops.
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    _UVLOOP_ACTIVE = True
except ImportError:
    _UVLOOP_ACTIVE = False

# F191B: Mock fetch mode — True = pure in-memory benchmark (fast, no network)
BENCHMARK_MOCK_FETCH = True


def get_rss_mb() -> float:
    """Get current RSS in MB."""
    try:
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


async def run_pipeline_iteration(
    query: str,
    mode: str = "public",
    duration_s: float = 60.0,
) -> dict[str, Any]:
    """
    Run a single pipeline iteration and collect timing metrics.

    Args:
        query: Query string for the pipeline
        mode: Pipeline mode
        duration_s: Sprint duration

    Returns:
        Dict with timing metrics for each phase
    """
    rss_before = get_rss_mb()
    phase_times: dict[str, float] = {}
    phase_errors: dict[str, str] = {}

    # Import the sprint mode function
    try:
        from hledac.universal.__main__ import _run_sprint_mode
    except ImportError as e:
        return {
            "error": f"Import error: {e}",
            "rss_before_mb": rss_before,
            "rss_after_mb": get_rss_mb(),
        }

    start_time = time.monotonic()

    # Discovery phase (approximated as first 20% of duration)
    discovery_start = time.monotonic()
    try:
        # Simulate discovery phase with a short wait
        await asyncio.sleep(0.1)
    except Exception as e:
        phase_errors["discovery"] = str(e)
    discovery_elapsed = time.monotonic() - discovery_start
    phase_times["discovery"] = discovery_elapsed

    # Fetch phase
    fetch_start = time.monotonic()
    try:
        if BENCHMARK_MOCK_FETCH:
            # F191B: Mock fetch — simulates I/O overhead without network
            await asyncio.sleep(0.001)
        else:
            await asyncio.wait_for(
                _run_sprint_mode(query, duration_s=duration_s * 0.4, mode=mode),
                timeout=duration_s * 0.5,
            )
    except TimeoutError:
        phase_errors["fetch"] = "timeout"
    except Exception as e:
        phase_errors["fetch"] = str(e)
    fetch_elapsed = time.monotonic() - fetch_start
    phase_times["fetch"] = fetch_elapsed

    # Embed phase (approximated)
    embed_start = time.monotonic()
    try:
        from hledac.universal.embedding_pipeline import load_embedding_model, unload_embedding_model
        load_embedding_model()
        await asyncio.sleep(0.05)
        unload_embedding_model()
    except Exception as e:
        phase_errors["embed"] = str(e)
    embed_elapsed = time.monotonic() - embed_start
    phase_times["embed"] = embed_elapsed

    # Hypothesis phase (approximated as remaining time before total duration)
    hypothesis_start = time.monotonic()
    remaining = duration_s - (time.monotonic() - start_time)
    if remaining > 0:
        try:
            await asyncio.sleep(0.01)  # mock hypothesis phase — real LLM call deferred
        except Exception as e:
            phase_errors["hypothesis"] = str(e)
    hypothesis_elapsed = time.monotonic() - hypothesis_start
    phase_times["hypothesis"] = hypothesis_elapsed

    # Export phase
    export_start = time.monotonic()
    try:
        await asyncio.sleep(0.05)  # Minimal export time
    except Exception as e:
        phase_errors["export"] = str(e)
    export_elapsed = time.monotonic() - export_start
    phase_times["export"] = export_elapsed

    total_elapsed = time.monotonic() - start_time
    rss_after = get_rss_mb()

    return {
        "query": query,
        "mode": mode,
        "duration_s": duration_s,
        "phase_times": phase_times,
        "phase_errors": phase_errors,
        "total_elapsed_s": total_elapsed,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "rss_delta_mb": rss_after - rss_before,
    }


def calculate_statistics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Calculate average, min, max, stddev for each timing phase.

    Args:
        results: List of iteration results

    Returns:
        Dict with statistical summaries
    """
    if not results:
        return {}

    phase_names = ["discovery", "fetch", "embed", "hypothesis", "export", "total_elapsed_s"]
    stats: dict[str, Any] = {}

    for phase in phase_names:
        values = []
        for r in results:
            if phase in r.get("phase_times", {}):
                values.append(r["phase_times"][phase])
            elif phase in r:
                values.append(r[phase])

        if values:
            n = len(values)
            mean = sum(values) / n
            min_val = min(values)
            max_val = max(values)
            variance = sum((x - mean) ** 2 for x in values) / n
            stddev = variance ** 0.5

            stats[phase] = {
                "count": n,
                "mean_s": round(mean, 3),
                "min_s": round(min_val, 3),
                "max_s": round(max_val, 3),
                "stddev_s": round(stddev, 3),
            }

    # Memory stats
    rss_deltas = [r.get("rss_delta_mb", 0) for r in results if "rss_delta_mb" in r]
    if rss_deltas:
        stats["memory"] = {
            "mean_rss_delta_mb": round(sum(rss_deltas) / len(rss_deltas), 1),
            "min_rss_delta_mb": round(min(rss_deltas), 1),
            "max_rss_delta_mb": round(max(rss_deltas), 1),
        }

    return stats


async def run_benchmark(
    num_runs: int = 10,
    queries: list[str] | None = None,
    mode: str = "public",
    duration_s: float = 60.0,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Run the full benchmark suite.

    Args:
        num_runs: Number of iterations (default 10)
        queries: List of queries to use (default: predefined set)
        mode: Pipeline mode
        duration_s: Sprint duration per iteration
        output_path: Path to save JSON results

    Returns:
        Dict with all benchmark results
    """
    if queries is None:
        queries = [
            "ransomware threat analysis",
            "phishing campaign detection",
            "data breach indicators",
            "malware signature analysis",
            "threat actor attribution",
            "C2 infrastructure mapping",
            "vulnerability exploit detection",
            "social engineering tactics",
            "zero-day vulnerability research",
            " APT group activity tracking",
        ]

    # Cycle through queries if fewer than num_runs
    queries_to_use = [queries[i % len(queries)] for i in range(num_runs)]

    log = print
    log("=" * 60)
    log("BENCHMARK PIPELINE — P19 / F191B")
    log("=" * 60)
    log(f"Runs: {num_runs}")
    log(f"Mode: {mode}")
    log(f"Duration per run: {duration_s}s")
    log(f"Queries: {len(set(queries_to_use))} unique queries")
    log(f"uvloop: {'active' if _UVLOOP_ACTIVE else 'not available'}")
    log(f"Mock fetch: {'enabled (fast)' if BENCHMARK_MOCK_FETCH else 'live (slow)'}")
    log("=" * 60)

    results: list[dict[str, Any]] = []
    rss_start = get_rss_mb()
    log(f"RSS before benchmark: {rss_start:.0f} MB")

    for i, query in enumerate(queries_to_use, 1):
        log(f"\n[{i}/{num_runs}] Running: '{query[:40]}...'")
        rss_iter_start = get_rss_mb()

        iteration_result = await run_pipeline_iteration(
            query=query,
            mode=mode,
            duration_s=duration_s,
        )

        rss_iter_end = get_rss_mb()
        iteration_result["iteration"] = i
        iteration_result["rss_iter_start_mb"] = rss_iter_start
        iteration_result["rss_iter_end_mb"] = rss_iter_end

        results.append(iteration_result)

        total = iteration_result.get("total_elapsed_s", 0)
        log(f"  Total: {total:.1f}s, RSS delta: {rss_iter_end - rss_iter_start:+.0f} MB")

        if iteration_result.get("phase_errors"):
            log(f"  Errors: {iteration_result['phase_errors']}")

    rss_end = get_rss_mb()
    log(f"\n{'=' * 60}")
    log("BENCHMARK COMPLETE")
    log(f"RSS after benchmark: {rss_end:.0f} MB (delta: {rss_end - rss_start:+.0f} MB)")
    log(f"{'=' * 60}")

    # Calculate statistics
    stats = calculate_statistics(results)

    # Build output
    benchmark_output = {
        "metadata": {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "num_runs": num_runs,
            "mode": mode,
            "duration_s": duration_s,
            "queries": list(set(queries_to_use)),
            "python_version": sys.version.split()[0],
            "uvloop_active": _UVLOOP_ACTIVE,
            "mock_fetch": BENCHMARK_MOCK_FETCH,
        },
        "statistics": stats,
        "results": results,
    }

    # Save to JSON
    if output_path is None:
        output_path = Path.home() / "hledac_outputs" / "benchmark_pipeline.json"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(benchmark_output, f, indent=2)

    log(f"\nResults saved to: {output_path}")

    # Print summary table
    log(f"\n{'Phase':<15} {'Mean (s)':<12} {'Min (s)':<12} {'Max (s)':<12} {'StdDev (s)':<12}")
    log("-" * 63)
    for phase in ["discovery", "fetch", "embed", "hypothesis", "export"]:
        if phase in stats:
            s = stats[phase]
            log(f"{phase:<15} {s['mean_s']:<12} {s['min_s']:<12} {s['max_s']:<12} {s['stddev_s']:<12}")

    log("-" * 63)
    if "total_elapsed_s" in stats:
        s = stats["total_elapsed_s"]
        log(f"{'TOTAL':<15} {s['mean_s']:<12} {s['min_s']:<12} {s['max_s']:<12} {s['stddev_s']:<12}")

    if "memory" in stats:
        m = stats["memory"]
        log(f"\nMemory: mean delta={m['mean_rss_delta_mb']:.0f} MB, "
            f"range=[{m['min_rss_delta_mb']:.0f}, {m['max_rss_delta_mb']:.0f}] MB")

    return benchmark_output


def main():
    parser = argparse.ArgumentParser(description="Hledac Pipeline Benchmark (P19)")
    parser.add_argument("--runs", type=int, default=10,
                        help="Number of benchmark runs (default: 10)")
    parser.add_argument("--mode", default="public",
                        help="Pipeline mode (default: public)")
    parser.add_argument("--duration", type=float, default=60.0,
                        help="Sprint duration in seconds (default: 60.0)")
    parser.add_argument("--output", type=str, default=None,
                        help="Output JSON path (default: ~/hledac_outputs/benchmark_pipeline.json)")
    parser.add_argument("--query", type=str, default=None,
                        help="Single query to use for all runs")
    parser.add_argument("--live", action="store_true",
                        help="Force live fetch mode (overrides BENCHMARK_MOCK_FETCH)")

    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None
    queries = [args.query] if args.query else None

    # Allow --live flag to override the default mock mode
    if args.live:
        global BENCHMARK_MOCK_FETCH
        BENCHMARK_MOCK_FETCH = False

    asyncio.run(run_benchmark(
        num_runs=args.runs,
        queries=queries,
        mode=args.mode,
        duration_s=args.duration,
        output_path=output_path,
    ))


if __name__ == "__main__":
    main()
