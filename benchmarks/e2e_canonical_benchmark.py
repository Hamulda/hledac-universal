#!/usr/bin/env python3
"""
Sprint F205E: Hermetic E2E Canonical Benchmark
=============================================

Benchmarks the canonical F204/F205 pipeline metrics:
- findings/minute (throughput)
- dedup_ratio (store acceptance rate)
- sidecar_total_ms (wall-clock bus execution)
- per_sidecar_ms (per-runner elapsed_ms from SidecarRunResult)
- peak_rss_mb (memory ceiling check)

Hermetic mode: no network, no MLX hardware, no model loading.
Synthetic CanonicalFinding-like objects + mock async_ingest_findings_batch.

Usage:
    python benchmarks/e2e_canonical_benchmark.py --hermetic --runs 3
    python benchmarks/e2e_canonical_benchmark.py --hermetic --runs 3 --output /tmp/bench.json
    python benchmarks/e2e_canonical_benchmark.py --runs 3    # live mode (requires full env)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time as _time

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    _UVLOOP_ACTIVE = True
except ImportError:
    _UVLOOP_ACTIVE = False

# ── Bounds ────────────────────────────────────────────────────────────────────
HERMETIC_DEFAULT_RUNS: int = 3
HERMETIC_MAX_FINDINGS: int = 200
HERMETIC_SIDECAR_COUNT: int = 11  # number of sidecar runners in bus
SYNTHETIC_ACCEPT_RATE: float = 0.70  # ~70% of synthetic findings pass quality gate
SIDECAR_LIGHT_LOAD_MS: float = 5.0  # synthetic delay per runner (matches light sidecar profile)

# M1 8GB RSS ceiling — benchmark fails if exceeded
M1_8GB_CEILING_MB: float = 6.5 * 1024


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_rss_mb() -> float:
    try:
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


def _make_synthetic_finding(index: int, query: str = "benchmark query") -> dict[str, Any]:
    """Create a synthetic CanonicalFinding-like dict for hermetic bench."""
    return {
        "finding_id": f"bench-f205e-{index:04d}",
        "query": query,
        "source_type": "ct_log",
        "confidence": 0.5 + (index % 10) * 0.05,
        "ts": _time.time(),
        "provenance": (f"hermetic_bench::{index}",),
        "payload_text": json.dumps({
            "triage": {"title": f" synthetic finding {index}", "author": "bench"},
            "ioc_type": "domain",
            "ioc_value": f"example-{index:04d}.com",
        }),
        "ioc_type": "domain",
        "ioc_value": f"example-{index:04d}.com",
    }


# ── Mock store for hermetic mode ────────────────────────────────────────────────

class MockTargetMemory:
    """Synthetic TargetMemory for hermetic benchmark."""
    def __init__(self, target_id: str, sprint_count: int = 1,
                 cumulative_finding_count: int = 0) -> None:
        self.target_id = target_id
        self.first_seen_ts = 0.0
        self.last_seen_ts = 0.0
        self.sprint_count = sprint_count
        self.cumulative_finding_count = cumulative_finding_count
        self.entity_facets = {}
        self.exposure_facets = {}
        self.pivot_facets = {}
        self.confidence_drift = {"sprints": 1, "total_findings": 0,
                                  "avg_findings_per_sprint": 0.0, "drift_ratio": 1.0}
        self.updated_by_sprint_id = "hermetic"


class MockDuckDBStore:
    """
    Hermetic mock of DuckDBShadowStore for benchmarking.
    Simulates async_ingest_findings_batch quality gate with SYNTHETIC_ACCEPT_RATE.
    All other store methods are no-ops.
    """

    def __init__(self, accept_rate: float = SYNTHETIC_ACCEPT_RATE) -> None:
        self._accept_rate = accept_rate
        self._stored: list[dict[str, Any]] = []
        self._seen_ids: set[str] = set()
        self._total_submitted: int = 0
        self._total_accepted: int = 0

    async def async_initialize(self) -> None:
        pass

    async def async_ingest_findings_batch(
        self, findings: list[Any]
    ) -> list[dict[str, Any]]:
        """
        Simulate quality gate: SYNTHETIC_ACCEPT_RATE pass-through.
        Tracks dedup via _seen_ids (first-seen = accepted, repeat = rejected).
        Returns list of {accepted: bool, finding_id: str} dicts.
        """
        import random
        results: list[dict[str, Any]] = []
        accepted_this_batch = 0

        for f in findings:
            fid = getattr(f, "finding_id", None) or (f.get("finding_id") if isinstance(f, dict) else None)
            fid = fid or f"unknown-{id(f)}"
            self._total_submitted += 1

            if fid in self._seen_ids:
                # Duplicate — rejected by semantic dedup
                results.append({"accepted": False, "finding_id": fid, "reason": "duplicate"})
            elif random.random() < self._accept_rate:
                self._seen_ids.add(fid)
                self._total_accepted += 1
                accepted_this_batch += 1
                results.append({"accepted": True, "finding_id": fid})
                # Store a copy
                fdict = dict(f) if isinstance(f, dict) else {
                    k: getattr(f, k, None) for k in (
                        "finding_id", "query", "source_type", "confidence",
                        "ts", "provenance", "payload_text", "ioc_type", "ioc_value",
                    )
                }
                self._stored.append(fdict)
            else:
                results.append({"accepted": False, "finding_id": fid, "reason": "quality_gate"})

        return results

    async def async_get_recent_findings(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._stored[-limit:]

    async def async_get_target_memory(self, target_id: str) -> MockTargetMemory | None:
        """
        F205J: Return synthetic target memory for hermetic benchmark.
        Returns a MockTargetMemory keyed by target_id for the query.
        """
        if not target_id:
            return None
        # Simulate cross-sprint memory: 2 prior sprints with accumulated findings
        stored = len(self._stored)
        return MockTargetMemory(
            target_id=target_id,
            sprint_count=2,
            cumulative_finding_count=stored * 2,
        )

    async def aclose(self) -> None:
        self._stored.clear()
        self._seen_ids.clear()


# ── Light hermetic sidecar runners ─────────────────────────────────────────────

async def _light_runner(
    findings: list,
    store: MockDuckDBStore,
    query: str,
    *,
    delay_ms: float = SIDECAR_LIGHT_LOAD_MS,
) -> None:
    """
    Light hermetic runner: simulates realistic light sidecar work.
    No network, no MLX, no model loading — just bounded async sleep.
    Calls store.async_ingest_findings_batch() so the mock quality gate is exercised.
    """
    if not findings:
        return
    # Simulate sidecar processing: ingest findings through the store's mock quality gate
    await store.async_ingest_findings_batch(findings)
    await asyncio.sleep(delay_ms / 1000.0)


def _make_light_runner(delay_ms: float = SIDECAR_LIGHT_LOAD_MS):
    """Factory for light hermetic runner with configurable delay."""
    async def runner(findings: list, store: MockDuckDBStore, query: str) -> None:
        await _light_runner(findings, store, query, delay_ms=delay_ms)
    return runner


# ── Hermetic benchmark ─────────────────────────────────────────────────────────

async def _run_hermetic_benchmark(
    num_findings: int = HERMETIC_MAX_FINDINGS,
    runs: int = HERMETIC_DEFAULT_RUNS,
) -> dict[str, Any]:
    """
    Run hermetic benchmark: synthetic findings + mock store + light sidecar runners.
    Measures sidecar bus throughput and memory independently of network/MLX.

    F205J: Also runs analyst brief generation to validate target memory integration.
    """
    from hledac.universal.runtime.sidecar_bus import (
        FindingSidecarBus,
        SidecarBatch,
        SIDECAR_STAGES,
    )

    run_metrics: list[dict[str, Any]] = []
    target_memory_summary_present = False
    analyst_brief_includes_memory = False

    # Pre-generate synthetic findings (shared across runs for dedup measurement)
    base_findings = [_make_synthetic_finding(i) for i in range(num_findings)]
    findings_list: list[dict[str, Any]] = base_findings

    # Canonical query/target_id for hermetic analyst brief
    hermetic_query = "hermetic benchmark query"
    hermetic_target_id = hermetic_query  # query IS the canonical target

    for run_idx in range(runs):
        store = MockDuckDBStore(accept_rate=SYNTHETIC_ACCEPT_RATE)
        await store.async_initialize()

        rss_baseline = get_rss_mb()

        # Create bus with light runners
        bus: FindingSidecarBus = FindingSidecarBus(governor=None)

        # Register light hermetic runners for all SIDECAR_STAGES
        stage_names: list[str] = []
        for stage in SIDECAR_STAGES:
            for name in stage:
                if name not in stage_names:
                    stage_names.append(name)

        for name in stage_names:
            bus.register(name, _make_light_runner(delay_ms=SIDECAR_LIGHT_LOAD_MS))

        # Wrap findings in SidecarBatch
        batch = SidecarBatch(
            sprint_id=f"bench-f205e-run{run_idx}",
            query=hermetic_query,
            source_branch="ct",
            findings=tuple(findings_list),
            created_ts=_time.time(),
        )

        # Run bus — measure wall-clock
        t0 = _time.monotonic()
        results: list[Any] = await bus.run_all_sidecars(batch, store)
        sidecar_total_ms = (_time.monotonic() - t0) * 1000

        rss_peak = get_rss_mb()
        rss_delta = rss_peak - rss_baseline

        # Per-sidecar metrics
        per_sidecar: dict[str, float] = {}
        for r in results:
            per_sidecar[r.sidecar_name] = r.elapsed_ms

        # Store metrics
        accepted = store._total_accepted
        stored = len(store._stored)
        dedup_ratio = (stored / accepted) if accepted > 0 else 0.0

        # F205J: Check target memory summary availability
        mem_summary = await store.async_get_target_memory(hermetic_target_id)
        has_memory_summary = mem_summary is not None

        # F205J: Run analyst brief with target memory
        analyst_brief_has_memory = False
        try:
            from hledac.universal.knowledge.analyst_workbench import AnalystWorkbench
            workbench = AnalystWorkbench(duckdb_store=store)
            brief = await workbench.build_sprint_brief(
                sprint_id=f"bench-f205e-run{run_idx}",
                target_id=hermetic_target_id,
                findings=findings_list,
                graph_signal={"graph_nodes": 0, "graph_edges": 0},
                governor=None,
                duckdb_store=store,
            )
            # Check if brief includes memory (headline or key_findings mention "Target memory")
            brief_text = brief.headline + "".join(brief.key_findings)
            analyst_brief_has_memory = "Target memory" in brief_text or "prior sprint" in brief_text
        except Exception:
            analyst_brief_has_memory = False

        if run_idx == 0:
            target_memory_summary_present = has_memory_summary
            analyst_brief_includes_memory = analyst_brief_has_memory

        # findings/minute = (stored_findings / wall_clock_seconds) * 60
        wall_s = sidecar_total_ms / 1000.0
        findings_per_min = (stored / wall_s * 60) if wall_s > 0 else 0.0

        run_metric = {
            "run": run_idx,
            "submitted_findings": len(findings_list),
            "accepted_findings": accepted,
            "stored_findings": stored,
            "dedup_ratio": round(dedup_ratio, 4),
            "findings_per_minute": round(findings_per_min, 2),
            "sidecar_total_ms": round(sidecar_total_ms, 2),
            "per_sidecar_ms": {k: round(v, 2) for k, v in per_sidecar.items()},
            "peak_rss_mb": round(rss_peak, 1),
            "rss_delta_mb": round(rss_delta, 1),
            "memory_ceiling_ok": rss_peak < M1_8GB_CEILING_MB,
        }
        run_metrics.append(run_metric)

        await store.aclose()

    # Aggregate across runs
    n = len(run_metrics)
    avg_fpm = sum(m["findings_per_minute"] for m in run_metrics) / n
    avg_sidecar_ms = sum(m["sidecar_total_ms"] for m in run_metrics) / n
    avg_stored = sum(m["stored_findings"] for m in run_metrics) / n
    avg_accepted = sum(m["accepted_findings"] for m in run_metrics) / n
    avg_dedup = sum(m["dedup_ratio"] for m in run_metrics) / n
    avg_rss = sum(m["peak_rss_mb"] for m in run_metrics) / n
    all_memory_ok = all(m["memory_ceiling_ok"] for m in run_metrics)

    per_sidecar_agg: dict[str, dict[str, float]] = {}
    all_names = set()
    for m in run_metrics:
        all_names.update(m["per_sidecar_ms"].keys())

    for name in sorted(all_names):
        vals = [m["per_sidecar_ms"].get(name, 0.0) for m in run_metrics]
        per_sidecar_agg[name] = {
            "avg_ms": round(sum(vals) / n, 2),
            "min_ms": round(min(vals), 2),
            "max_ms": round(max(vals), 2),
        }

    return {
        "metadata": {
            "probe": "F205E",
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+0000", "Z"),
            "mode": "hermetic",
            "runs": runs,
            "uvloop_active": _UVLOOP_ACTIVE,
            "python_version": sys.version.split()[0],
            "hermetic_num_findings": num_findings,
            "hermetic_accept_rate": SYNTHETIC_ACCEPT_RATE,
            "hermetic_sidecar_load_ms": SIDECAR_LIGHT_LOAD_MS,
        },
        "runs": run_metrics,
        "aggregate": {
            "findings_per_minute": round(avg_fpm, 2),
            "dedup_ratio": round(avg_dedup, 4),
            "sidecar_total_ms": round(avg_sidecar_ms, 2),
            "stored_count": round(avg_stored, 1),
            "accepted_count": round(avg_accepted, 1),
            "peak_rss_mb": round(avg_rss, 1),
            "memory_ceiling_ok": all_memory_ok,
            "target_memory_summary_present": target_memory_summary_present,
            "analyst_brief_includes_memory": analyst_brief_includes_memory,
        },
        "per_sidecar_ms": per_sidecar_agg,
        "status": "pass" if all_memory_ok else "fail",
    }


# ── CLI entry ──────────────────────────────────────────────────────────────────

async def run_benchmark(
    hermetic: bool = True,
    runs: int = HERMETIC_DEFAULT_RUNS,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Run benchmark, return results dict. Saves to output_path if given."""
    log = print
    log("=" * 60)
    log("Sprint F205E — Hermetic E2E Canonical Benchmark")
    log("=" * 60)
    log(f"Hermetic: {hermetic} | Runs: {runs}")
    log(f"uvloop: {'active' if _UVLOOP_ACTIVE else 'not available'}")
    log(f"M1 8GB ceiling: {M1_8GB_CEILING_MB:.0f}MB")
    log("=" * 60)

    t0 = _time.monotonic()

    if hermetic:
        result = await _run_hermetic_benchmark(
            num_findings=HERMETIC_MAX_FINDINGS,
            runs=runs,
        )
    else:
        # Live mode requires full system run with real data sources.
        # For benchmark reproducibility and M1 memory stability,
        # hermetic mode (synthetic data) is the canonical benchmark path.
        log("WARNING: Live mode not available — benchmark requires --hermetic")
        log("         Use: python -m benchmarks.e2e_canonical_benchmark --hermetic")
        result = await _run_hermetic_benchmark(
            num_findings=HERMETIC_MAX_FINDINGS,
            runs=runs,
        )

    elapsed_total_s = _time.monotonic() - t0
    result["metadata"]["elapsed_total_s"] = round(elapsed_total_s, 2)

    # ── Print summary ──────────────────────────────────────────────────────────
    log(f"\n{'=' * 60}")
    log(f"BENCHMARK RESULTS — Sprint F205E")
    log(f"{'=' * 60}")
    agg = result["aggregate"]
    log(f"  findings_per_minute : {agg['findings_per_minute']}")
    log(f"  dedup_ratio          : {agg['dedup_ratio']}")
    log(f"  sidecar_total_ms    : {agg['sidecar_total_ms']}ms")
    log(f"  stored_count         : {agg['stored_count']}")
    log(f"  accepted_count      : {agg['accepted_count']}")
    log(f"  peak_rss_mb          : {agg['peak_rss_mb']}MB")
    log(f"  memory_ceiling_ok    : {agg['memory_ceiling_ok']}")
    log(f"  target_memory_summary_present : {agg['target_memory_summary_present']}")
    log(f"  analyst_brief_includes_memory : {agg['analyst_brief_includes_memory']}")
    log(f"  status               : {result['status']}")
    log(f"\n  Per-sidecar avg ms:")
    for name, stats in result["per_sidecar_ms"].items():
        log(f"    {name:28s}: {stats['avg_ms']:7.2f}ms  (min={stats['min_ms']:.2f}, max={stats['max_ms']:.2f})")
    log(f"{'=' * 60}")

    if output_path is None:
        output_path = Path.home() / "hledac_outputs" / "e2e_canonical_benchmark.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result, f, indent=2)
    log(f"\nResults saved to: {output_path}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sprint F205E — Hermetic E2E Canonical Benchmark"
    )
    parser.add_argument(
        "--hermetic",
        action="store_true",
        default=True,
        help="Hermetic mode: synthetic data, no network/MLX (default: True)",
    )
    parser.add_argument(
        "--runs", type=int, default=HERMETIC_DEFAULT_RUNS,
        help=f"Number of benchmark runs (default: {HERMETIC_DEFAULT_RUNS})",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None
    result = asyncio.run(run_benchmark(
        hermetic=args.hermetic,
        runs=args.runs,
        output_path=output_path,
    ))
    sys.exit(0 if result.get("status") == "pass" else 1)


if __name__ == "__main__":
    main()
