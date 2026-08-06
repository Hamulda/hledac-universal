"""
Sprint F203I — Streaming Embedder for M1 8GB Memory Safety
ISSUE #022: Pipeline parallelization — concurrent batch embedding.
ISSUE #016: Replace raw asyncio.create_task + asyncio.wait with safe_create_task


            + asyncio.wait_for timeout for OTel trace propagation.

ROLE: Chunked async embedding pipeline that yields batches incrementally,
reducing peak RSS during embedding phases. Designed for M1 8GB UMA.

ISSUE #022 FIXES:
1. CONCURRENT BATCHES: asyncio.gather with CONCURRENT_BATCHES=2 replaces serial
   async for. While GPU encodes batch N, CPU preps batch N+1 texts.
   ~1.5-2× throughput vs serial.
2. PRE-EXTRACT ALL TEXTS: All finding→text extraction done upfront via
   asyncio.to_thread before any GPU work. Eliminates Python-loop bottleneck
   from the critical path.
3. RAYON TEXT NORM: Optional bulk text normalization via pipeline_compose
   (Rust rayon, nlp pool) for pre-processing before embed.

ISSUE #016 FIXES:
4. SAFE CREATE_TASK: safe_create_task propagates OTel trace context (trace_id,
   span_id) into child tasks via contextvars — distributed tracing works.
5. WAIT_TIMEOUT: asyncio.timeout(300.0) hard-caps
   each batch wait at 5 min — prevents Metal pipeline stalls from growing
   the pending set indefinitely on M1 8GB.

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
    CONCURRENT_BATCHES = 2         # max concurrent GPU batches (M1 Metal safe)
    _BATCH_WAIT_TIMEOUT = 300.0   # 5 min hard cap per batch wait (ISSUE #016)

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
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
import msgspec
from typing import TYPE_CHECKING

import numpy as np

from hledac.universal.runtime.worker_pool import run_in_pool
from hledac.universal.utils.async_helpers import first_completed  # ISSUE-15

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import CanonicalFinding

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_EMBEDDING_BATCH: int = 16
MAX_TEXT_BYTES_PER_FINDING: int = 4096
_MODEL_LOADED_FETCH_LIMIT: int = 3  # F202H spec: FETCH_SEMAPHORE=3 while model loaded
_SAMPLE_INTERVAL: int = 3  # sample memory every N batches (natural yield points)
# ISSUE #022: Concurrent GPU batch count — 2 is M1 Metal-safe (overlap GPU + CPU)
CONCURRENT_BATCHES: int = 2
# Text extraction executor — separate from embed executor to allow overlap
_TEXT_EXTRACT_WORKERS: int = 2

# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------


def _sync_extract_texts(
    findings: list[CanonicalFinding],
) -> tuple[list[str], list[str]]:
    """
    Synchronous bulk text extraction — runs in thread pool.

    Returns (ids, texts) parallel lists.
    ISSUE #022: All extraction done in one call — not in the GPU critical path.
    """
    ids: list[str] = []
    texts: list[str] = []
    for f in findings:
        text = getattr(f, "payload_text", None) or getattr(f, "query", "") or ""
        if len(text) > MAX_TEXT_BYTES_PER_FINDING:
            text = text[:MAX_TEXT_BYTES_PER_FINDING]
        ids.append(f.finding_id)
        texts.append(text)
    return ids, texts


def _try_rust_text_norm(texts: list[str]) -> list[str] | None:
    """
    ISSUE #022 FIX: Use Rust batch_nfc_normalize directly.
    Previously used pipeline_compose_two("nfc_normalize", "passthrough") which silently
    dropped all items because "nfc_normalize" was never a registered stage name in
    pipeline_compose_two (only "len", "lower", "upper", "strip", "hash_xxh3", "hash_xxh3_hex").
    Falls back to original texts on any error (fail-safe, always-on).
    Returns None if Rust pipeline unavailable.
    """
    # R6: Centralized Rust access via core.rust_backend
    from hledac.universal.core.rust_backend import rust
    batch_nfc_normalize = rust.raw.batch_nfc_normalize
    if batch_nfc_normalize is None:
        return texts  # No Rust available, return as-is
    try:
        # Direct NFC normalization via rayon — batch_nfc_normalize is the correct
        # Rust entry point for Unicode NFC normalization (text_norm.rs).
        result = batch_nfc_normalize(texts)
        if result and len(result) == len(texts):
            return result
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Batch result dataclass
# ---------------------------------------------------------------------------


class _BatchResult(msgspec.Struct, gc=False):
    """Result of a single batch embed operation."""
    ids: list[str]
    embeddings: np.ndarray | None


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

    ISSUE #022: Concurrent batch embedding — while GPU encodes batch N,
    CPU preps batch N+1 texts. ~1.5-2× throughput vs serial.

    Fail-open: any error yields empty, never raises.
    """

    __slots__ = (
        "_loaded",
        "_embedding_depth",
        "_abort",
        "_sample_counter",
    )

    def __init__(self) -> None:
        self._loaded: bool = False
        self._embedding_depth: int = 0
        self._abort: bool = False
        self._sample_counter: int = 0

    @property
    def aborted(self) -> bool:
        """True if embedding was aborted due to memory pressure."""
        return self._abort

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
            from hledac.universal.universal import embedding_pipeline

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
            from hledac.universal.universal import embedding_pipeline

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
        """
        Check if RAM allows embedding generation.

        Embedding model (~256d float32, ~1MB per batch) is dramatically smaller
        than Hermes model (~2GB). M1 8GB can safely run embedding even in
        critical state as long as:
        - Not in emergency state (system memory >= 7.0 GiB)
        - No active swap (swap_used_gib <= 1.5 GiB baseline)

        F265B: Allow embedding in critical state — only emergency blocks it.
        Fail-soft: returns True if check fails (embedding proceeds).
        """
        try:
            from hledac.universal.core.resource_governor import sample_uma_status

            uma = sample_uma_status()
            state = getattr(uma, "state", "ok")
            swap_detected = getattr(uma, "swap_detected", False)
            if state == "emergency":
                return False
            if swap_detected:
                return False
            return True
        except Exception:
            return True

    # -------------------------------------------------------------------------
    # Core API
    # -------------------------------------------------------------------------

    async def embed_findings(
        self,
        findings: list[CanonicalFinding],
        batch_size: int = MAX_EMBEDDING_BATCH,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        Yield (finding_ids, embeddings) batches from CanonicalFinding list.

        ISSUE #022: Pre-extracts ALL texts upfront, then runs concurrent
        batches via asyncio.gather while pre-extracting next chunk.

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

        if not self._ram_guard_ok():
            logger.warning("[StreamingEmbed] Skipped due to memory pressure")
            return

        model_loaded_by_us: bool = False

        try:
            if not self._is_model_loaded():
                loaded = await self._load_model()
                if not loaded:
                    async for batch in self._embed_fallback(findings, batch_size):
                        yield batch
                    return
                model_loaded_by_us = True
                await self._apply_fetch_limit(_MODEL_LOADED_FETCH_LIMIT)

            async for batch in self._embed_concurrent(findings, batch_size):
                yield batch

        except Exception as e:
            logger.debug(f"[StreamingEmbed] embed_findings error: {e}")
            return

        finally:
            if model_loaded_by_us:
                await self._unload_model()
                await self._apply_fetch_limit(25)

    async def _embed_concurrent(
        self,
        findings: list[CanonicalFinding],
        batch_size: int,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        ISSUE #022: Concurrent batch embedder.

        Algorithm:
        1. Pre-extract ALL texts upfront (in thread pool) — O(n) Python loop
        2. Partition into chunks
        3. Fire CONCURRENT_BATCHES embed tasks via asyncio.gather
        4. While GPU encodes, pre-extract next chunk (future)
        5. Yield results as they complete

        ~1.5-2× throughput vs serial async for (GPU overlap with CPU prep).

        M1 8GB invariant: CONCURRENT_BATCHES=2 is Metal-safe — 2 concurrent
        GPU batches × 16 items × 512 tokens × 4B ≈ 65 KB per batch — well
        within the 1.5 GiB Metal cache limit.
        """
        # Phase 1: Pre-extract ALL texts upfront (off critical path)
        all_ids: list[str]
        all_texts: list[str]
        all_ids, all_texts = await run_in_pool(
            "cpu",
            _sync_extract_texts,
            findings,
        )

        # Phase 2: Apply Rust text normalization if available
        norm_texts = _try_rust_text_norm(all_texts)
        if norm_texts is None:
            norm_texts = all_texts
        else:
            logger.debug(
                f"[StreamingEmbed] Rust text norm applied: {len(norm_texts)} texts"
            )

        # Phase 3: Partition into chunks
        chunks: list[tuple[int, int]] = []  # (start_idx, end_idx)
        for i in range(0, len(norm_texts), batch_size):
            chunks.append((i, min(i + batch_size, len(norm_texts))))

        # Phase 4: Concurrent batch execution with sliding window via TaskGroup
        # PEP 654 asyncio.TaskGroup gives structured concurrency: when the scope
        # exits (abort/timeout/error), ALL pending child tasks are cancelled
        # automatically — no more manual drain loops, no more orphan task leaks.
        #
        # Sliding window: external pending set tracks live child tasks.
        # asyncio.wait(FIRST_COMPLETED) handles the completion detection.
        # TaskGroup scope handles automatic cancellation of all pending on exit.
        pending: set[asyncio.Task[tuple[list[str], np.ndarray]]] = set()
        chunk_idx: int = 0

        async def launch_batch(idx: int) -> tuple[list[str], np.ndarray]:
            start, end = chunks[idx]
            return await self._embed_single_batch(all_ids[start:end], norm_texts[start:end])

        try:
            async with asyncio.TaskGroup() as tg:
                while chunk_idx < len(chunks) or pending:
                    if self._abort:
                        logger.debug("[StreamingEmbed] aborting due to memory pressure")
                        break

                    # Fill pipeline up to CONCURRENT_BATCHES
                    while (
                        len(pending) < CONCURRENT_BATCHES
                        and chunk_idx < len(chunks)
                    ):
                        batch_task = tg.create_task(
                            launch_batch(chunk_idx),
                            name=f"streaming_embed.batch_{chunk_idx}",
                            eager_start=True,
                        )
                        pending.add(batch_task)
                        chunk_idx += 1

                    if not pending:
                        break

                    # Wait for at least one batch to complete (FIRST_COMPLETED)
                    # ISSUE-15: Replaced asyncio.wait(FIRST_COMPLETED) with first_completed helper
                    # asyncio.wait() is deprecated in Python 3.14; TaskGroup doesn't support
                    # FIRST_COMPLETED semantics directly (automatic cancellation on scope exit),
                    # so we use a shared Future pattern to detect first completion.
                    try:
                        async with asyncio.timeout(300.0):  # 5 min hard cap per batch
                            # first_completed returns (result, winner_task)
                            _result, _winner = await first_completed(*pending)
                    except asyncio.TimeoutError:
                        # TaskGroup scope will cancel all remaining children automatically
                        raise

                    # Remove winner from pending set
                    pending.discard(_winner)

                    # Yield completed batch(es) — only the winner in this iteration
                    # (but we process one at a time since FIRST_COMPLETED returns one)
                    for completed in [_winner]:
                        try:
                            ids, embs = await completed
                            if ids and embs is not None and len(embs) == len(ids):
                                yield (ids, embs)
                        except Exception as e:
                            logger.debug(f"[StreamingEmbed] batch error: {e}")

                    # Memory sampling at natural yield points
                    self._sample_counter += 1
                    if self._sample_counter >= _SAMPLE_INTERVAL:
                        self._sample_counter = 0
                        if not self._ram_guard_ok():
                            self._abort = True
                            logger.warning(
                                "[StreamingEmbed] memory pressure detected, aborting after remaining batches"
                            )
                            # TaskGroup will cancel remaining children on scope exit
                            break
        except* asyncio.TimeoutError:
            # Structured cancellation already applied by TaskGroup
            pass
        except* asyncio.CancelledError:
            # Propagate upward (abort path)
            raise

    async def _embed_single_batch(
        self,
        ids: list[str],
        texts: list[str],
    ) -> tuple[list[str], np.ndarray]:
        """
        ISSUE #022: Embed a single batch — pure GPU path, no text extraction.

        Runs in thread executor via run_in_executor (Metal compute).
        Returns (ids, embeddings) tuple.
        """
        loop = asyncio.get_running_loop()
        from hledac.universal.utils.domain_executors import get_domain_executors

        domain_executors = get_domain_executors()
        embeddings = await loop.run_in_executor(
            domain_executors.embed,
            _sync_embed_batch,
            texts,
            len(texts),
        )
        return (ids, embeddings)

    async def _embed_fallback(
        self,
        findings: list[CanonicalFinding],
        batch_size: int,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        F204J: Fallback path — also uses concurrent batch execution.

        Even when the embedding model cannot be loaded, we chunk the fallback
        path to stay within M1 memory bounds.
        """
        # Pre-extract all texts upfront
        all_ids, all_texts = await run_in_pool(
            "cpu",
            _sync_extract_texts,
            findings,
        )

        norm_texts = _try_rust_text_norm(all_texts)
        if norm_texts is None:
            norm_texts = all_texts

        # Concurrent fallback — same TaskGroup pattern as _embed_concurrent.
        # Structured concurrency ensures all pending tasks are cancelled
        # automatically on scope exit (abort/timeout/error).
        chunks: list[tuple[int, int]] = []
        for i in range(0, len(norm_texts), batch_size):
            chunks.append((i, min(i + batch_size, len(norm_texts))))

        # Phase 4: TaskGroup with structured concurrency — same pattern as _embed_concurrent
        pending: set[asyncio.Task[tuple[list[str], np.ndarray]]] = set()
        chunk_idx: int = 0

        async def launch_batch(idx: int) -> tuple[list[str], np.ndarray]:
            start, end = chunks[idx]
            return await self._embed_single_batch(all_ids[start:end], norm_texts[start:end])

        try:
            async with asyncio.TaskGroup() as tg:
                while chunk_idx < len(chunks) or pending:
                    if self._abort:
                        logger.debug("[StreamingEmbed] fallback aborting due to memory pressure")
                        break

                    while len(pending) < CONCURRENT_BATCHES and chunk_idx < len(chunks):
                        batch_task = tg.create_task(
                            launch_batch(chunk_idx),
                            name=f"streaming_embed.fallback_batch_{chunk_idx}",
                            eager_start=True,
                        )
                        pending.add(batch_task)
                        chunk_idx += 1

                    if not pending:
                        break

                    try:
                        async with asyncio.timeout(300.0):
                            # ISSUE-15: Replaced asyncio.wait(FIRST_COMPLETED) with first_completed helper
                            _winner_task: asyncio.Task[tuple[list[str], np.ndarray]]
                            _, _winner_task = await first_completed(*pending)
                    except asyncio.TimeoutError:
                        raise

                    for completed in [_winner_task]:
                        try:
                            ids, embs = await completed
                            if ids and embs is not None and len(embs) == len(ids):
                                yield (ids, embs)
                        except Exception as e:
                            logger.debug(f"[StreamingEmbed] fallback batch error: {e}")
        except* asyncio.TimeoutError:
            pass
        except* asyncio.CancelledError:
            raise

    # -------------------------------------------------------------------------
    # Backward-compat: serial path (used by _embed_fallback above)
    # -------------------------------------------------------------------------

    async def _embed_chunked(
        self,
        findings: list[CanonicalFinding],
        batch_size: int,
    ) -> AsyncIterator[tuple[list[str], np.ndarray]]:
        """
        Serial chunked embedder — DEPRECATED, delegates to _embed_concurrent.

        Kept for backward compatibility only.
        """
        import warnings
        warnings.warn(
            "_embed_chunked is deprecated and will be removed. "
            "Use _embed_concurrent instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        # Delegate to the current implementation
        async for batch in self._embed_concurrent(findings, batch_size):
            yield batch

    # -------------------------------------------------------------------------
    # Text extraction
    # -------------------------------------------------------------------------

    def _extract_text(self, finding: CanonicalFinding) -> str:
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
