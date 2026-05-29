#!/usr/bin/env python3
"""
Sprint 5Q: P95 Latency Root Cause Diagnostic - OFFLINE_REPLAY MODE

Run: python hledac/universal/tests/diagnose_p95_offline.py
"""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def diagnose_p95_offline():
    import random

    import psutil
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[DIAGNOSTIC] P95 Latency - OFFLINE_REPLAY Mode")
    print("=" * 60)

    # Enable asyncio debug mode
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.05  # 50ms threshold

    # Create orchestrator
    orch = FullyAutonomousOrchestrator()

    # Initialize evidence packet storage for OFFLINE_REPLAY
    orch._evidence_packet_storage = EvidencePacketStorage()
    for i in range(15):
        # Create EvidencePacket
        packet = EvidencePacket(
            evidence_id=f"evidence_{i}",
            url=f"http://localhost:{64000+i}/test",
            final_url=f"http://localhost:{64000+i}/test",
            domain="localhost",
            fetched_at=time.time() - (i * 86400),  # Spread across days
            status=200,
            headers_digest="abc123",
            snapshot_ref={"blob_hash": f"hash_{i}", "path": "/tmp", "size": 1000, "encrypted": False},
            content_hash=f"content_hash_{i}",
            page_type="text/html",
        )
        # Add metadata for identity stitching
        packet.metadata_digests = {
            "email": f"researcher{i}@ai-lab.org",
            "handle": f"researcher{i}",
        }
        orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)
    print(f"[SETUP] Created {orch._evidence_packet_storage.get_stats()['total_packets']} packets")

    # Seed for reproducibility
    random.seed(42)

    # Reset research manager state
    if hasattr(orch, '_research_mgr') and orch._research_mgr:
        if hasattr(orch._research_mgr, '_findings_heap'):
            orch._research_mgr._findings_heap.clear()
        if hasattr(orch._research_mgr, '_sources_heap'):
            orch._research_mgr._sources_heap.clear()

    # Run OFFLINE_REPLAY benchmark
    print("\n[RUN] Starting 30s OFFLINE_REPLAY benchmark...")
    start_time = time.perf_counter()

    try:
        result = await asyncio.wait_for(
            orch.run_benchmark(
                mode="propagation_on",
                duration_seconds=30,
                warmup_iterations=0,
                query="artificial intelligence",
                prefer_offline_replay=True,  # OFFLINE_REPLAY mode
            ),
            timeout=45
        )
    except Exception as e:
        print(f"[ERROR] Benchmark failed: {e}")
        result = {}

    elapsed = time.perf_counter() - start_time

    # Extract metrics
    print("\n" + "=" * 60)
    print("[RESULTS] P95 Diagnostic Results - OFFLINE_REPLAY")
    print("=" * 60)

    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    p95_latency = result.get('p95_latency_ms', 0)
    avg_latency = result.get('avg_latency_ms', 0)
    data_mode = result.get('data_mode', 'unknown')

    print(f"\n[Basic] Mode: {data_mode}")
    print(f"[Basic] Iterations: {iterations}, Findings: {findings}, Sources: {sources}")
    print(f"[Basic] Elapsed: {elapsed:.1f}s, FPS: {iterations/elapsed:.1f}")
    print(f"[Latency] Avg: {avg_latency:.1f}ms, P95: {p95_latency:.1f}ms")

    # Action latency breakdown
    latency_by_action_avg = result.get('latency_by_action_avg_ms', {})
    latency_by_action_p95 = result.get('latency_by_action_p95_ms', {})
    action_selection_counts = result.get('action_selection_counts', {})

    print("\n[Action Latency Breakdown]:")
    print(f"{'Action':<25} {'Avg (ms)':<12} {'P95 (ms)':<12} {'Count':<8}")
    print("-" * 60)
    for action in sorted(latency_by_action_avg.keys(), key=lambda x: latency_by_action_avg.get(x, 0), reverse=True):
        avg = latency_by_action_avg.get(action, 0)
        p95 = latency_by_action_p95.get(action, 0)
        count = action_selection_counts.get(action, 0)
        print(f"{action:<25} {avg:<12.1f} {p95:<12.1f} {count:<8}")

    # RSS memory
    rss_mb = psutil.Process().memory_info().rss / (1024 * 1024)
    print(f"\n[Memory] RSS: {rss_mb:.0f} MB")

    # Summary
    print("\n" + "=" * 60)
    print("[SUMMARY]")
    print("=" * 60)
    if p95_latency > 1000:
        print(f"⚠️  P95 = {p95_latency:.1f}ms > 1000ms - ROOT CAUSE CONFIRMED")
    else:
        print(f"✅ P95 = {p95_latency:.1f}ms < 1000ms")

    return {
        "p95_ms": p95_latency,
        "iterations": iterations,
        "findings": findings,
        "data_mode": data_mode,
    }

if __name__ == "__main__":
    result = asyncio.run(diagnose_p95_offline())
    sys.exit(0)
