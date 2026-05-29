#!/usr/bin/env python3
"""
Sprint 5Q: P95 Latency Root Cause Diagnostic

Run: python hledac/universal/tests/diagnose_p95_latency.py
"""
import asyncio
import sys
import time

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def diagnose_p95():
    import random

    import psutil
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    print("=" * 60)
    print("[DIAGNOSTIC] P95 Latency Root Cause Hunt")
    print("=" * 60)

    # Enable asyncio debug mode
    loop = asyncio.get_event_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.05  # 50ms threshold

    # Create orchestrator
    orch = FullyAutonomousOrchestrator()

    # Seed for reproducibility
    random.seed(42)

    # Reset research manager state
    if hasattr(orch, '_research_mgr') and orch._research_mgr:
        if hasattr(orch._research_mgr, '_findings_heap'):
            orch._research_mgr._findings_heap.clear()
        if hasattr(orch._research_mgr, '_sources_heap'):
            orch._research_mgr._sources_heap.clear()

    # Run SYNTHETIC_MOCK benchmark (not OFFLINE_REPLAY) for baseline latency
    print("\n[RUN] Starting 30s SYNTHETIC_MOCK benchmark (baseline)...")
    start_time = time.perf_counter()

    try:
        result = await asyncio.wait_for(
            orch.run_benchmark(
                mode="propagation_on",
                duration_seconds=30,
                warmup_iterations=0,
                query="artificial intelligence",
                prefer_offline_replay=False,  # SYNTHETIC_MOCK mode
            ),
            timeout=40
        )
    except Exception as e:
        print(f"[ERROR] Benchmark failed: {e}")
        result = {}

    elapsed = time.perf_counter() - start_time

    # Extract metrics
    print("\n" + "=" * 60)
    print("[RESULTS] P95 Diagnostic Results")
    print("=" * 60)

    iterations = result.get('iterations_completed', 0)
    findings = result.get('findings_total', 0)
    sources = result.get('sources_total', 0)
    p95_latency = result.get('p95_latency_ms', 0)
    avg_latency = result.get('avg_latency_ms', 0)

    print(f"\n[Basic] Iterations: {iterations}, Findings: {findings}, Sources: {sources}")
    print(f"[Basic] Elapsed: {elapsed:.1f}s, FPS: {iterations/elapsed:.1f}")
    print(f"[Latency] Avg: {avg_latency:.1f}ms, P95: {p95_latency:.1f}ms")

    # Action latency breakdown from benchmark results
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
        print(f"⚠️  P95 = {p95_latency:.1f}ms > 1000ms - ROOT CAUSE NEEDED")
    else:
        print(f"✅ P95 = {p95_latency:.1f}ms < 1000ms")

    # Top slowest actions by P95
    if latency_by_action_p95:
        top3 = sorted(latency_by_action_p95.items(), key=lambda x: x[1], reverse=True)[:3]
        print("\nTop 3 slowest actions (P95):")
        for action, lat in top3:
            print(f"  {action}: {lat:.1f}ms")

    # Action diversity (HHI)
    if action_selection_counts:
        total = sum(action_selection_counts.values())
        if total > 0:
            hhi = sum((c/total)**2 for c in action_selection_counts.values())
            print(f"\n[Action Diversity] HHI: {hhi:.3f}")
            if hhi > 0.7:
                print("  ⚠️  High concentration - action diversity warning")

    return {
        "p95_ms": p95_latency,
        "iterations": iterations,
        "findings": findings,
    }

if __name__ == "__main__":
    result = asyncio.run(diagnose_p95())
    sys.exit(0)
