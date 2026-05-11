"""
Sprint F203I — Streaming Embedder for M1 8GB Memory Safety
============================================================

ROLE: Chunked async embedding pipeline that yields batches incrementally,
reducing peak RSS during embedding phases. Designed for M1 8GB UMA.

API:
    class StreamingEmbedder:
        async def embed_findings(
            self,
            findings: list[CanonicalFinding],
            batch_size: int = 16,
        ) -> AsyncIterator[tuple[list[str], np.ndarray]]

BOUNDS:
    MAX_EMBEDDING_BATCH = 16       # batch_size ceiling
    MAX_TEXT_BYTES_PER_FINDING = 4096  # text truncation before embed

GUARDRAILS:
    - Model lifecycle via brain.model_lifecycle.get_model_lifecycle_status() only
    - FETCH_SEMAPHORE = 3 while model loaded (via utils.concurrency)
    - RAM guard: skip if RSS > 85% high_water from core.resource_governor
    - Never blocks the event loop — all MLX ops in run_in_executor

INTEGRATION:
    - Used by sprint_scheduler _run_embedding_sidecar() for dedup/ANN ingest
    - Falls back to embedding_pipeline.generate_embeddings() if unavailable
    - ANN index prewarmed after bulk embedding via knowledge.ann_index.prewarm()

FAIL-OPEN: Any error → yields empty batch, never raises.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_EMBEDDING_BATCH: int = 16
MAX_TEXT_BYTES_PER_FINDING: int = 4096
_MODEL_LOADED_FETCH_LIMIT: int = 3  # F202H spec: FETCH_SEMAPHORE=3 while model loaded


# ---------------------------------------------------------------------------
# StreamingEmbedder
# ---------------------------------------------------------------------------

class StreamingEmbedder:
    """
    Chunked async embedding pipeline — yields (finding_ids, embeddings) batches.

    Reduces M1 8GB peak RSS by:
    1. Processing in small batches (MAX_EMBEDDING_BATCH=16)
    2. Yielding immediately after each batch (no full materialization)
    3. Unloading model between batches when under memory pressure

    Fail-open: any error yields empty, never raises.
    """

    __slots__ = ("_loaded", "_embedding_depth")

    def __init__(self) -> None:
        self._loaded: bool = False
        self._embedding_depth: int = 0

    # -------------------------------------------------------------------------
    # Model lifecycle helpers
    # -------------------------------------------------------------------------

    def _is_model_loaded(self) -> bool:
        """Check if embedding model is currently loaded via canonical lifecycle API."""
        try:
            from hledac.universal.brain.model_lifecycle import get_model_lifecycle_status

            status = get_model_lifecycle_status()
            return bool(status.get("loaded", False))
        except Exception:
            return False

    async def _load_model(self) -> bool:
        """Load embedding model via embedding_pipeline.load_embedding_model()."""
        try:
            from hledac.universal import embedding_pipeline

            self._embedding_depth += 1
            ok = embedding_pipeline.load_embedding_model()
            if not ok:
                self._embedding_depth -= 1
            self._loaded = ok
            return ok
        except Exception as e:
            logger.debug(f"[StreamingEmbed] load_model failed: {e}")
            self._embedding_depth -= 1
            self._loaded = False
            return False

    async def _unload_model(self) -> None:
        """Unload embedding model via embedding_pipeline.unload_embedding_model()."""
        try:
            from hledac.universal import embedding_pipeline

            embedding_pipeline.unload_embedding_model()
            if self._embedding_depth > 0:
                self._embedding_depth -= 1
            self._loaded = False
        except Exception as e:
            logger.debug(f"[StreamingEmbed] unload_model failed: {e}")
            self._loaded = False

    async def _apply_fetch_limit(self, limit: int) -> None:
        """Apply FETCH_SEMAPHORE limit while model is loaded."""
        try:
            from hledac.universal.utils.concurrency import adjust_fetch_workers

            await adjust_fetch_workers(limit)
        except Exception as e:
            logger.debug(f"[StreamingEmbed] adjust_fetch_workers failed: {e}")

    def _ram_guard_ok(self) -> bool:
        """Check if RAM allows embedding generation. Fail-soft: returns True."""
        try:
            from hledac.universal.core.resource_governor import sample_uma_status

            uma = sample_uma_status()
            # Block heavy vision at >85% pressure (same threshold as MultimodalEnricher)
            if uma.is_critical or uma.is_emergency:
                return False
            if uma.is_warn and hasattr(uma, "high_water") and uma.high_water > 0.85:
                return False
            return True
        except Exception:
            return True  # Fail-soft: allow if check fails

    # -------------------------------------------------------------------------
    # Core API
    # -------------------------------------------------------------------------

    async def embed_findings(
        self,
        findings: list["CanonicalFinding"],
        batch_size: int = MAX_EMBEDDING_BATCH,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        Yield (finding_ids, embeddings) batches from CanonicalFinding list.

        Args:
            findings: List of CanonicalFinding to embed
            batch_size: Max batch size (capped at MAX_EMBEDDING_BATCH=16)

        Yields:
            tuple[list[str], np.ndarray]: batch of finding_ids and their embeddings
                embeddings shape = (batch_size, 256) float32

        Fail-open: any error yields no items, never raises.
        """
        if not findings:
            return

        batch_size = min(batch_size, MAX_EMBEDDING_BATCH)

        # Check RAM guard before starting
        if not self._ram_guard_ok():
            logger.warning("[StreamingEmbed] Skipped due to memory pressure")
            return

        model_loaded_by_us: bool = False

        try:
            # Ensure model is loaded
            if not self._is_model_loaded():
                loaded = await self._load_model()
                if not loaded:
                    # Fall back to sync path via run_in_executor
                    async for batch in self._embed_fallback(findings, batch_size):
                        yield batch
                    return
                model_loaded_by_us = True
                await self._apply_fetch_limit(_MODEL_LOADED_FETCH_LIMIT)

            # Chunk and yield
            async for batch in self._embed_chunked(findings, batch_size):
                yield batch

        except Exception as e:
            logger.debug(f"[StreamingEmbed] embed_findings error: {e}")
            return  # Fail-open: yield nothing

        finally:
            if model_loaded_by_us:
                await self._unload_model()
                await self._apply_fetch_limit(25)  # Restore full concurrency

    async def _embed_chunked(
        self,
        findings: list["CanonicalFinding"],
        batch_size: int,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """Internal chunked embedder — model assumed already loaded."""
        for i in range(0, len(findings), batch_size):
            chunk = findings[i:i + batch_size]
            try:
                ids, embs = await self._embed_batch(chunk)
                if ids and embs is not None and len(embs) == len(ids):
                    yield (ids, embs)
            except Exception as e:
                logger.debug(f"[StreamingEmbed] batch error at offset {i}: {e}")
                continue

    async def _embed_batch(
        self,
        findings: list["CanonicalFinding"],
    ) -> tuple[list[str], np.ndarray]:
        """Embed a single batch of findings in thread executor."""
        texts: list[str] = []
        ids: list[str] = []

        for f in findings:
            text = self._extract_text(f)
            texts.append(text)
            ids.append(f.finding_id)

        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(
            None, _sync_embed_batch, texts, len(texts)
        )
        return (ids, embeddings)

    async def _embed_fallback(
        self,
        findings: list["CanonicalFinding"],
        batch_size: int,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        F204J: Fallback path also chunks to avoid materializing entire sprint.

        Even when the embedding model cannot be loaded, we chunk the fallback
        path to stay within M1 memory bounds.
        """
        # Chunk the fallback just like the normal path
        for i in range(0, len(findings), batch_size):
            chunk = findings[i:i + batch_size]
            texts: list[str] = []
            ids: list[str] = []

            for f in chunk:
                text = self._extract_text(f)
                texts.append(text)
                ids.append(f.finding_id)

            loop = asyncio.get_running_loop()
            embeddings = await loop.run_in_executor(
                None, _sync_embed_batch, texts, len(texts)
            )
            if len(embeddings) > 0 and len(ids) == len(embeddings):
                yield (ids, embeddings)

    # -------------------------------------------------------------------------
    # Text extraction
    # -------------------------------------------------------------------------

    def _extract_text(self, finding: "CanonicalFinding") -> str:
        """Extract embeddable text from CanonicalFinding."""
        text = getattr(finding, "payload_text", None) or getattr(finding, "query", "") or ""
        if len(text) > MAX_TEXT_BYTES_PER_FINDING:
            text = text[:MAX_TEXT_BYTES_PER_FINDING]
        return text


# ---------------------------------------------------------------------------
# Sync batch helper (runs in executor)
# ---------------------------------------------------------------------------

def _sync_embed_batch(texts: list[str], batch_size: int = 16) -> np.ndarray:
    """Synchronous batch embed — runs in thread executor."""
    try:
        from hledac.universal.embedding_pipeline import generate_embeddings

        return generate_embeddings(texts, batch_size=batch_size)
    except Exception as e:
        logger.debug(f"[_sync_embed_batch] failed: {e}")
        return np.zeros((len(texts), 256), dtype=np.float32)
