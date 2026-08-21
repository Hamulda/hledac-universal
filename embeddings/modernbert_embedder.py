"""
ModernBERTEmbedder — MLX-accelerated ModernBERT encoder for embeddings, dedup, routing.

Provides:



- Batch text embedding via mlx-embeddings (ModernBERT-base, 768d)
- Symmetric/asymmetric embedding support (search_query vs search_document prefixes)
- M1 Metal cache cleanup on unload
- Fallback: sentence-transformers (CPU) if mlx-embeddings unavailable
- Adaptive batch sizing based on available Metal memory (Sprint F265D)

Canonical import path: from hledac.universal.embeddings.modernbert_embedder import ModernBERTEmbedder
Replaces: utils/semantic.py ModernBERTEmbedding (DEPRECATED)
"""

import logging
import threading

import numpy as np

from compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)
MLX_EMBEDDINGS_AVAILABLE = False
_mlx_available = False

# MEM-UMA-003: M1 8GB memory bounds for embedding batches
# Per-document char limit: 50k chars ≈ ~12.5k tokens (well under ModernBERT 8192 max_seq_len safety margin)
MAX_EMBED_DOCUMENT_CHARS = 50_000
# Maximum documents per embed_batch call — prevents OOM on adversarial input
MAX_EMBED_BATCH_SIZE = 256
# Maximum total chars per batch — enforces sub-batch chunking for huge inputs
MAX_EMBED_BATCH_TOTAL_CHARS = 5_000_000

try:
    import mlx.core as mx

    _mlx_available = mx.metal.is_available() if hasattr(mx, "metal") else False
except ImportError:
    _mlx_available = False
try:
    from mlx_embeddings import load as mlx_embeddings_load

    class _ModernBERTMLXLoader:
        """
        Thread-safe deferred loader to avoid import overhead when mlx-embeddings unavailable.

        F265-3×-FIX: Uses threading.Lock to prevent duplicate mlx_embeddings_load()
        calls when prewarm daemon (background thread) races with main thread.
        MLX Metal backend is registered per-thread; ensure load happens once
        on the correct thread (main thread where inference runs).
        """

        _instance = None
        _model = None
        _processor = None
        _tokenizer = None
        _lock: threading.Lock | None = None

        @classmethod
        def _get_lock(cls) -> threading.Lock:
            """Lazily initialize lock (avoid import at module load)."""
            if cls._lock is None:
                cls._lock = threading.Lock()
            return cls._lock

        @classmethod
        def load(cls, model_path: str):
            if cls._instance is not None:
                return (cls._model, cls._tokenizer)
            lock = cls._get_lock()
            with lock:
                if cls._instance is None:
                    cls._model, cls._processor = mlx_embeddings_load(model_path, lazy=False)
                    cls._tokenizer = cls._processor._tokenizer
                    cls._instance = True
                    logger.info(f"[MODERNBERT] MLX load OK: {model_path}")
            return (cls._model, cls._tokenizer)

    MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    MLX_EMBEDDINGS_AVAILABLE = False
    _ModernBERTMLXLoader = None


class ModernBERTConfig(Struct):
    """Configuration for ModernBERT embedder."""

    model_path: str = "nomic-ai/modernbert-embed-base"
    max_seq_len: int = 512
    embed_dim: int = 768
    batch_size: int = 8
    normalize: bool = True
    batch_size_high: int = 16
    batch_size_low: int = 4
    batch_size_max: int = 32


