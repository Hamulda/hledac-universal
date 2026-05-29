"""
brain.modernbert_engine — ModernBERT extractive summarization for P14 pipeline.

Pipeline usage (live_public_pipeline.py:2180-2185):
    modernbert = ModernBertEngine()
    report_text = await modernbert.summarize(context_items)

summarize() replaces hermes_engine.generate_report(query, context_items) for
modernbert-routed P14 calls. Uses extractive summarization via MLX embeddings:
  1. Compute embedding for each context item (search_document prefix)
  2. Cluster/find representative items via cosine similarity
  3. Concatenate top-k items as the "summary"

M1 8GB: model loaded lazily on first call, Metal cache cleared on unload.
MODERNBERT_AVAILABLE flag False if no backend (mlx-embeddings or sentence-transformers).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)

# ── Backend availability flags ────────────────────────────────────────────────

MODERNBERT_AVAILABLE = False
_mlx_embeddings_ok = False

try:
    import mlx.core as mx

    _ = mx.metal.is_available()  # probe Metal availability
    _mlx_embeddings_ok = True
except Exception:
    _mlx_embeddings_ok = False

_sentence_transformers_ok = False
try:
    from sentence_transformers import SentenceTransformer
    _sentence_transformers_ok = True
except ImportError:
    _sentence_transformers_ok = False


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ModernBertConfig:
    """Configuration for ModernBertEngine."""
    # mlx-embeddings primary model (retrieval-tuned)
    mlx_model: str = "nomic-ai/modernbert-embed-base"
    # sentence-transformers fallback
    st_model: str = "nomic-ai/nomic-embed-text-v1.5"
    # Summarization
    summary_top_k: int = 5
    summary_max_chars: int = 3000
    embed_batch_size: int = 8


# ── ModernBertEngine ─────────────────────────────────────────────────────────

class ModernBertEngine:
    """
    Extractive summarization via ModernBERT embeddings.

    Replaces generate_report() for modernbert-routed P14 calls.
    Fail-soft: returns empty string if no backend is available.
    """

    EMBEDDING_DIM = 768

    def __init__(self, config: ModernBertConfig | None = None):
        self.config = config or ModernBertConfig()
        self._manager = None  # MLXEmbeddingManager or SentenceTransformer
        self._loaded = False
        self._backend: str | None = None  # "mlx" | "st"

    # ── Public API ────────────────────────────────────────────────────────────

    async def load(self) -> bool:
        """
        Lazy load — tries mlx-embeddings first, then sentence-transformers fallback.

        Returns:
            True if a backend is loaded and ready.
        """
        global MODERNBERT_AVAILABLE

        if self._loaded:
            return True

        # 1. mlx-embeddings (primary, M1 Metal-accelerated)
        if _mlx_embeddings_ok:
            try:
                from core.mlx_embeddings import MLXEmbeddingManager
                self._manager = MLXEmbeddingManager(lazy_load=True)
                # Trigger lazy load
                if not self._manager.is_loaded:
                    await asyncio.to_thread(self._manager._load_model)
                self._loaded = True
                self._backend = "mlx"
                MODERNBERT_AVAILABLE = True
                logger.info("[ModernBertEngine] Loaded via mlx-embeddings")
                return True
            except Exception as e:
                logger.warning(f"[ModernBertEngine] mlx-embeddings failed: {e}")

        # 2. sentence-transformers (CPU fallback)
        if _sentence_transformers_ok:
            try:
                self._manager = await asyncio.to_thread(
                    SentenceTransformer, self.config.st_model
                )
                self._loaded = True
                self._backend = "st"
                MODERNBERT_AVAILABLE = True
                logger.info("[ModernBertEngine] Loaded via sentence-transformers")
                return True
            except Exception as e:
                logger.warning(f"[ModernBertEngine] sentence-transformers failed: {e}")

        logger.error("[ModernBertEngine] No backend available (mlx-embeddings and sentence-transformers both failed)")
        self._loaded = False
        return False

    async def summarize(self, context_items: list[str]) -> str:
        """
        Extractive summarization of context items via embedding similarity.

        Selects the top-k most central context items (by average pairwise similarity
        to all other items — i.e. cluster centroids) and concatenates them.

        Args:
            context_items: List of context strings (finding payloads, snippets).

        Returns:
            Extractive summary string, or empty string if no backend available.
        """
        if not self._loaded:
            ok = await self.load()
            if not ok:
                return ""

        if not context_items:
            return ""

        try:
            return self._extractive_summary(context_items)
        except Exception as e:
            logger.error(f"[ModernBertEngine] summarize failed: {e}")
            return ""

    async def embed(self, texts: list[str]) -> np.ndarray:
        """
        Batch embed texts to embedding matrix.

        Args:
            texts: List of texts to encode.

        Returns:
            (N, 768) float32 embedding matrix.
        """
        if not self._loaded:
            ok = await self.load()
            if not ok:
                raise RuntimeError("ModernBertEngine: no backend available")

        # Safe: _loaded=True means _manager is set and _backend is set
        manager = self._manager
        backend = self._backend
        if backend == "mlx":
            return manager.encode(texts)
        elif backend == "st":
            return manager.encode(texts)
        else:  # pragma: no cover — defensive
            raise RuntimeError("ModernBertEngine: no backend loaded")

    async def unload(self) -> None:
        """M1 memory: clear model and Metal cache."""
        self._manager = None
        self._loaded = False
        self._backend = None

        if _mlx_embeddings_ok:
            try:
                import mlx.core as mx
                mx.eval([])
                mx.metal.clear_cache()
            except Exception:
                pass

        logger.info("[ModernBertEngine] Unloaded")

    async def is_ready(self) -> bool:
        """True if a backend is loaded."""
        return self._loaded

    # ── Private: extractive summary ────────────────────────────────────────────

    def _extractive_summary(self, items: list[str]) -> str:
        """
        Select top-k centroid items and concatenate as summary.

        Uses cosine similarity: each item's score = mean similarity to all others.
        Top-k by score are joined with "\n---\n" separator.
        """
        top_k = self.config.summary_top_k
        max_chars = self.config.summary_max_chars

        # Truncate items to avoid embedding blow-up
        truncated = [str(item)[:500] for item in items]
        truncated = [t for t in truncated if t]

        if len(truncated) <= top_k:
            selected = truncated
        else:
            # Batch embed
            embeddings = self._embed_sync(truncated)  # (N, 768)

            if embeddings.shape[0] == 0:
                return "\n".join(truncated[:top_k])

            # Pairwise cosine similarity (rows as vectors)
            # sim(i,j) = dot(ei, ej) since L2-normalized
            scores = embeddings @ embeddings.T  # (N, N)
            # Score per item = mean similarity to all others
            # Exclude self-similarity (diagonal)
            n = scores.shape[0]
            mask = np.ones((n, n)) - np.eye(n)
            mean_sim = (scores * mask).sum(axis=1) / (n - 1)

            # Top-k indices
            top_indices = np.argsort(mean_sim)[-top_k:][::-1]
            selected = [truncated[i] for i in top_indices]

        # Concatenate with separator, respect max_chars
        summary_parts = []
        total = 0
        for part in selected:
            if total + len(part) + 5 > max_chars:
                remaining = max_chars - total
                if remaining > 50:
                    summary_parts.append(part[:remaining])
                break
            summary_parts.append(part)
            total += len(part) + 5

        return "\n---\n".join(summary_parts)

    def _embed_sync(self, texts: list[str]) -> np.ndarray:
        """Synchronous embed — dispatches to correct backend."""
        manager = self._manager
        backend = self._backend
        if backend == "mlx":
            return manager.encode(texts)
        elif backend == "st":
            return manager.encode(texts)
        else:  # pragma: no cover — defensive
            raise RuntimeError("ModernBertEngine: no backend")
