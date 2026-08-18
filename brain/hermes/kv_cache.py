"""
brain/hermes/kv_cache.py — KV Cache Management
===========================================

PEP 698: Extracted from brain/deephermes3_engine.py.

Handles:
- KV cache pool management
- Prefix cache for system prompts
- Session cache for conversation contexts
- Warmup cache persistence

M1 8GB: Strict memory limits, LRU eviction, adaptive quantization.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

# FIXED: Use absolute import for utils module
from hledac.universal.utils.lru_cache import LRUCache, SlidingWindowKVCache

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# Constants for M1 8GB constraints
DEFAULT_KV_POOL_MAXSIZE = 4
DEFAULT_KV_POOL_MEMORY_MB = 256
DEFAULT_SESSION_CACHE_MAXSIZE = 8
DEFAULT_SESSION_CACHE_MEMORY_MB = 128
DEFAULT_PREFIX_CACHE_MAXSIZE = 64


def init_kv_cache_pool(maxsize: int, memory_mb: int) -> SlidingWindowKVCache:
    """
    Initialize KV cache pool with M1 8GB constraints.
    
    Args:
        maxsize: Maximum number of items in pool
        memory_mb: Memory limit in MB
        
    Returns:
        Initialized SlidingWindowKVCache
    """
    return SlidingWindowKVCache(
        max_size=maxsize,
        window_tokens=16,
        decay_base=0.85,
        token_interval_s=5.0,
        thread_safe=False,
    )


def init_session_cache(maxsize: int) -> LRUCache:
    """
    Initialize session cache.
    
    Args:
        maxsize: Maximum cache size
        
    Returns:
        Initialized LRUCache
    """
    return LRUCache(max_size=maxsize)


def init_prefix_cache(maxsize: int) -> LRUCache:
    """
    Initialize prefix cache for system prompts.
    
    Args:
        maxsize: Maximum cache size
        
    Returns:
        Initialized LRUCache
    """
    return LRUCache(max_size=maxsize)


def get_prefix_cache(engine, system_prompt: str) -> Any | None:
    """
    Get cached KV cache for system prompt prefix.
    
    Args:
        engine: DeepHermes3Engine instance
        system_prompt: System prompt string
        
    Returns:
        Cached KV cache or None
    """
    prompt_hash = engine._compute_system_prompt_hash(system_prompt)
    
    if prompt_hash in engine._prefix_cache:
        engine._prefix_cache_stats["prefix_cache_hits"] += 1
        return engine._prefix_cache[prompt_hash]
    
    engine._prefix_cache_stats["prefix_cache_misses"] += 1
    return None


def store_prefix_cache(engine, system_prompt: str, kv_cache: Any) -> None:
    """
    Store KV cache for system prompt prefix.
    
    Args:
        engine: DeepHermes3Engine instance
        system_prompt: System prompt string
        kv_cache: KV cache to store
    """
    prompt_hash = engine._compute_system_prompt_hash(system_prompt)
    
    # Evict if at capacity
    if len(engine._prefix_cache) >= engine._prefix_cache_maxsize:
        engine._prefix_cache_stats["prefix_cache_evictions"] += 1
    
    engine._prefix_cache[prompt_hash] = kv_cache
    engine._prefix_cache_stats["prefix_cache_size"] = len(engine._prefix_cache)


def get_session_cache(
    engine,
    formatted_prompt: str,
) -> tuple[Any, str] | None:
    """
    Get cached session KV cache.
    
    Args:
        engine: DeepHermes3Engine instance
        formatted_prompt: Formatted prompt string
        
    Returns:
        Tuple of (kv_cache, prompt_hash) or None
    """
    from hledac.universal.utils.hash import xxh3_64_hex
    
    prompt_hash = xxh3_64_hex(formatted_prompt)
    
    if prompt_hash in engine._session_cache_pool:
        engine._session_cache_stats["session_cache_hits"] += 1
        cache_entry = engine._session_cache_pool[prompt_hash]
        kv_cache = cache_entry[0]  # (kv_cache, hash, timestamp, size)
        return (kv_cache, prompt_hash)
    
    engine._session_cache_stats["session_cache_misses"] += 1
    return None


def store_session_cache(
    engine,
    formatted_prompt: str,
    kv_cache: Any,
    cache_size: int,
) -> None:
    """
    Store session KV cache.
    
    Args:
        engine: DeepHermes3Engine instance
        formatted_prompt: Formatted prompt string
        kv_cache: KV cache to store
        cache_size: Size of cache in bytes
    """
    from hledac.universal.utils.hash import xxh3_64_hex
    
    prompt_hash = xxh3_64_hex(formatted_prompt)
    timestamp = time.monotonic()
    
    # Evict if at capacity
    if len(engine._session_cache_pool) >= engine._session_cache_maxsize:
        engine._session_cache_stats["session_cache_evictions"] += 1
    
    engine._session_cache_pool[prompt_hash] = (kv_cache, prompt_hash, timestamp, cache_size)


def get_kv_cache_kwargs(
    engine,
    input_tokens: int | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """
    Build kwargs for KV cache configuration.
    
    Args:
        engine: DeepHermes3Engine instance
        input_tokens: Input token count
        max_tokens: Maximum tokens to generate
        
    Returns:
        Dictionary of kwargs for generation
    """
    kwargs: dict[str, Any] = {}
    
    if not engine._kv_cache_enabled:
        return kwargs
    
    # Configure KV cache bits
    kv_bits = engine._get_adaptive_kv_bits()
    if kv_bits != 4:  # Default is 4
        kwargs["kv_bits"] = kv_bits
    
    # Configure cache size
    if input_tokens is not None:
        max_kv = min(input_tokens + (max_tokens or 512), engine._max_kv_size)
        kwargs["max_kv_size"] = max_kv
    
    return kwargs


def resolve_kv_cache(
    engine,
    system_msg: str | None,
    formatted_prompt: str,
) -> Any | None:
    """
    Resolve KV cache for prompt (prefix or session).
    
    Args:
        engine: DeepHermes3Engine instance
        system_msg: System message
        formatted_prompt: Formatted prompt string
        
    Returns:
        KV cache if available, None otherwise
    """
    # Try prefix cache first
    if system_msg:
        prefix_cache = get_prefix_cache(engine, system_msg)
        if prefix_cache is not None:
            return prefix_cache
    
    # Try session cache
    session_cache = get_session_cache(engine, formatted_prompt)
    if session_cache is not None:
        return session_cache[0]
    
    return None


def measure_kv_cache_bytes(cache: Any, tokens: list[int]) -> int:
    """
    Measure KV cache memory usage.
    
    Args:
        cache: KV cache object
        tokens: Token list
        
    Returns:
        Estimated size in bytes
    """
    try:
        if hasattr(cache, "raw_elements"):
            # MLX cache structure
            return len(cache.raw_elements()) * 8  # Approximate
    except Exception:
        pass
    
    # Fallback estimate
    return len(tokens) * 1024


def invalidate_all_prompt_caches(engine, reason: str) -> None:
    """
    Invalidate all prompt caches.
    
    Args:
        engine: DeepHermes3Engine instance
        reason: Reason for invalidation
    """
    logger.info(f"[CACHE] Invalidating all caches: {reason}")
    
    engine._prefix_cache.clear()
    engine._session_cache_pool.clear()
    engine._kv_cache_pool.clear()
    
    engine._prefix_cache_stats["prefix_cache_evictions"] += 1
    engine._session_cache_stats["session_cache_evictions"] += 1
    engine._kv_cache_pool_stats["pool_evictions"] += 1
    
    engine._telemetry_counters["cache_invalidation_count"] += 1


# Warmup cache management
WARMUP_CACHE_DIR = Path.home() / ".hledac" / "cache" / "warmup"


def get_warmup_cache_path(
    system_prompt: str,
    few_shot_examples: list | None = None,
) -> Path:
    """
    Compute cache file path from system prompt fingerprint.
    
    Args:
        system_prompt: System prompt
        few_shot_examples: Optional few-shot examples
        
    Returns:
        Path to cache file
    """
    from hledac.universal.utils.hash import xxh3_64_hex
    
    parts = [system_prompt]
    if few_shot_examples:
        for ex in few_shot_examples[:3]:
            parts.append(f"{ex.get('user', '')}|{ex.get('assistant', '')}")
    
    canonical = "\n".join(parts)
    prompt_hash = xxh3_64_hex(canonical)
    
    WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return WARMUP_CACHE_DIR / f"warmup_{prompt_hash}.safetensors"


async def warmup_or_skip(
    engine,
    system_prompt: str,
    few_shot_examples: list | None = None,
) -> bool:
    """
    Skip warmup if unexpired cache exists.
    
    Args:
        engine: DeepHermes3Engine instance
        system_prompt: System prompt
        few_shot_examples: Optional few-shot examples
        
    Returns:
        True if cache hit (warmup skipped), False if cache miss
    """
    cache_path = get_warmup_cache_path(system_prompt, few_shot_examples)
    
    if not cache_path.exists():
        return False
    
    expected_hash = cache_path.stem.removeprefix("warmup_")
    
    try:
        if await engine._restore_warmup_cache(cache_path, expected_hash):
            logger.info(
                f"[WARMUP] Cache hit: {cache_path.name} "
                f"(hash={expected_hash[:8]})"
            )
            return True
    except Exception:
        pass
    
    try:
        cache_path.unlink(missing_ok=True)
    except Exception:
        pass
    
    return False


async def restore_warmup_cache(
    engine,
    cache_path: Path,
    prompt_hash: str,
) -> bool:
    """
    Restore warmup cache from disk.
    
    Args:
        engine: DeepHermes3Engine instance
        cache_path: Path to cache file
        prompt_hash: Expected hash for validation
        
    Returns:
        True if restored successfully
    """
    try:
        # Load cached KV cache
        import mlx.core as mx
        
        if not cache_path.exists():
            return False
        
        # Verify hash
        actual_hash = cache_path.stem.removeprefix("warmup_")
        if actual_hash != prompt_hash:
            logger.warning(
                f"[WARMUP] Hash mismatch: {actual_hash} != {prompt_hash}"
            )
            return False
        
        # Load cache
        cached = mx.load(str(cache_path))
        
        # Store in warmup cache
        engine._warmup_cache = cached
        engine._warmup_prompt_hash = prompt_hash
        
        logger.info(f"[WARMUP] Restored cache: {cache_path.name}")
        return True
        
    except Exception as e:
        logger.debug(f"[WARMUP] Restore failed: {e}")
        return False


async def save_warmup_cache(
    engine,
    cache_path: Path | None = None,
) -> bool:
    """
    Save warmup cache to disk.
    
    Args:
        engine: DeepHermes3Engine instance
        cache_path: Optional path override
        
    Returns:
        True if saved successfully
    """
    if engine._warmup_cache is None:
        return False
    
    if cache_path is None:
        cache_path = WARMUP_CACHE_DIR / f"warmup_{engine._warmup_prompt_hash}.safetensors"
    
    try:
        import mlx.core as mx
        
        WARMUP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        mx.save(str(cache_path), engine._warmup_cache)
        
        logger.info(f"[WARMUP] Saved cache: {cache_path.name}")
        return True
        
    except Exception as e:
        logger.debug(f"[WARMUP] Save failed: {e}")
        return False


# === Standalone functions for engine delegation ===

async def restore_warmup_cache_async(
    engine,
    cache_path: Path,
    prompt_hash: str,
) -> bool:
    """
    Restore warmup cache - standalone for engine delegation.
    
    Args:
        engine: DeepHermes3Engine instance
        cache_path: Path to cache file
        prompt_hash: Expected hash
        
    Returns:
        True if restored
    """
    try:
        import mlx.core as mx
        
        if not cache_path.exists():
            return False
        
        engine._warmup_cache = mx.load(str(cache_path))
        engine._warmup_prompt_hash = prompt_hash
        logger.info(f"[WARMUP] Restored: {cache_path.name}")
        return True
    except Exception as e:
        logger.debug(f"[WARMUP] Restore failed: {e}")
        return False


def invalidate_prefix_cache(engine) -> None:
    """Invalidate prefix cache - standalone for engine delegation."""
    engine._prefix_cache.clear()
    engine._prefix_cache_stats["prefix_cache_hits"] = 0
    engine._prefix_cache_stats["prefix_cache_misses"] = 0
