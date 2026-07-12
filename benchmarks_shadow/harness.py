"""
BenchmarkHarness — sprint performance measurement with warmup, latency percentiles,
RSS delta tracking, and findings_count.

Schema version 2.0 — output JSON always contains "schema_version": "2.0".
"""
import gc
import json
import random
import time
import traceback
from datetime import UTC, datetime
from math import sqrt
from pathlib import Path
from typing import Any
import psutil
try:
    import numpy as _np
    np = _np
    _NUMPY_AVAILABLE = True
except ImportError:
    _NUMPY_AVAILABLE = False

def _percentile(sorted_vals: list[float], p: float) -> float:
    """Compute the p-th percentile (0 < p <= 100) of a sorted list."""
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p / 100.0
    f = int(k)
    c = f + 1 if f + 1 < len(sorted_vals) else f
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

async def _run_single_sprint(query: str) -> dict[str, Any]:
    """
    Execute one sprint iteration and return a result dict with at least
    'latency_s' (float) and 'findings_count' (int).

    Uses ``core.__main__.run_sprint`` — the canonical sprint entry point.
    ``run_sprint`` returns None; findings are read from the latest
    JSON export written to the reports directory.
    """
    from core.__main__ import run_sprint
    start = time.monotonic()
    await run_sprint(query=query, duration_s=10, aggressive_mode=False)
    latency_s = time.monotonic() - start
    findings_count = _read_findings_count_from_latest_export()
    return {'latency_s': latency_s, 'findings_count': findings_count}

def _read_findings_count_from_latest_export() -> int:
    """Read findings_count from the most recent sprint JSON export."""
    reports_dir = Path.home() / '.hledac' / 'reports'
    if not reports_dir.is_dir():
        return 0
    try:
        json_files = sorted(reports_dir.glob('sprint_*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
        if not json_files:
            return 0
        with open(json_files[0], encoding='utf-8') as fh:
            data = json.load(fh)
        return data.get('findings_count', 0) if isinstance(data, dict) else 0
    except Exception:
        return 0

async def _run_single_sprint_unsafe(query: str) -> dict[str, Any]:
    """Wraps _run_single_sprint with try/except so one bad iter never kills the run."""
    try:
        return await _run_single_sprint(query)
    except Exception as exc:
        tb = traceback.format_exc()
        return {'latency_s': None, 'error': f'{type(exc).__name__}: {exc}\n{tb}'}

class BenchmarkHarness:
    """
    Async harness for measuring end-to-end sprint performance.

    Parameters
    ----------
    seed : int | None
        If set, seeds Python's ``random`` module and (if available) ``numpy.random``
        for reproducible iteration order.
    """
    __slots__ = tuple(('seed',))

    def __init__(self, *, seed: int | None=None) -> None:
        self.seed = seed
        if seed is not None:
            random.seed(seed)
            if _NUMPY_AVAILABLE:
                np.random.seed(seed)

    async def run(self, *, warmup: int=3, iterations: int=30, query: str, output_path: Path) -> dict[str, Any]:
        """
        Run the benchmark loop and write results to ``output_path`` as JSON.

        Parameters
        ----------
        warmup : int
            Number of warm-up sprints that are excluded from latency percentiles.
        iterations : int
            Total sprint iterations (including warmup).
        query : str
            Sprint query string.
        output_path : Path
            Destination file for the JSON report.

        Returns
        -------
        dict
            The full JSON report (same structure as written to ``output_path``).
        """
        if warmup < 0:
            raise ValueError('warmup must be >= 0')
        if iterations <= 0:
            raise ValueError('iterations must be > 0')
        if warmup >= iterations:
            raise ValueError('warmup must be < iterations')
        process = psutil.Process()
        for _ in range(warmup):
            await _run_single_sprint_unsafe(query)
            gc.collect()
        raw_results: list[dict[str, Any]] = []
        for i in range(iterations):
            iter_rss_before = process.memory_info().rss / 1024 / 1024
            iter_start = time.monotonic()
            result = await _run_single_sprint_unsafe(query)
            iter_wall = time.monotonic() - iter_start
            iter_rss_after = process.memory_info().rss / 1024 / 1024
            rss_delta = iter_rss_after - iter_rss_before
            row: dict[str, Any] = {'iteration': i + 1, 'latency_s': iter_wall, 'rss_delta_mb': rss_delta}
            if result.get('findings_count') is not None:
                row['findings_count'] = result['findings_count']
            if result.get('error'):
                row['error'] = result['error']
                row['latency_s'] = None
            raw_results.append(row)
            gc.collect()
        measured = [r for r in raw_results if r.get('latency_s') is not None]

        def _stats(vals: list[float]) -> dict[str, float]:
            if not vals:
                return {'p50': 0.0, 'p95': 0.0, 'p99': 0.0, 'mean': 0.0, 'std': 0.0, 'min': 0.0, 'max': 0.0}
            s = sorted(vals)
            n = len(s)
            mean = sum(s) / n
            std = sqrt(sum(((x - mean) ** 2 for x in s)) / n) if n > 1 else 0.0
            return {'p50': _percentile(s, 50), 'p95': _percentile(s, 95), 'p99': _percentile(s, 99), 'mean': mean, 'std': std, 'min': s[0], 'max': s[-1]}
        latency_vals = [r['latency_s'] for r in measured]
        rss_vals = [r['rss_delta_mb'] for r in measured]
        findings_vals = [r.get('findings_count', 0) for r in measured if 'findings_count' in r]
        report: dict[str, Any] = {'schema_version': '2.0', 'timestamp': datetime.now(UTC).isoformat(), 'params': {'warmup': warmup, 'iterations': iterations, 'query': query, 'seed': self.seed}, 'latency_s': _stats(latency_vals), 'rss_delta_mb': _stats(rss_vals), 'findings_count': _stats(findings_vals) if findings_vals else {'p50': 0.0, 'p95': 0.0, 'p99': 0.0, 'mean': 0.0, 'std': 0.0, 'min': 0.0, 'max': 0.0}, 'iterations_detail': raw_results, 'error_count': sum((1 for r in raw_results if r.get('error')))}
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
        return report