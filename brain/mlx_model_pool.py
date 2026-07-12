"""
MLXModelPool - Unified LRU Model Pool for M1 8GB
Issue #18: Memory-optimized model loading

Zajišťuje:
- Unified LRU eviction pro všechny MLX modely (Hermes, Embedder, NER)
- Hard memory budget enforcement
- Async-safe (asyncio.Lock)
- mx.eval([]) + clear_cache() po každém eviction
"""
import asyncio
import gc
import inspect
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
logger = logging.getLogger(__name__)
_mlx: Any | None = None

def _get_mlx() -> Any | None:
    global _mlx
    if _mlx is None:
        try:
            import mlx.core as mx
            _mlx = mx
        except ImportError:
            _mlx = None
    return _mlx

@dataclass(True)
class ModelEntry:
    model: Any
    tokenizer: Any | None = None
    size_bytes: int = 0
    loaded_at: float = 0.0
    access_count: int = 0

@dataclass(True)
class MLXModelPoolConfig:
    budget_gb: float = 4.0
    min_eviction_interval_s: float = 1.0
    auto_clear_cache: bool = True
    force_gc: bool = True

class MLXModelPool:
    """
    Unified LRU model pool with hard memory budget for M1 8GB.
    Automatically evicts LRU model when budget exceeded.
    Invariants: Always-on, fail-safe, mx.eval([]) before clear_cache.
    """
    __slots__ = ('_budget_bytes', '_loaded', '_lock', '_config', '_total_evictions', '_total_hits', '_total_misses', '_last_eviction_at', '_eviction_history', '_loaded_count')
    _instance: MLXModelPool | None = None

    def __init__(self, budget_gb: float | None=None, config: MLXModelPoolConfig | None=None) -> None:
        if config is None:
            config = MLXModelPoolConfig()
        if budget_gb is not None:
            config.budget_gb = budget_gb
        self._config = config
        self._budget_bytes = int(config.budget_gb * 1024 ** 3)
        self._loaded: OrderedDict[str, ModelEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._total_evictions = 0
        self._total_hits = 0
        self._total_misses = 0
        self._last_eviction_at = 0.0
        self._eviction_history: list[dict[str, Any]] = []
        self._loaded_count = 0

    @classmethod
    def get_instance(cls, **kwargs: Any) -> MLXModelPool:
        if cls._instance is None:
            cls._instance = cls(**kwargs)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    @property
    def loaded_count(self) -> int:
        return self._loaded_count

    @property
    def total_bytes_used(self) -> int:
        return sum((e.size_bytes for e in self._loaded.values()))

    def get_stats(self) -> dict[str, Any]:
        total = self._total_hits + self._total_misses
        return {'budget_gb': self._config.budget_gb, 'budget_bytes': self._budget_bytes, 'loaded_count': self._loaded_count, 'total_bytes_used': self.total_bytes_used, 'total_evictions': self._total_evictions, 'hit_rate_pct': self._total_hits / total * 100 if total > 0 else 0, 'models': {mid: {'size_mb': e.size_bytes / 1024 ** 2, 'access_count': e.access_count} for mid, e in self._loaded.items()}}

    async def acquire(self, model_id: str, loader: Callable[[], Awaitable[tuple[Any, Any | None]]] | Callable[[], tuple[Any, Any | None]]) -> tuple[Any, Any | None]:
        async with self._lock:
            if model_id in self._loaded:
                e = self._loaded[model_id]
                e.access_count += 1
                self._loaded.move_to_end(model_id)
                self._total_hits += 1
                return (e.model, e.tokenizer)
            self._total_misses += 1
            await self._evict_if_needed()
            try:
                start = time.monotonic()
                if inspect.iscoroutinefunction(loader):
                    model, tokenizer = await loader()
                else:
                    model, tokenizer = await asyncio.to_thread(loader)
                elapsed = time.monotonic() - start
                size = self._estimate_model_size(model, tokenizer)
                e = ModelEntry(model=model, tokenizer=tokenizer, size_bytes=size, loaded_at=time.time(), access_count=1)
                self._loaded[model_id] = e
                self._loaded.move_to_end(model_id)
                self._loaded_count = len(self._loaded)
                logger.info(f'[MLX_POOL] Loaded: {model_id} ({size / 1024 ** 2:.1f}MB) in {elapsed:.2f}s')
                return (model, tokenizer)
            except Exception as ex:
                logger.error(f'[MLX_POOL] Failed to load {model_id}: {ex}')
                raise

    async def release(self, model_id: str) -> None:
        async with self._lock:
            if model_id in self._loaded:
                self._loaded.move_to_end(model_id, last=False)

    async def evict(self, model_id: str) -> bool:
        async with self._lock:
            return await self._evict_internal(model_id)

    async def evict_lru(self, count: int=1) -> list[str]:
        evicted = []
        async with self._lock:
            for _ in range(min(count, max(1, len(self._loaded) - 1))):
                if not self._loaded:
                    break
                mid, _ = self._loaded.popitem(last=False)
                evicted.append(mid)
                self._total_evictions += 1
                self._loaded_count = len(self._loaded)
        if evicted:
            await self._post_eviction_cleanup()
        return evicted

    async def _evict_if_needed(self) -> None:
        while self.total_bytes_used >= self._budget_bytes and self._loaded:
            if len(self._loaded) == 1 and self.total_bytes_used <= int(self._budget_bytes * 1.5):
                break
            elapsed = time.time() - self._last_eviction_at
            if elapsed < self._config.min_eviction_interval_s and len(self._loaded) > 1:
                logger.warning(f'[MLX_POOL] Eviction throttled')
                break
            mid, e = self._loaded.popitem(last=False)
            self._total_evictions += 1
            self._last_eviction_at = time.time()
            self._loaded_count = len(self._loaded)
            logger.info(f'[MLX_POOL] Evicted (budget): {mid}')
        if self._total_evictions > 0:
            await self._post_eviction_cleanup()

    async def _evict_internal(self, model_id: str) -> bool:
        if model_id not in self._loaded:
            return False
        self._loaded.pop(model_id)
        self._total_evictions += 1
        self._loaded_count = len(self._loaded)
        await self._post_eviction_cleanup()
        return True

    async def _post_eviction_cleanup(self) -> None:
        if not self._config.auto_clear_cache:
            return
        mx = _get_mlx()
        if mx is None:
            return
        try:
            mx.eval([])
        except Exception:
            pass
        try:
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
        except Exception:
            pass
        if self._config.force_gc:
            try:
                gc.freeze()
            except Exception:
                pass

    def _estimate_model_size(self, model: Any, tokenizer: Any | None) -> int:
        mod = type(model).__module__.lower()
        name = getattr(model, 'model_name', '').lower() or type(model).__name__.lower()
        if 'hermes' in mod or 'hermes' in name:
            return int(1.75 * 1024 ** 3)
        elif 'embed' in mod:
            return int(0.5 * 1024 ** 3)
        elif 'gliner' in mod or 'ner' in mod:
            return int(0.3 * 1024 ** 3)
        return int(1.0 * 1024 ** 3)

    @asynccontextmanager
    async def scoped(self, model_id: str, loader):
        model, tokenizer = await self.acquire(model_id, loader)
        try:
            yield (model, tokenizer)
        finally:
            await self.release(model_id)
_pool: MLXModelPool | None = None

def get_mlx_model_pool(**kwargs: Any) -> MLXModelPool:
    global _pool
    if _pool is None:
        _pool = MLXModelPool.get_instance(**kwargs)
    return _pool

async def pool_acquire(model_id: str, loader):
    return await get_mlx_model_pool().acquire(model_id, loader)

async def pool_release(model_id: str) -> None:
    await get_mlx_model_pool().release(model_id)

def get_pool_stats() -> dict[str, Any]:
    return get_mlx_model_pool().get_stats()