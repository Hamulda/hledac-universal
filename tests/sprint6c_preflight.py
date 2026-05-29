#!/usr/bin/env python3
"""
Sprint 6C: Preflight Truth and Gap Audit
Step 0: Verify baseline state before any fixes
"""
import asyncio
import logging
import os
import sys
import time

logging.getLogger('hledac').setLevel(logging.WARNING)
logging.basicConfig(level=logging.INFO, format='%(message)s')

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')


async def run_preflight_audit():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 70)
    print("[6C] PREFLIGHT TRUTH AND GAP AUDIT")
    print("=" * 70)

    # Initialize orchestrator
    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Count replay packets
    packets_dir = os.path.expanduser("~/.hledac/evidence_packets/shards")
    packet_count = 0
    if os.path.exists(packets_dir):
        for _root, _dirs, files in os.walk(packets_dir):
            packet_count += len([f for f in files if f.endswith('.json')])

    print("\n[A] BASELINE VERIFICATION")
    print(f"  TS Active: {_get_ts_status(orch)}")
    print(f"  Adaptive Exploration: {hasattr(orch, '_compute_adaptive_exploration_ratio')}")
    print(f"  Contextual TS: {hasattr(orch, '_get_contextual_posterior')}")
    print(f"  OFFLINE_REPLAY packets: {packet_count}")

    # Setup test packets for benchmark
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

    # Run 10s smoke test
    print("\n[E] 10S SMOKE TEST")
    DURATION = 10
    START_TIME = time.monotonic()

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

    # Network recon stats
    network_recon_runs = getattr(orch, '_network_recon_selected_count', 0)

    print(f"  ELAPSED: {ELAPSED:.1f}s")
    print(f"  ITERATIONS: {iterations}")
    print(f"  BENCHMARK_FPS: {benchmark_fps:.1f}")
    print(f"  FINDINGS: {findings}")
    print(f"  FINDINGS_FPS: {findings_fps:.1f}")
    print(f"  SOURCES: {sources}")
    print(f"  HHI: {hhi:.3f}")
    print(f"  NETWORK_RECON runs: {network_recon_runs}")

    # Anti-mock verdict
    print("\n[F] ANTI-MOCK VERDICT")
    offline_confirmed = packet_count >= 100
    anti_mock_ok = benchmark_fps < 50
    verdict = "CLEAN" if (offline_confirmed and anti_mock_ok) else "SUSPICIOUS"
    print(f"  OFFLINE_REPLAY_CONFIRMED: {'YES' if offline_confirmed else 'NO'} ({packet_count} packets)")
    print(f"  ANTI_MOCK_VERDICT: {verdict}")
    print(f"  PREFLIGHT_OK: {'YES' if (offline_confirmed and anti_mock_ok) else 'NO'}")

    # Calibration audit
    print("\n[C] CALIBRATION CONTRADICTION AUDIT")
    if hasattr(orch, '_compute_ts_calibration'):
        calib = orch._compute_ts_calibration()
        print(f"  ts_well_calibrated_fraction: {calib.get('ts_well_calibrated_fraction', 0):.3f}")
        print(f"  weighted_mean_calibration_error: {calib.get('weighted_mean_calibration_error', 0):.3f}")
        print(f"  actions_calibrated: {calib.get('actions_calibrated', 0)}")
        print(f"  actions_excluded_low_data: {calib.get('actions_excluded_low_data', 0)}")

        # Check for contradiction
        fraction = calib.get('ts_well_calibrated_fraction', 0)
        wm_error = calib.get('weighted_mean_calibration_error', 0)
        if fraction > 0.5 and wm_error > 0.3:
            print("  CALIBRATION_CONTRADICTION_CONFIRMED: YES")
        else:
            print("  CALIBRATION_CONTRADICTION_CONFIRMED: NO")
    else:
        print("  Calibration not implemented")

    # Network recon audit
    print("\n[D] NETWORK_RECON BOTTLENECK AUDIT")
    print(f"  network_recon_selected_count: {getattr(orch, '_network_recon_selected_count', 0)}")
    print(f"  network_recon_executed_count: {getattr(orch, '_network_recon_executed_count', 0)}")
    print(f"  network_recon_wildcard_hit_count: {getattr(orch, '_network_recon_wildcard_hit_count', 0)}")
    print(f"  network_recon_wildcard_miss_count: {getattr(orch, '_network_recon_wildcard_miss_count', 0)}")
    print(f"  network_recon_subdomains_suppressed_by_wildcard_total: {getattr(orch, '_network_recon_subdomains_suppressed_by_wildcard_total', 0)}")

    exec_rate = (getattr(orch, '_network_recon_executed_count', 0) / iterations * 100) if iterations > 0 else 0
    print(f"  execution_rate: {exec_rate:.2f}%")

    # Handler binding audit
    print("\n[B] HANDLER BINDING AUDIT")
    action_registry = getattr(orch, '_action_registry', {})
    lambda_count = 0
    for _name, (handler, _scorer) in action_registry.items():
        if handler and 'lambda' in str(handler)[:50]:
            lambda_count += 1
    print(f"  Lambda bindings in registry: {lambda_count}")

    return {
        'packet_count': packet_count,
        'offline_confirmed': offline_confirmed,
        'anti_mock_ok': anti_mock_ok,
        'preflight_ok': offline_confirmed and anti_mock_ok,
        'iterations': iterations,
        'benchmark_fps': benchmark_fps,
        'findings': findings,
        'findings_fps': findings_fps,
        'hhi': hhi,
        'network_recon_runs': network_recon_runs,
        'exec_rate': exec_rate
    }


def _get_ts_status(orch):
    """Check TS status."""
    shadow = getattr(orch, '_TS_SHADOW_MODE', True)
    return "ACTIVE" if not shadow else "SHADOW"


if __name__ == "__main__":
    result = asyncio.run(run_preflight_audit())
    print("\n" + "=" * 70)
    print("PREFLIGHT COMPLETE")
    print("=" * 70)
