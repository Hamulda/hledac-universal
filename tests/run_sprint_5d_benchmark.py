#!/usr/bin/env python3
"""Run full 60s × 3 repeatability benchmark for Sprint 5D - post-5D baseline."""
import asyncio
import json
import sys

# Add project to path
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_full_benchmark():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    print("[SPRINT 5D] Starting full 60s × 3 repeatability benchmark...")
    print("=" * 60)

    # Create orchestrator with evidence packet storage
    orch = FullyAutonomousOrchestrator()

    # Initialize evidence packet storage for OFFLINE_REPLAY
    try:
        from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage
        orch._evidence_packet_storage = EvidencePacketStorage()
        # Create synthetic packets for testing (same as 5A baseline)
        for i in range(15):
            # Create proper EvidencePacket objects with required fields
            packet = EvidencePacket(
                evidence_id=f"evidence_{i}",
                url=f"http://localhost:{64000+i}/test",
                final_url=f"http://localhost:{64000+i}/test",
                domain=f"localhost:{64000+i}",
                fetched_at=1735689600.0,  # 2025-01-01
                status=200,
                headers_digest="dummy_hash",
                snapshot_ref={},
                content_hash=f"hash_{i}",
                # Add identity metadata for propagation testing
                metadata_digests={
                    "email": f"researcher{i}@ai-lab.org",
                    "handle": f"researcher{i}",
                }
            )
            orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)
        print(f"[SETUP] Created {orch._evidence_packet_storage.get_stats()['total_packets']} synthetic packets")
    except Exception as e:
        print(f"[SETUP] Warning: {e}")

    # Run repeatability benchmark with 60s × 3
    results = await orch.run_repeatability_benchmark(
        duration_seconds=60,
        warmup_seconds=10,
        num_runs=3,
        query="artificial intelligence",
        prefer_offline_replay=True,
    )

    print("\n" + "=" * 60)
    print("[RESULTS] 60s × 3 Repeatability Benchmark")
    print("=" * 60)

    # Print summary
    print(f"\nWarm-up: {results.get('warmup_result', {})}")
    print(f"\nMeasurement runs: {len(results.get('measurement_runs', []))}")

    for run in results.get('measurement_runs', []):
        print(f"\n--- Run {run.get('run_index')} ---")
        print(f"  Elapsed: {run.get('elapsed_seconds', 0):.2f}s")
        print(f"  Iterations: {run.get('iterations_completed', 0)}")
        print(f"  Findings: {run.get('findings_total', 0)}")
        print(f"  Sources: {run.get('sources_total', 0)}")
        print(f"  Unique Sources: {run.get('unique_sources_count', 0)}")
        print(f"  Hints Generated: {run.get('propagation_hints_generated', 0)}")
        print(f"  Hints Consumed: {run.get('propagation_hints_consumed', 0)}")
        print(f"  HH Index: {run.get('hh_index', 0):.3f}")
        print(f"  Avg Latency: {run.get('avg_latency_ms', 0):.2f}ms")
        print(f"  P95 Latency: {run.get('p95_latency_ms', 0):.2f}ms")
        print(f"  RSS Delta: {run.get('rss_delta_mb', 0):.2f} MB")

    summary = results.get('repeatability_summary', {})
    print("\n--- Repeatability Summary ---")
    print(f"  Findings - Min: {summary.get('findings_min', 0)}, Max: {summary.get('findings_max', 0)}, Mean: {summary.get('findings_mean', 0):.1f}")
    print(f"  Sources - Min: {summary.get('sources_min', 0)}, Max: {summary.get('sources_max', 0)}, Mean: {summary.get('sources_mean', 0):.1f}")
    print(f"  HH Index Mean: {summary.get('hh_index_mean', 0):.3f}")
    print(f"  Variability: {summary.get('findings_variability_pct', 0):.1f}%")
    print(f"  Variability Verdict: {summary.get('variability_verdict', 'UNKNOWN')}")

    # Save JSON artifact
    output_json = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/benchmark_scorecard_5D.json"
    with open(output_json, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n[SAVED] JSON scorecard: {output_json}")

    # Compare with 5A baseline
    try:
        with open("/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/tests/benchmark_scorecard_5A_final.json") as f:
            baseline = json.load(f)
        baseline_summary = baseline.get('repeatability_summary', {})

        print("\n" + "=" * 60)
        print("[COMPARISON] 5D vs 5A-R3 Baseline")
        print("=" * 60)

        findings_delta = summary.get('findings_mean', 0) - baseline_summary.get('findings_mean', 0)
        findings_pct = (findings_delta / baseline_summary.get('findings_mean', 1)) * 100

        hh_delta = summary.get('hh_index_mean', 0) - baseline_summary.get('hh_index_mean', 0)

        print(f"  Findings: {summary.get('findings_mean', 0):.0f} vs {baseline_summary.get('findings_mean', 0):.0f} ({findings_pct:+.1f}%)")
        print(f"  Sources: {summary.get('sources_mean', 0):.0f} vs {baseline_summary.get('sources_mean', 0):.0f}")
        print(f"  HH Index: {summary.get('hh_index_mean', 0):.3f} vs {baseline_summary.get('hh_index_mean', 0):.3f} ({hh_delta:+.3f})")

        # Classification
        if abs(findings_pct) < 15:
            classification = "EXPECTED_VARIANCE"
        elif findings_pct < -15:
            classification = "REAL_REGRESSION"
        else:
            classification = "IMPROVEMENT"

        print(f"\n  Classification: {classification}")

    except Exception as e:
        print(f"[COMPARISON] Warning: {e}")

    print("\n" + "=" * 60)
    print("[COMPLETE] Sprint 5D Benchmark")
    print("=" * 60)

    return results

if __name__ == "__main__":
    asyncio.run(run_full_benchmark())
