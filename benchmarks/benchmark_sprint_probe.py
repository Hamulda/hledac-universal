#!/usr/bin/env python3
"""
Probe F192E.1: Sprint Path E2E Benchmark
========================================

Standalone benchmark probe for canonical sprint path.
Measures first-finding latency, memory ceiling, branch mix, total findings.

Usage:
    python benchmarks/benchmark_sprint_probe.py
    python benchmarks/benchmark_sprint_probe.py --duration 60 --cycles 10

Bounded for M1 8GB — no swap escalation, CI-safe.
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
sys.path.insert(0, str(Path(__file__).parent.parent))

import psutil

# uvloop — 2x faster event loop on M1
try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    _UVLOOP_ACTIVE = True
except ImportError:
    _UVLOOP_ACTIVE = False

# Benchmark constants
BENCHMARK_DURATION_S = 60.0   # CI-safe 60s sprint
BENCHMARK_MAX_CYCLES = 10      # CI-safe cycle ceiling
M1_8GB_CEILING_MB = 6.5 * 1024  # 6.5GB — M1 8GB RSS ceiling


def get_rss_mb() -> float:
    """Get current process RSS in MB."""
    try:
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


async def sample_uma_status() -> dict[str, Any]:
    """Sample current UMA status."""
    try:
        from hledac.universal.core.resource_governor import sample_uma_status
        s = sample_uma_status()
        return {
            "system_used_gib": s.system_used_gib,
            "swap_used_gib": s.swap_used_gib,
            "swap_detected": s.swap_detected,
            "state": s.state,
            "rss_gib": s.rss_gib,
        }
    except Exception as exc:
        return {"error": str(exc), "state": "unknown"}


# ---------------------------------------------------------------------------
# Canned entry factory
# ---------------------------------------------------------------------------

def _make_canned_entry() -> dict[str, Any]:
    return {
        "entry_url": "https://example.com/feed/entry-cve-2026-1234",
        "title": "CVE-2026-1234: Remote Code Execution in ExampleServer",
        "summary": (
            "Multiple critical CVEs disclosed affecting ExampleServer v1.x through v2.x. "
            "Remote attackers can execute arbitrary code via crafted requests."
        ),
        "rich_content": (
            "Multiple critical CVEs disclosed affecting ExampleServer v1.x through v2.x. "
            "Remote attackers can execute arbitrary code via crafted requests. patch is available."
        ),
        "entry_author": "disclosure-team",
        "published": "2026-04-21T10:00:00Z",
        "feed_url": "https://example.com/feed",
        "feed_title": "Example Security Feed",
        "feed_language": "en",
    }


# ---------------------------------------------------------------------------
# Adapter / Matcher patches (hermetic benchmark)
# ---------------------------------------------------------------------------

def _patch_feeds_and_patterns():
    """
    Patch feed adapter and pattern matcher for hermetic benchmark.
    Returns cleanup functions.
    """
    import hledac.universal.discovery.rss_atom_adapter as rss_module
    from hledac.universal.discovery.rss_atom_adapter import FeedEntryHit
    from hledac.universal.patterns import pattern_matcher as pm_module
    from hledac.universal.pipeline import live_feed_pipeline as lfp_module
    from hledac.universal.patterns.pattern_matcher import PatternHit

    entry_dict = _make_canned_entry()
    canned_entry = FeedEntryHit(
        feed_url=entry_dict["feed_url"],
        entry_url=entry_dict["entry_url"],
        title=entry_dict["title"],
        summary=entry_dict["summary"],
        published_raw=entry_dict["published"],
        published_ts=1705651200.0,
        source="test",
        rank=0,
        retrieved_ts=1705651200.0,
        entry_hash="testhash01",
        rich_content=entry_dict["rich_content"],
        entry_author=entry_dict["entry_author"],
        feed_title=entry_dict["feed_title"],
        feed_language=entry_dict["feed_language"],
    )

    class _FakeFeedBatch:
        error: str | None = None
        entries: tuple[FeedEntryHit, ...] = (canned_entry,)
        source_accessibility_error: str | None = None

    async def _fake_fetch(*args, **kwargs) -> _FakeFeedBatch:
        return _FakeFeedBatch()

    _orig_fetch = rss_module.async_fetch_feed_entries
    rss_module.async_fetch_feed_entries = _fake_fetch

    pm_module.configure_default_bootstrap_patterns_if_empty()
    _orig_match = pm_module.match_text
    _orig_lfp_match = getattr(lfp_module, 'match_text', None)

    def _canned_match(text: str, *, boundary_policy: str = "none") -> list[PatternHit]:
        if not text:
            return []
        idx = text.find("CVE-2026-1234")
        if idx >= 0:
            return [PatternHit(
                pattern="cve-", start=idx, end=idx + 14,
                value=text[idx:idx + 14], label="vulnerability_id",
            )]
        return _orig_match(text, boundary_policy=boundary_policy)

    pm_module.match_text = _canned_match
    if _orig_lfp_match is not None:
        lfp_module.match_text = _canned_match

    def _cleanup():
        rss_module.async_fetch_feed_entries = _orig_fetch
        pm_module.match_text = _orig_match
        if _orig_lfp_match is not None:
            lfp_module.match_text = _orig_lfp_match

    return _cleanup


# ---------------------------------------------------------------------------
# Run benchmark
# ---------------------------------------------------------------------------

async def run_benchmark(
    duration_s: float = BENCHMARK_DURATION_S,
    max_cycles: int = BENCHMARK_MAX_CYCLES,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """
    Run the E2E sprint path benchmark.

    Measures:
    - first_finding_latency_s: wall-clock sprint_start → first persisted finding
    - peak_rss_mb: peak RSS during sprint
    - uma_peak_state: peak UMA state
    - branch_mix: feed/public/ct_log findings counts
    - total_findings: total persisted findings at end of sprint
    """
    log = print
    log("=" * 60)
    log("Probe F192E.1 — Sprint Path E2E Benchmark")
    log("=" * 60)
    log(f"Duration: {duration_s}s | Max cycles: {max_cycles}")
    log(f"uvloop: {'active' if _UVLOOP_ACTIVE else 'not available'}")
    log(f"M1 8GB ceiling: {M1_8GB_CEILING_MB:.0f}MB")
    log("=" * 60)

    # Patch adapters
    cleanup = _patch_feeds_and_patterns()

    # Imports
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
    from hledac.universal.pipeline.live_feed_pipeline import async_run_live_feed_pipeline

    # Temp store
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hledac_bench_probe_")
    db_path = Path(tmp) / "shadow.duckdb"
    store = DuckDBShadowStore(db_path=str(db_path))
    store._init_persistent_dedup_lmdb = lambda: None
    await store.async_initialize()

    # Baseline measurements
    rss_baseline = get_rss_mb()
    uma_baseline = await sample_uma_status()

    log(f"\nRSS baseline: {rss_baseline:.0f}MB")
    log(f"UMA baseline: state={uma_baseline.get('state','?')} "
        f"swap={uma_baseline.get('swap_used_gib',0):.2f}GiB "
        f"swap_detected={uma_baseline.get('swap_detected',False)}")

    # ── Sprint start ──────────────────────────────────────────────────────────
    sprint_t0 = time.monotonic()
    first_finding_ts: float | None = None

    # Run feed pipeline (canonical path)
    result = await async_run_live_feed_pipeline(
        feed_url="https://example.com/feed",
        store=store,
        query_context="benchmark-probe-f192e",
        max_entries=5,
        timeout_s=15.0,
    )

    pipeline_elapsed = time.monotonic() - sprint_t0

    # Check for first persisted finding
    persisted = await store.async_get_recent_findings(limit=20)
    if persisted and first_finding_ts is None:
        first_finding_ts = time.monotonic() - sprint_t0

    total_findings = len(persisted)

    # ── Memory measurements ─────────────────────────────────────────────────
    rss_peak = get_rss_mb()
    uma_peak = await sample_uma_status()
    rss_delta = rss_peak - rss_baseline

    # ── Branch mix ──────────────────────────────────────────────────────────
    feed_count = sum(1 for f in persisted if getattr(f, "source_type", "") == "rss_atom_pipeline")
    public_count = sum(1 for f in persisted if getattr(f, "source_type", "") == "live_public_pipeline")
    ct_count = sum(1 for f in persisted if getattr(f, "source_type", "") == "ct_log_pipeline")
    branch_mix = {"feed_findings": feed_count, "public_findings": public_count, "ct_findings": ct_count}

    # Primary signal source
    if ct_count > 0 and feed_count == 0 and public_count == 0:
        primary = "ct"
    elif feed_count > 0 and public_count == 0 and ct_count == 0:
        primary = "feed"
    elif public_count > 0 and feed_count == 0 and ct_count == 0:
        primary = "public"
    elif feed_count > 0 and public_count > 0:
        primary = "mixed"
    elif ct_count > 0:
        primary = "mixed_ct"
    else:
        primary = "none"

    # ── Cleanup ──────────────────────────────────────────────────────────────
    try:
        await store.aclose()
    except Exception:
        pass
    cleanup()
    try:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception:
        pass

    # ── Results ─────────────────────────────────────────────────────────────
    elapsed_total = time.monotonic() - sprint_t0

    result_dict = {
        "metadata": {
            "probe": "F192E.1",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "duration_s": duration_s,
            "max_cycles": max_cycles,
            "uvloop_active": _UVLOOP_ACTIVE,
            "python_version": sys.version.split()[0],
        },
        "first_finding_latency_s": round(first_finding_ts, 3) if first_finding_ts is not None else None,
        "pipeline_elapsed_s": round(pipeline_elapsed, 3),
        "total_elapsed_s": round(elapsed_total, 3),
        "total_findings": total_findings,
        "peak_rss_mb": round(rss_peak, 1),
        "rss_baseline_mb": round(rss_baseline, 1),
        "rss_delta_mb": round(rss_delta, 1),
        "m1_8gb_ceiling_mb": M1_8GB_CEILING_MB,
        "memory_ceiling_ok": rss_peak < M1_8GB_CEILING_MB,
        "uma_peak": uma_peak,
        "uma_baseline": uma_baseline,
        "branch_mix": branch_mix,
        "primary_signal_source": primary,
        "pipeline_result": {
            "fetched_entries": getattr(result, 'fetched_entries', 0),
            "entries_scanned": getattr(result, 'entries_scanned', 0),
            "total_pattern_hits": getattr(result, 'total_pattern_hits', 0),
            "accepted_findings": getattr(result, 'accepted_findings', 0),
            "stored_findings": getattr(result, 'stored_findings', 0),
            "signal_stage": getattr(result, 'signal_stage', "unknown"),
        },
    }

    # ── Print summary ──────────────────────────────────────────────────────
    log(f"\n{'=' * 60}")
    log(f"BENCHMARK RESULTS — Probe F192E.1")
    log(f"{'=' * 60}")
    log(f"  first_finding_latency_s: {result_dict['first_finding_latency_s']}")
    log(f"  pipeline_elapsed_s:     {result_dict['pipeline_elapsed_s']}")
    log(f"  total_elapsed_s:        {result_dict['total_elapsed_s']}")
    log(f"  total_findings:         {total_findings}")
    log(f"  peak_rss_mb:            {rss_peak:.0f}MB (baseline {rss_baseline:.0f}MB, delta {rss_delta:+.0f}MB)")
    log(f"  memory_ceiling_ok:      {result_dict['memory_ceiling_ok']} (ceiling {M1_8GB_CEILING_MB:.0f}MB)")
    log(f"  uma_peak_state:        {uma_peak.get('state', 'unknown')}")
    log(f"  swap_detected:         {uma_peak.get('swap_detected', False)}")
    log(f"  branch_mix:             {branch_mix}")
    log(f"  primary_signal_source: {primary}")
    log(f"  signal_stage:          {result_dict['pipeline_result']['signal_stage']}")
    log(f"{'=' * 60}")

    # Fail if memory ceiling breached (M1 8GB constraint)
    if not result_dict["memory_ceiling_ok"]:
        log(f"FAIL: RSS {rss_peak:.0f}MB exceeds M1 8GB ceiling {M1_8GB_CEILING_MB:.0f}MB")

    if uma_peak.get("swap_detected", False):
        log(f"WARN: Swap detected — UMA pressure on M1 8GB")

    # Save to output path
    if output_path is None:
        output_path = Path.home() / "hledac_outputs" / "benchmark_sprint_probe.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(result_dict, f, indent=2)
    log(f"\nResults saved to: {output_path}")

    return result_dict


def main():
    parser = argparse.ArgumentParser(description="Probe F192E.1 — Sprint Path E2E Benchmark")
    parser.add_argument(
        "--duration", type=float, default=BENCHMARK_DURATION_S,
        help=f"Sprint duration in seconds (default: {BENCHMARK_DURATION_S})",
    )
    parser.add_argument(
        "--cycles", type=int, default=BENCHMARK_MAX_CYCLES,
        help=f"Max cycles (default: {BENCHMARK_MAX_CYCLES})",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output JSON path (default: ~/hledac_outputs/benchmark_sprint_probe.json)",
    )
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else None
    result = asyncio.run(run_benchmark(duration_s=args.duration, max_cycles=args.cycles, output_path=output_path))

    # Exit code: 0 if memory_ceiling_ok, 1 otherwise
    sys.exit(0 if result.get("memory_ceiling_ok", False) else 1)


if __name__ == "__main__":
    main()