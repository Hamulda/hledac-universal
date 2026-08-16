"""
UnifiedCacheManager: Single lifecycle owner for all MLX caches in DeepHermes3Engine.

ROADMAP-001: Consolidates 5 isolated cache systems into one unified entry point:
    1. _kv_cache_pool: SlidingWindowKVCache  - Token-based sliding window
    2. _session_cache_pool: LRUCache         - Session-level cache
    3. _prefix_cache: LRUCache               - Token prefix cache
    4. _warmup_cache: dict                  - Warmup cache
    5. _prompt_cache: Any                   - MLX prompt cache

Benefits:
    - Single clear_all() call for complete cleanup
    - Unified memory footprint estimation
    - Centralized stats collection
    - M1 8GB optimized: minimal overhead, GPU barrier integration

Usage:
    from brain._cache import UnifiedCacheManager

    manager = UnifiedCacheManager(
        kv_cache_maxsize=4,
        session_cache_maxsize=8,
        prefix_cache_maxsize=64,
    )
    
    # In DeepHermes3Engine.__init__():
    self._cache_manager = manager
    
    # For cleanup (unload/reset_session):
    await self._cache_manager.clear_all()
    
    # For memory estimation:
    memory_bytes = self._cache_manager.get_memory_footprint()
"""
from __future__ import annotations

import gc
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    pass

K = TypeVar("K")
V = TypeVar("V")

logger: Any = None  # Lazy import to avoid circular deps


def _get_logger():
    global logger
    if logger is None:
        import logging
        logger = logging.getLogger(__name__)
    return logger


class Cache(Protocol):
    """Protocol defining the cache interface for UnifiedCacheManager."""

    def clear(self) -> None:
        """Clear all entries from the cache."""
        ...

    def __len__(self) -> int:
        """Return number of entries in cache."""
        ...


class LRUCacheAny:
    """Wrapper for LRUCache to provide unified interface."""

    __slots__ = ("_cache",)

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def clear(self) -> None:
        if hasattr(self._cache, "clear"):
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


class SlidingWindowKVCacheAny:
    """Wrapper for SlidingWindowKVCache to provide unified interface."""

    __slots__ = ("_cache",)

    def __init__(self, cache: Any) -> None:
        self._cache = cache

    def clear(self) -> None:
        if hasattr(self._cache, "clear"):
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


