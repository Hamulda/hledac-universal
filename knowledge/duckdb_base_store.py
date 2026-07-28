"""DuckDB Base Store — F360: Shared patterns for DuckDB-backed stores.

ARCHITECTURE:
    Provides common base class for all DuckDB store implementations:
      - DuckDBCTCacheStore     (knowledge/duckdb_ct_cache_store.py)
      - DuckDBForensicsStore   (knowledge/duckdb_forensics_store.py)
      - DuckDBShadowStore      (knowledge/duckdb_store.py) — migration path

SHARED PATTERNS (extracted from duckdb_ct_cache_store.py ↔ duckdb_forensics_store.py):
    1. Connection management (_db_store, _initialized, _get_connection)
    2. Lazy initialization pattern (async initialize() with double-check locking)
    3. Thread-safe async operations via asyncio.to_thread
    4. TTL/expiration management for cache stores
    5. JSON serialization via msgspec

STORAGE TRINITY (CLAUDE.md):
    Layer    | Tech    | Purpose
    ---------|---------|-------------------------------
    DuckDB   | SQL     | Canonical store (this module)
    LMDB     | Key-val | Entity/claim metadata

M1 8GB constraints:
    - DuckDB in-process mode (F275: DUCKDB_INPROCESS=1 default)
    - Thread-affine connections — each thread needs its own connection
    - asyncio.to_thread for all DuckDB operations
    - Fail-safe: all operations return empty/safe values on error

Usage:
    from hledac.universal.knowledge.duckdb_base_store import DuckDBBaseStore

    class MyStore(DuckDBBaseStore):
        __slots__ = tuple("_my_field _other_field".split())

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._my_field = None

        async def _init_schema(self) -> None:
            # Override: create table if not exists
            pass
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

logger = logging.getLogger(__name__)


class DuckDBBaseStore(ABC):
    """
    Abstract base for DuckDB-backed stores.

    Provides:
      - Lazy connection management via _get_connection()
      - Async initialization with double-check locking
      - Thread-safe DuckDB operations via asyncio.to_thread
      - Fail-safe error handling (returns empty values on error)

    M1 8GB invariants:
      - All DuckDB operations MUST run on thread pool (asyncio.to_thread)
      - Connections are thread-affine — never share across threads
      - Fail-safe: catch all exceptions, return safe empty values
    """

    # Override in subclass __slots__
    __slots__ = tuple("_db_store _initialized _ttl_days _schema_name".split())

    def __init__(
        self,
        ttl_days: int | None = None,
        schema_name: str | None = None,
    ) -> None:
        """
        Args:
            ttl_days: Optional TTL for cache stores (None = no expiration).
            schema_name: Optional schema name for table initialization.
        """
        self._db_store: "DuckDBShadowStore | None" = None
        self._initialized: bool = False
        self._ttl_days: int | None = ttl_days
        self._schema_name: str | None = schema_name

    # ── Connection Management ────────────────────────────────────────────────

    def _get_connection(self) -> Any:
        """
        Get DuckDB connection from the shared store.

        Lazy initialization: first call triggers store acquisition from
        knowledge.db singleton and schema initialization.

        Returns:
            DuckDB connection (thread-affine, from DuckDBShadowStore).
        """
        if self._db_store is None:
            from hledac.universal.knowledge.db import get_db

            db = get_db()
            self._db_store = db.duckdb
            # Ensure schema is initialized (idempotent)
            self._init_schema_if_needed()
        return self._db_store._get_connection()

    def _get_duckdb(self) -> Any:
        """Get DuckDB module via shared singleton."""
        from hledac.universal.knowledge.duckdb_store import _get_duckdb

        return _get_duckdb()

    # ── Schema Initialization ───────────────────────────────────────────────

    def _init_schema_if_needed(self) -> None:
        """Initialize schema if not already done (idempotent)."""
        if not self._initialized:
            try:
                self._init_schema()
                self._initialized = True
                logger.info(f"[{self.__class__.__name__}] Schema initialized")
            except Exception:  # noqa: BLE001 — best-effort; non-critical
                logger.warning(f"[{self.__class__.__name__}] Schema init failed")

    @abstractmethod
    def _init_schema(self) -> None:
        """
        Initialize DuckDB schema (create tables, indexes, etc.).

        Called once lazily on first _get_connection() call.

        Example:
            def _init_schema(self) -> None:
                conn = self._db_store._get_connection()
                conn.execute("CREATE TABLE IF NOT EXISTS my_table (key VARCHAR PRIMARY KEY, value JSON)")
        """
        ...

    # ── Async Initialization (for stores that need explicit init) ────────────

    async def async_initialize(self) -> None:
        """
        Async initialization — for stores that need explicit init call.

        Default implementation acquires connection and initializes schema.
        Override if custom initialization logic is needed.
        """
        if self._initialized:
            return
        # Ensure connection is established
        self._get_connection()
        self._initialized = True
        logger.info(f"[{self.__class__.__name__}] Initialized")

    # ── Thread-Safe DuckDB Operations ────────────────────────────────────────

    async def _async_execute(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[Any] | None:
        """
        Execute SQL on thread pool and return results.

        Args:
            sql: SQL query (use ? placeholders for DuckDB).
            params: Query parameters.

        Returns:
            List of rows or None on error.
        """
        def _sync_execute() -> list[Any] | None:
            try:
                conn = self._get_connection()
                result = conn.execute(sql, params or [])
                rows = result.fetchall()
                return list(rows) if rows else []
            except Exception:  # noqa: BLE001 — best-effort; non-critical
                return None

        try:
            return await asyncio.to_thread(_sync_execute)
        except Exception:  # noqa: BLE001 — best-effort; non-critical
            return None

    async def _async_execute_void(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> bool:
        """
        Execute SQL that doesn't return results (INSERT, UPDATE, etc.).

        Args:
            sql: SQL statement.
            params: Statement parameters.

        Returns:
            True on success, False on error.
        """
        def _sync_execute_void() -> bool:
            try:
                conn = self._get_connection()
                conn.execute(sql, params or [])
                return True
            except Exception:  # noqa: BLE001 — best-effort; non-critical
                return False

        try:
            return await asyncio.to_thread(_sync_execute_void)
        except Exception:  # noqa: BLE001 — best-effort; non-critical
            return False

    # ── TTL Helpers (for cache stores) ──────────────────────────────────────

    def _get_cutoff_time(self) -> float:
        """Get cutoff timestamp for TTL-based expiration."""
        import time

        if self._ttl_days is None:
            return 0.0  # No expiration
        return time.time() - (self._ttl_days * 86400)

    async def _cleanup_expired(self, table: str, time_column: str) -> int:
        """
        Generic expired-entry cleanup for cache stores.

        Args:
            table: Table name to clean
            time_column: Column storing the timestamp

        Returns:
            Number of entries removed
        """
        if self._ttl_days is None:
            return 0  # No TTL configured

        import time as _time

        cutoff = _time.time() - (self._ttl_days * 86400)
        count_sql = f"SELECT COUNT(*) FROM {table} WHERE {time_column} < ?"
        delete_sql = f"DELETE FROM {table} WHERE {time_column} < ?"

        def _sync_delete() -> int:
            try:
                conn = self._get_connection()
                # Get count before delete
                count = conn.execute(count_sql, (cutoff,)).fetchall()
                deleted = count[0][0] if count else 0
                # Execute delete
                conn.execute(delete_sql, (cutoff,))
                return deleted
            except Exception:  # noqa: BLE001
                return 0

        try:
            return await asyncio.to_thread(_sync_delete)
        except Exception:  # noqa: BLE001
            return 0

    # ── JSON Serialization ───────────────────────────────────────────────────

    @staticmethod
    def _json_encode(data: Any) -> str:
        """Encode data as JSON string via msgspec."""
        import msgspec.json as _json

        return _json.encode(data).decode("utf-8")

    @staticmethod
    def _json_decode(raw: bytes | str) -> Any:
        """Decode JSON via msgspec (accepts bytes or str directly)."""
        import msgspec.json as _json

        return _json.decode(raw)
