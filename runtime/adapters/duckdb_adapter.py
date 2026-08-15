"""
runtime/adapters/duckdb_adapter.py — F270: DuckDB Storage Adapter
=============================================================


Adapter implementing StorageProtocol for DuckDBShadowStore.
Non-breaking: wraps existing DuckDBShadowStore without changes.

GHOST_INVARIANTS:
- Fail-safe: all methods return empty/default on error
- Bounded: write queue bounded
"""



from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from hledac.universal.runtime.protocols.storage_protocol import StorageProtocol
from _core import aclose


class DuckDBStoreAdapter(StorageProtocol):
    """
    Adapter wrapping DuckDBShadowStore to implement StorageProtocol.

    This adapter is non-breaking — it wraps the existing store
    and delegates to it without changing behavior.

    Usage:
        store = DuckDBShadowStore(...)
        adapter = DuckDBStoreAdapter(store)
        # Use as StorageProtocol
        await adapter.async_ingest_findings(findings, sprint_id)
    """

    __slots__ = ('_store',)

    def __init__(self, store: Any) -> None:
        """
        Initialize adapter with existing DuckDBShadowStore.

        Args:
            store: DuckDBShadowStore instance to wrap
        """
        self._store = store

    async def async_ingest_findings(
        self, findings: list[Any], sprint_id: str
    ) -> None:
        """Delegate to store's canonical write path."""
        try:
            await self._store.async_ingest_findings_batch(findings, sprint_id)
        except Exception:  # noqa: BLE001
            # Fail-safe: log and continue
            pass

    async def async_flush_arrow(self) -> None:
        """Flush pending Arrow batches."""
        try:
            await self._store.async_flush_arrow()
        except Exception:  # noqa: BLE001
            pass

    def open_lmdb(self) -> Iterator[Any]:
        """Delegate LMDB open to store."""
        try:
            return self._store.open_lmdb()
        except Exception:
            return iter([])

    def query_sprint_results(self, sql: str) -> list[dict[str, Any]]:
        """Execute SQL query against store."""
        try:
            return self._store.query_sprint_results(sql)
        except Exception:
            return []

    async def async_initialize(self) -> None:
        """Initialize underlying store."""
        try:
            if hasattr(self._store, 'async_initialize'):
                await self._store.async_initialize()
        except Exception:  # noqa: BLE001
            pass
