#!/usr/bin/env python3
"""
Sprint 6A: 10s Smoke Test for preflight telemetry validation
"""
import asyncio
import logging
import os
import sys
import time

# Suppress verbose logging
logging.getLogger('hledac').setLevel(logging.WARNING)

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_smoke_test():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[6A] 10s PREFLIGHT SMOKE TEST")
    print("=" * 60)

    # Initialize orchestrator
    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Count packets
    packets_dir = os.path.expanduser("~/.hledac/evidence_packets/shards")
    packet_count = 0
    if os.path.exists(packets_dir):
        for _root, _dirs, files in os.walk(packets_dir):
            packet_count += len([f for f in files if f.endswith('.json')])

    print(f"[PREFLIGHT] Replay packets: {packet_count}")

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

    # Verify new telemetry attributes exist
    print("\n[TELEMETRY CHECK]")
    telemetry_attrs = [
        '_latency_window', '_gc_collected_total', '_gc_time_total_ms',
        '_action_success_counts', '_unique_sources_this_cycle',
        '_unique_sources_prev_cycle', '_unique_sources_total_cumulative',
        '_calibration_snapshots', '_calib_success_snap', '_calib_executed_snap',
        '_active_task_baseline', '_active_task_peak', '_exploration_budget_triggers'
    ]

    all_ok = True
    for attr in telemetry_attrs:
        exists = hasattr(orch, attr)
        print(f"  {attr}: {'OK' if exists else 'MISSING'}")
        if not exists:
            all_ok = False

    # Reset state
    for attr in ['_seen_domains', '_action_success_counts', '_action_executed_counts',
                 '_action_selection_counts', '_latency_window']:
        if hasattr(orch, attr):
            if isinstance(getattr(orch, attr), dict):
                getattr(orch, attr).clear()
            elif hasattr(getattr(orch, attr), 'clear'):
                getattr(orch, attr).clear()

    print("\n[TS CHECK]")
    print(f"  _TS_SHADOW_MODE: {orch._TS_SHADOW_MODE}")

    # Run 10s benchmark
    DURATION = 10
    START_TIME = time.monotonic()

    print("\n[START] 10s OFFLINE_REPLAY smoke test...")

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

    print("\n=== RESULTS ===")
    print(f"[ELAPSED] {ELAPSED:.1f}s (target: {DURATION}s)")
    print(f"[ITERATIONS] {iterations}")
    print(f"[BENCHMARK_FPS] {benchmark_fps:.1f}")
    print(f"[FINDINGS] {findings}")
    print(f"[FINDINGS_FPS] {findings_fps:.1f}")
    print(f"[SOURCES] {sources}")
    print(f"[HHI] {hhi:.3f}")
    print(f"[DATA_MODE] {result.get('data_mode', 'unknown')}")

    # Duration check
    duration_ok = ELAPSED >= 0.9 * DURATION
    print(f"[DURATION_CHECK] {'PASS' if duration_ok else 'FAIL'}")

    # Anti-mock check
    print("\n[ANTI-MOCK CHECK]")
    anti_mock_ok = packet_count >= 100 and benchmark_fps < 50
    print(f"  packets >= 100: {packet_count >= 100}")
    print(f"  benchmark_fps < 50: {benchmark_fps < 50}")
    print(f"  ANTI_MOCK_VERDICT: {'CLEAN' if anti_mock_ok else 'SUSPICIOUS'}")

    return all_ok and duration_ok and anti_mock_ok

if __name__ == "__main__":
    ok = asyncio.run(run_smoke_test())
    sys.exit(0 if ok else 1)
