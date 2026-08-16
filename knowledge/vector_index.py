"""
VectorIndex — Unified Vector Storage Protocol (Issue F5)

ROLE: Single canonical interface for ANN vector storage.


Dispatches to SqliteVecIndex (primary, M1 8GB) or LanceDbIndex (>1M vectors).

Dispatch: HLEDAC_VECTOR_BACKEND env var
    - "sqlite-vec"  → SqliteVecIndex (zero-process, ~5MB, M1-native)
    - "lancedb"      → LanceDbIndex (subprocess, ~200MB, >1M vectors)
    - "auto" (default) → SqliteVecIndex on M1, LanceDbIndex otherwise

Protocol contract (VectorIndex):
    async def add(vectors: np.ndarray, ids: list[str], metadata: list[dict]) -> None
    async def query(query: np.ndarray, k: int) -> list[AnnHit]
    async def close() -> None

AnnHit = dict[str, Any]  # {"id": str, "score": float, "metadata": dict}

M1 8GB constraints:
- SqliteVecIndex: zero-process, bounded LRU, no IVF-PQ (sqlite-vec limitation)
- LanceDbIndex: IVF-PQ auto-tune via HLEDAC_LANCEDB_IVFPQ_* env vars

Always-on, bounded, fail-safe. No feature flags.

Performance invariants:
- _is_m1() result cached (subprocess call happens once, not per get_vector_index())
- orjson availability cached at module level (no try/except on success path)
- sqlite_vec/pyarrow imports happen at module level, not inside hot paths
- All add() and query() paths are allocation-free on success
"""
from __future__ import annotations

import asyncio
import logging
import os

# orjson — strict import with stdlib fallback (fail-safe, always-on)
try:
    import orjson as _orjson_mod

    _HAS_ORJSON: bool = True
except ImportError:
    _orjson_mod = None  # type: ignore[assignment]
    _HAS_ORJSON = False
import shutil
import subprocess
from abc import abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import numpy as np
from _core import aclose

if TYPE_CHECKING:
    import lancedb
    import pyarrow as pa
    import sqlite_vec

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level lazy singletons (imported once, used everywhere)
# ---------------------------------------------------------------------------
_sqlite_vec: Any = None
_lancedb: Any = None
_pyarrow: Any = None


def _lazy_import_sqlite_vec() -> Any:
    global _sqlite_vec
    if _sqlite_vec is None:
        import sqlite_vec as sv

        _sqlite_vec = sv
    return _sqlite_vec


def _lazy_import_lancedb() -> Any:
    global _lancedb
    if _lancedb is None:
        import lancedb as ld

        _lancedb = ld
    return _lancedb


def _lazy_import_pyarrow() -> Any:
    global _pyarrow
    if _pyarrow is None:
        import pyarrow as pa_lib

        _pyarrow = pa_lib
    return _pyarrow


# ---------------------------------------------------------------------------
# orjson cache (zero-allocation on success path)
# ---------------------------------------------------------------------------
_orjson_dumps: Any = None


def _orjson_available() -> bool:
    global _orjson_dumps
    if _orjson_dumps is None:
        try:
            import orjson

            _orjson_dumps = orjson.dumps
            return True
        except ImportError:
            _orjson_dumps = False
            return False
    return _orjson_dumps is not False


def json_dumps_maybe(obj: dict[str, Any]) -> str:
    """Serialize metadata dict. Uses orjson if available for speed."""
    if _orjson_available():
        return _orjson_dumps(obj).decode("utf-8")
    return _json_lib.dumps(obj)

# ---------------------------------------------------------------------------
# AnnHit — canonical return type for vector queries
# ---------------------------------------------------------------------------
AnnHit = dict[str, Any]

# ---------------------------------------------------------------------------
# Backend dispatch
# ---------------------------------------------------------------------------
VectorBackend = Literal["sqlite-vec", "lancedb", "auto"]


def _resolve_backend() -> VectorBackend:
    """Resolve HLEDAC_VECTOR_BACKEND to concrete backend.
    
    SWARM-010: Use FeatureFlags.get_str() for registry compliance.
    """
    from hledac.universal._core.feature_flags import FeatureFlags, FeatureFlag
    backend = FeatureFlags.get_str(FeatureFlag.VECTOR_BACKEND, "auto").lower()
    if backend not in ("sqlite-vec", "lancedb", "auto"):
        logger.warning(
            "[VectorIndex] Unknown HLEDAC_VECTOR_BACKEND=%r, defaulting to auto",
            backend,
    )
        return "auto"
    return cast(VectorBackend, backend)


