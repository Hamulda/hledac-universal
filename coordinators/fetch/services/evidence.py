"""
Evidence Sink Service — Evidence Creation and Adapter
=====================================================

Provides evidence collection and storage for fetch operations.

Features:
- Evidence creation from fetch results
- Evidence adapter for different storage backends
- Content fingerprinting (hashes, entropy)
- Metadata enrichment

M1 8GB: Uses __slots__ for memory efficiency.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from hledac.universal.compat.msgspec_gc_compat import Struct

logger = logging.getLogger(__name__)


class EvidenceConfig(Struct, frozen=True):
    """Evidence configuration. M1 8GB: msgspec.Struct for fast init."""

    enable_fingerprinting: bool = True
    hash_algorithms: tuple[str, ...] = ("sha256", "md5")
    max_content_stored: int = 1024 * 1024  # 1MB
    enable_metadata: bool = True
    evidence_queue_size: int = 10000


def _utc_now() -> datetime:
    """Factory for UTC now timestamp."""
    return datetime.now(UTC)


@dataclass(slots=True)
class EvidenceRecord:
    """Evidence record for a fetch result."""

    url: str
    timestamp: datetime = field(default_factory=_utc_now)
    content_hash: str = ""
    content_type: str = ""
    status_code: int = 0
    headers: dict[str, str] = field(default_factory=dict)
    fetch_duration_ms: float = 0.0
    transport: str = "clearnet"
    entropy_score: float = 0.0
    error: str | None = None
    content_fingerprint: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    size_bytes: int = 0


@dataclass(slots=True)
class EvidenceSinkService:
    """
    Evidence sink service for fetch result storage.

    Provides evidence collection and storage:
    - Evidence creation from fetch results
    - Content fingerprinting (hashes)
    - Evidence adapter for different backends
    - Metadata enrichment

    M1 8GB: Uses __slots__ for memory efficiency.
    """

    config: EvidenceConfig = field(default_factory=EvidenceConfig)

    _evidence_queue: asyncio.Queue[EvidenceRecord] = field(default_factory=lambda: asyncio.Queue(maxsize=10000))
    _storage_backend: Any = field(default=None, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _stats: dict[str, Any] = field(
        default_factory=lambda: {
            "records_created": 0,
            "records_stored": 0,
            "storage_errors": 0,
            "total_size_bytes": 0,
        }
    )

    def set_storage_backend(self, backend: Any) -> None:
        """
        Set storage backend.

        Backend should implement:
        - async store(evidence: EvidenceRecord) -> bool
        - async retrieve(url: str) -> EvidenceRecord | None
        - async search(query: dict) -> list[EvidenceRecord]
        """
        self._storage_backend = backend

    async def create_evidence(
        self,
        url: str,
        content: bytes | None = None,
        status_code: int = 0,
        headers: dict[str, str] | None = None,
        fetch_duration_ms: float = 0.0,
        transport: str = "clearnet",
        entropy_score: float = 0.0,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> EvidenceRecord:
        """
        Create evidence record from fetch result.

        Args:
            url: Fetched URL
            content: Response content (optional)
            status_code: HTTP status code
            headers: Response headers
            fetch_duration_ms: Fetch duration in milliseconds
            transport: Transport used (clearnet/tor/i2p/gopher)
            entropy_score: Content entropy score
            error: Error message if fetch failed
            metadata: Additional metadata

        Returns:
            Created EvidenceRecord
        """
        record = EvidenceRecord(
            url=url,
            status_code=status_code,
            headers=headers or {},
            fetch_duration_ms=fetch_duration_ms,
            transport=transport,
            entropy_score=entropy_score,
            error=error,
            metadata=metadata or {},
            size_bytes=len(content) if content else 0,
        )

        # Fingerprint content
        if content and self.config.enable_fingerprinting:
            record.content_hash = self._compute_primary_hash(content)
            record.content_fingerprint = self._compute_fingerprint(content)

        # Enrich metadata
        if self.config.enable_metadata:
            record.metadata.update(
                {
                    "created_at": time.time(),
                    "content_length": len(content) if content else 0,
                    "content_type": headers.get("Content-Type", "") if headers else "",
                    "server": headers.get("Server", "") if headers else "",
                }
            )

        async with self._lock:
            self._stats["records_created"] += 1
            self._stats["total_size_bytes"] += record.size_bytes

        try:
            self._evidence_queue.put_nowait(record)
        except asyncio.QueueFull:
            logger.warning("Evidence queue full, dropping record")

        return record

    def _compute_primary_hash(self, content: bytes) -> str:
        """Compute primary hash (SHA256)."""
        return hashlib.sha256(content).hexdigest()

    def _compute_fingerprint(self, content: bytes) -> str:
        """
        Compute content fingerprint.

        Uses multiple hash algorithms for different use cases.
        """
        hashes = {}
        for algo in self.config.hash_algorithms:
            try:
                h = hashlib.new(algo)
                h.update(content[: self.config.max_content_stored])
                hashes[algo] = h.hexdigest()
            except Exception:  # noqa: BLE001
                pass

        return str(hashes)

    async def store(self, record: EvidenceRecord) -> bool:
        """
        Store evidence record.

        Args:
            record: Evidence record to store

        Returns:
            True if stored successfully
        """
        if self._storage_backend is None:
            # In-memory only
            async with self._lock:
                self._stats["records_stored"] += 1
            return True

        try:
            success = await self._storage_backend.store(record)
            async with self._lock:
                self._stats["records_stored"] += 1 if success else 0
            return success
        except Exception as e:  # noqa: BLE001
            logger.error(f"Evidence storage error: {e}")
            async with self._lock:
                self._stats["storage_errors"] += 1
            return False

    async def retrieve(self, url: str) -> EvidenceRecord | None:
        """
        Retrieve evidence for URL.

        Args:
            url: URL to look up

        Returns:
            EvidenceRecord if found, None otherwise
        """
        if self._storage_backend is None:
            return None

        try:
            return await self._storage_backend.retrieve(url)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Evidence retrieval error: {e}")
            return None

    async def search(self, query: dict[str, Any]) -> list[EvidenceRecord]:
        """
        Search evidence records.

        Args:
            query: Search query

        Returns:
            List of matching records
        """
        if self._storage_backend is None:
            return []

        try:
            return await self._storage_backend.search(query)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Evidence search error: {e}")
            return []

    async def process_queue(self, batch_size: int = 100) -> int:
        """
        Process evidence queue and store records.

        Args:
            batch_size: Number of records to process per batch

        Returns:
            Number of records processed

        M-2026-FIX: collect records, then drain queue and store concurrently
        via ``asyncio.gather(..., return_exceptions=True)``. The previous
        version awaited ``store()`` one-at-a-time, serializing all evidence
        writes through a single point of contention. Now N records fan out
        into the underlying batch writer.
        """
        # 1) Drain up to batch_size records in a tight loop (non-blocking).
        records: list = []
        for _ in range(batch_size):
            try:
                records.append(self._evidence_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not records:
            return 0

        # 2) Fan out writes; tolerate per-record failure.
        outcomes = await asyncio.gather(
            *(self.store(record) for record in records),
            return_exceptions=True,
        )

        # 3) Log per-record failures (logger.error preserves the message).
        for rec, outcome in zip(records, outcomes):
            if isinstance(outcome, BaseException):
                logger.error(
                    f"[evidence-svc] store failed for {type(rec).__name__}: {outcome!r}"
                )

        # 4) Count successful writes (anything that wasn't an exception, incl.
        # returns of None and returns of True).
        processed = sum(
            1 for o in outcomes if not isinstance(o, BaseException)
        )
        return processed

        return processed

    def get_queue_size(self) -> int:
        """Get queue size."""
        return self._evidence_queue.qsize()

    def get_stats(self) -> dict[str, Any]:
        """Get evidence statistics."""
        return {
            **self._stats,
            "queue_size": self._evidence_queue.qsize(),
            "has_storage_backend": self._storage_backend is not None,
        }

    async def aclose(self) -> None:
        """Close evidence sink service and release resources."""
        await self.process_queue()
        async with self._lock:
            self._storage_backend = None
        logger.debug("EvidenceSinkService closed")


class InMemoryEvidenceStorage:
    """In-memory storage backend for evidence records."""

    def __init__(self, max_records: int = 10000) -> None:
        self.max_records = max_records
        self._records: dict[str, EvidenceRecord] = {}
        self._lock = asyncio.Lock()

    async def store(self, evidence: EvidenceRecord) -> bool:
        """Store evidence record."""
        async with self._lock:
            if len(self._records) >= self.max_records:
                oldest = min(self._records.keys(), key=lambda k: self._records[k].timestamp)
                del self._records[oldest]

            self._records[evidence.url] = evidence
            return True

    async def retrieve(self, url: str) -> EvidenceRecord | None:
        """Retrieve evidence for URL."""
        async with self._lock:
            return self._records.get(url)

    async def search(self, query: dict[str, Any]) -> list[EvidenceRecord]:
        """Search evidence records."""
        results: list[EvidenceRecord] = []

        async with self._lock:
            for record in self._records.values():
                if self._matches_query(record, query):
                    results.append(record)

        return results

    def _matches_query(self, record: EvidenceRecord, query: dict[str, Any]) -> bool:
        """Check if record matches query."""
        for key, value in query.items():
            if not hasattr(record, key):
                continue
            if getattr(record, key) != value:
                return False
        return True


__all__ = [
    "EvidenceConfig",
    "EvidenceRecord",
    "EvidenceSinkService",
    "InMemoryEvidenceStorage",
    "_utc_now",  # Exported for testing
]
