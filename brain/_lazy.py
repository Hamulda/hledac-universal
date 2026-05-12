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
  Unload sequence: gc.collect() → mx.eval([]) → mx.metal.clear_cache()

NOTE: ModelManager (brain/model_manager.py) is the canonical owner of model
lifecycle (1-model-at-a-time policy, TTL eviction, memory guard). This module
provides a SECOND, independent lazy loading path for non-Hermes models that
don't go through ModelManager. Do NOT route Hermes/GLINER/ModernBERT through
this module — use ModelManager instead.
"""

from __future__ import annotations

import asyncio
import gc
import logging
from typing import TypeVar, Generic, Callable, Optional, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")

_MEMORY_GUARD_THRESHOLD_MB = 1024  # 1GB free → refuse new model loads


def _get_available_mb() -> float:
    """Non-raising available memory check."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 / 1024
    except ImportError:
        return float("inf")  # psutil not available → don't block


def _mlx_clear() -> None:
    """Best-effort MLX metal cache clear."""
    try:
        import mlx.core as mx
        mx.eval([])
        mx.metal.clear_cache()
    except Exception:
        pass


class LazyModel(Generic[T]):
    """
    Lazy model loader s TTL eviction pro M1 8GB unified memory.

    Lifecycle:
      None → loading (factory call) → loaded (instance alive)
      loaded → evict() after TTL → None

    Memory guard: odmítne load pokud available RAM < threshold.
    """

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
        self._instance: Optional[T] = None
        self._evict_task: Optional[asyncio.TimerHandle] = None
        self._load_count = 0
        self._evict_count = 0

    async def get(self, *, findings_count: int = 0) -> Optional[T]:
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

        # Memory guard
        if self._instance is None:
            avail = _get_available_mb()
            if avail < self._min_free_mb:
                logger.warning(
                    "[lazy:%s] MEMORY GUARD — available=%.0fMB < threshold=%.0fMB, refusing load",
                    self._name, avail, self._min_free_mb,
                )
                return None

            logger.debug("[lazy:%s] loading (load #%d)", self._name, self._load_count + 1)
            self._instance = self._factory()
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
        try:
            loop = asyncio.get_running_loop()
            self._evict_task = loop.call_later(self._ttl, self._evict)
        except RuntimeError:
            pass  # No running loop — eviction will not fire (batch mode OK)

    def _evict(self) -> None:
        if self._instance is not None:
            logger.debug(
                "[lazy:%s] evicting (evict #%d, TTL=%.0fs)",
                self._name, self._evict_count + 1, self._ttl,
            )
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
        from brain.hermes3_engine import Hermes3Engine  # type: ignore
        return Hermes3Engine()

    def _ner():
        from brain.ner_engine import NEREngine  # type: ignore
        return NEREngine()

    def _gnn():
        from brain.gnn_predictor import GNNPredictor  # type: ignore
        return GNNPredictor()

    def _ane():
        from brain.ane_embedder import ANEEmbedder  # type: ignore
        return ANEEmbedder()

    def _moe():
        from brain.moe_router import MoERouter  # type: ignore
        return MoERouter()

    def _modernbert():
        from brain.modernbert_engine import ModernBertEngine  # type: ignore
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


_REGISTRY: dict[str, LazyModel] | None = None


def _get_registry() -> dict[str, LazyModel]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _make_lazy_registry()
    return _REGISTRY


async def get(name: str, *, findings_count: int = 0) -> Any:
    """Public API: await brain._lazy.get('ner')"""
    registry = _get_registry()
    if name not in registry:
        raise KeyError(f"Unknown lazy model: {name!r}. Known: {list(registry)}")
    return await registry[name].get(findings_count=findings_count)


def unload_all() -> None:
    """Unload všech modelů — volat na konci sprint cycle."""
    if _REGISTRY:
        for m in _REGISTRY.values():
            m.unload()


def stats() -> list[dict]:
    """Memory diagnostics — volat pro debug."""
    if not _REGISTRY:
        return []
    return [m.stats() for m in _REGISTRY.values()]