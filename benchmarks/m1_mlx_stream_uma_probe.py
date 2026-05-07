#!/usr/bin/env python3
"""
Sprint F219L: MLX Stream + UMA Guard Reality Probe

Dry-run default: fake MLX, no model load. Reports RSS/Metal before/after.
Requires --live flag for real MLX operations.

Usage:
    python benchmarks/m1_mlx_stream_uma_probe.py              # dry-run (default)
    python benchmarks/m1_mlx_stream_uma_probe.py --live      # real MLX
    python benchmarks/m1_mlx_stream_uma_probe.py --sizes 16,32,64
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

# Ensure hledac.universal importable
sys.path.insert(0, str(Path(__file__).parents[1]))

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False


def get_rss_mb() -> float:
    if not PSUTIL_AVAILABLE:
        return 0.0
    return psutil.Process().memory_info().rss / (1024 * 1024)


def get_metal_mb() -> float | None:
    if not MLX_AVAILABLE:
        return None
    try:
        return mx.metal.get_active_memory() / (1024 * 1024)
    except Exception:
        return None


def get_memory_snapshot() -> dict:
    return {
        "rss_mb": get_rss_mb(),
        "metal_mb": get_metal_mb(),
        "mlx_available": MLX_AVAILABLE,
    }


def print_snapshot(label: str, snap: dict):
    parts = [f"{label}: RSS={snap['rss_mb']:.1f}MB"]
    if snap.get('metal_mb') is not None:
        parts.append(f"Metal={snap['metal_mb']:.1f}MB")
    parts.append(f"MLX={'yes' if snap['mlx_available'] else 'no'}")
    print("  " + " | ".join(parts))


def main():
    parser = argparse.ArgumentParser(description="MLX Stream + UMA Guard probe")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run real MLX operations (requires mlx-embeddings model)"
    )
    parser.add_argument(
        "--sizes",
        default="16,32,64",
        help="Comma-separated batch sizes to test (default: 16,32,64)"
    )
    args = parser.parse_args()

    sizes = [int(s) for s in args.sizes.split(",")]

    print("=" * 60)
    print("MLX Stream + UMA Guard Reality Probe")
    print("=" * 60)
    print(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    print(f"Sizes: {sizes}")
    print()

    before = get_memory_snapshot()
    print_snapshot("Before", before)
    print()

    if not args.live:
        print("[DRY-RUN] Skipping real MLX operations (use --live for real)")
        print()
        print("Memory changes in dry-run reflect gc/psutil overhead only.")
        print()
        after = get_memory_snapshot()
        print_snapshot("After", after)
        print()
        delta_rss = after['rss_mb'] - before['rss_mb']
        print(f"RSS delta: {delta_rss:+.1f}MB")
        print()
        print("SUCCESS: dry-run completed without model load")
        return

    # LIVE MODE
    print("[LIVE] Running MLX embedding operations...")
    print()

    if not MLX_AVAILABLE:
        print("ERROR: MLX not available. Install: pip install mlx mlx-embeddings")
        sys.exit(1)

    try:
        from hledac.universal.core.mlx_embeddings import get_embedding_manager

        manager = get_embedding_manager()

        for size in sizes:
            print(f"\n--- Batch size: {size} ---")
            gc.collect()
            mx.eval([])

            snap_before = get_memory_snapshot()
            print_snapshot("Before", snap_before)

            try:
                texts = [f"test document number {i}" for i in range(size)]
                embeddings = manager.encode(texts, batch_size=size)
                print(f"  Embeddings shape: {embeddings.shape}")
            except Exception as e:
                print(f"  ERROR: {e}")
                continue

            gc.collect()
            mx.eval([])

            snap_after = get_memory_snapshot()
            print_snapshot("After", snap_after)

            delta_rss = snap_after['rss_mb'] - snap_before['rss_mb']
            delta_metal = (snap_after['metal_mb'] or 0) - (snap_before['metal_mb'] or 0)
            print(f"  Delta: RSS={delta_rss:+.1f}MB Metal={delta_metal:+.1f}MB")

        # Unload
        manager.unload()
        gc.collect()
        mx.eval([])

        final = get_memory_snapshot()
        print()
        print_snapshot("Final", final)
        print()
        print("SUCCESS: live run completed")

    except Exception as e:
        print(f"ERROR during live run: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()