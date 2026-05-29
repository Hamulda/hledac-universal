#!/usr/bin/env python3
"""
Sprint 6A: 300s OFFLINE_REPLAY Calibration Preview
"""
import asyncio
import logging
import os
import sys
import time

logging.getLogger('hledac').setLevel(logging.WARNING)

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_300s_test():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[6A] 300s OFFLINE_REPLAY CALIBRATION PREVIEW")
    print("=" * 60)

    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    packets_dir = os.path.expanduser("~/.hledac/evidence_packets/shards")
    packet_count = 0
    if os.path.exists(packets_dir):
        for _root, _dirs, files in os.walk(packets_dir):
            packet_count += len([f for f in files if f.endswith('.json')])

    print(f"[PREFLIGHT] Replay packets: {packet_count}")

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

    for attr in ['_seen_domains', '_action_success_counts', '_action_executed_counts',
                 '_action_selection_counts', '_latency_window']:
        if hasattr(orch, attr):
            if isinstance(getattr(orch, attr), dict):
                getattr(orch, attr).clear()
            elif hasattr(getattr(orch, attr), 'clear'):
                getattr(orch, attr).clear()

    DURATION = 300
    START_TIME = time.monotonic()

    print("\n[START] 300s OFFLINE_REPLAY calibration preview...")

    result = await asyncio.wait_for(
        orch.run_benchmark(
            mode="propagation_on",
            duration_seconds=DURATION,
            warmup_iterations=0,
            query="artificial intelligence",
            prefer_offline_replay=True,
        ),
        timeout=DURATION + 60
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

    duration_ok = ELAPSED >= 0.9 * DURATION
    print(f"[DURATION_CHECK] {'PASS' if duration_ok else 'FAIL'}")

    # Sprint 6A: Extract calibration metrics
    print("\n[CALIBRATION METRICS]")
    if hasattr(orch, '_gc_collected_total') and hasattr(orch, '_gc_time_total_ms'):
        print(f"  GC collected: {orch._gc_collected_total}")
        print(f"  GC time ms: {orch._gc_time_total_ms:.1f}")

    # Compute calibration
    if hasattr(orch, '_compute_ts_calibration'):
        calib = orch._compute_ts_calibration()
        print(f"  ts_well_calibrated_fraction: {calib.get('ts_well_calibrated_fraction', 0):.3f}")
        print(f"  weighted_mean_calibration_error: {calib.get('weighted_mean_calibration_error', 0):.3f}")
        print(f"  actions_excluded_low_data: {calib.get('actions_excluded_low_data', 0)}")
        print(f"  actions_excluded_blocked: {calib.get('actions_excluded_blocked', 0)}")

    # Action counts
    if hasattr(orch, '_action_selection_counts'):
        print("\n[ACTION SELECTION]")
        for name, count in sorted(orch._action_selection_counts.items(), key=lambda x: -x[1])[:5]:
            print(f"  {name}: {count}")

    return duration_ok

if __name__ == "__main__":
    ok = asyncio.run(run_300s_test())
    sys.exit(0 if ok else 1)
