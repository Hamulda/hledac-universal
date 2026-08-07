"""
ModelManager - Správa životního cyklu modelů na M1 8GB

Zajišťuje:



- Sekvenční načítání modelů (nikdy nejsou 2 velké modely současně v RAM)
- Automatické uvolňování paměti (gc + MLX cache clear)

- Jednotné rozhraní pro Hermes3, ModernBERT a GLiNER
- Strict 1-model-at-a-time policy pro M1 8GB stabilitu

- F4XX: Out-of-Process inference přes mlxcel UNIX socket / subprocess
  (mlxcel = externí Rust binárka, šetří ~300MB Python RSS)
"""
import asyncio
import gc
import inspect
import logging
import os
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from enum import Enum, auto
from pathlib import Path
from typing import Any, Literal, TypeVar

from hledac.universal.brain.model_inference_guard import check_model_allowed, record_model_failure, record_model_success
from hledac.universal.brain.quantization_selector import QuantizationSelector
from hledac.universal.utils.async_helpers import safe_create_task
from hledac.universal.utils.concurrency import adjust_fetch_workers
from hledac.universal.utils.exceptions import MemoryPressureError
from hledac.universal.utils.executor_decorator import offload_to
from hledac.universal.utils.import_resolver import lazy

T = TypeVar('T')
MLX_AVAILABLE = False
_MLXCEL_DETECTED: bool = False

def _detect_mlxcel() -> bool:
    """
    F4XX: Detect mlxcel binary presence.

    Checks standard locations + PATH for the mlxcel Rust binary.
    Does NOT attempt connection — only checks if binary exists.

    mlxcel is the out-of-process inference server (Rust, separate process).
    When available, all Hermes inference routes through MlxcelIpcClient
    instead of in-process mlx-lm Python bindings (~300MB RSS savings).
    """
    global _MLXCEL_DETECTED
    if _MLXCEL_DETECTED:
        return True
    _search_paths = [Path.home() / '.local' / 'bin' / 'mlxcel', Path.home() / 'bin' / 'mlxcel', Path('/usr/local/bin/mlxcel'), Path('/opt/homebrew/bin/mlxcel'), Path('/opt/bin/mlxcel')]
    for path in _search_paths:
        if path.exists():
            _MLXCEL_DETECTED = True
            logger.info('[MLXCEL] Detected mlxcel binary at %s', path)
            return True
    for directory in os.environ.get('PATH', '').split(os.pathsep):
        candidate = Path(directory) / 'mlxcel'
        if candidate.exists():
            _MLXCEL_DETECTED = True
            logger.info('[MLXCEL] Detected mlxcel binary at %s', candidate)
            return True
    return False

def _mlxcel_is_available() -> bool:
    """Runtime check: mlxcel binary detected on system."""
    return _detect_mlxcel()
_model_max_rss_gb: float = 6.0
_MODEL_SIZES_GB = {'hermes': 1.75, 'modernbert': 0.5, 'gliner': 0.3}
_UNLOAD_TIMEOUT_S: float = 5.0

def _load_unload_timeout() -> float:
    """Load unload timeout from env, validated with fallback default."""
    try:
        val = float(os.environ.get('HLEDAC_MODEL_UNLOAD_TIMEOUT_S', '5.0'))
        if val <= 0:
            raise ValueError('timeout must be positive')
        return val
    except (ValueError, TypeError):
        logger.warning('[P1E-B] HLEDAC_MODEL_UNLOAD_TIMEOUT_S=%r invalid, using default 5.0s', os.environ.get('HLEDAC_MODEL_UNLOAD_TIMEOUT_S'))
        return 5.0
logger = logging.getLogger(__name__)

# ISSUE [LLM-SEC-001]: Lazy import for LLM input sanitization
sanitize_for_llm = lazy('.prompt_injection_validator.sanitize_for_llm')

def set_model_memory_limit(max_rss_gb: float) -> None:
    """P19: Set max RSS GB threshold for model memory guard."""
    global _model_max_rss_gb
    _model_max_rss_gb = max_rss_gb

