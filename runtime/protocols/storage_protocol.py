"""
runtime/protocols/storage_protocol.py — F270: Storage Interface
==============================================================

Protocol for DuckDB/LMDB storage operations.
Extracted from SprintScheduler's STORAGE group (~6 attributes).

GHOST_INVARIANTS:
- Fail-safe: all implementations return [] on error
- Bounded: no unbounded collections
"""



from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    pass


@runtime_checkable
class StorageProtocol(Protocol):
    """
    Storage operations protocol for DuckDB + LMDB.

    Implementations:
        - DuckDBStoreAdapter: wraps DuckDBShadowStore
        - InMemoryStorageAdapter: for testing

    Key methods:
        - async_ingest_findings: canonical write path
        - open_lmdb: zero-copy LMDB access
        - query_sprint_results: SQL queries
    """

    async def async_ingest_findings(
        self, findings: list[Any], sprint_id: str
    ) -> None:
        """Ingest findings into DuckDB canonical store."""
        ...

    async def async_flush_arrow(self) -> None:
        """Flush any pending Arrow batches."""
        ...

    def open_lmdb(self) -> Iterator[Any]:
        """Open LMDB environment for zero-copy metadata access."""
        ...

    def query_sprint_results(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL query against DuckDB store."""
        ...

    async def async_initialize(self) -> None:
        """Initialize storage (open DuckDB, LMDB)."""
        ...
