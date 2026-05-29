#!/usr/bin/env python3
"""
Sprint 5U: 30s Quick Test - Debug Run
"""
import asyncio
import random
import sys

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

async def run():
    from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator

    print("[TEST] Starting 30s test...")

    orch = FullyAutonomousOrchestrator()
    random.seed(42)

    # Quick 30s test
    try:
        result = await asyncio.wait_for(
            orch.run_benchmark(
                mode="propagation_on",
                duration_seconds=30,
                warmup_iterations=0,
                query="artificial intelligence",
                prefer_offline_replay=True,
            ),
            timeout=45
        )

        print("\n=== RESULTS ===")
        print(f"data_mode: {result.get('data_mode')}")
        print(f"iterations: {result.get('iterations_completed')}")
        print(f"findings: {result.get('findings_total')}")
        print(f"sources: {result.get('sources_total')}")
        print(f"HHI: {result.get('hh_index', 0):.3f}")
        print(f"actions: {result.get('actions_selected_distribution', {})}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run())