def _get_current_rss_gb() -> float:
    """P19: Get current RSS memory in GB. Used for memory guard checks."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1000000000.0
    except Exception:
        return 0.0

def _check_rss_before_load(model_key: str) -> float:
    """
    P19: Check RSS before model load.

    Args:
        model_key: Model identifier (hermes, modernbert, gliner)

    Returns:
        Current RSS in GB before check.

    Raises:
        MemoryPressureError: If RSS too high to safely load model.
    """
    current_rss = _get_current_rss_gb()
    model_size = _MODEL_SIZES_GB.get(model_key.lower(), 0.5)
    threshold = _model_max_rss_gb - model_size
    if current_rss > threshold:
        raise MemoryPressureError(f'[MODEL MEMORY] RSS {current_rss:.2f}GB > threshold {threshold:.2f}GB (max_rss_gb={_model_max_rss_gb}, model={model_key}, size~{model_size}GB). Skipping model load.')
    return current_rss

def _verify_rss_after_unload(model_key: str, rss_before: float) -> None:
    """
    P19: Verify RSS dropped after model unload.

    Args:
        model_key: Model identifier
        rss_before: RSS in GB before unload
    """
    rss_after = _get_current_rss_gb()
    model_size = _MODEL_SIZES_GB.get(model_key.lower(), 0.5)
    dropped = rss_before - rss_after
    noop_threshold = model_size * 0.5
    if rss_before < noop_threshold:
        logger.debug(f'[MODEL MEMORY] Unload was a no-op for {model_key} (rss_before={rss_before:.2f}GB < expected~{model_size:.2f}GB); skipping RSS drop verification.')
        return
    if dropped < noop_threshold:
        logger.warning(f'[MODEL MEMORY] RSS did not drop expected amount after unload: dropped={dropped:.2f}GB, expected~{model_size:.2f}GB (RSS before={rss_before:.2f}GB, after={rss_after:.2f}GB, model={model_key})')
    else:
        logger.info(f'[MODEL MEMORY] Model unloaded (RSS dropped={dropped:.2f}GB, model={model_key})')

def _get_mlx_safe() -> Any:
    """Get mlx.core module via mlx_memory lazy init. Returns mx or None."""
    global MLX_AVAILABLE
    if MLX_AVAILABLE:
        try:
            from ..utils.mlx_memory import _get_mlx_core
            return _get_mlx_core()
        except Exception:
            return None
    return None
MODELS_DIR = Path.home() / '.hledac' / 'models'
MODELS_DIR.mkdir(parents=True, exist_ok=True)
COREML_MODEL_PATH = MODELS_DIR / 'modernbert_ane.mlpackage'
ModelName = Literal['hermes', 'modernbert', 'gliner']

class ModelType(Enum):
    """Typy podporovaných modelů."""
    HERMES = auto()
    MODERNBERT = auto()
    GLINER = auto()

class MlxcelHermesAdapter:
    """
    F4XX: Out-of-Process Hermes adapter — routes ALL inference through mlxcel.

    This adapter wraps MlxcelIpcClient and exposes the same `generate()` interface
    as DeepHermes3Engine. It is returned by ModelManager._create_hermes_engine()
    when mlxcel binary is detected on the system.

    Benefits vs DeepHermes3Engine (in-process mlx-lm):
      - RSS savings: ~300MB (Python mlx-lm bindings + MLX Metal runtime not loaded)
      - Failure isolation: mlxcel crash ≠ Python crash
      - M1 8GB: Python orchestrator stays under the 6.25GB ceiling

    Fallback: If mlxcel becomes unavailable at runtime, generate() raises
    MlxcelUnavailable and callers should fall back to DeepHermes3Engine.
    """
    __slots__ = ('_client', '_config', '_initialized')

    def __init__(self) -> None:
        self._client = None
        self._config = self._default_config()
        self._initialized = False

    @staticmethod
    def _default_config() -> Any:
        """Return a minimal config object matching DeepHermes3Engine expectations."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class _Cfg:
            model_path: str = 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'
            max_tokens: int = 1024
            temperature: float = 0.7
            context_window: int = 8192
        return _Cfg()

    async def initialize(self) -> None:
        """Lazy-init mlxcel client on first use."""
        if self._initialized:
            return
        try:
            from hledac.universal.brain.mlxcel_ipc_client import get_mlxcel_client
            self._client = await get_mlxcel_client()
            self._initialized = True
            logger.info('[MLXCEL ADAPTER] Client initialized')
        except Exception as e:
            logger.warning('[MLXCEL ADAPTER] Failed to init mlxcel client: %s', e)
            self._initialized = False

    async def generate(self, prompt: str, temperature: float | None=None, max_tokens: int | None=None, system_msg: str | None=None, *, thinking: bool=True, adapter_path: str | None=None) -> str:
        """
        Generate text via mlxcel subprocess (same signature as DeepHermes3Engine.generate).

        Raises:
            MlxcelUnavailable: If mlxcel is not connected / not available.
        """
        if not self._initialized:
            await self.initialize()
        if self._client is None:
            from hledac.universal.brain.mlxcel_ipc_client import MlxcelUnavailable
            raise MlxcelUnavailable('mlxcel client not available')
        try:
            result = await self._client.generate(prompt=prompt, temperature=temperature or self._config.temperature, max_tokens=max_tokens or self._config.max_tokens, system_msg=system_msg, thinking=thinking, adapter_path=adapter_path)
            return result.text
        except Exception as e:
            from hledac.universal.brain.mlxcel_ipc_client import MlxcelUnavailable
            raise MlxcelUnavailable(f'mlxcel generate failed: {e}') from e

    async def generate_stream(self, prompt: str, max_tokens: int=512, system_msg: str | None=None, temperature: float | None=None, *, thinking: bool=True) -> AsyncIterator[str]:
        """
        Stream generated tokens via mlxcel subprocess.

        Yields:
            Token chunks as they are generated.
        """
        if not self._initialized:
            await self.initialize()
        if self._client is None:
            from hledac.universal.brain.mlxcel_ipc_client import MlxcelUnavailable
            raise MlxcelUnavailable('mlxcel client not available')
        try:
            async for chunk in self._client.generate_stream(prompt=prompt, temperature=temperature or self._config.temperature, max_tokens=max_tokens, system_msg=system_msg, thinking=thinking):
                yield chunk
        except Exception as e:
            logger.warning('[MLXCEL ADAPTER] Stream error: %s', e)

    def apply_lora_adapter(self, adapter_path: str | None) -> None:
        """LoRA adapter not yet supported via mlxcel IPC (stub for API compatibility)."""
        if adapter_path is not None:
            logger.debug('[MLXCEL ADAPTER] LoRA adapter not yet supported via IPC')

    @property
    def stats(self) -> Any:
        """Return IPC telemetry from mlxcel client."""
        if self._client is not None:
            return self._client.stats
        return None