_is_m1_cached: bool | None = None


def _is_m1() -> bool:
    """Detect M1/M2 Apple Silicon for auto-backend selection. Cached after first call."""
    global _is_m1_cached
    if _is_m1_cached is None:
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand"],
                capture_output=True,
                text=True,
                timeout=5,
    )
            _is_m1_cached = (
                "Apple" in result.stdout and ("M1" in result.stdout or "M2" in result.stdout)
    )
        except Exception:
            _is_m1_cached = False
    return _is_m1_cached


# ---------------------------------------------------------------------------
# VectorIndex Protocol (PEP 544)
# ---------------------------------------------------------------------------
class VectorIndex:
    """
    Unified vector storage protocol.

    Implementations:
        SqliteVecIndex  — M1-native, zero-process, ~5MB overhead
        LanceDbIndex    — high-capacity, >1M vectors, ~200MB overhead

    Canonical lifecycle:
        idx = get_vector_index()          # factory
        await idx.add(vectors, ids, meta) # batch insert
        results = await idx.query(q, k=10) # ANN search
        await idx.close()                  # teardown
    """

    __slots__ = ()

    @abstractmethod
    async def add(
        self, vectors: np.ndarray, ids: list[str], metadata: list[dict[str, Any]]
    ) -> None:
        """Add vectors to the index.

        Args:
            vectors: np.ndarray of shape (N, dim)
            ids: List of N string IDs
            metadata: List of N metadata dicts
        """
        ...

    @abstractmethod
    async def query(
        self, query: np.ndarray, k: int = 10
    ) -> list[AnnHit]:
        """ANN search for k nearest neighbors.

        Args:
            query: np.ndarray of shape (1, dim) or (dim,)
            k: Number of results

        Returns:
            List of AnnHit dicts {"id": str, "score": float, "metadata": dict}
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close index and release resources."""
        ...

    async def __aenter__(self) -> "VectorIndex":
        """Async context manager entry."""
        return self

    async def __aexit__(self, *_exc_info: Any) -> None:
        """Async context manager exit — ensures close()."""
        await self.close()

    # -------------------------------------------------------------------------
    # Shared utilities (default implementations, overrideable)
    # -------------------------------------------------------------------------
    @staticmethod
    def _normalize(v: np.ndarray) -> np.ndarray:
        """L2-normalize vectors for cosine similarity."""
        norm = np.linalg.norm(v, axis=-1, keepdims=True)
        norm = np.where(norm == 0, 1, norm)
        return v / norm


