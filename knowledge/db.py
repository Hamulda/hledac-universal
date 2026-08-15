"""
knowledge/db.py — Unified Database Facade
========================================




ISSUE-001: Databázová redundance a fragmentace

KONSOLIDACE NA 2 BACKENDY:
- DuckDB: analytika + DuckPGQ graph + FTS + vektorový vyhledávání
- LMDB: cache + dedup + persistent KV

CENTRALIZOVANÝ PŘÍSTUP:
- Jediná entry point pro všechny DB operace
- Connection pooling přes rust_extensions::StdConnectionPool
- Arrow zero-copy bulk insert přes validate_batch
- Lazy inicializace přes cached_property pattern

ROLE V ARCHITEKTUŘE:
┌─────────────────────────────────────────────────────────────┐
│  knowledge/db.py — UnifiedDatabaseFacade (singleton)        │
│  ├── _duckdb: DuckDBShadowStore (canonical findings, facts) │
│  ├── _lmdb_env: LMDB env (cache, dedup, KV)               │
│  └── _pool: StdConnectionPool (rust, O(1) access)         │
└─────────────────────────────────────────────────────────────┘

M1 8GB BOUNDS:
- DuckDB: in-process, WAL mode, 2 threads
- LMDB: map_size=256MB default, max 512MB
- Arrow IPC pro bulk insert (zero-copy)
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass, field
import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any
from _core import aclose

if TYPE_CHECKING:
    pass  # DuckDBShadowStore loaded lazily to avoid circular imports

logger = logging.getLogger(__name__)

# ============================================================================
# Phase 1: DuckDB consolidation — LanceDB removal
# ============================================================================
#
# MIGRATION STATUS (F350M-R, 2026-07):
# Migration complete: DuckDBRAGStore + DuckDBEntityStore (duckdb_rag_store.py)
#   fully replace LanceDBIdentityStore, LanceDBRAGEngine, SemanticStore.
#   Exported via knowledge/__init__.py as DuckDBRAGStore, DuckDBEntityStore,
#   get_identity_store(), get_rag_store().
#
# ⚠️  INCOMPLETE: knowledge/vector_index.py still contains LanceDbIndex
#   backend (get_vector_index factory). Phase 1 cleanup blocked by
#   sqlite-vec IVF-PQ limitations (M1 8GB). When sqlite-vec gains
#   IVF-PQ support OR HNSW is implemented in DuckDB, remove LanceDbIndex.
#
# DuckDB 1.4+ provides:
# - Native HNSW index (CREATE INDEX ... USING HNSW)
# - FTS5 extension (full-text search)
# - Arrow integration (zero-copy)


# ============================================================================
# Phase 2: SQLite3 → DuckDB migration
# ============================================================================

# SQLite3 was used for:
# 1. Audit trail (security/audit.py)
# 2. Temporal signal (layers/temporal_signal_store.py)
# 3. CT log cache (intel/ct_log_scanner.py)
# 4. Forensics metadata (forensics/metadata_extractor.py)
# 5. Evidence log (evidence_log.py)
# 6. Hive coordination (layers/hive_coordination.py) — DEPRECATED
#
# Migration strategy:
# - Audit trail → DuckDB audit_events table
# - Temporal signal → DuckDB temporal_signals table
# - CT log cache → DuckDB ct_cache table
# - Forensics metadata → DuckDB forensics_metadata table
# - Evidence log → Keep as-is (append-only ledger, separate concern)


# ============================================================================
# Phase 3: Centralized DuckDB import
# ============================================================================

# DuckDB imports were scattered across 21+ files
# Solution: Single module-level lazy import via this facade


# -----------------------------------------------------------------------------
# DuckDB lazy import helpers
# -----------------------------------------------------------------------------

_DUCKDB_STORE: Any | None = None
_DUCKDB_POOL_READY: bool = False


def _get_duckdb_store() -> Any:
    """Lazy DuckDBShadowStore singleton — canonical store for all DuckDB data."""
    global _DUCKDB_STORE
    if _DUCKDB_STORE is None:
        from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore
        _DUCKDB_STORE = DuckDBShadowStore()
    return _DUCKDB_STORE


# -----------------------------------------------------------------------------
# LMDB lazy import helpers
# -----------------------------------------------------------------------------

_LMDB_ENV: Any | None = None


def _get_lmdb_env() -> Any:
    """Lazy LMDB environment — single canonical LMDB env for cache/dedup."""
    global _LMDB_ENV
    if _LMDB_ENV is None:
        from paths import open_lmdb, LMDB_ROOT
        LMDB_ROOT.mkdir(parents=True, exist_ok=True)
        _LMDB_ENV = open_lmdb(LMDB_ROOT / "unified_cache.lmdb")
    return _LMDB_ENV


# -----------------------------------------------------------------------------
# Rust connection pool
# -----------------------------------------------------------------------------

_RUST_POOL: bool | None = None


def _get_rust_pool() -> bool | None:
    """Lazy Rust StdConnectionPool for DuckDB async queries.

    ISSUE-013 FIX: async_query.rs was deleted from Rust — init_async_pool()
    and rust_async_query() do not exist. The Rust async query path was never
    fully wired. Pool initialization always falls back to Python DuckDB.
    """
    global _RUST_POOL
    if _RUST_POOL is None:
        # ISSUE-013: init_async_pool() and rust_async_query() were removed.
        # async_query.rs was deleted; only the FFI manifest (stale) listed them.
        # DuckDB operations use Python duckdb directly via DuckDBShadowStore.
        _RUST_POOL = False
        logger.debug("[DB] Rust async pool unavailable — using Python DuckDB")
    return _RUST_POOL


# ============================================================================
# Dataclasses for unified interface
# ============================================================================


class DBCoordinates(Struct, frozen=True):
    """Coordinates for a database operation."""
    db: str  # "duckdb" | "lmdb"
    table: str | None = None
    schema: str | None = None


class QueryResult(Struct):
    """Generic query result."""
    rows: list[dict[str, Any]]
    columns: list[str]
    row_count: int
    duration_ms: float


# ============================================================================
# Unified Database Facade
# ============================================================================


class UnifiedDatabaseFacade:
    """
    Single entry point for all database operations.

    DESIGN PRINCIPLES:
    1. DuckDB for structured analytics, canonical facts, FTS, vectors
    2. LMDB for cache, dedup, ephemeral KV
    3. Rust connection pool for async queries (when available)
    4. Arrow IPC for bulk zero-copy operations
    5. Fail-soft throughout — errors never crash the pipeline

    MIGRATION PHASES:
    - Phase 1: This facade + LanceDB deprecation
    - Phase 2: SQLite3 → DuckDB migration
    - Phase 3: Centralized import consolidation
    """

    __slots__ = tuple('_duckdb_store _lmdb_env _rust_pool _initialized _init_lock'.split())

    _instance: "UnifiedDatabaseFacade | None" = None

    def __new__(cls) -> "UnifiedDatabaseFacade":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._duckdb_store: Any | None = None
        self._lmdb_env: Any | None = None
        self._rust_pool: Any | None = None
        self._init_lock = asyncio.Lock()
        self._initialized = True
        logger.info("[DB] UnifiedDatabaseFacade initialized")

    # --------------------------------------------------------------------------
    # DuckDB canonical store
    # --------------------------------------------------------------------------

    @cached_property
    def duckdb(self) -> Any:
        """DuckDBShadowStore singleton — canonical store for structured data."""
        if self._duckdb_store is None:
            self._duckdb_store = _get_duckdb_store()
        return self._duckdb_store

    # --------------------------------------------------------------------------
    # LMDB environment
    # --------------------------------------------------------------------------

    @cached_property
    def lmdb(self) -> Any:
        """LMDB environment for cache/dedup/KV operations."""
        if self._lmdb_env is None:
            self._lmdb_env = _get_lmdb_env()
        return self._lmdb_env

    # --------------------------------------------------------------------------
    # Rust async pool
    # --------------------------------------------------------------------------

    @property
    def rust_pool_ready(self) -> bool:
        """Check if Rust connection pool is available."""
        return _get_rust_pool() is not False

    def rust_query(self, sql: str, params: list[str] | None = None) -> list[list[str]]:
        """
        Execute query via Rust StdConnectionPool (O(1) connection access).

        Returns:
            List of rows, each row is a list of strings.

        ISSUE-013 FIX: rust_async_query/rust_async_query_with_params were removed
        (async_query.rs deleted). This method now delegates to DuckDB Python bindings
        via the Rust-initialized pool. The Rust async query path was never wired.
        """
        pool = _get_rust_pool()
        if pool is False:
            raise RuntimeError("Rust pool not available")
        # ISSUE-013: async_query.rs was removed — rust_async_query() does not exist.
        # Fall back to Python DuckDB for actual query execution.
        conn = self.duckdb._get_connection()
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        rows = cursor.fetchall()
        return [list(row) for row in rows]

    # --------------------------------------------------------------------------
    # DuckDB schema extensions (for migrated SQLite3 tables)
    # --------------------------------------------------------------------------

    def init_audit_schema(self) -> None:
        """Initialize audit events table in DuckDB."""
        conn = self.duckdb._get_connection()
        conn.execute("""
            CREATE SEQUENCE IF NOT EXISTS audit_events_id_seq
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id          BIGINT DEFAULT nextval('audit_events_id_seq'),
                timestamp   DOUBLE NOT NULL,
                event_type  VARCHAR,
                action      VARCHAR,
                resource    VARCHAR,
                user_id     VARCHAR,
                session_id  VARCHAR,
                details     JSON,
                level       VARCHAR,
                hash        VARCHAR,
                PRIMARY KEY (id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_events_timestamp
            ON audit_events(timestamp DESC)
        """)
        logger.info("[DB] audit_events table initialized")

    def init_temporal_schema(self) -> None:
        """Initialize temporal signals table in DuckDB."""
        conn = self.duckdb._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS temporal_signals (
                signal_id   VARCHAR PRIMARY KEY,
                signal_type VARCHAR,
                target      VARCHAR,
                first_seen  DOUBLE,
                last_seen   DOUBLE,
                count       INTEGER,
                metadata    JSON
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_temporal_signals_last_seen
            ON temporal_signals(last_seen DESC)
        """)
        logger.info("[DB] temporal_signals table initialized")

    def init_ct_cache_schema(self) -> None:
        """Initialize CT log cache table in DuckDB."""
        conn = self.duckdb._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ct_cache (
                domain      VARCHAR PRIMARY KEY,
                subdomains  JSON,
                fetched_at  DOUBLE
            )
        """)
        logger.info("[DB] ct_cache table initialized")

    def init_forensics_schema(self) -> None:
        """Initialize forensics metadata table in DuckDB."""
        conn = self.duckdb._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS forensics_metadata (
                file_hash   VARCHAR NOT NULL,
                mod_time    DOUBLE NOT NULL,
                file_size   BIGINT NOT NULL,
                file_type   VARCHAR,
                metadata     JSON,
                extracted_at DOUBLE,
                PRIMARY KEY (file_hash, mod_time, file_size)
            )
        """)
        logger.info("[DB] forensics_metadata table initialized")

    # --------------------------------------------------------------------------
    # Unified query interface
    # --------------------------------------------------------------------------

    async def query_duckdb(
        self,
        sql: str,
        params: list[Any] | None = None,
    ) -> QueryResult:
        """
        Execute DuckDB query with optional parameters.

        Uses Rust pool when available for O(1) connection access.
        Falls back to DuckDBShadowStore direct connection.
        """
        import time
        start = time.monotonic()

        if self.rust_pool_ready:
            try:
                rust_params = [str(p) for p in (params or [])]
                raw_rows = self.rust_query(sql, rust_params)
                # Convert string rows back to proper types
                # (Rust pool returns strings for simplicity)
                if raw_rows:
                    columns = [f"col_{i}" for i in range(len(raw_rows[0]))]
                    converted_rows = [dict(zip(columns, r)) for r in raw_rows]
                else:
                    columns = []
                    converted_rows = []
                return QueryResult(
                    rows=converted_rows,
                    columns=columns,
                    row_count=len(raw_rows),
                    duration_ms=(time.monotonic() - start) * 1000,
                )
            except Exception as e:
                logger.warning(f"[DB] Rust query failed, falling back: {e}")

        # Fallback to DuckDBShadowStore
        conn = self.duckdb._get_connection()
        if params:
            result = conn.execute(sql, params).fetchall()
        else:
            result = conn.execute(sql).fetchall()

        columns = [desc[0] for desc in conn.description] if conn.description else []
        rows = [dict(zip(columns, row)) for row in result]

        return QueryResult(
            rows=rows,
            columns=columns,
            row_count=len(rows),
            duration_ms=(time.monotonic() - start) * 1000,
        )

    # --------------------------------------------------------------------------
    # LMDB operations
    # --------------------------------------------------------------------------

    def lmdb_get(self, key: bytes) -> bytes | None:
        """Get value from LMDB cache."""
        try:
            with self.lmdb.begin() as txn:
                return txn.get(key)
        except Exception as e:
            logger.debug(f"[DB] LMDB get error: {e}")
            return None

    def lmdb_put(self, key: bytes, value: bytes) -> bool:
        """Put value into LMDB cache."""
        try:
            with self.lmdb.begin(write=True) as txn:
                txn.put(key, value)
            return True
        except Exception as e:
            logger.debug(f"[DB] LMDB put error: {e}")
            return False

    def lmdb_delete(self, key: bytes) -> bool:
        """Delete key from LMDB cache."""
        try:
            with self.lmdb.begin(write=True) as txn:
                txn.delete(key)
            return True
        except Exception as e:
            logger.debug(f"[DB] LMDB delete error: {e}")
            return False

    # --------------------------------------------------------------------------
    # Arrow zero-copy bulk insert
    # --------------------------------------------------------------------------

    async def bulk_insert_arrow(
        self,
        table: str,
        arrow_batch: bytes,
        schema: str,
    ) -> int:
        """
        Bulk insert using Arrow IPC with Rust validate_batch.

        Uses zero_copy.rs::validate_batch for validation,
        then Arrow→DuckDB for storage.

        NOTE: Currently unused (0 callers). Kept for future Arrow-based
        bulk ingest when DuckDB becomes primary vector store.
        validate_batch is not yet in Rust — falls through to no-op.
        """
        try:
            # R6: Centralized Rust access via core.rust_backend
            from hledac.universal._core.rust_backend import rust
            validate_batch = rust.raw.validate_batch
            validate_batch(arrow_batch, schema)
        except ImportError:
            logger.warning("[DB] validate_batch not available, skipping validation")

        # DuckDB Arrow insert
        conn = self.duckdb._get_connection()
        try:
            import pyarrow as pa
            reader = pa.ipc.open_record_batch_reader(arrow_batch)
            conn.execute(f"INSERT INTO {table} SELECT * FROM reader")
            return reader.num_record_batches
        except Exception as e:
            logger.error(f"[DB] Arrow bulk insert failed: {e}")
            return 0

    # --------------------------------------------------------------------------
    # Deprecation helpers
    # --------------------------------------------------------------------------

    @property
    def lancedb_available(self) -> bool:
        """LanceDB is deprecated — returns False."""
        return False

    @property
    def sqlite3_available(self) -> bool:
        """SQLite3 for caching is deprecated — use DuckDB or LMDB."""
        return False


# ============================================================================
# Singleton accessor
# ============================================================================

_db_facade: UnifiedDatabaseFacade | None = None


def get_db() -> UnifiedDatabaseFacade:
    """Get the unified database facade singleton."""
    global _db_facade
    if _db_facade is None:
        _db_facade = UnifiedDatabaseFacade()
    return _db_facade


# ============================================================================
# Module-level convenience functions
# ============================================================================

# For backwards compatibility during migration
def duckdb_store() -> Any:
    """Get DuckDB store singleton."""
    return get_db().duckdb


def lmdb_env() -> Any:
    """Get LMDB environment singleton."""
    return get_db().lmdb
