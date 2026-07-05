"""
MLX Embedding Manager — lazy ModernBERT via mlx-embeddings.

Single source of truth for embedding lifecycle (load/encode/unload/prewarm).
Metal buffers pre-warmed on load; mx.eval([]) barrier before clear_cache().
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import warnings
from enum import Enum
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# === Lazy MLX detection ===
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    warnings.warn("MLX not available. Install: pip install mlx>=0.15.0", stacklevel=2)

try:
    from mlx_embeddings import load as mlx_embeddings_load
    MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    MLX_EMBEDDINGS_AVAILABLE = False
    warnings.warn("mlx-embeddings not available. Install: pip install mlx-embeddings", stacklevel=2)

# === Persistent prewarm state ===
_EMBED_CACHE_DIR = Path.home() / ".hledac" / "cache" / "mlx_embed"
_EMBED_CACHE_DIR.mkdir(parents=True, exist_ok=True)
_PREWARM_LOCK = threading.Lock()


# === Embedding Task Enum (prefix discipline) ===
class EmbeddingTask(Enum):
    SEARCH_QUERY = "search_query"
    SEARCH_DOCUMENT = "search_document"
    CLUSTERING = "clustering"
    CLASSIFICATION = "classification"
    NONE = ""


def apply_task_prefix(text: str, task: EmbeddingTask) -> str:
    """Apply task prefix to text for ModernBERT retrieval quality."""
    if task == EmbeddingTask.NONE or not text:
        return text
    prefix = f"{task.value}: "
    if text.startswith(prefix):
        return text
    return prefix + text


def should_normalize(task: EmbeddingTask) -> bool:
    """Return True for all tasks except CLASSIFICATION."""
    return task != EmbeddingTask.CLASSIFICATION


def _get_rss_gb() -> float:
    """Return current RSS in GB, or 0.0 if unavailable."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1e9
    except Exception:
        return 0.0


