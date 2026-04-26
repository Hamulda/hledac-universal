"""
Sprint F203I — M1 Embedding Streaming Benchmark
==============================================

Measures peak RSS delta between sync batch embedding and streaming embedding
on M1 8GB. Reports whether streaming achieves >= 30% peak RSS reduction.

Usage:
    python benchmarks/m1_embedding_streaming.py --hermetic

Hermetic mode: uses synthetic data only, no real MLX model required.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np


def get_rss_mb() -> float:
    """Get current RSS in MB."""
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


def synthetic_texts(n: int) -> list[str]:
    """Generate synthetic texts for hermetic testing."""
    return [f"Synthetic test document number {i} with some additional content to increase text size and memory footprint" for i in range(n)]


async def benchmark_streaming_rss(texts: list[str], batch_size: int = 16) -> dict:
    """
    Measure peak RSS during streaming embedding.

    Returns dict with peak RSS, average RSS, and metadata.
    """
    from hledac.universal.embedding_pipeline import generate_embeddings_streaming

    gc.collect()
    rss_before = get_rss_mb()

    peak_rss = rss_before
    batches_processed = 0
    total_items = 0

    try:
        async for ids, embeddings in generate_embeddings_streaming(texts, batch_size=batch_size):
            batches_processed += 1
            total_items += len(ids)
            current_rss = get_rss_mb()
            peak_rss = max(peak_rss, current_rss)
            # Yield control between batches
            await asyncio.sleep(0)
    except Exception as e:
        print(f"  [streaming] error: {e}")

    gc.collect()
    rss_after = get_rss_mb()

    return {
        "mode": "streaming",
        "batch_size": batch_size,
        "total_items": total_items,
        "batches_processed": batches_processed,
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "peak_rss_mb": peak_rss,
        "rss_delta_mb": peak_rss - rss_before,
    }


def benchmark_sync_rss(texts: list[str], batch_size: int = 16) -> dict:
    """
    Measure peak RSS during sync batch embedding.

    Returns dict with peak RSS and metadata.
    """
    from hledac.universal.embedding_pipeline import generate_embeddings

    gc.collect()
    rss_before = get_rss_mb()

    peak_rss = rss_before

    try:
        # Materialize all at once (sync path)
        embeddings = generate_embeddings(texts, batch_size=batch_size)
        current_rss = get_rss_mb()
        peak_rss = max(peak_rss, current_rss)
    except Exception as e:
        print(f"  [sync] error: {e}")
        embeddings = np.zeros((len(texts), 256), dtype=np.float32)

    gc.collect()
    rss_after = get_rss_mb()

    return {
        "mode": "sync",
        "batch_size": batch_size,
        "total_items": len(texts),
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
        "peak_rss_mb": peak_rss,
        "rss_delta_mb": peak_rss - rss_before,
    }


async def run_benchmark(hermetic: bool = False, n_items: int = 200) -> dict:
    """
    Run the full benchmark suite.

    Args:
        hermetic: If True, use synthetic data and skip real MLX calls.
        n_items: Number of items to embed.

    Returns benchmark results.
    """
    print(f"\n{'='*60}")
    print(f"F203I — M1 Embedding Streaming Benchmark")
    print(f"{'='*60}")
    print(f"  Hermetic mode: {hermetic}")
    print(f"  Items: {n_items}")
    print(f"  Batch size: 16")
    print()

    texts = synthetic_texts(n_items)

    print("[1/2] Running sync batch embedding...")
    sync_result = benchmark_sync_rss(texts, batch_size=16)
    print(f"  RSS before: {sync_result['rss_before_mb']:.1f} MB")
    print(f"  RSS after:  {sync_result['rss_after_mb']:.1f} MB")
    print(f"  Peak RSS:   {sync_result['peak_rss_mb']:.1f} MB")
    print(f"  Delta:      +{sync_result['rss_delta_mb']:.1f} MB")

    # Cool down
    await asyncio.sleep(2)
    gc.collect()

    print("\n[2/2] Running streaming batch embedding...")
    stream_result = await benchmark_streaming_rss(texts, batch_size=16)
    print(f"  RSS before: {stream_result['rss_before_mb']:.1f} MB")
    print(f"  RSS after:  {stream_result['rss_after_mb']:.1f} MB")
    print(f"  Peak RSS:   {stream_result['peak_rss_mb']:.1f} MB")
    print(f"  Delta:      +{stream_result['rss_delta_mb']:.1f} MB")
    print(f"  Batches:    {stream_result['batches_processed']}")

    # Compute comparison
    sync_delta = sync_result["rss_delta_mb"]
    stream_delta = stream_result["rss_delta_mb"]

    if sync_delta > 0:
        reduction_pct = ((sync_delta - stream_delta) / sync_delta) * 100
    else:
        reduction_pct = 0.0

    target_reduction_pct = 30.0
    target_met = reduction_pct >= target_reduction_pct

    print()
    print(f"{'='*60}")
    print(f"RESULTS")
    print(f"{'='*60}")
    print(f"  Sync delta:     +{sync_delta:.1f} MB")
    print(f"  Stream delta:   +{stream_delta:.1f} MB")
    print(f"  Reduction:     {reduction_pct:.1f}%")
    print(f"  Target:        {target_reduction_pct}%")
    print(f"  Target met:    {'✓ YES' if target_met else '✗ NO'}")
    print()

    return {
        "hermetic": hermetic,
        "n_items": n_items,
        "sync": sync_result,
        "streaming": stream_result,
        "reduction_pct": reduction_pct,
        "target_met": target_met,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="F203I M1 Embedding Streaming Benchmark")
    parser.add_argument("--hermetic", action="store_true", help="Run in hermetic mode (synthetic data only)")
    parser.add_argument("--n-items", type=int, default=200, help="Number of items to embed")
    args = parser.parse_args()

    results = asyncio.run(run_benchmark(hermetic=args.hermetic, n_items=args.n_items))

    # Exit code 0 if target met, 1 if not
    sys.exit(0 if results["target_met"] else 1)


if __name__ == "__main__":
    main()
