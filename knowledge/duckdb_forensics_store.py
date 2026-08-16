"""
knowledge/duckdb_forensics_store.py — DuckDB-backed Forensics Metadata Store
=========================================================================


ISSUE-001 Phase 2: SQLite3 → DuckDB Migration

Drop-in replacement for forensics/metadata_extractor.py SQLite3 cache.
Stores forensics metadata in DuckDB instead of SQLite3.

MIGRATION:
    Old: ForensicsMetadataExtractor with SQLite3 cache
    New: ForensicsMetadataStore using DuckDB

SCHEMA:
    forensics_metadata (
        file_hash     VARCHAR NOT NULL,
        mod_time      DOUBLE NOT NULL,
        file_size     BIGINT NOT NULL,
        file_type     VARCHAR,
        metadata     JSON,
        extracted_at DOUBLE,
        PRIMARY KEY (file_hash, mod_time, file_size)
    )

Note: Uses composite key (file_hash, mod_time, file_size) for cache
invalidation, matching the original SQLite3 cache behavior. The file_hash
is a partial content hash (first+last 1MB for large files), so mod_time
and file_size are needed to detect actual file changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import msgspec.json as _json
from _core import aclose

logger = logging.getLogger(__name__)


class ForensicsMetadataStore:
    """
    DuckDB-backed forensics metadata store.

    Replaces SQLite3-based forensics metadata caching.
    Uses DuckDB for better M1 performance.

    MIGRATION:
        # Old
        from forensics.metadata_extractor import ForensicsMetadataExtractor
        extractor = ForensicsMetadataExtractor()
        metadata = extractor._get_cached(file_hash)

        # New
        from hledac.universal.knowledge.duckdb_forensics_store import ForensicsMetadataStore
        store = ForensicsMetadataStore()
        metadata = await store.get(file_hash)
    """

    __slots__ = tuple("_db_store _initialized".split())

    def __init__(self) -> None:
        self._db_store: Any = None
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Initialize DuckDB forensics store."""
        if self._initialized:
            return

        from hledac.universal.knowledge.db import get_db

        db = get_db()
        self._db_store = db.duckdb
        self._db_store.init_forensics_schema()
        self._initialized = True
        logger.info("[FORENSICS:DuckDB] Initialized")

    def _get_connection(self) -> Any:
        """Get DuckDB connection."""
        if self._db_store is None:
            from hledac.universal.knowledge.db import get_db
            self._db_store = get_db().duckdb
        return self._db_store._get_connection()

    async def get(
        self,
        file_hash: str,
        mod_time: float,
        file_size: int,
    ) -> dict[str, Any] | None:
        """
        Get cached metadata for file hash.

        Args:
            file_hash: Partial content hash of file (from _get_file_hash)
            mod_time: File modification time (for cache invalidation)
            file_size: File size in bytes (for cache invalidation)

        Returns:
            Metadata dictionary or None if not cached
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()

        rows = await asyncio.to_thread(
            lambda: conn.execute(
                """
                SELECT metadata FROM forensics_metadata
                WHERE file_hash = ? AND mod_time = ? AND file_size = ?
                """,
                (file_hash, mod_time, file_size),
            ).fetchall()
    )

        if not rows:
            return None

        metadata_bytes = rows[0][0]
        if isinstance(metadata_bytes, str):
            metadata_bytes = metadata_bytes.encode()
        return _json.decode(metadata_bytes)

    async def set(
        self,
        file_hash: str,
        mod_time: float,
        file_size: int,
        file_type: str,
        metadata: dict[str, Any],
    ) -> None:
        """
        Cache metadata for file.

        Args:
            file_hash: Partial content hash of file
            mod_time: File modification time (for cache invalidation)
            file_size: File size in bytes (for cache invalidation)
            file_type: Type of file (e.g., "pdf", "docx")
            metadata: Metadata dictionary
        """
        if not self._initialized:
            await self.initialize()

        import time

        conn = self._get_connection()
        metadata_json = _json.encode(metadata).decode("utf-8")
        extracted_at = time.time()

        await asyncio.to_thread(
            lambda: conn.execute(
                """
                INSERT OR REPLACE INTO forensics_metadata
                (file_hash, mod_time, file_size, file_type, metadata, extracted_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (file_hash, mod_time, file_size, file_type, metadata_json, extracted_at),
    )
        )

    async def delete(self, file_hash: str) -> None:
        """
        Delete cached metadata for file.

        Args:
            file_hash: SHA256 hash of file
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()
        await asyncio.to_thread(
            lambda: conn.execute(
                "DELETE FROM forensics_metadata WHERE file_hash = ?",
                (file_hash,),
    )
        )

    async def get_by_type(self, file_type: str, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get cached metadata by file type.

        Args:
            file_type: Type of file
            limit: Maximum results

        Returns:
            List of metadata dictionaries
        """
        if not self._initialized:
            await self.initialize()

        conn = self._get_connection()

        rows = await asyncio.to_thread(
            lambda: conn.execute(
                """
                SELECT file_hash, file_type, metadata, extracted_at
                FROM forensics_metadata
                WHERE file_type = ?
                ORDER BY extracted_at DESC
                LIMIT ?
                """,
                (file_type, limit),
            ).fetchall()
    )

        results = []
        for row in rows:
            metadata_bytes = row[2]
            if isinstance(metadata_bytes, str):
                metadata_bytes = metadata_bytes.encode()
            results.append({
                "file_hash": row[0],
                "file_type": row[1],
                "metadata": _json.decode(metadata_bytes),
                "extracted_at": row[3],
            })
        return results

    async def close(self) -> None:
        """Close forensics store."""
        self._initialized = False
        logger.info("[FORENSICS:DuckDB] Closed")
