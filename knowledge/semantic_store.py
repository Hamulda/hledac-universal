"""
Sprint 8SB — SemanticStore: FastEmbed + LanceDB Semantic IOC Search
Sprint F228B: CoreML/ANE embedder as preferred backend.

Singleton lifecycle — initialize() v BOOT, close() v TEARDOWN.
ROLE: Consumer/Enrichment (NOT backend owner, NOT grounding authority)

FastEmbed BAAI/bge-small-en-v1.5 ONNX model (dim=384, ~33MB, CoreML-friendly).
LanceDB ANN index pod ~/.hledac/lancedb/ — append mode, nikdy drop+recreate.

ANE path (preferred): CoreMLEmbedder → CoreML (.mlmodelc) → ANE
CPU fallback: FastEmbed TextEmbedding (onnxruntime)
Hash fallback: always works, zero RAM.

NENÍ owner backend storage → persistent_layer (depr!)
NENÍ owner embedding computation → MLXEmbeddingManager singleton
NENÍ owner primary retrieval → rag_engine
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    import lancedb


logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────────
_EMBED_DIM = 384
_MAX_PENDING = 2000  # Bounded pending buffer
_MAX_TEXT_LEN = 4096
_TABLE_NAME = "semantic_ioc_v1"

# Sprint F228B: CPU executor for embed (never block event loop)
CPU_EXECUTOR = asyncio.Semaphore(1)

# ── CoreML/ANE availability ────────────────────────────────────────────────────
try:
    from hledac.universal.brain.coreml_embedder import (
        ANE_AVAILABLE as _COREML_ANE_AVAILABLE,
    )
    from hledac.universal.brain.coreml_embedder import (
        CoreMLEmbedder,
        get_coreml_embedder,
    )

    _COREML_AVAILABLE = True
except ImportError:
    _COREML_AVAILABLE = False
    _COREML_ANE_AVAILABLE = False
    CoreMLEmbedder = None
    get_coreml_embedder = None


class SemanticStore:
    """
    FastEmbed + LanceDB pro sémantické vyhledávání findings.

    ANE path (F228B): CoreMLEmbedder.embed() → CoreML → ANE (preferred)
    CPU fallback: self._model.embed() — FastEmbed TextEmbedding
    Hash fallback: always works.

    Lifecycle:
        await store.initialize()  # BOOT — load model + open LanceDB
        await store.add_text(...)   # Buffer
        await store.flush()         # Batch embed + LanceDB upsert
        await store.semantic_pivot(...)  # ANN search
        await store.close()        # TEARDOWN
    """

    # -------------------------------------------------------------------------
    # Fields
    # -------------------------------------------------------------------------
    __slots__ = (
        "_db_path",
        "_db",
        "_table",
        "_model",
        "_coreml_embedder",
        "_pending_texts",
        "_pending_meta",
        "_embed_dim",
        "_initialized",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: lancedb.LanceDBConnection | None = None  # lancedb.LanceDBConnection
        self._table: lancedb.Table | None = None  # lancedb.Table
        self._model: Any = None  # FastEmbed TextEmbedding
        # Sprint F228B: CoreML/ANE embedder — preferred when ANE is available
        self._coreml_embedder: CoreMLEmbedder | None = (
            get_coreml_embedder() if _COREML_AVAILABLE else None
        )
        self._pending_texts: deque = deque()
        self._pending_meta: deque = deque()
        self._embed_dim: int = _EMBED_DIM
        self._initialized: bool = False

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """BOOT — load FastEmbed model + open LanceDB conn."""
        if self._initialized:
            return

        asyncio.get_running_loop()

        # Sprint F228B: Try CoreMLEmbedder ANE path first
        if self._coreml_embedder is not None:
            try:
                await self._coreml_embedder.load()
                logger.info(
                    "[SEMSTORE] CoreMLEmbedder loaded (ANE path=%s, model=%s)",
                    _COREML_ANE_AVAILABLE,
                    "coreml" if self._coreml_embedder._coreml_model is not None else "hash",
                )
            except Exception as e:
                logger.warning("[SEMSTORE] CoreMLEmbedder load failed: %s", e)
                self._coreml_embedder = None

        # CPU fallback: load FastEmbed (always works)
        try:
            from fastembed import TextEmbedding

            self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
            # Warm-up embed
            list(self._model.embed(["warmup"]))
            logger.info("[SEMSTORE] FastEmbed loaded (CPU fallback)")
        except ImportError:
            logger.warning("[SEMSTORE] FastEmbed not available — ANE/hash only")
            self._model = None
        except Exception as e:
            logger.warning("[SEMSTORE] FastEmbed load failed: %s", e)
            self._model = None

        # Open LanceDB
        try:
            import lancedb

            db_path_str = str(self._db_path.expanduser())
            self._db = lancedb.connect(db_path_str)
        except Exception as e:
            logger.warning("[SEMSTORE] LanceDB connect failed: %s", e)
            self._db = None

        # Open or create table (append mode — B.6)
        try:
            self._table = self._db.open_table(_TABLE_NAME)
            assert self._table is not None
            logger.info(
                f"SemanticStore: LanceDB table open: {self._table.count_rows()} rows"
            )
        except Exception:
            self._table = None  # Will be created on first flush

        self._initialized = True
        logger.info(f"SemanticStore initialized: dim={self._embed_dim}, coreml_ane={_COREML_ANE_AVAILABLE}")

    # -------------------------------------------------------------------------
    # Buffering (no I/O)
    # -------------------------------------------------------------------------

    async def add_text(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ioc_types: list[str] | None = None,
    ) -> None:
        """
        Buffer a finding for batch embed — ŽÁDNÉ I/O.

        Args:
            text: Raw text to embed.
            source_type: e.g. "certificate_transparency", "public_hunter".
            finding_id: Unique identifier.
            ioc_types: List of IOC type strings for filtering.
        """
        if not text.strip():
            return
        # Enforce bounded pending buffer (M1 8GB safety)
        if len(self._pending_texts) >= _MAX_PENDING:
            logger.debug("SemanticStore: pending buffer full, dropping oldest")
            self._pending_texts.popleft()
            self._pending_meta.popleft()
        self._pending_texts.append(text[:_MAX_TEXT_LEN])
        self._pending_meta.append(
            {
                "source_type": source_type,
                "finding_id": finding_id,
                "ts": asyncio.get_event_loop().time(),
                "ioc_types": ",".join(ioc_types) if ioc_types else "",
            }
        )

    # -------------------------------------------------------------------------
    # Flush — batch embed + LanceDB append
    # -------------------------------------------------------------------------

    async def flush(self) -> int:
        """
        Batch embed + LanceDB upsert.

        ANE path: CoreMLEmbedder.embed() → CoreML → ANE (F228B, preferred)
        CPU fallback: self._model.embed() → FastEmbed onnxruntime
        """
        if not self._initialized or self._table is None:
            return 0
        if not self._pending_texts:
            return 0

        texts = list(self._pending_texts)
        meta = list(self._pending_meta)
        self._pending_texts.clear()
        self._pending_meta.clear()

        loop = asyncio.get_running_loop()

        t0 = time.monotonic()
        backend_name = "unknown"

        # Sprint F228B: ANE path preferred — use CoreMLEmbedder (sync, must run in executor)
        if self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            backend_name = "ane"
            try:
                embeddings = await loop.run_in_executor(
                    None, lambda: self._coreml_embedder.embed(texts, batch_size=64)
                )
                logger.debug(
                    "[SEMSTORE] Batch embed via CoreMLEmbedder: %d texts", len(texts)
                )
            except Exception as e:
                logger.warning("[SEMSTORE] CoreMLEmbedder embed failed: %s", e)
                embeddings = await loop.run_in_executor(
                    None, lambda: list(self._model.embed(texts))
                )
                backend_name = "cpu_fallback"
        # FastEmbed CPU path
        elif self._model is not None:
            backend_name = "cpu_fallback"
            embeddings = await loop.run_in_executor(
                None, lambda: list(self._model.embed(texts))
            )
        else:
            # Hash fallback — deterministic zero-RAM
            backend_name = "hash_only"
            logger.debug("[SEMSTORE] Using hash fallback embed")
            import hashlib

            import numpy as np

            emb_dim = self._embed_dim
            embeddings = []
            for t in texts:
                h = int(hashlib.sha256(t[:512].encode()).hexdigest()[:16], 16)
                vec = np.zeros(emb_dim, dtype=np.float32)
                for j in range(min(emb_dim, 384)):
                    vec[j] = float((h >> (j % 32)) & 1) * 2.0 - 1.0
                norm = np.linalg.norm(vec)
                vec = vec / norm if norm > 1e-9 else vec
                embeddings.append(vec)
            embeddings = np.array(embeddings, dtype=np.float32)

        # LanceDB upsert (batched)
        records = []
        for i, (emb, m) in enumerate(zip(embeddings, meta, strict=False)):
            rec: dict[str, Any] = {
                "vector": emb.tolist(),
                "text": texts[i][: _MAX_TEXT_LEN],
                "source_type": m["source_type"],
                "finding_id": m["finding_id"],
                "ts": m["ts"],
                "ioc_types": m["ioc_types"],
            }
            records.append(rec)

        try:
            self._table.add(records)
            logger.debug("[SEMSTORE] LanceDB upserted %d records", len(records))
        except Exception as e:
            logger.warning("[SEMSTORE] LanceDB add failed: %s", e)

        elapsed_ms = (time.monotonic() - t0) * 1000
        logger.debug(
            "embedding_backend=%s latency_ms=%.1f texts=%d",
            backend_name, elapsed_ms, len(texts)
        )

        return len(records)

    # -------------------------------------------------------------------------
    # Semantic pivot — ANN search
    # -------------------------------------------------------------------------

    async def semantic_pivot(
        self, query: str, top_k: int = 10
    ) -> list[dict]:
        """
        ANN search — vrátí top-k sémanticky podobných findings.

        Uses cosine metric (LanceDB converts L2 distance internally).
        Returns list of dicts with keys: text, source_type, finding_id, ts,
        ioc_types, score (0.0–1.0 where 1.0 = identical).
        """
        if self._model is None or self._table is None:
            return []

        loop = asyncio.get_running_loop()
        q_vec = await loop.run_in_executor(
            None,
            lambda: list(self._model.embed([query]))[0],
        )

        try:

            results = (
                self._table.search(q_vec)
                .metric("cosine")
                .limit(top_k)
                .to_list()
            )
            return [
                {
                    "text": r["text"],
                    "source_type": r["source_type"],
                    "finding_id": r["finding_id"],
                    "ts": r["ts"],
                    "ioc_types": r["ioc_types"],
                    "score": 1.0 - r["_distance"],
                }
                for r in results
            ]
        except Exception as e:
            logger.warning("[SEMSTORE] ANN search failed: %s", e)
            return []

    # -------------------------------------------------------------------------
    # Embed query (direct, no buffer)
    # -------------------------------------------------------------------------

    async def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a single query string — uses ANE path if available.

        Returns:
            ndarray dtype=float32, shape=(384,)
        """
        loop = asyncio.get_running_loop()

        if self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            try:
                emb = await self._coreml_embedder.embed([query], batch_size=1)
                return emb[0]
            except Exception:
                pass

        if self._model is not None:
            return await loop.run_in_executor(
                None, lambda: list(self._model.embed([query]))[0]
            )

        # Hash fallback
        import hashlib

        h = int(hashlib.sha256(query[:512].encode()).hexdigest()[:16], 16)
        vec = np.zeros(self._embed_dim, dtype=np.float32)
        for j in range(min(self._embed_dim, 384)):
            vec[j] = float((h >> (j % 32)) & 1) * 2.0 - 1.0
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 1e-9 else vec

    # -------------------------------------------------------------------------
    # Utility
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """TEARDOWN — final flush + close connections."""
        await self.flush()
        self._model = None
        # Sprint F228B: unload CoreMLEmbedder
        if self._coreml_embedder is not None:
            self._coreml_embedder.unload()
        self._table = None
        if self._db is not None:
            try:
                getattr(self._db, "close", lambda: None)()
            except Exception:
                pass
            self._db = None
        self._initialized = False
        logger.info("SemanticStore closed")
