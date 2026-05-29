#!/usr/bin/env python3
"""
Sprint 5R: Shadow Baseline Reconfirm - 3×60s OFFLINE_REPLAY
"""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run_shadow_baseline():
    import gc
    import random

    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator
    from hledac.universal.knowledge.atomic_storage import EvidencePacket, EvidencePacketStorage

    print("=" * 60)
    print("[SPRINT 5R] SHADOW BASELINE RECONFIRM - 3×60s OFFLINE_REPLAY")
    print("=" * 60)

    results = []

    for run_idx in range(3):
        print(f"\n{'='*40}")
        print(f"[RUN {run_idx+1}/3] Starting 60s OFFLINE_REPLAY benchmark...")
        print(f"{'='*40}")

        # Fresh orchestrator per run
        orch = FullyAutonomousOrchestrator()

        # Initialize packets for OFFLINE_REPLAY
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
            # Add metadata for identity stitching (Sprint 4G)
            metadata = {
                "email": f"researcher{i}@ai-lab.org",
                "handle": f"researcher{i}",
            }
            if i % 2 == 0:
                metadata["alt_email"] = f"researcher{i}@github.com"
            packet.metadata_digests = metadata
            orch._evidence_packet_storage.store_packet(f"evidence_{i}", packet)

        random.seed(42 + run_idx)

        # Clear state
        if hasattr(orch, '_research_mgr') and orch._research_mgr:
            if hasattr(orch._research_mgr, '_findings_heap'):
                orch._research_mgr._findings_heap.clear()
            if hasattr(orch._research_mgr, '_sources_heap'):
                orch._research_mgr._sources_heap.clear()

        start = time.perf_counter()
        try:
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
        except Exception as e:
            print(f"[ERROR] Benchmark failed: {e}")
            result = {}

        elapsed = time.perf_counter() - start

        iterations = result.get('iterations_completed', 0)
        findings = result.get('findings_total', 0)
        sources = result.get('sources_total', 0)
        fps = findings / elapsed if elapsed > 0 else 0
        p95 = result.get('p95_latency_ms', 0)
        avg = result.get('avg_latency_ms', 0)
        hhi = result.get('hh_index', 0)

        # TS posteriors snapshot
        ts_posteriors = {}
        if hasattr(orch, '_ts_posteriors'):
            for action, posterior in orch._ts_posteriors.items():
                alpha = posterior.get('alpha', 1.0)
                beta = posterior.get('beta', 1.0)
                # Calculate uncertainty: sqrt(a*b / ((a+b)^2 * (a+b+1)))
                uncertainty = (alpha * beta) ** 0.5 / ((alpha + beta) ** 1.5)
                ts_posteriors[action] = {
                    'alpha': alpha,
                    'beta': beta,
                    'uncertainty': uncertainty,
                    'mean': alpha / (alpha + beta) if (alpha + beta) > 0 else 0.5
                }

        results.append({
            'run': run_idx + 1,
            'iterations': iterations,
            'findings': findings,
            'sources': sources,
            'fps': fps,
            'p95': p95,
            'avg': avg,
            'hhi': hhi,
            'elapsed': elapsed,
            'ts_posteriors': ts_posteriors,
        })

        print(f"[RUN {run_idx+1}] iters={iterations} findings={findings} sources={sources}")
        print(f"[RUN {run_idx+1}] FPS={fps:.1f} P95={p95:.1f}ms HHI={hhi:.3f}")

        gc.collect()
        await asyncio.sleep(2)

    # Summary
    print("\n" + "=" * 60)
    print("[SHADOW BASELINE] SUMMARY")
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

    print(f"\nFindings: {findings_values} (mean={statistics.mean(findings_values):.0f})")
    print(f"FPS: {fps_values} (mean={fps_mean:.1f}, CV={fps_cv:.2%})")
    print(f"P95: {p95_values} (mean={p95_mean:.1f}ms, CV={p95_cv:.2%})")
    print(f"HHI: {hhi_values} (mean={hhi_mean:.3f})")

    # Posterior uncertainty audit
    print("\n[TS POSTERIOR UNCERTAINTY AUDIT]:")
    last_result = results[-1]
    ts_posteriors = last_result.get('ts_posteriors', {})

    healthy_uncertainty_count = 0
    total_actions = len(ts_posteriors)

    for action, posterior in sorted(ts_posteriors.items(), key=lambda x: x[1].get('uncertainty', 0), reverse=True):
        uncertainty = posterior.get('uncertainty', 0)
        mean = posterior.get('mean', 0.5)
        is_healthy = uncertainty >= 0.05
        if is_healthy:
            healthy_uncertainty_count += 1
        print(f"  {action:<25}: mean={mean:.3f} uncertainty={uncertainty:.3f} {'✓' if is_healthy else '⚠️'}")

    posterior_collapse_count = total_actions - healthy_uncertainty_count
    healthy_fraction = healthy_uncertainty_count / total_actions if total_actions > 0 else 0

    print(f"\nPOSTERIOR_COLLAPSE_COUNT: {posterior_collapse_count}/{total_actions}")
    print(f"TS_HEALTHY_UNCERTAINTY_FRACTION: {healthy_fraction:.2%}")

    # Stability gates
    fps_stable = fps_cv < 0.15
    p95_stable = p95_cv < 0.30
    healthy_enough = healthy_fraction >= 0.30

    print(f"\n[Stability] FPS CV < 15%: {'✅' if fps_stable else '⚠️'}")
    print(f"[Stability] P95 CV < 30%: {'✅' if p95_stable else '⚠️'}")
    print(f"[Stability] Healthy Uncertainty ≥ 30%: {'✅' if healthy_enough else '⚠️'}")

    if fps_stable and p95_stable and healthy_enough:
        print("\n✅ SHADOW BASELINE RECONFIRMED")
    else:
        print("\n⚠️  SHADOW BASELINE NEEDS ATTENTION")

    return {
        'fps_mean': fps_mean,
        'fps_cv': fps_cv,
        'p95_mean': p95_mean,
        'p95_cv': p95_cv,
        'hhi_mean': hhi_mean,
        'findings_mean': statistics.mean(findings_values),
        'posterior_collapse_count': posterior_collapse_count,
        'total_actions': total_actions,
        'healthy_uncertainty_fraction': healthy_fraction,
    }

if __name__ == "__main__":
    result = asyncio.run(run_shadow_baseline())
    print(f"\n[BASELINE] FPS={result['fps_mean']:.1f} P95={result['p95_mean']:.1f}ms HHI={result['hhi_mean']:.3f}")
    print(f"[BASELINE] POSTERIOR_COLLAPSE_COUNT: {result['posterior_collapse_count']}/{result['total_actions']}")
    print(f"[BASELINE] TS_HEALTHY_UNCERTAINTY_FRACTION: {result['healthy_uncertainty_fraction']:.2%}")
    sys.exit(0)