class MLXEmbeddingManager:
    """
    Embedding manager using ModernBERT via MLX.

    Lazy load on first encode; prewarm via prewarm() in sprint pre-flight.
    Thread-safe via double-check locking (_load_lock).
    """

    DEFAULT_MODEL = "nomic-ai/modernbert-embed-base"
    NATIVE_DIM = 768
    EMBEDDING_DIM = 256
    MRL_DIM = 256
    MRL_DIMS: tuple[int, ...] = (256, 512, 768)
    MAX_LENGTH = 512
    SUPPORTS_TASK_PREFIX = True
    BATCH_SIZE = 32

    _current_task: EmbeddingTask | None = None

    def __init__(self, model_path: str | Path | None = None, lazy_load: bool = True):
        if not MLX_AVAILABLE:
            raise RuntimeError("MLX not available. Install: pip install mlx>=0.15.0 mlx-lm>=0.4.0")

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
        """Load ModernBERT via mlx-embeddings (thread-safe double-check)."""
        if self._is_loaded:
            return
        with self._load_lock:
            if self._is_loaded:
                return

            model_name = str(self.model_path)
            logger.info(f"Loading embedding model: {model_name}")

            if not MLX_EMBEDDINGS_AVAILABLE:
                raise RuntimeError("mlx-embeddings not available. Install: pip install mlx-embeddings")

            try:
                self._model, self._processor = mlx_embeddings_load(model_name, lazy=False)
                self._tokenizer = self._processor._tokenizer

                self._tokenizer_args = {
                    "model_name": model_name,
                    "vocab_size": getattr(self._tokenizer, "vocab_size", None),
                    "model_max_length": getattr(self._tokenizer, "model_max_length", self.MAX_LENGTH),
                    "padding_side": getattr(self._tokenizer, "padding_side", "right"),
                    "truncation": True,
                    "max_length": self.MAX_LENGTH,
                }
                self._write_prewarm_marker(model_name)

                # E.4: Pre-warm Metal buffers
                try:
                    from hledac.universal.utils.metal_embedder_buffers import init_metal_embedder_buffers
                    result = init_metal_embedder_buffers(
                        max_batch=self.BATCH_SIZE,
                        seq_len=self.MAX_LENGTH,
                        hidden=self.NATIVE_DIM
                    )
                    if result.get("success"):
                        logger.info(
                            f"[MLXEmbeddingManager] Metal buffers pre-warmed: "
                            f"batch={self.BATCH_SIZE}, seq={self.MAX_LENGTH}, hidden={self.NATIVE_DIM}"
                        )
                except Exception as ex:
                    logger.debug(f"[MLXEmbeddingManager] Buffer pre-warm failed (non-fatal): {ex}")

                self._is_loaded = True
                logger.info("✅ Embedding model loaded successfully via mlx-embeddings")

            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
                raise

    async def ensure_loaded(self) -> None:
        """Async lazy load — runs _load_model in thread pool, non-blocking."""
        if self._is_loaded:
            return
        t0 = time.monotonic()
        try:
            await asyncio.to_thread(self._load_model)
        finally:
            load_dur = time.monotonic() - t0
            rss_gb = _get_rss_gb()
            if load_dur > 1.0:
                logger.info(f"[mlx-embed] ensure_loaded completed in {load_dur:.1f}s RSS={rss_gb:.2f}GB")

    async def encode_async(
        self,
        texts: str | list[str],
        batch_size: int = 32,
        normalize: bool = True,
        show_progress: bool = False,
        truncate_dim: int | None = None,
        _for_indexing: bool = False,
    ) -> np.ndarray:
        """Async-safe encode — load + encode in thread pool."""
        t0 = time.monotonic()
        await self.ensure_loaded()
        load_dur = time.monotonic() - t0
        if load_dur > 1.0:
            logger.info(f"[mlx-embed] encode_async load: {load_dur:.1f}s")

        t1 = time.monotonic()

        def _encode_sync():
            return self.encode(
                texts,
                batch_size=batch_size,
                normalize=normalize,
                show_progress=show_progress,
                truncate_dim=truncate_dim,
                _for_indexing=_for_indexing,
            )

        result = await asyncio.to_thread(_encode_sync)
        encode_dur = time.monotonic() - t1
        if encode_dur > 0.5:
            logger.debug(f"[mlx-embed] encode_async: {encode_dur:.2f}s for {len(texts) if isinstance(texts, list) else 1} texts")
        return result

    @property
    def is_loaded(self) -> bool:
        return self._is_loaded

    @property
    def supports_task_prefix(self) -> bool:
        return self.SUPPORTS_TASK_PREFIX

    @classmethod
    def validate_mrl_dim(cls, dim: int) -> bool:
        return dim in cls.MRL_DIMS

    @classmethod
    def get_mrl_dims(cls) -> tuple[int, ...]:
        return cls.MRL_DIMS

    # === Task-aware embedding methods ===

    def embed_query(self, text: str, truncate_dim: int | None = None) -> np.ndarray:
        return self._embed_task(text, EmbeddingTask.SEARCH_QUERY, truncate_dim)

    def embed_document(self, text: str, truncate_dim: int | None = None) -> np.ndarray:
        return self._embed_task(text, EmbeddingTask.SEARCH_DOCUMENT, truncate_dim)

    def embed_for_clustering(self, text: str, truncate_dim: int | None = None) -> np.ndarray:
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim)

    def embed_for_dedup(self, text: str, truncate_dim: int | None = None) -> np.ndarray:
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim, force_normalize=True)

    def _embed_for_indexing(self, texts: str | list[str], truncate_dim: int | None = None) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]
        results = [self.embed_document(t, truncate_dim=truncate_dim) for t in texts]
        return np.vstack(results) if results else np.array([])

    def _embed_task(
        self,
        text: str,
        task: EmbeddingTask,
        truncate_dim: int | None = None,
        force_normalize: bool = False
    ) -> np.ndarray:
        self._current_task = task
        self._log_task(task)

        if self.supports_task_prefix:
            text = apply_task_prefix(text, task)

        normalize = force_normalize or should_normalize(task)
        for_indexing = task == EmbeddingTask.SEARCH_DOCUMENT

        try:
            result = self.encode(
                text,
                normalize=normalize,
                truncate_dim=truncate_dim or self.MRL_DIM,
                _for_indexing=for_indexing
            )
        finally:
            self._current_task = None

        return result

    def _log_task(self, task: EmbeddingTask) -> None:
        global _task_logged
        if not _task_logged:
            logger.info(f"[EMBEDDER] task={task.value}")
            _task_logged = True

    def encode(
        self,
        texts: str | list[str],
        batch_size: int = 32,
        normalize: bool = True,
        show_progress: bool = False,
        truncate_dim: int | None = None,
        _for_indexing: bool = False,
    ) -> np.ndarray:
        if _for_indexing and self._current_task != EmbeddingTask.SEARCH_DOCUMENT:
            raise RuntimeError(
                f"Attempt to index non-document embedding. "
                f"Current task: {self._current_task}. "
                f"Use embed_document() for indexing."
            )

        if not self._is_loaded:
            self._load_model()

        batch_size = min(batch_size, self.BATCH_SIZE)

        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return np.array([])

        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            if show_progress:
                logger.info(f"Encoding batch {i // batch_size + 1}/{(len(texts) - 1) // batch_size + 1}")

            inputs = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.MAX_LENGTH,
                return_tensors="mlx"
            )

            from hledac.universal.utils.mlx_memory import get_metal_stream_context
            with get_metal_stream_context():
                outputs = self._model(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask
                )
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

    def similarity(self, text1: str | list[str], text2: str | list[str]) -> float | np.ndarray:
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
        """Unload model and release Metal buffers."""
        if not self._is_loaded:
            return
        logger.info("Unloading embedding model")
        self._model = None
        self._tokenizer = None
        self._is_loaded = False

        import gc
        gc.collect()

        if MLX_AVAILABLE:
            try:
                import mlx.core as _mx
                _mx.eval([])
                gc.collect()
                if hasattr(_mx, "clear_cache"):
                    _mx.clear_cache()
                elif hasattr(_mx.metal, "clear_cache"):
                    _mx.metal.clear_cache()
                gc.collect()
            except Exception as exc:
                logger.debug(f"mx.clear_cache() raised (non-fatal): {exc}")

        try:
            from hledac.universal.utils.metal_embedder_buffers import release_metal_embedder_buffers
            release_metal_embedder_buffers()
        except Exception:
            pass

    def get_info(self) -> dict:
        return {
            "model_path": str(self.model_path),
            "is_loaded": self._is_loaded,
            "embedding_dim": self.EMBEDDING_DIM,
            "max_length": self.MAX_LENGTH,
            "mlx_available": MLX_AVAILABLE,
        }

    # === F275-5: Persistent prewarm helpers ===

    def _prewarm_marker_path(self, model_name: str) -> Path:
        safe_name = model_name.replace("/", "_").replace("-", "_")
        return _EMBED_CACHE_DIR / f"prewarm_{safe_name}.marker"

    def _write_prewarm_marker(self, model_name: str) -> None:
        try:
            marker_path = self._prewarm_marker_path(model_name)
            with _PREWARM_LOCK:
                marker_path.parent.mkdir(parents=True, exist_ok=True)
                marker_path.write_text(json.dumps({
                    "model": model_name,
                    "loaded_at": time.time(),
                    "version": 1,
                }))
            self._prewarm_marker_file = marker_path
        except Exception as e:
            logger.debug(f"[MLXEmbed] prewarm marker write failed (non-fatal): {e}")

    def _read_prewarm_marker(self, model_name: str) -> dict | None:
        try:
            marker_path = self._prewarm_marker_path(model_name)
            with _PREWARM_LOCK:
                if not marker_path.exists():
                    return None
                data = json.loads(marker_path.read_text())
            if data.get("model") == model_name:
                return data
            return None
        except Exception:
            return None

    def prewarm(self) -> bool:
        """Pre-warm: load model if not loaded; skip if fresh marker exists."""
        _FRESH_HOURS = 168

        if self._is_loaded:
            return True

        model_name = str(self.model_path)
        marker = self._read_prewarm_marker(model_name)
        if marker:
            age_hours = (time.time() - marker.get("loaded_at", 0)) / 3600
            if age_hours < _FRESH_HOURS:
                self._prewarm_marker_file = self._prewarm_marker_path(model_name)
                logger.info(f"[MLXEmbed] prewarm: marker fresh ({age_hours:.1f}h < {_FRESH_HOURS}h)")
                try:
                    self._load_model()
                except Exception as e:
                    logger.warning(f"[MLXEmbed] prewarm load failed: {e}")
                    return False
                return True
            else:
                logger.debug(f"[MLXEmbed] prewarm: marker stale ({age_hours:.1f}h old)")
                marker = None

        if not marker:
            try:
                self._load_model()
                return True
            except Exception as e:
                logger.warning(f"[MLXEmbed] prewarm load failed: {e}")
                return False

        return True

    def is_prewarm(self) -> bool:
        if not self._is_loaded:
            return False
        model_name = str(self.model_path)
        return self._read_prewarm_marker(model_name) is not None


