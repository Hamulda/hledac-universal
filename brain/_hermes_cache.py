"""
brain/_hermes_cache.py — Sprint P0-04
Thread-safe bounded LRU model cache for DeepHermes3Engine.

Invarianty (M1 8GB):
  - Max 2 base modely (~2GB RAM každý) — _HERMES_MODEL_CACHE_MAX
  - Max 2 LoRA adaptéry — _LORA_CACHE_MAX
  - thread-safe RLock pro přístup z async + sync kontextů
  - Active pressure monitor — koriguje pasivní only-insert-time eviction
  - mx.eval([]) barrier před gc.collect + clear_cache — F300-MLX canonical order

Architecture (Sprint Split-Brain):
- HermesModelCache: Facade orchestrating Loader + Monitor
- HermesModelLoader: Pure model/LoRA cache storage (get, put, evict)
- HermesModelMonitor: MemoryPressureListener + TTL sweep loop
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import warnings
from collections import OrderedDict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mlx_lm import Model as MLXModel
    from mlx_lm import TokenizerWrapper as MLXTokenizer
from collections.abc import Callable

from hledac.universal.utils.asyncx import safe_create_task
from hledac.universal.utils.memory_tier import get_lora_cache_max, get_model_cache_max

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_HERMES_MODEL_CACHE_MAX = 2  # M1 8GB: max 2 base models ~2GB each
_LORA_CACHE_MAX = 2  # M1 8GB: max 2 LoRA adapters
_MODEL_TTL_S = 600.0  # 10 minutes — idle model eviction threshold


def _mlx_cache_clear(reason: str) -> None:
    """Canonical MLX cache clear — delegates to mlx_cleanup_sync()."""
    try:
        from hledac.universal.utils.mlx_memory import mlx_cleanup_sync

        mlx_cleanup_sync()
    except ImportError:  # noqa: BLE001
        pass
    except Exception:  # noqa: BLE001
        pass
    logger.debug("[HERMES cache] MLX clear (%s)", reason)


class HermesModelLoader:
    """
    Pure model/LoRA cache storage — get, put, evict operations.

    Sprint Split-Brain: Extracted from HermesModelCache to isolate
    cache storage from monitoring. Enables independent testing of
    cache operations vs pressure response.

    Thread-safe via RLock for concurrent access from async + sync contexts.
    """

    __slots__ = (
        "_model_cache",
        "_lora_cache",
        "_access_times",
        "_max_size",
        "_lora_max_size",
        "_model_eviction_count",
        "_lora_eviction_count",
        "_lock",
        "_on_evict_model",
        "_on_evict_lora",
    )

    def __init__(
        self,
        max_size: int | None = None,
        lora_max_size: int | None = None,
        on_evict_model: Callable[[str], None] | None = None,
        on_evict_lora: Callable[[str], None] | None = None,
    ) -> None:
        self._max_size = max_size or get_model_cache_max() or _HERMES_MODEL_CACHE_MAX
        self._lora_max_size = lora_max_size or get_lora_cache_max() or _LORA_CACHE_MAX
        self._model_cache: OrderedDict[str, tuple[MLXModel, MLXTokenizer]] = OrderedDict()
        self._lora_cache: OrderedDict[str, tuple[MLXModel, MLXTokenizer]] = OrderedDict()
        self._access_times: dict[str, float] = {}
        self._model_eviction_count = 0
        self._lora_eviction_count = 0
        self._lock = threading.RLock()
        self._on_evict_model = on_evict_model
        self._on_evict_lora = on_evict_lora

    def _emit_eviction_telemetry(self, count: int, metric: str) -> None:
        """Fail-open OTel telemetry emit."""
        try:
            from otel._instrumentation import set_attribute

            set_attribute(metric, count)
        except Exception:  # noqa: BLE001
            pass

    def _safe_call_hook(self, hook: Callable[[str], None], key: str) -> None:
        """Fail-open hook call."""
        try:
            hook(key)
        except Exception:  # noqa: BLE001
            pass

    @property
    def lock(self) -> threading.RLock:
        return self._lock

    async def async_acquire(self) -> None:
        """Async-context lock acquire — runs in thread pool."""
        await asyncio.to_thread(self._lock.acquire)

    def release(self) -> None:
        """Release the RLock."""
        try:
            self._lock.release()
        except RuntimeError:  # noqa: BLE001
            pass

    def get_model(self, key: str) -> tuple[MLXModel, MLXTokenizer] | None:
        """Sync get — returns (model, tokenizer) or None."""
        with self._lock:
            if key not in self._model_cache:
                return None
            self._model_cache.move_to_end(key)
            self._access_times[key] = time.monotonic()
            return self._model_cache[key]

    def put_model(self, key: str, model: MLXModel, tokenizer: MLXTokenizer) -> bool:
        """Sync put — returns True if new entry added."""
        with self._lock:
            if key in self._model_cache:
                self._model_cache.move_to_end(key)
                return False
            while len(self._model_cache) >= self._max_size:
                self._evict_model_internal()
            self._model_cache[key] = (model, tokenizer)
            self._model_cache.move_to_end(key)
            self._access_times[key] = time.monotonic()
            return True

    def _evict_model_internal(self) -> str | None:
        """Internal LRU eviction — caller must hold _lock."""
        if not self._model_cache:
            return None
        key = next(iter(self._model_cache))
        del self._model_cache[key]
        self._access_times.pop(key, None)
        self._model_eviction_count += 1
        _mlx_cache_clear(f"model_evict:{key}")
        self._emit_eviction_telemetry(self._model_eviction_count, "hermes.cache.model_evictions")
        if self._on_evict_model:
            self._safe_call_hook(self._on_evict_model, key)
        return key

    def evict_model(self, key: str) -> bool:
        """Evict specific model. Returns True if evicted."""
        with self._lock:
            if key not in self._model_cache:
                return False
            del self._model_cache[key]
            self._access_times.pop(key, None)
            self._model_eviction_count += 1
            _mlx_cache_clear(f"model_evict:{key}")
            self._emit_eviction_telemetry(self._model_eviction_count, "hermes.cache.model_evictions")
            if self._on_evict_model:
                self._safe_call_hook(self._on_evict_model, key)
            return True

    def clear_models(self) -> int:
        """Clear all models. Returns count evicted."""
        with self._lock:
            count = len(self._model_cache)
            self._model_cache.clear()
            self._access_times.clear()
        if count > 0:
            _mlx_cache_clear("clear_models")
        return count

    def get_lora(self, key: str) -> tuple[MLXModel, MLXTokenizer] | None:
        """Sync get — returns (lora_model, lora_tokenizer) or None."""
        with self._lock:
            if key not in self._lora_cache:
                return None
            self._lora_cache.move_to_end(key)
            return self._lora_cache[key]

    def put_lora(self, key: str, lora_model: MLXModel, lora_tokenizer: MLXTokenizer) -> bool:
        """Sync put — returns True if new entry added."""
        with self._lock:
            if key in self._lora_cache:
                self._lora_cache.move_to_end(key)
                return False
            while len(self._lora_cache) >= self._lora_max_size:
                self._evict_lora_internal()
            self._lora_cache[key] = (lora_model, lora_tokenizer)
            self._lora_cache.move_to_end(key)
            return True

    def _evict_lora_internal(self) -> str | None:
        """Internal LRU eviction — caller must hold _lock."""
        if not self._lora_cache:
            return None
        key = next(iter(self._lora_cache))
        del self._lora_cache[key]
        self._lora_eviction_count += 1
        _mlx_cache_clear(f"lora_evict:{key}")
        self._emit_eviction_telemetry(self._lora_eviction_count, "hermes.cache.lora_evictions")
        if self._on_evict_lora:
            self._safe_call_hook(self._on_evict_lora, key)
        return key

    def clear_loras(self) -> int:
        """Clear all LoRAs. Returns count evicted."""
        with self._lock:
            count = len(self._lora_cache)
            self._lora_cache.clear()
        if count > 0:
            _mlx_cache_clear("clear_loras")
        return count

    @property
    def model_count(self) -> int:
        with self._lock:
            return len(self._model_cache)

    @property
    def lora_count(self) -> int:
        with self._lock:
            return len(self._lora_cache)

    @property
    def model_eviction_count(self) -> int:
        return self._model_eviction_count

    @property
    def lora_eviction_count(self) -> int:
        return self._lora_eviction_count

    def __len__(self) -> tuple[int, int]:
        with self._lock:
            return len(self._model_cache), len(self._lora_cache)


class HermesModelMonitor:
    """
    MemoryPressureListener + TTL sweep loop for model cache.

    Sprint Split-Brain: Extracted from HermesModelCache to isolate
    monitoring from storage. Enables independent testing of pressure
    response vs cache operations.
    """

    __slots__ = ("_loader", "_monitor_task", "_running")

    def __init__(self, loader: HermesModelLoader) -> None:
        self._loader = loader
        self._monitor_task: asyncio.Task | None = None
        self._running = False

    @property
    def listener_priority(self) -> int:
        return 0

    @property
    def listener_name(self) -> str:
        return "hermes_cache"

    def on_soft_warn(self) -> None:
        """R8: ELEVATED pressure — TTL sweep + evict idle LoRA adapters."""
        now = time.monotonic()
        cutoff = now - _MODEL_TTL_S
        evicted_models = 0
        evicted_loras = 0

        with self._loader._lock:
            stale_keys = [
                k
                for k, ts in list(self._loader._access_times.items())
                if ts < cutoff and k in self._loader._model_cache
            ]
            for key in stale_keys:
                del self._loader._model_cache[key]
                self._loader._access_times.pop(key, None)
                self._loader._model_eviction_count += 1
                evicted_models += 1
                if self._loader._on_evict_model:
                    self._loader._safe_call_hook(self._loader._on_evict_model, key)

            lora_to_evict = max(1, len(self._loader._lora_cache) // 2)
            for _ in range(lora_to_evict):
                self._loader._evict_lora_internal()
                evicted_loras += 1

        if evicted_models or evicted_loras:
            _mlx_cache_clear("soft_warn")
            logger.info(
                "[HermesModelMonitor] on_soft_warn: evicted %d model(s), %d LoRA(s)", evicted_models, evicted_loras
            )

    def on_warn(self) -> None:
        """R8: HIGH pressure — evict ALL LoRA adapters + oldest model."""
        evicted_models = 0
        evicted_loras = 0

        with self._loader._lock:
            lora_count = len(self._loader._lora_cache)
            for _ in range(lora_count):
                self._loader._evict_lora_internal()
                evicted_loras += 1

            while len(self._loader._model_cache) > 1:
                self._loader._evict_model_internal()
                evicted_models += 1

        if evicted_models or evicted_loras:
            _mlx_cache_clear("warn")
            logger.warning(
                "[HermesModelMonitor] on_warn: evicted %d model(s), %d LoRA(s)", evicted_models, evicted_loras
            )
        self._loader._emit_eviction_telemetry(self._loader._model_eviction_count, "hermes.cache.model_evictions")
        self._loader._emit_eviction_telemetry(self._loader._lora_eviction_count, "hermes.cache.lora_evictions")

    def on_critical(self) -> None:
        """R8: CRITICAL pressure — invalidate everything."""
        self._loader.clear_models()
        self._loader.clear_loras()
        _mlx_cache_clear("critical")
        logger.critical("[HermesModelMonitor] on_critical: all models/LORAs cleared")

    def on_normal(self) -> None:
        """R8: NORMAL pressure — no action needed."""

    async def pressure_check_loop(self) -> None:
        """Background loop: TTL sweep every 60s."""
        while True:
            await asyncio.sleep(60.0)
            try:
                now = time.monotonic()
                cutoff = now - _MODEL_TTL_S
                with self._loader._lock:
                    stale_keys = [
                        k
                        for k, ts in list(self._loader._access_times.items())
                        if ts < cutoff and k in self._loader._model_cache
                    ]
                    for key in stale_keys:
                        del self._loader._model_cache[key]
                        self._loader._access_times.pop(key, None)
                        self._loader._model_eviction_count += 1
                        _mlx_cache_clear(f"ttl_evict:{key}")
                        self._loader._emit_eviction_telemetry(
                            self._loader._model_eviction_count, "hermes.cache.model_evictions"
                        )
                        logger.debug("[HermesModelMonitor] TTL expired, evicted model: %s", key)
                        if self._loader._on_evict_model:
                            self._loader._safe_call_hook(self._loader._on_evict_model, key)
            except asyncio.CancelledError:
                logger.debug("[HermesModelMonitor] Pressure monitor cancelled")
                return
            except Exception:  # noqa: BLE001
                pass

    def start_monitor(self, _loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Start the background pressure monitor."""
        if _loop is not None:
            warnings.warn(
                "loop= argument is deprecated; the event loop is resolved automatically",
                DeprecationWarning,
                stacklevel=2,
            )
        if self._monitor_task is not None and not self._monitor_task.done():
            return
        self._monitor_task = safe_create_task(self.pressure_check_loop(), name="hermes_cache:monitor")
        try:
            from hledac.universal._core.memory_pressure import get_broadcaster

            bc = get_broadcaster()
            safe_create_task(bc.start(), name="memory_pressure:start")
        except Exception:  # noqa: BLE001:
            pass
        logger.info("[HermesModelMonitor] Monitor task started")

    async def stop_monitor(self) -> None:
        """Cancel and await the monitor task shutdown."""
        if self._monitor_task is None:
            return
        self._monitor_task.cancel()
        try:
            await self._monitor_task
        except asyncio.CancelledError:  # noqa: BLE001
            pass
        self._monitor_task = None
        logger.info("[HermesModelMonitor] Monitor task stopped")


