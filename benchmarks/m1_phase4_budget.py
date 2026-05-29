#!/usr/bin/env python3
"""
benchmarks/m1_phase4_budget.py — F204J: M1 Mission Budget Benchmark
====================================================================

Hermetic benchmark that verifies peak RSS <= 5.5 GiB without model loaded.

Measures:
- Peak RSS during sidecar admission checks
- Governor sidecar_admission() behavior
- Streaming embedder fallback chunking

Usage:
    python benchmarks/m1_phase4_budget.py --hermetic
    python benchmarks/m1_phase4_budget.py --hermetic --output /tmp/budget.json

Definition of Done:
    python benchmarks/m1_phase4_budget.py --hermetic
    → peak_rss_gib <= 5.5 (M1 8GB mission budget)

GHOST_INVARIANTS enforced:
- asyncio.gather always with return_exceptions=True (if used)
- _check_gathered() after every gather
- asyncio.CancelledError re-raised
- Benchmark is sync CLI (no event loop blocking)
- Fail-soft: budget sampler failure → safe degraded mode
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

# M1 8GB mission budget ceiling
MISSION_PEAK_RSS_GIB = 5.5


def get_rss_gib() -> float:
    """Get current process RSS in GiB."""
    try:
        return psutil.Process().memory_info().rss / (1024**3)
    except Exception:
        return 0.0


@dataclass
class BudgetBenchmarkResult:
    """Result of the M1 mission budget benchmark."""
    status: str  # "pass" | "fail" | "swap_detected"
    peak_rss_gib: float
    rss_samples: int
    sidecar_admission_checks: int
    sidecars_blocked: int
    embedding_fallback_chunked: bool
    timestamp: str


def _simulate_sidecar_admission(governor: Any, names: list[str]) -> tuple[int, int]:
    """
    Simulate sidecar admission checks.
    Returns (checks, blocked).
    """
    checks = 0
    blocked = 0
    for name in names:
        try:
            admission = governor.sidecar_admission(name, 128)
            checks += 1
            if not admission.allowed:
                blocked += 1
        except Exception:
            pass
    return checks, blocked


def _simulate_embedding_fallback(embedder: Any, findings: list) -> bool:
    """
    Simulate embedding fallback to verify it chunks.
    Returns True if fallback yields in chunks (not all at once).
    """
    import asyncio

    chunked_yields = 0
    total_items = 0

    async def _iterate():
        nonlocal chunked_yields, total_items
        async for ids, _embs in embedder.embed_findings(findings, batch_size=16):
            chunked_yields += 1
            total_items += len(ids)

    try:
        asyncio.get_event_loop().run_until_complete(_iterate())
    except Exception:
        return False

    # If chunked properly, we should get multiple yields for large input
    # (not just one big batch)
    return chunked_yields > 1 or total_items == 0


def run_budget_benchmark() -> BudgetBenchmarkResult:
    """
    Run hermetic M1 mission budget benchmark.

    No real network, no real MLX inference — only measures:
    - Peak RSS during simulated sidecar processing
    - Sidecar admission check behavior
    - Embedding fallback chunking
    """
    from hledac.universal.intelligence.streaming_embedder import StreamingEmbedder
    from hledac.universal.runtime.resource_governor import (
        HEAVY_SIDECARS,
        MISSION_PEAK_RSS_GIB,
        get_governor,
    )

    governor = get_governor()
    rss_start_gib = get_rss_gib()
    rss_peak_gib = rss_start_gib
    samples = 0
    admission_checks = 0
    sidecars_blocked = 0

    # Simulate sidecar admission checks
    heavy_names = list(HEAVY_SIDECARS)
    for _ in range(10):
        checks, blocked = _simulate_sidecar_admission(governor, heavy_names)
        admission_checks += checks
        sidecars_blocked += blocked

        rss_gib = get_rss_gib()
        if rss_gib > rss_peak_gib:
            rss_peak_gib = rss_gib
        samples += 1

    # Simulate embedding fallback with chunking
    class _MockFinding:
        __slots__ = ("finding_id", "payload_text")
        def __init__(self, fid: str, text: str):
            self.finding_id = fid
            self.payload_text = text

    mock_findings = [
        _MockFinding(f"f{i}", f"test text content {i}" * 50)
        for i in range(100)
    ]

    embedder = StreamingEmbedder()
    embedding_chunked = _simulate_embedding_fallback(embedder, mock_findings)

    # Final RSS check
    final_rss = get_rss_gib()
    if final_rss > rss_peak_gib:
        rss_peak_gib = final_rss

    # Determine status
    status = "pass"
    if rss_peak_gib > MISSION_PEAK_RSS_GIB:
        status = "fail"

    return BudgetBenchmarkResult(
        status=status,
        peak_rss_gib=round(rss_peak_gib, 3),
        rss_samples=samples,
        sidecar_admission_checks=admission_checks,
        sidecars_blocked=sidecars_blocked,
        embedding_fallback_chunked=embedding_chunked,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _format_summary(result: BudgetBenchmarkResult) -> str:
    """Format benchmark result as human-readable summary."""
    lines = [
        "=" * 60,
        "M1 Mission Budget Benchmark — F204J Hermetic",
        "=" * 60,
        f"  Status:                     {result.status}",
        f"  Peak RSS:                   {result.peak_rss_gib:.3f} GiB",
        f"  Mission Ceiling:            {MISSION_PEAK_RSS_GIB} GiB",
        f"  RSS Samples:                {result.rss_samples}",
        f"  Sidecar Admission Checks:    {result.sidecar_admission_checks}",
        f"  Sidecars Blocked:           {result.sidecars_blocked}",
        f"  Embedding Fallback Chunked: {result.embedding_fallback_chunked}",
        f"  Timestamp:                  {result.timestamp}",
        "=" * 60,
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="M1 Mission Budget Benchmark")
    parser.add_argument("--hermetic", action="store_true", default=True,
                        help="Hermetic mode (default: True)")
    parser.add_argument("--output", type=str, default=None,
                        help="Optional JSON output path")
    args = parser.parse_args()

    print("[Benchmark] Running M1 mission budget benchmark...", file=sys.stderr)

    result = run_budget_benchmark()
    summary = _format_summary(result)
    print(summary)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w") as f:
            json.dump(asdict(result), f, indent=2)
        print(f"\n[Benchmark] Results written to {output_path}", file=sys.stderr)

    # Check mission budget
    from hledac.universal.runtime.resource_governor import MISSION_PEAK_RSS_GIB
    if result.peak_rss_gib > MISSION_PEAK_RSS_GIB:
        print(f"\n[FAIL] Mission budget exceeded: {result.peak_rss_gib:.3f} GiB > {MISSION_PEAK_RSS_GIB} GiB",
              file=sys.stderr)
        return 1

    print("\n[PASS] Mission budget benchmark complete", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
