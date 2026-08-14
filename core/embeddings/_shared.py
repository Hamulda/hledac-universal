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
import asyncio
import functools
import gc
import os
import threading
from typing import Any
from collections.abc import Callable
DEFAULT_MODEL_PATH = 'mlx-community/Hermes-3-Llama-3.2-3B-4bit'
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
        return process.memory_info().rss / 1024 ** 3
    except ImportError:
        import resource
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return usage.ru_maxrss / 1024 ** 3

def apply_task_prefix(task: str, prompt: str) -> str:
    """
    Apply task-specific prefix to prompt.

    Args:
        task: Task identifier (e.g., "extract", "classify", "summarize")
        prompt: Original prompt

    Returns:
        Prompt with task prefix applied
    """
    prefixes = {'extract': 'Extract structured information: ', 'classify': 'Classify this: ', 'summarize': 'Summarize: ', 'analyze': 'Analyze: ', 'search': 'Search: ', 'research': 'Research: '}
    prefix = prefixes.get(task.lower(), f'[{task}] ')
    return prefix + prompt

def is_embedding_model_prewarmed(model_path: str | None=None) -> bool:
    """
    Check if embedding model is prewarmed.

    Args:
        model_path: Model path to check

    Returns:
        True if model is loaded and prewarmed
    """
    prewarm_key = f"HLEDAC_EMBED_PREWARM_{hash(model_path or 'default')}"
    if os.environ.get(prewarm_key) == '1':
        return True
    try:
        import mlx.core as mx
        if hasattr(mx, '_cached_memory'):
            return mx._cached_memory > 100000000
    except Exception:  # noqa: BLE001
        pass
    return False

def encode_sync_mlx(text: str, model: Any, tokenizer: Any, max_length: int = 512) -> tuple[Any, Any] | None:
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
        import mlx.core as mx
        inputs = tokenizer(text, return_tensors='pt', max_length=max_length, truncation=True)
        input_ids = mx.array(inputs['input_ids'].tolist())
        attention_mask = mx.array(inputs['attention_mask'].tolist())
        mx.eval(input_ids, attention_mask)
        return (input_ids, attention_mask)
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
    available = max(8.0 - rss - 3.0, 0.5)
    return available

class EmbeddingCacheStats:
    """Shared cache statistics for embedding modules."""
    __slots__ = tuple(('_evictions', '_hits', '_lock', '_misses'))

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
            return {'hits': self._hits, 'misses': self._misses, 'evictions': self._evictions, 'hit_rate': hit_rate}

    def reset(self) -> None:
        with self._lock:
            self._hits = 0
            self._misses = 0
            self._evictions = 0
_global_cache_stats = EmbeddingCacheStats()

def get_cache_stats() -> dict[str, Any]:
    """Get global cache statistics."""
    return _global_cache_stats.get_stats()

def reset_cache_stats() -> None:
    """Reset global cache statistics."""
    _global_cache_stats.reset()


# Lazy lock: asyncio.Lock created on first async call, not at module import.
# ISSUE-014: asyncio.Lock() at module import is CRITICAL bug on macOS —
# Lock() without event loop is created in broken state.
# Fix: None placeholder + lazy _get_lock() helper (canonical pattern).
_CACHE_LOCK: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Lazy lock accessor — creates asyncio.Lock only when event loop exists."""
    global _CACHE_LOCK
    if _CACHE_LOCK is None:
        _CACHE_LOCK = asyncio.Lock()
    return _CACHE_LOCK


def get_cache_lock() -> asyncio.Lock:
    """Get shared async lock singleton for cache operations.

    Consolidates duplicate implementations in cache.py and pool.py.
    Uses lazy init — asyncio.Lock created only when event loop exists.
    Only one asyncio.Lock is ever created even under concurrent first-access.

    Usage:
        async with get_cache_lock():
            # critical section
    """
    return _get_lock()

async def get_cache_lock_async() -> asyncio.Lock:
    """
    Async wrapper for get_cache_lock - for contexts that require await.

    Usage:
        async with await get_cache_lock_async():
            # critical section
    """
    return get_cache_lock()