class HermesModelCache:
    """
    Unified model cache facade — delegates to Loader + Monitor.

    Sprint Split-Brain: Facade orchestrating HermesModelLoader
    (storage) and HermesModelMonitor (pressure response).
    """

    __slots__ = ("_loader", "_monitor")

    def __init__(
        self,
        max_size: int | None = None,
        lora_max_size: int | None = None,
        on_evict_model: Callable[[str], None] | None = None,
        on_evict_lora: Callable[[str], None] | None = None,
    ) -> None:
        self._loader = HermesModelLoader(
            max_size=max_size,
            lora_max_size=lora_max_size,
            on_evict_model=on_evict_model,
            on_evict_lora=on_evict_lora,
        )
        self._monitor = HermesModelMonitor(self._loader)

    # Delegate storage operations to loader
    @property
    def lock(self) -> threading.RLock:
        return self._loader.lock

    async def async_acquire(self) -> None:
        return await self._loader.async_acquire()

    def release(self) -> None:
        self._loader.release()

    def get_model(self, key: str) -> tuple[MLXModel, MLXTokenizer] | None:
        return self._loader.get_model(key)

    def put_model(self, key: str, model: MLXModel, tokenizer: MLXTokenizer) -> bool:
        return self._loader.put_model(key, model, tokenizer)

    def evict_model(self, key: str) -> bool:
        return self._loader.evict_model(key)

    def clear_models(self) -> int:
        return self._loader.clear_models()

    def get_lora(self, key: str) -> tuple[MLXModel, MLXTokenizer] | None:
        return self._loader.get_lora(key)

    def put_lora(self, key: str, lora_model: MLXModel, lora_tokenizer: MLXTokenizer) -> bool:
        return self._loader.put_lora(key, lora_model, lora_tokenizer)

    def clear_loras(self) -> int:
        return self._loader.clear_loras()

    @property
    def model_count(self) -> int:
        return self._loader.model_count

    @property
    def lora_count(self) -> int:
        return self._loader.lora_count

    @property
    def model_eviction_count(self) -> int:
        return self._loader.model_eviction_count

    @property
    def lora_eviction_count(self) -> int:
        return self._loader.lora_eviction_count

    def __len__(self) -> tuple[int, int]:
        return self._loader.__len__()

    # Delegate monitoring to monitor
    @property
    def listener_priority(self) -> int:
        return self._monitor.listener_priority

    @property
    def listener_name(self) -> str:
        return self._monitor.listener_name

    def on_soft_warn(self) -> None:
        self._monitor.on_soft_warn()

    def on_warn(self) -> None:
        self._monitor.on_warn()

    def on_critical(self) -> None:
        self._monitor.on_critical()

    def on_normal(self) -> None:
        self._monitor.on_normal()

    def start_monitor(self, _loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._monitor.start_monitor(_loop)

    async def stop_monitor(self) -> None:
        await self._monitor.stop_monitor()


def _set_eviction_attr(name: str, value: str | int) -> None:
    """Fail-open OTel attribute emit."""
    try:
        from otel._instrumentation import set_attribute

        set_attribute(name, value)
    except Exception:  # noqa: BLE001:
        pass


def _hermes_cache_evict_model_otel(key: str) -> None:
    """Emit OTel span attrs on model eviction."""
    _set_eviction_attr("hermes.cache.model_eviction", key)


def _hermes_cache_evict_lora_otel(key: str) -> None:
    """Emit OTel span attrs on LoRA adapter eviction."""
    _set_eviction_attr("hermes.cache.lora_eviction", key)


_HERMES_CACHE: HermesModelCache | None = None


def hermes_cache() -> HermesModelCache:
    """Return the global HermesModelCache singleton (lazy init)."""
    global _HERMES_CACHE
    if _HERMES_CACHE is None:
        _HERMES_CACHE = HermesModelCache(
            on_evict_model=_hermes_cache_evict_model_otel,
            on_evict_lora=_hermes_cache_evict_lora_otel,
        )
    return _HERMES_CACHE