# === Module-level singleton + helpers ===
_default_manager: MLXEmbeddingManager | None = None
_init_logged: bool = False
_task_logged: bool = False
_init_lock = threading.Lock()


def get_mlx_embedder() -> MLXEmbeddingManager:
    """Global singleton MLX embedding manager."""
    global _default_manager, _init_logged
    if _default_manager is None:
        with _init_lock:
            if _default_manager is None:
                _default_manager = MLXEmbeddingManager(lazy_load=True)

    with _init_lock:
        if not _init_logged:
            mgr = _default_manager
            metal_status = "unknown"
            try:
                import mlx.core as mx
                metal_status = "yes" if hasattr(mx, 'metal') and mx.metal.is_available() else "no"
            except Exception:
                pass

            logger.info(
                f"[EMBEDDER] provider=MLX model={mgr.model_path} dim={mgr.EMBEDDING_DIM} "
                f"MRL_dim={mgr.MRL_DIM} max_length={mgr.MAX_LENGTH} "
                f"source=auto normalized=yes pooling=mean metal={metal_status}"
            )
            _init_logged = True

    return _default_manager


def get_embedding_manager() -> MLXEmbeddingManager:
    """Deprecated: use get_mlx_embedder()."""
    return get_mlx_embedder()


def get_embedding_info() -> dict:
    global _default_manager
    if _default_manager is None:
        return {"provider": "not_initialized"}

    metal_status = "unknown"
    try:
        import mlx.core as mx
        metal_status = "yes" if hasattr(mx, 'metal') and mx.metal.is_available() else "no"
    except Exception:
        pass

    return {
        "provider": "MLXEmbeddingManager",
        "model": str(_default_manager.model_path),
        "dim": _default_manager.EMBEDDING_DIM,
        "mrl_dim": _default_manager.MRL_DIM,
        "max_length": _default_manager.MAX_LENGTH,
        "metal": metal_status,
        "is_loaded": _default_manager.is_loaded
    }


