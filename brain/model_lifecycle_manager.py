"""
ModelLifecycleManager — Single source of truth pro model load/unload lifecycle
============================================================================


A5-03: ModelManager God Object decomposition.

Tento modul obsahuje POUZE lifecycle logiku:
- load_model() / unload_model() / release_all()
- Model swap (unload current, load new)
- Memory admission check
- RSS verification po unload

Na rozdíl od původního ModelManager, tento:
- NENÍ závislý na embedding_pipeline
- NENÍ závislý na report generation
- Drží pouze _loaded_models a _current_model state

Facade pattern: ModelManager deleguje na tuto službu.
"""
from __future__ import annotations

import asyncio
import gc
import inspect
import logging
import time
from collections import defaultdict
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from hledac.universal.brain.model_inference_guard import check_model_allowed, record_model_failure, record_model_success
from hledac.universal.brain.model_lifecycle import ensure_mlx_runtime_initialized
from hledac.universal.utils.asyncx import safe_create_task
from hledac.universal.utils.concurrency import adjust_fetch_workers
from hledac.universal.utils.exceptions import MemoryPressureError

if TYPE_CHECKING:
    from enum import Enum

logger = logging.getLogger(__name__)

# Model size estimates (GB)
_MODEL_SIZES_GB = {'hermes': 1.75, 'modernbert': 0.5, 'gliner': 0.3}
_UNLOAD_TIMEOUT_S: float = 5.0


