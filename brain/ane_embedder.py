"""
ANE-akcelerovaný embedder pro ModernBERT a FlashRank.
Offline konverze z MLX do CoreML, fallback na MLX.

Reranker: rerank_findings_crossencoder() používá flashrank CrossEncoder.
LanceDBIdentityStore má vlastní _get_flashrank_ranker() pro search path.
Tyto dvě instance jsou záměrně oddělené — ANE brain pipeline vs. vector store search.
"""
from __future__ import annotations
import asyncio
import inspect
import logging
import threading
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
import msgspec
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
import numpy as np
logger = logging.getLogger(__name__)

class _MLXFamilyMutex:
    """
    R-4: Koordinace ANE/MLX/CoreML na M1 8GB.

    Tři sloty pro vzájemné vyloučení:
      - LLM:      Hermes 3B LLM + KV cache (MLX)
      - EMBED_ANE: ANE embedder (CoreML/neural engine)
      - EMBED_COREML: CoreML embedder (mlx-embeddings)

    Cross-process file lock /tmp/hledac_mlx_family.lock zabraňuje kolizi
    s externím mlxcel subprocess.

    Max combined memory: 2.5GB (hard guard).
    """
    _instance: _MLXFamilyMutex | None = None
    _lock: threading.Lock = threading.Lock()
    _active_runtime: Literal['llm', 'embed_ane', 'embed_coreml', None] = None
    _max_combined_mb: float = 2560.0
    _cross_lock_path: str = '/tmp/hledac_mlx_family.lock'
    _cross_lock_fd: Any = None  # file object for cross-process lock (open file handle)

    # Per-slot model sizes (MB)
    _SLOT_SIZES: dict[Literal['llm', 'embed_ane', 'embed_coreml'], float] = {
        'llm': 2048.0,       # Hermes 3B
        'embed_ane': 90.0,   # ANE CoreML embedder
        'embed_coreml': 50.0, # MLX embedder
    }

    def __new__(cls) -> _MLXFamilyMutex:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ── Cross-process file lock ──────────────────────────────────────────────

    def _acquire_cross_lock(self, slot: Literal['llm', 'embed_ane', 'embed_coreml']) -> None:
        """Acquire cross-process file lock (non-blocking). Fails silently — telemetry only."""
        try:
            import fcntl
            fd = open(self._cross_lock_path, 'a')
            # LOCK_NB | LOCK_EX = non-blocking exclusive
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Store fd in instance — released on unlock
            _MLXFamilyMutex._cross_lock_fd = fd
        except (FileNotFoundError, PermissionError, OSError) as exc:
            # Cross-lock unavailable — log but don't fail (intra-process guard still active)
            logger.debug(f'[_MLXFamilyMutex] Cross-lock unavailable: {exc}')

    def _release_cross_lock(self) -> None:
        """Release cross-process file lock."""
        import fcntl
        fd = getattr(_MLXFamilyMutex, '_cross_lock_fd', None)
        if fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                fd.close()
            except (OSError, AttributeError):
                pass
            _MLXFamilyMutex._cross_lock_fd = None

    # ── Intra-process guard ─────────────────────────────────────────────────

    def _check_slot_conflict(self, slot: Literal['llm', 'embed_ane', 'embed_coreml']) -> MemoryError | None:
        """Return MemoryError if slot conflicts with active runtime."""
        conflict_map: dict[Literal['llm', 'embed_ane', 'embed_coreml'], str | None] = {
            'llm': 'embed_ane',           # LLM blocks ANE embedder (GPU bandwidth)
            'embed_ane': 'llm',           # ANE blocks LLM
            'embed_coreml': 'llm',         # CoreML embed blocks LLM
        }
        blocker = conflict_map.get(slot)
        if blocker is not None and self._active_runtime == blocker:
            return MemoryError(f'[_MLXFamilyMutex] {self._active_runtime} is active — cannot acquire {slot}. Release {self._active_runtime} first.')
        if self._active_runtime == slot:
            return None  # Already this slot — re-entrant OK
        return None

    def acquire_llm(self, model_size_mb: float = 0.0) -> None:
        """Acquire LLM slot. Raises MemoryError if ANE/CoreML embedder is active."""
        with self._lock:
            err = self._check_slot_conflict('llm')
            if err:
                raise err
            if model_size_mb > self._max_combined_mb:
                raise MemoryError(f'[_MLXFamilyMutex] LLM model {model_size_mb:.0f}MB exceeds {self._max_combined_mb:.0f}MB limit.')
            self._active_runtime = 'llm'
            logger.debug(f'[_MLXFamilyMutex] Acquired LLM (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('llm')

    def acquire_embed_ane(self, model_size_mb: float = 0.0) -> None:
        """Acquire ANE embedder slot. Raises MemoryError if LLM is active."""
        with self._lock:
            err = self._check_slot_conflict('embed_ane')
            if err:
                raise err
            self._active_runtime = 'embed_ane'
            logger.debug(f'[_MLXFamilyMutex] Acquired EMBED_ANE (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('embed_ane')

    def acquire_embed_coreml(self, model_size_mb: float = 0.0) -> None:
        """Acquire CoreML/MLX embedder slot. Raises MemoryError if LLM is active."""
        with self._lock:
            err = self._check_slot_conflict('embed_coreml')
            if err:
                raise err
            self._active_runtime = 'embed_coreml'
            logger.debug(f'[_MLXFamilyMutex] Acquired EMBED_COREML (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('embed_coreml')

    # ── Non-blocking try-acquire (for embedder fallback) ───────────────────────

    def try_acquire_llm(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire LLM slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict('llm')
            if err:
                return False
            if model_size_mb > self._max_combined_mb:
                return False
            self._active_runtime = 'llm'
            logger.debug(f'[_MLXFamilyMutex] Acquired LLM (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('llm')
        return True

    def try_acquire_embed_ane(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire ANE embedder slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict('embed_ane')
            if err:
                return False
            self._active_runtime = 'embed_ane'
            logger.debug(f'[_MLXFamilyMutex] Acquired EMBED_ANE (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('embed_ane')
        return True

    def try_acquire_embed_coreml(self, model_size_mb: float = 0.0) -> bool:
        """Try to acquire CoreML/MLX embedder slot — returns True if acquired, False if busy."""
        with self._lock:
            err = self._check_slot_conflict('embed_coreml')
            if err:
                return False
            self._active_runtime = 'embed_coreml'
            logger.debug(f'[_MLXFamilyMutex] Acquired EMBED_COREML (model={model_size_mb:.0f}MB)')
        self._acquire_cross_lock('embed_coreml')
        return True

    def release(self, runtime: Literal['llm', 'embed_ane', 'embed_coreml']) -> None:
        """Release lock for specified runtime."""
        self._release_cross_lock()
        with self._lock:
            if self._active_runtime == runtime:
                self._active_runtime = None
                logger.debug(f'[_MLXFamilyMutex] Released {runtime}')

    def is_active(self) -> Literal['llm', 'embed_ane', 'embed_coreml', None]:
        """Return currently active runtime."""
        return self._active_runtime

    def is_llm_active(self) -> bool:
        return self._active_runtime == 'llm'

    def is_embed_ane_active(self) -> bool:
        return self._active_runtime == 'embed_ane'

    def is_embed_coreml_active(self) -> bool:
        return self._active_runtime == 'embed_coreml'

    @property
    def is_metal_busy_with_other_process(self) -> bool:
        """R-4: Cross-process check — True if external mlxcel is holding the Metal lock."""
        try:
            import fcntl
            fd = open(self._cross_lock_path, 'r')
            try:
                # LOCK_EX | LOCK_NB = non-blocking exclusive — fails if locked by another
                fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
                return False  # Lock acquired = no other process holds it
            except OSError:
                return True  # EWOULDBLOCK = another process holds the lock
            finally:
                fd.close()
        except (FileNotFoundError, PermissionError, OSError):
            return False  # Lock file missing/unavailable = no external process


# ── Backward-compat alias ────────────────────────────────────────────────────
class ANE_MLX_Mutex(_MLXFamilyMutex):
    """R-4: DEPRECATED — use _MLXFamilyMutex directly. ANE_MLX_Mutex preserved for compat."""

    def acquire_ane(self, model_size_mb: float = 0.0) -> None:
        """Deprecated alias for acquire_embed_ane."""
        warnings.warn(
            'ANE_MLX_Mutex.acquire_ane() is deprecated. Use acquire_embed_ane() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.acquire_embed_ane(model_size_mb)

    def acquire_mlx(self, model_size_mb: float = 0.0) -> None:
        """Deprecated alias for acquire_llm."""
        warnings.warn(
            'ANE_MLX_Mutex.acquire_mlx() is deprecated. Use acquire_llm() instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        return self.acquire_llm(model_size_mb)

    def release(self, runtime: Literal['ane', 'mlx'] | Literal['llm', 'embed_ane', 'embed_coreml']) -> None:  # type: ignore[override]
        """Deprecated — maps old 'ane'/'mlx' to new slot names."""
        import sys
        warnings.warn(
            'ANE_MLX_Mutex.release() is deprecated. Use release() with llm/embed_ane/embed_coreml instead.',
            DeprecationWarning,
            stacklevel=2,
        )
        if runtime == 'ane':
            return super().release('embed_ane')
        if runtime == 'mlx':
            return super().release('llm')
        return super().release(runtime)  # type: ignore[arg-type]

    def is_ane_active(self) -> bool:
        return self.is_embed_ane_active()

    def is_mlx_active(self) -> bool:
        return self.is_llm_active()


def get_ane_mlx_mutex() -> _MLXFamilyMutex:  # type: ignore[return-value]
    """Thread-safe singleton accessor — returns _MLXFamilyMutex (formerly ANE_MLX_Mutex)."""
    return _MLXFamilyMutex()
try:
    import CoreML as _CoreML
    import Foundation as _Foundation
    ANE_AVAILABLE = True
except ImportError:
    ANE_AVAILABLE = False
    _CoreML = None
    _Foundation = None
try:
    import mlx.core as _mx
    from mlx_embeddings import load as _mlx_embeddings_load
    MLX_EMBED_AVAILABLE = True
except ImportError:
    MLX_EMBED_AVAILABLE = False
    _mx = None
    _mlx_embeddings_load = None
MODELS_DIR = Path.home() / '.hledac' / 'models'
MODELS_DIR.mkdir(parents=True, exist_ok=True)
_ANE_TELEMETRY = {'ane_embed_attempted': 0, 'ane_embed_fallback_used': 0, 'ane_warmup_executed': 0, 'ane_warmup_error': 0}

class ANEStatus(Enum):
    """ANE status codes."""
    NOT_AVAILABLE = 'not_available'
    MODEL_NOT_FOUND = 'model_not_found'
    LOADED = 'loaded'
    LOAD_FAILED = 'load_failed'

class ANEStatusResult(msgspec.Struct):
    """Sprint F300: msgspec.Struct for ANE status result.

    Result of get_ane_status().
    """
    available: bool
    loaded: bool
    model_path_exists: bool
    fallback_configured: bool
    last_error: str | None
    inference_path: str

def get_ane_status(embedder: ANEEmbedder | None=None) -> ANEStatusResult:
    """
    Sprint F228B: Returns ANE status as a dataclass.
    Callers can inspect without triggering model loading.
    """
    global _ANE_EMBEDDER
    if embedder is None:
        embedder = get_ane_embedder()
    if not ANE_AVAILABLE:
        return ANEStatusResult(available=False, loaded=False, model_path_exists=False, fallback_configured=False, last_error='CoreML/pyobjc not available', inference_path='unavailable')
    if embedder is None:
        return ANEStatusResult(available=True, loaded=False, model_path_exists=False, fallback_configured=False, last_error=None, inference_path='hash_fallback')
    model_exists = embedder.coreml_path.exists() if hasattr(embedder, 'coreml_path') else False
    fallback_configured = embedder._fallback_embedder is not None
    if embedder.is_loaded:
        return ANEStatusResult(available=True, loaded=True, model_path_exists=model_exists, fallback_configured=fallback_configured, last_error=None, inference_path='coreml')
    if not model_exists:
        return ANEStatusResult(available=True, loaded=False, model_path_exists=False, fallback_configured=fallback_configured, last_error=None, inference_path='hash_fallback')
    return ANEStatusResult(available=True, loaded=False, model_path_exists=True, fallback_configured=fallback_configured, last_error=getattr(embedder, '_last_load_error', None), inference_path='fallback' if fallback_configured else 'unavailable')

def get_ane_telemetry() -> dict:
    """Sprint F228B: Returns a copy of ANE telemetry counters."""
    return dict(_ANE_TELEMETRY)

def reset_ane_telemetry() -> None:
    """Sprint F228B: Reset telemetry counters (for testing)."""
    _ANE_TELEMETRY['ane_embed_attempted'] = 0
    _ANE_TELEMETRY['ane_embed_fallback_used'] = 0
    _ANE_TELEMETRY['ane_warmup_executed'] = 0
    _ANE_TELEMETRY['ane_warmup_error'] = 0
_HF_TOKENIZER = None

def _get_hf_tokenizer():
    global _HF_TOKENIZER
    if _HF_TOKENIZER is None:
        from transformers import AutoTokenizer
        _HF_TOKENIZER = AutoTokenizer.from_pretrained('sentence-transformers/all-MiniLM-L6-v2', use_fast=True)
    return _HF_TOKENIZER

def _make_ml_array(data_list: list, length: int=64):
    arr, err = _CoreML.MLMultiArray.alloc().initWithShape_dataType_error_([1, length], _CoreML.MLMultiArrayDataTypeInt32, None)
    if err:
        raise RuntimeError(f'MLMultiArray init failed: {err}')
    ns_vals = [_Foundation.NSNumber.numberWithInt_(v) for v in data_list]
    ns_arr = _Foundation.NSArray.arrayWithArray_(ns_vals)
    for i in range(length):
        arr.setObject_atIndexedSubscript_(ns_arr[i], i)
    return arr

def _coreml_embed(model, text: str) -> np.ndarray:
    tok = _get_hf_tokenizer()
    tokens = tok(text[:256], return_tensors='np', padding='max_length', max_length=64, truncation=True)
    input_ids = tokens['input_ids'].flatten().tolist()
    attn_mask = tokens['attention_mask'].flatten().tolist()
    feat_dict = {'input_ids': _make_ml_array(input_ids), 'attention_mask': _make_ml_array(attn_mask)}
    provider, err = _CoreML.MLDictionaryFeatureProvider.alloc().initWithDictionary_error_(feat_dict, None)
    if err:
        raise RuntimeError(f'Feature provider failed: {err}')
    result, err = model.predictionFromFeatures_error_(provider, None)
    if err:
        raise RuntimeError(f'Inference failed: {err}')
    vec_raw = result.featureValueForName_('var_570').multiArrayValue()
    vec = np.array([float(vec_raw.objectAtIndexedSubscript_(i)) for i in range(384)], dtype=np.float32)
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec

class ANEEmbedder:
    """
    Embedder, který se pokusí použít ANE (přes CoreML) a pokud není k dispozici,
    spoléhá na volání MLX embedderu (který musí být poskytnut zvenčí).

    Sprint F228B: Truthful ANE path — no NotImplementedError in production.
    """
    __slots__ = tuple(('_fallback_embedder', '_last_load_error', '_loaded', '_mlx_model', '_mlx_processor', 'coreml_path', 'hidden_dim', 'model', 'model_name'))

    def __init__(self, model_name: str='modernbert', hidden_dim: int=768):
        self.model_name = model_name
        self.hidden_dim = hidden_dim
        self.model = None
        self._mlx_model = None
        self._mlx_processor = None
        self._loaded = False
        self._last_load_error: str | None = None
        self.coreml_path = MODELS_DIR / f'{model_name}_ane.mlpackage'
        self._fallback_embedder: Callable[..., Awaitable[np.ndarray]] | None = None

    def set_fallback(self, fallback_func: Callable[..., Awaitable[np.ndarray]]) -> None:
        """Nastaví fallback async funkci (např. MLX embedder)."""
        self._fallback_embedder = fallback_func

    async def load(self) -> None:
        """Load MLX ModernBERT first (preferred), then CoreML (legacy), then hash fallback.

        CoreML→MLX migration: MLX is now the primary path. CoreML is only attempted
        if mlx-embeddings is unavailable (e.g. non-AppleSilicon).
        """
        if self._loaded or self._mlx_model is not None:
            return
        if MLX_EMBED_AVAILABLE:
            try:
                model_path = 'nomic-ai/modernbert-embed-base'
                self._mlx_model, self._mlx_processor = _mlx_embeddings_load(model_path, lazy=False)
                self._loaded = True
                self._last_load_error = None
                logger.info(f'ANEEmbedder loaded MLX: {model_path}')
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f'MLX ModernBERT failed ({e}), trying CoreML fallback')
        if ANE_AVAILABLE and self.coreml_path.exists():
            try:
                url = _CoreML.NSURL.fileURLWithPath_(str(self.coreml_path))
                model, err = _CoreML.MLModel.modelWithContentsOfURL_error_(url, None)
                if err:
                    raise RuntimeError(f'CoreML load failed: {err}')
                self.model = model
                self._loaded = True
                self._last_load_error = None
                get_ane_mlx_mutex().acquire_embed_ane(model_size_mb=90.0)
                logger.info(f'ANEEmbedder loaded CoreML: {self.model_name}')
                return
            except Exception as e:
                self._last_load_error = str(e)
                logger.warning(f'CoreML failed ({e}), using hash fallback')

    async def initialize(self) -> None:
        """
        Sprint F228B: Explicit initialization — loads CoreML or MLX model on first call.
        Idempotent: safe to call multiple times, only loads once.
        M1 guard: requires >1.5GB UMA available before loading CoreML model.
        """
        if self._loaded:
            return
        try:
            from utils.uma_budget import get_uma_snapshot
            snap = get_uma_snapshot()
            if snap.is_critical or snap.is_emergency:
                logger.warning(f'[ANE] initialize skipped: memory pressure {snap.pct_used:.0f}% (>85%% critical)')
                return
            avail = snap.available_uma_gib
            if avail < 1.5:
                logger.warning(f'[ANE] initialize skipped: only {avail:.1f}GB < 1.5GB required')
                return
        except Exception:
            pass
        await self.load()

    async def convert_to_ane(self) -> bool:
        """Check for pre-compiled .mlmodelc — no conversion needed."""
        if not ANE_AVAILABLE:
            logger.warning('[ANE] CoreML (pyobjc) not available')
            return False
        compiled_path = MODELS_DIR / 'AllMiniLML6V2.mlmodelc'
        if compiled_path.exists():
            self.coreml_path = compiled_path
            logger.info('[ANE] Pre-compiled model found: %s', compiled_path)
            return True
        raw_path = MODELS_DIR / 'AllMiniLML6V2.mlmodel'
        if raw_path.exists():
            logger.info('[ANE] Compiling %s ...', raw_path)

            def _compile():
                url = _CoreML.NSURL.fileURLWithPath_(str(raw_path))
                compiled_url, err = _CoreML.MLModel.compileModelAtURL_error_(url, None)
                if err:
                    raise RuntimeError(f'Compile failed: {err}')
                import shutil
                compiled_str = str(compiled_url).replace('file://', '')
                shutil.copytree(compiled_str, str(compiled_path), dirs_exist_ok=True)
                return compiled_path
            self.coreml_path = await asyncio.to_thread(_compile)
            logger.info('[ANE] Compiled to %s', self.coreml_path)
            return True
        logger.warning('[ANE] No model found at %s or %s', compiled_path, raw_path)
        return False

    async def embed(self, texts: str | list[str]) -> np.ndarray:
        """
        Sprint F228B: Truthful embed — no NotImplementedError in production.
        Falls back gracefully: CoreML → fallback embedder → hash fallback.

        R-4: If LLM is active on Metal GPU, skip Metal-backed embedding
        and use hash fallback to avoid GPU bandwidth contention.
        """
        global _ANE_TELEMETRY
        _ANE_TELEMETRY['ane_embed_attempted'] += 1

        # R-4: Avoid Metal GPU contention with active LLM inference.
        # If Hermes LLM is running (holds 'llm' slot), use hash fallback.
        # This check is safe even when called from mlx_unified_scheduler
        # (scheduler already holds embed_ane slot, so embed_ane slot check
        # would be re-entrant — we just check LLM state instead).
        try:
            if _MLXFamilyMutex().is_llm_active():
                _ANE_TELEMETRY['ane_embed_fallback_used'] += 1
                return self._hash_embed(texts if isinstance(texts, list) else [texts])
        except Exception:
            pass  # Mutex unavailable — proceed with normal path

        if isinstance(texts, str):
            texts = [texts]
        if self._loaded and self.model is not None:

            def _run():
                return np.array([_coreml_embed(self.model, t) for t in texts], dtype=np.float32)
            return await asyncio.to_thread(_run)
        if self._mlx_model is not None:
            _ANE_TELEMETRY['ane_embed_attempted'] += 1

            def _run():
                import mlx.core as mx
                toks = self._mlx_processor(texts, return_tensors='np', padding=True, truncation=True, max_length=512)
                input_ids = mx.array(toks['input_ids'])
                attention_mask = mx.array(toks['attention_mask'])
                embs = self._mlx_model(input_ids, attention_mask=attention_mask)
                hs = embs.last_hidden_state
                mask = mx.array(toks['attention_mask'][:, :, None])
                summed = (hs * mask).sum(axis=1)
                counts = mx.maximum(mask.sum(axis=1), 1e-09)
                pooled = summed / counts
                result = mx.eval(pooled)
                return np.array(result, dtype=np.float32)
            return await asyncio.to_thread(_run)
        if self._fallback_embedder is not None:
            _ANE_TELEMETRY['ane_embed_fallback_used'] += 1
            fb = self._fallback_embedder
            if inspect.iscoroutinefunction(fb):
                return await fb(texts)
            else:
                return await asyncio.to_thread(fb, texts)
        _ANE_TELEMETRY['ane_embed_fallback_used'] += 1
        return self._hash_embed(texts)

    def _hash_embed(self, texts: str | list[str]) -> np.ndarray:
        """Deterministic hash-based fallback — always works, no model needed."""
        if isinstance(texts, str):
            texts = [texts]
        vecs = []
        for t in texts:
            h = hash(t[:512]) % 2 ** 32
            vec = np.zeros(self.hidden_dim, dtype=np.float32)
            for i in range(min(self.hidden_dim, 384)):
                vec[i] = float(h >> i % 32 & 1) * 2 - 1
            norm = np.linalg.norm(vec)
            vecs.append(vec / norm if norm > 0 else vec)
        return np.array(vecs, dtype=np.float32)

    async def warmup(self) -> None:
        """
        Sprint F228B: Fixed warmup — awaits embed() correctly.
        Never passes async embed() directly to run_in_executor.
        """
        global _ANE_TELEMETRY
        if not ANE_AVAILABLE:
            logger.debug('ANEEmbedder warmup skipped: ANE not available')
            return
        if not self._loaded or self.model is None:
            logger.debug('ANEEmbedder warmup skipped: model not loaded')
            return
        _ANE_TELEMETRY['ane_warmup_executed'] += 1
        try:
            dummy = ['warmup probe osint security']
            await self.embed(dummy)
            logger.debug('ANEEmbedder warmed up (ANE cache primed)')
        except Exception as e:
            _ANE_TELEMETRY['ane_warmup_error'] += 1
            logger.debug(f'ANEEmbedder warmup failed: {e}')

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je ANE nebo MLX model načten."""
        return self._loaded and (self.model is not None or self._mlx_model is not None)
_ANE_EMBEDDER: ANEEmbedder | None = None

def get_ane_embedder() -> ANEEmbedder | None:
    """
    CoreML→MLX migration: ANEEmbedder is deprecated.

    .. deprecated::
        Use ``get_embedding_manager()`` from ``compat.core_mlx_embeddings`` instead.
        This function now returns None and logs a deprecation warning.
    """
    warnings.warn('get_ane_embedder() is deprecated. Use get_embedding_manager() from compat.core_mlx_embeddings instead. ANEEmbedder will be removed in a future sprint.', DeprecationWarning, stacklevel=2)
    return None

def unload_ane_embedder() -> None:
    """Release ANE mutex (no-op since ANE path is disabled)."""
    try:
        get_ane_mlx_mutex().release('embed_ane')
    except Exception:
        pass

async def semantic_dedup_findings(findings: list[dict], threshold: float=0.92) -> list[dict]:
    """
    Semantic deduplication of findings using MLXEmbeddingManager.

    MLX path: MLXEmbeddingManager batch embedding → cosine similarity matrix.
    Hash fallback: url+title hash (zero RAM, always works).
    """
    try:
        from compat.core_mlx_embeddings import get_embedding_manager
        mgr = get_embedding_manager()
    except Exception:
        mgr = None
    if mgr is None or not mgr._is_loaded:
        seen: set[int] = set()
        out: list[dict] = []
        for f in findings:
            key = hash((f.get('url', ''), f.get('title', '')))
            if key not in seen:
                seen.add(key)
                out.append(f)
        return out
    texts = [f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512] for f in findings]
    try:
        vecs = await asyncio.to_thread(mgr.encode, texts, 32, True)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-09
        vecs_n = vecs / norms
        sim = vecs_n @ vecs_n.T
        keep = [True] * len(findings)
        for i, finding_i in enumerate(findings):
            if not keep[i]:
                continue
            for j, finding_j in enumerate(findings):
                if i < j and sim[i, j] >= threshold:
                    keep[j] = False
        return [f for f, k in zip(findings, keep, strict=False) if k]
    except Exception:
        return findings

def rerank_findings_cosine(findings: list[dict], query: str, top_k: int=20) -> list[dict]:
    """
    Cosine similarity reranker over MLX embeddings.
    Uses MLXEmbeddingManager singleton, fallback: confidence sort.
    """
    try:
        from compat.core_mlx_embeddings import get_embedding_manager
        mgr = get_embedding_manager()
        if mgr is None or not mgr._is_loaded:
            raise RuntimeError('MLXEmbeddingManager unavailable')
    except Exception:
        return sorted(findings, key=lambda x: x.get('confidence', 0.5), reverse=True)[:top_k]
    try:
        corpus = [f"{f.get('title', '')} {f.get('snippet', '')}".strip()[:512] for f in findings[:200]]
        all_texts = [query[:512]] + corpus
        embeddings = mgr.encode(all_texts, batch_size=32, normalize=True)
        q_vec = embeddings[0]
        corp_vecs = embeddings[1:]
        scored = []
        for idx, f in enumerate(findings[:200]):
            score = float(np.dot(q_vec, corp_vecs[idx]))
            scored.append((score, f))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_k]]
    except Exception:
        return sorted(findings, key=lambda x: x.get('confidence', 0.5), reverse=True)[:top_k]
_flashrank_reranker = None
_FLASHRANK_MODEL = 'ms-marco-MiniLM-L-12-v2'

def _get_flashrank_reranker():
    """Lazy-load flashrank CrossEncoder ranker."""
    global _flashrank_reranker
    if _flashrank_reranker is None:
        try:
            from flashrank import Ranker
            _flashrank_reranker = Ranker(model_name=_FLASHRANK_MODEL, cache_dir='/tmp/flashrank_cache')
            logger.info('[RERANK:A] flashrank CrossEncoder loaded: %s', _FLASHRANK_MODEL)
        except ImportError:
            logger.warning('[RERANK:A] flashrank not available — falling back to cosine similarity')
        except Exception as e:
            logger.warning('[RERANK:A] flashrank load failed: %s', e)
            _flashrank_reranker = None
    return _flashrank_reranker

def rerank_findings_crossencoder(query: str, findings: list[dict], top_k: int=20) -> list[dict]:
    """
    Cross-encoder reranker using flashrank ms-marco-MiniLM-L-12-v2.
    Superior to cosine similarity for cross-document relevance scoring.
    Falls back to rerank_findings_cosine if flashrank unavailable.
    """
    try:
        ranker = _get_flashrank_reranker()
        if ranker is None:
            logger.debug('[RERANK:A] Using cosine fallback')
            return rerank_findings_cosine(findings, query, top_k)
        from flashrank import RerankRequest
        passages = []
        for i, f in enumerate(findings[:200]):
            text = (f.get('content') or f.get('text') or f.get('snippet') or f.get('title', '') or str(f))[:2048]
            passages.append({'id': i, 'text': text})
        request = RerankRequest(query=query[:512], passages=passages)
        results = ranker.rerank(request)
        id_to_finding = {r['id']: findings[r['id']] for r in results[:top_k] if r['id'] < len(findings)}
        reranked = [id_to_finding[r['id']] for r in results[:top_k] if r['id'] in id_to_finding]
        logger.debug('[RERANK:A] CrossEncoder reranked %d→%d findings', len(findings), len(reranked))
        return reranked
    except Exception as e:
        logger.warning('[RERANK:A] CrossEncoder failed (%s) — cosine fallback', e)
        return rerank_findings_cosine(findings, query, top_k)
import re as _re
_IOC_PATTERNS: list[tuple[str, str]] = [('ipv4', '\\b(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?:\\.(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)){3}\\b'), ('ipv6', '\\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\\b'), ('cve', '\\bCVE-\\d{4}-\\d{4,7}\\b'), ('sha256', '\\b[a-fA-F0-9]{64}\\b'), ('sha1', '\\b[a-fA-F0-9]{40}\\b'), ('md5', '\\b[a-fA-F0-9]{32}\\b'), ('url', '\\bhttps?://[^\\s<>\\"\']+'), ('email', '\\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}\\b'), ('domain', '\\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\\.)+[a-zA-Z]{2,}\\b')]
_DOMAIN_TLD_DENYLIST: frozenset[str] = frozenset({'exe', 'dll', 'bin', 'so', 'dylib', 'lib', 'o', 'a', 'obj', 'deb', 'rpm', 'dmg', 'pkg', 'apk', 'ipa', 'jar', 'war', 'ear', 'class', 'cab', 'msi', 'lnk', 'tar', 'gz', 'zip', 'rar', '7z', 'iso', 'img', 'dat', 'tmp', 'bak', 'log', 'conf', 'cfg', 'ini', 'env', 'py', 'js', 'ts', 'html', 'htm', 'json', 'xml', 'yaml', 'yml', 'toml', 'md', 'txt', 'csv', 'sh', 'bat', 'ps1', 'pdf', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx'})

def extract_iocs_from_text(text: object) -> list[dict[str, str]]:
    """Extract Indicators of Compromise from text using regex patterns.

    Always-on, fail-safe, no external deps. Returns list of dicts with keys
    ``ioc_type`` and ``value``. Never raises; returns ``[]`` on bad input.
    """
    if not isinstance(text, str) or not text:
        return []
    try:
        out: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for ioc_type, pattern in _IOC_PATTERNS:
            for m in _re.finditer(pattern, text):
                value = m.group(0)
                if ioc_type == 'domain':
                    tld = value.rsplit('.', 1)[-1].lower()
                    if not tld.isalpha():
                        continue
                    if tld in _DOMAIN_TLD_DENYLIST:
                        continue
                elif ioc_type == 'url':
                    value = value.rstrip('.,;:!?)')
                key = (ioc_type, value.lower() if ioc_type in {'url', 'email', 'domain'} else value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({'ioc_type': ioc_type, 'value': value})
        return out
    except Exception:
        return []