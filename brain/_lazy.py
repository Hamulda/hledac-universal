"""
brain._lazy — LazyModel with TTL Eviction for M1 8GB Unified Memory
====================================================================


Provides:
- LazyModel[T]: Generic lazy model loader with TTL eviction and memory guard
- _get_registry() / get() / unload_all() / stats(): Public API
- Conditional model loading (GNN: only when findings >= threshold)
- Memory guard: refuses load if available RAM < threshold

TTL calibration (from F2 memory audit, LOW risk confirmed):
  hermes3       90s   (~2GB — expensive to load, expensive to hold)
  ner          300s   (~300MB — lighter, shared across sprint)
  gnn          120s   (~200-400MB — conditional load, >50 findings)
  ane          600s   (~300MB CoreML — slow ANE initialization)
  moe_router  180s    (~100MB — medium weight, medium TTL)

Memory budget (F2 audit):
  Peak RSS: ~2.9GB (53% of 5.5GB usable)
  Headroom: ~2.6GB before macOS compression threshold
  Hermes3 weights: ~2GB (Q4_K_M)
  NER/GNN/ANE: ~150-350MB each
  KV cache: ~32MB
  Unload sequence: mx.eval([]) barrier → gc.collect() → mx.clear_cache()
  (F300-MLX invariant: mx.eval() PŘED gc.collect() — clear_cache is no-op without barrier)

NOTE: ModelManager (brain/model_manager.py) is the canonical owner of model
lifecycle (1-model-at-a-time policy, TTL eviction, memory guard). This module
provides a SECOND, independent lazy loading path for non-Hermes models that
don't go through ModelManager. Do NOT route Hermes/GLINER/ModernBERT through
this module — use ModelManager instead.
"""

from hledac.universal.utils.asyncx import safe_create_task
from hledac.universal.utils.cache import PyCacheDict

import asyncio
import gc
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T", default=Any)  # PEP 696: TypeVar with default

_MEMORY_GUARD_THRESHOLD_MB = 1024  # 1GB free → refuse new model loads


