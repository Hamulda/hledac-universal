#!/usr/bin/env python3
"""
Sprint 4C Runtime Benchmark - BEFORE L1 Gate Fix
"""
import asyncio
import gc
import os
import sys
import time

import psutil

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')

from hledac.universal.autonomous_orchestrator import FullyAutonomousOrchestrator


async def run_benchmark():
    process = psutil.Process(os.getpid())

    # GC before start
    gc.collect()
    rss_start = process.memory_info().rss / 1024**2
    print(f"RSS before init: {rss_start:.1f} MB")

    orchestrator = FullyAutonomousOrchestrator()

    # Initialize
    gc.collect()
    await orchestrator.initialize()

    gc.collect()
    rss_after_init = process.memory_info().rss / 1024**2
    print(f"RSS after init: {rss_after_init:.1f} MB")

    try:
        # Run 60s benchmark with OFFLINE_REPLAY
        print("\n[BENCHMARK] Starting 60s OFFLINE_REPLAY benchmark (AFTER L1 fix)...")
        start_time = time.time()

        result = await asyncio.wait_for(
            orchestrator.run_benchmark(
                duration_seconds=60,
                prefer_offline_replay=True,
                warmup_iterations=3,
                query="artificial intelligence research"
            ),
            timeout=65
        )

        end_time = time.time()
        duration = end_time - start_time

        gc.collect()
        rss_end = process.memory_info().rss / 1024**2

        # Extract metrics
        metrics = {
            'duration_seconds': duration,
            'rss_start_mb': rss_start,
            'rss_end_mb': rss_end,
            'rss_delta_mb': rss_end - rss_start,
        }

        # Extract all available metrics from result
        for key in ['iterations_completed', 'findings_total', 'sources_total',
                    'propagation_hints_generated', 'propagation_hints_consumed',
                    'propagation_hints_expired', 'pending_hints_at_end',
                    'hh_index', 'actions_selected_distribution',
                    'data_mode', 'gate_l0_reject', 'gate_l1_echo',
                    'gate_l2_hold', 'gate_admit']:
            if key in result:
                metrics[key] = result[key]
            elif key in result.get('sprint_state', {}):
                metrics[key] = result['sprint_state'][key]

        # Print scorecard
        print("\n" + "="*60)
        print("AFTER SCORECARD (Sprint 4A baseline)")
        print("="*60)
        print(f"Duration: {metrics.get('duration_seconds', 0):.1f}s")
        print(f"Iterations: {metrics.get('iterations_completed', 0)}")
        print(f"Findings: {metrics.get('findings_total', 0)}")
        print(f"Sources: {metrics.get('sources_total', 0)}")
        print(f"Hints Generated: {metrics.get('propagation_hints_generated', 0)}")
        print(f"Hints Consumed: {metrics.get('propagation_hints_consumed', 0)}")
        print(f"Hints Expired: {metrics.get('propagation_hints_expired', 0)}")
        print(f"HH Index: {metrics.get('hh_index', 0):.3f}")
        print(f"Data Mode: {metrics.get('data_mode', 'UNKNOWN')}")

        # Gate metrics
        print("\nGate Metrics:")
        print(f"  L0 Reject: {metrics.get('gate_l0_reject', 0)}")
        print(f"  L1 Echo: {metrics.get('gate_l1_echo', 0)}")
        print(f"  L2 Hold: {metrics.get('gate_l2_hold', 0)}")
        print(f"  Admit: {metrics.get('gate_admit', 0)}")

        # Calculate admit rate if possible
        total = (metrics.get('gate_l0_reject', 0) + metrics.get('gate_l1_echo', 0) +
                 metrics.get('gate_l2_hold', 0) + metrics.get('gate_admit', 0))
        if total > 0:
            admit_rate = metrics.get('gate_admit', 0) / total * 100
            print(f"\nAdmit Rate: {admit_rate:.1f}%")

        # Actions distribution
        actions = metrics.get('actions_selected_distribution', {})
        print(f"\nActions: {actions}")

        print(f"\nMemory: {rss_start:.1f} → {rss_end:.1f} MB ({rss_end - rss_start:+.1f} MB)")
        print("="*60)

        return metrics

    except TimeoutError:
        print("[BENCHMARK] TIMEOUT - benchmark took too long")
        return {'error': 'timeout'}
    except Exception as e:
        print(f"[BENCHMARK] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return {'error': str(e)}
    finally:
        try:
            await orchestrator.cleanup()
        except Exception:
            pass

if __name__ == "__main__":
    result = asyncio.run(run_benchmark())
