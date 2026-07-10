"""
Shared utilities for embeddings modules.

Consolidates duplicate implementations detected by vibedrift:
- _encode_sync() - MLX encoding with fallback
- _get_rss_gb() - memory measurement
- apply_task_prefix() - task prefixing for prompts
- is_embedding_model_prewarmed() - prewarm state checking
- get_cache_lock() - async lock singleton pattern

This module is auto-imported by embeddings/manager.py and core/mlx_embeddings.py
to eliminate semantic duplication while maintaining M1 8GB RAM constraints.
"""
from __future__ import annotations

import asyncio
import gc
import os
import threading
import time
from typing import Any, Callable

import mlx.core as mx
from mlx_lm import generate, stream_generate

# Shared constants
DEFAULT_MODEL_PATH = "mlx-community/Hermes-3-Llama-3.2-3B-4bit"
DEFAULT_MAX_KV_SIZE = 8192
DEFAULT_KV_BITS = 4


def get_rss_gb() -> float:
    """
    Get current RSS memory in GB.

    Returns:
        RSS in gigabytes
    """
    try:
        import psutil
        process = psutil.Process(os.getpid())
        return process.memory_info().rss / (1024**3)
    except ImportError:
        # Fallback using resource module
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / (1024**3)


def apply_task_prefix(task: str, prompt: str) -> str:
    """
    Apply task-specific prefix to prompt.

    Args:
        task: Task identifier (e.g., "extract", "classify", "summarize")
        prompt: Original prompt

    Returns:
        Prompt with task prefix applied
    """
    prefixes = {
        "extract": "Extract structured information: ",
        "classify": "Classify this: ",
        "summarize": "Summarize: ",
        "analyze": "Analyze: ",
        "search": "Search: ",
        "research": "Research: ",
    }
    prefix = prefixes.get(task.lower(), f"[{task}] ")
    return prefix + prompt


def is_embedding_model_prewarmed(model_path: str | None = None) -> bool:
    """
    Check if embedding model is prewarmed.

    Args:
        model_path: Model path to check

    Returns:
        True if model is loaded and prewarmed
    """
    # Check environment variable for prewarm state
    prewarm_key = f"HLEDAC_EMBED_PREWARM_{hash(model_path or 'default')}"
    if os.environ.get(prewarm_key) == "1":
        return True

    # Check if MLX model is loaded (heuristic)
    try:
        import mlx.core as mx
        # If mx has loaded a model, it will have non-trivial memory usage
        if hasattr(mx, '_cached_memory'):
            return mx._cached_memory > 100_000_000  # > 100MB heuristic
    except Exception:
        pass

    return False


def encode_sync_mlx(
    text: str,
    model: Any,
    tokenizer: Any,
    max_length: int = 512,
) -> tuple[mx.array, mx.array] | None:
    """
    Synchronous MLX encoding with proper evaluation.

    M1 Metal memory management:
    - mx.eval() forces GPU sync before memory measurement
    - Metal cache is managed separately via resource_governor

    Args:
        text: Text to encode
        model: MLX model
        tokenizer: Tokenizer
        max_length: Maximum sequence length

    Returns:
        Tuple of (input_ids, attention_mask) or None on failure
    """
    try:
        inputs = tokenizer(text, return_tensors="pt", max_length=max_length, truncation=True)

        # Convert to MLX arrays
        input_ids = mx.array(inputs["input_ids"].tolist())
        attention_mask = mx.array(inputs["attention_mask"].tolist())

        # Force evaluation for Metal memory management
        mx.eval(input_ids, attention_mask)

        return input_ids, attention_mask
    except Exception:
        return None


def estimate_available_memory() -> float:
    """
    Estimate available memory for embeddings in GB.

    M1 8GB UMA constrained:
    - System uses ~2.5GB
    - Budget: 8GB - 2.5GB - 2GB (LLM) - 0.75GB (KV cache) = ~2.75GB max

    Returns:
        Available memory in GB
    """
    rss = get_rss_gb()
    # Conservative estimate: 8GB total - 2.5GB system - 2GB LLM = 3.5GB
    # But be more aggressive if under memory pressure
    available = max(8.0 - rss - 3.0, 0.5)
    return available


class EmbeddingCacheStats:
    """Shared cache statistics for embedding modules."""

    def __init__(self):
        self._hits = 0
        self._misses = 0
        self._evictions = 0
        self._lock = threading.Lock()

    def record_hit(self) -> None:
        with self._lock:
            self._hits += 1

    def record_miss(self) -> None:
        with self._lock:
            self._misses += 1

    def record_eviction(self) -> None:
        with self._lock:
            self._evictions += 1

    def get_stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            hit_rate = self._hits / total if total > 0 else 0.0
            return {
                'hits': self._hits,
                'misses': self._misses,
                'evictions': self._evictions,
                'hit_rate': hit_rate,
            }

    def reset(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0


# Global cache stats instance
_global_cache_stats = EmbeddingCacheStats()


def get_cache_stats() -> dict[str, Any]:
    """Get global cache statistics."""
    return _global_cache_stats.get_stats()


def reset_cache_stats() -> None:
    """Reset global cache statistics."""
    _global_cache_stats.reset()


# === Shared async lock singleton for cache operations ===
_SHARED_CACHE_LOCK: asyncio.Lock | None = None


def get_cache_lock() -> asyncio.Lock:
    """
    Get shared async lock singleton for cache operations.

    Consolidates duplicate implementations in cache.py and pool.py.
    Uses module-level singleton pattern for efficient locking.

    Usage:
        async with get_cache_lock():
            # critical section
    """
    global _SHARED_CACHE_LOCK
    if _SHARED_CACHE_LOCK is None:
        _SHARED_CACHE_LOCK = asyncio.Lock()
    return _SHARED_CACHE_LOCK


async def get_cache_lock_async() -> asyncio.Lock:
    """
    Async wrapper for get_cache_lock - for contexts that require await.

    Usage:
        async with await get_cache_lock_async():
            # critical section
    """
    return get_cache_lock()