def encode_texts(texts: str | list[str], **kwargs) -> np.ndarray:
    manager = get_mlx_embedder()
    return manager.encode(texts, **kwargs)


def compute_similarity(text1: str, text2: str) -> float | np.ndarray:
    manager = get_mlx_embedder()
    return manager.similarity(text1, text2)


def prewarm_embedding_model() -> bool:
    """Module-level prewarm — load MLX embedding model before sprint."""
    try:
        mgr = get_mlx_embedder()
        return mgr.prewarm()
    except Exception as e:
        logger.warning(f"[MLXEmbed] prewarm_embedding_model failed: {e}")
        return False


def is_embedding_model_prewarmed() -> bool:
    """Return True if MLX embedder singleton has a valid prewarm marker."""
    try:
        if _default_manager is None:
            return False
        return _default_manager.is_prewarm()
    except Exception:
        return False


class EmbeddingDimensionError(Exception):
    """Raised when embedding dimension mismatch is detected."""
    pass


def assert_embedding_dimension(expected_dim: int, context: str = "") -> None:
    """Verify current embedding dimension matches expected."""
    _VALID_DIMS = frozenset({256, 384, 512, 768})

    global _default_manager
    if _default_manager is None:
        raise EmbeddingDimensionError(
            f"Embedding provider not initialized. Cannot verify dimension {expected_dim}. "
            f"Context: {context}"
        )

    actual_dim = _default_manager.EMBEDDING_DIM
    if expected_dim not in _VALID_DIMS:
        raise EmbeddingDimensionError(
            f"Invalid expected_dim {expected_dim}. Must be 256, 384, 512, or 768. "
            f"Context: {context}"
        )

    if actual_dim != expected_dim:
        raise EmbeddingDimensionError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {actual_dim}. "
            f"Model: {_default_manager.model_path}. Context: {context}. "
            f"Set HLEDAC_RESET_EMBEDDING_CACHE=1 to force reset."
        )
