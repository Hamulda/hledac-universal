"""
MLX-based Embedding Manager
===========================

Nahrazuje sentence-transformers ModernBERT modelem přes MLX.
Výhody:
- Rychlejší inference na Apple Silicon
- 4-bit kvantizace = nižší paměťová náročnost
- ModernBERT = lepší kvalita než MiniLM

Použití:
    from hledac.core.mlx_embeddings import MLXEmbeddingManager
    
    manager = MLXEmbeddingManager()
    embeddings = manager.encode(["text 1", "text 2"])
"""

from __future__ import annotations

import logging
import warnings
from pathlib import Path
from typing import List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

# MLX importy
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    warnings.warn("MLX not available. Install: pip install mlx>=0.15.0")

# mlx-embeddings for ModernBERT (works, mlx-lm.load does NOT support ModernBERT)
try:
    from mlx_embeddings import load as mlx_embeddings_load
    MLX_EMBEDDINGS_AVAILABLE = True
except ImportError:
    MLX_EMBEDDINGS_AVAILABLE = False
    warnings.warn("mlx-embeddings not available. Install: pip install mlx-embeddings")


# === Embedding Task Enum (prefix discipline) ===
from enum import Enum


class EmbeddingTask(Enum):
    """Embedding task types for ModernBERT prefix discipline."""

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

    # Idempotence: do not apply twice
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

    DEFAULT_MODEL = "nomic-ai/modernbert-embed-base"  # Retrieval-tuned, NOT fill-mask
    EMBEDDING_DIM = 768  # ModernBERT-base dimenze
    MRL_DIM = 256  # Matryoshka Representation Learning dimension
    MAX_LENGTH = 512
    SUPPORTS_TASK_PREFIX = True  # ModernBERT supports search_query/search_document prefixes

    # Task safety: track current embedding task
    _current_task: Optional[EmbeddingTask] = None

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        lazy_load: bool = True
    ):
        """
        Inicializace embedding manageru.

        Args:
            model_path: Cesta k modelu (default: ModernBERT)
            lazy_load: Načíst model až při prvním použití
        """
        if not MLX_AVAILABLE:
            raise RuntimeError(
                "MLX not available. Install: pip install mlx>=0.15.0 mlx-lm>=0.4.0"
            )

        self.model_path = Path(model_path) if model_path else Path(self.DEFAULT_MODEL)
        self._model = None
        self._tokenizer = None
        self._is_loaded = False

        if not lazy_load:
            self._load_model()

        logger.info(f"MLXEmbeddingManager initialized: {self.model_path}")
    
    def _load_model(self) -> None:
        """Načte ModernBERT model přes mlx-embeddings."""
        if self._is_loaded:
            return

        model_name = str(self.model_path)
        logger.info(f"Loading embedding model: {model_name}")

        if not MLX_EMBEDDINGS_AVAILABLE:
            raise RuntimeError(
                "mlx-embeddings not available. Install: pip install mlx-embeddings"
            )

        try:
            # Načtení přes mlx-embeddings (mlx_lm.load does NOT support ModernBERT)
            self._model, self._processor = mlx_embeddings_load(model_name, lazy=False)
            self._tokenizer = self._processor._tokenizer
            self._is_loaded = True

            logger.info("✅ Embedding model loaded successfully via mlx-embeddings")

        except Exception as e:
            logger.error(f"Failed to load embedding model: {e}")
            raise
    
    @property
    def is_loaded(self) -> bool:
        """Vrátí True pokud je model načten."""
        return self._is_loaded

    @property
    def supports_task_prefix(self) -> bool:
        """Vrátí True pokud provider podporuje task prefixy (ModernBERT ano, FastEmbed ne)."""
        return self.SUPPORTS_TASK_PREFIX

    # === Task-aware embedding methods (prefix discipline) ===

    def embed_query(self, text: str, truncate_dim: Optional[int] = None) -> np.ndarray:
        """Embed user query (asymmetric - search_query prefix)."""
        return self._embed_task(text, EmbeddingTask.SEARCH_QUERY, truncate_dim)

    def embed_document(self, text: str, truncate_dim: Optional[int] = None) -> np.ndarray:
        """Embed document for indexing (asymmetric - search_document prefix)."""
        return self._embed_task(text, EmbeddingTask.SEARCH_DOCUMENT, truncate_dim)

    def embed_for_clustering(self, text: str, truncate_dim: Optional[int] = None) -> np.ndarray:
        """Embed text for clustering (symmetric - clustering prefix)."""
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim)

    def embed_for_dedup(self, text: str, truncate_dim: Optional[int] = None) -> np.ndarray:
        """Embed text for deduplication (symmetric - clustering task)."""
        # Deduplication is symmetric text-vs-text comparison, not asymmetric query-document
        # CLUSTERING is the correct task, not CLASSIFICATION
        return self._embed_task(text, EmbeddingTask.CLUSTERING, truncate_dim, force_normalize=True)

    def _embed_for_indexing(self, texts: Union[str, List[str]], truncate_dim: Optional[int] = None) -> np.ndarray:
        """
        Internal method for batch document embedding (used by LanceDB store for indexing).

        This wraps embed_document to ensure task safety for indexing operations.
        """
        if isinstance(texts, str):
            texts = [texts]

        # Use embed_document for each text in batch
        results = [self.embed_document(t, truncate_dim=truncate_dim) for t in texts]
        return np.vstack(results) if results else np.array([])

    def _embed_task(
        self,
        text: str,
        task: EmbeddingTask,
        truncate_dim: Optional[int] = None,
        force_normalize: bool = False
    ) -> np.ndarray:
        """
        Internal task-aware embed method.

        Applies prefix only if provider supports it.
        Prefix is applied ONLY during embedding, never stored in DB.
        """
        # Task safety: set current task before encoding
        self._current_task = task
        self._log_task(task)

        # Apply prefix only if provider supports it
        if self.supports_task_prefix:
            text = apply_task_prefix(text, task)

        # Normalization: forced or based on task rule
        normalize = force_normalize or should_normalize(task)

        # Use existing encode (pass _for_indexing only for documents)
        for_indexing = task == EmbeddingTask.SEARCH_DOCUMENT
        try:
            result = self.encode(
                text,
                normalize=normalize,
                truncate_dim=truncate_dim or self.MRL_DIM,
                _for_indexing=for_indexing
            )
        finally:
            # Clear task after encoding
            self._current_task = None

        return result

    def _log_task(self, task: EmbeddingTask) -> None:
        """Log task on first occurrence for runtime truth."""
        global _task_logged
        if not _task_logged:
            logger.info(f"[EMBEDDER] task={task.value}")
            _task_logged = True

    def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: int = 32,
        normalize: bool = True,
        show_progress: bool = False,
        truncate_dim: Optional[int] = None,  # Matryoshka truncation
        _for_indexing: bool = False  # Internal flag for indexing validation
    ) -> np.ndarray:
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
        # Task safety guard for indexing
        if _for_indexing and self._current_task != EmbeddingTask.SEARCH_DOCUMENT:
            raise RuntimeError(
                f"Attempt to index non-document embedding. "
                f"Current task: {self._current_task}. "
                f"Use embed_document() for indexing."
            )

        # Zajistit načtení modelu
        if not self._is_loaded:
            self._load_model()

        # Normalizace vstupu
        if isinstance(texts, str):
            texts = [texts]

        if not texts:
            return np.array([])

        # Zpracování po batchích
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]

            if show_progress:
                logger.info(f"Encoding batch {i//batch_size + 1}/{(len(texts)-1)//batch_size + 1}")

            # Tokenizace přes mlx-embeddings processor
            inputs = self._tokenizer(
                batch,
                padding=True,
                truncation=True,
                max_length=self.MAX_LENGTH,
                return_tensors="mlx"
            )

            # Forward pass - mlx-embeddings returns pooled text_embeds
            # B4: scope Metal buffers to with-block for immediate release on UMA.
            # F219L: single-source guard via mlx_memory helper
            from hledac.universal.utils.mlx_memory import get_metal_stream_context
            with get_metal_stream_context():
                outputs = self._model(
                    input_ids=inputs.input_ids,
                    attention_mask=inputs.attention_mask
                )

            # Use pre-pooled embeddings from the model (includes attention mask pooling internally)
            embeddings = outputs.text_embeds

            # Matryoshka truncation: slice BEFORE normalization
            if truncate_dim and truncate_dim < self.EMBEDDING_DIM:
                embeddings = embeddings[:, :truncate_dim]

            # L2 normalization
            if normalize:
                norms = mx.linalg.norm(embeddings, axis=1, keepdims=True)
                embeddings = embeddings / mx.clip(norms, a_min=1e-12, a_max=None)

            # B4: Convert to numpy THEN release MLX refs while still in scope.
            # This ensures Metal buffers are freed before next batch on UMA.
            embeddings_np = np.array(embeddings)

            # Release large MLX objects immediately — reduces peak on M1 8GB.
            # These may share backing storage with the GPU buffer, so del is critical.
            del outputs
            del embeddings
            del inputs

            all_embeddings.append(embeddings_np)
        
        # Spojení všech batchů
        return np.vstack(all_embeddings)
    
    def _mean_pooling(
        self,
        token_embeddings: mx.array,
        attention_mask: mx.array
    ) -> mx.array:
        """
        Mean pooling s ohledem na attention mask.
        
        Args:
            token_embeddings: Vstupní embeddngy tvaru (batch, seq_len, hidden)
            attention_mask: Attention mask tvaru (batch, seq_len)
            
        Returns:
            Pooled embeddings tvaru (batch, hidden)
        """
        # Expand mask pro broadcasting
        mask_expanded = mx.expand_dims(attention_mask, -1)
        mask_expanded = mx.broadcast_to(mask_expanded, token_embeddings.shape)
        
        # Masked sum
        sum_embeddings = mx.sum(token_embeddings * mask_expanded, axis=1)
        
        # Sum mask (počet platných tokenů)
        sum_mask = mx.clip(mx.sum(attention_mask, axis=1, keepdims=True), a_min=1e-9)
        
        # Mean
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
    
    def similarity(
        self,
        text1: Union[str, List[str]],
        text2: Union[str, List[str]]
    ) -> Union[float, np.ndarray]:
        """
        Vypočítá kosinovou podobnost mezi texty.
        
        Args:
            text1: První text nebo seznam textů
            text2: Druhý text nebo seznam textů
            
        Returns:
            Podobnost skóre (0-1)
        """
        # Zakódování
        emb1 = self.encode(text1, normalize=True)
        emb2 = self.encode(text2, normalize=True)
        
        # Kosinová podobnost (pro normalizované vektory = dot product)
        if emb1.ndim == 1:
            emb1 = emb1.reshape(1, -1)
        if emb2.ndim == 1:
            emb2 = emb2.reshape(1, -1)
        
        similarity = np.dot(emb1, emb2.T)
        
        # Vrátit skalár pokud jeden vstup
        if similarity.shape == (1, 1):
            return float(similarity[0, 0])
        
        return similarity
    
    def unload(self) -> None:
        """Uvolní model z paměti."""
        if self._is_loaded:
            logger.info("Unloading embedding model")
            self._model = None
            self._tokenizer = None
            self._is_loaded = False

            import gc
            gc.collect()

            # M218A: Force Metal to release buffers — mx.eval() flushes pending ops,
            # clear_cache() releases GPU memory back to the OS. Fail-soft if MLX
            # is unavailable or clear_cache raises.
            if MLX_AVAILABLE:
                try:
                    import mlx.core as mx
                    mx.eval([])
                    try:
                        mx.metal.clear_cache()
                    except Exception as exc:
                        logger.debug(f"mx.metal.clear_cache() raised (non-fatal): {exc}")
                except Exception as exc:
                    logger.debug(f"MLX eval during unload raised (non-fatal): {exc}")
    
    def get_info(self) -> dict:
        """Vrátí informace o manageru."""
        return {
            "model_path": str(self.model_path),
            "is_loaded": self._is_loaded,
            "embedding_dim": self.EMBEDDING_DIM,
            "max_length": self.MAX_LENGTH,
            "mlx_available": MLX_AVAILABLE,
        }


