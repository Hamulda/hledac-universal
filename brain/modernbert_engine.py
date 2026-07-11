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
MLX-only: no CPU fallback, no sentence-transformers.

ENVIRONMENT REQUIREMENT: Must run via `uv run python` to use the correct
.venv interpreter with mlx-embeddings installed. Direct `python3` may use
system interpreter lacking mlx-embeddings.
"""



import asyncio
import logging
import sys
from dataclasses import dataclass
import msgspec

import numpy as np

logger = logging.getLogger(__name__)

# ── MLX availability (lazy — no top-level import) ─────────────────────────────

MODERNBERT_AVAILABLE = False
_mlx_embeddings_ok = False

try:
    import mlx.core as mx

    _ = mx.metal.is_available()  # probe Metal availability
    _mlx_embeddings_ok = True
except Exception:
    _mlx_embeddings_ok = False


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class ModernBertConfig:
    """Configuration for ModernBertEngine."""
    # mlx-embeddings model (retrieval-tuned)
    mlx_model: str = "nomic-ai/modernbert-embed-base"
    # Summarization
    summary_top_k: int = 5
    summary_max_chars: int = 3000
    embed_batch_size: int = 8


# ── ModernBertEngine ─────────────────────────────────────────────────────────

class ModernBertEngine:
    """
    Extractive summarization via ModernBERT embeddings (MLX-only).

    Replaces generate_report() for modernbert-routed P14 calls.
    Fail-soft: returns empty string if MLX backend is unavailable.
    """

    __slots__ = ('config', '_manager', '_loaded')

    EMBEDDING_DIM = 768

    def __init__(self, config: ModernBertConfig | None = None):
        self.config = config or ModernBertConfig()
        self._manager = None  # MLXEmbeddingManager
        self._loaded = False

    # ── Public API ────────────────────────────────────────────────────────────

    async def load(self) -> bool:
        """
        Lazy load MLX embedding backend.

        Returns:
            True if backend is loaded and ready.
        """
        global MODERNBERT_AVAILABLE

        if self._loaded:
            return True

        if not _mlx_embeddings_ok:
            # Actionable diagnostic message
            logger.error(
                "[ModernBertEngine] MLX backend unavailable.\n"
                "  Likely cause: running via `python3` instead of `uv run python`.\n"
                f"  sys.executable: {sys.executable!r}\n"
                "  Fix: use `uv run python -m hledac.universal ...`\n"
                "  Verify: `uv run python -c 'import mlx.core; print(mlx.core.__version__)'`"
            )
            MODERNBERT_AVAILABLE = False
            return False

        try:
            from compat.core_mlx_embeddings import get_mlx_embedder
            self._manager = get_mlx_embedder()
            if not self._manager.is_loaded:
                await asyncio.to_thread(self._manager._load_model)
            self._loaded = True
            MODERNBERT_AVAILABLE = True
            logger.info("[ModernBertEngine] MLX backend loaded")
            return True
        except Exception as e:
            logger.error(
                f"[ModernBertEngine] MLX load failed: {e}\n"
                f"  sys.executable: {sys.executable!r}\n"
                "  Verify mlx-embeddings: `uv run python -c 'from mlx_embeddings import load; print(\"OK\")'`"
            )
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
                raise RuntimeError("ModernBertEngine: MLX backend unavailable")

        assert self._manager is not None
        return self._manager.encode(texts)

    async def unload(self) -> None:
        """M1 memory: clear Metal cache.

        Does NOT unload the singleton MLXEmbeddingManager — other callers
        may still hold references. Only marks this instance as not-loaded.
        """
        self._loaded = False
        # Keep self._manager (shared singleton) — do NOT set to None

        if _mlx_embeddings_ok:
            try:
                import mlx.core as mx
                mx.eval([])  # barrier: flush GPU queue BEFORE Python GC
                import gc
                gc.collect()  # collect Python refs that held MLX objects
                if hasattr(mx, "clear_cache"):
                    mx.clear_cache()
                elif hasattr(mx.metal, "clear_cache"):
                    mx.metal.clear_cache()
            except Exception:  # noqa: BLE001
                pass

        logger.info("[ModernBertEngine] Unloaded")

    async def is_ready(self) -> bool:
        """True if a backend is loaded."""
        return self._loaded

    # ── P0-3: Pivot similarity scoring via embeddings ─────────────────────────

    async def score_pivots_by_similarity(
        self,
        pivot_candidates: list[dict],
        finding_texts: list[str],
        top_k: int = 10,
    ) -> list[tuple[dict, float]]:
        """
        Rank pivot candidates by cosine similarity to finding embeddings.

        Args:
            pivot_candidates: List of dicts with at least 'query' or 'pivot_type' key.
            finding_texts: List of finding description strings to embed as reference.
            top_k: Maximum number of pivots to return.

        Returns:
            List of (pivot_dict, similarity_score) tuples sorted by descending score.
            Fails soft: returns empty list on any error.
        """
        if not pivot_candidates or not finding_texts:
            return []

        try:
            # Truncate for embedding efficiency
            finding_emb = self._embed_sync([str(f)[:500] for f in finding_texts if f])
            if finding_emb.shape[0] == 0:
                return []


            # Compute centroid of findings (mean embedding)
            finding_centroid = finding_emb.mean(axis=0, keepdims=True)  # (1, 768)
            # L2-normalize for cosine similarity
            f_norm = finding_centroid / (np.linalg.norm(finding_centroid, axis=1, keepdims=True) + 1e-8)


            # Embed pivot texts
            pivot_texts = []
            for p in pivot_candidates:
                txt = p.get("query") or p.get("pivot_type") or p.get("reason") or str(p)
                pivot_texts.append(str(txt)[:200])

            if not pivot_texts:
                return []

            pivot_emb = self._embed_sync(pivot_texts)  # (M, 768)
            if pivot_emb.shape[0] == 0:
                return []

            # Cosine similarity: each pivot vs finding centroid
            p_norm = pivot_emb / (np.linalg.norm(pivot_emb, axis=1, keepdims=True) + 1e-8)
            sims = (p_norm @ f_norm.T).flatten()  # (M,)

            # Pair with pivots, sort descending
            scored = sorted(zip(pivot_candidates, sims, strict=True), key=lambda x: x[1], reverse=True)
            return scored[:top_k]
        except Exception as e:
            logger.debug("[ModernBertEngine] score_pivots_by_similarity failed: %s", e)
            return []

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
        """Synchronous embed via MLX backend."""
        if self._manager is None:
            raise RuntimeError("ModernBertEngine: MLX backend not loaded")
        return self._manager.encode(texts)
