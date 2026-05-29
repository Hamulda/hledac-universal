#!/usr/bin/env python3
"""
Sprint 5R: Quick 10s Diagnostic Benchmark
"""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def quick_diagnostic():
    import random

    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("[DIAGNOSTIC] 10s OFFLINE_REPLAY benchmark")

    orch = FullyAutonomousOrchestrator()
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
        metadata = {"email": f"researcher{i}@ai-lab.org", "handle": f"researcher{i}"}
        if i % 2 == 0:
            metadata["alt_email"] = f"researcher{i}@github.com"
        packet.metadata_digests = metadata
        orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)

    random.seed(42)

    if hasattr(orch, '_research_mgr') and orch._research_mgr:
        if hasattr(orch._research_mgr, '_findings_heap'):
            orch._research_mgr._findings_heap.clear()
        if hasattr(orch._research_mgr, '_sources_heap'):
            orch._research_mgr._sources_heap.clear()

    print("[RUN] Starting 10s benchmark...")
    start = time.perf_counter()
    result = await asyncio.wait_for(
        orch.run_benchmark(
            mode="propagation_on",
            duration_seconds=10,
            warmup_iterations=0,
            query="artificial intelligence",
            prefer_offline_replay=True,
        ),
        timeout=20
    )
    elapsed = time.perf_counter() - start

    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    fps = findings / elapsed if elapsed > 0 else 0
    p95 = result.get('p95_latency_ms', 0)
    hhi = result.get('hh_index', 0)
    data_mode = result.get('data_mode', 'unknown')

    # TS posteriors
    ts_posteriors = {}
    if hasattr(orch, '_ts_posteriors'):
        for action, posterior in orch._ts_posteriors.items():
            alpha = posterior.get('alpha', 1.0)
            beta = posterior.get('beta', 1.0)
            uncertainty = (alpha * beta) ** 0.5 / ((alpha + beta) ** 1.5) if (alpha + beta) > 0 else 0
            ts_posteriors[action] = {'alpha': alpha, 'beta': beta, 'uncertainty': uncertainty}

    healthy_count = sum(1 for p in ts_posteriors.values() if p.get('uncertainty', 0) >= 0.05)
    total = len(ts_posteriors)
    healthy_fraction = healthy_count / total if total > 0 else 0

    print(f"\n[RESULT] data_mode={data_mode}")
    print(f"[RESULT] iterations={iterations} findings={findings} sources={sources}")
    print(f"[RESULT] FPS={fps:.1f} P95={p95:.1f}ms HHI={hhi:.3f}")
    print(f"[RESULT] TS posteriors: {len(ts_posteriors)} actions")
    print(f"[RESULT] Healthy uncertainty: {healthy_count}/{total} ({healthy_fraction:.1%})")

    for action, p in sorted(ts_posteriors.items(), key=lambda x: x[1].get('uncertainty', 0), reverse=True)[:5]:
        print(f"  {action}: uncertainty={p['uncertainty']:.3f}")

    return {
        'iterations': iterations,
        'findings': findings,
        'sources': sources,
        'fps': fps,
        'p95': p95,
        'hhi': hhi,
        'data_mode': data_mode,
        'healthy_fraction': healthy_fraction,
        'total_actions': total,
    }

if __name__ == "__main__":
    result = asyncio.run(quick_diagnostic())
    sys.exit(0)
