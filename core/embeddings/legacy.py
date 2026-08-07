"""
MLX Embedding Manager — Canonical Implementation
================================================


Single source of truth for MLXEmbeddingManager.

This module was moved from core/mlx_embeddings.py (F350M-R A-07 refactor).
core/mlx_embeddings.py is now a deprecated re-export for backward compat.

Použití:
    from hledac.universal.core.embeddings.legacy import MLXEmbeddingManager

    manager = MLXEmbeddingManager()
    embeddings = manager.encode(["text 1", "text 2"])
"""
import asyncio
import logging
from hledac.universal.utils.msgspec_json import dumps_str as _msgspec_dumps_str, loads as _msgspec_loads
import threading
import time
import warnings
from pathlib import Path
from typing import Any
import numpy as np
logger = logging.getLogger(__name__)

def _get_rss_gb() -> float:
    """Return current RSS in GB, or 0.0 if unavailable."""
    from ._shared import get_rss_gb as _shared_get_rss_gb
    return _shared_get_rss_gb()
_EMBED_CACHE_DIR = Path.home() / '.hledac' / 'cache' / 'mlx_embed'
_EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_PREWARM_LOCK = threading.Lock()

# MLX_AVAILABLE — deferred to lazy accessor (ISSUE 3.2 fix)
# Previously: top-level `import mlx.core as mx` loaded MLX into RAM at module import,
# breaking PLANNER: ZERO MLX invariant when imported by runtime/acquisition_strategy_planner.
# Fix: lazy probe via _get_mx(), first access only when MLXEmbeddingManager is instantiated.
MLX_AVAILABLE: bool | None = None

# Lazy mlx.core accessor
_MLX_CORE: Any | None = None


def _get_mx() -> Any | None:
    """Lazily cached mlx.core module reference. Returns None if unavailable."""
    global _MLX_CORE, MLX_AVAILABLE
    if _MLX_CORE is None:
        try:
            import mlx.core as _mx
            _MLX_CORE = _mx
            MLX_AVAILABLE = True
        except ImportError:
            _MLX_CORE = False
            MLX_AVAILABLE = False
            warnings.warn('MLX not available. Install: pip install mlx>=0.15.0', stacklevel=2)
    return _MLX_CORE if _MLX_CORE is not False else None


MLX_EMBEDDINGS_LOAD: Any | None = None
MLX_EMBEDDINGS_AVAILABLE: bool | None = None
from enum import Enum


class EmbeddingTask(Enum):
    """Embedding task types for ModernBERT prefix discipline."""
    SEARCH_QUERY = 'search_query'
    SEARCH_DOCUMENT = 'search_document'
    CLUSTERING = 'clustering'
    CLASSIFICATION = 'classification'
    NONE = ''


def apply_task_prefix(text: str, task: EmbeddingTask) -> str:
    """Apply task prefix to text for ModernBERT retrieval quality."""
    if task == EmbeddingTask.NONE or not text:
        return text
    prefix = f'{task.value}: '
    if text.startswith(prefix):
        return text
    return prefix + text


def should_normalize(task: EmbeddingTask) -> bool:
    """Return True for all tasks except CLASSIFICATION (rule from embedding_task.py)."""
    return task != EmbeddingTask.CLASSIFICATION


