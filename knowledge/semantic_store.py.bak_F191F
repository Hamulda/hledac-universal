"""
Sprint 8SB — SemanticStore: FastEmbed + LanceDB Semantic IOC Search
===================================================================
Singleton lifecycle — initialize() v BOOT, close() v TEARDOWN.

ROLE: Consumer/Enrichment (NOT backend owner, NOT grounding authority)
======================================================================
FastEmbed BAAI/bge-small-en-v1.5 ONNX model (dim=384, ~33MB, CoreML-friendly).
LanceDB ANN index pod ~/.hledac/lancedb/ — append mode, nikdy drop+recreate.

NENÍ owner backend storage → persistent_layer (deprecated!)
NENÍ owner embedding computation → MLXEmbeddingManager singleton
NENÍ owner primary retrieval → rag_engine

B.1: Singleton TextEmbedding instance — NIKDY re-init per-request.
B.2: LanceDB path = PATHS.hledac_home / "lancedb"
B.3: Embed přes CPU_EXECUTOR — nikdy neblokovat event loop.
B.5: semantic_pivot() = ANN search, cosine metric, score = 1 - L2 distance.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

import numpy as np

from ..utils.executors import CPU_EXECUTOR

logger = logging.getLogger(__name__)

# LanceDB table name
_TABLE_NAME = "findings_v1"
_EMBED_DIM = 384
_MAX_TEXT_LEN = 512  # bge-small max 512 tokens
_MAX_PENDING = 10_000  # Bounded pending buffer (M1 8GB safety)


class SemanticStore:
    """
    FastEmbed + LanceDB pro sémantické vyhledávání findings.

    Lifecycle:
        store = SemanticStore(db_path)
        await store.initialize()   # BOOT
        store.buffer_finding(...)  # per-finding (žádné I/O)
        await store.flush()         # WINDUP — batch embed + LanceDB upsert
        await store.semantic_pivot("ransomware CVE", top_k=10)  # query
        await store.close()         # TEARDOWN
    """

    __slots__ = (
        "_db_path",
        "_db",
        "_table",
        "_model",
        "_pending_texts",
        "_pending_meta",
        "_embed_dim",
        "_initialized",
    )

    def __init__(self, db_path: Path) -> None:
        self._db_path: Path = db_path
        self._db: Optional[Any] = None  # lancedb.LanceDBConnection
        self._table: Optional[Any] = None  # lancedb.Table
        self._model: Optional[Any] = None  # TextEmbedding
        self._pending_texts: list[str] = []
        self._pending_meta: list[dict] = []
        self._embed_dim: int = _EMBED_DIM
        self._initialized: bool = False

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def initialize(self) -> None:
        """BOOT — load FastEmbed model + open LanceDB connection."""
        if self._initialized:
            return

        loop = asyncio.get_running_loop()

        # Ensure db directory
        self._db_path.mkdir(parents=True, exist_ok=True)

        # Connect to LanceDB
        import lancedb

        self._db = lancedb.connect(str(self._db_path))

        # Load model in CPU executor
        def _load_model() -> Any:
            from fastembed import TextEmbedding

            m = TextEmbedding("BAAI/bge-small-en-v1.5")
            # Warm-up embed
            list(m.embed(["warmup"]))
            return m

        self._model = await loop.run_in_executor(CPU_EXECUTOR, _load_model)

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
        logger.info(f"SemanticStore initialized: dim={self._embed_dim}")

    # -------------------------------------------------------------------------
    # Buffering (no I/O)
    # -------------------------------------------------------------------------

    def buffer_finding(
        self,
        text: str,
        source_type: str,
        finding_id: str,
        ts: float,
        ioc_types: list[str],
    ) -> None:
        """
        Buffer a finding for batch embed — ŽÁDNÉ I/O.

        Truncates text to _MAX_TEXT_LEN chars (bge-small max 512 tokens).
        Bounded: MAX_PENDING cap prevents unbounded growth.
        """
        if not text.strip():
            return
        # Enforce bounded pending buffer (M1 8GB safety)
        if len(self._pending_texts) >= _MAX_PENDING:
            logger.debug("SemanticStore: pending buffer full, dropping oldest")
            self._pending_texts.pop(0)
            self._pending_meta.pop(0)
        self._pending_texts.append(text[:_MAX_TEXT_LEN])
        self._pending_meta.append(
            {
                "source_type": source_type,
                "finding_id": finding_id,
                "ts": ts,
                "ioc_types": ",".join(ioc_types),
            }
        )

    # -------------------------------------------------------------------------
    # Flush — batch embed + LanceDB append
    # -------------------------------------------------------------------------

    async def flush(self) -> int:
        """
        Batch embed + LanceDB upsert.

        Called in WINDUP. Returns number of findings written.
        Idempotent: second flush with no pending items returns 0.
        """
        if not self._pending_texts or self._model is None:
            return 0

        texts = self._pending_texts[:]
        meta = self._pending_meta[:]
        self._pending_texts.clear()
        self._pending_meta.clear()

        loop = asyncio.get_running_loop()

        # Batch embed in CPU executor (B.3)
        embeddings = await loop.run_in_executor(
            CPU_EXECUTOR, lambda: list(self._model.embed(texts))
        )

        # Build rows
        rows = []
        for i, (emb, m) in enumerate(zip(embeddings, meta)):
            rows.append(
                {
                    "vector": np.array(emb, dtype="float32").tolist(),
                    "text": texts[i],
                    **m,
                }
            )

        def _write() -> int:
            import pyarrow as pa

            if self._table is None:
                schema = pa.schema(
                    [
                        pa.field("vector", pa.list_(pa.float32(), _EMBED_DIM)),
                        pa.field("text", pa.string()),
                        pa.field("source_type", pa.string()),
                        pa.field("finding_id", pa.string()),
                        pa.field("ts", pa.float64()),
                        pa.field("ioc_types", pa.string()),
                    ]
                )
                self._table = self._db.create_table(
                    _TABLE_NAME, data=rows, schema=schema
                )
            else:
                self._table.add(rows)
            return len(rows)

        count = await loop.run_in_executor(CPU_EXECUTOR, _write)
        logger.info(f"SemanticStore flushed: {count} findings")
        return count

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
        if not query.strip():
            return []

        loop = asyncio.get_running_loop()

        # Embed query
        query_emb = await loop.run_in_executor(
            CPU_EXECUTOR,
            lambda: list(self._model.embed([query]))[0],
        )
        q_vec = np.array(query_emb, dtype="float32")

        def _search() -> list[dict]:
            results = (
                self._table.search(q_vec.tolist())
                .metric("cosine")
                .limit(top_k)
                .to_list()
            )
            return [
                {
                    "text": r.get("text", ""),
                    "source_type": r.get("source_type", ""),
                    "finding_id": r.get("finding_id", ""),
                    "ts": r.get("ts", 0.0),
                    "ioc_types": r.get("ioc_types", ""),
                    # LanceDB cosine returns _distance in [0, 2]; cosine similarity = 1 - d/2
                    # But when using .metric("cosine"), _distance is cosine distance [0,1]
                    # score = 1 - _distance (range 0..1)
                    "score": max(0.0, min(1.0, 1.0 - r.get("_distance", 1.0))),
                }
                for r in results
            ]

        return await loop.run_in_executor(CPU_EXECUTOR, _search)

    # -------------------------------------------------------------------------
    # Stats
    # -------------------------------------------------------------------------

    async def count(self) -> int:
        """Return current row count in LanceDB table."""
        if self._table is None:
            return 0

        def _count() -> int:
            assert self._table is not None
            return self._table.count_rows()

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(CPU_EXECUTOR, _count)

    # -------------------------------------------------------------------------
    # Close
    # -------------------------------------------------------------------------

    async def close(self) -> None:
        """TEARDOWN — final flush + close connections."""
        await self.flush()
        self._model = None
        self._table = None
        if self._db is not None:
            try:
                getattr(self._db, "close", lambda: None)()
            except Exception:
                pass
            self._db = None
        self._initialized = False
        logger.info("SemanticStore closed")
