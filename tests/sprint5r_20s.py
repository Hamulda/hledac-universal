#!/usr/bin/env python3
"""
Sprint 5R: TS Active 20s Benchmark (faster)
"""
import asyncio
import random
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

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

    print(f"TS Active: {orch._TS_SHADOW_MODE}")
    start = time.perf_counter()
    result = await asyncio.wait_for(
        orch.run_benchmark(
            mode="propagation_on",
            duration_seconds=20,
            warmup_iterations=0,
            query="artificial intelligence",
            prefer_offline_replay=True,
        ),
        timeout=30
    )
    elapsed = time.perf_counter() - start

    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    fps = findings / elapsed if elapsed > 0 else 0
    p95 = result.get('p95_latency_ms', 0)
    hhi = result.get('hh_index', 0)
    data_mode = result.get('data_mode', 'unknown')
    action_counts = result.get('action_selection_counts', {})

    def gini(counts):
        if not counts:
            return 0.0
        vals = sorted(counts.values())
        n = len(vals)
        cum = sum((i+1) * v for i, v in enumerate(vals))
        return (2 * cum) / (n * sum(vals)) - (n + 1) / n

    g = gini(action_counts)

    print(f"[RESULT] {data_mode}: iters={iterations}, findings={findings}, sources={sources}")
    print(f"[RESULT] FPS={fps:.1f}, P95={p95:.1f}ms, HHI={hhi:.3f}, Gini={g:.3f}")
    print(f"[RESULT] Actions: {action_counts}")

if __name__ == "__main__":
    asyncio.run(run())