class MLXEmbeddingManager:
    """
    Embedding manager používající ModernBERT přes MLX.

    Nahrazuje sentence-transformers s lepším výkonem na M1.
    """
    DEFAULT_MODEL = 'nomic-ai/modernbert-embed-base'
    NATIVE_DIM = 768
    EMBEDDING_DIM = 256
    MRL_DIM = 256
    MRL_DIMS: tuple[int, ...] = (256, 512, 768)
    MAX_LENGTH = 512
    SUPPORTS_TASK_PREFIX = True
    BATCH_SIZE = 32
    _current_task: EmbeddingTask | None = None
    __slots__ = tuple(('_is_loaded', '_load_lock', '_model', '_prewarm_marker_file', '_processor', '_tokenizer', '_tokenizer_args', 'model_path'))

    def __init__(self, model_path: str | Path | None=None, lazy_load: bool=True):
        """
        Inicializace embedding manageru.

        Args:
            model_path: Cesta k modelu (default: ModernBERT)
            lazy_load: Načíst model až při prvním použití
        """
        if not MLX_AVAILABLE:
            raise RuntimeError('MLX not available. Install: pip install mlx>=0.15.0 mlx-lm>=0.4.0')
        self.model_path = Path(model_path) if model_path else Path(self.DEFAULT_MODEL)
        self._model: Any = None
        self._tokenizer: Any = None
        self._processor: Any = None
        self._is_loaded = False
        self._tokenizer_args: dict[str, Any] | None = None
        self._prewarm_marker_file: Path | None = None
        self._load_lock = threading.Lock()
        if not lazy_load:
            self._load_model()

    def _load_model(self) -> None:
        """Načte ModernBERT model přes mlx-embeddings (thread-safe)."""
        if self._is_loaded:
            return
        with self._load_lock:
            if self._is_loaded:
                return
            model_name = str(self.model_path)
            logger.info(f'MLXEmbeddingManager initialized: {model_name}')
            logger.info(f'Loading embedding model: {model_name}')
            if not MLX_EMBEDDINGS_AVAILABLE:
                raise RuntimeError('mlx-embeddings not available. Install: pip install mlx-embeddings')
            try:
                self._model, self._processor = mlx_embeddings_load(model_name, lazy=False)
                self._tokenizer = self._processor._tokenizer
                self._tokenizer_args = {'model_name': model_name, 'vocab_size': getattr(self._tokenizer, 'vocab_size', None), 'model_max_length': getattr(self._tokenizer, 'model_max_length', self.MAX_LENGTH), 'padding_side': getattr(self._tokenizer, 'padding_side', 'right'), 'truncation': True, 'max_length': self.MAX_LENGTH}
                self._write_prewarm_marker(model_name)
                try:
                    from hledac.universal.utils.metal_embedder_buffers import init_metal_embedder_buffers
                    result = init_metal_embedder_buffers(max_batch=self.BATCH_SIZE, seq_len=self.MAX_LENGTH, hidden=self.NATIVE_DIM)
                    if result.get('success'):
                        logger.info(f'[MLXEmbeddingManager] Metal buffers pre-warmed: batch={self.BATCH_SIZE}, seq={self.MAX_LENGTH}, hidden={self.NATIVE_DIM}')
                    else:
                        logger.debug(f"[MLXEmbeddingManager] Buffer pre-warm skipped: {result.get('error')}")
                except Exception as ex:
                    logger.debug(f'[MLXEmbeddingManager] Buffer pre-warm failed (non-fatal): {ex}')
                self._is_loaded = True
                logger.info('✅ Embedding model loaded successfully via mlx-embeddings')
            except Exception as e:
                logger.error(f'Failed to load embedding model: {e}')
                raise

    async def ensure_loaded(self) -> None:
        """
        Async lazy load — volá _load_model v thread pool, nikdy neblokuje event loop.

        Telemetrie: loguje čas load (8-15s na M1 Air) + RSS po load.
        Cancellation-safe: pokud sprint skončí během load, load se dokončí v pozadí.
        """
        if self._is_loaded:
            return
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self._load_model)
        finally:
            load_dur = time.monotonic() - t0
            rss_gb = _get_rss_gb()
            if load_dur > 1.0:
                logger.info(f'[mlx-embed] ensure_loaded completed in {load_dur:.1f}s RSS={rss_gb:.2f}GB')

    async def encode_async(self, texts: str | list[str], batch_size: int=32, normalize: bool=True, show_progress: bool=False, truncate_dim: int | None=None, _for_indexing: bool=False) -> np.ndarray:
        """
        Async-safe encode — load + encode v thread pool, plně non-blocking event loop.

        Pro volání z async kontextu: ``await mlx_mgr.encode_async(texts)``.
        Pro volání z thread pool: ``await loop.run_in_executor(None, partial(encode_async, texts))``.

        Telemetrie: load_duration_s (>1s threshold), encode_duration_s (>0.5s threshold).
        """
        t0 = time.monotonic()
        await self.ensure_loaded()
        load_dur = time.monotonic() - t0
        if load_dur > 1.0:
            logger.info(f'[mlx-embed] encode_async load: {load_dur:.1f}s')
        t1 = time.monotonic()

        def _encode_sync():
            return self.encode(texts, batch_size=batch_size, normalize=normalize, show_progress=show_progress, truncate_dim=truncate_dim, _for_indexing=_for_indexing)
        result = await asyncio.to_thread(_encode_sync)
        encode_dur = time.monotonic() - t1
        if encode_dur > 0.5:
            logger.debug(f'[mlx-embed] encode_async: {encode_dur:.2f}s for {(len(texts) if isinstance(texts, list) else 1)} texts')
        return result

    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je model načten."""
        return self._is_loaded

    @property
    def supports_task_prefix(self) -> bool:
        """Vrátí True pokud provider podporuje task prefixy (ModernBERT ano, FastEmbed ne)."""
        return self.SUPPORTS_TASK_PREFIX

    @classmethod
    def validate_mrl_dim(cls, dim: int) -> bool:
        """
        Validate that a dimension is a supported MRL slice.

        Matryoshka Representation Learning (MRL) allows truncating ModernBERT
        embeddings to smaller dimensions while preserving retrieval quality.
        The native ModernBERT output is 768d; MRL supports slicing to any
        of (256, 512, 768). Using 256 is the M1 8GB UMA RAM/bandwidth
        sweet-spot (~3x smaller LanceDB vectors, ~3x faster cosine sim).

        Args:
            dim: Candidate embedding dimension.

        Returns:
            True if dim is a valid MRL dimension, False otherwise.
        """
        return dim in cls.MRL_DIMS

    @classmethod
    def get_mrl_dims(cls) -> tuple[int, ...]:
        """Return the tuple of supported MRL dimensions (single source of truth)."""
        return cls.MRL_DIMS

    def embed_query(self, text: str, truncate_dim: int | None=None) -> np.ndarray:
        """Embed user query (asymmetric - search_query prefix)."""
        return self._embed_task(text, EmbeddingTask.SEARCH_QUERY, truncate_dim)

    def embed_document(self, text: str, truncate_dim: int | None=None) -> np.ndarray:
        """Embed document for indexing (asymmetric - search_document prefix)."""
        return self._embed_task(text, EmbeddingTask.SEARCH_DOCUMENT, truncate_dim)

    def embed_for_clustering(self, text: str, truncate_dim: int | None=None) -> np.ndarray:
        """Embed text for clustering (symmetric - clustering prefix)."""
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim)

    def embed_for_dedup(self, text: str, truncate_dim: int | None=None) -> np.ndarray:
        """Embed text for deduplication (symmetric - clustering task)."""
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim, force_normalize=True)

    def _embed_for_indexing(self, texts: str | list[str], truncate_dim: int | None=None) -> np.ndarray:
        """
        Internal method for batch document embedding (used by LanceDB store for indexing).

        This wraps embed_document to ensure task safety for indexing operations.
        """
        if isinstance(texts, str):
            texts = [texts]
        results = [self.embed_document(t, truncate_dim=truncate_dim) for t in texts]
        return np.vstack(results) if results else np.array([])

    def _embed_task(self, text: str, task: EmbeddingTask, truncate_dim: int | None=None, force_normalize: bool=False) -> np.ndarray:
        """
        Internal task-aware embed method.

        Applies prefix only if provider supports it.
        Prefix is applied ONLY during embedding, never stored in DB.
        """
        self._current_task = task
        self._log_task(task)
        if self.supports_task_prefix:
            text = apply_task_prefix(text, task)
        normalize = force_normalize or should_normalize(task)
        for_indexing = task == EmbeddingTask.SEARCH_DOCUMENT
        try:
            result = self.encode(text, normalize=normalize, truncate_dim=truncate_dim or self.MRL_DIM, _for_indexing=for_indexing)
        finally:
            self._current_task = None
        return result

    def _log_task(self, task: EmbeddingTask) -> None:
        """Log task on first occurrence for runtime truth."""
        global _task_logged
        if not _task_logged:
            logger.info(f'[EMBEDDER] task={task.value}')
            _task_logged = True

    def encode(self, texts: str | list[str], batch_size: int=32, normalize: bool=True, show_progress: bool=False, truncate_dim: int | None=None, _for_indexing: bool=False) -> np.ndarray:
        """
        Zakóduje texty do embedding vektorů.

        Args:
            texts: Jeden text nebo seznam textů
            batch_size: Velikost batch pro zpracování
            normalize: Normalizovat vektory (L2 norm)
            show_progress: Zobrazit progress bar
            truncate_dim: Optional truncation to 256 for Matryoshka
            _for_indexing: Internal flag - if True, validate task is DOCUMENT

        Returns:
            NumPy array tvaru (n_texts, EMBEDDING_DIM) or (n_texts, truncate_dim)

        Raises:
            RuntimeError: If _for_indexing=True but task is not SEARCH_DOCUMENT
        """
        if _for_indexing and self._current_task != EmbeddingTask.SEARCH_DOCUMENT:
            raise RuntimeError(f'Attempt to index non-document embedding. Current task: {self._current_task}. Use embed_document() for indexing.')
        if not self._is_loaded:
            self._load_model()
        mx = _get_mx()
        if mx is None:
            raise RuntimeError('MLX not available — cannot call _embed_task')
        batch_size = min(batch_size, self.BATCH_SIZE)
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.array([])
        all_embeddings = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            if show_progress:
                logger.info(f'Encoding batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1}')
            inputs = self._tokenizer(batch, padding=True, truncation=True, max_length=self.MAX_LENGTH, return_tensors='mlx')
            from hledac.universal.utils.mlx_memory import get_metal_stream_context
            with get_metal_stream_context():
                outputs = self._model(input_ids=inputs.input_ids, attention_mask=inputs.attention_mask)
                embeddings = outputs.text_embeds
                if truncate_dim and truncate_dim < self.EMBEDDING_DIM:
                    embeddings = embeddings[:, :truncate_dim]
                if normalize:
                    norms = mx.linalg.norm(embeddings, axis=1, keepdims=True)
                    embeddings = embeddings / mx.clip(norms, a_min=1e-12, a_max=None)
                mx.eval(embeddings)
            embeddings_np = np.array(embeddings)
            del outputs
            del embeddings
            del inputs
            all_embeddings.append(embeddings_np)
        return np.vstack(all_embeddings)

    def _mean_pooling(self, token_embeddings: mx.array, attention_mask: mx.array) -> mx.array:
        """
        Mean pooling s ohledem na attention mask.

        Args:
            token_embeddings: Vstupní embeddngy tvaru (batch, seq_len, hidden)
            attention_mask: Attention mask tvaru (batch, seq_len)

        Returns:
            Pooled embeddings tvaru (batch, hidden)
        """
        mx = _get_mx()
        if mx is None:
            raise RuntimeError('MLX not available — cannot call _mean_pooling')
        mask_expanded = mx.expand_dims(attention_mask, -1)
        mask_expanded = mx.broadcast_to(mask_expanded, token_embeddings.shape)
        sum_embeddings = mx.sum(token_embeddings * mask_expanded, axis=1)
        sum_mask = mx.clip(mx.sum(attention_mask, axis=1, keepdims=True), a_min=1e-09, a_max=None)
        return sum_embeddings / sum_mask

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        """
        L2 normalizace embedding vektorů.

        Args:
            embeddings: Vstupní vektory tvaru (n, dim)

        Returns:
            Normalizované vektory
        """
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        return embeddings / np.clip(norms, a_min=1e-12)

    def similarity(self, text1: str | list[str], text2: str | list[str]) -> float | np.ndarray:
        """
        Vypočítá kosinovou podobnost mezi texty.

        Args:
            text1: První text nebo seznam textů
            text2: Druhý text nebo seznam textů

        Returns:
            Podobnost skóre (0-1)
        """
        emb1 = self.encode(text1, normalize=True)
        emb2 = self.encode(text2, normalize=True)
        if emb1.ndim == 1:
            emb1 = emb1.reshape(1, -1)
        if emb2.ndim == 1:
            emb2 = emb2.reshape(1, -1)
        similarity = np.dot(emb1, emb2.T)
        if similarity.shape == (1, 1):
            return float(similarity[0, 0])
        return similarity

    def unload(self) -> None:
        """Uvolní model z paměti."""
        if self._is_loaded:
            logger.info('Unloading embedding model')
            self._model = None
            self._tokenizer = None
            self._is_loaded = False
            import gc
            gc.collect()
            mx = _get_mx()
            if mx:
                try:
                    mx.eval([])
                    import gc
                    gc.collect()
                    try:
                        if hasattr(mx, 'clear_cache'):
                            mx.clear_cache()
                        elif hasattr(mx.metal, 'clear_cache'):
                            mx.metal.clear_cache()
                    except Exception as exc:
                        logger.debug(f'mx.clear_cache() raised (non-fatal): {exc}')
                    gc.collect()
                except Exception as exc:
                    logger.debug(f'MLX eval during unload raised (non-fatal): {exc}')
            try:
                from hledac.universal.utils.metal_embedder_buffers import release_metal_embedder_buffers
                release_metal_embedder_buffers()
            except Exception:  # noqa: BLE001
                pass

    def get_info(self) -> dict:
        """Vrátí informace o manageru."""
        return {'model_path': str(self.model_path), 'is_loaded': self._is_loaded, 'embedding_dim': self.EMBEDDING_DIM, 'max_length': self.MAX_LENGTH, 'mlx_available': MLX_AVAILABLE}

    def _prewarm_marker_path(self, model_name: str) -> Path:
        """Return path to prewarm marker file for given model."""
        safe_name = model_name.replace('/', '_').replace('-', '_')
        return _EMBED_CACHE_DIR / f'prewarm_{safe_name}.marker'

    def _write_prewarm_marker(self, model_name: str) -> None:
        """Write prewarm marker after successful model load (thread-safe)."""
        try:
            marker_path = self._prewarm_marker_path(model_name)
            with _PREWARM_LOCK:
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(_msgspec_dumps_str({'model': model_name, 'loaded_at': time.time(), 'version': 1}))
            self._prewarm_marker_file = marker_path
            logger.debug(f'[MLXEmbed] prewarm marker written: {marker_path}')
        except Exception as e:
            logger.debug(f'[MLXEmbed] prewarm marker write failed (non-fatal): {e}')

    def _read_prewarm_marker(self, model_name: str) -> dict | None:
        """Read prewarm marker if it exists and is valid (thread-safe)."""
        try:
            marker_path = self._prewarm_marker_path(model_name)
            with _PREWARM_LOCK:
                if not marker_path.exists():
                    return None
                data = _msgspec_loads(marker_path.read_text())
            if data.get('model') == model_name:
                return data
            return None
        except Exception:
            return None

    def prewarm(self) -> bool:
        """
        F275-5: Pre-warm the embedding model — load if not already loaded.

        Checks for prewarm marker; if fresh (<7 days), skips load and returns True.
        If marker missing or stale, loads the model and writes new marker.

        This should be called during sprint pre-flight (run_prelude) so the
        embedding model is ready before first encode() call during acquisition.

        Returns:
            True if model is ready (loaded or already warm), False on error.
        """
        _FRESH_HOURS = 168
        if self._is_loaded:
            logger.debug('[MLXEmbed] prewarm: already loaded')
            return True
        model_name = str(self.model_path)
        marker = self._read_prewarm_marker(model_name)
        if marker:
            age_hours = (time.time() - marker.get('loaded_at', 0)) / 3600
            if age_hours < _FRESH_HOURS:
                self._prewarm_marker_file = self._prewarm_marker_path(model_name)
                logger.info(f'[MLXEmbed] prewarm: marker fresh ({age_hours:.1f}h < {_FRESH_HOURS}h), hot HF cache')
                try:
                    self._load_model()
                except Exception as e:
                    logger.warning(f'[MLXEmbed] prewarm load failed: {e}')
                    return False
                return True
            else:
                logger.debug(f'[MLXEmbed] prewarm: marker stale ({age_hours:.1f}h old)')
                marker = None
        if not marker:
            try:
                self._load_model()
                return True
            except Exception as e:
                logger.warning(f'[MLXEmbed] prewarm load failed: {e}')
                return False
        return True

    def is_prewarm(self) -> bool:
        """Return True if a valid prewarm marker exists AND model is loaded."""
        if not self._is_loaded:
            return False
        model_name = str(self.model_path)
        return self._read_prewarm_marker(model_name) is not None


def prewarm_embedding_model() -> bool:
    """
    F275-5: Module-level prewarm — loads MLX embedding model before sprint.

    Call during sprint pre-flight (run_prelude) to ensure the embedding model
    is ready before the first encode() call. Uses lazy singleton so no
    impact if called when embeddings aren't needed.

    Returns:
        True if model loaded/ready, False on error.
    """
    try:
        mgr = get_mlx_embedder()
        return mgr.prewarm()
    except Exception as e:
        logger.warning(f'[MLXEmbed] prewarm_embedding_model failed: {e}')
        return False


def is_embedding_model_prewarmed() -> bool:
    """Return True if MLX embedder singleton has a valid prewarm marker."""
    try:
        if _default_manager is None:
            return False
        return _default_manager.is_prewarm()
    except Exception:
        return False


_default_manager: MLXEmbeddingManager | None = None
_init_logged: bool = False
_task_logged: bool = False
_init_lock = threading.Lock()


def get_mlx_embedder() -> MLXEmbeddingManager:
    """Vrátí globální instanci MLX embedding manageru (singleton)."""
    global _default_manager, _init_logged, _task_logged
    if _default_manager is None:
        with _init_lock:
            if _default_manager is None:
                _default_manager = MLXEmbeddingManager(lazy_load=True)
    with _init_lock:
        if not _init_logged:
            mgr = _default_manager
            metal_status = 'unknown'
            mx = _get_mx()
            if mx and hasattr(mx, 'metal'):
                metal_status = 'yes' if mx.metal.is_available() else 'no'
            logger.info(f'[EMBEDDER] provider=MLX model={mgr.model_path} dim={mgr.EMBEDDING_DIM} MRL_dim={mgr.MRL_DIM} max_length={mgr.MAX_LENGTH} source=auto normalized=yes pooling=mean metal={metal_status}')
            _init_logged = True
    return _default_manager


def get_embedding_manager() -> MLXEmbeddingManager:
    """Deprecated: use get_mlx_embedder() instead."""
    return get_mlx_embedder()


def get_embedding_info() -> dict:
    """Vrátí info o aktuálním embedding provideru."""
    global _default_manager
    if _default_manager is None:
        return {'provider': 'not_initialized'}
    metal_status = 'unknown'
    mx = _get_mx()
    if mx and hasattr(mx, 'metal'):
        metal_status = 'yes' if mx.metal.is_available() else 'no'
    return {'provider': 'MLXEmbeddingManager', 'model': str(_default_manager.model_path), 'dim': _default_manager.EMBEDDING_DIM, 'mrl_dim': _default_manager.MRL_DIM, 'max_length': _default_manager.MAX_LENGTH, 'metal': metal_status, 'is_loaded': _default_manager.is_loaded}


class EmbeddingDimensionError(Exception):
    """Raised when embedding dimension mismatch is detected."""
    pass


def assert_embedding_dimension(expected_dim: int, context: str='') -> None:
    """
    Verify that current embedding dimension matches expected dimension.

    Supports all canonical embedding backends:
    - 256  (MRL canonical — M1 8GB UMA sweet-spot, 3x smaller vectors)
    - 384  (MiniLM-L6-v2 fallback — backward compat)
    - 512  (MRL mid — half the native dim, balanced)
    - 768  (ModernBERT native / MRL full — max quality, max RAM)

    Args:
        expected_dim: Expected dimension (256, 384, 512, or 768)
        context: Context string for error message

    Raises:
        EmbeddingDimensionError: If dimension doesn't match
    """
    _VALID_DIMS = frozenset({256, 384, 512, 768})
    global _default_manager
    if _default_manager is None:
        raise EmbeddingDimensionError(f'Embedding provider not initialized. Cannot verify dimension {expected_dim}. Context: {context}')
    actual_dim = _default_manager.EMBEDDING_DIM
    if expected_dim not in _VALID_DIMS:
        raise EmbeddingDimensionError(f'Invalid expected_dim {expected_dim}. Must be 256, 384, 512, or 768. Context: {context}')
    if actual_dim != expected_dim:
        raise EmbeddingDimensionError(f'Embedding dimension mismatch: expected {expected_dim}, got {actual_dim}. Model: {_default_manager.model_path}. Context: {context}. Set HLEDAC_RESET_EMBEDDING_CACHE=1 to force reset.')


def encode_texts(texts: str | list[str], **kwargs) -> np.ndarray:
    """
    Jednoduchá funkce pro zakódování textů.

    Args:
        texts: Texty k zakódování
        **kwargs: Další parametry pro encode()

    Returns:
        Embedding vektory
    """
    manager = get_embedding_manager()
    return manager.encode(texts, **kwargs)


def compute_similarity(text1: str, text2: str) -> float | np.ndarray:
    """
    Vypočítá podobnost dvou textů.

    Args:
        text1: První text
        text2: Druhý text

    Returns:
        Podobnost skóre 0-1
    """
    manager = get_embedding_manager()
    return manager.similarity(text1, text2)


def __getattr__(name: str):
    """Lazy-load mlx_embeddings module-level variables (backward compat)."""
    if name in ('MLX_EMBEDDINGS_LOAD', 'MLX_EMBEDDINGS_AVAILABLE'):
        global MLX_EMBEDDINGS_LOAD, MLX_EMBEDDINGS_AVAILABLE
        try:
            from mlx_embeddings import load as mlx_embeddings_load
            MLX_EMBEDDINGS_LOAD = mlx_embeddings_load
            MLX_EMBEDDINGS_AVAILABLE = True
        except ImportError:
            MLX_EMBEDDINGS_LOAD = None
            MLX_EMBEDDINGS_AVAILABLE = False
        return globals()[name]
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    print('Testing MLX Embedding Manager...')
    manager = MLXEmbeddingManager()
    test_texts = ['Machine learning is fascinating', 'Deep learning transforms AI', 'The weather is nice today']
    print(f'\nEncoding {len(test_texts)} texts...')
    embeddings = manager.encode(test_texts)
    print(f'Shape: {embeddings.shape}')
    print(f'Sample (first 5 dims of first text): {embeddings[0, :5]}')
    print('\nSimilarity matrix:')
    sim = manager.similarity(test_texts, test_texts)
    print(sim)