@dataclass
class UnifiedCacheManager:
    """
    Single lifecycle owner for all MLX caches in DeepHermes3Engine.

    ROADMAP-001: Provides unified interface for 5 cache systems:
        - kv_cache: SlidingWindowKVCache - Token-based sliding window for KV pools
        - session_cache: LRUCache - Session-level LRU cache
        - prefix_cache: LRUCache - Token prefix cache
        - warmup_cache: dict - Warmup cache (simple dict)
        - prompt_cache: Any - MLX prompt cache reference

    M1 8GB Optimization:
        - Uses __slots__ for minimal memory overhead
        - Integrates with mlx.core for GPU barrier on cleanup
        - Estimates memory footprint for UMA monitoring

    Thread Safety:
        - All managed caches should be thread-safe if accessed from multiple threads
        - clear_all() is NOT thread-safe - call from single-threaded context
    """

    # ── Managed caches (initialized lazily) ────────────────────────────

    _kv_cache: Any = field(default=None, repr=False)
    _session_cache: Any = field(default=None, repr=False)
    _prefix_cache: Any = field(default=None, repr=False)
    _warmup_cache: dict = field(default_factory=dict, repr=False)
    _prompt_cache: Any = field(default=None, repr=False)
    _system_prompt_cache: Any = field(default=None, repr=False)

    # ── Configuration (set at init) ────────────────────────────────────

    _kv_cache_maxsize: int = 4
    _kv_cache_window_tokens: int = 16
    _kv_cache_decay_base: float = 0.85
    _kv_cache_token_interval_s: float = 5.0

    _session_cache_maxsize: int = 8
    _prefix_cache_maxsize: int = 64

    # ── Initialization ────────────────────────────────────────────────

    def __init__(
        self,
        *,
        kv_cache_maxsize: int = 4,
        kv_cache_window_tokens: int = 16,
        kv_cache_decay_base: float = 0.85,
        kv_cache_token_interval_s: float = 5.0,
        session_cache_maxsize: int = 8,
        prefix_cache_maxsize: int = 64,
        kv_cache_pool: Any = None,
        session_cache_pool: Any = None,
        prefix_cache: Any = None,
    ) -> None:
        """
        Initialize UnifiedCacheManager with cache configurations.

        Args:
            kv_cache_maxsize: Max entries in KV cache pool
            kv_cache_window_tokens: Token window size for sliding window
            kv_cache_decay_base: Decay base for token sliding window
            kv_cache_token_interval_s: Token interval in seconds
            session_cache_maxsize: Max entries in session cache
            prefix_cache_maxsize: Max entries in prefix cache
            kv_cache_pool: Optional pre-created KV cache pool
            session_cache_pool: Optional pre-created session cache pool
            prefix_cache: Optional pre-created prefix cache
        """
        # Store configuration
        self._kv_cache_maxsize = kv_cache_maxsize
        self._kv_cache_window_tokens = kv_cache_window_tokens
        self._kv_cache_decay_base = kv_cache_decay_base
        self._kv_cache_token_interval_s = kv_cache_token_interval_s
        self._session_cache_maxsize = session_cache_maxsize
        self._prefix_cache_maxsize = prefix_cache_maxsize

        # Initialize managed caches (override dataclass defaults)
        self._warmup_cache = {}  # Initialize warmup_cache since custom __init__ overrides dataclass
        self._prompt_cache = None
        self._system_prompt_cache = None

        # Use provided caches or create new ones (lazy)
        self._kv_cache = kv_cache_pool  # Will be created lazily if None
        self._session_cache = session_cache_pool  # Will be created lazily if None
        self._prefix_cache = prefix_cache  # Will be created lazily if None

    # ── Lazy initialization ────────────────────────────────────────────

    def _ensure_kv_cache(self) -> Any:
        """Lazily create KV cache pool."""
        if self._kv_cache is None:
            from utils.cache._sync import SlidingWindowKVCache

            self._kv_cache = SlidingWindowKVCache(
                max_size=self._kv_cache_maxsize,
                window_tokens=self._kv_cache_window_tokens,
                decay_base=self._kv_cache_decay_base,
                token_interval_s=self._kv_cache_token_interval_s,
                thread_safe=False,
            )
        return self._kv_cache

    def _ensure_session_cache(self) -> Any:
        """Lazily create session cache pool."""
        if self._session_cache is None:
            from utils.cache._sync import LRUCache

            self._session_cache = LRUCache(
                max_size=self._session_cache_maxsize,
                thread_safe=False,
            )
        return self._session_cache

    def _ensure_prefix_cache(self) -> Any:
        """Lazily create prefix cache."""
        if self._prefix_cache is None:
            from utils.cache._sync import LRUCache

            self._prefix_cache = LRUCache(
                max_size=self._prefix_cache_maxsize,
                thread_safe=False,
            )
        return self._prefix_cache

    # ── Properties for backward compatibility ────────────────────────────

    @property
    def kv_cache(self) -> Any:
        """Get KV cache pool (creates if needed)."""
        return self._ensure_kv_cache()

    @property
    def session_cache(self) -> Any:
        """Get session cache pool (creates if needed)."""
        return self._ensure_session_cache()

    @property
    def prefix_cache(self) -> Any:
        """Get prefix cache (creates if needed)."""
        return self._ensure_prefix_cache()

    @property
    def warmup_cache(self) -> dict:
        """Get warmup cache dict."""
        return self._warmup_cache

    @warmup_cache.setter
    def warmup_cache(self, value: Any) -> None:
        """Set warmup cache."""
        self._warmup_cache = value

    @property
    def prompt_cache(self) -> Any:
        """Get MLX prompt cache."""
        return self._prompt_cache

    @prompt_cache.setter
    def prompt_cache(self, value: Any) -> None:
        """Set MLX prompt cache."""
        self._prompt_cache = value

    @property
    def system_prompt_cache(self) -> Any:
        """Get system prompt cache."""
        return self._system_prompt_cache

    @system_prompt_cache.setter
    def system_prompt_cache(self, value: Any) -> None:
        """Set system prompt cache."""
        self._system_prompt_cache = value

    # ── Canonical cleanup ───────────────────────────────────────────────

    async def clear_all(self, sync_context: bool = False) -> dict[str, int]:
        """
        ROADMAP-001: Canonical cleanup - call before model unload.

        Clears all caches in correct order:
            1. warmup_cache - Python dict (fast)
            2. prompt_cache - MLX reference (needs GPU barrier)
            3. system_prompt_cache - MLX reference (needs GPU barrier)
            4. prefix_cache - Python LRU (fast)
            5. session_cache - Python LRU (fast)
            6. kv_cache - Sliding window (fast)
            7. GPU barrier (mx.eval + mx.clear_cache)
            8. gc.collect() for circular refs

        Args:
            sync_context: If True, run GPU barrier synchronously.
                         If False (async), caller should run in thread pool.

        Returns:
            dict with cleared cache names as keys and 1 as values
        """
        _log = _get_logger()
        cleared: dict[str, int] = {}

        # Phase 1: Clear Python caches (no GPU dependency)
        try:
            self._warmup_cache.clear()
            cleared["warmup_cache"] = 1
        except Exception:
            self._warmup_cache = {}
            cleared["warmup_cache"] = 1

        if self._prefix_cache is not None:
            try:
                self._prefix_cache.clear()
                cleared["prefix_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] prefix_cache clear failed: %s", e)

        if self._session_cache is not None:
            try:
                self._session_cache.clear()
                cleared["session_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] session_cache clear failed: %s", e)

        if self._kv_cache is not None:
            try:
                self._kv_cache.clear()
                cleared["kv_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] kv_cache clear failed: %s", e)

        # Phase 2: Clear MLX references (needs GPU barrier)
        self._prompt_cache = None
        cleared["prompt_cache"] = 1

        self._system_prompt_cache = None
        cleared["system_prompt_cache"] = 1

        # Phase 3: GPU barrier - flush Metal command queue
        try:
            import mlx.core as mx

            # Barrier: flush GPU queue BEFORE clear_cache
            mx.eval([])

            # Clear MLX Metal cache
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()

            cleared["metal_cache"] = 1
        except ImportError:
            # MLX not available - skip GPU barrier
            pass
        except Exception as e:
            _log.debug("[ROADMAP-001] GPU barrier failed: %s", e)

        # Phase 4: GC cleanup
        gc.collect()

        _log.debug("[ROADMAP-001] Caches cleared: %s", list(cleared.keys()))
        return cleared

    def clear_all_sync(self) -> dict[str, int]:
        """
        Synchronous version of clear_all() for non-async contexts.

        Returns:
            dict with cleared cache names as keys and 1 as values
        """
        _log = _get_logger()
        cleared: dict[str, int] = {}

        # Phase 1: Clear Python caches
        try:
            self._warmup_cache.clear()
            cleared["warmup_cache"] = 1
        except Exception:
            self._warmup_cache = {}
            cleared["warmup_cache"] = 1

        if self._prefix_cache is not None:
            try:
                self._prefix_cache.clear()
                cleared["prefix_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] prefix_cache clear failed: %s", e)

        if self._session_cache is not None:
            try:
                self._session_cache.clear()
                cleared["session_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] session_cache clear failed: %s", e)

        if self._kv_cache is not None:
            try:
                self._kv_cache.clear()
                cleared["kv_cache"] = 1
            except Exception as e:
                _log.debug("[ROADMAP-001] kv_cache clear failed: %s", e)

        # Phase 2: Clear MLX references
        self._prompt_cache = None
        cleared["prompt_cache"] = 1

        self._system_prompt_cache = None
        cleared["system_prompt_cache"] = 1

        # Phase 3: GPU barrier
        try:
            import mlx.core as mx

            mx.eval([])
            if hasattr(mx, "clear_cache"):
                mx.clear_cache()
            cleared["metal_cache"] = 1
        except ImportError:
            pass
        except Exception as e:
            _log.debug("[ROADMAP-001] GPU barrier failed: %s", e)

        # Phase 4: GC cleanup
        gc.collect()

        return cleared

    # ── Memory estimation ───────────────────────────────────────────────

    def get_memory_footprint(self) -> int:
        """
        Estimate total memory usage of all caches in bytes.

        ROADMAP-001: M1 8GB memory monitoring integration.

        Note: This is an estimate. Actual memory depends on:
            - MLX buffer allocations
            - Python object overhead
            - GC state

        Returns:
            Estimated memory usage in bytes
        """
        total_bytes = 0

        # Warmup cache (simple dict)
        total_bytes += self._estimate_dict_memory(self._warmup_cache)

        # Prefix cache (LRUCache)
        if self._prefix_cache is not None and hasattr(self._prefix_cache, "_data"):
            total_bytes += self._estimate_dict_memory(self._prefix_cache._data)

        # Session cache (LRUCache)
        if self._session_cache is not None and hasattr(self._session_cache, "_data"):
            total_bytes += self._estimate_dict_memory(self._session_cache._data)

        # KV cache pool (SlidingWindowKVCache)
        if self._kv_cache is not None and hasattr(self._kv_cache, "_data"):
            total_bytes += self._estimate_kv_cache_memory(self._kv_cache)

        # MLX caches (approximate)
        if self._prompt_cache is not None:
            total_bytes += self._estimate_mlx_cache_memory(self._prompt_cache)

        if self._system_prompt_cache is not None:
            total_bytes += self._estimate_mlx_cache_memory(self._system_prompt_cache)

        return total_bytes

    def _estimate_dict_memory(self, d: dict) -> int:
        """Estimate memory for a dict with simple values."""
        if not d:
            return 0
        # Base dict overhead + per-entry overhead
        # dict entry: ~72 bytes (PyObject* + hash + key + value)
        # key/value strings: varies
        entry_overhead = 72
        size = 64  # dict base size
        for k, v in d.items():
            size += entry_overhead + len(str(k)) + len(str(v))
        return size

    def _estimate_kv_cache_memory(self, kv_cache: Any) -> int:
        """Estimate memory for SlidingWindowKVCache."""
        if not hasattr(kv_cache, "_data"):
            return 0
        # KV caches typically hold MLX arrays
        # Each KV entry is roughly: key_size + value_tuple_size
        # value is (mlx_array, timestamp, memory_mb)
        # MLX arrays for Hermes-3 3B: ~6GB per entry
        # But we estimate based on entry count, not actual data
        entries = len(kv_cache._data)
        # Conservative estimate: 100MB per KV entry
        return entries * 100 * 1024 * 1024

    def _estimate_mlx_cache_memory(self, cache: Any) -> int:
        """Estimate memory for MLX prompt cache."""
        if cache is None:
            return 0
        # MLX prompt caches hold attention key/value tensors
        # Conservative estimate: 256MB per MLX cache
        return 256 * 1024 * 1024

    # ── Stats collection ────────────────────────────────────────────────

    def get_stats(self) -> dict[str, Any]:
        """
        Get unified statistics from all caches.

        Returns:
            dict with per-cache stats and total memory estimate
        """
        stats: dict[str, Any] = {
            "total_memory_bytes": self.get_memory_footprint(),
            "total_memory_mb": self.get_memory_footprint() / (1024 * 1024),
        }

        # KV cache stats
        if self._kv_cache is not None:
            if hasattr(self._kv_cache, "stats"):
                stats["kv_cache"] = self._kv_cache.stats
            else:
                stats["kv_cache"] = {"size": len(self._kv_cache), "type": "SlidingWindowKVCache"}

        # Session cache stats
        if self._session_cache is not None:
            if hasattr(self._session_cache, "stats"):
                stats["session_cache"] = self._session_cache.stats
            else:
                stats["session_cache"] = {"size": len(self._session_cache), "type": "LRUCache"}

        # Prefix cache stats
        if self._prefix_cache is not None:
            if hasattr(self._prefix_cache, "stats"):
                stats["prefix_cache"] = self._prefix_cache.stats
            else:
                stats["prefix_cache"] = {"size": len(self._prefix_cache), "type": "LRUCache"}

        # Warmup cache
        stats["warmup_cache"] = {"size": len(self._warmup_cache), "type": "dict"}

        # MLX caches (just presence flag)
        stats["prompt_cache_loaded"] = self._prompt_cache is not None
        stats["system_prompt_cache_loaded"] = self._system_prompt_cache is not None

        return stats

    # ── Partial clear operations ───────────────────────────────────────

    def clear_prefix_cache(self) -> None:
        """Clear only prefix cache (for session reset)."""
        if self._prefix_cache is not None:
            self._prefix_cache.clear()

    def clear_session_cache(self) -> None:
        """Clear only session cache (for full session reset)."""
        if self._session_cache is not None:
            self._session_cache.clear()

    def clear_kv_cache(self) -> None:
        """Clear only KV cache pool (for full session reset)."""
        if self._kv_cache is not None:
            self._kv_cache.clear()

    def reset_prompt_caches(self) -> None:
        """Reset MLX prompt caches (keeps Python caches)."""
        self._prompt_cache = None
        self._system_prompt_cache = None
