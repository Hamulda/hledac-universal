#!/usr/bin/env python3
"""
Sprint 6D: 10s Smoke Test - Step 0D
"""
import asyncio
import logging
import sys
import time

logging.getLogger('hledac').setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(message)s')

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


async def run_smoke():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("="*70)
    print("[6D] 10S SMOKE TEST - PRE-FLIGHT")
    print("="*70)

    # Init
    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Setup test packets
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

    import random
    random.seed(42)

    # Reset state
    for attr in ['_seen_domains', '_action_success_counts', '_action_executed_counts',
                 '_action_selection_counts', '_latency_window']:
        if hasattr(orch, attr):
            if isinstance(getattr(orch, attr), dict):
                getattr(orch, attr).clear()
            elif hasattr(getattr(orch, attr), 'clear'):
                getattr(orch, attr).clear()

    DURATION = 10
    START_TIME = time.monotonic()

    print("\n[START] 10s smoke test...")

    result = await asyncio.wait_for(
        orch.run_benchmark(
            mode="propagation_on",
            duration_seconds=DURATION,
            warmup_iterations=0,
            query="artificial intelligence",
            prefer_offline_replay=True,
        ),
        timeout=DURATION + 30
    )

    ELAPSED = time.monotonic() - START_TIME
    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    benchmark_fps = iterations / ELAPSED if ELAPSED > 0 else 0
    findings_fps = findings / ELAPSED if ELAPSED > 0 else 0
    hhi = result.get('hh_index', 0)

    print("\n=== 10S SMOKE RESULTS ===")
    print(f"ELAPSED: {ELAPSED:.1f}s (target: {DURATION}s)")
    print(f"ITERATIONS: {iterations}")
    print(f"BENCHMARK_FPS: {benchmark_fps:.1f}")
    print(f"FINDINGS: {findings}")
    print(f"FINDINGS_FPS: {findings_fps:.1f}")
    print(f"SOURCES: {sources}")
    print(f"HHI: {hhi:.3f}")

    # Action distribution
    action_dist = result.get('actions_selected_distribution', {})
    print("\n[ACTION DISTRIBUTION]")
    for name, count in sorted(action_dist.items(), key=lambda x: -x[1])[:8]:
        pct = (count / iterations * 100) if iterations > 0 else 0
        print(f"  {name}: {count} ({pct:.1f}%)")

    # Academic search
    academic_runs = action_dist.get('academic_search', 0)
    print(f"\n[ACADEMIC_SEARCH] runs: {academic_runs}")

    # Network recon
    network_recon_runs = action_dist.get('network_recon', 0)
    network_recon_rate = (network_recon_runs / iterations * 100) if iterations > 0 else 0
    print(f"[NETWORK_RECON] runs: {network_recon_runs} ({network_recon_rate:.2f}%)")

    # Telemetry
    data_mode = getattr(orch, '_data_mode', 'UNKNOWN')
    print("\n[TELEMETRY]")
    print(f"  DATA_MODE: {data_mode}")

    # Anti-mock verdict
    print("\n[ANTI-MOCK]")
    replay_packets = getattr(orch, '_replay_packet_count', 0)
    print(f"  Replay packets: {replay_packets}")
    print(f"  FPS realistic: {benchmark_fps < 50}")

    print(f"\n{'='*70}")
    print("SMOKE COMPLETE")
    print(f"{'='*70}")

    return {
        'elapsed': ELAPSED,
        'iterations': iterations,
        'benchmark_fps': benchmark_fps,
        'findings': findings,
        'findings_fps': findings_fps,
        'sources': sources,
        'hhi': hhi,
        'academic_runs': academic_runs,
        'network_recon_runs': network_recon_runs,
        'network_recon_rate': network_recon_rate,
        'data_mode': data_mode,
    }


if __name__ == "__main__":
    result = asyncio.run(run_smoke())
