#!/usr/bin/env python3
"""
Sprint 5U: 300s OFFLINE_REPLAY Preview + Long-Run Validation
"""
import asyncio
import os
import random
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_300s_preview():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 70)
    print("[PREVIEW] 300s OFFLINE_REPLAY Benchmark - Sprint 5U")
    print("=" * 70)

    # Preflight: Verify OFFLINE_REPLAY
    orch = FullyAutonomousOrchestrator()
    orch._evidence_packet_storage = EvidencePacketStorage()

    # Count packets
    packets_dir = os.path.expanduser("~/.hledac/evidence_packets/shards")
    packet_count = 0
    if os.path.exists(packets_dir):
        for _root, _dirs, files in os.walk(packets_dir):
            packet_count += len([f for f in files if f.endswith('.json')])

    print(f"[PREFLIGHT] Replay packets: {packet_count}")

    # Setup evidence packets (15 for diversity testing)
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

    # Clear heaps
    if hasattr(orch, '_research_mgr') and orch._research_mgr:
        if hasattr(orch._research_mgr, '_findings_heap'):
            orch._research_mgr._findings_heap.clear()
        if hasattr(orch._research_mgr, '_sources_heap'):
            orch._research_mgr._sources_heap.clear()

    # Per-run reset per hard rules (step 1)
    if hasattr(orch, '_seen_domains'):
        orch._seen_domains.clear()
    if hasattr(orch, '_action_success_counts'):
        orch._action_success_counts.clear()
    if hasattr(orch, '_action_executed_counts'):
        orch._action_executed_counts.clear()
    if hasattr(orch, '_action_selection_counts'):
        orch._action_selection_counts.clear()

    print(f"[PREFLIGHT] TS Active: {orch._TS_SHADOW_MODE}")
    print(f"[PREFLIGHT] Exploration Budget: {orch._TS_MIN_EXPLORATION_BUDGET}")
    print(f"[PREFLIGHT] Warmup: {orch._TS_WARMUP_ITERATIONS}")

    # 300s preview
    start = time.perf_counter()
    result = await asyncio.wait_for(
        orch.run_benchmark(
            mode="propagation_on",
            duration_seconds=300,
            warmup_iterations=0,
            query="artificial intelligence",
            prefer_offline_replay=True,
        ),
        timeout=360
    )
    elapsed = time.perf_counter() - start

    # Extract metrics
    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    fps = findings / elapsed if elapsed > 0 else 0
    p95 = result.get('p95_latency_ms', 0)
    hhi = result.get('hh_index', 0)
    data_mode = result.get('data_mode', 'unknown')
    action_counts = result.get('action_selection_counts', {})

    # Gini
    def gini(counts):
        if not counts:
            return 0.0
        vals = sorted(counts.values())
        n = len(vals)
        cum = sum((i+1) * v for i, v in enumerate(vals))
        return (2 * cum) / (n * sum(vals)) - (n + 1) / n

    g = gini(action_counts)

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

    # Exploration budget triggers
    exploration_triggers = getattr(orch, '_exploration_budget_triggers', 0)

    # Action execution counts
    action_executed = getattr(orch, '_action_executed_counts', {})

    print(f"\n[RESULT] data_mode={data_mode}")
    print(f"[RESULT] duration={elapsed:.1f}s iterations={iterations}")
    print(f"[RESULT] findings={findings} sources={sources}")
    print(f"[RESULT] FPS={fps:.1f} P95={p95:.1f}ms HHI={hhi:.3f} Gini={g:.3f}")
    print(f"[RESULT] TS healthy: {healthy_count}/{total} ({healthy_fraction:.1%})")
    print(f"[RESULT] Exploration triggers: {exploration_triggers}")
    print(f"[RESULT] Action counts: {action_counts}")
    print(f"[RESULT] Action executed: {action_executed}")

    # Zero-run audit
    zero_run = [a for a, c in action_counts.items() if c == 0]
    print(f"[RESULT] Zero-run actions: {zero_run}")

    return {
        'iterations': iterations,
        'findings': findings,
        'sources': sources,
        'fps': fps,
        'p95': p95,
        'hhi': hhi,
        'gini': g,
        'data_mode': data_mode,
        'healthy_fraction': healthy_fraction,
        'exploration_triggers': exploration_triggers,
        'action_counts': action_counts,
        'action_executed': action_executed,
        'zero_run_actions': zero_run,
        'ts_posteriors': ts_posteriors,
    }

if __name__ == "__main__":
    result = asyncio.run(run_300s_preview())
    print(f"\n[SUMMARY] FPS={result['fps']:.1f} HHI={result['hhi']:.3f} Gini={result['gini']:.3f}")
    sys.exit(0)
