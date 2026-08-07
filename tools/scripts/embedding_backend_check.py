#!/usr/bin/env python3
"""
Embedding Backend Check — F228B/F216A
Reports current backend (ANE/CPU/hash), latency for 100 texts, UMA usage.

Usage:
    python scripts/embedding_backend_check.py
    python scripts/embedding_backend_check.py --warm  # warm-up before measure
    python scripts/embedding_backend_check.py --batch 512  # custom batch size
"""


import logging
import sys
import time
from pathlib import Path

import psutil

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

# NOTE: basicConfig removed — use utils.logging_config.configure_logging() for structured logging.
# This script uses stdlib logging since it's a standalone tool.
logger = logging.getLogger("embedding_backend_check")


def get_uma_usage() -> dict:
    """Get UMA memory snapshot."""
    mem = psutil.virtual_memory()
    return {
        "total_gib": round(mem.total / 1024**3, 2),
        "used_gib": round(mem.used / 1024**3, 2),
        "percent": mem.percent,
        "swap_used_mb": psutil.swap_memory().used / 1024**2,
    }


def get_backend() -> str:
    """Detect active embedding backend."""
    # Check 1: MLX ModernBERT via EmbeddingRouter (primary path)
    try:
        from hledac.universal.embedding_pipeline import get_embedding_backend
        be = get_embedding_backend()
        if be in ("mlx", "mlx_manager"):
            return "mlx"
    except Exception:  # noqa: BLE001
        pass

    # Check 2: psutil RAM — if >80% probably can't load MLX
    uma = get_uma_usage()
    if uma["percent"] > 80:
        return "cpu_fallback"

    # Check 3: FastEmbed available (CPU fallback)
    try:
        from fastembed import TextEmbedding as _  # noqa: F401 — availability check only
        return "cpu_fallback"  # FastEmbed on CPU
    except ImportError:  # noqa: BLE001
        pass

    return "hash_only"


def measure_latency(n_texts: int = 100, batch_size: int = 32, warm_up: bool = False) -> dict:
    """Measure embedding latency for n_texts."""
    texts = [f"Synthetic benchmark text number {i} for embedding latency measurement." for i in range(n_texts)]

    # Warm-up
    if warm_up:
        try:
            from fastembed import TextEmbedding
            model = TextEmbedding("BAAI/bge-small-en-v1.5")
            list(model.embed(["warmup"]))
            logger.info("Warm-up complete")
        except Exception as e:
            logger.warning("Warm-up failed: %s", e)

    # Measure
    try:
        from fastembed import TextEmbedding
        model = TextEmbedding("BAAI/bge-small-en-v1.5")

        start = time.monotonic()
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            emb = list(model.embed(batch))
            all_embeddings.extend(emb)
        elapsed_s = time.monotonic() - start

        latency_ms = (elapsed_s / n_texts) * 1000
        docs_per_sec = n_texts / elapsed_s

        return {
            "backend": "cpu_fallback",
            "n_texts": n_texts,
            "batch_size": batch_size,
            "elapsed_s": round(elapsed_s, 3),
            "latency_ms": round(latency_ms, 2),
            "docs_per_sec": round(docs_per_sec, 1),
            "embedding_dim": len(all_embeddings[0]) if all_embeddings else 0,
            "success": True,
        }
    except Exception as e:
        return {
            "backend": "unknown",
            "n_texts": n_texts,
            "success": False,
            "error": str(e),
        }


def print_report(backend: str, latency: dict, uma: dict) -> None:
    """Print human-readable report."""
    print("\n=== Embedding Backend Check ===")
    print(f"  Backend:      {backend}")
    print(f"  UMA usage:    {uma['percent']:.1f}% ({uma['used_gib']:.1f}/{uma['total_gib']} GB)")
    print(f"  Swap used:    {uma['swap_used_mb']:.0f} MB")

    if latency.get("success"):
        print(f"  Latency:      {latency['latency_ms']:.1f} ms/text ({latency['n_texts']} texts in {latency['elapsed_s']:.3f}s)")  # noqa: E501
        print(f"  Throughput:   {latency['docs_per_sec']:.0f} docs/s")
        print(f"  Embed dim:    {latency['embedding_dim']}")
    else:
        print(f"  Latency:      FAILED — {latency.get('error', 'unknown')}")

    # MLX-specific info
    if backend == "mlx":
        try:
            from hledac.universal.embedding_pipeline import get_embedding_backend
            be = get_embedding_backend()
            print(f"  MLX backend:   {be}")
        except Exception:  # noqa: BLE001
            pass

    print()


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Embedding backend check")
    parser.add_argument("--warm", action="store_true", help="Warm-up before measurement")
    parser.add_argument("--batch", type=int, default=32, help="Batch size (default: 32)")
    parser.add_argument("--n", type=int, default=100, help="Number of texts (default: 100)")
    args = parser.parse_args()

    uma = get_uma_usage()
    backend = get_backend()
    latency = measure_latency(n_texts=args.n, batch_size=args.batch, warm_up=args.warm)
    print_report(backend, latency, uma)

    # Exit code: 0 if latency measured, 1 if failed
    return 0 if latency.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
