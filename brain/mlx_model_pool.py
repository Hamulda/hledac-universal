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
import msgspec
from compat.msgspec_gc_compat import Struct
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mlx_lm import Model as MLXModel
    from mlx_lm import TokenizerWrapper as MLXTokenizer
from _core import aclose
logger = logging.getLogger(__name__)
_mlx: Any | None = None  # type: ignore[assignment]

def _get_mlx() -> Any | None:  # type: ignore[type-arg]
    global _mlx
    if _mlx is None:
        try:
            import mlx.core as mx
            _mlx = mx
        except ImportError:
            _mlx = None
    return _mlx

class ModelEntry(Struct):
    """ISSUE #15: Přidána weakref pro referenční počítání."""
    model: MLXModel
    tokenizer: MLXTokenizer | None = None
    size_bytes: int = 0
    loaded_at: float = 0.0
    access_count: int = 0
    ref_count: int = 1  # ISSUE #15: Reference count pro pool management
    weak_ref: Any = None  # type: ignore[assignment]  # ISSUE #15: weakref pro GC-safe referenci

class MLXModelPoolConfig(Struct):
    budget_gb: float = 4.0
    min_eviction_interval_s: float = 1.0
    auto_clear_cache: bool = True
    force_gc: bool = True
    # ISSUE #15: Priority queue settings
    priority_q4_k_m_before_q8_0: bool = True  # Q4_K_M modely mají přednost při evict

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

    async def acquire(self, model_id: str, loader: Callable[[], Awaitable[tuple[MLXModel, MLXTokenizer | None]]] | Callable[[], tuple[MLXModel, MLXTokenizer | None]]) -> tuple[MLXModel, MLXTokenizer | None]:
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
        """ISSUE #15: Priority-aware LRU eviction."""
        evicted = []
        async with self._lock:
            for _ in range(min(count, max(1, len(self._loaded) - 1))):
                if not self._loaded:
                    break
                # ISSUE #15: Výběr LRU candidate s ohledem na priority
                mid = self._select_lru_candidate()
                if mid is None:
                    break
                self._loaded.pop(mid)
                evicted.append(mid)
                self._total_evictions += 1
                self._loaded_count = len(self._loaded)
        if evicted:
            await self._post_eviction_cleanup()
        return evicted

    def _select_lru_candidate(self) -> str | None:
        """
        ISSUE #15: Vybere LRU candidate s ohledem na priority.

        Pokud je povolena priority Q4_K_M před Q8_0:
        - Nejprve evict Q8_0 modely (nízká priorita)
        - Pak modely s nízkou prioritou napříč kategoriím
        Jinak: standard LRU (oldest access)

        LRU = OrderedDict.keys()[0] = oldest by insertion/access order.
        """
        if not self._loaded:
            return None

        if not self._config.priority_q4_k_m_before_q8_0:
            # Standard LRU — first item in OrderedDict is oldest
            return next(iter(self._loaded))

        # ISSUE #15: Priority-based selection
        # Q8_0 = nízká priorita (vysoká paměť), Q4_K_M = vysoká priorita
        # OrderedDict udržuje pořadí přístupů — first = LRU
        q8_candidates = [mid for mid in self._loaded.keys()
                        if self._get_model_priority(mid) < 5]
        if q8_candidates:
            # Evict Q8_0 model s nejstarším přístupem (LRU = first in OrderedDict)
            return q8_candidates[0]

        # Jinak evict oldest (LRU)
        return next(iter(self._loaded))

    def _get_model_priority(self, model_id: str) -> int:
        """Vrátí prioritu modelu (vyšší = důležitější)."""
        # Priority podle velikosti modelu
        if model_id not in self._loaded:
            return 5
        size = self._loaded[model_id].size_bytes
        # Velké modely (Hermes ~1.75GB) mají vysokou prioritu
        if size >= 1.5 * 1024 ** 3:
            return 10  # Q8_0 large models - protected unless desperate
        elif size >= 1.0 * 1024 ** 3:
            return 7
        elif size >= 0.5 * 1024 ** 3:
            return 5
        return 3  # Small models - evict first

    async def _evict_if_needed(self) -> None:
        while self.total_bytes_used >= self._budget_bytes and self._loaded:
            if len(self._loaded) == 1 and self.total_bytes_used <= int(self._budget_bytes * 1.5):
                break
            elapsed = time.time() - self._last_eviction_at
            if elapsed < self._config.min_eviction_interval_s and len(self._loaded) > 1:
                logger.warning(f'[MLX_POOL] Eviction throttled')
                break
            mid = self._select_lru_candidate()
            if mid is None:
                break
            self._loaded.pop(mid)
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
        except Exception:  # noqa: BLE001
            pass
        try:
            if hasattr(mx, 'clear_cache'):
                mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass
        if self._config.force_gc:
            try:
                gc.freeze()
            except Exception:  # noqa: BLE001
                pass

    def _estimate_model_size(self, model: MLXModel, tokenizer: MLXTokenizer | None) -> int:
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

    # ── Async Preload (ISSUE #15) ─────────────────────────────────────────────

    async def preload_async(self, model_id: str, loader: Callable[[], Awaitable[tuple[MLXModel, MLXTokenizer | None]]]) -> None:
        """
        ISSUE #15: Fire-and-forget async preload.

        Načte model na pozadí bez blokování. Pokud už model existuje,
        nic nedělá. Pokud preload běží, zruší starý a spustí nový.

        Args:
            model_id: Identifikátor modelu
            loader: Async funkce vracející (model, tokenizer)
        """
        async with self._lock:
            if model_id in self._loaded:
                # Model už načten
                return
            # Spust preload jako Task
            async def _preload_task() -> None:
                try:
                    model, tokenizer = await loader()
                    async with self._lock:
                        if model_id not in self._loaded:
                            size = self._estimate_model_size(model, tokenizer)
                            e = ModelEntry(model=model, tokenizer=tokenizer, size_bytes=size, loaded_at=time.time(), access_count=1)
                            self._loaded[model_id] = e
                            self._loaded.move_to_end(model_id)
                            self._loaded_count = len(self._loaded)
                            logger.info(f'[MLX_POOL] Preloaded: {model_id} ({size / 1024 ** 2:.1f}MB)')
                except Exception as ex:
                    logger.debug(f'[MLX_POOL] Preload failed for {model_id}: {ex}')

            # F350M-R ISSUE #31: use safe_create_task with eager_start=True (CPU-bound preload on hot path)
            task = safe_create_task(_preload_task(), eager_start=True)
            # ISSUE #15: Uložíme Task pro případné zrušení
            # Non-slotted dict pro dynamické atributy (objekt nemá __slots__)
            try:
                self._preload_tasks
            except AttributeError:
                self._preload_tasks: dict[str, asyncio.Task] = {}
            self._preload_tasks[model_id] = task

    def preload_cancel(self, model_id: str) -> None:
        """ISSUE #15: Zruší aktivní preload pokud existuje."""
        if hasattr(self, '_preload_tasks') and model_id in self._preload_tasks:
            task = self._preload_tasks.pop(model_id)
            if not task.done():
                task.cancel()

    # ── Reference Counting (ISSUE #15) ─────────────────────────────────────────

    async def acquire_with_ref(self, model_id: str, loader: Callable[[], Awaitable[tuple[MLXModel, MLXTokenizer | None]]]) -> tuple[MLXModel, MLXTokenizer | None]:
        """
        ISSUE #15: acquire + inkrementace ref count.

        Args:
            model_id: Identifikátor modelu
            loader: Async funkce vracející (model, tokenizer)

        Returns:
            (model, tokenizer)
        """
        result = await self.acquire(model_id, loader)
        async with self._lock:
            if model_id in self._loaded:
                self._loaded[model_id].ref_count += 1
        return result

    async def release_with_ref(self, model_id: str) -> None:
        """
        ISSUE #15: decrementace ref count + eviction pokud ref_count == 0.

        Args:
            model_id: Identifikátor modelu
        """
        async with self._lock:
            if model_id not in self._loaded:
                return
            entry = self._loaded[model_id]
            entry.ref_count -= 1
            if entry.ref_count <= 0:
                # Evict z pool
                await self._evict_internal(model_id)

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