# Singleton pro celou aplikaci
_default_manager: Optional[MLXEmbeddingManager] = None
_init_logged: bool = False
_task_logged: bool = False


def get_embedding_manager() -> MLXEmbeddingManager:
    """Vrátí globální instanci embedding manageru."""
    global _default_manager, _init_logged, _task_logged
    if _default_manager is None:
        _default_manager = MLXEmbeddingManager(lazy_load=True)

    # Loud runtime truth logging on first encode
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


def get_embedding_info() -> dict:
    """Vrátí info o aktuálním embedding provideru."""
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


class EmbeddingDimensionError(Exception):
    """Raised when embedding dimension mismatch is detected."""
    pass


def assert_embedding_dimension(expected_dim: int, context: str = "") -> None:
    """
    Verify that current embedding dimension matches expected dimension.

    Args:
        expected_dim: Expected dimension (256, 384, 768)
        context: Context string for error message

    Raises:
        EmbeddingDimensionError: If dimension doesn't match
    """
    global _default_manager
    if _default_manager is None:
        raise EmbeddingDimensionError(
            f"Embedding provider not initialized. Cannot verify dimension {expected_dim}. "
            f"Context: {context}"
        )

    actual_dim = _default_manager.EMBEDDING_DIM
    if expected_dim not in (256, 384, 768):
        raise EmbeddingDimensionError(
            f"Invalid expected_dim {expected_dim}. Must be 256, 384, or 768. Context: {context}"
        )

    if actual_dim != expected_dim:
        raise EmbeddingDimensionError(
            f"Embedding dimension mismatch: expected {expected_dim}, got {actual_dim}. "
            f"Model: {_default_manager.model_path}. Context: {context}. "
            f"Set HLEDAC_RESET_EMBEDDING_CACHE=1 to force reset."
        )


def encode_texts(texts: Union[str, List[str]], **kwargs) -> np.ndarray:
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


def compute_similarity(text1: str, text2: str) -> float:
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


if __name__ == "__main__":
    # Test
    logging.basicConfig(level=logging.INFO)
    
    print("Testing MLX Embedding Manager...")
    
    manager = MLXEmbeddingManager()
    
    test_texts = [
        "Machine learning is fascinating",
        "Deep learning transforms AI",
        "The weather is nice today"
    ]
    
    print(f"\nEncoding {len(test_texts)} texts...")
    embeddings = manager.encode(test_texts)
    
    print(f"Shape: {embeddings.shape}")
    print(f"Sample (first 5 dims of first text): {embeddings[0, :5]}")
    
    # Test similarity
    print(f"\nSimilarity matrix:")
    sim = manager.similarity(test_texts, test_texts)
    print(sim)
