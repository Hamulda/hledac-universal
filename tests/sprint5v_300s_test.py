#!/usr/bin/env python3
"""
Sprint 5V: TRUE TIME-BOUNDED BENCHMARK - 5min test with logging control
"""
import asyncio
import logging
import os
import random
import sys
import time

# Suppress verbose logging
logging.getLogger('hledac').setLevel(logging.WARNING)

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_5min_test():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[5V] 300s OFFLINE_REPLAY TEST")
    print("=" * 60)

    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Count packets
    packets_dir = os.path.expanduser("~/.hledac/evidence_packets/shards")
    packet_count = 0
    if os.path.exists(packets_dir):
        for _root, _dirs, files in os.walk(packets_dir):
            packet_count += len([f for f in files if f.endswith('.json')])

    print(f"[PREFLIGHT] Replay packets: {packet_count}")

    # Setup evidence packets
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

    # Reset state
    if hasattr(orch, '_seen_domains'):
        orch._seen_domains.clear()
    if hasattr(orch, '_action_success_counts'):
        orch._action_success_counts.clear()
    if hasattr(orch, '_action_executed_counts'):
        orch._action_executed_counts.clear()
    if hasattr(orch, '_action_selection_counts'):
        orch._action_selection_counts.clear()

    print(f"[PREFLIGHT] TS Active: {orch._TS_SHADOW_MODE}")

    # 1800s test (30 minutes)
    DURATION = 1800
    START_TIME = time.monotonic()

    print("\n[START] 300s OFFLINE_REPLAY test...")

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

    print("\n=== RESULTS ===")
    print(f"[DATA_MODE] {result.get('data_mode', 'unknown')}")
    print(f"[ELAPSED] {ELAPSED:.1f}s (target: {DURATION}s)")
    print(f"[ITERATIONS] {result.get('iterations_completed', 0)}")
    print(f"[FINDINGS] {result.get('findings_total', 0)}")
    print(f"[SOURCES] {result.get('sources_total', 0)}")
    print(f"[HHI] {result.get('hh_index', 0):.3f}")

    # Duration check
    duration_ok = ELAPSED >= 0.9 * DURATION
    print(f"[DURATION_CHECK] {'PASS' if duration_ok else 'FAIL'}")

    return duration_ok

if __name__ == "__main__":
    ok = asyncio.run(run_5min_test())
    print(f"\n[DONE] Duration check: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)