def _get_available_mb() -> float:
    """Non-raising available memory check."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 / 1024
    except ImportError:
        return float("inf")  # psutil not available → don't block


def _mlx_clear() -> None:
    """
    Issue #20+31 FIX: Best-effort MLX Metal cache clear.
    Canonical order (GHOST_INVARIANTS.md:80):
      gc.collect() -> mx.eval([]) -> mx.clear_cache() -> gc.collect()
    """
    try:
        import mlx.core as mx
        import gc
        gc.collect()
        mx.eval([])
        # Modern-first: mx.clear_cache() — mlx >= 0.20, no fallback needed
        if hasattr(mx, "clear_cache"):
            mx.clear_cache()
        gc.collect()
    except Exception:  # noqa: BLE001
        pass


class LazyModel[T]:
    """
    PEP 749 (Python 3.13+) — Lazy loader s async-safe eviction.

    Lifecycle:
      None → loading (factory call) → loaded (instance alive)
      loaded → evict() after TTL → None

    Memory guard: odmítne load pokud available RAM < threshold.
    Adaptive threshold: multiplier increases under UMA pressure (critical/emergency).

    Async-safe eviction protocol:
      1. _evict() schedules _async_evict() as a task (never runs sync)
      2. _async_evict() acquires _load_lock, checks is_busy(), defers if busy
      3. gc.collect() + _mlx_clear() run in to_thread outside the lock
      4. Stale evict guard prevents evicting a freshly loaded instance
    """

    __slots__ = (
        "_factory",
        "_ttl",
        "_name",
        "_min_free_mb",
        "_min_findings",
        "_instance",
        "_thread_lock",
        "_load_lock",
        "_evict_task",
        "_load_count",
        "_evict_count",
        "_scheduled_load_gen",
    )

    def __init__(
        self,
        factory: Callable[[], T],
        *,
        ttl_seconds: float = 120.0,
        name: str = "unknown",
        min_free_mb: float = _MEMORY_GUARD_THRESHOLD_MB,
        conditional_min_findings: int = 0,  # 0 = always load
    ) -> None:
        self._factory = factory
        self._ttl = ttl_seconds
        self._name = name
        self._min_free_mb = min_free_mb
        self._min_findings = conditional_min_findings
        self._instance: T | None = None
        self._evict_task: asyncio.TimerHandle | None = None
        self._load_count = 0
        self._evict_count = 0
        self._scheduled_load_gen: int = 0  # load_gen when timer was last set
        # Thread-safe lazy lock init — DCLP with threading.Lock (OS-provided,
        # reentrant, safe across executor threads). Protects _instance race during
        # concurrent get() calls from multiple asyncio tasks or to_thread workers.
        self._thread_lock: threading.Lock = threading.Lock()
        self._load_lock: asyncio.Lock | None = None

    def _effective_min_free_mb(self) -> float:
        """
        Issue #21: Adaptive threshold — more conservative under UMA pressure.

        Under critical/emergency: multiplier 1.5×/2.0× to avoid loading new
        models when the system is already under memory pressure.
        Falls back to base threshold if governor is unavailable.
        """
        try:
            # sample_uma_status is sync and lightweight (TTL-cached psutil reads)
            from hledac.universal.core.resource_governor import sample_uma_status
            uma = sample_uma_status()
            match uma.state:
                case "critical":
                    return self._min_free_mb * 1.5
                case "emergency":
                    return self._min_free_mb * 2.0
                case _:
                    return self._min_free_mb
        except Exception:  # noqa: BLE001
            return self._min_free_mb

    def _get_lock(self) -> asyncio.Lock:
        """Thread-safe lazy init pro asyncio.Lock — DCLP protected by threading.Lock.

        asyncio.Lock() není thread-safe při init z více vláken současně.
        Používáme threading.Lock (reentrant, OS-provided) k ochraně init bloku.
        Po init už asyncio.Lock běží čistě v event loop — žádné cross-thread race.
        """
        lock = self._load_lock
        if lock is None:
            with self._thread_lock:
                # DCL: druhý check uvnitř kritické sekce
                lock = self._load_lock
                if lock is None:
                    lock = asyncio.Lock()
                    self._load_lock = lock
        return lock

    async def get(self, *, findings_count: int = 0) -> T | None:
        """
        Returns model instance. Returns None if:
        - Memory guard triggered (< min_free_mb available)
        - Conditional threshold not met (findings_count < min_findings)
        """
        # Conditional load guard (GNN: only if > 50 findings)
        if self._min_findings > 0 and findings_count < self._min_findings:
            logger.debug(
                "[lazy:%s] skipped — findings=%d < min=%d",
                self._name, findings_count, self._min_findings,
            )
            return None

        # Fast path: already loaded
        if self._instance is not None:
            self._reset_evict_timer()
            return self._instance

        # Slow path: serialized load via per-model lock (double-check pattern)
        async with self._get_lock():
            # Double-check after acquiring lock — another coroutine may have loaded
            if self._instance is not None:
                self._reset_evict_timer()
                return self._instance

            # Memory guard (checked inside lock to prevent race)
            # Issue #21: Use adaptive threshold under UMA pressure
            effective_min = self._effective_min_free_mb()
            avail = _get_available_mb()
            if avail < effective_min:
                logger.warning(
                    "[lazy:%s] MEMORY GUARD — available=%.0fMB < threshold=%.0fMB (adaptive), refusing load",
                    self._name, avail, effective_min,
                )
                return None

            # Issue #21: Drain pending MLX evaluations BEFORE loading new model.
            # Without this, eval queue from previous model can hold intermediate
            # tensors that compound with new model allocation → RAM spike on M1 8GB.
            _mlx_clear()

            logger.debug("[lazy:%s] loading (load #%d)", self._name, self._load_count + 1)
            # Factory is CPU/Metal-bound (MLX model init) — offload to thread pool
            # to avoid blocking the event loop. GHOST_INVARIANTS:40 forbids
            # asyncio.to_thread only for DNS/CoreML/DuckDB; MLX model loading is OK.
            self._instance = await asyncio.to_thread(self._factory)
            self._load_count += 1

        self._reset_evict_timer()
        return self._instance

    def unload(self) -> None:
        """Immediate synchronous unload."""
        if self._evict_task:
            self._evict_task.cancel()
            self._evict_task = None
        self._evict()

    def _reset_evict_timer(self) -> None:
        if self._evict_task:
            self._evict_task.cancel()
        # Record load_gen when timer is set — used in _evict() to detect stale evicts
        self._scheduled_load_gen = self._load_count
        try:
            loop = asyncio.get_running_loop()
            self._evict_task = loop.call_later(self._ttl, self._evict)
        except RuntimeError:  # noqa: BLE001
            pass  # No running loop — eviction will not fire (batch mode OK)

    def _evict(self) -> None:
        """Schedule async eviction — never runs sync gc/mlx_clear.

        Schedules _async_evict() as a task so that:
        1. gc.collect() runs in to_thread (no event loop blocking)
        2. _mlx_clear() can check is_busy() before Metal device access
        3. Stale evict guard runs inside async context
        """
        try:
            safe_create_task(self._async_evict(), name="lazy:evict")
        except RuntimeError:
            # No running loop — fallback to sync (batch mode)
            self._evict_sync_fallback()

    async def _async_evict(self) -> None:
        """Async-safe eviction with is_busy() check and deferred gc/mlx_clear.

        Protocol:
          1. Acquire _load_lock to serialize with concurrent get()
          2. Stale evict guard: skip if a newer load happened
          3. is_busy() check: if model is generating, reschedule eviction
          4. Clear _instance inside lock
          5. Release lock, then run gc.collect() + _mlx_clear() in to_thread
        """
        async with self._get_lock():
            if self._instance is None:
                return
            # STALE EVICT GUARD
            if self._load_count > self._scheduled_load_gen:
                logger.debug(
                    "[lazy:%s] stale evict skipped (load_gen=%d > scheduled=%d)",
                    self._name, self._load_count, self._scheduled_load_gen,
                )
                self._evict_task = None
                return

            # BUSY GUARD: if model is actively generating, defer eviction
            instance = self._instance
            if hasattr(instance, "is_busy") and callable(instance.is_busy):
                try:
                    if await instance.is_busy():
                        logger.debug(
                            "[lazy:%s] busy — rescheduling eviction (TTL=%.0fs)",
                            self._name, self._ttl,
                        )
                        self._reset_evict_timer()
                        return
                except Exception:  # noqa: BLE001
                    pass  # is_busy() failed — proceed with eviction

            # Clear instance inside lock
            self._instance = None
            self._evict_task = None
            self._evict_count += 1
            logger.debug(
                "[lazy:%s] evicted (evict #%d, TTL=%.0fs)",
                self._name, self._evict_count, self._ttl,
            )

        # gc.collect() + _mlx_clear() outside the lock, in to_thread
        # This avoids holding the lock while blocking the event loop
        try:
            await asyncio.to_thread(gc.collect)
        except Exception:  # noqa: BLE001
            pass
        # _mlx_clear() must run after gc.collect per F300-MLX invariant
        _mlx_clear()

    def _evict_sync_fallback(self) -> None:
        """Sync fallback for batch mode (no event loop).

        Used when _evict() is called outside an async context.
        gc.collect() runs sync — only use in batch/shutdown paths.
        """
        if self._instance is None:
            return
        if self._load_count > self._scheduled_load_gen:
            self._evict_task = None
            return
        self._instance = None
        self._evict_task = None
        self._evict_count += 1
        gc.collect()
        _mlx_clear()

    @property
    def loaded(self) -> bool:
        return self._instance is not None

    def stats(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "loaded": self.loaded,
            "load_count": self._load_count,
            "evict_count": self._evict_count,
            "ttl_seconds": self._ttl,
        }


# ── Pre-configured instances ─────────────────────────────────────────

def _make_lazy_registry() -> dict[str, LazyModel]:
    """
    Factory pro všechny brain/ lazy models.
    Import je deferred — moduly se nenačtou dokud LazyModel.get() není voláno.
    """

    def _hermes3():
        from hledac.universal.brain.deephermes3_engine import DeepHermes3Engine  # type: ignore
        return DeepHermes3Engine()

    def _ner():
        from hledac.universal.brain.ner_engine import NEREngine  # type: ignore
        return NEREngine()

    def _gnn():
        from hledac.universal.brain.gnn_predictor import GNNPredictor  # type: ignore
        return GNNPredictor()

    def _ane():
        from hledac.universal.brain.ane_embedder import ANEEmbedder  # type: ignore
        return ANEEmbedder()

    def _moe():
        from hledac.universal.brain.moe_router import MoERouter  # type: ignore
        return MoERouter()

    def _modernbert():
        from hledac.universal.brain.modernbert_engine import ModernBertEngine  # type: ignore
        return ModernBertEngine()

    return {
        "hermes3":    LazyModel(_hermes3, ttl_seconds=90,  name="hermes3"),
        "ner":        LazyModel(_ner,     ttl_seconds=300, name="ner"),
        "gnn":        LazyModel(_gnn,     ttl_seconds=120, name="gnn",
                               conditional_min_findings=50),
        "ane":        LazyModel(_ane,     ttl_seconds=600, name="ane"),
        "moe_router": LazyModel(_moe,     ttl_seconds=180, name="moe_router"),
        "modernbert": LazyModel(_modernbert, ttl_seconds=180, name="modernbert"),
    }


# P1-2: Replace unbounded lru_cache(maxsize=None) with bounded PyCacheDict.
# PyCacheDict provides: bounded LRU + TTL (600s) + thread-safe OrderedDict.
# Thread-safety: PyCacheDict holds threading.RLock internally.
# Registry is small (7 entries), TTL of 600s matches longest model TTL (ane:600s).
# lru_cache(maxsize=None) would grow unbounded over 24h sprint → M1 swap.
_registry_lock = threading.Lock()
_registry_cache: "PyCacheDict[None, dict[str, LazyModel]]" = PyCacheDict(2, 600.0)


def _get_registry() -> dict[str, LazyModel]:
    """Thread-safe registry via PyCacheDict — init-once, cached 600s.

    Double-checked locking pattern:
    - Fast path: cache hit (no lock acquired)
    - Slow path: cache miss → lock → double-check → init → cache
    Thread-safe: PyCacheDict holds threading.RLock; DCLP prevents re-init.
    """
    cached = _registry_cache.get(None)
    if cached is not None:
        return cached
    with _registry_lock:
        # DCLP: another thread may have populated while we waited for the lock
        cached = _registry_cache.get(None)
        if cached is not None:
            return cached
        registry = _make_lazy_registry()
        _registry_cache.set(None, registry)
        return registry


async def get(name: str, *, findings_count: int = 0) -> Any:
    """Public API: await brain._lazy.get('ner')"""
    registry = _get_registry()
    if name not in registry:
        raise KeyError(f"Unknown lazy model: {name!r}. Known: {list(registry)}")
    return await registry[name].get(findings_count=findings_count)


def unload_all() -> None:
    """Unload všech modelů — volat na konci sprint cycle."""
    registry = _get_registry()
    for m in registry.values():
        m.unload()


def stats() -> list[dict]:
    """Memory diagnostics — volat pro debug."""
    registry = _get_registry()
    return [m.stats() for m in registry.values()]