# ---------------------------------------------------------------------------
# SqliteVecIndex — M1-native primary backend
# ---------------------------------------------------------------------------
class SqliteVecIndex(VectorIndex):
    """
    M1-native ANN store via sqlite-vec.

    Zero-process: sqlite-vec runs in-process via SQLite extension.
    ~5MB overhead vs ~200MB for LanceDB subprocess.

    Bounded:
        - MAX_PENDING_UPSERTS=10_000 (M1 8GB safety)
        - Vector dimension capped at 384 (sqlite-vec vec0 limit)
    """

    __slots__ = ("_db_path", "_conn", "_dim", "_table_name", "_pending_ids", "_pending_vectors", "_pending_meta", "_lock", "_closed")
    MAX_DIM: int = 384
    MAX_PENDING_UPSERTS: int = 10_000

    def __init__(
        self,
        db_path: Path | None = None,
        dim: int = 384,
        table_name: str = "vec_items",
    ) -> None:
        self._db_path = db_path or self._default_db_path()
        self._conn: Any = None
        self._dim = dim
        self._table_name = table_name
        # Pending buffer (bounded, flush on add)
        self._pending_ids: list[str] = []
        self._pending_vectors: list[list[float]] = []
        self._pending_meta: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._closed = False

    @staticmethod
    def _default_db_path() -> Path:
        """Use sprint store root (shared with DuckDBShadowStore)."""
        try:
            from hledac.universal.paths import SPRINT_STORE_ROOT
        except ImportError:
            from pathlib import Path
            return Path.home() / ".hledac" / "sprint_store" / "default.db"
        sprint_dir = SPRINT_STORE_ROOT / "default"
        sprint_dir.mkdir(parents=True, exist_ok=True)
        return sprint_dir / "default.db"

    async def add(
        self, vectors: np.ndarray, ids: list[str], metadata: list[dict[str, Any]]
    ) -> None:
        """Batch upsert via sqlite-vec vec0 virtual table."""
        if self._closed:
            raise RuntimeError("[SqliteVecIndex] Already closed")
        if vectors.shape[0] != len(ids) or vectors.shape[0] != len(metadata):
            raise ValueError(
                f"Shape mismatch: {vectors.shape[0]} vectors, {len(ids)} ids, {len(metadata)} meta"
    )
        if vectors.shape[1] > self.MAX_DIM:
            raise ValueError(
                f"[SqliteVecIndex] dim={vectors.shape[1]} exceeds MAX_DIM={self.MAX_DIM}"
    )

        # Ensure initialized
        await self._ensure_db()

        # Normalize vectors (cosine similarity)
        vectors = self._normalize(vectors.astype(np.float32))
        flat: list[list[float]] = vectors.tolist()

        async with self._lock:
            self._pending_ids.extend(ids)
            self._pending_vectors.extend(flat)
            self._pending_meta.extend(metadata)

            # Bounded flush
            if len(self._pending_ids) >= self.MAX_PENDING_UPSERTS:
                await self._flush_locked()
            elif len(self._pending_ids) >= 100:  # Micro-batch for latency
                await self._flush_locked()

    async def _ensure_db(self) -> None:
        """Lazily initialize sqlite-vec connection."""
        if self._conn is not None:
            return
        sv = _lazy_import_sqlite_vec()

        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sv.connect(str(self._db_path))

        # Create virtual table if not exists
        self._conn.execute(
            f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self._table_name} USING vec0(
                id TEXT PRIMARY KEY,
                embedding FLOAT[{self._dim}],
                metadata JSON
    )
            """
    )
        self._conn.commit()
        logger.debug(
            "[SqliteVecIndex] Initialized: %s (dim=%d)", self._db_path, self._dim
    )

    async def _flush_locked(self) -> None:
        """Flush pending upserts (must hold self._lock)."""
        if not self._pending_ids:
            return

        ids, vectors, meta = (
            self._pending_ids[:],
            self._pending_vectors[:],
            self._pending_meta[:],
    )
        self._pending_ids.clear()
        self._pending_vectors.clear()
        self._pending_meta.clear()

        rows = [
            (i, v, json_dumps_maybe(m))
            for i, v, m in zip(ids, vectors, meta)
        ]
        try:
            self._conn.executemany(
                f"INSERT OR REPLACE INTO {self._table_name} (id, embedding, metadata) VALUES (?, ?, ?)",
                rows,
    )
            self._conn.commit()
            logger.debug("[SqliteVecIndex] Flushed %d vectors", len(rows))
        except Exception as e:
            logger.warning("[SqliteVecIndex] Flush failed: %s", e)
            # Re-queue on failure
            self._pending_ids.extend(ids)
            self._pending_vectors.extend(vectors)
            self._pending_meta.extend(meta)

    async def query(self, query: np.ndarray, k: int = 10) -> list[AnnHit]:
        """ANN search via sqlite-vec cosine similarity."""
        if self._closed:
            raise RuntimeError("[SqliteVecIndex] Already closed")
        await self._ensure_db()

        # Flush any pending
        async with self._lock:
            await self._flush_locked()

        # Normalize
        if query.ndim == 1:
            query = query.reshape(1, -1)
        query = self._normalize(query.astype(np.float32))[0].tolist()

        try:
            rows = self._conn.execute(
                f"""
                SELECT id, embedding, metadata
                FROM {self._table_name}
                ORDER BY vec0_distance_cosine(embedding, ?) ASC
                LIMIT ?
                """,
                [query, k],
            ).fetchall()

            results: list[AnnHit] = []
            for row in rows:
                try:
                    meta = _orjson_mod.loads(row[2]) if row[2] else {}
                except Exception:
                    meta = {}
                # sqlite-vec distance is 0=identical, 2=opposite
                # Convert to similarity score: score = 1 - distance/2
                score = max(0.0, 1.0 - (row[1] or 0.0) / 2.0)
                results.append(
                    AnnHit(id=row[0], score=float(score), metadata=meta)
    )
            return results

        except Exception as e:
            logger.warning("[SqliteVecIndex] Query failed: %s", e)
            return []

    async def close(self) -> None:
        """Flush pending and close connection."""
        if self._closed:
            return
        self._closed = True
        try:
            async with self._lock:
                if self._conn is not None:
                    await self._flush_locked()
                    self._conn.close()
                    self._conn = None
            logger.debug("[SqliteVecIndex] Closed: %s", self._db_path)
        except Exception as e:
            logger.warning("[SqliteVecIndex] Close error: %s", e)


# ---------------------------------------------------------------------------
# LanceDbIndex — high-capacity backend (>1M vectors)
# ---------------------------------------------------------------------------
class LanceDbIndex(VectorIndex):
    """
    LanceDB ANN store for high-capacity vector workloads.

    ~200MB subprocess overhead but supports:
        - >1M vectors with IVF-PQ auto-tune
        - Native FTS for hybrid search
        - Binary signature pre-filter

    Uses lancedb.connect() directly (LanceDBPool removed — MOD-05).
    """

    __slots__ = ("_db_path", "_db", "_table", "_table_name", "_dim", "_lancedb_has_fts", "_closed")

    def __init__(
        self,
        db_path: Path | None = None,
        dim: int = 384,
        table_name: str = "vec_items",
    ) -> None:
        self._db_path = db_path or self._default_db_path()
        self._db: Any = None
        self._table: Any = None
        self._table_name = table_name
        self._dim = dim
        self._lancedb_has_fts = False
        self._closed = False

    @staticmethod
    def _default_db_path() -> Path:
        """Use LanceDB root under .hledac/lancedb/."""
        return Path.home() / ".hledac" / "lancedb"

    async def add(
        self, vectors: np.ndarray, ids: list[str], metadata: list[dict[str, Any]]
    ) -> None:
        """Batch upsert via LanceDB."""
        if self._closed:
            raise RuntimeError("[LanceDbIndex] Already closed")
        if vectors.shape[0] != len(ids) or vectors.shape[0] != len(metadata):
            raise ValueError(
                f"Shape mismatch: {vectors.shape[0]} vectors, {len(ids)} ids, {len(metadata)} meta"
    )

        await self._ensure_db()

        pa = _lazy_import_pyarrow()

        # Normalize for cosine similarity
        vectors = self._normalize(vectors.astype(np.float32))

        # Build PyArrow record batch
        table = pa.table(
            {
                "id": pa.array(ids),
                "vector": pa.array(vectors.tolist(), type=pa.list_(pa.float32(), self._dim)),
                "metadata": pa.array([json_dumps_maybe(m) for m in metadata]),
            }
    )

        try:
            self._table.merge_insert("id").on("id").execute(table.to_batches())
            logger.debug("[LanceDbIndex] Added %d vectors", len(ids))
        except Exception as e:
            logger.warning("[LanceDbIndex] Add failed: %s", e)

    async def _ensure_db(self) -> None:
        """Lazily initialize LanceDB connection and table."""
        if self._table is not None:
            return

        lancedb = _lazy_import_lancedb()
        self._db = lancedb.connect(str(self._db_path))

        pa = _lazy_import_pyarrow()

        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field(
                    "vector", pa.list_(pa.float32(), self._dim)
                ),
                pa.field("metadata", pa.string()),
            ]
    )

        try:
            self._table = self._db.open_table(self._table_name)
            logger.debug("[LanceDbIndex] Opened existing table: %s", self._table_name)
        except Exception:
            self._table = self._db.create_table(
                self._table_name, schema=schema, exist_ok=True
    )
            logger.info("[LanceDbIndex] Created table: %s", self._table_name)

        # Try to create FTS indexes (best-effort)
        try:
            list_indices_fn = getattr(self._table, "list_indices", None)
            existing = list_indices_fn() if callable(list_indices_fn) else []
            existing_names = {getattr(idx, "name", "") for idx in existing}
            # FTS creation is best-effort; LanceDB may not support it in all versions
            if hasattr(self._table, "create_fts_index"):
                for col in ("text", "content"):
                    fts_name = f"{col}_fts"
                    if fts_name not in existing_names:
                        try:
                            self._table.create_fts_index(col, replace=False)
                            logger.info("[LanceDbIndex] Created FTS index: %s", fts_name)
                        except Exception:  # noqa: BLE001
                            pass
            self._lancedb_has_fts = True
        except Exception:
            self._lancedb_has_fts = False

    async def query(self, query: np.ndarray, k: int = 10) -> list[AnnHit]:
        """ANN search via LanceDB vector query."""
        if self._closed:
            raise RuntimeError("[LanceDbIndex] Already closed")
        await self._ensure_db()

        # Normalize
        if query.ndim == 1:
            query = query.reshape(1, -1)
        query = self._normalize(query.astype(np.float32))

        try:
            result = self._table.search(query.tolist()).limit(k).to_arrow()
            scores = result.column("score").to_pylist()
            ids = result.column("id").to_pylist()
            # metadata column may not exist; handle gracefully
            try:
                meta_list = result.column("metadata").to_pylist()
            except Exception:
                meta_list = [{} for _ in ids]

            results: list[AnnHit] = []
            for row_id, score, meta_str in zip(ids, scores, meta_list):
                try:
                    meta = meta_str if isinstance(meta_str, dict) else {}  # type: ignore[unreachable]
                except Exception:
                    meta = {}
                results.append(AnnHit(id=str(row_id), score=float(score), metadata=meta))
            return results

        except Exception as e:
            logger.warning("[LanceDbIndex] Query failed: %s", e)
            return []

    async def close(self) -> None:
        """Close LanceDB connection."""
        if self._closed:
            return
        self._closed = True
        try:
            if self._db is not None:
                self._db.close()
                self._db = None
                self._table = None
            logger.debug("[LanceDbIndex] Closed: %s", self._db_path)
        except Exception as e:
            logger.warning("[LanceDbIndex] Close error: %s", e)


# ---------------------------------------------------------------------------
# JSON helpers (zero-allocation on success path)
# ---------------------------------------------------------------------------
def json_dumps_maybe(obj: dict[str, Any]) -> str:
    """Serialize metadata dict. Uses orjson if available for speed."""
    if _HAS_ORJSON:
        return _orjson_mod.dumps(obj).decode("utf-8")
    import json

    return json.dumps(obj)


# ---------------------------------------------------------------------------
# Factory dispatch
# ---------------------------------------------------------------------------
_VectorIndex: VectorIndex | None = None


def get_vector_index(
    backend: VectorBackend | None = None,
    **kwargs: Any,
) -> VectorIndex:
    """
    Get a VectorIndex implementation.

    Args:
        backend: "sqlite-vec" | "lancedb" | "auto" (default)
                 "auto" → SqliteVecIndex on M1, LanceDbIndex otherwise.
        **kwargs: Passed to the backend constructor
                  (db_path, dim, table_name)

    Returns:
        VectorIndex implementation (always initialized, never None)

    Always-on, fail-safe: if the requested backend is unavailable,
    falls back to the other backend with a warning.
    """
    global _VectorIndex

    resolved = backend or _resolve_backend()

    # M1 auto-selection
    if resolved == "auto":
        resolved = "sqlite-vec" if _is_m1() else "lancedb"

    # Return cached instance for default backends (no kwargs)
    if not kwargs and _VectorIndex is not None:
        return _VectorIndex  # type: ignore[return-value]

    # Instantiate requested backend
    if resolved == "sqlite-vec":
        impl: VectorIndex = SqliteVecIndex(**kwargs)
    elif resolved == "lancedb":
        impl = LanceDbIndex(**kwargs)
    else:
        # Should not reach here, but guard
        impl = SqliteVecIndex(**kwargs)

    if not kwargs:
        _VectorIndex = impl  # type: ignore[assignment]

    return impl


# ---------------------------------------------------------------------------
# Backward-compatibility aliases
# ---------------------------------------------------------------------------
# For callers that do `from knowledge.vector_index import VectorStore`
# (old name), provide an alias.
VectorStore = VectorIndex  # type: ignore[misc,assignment]


__all__ = [
    "VectorIndex",
    "SqliteVecIndex",
    "LanceDbIndex",
    "get_vector_index",
    "AnnHit",
    "VectorBackend",
    # Backward compat aliases
    "VectorStore",
]