class ModernBERTEmbedder:
    """
    MLX-accelerated ModernBERT encoder.

    Supports task-aware prefixes:
    - search_query: for query-side embeddings
    - search_document: for document-side embeddings
    - clustering/classification: no prefix, L2 norm varies

    M1 8GB safe: Metal cache cleared on unload.
    """

    _SEARCH_QUERY_PREFIX = "search_query: "
    _SEARCH_DOC_PREFIX = "search_document: "
    __slots__ = ("_is_loaded", "_model", "_tokenizer", "config")

    def __init__(
        self,
        model_path: str | None = None,
        lazy_load: bool = True,
        normalize: bool = True,
        batch_size: int = 8,
        batch_size_high: int = 16,
        batch_size_low: int = 4,
        batch_size_max: int = 32,
    ) -> None:
        """
        Initialize ModernBERT embedder.

        Args:
            model_path: HuggingFace model ID or local path. Defaults to nomic-ai/modernbert-embed-base.
            lazy_load: If True, defer model load until first embed() call.
            normalize: L2-normalize embeddings (default True for retrieval).
            batch_size: Default batch size (WARNING memory pressure, default 8).
            batch_size_high: Max batch size when Metal memory 50-80% (default 16).
            batch_size_low: Min batch size when Metal memory >90% (default 4).
            batch_size_max: Max batch size when Metal memory <50% (P3-1, default 32).
        """
        self.config = ModernBERTConfig(
            model_path=model_path or "nomic-ai/modernbert-embed-base",
            batch_size=batch_size,
            normalize=normalize,
            batch_size_high=batch_size_high,
            batch_size_low=batch_size_low,
            batch_size_max=batch_size_max,
        )
        self._model = None
        self._tokenizer = None
        self._is_loaded = False
        if not lazy_load:
            self._load_model()

    def _load_model(self) -> None:
        """Load model via mlx-embeddings. Raises RuntimeError on failure."""
        if self._is_loaded:
            return
        if not MLX_EMBEDDINGS_AVAILABLE:
            raise RuntimeError("mlx-embeddings not available. Install: pip install mlx-embeddings")
        model_name = str(self.config.model_path)
        logger.info(f"[MODERNBERT] Loading: {model_name}")
        try:
            self._model, self._tokenizer = _ModernBERTMLXLoader.load(model_name)
            self._is_loaded = True
            logger.info("[MODERNBERT] Loaded successfully via mlx-embeddings")
        except Exception as e:
            logger.error(f"[MODERNBERT] Failed to load: {e}")
            raise RuntimeError(f"ModernBERT load failed: {e}") from e

    @property
    def is_loaded(self) -> bool:
        """True if model is loaded and ready."""
        return self._is_loaded

    def encode(self, texts: str | list[str], **kwargs) -> np.ndarray:
        """
        Compatibility adapter: .encode() interface expected by EmbeddingRouter.

        Delegates to embed_batch() (list) or embed() (single string), applying
        the same fail-soft/lazy-load semantics as the underlying methods.
        No model load at import time — deferred to first call.

        Args:
            texts: Single text or list of texts.
            **kwargs: Passed through to embed()/embed_batch().
                Supported: task (default "search_document").
        Returns:
            np.ndarray embedding matrix (N, 768) for list, (768,) for single.
        """
        if isinstance(texts, str):
            return self.embed(texts, **kwargs)
        return self.embed_batch(texts, **kwargs)

    def embed(self, text: str, task: str = "search_document") -> np.ndarray:
        """
        Encode a single text to embedding vector.

        Args:
            text: Text to encode.
            task: Task type — "search_query", "search_document", "clustering", "classification".
                  Applies appropriate prefix for ModernBERT retrieval quality.

        Returns:
            Embedding vector as np.ndarray (768d, float32).
        """
        if not self._is_loaded:
            self._load_model()
        # MEM-UMA-003: bound single-doc input BEFORE tokenizer processes it (prevents memory spike on huge strings)
        if len(text) > MAX_EMBED_DOCUMENT_CHARS:
            text = text[:MAX_EMBED_DOCUMENT_CHARS]
            logger.warning(f"[MODERNBERT] Single text truncated to {MAX_EMBED_DOCUMENT_CHARS} chars")
        prefixed = self._apply_prefix(text, task)
        inputs = self._tokenizer(
            [prefixed], padding=True, truncation=True, max_length=self.config.max_seq_len, return_tensors="mlx"
        )
        with self._metal_context():
            outputs = self._model(input_ids=inputs.input_ids, attention_mask=inputs.attention_mask)
        emb = outputs.text_embeds
        if self.config.normalize:
            norms = mx.linalg.norm(emb, axis=1, keepdims=True)
            emb = emb / mx.clip(norms, a_min=1e-12, a_max=None)
        result = np.array(emb)[0]
        del outputs, emb, inputs
        return result

    def embed_batch(self, texts: list[str], task: str = "search_document") -> np.ndarray:
        """
        Encode a batch of texts to embedding matrix.

        Uses adaptive batch sizing based on Metal memory pressure (Sprint F265D):
        - NORMAL (<80% Metal budget): batch_size_high (default 16)
        - WARNING (80-90%): batch_size (default 8)
        - CRITICAL (>90%): batch_size_low (default 4)

        Args:
            texts: List of texts to encode.
            task: Task type (see embed()).

        Returns:
            Embedding matrix np.ndarray (N, 768), float32.
        """
        if not self._is_loaded:
            self._load_model()
        if not texts:
            return np.array([])

        # MEM-UMA-003: OOM guard — validate batch size, per-doc length, total chars
        if len(texts) > MAX_EMBED_BATCH_SIZE:
            logger.warning(f"[MODERNBERT] Batch size {len(texts)} > {MAX_EMBED_BATCH_SIZE}, truncating")
            texts = texts[:MAX_EMBED_BATCH_SIZE]

        # Truncate individual docs BEFORE tokenization (prevents memory spike during tokenizer() call)
        truncated = False
        for idx, t in enumerate(texts):
            if len(t) > MAX_EMBED_DOCUMENT_CHARS:
                texts[idx] = t[:MAX_EMBED_DOCUMENT_CHARS]
                truncated = True
        if truncated:
            logger.warning(f"[MODERNBERT] Some texts truncated to {MAX_EMBED_DOCUMENT_CHARS} chars")

        total_chars = sum(len(t) for t in texts)
        if total_chars > MAX_EMBED_BATCH_TOTAL_CHARS:
            # Chunk into sub-batches by total char budget
            sub_batches: list[list[str]] = []
            current_chars = 0
            current_batch: list[str] = []
            for t in texts:
                if current_chars + len(t) >= MAX_EMBED_BATCH_TOTAL_CHARS and current_batch:
                    sub_batches.append(current_batch)
                    current_batch = []
                    current_chars = 0
                current_batch.append(t)
                current_chars += len(t)
            if current_batch:
                sub_batches.append(current_batch)
            # Recursively embed sub-batches and stack
            all_embs: list[np.ndarray] = []
            for sub in sub_batches:
                sub_emb = self.embed_batch(sub, task=task)
                all_embs.append(sub_emb)
            return np.vstack(all_embs) if all_embs else np.array([])

        effective_batch_size = self._get_adaptive_batch_size()
        prefixed = [self._apply_prefix(t, task) for t in texts]
        all_embeddings = []
        for i in range(0, len(prefixed), effective_batch_size):
            batch = prefixed[i : i + effective_batch_size]
            inputs = self._tokenizer(
                batch, padding=True, truncation=True, max_length=self.config.max_seq_len, return_tensors="mlx"
            )
            with self._metal_context():
                outputs = self._model(input_ids=inputs.input_ids, attention_mask=inputs.attention_mask)
            emb = outputs.text_embeds
            if self.config.normalize:
                norms = mx.linalg.norm(emb, axis=1, keepdims=True)
                emb = emb / mx.clip(norms, a_min=1e-12, a_max=None)
            all_embeddings.append(np.array(emb))
            del outputs, emb, inputs
        return np.vstack(all_embeddings) if all_embeddings else np.array([])

    def _apply_prefix(self, text: str, task: str) -> str:
        """Apply task prefix to text. Guards against double-prefixing."""
        if task == "search_query":
            prefix = self._SEARCH_QUERY_PREFIX
        elif task == "search_document":
            prefix = self._SEARCH_DOC_PREFIX
        else:
            prefix = ""
        if prefix and (not text.startswith(prefix)):
            return prefix + text
        return text

    def _metal_context(self):
        """Return Metal stream context manager for M1 UMA buffer management."""
        try:
            from hledac.universal.utils.mlx_memory import get_metal_stream_context

            return get_metal_stream_context()
        except ImportError:
            return _NoOpContext()

    def _get_mlx_memory(self):
        """Lazy-load mlx_memory module for adaptive batching (Sprint F265D)."""
        from hledac.universal.utils.mlx_memory import get_mlx_memory_module

        return get_mlx_memory_module()

    def _get_adaptive_batch_size(self) -> int:
        """
        P3-1: Return adaptive batch size based on Metal memory pressure.

        Memory pressure tiers (using mlx_memory.get_mlx_memory_pressure()):
        - ABUNDANT (<50% of MLX budget): batch_size_max (default 32) — P3-1
        - NORMAL (50-80% of budget): batch_size_high (default 16)
        - WARNING (80-90%): batch_size (default 8)
        - CRITICAL (>90%): batch_size_low (default 4)

        Returns:
            Adaptive batch size in range [batch_size_low, batch_size_max].
        """
        mlx_mem = self._get_mlx_memory()
        if mlx_mem is None:
            return self.config.batch_size
        try:
            usage_pct, pressure_level = mlx_mem.get_mlx_memory_pressure()
        except Exception:
            return self.config.batch_size
        if pressure_level == "NORMAL" and usage_pct < 50:
            return self.config.batch_size_max
        if pressure_level == "NORMAL":
            return self.config.batch_size_high
        elif pressure_level == "WARNING":
            return self.config.batch_size
        else:
            return self.config.batch_size_low

    def unload(self) -> None:
        """Explicitly unload model and clear Metal cache."""
        self._model = None
        self._tokenizer = None
        self._is_loaded = False
        if _mlx_available:
            try:
                mx.eval([])
                import gc

                gc.collect()
                if hasattr(mx, "clear_cache"):
                    mx.clear_cache()
                elif hasattr(mx.metal, "clear_cache"):
                    mx.metal.clear_cache()
            except Exception:  # noqa: BLE001
                pass
        logger.info("[MODERNBERT] Unloaded, Metal cache cleared")


class _NoOpContext:
    """No-op context manager when mlx_memory is unavailable."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass
