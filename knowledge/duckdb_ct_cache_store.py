"""
knowledge/duckdb_ct_cache_store.py — DuckDB-backed CT Log Cache
=============================================================


ISSUE-001 Phase 2: SQLite3 → DuckDB Migration

Drop-in replacement for intel/ct_log_scanner.py CT cache.
Stores CT log data in DuckDB instead of SQLite3.

MIGRATION:
    Old: CTLogScanner with SQLite3 cache
    New: CTLogCacheStore using DuckDB

SCHEMA:
    ct_cache (
        domain      VARCHAR PRIMARY KEY,
        subdomains  JSON,       -- List of subdomains
        fetched_at  DOUBLE      -- Unix timestamp
    )
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import msgspec.json as _json
from core import aclose

logger = logging.getLogger(__name__)


class CTLogCacheStore:
    """
    DuckDB-backed CT log cache.

    Replaces SQLite3-based CT log caching in intel/ct_log_scanner.py.
    Uses DuckDB for better M1 performance.

    MIGRATION:
        # Old
        from hledac.universal.network.ct_log_scanner import CTLogScanner
        scanner = CTLogScanner()
        subdomains = scanner._get_cached(domain)

        # New
        from hledac.universal.knowledge.duckdb_ct_cache_store import CTLogCacheStore
        cache = CTLogCacheStore()
        subdomains = await cache.get(domain)
    """

    __slots__ = tuple("_db_store _initialized _ttl_days".split())

    DEFAULT_TTL_DAYS: int = 7

    def __init__(self, ttl_days: int = DEFAULT_TTL_DAYS) -> None:
        self._db_store: Any = None
        self._initialized: bool = False
        self._ttl_days: int = ttl_days

    async def initialize(self) -> None:
        """Initialize DuckDB CT cache store."""
        if self._initialized:
            return

        from hledac.universal.knowledge.db import get_db

        db = get_db()
        self._db_store = db.duckdb
        self._db_store.init_ct_cache_schema()
        self._initialized = True
        logger.info("[CT_CACHE:DuckDB] Initialized")

    def _get_connection(self) -> Any:
        """Get DuckDB connection."""
        if self._db_store is None:
            from hledac.universal.knowledge.db import get_db
            self._db_store = get_db().duckdb
        return self._db_store._get_connection()

    async def get(self, domain: str) -> list[str] | None:
        """
        Get cached subdomains for domain.

        Args:
            domain: Domain to look up

        Returns:
            List of subdomains or None if not cached/expired
        """
        if not self._initialized:
            await self.initialize()

        import time

        cutoff = time.time() - (self._ttl_days * 86400)
        conn = self._get_connection()

        rows = await asyncio.to_thread(
            lambda: conn.execute(
                "SELECT subdomains FROM ct_cache WHERE domain = ? AND fetched_at >= ?",
                (domain, cutoff),
            ).fetchall()
        )

        if not rows:
            return None

        subdomains_bytes = rows[0][0]
        if isinstance(subdomains_bytes, str):
            subdomains_bytes = subdomains_bytes.encode()
        return _json.decode(subdomains_bytes)

    async def set(self, domain: str, subdomains: list[str]) -> None:
        """
        Cache subdomains for domain.

        Args:
            domain: Domain
            subdomains: List of subdomains
        """
        if not self._initialized:
            await self.initialize()

        import time

        conn = self._get_connection()
        subdomains_json = _json.encode(subdomains).decode("utf-8")
        fetched_at = time.time()

        await asyncio.to_thread(
            lambda: conn.execute(
                """
                INSERT OR REPLACE INTO ct_cache (domain, subdomains, fetched_at)
                VALUES (?, ?, ?)
                """,
                (domain, subdomains_json, fetched_at),
            )
        )

    async def delete(self, domain: str) -> None:
        """
        Delete cached entry for domain.

        Args:
            domain: Domain to delete
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        await asyncio.to_thread(
            lambda: conn.execute("DELETE FROM ct_cache WHERE domain = ?", (domain,))
        )

    async def cleanup_expired(self) -> int:
        """
        Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        if not self._initialized:
            await self.initialize()

        import time

        cutoff = time.time() - (self._ttl_days * 86400)
        conn = self._get_connection()

        result = await asyncio.to_thread(
            lambda: conn.execute(
                "DELETE FROM ct_cache WHERE fetched_at < ?",
                (cutoff,),
            )
        )
        return result.rowcount if hasattr(result, "rowcount") else 0

    async def close(self) -> None:
        """Close cache store."""
        self._initialized = False
        logger.info("[CT_CACHE:DuckDB] Closed")