def _get_current_rss_gb() -> float:
    """Get current RSS memory in GB. Used for memory guard checks."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1000000000.0
    except Exception:
        return 0.0


def _check_rss_before_load(model_key: str, max_rss_gb: float = 6.0) -> float:
    """Check RSS before model load. Raises MemoryPressureError if too high."""
    current_rss = _get_current_rss_gb()
    model_size = _MODEL_SIZES_GB.get(model_key.lower(), 0.5)
    threshold = max_rss_gb - model_size
    if current_rss > threshold:
        raise MemoryPressureError(
            f'[MODEL MEMORY] RSS {current_rss:.2f}GB > threshold {threshold:.2f}GB '
            f'(max_rss_gb={max_rss_gb}, model={model_key}, size~{model_size}GB). '
            f'Skipping model load.'
        )
    return current_rss


def _verify_rss_after_unload(model_key: str, rss_before: float) -> None:
    """Verify RSS dropped after model unload."""
    rss_after = _get_current_rss_gb()
    model_size = _MODEL_SIZES_GB.get(model_key.lower(), 0.5)
    dropped = rss_before - rss_after
    noop_threshold = model_size * 0.5
    if rss_before < noop_threshold:
        logger.debug(f'[MODEL MEMORY] Unload was a no-op for {model_key} '
                      f'(rss_before={rss_before:.2f}GB < expected~{model_size:.2f}GB)')
        return
    if dropped < noop_threshold:
        logger.warning(f'[MODEL MEMORY] RSS did not drop after unload: '
                       f'dropped={dropped:.2f}GB, expected~{model_size:.2f}GB')
    else:
        logger.info(f'[MODEL MEMORY] Model unloaded (RSS dropped={dropped:.2f}GB, model={model_key})')


class ModelLifecycleManager:
    """
    Pure lifecycle manager — load/unload/swap bez vedlejších zodpovědností.

    A5-03: Single Responsibility — tento manager NENÍ zodpovědný za:
    - Embedding management (to je embedding_pipeline)
    - Report generation (to je ReportGenerator)
    - Quantization selection (to je QuantizationSelector)

    Drží pouze:
    - _loaded_models: dict[ModelType, Any]
    - _current_model: ModelType | None
    - _lock: asyncio.Lock (pro thread-safety)
    """

    __slots__ = ('_loaded_models', '_current_model', '_model_factories', '_lock', '_model_locks')

    def __init__(self) -> None:
        self._loaded_models: dict[Any, Any] = {}
        self._current_model: Any = None
        self._model_factories: dict[Any, Callable[[], Any]] = {}
        self._lock = asyncio.Lock()
        self._model_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def register_factory(self, model_type: Any, factory: Callable[[], Any]) -> None:
        """Register a model factory for given model type."""
        self._model_factories[model_type] = factory

    async def load_model(self, model_name: str) -> Any:
        """
        Async načtení modelu do paměti.

        Pokud je již načten jiný model, nejprve ho uvolní (swap).
        """
        async with self._lock:
            return await self._load_model_async(model_name)

    async def _load_model_async(self, model_name: str) -> Any:
        """Internal async implementation of model loading."""
        model_key = model_name.lower()
        decision = check_model_allowed(model_key)
        if not decision.allowed:
            raise RuntimeError(f'model inference blocked: {model_key}, retry after {decision.retry_after_s:.1f}s')

        async with self._model_locks[model_key]:
            if model_key not in self._model_factories:
                raise ValueError(f'Unknown model: {model_name}')

            from hledac.universal.brain.model_lifecycle import ensure_mlx_runtime_initialized
            ensure_mlx_runtime_initialized()

            if self._is_model_loaded(model_key):
                self._current_model = self._get_model_type(model_key)
                logger.debug(f'Model {model_name} already loaded')
                return self._get_loaded_model(self._get_model_type(model_key))

            # Swap: unload current model first
            if self._current_model is not None:
                logger.info(f'[PHASE SWITCH] Releasing {self._current_model.name} before loading {model_name}')
                unload_task = safe_create_task(self._release_current_async())
                if unload_task:
                    try:
                        await unload_task
                    except Exception as e:
                        logger.warning(f'[PHASE SWITCH] Unload error: {e}')

            factory = self._model_factories[model_key]
            model = factory()
            self._set_loaded_model(self._get_model_type(model_key), model)
            self._current_model = self._get_model_type(model_key)
            record_model_success(model_key)
            logger.info(f'[MODEL LOAD] {model_name} loaded successfully')
            return model

    async def release_model(self, model_name: str) -> None:
        """Async uvolnění modelu z paměti."""
        async with self._lock:
            model_type = self._get_model_type(model_name.lower())
            if model_type is None:
                raise ValueError(f'Unknown model: {model_name}')
            if not self._is_model_loaded(model_name.lower()):
                logger.debug(f'Model {model_name} not loaded')
                return
            await self._release_model_async(model_type, model_name)

    async def _release_model_async(self, model_type: Any, model_name: str) -> None:
        """Internal async implementation of model release."""
        model = self._loaded_models.get(model_type)
        rss_before = _get_current_rss_gb()
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]
        if self._current_model == model_type:
            self._current_model = None
        await self._unload_model_with_verification(model, model_type, model_name, rss_before)

    async def _unload_model_with_verification(
        self, model: Any, model_type: Any, model_name: str, rss_before: float
    ) -> None:
        """Shared helper: unload model + verify RSS delta."""
        if model is not None and hasattr(model, 'unload'):
            logger.info(f'[MODEL RELEASE] {model_name} start')
            try:
                unload_coro = model.unload() if inspect.iscoroutinefunction(model.unload) else asyncio.to_thread(model.unload)
                try:
                    async with asyncio.timeout(_UNLOAD_TIMEOUT_S):
                        await unload_coro
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    logger.warning('[MODEL] Unload timed out after %.1fs for %s', _UNLOAD_TIMEOUT_S, model_name)
                except Exception as e:
                    logger.error(f'Failed to release model {model_name}: {e}')
                else:
                    logger.info(f'[MODEL RELEASE] {model_name} done')
            finally:
                await self._cleanup_memory_async(model_type, engine=model)
        _verify_rss_after_unload(model_name.lower(), rss_before)
        await adjust_fetch_workers(25)

    async def _cleanup_memory_async(self, model_type: Any | None = None, engine: Any | None = None) -> None:
        """Agresivní async čištění paměti po uvolnění modelu."""
        gc.collect()
        try:
            from hledac.universal.utils.mlx_memory import _get_mlx_core
            mx = _get_mlx_core()
            if mx is not None:
                mx.eval([])
                mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass

    async def release_current(self) -> None:
        """Async uvolnění aktuálně načteného modelu."""
        async with self._lock:
            await self._release_current_async()

    async def _release_current_async(self) -> None:
        """Internal async implementation of releasing current model."""
        if self._current_model is None:
            return
        model_type = self._current_model
        model_name = model_type.name.lower()
        rss_before = _get_current_rss_gb()
        model = self._loaded_models.get(model_type)
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]
        if self._current_model == model_type:
            self._current_model = None
        await self._unload_model_with_verification(model, model_type, model_name, rss_before)
        await adjust_fetch_workers(25)

    async def release_all(self) -> None:
        """Async uvolnění všech modelů z paměti."""
        logger.info('Releasing all models...')
        async with self._lock:
            last_released: Any = None
            last_engine: Any = None
            for model_type, model in list(self._loaded_models.items()):
                last_released = model_type
                last_engine = model
            self._loaded_models.clear()
            self._current_model = None
            if last_engine is not None:
                await self._cleanup_memory_async(last_released, engine=last_engine)
            logger.info('✓ All models released')

    def is_loaded(self, model_key: str) -> bool:
        """Check if model is currently loaded."""
        mt = self._get_model_type(model_key)
        return mt is not None and mt in self._loaded_models

    def get_current_model_name(self) -> str | None:
        """Get name of currently loaded model."""
        if self._current_model is None:
            return None
        return self._current_model.name.lower()

    def _is_model_loaded(self, model_key: str) -> bool:
        mt = self._get_model_type(model_key)
        return mt is not None and mt in self._loaded_models

    def _get_loaded_model(self, model_type: Any) -> Any:
        return self._loaded_models.get(model_type)

    def _set_loaded_model(self, model_type: Any, model: Any) -> None:
        self._loaded_models[model_type] = model

    def _get_model_type(self, model_key: str) -> Any:
        """Get ModelType enum from string key. Override in subclass."""
        # This should be overridden or the enum passed in constructor
        from enum import auto
        class ModelType(Enum):
            HERMES = auto()
            MODERNBERT = auto()
            GLINER = auto()
        key_map = {'hermes': ModelType.HERMES, 'modernbert': ModelType.MODERNBERT, 'gliner': ModelType.GLINER}
        return key_map.get(model_key.lower())
