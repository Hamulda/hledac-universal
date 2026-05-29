#!/usr/bin/env python3
"""
Sprint 5Q: 3×60s TS Shadow Validation
"""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_3x60s():
    import gc
    import random

    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[3×60s] TS Shadow Validation Series")
    print("=" * 60)

    results = []

    for run_idx in range(3):
        print(f"\n{'='*40}")
        print(f"[RUN {run_idx+1}/3] Starting 60s benchmark...")
        print(f"{'='*40}")

        # Fresh orchestrator per run
        orch = FullyAutonomousOrchestrator()

        # Initialize packets
        orch._evidence_packet_storage = EvidencePacketStorage()
        for i in range(15):
            packet = EvidencePacket(
                evidence_id=f"evidence_{i}",
                url=f"http://localhost:{64000+i}/test",
                final_url=f"http://localhost:{64000+i}/test",
                domain="localhost",
                fetched_at=time.time() - (i * 86400),
                status=200,
                headers_digest="abc123",
                snapshot_ref={"blob_hash": f"hash_{i}", "path": "/tmp", "size": 1000, "encrypted": False},
                content_hash=f"content_hash_{i}",
                page_type="text/html",
            )
            packet.metadata_digests = {"email": f"researcher{i}@ai-lab.org", "handle": f"researcher{i}"}
            orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)

        random.seed(42 + run_idx)

        # Clear state
        if hasattr(orch, '_research_mgr') and orch._research_mgr:
            if hasattr(orch._research_mgr, '_findings_heap'):
                orch._research_mgr._findings_heap.clear()
            if hasattr(orch._research_mgr, '_sources_heap'):
                orch._research_mgr._sources_heap.clear()

        start = time.perf_counter()
        result = await asyncio.wait_for(
            orch.run_benchmark(
                mode="propagation_on",
                duration_seconds=60,
                warmup_iterations=0,
                query="artificial intelligence",
                prefer_offline_replay=True,
            ),
            timeout=75
        )
        elapsed = time.perf_counter() - start

        fps = result.get('iterations_completed', 0) / elapsed if elapsed > 0 else 0
        findings = result.get('findings_total', 0)
        sources = result.get('sources_total', 0)
        p95 = result.get('p95_latency_ms', 0)
        avg = result.get('avg_latency_ms', 0)
        hhi = result.get('hh_index', 0)

        results.append({
            'run': run_idx + 1,
            'iterations': result.get('iterations_completed', 0),
            'findings': findings,
            'sources': sources,
            'fps': fps,
            'p95': p95,
            'avg': avg,
            'hhi': hhi,
            'elapsed': elapsed,
        })

        print(f"[RUN {run_idx+1}] iters={result.get('iterations_completed', 0)} findings={findings} sources={sources}")
        print(f"[RUN {run_idx+1}] FPS={fps:.1f} P95={p95:.1f}ms HHI={hhi:.3f}")

        gc.collect()
        await asyncio.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("[3×60s] SUMMARY")
    print("=" * 60)

    fps_values = [r['fps'] for r in results]
    p95_values = [r['p95'] for r in results]
    hhi_values = [r['hhi'] for r in results]
    findings_values = [r['findings'] for r in results]

    import statistics
    fps_mean = statistics.mean(fps_values)
    fps_stdev = statistics.stdev(fps_values) if len(fps_values) > 1 else 0
    fps_cv = fps_stdev / fps_mean if fps_mean > 0 else 0

    p95_mean = statistics.mean(p95_values)
    p95_stdev = statistics.stdev(p95_values) if len(p95_values) > 1 else 0
    p95_cv = p95_stdev / p95_mean if p95_mean > 0 else 0

    hhi_mean = statistics.mean(hhi_values)
    findings_mean = statistics.mean(findings_values)

    print(f"\nFindings: {findings_values} (mean={findings_mean:.0f})")
    print(f"FPS: {fps_values} (mean={fps_mean:.1f}, CV={fps_cv:.2%})")
    print(f"P95: {p95_values} (mean={p95_mean:.1f}ms, CV={p95_cv:.2%})")
    print(f"HHI: {hhi_values} (mean={hhi_mean:.3f})")

    # Stability gates
    fps_stable = fps_cv < 0.15
    p95_stable = p95_cv < 0.30

    print(f"\n[Stability] FPS CV < 15%: {'✅' if fps_stable else '⚠️'}")
    print(f"[Stability] P95 CV < 30%: {'✅' if p95_stable else '⚠️'}")

    if fps_stable and p95_stable:
        print("\n✅ TS SHADOW VALIDATION PASSED")
    else:
        print("\n⚠️  TS SHADOW VALIDATION NEEDS TUNING")

    return results

if __name__ == "__main__":
    asyncio.run(run_3x60s())