@asynccontextmanager
async def model_lifecycle(model_name: ModelName) -> Any:  # type: ignore[return-value]
    """
    Async context manager pro striktní 1-model-at-a-time lifecycle.

    Zajišťuje:
    - Načtení modelu s proper logging
    - Yield model instance
    - V finally: release + gc.collect() + mx.clear_cache()

    Usage:
        async with model_lifecycle("hermes") as model:
            result = await model.generate(...)

    Args:
        model_name: Jméno modelu ("hermes", "modernbert", "gliner")

    Yields:
        Načtená instance modelu
    """
    manager = get_model_manager()
    if manager._current_model is not None:
        current = manager._current_model.name.lower()
        if current != model_name:
            logger.warning(f"[MODEL CONFLICT] Requested '{model_name}' but '{current}' is loaded. Releasing current model first.")
            await manager._release_current_async()
    model = await manager._load_model_async(model_name)
    try:
        yield model
    finally:
        await manager._release_current_async()

class ModelManager:
    """
    Centrální správa životního cyklu modelů.

    Klíčová vlastnost: Pouze JEDEN model může být najednou v RAM.
    To zajišťuje stabilitu na M1 8GB.

    Použití:
        # Doporučené - context manager:
        async with model_lifecycle("hermes") as model:
            result = await model.generate(...)

        # Nebo explicitní management:
        manager = ModelManager()
        model = await manager.load_model("hermes")
        # ... použití ...
        await manager.release_current()
    """
    MODEL_REGISTRY: dict[str, ModelType] = {'hermes': ModelType.HERMES, 'modernbert': ModelType.MODERNBERT, 'gliner': ModelType.GLINER}
    PHASE_MODEL_MAP: dict[str, ModelName] = {'PLAN': 'hermes', 'DECIDE': 'hermes', 'GENERATE': 'hermes', 'EMBED': 'modernbert', 'DEDUP': 'modernbert', 'ROUTING': 'modernbert', 'NER': 'gliner', 'ENTITY': 'gliner'}
    __slots__ = ('_ane_embedder', '_current_model', '_loaded_models', '_lock', '_mlx_embedder', '_model_factories', '_model_locks', '_psutil', '_psutil_available')

    def __init__(self) -> None:
        self._loaded_models: dict[ModelType, Any] = {}
        self._current_model: ModelType | None = None
        self._model_factories: dict[ModelType, Callable[[], Any]] = {ModelType.HERMES: self._create_hermes_engine, ModelType.MODERNBERT: self._create_modernbert_engine, ModelType.GLINER: self._create_gliner_engine}
        self._lock = asyncio.Lock()
        self._ane_embedder = None
        self._mlx_embedder = None
        self._model_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        from hledac.universal.core.psutil_shim import PSUTIL_AVAILABLE
        from hledac.universal.core.psutil_shim import psutil as _ps
        self._psutil = _ps
        self._psutil_available = PSUTIL_AVAILABLE

    def _create_hermes_engine(self) -> Any:
        """
        Factory pro Hermes3Engine / MlxcelHermesAdapter.

        F4XX: Pokud je dostupný mlxcel (Rust out-of-process inference server),
        vrací MlxcelHermesAdapter — všechna generování jdou přes UNIX socket do
        externího Rust procesu (~300MB RSS úspora oproti in-process mlx-lm).

        Pokud mlxcel není k dispozici, vrací DeepHermes3Engine (původní
        in-process mlx-lm Python bindings) — nouzový fallback.
        """
        if _mlxcel_is_available():
            logger.info('[MODEL MANAGER] mlxcel detected — routing Hermes via MlxcelHermesAdapter')
            return MlxcelHermesAdapter()
        logger.info('[MODEL MANAGER] mlxcel not available — using DeepHermes3Engine (in-process mlx-lm)')
        from .deephermes3_engine import DeepHermes3Engine
        return DeepHermes3Engine()

    def _create_modernbert_engine(self) -> Any:
        """Factory pro ModernBertModelAdapter (bridges ModernBertEngine → ModelEngine)."""
        from .modernbert_adapter import ModernBertModelAdapter
        return ModernBertModelAdapter()

    def _create_gliner_engine(self) -> Any:
        """Factory pro NEREngine s gliner-relex (NER + relation extraction)."""
        try:
            from gliner import GLiNER

            class NEREngine:
                """NER+RE Engine pomocí gliner-relex-large-v0.5."""
                DEFAULT_MODEL = 'knowledgator/gliner-relex-large-v0.5'
                __slots__ = ('_is_loaded', '_model')

                def __init__(self) -> None:
                    self._model = None
                    self._is_loaded = False

                async def load(self) -> None:
                    """Načte gliner-relex model - async verze."""
                    if not self._is_loaded:
                        logger.info('[MODEL LOAD] gliner-relex start')
                        self._model = await asyncio.to_thread(lambda: GLiNER.from_pretrained(self.DEFAULT_MODEL, map_location='cpu'))
                        self._is_loaded = True
                        logger.info('[MODEL LOAD] gliner-relex done')

                def extract(self, text: str, labels: list[str], relations: list[dict] | None=None, threshold: float=0.5) -> dict[str, Any]:
                    """Extract entities and optionally relations."""
                    if not self._is_loaded:
                        raise RuntimeError('Model not loaded. Use load() first.')
                    if relations:
                        entities, rels = self._model.predict(texts=[text], labels=labels, relations=relations, threshold=threshold, return_relations=True)
                        return {'entities': entities[0] if entities else [], 'relations': rels[0] if rels else []}
                    else:
                        entities = self._model.predict_entities(text, labels, threshold=threshold)
                        return {'entities': entities, 'relations': []}

                async def unload(self) -> None:
                    """Uvolní model z paměti - async verze."""
                    if self._is_loaded:
                        logger.info('[MODEL RELEASE] gliner-relex start')
                        self._model = None
                        self._is_loaded = False
                        logger.info('[MODEL RELEASE] gliner-relex done')
            return NEREngine()
        except ImportError:
            logger.error('GLiNER not installed. Install with: pip install gliner')
            raise

    def _check_memory_admission(self) -> None:
        """
        Deterministický fail-fast gate před těžkým model loadem.

        F183C FIX: Používá status.state PŘÍMO z sample_uma_status(),
        ne znovu volá evaluate_uma_state() — předchází redundantnímu přepočtu.

        Raises:
            RuntimeError: Pokud je memory pressure příliš vysoký.
        """
        try:
            from hledac.universal.core.resource_governor import (
                UMA_STATE_CRITICAL,
                UMA_STATE_EMERGENCY,
                sample_uma_status,
            )
        except ImportError:
            return
        try:
            status = sample_uma_status()
            if status.state == UMA_STATE_EMERGENCY:
                raise RuntimeError(f'[MEMORY ADMISSION] EMERGENCY state ({status.system_used_gib:.2f} GiB) — model load BLOCKED to prevent OOM. Free up memory before retrying.')
            if status.state == UMA_STATE_CRITICAL:
                raise RuntimeError(f'[MEMORY ADMISSION] CRITICAL state ({status.system_used_gib:.2f} GiB) — model load BLOCKED to prevent OOM. Free up memory before retrying.')
        except RuntimeError:
            raise
        except Exception:  # noqa: BLE001
            pass

    def _check_memory_pressure(self, threshold_gb: float=1.5) -> bool:
        """Check free RAM, clear MLX cache if below threshold (soft fail)."""
        if not self._psutil_available:
            return False
        try:
            available = self._psutil.virtual_memory().available / 1000000000.0
            if available < threshold_gb:
                mx = _get_mlx_safe()
                if MLX_AVAILABLE and mx is not None:
                    # U2-02 FIX: offload mx.eval([]) + clear_cache() to thread —
                    # direct calls block the event loop for 1-50ms during Metal GPU sync.
                    try:
                        _loop = asyncio.get_running_loop()
                        _loop.run_in_executor(None, _sync_eval_and_clear_cache)
                    except RuntimeError:
                        # No event loop — fall back to direct sync call.
                        _sync_eval_and_clear_cache()
                logger.warning(f'[MEMORY] Low RAM: {available:.2f}GB, MLX cache cleared')
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _load_coreml_embedder(self) -> Any:
        """Load CoreML version of ModernBERT if available. Returns None if not."""
        if not COREML_MODEL_PATH.exists():
            logger.debug('[COREML] CoreML model not found, will use MLX fallback')
            return None
        try:
            import coremltools as ct
            mlmodel = ct.models.MLModel(str(COREML_MODEL_PATH))
            logger.info('[COREML] Loaded ANE version of ModernBERT')
            return mlmodel
        except Exception as e:
            logger.warning(f'[COREML] Failed to load CoreML model: {e}')
            return None

    async def _convert_modernbert_to_coreml(self, embedder: Any) -> bool:
        """
        Convert ModernBERT embedder to CoreML format.
        Returns True if conversion succeeded and accuracy passes threshold.
        """
        if COREML_MODEL_PATH.exists():
            return True
        if embedder is None or not hasattr(embedder, '_model'):
            logger.warning('[COREML] No embedder model to convert')
            return False
        try:
            import coremltools as ct
            logger.info('[COREML] Starting conversion to CoreML...')
            mlx_model = embedder._model
            try:
                ct_model = ct.convert(mlx_model, source='pytorch', convert_to='mlprogram', compute_units=ct.ComputeUnit.CPU_AND_NE)
            except Exception as e:
                logger.debug(f'[COREML] Direct conversion failed: {e}, trying alternative')
                return False
            ct_model.save(str(COREML_MODEL_PATH))
            logger.info(f'[COREML] Model saved to {COREML_MODEL_PATH}')
            return True
        except Exception as e:
            logger.warning(f'[COREML] Conversion failed: {e}')
            return False

    @asynccontextmanager
    async def acquire_model_ctx(self, model_name: str) -> Any:  # type: ignore[return-value]
        """
        Context manager that guarantees model unload on exit.

        Usage:
            async with manager.acquire_model_ctx("gliner") as model:
                result = await model.extract(...)
        """
        model = await self.load_model(model_name)
        try:
            yield model
        finally:
            await self.release_model(model_name)
            mx = _get_mlx_safe()
            if MLX_AVAILABLE and mx is not None:
                # U2-02 FIX: offload mx.eval([]) + clear_cache() to thread —
                # direct calls block the event loop for 1-50ms during Metal GPU sync.
                try:
                    _loop = asyncio.get_running_loop()
                    _loop.run_in_executor(None, _sync_eval_and_clear_cache)
                except RuntimeError:
                    _sync_eval_and_clear_cache()

    async def with_model(self, model_name: ModelName) -> Any:  # type: ignore[return-value]
        """
        Vrátí async context manager pro daný model.

        Usage:
            async with manager.with_model("hermes") as model:
                result = await model.generate(...)

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Async context manager yielding model instance
        """
        return model_lifecycle(model_name)

    def _estimate_context_length(self, cache) -> int:
        """Estimate context length from KV cache structure."""
        try:
            if hasattr(cache, 'shape') and len(cache.shape) >= 2:
                return cache.shape[1] if len(cache.shape) > 1 else 0
            return 0
        except Exception:
            return 0

    async def load_model(self, model_name: ModelName) -> Any:
        """
        Async načtení modelu do paměti.

        Pokud je již načten jiný model, nejprve ho uvolní.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Instance načteného modelu

        Raises:
            ValueError: Pokud je model_name neznámé
            RuntimeError: Pokud se načtení nepodaří
        """
        async with self._lock:
            return await self._load_model_async(model_name)

    async def _ensure_hermes_model_downloaded(self) -> None:
        """
        Ensure Hermes-3 model is downloaded. If not present, downloads it.
        During download, reduces HTTP worker pool from 25 to 3 to conserve memory.
        After download completes, restores full concurrency.
        """
        try:
            import mlx_lm
        except ImportError:
            logger.error('[MODEL DOWNLOAD] mlx_lm not available - cannot download Hermes')
            raise RuntimeError('mlx_lm required for Hermes model download')
        model_id = 'mlx-community/DeepHermes-3-Llama-3-3B-Preview-4bit'
        try:
            # C2-FIX: mlx_lm.load() is blocking I/O. Wrapped in offload_to() to avoid blocking event loop.
            await offload_to('cpu_io_pool', mlx_lm.load, model_id)
            logger.info(f'[MODEL DOWNLOAD] Hermes-3 already cached at {model_id}')
            return
        except Exception:  # noqa: BLE001
            pass
        logger.info(f'[MODEL DOWNLOAD] Hermes-3 not found, downloading {model_id}...')
        logger.info('[MODEL DOWNLOAD] Reducing HTTP worker pool to 3 during download')
        await adjust_fetch_workers(3)
        try:
            await offload_to('cpu_io_pool', mlx_lm.download, model_id)
            logger.info('[MODEL DOWNLOAD] Hermes-3 downloaded successfully')
        finally:
            logger.info('[MODEL DOWNLOAD] Restoring HTTP worker pool to 25')
            await adjust_fetch_workers(25)

    async def _load_model_async(self, model_name: str) -> Any:
        """Interní async implementace načtení modelu."""
        model_key = model_name.lower()
        decision = check_model_allowed(model_key)
        if not decision.allowed:
            raise RuntimeError(f'model inference blocked: {model_key}, retry after {decision.retry_after_s:.1f}s')
        async with self._model_locks[model_key]:
            model_type = self.MODEL_REGISTRY.get(model_key)
            if model_type is None:
                raise ValueError(f'Unknown model: {model_name}')
            if model_type == ModelType.HERMES:
                await self._ensure_hermes_model_downloaded()
            self._check_memory_pressure()
            from hledac.universal.brain.model_lifecycle import ensure_mlx_runtime_initialized
            ensure_mlx_runtime_initialized()
            if model_type in self._loaded_models:
                self._current_model = model_type
                logger.debug(f'Model {model_name} already loaded')
                return self._loaded_models[model_type]
            unload_task = await self._begin_model_unload(model_name, model_type)
            await self._prepare_hermes_quantization(model_type)
            if unload_task is not None:
                await unload_task
            self._check_memory_admission()
            rss_before_load = _check_rss_before_load(model_key)
            model = await self._instantiate_model(model_name, model_type)
            self._loaded_models[model_type] = model
            self._current_model = model_type
            logger.info(f'[MODEL LOAD] {model_name} done (RSS before={rss_before_load:.2f}GB)')
            record_model_success(model_key)
            await adjust_fetch_workers(3)
            return model

    async def _begin_model_unload(self, model_name: str, model_type: ModelType) -> Any | None:
        """Start background unload if a model is currently loaded. Returns task or None."""
        if self._current_model is None:
            return None
        logger.info(f'[PHASE SWITCH] Releasing {self._current_model.name} before loading {model_name}')
        unload_task = safe_create_task(self._release_current_async())
        mx = _get_mlx_safe()
        if MLX_AVAILABLE and mx is not None:
            try:
                await asyncio.to_thread(_sync_maybe_eval)
            except Exception:  # noqa: BLE001
                pass
        return unload_task

    async def _prepare_hermes_quantization(self, model_type: ModelType) -> None:
        """Select Hermes quantization if applicable. Failures are non-fatal."""
        if model_type != ModelType.HERMES:
            return
        try:
            from hledac.universal.core.resource_governor import sample_uma_status
            uma = sample_uma_status()
            selector = QuantizationSelector()
            budget = selector.select(uma, requested_model='hermes')
            if budget.max_tokens == 0 and budget.max_latency_ms == 0:
                raise RuntimeError(f'[F203J] QuantizationSelector denied Hermes load: {budget.reason}')
            from hledac.universal.brain.model_lifecycle import set_selected_quantization
            set_selected_quantization(budget.quantization)
            logger.info(f'[F203J] Hermes quantization selected: {budget.quantization} (tokens={budget.max_tokens}, latency={budget.max_latency_ms}ms, reason={budget.reason})')
        except Exception as e:
            logger.debug('[F203J] QuantizationSelector error (using defaults): %s', e)

    async def _instantiate_model(self, model_name: str, model_type: ModelType) -> Any:
        """Factory instantiation + initialize/load. Raises RuntimeError on failure."""
        logger.info(f'[MODEL LOAD] {model_name} start')
        factory = self._model_factories[model_type]
        model = factory()
        try:
            if hasattr(model, 'initialize'):
                if inspect.iscoroutinefunction(model.initialize):
                    await model.initialize()
                else:
                    await asyncio.to_thread(model.initialize)
            elif hasattr(model, 'load'):
                if inspect.iscoroutinefunction(model.load):
                    await model.load()
                else:
                    await asyncio.to_thread(model.load)
            return model
        except asyncio.CancelledError:
            logger.warning(f'Model load cancelled: {model_name}')
            raise
        except RuntimeError as e:
            model_key = model_name.lower()
            if 'MEMORY ADMISSION' in str(e) or 'memory_admission' in str(e).lower():
                record_model_failure(model_key, failure_kind='memory_admission_blocked')
            else:
                record_model_failure(model_key, failure_kind='load_error')
            logger.error(f'Failed to load model {model_name}: {e}')
            raise RuntimeError(f'Failed to load model {model_name}: {e}') from e
        except Exception as e:
            model_key = model_name.lower()
            record_model_failure(model_key, failure_kind='load_error')
            logger.error(f'Failed to load model {model_name}: {e}')
            raise RuntimeError(f'Failed to load model {model_name}: {e}') from e

    async def release_model(self, model_name: ModelName) -> None:
        """
        Async uvolnění modelu z paměti.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Raises:
            ValueError: Pokud je model_name neznámé
        """
        async with self._lock:
            model_type = self.MODEL_REGISTRY.get(model_name.lower())
            if model_type is None:
                raise ValueError(f'Unknown model: {model_name}')
            if model_type not in self._loaded_models:
                logger.debug(f'Model {model_name} not loaded')
                return
            await self._release_model_async(model_type, model_name)

    async def _release_model_async(self, model_type: ModelType, model_name: str) -> None:
        """Interní async implementace uvolnění modelu."""
        model = self._loaded_models.get(model_type)
        rss_before_unload = _get_current_rss_gb()
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]
        if self._current_model == model_type:
            self._current_model = None
        await self._unload_model_with_verification(model, model_type, model_name, rss_before_unload)

    async def _unload_model_with_verification(self, model: Any, model_type: ModelType, model_name: str, rss_before_unload: float) -> None:
        """Shared helper: unload model + verify RSS delta."""
        if model is not None and hasattr(model, 'unload'):
            logger.info(f'[MODEL RELEASE] {model_name} start')
            try:
                unload_coro = model.unload() if inspect.iscoroutinefunction(model.unload) else asyncio.to_thread(model.unload)
                timeout_s = _load_unload_timeout()
                try:
                    async with asyncio.timeout(timeout_s):
                        await unload_coro
                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    logger.warning('[P1E-B] Model unload timed out after %.1fs for %s — continuing shutdown', timeout_s, model_name)
                except Exception as e:
                    logger.error(f'Failed to release model {model_name}: {e}')
                else:
                    logger.info(f'[MODEL RELEASE] {model_name} done')
            finally:
                await self._cleanup_memory_async(model_type, engine=model)
        _verify_rss_after_unload(model_name.lower(), rss_before_unload)
        await adjust_fetch_workers(25)

    async def release_current(self) -> None:
        """Async uvolnění aktuálně načteného modelu."""
        async with self._lock:
            await self._release_current_async()

    async def _release_current_async(self) -> None:
        """Interní async implementace uvolnění aktuálního modelu."""
        if self._current_model is None:
            return
        model_type = self._current_model
        model_name = model_type.name.lower()
        rss_before_unload = _get_current_rss_gb()
        model = self._loaded_models.get(model_type)
        if model_type in self._loaded_models:
            del self._loaded_models[model_type]
        if self._current_model == model_type:
            self._current_model = None
        await self._unload_model_with_verification(model, model_type, model_name, rss_before_unload)
        await adjust_fetch_workers(25)

    async def _cleanup_memory_async(self, model_type: ModelType | None=None, engine: Any | None=None) -> None:
        """Agresivní async čištění paměti po uvolnění modelu.

        Args:
            model_type: ModelType being released. If None, uses self._current_model.
            engine: Pre-captured model/engine instance (F182B: required when registry already cleared).
        """
        target_model = model_type if model_type is not None else self._current_model
        if target_model and target_model.name == 'HERMES' and (engine is not None):
            try:
                if hasattr(engine, '_prompt_cache') and engine._prompt_cache:
                    context_len = self._estimate_context_length(engine._prompt_cache)
                    if context_len > 1024:
                        await engine._compress_kv_cache()
            except Exception:  # noqa: BLE001
                pass
        mx = _get_mlx_safe()
        if MLX_AVAILABLE and mx is not None:
            try:
                # U2-02 FIX: offload mx.eval([]) + clear_cache() to thread — direct
                # calls in async function block the event loop for 1-50ms.
                await asyncio.to_thread(_sync_eval_and_clear_cache)
            except Exception as e:
                logger.warning(f'Failed to clear MLX cache: {e}')
        gc.collect()

    # ── Model Accessors ───────────────────────────────────────────────────────

    def get_model(self, model_name: ModelName) -> Any | None:
        """
        Vrátí instanci načteného modelu.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            Instance modelu nebo None pokud není načten
        """
        model_type = self.MODEL_REGISTRY.get(model_name.lower())
        if model_type is None:
            logger.error(f'Unknown model: {model_name}')
            return None
        return self._loaded_models.get(model_type)

    def is_loaded(self, model_name: ModelName) -> bool:
        """
        Zkontroluje zda je model načten.

        Args:
            model_name: Jméno modelu ("hermes", "modernbert", "gliner")

        Returns:
            True pokud je model načten, False jinak
        """
        model_type = self.MODEL_REGISTRY.get(model_name.lower())
        if model_type is None:
            return False
        return model_type in self._loaded_models

    def get_current_model(self) -> str | None:
        """
        Vrátí jméno aktuálně načteného modelu.

        Returns:
            Jméno modelu nebo None
        """
        if self._current_model is None:
            return None
        return self._current_model.name.lower()

    async def _ensure_embedders(self) -> bool:
        """Lazily initialize ANE and MLX embedders. Returns True if ANE is available."""
        try:
            from ..embeddings.modernbert_embedder import ModernBERTEmbedder
            from .ane_embedder import ANEEmbedder
        except ImportError:
            return False
        if self._ane_embedder is None:
            self._ane_embedder = ANEEmbedder()
        if self._mlx_embedder is None:
            try:
                self._mlx_embedder = ModernBERTEmbedder()
            except Exception:
                self._mlx_embedder = None
        return self._ane_embedder is not None

    async def get_embedder(self, resource_allocator: Any = None) -> Any | None:
        """
        Vrátí funkci pro embeddování, která se rozhodne podle dostupnosti ANE a zátěže.

        Args:
            resource_allocator: Volitelný resource allocator pro rozhodování

        Returns:
            Funkce pro embeddování textů na embeddingy
        """
        if not await self._ensure_embedders():
            return None
        use_ane = False
        if resource_allocator:
            try:
                use_ane = await resource_allocator.can_use_ane()
            except Exception:
                use_ane = False
        if use_ane:
            if not self._ane_embedder.is_loaded:
                await self._ane_embedder.load()
            if self._ane_embedder.is_loaded:
                if self._mlx_embedder:
                    self._ane_embedder.set_fallback(self._mlx_embedder.embed)
                return self._ane_embedder.embed
        if self._mlx_embedder:
            return self._mlx_embedder.embed
        return None

    async def release_all(self) -> None:
        """Async uvolnění všech modelů z paměti."""
        logger.info('Releasing all models...')
        async with self._lock:
            last_released: ModelType | None = None
            last_engine: Any | None = None
            for model_type in list(self._loaded_models.keys()):
                model_name = model_type.name.lower()
                last_released = model_type
                try:
                    model = self._loaded_models[model_type]
                    last_engine = model
                    if hasattr(model, 'unload'):
                        logger.info(f'[MODEL RELEASE] {model_name} start')
                        if inspect.iscoroutinefunction(model.unload):
                            await model.unload()
                        else:
                            asyncio.get_running_loop()
                            await asyncio.to_thread(model.unload)
                        logger.info(f'[MODEL RELEASE] {model_name} done')
                    del self._loaded_models[model_type]
                    logger.info(f'✓ Released {model_name}')
                except Exception as e:
                    logger.error(f'Failed to release {model_name}: {e}')
            self._current_model = None
            await self._cleanup_memory_async(last_released, engine=last_engine)
            logger.info('✓ All models released')

    async def generate_report(
        self,
        graph_summary: str,
        hypotheses: list[str],
        findings: list[Any] | None = None,
        output_path: str | None = None,
    ) -> str:
        """
        P12: Generate final OSINT report from graph summary and hypotheses.

        Uses Hermes 3 to synthesize the research findings into a structured
        Markdown report. Results are saved to a file.

        Args:
            graph_summary: Graph data as summary string
            hypotheses: List of hypotheses that were investigated
            findings: Optional list of finding dicts/objects
            output_path: Optional path for Markdown output (default: ~/hledac_report.md)

        Returns:
            Generated report as Markdown string
        """
        import os

        if output_path is None:
            output_path = os.path.expanduser('~/hledac_report.md')
        max_context = 4000
        max_hypotheses = 10
        max_findings = 20
        # ISSUE [LLM-SEC-001]: Sanitize all user-controlled inputs before LLM consumption
        _sanitize = sanitize_for_llm()
        hypo_lines = []
        for i, h in enumerate(hypotheses[:max_hypotheses], 1):
            sanitized_h = _sanitize(str(h)[:200]) if _sanitize else str(h)[:200]
            hypo_lines.append(f'{i}. {sanitized_h}')
        hypo_text = '\n'.join(hypo_lines)
        finding_lines = []
        for f in (findings or [])[:max_findings]:
            sanitized_f = _sanitize(str(f)[:300]) if _sanitize else str(f)[:300]
            finding_lines.append(f'- {sanitized_f}')
        finding_text = '\n'.join(finding_lines)
        # Sanitize graph_summary (may contain raw web content)
        sanitized_graph = _sanitize(graph_summary[:max_context]) if _sanitize else graph_summary[:max_context]
        prompt = (
            f"Vytvoř strukturovaný OSINT report v Markdown formátu.\n\n"
            f"Grafový souhrn:\n{sanitized_graph}\n\n"
            f"Hypotézy:\n{hypo_text}\n\n"
            f"Zjištění (findings):\n{finding_text if finding_text else 'Žádná zjištění'}\n\n"
            f"Report musí obsahovat:\n"
            f"1. # OSINT Report\n"
            f"2. ## Shrnutí (Executive Summary) - max 3 věty\n"
            f"3. ## Klíčová zjištění (Key Findings)\n"
            f"4. ## Hypotézy a výsledky\n"
            f"5. ## Doporučení (Recommendations)\n"
            f"6. ## Metadata (timestamp, version)\n\n"
            f"Piš v češtině, buď konkrétní a stručný."
        )

        async with self.acquire_model_ctx('hermes') as engine:
            try:
                report = await engine.generate(
                    prompt=prompt,
                    temperature=0.3,
                    max_tokens=2048,
                    system_msg='Jsi OSINT research assistant. Vytvářej strukturované reporty v češtině.',
                )
            except Exception as e:
                logger.warning(f'[GENERATE_REPORT] Generation failed: {e}')
                report = f'# Report Generation Failed\n\nError: {e}'

        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        final_report = f'---\nGenerated: {timestamp}\nHledac OSINT Report\n---\n\n{report}'
        try:
            import aiofiles as _f273_af

            async with _f273_af.open(output_path, 'w', encoding='utf-8') as f:
                await f.write(final_report)
            logger.info(f'[GENERATE_REPORT] Saved to {output_path}')
        except Exception as e:
            logger.warning(f'[GENERATE_REPORT] Failed to save: {e}')
        return final_report

    async def with_phase(self, phase_name: str) -> Any:  # type: ignore[return-value]
        """
        Context manager pro fázové workflow.

        Automaticky vybere správný model podle fáze:
        - PLAN/DECIDE/GENERATE → Hermes
        - EMBED/DEDUP/ROUTING → ModernBERT
        - NER/ENTITY → GLiNER

        Usage:
            async with manager.with_phase("PLAN") as model:
                result = await model.generate(...)

        Args:
            phase_name: Název fáze (např. "PLAN", "EMBED", "NER")

        Returns:
            Async context manager yielding model instance
        """
        model_name = self.PHASE_MODEL_MAP.get(phase_name.upper())
        if model_name is None:
            raise ValueError(f'Unknown phase: {phase_name}')
        logger.info(f'[PHASE START] {phase_name} -> using {model_name}')

        @asynccontextmanager
        async def _phase_context() -> Any:  # type: ignore[return-value]
            async with model_lifecycle(model_name) as model:
                yield model
            logger.info(f'[PHASE END] {phase_name}')

        return _phase_context()

    async def __aenter__(self) -> ModelManager:
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit - uvolní všechny modely."""
        await self.release_all()

    # ── Embedding Lifecycle ───────────────────────────────────────────────────

    def load_embedding_model(self) -> bool:
        """
        Initialize the ModernBERTEmbedding singleton for embedding pipeline.

        Uses the singleton embedder from embedding_pipeline module.
        Returns True if embedder is ready, False on error.
        """
        try:
            from hledac.universal.embedding_pipeline import _get_embedder

            embedder = _get_embedder()
            if embedder is not None:
                logger.info('[EMBED] Embedding model loaded via ModelManager')
                return True
            return False
        except Exception as e:
            logger.error(f'[EMBED] Failed to load embedding model: {e}')
            return False

    def unload_embedding_model(self) -> None:
        """
        Unload the ModernBERTEmbedding singleton from memory.

        Called after batch embedding operations to free GPU/RAM.
        """
        try:
            from hledac.universal.embedding_pipeline import _release_embedder

            _release_embedder()
            logger.info('[EMBED] Embedding model unloaded via ModelManager')
        except Exception as e:
            logger.debug(f'[EMBED] Failed to unload embedding model: {e}')

    @asynccontextmanager
    async def embedding_lifecycle(self) -> Any:  # type: ignore[return-value]
        """
        Context manager for embedding model lifecycle.

        On entry: loads the embedding model.
        On exit: releases the embedding model and clears MLX cache.

        Usage:
            async with manager.embedding_lifecycle():
                embeddings = await generate_embeddings_async(texts)

        This ensures proper memory management on M1 8GB.
        """
        self.load_embedding_model()
        try:
            yield
        finally:
            self.unload_embedding_model()
            # U2-02 FIX: offload eval+clear to thread — in async context (finally block
            # of async contextmanager), direct mx.eval([]) blocks the event loop.
            try:
                await asyncio.to_thread(_sync_eval_and_clear_cache)
            except Exception:  # noqa: BLE001
                pass


def _sync_maybe_eval() -> None:
    """U2-02 FIX: sync throttled mx.eval([]) for asyncio.to_thread offload."""
    import time as _time
    _mx = _get_mlx_safe()
    if not MLX_AVAILABLE or _mx is None:
        return
    _min_interval = 0.05  # 50ms throttle — matches _MIN_EVAL_INTERVAL in mlx_memory
    try:
        _now = _time.monotonic()
        if not hasattr(_sync_maybe_eval, '_last_eval'):
            _sync_maybe_eval._last_eval = 0.0
        if _now - _sync_maybe_eval._last_eval > _min_interval:
            _mx.eval([])
            _sync_maybe_eval._last_eval = _now
    except Exception:  # noqa: BLE001
        pass


def _sync_eval_and_clear_cache() -> None:
    """U2-02 FIX: sync eval+clear for asyncio.to_thread offload (no gc.collect)."""
    _mx = _get_mlx_safe()
    if MLX_AVAILABLE and _mx is not None:
        try:
            _mx.eval([])
            if hasattr(_mx, 'clear_cache'):
                _mx.clear_cache()
        except Exception:  # noqa: BLE001
            pass


_model_manager: ModelManager | None = None

def get_model_manager() -> ModelManager:
    """Vrátí globální instanci ModelManager."""
    global _model_manager
    if _model_manager is None:
        _model_manager = ModelManager()
    return _model_manager

async def reset_model_manager() -> None:
    """Resetuje globální instanci ModelManager."""
    global _model_manager
    if _model_manager is not None:
        await _model_manager.release_all()
        _model_manager = None

class _SyncCompatibilityWrapper:
    """
    Wrapper pro zpětnou kompatibilitu se sync API.

    DEPRECATED: Používejte async metody přímo!
    """
    __slots__ = ('_manager',)

    def __init__(self, manager: ModelManager) -> None:
        self._manager = manager

    def acquire(self, model_name: str) -> bool:
        """DEPRECATED: Použijte await load_model()"""
        logger.warning('DEPRECATED: acquire() is deprecated, use await load_model()')
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError("Cannot use sync acquire() in async context. Use: model = await manager.load_model('hermes')")
            else:
                loop.run_until_complete(self._manager.load_model(model_name))
            return True
        except Exception as e:
            logger.error(f'Failed to acquire model: {e}')
            return False

    def release(self, model_name: str) -> bool:
        """DEPRECATED: Použijte await release_model()"""
        logger.warning('DEPRECATED: release() is deprecated, use await release_model()')
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError("Cannot use sync release() in async context. Use: await manager.release_model('hermes')")
            else:
                loop.run_until_complete(self._manager.release_model(model_name))
            return True
        except Exception as e:
            logger.error(f'Failed to release model: {e}')
            return False

def get_sync_wrapper() -> _SyncCompatibilityWrapper:
    """Vrátí sync wrapper pro zpětnou kompatibilitu. DEPRECATED!"""
    return _SyncCompatibilityWrapper(get_model_manager())
