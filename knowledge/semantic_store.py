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

Migrated to ConcurrencyBudgetRegistry (F268).
NENÍ owner embedding computation → MLXEmbeddingManager singleton
NENÍ owner primary retrieval → rag_engine
"""


import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np

if TYPE_CHECKING:
    import lancedb
    from lancedb.query import LanceVectorQueryBuilder


logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────────
_EMBED_DIM = 384
_MAX_PENDING = 2000  # Bounded pending buffer
_MAX_TEXT_LEN = 4096
_TABLE_NAME = "semantic_ioc_v1"

# Sprint F228B: CPU executor for embed (never block event loop)
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
CPU_EXECUTOR = get_semaphore_for_testing(ConcurrencyCategory.MLX_INFERENCE)

# ── CoreML/ANE availability ────────────────────────────────────────────────────
try:
    from hledac.universal.brain.coreml_embedder import (
        CoreMLEmbedder,
    )
    from hledac.universal.brain.coreml_embedder import (
        get_coreml_embedder as _get_coreml_embedder_impl,
    )
    from hledac.universal.brain.coreml_embedder import (
        is_ane_available as _COREML_ANE_AVAILABLE,
    )

    _COREML_AVAILABLE = True
    _get_coreml_embedder: Any = _get_coreml_embedder_impl
except ImportError:
    _COREML_AVAILABLE = False
    _COREML_ANE_AVAILABLE: bool = False
    _get_coreml_embedder: Any = None
    # NOTE: CoreMLEmbedder left as undefined (TypeError at runtime if accessed)
    # — callers guard with _COREML_AVAILABLE or isinstance checks


class SemanticStore:
    """
    FastEmbed + LanceDB pro sémantické vyhledávání findings.

    ANE path (F228B): CoreMLEmbedder.embed() → CoreML → ANE (preferred)
    CPU fallback: self._model.embed() — FastEmbed TextEmbedding
    Hash fallback: always works.

    Lifecycle:
        await store.initialize()  # BOOT — load model + open LanceDB
        store.add_text(...)        # Buffer (sync, no I/O)
        await store.flush()        # Batch embed + LanceDB upsert
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
        "_vec_db",  # Issue 4.3: sqlite-vec fallback when LanceDB unavailable
        "_model",
        "_coreml_embedder",
        "_mlx_embedder",
        "_pending_texts",
        "_pending_meta",
        "_embed_dim",
        "_initialized",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: lancedb.LanceDBConnection | None = None  # lancedb.LanceDBConnection
        self._table: lancedb.Table | None = None  # lancedb.Table
        self._vec_db: Any = None  # Issue 4.3: sqlite-vec.Connection fallback
        self._model: Any = None  # FastEmbed TextEmbedding
        # Sprint F228B: CoreML/ANE embedder — lazy async init in initialize()
        # (get_coreml_embedder() is now async; __init__ cannot await)
        self._coreml_embedder: CoreMLEmbedder | None = None
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

        # Sprint F228B: Try CoreMLEmbedder ANE path first (async lazy init)
        if _COREML_AVAILABLE:
            try:
                from hledac.universal.utils.coreml import CoreMLServiceManager
                CoreMLServiceManager.ensure_running()
            except Exception:  # noqa: BLE001
                pass
            # async get — safe to call from async context (DCLP singleton)
            self._coreml_embedder = await _get_coreml_embedder()

        # Sprint F228B: Try CoreMLEmbedder ANE path first
        if self._coreml_embedder is not None:
            try:
                await self._coreml_embedder.load()
                logger.info(
                    "[SEMSTORE] CoreMLEmbedder loaded (ANE path=%s, backend=%s)",
                    _COREML_ANE_AVAILABLE,
                    getattr(self._coreml_embedder, '_backend', None) or "hash",
                )
            except Exception as e:
                logger.warning("[SEMSTORE] CoreMLEmbedder load failed: %s", e)
                self._coreml_embedder = None

        # MLX path: Use MLXEmbeddingManager singleton (modernbert-embed-base)
        # This uses mlx_embeddings package via _shims/core_mlx_embeddings shim
        self._mlx_embedder = None
        try:
            from _shims.core_mlx_embeddings import get_embedding_manager

            self._mlx_embedder = get_embedding_manager()
            # Ensure loaded
            if not self._mlx_embedder.is_loaded:
                await asyncio.to_thread(self._mlx_embedder._load_model)
            logger.info("[SEMSTORE] MLXEmbeddingManager loaded (ModernBERT, unified memory)")
        except Exception as e:
            logger.debug("[SEMSTORE] MLXEmbeddingManager not available: %s", e)
            self._mlx_embedder = None

        # CPU fallback: load FastEmbed (always works, if mlx-embeddings unavailable)
        self._model = None
        if self._mlx_embedder is None:
            try:
                from fastembed import TextEmbedding

                self._model = TextEmbedding("BAAI/bge-small-en-v1.5")
                # Warm-up embed
                list(self._model.embed(["warmup"]))
                logger.info("[SEMSTORE] FastEmbed loaded (CPU fallback)")
            except ImportError:
                logger.warning("[SEMSTORE] FastEmbed not available — MLX only")
            except Exception as e:
                logger.warning("[SEMSTORE] FastEmbed load failed: %s", e)

        # Open LanceDB (primary) — falls back to sqlite-vec on failure
        try:
            from knowledge.lancedb_pool import get_connection

            db_path_str = str(self._db_path.expanduser())
            self._db = get_connection(db_path_str)  # type: ignore[assignment]
        except Exception as e:
            logger.warning("[SEMSTORE] LanceDB connect failed: %s", e)
            self._db = None

        # Open or create LanceDB table (append mode — B.6)
        try:
            if self._db is not None:
                self._table = self._db.open_table(_TABLE_NAME)
                assert self._table is not None
                logger.info(
                    f"SemanticStore: LanceDB table open: {self._table.count_rows()} rows"
                )
            else:
                self._table = None
        except Exception:
            self._table = None  # Will be created on first flush

        # Issue 4.3: sqlite-vec fallback — zero-RAM ANN search via SQLite extension.
        # On M1 8GB: avoids LanceDB process overhead (~50MB resident).
        # sqlite-vec is a single-file SQLite extension (<1MB), loaded in-process.
        if self._table is None:
            try:
                import sqlite_vec

                vec_db_path = str(self._db_path.parent / "semantic_vec.db")
                self._vec_db = sqlite_vec.connect(vec_db_path)
                # Create virtual table for vectors
                self._vec_db.execute(
                    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_TABLE_NAME} USING vec0("
                    f"finding_id TEXT PRIMARY KEY, text TEXT, source_type TEXT, "
                    f"finding_id_idx TEXT, ts REAL, ioc_types TEXT, "
                    f"embedding float[{self._embed_dim}])"
                )
                logger.info(f"[SEMSTORE] sqlite-vec fallback active: {vec_db_path}")
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec fallback failed: %s", e)
                self._vec_db = None

        self._initialized = True
        logger.info(f"SemanticStore initialized: dim={self._embed_dim}, coreml_ane={_COREML_ANE_AVAILABLE}, vec_backend={'lancedb' if self._table else 'sqlite-vec' if self._vec_db else 'memory'}")

    # -------------------------------------------------------------------------
    # Buffering (no I/O)
    # -------------------------------------------------------------------------

    def add_text(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ioc_types: list[str] | None = None,
        ts: float | None = None,
    ) -> None:
        """
        Buffer a finding for batch embed — ŽÁDNÉ I/O.

        Args:
            text: Raw text to embed.
            source_type: e.g. "certificate_transparency", "public_hunter".
            finding_id: Unique identifier.
            ioc_types: List of IOC type strings for filtering.
            ts: Optional timestamp (defaults to current loop time if not provided).
        """
        if not text.strip():
            return
        # Enforce bounded pending buffer (M1 8GB safety)
        if len(self._pending_texts) >= _MAX_PENDING:
            logger.debug("SemanticStore: pending buffer full, dropping oldest")
            self._pending_texts.popleft()
            self._pending_meta.popleft()
        self._pending_texts.append(text[:_MAX_TEXT_LEN])
        if ts is None:
            try:
                ts = asyncio.get_running_loop().time()
            except RuntimeError:
                ts = 0.0
        self._pending_meta.append(
            {
                "source_type": source_type,
                "finding_id": finding_id,
                "ts": ts,
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
        _backend_name = "unknown"

        # MLX path preferred — Apple Silicon native, unified memory
        # MLXEmbeddingManager.encode() takes list[str] and returns np.ndarray
        mlx_mgr = self._mlx_embedder
        if mlx_mgr is not None:
            backend_name = "mlx"
            try:
                # Use encode() method which handles batch internally
                def batch_encode(manager, txts: list[str]) -> np.ndarray:
                    return manager.encode(txts, normalize=True)

                embeddings = await loop.run_in_executor(
                    None, lambda: batch_encode(mlx_mgr, texts)
                )
                logger.debug(
                    "[SEMSTORE] Batch embed via MLXEmbeddingManager: %d texts", len(texts)
                )
            except Exception as e:
                logger.warning("[SEMSTORE] MLXEmbeddingManager embed failed: %s", e)
                embeddings = await loop.run_in_executor(
                    None, lambda: list(self._model.embed(texts))
                )
                backend_name = "cpu_fallback"
        # Sprint F228B: ANE path preferred — use CoreMLEmbedder (sync, must run in executor)
        elif self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            backend_name = "ane"
            try:
                embeddings = await loop.run_in_executor(
                    None, lambda: self._coreml_embedder.embed(texts, batch_size=64)  # type: ignore[union-attr]
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

        # Issue 4.3: Route to LanceDB (primary) or sqlite-vec (fallback)
        if self._table is not None:
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
        elif self._vec_db is not None:
            # sqlite-vec fallback — batch insert via executemany
            try:
                rows = [
                    (
                        m["finding_id"],
                        texts[i][: _MAX_TEXT_LEN],
                        m["source_type"],
                        m["finding_id"],
                        m["ts"],
                        m["ioc_types"],
                        emb.tolist(),
                    )
                    for i, (emb, m) in enumerate(zip(embeddings, meta, strict=False))
                ]
                self._vec_db.executemany(
                    f"INSERT OR REPLACE INTO {_TABLE_NAME} "
                    f"(finding_id, text, source_type, finding_id_idx, ts, ioc_types, embedding) "
                    f"VALUES (?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
                self._vec_db.commit()
                logger.debug("[SEMSTORE] sqlite-vec upserted %d records", len(rows))
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec upsert failed: %s", e)

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
        if self._model is None:
            return []

        loop = asyncio.get_running_loop()
        q_vec = await loop.run_in_executor(
            None,
            lambda: list(self._model.embed([query]))[0],
        )

        # Issue 4.3: LanceDB (primary) or sqlite-vec (fallback)
        if self._table is not None:
            try:
                _qv = cast("LanceVectorQueryBuilder", self._table.search(q_vec))
                results = (
                    _qv.metric("cosine")
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
                logger.warning("[SEMSTORE] LanceDB ANN search failed: %s", e)
                return []
        elif self._vec_db is not None:
            # sqlite-vec fallback — top-k by cosine similarity
            try:
                rows = self._vec_db.execute(
                    f"SELECT finding_id, text, source_type, ts, ioc_types, "
                    f"vec_distance_cosine(embedding, ?) AS score "
                    f"FROM {_TABLE_NAME} ORDER BY score DESC LIMIT ?",
                    [q_vec.tolist(), top_k],
                ).fetchall()
                return [
                    {
                        "text": r[1],
                        "source_type": r[2],
                        "finding_id": r[0],
                        "ts": r[3],
                        "ioc_types": r[4] or "",
                        "score": r[5],
                    }
                    for r in rows
                ]
            except Exception as e:
                logger.warning("[SEMSTORE] sqlite-vec ANN search failed: %s", e)
                return []
        else:
            return []

    # -------------------------------------------------------------------------
    # Embed query (direct, no buffer)
    # -------------------------------------------------------------------------

    async def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a single query string — uses MLX path if available.

        Returns:
            ndarray dtype=float32, shape=(384,)
        """
        loop = asyncio.get_running_loop()

        # MLX path preferred — Apple Silicon native (MLXEmbeddingManager)
        mlx_mgr = self._mlx_embedder
        if mlx_mgr is not None:
            try:
                def single_encode(manager, text: str) -> np.ndarray:
                    return manager.encode([text], normalize=True)

                result = await loop.run_in_executor(
                    None, lambda: single_encode(mlx_mgr, query)
                )
                return result[0] if result else np.zeros(384, dtype=np.float32)
            except Exception:  # noqa: BLE001
                pass

        if self._coreml_embedder is not None and self._coreml_embedder.is_loaded:
            try:
                emb = await loop.run_in_executor(
                    None, lambda: self._coreml_embedder.embed([query], batch_size=1)  # type: ignore[union-attr]
                )
                return emb[0]
            except Exception:  # noqa: BLE001
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
                close_fn = getattr(self._db, "close", None)
                if callable(close_fn):
                    close_fn()
            except Exception:  # noqa: BLE001
                pass
            self._db = None
        # Issue 4.3: sqlite-vec fallback close
        if self._vec_db is not None:
            try:
                self._vec_db.close()
            except Exception:  # noqa: BLE001
                pass
            self._vec_db = None
        self._initialized = False
        logger.info("SemanticStore closed")
